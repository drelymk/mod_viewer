"""Opt-in WebGPU/control/contact-sheet pass over a backend audit manifest.

Run after audit_mod_library.py. Default coverage selects one mod per library;
--all covers every successful backend selection. Each browser worker is isolated
and its results resume independently. Source mods are never edited or exported.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools.audit_mod_library import (  # noqa: E402
    digest,
    pipeline_signature,
    write_json,
)


def probe_signature(config):
    return hashlib.sha256(
        pipeline_signature(config).encode()
        + Path(__file__).read_bytes()
        + (ROOT / "tools/benchmark_texture_pipeline.py").read_bytes()
    ).hexdigest()


def menu_position_count(menu):
    if menu.get("kind") == "shape_slider":
        return 3
    lengths = [len(cycle.get("values", [])) for cycle in menu.get("cycles", [menu])]
    # Menu cycles wrap each variable independently, unlike clamped Key tuples.
    return math.lcm(*lengths) if lengths and all(lengths) else 0


def select_entries(manifest, backend_results, all_entries=False, per_library=1):
    eligible = [
        entry
        for entry in manifest["entries"]
        if backend_results.get(entry["id"], {}).get("status") in {"loaded", "review"}
    ]
    if all_entries:
        return eligible
    selected = []
    for library in manifest["libraries"]:
        candidates = [
            entry for entry in eligible if entry["library"] == library["name"]
        ]

        # Exercise feature-rich examples first, with stable path tie-breaking.
        def priority(entry):
            metrics = backend_results[entry["id"]].get("metrics", {})
            features = sum(
                bool(metrics.get(key))
                for key in (
                    "toggle_count",
                    "menu_count",
                    "shape_meshes",
                    "animated_meshes",
                )
            )
            return (-features, metrics.get("geometry_bytes", 0), entry["relative"])

        selected.extend(sorted(candidates, key=priority)[:per_library])
    return selected


def probe_page(page, payload, output, max_states):
    """Exercise actual cycle/slider handlers, then restore by reloading."""
    print("Browser load reached the audit probe", flush=True)
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "initial.png"))
    if payload is None:
        return {"status": "load_incomplete", "states_checked": 0}
    real_webgpu = page.evaluate("""async () => {
      const {renderer, rendererReady} = await import('./js/scene/scene.js');
      await rendererReady;
      return renderer?.backend?.isWebGPUBackend === true;
    }""")
    if not real_webgpu:
        return {"status": "renderer_unavailable", "states_checked": 0}
    page.locator("#controls-tab").click()
    controls = payload.get("controls", {})
    toggles = list(controls.get("toggles", {}).values())
    menus = list(controls.get("menu", {}).values())

    # UI source grouping can differ from payload insertion order.
    def grouped(records):
        sources = list(dict.fromkeys(record.get("source") or "" for record in records))
        return [
            record
            for source in sources
            for record in records
            if (record.get("source") or "") == source
        ]

    toggles, menus = grouped(toggles), grouped(menus)
    scenarios = []
    for index, toggle in enumerate(toggles):
        variables = toggle.get("cycle_vars") or toggle.get("vars", [])
        positions = max((len(var.get("values", [])) for var in variables), default=0)
        scenarios.append(
            {
                "selector": "#toggle-list .toggle-item",
                "index": index,
                "positions": positions,
                "kind": "toggle",
                "section": toggle.get("section"),
            }
        )
    for index, menu in enumerate(menus):
        positions = menu_position_count(menu)
        scenarios.append(
            {
                "selector": "#menu-list .menu-item",
                "index": index,
                "positions": positions,
                "kind": menu.get("kind", "menu"),
                "section": menu.get("section"),
            }
        )
    present = controls.get("present", {}).get("item")
    if present and not present.get("sync_error") and present.get("count", 0) > 0:
        scenarios.append(
            {
                "selector": "#present-list",
                "index": 0,
                "positions": present.get("count", 0),
                "kind": "present",
            }
        )
    total = sum(row["positions"] for row in scenarios)
    observations = []
    checked = 0
    for scenario in scenarios:
        remaining = max_states - checked if max_states else scenario["positions"]
        positions = min(scenario["positions"], remaining)
        if positions <= 0:
            continue
        item = page.locator(scenario["selector"]).nth(scenario["index"])
        for position in range(positions):
            if scenario["kind"] == "shape_slider":
                item.locator("input[type=range]").evaluate(
                    """(input, position) => {
                  input.value = [input.min, (Number(input.min) + Number(input.max)) / 2, input.max][position];
                  input.dispatchEvent(new Event('input', {bubbles: true}));
                }""",
                    position,
                )
            else:
                # Calling the DOM handler also reaches controls in collapsed
                # source groups without changing their values independently.
                item.locator(".toggle-cycle-btn").first.evaluate(
                    "button => button.click()"
                )
            observation = page.evaluate("""async () => {
              const {requestRender} = await import('./js/scene/render-scheduler.js');
              const {renderer} = await import('./js/scene/scene.js');
              requestRender();
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              await renderer.backend.device.queue.onSubmittedWorkDone();
              return {visible: window.modViewer.activeMeshes.filter(mesh => mesh.visible).length,
                      meshes: window.modViewer.activeMeshes.length};
            }""")
            observations.append(
                {
                    "kind": scenario["kind"],
                    "section": scenario.get("section"),
                    "step": position,
                    **observation,
                }
            )
            checked += 1
            if checked % 10 == 0:
                print(f"Checked {checked}/{total} control states", flush=True)
                write_json(
                    output / "progress.json",
                    {"states_checked": checked, "states_available": total},
                )
    print("Checking animation playback", flush=True)
    animation = page.evaluate("""async () => {
      const runtime = await import('./js/mesh/animation-runtime.js');
      const before = runtime.getAnimationPlaybackState();
      if (before.available) {
        runtime.setAnimationPaused(true);
        if (before.canSeek) {
          runtime.setAnimationFrame(before.frameStart);
          runtime.setAnimationFrame(Math.round((before.frameStart + before.frameEnd) / 2));
          runtime.setAnimationFrame(before.frameEnd);
        }
        runtime.setAnimationPaused(false);
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      return before;
    }""")
    # Reload resets preview controls and also exercises replacement/teardown.
    print("Checking model reload", flush=True)
    reloaded = page.evaluate("""async () => {
      let timer;
      const result = await Promise.race([
        window.modViewer.reloadCurrentMod(),
        new Promise(resolve => {timer = setTimeout(() => resolve(false), 20000);}),
      ]);
      clearTimeout(timer);
      return result !== false;
    }""")
    print(f"Model reload completed: {reloaded}", flush=True)
    page.wait_for_load_state("networkidle")
    screenshots = []
    for view, turns in (("front", 0), ("side", 1), ("back", 1)):
        if turns:
            page.locator("#camera-flip-btn").click()
        page.evaluate("""async () => {
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          requestRender();
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }""")
        path = output / f"{view}.png"
        page.screenshot(path=str(path))
        screenshots.append(path.name)
    return {
        "status": "checked" if reloaded else "reload_error",
        "states_checked": checked,
        "states_available": total,
        "states_truncated": checked < total,
        "observations": observations,
        "animation": animation,
        "reload": reloaded,
        "screenshots": screenshots,
        "coverage": "Individual UI cycles and slider endpoints; interaction combinations and editing are unverified.",
    }


def worker(job):
    from app.bridge.api import ModViewerAPI
    from app.runtime import server
    from tools.benchmark_texture_pipeline import _ProcessSampler, _run_browser

    api = ModViewerAPI()
    api._access.remember_mod_picker_selection(job["entry"]["path"])
    sampler = _ProcessSampler()
    sampler.start()
    result = {"cache_key": job["cache_key"], "entry_id": job["entry"]["id"]}
    try:
        print("Starting Edge load probe", flush=True)
        browser, _payload = _run_browser(
            server.start(),
            api,
            job["entry"]["path"],
            sampler,
            "msedge",
            force_disabled_ini=job["entry"].get("disabled_ini", False),
            audit_callback=lambda page, payload: probe_page(
                page, payload, Path(job["images"]), job["max_states"]
            ),
            launch_args=[
                "--no-proxy-server",
                "--enable-unsafe-webgpu",
                "--disable-gpu-sandbox",
                "--ignore-gpu-blocklist",
                "--use-webgpu-adapter=d3d11",
            ],
        )

        # A missing browser favicon is recorded but does not invalidate a
        # geometry/texture/control smoke check.
        def incidental(url):
            return urlsplit(url).path == "/favicon.ico"

        functional_console = [
            entry
            for entry in browser["console_error_details"]
            if not incidental(entry["url"])
        ]
        functional_http = [
            entry for entry in browser["http_errors"] if not incidental(entry["url"])
        ]
        findings = bool(
            browser["page_errors"]
            or functional_console
            or functional_http
            or any(
                status >= 400
                for status in browser["texture_statuses"] + browser["geometry_statuses"]
            )
        )
        result.update(
            status=("review" if findings else "checked")
            if browser.get("audit", {}).get("status") == "checked"
            else browser.get("audit", {}).get("status", "incomplete"),
            browser=browser,
        )
    except Exception as error:
        traceback.print_exc()
        result.update(status="browser_error", error=f"{type(error).__name__}: {error}")
    finally:
        sampler.stop()
    write_json(job["result_path"], result)


def gallery(manifest, results, output):
    cards = []
    for entry in manifest["entries"]:
        result = results.get(entry["id"])
        if not result:
            continue
        title = html.escape(entry["library"] + " / " + entry["relative"])
        audit = result.get("browser", {}).get("audit", {})
        screenshot_names = audit.get("screenshots", []) or [
            path.name for path in (output / "images" / entry["id"]).glob("*.png")
        ]
        images = "".join(
            f'<a href="images/{entry["id"]}/{name}"><img width="320" src="images/{entry["id"]}/{name}" alt="{name}"></a>'
            for name in screenshot_names
        )
        cards.append(
            f"<article><h2>{title}</h2><p>{html.escape(result['status'])}; states {audit.get('states_checked', 0)}/{audit.get('states_available', 0)}</p>{images}</article>"
        )
    (output / "gallery.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Mod render audit</title><style>body{font:14px system-ui;background:#171b22;color:#e8edf5;margin:24px}article{border-top:1px solid #465063;padding:12px}img{margin:4px}</style><h1>Mod render audit</h1><p>Screenshots need visual review. Automated checks do not establish correct appearance.</p>'
        + "\n".join(cards),
        encoding="utf-8",
    )
    write_json(
        output / "summary.json",
        {
            "completed": len(results),
            "statuses": dict(Counter(row["status"] for row in results.values())),
            "excluded": manifest["excluded"],
            "total_backend_selections": len(manifest["entries"]),
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit", type=Path, default=ROOT / "mod-analysis/library-audit"
    )
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--per-library", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--max-states",
        type=int,
        default=100,
        help="Per-mod state budget; 0 is unlimited",
    )
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(json.loads(args.worker.read_text(encoding="utf-8")))
        return
    if args.per_library < 1 or args.timeout <= 0 or args.max_states < 0:
        parser.error("Invalid browser audit limits")
    audit = args.audit.resolve()
    manifest = json.loads((audit / "manifest.json").read_text(encoding="utf-8"))
    backend = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (audit / "results").glob("*.json")
    }
    selected = select_entries(manifest, backend, args.all, args.per_library)
    output = audit / "browser"
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    signature = probe_signature(
        json.loads((ROOT / "src/config.json").read_text(encoding="utf-8"))
    )
    for entry in selected:
        result_path = output / "results" / f"{entry['id']}.json"
        cache_key = digest(
            [backend[entry["id"]]["cache_key"], signature, args.max_states]
        )
        if result_path.exists():
            cached = json.loads(result_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                results[entry["id"]] = cached
                continue
        temporary = output / "tmp" / entry["id"]
        temporary.mkdir(parents=True, exist_ok=True)
        job_path = output / "jobs" / f"{entry['id']}.json"
        write_json(
            job_path,
            {
                "entry": entry,
                "cache_key": cache_key,
                "result_path": str(result_path),
                "images": str(output / "images" / entry["id"]),
                "max_states": args.max_states,
            },
        )
        log_path = output / "logs" / f"{entry['id']}.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    str(job_path),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                env={
                    **os.environ,
                    "PYTHONUTF8": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TEMP": str(temporary),
                    "TMP": str(temporary),
                },
            )
            try:
                process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            stdout=log,
                            stderr=log,
                            check=False,
                            timeout=10,
                        )
                    except subprocess.TimeoutExpired:
                        pass
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                write_json(
                    result_path,
                    {
                        "cache_key": cache_key,
                        "entry_id": entry["id"],
                        "status": "timeout",
                    },
                )
            current_result = (
                json.loads(result_path.read_text(encoding="utf-8"))
                if result_path.exists()
                else {}
            )
            if current_result.get("cache_key") != cache_key:
                write_json(
                    result_path,
                    {
                        "cache_key": cache_key,
                        "entry_id": entry["id"],
                        "status": "worker_crash",
                    },
                )
        results[entry["id"]] = json.loads(result_path.read_text(encoding="utf-8"))
        gallery(manifest, results, output)
        print(
            f"Browser {len(results)}/{len(selected)}: {entry['library']} {results[entry['id']]['status']}",
            flush=True,
        )
    gallery(manifest, results, output)


if __name__ == "__main__":
    main()
