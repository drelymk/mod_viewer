"""Native destination authorization and atomic GLB snapshot saves."""

import base64
import json
import struct
from types import SimpleNamespace

import pytest

from app.bridge.api import ModViewerAPI
from app.bridge import model_export
from app.bridge import ini as ini_api
from app.session import edit as edit_session
from app.settings import paths


def glb_data(document=None):
    encoded = json.dumps(document or {"asset": {"version": "2.0"}, "meshes": [{}]}).encode()
    encoded += b" " * (-len(encoded) % 4)
    data = struct.pack("<4sIIII", b"glTF", 2, 20 + len(encoded), len(encoded), 0x4E4F534A) + encoded
    return data, base64.b64encode(data).decode()


def test_native_export_cancel_failure_and_retry_preserve_staged_mod(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'config_path', lambda: str(tmp_path / 'config.json'))
    mod = tmp_path / 'mod-01'
    mod.mkdir()
    ini = mod / 'source-01.ini'
    ini.write_text('[SectionFixture]\nvalue = 0\n', encoding='utf-8')
    api = ModViewerAPI(startup_mod=str(mod))
    data, encoded = glb_data()
    folder = str(mod)
    edit_session.load_documents(folder, [str(ini)])
    assert ini_api.update_text(folder, 'source-01.ini', '[SectionFixture]\nvalue = 1\n')['pending']
    destination = tmp_path / 'model.glb'
    calls = []
    selection = [None]
    api._window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: calls.append((args, kwargs)) or selection[0])
    try:
        assert api.export_model_glb(folder, encoded) == {'cancelled': True}
        assert not destination.exists()
        selection[0] = [str(destination)]
        destination.write_bytes(b'original')
        replace = model_export.os.replace
        monkeypatch.setattr(model_export.os, 'replace', lambda *_args: (_ for _ in ()).throw(OSError('fixture failure')))
        assert api.export_model_glb(folder, encoded) == {'error': 'fixture failure'}
        assert destination.read_bytes() == b'original'
        assert not list(tmp_path.glob('.model-export-*'))
        monkeypatch.setattr(model_export.os, 'replace', replace)
        assert api.export_model_glb(folder, encoded) == {'ok': True, 'name': 'model.glb'}
        assert destination.read_bytes() == data
        selection[0] = str(tmp_path / 'model')
        assert api.export_model_glb(folder, encoded)['error']
        assert destination.read_bytes() == data
        selection[0] = str(tmp_path / 'model-02')
        assert api.export_model_glb(folder, encoded) == {'ok': True, 'name': 'model-02.glb'}
        assert (tmp_path / 'model-02.glb').read_bytes() == data
        assert edit_session.has_pending(folder)
        assert ini.read_text() == '[SectionFixture]\nvalue = 0\n'
        assert all(call[1]['file_types'] == ('glTF Binary (*.glb)',) for call in calls)
    finally:
        edit_session.discard(folder)


def test_export_rejects_browser_paths_archives_and_invalid_glb_before_dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'config_path', lambda: str(tmp_path / 'config.json'))
    api = ModViewerAPI()
    api._window = SimpleNamespace(create_file_dialog=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected dialog')))
    mod = tmp_path / 'mod-01'
    mod.mkdir()
    _, encoded = glb_data()
    assert api.export_model_glb(str(mod), encoded)['error']
    assert api.export_model_glb(str(mod), {'action': 'begin', 'size': 100})['error']
    api._access.remember_mod_picker_selection(str(mod))
    for payload in [None, {'action': 'begin', 'size': True}, {'action': 'begin', 'size': -1},
                    {'action': 'append', 'token': 'unknown-session', 'chunk': 'AAAA'}, 'invalid', base64.b64encode(b'bad header').decode(),
                    glb_data({'meshes': [{}], 'images': [{'uri': 'image.png'}]})[1]]:
        assert api.export_model_glb(str(mod), payload)['error']
    archive = tmp_path / 'mod-01.zip'
    import zipfile
    with zipfile.ZipFile(archive, 'w'):
        pass
    api._access.remember_mod_picker_selection(str(archive))
    assert api.export_model_glb(str(archive), encoded) == {'error': 'Export is unavailable for compressed mods.'}
    assert api.export_model_glb(str(archive), {'action': 'begin', 'size': 100}) == {'error': 'Export is unavailable for compressed mods.'}


def test_chunked_export_cancel_failure_retry_and_staged_state(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'config_path', lambda: str(tmp_path / 'config.json'))
    mod = tmp_path / 'mod-01'
    mod.mkdir()
    ini = mod / 'source-01.ini'
    ini.write_text('[SectionFixture]\nvalue = 0\n', encoding='utf-8')
    folder = str(mod)
    api = ModViewerAPI(startup_mod=folder)
    edit_session.load_documents(folder, [str(ini)])
    assert ini_api.update_text(folder, 'source-01.ini', '[SectionFixture]\nvalue = 1\n')['pending']
    destination = tmp_path / 'model.glb'
    destination.write_bytes(b'original')
    selection = [None]
    api._window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: selection[0])
    data, _ = glb_data()
    def begin():
        return api.export_model_glb(folder, {'action': 'begin', 'size': len(data)})

    def send(token, action, **fields):
        return api.export_model_glb(folder, {'action': action, 'token': token, **fields})
    try:
        assert begin() == {'cancelled': True}
        selection[0] = [str(destination)]
        token = begin()['token']
        assert begin()['error']
        assert send('unknown-session', 'finish')['error']
        other = tmp_path / 'mod-02'
        other.mkdir()
        api._access.remember_mod_picker_selection(str(other))
        assert api.export_model_glb(str(other), {'action': 'cancel', 'token': token})['error']
        assert send(token, 'append', chunk=base64.b64encode(data[:13]).decode()) == {'ok': True}
        assert send(token, 'finish')['error']
        assert destination.read_bytes() == b'original'
        assert not list(tmp_path.glob('.model-export-*'))
        token = begin()['token']
        assert send(token, 'append', chunk=base64.b64encode(data).decode()) == {'ok': True}
        assert send(token, 'cancel') == {'cancelled': True}
        token = begin()['token']
        assert send(token, 'append', chunk=base64.b64encode(data).decode()) == {'ok': True}
        replace = model_export.os.replace
        monkeypatch.setattr(model_export.os, 'replace', lambda *_args: (_ for _ in ()).throw(OSError('fixture failure')))
        assert send(token, 'finish') == {'error': 'fixture failure'}
        assert destination.read_bytes() == b'original'
        monkeypatch.setattr(model_export.os, 'replace', replace)
        token = begin()['token']
        for chunk in [data[:13], data[13:]]:
            assert send(token, 'append', chunk=base64.b64encode(chunk).decode()) == {'ok': True}
        assert destination.read_bytes() == b'original'
        assert send(token, 'finish') == {'ok': True, 'name': 'model.glb'}
        assert send(token, 'finish')['error']
        assert destination.read_bytes() == data
        assert not list(tmp_path.glob('.model-export-*'))
        assert edit_session.has_pending(folder)
        assert ini.read_text() == '[SectionFixture]\nvalue = 0\n'
    finally:
        edit_session.discard(folder)


@pytest.mark.parametrize('failure', ['bad-base64', 'too-large', 'overflow', 'bad-header'])
def test_chunked_export_rejects_invalid_data_and_cleans_pending_file(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(paths, 'config_path', lambda: str(tmp_path / 'config.json'))
    mod = tmp_path / 'mod-01'
    mod.mkdir()
    api = ModViewerAPI(startup_mod=str(mod))
    destination = tmp_path / 'model.glb'
    destination.write_bytes(b'original')
    api._window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: [str(destination)])
    data, _ = glb_data()
    token = api.export_model_glb(str(mod), {'action': 'begin', 'size': len(data), 'path': str(tmp_path / 'ignored.glb')})['token']
    chunks = {'bad-base64': 'invalid', 'too-large': '*' * (model_export.MAX_CHUNK_CHARS + 1),
              'overflow': base64.b64encode(data + b'extra').decode(),
              'bad-header': base64.b64encode(b'NOPE' + data[4:]).decode()}
    appended = api.export_model_glb(str(mod), {'action': 'append', 'token': token, 'chunk': chunks[failure]})
    if failure == 'bad-header':
        assert appended == {'ok': True}
        assert api.export_model_glb(str(mod), {'action': 'finish', 'token': token})['error']
    else:
        assert appended['error']
    assert destination.read_bytes() == b'original'
    assert not (tmp_path / 'ignored.glb').exists()
    assert not list(tmp_path.glob('.model-export-*'))
