"""Reusable browser harness for frontend integration tests."""

import io
import json
import re
from urllib.request import urlopen

import pytest
from PIL import Image

from app.settings import paths as paths
from app.runtime import server as server

playwright = pytest.importorskip("playwright.sync_api")

@pytest.fixture(scope="session")
def frontend_url():
    if not paths.has_vendored_three():
        pytest.skip("frontend smoke tests require the vendored Three.js assets")
    return server.start()


@pytest.fixture(scope="session")
def edge_browser():
    with playwright.sync_playwright() as runtime:
        try:
            # Tests use the local server directly. Ambient proxy discovery can
            # otherwise delay the first request in every isolated context.
            browser = runtime.chromium.launch(
                channel="msedge", headless=True, args=["--no-proxy-server"])
        except playwright.Error:
            pytest.skip("frontend smoke tests require a compatible browser runtime")
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def module_document(frontend_url):
    """Reuse the served import map without duplicating vendor URL rules."""
    with urlopen(frontend_url, timeout=5) as response:
        html = response.read().decode("utf-8")
    import_map = re.search(
        r'<script type="importmap"[^>]*>.*?</script>', html, re.DOTALL)
    assert import_map, "The frontend must provide its vendored module import map"
    return f"<!doctype html><html><head>{import_map[0]}</head><body></body></html>"


@pytest.fixture(scope="module")
def module_context(edge_browser):
    """Reuse browser setup while keeping module pages independently isolated."""
    context = edge_browser.new_context(bypass_csp=True)
    try:
        yield context
    finally:
        context.close()


@pytest.fixture
def module_page(module_context, frontend_url, module_document):
    """Isolate module contracts without starting the viewer or its GPU scene."""
    page = module_context.new_page()
    try:
        # Import subjects explicitly on the real server's origin, with no app
        # entrypoint, editor scripts, UI stylesheet, or renderer startup.
        url = frontend_url.rstrip("/") + "/__module_test__.html"
        page.route(url, lambda route: route.fulfill(
            status=200, content_type="text/html", body=module_document))
        page.goto(url)
        yield page
    finally:
        page.close()


def _page(edge_browser, frontend_url, responses, pending=None, picks=None,
          mod_folders=None, subfolders=None, diagnostics=None, panel_opacity=58,
          panel_opacity_api=True, asset_folders=None, asset_subfolders=None,
          startup_request=None, startup_api_ready=True):
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
        "startupRequest": startup_request,
        "startupApiReady": startup_api_ready,
    }
    encoded_state = json.dumps(json.dumps(state))
    context.add_init_script(
        """
        {
          const state = window.__fakeApi = JSON.parse(__STATE__);
          state.calls = new Proxy({}, {get: (target, key) =>
            target[key] ||= []});
          const copy = value => value == null ? value : structuredClone(value);
          const loadWaiters = {};
          const colorSaveWaiters = [];
          state.releaseColorSaves = () => {
            state.blockColorSaves = false;
            colorSaveWaiters.splice(0).forEach(resolve => resolve());
          };
          state.releaseLoad = path => {
            state.blockLoads = state.blockLoads || {};
            state.blockLoads[path] = false;
            (loadWaiters[path] || []).splice(0).forEach(resolve => resolve());
          };
          window.pywebview = { api: {
            select_folder: async () => {
              const path = state.nextPath || null;
              state.nextPath = null;
              state.calls.selectFolder.push(path);
              return path;
            },
            select_archive_mod: async () => {
              const path = state.nextPath || null;
              state.nextPath = null;
              state.calls.selectArchiveMod.push(path);
              return path;
            },
            select_asset_folder: async () => {
              const path = state.nextPath || null;
              state.nextPath = null;
              state.calls.selectAssetFolder.push(path);
              return path;
            },
            consume_startup_request: async () => {
              state.calls.consumeStartupRequest.push(true);
              const request = state.startupRequest;
              state.startupRequest = null;
              return copy(request);
            },
            load_mod: async (path, disabledIni = false) => {
              state.calls.loadMod.push(path);
              state.calls.loadModArgs.push([path, disabledIni]);
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
            save_texture_color: async (path, texKey, targets, usage, requestId) => {
              state.calls.saveTextureColor.push([
                path, texKey, targets, usage, requestId,
              ]);
              return copy(state.responses[path]?.textureSaveResult || {
                status: 'ok',
                tex_key: texKey,
                affected_tex_keys: texKey ? [texKey] : [],
                saved_meshes: (targets || []).map(target => ({
                  semantic_key: target.semantic_key,
                  metadata_key: target.metadata_key,
                })),
                texture: {file: 'body.dds'},
                backup: {file: 'body.modviewer.bak'},
              });
            },
            get_model_skinning_preview: async path => {
              const single = window.__testSkinningPreview;
              if (typeof single !== 'function') {
                return {status: 'error', error: 'Skin preview unavailable.'};
              }
              const meshes = {};
              const chunks = [];
              let byteLength = 0;
              for (const mesh of window.modViewer?.activeMeshes || []) {
                const key = mesh.userData.semanticKey;
                const entry = await single(path, key);
                const copied = copy(entry);
                if (copied?.status === 'ok' && copied.data?.url) {
                  const response = await fetch(copied.data.url,
                    {cache: 'no-store'});
                  if (!response.ok) {
                    throw new Error(`Skin data download failed (${response.status}).`);
                  }
                  const bytes = new Uint8Array(
                    await response.arrayBuffer());
                  const base = byteLength;
                  byteLength += bytes.byteLength;
                  chunks.push(bytes);
                  const rebase = descriptor => descriptor
                    ? {...descriptor,
                      offset: Number(descriptor.offset || 0) + base}
                    : descriptor;
                  copied.data = {
                    ...copied.data,
                    indices: rebase(copied.data.indices),
                    weights: rebase(copied.data.weights),
                  };
                  delete copied.data.url;
                  delete copied.data.length;
                }
                meshes[key] = copied;
              }
              let data = null;
              if (chunks.length) {
                const bytes = new Uint8Array(byteLength);
                let offset = 0;
                for (const chunk of chunks) {
                  bytes.set(chunk, offset);
                  offset += chunk.byteLength;
                }
                data = {
                  url: URL.createObjectURL(new Blob([bytes])),
                  length: byteLength,
                };
              }
              return copy({
                status: 'ok', saved_bones: [], meshes, data,
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
            save_mesh_color_adjustment: async (path, key, adjustment) => {
              state.calls.saveMeshColorAdjustment.push([path, key, adjustment]);
              if (state.blockColorSaves) {
                await new Promise(resolve => colorSaveWaiters.push(resolve));
              }
              return {};
            },
            save_mesh_names: async () => ({}),
            save_weight_selection: async (_path, bones) => ({
              saved: true, selected_bones: [...bones],
            }),
            save_component_material_kind: async () => ({}),
            pick_texture_file: async () => copy(state.picks.shift() || null),
            get_record_positions: async () => ({positions: 2, vars: ['toggle']}),
            record_toggle: async (path, ini, section, positionLines, targetLines) => {
              state.calls.recordToggle.push(
                [path, ini, section, positionLines, targetLines]);
              return copy({ok: true, result: {}});
            },
          }};
          if (!state.panelOpacityApi) {
            delete window.pywebview.api.get_panel_opacity;
            delete window.pywebview.api.set_panel_opacity;
          }
          if (!state.startupApiReady) {
            delete window.pywebview.api.consume_startup_request;
          }
        }
        """.replace("__STATE__", encoded_state),
    )
    page = context.new_page()
    page.goto(frontend_url)
    page.wait_for_function("window.modViewer !== undefined")
    # Weight/Rig integration tests import their module directly. The app's
    # public browser object intentionally contains no Weight/Rig internals.
    page.evaluate("""async () => {
      window.__testWeightRigRuntime = await import(
        './js/mesh/weight-rig-runtime.js');
    }""")
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
    return _sample_mesh_pixels_at(page, [(x, y)])[0]


def _sample_mesh_pixels_at(page, coordinates):
    """Read all comparison points from the same captured frame."""
    points = page.evaluate("""
      async coordinates => {
        const THREE = await import('three');
        const {camera, renderer} = await import('./js/scene/scene.js');
        const mesh = window.modViewer.activeMeshes[0];
        const rect = renderer.domElement.getBoundingClientRect();
        return coordinates.map(([x, y]) => {
          const projected = new THREE.Vector3(x, y, 0)
            .applyMatrix4(mesh.matrixWorld).project(camera);
          return {
            x: Math.round(rect.left + (projected.x + 1) * rect.width / 2),
            y: Math.round(rect.top + (1 - projected.y) * rect.height / 2),
          };
        });
      }
    """, coordinates)
    image = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    return [image.getpixel((point["x"], point["y"])) for point in points]
