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

from app.settings import paths as paths
from app.runtime import server as server
from core.materials.profiles import material_profile_for

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
                "skinning_available": True,
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
        "calls": {"loadMod": [], "loadAsset": [], "listSubfolders": [],
                   "listAssetSubfolders": [],
                   "selectAssetFolder": [],
                   "rebuildAssetIndex": [],
                   "loadMissingAssetParts": [],
                   "removeMissingAssetParts": [],
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
            load_asset: async path => {
              state.calls.loadAsset.push(path);
              return copy(state.responses[path]);
            },
            load_missing_asset_parts: async path => {
              state.calls.loadMissingAssetParts.push(path);
              return copy(state.responses[path]?.assetFillResponse || {
                status: 'nothing_missing',
              });
            },
            remove_missing_asset_parts: async path => {
              state.calls.removeMissingAssetParts.push(path);
              return {status: 'removed', removed: true};
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
                Object.entries(payload.meshes || {}).map(([name, entry]) => {
                  const semantic = {};
                  for (const key of [
                    'conditions', 'sources', 'source', 'component',
                    'identity',
                    'tex_key', 'texture_variants', 'normal_map_key',
                    'normal_map_variants', 'normal_data_key',
                    'normal_data_variants', 'light_map_key',
                    'light_map_variants', 'material_map_key',
                    'material_map_variants', 'emission_map_key',
                    'emission_map_variants', 'asset_binding',
                    'texture_resolution', 'asset_slot_evidence',
                    'material_kind', 'material_kind_reliable',
                    'material_kind_reason', 'material_kind_override',
                    'material_profile_id',
                  ]) {
                    if (Object.hasOwn(entry, key)) semantic[key] = entry[key];
                  }
                  return [name, semantic];
                })
              );
              return copy({
                meshes,
                material_profiles: payload.materialProfiles
                  || payload.metadata?.material_profiles || {},
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
        const {camera, renderer} = await import('./js/scene/scene.js');
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


__all__ = [
    name for name in globals()
    if not name.startswith('__') and name not in {"edge_browser", "frontend_url"}
]
