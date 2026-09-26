"""Reusable browser harness for frontend integration tests."""

import copy
import io
import json
import re
from urllib.request import ProxyHandler, build_opener

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
                channel="msedge", headless=True,
                args=["--no-proxy-server", "--enable-unsafe-webgpu",
                      "--disable-gpu-sandbox", "--ignore-gpu-blocklist",
                      "--use-webgpu-adapter=d3d11"])
        except playwright.Error:
            pytest.skip("frontend smoke tests require a compatible browser runtime")
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def module_document(frontend_url):
    """Reuse the served import map without duplicating vendor URL rules."""
    with build_opener(ProxyHandler({})).open(frontend_url, timeout=5) as response:
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


@pytest.fixture
def viewer(edge_browser, frontend_url):
    """Isolated viewer pages with an observable native bridge double."""
    contexts = []

    def create(responses, pending=None, startup=None, native=None):
        context = edge_browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 900})
        contexts.append(context)
        state = json.dumps({
            "pending": pending or {}, "startup": startup,
            "controls": {path: value.get('controls', {}) for path, value in responses.items()},
            "results": native or {},
        })
        context.add_init_script("""
          window.__bridge = __STATE__;
          const state = window.__bridge;
          state.calls = [];
          state.errors = {};
          state.blocked = {};
          state.waiters = {};
          state.iniText = '[SectionFixture]\\nvalue = 0\\n';
          state.release = name => {
            state.blocked[name] = false;
            (state.waiters[name] || []).splice(0).forEach(resolve => resolve());
          };
          const load = async path => {
            const payload = await window.__fixtureLoad(path);
            if (state.controls[path]) payload.controls = structuredClone(state.controls[path]);
            return payload;
          };
          const semantics = async path => {
            const payload = await window.__fixtureLoad(path, false);
            return {meshes: payload.meshes, controls: state.controls[path] || payload.controls,
              state: payload.state, material_profiles: payload.metadata?.material_profiles || {}};
          };
          const stage = path => {state.pending[path] = true; return {ok: true, result: {}};};
          const call = (name, result) => async (...args) => {
            state.calls.push({name, args});
            if (state.errors[name]) throw new Error('fixture failure');
            const value = structuredClone(await (Object.hasOwn(state.results, name)
              ? state.results[name] : typeof result === 'function' ? result(...args) : result));
            if (state.blocked[name]) await new Promise(resolve => (state.waiters[name] ||= []).push(resolve));
            return value;
          };
          window.pywebview = {api: {
            select_folder: call('pick', () => {
              const path = state.nextPath || null;
              state.nextPath = null;
              return path;
            }),
            consume_startup_request: call('startup', () => {const request = state.startup; state.startup = null; return request;}),
            load_mod: call('load', load),
            select_archive_mod: call('archivePick', () => {
              const path = state.nextPath || null; state.nextPath = null; return path;
            }),
            load_asset: call('asset', path => window.__fixtureLoad(path)),
            has_pending_changes: call('pending', path => !!state.pending[path]),
            discard_changes: call('discard', path => {state.pending[path] = false;}),
            export_changes: call('export', path => {
              if (state.exportFailure) return {saved: [], failed: [{ini: 'source-01.ini', error: 'fixture failure'}]};
              state.pending[path] = false;
              return {saved: ['source-01.ini'], failed: []};
            }),
            get_mod_folders: call('folders', {folders: []}),
            get_asset_folders: call('assets', {folders: []}),
            get_language: call('language', {value: 'en'}),
            get_diagnostics: call('diagnostics', {summary: {issues: 0, errors: 0}, files: {}, issues: []}),
            list_ini_files: call('iniList', [{value: 'source-01.ini', label: 'source-01.ini'}]),
            get_ini_text: call('iniRead', (path, ini) => ({ini, text: state.iniText, dirty: !!state.pending[path]})),
            update_ini_text: call('iniUpdate', (path, ini, text) => {
              state.iniText = text; stage(path); return {pending: true};
            }),
            list_toggle_source_inis: call('toggleSources', [{value: 'source-01.ini', label: 'source-01.ini'}]),
            get_toggle_details: call('toggleDetails', () => ({name: 'control-01', key: 'K', back: '', vars: {input01: ['0', '1']}})),
            add_toggle: call('toggleAdd', (path, ini, name, key, variable, values) => {
              state.controls[path].toggles.KeyFixture = {name, ini, section: 'KeyFixture', wired: true,
                vars: [{var: variable, default: values[0], values}]}; return stage(path);
            }),
            edit_toggle: call('toggleEdit', (path, ini, section, changes) => {
              state.controls[path].toggles[section].name = changes.new_name; return stage(path);
            }),
            delete_toggle: call('toggleDelete', (path, ini, section) => {
              delete state.controls[path].toggles[section]; return stage(path);
            }),
            get_record_positions: call('recordPositions', {positions: 2, vars: ['input01']}),
            record_toggle: call('record', stage),
            get_control_state: call('controls', semantics),
            get_mesh_semantics: call('semantics', semantics),
            get_semantic_state: call('semanticState', semantics),
            load_missing_asset_parts: call('fill', async () => ({status: 'loaded', fill_id: 'fill-01',
              payload: await window.__fixtureLoad('fixture-fill')})),
            remove_missing_asset_parts: call('fillRemove', {status: 'removed', removed: true}),
            save_texture_color: call('textureSave', {status: 'error', error_code: 'fixture_rejected'}),
            apply_component_mesh_changes: call('meshApply', stage),
            save_mesh_names: call('names', {}),
            save_mesh_textures: call('textures', {}),
            save_mesh_color_adjustment: call('color', {}),
            get_model_skinning_preview: call('weights', () => window.__fixtureLoad('fixture-weights')),
            load_model_rig: call('rigRead', null),
            save_model_rig: call('rigWrite', {saved: true}),
          }};
        """.replace('__STATE__', state))
        def load_fixture(_source, path, publish=True):
            payload = copy.deepcopy(responses[path])
            blob = payload.pop('_fixture_blob', None)
            if blob is not None and publish:
                descriptor = {
                    'url': server.publish_geometry(blob, replace=False),
                    'length': len(blob) + payload.pop('_fixture_length_delta', 0),
                }
                payload['data' if path == 'fixture-weights' else 'geometry'] = descriptor
            if payload.pop('_fixture_missing_geometry', False):
                payload['geometry']['url'] = '/geometry/fixture-missing'
            return payload

        context.expose_binding('__fixtureLoad', load_fixture)
        page = context.new_page()
        page.goto(frontend_url)
        available = page.evaluate("""async () => {
          if (!navigator.gpu) return false;
          return !!await navigator.gpu.requestAdapter({featureLevel: 'core'});
        }""")
        if not available:
            pytest.skip('viewer tests require a compatible WebGPU adapter')
        page.wait_for_function('window.modViewer !== undefined')
        return page

    yield create
    for context in reversed(contexts):
        context.close()


def open_model(page, path):
    page.evaluate('path => {window.__bridge.nextPath = path;}', path)
    page.locator('#open-btn').click()


def wait_loaded(page, count=1):
    page.wait_for_function('count => window.modViewer.activeMeshes.length === count && !document.querySelector("#loading").classList.contains("show") && !document.querySelector("#open-btn").disabled', arg=count)
    page.locator('.draw-item').first.wait_for()


def bridge_calls(page, name):
    return page.evaluate("name => window.__bridge.calls.filter(call => call.name === name).map(call => call.args)", name)


def mesh_pixel(page):
    """Sample the generated triangle away from edges and UI overlays."""
    return mesh_pixels(page, [[0.25, 0.25, 0]])[0]


def project_mesh_points(page, points):
    return page.evaluate("""async points => {
      const THREE = await import('three/webgpu');
      const {camera, renderer} = await import('./js/scene/scene.js');
      const mesh = window.modViewer.activeMeshes[0];
      const rect = renderer.domElement.getBoundingClientRect();
      return points.map(values => {
        const p = new THREE.Vector3(...values).applyMatrix4(mesh.matrixWorld).project(camera);
        return [Math.round(rect.left + (p.x + 1) * rect.width / 2),
                Math.round(rect.top + (1 - p.y) * rect.height / 2)];
      });
    }""", points)


def mesh_pixels(page, points):
    previous = page.evaluate('window.modViewer.getRenderCount()')
    page.evaluate("""async () => {
      const {requestRender} = await import('./js/scene/render-scheduler.js');
      requestRender();
    }""")
    page.wait_for_function('count => window.modViewer.getRenderCount() > count', arg=previous)
    projected = project_mesh_points(page, points)
    with Image.open(io.BytesIO(page.screenshot())) as image:
        rgb = image.convert('RGB')
        return [rgb.getpixel(tuple(point)) for point in projected]


def wait_texture(page, index=0, role='diffuse'):
    page.evaluate("""async () => {
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      window.__getTexture = getGameMaterialTexture;
    }""")
    page.wait_for_function("""({index, role}) =>
      !!window.__getTexture(window.modViewer.activeMeshes[index]?.material, role)?.image
    """, arg={'index': index, 'role': role})
