"""Native loads, staged edit protection, and recovery."""

import pytest

from .payloads import controlled_payload, model_payload
from .support import bridge_calls, open_model, wait_loaded


def test_cancel_switch_then_export_and_switch(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': model_payload(2)},
                  pending={'fixture-01': True})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#pending-indicator.show').wait_for()
    open_model(page, 'fixture-02')
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-cancel').click()
    assert bridge_calls(page, 'load') == [['fixture-01', False]]
    assert bridge_calls(page, 'discard') == []
    assert page.evaluate('window.__bridge.pending["fixture-01"]')
    assert page.evaluate('window.modViewer.getCurrentSource().path') == 'fixture-01'
    page.locator('#export-btn').click()
    page.wait_for_function('!window.__bridge.pending["fixture-01"]')
    assert bridge_calls(page, 'export') == [['fixture-01']]
    assert bridge_calls(page, 'load') == [['fixture-01', False]]
    open_model(page, 'fixture-02')
    wait_loaded(page, 2)
    assert page.evaluate('window.modViewer.getCurrentSource().path') == 'fixture-02'
    assert bridge_calls(page, 'discard') == []


def test_load_error_clears_stale_model_and_allows_recovery(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': {'error': 'fixture failure'}})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    open_model(page, 'fixture-02')
    page.locator('#dialog-backdrop.show').wait_for()
    assert page.evaluate('window.modViewer.activeMeshes.length') == 0
    assert page.locator('.draw-item').count() == 0
    assert page.locator('#export-btn').is_disabled()
    page.locator('#dialog-ok').click()
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert page.evaluate('window.modViewer.getCurrentSource().path') == 'fixture-01'


def test_failed_export_keeps_changes_pending_until_successful_retry(viewer):
    page = viewer({'fixture-01': model_payload()}, pending={'fixture-01': True})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#pending-indicator.show').wait_for()
    page.evaluate('window.__bridge.exportFailure = true')
    page.locator('#export-btn').click()
    page.locator('#dialog-backdrop.show').wait_for()
    assert page.evaluate('window.__bridge.pending["fixture-01"]')
    page.locator('#dialog-ok').click()
    page.wait_for_function('!document.querySelector("#export-btn").disabled')
    assert page.locator('#pending-indicator.show').is_visible()
    page.evaluate('window.__bridge.exportFailure = false')
    page.locator('#export-btn').click()
    page.wait_for_function('!window.__bridge.pending["fixture-01"]')
    page.wait_for_function('!document.querySelector("#pending-indicator").classList.contains("show")')
    assert bridge_calls(page, 'export') == [['fixture-01'], ['fixture-01']]
    assert bridge_calls(page, 'load') == [['fixture-01', False]]


def test_picker_cancellation_preserves_source_and_pending_edits(viewer):
    page = viewer({'fixture-01': model_payload()}, pending={'fixture-01': True})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate('window.__before = window.modViewer.activeMeshes[0]')
    page.locator('#open-btn').click()
    page.wait_for_function('window.__bridge.calls.filter(call => call.name === "pick").length === 2 && !document.querySelector("#open-btn").disabled')
    assert page.evaluate('window.modViewer.activeMeshes[0] === window.__before')
    assert bridge_calls(page, 'load') == [['fixture-01', False]]
    assert bridge_calls(page, 'discard') == []
    assert page.evaluate('window.__bridge.pending["fixture-01"]')


@pytest.mark.parametrize('extension', ['zip', '7z', 'rar'])
def test_archive_picker_routes_to_read_only_preview(viewer, extension):
    path = f'fixture-01.{extension}'
    payload = model_payload()
    payload['metadata'].update(source_kind='archive', source_read_only=True)
    page = viewer({path: payload})
    page.evaluate('path => {window.__bridge.nextPath = path;}', path)
    page.locator('#open-menu-btn').click()
    page.locator('#open-archive-choice').click()
    wait_loaded(page)
    assert bridge_calls(page, 'archivePick') == [[]]
    assert bridge_calls(page, 'pick') == []
    assert bridge_calls(page, 'load') == [[path, False]]
    assert page.evaluate('window.modViewer.getCurrentSource().readOnly')
    assert page.locator('#export-btn').is_disabled()


@pytest.mark.parametrize('startup', [
    {'path': 'fixture-01', 'disabled_ini': True},
    {'error': 'fixture failure'},
], ids=['load', 'error'])
def test_startup_request_is_consumed_once_and_viewer_remains_usable(viewer, startup):
    page = viewer({'fixture-01': model_payload()}, startup=startup)
    if 'error' in startup:
        page.locator('#dialog-backdrop.show').wait_for()
        assert bridge_calls(page, 'load') == []
        page.locator('#dialog-ok').click()
        open_model(page, 'fixture-01')
    wait_loaded(page)
    assert bridge_calls(page, 'startup') == [[]]
    assert bridge_calls(page, 'load') == [['fixture-01', 'error' not in startup]]


@pytest.mark.parametrize('failure', ['missing', 'truncated'])
def test_geometry_transport_failure_never_keeps_a_partial_scene(viewer, failure):
    broken = model_payload(2)
    broken['_fixture_missing_geometry' if failure == 'missing' else '_fixture_length_delta'] = True
    page = viewer({'fixture-01': broken, 'fixture-02': model_payload()})
    open_model(page, 'fixture-01')
    page.locator('#dialog-backdrop.show').wait_for()
    assert page.evaluate('window.modViewer.activeMeshes.length') == 0
    assert page.locator('.draw-item').count() == 0
    assert page.locator('#export-btn').is_disabled()
    page.locator('#dialog-ok').click()
    open_model(page, 'fixture-02')
    wait_loaded(page)


def test_mesh_construction_failure_rolls_back_previously_created_meshes(viewer):
    payload = model_payload(2)
    payload['meshes']['mesh-01']['pos'] = {'offset': 1000000, 'length': 36}
    page = viewer({'fixture-01': payload, 'fixture-02': model_payload()})
    open_model(page, 'fixture-01')
    page.locator('#dialog-backdrop.show').wait_for()
    assert page.evaluate('window.modViewer.activeMeshes.length') == 0
    assert page.locator('.draw-item').count() == 0
    page.locator('#dialog-ok').click()
    open_model(page, 'fixture-02')
    wait_loaded(page)


def test_native_loader_rejection_releases_transition_lock(viewer):
    page = viewer({'fixture-01': model_payload()})
    page.evaluate('window.__bridge.errors.load = true')
    open_model(page, 'fixture-01')
    page.locator('#dialog-backdrop.show').wait_for()
    page.evaluate('delete window.__bridge.errors.load')
    page.locator('#dialog-ok').click()
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert len(bridge_calls(page, 'load')) == 2


def test_confirmed_switch_discards_only_the_previous_pending_session(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': model_payload(2)},
                  pending={'fixture-01': True})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    open_model(page, 'fixture-02')
    page.locator('#dialog-backdrop.show').wait_for()
    assert len(bridge_calls(page, 'load')) == 1
    page.locator('#dialog-ok').click()
    wait_loaded(page, 2)
    assert bridge_calls(page, 'discard') == [['fixture-01']]
    assert page.evaluate('!window.__bridge.pending["fixture-01"]')
    assert page.evaluate('window.modViewer.getCurrentSource().path') == 'fixture-02'


def test_in_flight_load_blocks_competing_transitions(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': model_payload(2)})
    page.evaluate('window.__bridge.blocked.load = true')
    open_model(page, 'fixture-01')
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "load")')
    assert page.locator('#open-btn').is_disabled()
    assert page.evaluate("window.modViewer.switchMod('fixture-02')") is False
    assert bridge_calls(page, 'load') == [['fixture-01', False]]
    page.evaluate('window.__bridge.release("load")')
    wait_loaded(page)
    open_model(page, 'fixture-02')
    wait_loaded(page, 2)


def test_same_source_reload_preserves_pending_session_and_reads_new_geometry(viewer):
    payload = model_payload()
    responses = {'fixture-01': payload}
    page = viewer(responses, pending={'fixture-01': True})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate('window.__before = window.modViewer.activeMeshes[0]')
    responses['fixture-01'] = model_payload(2)
    assert page.evaluate('window.modViewer.reloadCurrentMod()')
    wait_loaded(page, 2)
    assert bridge_calls(page, 'discard') == []
    assert page.evaluate('window.__bridge.pending["fixture-01"]')
    assert page.evaluate('window.modViewer.activeMeshes[0] !== window.__before')


def test_stale_semantic_response_cannot_overwrite_a_new_source(viewer):
    page = viewer({'fixture-01': controlled_payload(), 'fixture-02': model_payload(2)})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate('window.__bridge.blocked.semanticState = true; window.__refresh = window.modViewer.refreshSemanticState(); void 0')
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "semanticState")')
    open_model(page, 'fixture-02')
    wait_loaded(page, 2)
    page.evaluate('window.__bridge.release("semanticState")')
    assert page.evaluate('window.__refresh') is False
    assert page.evaluate('window.modViewer.getCurrentSource().path') == 'fixture-02'
    assert page.locator('#toggle-list .toggle-item').count() == 0
    assert page.locator('.draw-item').count() == 2


def test_semantic_identity_mismatch_leaves_live_meshes_unchanged(viewer):
    payload = model_payload()
    payload['meshes']['mesh-00']['identity'] = {'key': 'identity-01'}
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate("""() => {
      window.__before = window.modViewer.activeMeshes[0];
      window.__bridge.results.semantics = {meshes: {'mesh-00': {
        identity: {key: 'identity-02'}, conditions: [], sources: []}}};
      window.__refresh = window.modViewer.refreshMeshSemantics();
    }""")
    page.locator('#dialog-backdrop.show').wait_for()
    assert page.evaluate('window.modViewer.activeMeshes[0] === window.__before')
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.identity.key') == 'identity-01'
    page.locator('#dialog-ok').click()
    assert page.evaluate('window.__refresh') is False


def test_ini_apply_stages_text_and_export_is_the_only_write_action(viewer):
    page = viewer({'fixture-01': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#ini-view-btn').click()
    page.locator('#ini-editor-backdrop.show').wait_for()
    page.evaluate("window.ace.edit('ini-editor-text').setValue('[SectionFixture]\\nvalue = 1\\n', -1)")
    page.locator('#ini-editor-apply').click()
    page.wait_for_function('window.__bridge.pending["fixture-01"] && window.__bridge.calls.filter(call => call.name === "load").length === 2')
    assert bridge_calls(page, 'export') == []
    assert bridge_calls(page, 'iniUpdate')[0] == ['fixture-01', 'source-01.ini', '[SectionFixture]\nvalue = 1\n']
    page.locator('#ini-editor-backdrop.show').wait_for(state='hidden')
    wait_loaded(page)
    page.locator('#export-btn').click()
    page.wait_for_function('!window.__bridge.pending["fixture-01"]')
    assert bridge_calls(page, 'export') == [['fixture-01']]


def test_toggle_add_edit_delete_follow_staged_refresh_lifecycle(viewer):
    page = viewer({'fixture-01': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#toggle-add-btn').click()
    page.locator('#toggle-modal-backdrop.show').wait_for()
    for field, value in [('name', 'control-01'), ('key', 'K'), ('var', 'input01'), ('values', '0,1')]:
        page.locator(f'#tm-{field}').fill(value)
    page.locator('#tm-save').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "semanticState")')
    page.locator('#toggle-list [aria-label="Edit toggle"]').click()
    page.locator('#tm-name').fill('control-02')
    page.locator('#tm-save').click()
    page.wait_for_function('document.querySelector(".toggle-name").textContent === "control-02"')
    page.locator('#toggle-list [aria-label="Delete toggle"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-ok').click()
    page.wait_for_function('document.querySelectorAll("#toggle-list .toggle-item").length === 0')
    assert len(bridge_calls(page, 'toggleAdd')) == 1
    assert len(bridge_calls(page, 'toggleEdit')) == 1
    assert len(bridge_calls(page, 'toggleDelete')) == 1
    assert len(bridge_calls(page, 'load')) == 2
    assert bridge_calls(page, 'export') == []
    assert page.evaluate('window.__bridge.pending["fixture-01"]')


def test_record_cancel_restores_handler_and_save_sends_all_positions(viewer):
    page = viewer({'fixture-01': controlled_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate('window.__cycleHandler = document.querySelector(".toggle-cycle-btn").onclick')
    record = page.locator('#toggle-list [aria-label="Record toggle mesh visibility"]')
    record.click()
    page.locator('.toggle-row.recording').wait_for()
    page.locator('.toggle-cycle-btn').click()
    page.locator('.toggle-record-cancel').click()
    assert page.evaluate('document.querySelector(".toggle-cycle-btn").onclick === window.__cycleHandler')
    assert bridge_calls(page, 'record') == []
    record.click()
    page.locator('.toggle-row.recording').wait_for()
    assert page.locator('#open-btn').is_disabled()
    page.locator('.toggle-cycle-btn').click()
    page.locator('.toggle-record-save').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "record")')
    request = bridge_calls(page, 'record')[0]
    assert request[:3] == ['fixture-01', 'source-01.ini', 'KeyFixture']
    assert request[3] == {'0': [1], '1': []}
    assert request[4][0]['line'] == 1
    assert bridge_calls(page, 'export') == []


def test_unwired_pending_toggle_blocks_export_but_remains_editable(viewer):
    payload = controlled_payload()
    payload['controls']['toggles']['KeyFixture']['wired'] = False
    page = viewer({'fixture-01': payload}, pending={'fixture-01': True})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert page.locator('#pending-indicator.show').is_visible()
    assert page.locator('#export-btn').is_disabled()
    assert page.locator('.toggle-unwired-badge').count() == 1
    assert page.locator('#toggle-add-btn').is_enabled()
    assert bridge_calls(page, 'export') == []


def test_diagnostic_failure_does_not_fail_model_loading(viewer):
    page = viewer({'fixture-01': model_payload()}, native={'diagnostics': {'error': 'fixture failure'}})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "diagnostics")')
    assert page.locator('#open-btn').is_enabled()
    assert page.evaluate('window.modViewer.getCurrentSource().path') == 'fixture-01'
    assert page.locator('.draw-item').count() == 1
