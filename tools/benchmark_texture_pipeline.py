"""Benchmark the lazy texture pipeline on a real mod.

The default command runs one isolated worker for each requested semaphore size
and prints JSON suitable for comparing PRs:

    python tools/benchmark_texture_pipeline.py "<mod-folder>"

Each worker loads the mod through ``ModViewerAPI``, starts the real localhost
server, displays the payload in Edge, and waits for the visible texture
requests to settle.  The worker is isolated so Pillow's cache and browser
processes cannot make later concurrency rows artificially warm.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


_PROFILE_STAGES = (
    "decode",
    "rgb_rgba_conversion",
    "resize",
    "normal_z_reconstruction",
    "png_encoding",
)


class TextureProfiler:
    """Thread-safe collector for the optional core texture profile hook."""

    def __init__(self):
        self._lock = threading.Lock()
        self.events = []

    def __call__(self, stage, seconds, details):
        with self._lock:
            self.events.append({
                "stage": stage,
                "seconds": seconds,
                **details,
            })

    def clear(self):
        with self._lock:
            events = list(self.events)
            self.events.clear()
            return events

    @staticmethod
    def _identities(events, stage):
        return {
            (event.get("path"), event.get("role"), event.get("transform"))
            for event in events if event["stage"] == stage
        }

    def summarize(self, events):
        stage_seconds = defaultdict(float)
        stage_calls = defaultdict(int)
        for event in events:
            stage = event["stage"]
            if stage in _PROFILE_STAGES:
                stage_seconds[stage] += event["seconds"]
                stage_calls[stage] += 1
        encoded = [event for event in events if event["stage"] == "encoded"]
        return {
            "stage_seconds": {
                stage: stage_seconds.get(stage, 0.0)
                for stage in _PROFILE_STAGES
            },
            "stage_calls": {
                stage: stage_calls.get(stage, 0)
                for stage in _PROFILE_STAGES
            },
            "cache_hits": sum(event["stage"] == "cache_hit"
                               for event in events),
            "cache_misses": sum(event["stage"] == "cache_miss"
                                 for event in events),
            "actually_rendered": len(self._identities(events, "encoded")),
            "png_bytes_encoded": sum(event.get("bytes", 0)
                                      for event in encoded),
        }


class RenderConcurrencyProbe:
    def __init__(self):
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def wrap(self, function):
        def measured(*args, **kwargs):
            with self._lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                return function(*args, **kwargs)
            finally:
                with self._lock:
                    self.active -= 1
        return measured


def _install_timing(module, name, label, timings):
    original = getattr(module, name)

    def measured(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            timings[label].append(time.perf_counter() - started)

    setattr(module, name, measured)


class _ProcessSampler:
    """Sample aggregate RSS/CPU for this worker and its descendants."""

    def __init__(self):
        self._lock = threading.Lock()
        self._samples = []
        self._stop = threading.Event()
        self._thread = None
        self.source = "unavailable"
        if _psutil is not None:
            self._snapshot = _psutil_snapshot
            self.source = "psutil-process-tree"
        elif os.name == "nt":
            self._snapshot = _windows_snapshot
            self.source = "windows-process-tree"
        else:
            self._snapshot = _resource_snapshot
            self.source = "worker-only"

    def start(self):
        self._sample()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(0.05):
            self._sample()

    def _sample(self):
        rss, cpu = self._snapshot()
        with self._lock:
            self._samples.append((time.perf_counter(), rss, cpu))

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._sample()

    def window(self, started, ended):
        with self._lock:
            samples = [sample for sample in self._samples
                       if started <= sample[0] <= ended]
        if len(samples) < 2:
            return {"peak_rss_mb": None, "average_cpu_percent": None}
        first = samples[0]
        last = samples[-1]
        elapsed = max(last[0] - first[0], 0.000001)
        return {
            "peak_rss_mb": max(sample[1] for sample in samples) / (1024 * 1024),
            "average_cpu_percent": max(
                0.0, (last[2] - first[2]) / elapsed * 100.0),
        }


try:
    import psutil as _psutil
except ImportError:  # pragma: no cover - optional benchmark dependency
    _psutil = None


def _psutil_snapshot():
    root = _psutil.Process(os.getpid())
    processes = [root]
    try:
        processes.extend(root.children(recursive=True))
    except _psutil.Error:
        pass
    rss = 0
    cpu = 0.0
    for process in processes:
        try:
            rss += process.memory_info().rss
            times = process.cpu_times()
            cpu += times.user + times.system
        except _psutil.Error:
            continue
    return rss, cpu


def _resource_snapshot():
    import resource
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_maxrss * 1024, usage.ru_utime + usage.ru_stime


if os.name == "nt":
    _TH32CS_SNAPPROCESS = 0x00000002
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_VM_READ = 0x0010

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.wintypes.DWORD),
                    ("high", ctypes.wintypes.DWORD)]

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", ctypes.wintypes.DWORD),
            ("usage", ctypes.wintypes.DWORD),
            ("process_id", ctypes.wintypes.DWORD),
            ("default_heap", ctypes.c_size_t),
            ("module_id", ctypes.wintypes.DWORD),
            ("threads", ctypes.wintypes.DWORD),
            ("parent_id", ctypes.wintypes.DWORD),
            ("priority", ctypes.wintypes.LONG),
            ("flags", ctypes.wintypes.DWORD),
            ("exe_file", ctypes.wintypes.WCHAR * 260),
        ]

    class _MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD),
            ("page_fault_count", ctypes.wintypes.DWORD),
            ("peak_working_set", ctypes.c_size_t),
            ("working_set", ctypes.c_size_t),
            ("quota_peak_paged", ctypes.c_size_t),
            ("quota_paged", ctypes.c_size_t),
            ("quota_peak_non_paged", ctypes.c_size_t),
            ("quota_non_paged", ctypes.c_size_t),
            ("pagefile", ctypes.c_size_t),
            ("peak_pagefile", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _kernel32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [ctypes.wintypes.HANDLE,
                                          ctypes.POINTER(_ProcessEntry)]
    _kernel32.Process32NextW.argtypes = [ctypes.wintypes.HANDLE,
                                         ctypes.POINTER(_ProcessEntry)]
    _kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD,
                                      ctypes.wintypes.BOOL,
                                      ctypes.wintypes.DWORD]
    _kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    _kernel32.GetProcessTimes.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime)]
    _psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.POINTER(_MemoryCounters),
        ctypes.wintypes.DWORD]

    def _windows_parent_map():
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        invalid = ctypes.wintypes.HANDLE(-1).value
        if snapshot == invalid:
            return {}
        parents = {}
        try:
            entry = _ProcessEntry()
            entry.size = ctypes.sizeof(entry)
            if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return parents
            while True:
                parents[entry.process_id] = entry.parent_id
                if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        finally:
            _kernel32.CloseHandle(snapshot)
        return parents

    def _windows_snapshot():
        parents = _windows_parent_map()
        pids = {os.getpid()}
        changed = True
        while changed:
            changed = False
            for pid, parent in parents.items():
                if parent in pids and pid not in pids:
                    pids.add(pid)
                    changed = True
        rss = 0
        cpu = 0.0
        for pid in pids:
            handle = _kernel32.OpenProcess(
                _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ,
                False, pid)
            if not handle:
                continue
            try:
                counters = _MemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if _psapi.GetProcessMemoryInfo(
                        handle, ctypes.byref(counters), counters.cb):
                    rss += counters.working_set
                created = _FileTime()
                exited = _FileTime()
                kernel = _FileTime()
                user = _FileTime()
                if _kernel32.GetProcessTimes(
                        handle, ctypes.byref(created), ctypes.byref(exited),
                        ctypes.byref(kernel), ctypes.byref(user)):
                    kernel_ticks = (kernel.high << 32) | kernel.low
                    user_ticks = (user.high << 32) | user.low
                    cpu += (kernel_ticks + user_ticks) / 10_000_000
            finally:
                _kernel32.CloseHandle(handle)
        return rss, cpu
else:  # pragma: no cover - definitions are selected by platform
    def _windows_snapshot():
        return 0, 0.0


def _run_browser(base_url, payload, profiler, sampler, concurrency,
                 browser_channel, render_probe):
    from playwright.sync_api import sync_playwright

    requested_urls = set()
    texture_responses = []
    page_errors = []
    display_started = None

    def on_request(request):
        if "/texture/" in request.url:
            requested_urls.add(request.url)

    def on_response(response):
        if "/texture/" not in response.url:
            return
        try:
            bytes_served = int(response.headers.get("content-length", "0"))
        except (TypeError, ValueError):
            bytes_served = 0
        texture_responses.append({
            "url": response.url,
            "status": response.status,
            "bytes": bytes_served,
        })

    def on_page_error(error):
        page_errors.append(str(error))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=browser_channel,
            headless=True,
            args=["--use-angle=swiftshader"],
        )
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.set_default_timeout(180000)
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("pageerror", on_page_error)

        navigation_started = time.perf_counter()
        page.goto(base_url, wait_until="load", timeout=180000)
        navigation_seconds = time.perf_counter() - navigation_started

        display_started = time.perf_counter()
        display_start_browser_ms = page.evaluate(
            """async payload => {
              const started = performance.now();
              await window.modViewer.displayMeshPayload(payload);
              return started;
            }""",
            payload)
        display_seconds = time.perf_counter() - display_started
        texture_started = display_started
        network_idle_error = None
        try:
            page.wait_for_load_state("networkidle", timeout=180000)
        except Exception as error:
            network_idle_error = str(error)
        page.wait_for_timeout(250)
        texture_finished = time.perf_counter()
        resource_metrics = page.evaluate("""displayStart => performance
          .getEntriesByType('resource')
          .filter(entry => entry.name.includes('/texture/'))
          .map(entry => ({
            duration: entry.duration,
            response_end: entry.responseEnd - entry.startTime,
            elapsed_since_display: entry.responseEnd - displayStart,
          }))""", display_start_browser_ms)
        response_elapsed = [
            entry["elapsed_since_display"] / 1000.0
            for entry in resource_metrics
            if entry["elapsed_since_display"] >= 0
        ]
        active_meshes = page.evaluate(
            "window.modViewer.activeMeshes.length")
        browser_result = {
            "navigation_seconds": navigation_seconds,
            "browser_display_seconds": display_seconds,
            "first_texture_response_seconds": min(
                response_elapsed, default=None),
            "all_texture_responses_seconds": max(
                response_elapsed, default=None),
            "requested_texture_sources": len(requested_urls),
            "texture_response_count": len(texture_responses),
            "texture_statuses": sorted({
                response["status"] for response in texture_responses}),
            "native_dds_request_count": sum(
                response["url"].split("?", 1)[0].lower().endswith(".dds")
                for response in texture_responses),
            "png_request_count": sum(
                not response["url"].split("?", 1)[0].lower().endswith(".dds")
                for response in texture_responses),
            "dds_bytes_served": sum(
                response["bytes"] for response in texture_responses
                if response["url"].split("?", 1)[0].lower().endswith(".dds")),
            "png_bytes_served": sum(
                response["bytes"] for response in texture_responses
                if not response["url"].split("?", 1)[0].lower().endswith(".dds")),
            "texture_bytes_served": sum(
                response["bytes"] for response in texture_responses),
            "http_resource_max_seconds": max(
                (entry["response_end"] / 1000.0
                 for entry in resource_metrics),
                default=0.0),
            "active_meshes": active_meshes,
            "page_errors": page_errors,
            "network_idle_error": network_idle_error,
        }
        browser.close()

    texture_window = sampler.window(texture_started, texture_finished)
    browser_result.update(texture_window)
    browser_result["rss_cpu_source"] = sampler.source
    browser_result["peak_simultaneous_encodes"] = render_probe.peak
    return browser_result


def _run_once(mod_path, concurrency, browser_channel):
    from app.bridge import api as api_module
    from app.session import edit as edit_session
    from app.mods import metadata, loader as mod_loader
    from app.runtime import server
    from app.bridge.api import ModViewerAPI
    from core import textures

    timings = defaultdict(list)
    profiler = TextureProfiler()
    sampler = _ProcessSampler()
    sampler.start()
    old_hook = textures.set_texture_profile_hook(profiler)
    textures.reset_texture_cache()
    for module, name, label in (
        (api_module, "discover_ini_paths", "ini_discovery"),
        (edit_session, "load_documents", "session_load"),
        (metadata, "load", "metadata_load"),
        (mod_loader, "load_mod", "mod_loader_load_mod"),
        (metadata, "hydrate_textures", "metadata_hydrate_textures"),
        (server, "publish_payload_geometry", "geometry_publication"),
    ):
        _install_timing(module, name, label, timings)

    api = ModViewerAPI()
    api._authorized_folders.add(os.path.normcase(os.path.abspath(mod_path)))
    backend_started = time.perf_counter()
    payload = api.load_mod(mod_path)
    backend_seconds = time.perf_counter() - backend_started
    if not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError(f"Mod load failed: {payload}")

    publication = server.active_texture_publication(mod_path)
    sources = list(publication._sources.values()) if publication else []
    source_paths = {
        os.path.normcase(os.path.abspath(source.path)) for source in sources}
    backend_events = profiler.clear()
    backend_rendered = {
        (event.get("path"), event.get("role"))
        for event in backend_events if event["stage"] == "encoded"
    }
    backend_model_rendered = {
        identity for identity in backend_rendered
        if os.path.normcase(identity[0]) in source_paths
    }

    server._texture_encode_semaphore = threading.BoundedSemaphore(concurrency)
    render_probe = RenderConcurrencyProbe()
    original_server_render = server.render_texture_png
    server.render_texture_png = render_probe.wrap(original_server_render)
    try:
        base_url = server.start()
        browser = _run_browser(
            base_url, payload, profiler, sampler, concurrency,
            browser_channel, render_probe)
    finally:
        server.render_texture_png = original_server_render
        textures.set_texture_profile_hook(old_hook)
        sampler.stop()

    texture_events = profiler.clear()
    profile = profiler.summarize(texture_events)
    browser["texture_profile"] = profile
    browser["actually_rendered"] = profile["actually_rendered"]
    browser["profiled_texture_seconds"] = sum(
        profile["stage_seconds"].values())

    source_bytes = sum(
        os.path.getsize(source.path) for source in sources
        if os.path.isfile(source.path))
    mesh_entries = [
        entry for entry in payload.get("meshes", {}).values()
        if isinstance(entry, dict) and not entry.get("error")]
    component_groups = {
        (entry.get("source"), entry.get("component"))
        for entry in mesh_entries
    }
    texture_pools = payload.get("texture_pools", {})
    pool_option_count = sum(
        len(pool) for pool in texture_pools.values()
        if isinstance(pool, list))
    payload_bytes = len(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return {
        "concurrency": concurrency,
        "backend": {
            "api_load_seconds": backend_seconds,
            "mod_loader_load_mod_seconds": sum(
                timings["mod_loader_load_mod"]),
            "ini_discovery_seconds": sum(timings["ini_discovery"]),
            "session_load_seconds": sum(timings["session_load"]),
            "metadata_load_seconds": sum(timings["metadata_load"]),
            "metadata_hydrate_textures_seconds": sum(
                timings["metadata_hydrate_textures"]),
            "geometry_publication_seconds": sum(
                timings["geometry_publication"]),
            "registered_texture_sources": len(sources),
            "native_dds_sources": sum(source.native_dds for source in sources),
            "png_fallback_sources": sum(not source.native_dds for source in sources),
            "backend_model_texture_renders": len(backend_model_rendered),
            "backend_other_texture_renders": len(backend_rendered)
                - len(backend_model_rendered),
        },
        "assets": {
            "mesh_count": len(mesh_entries),
            "component_count": len(component_groups),
            "texture_pool_count": len(texture_pools),
            "texture_pool_option_count": pool_option_count,
            "texture_key_count": len(payload.get("textures", {})),
            "payload_json_bytes": payload_bytes,
            "texture_source_bytes": source_bytes,
            "geometry_bytes": (payload.get("geometry") or {}).get("length"),
        },
        "browser": browser,
    }


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mod_path", type=Path)
    parser.add_argument("--concurrency", nargs="+", type=int,
                        default=[1, 2, 4],
                        help="semaphore sizes (default: 1 2 4)")
    parser.add_argument("--browser-channel", default="msedge")
    parser.add_argument("--worker", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main():
    args = _parse_args()
    mod_path = str(args.mod_path.resolve())
    if not os.path.isdir(mod_path):
        raise SystemExit(f"Mod folder not found: {mod_path}")
    if any(value <= 0 for value in args.concurrency):
        raise SystemExit("--concurrency values must be positive")

    if args.worker:
        with contextlib.redirect_stdout(sys.stderr):
            result = _run_once(
                mod_path, args.concurrency[0], args.browser_channel)
        print(json.dumps(result, indent=2 if args.pretty else None))
        return

    runs = []
    for concurrency in args.concurrency:
        command = [
            sys.executable, str(Path(__file__).resolve()), mod_path,
            "--worker", "--concurrency", str(concurrency),
            "--browser-channel", args.browser_channel,
        ]
        completed = subprocess.run(
            command, cwd=str(REPO_ROOT), text=True,
            capture_output=True, timeout=600)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        if completed.returncode:
            raise SystemExit(
                f"benchmark worker failed for concurrency {concurrency}: "
                f"{completed.stdout}")
        runs.append(json.loads(completed.stdout))
    output = {
        "mod_path": mod_path,
        "browser_channel": args.browser_channel,
        "runs": runs,
    }
    print(json.dumps(output, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
