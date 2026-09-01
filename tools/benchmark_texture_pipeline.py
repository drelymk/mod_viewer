"""Benchmark the lazy texture pipeline on a real mod.

The default command runs one isolated worker for each requested semaphore size
and prints JSON suitable for comparing PRs. Use ``--repeats 3`` for a
three-sample summary per semaphore size:

    python tools/benchmark_texture_pipeline.py "<mod-folder>"

Each worker starts the real localhost server, loads the mod through the
``pywebview.api.load_mod``-shaped browser bridge backed by ``ModViewerAPI``,
and runs the frontend load path in Edge. It reports backend stages, bridge
transport, geometry transfer, CPU-side Three.js construction, and texture
requests. Playwright bridge timings are harness overhead estimates, not
native pywebview/WebView2 transport measurements. The worker is isolated so
Pillow's cache and browser processes cannot make later concurrency rows
artificially warm. Process isolation does not flush the operating system's
filesystem cache, so repeated buffer reads may be filesystem-cache warm.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import ctypes.wintypes
import json
import os
import statistics
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

_SUMMARY_TIMING_FIELDS = (
    ("backend.api_load_seconds", ("backend", "api_load_seconds")),
    ("backend.authoritative_context_seconds",
     ("backend", "authoritative_context_seconds")),
    ("backend.analyze_mod_inis_seconds",
     ("backend", "analyze_mod_inis_seconds")),
    ("backend.asset_index_load_seconds",
     ("backend", "asset_index_load_seconds")),
    ("backend.asset_enrichment_seconds",
     ("backend", "asset_enrichment_seconds")),
    ("backend.build_mesh_result_seconds",
     ("backend", "build_mesh_result_seconds")),
    ("backend.pack_draw_geometry_seconds",
     ("backend", "pack_draw_geometry_seconds")),
    ("backend.metadata_hydrate_textures_seconds",
     ("backend", "metadata_hydrate_textures_seconds")),
    ("backend.geometry_publication_seconds",
     ("backend", "geometry_publication_seconds")),
    ("browser.browser_display_seconds",
     ("browser", "browser_display_seconds")),
    ("browser.bridge_load_mod_seconds",
     ("browser", "bridge_load_mod_seconds")),
    ("browser.payload_json_encode_probe_seconds",
     ("browser", "payload_json_encode_probe_seconds")),
    ("browser.playwright_bridge_remainder_seconds",
     ("browser", "playwright_bridge_remainder_seconds")),
    ("browser.geometry_fetch_arraybuffer_seconds",
     ("browser", "geometry_fetch_arraybuffer_seconds")),
    ("browser.geometry_http_seconds",
     ("browser", "geometry_http_seconds")),
    ("browser.build_mesh_panel_seconds",
     ("browser", "build_mesh_panel_seconds")),
    ("browser.control_panels_seconds",
     ("browser", "control_panels_seconds")),
    ("browser.refresh_all_seconds",
     ("browser", "refresh_all_seconds")),
    ("browser.fit_to_seconds", ("browser", "fit_to_seconds")),
    ("browser.first_model_frame_seconds",
     ("browser", "first_model_frame_seconds")),
)

_SUMMARY_COUNTER_FIELDS = (
    "asset_index_bytes", "raw_buffer_bytes_read", "final_geometry_bytes",
    "structured_bridge_payload_bytes", "mesh_count", "draw_count",
    "packed_vertices", "packed_indices", "shape_target_bytes",
    "meshes_missing_authored_normals",
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


def _install_load_instrumentation(timings, counters):
    """Install opt-in probes around the existing load pipeline.

    The worker is isolated for each benchmark row, so these probes can wrap
    the real functions without becoming application state or changing the
    production loader contract.
    """
    from app.bridge import mod_preview
    from app.runtime import server
    from app.session import edit as edit_session
    from app.mods import metadata
    from app.mods import loader as mod_loader
    from app.assets import index as asset_index
    from core.geometry import buffers as geometry_buffers
    from core.geometry import mesh_builder

    patches = []

    def timing(module, name, label):
        original = getattr(module, name)

        def measured(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                timings[label].append(time.perf_counter() - started)

        setattr(module, name, measured)
        patches.append((module, name, original))

    timing(mod_preview.ModPreview, "authoritative_context",
           "authoritative_context")
    timing(mod_preview, "discover_ini_paths", "ini_discovery")
    timing(edit_session, "load_documents", "session_load")
    timing(metadata, "load", "metadata_load")
    timing(mod_loader, "analyze_mod_inis", "analyze_mod_inis")
    timing(mod_loader, "enrich_mod_analysis", "asset_enrichment")
    timing(mod_loader, "build_mesh_result", "build_mesh_result")
    timing(metadata, "hydrate_textures", "metadata_hydrate_textures")
    timing(server, "publish_payload_geometry", "geometry_publication")

    original_index_load = asset_index.load_index

    def measured_index_load(*args, **kwargs):
        started = time.perf_counter()
        filename = asset_index.index_path(*args[:2])
        try:
            return original_index_load(*args, **kwargs)
        finally:
            timings["asset_index_load"].append(
                time.perf_counter() - started)
            if os.path.isfile(filename):
                counters["asset_index_bytes"] += os.path.getsize(filename)
                counters["asset_index_files_read"] += 1

    asset_index.load_index = measured_index_load
    patches.append((asset_index, "load_index", original_index_load))

    original_raw = geometry_buffers.BufferStore.raw

    def measured_raw(store, path):
        was_cached = path in store._raw
        result = original_raw(store, path)
        if not was_cached:
            counters["raw_buffer_bytes_read"] += len(result)
            counters["raw_buffer_files_read"] += 1
        return result

    geometry_buffers.BufferStore.raw = measured_raw
    patches.append((geometry_buffers.BufferStore, "raw", original_raw))

    original_pack = mesh_builder.pack_draw_geometry

    def measured_pack(*args, **kwargs):
        started = time.perf_counter()
        try:
            result = original_pack(*args, **kwargs)
            if result is not None:
                counters["packed_draw_count"] += 1
                counters["packed_vertices"] += len(result.positions) // 12
                counters["packed_indices"] += len(result.indices) // 4
                counters["shape_target_bytes"] += sum(
                    len(target.positions)
                    + len(target.low_positions or b"")
                    for target in result.shape_targets)
                counters["meshes_missing_authored_normals"] += int(
                    result.normals is None)
            return result
        finally:
            timings["pack_draw_geometry"].append(
                time.perf_counter() - started)

    mesh_builder.pack_draw_geometry = measured_pack
    patches.append((mesh_builder, "pack_draw_geometry", original_pack))

    return patches


def _restore_patches(patches):
    for module, name, original in reversed(patches):
        setattr(module, name, original)


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


def _run_browser(base_url, api, mod_path, profiler, sampler, concurrency,
                 browser_channel, render_probe):
    from playwright.sync_api import sync_playwright

    requested_urls = set()
    texture_responses = []
    geometry_responses = []
    page_errors = []
    console_errors = []
    payload_holder = {}
    backend_events_holder = []
    bridge_callback_timings = []
    bridge_json_serialization_timings = []

    def on_request(request):
        if "/texture/" in request.url:
            requested_urls.add(request.url)

    def on_response(response):
        if "/texture/" not in response.url and "/geometry/" not in response.url:
            return
        try:
            bytes_served = int(response.headers.get("content-length", "0"))
        except (TypeError, ValueError):
            bytes_served = 0
        item = {
            "url": response.url,
            "status": response.status,
            "bytes": bytes_served,
        }
        if "/texture/" in response.url:
            texture_responses.append(item)
        else:
            geometry_responses.append(item)

    def on_page_error(error):
        page_errors.append(str(error))

    def on_console(message):
        if message.type == "error":
            console_errors.append(message.text)

    def bridge_load_mod(path, disabled_ini=False):
        # This callback models the browser-side API call, but its transport is
        # Playwright's exposed-function channel rather than native pywebview.
        started = time.perf_counter()
        try:
            result = api.load_mod(path, disabled_ini)
        finally:
            bridge_callback_timings.append(time.perf_counter() - started)
            backend_events_holder.extend(profiler.clear())
        payload_holder["payload"] = result
        if isinstance(result, dict):
            serialization_started = time.perf_counter()
            payload_holder["json_bytes"] = len(json.dumps(
                result, ensure_ascii=False,
                separators=(",", ":")).encode("utf-8"))
            bridge_json_serialization_timings.append(
                time.perf_counter() - serialization_started)
        return result

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=browser_channel,
            headless=True,
            args=["--use-angle=swiftshader"],
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 900}, bypass_csp=True)
        page = context.new_page()
        page.set_default_timeout(180000)
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("pageerror", on_page_error)
        page.on("console", on_console)

        page.expose_function("__benchmark_load_mod", bridge_load_mod)
        page.add_init_script(
            """
            globalThis.__modViewerBenchmark = {enabled: true};
            window.pywebview = {api: {
              load_mod: (...args) => window.__benchmark_load_mod(...args),
              has_pending_changes: async () => false,
              get_diagnostics: async () => ({
                summary: {issues: 0, errors: 0, warnings: 0},
                files: {}, issues: [],
              }),
              consume_startup_request: async () => null,
              get_mod_folders: async () => ({folders: []}),
              get_asset_folders: async () => ({folders: []}),
              get_panel_opacity: async () => ({value: 58}),
            }};
            """)

        navigation_started = time.perf_counter()
        page.goto(base_url, wait_until="load", timeout=180000)
        navigation_seconds = time.perf_counter() - navigation_started

        renderer_available = True
        bootstrap_error = None
        try:
            page.wait_for_function(
                "(window.modViewer && typeof window.modViewer.switchMod === 'function')"
                " || document.getElementById('renderer-error')?.classList.contains('show')",
                timeout=30000)
        except Exception as error:
            renderer_available = False
            bootstrap_error = (
                f"{error}; page_errors={page_errors}; "
                f"console_errors={console_errors}")
        else:
            renderer_available = bool(page.evaluate(
                "window.modViewer && typeof window.modViewer.switchMod === 'function'"))
            if not renderer_available:
                bootstrap_error = page.locator("#renderer-error").inner_text()
        frontend_started = time.perf_counter()
        if renderer_available:
            bridge_result = page.evaluate(
                """async path => {
                  const loaded = await window.modViewer.switchMod(path);
                  // Let the probe's first requestAnimationFrame callback run
                  // before returning its snapshot to Python.
                  await new Promise(resolve => requestAnimationFrame(resolve));
                  return {
                    loaded,
                    timing: window.modViewer.getLoadBenchmark(),
                  };
                }""",
                mod_path)
        else:
            # Headless Edge can expose navigator.gpu while offering no usable
            # adapter. Keep the backend/bridge and CPU-side Three.js creation
            # measurable, but label this path because it cannot render a GPU
            # frame. A WebGPU-capable run always uses switchMod above.
            bridge_result = page.evaluate(
                """async path => {
                  const flow = await import('./js/app/model-flow.js');
                  const benchmark = await import('./js/app/load-benchmark.js');
                  const {viewerState} = await import('./js/app/state.js');
                  viewerState.currentModPath = path;
                  viewerState.currentSource = {kind: 'mod', path};
                  benchmark.beginLoadBenchmark();
                  const data = await benchmark.measureAsyncLoadStage(
                    'bridge_load_mod', () => window.pywebview.api.load_mod(path));
                  if (data?.error) {
                    benchmark.finishLoadBenchmark({success: false, error: data.error});
                    return {
                      loaded: false,
                      timing: benchmark.getLoadBenchmark(),
                    };
                  }
                  await flow.displayMeshPayload(data);
                  benchmark.finishLoadBenchmark({
                    success: true,
                    active_meshes: (await import('./js/mesh/visibility.js')).activeMeshes.length,
                    payload_meshes: Object.keys(data?.meshes || {}).length,
                  });
                  await new Promise(resolve => requestAnimationFrame(resolve));
                  return {
                    loaded: true,
                    timing: benchmark.getLoadBenchmark(),
                  };
                }""",
                mod_path)
        frontend_elapsed = time.perf_counter() - frontend_started
        frontend = bridge_result.get("timing") or {}
        texture_started = frontend_started
        network_idle_error = None
        try:
            page.wait_for_load_state("networkidle", timeout=180000)
        except Exception as error:
            network_idle_error = str(error)
        # Without a WebGPU adapter Three.js still queues texture requests,
        # but there is no render loop to keep the page alive while the local
        # PNG workers finish. Give those requests a short settling window so
        # the benchmark does not close the client socket mid-response.
        page.wait_for_timeout(5000 if not renderer_available else 250)
        texture_finished = time.perf_counter()
        resource_metrics = page.evaluate("""loadStart => performance
          .getEntriesByType('resource')
          .filter(entry => entry.name.includes('/texture/'))
          .map(entry => ({
            duration: entry.duration,
            response_end: entry.responseEnd - entry.startTime,
            elapsed_since_load: entry.responseEnd - loadStart,
          }))""", frontend.get("started", 0))
        response_elapsed = [
            entry["elapsed_since_load"] / 1000.0
            for entry in resource_metrics
            if entry["elapsed_since_load"] >= 0
        ]
        active_meshes = (page.evaluate("window.modViewer.activeMeshes.length")
                         if renderer_available else frontend.get(
                             "active_meshes", 0))
        browser_result = {
            "navigation_seconds": navigation_seconds,
            "browser_display_seconds": frontend.get(
                "total_seconds", frontend_elapsed),
            "frontend": frontend,
            "frontend_evaluate_seconds": frontend_elapsed,
            "bridge_load_mod_seconds": frontend.get("stages", {}).get(
                "bridge_load_mod"),
            "geometry_fetch_arraybuffer_seconds": frontend.get(
                "stages", {}).get("geometry_fetch_arraybuffer"),
            "build_mesh_panel_seconds": frontend.get("stages", {}).get(
                "build_mesh_panel"),
            "control_panels_seconds": frontend.get("stages", {}).get(
                "control_panels"),
            "refresh_all_seconds": frontend.get("stages", {}).get(
                "refresh_all"),
            "fit_to_seconds": frontend.get("stages", {}).get("fit_to"),
            "first_model_frame_seconds": frontend.get(
                "first_model_frame_seconds"),
            "renderer_available": renderer_available,
            "renderer_bootstrap_error": bootstrap_error,
            "benchmark_path": "switchMod" if renderer_available
                else "displayMeshPayload_without_webgpu",
            "bridge_callback_seconds": sum(bridge_callback_timings),
            "payload_json_encode_probe_seconds": sum(
                bridge_json_serialization_timings),
            "playwright_bridge_remainder_seconds": max(
                0.0,
                frontend.get("stages", {}).get("bridge_load_mod", 0.0)
                - sum(bridge_callback_timings)
                - sum(bridge_json_serialization_timings)),
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
            "geometry_response_count": len(geometry_responses),
            "geometry_bytes_served": sum(
                response["bytes"] for response in geometry_responses),
            "geometry_http_seconds": max(
                (entry["response_end"] / 1000.0
                 for entry in page.evaluate("""performance
                   .getEntriesByType('resource')
                   .filter(entry => entry.name.includes('/geometry/'))
                   .map(entry => ({response_end: entry.responseEnd - entry.startTime}))""")),
                default=0.0),
            "http_resource_max_seconds": max(
                (entry["response_end"] / 1000.0
                 for entry in resource_metrics),
                default=0.0),
            "active_meshes": active_meshes,
            "page_errors": page_errors,
            "console_errors": console_errors,
            "network_idle_error": network_idle_error,
        }
        if not bridge_result.get("loaded"):
            raise RuntimeError(
                f"Frontend mod load failed: {frontend.get('error') or bridge_result}")
        context.close()
        browser.close()

    texture_window = sampler.window(texture_started, texture_finished)
    browser_result.update(texture_window)
    browser_result["rss_cpu_source"] = sampler.source
    browser_result["peak_simultaneous_encodes"] = render_probe.peak
    return browser_result, payload_holder.get("payload"), backend_events_holder


def _run_once(mod_path, concurrency, browser_channel):
    from app.runtime import server
    from app.bridge.api import ModViewerAPI
    from core import textures

    timings = defaultdict(list)
    counters = defaultdict(int)
    profiler = TextureProfiler()
    sampler = _ProcessSampler()
    sampler.start()
    old_hook = textures.set_texture_profile_hook(profiler)
    textures.reset_texture_cache()
    api = ModViewerAPI()
    api._access.remember_mod_picker_selection(mod_path)
    patches = _install_load_instrumentation(timings, counters)

    server._texture_encode_semaphore = threading.BoundedSemaphore(concurrency)
    render_probe = RenderConcurrencyProbe()
    original_server_render = server.render_texture_png
    server.render_texture_png = render_probe.wrap(original_server_render)
    try:
        base_url = server.start()
        browser, payload, backend_events = _run_browser(
            base_url, api, mod_path, profiler, sampler, concurrency,
            browser_channel, render_probe)
    finally:
        server.render_texture_png = original_server_render
        textures.set_texture_profile_hook(old_hook)
        sampler.stop()
        _restore_patches(patches)

    if not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError(f"Mod load failed: {payload}")

    publication = server.active_texture_publication(mod_path)
    sources = list(publication._sources.values()) if publication else []
    source_paths = {
        os.path.normcase(os.path.abspath(source.path)) for source in sources}
    backend_rendered = {
        (event.get("path"), event.get("role"))
        for event in backend_events if event["stage"] == "encoded"
    }
    backend_model_rendered = {
        identity for identity in backend_rendered
        if os.path.normcase(identity[0]) in source_paths
    }

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
    counters["mesh_count"] = len(mesh_entries)
    counters["draw_count"] = len(mesh_entries)
    counters["final_geometry_bytes"] = (
        (payload.get("geometry") or {}).get("length", 0)
        if isinstance(payload.get("geometry"), dict) else 0)
    counters["structured_bridge_payload_bytes"] = payload_bytes
    return {
        "concurrency": concurrency,
        "backend": {
            "api_load_seconds": browser.get("bridge_callback_seconds", 0.0),
            "authoritative_context_seconds": sum(
                timings["authoritative_context"]),
            "ini_discovery_seconds": sum(timings["ini_discovery"]),
            "session_load_seconds": sum(timings["session_load"]),
            "metadata_load_seconds": sum(timings["metadata_load"]),
            "analyze_mod_inis_seconds": sum(timings["analyze_mod_inis"]),
            "asset_index_load_seconds": sum(timings["asset_index_load"]),
            "asset_enrichment_seconds": sum(timings["asset_enrichment"]),
            "build_mesh_result_seconds": sum(timings["build_mesh_result"]),
            "pack_draw_geometry_seconds": sum(
                timings["pack_draw_geometry"]),
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
            **counters,
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


def _summary_stats(values):
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _nested_value(value, path):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _summarize_runs(runs, concurrency):
    selected = [run for run in runs if run["concurrency"] == concurrency]
    timings = {}
    for label, path in _SUMMARY_TIMING_FIELDS:
        values = [
            value for run in selected
            if (value := _nested_value(run, path)) is not None
        ]
        if values:
            timings[label] = _summary_stats(values)

    counters = {}
    for field in _SUMMARY_COUNTER_FIELDS:
        values = [
            value for run in selected
            if (value := run.get("backend", {}).get(field)) is not None
        ]
        if values:
            counters[field] = _summary_stats(values)
    return {
        "concurrency": concurrency,
        "repeats": len(selected),
        "timings": timings,
        "counters": counters,
    }


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mod_path", type=Path)
    parser.add_argument("--concurrency", nargs="+", type=int,
                        default=[1, 2, 4],
                        help="semaphore sizes (default: 1 2 4)")
    parser.add_argument("--repeats", type=int, default=1,
                        help="isolated samples per semaphore size (default: 1)")
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
    if args.repeats <= 0:
        raise SystemExit("--repeats must be positive")

    if args.worker:
        with contextlib.redirect_stdout(sys.stderr):
            result = _run_once(
                mod_path, args.concurrency[0], args.browser_channel)
        print(json.dumps(result, indent=2 if args.pretty else None))
        return

    runs = []
    for concurrency in args.concurrency:
        for repeat in range(1, args.repeats + 1):
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
                    f"benchmark worker failed for concurrency {concurrency}, "
                    f"repeat {repeat}: {completed.stdout}")
            result = json.loads(completed.stdout)
            result["repeat"] = repeat
            runs.append(result)
    output = {
        "mod_path": mod_path,
        "browser_channel": args.browser_channel,
        "repeats": args.repeats,
        "os_filesystem_cache_flushed": False,
        "notes": [
            "Process isolation does not flush the operating system filesystem "
            "cache.",
            "Playwright bridge timings are harness estimates, not native "
            "pywebview/WebView2 measurements.",
        ],
        "runs": runs,
        "summary": [
            _summarize_runs(runs, concurrency)
            for concurrency in args.concurrency
        ],
    }
    print(json.dumps(output, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
