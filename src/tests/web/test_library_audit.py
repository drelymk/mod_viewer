"""The opt-in corpus browser probe uses real controls and WebGPU rendering."""

from tools.audit_mod_browser import probe_page

from .payloads import controlled_payload
from .support import bridge_calls, open_model, wait_loaded


def test_corpus_probe_cycles_reloads_and_captures_views_without_writes(viewer, tmp_path):
    payload = controlled_payload()
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    result = probe_page(page, payload, tmp_path, 0)
    assert result['status'] == 'checked'
    assert result['states_checked'] == result['states_available'] == 2
    assert [step['visible'] for step in result['observations']] == [0, 1]
    assert result['reload'] is True
    assert {path.name for path in tmp_path.glob('*.png')} == {'initial.png', 'front.png', 'side.png', 'back.png'}
    assert len(bridge_calls(page, 'load')) == 2
    assert not any(call['name'] in {'export', 'iniUpdate', 'color', 'textures'}
                   for call in page.evaluate('window.__bridge.calls'))
