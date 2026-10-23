"""Read-only, resumable corpus audit using the production mod-open pipeline.

Example: python tools/audit_mod_library.py --exclude downloads --workers 2
Reports and worker logs stay in the ignored mod-analysis directory. Use
--disabled-ini to select disabled INIs exclusively, as in the viewer. Linked
directories are inventoried as skips. Backend success is
not a claim of visual correctness; browser coverage is reported independently.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
SCHEMA = 1
ARCHIVES = {".zip", ".7z", ".rar"}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def file_stamp(path):
    stat = path.stat()
    return [path.name, stat.st_size, stat.st_mtime_ns]


def inventory(config, excluded, *, disabled_ini=False):
    """Inventory selections without merging category folders into one mod."""
    from core.mod_discovery import discover_ini_paths

    exclusions = {item.casefold() for item in excluded}
    roots = config.get("modFolders", [])
    known = {str(root[key]).casefold() for root in roots for key in ("name", "path")}
    if exclusions - known:
        raise ValueError(
            "Unknown excluded library: " + ", ".join(sorted(exclusions - known))
        )
    entries, skipped, errors, libraries = [], [], [], []
    seen = set()
    for root in roots:
        library = root["name"]
        folder = Path(root["path"]).resolve()
        if (
            library.casefold() in exclusions
            or str(root["path"]).casefold() in exclusions
        ):
            skipped.append(
                {"library": library, "path": str(folder), "reason": "excluded_library"}
            )
            continue
        libraries.append({"name": library, "path": str(folder)})
        if not folder.is_dir():
            errors.append(
                {
                    "library": library,
                    "path": str(folder),
                    "error": "Library is unavailable",
                }
            )
            continue
        directories, archives, tree = [], [], {}

        def onerror(error):
            errors.append(
                {"library": library, "path": str(error.filename), "error": str(error)}
            )

        for base, dirs, files in os.walk(folder, followlinks=False, onerror=onerror):
            base = Path(base)
            allowed = []
            for name in sorted(dirs):
                path = base / name
                if (name.upper().startswith("DISABLED") and not disabled_ini) or linked(
                    path
                ):
                    skipped.append(
                        {
                            "library": library,
                            "path": str(path),
                            "reason": "disabled_or_linked",
                        }
                    )
                else:
                    allowed.append(name)
            dirs[:] = allowed
            stamps, inis = [], []
            for name in sorted(files):
                path = base / name
                is_disabled = name.upper().startswith("DISABLED")
                if (is_disabled and not disabled_ini) or linked(path):
                    skipped.append(
                        {
                            "library": library,
                            "path": str(path),
                            "reason": "disabled_or_linked",
                        }
                    )
                    continue
                try:
                    stamp = file_stamp(path)
                except OSError as error:
                    onerror(error)
                    continue
                stamps.append(stamp)
                if path.suffix.lower() == ".ini" and is_disabled == disabled_ini:
                    inis.append(str(path))
                elif path.suffix.lower() in ARCHIVES:
                    archives.append((path, digest(stamp)))
            tree[base] = {
                "files": stamps,
                "children": [base / name for name in allowed],
            }
            if inis:
                directories.append((base, inis))
        fingerprints = {}
        for base, node in reversed(list(tree.items())):
            fingerprints[base] = digest(
                [node["files"], [fingerprints.get(child) for child in node["children"]]]
            )
        covered = set()
        for base, inis in directories:
            if set(inis) <= covered:
                skipped.append(
                    {
                        "library": library,
                        "path": str(base),
                        "reason": "included_by_parent_selection",
                    }
                )
                continue
            try:
                selected = [
                    str(path)
                    for path in discover_ini_paths(str(base), disabled=disabled_ini)
                ]
            except Exception as error:
                errors.append(
                    {"library": library, "path": str(base), "error": str(error)}
                )
                selected = inis
            # Discovery may include a disabled directory; retain the production
            # selection and make that boundary visible in the manifest.
            covered.update(selected)
            path_key = os.path.normcase(str(base))
            if path_key in seen:
                continue
            seen.add(path_key)
            entries.append(
                {
                    "id": digest(path_key)[:20],
                    "library": library,
                    "path": str(base),
                    "relative": base.relative_to(folder).as_posix(),
                    "kind": "directory",
                    "ini_files": selected,
                    "fingerprint": fingerprints[base],
                    "disabled_ini": disabled_ini,
                }
            )
        for path, fingerprint in archives:
            path_key = os.path.normcase(str(path))
            if path_key not in seen:
                seen.add(path_key)
                entries.append(
                    {
                        "id": digest(path_key)[:20],
                        "library": library,
                        "path": str(path),
                        "relative": path.relative_to(folder).as_posix(),
                        "kind": path.suffix.lower()[1:],
                        "ini_files": [],
                        "fingerprint": fingerprint,
                        "disabled_ini": disabled_ini,
                    }
                )
    return {
        "schema": SCHEMA,
        "libraries": libraries,
        "excluded": sorted(exclusions),
        "entries": entries,
        "skipped": skipped,
        "inventory_errors": errors,
        "disabled_ini": disabled_ini,
    }


def pipeline_signature(config):
    files = sorted((ROOT / "src").rglob("*.py"))
    files += sorted((ROOT / "src/web/js").rglob("*.js"))
    files += [Path(__file__)]
    hasher = hashlib.sha256(json.dumps(config, sort_keys=True).encode())
    for path in files:
        hasher.update(path.relative_to(ROOT).as_posix().encode())
        hasher.update(path.read_bytes())
    # Asset index changes can alter enrichment without changing mod files.
    for path in sorted((ROOT / "src/asset-index").glob("*.json")):
        hasher.update(json.dumps(file_stamp(path)).encode())
    return hasher.hexdigest()


def summarize_payload(payload, parsed, context):
    from core.geometry.semantics import build_mesh_semantics

    meshes = payload.get("meshes", {})
    semantic = (
        build_mesh_semantics(
            parsed.groups,
            context.mod_dir,
            game_profile=parsed.game.game,
            source=context.source,
        )
        if parsed is not None
        else {}
    )

    # Animation families may consolidate many draws. Compare source coverage
    # as well as identities and keep omissions as review candidates, not bugs.
    def sources(entry):
        return {
            json.dumps(source, sort_keys=True) for source in entry.get("sources", [])
        }

    represented = set().union(*(sources(entry) for entry in meshes.values()))
    omitted = [
        key
        for key, entry in semantic.items()
        if key not in meshes and not (sources(entry) and sources(entry) <= represented)
    ]
    identities = [entry.get("identity", {}).get("key") for entry in meshes.values()]
    identity_errors = (
        len(identities) - len(set(identities)) + sum(key is None for key in identities)
    )
    controls = payload.get("controls", {})
    return {
        "mesh_count": len(meshes),
        "semantic_draw_count": len(semantic),
        "unrepresented_draws": omitted,
        "identity_errors": identity_errors,
        "vertices": sum(
            entry.get("pos", {}).get("length", 0) // 12 for entry in meshes.values()
        ),
        "triangles": sum(
            entry.get("idx", {}).get("length", 0) // 12 for entry in meshes.values()
        ),
        "geometry_bytes": (payload.get("geometry") or {}).get("length", 0),
        "texture_count": len(payload.get("textures", {})),
        "toggle_count": len(controls.get("toggles", {})),
        "menu_count": len(controls.get("menu", {})),
        "shape_meshes": sum(
            bool(entry.get("shape_targets")) for entry in meshes.values()
        ),
        "animated_meshes": sum(
            bool(entry.get("animation_geometry")) for entry in meshes.values()
        ),
        "skinning_meshes": sum(
            bool(entry.get("skinning_available")) for entry in meshes.values()
        ),
        "game": payload.get("metadata", {}).get("game"),
        "asset_resolution": payload.get("asset_resolution"),
    }


def backend_audit(entry):
    from app.bridge.api import ModViewerAPI
    from app.mods import loader
    from app.runtime import server
    from core.ini.health import analyze_mod
    from tools.benchmark_texture_pipeline import _ProcessSampler

    api = ModViewerAPI()
    api._access.remember_mod_picker_selection(entry["path"])
    captured = {}
    original = loader.analyze_mod_inis

    def analyze(*args, **kwargs):
        parsed = original(*args, **kwargs)
        captured["parsed"] = parsed
        return parsed

    loader.analyze_mod_inis = analyze
    sampler = _ProcessSampler()
    sampler.start()
    started = time.perf_counter()
    result = {}
    disabled_ini = entry.get("disabled_ini", False)
    try:
        server.start(require_ui_assets=False)
        payload = api.load_mod(entry["path"], disabled_ini=disabled_ini)
        loaded = time.perf_counter()
        context = api._mod_preview.authoritative_context(
            entry["path"], disabled_ini=disabled_ini
        )[2]
        # An empty disabled selection must not trigger diagnostics' ordinary
        # active-INI discovery fallback and contaminate the audit session.
        health = (
            analyze_mod(
                context.mod_dir,
                ini_paths=[],
                documents=context.docs,
                source=context.source,
            )
            if disabled_ini and not context.ini_paths
            else api.get_diagnostics(entry["path"])
        )
        metrics = summarize_payload(payload, captured.get("parsed"), context)
        error = payload.get("error")
        review = bool(metrics["unrepresented_draws"] or metrics["identity_errors"])
        result.update(
            status="load_error" if error else "review" if review else "loaded",
            error=error,
            disabled_ini=disabled_ini,
            load_seconds=loaded - started,
            metrics=metrics,
            health=health,
            selected_inis=[
                context.source.logical_path(path) for path in context.ini_paths
            ],
        )
        return result
    finally:
        loader.analyze_mod_inis = original
        sampler.stop()
        result["resources"] = sampler.window(started, time.perf_counter())


def run_worker(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    started = time.perf_counter()
    result = {"entry_id": job["entry"]["id"], "cache_key": job["cache_key"]}
    try:
        result.update(backend_audit(job["entry"]))
    except Exception as error:
        traceback.print_exc()
        result.update(status="exception", error=f"{type(error).__name__}: {error}")
    result["total_seconds"] = time.perf_counter() - started
    write_json(job["result_path"], result)


def execute(entry, output, signature, timeout, resume):
    result_path = output / "results" / f"{entry['id']}.json"
    cache_key = digest(
        [SCHEMA, signature, entry["fingerprint"], entry.get("disabled_ini", False)]
    )
    if resume and result_path.exists():
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        if existing.get("cache_key") == cache_key:
            return existing
    job_path = output / "jobs" / f"{entry['id']}.json"
    write_json(
        job_path,
        {"entry": entry, "cache_key": cache_key, "result_path": str(result_path)},
    )
    log_path = output / "logs" / f"{entry['id']}.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output / "tmp" / entry["id"]
    temporary.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    str(job_path),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                timeout=timeout,
                env={
                    **os.environ,
                    "PYTHONUTF8": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TEMP": str(temporary),
                    "TMP": str(temporary),
                },
            )
        except subprocess.TimeoutExpired:
            result = {"status": "timeout", "error": f"Exceeded {timeout:g} seconds"}
        else:
            if completed.returncode == 0 and result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("cache_key") == cache_key:
                    return result
            result = {
                "status": "worker_crash",
                "error": f"Worker exited with code {completed.returncode}",
            }
    result.update(entry_id=entry["id"], cache_key=cache_key)
    write_json(result_path, result)
    return result


def report(manifest, results, output):
    rows = [
        {**entry, **results[entry["id"]]}
        for entry in manifest["entries"]
        if entry["id"] in results
    ]
    counts = Counter(row["status"] for row in rows)
    issue_counts = Counter(
        issue["code"]
        for row in rows
        for issue in row.get("health", {}).get("issues", [])
    )
    summary = {
        "selections": len(manifest["entries"]),
        "completed": len(rows),
        "statuses": dict(counts),
        "issue_codes": dict(issue_counts.most_common()),
        "excluded": manifest["excluded"],
        "inventory_errors": manifest["inventory_errors"],
        "disabled_ini": manifest.get("disabled_ini", False),
        "browser_completed": 0,
        "coverage": "Backend load and diagnostics only; controls, rendering and editing are unverified.",
    }
    write_json(output / "summary.json", summary)
    body = []
    for row in rows:
        metrics = row.get("metrics", {})
        issues = Counter(
            issue["code"] for issue in row.get("health", {}).get("issues", [])
        )
        detail = row.get("error") or "; ".join(
            f"{code}: {count}" for code, count in issues.most_common(8)
        )
        cells = [
            row["library"],
            row["relative"],
            row["status"],
            str(metrics.get("mesh_count", "")),
            str(len(metrics.get("unrepresented_draws", []))),
            str(round(row.get("load_seconds", 0), 2)),
            detail,
        ]
        body.append(
            "<tr>"
            + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells)
            + "</tr>"
        )
    document = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Mod library audit</title>
<style>body{font:14px system-ui;margin:24px;background:#171b22;color:#e8edf5}input{padding:10px;width:45%;margin:12px 0}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #465063;text-align:left;vertical-align:top}th{background:#293242}td:nth-child(2){overflow-wrap:anywhere;max-width:420px}</style>
<h1>Mod library audit</h1><p>SUMMARY</p><p>Backend load and diagnostics only. A loaded result does not establish visual correctness. Diagnostics and omitted draws require triage.</p>
<input id="filter" placeholder="Filter by library, mod, status or issue code" aria-label="Filter results">
<table><thead><tr><th>Library</th><th>Selection</th><th>Status</th><th>Meshes</th><th>Unrepresented draws</th><th>Load seconds</th><th>Error / frequent diagnostics</th></tr></thead><tbody>ROWS</tbody></table>
<script>document.getElementById('filter').addEventListener('input',event=>{const query=event.target.value.toLowerCase();for(const row of document.querySelectorAll('tbody tr'))row.hidden=!row.textContent.toLowerCase().includes(query);});</script></html>"""
    document = document.replace(
        "SUMMARY", html.escape(json.dumps(summary, ensure_ascii=False))
    ).replace("ROWS", "\n".join(body))
    (output / "report.html").write_text(document, encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Library name or configured root; repeatable",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--disabled-ini",
        action="store_true",
        help="Select only DISABLED-prefixed INIs; never combine with active INIs",
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        run_worker(args.worker)
        return 0
    if args.workers < 1 or args.timeout <= 0 or args.limit < 0:
        parser.error("workers and timeout must be positive; limit must be nonnegative")
    config = json.loads((ROOT / "src/config.json").read_text(encoding="utf-8"))
    manifest = inventory(config, args.exclude, disabled_ini=args.disabled_ini)
    output = (
        args.output
        or ROOT
        / "mod-analysis"
        / ("library-audit-disabled" if args.disabled_ini else "library-audit")
    ).resolve()
    # Reports may contain private corpus identities and must never enter source.
    for library in config["modFolders"] + config.get("assetFolders", []):
        if output.is_relative_to(Path(library["path"]).resolve()):
            parser.error("Report output must be outside mod and Asset libraries")
    write_json(output / "manifest.json", manifest)
    print(
        f"Inventoried {len(manifest['entries'])} selections; excluded {manifest['excluded']}",
        flush=True,
    )
    if args.inventory_only:
        return 0
    signature = pipeline_signature(config)
    results = {}
    entries = manifest["entries"][: args.limit or None]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                execute, entry, output, signature, args.timeout, not args.no_resume
            ): entry
            for entry in entries
        }
        for future in as_completed(futures):
            entry = futures[future]
            results[entry["id"]] = future.result()
            if len(results) % 25 == 0 or len(results) == len(entries):
                summary = report(manifest, results, output)
                print(
                    f"Completed {len(results)}/{len(entries)}: {summary['statuses']}",
                    flush=True,
                )
    report(manifest, results, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
