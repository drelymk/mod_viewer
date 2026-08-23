"""Real-server Edge smoke coverage for frontend state transitions."""

import base64
import copy
import io
import json
import math
import struct
import zlib

import pytest
from PIL import Image

from app import paths, server
from core.material_profiles import material_profile_for

playwright = pytest.importorskip("playwright.sync_api")

_PNG_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "ScLkWQAAAABJRU5ErkJggg==")
_MOD_LIBRARY = "fixture-mod-library"


def _f32(*values):
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()


def _u32(*values):
    return base64.b64encode(struct.pack(f"<{len(values)}I", *values)).decode()


def _flat_png_uri(rgba, size=4):
    """Build a real multi-pixel PNG so WebGPU texture mip sampling is tested."""
    raw = b"".join(b"\x00" + bytes(rgba) * size for _ in range(size))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _banded_png_uri(rgba_columns, height=4):
    """Build a nearest-column diagnostic texture for per-pixel debug tests."""
    width = len(rgba_columns)
    raw_row = b"".join(bytes(rgba) for rgba in rgba_columns)
    raw = b"".join(b"\x00" + raw_row for _ in range(height))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _bc7_dds_bytes(width=8, height=4, mip_count=2):
    data = bytearray(148)
    data[:4] = b"DDS "
    struct.pack_into("<I", data, 4, 124)
    struct.pack_into("<II", data, 12, height, width)
    struct.pack_into("<I", data, 28, mip_count)
    struct.pack_into("<I", data, 76, 32)
    struct.pack_into("<II", data, 80, 4, int.from_bytes(b"DX10", "little"))
    struct.pack_into("<IIIII", data, 128, 98, 3, 0, 1, 0)
    w, h = width, height
    for level in range(mip_count):
        data.extend(bytes([level + 1]) * (((w + 3) // 4) * ((h + 3) // 4) * 16))
        w, h = max(1, w // 2), max(1, h // 2)
    return bytes(data)


def _payload(label="A"):
    texture_pool = [
        {"tex_key": f"diffuse::{label}-one.png", "label": f"{label} one"},
        {"tex_key": f"diffuse::{label}-two.png", "label": f"{label} two"},
    ]
    return {
        "meshes": {
            f"Body-{label}-0": {
                "component": f"Body {label}",
                "drawindexed": [3, 0, 0],
                "pos": _f32(0, 0, 0, 1, 0, 0, 0, 1, 0),
                "idx": _u32(0, 1, 2),
                "tex_key": texture_pool[0]["tex_key"],
                "texture_pool_id": "p0",
                "texture_variants": [{
                    "conditions": [[{"var": "menu", "value": "1", "negate": False}]],
                    "tex_key": texture_pool[1]["tex_key"],
                }],
                "shape_targets": [{
                    "var": "shape",
                    "pos": _f32(0, 0, 0, 1.2, 0, 0, 0, 1.2, 0),
                }],
                "conditions": [],
                "sources": [{"ini": f"{label}.ini", "line": 10}],
            },
        },
        "texture_pools": {"p0": texture_pool},
        "textures": {option["tex_key"]: _PNG_URI for option in texture_pool},
        "controls": {
            "toggles": {
                f"Key{label}": {
                    "name": f"Toggle {label}", "ini": f"{label}.ini",
                    "section": f"Key{label}", "wired": True,
                    "vars": [{"var": "toggle", "default": "0", "values": ["0", "1"]}],
                },
            },
            "menu": {
                "menu": {"name": "Menu", "slot": 1, "var": "menu",
                         "default": "0", "values": ["0", "1"], "effects": []},
                "shape": {"name": "Shape", "var": "shape", "kind": "shape_slider",
                          "default": "0", "min": "0", "max": "1", "step": "0.1"},
            },
            "present": {"target_inis": []},
        },
        "state": {"rules": [], "defaults": {"toggle": "0", "menu": "0", "shape": "0"}},
        "geometry": None,
        "metadata": {"mesh_names": {}, "material_profiles": {}},
        "health": {"summary": {"issues": 0, "errors": 0}, "files": {}, "issues": []},
    }


def _present_payload(label="Present"):
    payload = _payload(label)
    payload["controls"]["present"] = {
        "target_inis": [{
            "value": f"{label}.ini", "label": f"{label}.ini",
            "vars": ["toggle"], "has_present": True,
        }],
        "item": {
            "inis": [f"{label}.ini"], "target_inis": [],
            "key": "ctrl p", "key_raw": "ctrl p", "back": "",
            "vars": [{"var": "toggle", "values": ["0", "1"],
                      "default": "0"}],
            "capture_vars": ["toggle"], "count": 2, "aligned": True,
            "missing_inis": [], "sync_error": None,
            "names": ["Present 1", "Present 2"],
        },
    }
    return payload


def _dxt1_vertical_gradient():
    """One DXT1 block with red top rows and blue bottom rows."""
    data = bytearray(136)
    data[:4] = b"DDS "
    struct.pack_into("<I", data, 4, 124)
    struct.pack_into("<II", data, 12, 4, 4)
    struct.pack_into("<I", data, 76, 32)
    struct.pack_into("<II", data, 80, 4, int.from_bytes(b"DXT1", "little"))
    struct.pack_into("<HHI", data, 128, 0xF800, 0x001F, 0x55550000)
    return bytes(data)


def _parity_payload(uri):
    payload = _payload("Parity")
    entry = payload["meshes"]["Body-Parity-0"]
    entry["drawindexed"] = [6, 0, 0]
    entry["pos"] = _f32(-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
    # Exercise the sampler outside the nominal range so DDS and PNG transport
    # must agree on their default clamp-to-edge wrapping.
    entry["uv"] = _f32(-0.25, -0.25, 1.25, -0.25,
                       1.25, 1.25, -0.25, 1.25)
    entry["idx"] = _u32(0, 1, 2, 0, 2, 3)
    payload["textures"] = {"diffuse::Parity-one.png": uri}
    return payload


def _packed_material_payload(profile_id="zzz:zzmi"):
    payload = _payload("Packed")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    entry["light_map_key"] = "light_map::Packed-light.png"
    entry["material_map_key"] = "material_map::Packed-material.png"
    entry["normal_data_key"] = "normal_data::Packed-normal.png"
    payload["textures"] = {
        "diffuse::Packed-one.png": _PNG_URI,
        "light_map::Packed-light.png": _PNG_URI,
        "material_map::Packed-material.png": _PNG_URI,
        "normal_data::Packed-normal.png": _PNG_URI,
    }
    profile_args = {
        "zzz:zzmi": ("zzz", "zzmi"),
        "genshin:gimi": ("genshin", "gimi"),
        "wuwa:rabbitfx": ("wuwa", "rabbitfx"),
        "wuwa:rabbitfx:body": ("wuwa", "rabbitfx", "body"),
        "wuwa:raw": ("wuwa", "raw"),
    }
    profile = (material_profile_for(*profile_args[profile_id]).to_metadata()
               if profile_id in profile_args
               else material_profile_for("unknown", "unknown").to_metadata())
    entry["material_kind"] = "body"
    entry["material_kind_reliable"] = False
    entry["material_profile_id"] = profile["id"]
    payload["metadata"]["material_profiles"] = {profile["id"]: profile}
    return payload


def _construction_failure_payload():
    payload = _payload("Broken")
    payload["textures"] = {
        "diffuse::Broken-one.png":
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLkWQAAAABJRU5ErkJggg==",
    }
    # Keep one valid mesh first so the frontend has already allocated scene,
    # material and texture state when the next mesh construction fails.
    payload["meshes"]["Broken-after-first"] = {
        "component": "Broken after",
        "drawindexed": [3, 0, 0],
        "pos": "!",  # invalid base64: decodeF32 must reject this buffer
        "idx": _u32(0, 1, 2),
        "conditions": [],
        "sources": [{"ini": "Broken.ini", "line": 20}],
    }
    return payload


def _state_sync_payload():
    payload = _payload("Sync")
    payload["controls"]["menu"]["sibling"] = {
        "name": "Sibling", "slot": 2, "var": "sibling",
        "default": "0", "values": ["0", "1"], "effects": [],
    }
    payload["controls"]["menu"]["menu"]["effects"] = [
        {"var": "sibling", "value": "1"},
    ]
    payload["controls"]["present"] = {
        "target_inis": [],
        "item": {
            "key": "p", "back": "", "count": 2, "names": ["Base", "Alt"],
            "vars": [
                {"var": "toggle", "values": ["0", "1"]},
                {"var": "menu", "values": ["0", "1"]},
            ],
            "capture_vars": ["toggle", "menu"], "missing_inis": [],
        },
    }
    payload["state"] = {
        "defaults": {"toggle": "0", "menu": "0", "sibling": "0", "shape": "0"},
        "rules": [{
            "conditions": [[{"var": "toggle", "value": "1", "negate": False}]],
            "var": "menu", "value": "1",
        }],
    }
    return payload


def _source_payload():
    payload = _payload("Source")
    template = next(iter(payload["meshes"].values()))
    payload["meshes"] = {}
    for key, source in [("BodyRoot-0", "Root.ini"),
                        ("BodyNested-0", "variants/sub"),
                        ("BodyNested-1", "variants/sub")]:
        entry = copy.deepcopy(template)
        entry["component"] = "Body"
        entry["source"] = source
        entry["texture_pool_id"] = "p0" if source == "Root.ini" else "p1"
        payload["meshes"][key] = entry
    payload["texture_pools"] = {
        "p0": copy.deepcopy(payload["texture_pools"]["p0"]),
        "p1": copy.deepcopy(payload["texture_pools"]["p0"]),
    }

    first_toggle = next(iter(payload["controls"]["toggles"].values()))
    payload["controls"]["toggles"] = {
        "KeyRoot": {**copy.deepcopy(first_toggle), "name": "Duplicate", "source": "Root.ini"},
        "KeyNested": {**copy.deepcopy(first_toggle), "name": "Duplicate", "source": "variants/sub"},
    }
    menu = payload["controls"]["menu"]
    menu["menu"]["source"] = "Root.ini"
    menu["shape"]["source"] = "variants/sub"
    return payload


@pytest.fixture(scope="session")
def frontend_url():
    if not paths.has_vendored_three():
        pytest.skip("frontend smoke tests require the vendored Three.js assets")
    return server.start()


@pytest.fixture(scope="session")
def edge_browser():
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(channel="msedge", headless=True)
        except playwright.Error:
            pytest.skip("frontend smoke tests require a compatible browser runtime")
        yield browser
        browser.close()


def _page(edge_browser, frontend_url, responses, pending=None, picks=None,
          mod_folders=None, subfolders=None, diagnostics=None, panel_opacity=58,
          panel_opacity_api=True, asset_folders=None, asset_subfolders=None):
    # Playwright's wait_for_function uses eval internally. Bypass the app's
    # production CSP only in this isolated test context so behavioral waits
    # do not require weakening the served application's policy.
    context = edge_browser.new_context(bypass_csp=True)
    state = {
        "responses": responses,
        "pending": pending or {},
        "picks": picks or [],
        "modFolders": mod_folders or [],
        "subfolders": subfolders or {},
        "assetFolders": asset_folders or [],
        "assetSubfolders": asset_subfolders or {},
        "diagnostics": diagnostics or {
            "summary": {"issues": 0, "errors": 0}, "files": {}, "issues": []},
        "panelOpacity": panel_opacity,
        "panelOpacityApi": panel_opacity_api,
        "calls": {"loadMod": [], "listSubfolders": [], "listAssetSubfolders": [],
                   "selectAssetFolder": [],
                   "rebuildAssetIndex": [],
                   "discardChanges": [], "switches": [], "diagnostics": [],
                   "panelOpacity": [], "presentState": [],
                   "controlState": [], "meshSemantics": [],
                   "deleteToggle": [], "exportChanges": []},
    }
    encoded_state = json.dumps(json.dumps(state))
    context.add_init_script(
        """
        {
          const state = window.__fakeApi = JSON.parse(__STATE__);
          const copy = value => value == null ? value : structuredClone(value);
          const loadWaiters = {};
          state.releaseLoad = path => {
            state.blockLoads = state.blockLoads || {};
            state.blockLoads[path] = false;
            (loadWaiters[path] || []).splice(0).forEach(resolve => resolve());
          };
          window.pywebview = { api: {
            select_folder: async () => {
              const path = state.nextPath || null;
              state.nextPath = null;
              return path;
            },
            select_asset_folder: async () => {
              const path = state.nextPath || null;
              state.nextPath = null;
              state.calls.selectAssetFolder.push(path);
              return path;
            },
            load_mod: async path => {
              state.calls.loadMod.push(path);
              if (state.blockLoads?.[path]) {
                await new Promise(resolve => {
                  (loadWaiters[path] ||= []).push(resolve);
                });
              }
              return copy(state.responses[path]);
            },
            get_present_state: async path => {
              state.calls.presentState.push(path);
              return copy({present: state.responses[path]?.controls?.present || {
                target_inis: [], item: null,
              }});
            },
            get_control_state: async path => {
              state.calls.controlState.push(path);
              const payload = state.responses[path] || {};
              return copy({
                controls: payload.controls || {},
                state: payload.state || {rules: [], defaults: {}},
              });
            },
            get_mesh_semantics: async path => {
              state.calls.meshSemantics.push(path);
              const payload = state.responses[path] || {};
              const meshes = payload.meshSemantics || Object.fromEntries(
                Object.entries(payload.meshes || {}).map(([name, entry]) => [name, {
                  conditions: entry.conditions || [],
                  sources: entry.sources || [],
                }])
              );
              return copy({
                meshes,
                asset_resolution: payload.meshSemanticsAssetResolution
                  ?? payload.asset_resolution ?? null,
              });
            },
            delete_toggle: async (path, ini, section) => {
              state.calls.deleteToggle.push([path, ini, section]);
              return copy({ok: true, result: {}});
            },
            export_changes: async path => {
              state.calls.exportChanges.push(path);
              state.pending[path] = false;
              return copy({saved: [], failed: []});
            },
            has_pending_changes: async path => !!state.pending[path],
            discard_changes: async path => {
              state.calls.discardChanges.push(path);
              state.pending[path] = false;
            },
            get_mod_folders: async () => copy({folders: state.modFolders}),
            get_panel_opacity: async () => ({value: state.panelOpacity}),
            set_panel_opacity: async value => {
              state.panelOpacity = value;
              state.calls.panelOpacity.push(value);
              return {value};
            },
            add_mod_folder: async (name, path) => {
              state.modFolders.push({name, path, exists: true});
              return copy({folders: state.modFolders});
            },
            edit_mod_folder: async (original, name, path) => {
              const item = state.modFolders.find(folder => folder.path === original);
              if (item) Object.assign(item, {name, path, exists: true});
              return copy({folders: state.modFolders});
            },
            delete_mod_folder: async path => {
              state.modFolders = state.modFolders.filter(folder => folder.path !== path);
              return copy({folders: state.modFolders});
            },
            list_subfolders: async path => {
              state.calls.listSubfolders.push(path);
              return copy({folders: state.subfolders[path] || []});
            },
            get_asset_folders: async () => copy({folders: state.assetFolders}),
            add_asset_folder: async (type, path) => {
              state.assetFolders.push({type, path, enabled: true, exists: true});
              return copy({folders: state.assetFolders});
            },
            edit_asset_folder: async (original, type, path) => {
              const item = state.assetFolders.find(folder => folder.path === original);
              if (item) Object.assign(item, {type, path, exists: true});
              return copy({folders: state.assetFolders});
            },
            delete_asset_folder: async path => {
              state.assetFolders = state.assetFolders.filter(folder => folder.path !== path);
              return copy({folders: state.assetFolders});
            },
            set_asset_folder_enabled: async (path, enabled) => {
              const item = state.assetFolders.find(folder => folder.path === path);
              if (item) item.enabled = enabled;
              return copy({folders: state.assetFolders});
            },
            rebuild_asset_index: async path => {
              state.calls.rebuildAssetIndex.push(path);
              return copy({folders: state.assetFolders});
            },
            list_asset_subfolders: async path => {
              state.calls.listAssetSubfolders.push(path);
              return copy({folders: state.assetSubfolders[path] || []});
            },
            get_diagnostics: async path => {
              state.calls.diagnostics.push(path);
              return copy(state.diagnostics);
            },
            list_toggle_source_inis: async () => [{value: 'A.ini', label: 'A.ini'}],
            list_ini_files: async () => [{value: 'A.ini', label: 'A.ini', dirty: false}],
            get_ini_text: async () => ({ini: 'A.ini', text: '[Test]\\nkey = 1\\n', dirty: false}),
            update_ini_text: async () => ({pending: true}),
            save_mesh_textures: async () => ({}),
            save_mesh_names: async () => ({}),
            save_component_material_kind: async () => ({}),
            pick_texture_file: async () => copy(state.picks.shift() || null),
            get_record_positions: async () => ({positions: 2, vars: ['toggle']}),
          }};
          if (!state.panelOpacityApi) {
            delete window.pywebview.api.get_panel_opacity;
            delete window.pywebview.api.set_panel_opacity;
          }
        }
        """.replace("__STATE__", encoded_state),
    )
    page = context.new_page()
    page.goto(frontend_url)
    page.wait_for_function("window.modViewer !== undefined")
    return context, page


def _open(page, path):
    page.evaluate("path => { window.__fakeApi.nextPath = path; }", path)
    page.locator("#open-btn").click()


def _open_library(page):
    page.locator("#mod-library-tab").click()
    page.locator("#mod-folder-panel:not([hidden])").wait_for()


def _sample_mesh_pixel(page):
    return _sample_mesh_pixel_at(page, 0.25, 0.25)


def _sample_mesh_pixel_at(page, x, y):
    point = page.evaluate("""
      async ({x, y}) => {
        const THREE = await import('three');
        const {camera, renderer} = await import('./js/scene.js');
        const mesh = window.modViewer.activeMeshes[0];
        const projected = new THREE.Vector3(x, y, 0)
          .applyMatrix4(mesh.matrixWorld).project(camera);
        const rect = renderer.domElement.getBoundingClientRect();
        return {
          x: Math.round(rect.left + (projected.x + 1) * rect.width / 2),
          y: Math.round(rect.top + (1 - projected.y) * rect.height / 2),
        };
      }
    """, {"x": x, "y": y})
    image = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    return image.getpixel((point["x"], point["y"]))


def test_webgpu_startup_uses_actual_webgpu_backend(edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#open-btn:not([disabled])").wait_for(timeout=10000)
        page.wait_for_function(
            "import('./js/scene.js').then(({renderer}) => renderer.currentSamples === 4)")
        state = page.evaluate("""async () => {
          const {renderer} = await import('./js/scene.js');
          return {
            isWebGPURenderer: renderer.isWebGPURenderer === true,
            isWebGPUBackend: renderer.backend?.isWebGPUBackend === true,
            compatibilityMode: renderer.backend?.compatibilityMode,
            samples: renderer.samples,
            animationLoop: renderer.getAnimationLoop() !== null,
            outputColorSpace: renderer.outputColorSpace,
            toneMapping: renderer.toneMapping,
            clearAlpha: renderer.getClearAlpha(),
          };
        }""")
        assert state["isWebGPURenderer"]
        assert state["isWebGPUBackend"]
        assert state["compatibilityMode"] is False
        assert state["samples"] == 4
        assert not state["animationLoop"]
        assert state["outputColorSpace"] == "srgb"
        assert state["toneMapping"] == 0
        assert state["clearAlpha"] == 1
    finally:
        context.close()


def test_asset_identity_and_texture_provenance_are_diagnostic_only(
        edge_browser, frontend_url):
    payload = _payload("Asset")
    entry = payload["meshes"]["Body-Asset-0"]
    entry["asset_binding"] = {
        "status": "exact", "component_status": "exact",
        "range_status": "exact", "asset_type": "GIMI",
        "asset": "Alice", "component_name": "Body",
        "classification": "B", "geometry_hash": "73c8cae2",
        "first_index": 43845, "index_count": 24,
    }
    entry["texture_resolution"] = {
        "diffuse": "mod_semantic",
        "normal_map": "asset_original_fallback",
        "light_map": "mod_texture_hash",
    }
    entry["asset_slot_evidence"] = [{
        "resource": "ps-t1", "texture_hash": "11111111",
        "vs_hash": "aaaaaaaa", "ps_hash": "bbbbbbbb",
    }]
    payload["asset_resolution"] = {
        "total_draws": 1, "exact_draws": 1, "partial_draws": 0,
        "ambiguous_draws": 0, "unmatched_draws": 0,
        "index_unavailable_draws": 0, "index_status": "ready",
        "components": [],
    }
    context, page = _page(edge_browser, frontend_url, {"Asset": payload})
    try:
        _open(page, "Asset")
        page.locator(".draw-item").wait_for()
        assert page.locator(".asset-draw-label").inner_text() == "Alice · Body B"
        page.locator("#health-btn").click()
        page.locator("#health-modal-backdrop.show").wait_for()
        assert page.locator("#health-asset-summary").inner_text() == (
            "Asset resolution: 1 / 1 draws exact")
        page.locator("#health-close").click()

        summary = page.evaluate("""async () => {
          const {summarizeAssetBindings} = await import('./js/asset-diagnostics.js');
          return summarizeAssetBindings([
            {asset_binding: {
              status: 'exact', component_status: 'exact', range_status: 'exact',
              asset: 'Alice', component_name: 'Body', classification: 'A',
            }},
            {asset_binding: {
              status: 'exact', component_status: 'exact', range_status: 'exact',
              asset: 'Alice', component_name: 'Body', classification: 'A',
            }},
            {asset_binding: {
              status: 'not_found', component_status: 'not_found',
              range_status: 'unknown',
            }},
          ]);
        }""")
        assert summary["status"] == "partial"
        assert summary["assets"] == ["Alice"]
        assert summary["matchedDraws"] == 2
        assert summary["rangesVary"] is False

        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        inspector = page.locator("#inspector-content")
        assert inspector.locator(".inspector-asset-section").inner_text() == (
            "ASSET MATCH\nAsset\nAlice\nType\nGIMI\nComponent\nBody\nObject\nB\n"
            "Geometry hash\n73c8cae2\nRange\n43845 / 24\nComponent match\nExact\n"
            "Range match\nExact\nMatch\nExact")
        assert "Normal (automatic)\nAsset fallback" in inspector.inner_text()
        assert "ps-t1" in inspector.locator(".inspector-slot-section").inner_text()
        assert "Role\nUnknown" in inspector.locator(
            ".inspector-slot-section").inner_text()

        page.locator(".inspector-texture-option", has_text="Asset two").click()
        assert inspector.locator(
            '[data-provenance-kind="viewer"] .inspector-value').inner_text() == (
                "Viewer override (diffuse::Asset-two.png)")
        page.locator("#health-btn").click()
        page.locator("#health-modal-backdrop.show").wait_for()
        assert page.locator("#health-asset-summary").inner_text() == (
            "Asset resolution: 1 / 1 draws exact")
        page.locator("#health-close").click()

        page.locator(".group-hdr .group-name").first.click()
        assert "1 of 1 matched" in inspector.inner_text()
    finally:
        context.close()


def test_left_dock_tabs_toggle_and_keep_aria_state(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {}, asset_folders=[])
    try:
        page.locator("#mod-library-tab").click()
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#mod-folder-list").is_hidden()
        assert page.locator("#mod-folder-empty").is_visible()
        assert page.locator("#mod-library-tab").get_attribute("aria-selected") == "true"
        page.locator("#assets-tab").click()
        assert page.locator("#mod-folder-panel").is_hidden()
        assert page.locator("#asset-folder-panel").is_visible()
        assert page.locator("#asset-folder-list").is_hidden()
        assert page.locator("#asset-folder-empty").is_visible()
        assert page.locator("#assets-tab").get_attribute("aria-expanded") == "true"
        page.locator("#assets-tab").click()
        assert page.locator("#asset-folder-panel").is_hidden()
        assert page.locator("#left-panel-container").is_hidden()
        assert page.locator("#left-dock-tabs").is_visible()
        assert page.locator(".left-dock-tabs .active").count() == 0
        assert all(value == "false" for value in page.locator(
            ".left-dock-tabs > button").evaluate_all(
                "buttons => buttons.map(button => button.getAttribute('aria-selected'))"))
    finally:
        context.close()


def test_asset_diagnostics_refresh_with_semantic_updates(
        edge_browser, frontend_url):
    payload = _payload("Semantic")
    entry = payload["meshes"]["Body-Semantic-0"]
    entry["asset_binding"] = {
        "status": "exact", "component_status": "exact",
        "range_status": "exact", "asset_type": "GIMI",
        "asset": "Alice", "component_name": "Body",
        "geometry_hash": "73c8cae2",
    }
    entry["texture_resolution"] = {"normal_map": "asset_original_fallback"}
    payload["asset_resolution"] = {
        "total_draws": 1, "exact_draws": 1, "index_status": "ready",
        "configured_roots": 1, "ready_roots": 1,
    }
    context, page = _page(edge_browser, frontend_url, {"Semantic": payload})
    try:
        _open(page, "Semantic")
        page.locator(".draw-item").wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        assert "Alice" in page.locator(".inspector-asset-section").inner_text()

        page.evaluate("""() => {
          const state = window.__fakeApi;
          const entry = state.responses.Semantic.meshes['Body-Semantic-0'];
          state.responses.Semantic.meshSemantics = {
            'Body-Semantic-0': {
              conditions: entry.conditions || [], sources: entry.sources || [],
              tex_key: entry.tex_key, normal_map_key: null,
              normal_data_key: null, light_map_key: null,
              material_map_key: null,
              asset_binding: {
                status: 'not_found', component_status: 'not_found',
                range_status: 'unknown', asset_type: 'GIMI',
              },
              texture_resolution: {normal_map: 'mod_semantic'},
              asset_slot_evidence: [],
            },
          };
          state.responses.Semantic.meshSemanticsAssetResolution = {
            total_draws: 1, exact_draws: 0, partial_draws: 0,
            ambiguous_draws: 0, unmatched_draws: 1,
            index_unavailable_draws: 0, index_status: 'ready',
            configured_roots: 1, ready_roots: 1,
          };
        }""")
        page.evaluate("window.modViewer.refreshMeshSemantics()")
        page.wait_for_function(
            "document.querySelector('#inspector-content')?.innerText.includes('Not found')")
        assert "Normal (automatic)\nMod" in page.locator(
            "#inspector-content").inner_text()
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.assetEntry"
            ".asset_binding.status === 'not_found'")
        assert page.locator(".asset-draw-label").count() == 0
        assert "Match\nNot found" in page.locator(
            ".inspector-asset-section").inner_text()
        assert page.locator(".asset-component-label").inner_text() == (
            "Asset: Partial")
        page.locator("#health-btn").click()
        page.locator("#health-modal-backdrop.show").wait_for()
        health_asset_summary = page.locator("#health-asset-summary").inner_text()
        assert "Asset resolution: 0 / 1 draws exact" in health_asset_summary
        assert "1 not found" in health_asset_summary
    finally:
        context.close()


def test_right_dock_tabs_toggle_without_reopening_on_refresh(edge_browser, frontend_url):
    path = "fixture-model"
    context, page = _page(edge_browser, frontend_url, {path: _payload()})
    try:
        _open(page, path)
        page.locator("#right-dock.ui-visible").wait_for()
        assert page.locator("#controls-panel").is_visible()
        assert page.locator("body.right-dock-visible").count() == 1
        assert page.locator("body.right-dock-mounted").count() == 1
        page.locator("#controls-tab").click()
        assert page.locator("#controls-panel").is_hidden()
        assert page.locator("body.right-dock-visible").count() == 0
        assert page.locator("body.right-dock-mounted").count() == 1
        assert page.evaluate("""() => {
          const tabs = document.querySelector('#right-dock .right-dock-tabs').getBoundingClientRect();
          const gizmo = document.querySelector('#view-gizmo').getBoundingClientRect();
          return gizmo.top >= tabs.bottom;
        }""")
        assert page.locator("#right-dock .right-dock-tabs").is_visible()
        page.locator("#inspector-tab").click()
        assert page.locator("#inspector-panel").is_visible()
        assert page.locator("body.right-dock-visible").count() == 1
        page.locator("#inspector-tab").click()
        assert page.locator("#inspector-panel").is_hidden()
        assert page.locator("body.right-dock-visible").count() == 0
        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 2")
        assert page.locator("#inspector-panel").is_hidden()
        assert page.locator("#controls-panel").is_hidden()
        assert page.locator("#controls-tab").get_attribute("aria-selected") == "false"
        assert page.locator("#inspector-tab").get_attribute("aria-expanded") == "false"
    finally:
        context.close()


def test_assets_panel_uses_badges_and_lazy_browse_only_children(
        edge_browser, frontend_url):
    root = "fixture-assets"
    child = root + r"\Character"
    context, page = _page(
        edge_browser, frontend_url, {},
        asset_folders=[{"type": "GIMI", "path": root, "exists": True}],
        asset_subfolders={root: [{"name": "Character", "path": child}]},
    )
    try:
        page.locator("#assets-tab").click()
        page.locator("#asset-folder-list .asset-folder-select").first.wait_for()
        assert "GIMI" in page.locator("#asset-folder-list").first.inner_text()
        page.locator("#asset-folder-list .asset-folder-expand").first.click()
        page.locator(".asset-folder-select", has_text="Character").wait_for()
        assert page.evaluate("window.__fakeApi.calls.listAssetSubfolders") == [root]
        child_select = page.locator(".asset-folder-select", has_text="Character")
        child_select.click()
        assert page.evaluate("window.__fakeApi.calls.loadMod") == []
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == child
        switch = page.locator(".asset-folder-switch").first
        assert switch.get_attribute("aria-checked") == "true"
        switch.click()
        page.wait_for_function("window.__fakeApi.assetFolders[0].enabled === false")
        page.locator(".asset-folder-switch[aria-checked='false']").wait_for()
        child_select.wait_for()
        assert page.locator(".asset-folder-expand.expanded").count() == 1
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == child
        page.locator(".asset-folder-switch").first.click()
        page.wait_for_function("window.__fakeApi.assetFolders[0].enabled === true")
        page.locator(".asset-folder-switch[aria-checked='true']").wait_for()
        child_select.wait_for()
        assert page.locator(".asset-folder-expand.expanded").count() == 1
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == child
        root_select = page.locator("#asset-folder-list .asset-folder-select").first
        root_select.click()
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == root
        switch = page.locator(".asset-folder-switch").first
        page.evaluate("""() => {
          window.pywebview.api.set_asset_folder_enabled = async () => ({
            error: 'write failed'});
        }""")
        page.locator(".asset-folder-switch").first.click()
        page.locator("#asset-folder-error.show").wait_for()
        assert page.locator(".asset-folder-switch").first.get_attribute(
            "aria-checked") == "true"
        page.evaluate("""() => {
          window.pywebview.api.set_asset_folder_enabled = async (path, enabled) => {
            const item = window.__fakeApi.assetFolders.find(folder => folder.path === path);
            if (item) item.enabled = enabled;
            return {folders: window.__fakeApi.assetFolders.map(folder => ({...folder}))};
          };
        }""")
        page.locator(".asset-folder-switch").first.click()
        page.wait_for_function(
            "!document.querySelector('#asset-folder-error').classList.contains('show')")
        page.locator("#asset-folder-add").click()
        assert page.locator("#afm-type option").all_inner_texts() == ["ZZMI", "GIMI", "WWMI"]
        page.evaluate("window.__fakeApi.nextPath = 'picked-asset-folder'")
        page.locator("#afm-browse").click()
        assert page.locator("#afm-path").input_value() == "picked-asset-folder"
        assert page.evaluate("window.__fakeApi.calls.selectAssetFolder") == [
            "picked-asset-folder"]
        assert page.locator(".asset-folder-path-field").count() == 1
        assert page.locator("#afm-save").evaluate(
            "button => getComputedStyle(button).backgroundColor") == "rgb(35, 134, 54)"
        assert page.locator("#afm-cancel").evaluate(
            "button => getComputedStyle(button).backgroundColor") == "rgb(33, 38, 45)"
    finally:
        context.close()






def test_asset_rebuild_preserves_tree_and_disables_only_one_root(
        edge_browser, frontend_url):
    root = "fixture-assets"
    child = root + r"\Character"
    context, page = _page(
        edge_browser, frontend_url, {},
        asset_folders=[{
            "type": "GIMI", "path": root, "exists": True,
            "index": {
                "status": "ready", "assetCount": 3,
                "geometryHashCount": 4, "skippedCount": 1,
            },
        }],
        asset_subfolders={root: [{"name": "Character", "path": child}]},
    )
    try:
        page.locator("#assets-tab").click()
        page.locator("#asset-folder-list .asset-folder-select").first.wait_for()
        assert "3 assets" in page.locator(".asset-folder-index-status").inner_text()
        page.locator(".asset-folder-expand").first.click()
        page.locator(".asset-folder-select", has_text="Character").click()
        page.evaluate("""() => {
          const state = window.__fakeApi;
          window.pywebview.api.rebuild_asset_index = async path => {
            state.calls.rebuildAssetIndex.push(path);
            return new Promise(resolve => { state.releaseRebuild = () => resolve({
              folders: state.assetFolders}); });
          };
        }""")
        page.locator(".asset-folder-rebuild").click()
        page.wait_for_function(
            "window.__fakeApi.calls.rebuildAssetIndex.length === 1")
        assert page.locator(".asset-folder-rebuild").is_disabled()
        assert page.locator(".asset-folder-switch").is_disabled()
        assert page.locator(".asset-folder-more").is_disabled()
        page.evaluate("window.__fakeApi.releaseRebuild()")
        page.locator(".asset-folder-rebuild:not([disabled])").wait_for()
        assert page.locator(".asset-folder-expand.expanded").count() == 1
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == child
        page.evaluate("""() => {
          window.pywebview.api.rebuild_asset_index = async () => ({
            error: 'rebuild failed', indexPreserved: true});
        }""")
        page.locator(".asset-folder-rebuild").click()
        page.locator("#asset-folder-error.show").wait_for()
        page.evaluate("""() => {
          window.pywebview.api.rebuild_asset_index = async () => ({
            folders: window.__fakeApi.assetFolders.map(folder => ({...folder}))});
        }""")
        page.locator(".asset-folder-rebuild").click()
        page.wait_for_function(
            "!document.querySelector('#asset-folder-error').classList.contains('show')")
    finally:
        context.close()


def test_asset_save_shows_building_state_until_index_is_ready(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {}, asset_folders=[])
    try:
        page.locator("#assets-tab").click()
        page.locator("#asset-folder-add").click()
        page.evaluate("window.__fakeApi.nextPath = 'picked-asset-folder'")
        page.locator("#afm-browse").click()
        page.evaluate("""() => {
          const state = window.__fakeApi;
          window.pywebview.api.add_asset_folder = async () => new Promise(resolve => {
            state.releaseAssetSave = () => resolve({folders: []});
          });
        }""")
        page.locator("#afm-save").click()
        page.wait_for_function(
            "document.querySelector('#afm-save').textContent === 'Building index…'")
        assert page.locator("#afm-save").is_disabled()
        assert page.locator("#afm-browse").is_disabled()
        assert page.locator("#afm-cancel").is_disabled()
        assert page.locator("#afm-type").is_disabled()
        page.evaluate("window.__fakeApi.releaseAssetSave()")
        page.locator("#asset-folder-modal-backdrop").wait_for(state="hidden")
    finally:
        context.close()


def test_direct_dds_matches_png_orientation_and_diffuse_color(
        edge_browser, frontend_url, tmp_path):
    dds_path = tmp_path / "orientation.dds"
    dds_path.write_bytes(_dxt1_vertical_gradient())
    publication = server.begin_texture_publication(str(tmp_path))
    dds_url = publication.register(str(dds_path))
    publication.commit()
    png_url = dds_url[:-4] + ".png"
    payload = _parity_payload(dds_url)
    context, page = _page(edge_browser, frontend_url, {"Parity": payload})
    try:
        _open(page, "Parity")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.image?.width === 4")
        page.wait_for_timeout(250)
        direct_pixels = [
            _sample_mesh_pixel_at(page, 0, 0.65),
            _sample_mesh_pixel_at(page, 0, -0.65),
        ]

        page.evaluate("""async ({key, uri}) => {
          const {refreshMeshTexture, setTextures} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setTextures({[key]: uri});
          refreshMeshTexture(mesh);
        }""", {"key": "diffuse::Parity-one.png", "uri": png_url})
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.image?.width === 4"
            " && window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.isCompressedTexture !== true")
        page.wait_for_timeout(250)
        png_pixels = [
            _sample_mesh_pixel_at(page, 0, 0.65),
            _sample_mesh_pixel_at(page, 0, -0.65),
        ]

        assert all(
            max(abs(left - right) for left, right in zip(direct, fallback)) <= 40
            for direct, fallback in zip(direct_pixels, png_pixels)), (
                direct_pixels, png_pixels)
        assert direct_pixels[0] != direct_pixels[1]
        assert png_pixels[0] != png_pixels[1]
    finally:
        context.close()






def test_texture_stays_fallback_until_png_load_completes(
        edge_browser, frontend_url):
    payload = _payload("Delayed")
    entry = payload["meshes"]["Body-Delayed-0"]
    entry["pos"] = _f32(-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
    entry["idx"] = _u32(0, 1, 2, 0, 2, 3)
    entry["uv"] = _f32(0, 0, 1, 0, 1, 1, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/delayed.png"
    texture_uri = _flat_png_uri((236, 42, 38, 255))
    pending = {"requests": 0, "route": None}

    context, page = _page(edge_browser, frontend_url, {"Delayed": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/delayed.png", hold)
    try:
        _open(page, "Delayed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => {
            const mesh = window.modViewer.activeMeshes[0];
            return mesh?.material?.userData?.gameMaterial
              ?.bindings?.diffuse?.enabledNode?.value === false;
          }
        """)
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            diffuseEnabled: game.bindings.diffuse.enabledNode.value,
            fallbackColor: mesh.material.color.getHex(),
          };
        }""")
        assert pending["requests"] == 1
        assert state == {"diffuseEnabled": False, "fallbackColor": 0xcccccc}
        pending_pixel = _sample_mesh_pixel(page)
        assert min(pending_pixel) > 20
        assert max(pending_pixel) - min(pending_pixel) < 30

        idle_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == idle_count

        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=idle_count)
        changed_count = page.evaluate("window.modViewer.getRenderCount()")

        pending["route"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(texture_uri.split(",", 1)[1]),
        )
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.diffuse;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=changed_count)
        loaded_pixel = _sample_mesh_pixel(page)
        assert loaded_pixel[0] > loaded_pixel[1] * 1.5, loaded_pixel
        assert loaded_pixel != pending_pixel, (pending_pixel, loaded_pixel)
    finally:
        context.close()


def test_view_gizmo_snap_renders_only_during_animation(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Snap": _payload("Snap")})
    try:
        _open(page, "Snap")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        page.wait_for_timeout(200)
        idle_count = page.evaluate("window.modViewer.getRenderCount()")

        page.evaluate("""
          () => document.querySelector('.gizmo-axis.positive')
            .dispatchEvent(new KeyboardEvent('keydown', {
              key: 'Enter', bubbles: true,
            }))
        """)
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() >= count + 2",
            arg=idle_count)
        page.wait_for_timeout(350)
        settled_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == settled_count
        assert settled_count > idle_count + 2
    finally:
        context.close()


def test_mesh_row_selection_invalidates_on_demand_renderer(
        edge_browser, frontend_url):
    payload = _payload("Selection")
    second = copy.deepcopy(payload["meshes"]["Body-Selection-0"])
    second["component"] = "Face Selection"
    payload["meshes"]["Face-Selection-0"] = second
    context, page = _page(edge_browser, frontend_url, {"Selection": payload})
    try:
        _open(page, "Selection")
        rows = page.locator(".draw-item")
        rows.nth(0).wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        page.wait_for_timeout(300)
        idle_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == idle_count

        rows.nth(0).click()
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=idle_count)
        first_state = page.evaluate("""() => {
          return window.modViewer.activeMeshes.map(mesh => ({
            emissive: mesh.material.emissive.getHex(),
            intensity: mesh.material.emissiveIntensity,
          }));
        }""")
        assert first_state == [
            {"emissive": 0xffd60a, "intensity": 0.22},
            {"emissive": 0x000000, "intensity": 1},
        ]

        selected_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == selected_count

        rows.nth(1).click()
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=selected_count)
        second_state = page.evaluate("""() => {
          return window.modViewer.activeMeshes.map(mesh => ({
            emissive: mesh.material.emissive.getHex(),
            intensity: mesh.material.emissiveIntensity,
          }));
        }""")
        assert second_state == [
            {"emissive": 0x000000, "intensity": 1},
            {"emissive": 0xffd60a, "intensity": 0.22},
        ]

        final_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == final_count
    finally:
        context.close()


def test_nonfinite_mesh_positions_do_not_poison_camera_fit(
        edge_browser, frontend_url):
    payload = _payload("InvalidGeometry")
    entry = payload["meshes"]["Body-InvalidGeometry-0"]
    entry["pos"] = _f32(
        0, 0, 0, 1, 0, 0, 0, 1, 0,
        float("nan"), float("nan"), float("nan"),
    )
    entry["idx"] = _u32(0, 1, 2)
    context, page = _page(
        edge_browser, frontend_url, {"InvalidGeometry": payload})
    try:
        _open(page, "InvalidGeometry")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        camera = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene.js');
          return {
            position: camera.position.toArray(),
            target: controls.target.toArray(),
            near: camera.near,
            far: camera.far,
          };
        }""")
        assert all(math.isfinite(value)
                   for value in camera["position"] + camera["target"])
        assert math.isfinite(camera["near"])
        assert math.isfinite(camera["far"])
    finally:
        context.close()


def test_shared_texture_waits_once_and_updates_all_meshes(
        edge_browser, frontend_url):
    payload = _payload("Shared")
    entry = payload["meshes"]["Body-Shared-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    second = copy.deepcopy(entry)
    second["component"] = "Face Shared"
    payload["meshes"]["Face-Shared-0"] = second
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/shared.png"
    pending = {"requests": 0, "route": None}

    context, page = _page(edge_browser, frontend_url, {"Shared": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/shared.png", hold)
    try:
        _open(page, "Shared")
        page.wait_for_function("window.modViewer.activeMeshes.length === 2")
        page.wait_for_function("""
          () => window.modViewer.activeMeshes.every(mesh =>
            mesh.material.userData.gameMaterial.bindings.diffuse.enabledNode.value
              === false)
        """)
        assert pending["requests"] == 1

        pending["route"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
        )
        page.wait_for_function("""
          () => window.modViewer.activeMeshes.every(mesh =>
            mesh.material.userData.gameMaterial.bindings.diffuse.enabledNode.value
              === true)
        """)
    finally:
        context.close()


@pytest.mark.parametrize(("profile_id", "normal_role"), [
    ("zzz:zzmi", "normal_map"),
    ("wuwa:rabbitfx:body", "normal_data"),
])
def test_diffuse_normal_mode_keeps_only_color_and_normal_bindings(
        edge_browser, frontend_url, profile_id, normal_role):
    payload = _packed_material_payload(profile_id)
    entry = payload["meshes"]["Body-Packed-0"]
    if normal_role == "normal_map":
        normal_key = "normal_map::Packed-surface-normal.png"
        entry["normal_map_key"] = normal_key
        payload["textures"][normal_key] = _PNG_URI
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function("""normalRole => {
          const bindings = window.modViewer.activeMeshes[0]?.material
            ?.userData?.gameMaterial?.bindings;
          return bindings?.diffuse.enabledNode.value === true
            && bindings[normalRole].enabledNode.value === true;
        }""", arg=normal_role)

        page.locator("#texture-btn").click()
        options = page.locator("#texture-popover .ui-popover-option")
        assert options.all_inner_texts() == [
            "All maps", "Diffuse and NormalMap", "Diffuse only", "No textures"]
        options.filter(has_text="Diffuse and NormalMap").click()
        bindings = page.evaluate("""() => {
          const values = window.modViewer.activeMeshes[0].material
            .userData.gameMaterial.bindings;
          return Object.fromEntries(Object.entries(values).map(
            ([role, binding]) => [role, binding.enabledNode.value]));
        }""")
        expected_bindings = {
            "diffuse": True,
            "normal_map": False,
            "normal_data": False,
            "light_map": False,
            "material_map": False,
        }
        expected_bindings[normal_role] = True
        assert bindings == expected_bindings
        button = page.locator("#texture-btn")
        assert "diffuse-normal" in (button.get_attribute("class") or "")
        assert button.get_attribute("aria-label") == (
            "Textures: diffuse and normal map")
        assert button.evaluate("button => getComputedStyle(button).color") == (
            "rgb(242, 204, 114)")
    finally:
        context.close()


def test_failed_texture_stays_fallback_without_retrying(
        edge_browser, frontend_url):
    payload = _payload("FailedTexture")
    entry = payload["meshes"]["Body-FailedTexture-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/failed.png"
    pending = {"requests": 0, "route": None}

    context, page = _page(
        edge_browser, frontend_url, {"FailedTexture": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/failed.png", hold)
    try:
        _open(page, "FailedTexture")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        pending["route"].abort()
        page.wait_for_function(
            """async key => {
              const {hasTexture} = await import('./js/mesh-factory.js');
              return hasTexture(key) === false;
            }""",
            arg=key,
        )
        page.wait_for_timeout(100)
        assert pending["requests"] == 1
        assert page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            enabled: mesh.material.userData.gameMaterial
              .bindings.diffuse.enabledNode.value,
            fallbackColor: mesh.material.color.getHex(),
          };
        }""") == {"enabled": False, "fallbackColor": 0xcccccc}
    finally:
        context.close()


def test_native_dds_failure_falls_back_without_black_frame(
        edge_browser, frontend_url):
    payload = _payload("NativeDDS")
    entry = payload["meshes"]["Body-NativeDDS-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = "diffuse::NativeDDS.dds"
    entry["tex_key"] = key
    payload["textures"] = {key: f"{frontend_url}/native.dds"}
    pending = {"dds": None}
    requests = {"dds": 0, "png": 0}

    context, page = _page(edge_browser, frontend_url, {"NativeDDS": payload})
    try:
        supported = page.evaluate("""
          async () => {
            const {supportsBCTextureCompression} =
              await import('./js/renderer-capabilities.js');
            return supportsBCTextureCompression();
          }
        """)
        if not supported:
            pytest.skip("native DDS is not supported by the test renderer")

        def hold_dds(route):
            requests["dds"] += 1
            pending["dds"] = route

        def fulfill_png(route):
            requests["png"] += 1
            route.fulfill(
                status=200,
                content_type="image/png",
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
            )

        page.route("**/native.dds", hold_dds)
        page.route("**/native.png", fulfill_png)
        _open(page, "NativeDDS")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        assert requests == {"dds": 1, "png": 0}

        pending["dds"].abort()
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.diffuse;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.isCompressedTexture !== true
              && binding.textureNode.value?.image?.width === 1;
          }
        """)
        assert requests == {"dds": 1, "png": 1}
    finally:
        context.close()


def test_replaced_pending_texture_ignores_stale_completion(
        edge_browser, frontend_url):
    payload = _payload("Replacement")
    entry = payload["meshes"]["Body-Replacement-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/old.png"
    pending = {"old": None}
    requests = {"old": 0, "new": 0}

    context, page = _page(edge_browser, frontend_url, {"Replacement": payload})
    try:
        def hold_old(route):
            requests["old"] += 1
            pending["old"] = route

        def fulfill_new(route):
            requests["new"] += 1
            route.fulfill(
                status=200,
                content_type="image/png",
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
            )

        page.route("**/old.png", hold_old)
        page.route("**/new.png", fulfill_new)
        _open(page, "Replacement")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        assert requests == {"old": 1, "new": 0}

        page.evaluate("""async ({key, uri}) => {
          const {addTexture, refreshMeshTexture} =
            await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          addTexture(key, uri);
          refreshMeshTexture(mesh);
        }""", {"key": key, "uri": f"{frontend_url}/new.png"})
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === true
        """)
        assert requests == {"old": 1, "new": 1}
        page.evaluate("""() => {
          window.__replacementTexture = window.modViewer.activeMeshes[0]
            .material.userData.gameMaterial.bindings.diffuse.textureNode.value;
        }""")

        pending["old"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(_flat_png_uri((255, 0, 0, 255))
                                 .split(",", 1)[1]),
        )
        page.wait_for_timeout(200)
        assert page.evaluate("""
          () => {
            const binding = window.modViewer.activeMeshes[0].material
              .userData.gameMaterial.bindings.diffuse;
            return binding.enabledNode.value === true
              && binding.textureNode.value === window.__replacementTexture;
          }
        """)
    finally:
        context.close()


def test_webgpu_unsupported_state_is_visible_and_never_falls_back(
        edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    context.add_init_script("""
      Object.defineProperty(Navigator.prototype, 'gpu', {
        configurable: true, get: () => undefined,
      });
      window.__webglFallbackRequested = false;
      const getContext = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function(type, ...args) {
        if (type === 'webgl' || type === 'webgl2') {
          window.__webglFallbackRequested = true;
        }
        return getContext.call(this, type, ...args);
      };
    """)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#renderer-error.show").wait_for(timeout=5000)
        assert "WebGPU is required" in page.locator("#renderer-error").inner_text()
        assert page.locator("#open-btn").is_disabled()
        assert not page.evaluate("window.__webglFallbackRequested")
    finally:
        context.close()








def test_diagnostics_badge_populates_after_mod_load(
        edge_browser, frontend_url):
    diagnostics = {
        "summary": {"issues": 2, "errors": 1, "warnings": 1},
        "files": {"referenced": 1},
        "issues": [{"severity": "error", "category": "ini",
                     "message": "Missing resource"}],
    }
    context, page = _page(
        edge_browser, frontend_url, {"A": _payload("A")},
        diagnostics=diagnostics,
    )
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "document.querySelector('#health-count').textContent === '2'")
        assert page.locator("#health-btn").get_attribute("title") == (
            "2 INI diagnostic issues")
        assert page.locator("#health-modal-backdrop.show").count() == 0
        assert page.evaluate("window.__fakeApi.calls.diagnostics") == ["A"]
    finally:
        context.close()


@pytest.mark.parametrize("failed_payload", [
    {"error": "loader failed", "health": {"summary": {"issues": 1, "errors": 1}}},
], ids=["loader-error"])
def test_failed_mod_switch_clears_previous_ui_and_pending_state(
        edge_browser, frontend_url, failed_payload):
    context, page = _page(
        edge_browser, frontend_url,
        {"A": _payload("A"), "B": failed_payload},
        pending={"A": True, "B": False},
    )
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.locator("#pending-indicator.show").wait_for()
        # Keep the visible indicator stale while allowing the switch itself
        # to proceed without the discard-confirmation dialog.
        page.evaluate("window.__fakeApi.pending.A = false")

        _open(page, "B")
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator(".draw-item").count() == 0
        assert page.locator("#toggle-list .toggle-item").count() == 0
        assert page.locator("#menu-list .menu-item").count() == 0
        assert page.locator("#present-list .toggle-item").count() == 0
        assert not page.locator("#sidebar").is_visible()
        assert not page.locator("#camera-panel").is_visible()
        assert page.locator("#pending-indicator.show").count() == 0
        assert page.locator("#export-btn").is_disabled()
        assert page.locator("#mod-path").inner_text() == "B"
        assert not page.locator("#ini-view-btn").is_disabled()
        page.locator("#dialog-ok").click()
    finally:
        context.close()


def test_frontend_construction_failure_rolls_back_partial_scene(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"A": _payload("A"), "B": _construction_failure_payload()},
        pending={"A": True, "B": False},
    )
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.evaluate("window.__fakeApi.pending.A = false")

        _open(page, "B")
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator(".draw-item").count() == 0
        assert page.locator("#mesh-list").inner_text() == ""
        assert not page.locator("#sidebar").is_visible()
        assert not page.locator("#camera-panel").is_visible()
        assert not page.locator("#toggle-panel").is_visible()
        assert not page.locator("#menu-panel").is_visible()
        assert not page.locator("#present-panel").is_visible()
        assert page.locator("#mod-path").inner_text() == "B"
        assert not page.locator("#ini-view-btn").is_disabled()
        page.locator("#dialog-ok").click()
    finally:
        context.close()


@pytest.mark.parametrize("profile_id", ["zzz:zzmi"])
def test_packed_material_profile_uses_tsl_nodes_and_stable_bindings(
        edge_browser, frontend_url, profile_id):
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": _packed_material_payload(profile_id)},
    )
    runtime_errors = []
    page.on("pageerror", lambda error: runtime_errors.append(str(error)))
    page.on("console", lambda message: runtime_errors.append(message.text)
            if message.type == "error" and "Failed to load resource" not in message.text
            else None)
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        packed_role = "material_map" if profile_id == "zzz:zzmi" else "light_map"
        page.wait_for_function(
            f"window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            f"?.bindings?.{packed_role}?.enabledNode?.value")

        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const packedRole = game.profile.id.startsWith('zzz') ? 'material_map' : 'light_map';
          const lightingModel = material.setupLightingModel();
          return {
            physical: material.isMeshPhysicalNodeMaterial,
            profile: game.profile.id,
            hasGeneratedShaderState: Object.hasOwn(game, 'shader'),
            hasLegacyUniformState: Object.hasOwn(game, 'uniforms'),
            lightMap: game.bindings.light_map.enabledNode.value,
            materialMap: game.bindings.material_map.enabledNode.value,
            sampledRole: game.bindings[packedRole].textureNode.value
              !== game.bindings[packedRole].placeholder,
            hasMetalnessNode: !!material.metalnessNode,
            hasSpecularResponseNode: !!game.specularResponseNode,
            lightingModel: lightingModel.constructor.name,
            metalnessScale: game.profile.metalness_scale,
            specularScale: game.profile.specular_scale,
            specularInfluence: game.profile.specular_influence,
            shadowThreshold: game.shadowThresholdNode.value,
            shadowSoftness: game.shadowSoftnessNode.value,
            shadowMaskStrength: game.shadowMaskStrengthNode.value,
            shadowInfluence: game.shadowInfluenceNode.value,
            materialKind: mesh.userData.materialKind,
            materialKindReliable: mesh.userData.materialKindReliable,
            materialProfileId: mesh.userData.materialProfileId,
            version: material.version,
          };
        }""")
        assert state["physical"]
        assert state["profile"] == profile_id
        assert not state["hasGeneratedShaderState"]
        assert not state["hasLegacyUniformState"]
        assert state["metalnessScale"] == (1 if profile_id == "zzz:zzmi" else 0.08)
        assert state["specularScale"] == 1
        assert state["specularInfluence"] == (None if profile_id == "zzz:zzmi" else 0.15)
        assert (state["shadowThreshold"], state["shadowSoftness"],
                state["shadowMaskStrength"], state["shadowInfluence"]) == (
                    0.5, 0.08, 0.5, 1)
        assert (state["materialKind"], state["materialKindReliable"],
                state["materialProfileId"]) == ("body", False, profile_id)
        assert state["lightingModel"] == (
            "GenshinLightingModel" if profile_id == "genshin:gimi"
            else "PhysicalLightingModel")
        assert state["hasSpecularResponseNode"]
        assert state["sampledRole"]
        assert not runtime_errors, "\n".join(runtime_errors)
        assert state["lightMap"] and state["materialMap"] \
            if profile_id == "zzz:zzmi" else state["lightMap"]

        after = page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const packedRole = game.profile.id.startsWith('zzz') ? 'material_map' : 'light_map';
          const packedNode = game.bindings[packedRole].textureNode;
          const packedEnabled = game.bindings[packedRole].enabledNode;
          const version = material.version;
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: mesh.userData.normalMapKey,
            normal_data: null,
            light_map: null,
            material_map: null,
          });
          return {
            sameTextureNode: game.bindings[packedRole].textureNode === packedNode,
            sameEnabledNode: game.bindings[packedRole].enabledNode === packedEnabled,
            sameVersion: material.version === version,
            mapEnabled: packedEnabled.value,
            usesPlaceholder: game.bindings[packedRole].textureNode.value
              === game.bindings[packedRole].placeholder,
          };
        }""")
        assert after == {"sameTextureNode": True, "sameEnabledNode": True,
                         "sameVersion": True, "mapEnabled": False,
                         "usesPlaceholder": True}
    finally:
        context.close()


def test_each_mesh_resolves_its_own_profile_and_packed_source(
        edge_browser, frontend_url):
    payload = _packed_material_payload("zzz:zzmi")
    body_name, body = next(iter(payload["meshes"].items()))
    face_name = "Face-Packed-0"
    face = copy.deepcopy(body)
    face["component"] = "Face Packed"
    face["material_kind"] = "face"
    face["material_kind_reliable"] = False
    face["material_profile_id"] = "genshin:gimi"
    payload["meshes"] = {body_name: body, face_name: face}
    payload["metadata"]["material_profiles"]["genshin:gimi"] = \
        material_profile_for("genshin", "gimi").to_metadata()

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes.length === 2 && "
            "window.modViewer.activeMeshes.every(mesh => "
            "mesh.material.userData.gameMaterial)")
        states = page.evaluate("""() => window.modViewer.activeMeshes.map(mesh => ({
          profileId: mesh.userData.materialProfileId,
          kind: mesh.userData.materialKind,
          lightMap: mesh.material.userData.gameMaterial.bindings.light_map.enabledNode.value,
          materialMap: mesh.material.userData.gameMaterial.bindings.material_map.enabledNode.value,
        }))""")
        assert states == [
            {"profileId": "zzz:zzmi", "kind": "body",
             "lightMap": True, "materialMap": True},
            {"profileId": "genshin:gimi", "kind": "face",
             "lightMap": True, "materialMap": False},
        ]
        assert page.evaluate("window.modViewer.getMaterialState(1).profileId") == (
            "genshin:gimi")
    finally:
        context.close()




def test_genshin_no_uv_keeps_a_and_b_out_of_conservative_material_graph(
        edge_browser, frontend_url):
    payload = _packed_material_payload("genshin:gimi")
    payload["meshes"]["Body-Packed-0"].pop("uv")
    uv_messages = []
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    page.on("console", lambda message: uv_messages.append(message.text)
            if "uv" in message.text.lower() else None)
    try:
        _open(page, "Packed")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          window.__rawMaterial = material;
          window.__rawColorNode = material.colorNode;
          return {
            standard: !!material.isMeshStandardNodeMaterial,
            physical: !!material.isMeshPhysicalNodeMaterial,
            packedResponse: game.packedResponse,
            hasMaterialId: game.hasMaterialId,
            hasSpecularArea: game.hasSpecularArea,
            lightMap: game.bindings.light_map.enabledNode.value,
          };
        }""")
        assert state == {
            "standard": True, "physical": False,
            "packedResponse": False,
            "hasMaterialId": False, "hasSpecularArea": False,
            "lightMap": False,
        }
        assert not uv_messages, uv_messages
    finally:
        context.close()






def test_wuwa_debug_modes_are_capability_gated_and_uniform_only(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    normal_low = "normal_data::Packed-debug-low.png"
    normal_high = "normal_data::Packed-debug-high.png"
    entry["normal_data_key"] = normal_low
    payload["textures"][normal_low] = _flat_png_uri((0, 0, 0, 255))
    payload["textures"][normal_high] = _flat_png_uri((0, 0, 255, 255))
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": payload},
    )
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const colorNode = material.colorNode;
          const version = material.version;
          const before = window.modViewer.getMaterialState(0);
          const modes = [
            window.modViewer.setMaterialDebugMode('material-id'),
            window.modViewer.getMaterialState(0).debugMode,
            window.modViewer.setMaterialDebugMode('normal-data-b'),
            window.modViewer.getMaterialState(0).debugMode,
            window.modViewer.setMaterialDebugMode('normal-data-a'),
            window.modViewer.getMaterialState(0).debugMode,
          ];
          return {
            before,
            modes,
            sameMaterial: material === mesh.material,
            sameColorNode: material.colorNode === colorNode,
            sameVersion: material.version === version,
            hasDebugNodes: !!game.normalDataBNode && !!game.normalDataANode,
          };
        }""")
        assert state["before"]["supportedDebugModes"] == [
            "shadow-mask", "normal-data-b", "normal-data-a"]
        assert state["before"]["hasShadowMask"] is True
        assert state["before"]["hasNormalData"] is True
        assert state["before"]["hasNormalDataB"] is True
        assert state["before"]["hasNormalDataA"] is True
        assert state["modes"] == [
            "material-id", "material-id", "normal-data-b", "normal-data-b",
            "normal-data-a", "normal-data-a",
        ]
        assert state["sameMaterial"]
        assert state["sameColorNode"]
        assert state["sameVersion"]
        assert state["hasDebugNodes"]

        page.evaluate("window.modViewer.setMaterialDebugMode('normal-data-b')")
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.normal_data;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_timeout(300)
        low_pixel = _sample_mesh_pixel(page)
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          window.__normalLowTexture = mesh.material.userData.gameMaterial
            .bindings.normal_data.textureNode.value;
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: key,
            light_map: null,
            material_map: null,
          });
        }""", normal_high)
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.normal_data;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value !== window.__normalLowTexture
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_timeout(300)
        high_pixel = _sample_mesh_pixel(page)
        assert sum(high_pixel) > sum(low_pixel), (low_pixel, high_pixel)
    finally:
        context.close()


def test_wuwa_body_profile_binds_normal_data_without_stock_pbr_mapping(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["material_kind"] = "body"
    entry["material_kind_reliable"] = True
    entry["material_kind_reason"] = "viewer material-kind override"
    entry["material_kind_override"] = "body"
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.wait_for_timeout(300)
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        assert page.locator(".inspector-material-kind-control").count() == 1
        assert page.locator(".draw-item .material-kind-select").count() == 0
        layout = page.evaluate("""
          () => {
            const header = document.querySelector('.group-hdr');
            const name = header.querySelector('.group-name');
            const select = header.querySelector('.material-kind-select');
            const texture = header.querySelector('.group-tex-btn');
            return {
              hasMaterialLabel: !!header.querySelector('label'),
              hasMaterialControl: !!select,
              hasTextureControl: !!texture,
              nameTitle: name.title,
              nameOverflow: getComputedStyle(name).textOverflow,
              nameWhiteSpace: getComputedStyle(name).whiteSpace,
            };
          }
        """)
        assert layout == {
            "hasMaterialLabel": False,
            "hasMaterialControl": False,
            "hasTextureControl": False,
            "nameTitle": "Body Packed",
            "nameOverflow": "ellipsis",
            "nameWhiteSpace": "nowrap",
        }
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            physical: !!mesh.material.isMeshPhysicalNodeMaterial,
            profileId: game.profile.id,
            directShadowModel: game.profile.direct_shadow_model,
            directSpecularModel: game.profile.direct_specular_model,
            materialKindOverride: window.modViewer.getMaterialState(0)
              .materialKindOverride,
            normalData: game.bindings.normal_data.enabledNode.value,
            lightMap: game.bindings.light_map.enabledNode.value,
            materialMap: game.bindings.material_map.enabledNode.value,
            normalSource: game.normalSource,
            normalPacking: game.normalPacking,
            normalMapKey: mesh.userData.normalMapKey,
            stockNormalMap: mesh.material.normalMap === null,
            stockMetalness: game.profile.metalness,
            stockSpecular: game.profile.specular,
            model: mesh.material.setupLightingModel().constructor.name,
          };
        }""")
        assert state == {
            "physical": True,
            "profileId": "wuwa:rabbitfx:body",
            "directShadowModel": "wuwa_base",
            "directSpecularModel": "wuwa_body",
            "materialKindOverride": "body",
            "normalData": True,
            "lightMap": True,
            "materialMap": False,
            "normalSource": "normal_data",
            "normalPacking": "rg",
            "normalMapKey": None,
            "stockNormalMap": True,
            "stockMetalness": None,
            "stockSpecular": None,
            "model": "WuwaBodyLightingModel",
        }
        _sample_mesh_pixel(page)
        before = page.evaluate("""() => {
          window.__bodyMaterial = window.modViewer.activeMeshes[0].material;
          return window.__bodyMaterial.version;
        }""")
        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: null,
            light_map: mesh.userData.lightMapKey,
            material_map: null,
          });
        }""")
        page.wait_for_timeout(250)
        missing = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            bound: mesh.material.userData.gameMaterial
              .bindings.normal_data.enabledNode.value,
            sameMaterial: mesh.material === window.__bodyMaterial,
            version: mesh.material.version,
          };
        }""")
        assert missing["bound"] is False
        assert missing["sameMaterial"]
        assert missing["version"] == before
    finally:
        context.close()


def test_wuwa_body_b_threshold_controls_toon_classification(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = payload["meshes"]["Body-Packed-0"]
    low_key = "normal_data::Packed-body-b-low.png"
    high_key = "normal_data::Packed-body-b-high.png"
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (72, 72, 72, 255))
    payload["textures"][low_key] = _flat_png_uri((128, 128, 124, 0))
    payload["textures"][high_key] = _flat_png_uri((128, 128, 130, 0))
    entry["normal_data_key"] = low_key
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.normal_data.enabledNode.value === true")
        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            const {requestRender} = await import('./js/render-scheduler.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) object.intensity = 0;
              if (object.isSprite || object.isGridHelper) object.visible = false;
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
            key.intensity = 3;
            requestRender();
          }
        """)
        page.wait_for_timeout(400)
        low_pixels = [_sample_mesh_pixel_at(page, x, 0.1)
                      for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: key,
            light_map: mesh.userData.lightMapKey,
            material_map: null,
          });
        }""", high_key)
        page.wait_for_timeout(400)
        high_pixels = [_sample_mesh_pixel_at(page, x, 0.1)
                       for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
        assert any(sum(high) != sum(low)
                   for low, high in zip(low_pixels, high_pixels)), (
                       low_pixels, high_pixels)
    finally:
        context.close()


def test_wuwa_body_missing_toon_mask_keeps_physical_direct_specular(
        edge_browser, frontend_url):
    body_payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = body_payload["meshes"]["Body-Packed-0"]
    body_payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in body_payload["textures"]
    }
    body_payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (80, 80, 80, 255))
    low_mask_key = "normal_data::Packed-body-no-toon-mask.png"
    body_payload["textures"][low_mask_key] = _flat_png_uri((128, 128, 0, 0))
    entry["normal_data_key"] = low_mask_key
    endpoint_light_key = entry["light_map_key"]
    body_payload["textures"][endpoint_light_key] = _flat_png_uri(
        (255, 0, 0, 255))

    base_payload = copy.deepcopy(body_payload)
    base_entry = base_payload["meshes"]["Body-Packed-0"]
    base_entry["material_profile_id"] = "wuwa:rabbitfx"
    base_entry["light_map_key"] = None
    base_profile = material_profile_for("wuwa", "rabbitfx").to_metadata()
    base_payload["metadata"]["material_profiles"] = {
        base_profile["id"]: base_profile,
    }

    def render(payload):
        context, page = _page(edge_browser, frontend_url, {"Packed": payload})
        try:
            _open(page, "Packed")
            page.wait_for_function(
                "window.modViewer.activeMeshes[0]?.material?.userData"
                "?.gameMaterial?.bindings.normal_data.enabledNode.value === true")
            page.evaluate("""
              async () => {
                const THREE = await import('three');
                const {scene, controls} = await import('./js/scene.js');
                const {requestRender} = await import('./js/render-scheduler.js');
                scene.traverse(object => {
                  if (object.isAmbientLight || object.isHemisphereLight) {
                    object.intensity = 0;
                  } else if (object.isSprite || object.isGridHelper) {
                    object.visible = false;
                  }
                });
                const key = scene.children.find(object => object.isDirectionalLight);
                key.target.position.copy(controls.target);
                key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
                key.intensity = 3;
                window.modViewer.activeMeshes[0].material.roughness = 0.2;
                requestRender();
              }
            """)
            page.wait_for_timeout(400)
            return _sample_mesh_pixel(page)
        finally:
            context.close()

    packed_pixel = render(body_payload)
    physical_pixel = render(base_payload)
    assert abs(sum(packed_pixel) - sum(physical_pixel)) <= 3, (
        packed_pixel, physical_pixel)






def test_wuwa_packed_rg_normal_matches_derived_reference_and_y_sign(
        edge_browser, frontend_url):
    # Keep the source constant so the comparison exercises normal decoding,
    # not texture filtering or mip selection.  These are the same channels
    # used to build the old CPU-derived RGB reference.
    red, green = 160, 192
    x = red / 127.5 - 1.0
    y = green / 127.5 - 1.0
    z = max(0.0, 1.0 - x * x - y * y) ** 0.5
    blue = round((z * 0.5 + 0.5) * 255.0)
    diffuse_uri = _flat_png_uri((120, 120, 120, 255))
    packed_uri = _flat_png_uri((red, green, 17, 241))
    derived_uri = _flat_png_uri((red, green, blue, 255))

    def configure_light(page):
        page.evaluate("""
          async () => {
                const THREE = await import('three');
                const {scene, controls} = await import('./js/scene.js');
                const {requestRender} = await import('./js/render-scheduler.js');
            let key = null;
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              } else if (object.isDirectionalLight) {
                if (!key) key = object;
                else object.intensity = 0;
              }
            });
            key.target.position.copy(controls.target);
            key.position.copy(controls.target)
              .add(new THREE.Vector3(0.8, 0.4, 2.0));
            key.intensity = 1;
            requestRender();
          }
        """)
        page.wait_for_timeout(400)

    def sample_quad(page):
        return [
            _sample_mesh_pixel_at(page, x, y)
            for x, y in ((0.15, 0.15), (0.5, 0.15), (0.85, 0.15),
                         (0.15, 0.85), (0.85, 0.85))
        ]

    reference = _parity_payload(diffuse_uri)
    reference_entry = reference["meshes"]["Body-Parity-0"]
    reference_key = "normal_map::Parity-reference.png"
    reference_entry["normal_map_key"] = reference_key
    reference["textures"][reference_key] = derived_uri

    reference_context, reference_page = _page(
        edge_browser, frontend_url, {"Reference": reference})
    packed_context = None
    try:
        _open(reference_page, "Reference")
        reference_page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData"
            "?.gameMaterial?.bindings.normal_map.textureNode.value.image"
            "?.width === 4")
        configure_light(reference_page)
        reference_pixels = sample_quad(reference_page)

        packed = _packed_material_payload("wuwa:raw")
        packed_entry = packed["meshes"]["Body-Packed-0"]
        packed_entry["drawindexed"] = [6, 0, 0]
        packed_entry["pos"] = _f32(
            -1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
        packed_entry["uv"] = _f32(0, 0, 1, 0, 1, 1, 0, 1)
        packed_entry["idx"] = _u32(0, 1, 2, 0, 2, 3)
        packed_entry["normal_data_key"] = "normal_data::Packed-reference.png"
        packed["textures"] = {
            "diffuse::Packed-one.png": diffuse_uri,
            packed_entry["normal_data_key"]: packed_uri,
        }
        packed_context, packed_page = _page(
            edge_browser, frontend_url, {"Packed": packed})
        _open(packed_page, "Packed")
        packed_page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData"
            "?.gameMaterial?.bindings.normal_data.textureNode.value.image"
            "?.width === 4")
        configure_light(packed_page)
        packed_pixels = sample_quad(packed_page)

        assert all(
            max(abs(a - b) for a, b in zip(reference_pixel, packed_pixel)) <= 8
            for reference_pixel, packed_pixel
            in zip(reference_pixels, packed_pixels)
        ), (reference_pixels, packed_pixels, (red, green, blue))

        packed_page.evaluate("""
          async () => {
            const {setMeshTextureState} = await import('./js/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null,
              normal_data: null,
              light_map: null,
              material_map: null,
            });
          }
        """)
        packed_page.wait_for_timeout(300)
        geometry_pixels = sample_quad(packed_page)
        assert any(
            sum(abs(a - b) for a, b in zip(packed_pixel, geometry_pixel)) > 6
            for packed_pixel, geometry_pixel
            in zip(packed_pixels, geometry_pixels)
        ), (packed_pixels, geometry_pixels)

        packed_page.evaluate("""
          async key => {
            const {setMeshTextureState} = await import('./js/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            mesh.userData.normalMapYSign = 1;
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null,
              normal_data: key,
              light_map: null,
              material_map: null,
            });
          }
        """, packed_entry["normal_data_key"])
        packed_page.wait_for_timeout(300)
        positive_y_pixels = sample_quad(packed_page)
        assert any(
            sum(abs(a - b) for a, b in zip(packed_pixel, positive_pixel)) > 6
            for packed_pixel, positive_pixel
            in zip(packed_pixels, positive_y_pixels)
        ), (packed_pixels, positive_y_pixels)
    finally:
        reference_context.close()
        if packed_context is not None:
            packed_context.close()


def test_wuwa_missing_lightmap_disables_shadow_mask_without_rebuilding(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    low_key = "light_map::Packed-shadow-low.png"
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (24, 24, 24, 255))
    payload["textures"][entry["normal_data_key"]] = _flat_png_uri(
        (128, 128, 255, 255))
    # Keep the low value below the shadow cutoff but above the invalid
    # endpoint tolerance; an exact zero is intentionally treated as absent.
    payload["textures"][low_key] = _flat_png_uri((0, 16, 0, 255))
    entry["light_map_key"] = low_key

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.light_map.enabledNode.value === true")
        page.wait_for_function("""
          () => {
            const game = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial;
            return game?.bindings?.diffuse?.enabledNode?.value === true
              && game.bindings.normal_data.enabledNode.value === true
              && game.bindings.light_map.enabledNode.value === true;
          }
        """)
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            const {requestRender} = await import('./js/render-scheduler.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              }
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0.8, 0, 0.35));
            key.intensity = 1;
            requestRender();
          }
        """)
        page.wait_for_timeout(400)
        shadowed_pixel = _sample_mesh_pixel(page)
        before_version = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          window.__missingLightmapMaterial = mesh.material;
          return mesh.material.version;
        }""")
        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: mesh.userData.normalDataKey,
            light_map: null,
            material_map: null,
          });
        }""")
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.light_map?.enabledNode?.value === false
        """)
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            bound: mesh.material.userData.gameMaterial
              .bindings.light_map.enabledNode.value,
            sameMaterial: mesh.material === window.__missingLightmapMaterial,
            version: mesh.material.version,
          };
        }""")
        missing_pixel = _sample_mesh_pixel(page)
        assert state["bound"] is False
        assert state["version"] == before_version
        assert state["sameMaterial"]
        assert sum(missing_pixel) > sum(shadowed_pixel), (
            shadowed_pixel, missing_pixel)
    finally:
        context.close()
















def test_controls_precede_inspector_and_are_the_default_right_dock_tab(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        assert page.locator(".right-dock-tabs > button").evaluate_all(
            "tabs => tabs.map(tab => tab.id)") == ["controls-tab", "inspector-tab"]
        assert page.locator("#controls-tab").get_attribute(
            "aria-selected") == "true"
        assert page.locator("#controls-panel").is_visible()
        assert page.locator("#inspector-panel").is_hidden()

        page.locator("#inspector-tab").click()
        page.reload()
        page.wait_for_function("window.modViewer !== undefined")
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        assert page.locator("#inspector-tab").get_attribute(
            "aria-selected") == "true"
        assert page.locator("#inspector-panel").is_visible()
        assert page.locator("#controls-panel").is_hidden()
    finally:
        context.close()


def test_app_version_is_centered_in_footer(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {})
    try:
        for width in (1280, 600):
            page.set_viewport_size({"width": width, "height": 720})
            centers = page.evaluate("""() => {
              const footer = document.querySelector('#footer').getBoundingClientRect();
              const version = document.querySelector('#app-version').getBoundingClientRect();
              return {
                footer: footer.left + footer.width / 2,
                version: version.left + version.width / 2,
              };
            }""")
            assert abs(centers["footer"] - centers["version"]) < 0.5
    finally:
        context.close()


def test_texture_rows_are_reused_for_control_changes_and_rebuilt_for_pool_changes(
        edge_browser, frontend_url):
    pick = {
        "tex_key": "diffuse::added.png", "file": "added.png", "uri":
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLkWQAAAABJRU5ErkJggg==",
    }
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")}, picks=[pick])
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        page.locator(".draw-item").first.click()
        page.evaluate("""() => {
          window.__meshStateEvents = 0;
          window.addEventListener('mod-viewer-mesh-state-changed', () => {
            window.__meshStateEvents += 1;
          });
        }""")
        page.locator(".inspector-texture-option", has_text="A two").click()
        assert page.evaluate("window.__meshStateEvents") == 1
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manualTexOverride"
            " === 'diffuse::A-two.png'")
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.inspector-texture-option')]")

        page.locator("#controls-tab").click()
        page.locator("#toggle-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")
        page.locator("#menu-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")
        page.locator("#menu-list .menu-slider").evaluate(
            "input => { input.value = '0.5'; input.dispatchEvent(new Event('input', {bubbles: true})); }")
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")

        page.locator("#inspector-tab").click()
        page.locator(".inspector-manage-textures").click()
        page.locator("#texm-add").click()
        page.wait_for_function("document.querySelectorAll('.inspector-texture-option').length === 5")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.inspector-texture-option')")
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.inspector-texture-option')]")
        page.locator(".texm-row .toggle-icon-btn").last.click()
        page.wait_for_function("document.querySelectorAll('.inspector-texture-option').length === 4")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.inspector-texture-option')")
    finally:
        context.close()


def test_record_handler_is_replaced_and_restored(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        cycle = page.locator("#toggle-list .toggle-cycle-btn")
        cycle.wait_for()
        original_label = page.locator("#toggle-list .toggle-value").inner_text()
        original_state = page.evaluate(
            "import('./js/visibility.js').then(module => module.getToggleState())")
        page.evaluate("""
          () => { window.__cycleHandler = document.querySelector('#toggle-list .toggle-cycle-btn').onclick; }
        """)
        page.locator("#toggle-list [title^='Record']").click()
        page.locator("#toggle-list .toggle-row.recording").wait_for()
        recording_state = page.evaluate(
            "import('./js/visibility.js').then(module => module.getToggleState())")
        assert page.evaluate("window.__cycleHandler !== document.querySelector('#toggle-list .toggle-cycle-btn').onclick")
        page.locator("#toggle-list .toggle-record-cancel").click()
        assert page.evaluate("window.__cycleHandler === document.querySelector('#toggle-list .toggle-cycle-btn').onclick")
        restored_state = page.evaluate(
            "import('./js/visibility.js').then(module => module.getToggleState())")
        assert page.locator("#toggle-list .toggle-value").inner_text() == original_label, (
            original_state, recording_state, restored_state)
    finally:
        context.close()




def test_glossy_tool_applies_to_all_mesh_materials(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        assert page.locator("#glossy-btn").get_attribute("class") == "tool-btn off"
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "false"
        before = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert before == [1]

        page.locator("#glossy-btn").click()
        glossy = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert glossy == [0.2]
        assert page.locator("#glossy-btn").get_attribute("aria-label") == (
            "Glossy materials: on")
        assert "off" not in (page.locator("#glossy-btn").get_attribute("class") or "")
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "true"

        page.locator("#glossy-btn").click()
        restored = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert restored == [1]
        assert "off" in (page.locator("#glossy-btn").get_attribute("class") or "")
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "false"
    finally:
        context.close()


def test_outline_child_shares_geometry_and_render_modes_suppress_it(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        structure = page.evaluate("""async () => {
          const THREE = await import('three');
          const mesh = window.modViewer.activeMeshes[0];
          const outline = mesh.children.filter(
            child => child.userData.isViewerOutline);
          const item = outline[0];
          return {
            count: outline.length,
            sharedGeometry: item?.geometry === mesh.geometry,
            isMeshBasicNodeMaterial: item?.material?.isMeshBasicNodeMaterial === true,
            side: item?.material?.side,
            backSide: THREE.BackSide,
            depthTest: item?.material?.depthTest,
            depthWrite: item?.material?.depthWrite,
            renderOrder: item?.renderOrder,
            baseRenderOrder: mesh.renderOrder,
            map: item?.material?.map || null,
            state: window.modViewer.getOutlineState(0),
          };
        }""")
        assert structure["count"] == 1
        assert structure["sharedGeometry"]
        assert structure["isMeshBasicNodeMaterial"]
        assert structure["side"] == structure["backSide"]
        assert structure["depthTest"] is True
        assert structure["depthWrite"] is False
        assert structure["renderOrder"] > structure["baseRenderOrder"]
        assert structure["map"] is None
        assert structure["state"] == {
            "attached": True, "visible": False, "globalEnabled": False,
            "widthPixels": 1.5, "suppressedByWireframe": False,
            "suppressedByDebug": False,
        }

        page.locator("#outline-btn").click()
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            "attached": True, "visible": True, "globalEnabled": True,
            "widthPixels": 1.5, "suppressedByWireframe": False,
            "suppressedByDebug": False,
        }
        page.locator("#wire-btn").click()
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            "attached": True, "visible": False, "globalEnabled": True,
            "widthPixels": 1.5, "suppressedByWireframe": True,
            "suppressedByDebug": False,
        }
        page.locator("#wire-btn").click()
        page.evaluate("window.modViewer.setMaterialDebugMode('shadow-mask')")
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            "attached": True, "visible": False, "globalEnabled": True,
            "widthPixels": 1.5, "suppressedByWireframe": False,
            "suppressedByDebug": True,
        }
        page.evaluate("window.modViewer.setMaterialDebugMode('off')")
        assert page.evaluate("window.modViewer.getOutlineState(0).visible")

        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            "attached": True, "visible": True, "globalEnabled": True,
            "widthPixels": 1.5, "suppressedByWireframe": False,
            "suppressedByDebug": False,
        }
    finally:
        context.close()














def test_source_grouping_and_collapse_are_shared_without_losing_duplicates(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"Single": _payload("Single"), "Sources": _source_payload()},
    )
    try:
        _open(page, "Single")
        page.locator(".draw-item").wait_for()
        assert page.locator("#mesh-list .mesh-src-hdr").count() == 0
        assert page.locator("#toggle-list .toggle-src-hdr").count() == 0
        assert page.locator("#menu-list .toggle-src-hdr").count() == 0

        page.evaluate("window.__oldDrawRow = document.querySelector('.draw-item')")
        _open(page, "Sources")
        page.wait_for_function(
            "document.querySelector('.draw-item') !== window.__oldDrawRow")
        pool_identity = page.evaluate("""() => {
          const meshes = window.modViewer.activeMeshes;
          const root = meshes[0];
          const nested = meshes.slice(1);
          return {
            poolCount: new Set(meshes.map(mesh => mesh.userData.texturePool)).size,
            nestedShared: nested[0]?.userData.texturePool
              === nested[1]?.userData.texturePool,
            rootDistinct: root?.userData.texturePool
              !== nested[0]?.userData.texturePool,
          };
        }""")
        expected = ["Root.ini", "variants/sub"]
        assert page.locator("#mesh-list .mesh-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#toggle-list .toggle-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#menu-list .toggle-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#mesh-list .group-hdr .group-name").all_inner_texts() == ["Body", "Body"]
        assert page.locator("#toggle-list .toggle-name").all_inner_texts() == ["Duplicate", "Duplicate"]
        assert pool_identity["poolCount"] == 2
        assert pool_identity["nestedShared"]
        assert pool_identity["rootDistinct"]

        first_header = page.locator("#mesh-list .mesh-src-hdr").first
        first_header.click()
        assert page.locator("#mesh-list .mesh-src-items.collapsed").count() == 1
        assert first_header.locator(".group-toggle.collapsed").count() == 1
        first_header.click()
        assert page.locator("#mesh-list .mesh-src-items.collapsed").count() == 0
    finally:
        context.close()




def test_toggle_panel_headers_collapse_and_expand_their_content(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator("#toggle-list .toggle-item").wait_for()
        page.locator("#menu-list .menu-item").first.wait_for()

        for panel_id, content_id in (("toggle-panel", "toggle-list"),
                                     ("menu-panel", "menu-list")):
            panel = page.locator(f"#{panel_id}")
            content = page.locator(f"#{content_id}")
            chevron = panel.locator(".panel-hdr .group-toggle")

            assert "collapsed" not in (content.get_attribute("class") or "")
            chevron.click()
            assert "collapsed" in (content.get_attribute("class") or "")
            assert chevron.get_attribute("aria-expanded") == "false"
            assert not content.is_visible()

            chevron.click()
            assert "collapsed" not in (content.get_attribute("class") or "")
            assert chevron.get_attribute("aria-expanded") == "true"
            assert content.is_visible()
    finally:
        context.close()


def test_feature_flag_css_keeps_cycle_preview_and_core_invariants(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator("#toggle-list .toggle-cycle-btn").wait_for()
        assert page.locator("#present-list").inner_text() == (
            "No key or menu toggle is available for PRESENT.")
        assert page.locator("#present-action-btn").is_enabled()
        page.evaluate("""
          document.body.classList.add('feature-export-off', 'feature-modify-toggle-off')
        """)
        assert page.locator("#export-btn").is_hidden()
        assert page.locator("#toggle-add-btn").is_hidden()
        assert page.locator("#toggle-list .toggle-actions").is_hidden()
        assert page.locator("#toggle-list .toggle-cycle-btn").is_visible()

        css = (paths.web_dir() + "/css/app.css")
        with open(css, encoding="utf-8") as handle:
            source = handle.read().replace(" ", "")
        assert "#menu-list.image-layout.collapsed{display:none;}" in source
        with open(paths.web_dir() + "/index.html", encoding="utf-8") as handle:
            html = handle.read()
        assert "https://cdn" not in html.lower()
        assert "http://" not in html.lower()
    finally:
        context.close()


def test_mod_folder_panel_browses_children_lazily(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    alice = root + r"\Alice"
    astra = root + r"\Astra"
    context, page = _page(
        edge_browser, frontend_url, {},
        mod_folders=[{"name": "Library", "path": root, "exists": True}],
        subfolders={
            root: [
                {"name": "Alice", "path": alice},
                {"name": "Astra", "path": astra},
            ],
            alice: [{"name": "Summer", "path": alice + r"\Summer"}],
        },
    )
    try:
        assert page.locator("#empty-add-folder-btn").inner_text() == "Open Mod Folder"
        _open_library(page)
        assert page.locator("#mod-folder-modal-backdrop.show").count() == 0
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#sidebar").is_hidden()
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"

        root_node = page.locator("#mod-folder-list > .mod-folder-node").first
        arrow_style = page.evaluate("""() => {
          const arrow = document.querySelector('.mod-folder-expand');
          const style = getComputedStyle(arrow);
          return {width: style.width, height: style.height, fontSize: style.fontSize};
        }""")
        assert arrow_style == {"width": "20px", "height": "22px", "fontSize": "18px"}
        root_node.locator(":scope > .mod-folder-row > .mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="Alice").wait_for()
        assert page.evaluate("window.__fakeApi.calls.listSubfolders") == [root]
        assert page.evaluate("window.__fakeApi.calls.loadMod") == []

        alice_node = page.locator(".mod-folder-select", has_text="Alice").locator(
            "xpath=../..")
        alice_node.locator(".mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="Summer").wait_for()
        assert page.evaluate("window.__fakeApi.calls.listSubfolders") == [root, alice]

        root_arrow = root_node.locator(":scope > .mod-folder-row > .mod-folder-expand")
        root_children = root_node.locator(":scope > .mod-folder-children")
        root_arrow.click()
        assert root_children.is_hidden()
        root_arrow.click()
        assert not root_children.is_hidden()
        assert page.evaluate("window.__fakeApi.calls.listSubfolders") == [root, alice]
        page.locator("#mod-library-tab").click()
        assert page.locator("#mod-folder-panel").is_hidden()
        assert page.locator("#sidebar").is_hidden()
        assert page.locator(".left-dock-tabs .active").count() == 0
    finally:
        context.close()


def test_empty_mod_folder_panel_stays_above_navigation_hint(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {}, mod_folders=[])
    try:
        assert page.locator("#empty-add-folder-btn").inner_text() == "Add Mod Folder"
        page.locator("#empty-add-folder-btn").click()
        page.locator("#mod-folder-panel:not([hidden])").wait_for()
        page.locator("#mod-folder-modal-backdrop.show").wait_for()
        page.locator("#mfm-cancel").click()
        panel = page.locator("#mod-folder-panel").bounding_box()
        info = page.locator("#footer").bounding_box()
        assert panel["y"] + panel["height"] <= info["y"]
        assert page.locator("#mod-folder-list").inner_text() == ""
        assert page.evaluate("""() => {
          const panel = document.querySelector('#mod-folder-panel');
          return {inert: panel.inert, ariaHidden: panel.getAttribute('aria-hidden')};
        }""") == {"inert": False, "ariaHidden": "false"}
        page.locator("#mod-library-tab").click()
        assert page.locator("#mod-folder-panel").is_hidden()
        assert page.evaluate("document.querySelector('#mod-folder-panel').inert")
    finally:
        context.close()


def test_mod_folder_name_selection_preserves_dock_state_across_reload(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    alice = root + r"\Alice"
    context, page = _page(
        edge_browser, frontend_url, {alice: _payload("Alice")},
        mod_folders=[{"name": "Library", "path": root, "exists": True}],
        subfolders={root: [{"name": "Alice", "path": alice}]},
    )
    try:
        _open_library(page)
        page.locator("#mod-folder-list > .mod-folder-node .mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="Alice").click()
        page.locator(".draw-item").wait_for(state="attached")
        assert page.evaluate("window.__fakeApi.calls.loadMod") == [alice]
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
        page.locator("#mod-folder-list > .mod-folder-node > .mod-folder-row > .mod-folder-expand").click()
        assert "active-descendant" in page.locator(
            "#mod-folder-list > .mod-folder-node > .mod-folder-row").get_attribute("class")

        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 2")
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
        page.locator("#mod-library-tab").click()
        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 3")
        assert page.locator("#mod-folder-panel").is_hidden()
    finally:
        context.close()


def test_mismatched_toggle_lists_hold_the_last_short_value(
        edge_browser, frontend_url):
    payload = _payload("Cycle")
    payload["controls"]["toggles"]["KeyCycle"]["vars"] = [
        {"var": "short", "default": "0", "values": ["0", "1"]},
        {"var": "long", "default": "0", "values": ["0", "1", "2"]},
    ]
    payload["state"]["defaults"].update({"short": "0", "long": "0"})
    context, page = _page(edge_browser, frontend_url, {"Cycle": payload})
    try:
        _open(page, "Cycle")
        button = page.locator("#toggle-list .toggle-cycle-btn")
        value = page.locator("#toggle-list .toggle-value")
        button.wait_for()
        assert value.inner_text() == "short=0, long=0"
        button.click()
        assert value.inner_text() == "short=1, long=1"
        button.click()
        assert value.inner_text() == "short=1, long=2"
        button.click()
        assert value.inner_text() == "short=0, long=0"
    finally:
        context.close()


def test_shared_control_values_reconcile_as_a_union(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Shared": _payload("Shared")})
    try:
        _open(page, "Shared")
        result = page.evaluate("""async () => {
          const controls = await import('./js/control-state.js');
          controls.setControlValue('shared', '2');
          controls.setControlStateRules([], {shared: '0'}, {
            toggles: {
              KeyA: {vars: [{var: 'shared', values: ['0', '1']}]},
              KeyB: {vars: [{var: 'shared', values: ['0', '2']}]},
            },
            menu: {},
          });
          return controls.getControlState().shared;
        }""")
        assert result == "2"
    finally:
        context.close()


def test_delete_reloads_model_for_geometry_safety(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Delete": _payload("Delete")},
        pending={"Delete": True})
    try:
        _open(page, "Delete")
        page.locator(".draw-item").wait_for()
        page.evaluate("window.__deleteMesh = window.modViewer.activeMeshes[0]")
        page.locator("#toggle-list [title='Delete toggle']").click()
        page.locator("#dialog-backdrop.show").wait_for()
        page.locator("#dialog-ok").click()
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 2")

        assert page.evaluate("window.__fakeApi.calls.meshSemantics") == []
        assert page.evaluate("window.modViewer.activeMeshes[0] !== window.__deleteMesh")
    finally:
        context.close()


def test_export_refreshes_status_without_reloading_model(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Export": _payload("Export")},
        pending={"Export": True})
    try:
        _open(page, "Export")
        page.locator(".draw-item").wait_for()
        page.evaluate("window.__exportMesh = window.modViewer.activeMeshes[0]")
        page.locator("#export-btn").click()
        page.wait_for_function("window.__fakeApi.calls.exportChanges.length === 1")
        page.wait_for_function("!window.__fakeApi.pending.Export")

        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Export"]
        assert page.evaluate(
            "window.modViewer.activeMeshes[0] === window.__exportMesh")
    finally:
        context.close()


def test_record_advances_read_only_vars_across_complete_cycle(
        edge_browser, frontend_url):
    payload = _payload("RecordCycle")
    payload["controls"]["toggles"]["KeyRecordCycle"]["vars"] = [
        {"var": "local", "default": "0", "values": ["0", "1"]},
    ]
    payload["controls"]["toggles"]["KeyRecordCycle"]["cycle_vars"] = [
        {"var": "local", "default": "0", "values": ["0", "1"]},
        {"var": r"\Other\Master\Mode", "default": "0",
         "values": ["0", "1", "2"]},
    ]
    payload["state"]["defaults"].update(
        {"local": "0", r"\Other\Master\Mode": "0"})
    context, page = _page(
        edge_browser, frontend_url, {"RecordCycle": payload})
    try:
        _open(page, "RecordCycle")
        page.evaluate("""
          () => {
            window.pywebview.api.get_record_positions = async () => ({
              positions: 3, vars: ['local'],
            });
          }
        """)
        page.locator("#toggle-list [title^='Record']").click()
        row = page.locator("#toggle-list .toggle-row.recording")
        row.wait_for()
        value = page.locator("#toggle-list .toggle-value")
        assert "local=0" in value.inner_text()
        assert r"\Other\Master\Mode=0" in value.inner_text()

        cycle = page.locator("#toggle-list .toggle-cycle-btn")
        cycle.click()
        cycle.click()
        assert "Position 3 of 3" in value.inner_text()
        assert "local=1" in value.inner_text()
        assert r"\Other\Master\Mode=2" in value.inner_text()
    finally:
        context.close()


def test_reload_preserves_camera_but_switching_mod_resets_it(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"First": _payload("First"), "Second": _payload("Second")},
    )
    try:
        _open(page, "First")
        page.locator(".draw-item").wait_for()
        baseline_model = page.evaluate("""() => ({
          position: window.modViewer.activeMeshes[0].position.toArray(),
          quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
        })""")
        page.locator("#camera-flip-btn").click()
        page.locator("#camera-flip-horizontal-btn").click()
        expected = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene.js');
          camera.position.set(7, 8, 9);
          camera.up.set(0.2, 0.9, 0.3).normalize();
          camera.zoom = 1.7;
          controls.target.set(1, 2, 3);
          camera.updateProjectionMatrix();
          controls.update();
          // Arcball orientation can include roll that position/target/up do
          // not reconstruct after the model fitting pass.
          camera.rotateZ(0.17);
          camera.updateMatrix();
          return {
            position: camera.position.toArray(),
            quaternion: camera.quaternion.toArray(),
            up: camera.up.toArray(),
            target: controls.target.toArray(),
            zoom: camera.zoom,
            modelPosition: window.modViewer.activeMeshes[0].position.toArray(),
            modelQuaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
          };
        }""")

        reloaded = page.evaluate("""async () => {
          await window.modViewer.reloadCurrentMod();
          const {camera, controls} = await import('./js/scene.js');
          return {
            position: camera.position.toArray(),
            quaternion: camera.quaternion.toArray(),
            up: camera.up.toArray(),
            target: controls.target.toArray(),
            zoom: camera.zoom,
            modelPosition: window.modViewer.activeMeshes[0].position.toArray(),
            modelQuaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
          };
        }""")
        assert reloaded["position"] == pytest.approx(expected["position"])
        assert reloaded["quaternion"] == pytest.approx(expected["quaternion"])
        assert reloaded["up"] == pytest.approx(expected["up"])
        assert reloaded["target"] == pytest.approx(expected["target"])
        assert reloaded["zoom"] == pytest.approx(expected["zoom"])
        assert reloaded["modelPosition"] == pytest.approx(expected["modelPosition"])
        assert reloaded["modelQuaternion"] == pytest.approx(
            expected["modelQuaternion"])

        page.locator("#camera-reset-view-btn").click()
        reset_model = page.evaluate("""() => ({
          position: window.modViewer.activeMeshes[0].position.toArray(),
          quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
        })""")
        assert reset_model["position"] == pytest.approx(
            baseline_model["position"])
        assert reset_model["quaternion"] == pytest.approx(
            baseline_model["quaternion"])
        reset_reloaded = page.evaluate("""async () => {
          await window.modViewer.reloadCurrentMod();
          return {
            position: window.modViewer.activeMeshes[0].position.toArray(),
            quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
          };
        }""")
        assert reset_reloaded["position"] == pytest.approx(
            baseline_model["position"])
        assert reset_reloaded["quaternion"] == pytest.approx(
            baseline_model["quaternion"])

        switched = page.evaluate("""async () => {
          await window.modViewer.switchMod('Second');
          const {camera, controls} = await import('./js/scene.js');
          return {
            position: camera.position.toArray(),
            target: controls.target.toArray(),
            modelPosition: window.modViewer.activeMeshes[0].position.toArray(),
            modelQuaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
          };
        }""")
        assert switched["position"] != pytest.approx(expected["position"])
        assert switched["target"] != pytest.approx(expected["target"])
        assert switched["modelPosition"] == pytest.approx(
            baseline_model["position"])
        assert switched["modelQuaternion"] == pytest.approx(
            baseline_model["quaternion"])
    finally:
        context.close()


def test_wuwa_models_start_with_a_180_degree_base_turn(
        edge_browser, frontend_url):
    payload = _payload("WuWa")
    payload["metadata"]["game"] = {
        "id": "wuwa", "runtime": "wwmi", "texture_api": "raw",
        "confidence": "high",
    }
    context, page = _page(edge_browser, frontend_url, {"WuWa": payload})
    try:
        _open(page, "WuWa")
        page.locator(".draw-item").wait_for()

        initial = page.evaluate("""() => ({
          position: window.modViewer.activeMeshes[0].position.toArray(),
          quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
        })""")
        assert initial["quaternion"] == pytest.approx([0, 1, 0, 0])

        page.locator("#camera-reset-view-btn").click()
        reset = page.evaluate("""() => ({
          position: window.modViewer.activeMeshes[0].position.toArray(),
          quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
        })""")
        assert reset["position"] == pytest.approx(initial["position"])
        assert reset["quaternion"] == pytest.approx(initial["quaternion"])

        reloaded = page.evaluate("""async () => {
          await window.modViewer.reloadCurrentMod();
          return window.modViewer.activeMeshes[0].quaternion.toArray();
        }""")
        assert reloaded == pytest.approx([0, 1, 0, 0])
    finally:
        context.close()


def test_present_refresh_keeps_model_identity_and_selection(
        edge_browser, frontend_url):
    payload = _present_payload()
    context, page = _page(edge_browser, frontend_url, {"Present": payload})
    try:
        _open(page, "Present")
        page.locator(".draw-item").wait_for()
        page.evaluate("""async () => {
          const {setToggleValue, refreshAll} = await import('./js/visibility.js');
          setToggleValue('toggle', '1');
          refreshAll();
          window.__presentMesh = window.modViewer.activeMeshes[0];
        }""")
        page.evaluate("""() => {
          window.__fakeApi.responses.Present.controls.present.item.names = ['Zero', 'One'];
        }""")

        assert page.evaluate("window.modViewer.refreshPresentState()") is True
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Present"]
        assert page.evaluate("window.__fakeApi.calls.presentState") == ["Present"]
        assert page.evaluate(
            "window.modViewer.activeMeshes[0] === window.__presentMesh")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.conditions") == []
        assert page.locator("#present-list .toggle-value").inner_text() == "One"
        assert page.evaluate("window.modViewer.refreshPresentState({"
                            "selectedPosition: 0, applySelection: true})") is True
        assert page.evaluate("window.modViewer.activeMeshes[0] === window.__presentMesh")
        assert page.evaluate("window.modViewer.activeMeshes[0] && "
                            "window.__fakeApi.calls.loadMod.length") == 1
        assert page.locator("#present-list .toggle-value").inner_text() == "Zero"
    finally:
        context.close()


def test_control_and_mesh_semantic_refreshes_preserve_existing_meshes(
        edge_browser, frontend_url):
    payload = _payload("Semantic")
    mesh_name = next(iter(payload["meshes"]))
    payload["meshSemantics"] = {
        mesh_name: {"conditions": [[{
            "var": "toggle", "value": "1", "negate": False,
        }]], "sources": payload["meshes"][mesh_name]["sources"],
        "tex_key": "diffuse::Semantic-one.png",
        "texture_variants": [{
            "conditions": [[{
                "var": "toggle", "value": "1", "negate": False,
            }]], "tex_key": "diffuse::Semantic-two.png",
        }],
        "normal_map_key": "normal_map::Semantic-normal.png",
        "normal_map_variants": [{
            "conditions": [[{
                "var": "toggle", "value": "1", "negate": False,
            }]], "tex_key": "normal_map::Semantic-normal-alt.png",
        }],
        "normal_data_key": "normal_data::Semantic-packed.png",
        "light_map_key": "light_map::Semantic-light.png",
        "material_map_key": "material_map::Semantic-material.png"},
    }
    context, page = _page(edge_browser, frontend_url, {"Semantic": payload})
    try:
        _open(page, "Semantic")
        page.locator(".draw-item").wait_for()
        page.evaluate("""async () => {
          const {setToggleValue, refreshAll} = await import('./js/visibility.js');
          setToggleValue('toggle', '1');
          refreshAll();
          window.__semanticMesh = window.modViewer.activeMeshes[0];
          window.__fakeApi.responses.Semantic.controls.toggles.KeySemantic.name = 'Renamed';
        }""")

        assert page.evaluate("window.modViewer.refreshControlSemantics()") is True
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Semantic"]
        assert page.evaluate("window.__fakeApi.calls.controlState") == ["Semantic"]
        assert page.evaluate(
            "window.modViewer.activeMeshes[0] === window.__semanticMesh")
        assert page.locator("#toggle-list .toggle-name").inner_text() == "Renamed"
        assert page.evaluate("""async () => {
          await window.modViewer.refreshMeshSemantics();
          return {
            same: window.modViewer.activeMeshes[0] === window.__semanticMesh,
            conditions: window.modViewer.activeMeshes[0].userData.conditions,
            diffuse: window.modViewer.activeMeshes[0].userData.resolvedTexKey,
            normal: window.modViewer.activeMeshes[0].userData.resolvedNormalMapKey,
          };
        }""") == {
            "same": True,
            "conditions": [[{"var": "toggle", "value": "1", "negate": False}]],
            "diffuse": "diffuse::Semantic-two.png",
            "normal": "normal_map::Semantic-normal-alt.png",
        }
        assert page.evaluate("window.__fakeApi.calls.loadMod.length") == 1
        assert page.evaluate("window.__fakeApi.calls.meshSemantics") == ["Semantic"]
    finally:
        context.close()


def test_record_refreshes_controls_and_meshes_without_reloading_model(
        edge_browser, frontend_url):
    payload = _payload("Record")
    payload["controls"]["toggles"]["KeyRecord"]["wired"] = False
    context, page = _page(
        edge_browser, frontend_url, {"Record": payload},
        pending={"Record": True})
    try:
        _open(page, "Record")
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          window.pywebview.api.record_toggle = async () => {
            window.__fakeApi.responses.Record.controls.toggles.KeyRecord.wired = true;
            return {ok: true, result: {}};
          };
          window.__recordMesh = window.modViewer.activeMeshes[0];
        }""")
        assert page.locator("#toggle-list .toggle-unwired-badge").count() == 1
        assert page.locator("#export-btn").is_disabled()

        page.locator("#toggle-list [title^='Record']").click()
        page.locator("#toggle-list .toggle-row.recording").wait_for()
        page.locator("#toggle-list .toggle-record-save").click()
        page.wait_for_function("window.__fakeApi.calls.controlState.length === 1")

        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Record"]
        assert page.evaluate(
            "window.modViewer.activeMeshes[0] === window.__recordMesh")
        assert page.locator("#toggle-list .toggle-unwired-badge").count() == 0
        assert not page.locator("#export-btn").is_disabled()
    finally:
        context.close()


def test_stale_semantic_response_cannot_modify_new_mod(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"A": _payload("A"), "B": _payload("B")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          const original = window.pywebview.api.get_control_state;
          window.__releaseAControls = null;
          window.pywebview.api.get_control_state = async path => {
            if (path === 'A') {
              await new Promise(resolve => window.__releaseAControls = resolve);
            }
            return original(path);
          };
          window.__staleControls = window.modViewer.refreshControlSemantics();
        }""")
        page.wait_for_function("window.__releaseAControls !== null")

        _open(page, "B")
        page.locator(".draw-item").wait_for()
        page.evaluate("window.__releaseAControls()")
        page.evaluate("async () => await window.__staleControls")

        assert page.locator("#toggle-list .toggle-name").inner_text() == "Toggle B"
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["A", "B"]
    finally:
        context.close()


def test_newer_same_mod_semantic_response_wins(
        edge_browser, frontend_url):
    payload = _payload("Race")
    context, page = _page(edge_browser, frontend_url, {"Race": payload})
    try:
        _open(page, "Race")
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          const original = window.pywebview.api.get_control_state;
          let calls = 0;
          window.__releaseOldControls = null;
          window.pywebview.api.get_control_state = async path => {
            calls += 1;
            if (calls === 1) {
              const stale = await original(path);
              await new Promise(resolve => window.__releaseOldControls = resolve);
              stale.controls.toggles.KeyRace.name = 'Old';
              return stale;
            }
            return original(path);
          };
          window.__oldControls = window.modViewer.refreshControlSemantics();
        }""")
        page.wait_for_function("window.__releaseOldControls !== null")
        page.evaluate("""() => {
          window.__fakeApi.responses.Race.controls.toggles.KeyRace.name = 'Newest';
          window.__newControls = window.modViewer.refreshControlSemantics();
        }""")
        page.evaluate("async () => await window.__newControls")
        page.evaluate("window.__releaseOldControls()")
        page.evaluate("async () => await window.__oldControls")

        assert page.locator("#toggle-list .toggle-name").inner_text() == "Newest"
    finally:
        context.close()


def test_failed_semantic_refresh_still_updates_pending_state(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Failure": _payload("Failure")},
        pending={"Failure": True})
    try:
        _open(page, "Failure")
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          window.pywebview.api.get_mesh_semantics = async () => ({
            error: 'semantic refresh failed',
          });
          window.__failedRefresh = window.modViewer.refreshMeshSemantics();
        }""")
        page.locator("#dialog-backdrop.show").wait_for()
        page.locator("#dialog-ok").click()
        page.evaluate("async () => await window.__failedRefresh")

        assert "show" in (page.locator("#pending-indicator").get_attribute("class") or "")
        assert not page.locator("#export-btn").is_disabled()
    finally:
        context.close()


def test_reload_and_tree_selection_share_one_transition_guard(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    alice = root + r"\Alice"
    astra = root + r"\Astra"
    context, page = _page(
        edge_browser, frontend_url,
        {alice: _payload("Alice"), astra: _payload("Astra")},
        mod_folders=[{"name": "Library", "path": root, "exists": True}],
        subfolders={root: [
            {"name": "Alice", "path": alice},
            {"name": "Astra", "path": astra},
        ]},
    )
    try:
        _open_library(page)
        page.locator("#mod-folder-list > .mod-folder-node .mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="Alice").click()
        page.locator(".draw-item").wait_for(state="attached")

        page.evaluate("""path => {
          window.__fakeApi.blockLoads = {[path]: true};
        }""", alice)
        page.evaluate("""() => {
          window.__reloadPromise = window.modViewer.reloadCurrentMod();
        }""")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 2")

        # The loading veil normally blocks pointer input; force the tree event
        # here to exercise the shared transition guard at the API boundary.
        page.locator(".mod-folder-select", has_text="Astra").click(force=True)
        page.wait_for_timeout(100)
        assert page.evaluate("window.__fakeApi.calls.loadMod") == [alice, alice]

        page.evaluate("path => window.__fakeApi.releaseLoad(path)", alice)
        page.evaluate("window.__reloadPromise")
        assert page.evaluate("window.__fakeApi.calls.loadMod") == [alice, alice]
    finally:
        context.close()


def test_mod_folder_failed_selection_keeps_panel_open_and_reports_error(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    alice = root + r"\Alice"
    broken = root + r"\Broken"
    context, page = _page(
        edge_browser, frontend_url,
        {alice: _payload("Alice"), broken: {"error": "loader failed"}},
        mod_folders=[
            {"name": "Alice", "path": alice, "exists": True},
            {"name": "Broken", "path": broken, "exists": True},
        ],
    )
    try:
        _open_library(page)
        page.locator(".mod-folder-select", has_text="Alice").click()
        page.locator(".draw-item").wait_for(state="attached")
        assert page.locator(".mod-folder-row.active").count() == 1

        page.locator(".mod-folder-select", has_text="Broken").click()
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
        assert page.locator(".mod-folder-row.active").count() == 0
        assert page.locator(".draw-item").count() == 0
        page.locator("#dialog-ok").click()
    finally:
        context.close()


def test_mod_folder_tree_switch_respects_pending_change_confirmation(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    first = root + r"\First"
    second = root + r"\Second"
    context, page = _page(
        edge_browser, frontend_url,
        {first: _payload("First"), second: _payload("Second")},
        pending={first: True},
        mod_folders=[{"name": "Library", "path": root, "exists": True}],
        subfolders={root: [
            {"name": "First", "path": first},
            {"name": "Second", "path": second},
        ]},
    )
    try:
        _open_library(page)
        page.locator("#mod-folder-list > .mod-folder-node .mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="First").click()
        page.locator(".draw-item").wait_for(state="attached")

        page.locator(".mod-folder-select", has_text="Second").click()
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator("#dialog-message").inner_text().startswith(
            "This mod has unsaved changes")
        page.locator("#dialog-cancel").click()
        assert page.evaluate("window.__fakeApi.calls.loadMod") == [first]
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
    finally:
        context.close()


def test_mod_folder_add_edit_delete_modal_flow(
        edge_browser, frontend_url):
    original = _MOD_LIBRARY + r"\Original"
    replacement = _MOD_LIBRARY + r"\Replacement"
    context, page = _page(
        edge_browser, frontend_url, {},
        mod_folders=[{"name": "Original", "path": original, "exists": True}],
    )
    try:
        _open_library(page)

        page.locator("#mod-folder-add").click()
        page.locator("#mod-folder-modal-backdrop.show").wait_for()
        modal_styles = page.evaluate("""() => {
          const style = id => {
            const value = getComputedStyle(document.querySelector(id));
            return {background: value.backgroundColor, color: value.color,
              fontSize: value.fontSize, padding: value.padding};
          };
          return {
            cancel: style('#mfm-cancel'),
            save: style('#mfm-save'),
            browse: style('#mfm-browse'),
            presentCancel: style('#pm-cancel'),
            presentSave: style('#pm-save'),
          };
        }""")
        assert modal_styles["cancel"] == modal_styles["presentCancel"]
        assert modal_styles["save"] == modal_styles["presentSave"]
        assert modal_styles["browse"]["background"] == modal_styles["cancel"]["background"]
        page.evaluate("path => { window.__fakeApi.nextPath = path; }", replacement)
        page.locator("#mfm-browse").click()
        assert page.locator("#mfm-path").input_value() == replacement
        assert page.locator("#mfm-name").input_value() == "Replacement"
        page.locator("#mfm-save").click()
        page.locator("#mod-folder-modal-backdrop.show").wait_for(state="hidden")
        page.locator(".mod-folder-select", has_text="Replacement").wait_for()

        page.locator("[aria-label='More actions for Original']").click()
        original_menu = page.locator(
            ".mod-folder-node", has_text="Original").locator(
            ".mod-folder-action-menu")
        assert page.locator(
            "[aria-label='More actions for Original']").get_attribute(
                "aria-expanded") == "true"
        assert original_menu.evaluate("menu => getComputedStyle(menu).position") == "absolute"
        assert original_menu.evaluate(
            "menu => getComputedStyle(menu).flexDirection") == "column"
        menu_widths = original_menu.evaluate("""menu => {
          const menuBox = menu.getBoundingClientRect();
          return [...menu.querySelectorAll('button')].map(button => ({
            button: button.getBoundingClientRect().width,
            menu: menuBox.width,
          }));
        }""")
        assert all(item["button"] >= item["menu"] - 12 for item in menu_widths)
        assert original_menu.locator("button").all_inner_texts() == [
            "Edit", "Remove"]
        page.locator(".mod-folder-node").filter(has_text="Original").locator(
            ".mod-folder-action-menu button", has_text="Edit").click()
        assert page.locator(
            "[aria-label='More actions for Original']").get_attribute(
                "aria-expanded") == "false"
        page.locator("#mfm-name").fill("Renamed")
        page.locator("#mfm-save").click()
        page.locator(".mod-folder-select", has_text="Renamed").wait_for()

        page.locator("[aria-label='More actions for Renamed']").click()
        page.locator(".mod-folder-node").filter(has_text="Renamed").locator(
            ".mod-folder-action-menu button", has_text="Remove").click()
        page.locator("#dialog-backdrop.show").wait_for()
        assert "Files on disk will not be deleted." in page.locator(
            "#dialog-message").inner_text()
        page.locator("#dialog-ok").click()
        assert page.locator(".mod-folder-select", has_text="Renamed").count() == 0
        assert page.locator(".mod-folder-select", has_text="Replacement").count() == 1
    finally:
        context.close()


def test_missing_opacity_bridge_does_not_block_open_or_mod_library(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"A": _payload("A")},
        panel_opacity_api=False)
    try:
        assert page.locator("#open-btn").is_enabled()
        assert page.locator("#empty-add-folder-btn").is_enabled()
        page.locator("#empty-add-folder-btn").click()
        page.locator("#mod-folder-modal-backdrop.show").wait_for()
        page.locator("#mfm-cancel").click()
        page.locator("#mod-library-tab").click()
        _open(page, "A")
        page.locator("#meshes-tab").click()
        page.locator(".draw-item").wait_for()
    finally:
        context.close()


def test_panel_opacity_control_applies_and_saves_whole_percent(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"A": _payload("A")}, panel_opacity=35)
    try:
        page.wait_for_function("document.querySelector('#panel-opacity').value === '35'")
        _open(page, "A")
        slider = page.locator("#panel-opacity")
        assert slider.get_attribute("min") == "0"
        assert slider.get_attribute("max") == "100"
        assert page.locator("#appearance-btn").get_attribute("aria-label") == (
            "Panel opacity: 35%")
        page.evaluate("""() => document.querySelector('#panel-opacity')
          .dispatchEvent(new Event('change', {bubbles: true}))""")
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == []
        header_controls = page.evaluate("""() => {
          const toolbar = document.querySelector('#toolbar').getBoundingClientRect();
          const environment = document.querySelector('#environment-btn').getBoundingClientRect();
          const appearance = document.querySelector('#appearance-btn').getBoundingClientRect();
          return {
            environmentLeftOfAppearance: environment.right <= appearance.left,
            equalSize: environment.width === appearance.width &&
              environment.height === appearance.height,
            appearanceAtRight: appearance.right >= toolbar.right - 16,
          };
        }""")
        assert header_controls == {
            "environmentLeftOfAppearance": True,
            "equalSize": True,
            "appearanceAtRight": True,
        }
        assert page.locator("#environment-label").count() == 0
        opacity_surfaces = page.locator(
            "#sidebar, .right-dock-tabs, #tool-panel.viewport-toolbar")
        assert set(opacity_surfaces.evaluate_all(
            "panels => panels.map(panel => getComputedStyle(panel).backgroundColor)")) == {
                "rgba(13, 17, 23, 0.35)"}

        page.locator("#appearance-btn").click()
        assert not page.locator("#appearance-popover").is_hidden()
        page.evaluate("""() => {
          const slider = document.querySelector('#panel-opacity');
          slider.value = '100';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        assert page.locator("#panel-opacity-value").inner_text() == "100%"
        assert set(opacity_surfaces.evaluate_all(
            "panels => panels.map(panel => getComputedStyle(panel).backgroundColor)")) == {
                "rgb(13, 17, 23)"}
        assert page.evaluate("""() => {
          const style = getComputedStyle(document.documentElement);
          return [
            style.getPropertyValue('--panel-blur').trim(),
            style.getPropertyValue('--panel-shadow-opacity').trim(),
          ];
        }""") == ["10px", "0.22"]
        page.evaluate("""() => {
          const slider = document.querySelector('#panel-opacity');
          slider.value = '0';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        assert page.locator("#panel-opacity-value").inner_text() == "0%"
        assert set(opacity_surfaces.evaluate_all(
            "panels => panels.map(panel => getComputedStyle(panel).backgroundColor)")) == {
                "rgba(13, 17, 23, 0)"}
        assert page.evaluate("""() => {
          const style = getComputedStyle(document.documentElement);
          return [
            style.getPropertyValue('--panel-blur').trim(),
            style.getPropertyValue('--panel-shadow-opacity').trim(),
          ];
        }""") == ["0px", "0"]
        assert set(opacity_surfaces.evaluate_all(
            "panels => panels.map(panel => getComputedStyle(panel).borderColor)")) == {
                "rgba(139, 148, 158, 0.45)"}
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == []
        page.evaluate("""() => document.querySelector('#panel-opacity')
          .dispatchEvent(new Event('change', {bubbles: true}))""")
        page.wait_for_function("window.__fakeApi.calls.panelOpacity.length === 1")
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == [0]

        page.evaluate("""() => {
          const slider = document.querySelector('#panel-opacity');
          slider.value = '58';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
          slider.dispatchEvent(new Event('change', {bubbles: true}));
        }""")
        page.wait_for_function("window.__fakeApi.calls.panelOpacity.length === 2")
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == [0, 58]
        page.keyboard.press("Escape")
        assert page.locator("#appearance-popover").is_hidden()
        assert page.locator("#appearance-btn").get_attribute("aria-expanded") == "false"
    finally:
        context.close()


def test_panel_opacity_loads_when_native_bridge_becomes_ready(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"A": _payload("A")},
        panel_opacity=27, panel_opacity_api=False)
    try:
        assert page.locator("#panel-opacity").input_value() == "58"
        page.evaluate("""() => {
          const state = window.__fakeApi;
          window.pywebview.api.get_panel_opacity = async () => ({value: state.panelOpacity});
          window.pywebview.api.set_panel_opacity = async value => {
            state.panelOpacity = value;
            state.calls.panelOpacity.push(value);
            return {value};
          };
          window.dispatchEvent(new Event('pywebviewready'));
        }""")
        page.wait_for_function("document.querySelector('#panel-opacity').value === '27'")
        assert page.locator("#appearance-btn").get_attribute("aria-label") == (
            "Panel opacity: 27%")
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == []
    finally:
        context.close()


def test_inspector_follows_component_and_mesh_selection(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        assert page.evaluate("document.querySelector('#camera-buttons').parentElement.id") == (
            "viewport-camera-buttons")
        assert not page.locator("#camera-panel").is_visible()
        assert page.locator("#tool-panel").evaluate(
            "panel => getComputedStyle(panel).flexDirection") == "row"
        layout = page.evaluate("""() => {
          const toolbar = document.querySelector('#toolbar').getBoundingClientRect();
          const tabs = document.querySelector('#right-dock .right-dock-tabs').getBoundingClientRect();
          const present = document.querySelector('#present-panel').getBoundingClientRect();
          const gizmo = document.querySelector('#view-gizmo').getBoundingClientRect();
          return {
            toolbarBottom: toolbar.bottom,
            tabsBottom: tabs.bottom,
            presentTop: present.top,
            gizmoTop: gizmo.top,
            gizmoWidth: gizmo.width,
            gizmoHeight: gizmo.height,
            inCanvas: !!document.querySelector('#view-gizmo').closest('#canvas-container'),
          };
        }""")
        assert layout["inCanvas"]
        assert layout["gizmoTop"] >= layout["toolbarBottom"]
        assert layout["gizmoTop"] >= layout["tabsBottom"]
        assert abs(layout["gizmoTop"] - layout["presentTop"]) < 1
        assert layout["gizmoWidth"] == 104
        assert layout["gizmoHeight"] == 104
        page.locator("#view-gizmo .gizmo-axis.positive").first.click()
        assert "collapsed" not in (page.locator("#present-list").get_attribute("class") or "")
        panel_styles = page.locator(
            "#sidebar, #mod-folder-panel, #present-panel, #toggle-panel, "
            "#menu-panel, #inspector-panel, .right-dock-tabs, "
            "#tool-panel.viewport-toolbar").evaluate_all(
                "panels => panels.map(panel => { const style = getComputedStyle(panel); "
                "return {background: style.backgroundColor, backdrop: style.backdropFilter}; })")
        assert panel_styles
        assert {style["background"] for style in panel_styles} == {
            "rgba(13, 17, 23, 0.58)"}
        assert all("blur(5.8px)" in style["backdrop"] for style in panel_styles)
        assert page.locator("#health-btn .ui-icon").count() == 1
        assert page.locator("#wire-btn").get_attribute("aria-pressed") == "false"
        assert page.locator("#interaction-help").inner_text() == "LMB Orbit · RMB Pan · Wheel Zoom"
        assert page.locator("#sidebar > .panel-hdr .group-toggle").evaluate(
            "button => button.tagName") == "BUTTON"
        assert page.locator("#sidebar > .panel-hdr #reset-state-btn").count() == 1
        assert page.locator("#sidebar > .panel-hdr > *").evaluate_all(
            "nodes => nodes.map(node => node.id || node.tagName)") == [
                "BUTTON", "H3", "reset-state-btn"]
        assert page.locator("#sidebar > .panel-hdr .group-toggle").get_attribute(
            "aria-expanded") == "true"
        assert page.locator("#sidebar").is_visible()
        assert page.locator("#meshes-tab").get_attribute("aria-expanded") == "true"
        page.locator("#mod-library-tab").click()
        assert page.locator("#sidebar").is_hidden()
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
        assert page.locator("#sidebar > .panel-hdr .group-toggle").get_attribute(
            "aria-expanded") == "true"
        page.locator("#mod-library-tab").click()
        assert page.locator("#sidebar").is_hidden()
        assert page.locator("#mod-folder-panel").is_hidden()
        assert page.locator(".left-dock-tabs .active").count() == 0
        page.locator("#meshes-tab").click()
        assert page.locator("#sidebar").is_visible()
        assert page.locator("#meshes-tab").get_attribute("aria-expanded") == "true"
        page.locator("#reset-state-btn").click()
        assert page.locator("#sidebar > .panel-hdr .group-toggle").get_attribute(
            "aria-expanded") == "true"
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        page.locator("#inspector-content").wait_for()
        assert "Draw calls" in page.locator("#inspector-content").inner_text()
        assert page.locator("#inspector-empty").is_hidden()

        page.locator(".draw-item").first.click()
        assert page.locator("#inspector-content .inspector-header h3").inner_text()
        assert "Body A >" in page.locator("#selected-mesh-status").inner_text()
        assert page.locator(".draw-item.selected").count() == 1
        page.evaluate("import('./js/selection.js').then(({clearSelection}) => clearSelection())")
        assert page.locator("#inspector-empty").is_visible()
        assert page.locator("#inspector-content").is_hidden()

        page.locator(".group-hdr .group-name").first.click()
        assert page.locator(".draw-item.selected").count() == 0
        assert "selected" in page.locator(".group-hdr").first.get_attribute("class")
        assert page.locator("#inspector-content .inspector-row", has_text="1 of 1").count() == 1
        assert page.locator("#inspector-empty").is_hidden()

        page.locator(".draw-item .mesh-state-btn").first.click()
        assert page.locator("#inspector-content .inspector-row", has_text="0 of 1").count() == 1
        eye = page.locator(".draw-item .mesh-state-btn").first
        assert eye.get_attribute("aria-pressed") == "false"
        assert "state-hidden" in eye.get_attribute("class")
        assert "state-manual" in eye.get_attribute("class")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manuallyToggled") is True
        page.locator("#reset-state-btn").click()
        assert page.locator("#inspector-content .inspector-row", has_text="1 of 1").count() == 1
        assert eye.get_attribute("aria-pressed") == "true"
        assert "state-hidden" not in eye.get_attribute("class")
        assert "state-manual" not in eye.get_attribute("class")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manuallyToggled") is False

        eye.click()
        eye.click()
        assert eye.get_attribute("aria-pressed") == "true"
        assert "state-manual" not in eye.get_attribute("class")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manuallyToggled") is False
        page.evaluate("import('./js/visibility.js').then(({refreshAll}) => refreshAll())")
        assert eye.get_attribute("aria-pressed") == "true"
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manuallyToggled") is False

        assert page.locator(".group-hdr .material-kind-select").count() == 0
        assert page.locator(".group-hdr .group-tex-btn").count() == 0

        page.locator("#controls-tab").click()
        assert page.locator("#inspector-panel").is_hidden()
        assert page.locator("#controls-panel").evaluate(
            "panel => getComputedStyle(panel).overflowY") == "auto"
        page.locator("#inspector-tab").click()
        assert not page.locator("#inspector-panel").is_hidden()
    finally:
        context.close()


def test_viewport_toolbar_popovers_and_responsive_overflow(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        page.evaluate(
            "localStorage.setItem('mod-viewer.panel.tool-panel.collapsed', 'true')")
        page.reload()
        page.wait_for_function("window.modViewer !== undefined")
        _open(page, "A")
        page.locator(".draw-item").first.wait_for()
        assert page.locator("#tool-buttons").is_visible()
        toolbar_geometry = page.evaluate("""() => {
          const style = selector => getComputedStyle(document.querySelector(selector));
          return {
            token: getComputedStyle(document.documentElement)
              .getPropertyValue('--toolbar-height').trim(),
            toolbarHeight: style('#toolbar').height,
            canvasTop: style('#canvas-container').top,
            leftDockTop: style('#left-dock').top,
            rightDockTop: style('#right-dock').top,
          };
        }""")
        assert toolbar_geometry == {
            "token": "54px",
            "toolbarHeight": "54px",
            "canvasTop": "54px",
            "leftDockTop": "58px",
            "rightDockTop": "58px",
        }
        toolbar_layout = page.evaluate("""() => {
          const rect = selector => document.querySelector(selector).getBoundingClientRect();
          const style = selector => getComputedStyle(document.querySelector(selector));
          const toolbar = rect('#tool-panel');
          const camera = rect('#viewport-camera-buttons');
          const tools = rect('#tool-buttons');
          return {
            centered: Math.abs(toolbar.left + toolbar.width / 2 - window.innerWidth / 2) < 1,
            sameRow: Math.abs(camera.top + camera.height / 2
              - tools.top - tools.height / 2) < 1,
            toolbarDirection: style('#tool-panel').flexDirection,
            toolbarWrap: style('#tool-panel').flexWrap,
            toolsDisplay: style('#tool-buttons').display,
            toolsDirection: style('#tool-buttons').flexDirection,
            cameraLabelsHidden: [...document.querySelectorAll(
              '#viewport-camera-buttons .camera-btn span')]
              .every(span => getComputedStyle(span).display === 'none'),
          };
        }""")
        assert toolbar_layout == {
            "centered": True,
            "sameRow": True,
            "toolbarDirection": "row",
            "toolbarWrap": "nowrap",
            "toolsDisplay": "flex",
            "toolsDirection": "row",
            "cameraLabelsHidden": True,
        }
        toolbar_center = page.evaluate("""() => {
          const box = document.querySelector('#tool-panel').getBoundingClientRect();
          return box.left + box.width / 2;
        }""")
        page.locator("#inspector-tab").click()
        page.wait_for_function("document.querySelector('#inspector-panel').hidden === false")
        assert abs(page.evaluate("""() => {
          const box = document.querySelector('#tool-panel').getBoundingClientRect();
          return box.left + box.width / 2;
        }""") - toolbar_center) < 1
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        page.wait_for_timeout(250)

        for button_id in ("camera-reset-view-btn", "camera-flip-btn",
                          "camera-flip-horizontal-btn"):
            before = page.evaluate("window.modViewer.getRenderCount()")
            page.locator(f"#{button_id}").click()
            page.wait_for_function(
                "count => window.modViewer.getRenderCount() > count", arg=before)

        page.locator("#environment-btn").click()
        page.locator("#environment-popover:not([hidden])").wait_for()
        page.locator("#environment-popover .ui-popover-option", has_text="Studio").click()
        assert page.locator("#environment-btn").get_attribute("aria-label") == (
            "Environment: Studio. Click to change.")
        assert page.locator("#environment-popover").is_hidden()

        page.locator("#texture-btn").click()
        page.locator("#texture-popover:not([hidden])").wait_for()
        assert page.locator("#texture-btn").get_attribute("aria-expanded") == "true"
        assert page.locator(
            "#texture-popover .ui-popover-option").all_inner_texts() == [
                "All maps", "Diffuse and NormalMap", "Diffuse only", "No textures"]
        texture_position = page.evaluate("""() => {
          const button = document.querySelector('#texture-btn').getBoundingClientRect();
          const popover = document.querySelector('#texture-popover').getBoundingClientRect();
          return {
            above: popover.bottom <= button.top,
            within: popover.left >= 0 && popover.right <= window.innerWidth
              && popover.top >= 0 && popover.bottom <= window.innerHeight,
          };
        }""")
        assert texture_position == {"above": True, "within": True}
        page.locator("#texture-popover .ui-popover-option", has_text="Diffuse only").click()
        assert page.locator("#texture-btn").get_attribute("aria-label") == (
            "Textures: diffuse only")
        assert page.locator("#texture-btn").get_attribute("aria-expanded") == "false"
        assert page.locator("#texture-popover").is_hidden()

        page.locator("#light-btn").click()
        page.locator("#light-popover:not([hidden])").wait_for()
        assert page.locator("#light-btn").get_attribute("aria-expanded") == "true"
        light_position = page.evaluate("""() => {
          const button = document.querySelector('#light-btn').getBoundingClientRect();
          const popover = document.querySelector('#light-popover').getBoundingClientRect();
          return {
            above: popover.bottom <= button.top,
            within: popover.left >= 0 && popover.right <= window.innerWidth
              && popover.top >= 0 && popover.bottom <= window.innerHeight,
          };
        }""")
        assert light_position == {"above": True, "within": True}
        assert page.locator("#light-popover .ui-popover-option").all_inner_texts() == [
            "Bright", "Normal", "Off"]
        page.locator("#light-popover .ui-popover-option", has_text="Normal").click()
        assert page.locator("#light-popover").is_hidden()
        assert page.locator("#light-btn").get_attribute("aria-expanded") == "false"
        assert page.locator("#light-btn").get_attribute("aria-label").startswith("Key light: normal")

        page.locator("#light-btn").click()
        page.locator("#light-popover:not([hidden])").wait_for()
        assert page.locator("#light-popover .ui-popover-option").all_inner_texts() == [
            "Bright", "Normal", "Off"]
        page.locator("#light-btn").click()
        assert page.locator("#light-popover").is_hidden()

        page.locator("#environment-btn").click()
        page.locator("#environment-popover:not([hidden])").wait_for()
        assert page.locator("#environment-btn").get_attribute("aria-expanded") == "true"
        page.keyboard.press("Escape")
        assert page.locator("#environment-popover").is_hidden()
        assert page.locator("#environment-btn").get_attribute("aria-expanded") == "false"

        page.set_viewport_size({"width": 640, "height": 720})
        page.locator("#toolbar-more").wait_for()
        assert page.locator("#tool-panel").evaluate(
            "panel => getComputedStyle(panel).flexWrap") == "nowrap"
        assert page.locator("#tool-panel").evaluate(
            "panel => getComputedStyle(panel).overflowX") == "auto"
        assert page.locator("#health-btn .health-label").is_hidden()
        page.locator("#toolbar-more").click()
        assert not page.locator("#toolbar-overflow").is_hidden()
        assert page.locator("#toolbar-overflow [data-toolbar-target='health-btn']").count() == 1

        assert page.evaluate("""() => {
          const panel = document.querySelector('#mod-folder-panel');
          return panel.inert && panel.getAttribute('aria-hidden') === 'true';
        }""")
        page.locator("#mod-library-tab").click()
        assert page.evaluate("document.querySelector('#mod-folder-panel').inert === false")
        page.locator("#mod-library-tab").click()
        assert page.evaluate("""() => {
          const panel = document.querySelector('#mod-folder-panel');
          return panel.inert && panel.getAttribute('aria-hidden') === 'true';
        }""")
    finally:
        context.close()
