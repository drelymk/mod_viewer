"""Real-server Edge smoke coverage for frontend state transitions."""

import base64
import json
import struct

import pytest

from app import paths, server

playwright = pytest.importorskip("playwright.sync_api")


def _f32(*values):
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()


def _u32(*values):
    return base64.b64encode(struct.pack(f"<{len(values)}I", *values)).decode()


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
                "texture_options": texture_pool,
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
        "textures": {},
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
        "metadata": {"mesh_names": {}},
        "health": {"summary": {"issues": 0, "errors": 0}, "files": {}, "issues": []},
    }


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
        except playwright.Error as error:
            pytest.skip(f"installed Edge is required for frontend smoke tests: {error}")
        yield browser
        browser.close()


def _page(edge_browser, frontend_url, responses, pending=None, picks=None):
    context = edge_browser.new_context()
    state = {
        "responses": responses,
        "pending": pending or {},
        "picks": picks or [],
    }
    encoded_state = json.dumps(json.dumps(state))
    context.add_init_script(
        """
        {
          const state = window.__fakeApi = JSON.parse(__STATE__);
          const copy = value => value == null ? value : structuredClone(value);
          window.pywebview = { api: {
            select_folder: async () => {
              const path = state.nextPath || null;
              state.nextPath = null;
              return path;
            },
            load_mod: async path => copy(state.responses[path]),
            has_pending_changes: async path => !!state.pending[path],
            discard_changes: async path => { state.pending[path] = false; },
            get_diagnostics: async () => ({summary: {issues: 0, errors: 0}, files: {}, issues: []}),
            save_mesh_textures: async () => ({}),
            save_mesh_names: async () => ({}),
            pick_texture_file: async () => copy(state.picks.shift() || null),
            load_texture_file: async (path, key) => ({tex_key: key, uri: ''}),
            get_record_positions: async () => ({positions: 2, vars: ['toggle']}),
          }};
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


@pytest.mark.parametrize("failed_payload", [
    {"error": "loader failed", "health": {"summary": {"issues": 1, "errors": 1}}},
    {"meshes": {}, "textures": {}, "controls": {}, "state": {},
     "geometry": {"url": "/geometry/missing", "length": 12}},
], ids=["loader-error", "geometry-error"])
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
        assert page.locator("#pending-indicator.show").count() == 0
        assert page.locator("#export-btn").is_disabled()
        assert page.locator("#mod-path").inner_text() == "B"
        assert not page.locator("#ini-view-btn").is_disabled()
        page.locator("#dialog-ok").click()
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
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.tex-item')]")

        page.locator("#toggle-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.tex-item')[i])")
        page.locator("#menu-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.tex-item')[i])")
        page.locator("#menu-list .menu-slider").evaluate(
            "input => { input.value = '0.5'; input.dispatchEvent(new Event('input', {bubbles: true})); }")
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.tex-item')[i])")

        page.locator(".group-tex-btn").click()
        page.locator("#texm-add").click()
        page.wait_for_function("document.querySelectorAll('.tex-item').length === 3")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.tex-item')")
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.tex-item')]")
        page.locator(".texm-row .toggle-icon-btn").last.click()
        page.wait_for_function("document.querySelectorAll('.tex-item').length === 2")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.tex-item')")
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


def test_tool_panel_is_in_left_dock_and_available_before_load(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {})
    try:
        assert page.evaluate("document.querySelector('#tool-panel').parentElement.id") == "left-dock"
        assert page.evaluate("getComputedStyle(document.querySelector('#tool-panel')).position") != "fixed"
        assert page.locator("#tool-panel").is_visible()
        assert page.locator("#tool-buttons .tool-btn").count() == 6
    finally:
        context.close()


def test_control_updates_synchronize_all_current_panels(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Sync": _state_sync_payload()})
    try:
        _open(page, "Sync")
        page.locator("#present-list .toggle-item").wait_for()

        page.locator("#toggle-list .toggle-cycle-btn").click()
        assert "toggle=1" in page.locator("#toggle-list .toggle-value").inner_text()
        menu_values = page.evaluate("""
          Object.fromEntries([...document.querySelectorAll('#menu-list .menu-item')]
            .map(item => [item.querySelector('.menu-name').textContent,
                          item.querySelector('.menu-value').textContent]))
        """)
        assert menu_values["Menu"] == "1"

        page.locator("#menu-list .menu-item").filter(has_text="Menu").locator("button").click()
        menu_values = page.evaluate("""
          Object.fromEntries([...document.querySelectorAll('#menu-list .menu-item')]
            .map(item => [item.querySelector('.menu-name').textContent,
                          item.querySelector('.menu-value').textContent]))
        """)
        assert menu_values["Sibling"] == "1"

        page.locator("#present-list .toggle-cycle-btn").click()
        assert "toggle=0" in page.locator("#toggle-list .toggle-value").inner_text()
        assert page.locator("#menu-list .menu-item").filter(
            has_text="Menu").locator(".menu-value").inner_text() == "0"
    finally:
        context.close()


def test_selection_uses_view_binding_and_reload_replaces_sync_callbacks(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.locator(".group-hdr").click()
        assert page.locator(".group-items.collapsed").count() == 1
        page.evaluate("""
          async () => {
            const row = document.querySelector('.draw-item');
            row.scrollIntoView = () => { window.__selectionScrolled = true; };
            const selection = await import('./js/selection.js');
            selection.selectMesh(window.modViewer.activeMeshes[0]);
          }
        """)
        assert page.locator(".draw-item.selected").count() == 1
        assert page.locator(".group-items.collapsed").count() == 0
        assert page.evaluate("window.__selectionScrolled")

        before = page.evaluate("import('./js/view-sync.js').then(module => module.viewSyncCount())")
        page.evaluate("window.__oldDrawRow = document.querySelector('.draw-item')")
        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("""
          document.querySelector('.draw-item') !== window.__oldDrawRow
            && !document.querySelector('#loading').classList.contains('show')
        """)
        after = page.evaluate("import('./js/view-sync.js').then(module => module.viewSyncCount())")
        assert before == after == 4
    finally:
        context.close()
