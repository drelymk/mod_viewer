from .support import *

def test_startup_mod_uses_switch_flow_and_disabled_ini_setting(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Startup": _payload("Startup")},
        startup_request={"path": "Startup", "disabled_ini": True})
    try:
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 1")
        page.locator(".draw-item").wait_for()

        assert page.evaluate("window.__fakeApi.calls.consumeStartupRequest") == [True]
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Startup"]
        assert page.evaluate("window.__fakeApi.calls.loadModArgs") == [["Startup", True]]
        assert page.locator("#open-disabled-mod").is_checked()
        assert page.evaluate("window.modViewer.getCurrentSource().path") == "Startup"
    finally:
        context.close()


def test_startup_mod_waits_for_pywebview_ready(edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Startup": _payload("Startup")},
        startup_request={"path": "Startup"}, startup_api_ready=False)
    try:
        assert page.evaluate("window.__fakeApi.calls.loadMod") == []
        page.evaluate("""() => {
          const state = window.__fakeApi;
          window.pywebview.api.consume_startup_request = async () => {
            state.calls.consumeStartupRequest.push(true);
            const request = state.startupRequest;
            state.startupRequest = null;
            return structuredClone(request);
          };
          window.dispatchEvent(new Event('pywebviewready'));
        }""")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 1")

        assert page.evaluate("window.__fakeApi.calls.consumeStartupRequest") == [True]
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Startup"]
    finally:
        context.close()


def test_invalid_startup_request_shows_dialog_and_keeps_viewer_usable(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Unused": _payload("Unused")},
        startup_request={"error": "Startup mod folder does not exist: missing"})
    try:
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator("#dialog-message").inner_text() == (
            "Could not open startup mod:\n\n"
            "Startup mod folder does not exist: missing")
        assert page.evaluate("window.__fakeApi.calls.loadMod") == []
        page.locator("#dialog-ok").click()
        assert page.locator("#open-btn").is_enabled()
    finally:
        context.close()


def test_frontend_public_surface_and_lifecycle_events(edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"ContractMod": _payload("ContractMod"),
         "ContractAsset": _payload("ContractAsset")})
    try:
        assert page.evaluate("Object.keys(window.modViewer).sort()") == sorted([
            "activeMeshes", "displayMeshPayload", "exportChanges",
            "getAmbientOcclusionStrength", "getBloomEnabled", "getCurrentSource",
            "getEnvironmentPreset", "getLoadBenchmark", "getMaterialState",
            "getOutlineState", "getRenderCount", "openMod",
            "disableModelPhysics", "enableModelPhysics", "getModelPhysicsState",
            "getWeightPhysicsPerformanceStats",
            "resetWeightPhysicsPerformanceStats",
            "ensureModelWeightsLoaded",
            "getModelWeightState", "setModelWeightHeatmap",
            "refreshControlSemantics", "refreshMeshSemantics",
            "refreshPresentState", "reloadCurrentMod", "setEnvironmentPreset",
            "resetModelPhysicsMotion",
            "setBloomEnabled", "setMaterialDebugMode", "setOutlineEnabled", "switchAsset",
            "setAmbientOcclusionStrength", "switchMod",
        ])
        page.evaluate("""() => {
          window.__lifecycleEvents = [];
          for (const name of [
            'mod-viewer-mod-load-started', 'mod-viewer-mod-loaded',
            'mod-viewer-asset-load-started', 'mod-viewer-asset-loaded',
          ]) {
            window.addEventListener(name, () => window.__lifecycleEvents.push(name));
          }
        }""")

        _open(page, "ContractMod")
        page.locator(".draw-item").wait_for()
        page.evaluate("""async () => await window.modViewer.switchAsset(
          'ContractAsset', {asset: 'ContractAsset', asset_type: 'GIMI'})""")
        page.wait_for_function(
            "window.__fakeApi.calls.loadAsset.length === 1"
            " && window.modViewer.getCurrentSource().kind === 'asset'")
        assert page.evaluate("window.__lifecycleEvents") == [
            "mod-viewer-mod-load-started", "mod-viewer-mod-loaded",
            "mod-viewer-asset-load-started", "mod-viewer-asset-loaded",
        ]
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

def test_failed_mod_switch_clears_previous_ui_and_pending_state(
        edge_browser, frontend_url):
    failed_payload = {
        "error": "loader failed", "health": {"summary": {"issues": 1, "errors": 1}},
    }
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

def test_record_handler_is_replaced_and_restored(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        cycle = page.locator("#toggle-list .toggle-cycle-btn")
        cycle.wait_for()
        original_label = page.locator("#toggle-list .toggle-value").inner_text()
        original_state = page.evaluate(
            "import('./js/mesh/visibility.js').then(module => module.getToggleState())")
        page.evaluate("""
          () => { window.__cycleHandler = document.querySelector('#toggle-list .toggle-cycle-btn').onclick; }
        """)
        page.locator("#toggle-list [title^='Record']").click()
        page.locator("#toggle-list .toggle-row.recording").wait_for()
        recording_state = page.evaluate(
            "import('./js/mesh/visibility.js').then(module => module.getToggleState())")
        assert page.evaluate("window.__cycleHandler !== document.querySelector('#toggle-list .toggle-cycle-btn').onclick")
        page.locator("#toggle-list .toggle-record-cancel").click()
        assert page.evaluate("window.__cycleHandler === document.querySelector('#toggle-list .toggle-cycle-btn').onclick")
        restored_state = page.evaluate(
            "import('./js/mesh/visibility.js').then(module => module.getToggleState())")
        assert page.locator("#toggle-list .toggle-value").inner_text() == original_label, (
            original_state, recording_state, restored_state)
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
          const controls = await import('./js/editing/control-state.js');
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


def test_record_scopes_targets_and_keeps_touched_mesh_snapshots(
        edge_browser, frontend_url):
    payload = _payload("RecordScope")
    body_name = "Body-RecordScope-0"
    body = payload["meshes"][body_name]
    body["conditions"] = [[{
        "var": "toggle", "value": "0", "negate": False,
    }]]
    body["sources"] = [{"ini": "RecordScope.ini", "line": 10,
                         "section": "TextureOverrideBody",
                         "occurrence": {
                             "section": "TextureOverrideBody",
                             "ordinal": 0, "path": [],
                         }}]

    for name, component, line, conditions in [
        ("Face-RecordScope-0", "Face RecordScope", 20, []),
        ("Weapon-RecordScope-0", "Weapon RecordScope", 30, [[{
            "var": "weapon", "value": "1", "negate": False,
        }]]),
    ]:
        entry = copy.deepcopy(body)
        entry["component"] = component
        entry["conditions"] = conditions
        entry["sources"] = [{"ini": "RecordScope.ini", "line": line,
                              "section": "TextureOverrideBody",
                              "occurrence": {
                                  "section": "TextureOverrideBody",
                                  "ordinal": (1 if line == 20 else 2),
                                  "path": [],
                              }}]
        entry["texture_variants"] = []
        entry["shape_targets"] = []
        payload["meshes"][name] = entry

    payload["controls"]["toggles"]["KeyWeapon"] = {
        "name": "Weapon", "ini": "RecordScope.ini", "section": "KeyWeapon",
        "wired": True,
        "vars": [{"var": "weapon", "default": "1", "values": ["0", "1"]}],
    }
    payload["state"]["defaults"]["weapon"] = "1"
    context, page = _page(
        edge_browser, frontend_url, {"RecordScope": payload})
    try:
        _open(page, "RecordScope")
        page.locator(".draw-item").first.wait_for()
        page.evaluate("""
          () => {
            window.pywebview.api.get_record_positions = async () => ({
              positions: 3, vars: ['toggle'],
            });
          }
        """)

        page.locator(".toggle-item").first.locator(
            "[title^='Record']").click()
        page.locator(".toggle-row.recording").wait_for()

        cycle = page.locator(".toggle-row.recording .toggle-cycle-btn")
        cycle.click()
        page.locator(".draw-item .mesh-state-btn").nth(1).click()
        assert page.locator(".toggle-row.recording").count() == 1
        cycle.click()
        assert page.locator(".toggle-row.recording").count() == 1
        page.locator(".toggle-item:has(.toggle-row.recording) .toggle-record-save").click()
        page.wait_for_function("window.__fakeApi.calls.recordToggle.length === 1")

        call = page.evaluate("window.__fakeApi.calls.recordToggle[0]")
        assert call[0:3] == ["RecordScope", "RecordScope.ini", "KeyRecordScope"]
        assert call[3] == {"0": [10, 20], "1": [], "2": [20]}
        assert call[4] == [
            {"ini": "RecordScope.ini", "line": 10, "section": "TextureOverrideBody", "drawindexed": [3, 0, 0],
             "occurrence": {"section": "TextureOverrideBody", "ordinal": 0, "path": []}},
            {"ini": "RecordScope.ini", "line": 20, "section": "TextureOverrideBody", "drawindexed": [3, 0, 0],
             "occurrence": {"section": "TextureOverrideBody", "ordinal": 1, "path": []}},
        ]
    finally:
        context.close()


def test_record_uses_source_conditions_for_deduplicated_meshes(
        edge_browser, frontend_url):
    payload = _payload("RecordSources")
    body = payload["meshes"]["Body-RecordSources-0"]
    body["conditions"] = [
        [{"var": "toggle", "value": "0", "negate": False}],
        [{"var": "other", "value": "1", "negate": False}],
    ]
    body["sources"] = [
        {
            "ini": "RecordSources.ini", "line": 10,
            "section": "TextureOverrideBody",
            "occurrence": {
                "section": "TextureOverrideBody", "ordinal": 0, "path": [],
            },
            "conditions": [[{
                "var": "toggle", "value": "0", "negate": False,
            }]],
        },
        {
            "ini": "RecordSources.ini", "line": 20,
            "section": "TextureOverrideBody",
            "occurrence": {
                "section": "TextureOverrideBody", "ordinal": 1, "path": [],
            },
            "conditions": [[{
                "var": "other", "value": "1", "negate": False,
            }]],
        },
    ]
    payload["controls"]["toggles"]["KeyOther"] = {
        "name": "Other", "ini": "RecordSources.ini", "section": "KeyOther",
        "wired": True,
        "vars": [{"var": "other", "default": "1", "values": ["0", "1"]}],
    }
    payload["state"]["defaults"]["other"] = "1"
    context, page = _page(
        edge_browser, frontend_url, {"RecordSources": payload})
    try:
        _open(page, "RecordSources")
        page.locator(".draw-item").first.wait_for()
        page.evaluate("""
          () => {
            window.pywebview.api.get_record_positions = async () => ({
              positions: 2, vars: ['toggle'],
            });
          }
        """)

        page.locator(".toggle-item").first.locator(
            "[title^='Record']").click()
        page.locator(".toggle-row.recording").wait_for()
        page.locator(".toggle-item:has(.toggle-row.recording) .toggle-record-save").click()
        page.wait_for_function("window.__fakeApi.calls.recordToggle.length === 1")

        call = page.evaluate("window.__fakeApi.calls.recordToggle[0]")
        assert call[3] == {"0": [10], "1": []}
        assert [ref["line"] for ref in call[4]] == [10]
        assert call[4][0]["occurrence"]["ordinal"] == 0
    finally:
        context.close()


def test_add_toggle_refreshes_mesh_provenance_before_record(
        edge_browser, frontend_url):
    payload = _payload("RecordAdd")
    mesh_name = "Body-RecordAdd-0"
    body = payload["meshes"][mesh_name]
    body["sources"] = [{
        "ini": "RecordAdd.ini", "line": 10,
        "section": "TextureOverrideBody",
        "occurrence": {"section": "TextureOverrideBody", "ordinal": 0, "path": []},
    }]
    context, page = _page(
        edge_browser, frontend_url, {"RecordAdd": payload})
    try:
        _open(page, "RecordAdd")
        page.locator(".draw-item").first.wait_for()
        page.evaluate("""
          () => {
            const state = window.__fakeApi;
            const response = state.responses.RecordAdd;
            response.controls.toggles.KeyAdded = {
              name: 'Added', ini: 'RecordAdd.ini', section: 'KeyAdded',
              wired: false,
              vars: [{var: 'added', default: '0', values: ['0', '1']}],
            };
            response.state.defaults.added = '0';
            response.meshSemantics = {
              ["Body-RecordAdd-0"]: {
                conditions: [[{var: 'added', value: '0', negate: false}]],
                sources: [{
                  ini: 'RecordAdd.ini', line: 17,
                  section: 'TextureOverrideBody',
                  occurrence: {section: 'TextureOverrideBody', ordinal: 0, path: []},
                }],
                identity: response.meshes['Body-RecordAdd-0'].identity || null,
              },
            };
            state.addToggleCalls = [];
            window.pywebview.api.add_toggle = async (...args) => {
              state.addToggleCalls.push(args);
              return {ok: true};
            };
            window.pywebview.api.get_record_positions = async () => ({
              positions: 2, vars: ['added'],
            });
          }
        """)

        page.locator("#toggle-add-btn").click()
        page.locator("#toggle-modal-backdrop.show").wait_for()
        page.locator("#tm-name").fill("Added")
        page.locator("#tm-key").fill("2")
        page.locator("#tm-var").fill("added")
        page.locator("#tm-values").fill("0,1")
        page.locator("#tm-save").click()

        page.wait_for_function(
            "window.modViewer.activeMeshes[0].userData.sources[0].line === 17")
        added_row = page.locator(".toggle-item").filter(has_text="Added")
        added_row.locator("[title^='Record']").click()
        page.locator(".toggle-row.recording").wait_for()
        added_row.locator(".toggle-record-save").click()
        page.wait_for_function("window.__fakeApi.calls.recordToggle.length === 1")

        call = page.evaluate("window.__fakeApi.calls.recordToggle[0]")
        assert call[4][0]["line"] == 17
        assert call[4][0]["section"] == "TextureOverrideBody"
        assert call[4][0]["drawindexed"] == [3, 0, 0]
        assert call[4][0]["occurrence"]["ordinal"] == 0
    finally:
        context.close()


def test_edit_toggle_refreshes_mesh_provenance_before_record(
        edge_browser, frontend_url):
    payload = _payload("RecordEdit")
    context, page = _page(
        edge_browser, frontend_url, {"RecordEdit": payload})
    try:
        _open(page, "RecordEdit")
        page.locator(".draw-item").first.wait_for()
        page.evaluate("""
          () => {
            const state = window.__fakeApi;
            const response = state.responses.RecordEdit;
            response.meshSemantics = {
              ["Body-RecordEdit-0"]: {
                conditions: [],
                sources: [{
                  ini: 'RecordEdit.ini', line: 17,
                  section: 'TextureOverrideBody',
                  occurrence: {section: 'TextureOverrideBody', ordinal: 0, path: []},
                }],
              },
            };
            state.editToggleCalls = [];
            window.pywebview.api.get_toggle_details = async () => ({
              name: 'Toggle RecordEdit', key: '1', back: '',
              vars: {toggle: ['0', '1']},
            });
            window.pywebview.api.edit_toggle = async (...args) => {
              state.editToggleCalls.push(args);
              return {ok: true};
            };
          }
        """)

        page.locator("#toggle-list [title='Edit toggle']").click()
        page.locator("#toggle-modal-backdrop.show").wait_for()
        page.wait_for_function("document.querySelector('#tm-name').value !== ''")
        page.locator("#tm-name").fill("Edited")
        page.locator("#tm-save").click()

        page.wait_for_function(
            "window.modViewer.activeMeshes[0].userData.sources[0].line === 17")
        assert page.evaluate("window.__fakeApi.editToggleCalls.length") == 1
        assert page.evaluate("window.__fakeApi.calls.meshSemantics") == [
            "RecordEdit"]
    finally:
        context.close()


def test_present_delete_refreshes_mesh_provenance_without_reload(
        edge_browser, frontend_url):
    payload = _present_payload("PresentDelete")
    context, page = _page(
        edge_browser, frontend_url, {"PresentDelete": payload})
    try:
        _open(page, "PresentDelete")
        page.locator(".draw-item").first.wait_for()
        page.evaluate("""
          () => {
            const state = window.__fakeApi;
            const response = state.responses.PresentDelete;
            response.controls.present.item = null;
            response.meshSemantics = {
              ["Body-PresentDelete-0"]: {
                conditions: [],
                sources: [{
                  ini: 'PresentDelete.ini', line: 17,
                  section: 'TextureOverrideBody',
                  occurrence: {section: 'TextureOverrideBody', ordinal: 0, path: []},
                }],
              },
            };
            state.deletePresentCalls = [];
            window.pywebview.api.delete_present = async path => {
              state.deletePresentCalls.push(path);
              return {ok: true};
            };
          }
        """)
        page.locator("#present-action-btn").click()
        page.locator("#present-key-remove").click()
        page.locator("#dialog-backdrop.show").wait_for()
        page.locator("#dialog-ok").click()

        page.wait_for_function(
            "window.modViewer.activeMeshes[0].userData.sources[0].line === 17")
        assert page.evaluate("window.__fakeApi.deletePresentCalls") == [
            "PresentDelete"]
        assert page.evaluate("window.__fakeApi.calls.loadMod") == [
            "PresentDelete"]
        assert page.evaluate("window.__fakeApi.calls.meshSemantics") == [
            "PresentDelete"]
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
          const {camera, controls} = await import('./js/scene/scene.js');
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
          const {camera, controls} = await import('./js/scene/scene.js');
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
          const {camera, controls} = await import('./js/scene/scene.js');
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

def test_present_refresh_keeps_model_identity_and_selection(
        edge_browser, frontend_url):
    payload = _present_payload()
    context, page = _page(edge_browser, frontend_url, {"Present": payload})
    try:
        _open(page, "Present")
        page.locator(".draw-item").wait_for()
        page.evaluate("""async () => {
          const {setToggleValue, refreshAll} = await import('./js/mesh/visibility.js');
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
    identity = {
        "version": 5, "key": "mesh:[5,\"Semantic.ini\",\"Body\",null,null,[3,0,0],[null,null,null,null,null,null,null]]",
        "source": "Semantic.ini", "component": "Body", "geometry": None,
        "draw": {"count": 3, "start": 0, "base": 0},
    }
    payload["meshes"][mesh_name]["identity"] = identity
    payload["meshSemantics"] = {
        mesh_name: {"identity": identity, "conditions": [[{
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
          const {setToggleValue, refreshAll} = await import('./js/mesh/visibility.js');
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


def test_frontend_uses_backend_mesh_identity_for_metadata_key(
        edge_browser, frontend_url):
    payload = _payload("Identity")
    mesh_name, entry = next(iter(payload["meshes"].items()))
    entry["identity"] = {
        "version": 5,
        "key": "mesh:[5,\"Identity.ini\",\"Body\",null,null,[3,0,0],[null,null,null,null,null,null,null]]",
        "source": "Identity.ini", "component": "Body", "geometry": None,
        "draw": {"count": 3, "start": 0, "base": 0},
    }
    context, page = _page(edge_browser, frontend_url, {"Identity": payload})
    try:
        _open(page, "Identity")
        page.locator(".draw-item").wait_for()
        assert page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            key: mesh.userData.metadataKey,
            identity: mesh.userData.identity,
          };
        }""") == {
            "key": entry["identity"]["key"],
            "identity": entry["identity"],
        }
        assert page.evaluate("window.modViewer.activeMeshes[0].userData.metadataKey") != (
            "Body::3,0,0")
    finally:
        context.close()


def test_semantic_refresh_rejects_identity_mismatch_without_mutation(
        edge_browser, frontend_url):
    payload = _payload("Mismatch")
    mesh_name, entry = next(iter(payload["meshes"].items()))
    identity_a = {
        "version": 5, "key": "mesh:[5,\"Mismatch.ini\",\"Body\",null,null,[3,0,0],[null,null,null,null,null,null,null]]",
        "source": "Mismatch.ini", "component": "Body", "geometry": None,
        "draw": {"count": 3, "start": 0, "base": 0},
    }
    identity_b = {
        **identity_a,
        "key": "mesh:[5,\"Mismatch.ini\",\"Body\",null,null,[4,0,0],[null,null,null,null,null,null,null]]",
        "draw": {"count": 4, "start": 0, "base": 0},
    }
    entry["identity"] = identity_a
    payload["meshSemantics"] = {mesh_name: {"identity": identity_b}}
    context, page = _page(edge_browser, frontend_url, {"Mismatch": payload})
    try:
        _open(page, "Mismatch")
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          window.__identityBefore = {
            mesh,
            geometry: mesh.geometry,
            material: mesh.material,
            identity: mesh.userData.identity,
          };
          window.__identityRefresh = window.modViewer.refreshMeshSemantics();
        }""")
        page.locator("#dialog-backdrop.show").wait_for()
        page.locator("#dialog-ok").click()
        assert page.evaluate(
            "async () => await window.__identityRefresh") is False
        assert page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            sameMesh: mesh === window.__identityBefore.mesh,
            sameGeometry: mesh.geometry === window.__identityBefore.geometry,
            sameMaterial: mesh.material === window.__identityBefore.material,
            identity: mesh.userData.identity,
          };
        }""") == {
            "sameMesh": True,
            "sameGeometry": True,
            "sameMaterial": True,
            "identity": identity_a,
        }
    finally:
        context.close()


def test_mesh_semantic_refresh_forces_changed_rules_with_unchanged_controls(
        edge_browser, frontend_url):
    payload = _payload("SemanticForce")
    mesh_name = next(iter(payload["meshes"]))
    source = payload["meshes"][mesh_name]["sources"]
    payload["meshSemantics"] = {
        mesh_name: {
            "conditions": [[{
                "var": "menu", "value": "1", "negate": False,
            }]],
            "sources": source,
            "tex_key": "diffuse::SemanticForce-one.png",
            "texture_variants": [{
                "conditions": [[{
                    "var": "menu", "value": "0", "negate": False,
                }]],
                "tex_key": "diffuse::SemanticForce-two.png",
            }],
        },
    }
    context, page = _page(
        edge_browser, frontend_url, {"SemanticForce": payload})
    try:
        _open(page, "SemanticForce")
        page.locator(".draw-item").wait_for()
        before = page.evaluate("""() => ({
          value: window.modViewer.activeMeshes[0].visible,
          texture: window.modViewer.activeMeshes[0].userData.resolvedTexKey,
        })""")
        assert before == {
            "value": True, "texture": "diffuse::SemanticForce-one.png",
        }
        assert page.evaluate("window.modViewer.refreshMeshSemantics()") is True
        after = page.evaluate("""() => ({
          value: window.modViewer.activeMeshes[0].visible,
          texture: window.modViewer.activeMeshes[0].userData.resolvedTexKey,
        })""")
        assert after == {
            "value": False, "texture": "diffuse::SemanticForce-two.png",
        }
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["SemanticForce"]
        assert page.evaluate("window.__fakeApi.calls.meshSemantics") == [
            "SemanticForce"]
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
        assert "Record (⏺)" in page.locator("#export-btn").get_attribute("title")

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
