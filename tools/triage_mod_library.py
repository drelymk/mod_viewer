"""Separate read-only corpus audit outcomes into actionable review buckets."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools.audit_mod_library import write_json  # noqa: E402

DRAW = re.compile(r"^\s*(?:(?:pre|post)\s+)?drawindexed\s*=", re.I | re.M)


def classify(result, raw_draws=None, omissions=None, empty=False):
    status = result["status"]
    if status == "loaded":
        return "loaded"
    if status == "load_error" and result.get("error") in {
        "No active .ini files found in this folder.",
        "No disabled .ini files found in this folder.",
    }:
        return (
            "no_disabled_ini"
            if result["error"].startswith("No disabled")
            else "no_active_ini"
        )
    if status == "review":
        if result.get("metrics", {}).get("identity_errors"):
            return "identity_error"
        if omissions and all(draw.get("count") == 0 for draw in omissions):
            return "zero_count_draws"
        return "partial_geometry_review"
    if status == "load_error" and str(result.get("error", "")).startswith(
        "No mesh geometry found"
    ):
        return (
            "empty_ini"
            if empty
            else "no_authored_draws"
            if raw_draws == 0
            else "unrecognized_draws"
        )
    return status


def inspect_selection(entry, result):
    from app.mods.analysis import analyze_mod_inis
    from core.geometry.semantics import deduplicate_draws
    from core.mod_discovery import discover_ini_paths
    from core.mod_source import mod_source_for_path

    source = mod_source_for_path(entry["path"])
    paths = discover_ini_paths(
        entry["path"], source=source, disabled=entry.get("disabled_ini", False)
    )
    texts = [source.read_text(path) for path in paths]
    raw_draws = sum(len(DRAW.findall(text)) for text in texts)
    omissions = []
    if result["status"] == "review":
        parsed = analyze_mod_inis(paths, entry["path"], source=source)
        missing = set(result.get("metrics", {}).get("unrepresented_draws", []))
        for group in parsed.groups:
            for draw in deduplicate_draws(group):
                if draw.label in missing:
                    omissions.append(
                        {
                            "label": draw.label,
                            "count": draw.count,
                            "conditions": draw.conditions,
                            "source_count": len(draw.sources or []),
                        }
                    )
        for key in missing - {draw["label"] for draw in omissions}:
            omissions.append(
                {
                    "label": key,
                    "count": None,
                    "note": "Draw no longer matches the audit snapshot",
                }
            )
    return (
        raw_draws,
        omissions,
        bool(texts) and all(not text.strip("\ufeff \t\r\n") for text in texts),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit", type=Path, default=ROOT / "mod-analysis/library-audit"
    )
    args = parser.parse_args()
    output = args.audit.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for entry in manifest["entries"]:
        path = output / "results" / f"{entry['id']}.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        raw_draws, omissions, error, empty = None, None, None, False
        if result["status"] in {"load_error", "review"}:
            try:
                raw_draws, omissions, empty = inspect_selection(entry, result)
            except Exception as exception:
                error = f"{type(exception).__name__}: {exception}"
        rows.append(
            {
                "id": entry["id"],
                "library": entry["library"],
                "relative": entry["relative"],
                "path": entry["path"],
                "bucket": classify(result, raw_draws, omissions, empty),
                "raw_draws": raw_draws,
                "omissions": omissions,
                "inspection_error": error,
                "load_error": result.get("error"),
                "metrics": result.get("metrics", {}),
            }
        )
        if len(rows) % 250 == 0:
            print(f"Triaged {len(rows)} selections", flush=True)
    counts = dict(Counter(row["bucket"] for row in rows))
    write_json(
        output / "triage.json",
        {
            "counts": counts,
            "completed": len(rows),
            "total": len(manifest["entries"]),
            "excluded": manifest["excluded"],
            "disabled_ini": manifest.get("disabled_ini", False),
            "rows": rows,
        },
    )
    lines = [
        "# Mod library audit findings",
        "",
        f"Audited {len(rows)} of {len(manifest['entries'])} selections. Excluded: {', '.join(manifest['excluded'])}.",
        f"INI selection: {'disabled only' if manifest.get('disabled_ini') else 'active only'}.",
        "",
        "Backend results do not establish visual correctness. These are review buckets, not confirmed bug counts.",
        "",
        "| Bucket | Selections |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {bucket} | {count} |" for bucket, count in sorted(counts.items()))
    lines += [
        "",
        "`empty_ini` covers selections whose selected INIs are empty. `no_active_ini` / `no_disabled_ini` cover selections without a matching INI.",
        "`no_authored_draws` covers selected INIs without drawindexed commands, including master/control and texture-only selections.",
        "`zero_count_draws` covers omitted draws that explicitly draw zero indices; those omissions are expected.",
        "`unrecognized_draws` and `partial_geometry_review` need inspection of the INI, buffers and intended game behavior.",
        "",
        "## Compatibility candidates",
        "",
        "| Library | Selection | Reason | Details |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        if row["bucket"] in {
            "loaded",
            "no_authored_draws",
            "zero_count_draws",
            "empty_ini",
            "no_active_ini",
            "no_disabled_ini",
        }:
            continue
        detail = row["load_error"] or ", ".join(
            f"{draw['label']} (count={draw.get('count')})"
            for draw in row["omissions"] or []
        )
        cells = [row["library"], row["relative"], row["bucket"], detail]
        lines.append(
            "| "
            + " | ".join(
                str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells
            )
            + " |"
        )
    lines += [
        "",
        "Full per-selection evidence is in triage.json, results/, and logs/. Rendering/control coverage is reported separately in browser/.",
    ]
    (output / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(counts), flush=True)


if __name__ == "__main__":
    main()
