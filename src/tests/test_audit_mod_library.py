"""Corpus runner boundaries, read-only loading and resumable worker outcomes."""

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from tools import audit_mod_library as audit
from tests.support.model_data import basic_model_ini, triangle_geometry


def make_mod(folder):
    folder.mkdir(parents=True)
    (folder / 'model.ini').write_text(basic_model_ini(), encoding='utf-8')
    for name, data in triangle_geometry().items():
        (folder / name).write_bytes(data)
    return folder


def test_inventory_groups_selected_inis_excludes_library_and_invalidates_changes(tmp_path):
    root = tmp_path / 'library'
    mod = make_mod(root / 'category' / 'mod-a')
    child = make_mod(mod / 'variant')
    sibling = make_mod(root / 'category' / 'mod-b')
    disabled = make_mod(root / 'DISABLED-mod')
    downloads = make_mod(tmp_path / 'downloads' / 'other')
    (mod / 'bundle.zip').write_bytes(b'archive candidate')
    config = {'modFolders': [{'name': 'Library', 'path': str(root)},
                             {'name': 'Downloads', 'path': str(downloads.parent)}]}
    manifest = audit.inventory(config, ['downloads'])
    selected = {entry['path'] for entry in manifest['entries']}
    assert selected == {str(mod), str(sibling), str(mod / 'bundle.zip')}
    assert str(child / 'model.ini') in manifest['entries'][0]['ini_files']
    assert str(disabled) not in selected
    assert not any(str(downloads) in entry['path'] for entry in manifest['entries'])
    assert any(row['reason'] == 'included_by_parent_selection' for row in manifest['skipped'])
    fingerprint = manifest['entries'][0]['fingerprint']
    (child / 'p.buf').write_bytes(b'changed geometry')
    assert audit.inventory(config, ['Downloads'])['entries'][0]['fingerprint'] != fingerprint
    with pytest.raises(ValueError, match='Unknown excluded library'):
        audit.inventory(config, ['unregistered'])


@pytest.mark.parametrize('archived', [False, True], ids=['directory', 'zip'])
@pytest.mark.parametrize('disabled_ini', [False, True], ids=['active', 'disabled'])
def test_backend_worker_loads_geometry_and_preserves_source_bytes(tmp_path, monkeypatch, archived, disabled_ini):
    from app.assets import folders
    from app.runtime import server

    monkeypatch.setattr(folders, 'load_registry', lambda: [])
    monkeypatch.setattr(server.paths, 'has_vendored_three', lambda: False)
    mod = make_mod(tmp_path / 'mod')
    if disabled_ini:
        (mod / 'model.ini').rename(mod / 'DISABLED-model.ini')
        (mod / 'model.ini').write_text('', encoding='utf-8')
    if archived:
        path = tmp_path / 'bundle.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for file in mod.iterdir():
                archive.write(file, 'nested/' + file.name)
    else:
        path = mod
    before = {str(file): file.read_bytes() for file in tmp_path.rglob('*') if file.is_file()}
    result = audit.backend_audit({'path': str(path), 'disabled_ini': disabled_ini})
    assert result['status'] == 'loaded', result
    assert result['metrics']['mesh_count'] == 1
    assert result['metrics']['triangles'] == 1
    assert result['metrics']['unrepresented_draws'] == []
    assert result['selected_inis']
    assert all(Path(name).name.upper().startswith('DISABLED') == disabled_ini
               for name in result['selected_inis'])
    assert 'peak_rss_mb' in result['resources']
    after = {str(file): file.read_bytes() for file in tmp_path.rglob('*') if file.is_file()}
    assert before == after
    if disabled_ini:
        active_only = make_mod(tmp_path / 'active-only')
        absent = audit.backend_audit({'path': str(active_only), 'disabled_ini': True})
        assert absent['error'] == 'No disabled .ini files found in this folder.'
        assert absent['selected_inis'] == []
        assert absent['metrics']['semantic_draw_count'] == 0


def test_disabled_inventory_selects_only_disabled_documents_and_preserves_discovery(tmp_path):
    root = tmp_path / 'library'
    mod = make_mod(root / 'model')
    (mod / 'model.ini').rename(mod / 'DISABLED-model.ini')
    (mod / 'model.ini').write_text('', encoding='utf-8')
    child = make_mod(mod / 'variant')
    (child / 'model.ini').rename(child / 'DISABLED-variant.ini')
    disabled_folder = make_mod(root / 'DISABLED-folder')
    (disabled_folder / 'model.ini').rename(disabled_folder / 'DISABLED-model.ini')
    make_mod(root / 'active-only')
    config = {'modFolders': [{'name': 'Library', 'path': str(root)}]}
    manifest = audit.inventory(config, [], disabled_ini=True)
    assert manifest['disabled_ini'] is True
    assert {entry['path'] for entry in manifest['entries']} == {str(mod), str(disabled_folder)}
    assert all(entry['disabled_ini'] for entry in manifest['entries'])
    assert all(Path(path).name.upper().startswith('DISABLED')
               for entry in manifest['entries'] for path in entry['ini_files'])
    selected = next(entry for entry in manifest['entries'] if entry['path'] == str(mod))
    assert str(child / 'DISABLED-variant.ini') in selected['ini_files']


def test_worker_resume_cache_and_report_are_scoped_to_current_manifest(tmp_path, monkeypatch):
    entry = {'id': 'selection-01', 'fingerprint': 'revision-a', 'library': 'Library',
             'relative': '<model>', 'path': str(tmp_path / 'mod')}
    calls = []

    def run(command, **kwargs):
        job = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
        calls.append(job)
        audit.write_json(job['result_path'], {'entry_id': entry['id'],
                                             'cache_key': job['cache_key'], 'status': 'loaded'})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(audit.subprocess, 'run', run)
    first = audit.execute(entry, tmp_path, 'pipeline-a', 10, True)
    assert audit.execute(entry, tmp_path, 'pipeline-a', 10, True) == first
    assert len(calls) == 1
    audit.execute({**entry, 'fingerprint': 'revision-b'}, tmp_path, 'pipeline-a', 10, True)
    audit.execute(entry, tmp_path, 'pipeline-b', 10, True)
    assert len(calls) == 3
    audit.execute({**entry, 'disabled_ini': True}, tmp_path, 'pipeline-b', 10, True)
    assert len(calls) == 4
    manifest = {'entries': [entry], 'excluded': ['downloads'], 'inventory_errors': []}
    summary = audit.report(manifest, {entry['id']: first, 'old-entry': first}, tmp_path)
    assert summary['completed'] == 1
    assert summary['excluded'] == ['downloads']
    assert '&lt;model&gt;' in (tmp_path / 'report.html').read_text(encoding='utf-8')


def test_resume_signatures_follow_external_assets_indexes_and_browser_ui(tmp_path, monkeypatch):
    from tools import audit_mod_browser as browser

    root = tmp_path / 'project'
    files = ['src/loader.py', 'src/web/js/view.js', 'src/web/index.html',
             'src/web/css/view.css', 'src/asset-index/index.json',
             'tools/audit_mod_library.py', 'tools/audit_mod_browser.py',
             'tools/benchmark_texture_pipeline.py']
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('original', encoding='utf-8')
    asset = tmp_path / 'assets' / 'Asset01'
    asset.mkdir(parents=True)
    metadata = asset / 'hash.json'
    texture = asset / 'original.dds'
    metadata.write_text('original', encoding='utf-8')
    texture.write_bytes(b'original')
    disabled = tmp_path / 'disabled-assets'
    disabled.mkdir()
    missing = tmp_path / 'missing-assets'
    config = {'assetFolders': [{'path': str(asset.parent)},
                              {'path': str(disabled), 'enabled': False},
                              {'path': str(missing)}]}
    monkeypatch.setattr(audit, 'ROOT', root)
    monkeypatch.setattr(browser, 'ROOT', root)
    monkeypatch.setattr(audit, '__file__', str(root / 'tools/audit_mod_library.py'))
    monkeypatch.setattr(browser, '__file__', str(root / 'tools/audit_mod_browser.py'))

    def signatures():
        return audit.pipeline_signature(config), browser.probe_signature(config)

    previous = signatures()
    assert signatures() == previous
    (disabled / 'ignored.dds').write_bytes(b'changed')
    assert signatures() == previous
    for path in [metadata, texture, root / 'src/asset-index/index.json',
                 root / 'src/web/index.html', root / 'src/web/css/view.css']:
        stamp = path.stat()
        path.write_bytes(b'modified')
        # Metadata content is relevant even when an indexer preserves its stamp.
        mtime = stamp.st_mtime_ns + (1_000_000 if path == texture else 0)
        os.utime(path, ns=(stamp.st_atime_ns, mtime))
        current = signatures()
        assert all(new != old for new, old in zip(current, previous)), path.name
        previous = current
    added = asset / 'extra.dds'
    added.write_bytes(b'added')
    assert signatures() != previous
    added.unlink()
    assert signatures() == previous
    texture.rename(asset / 'renamed.dds')
    assert signatures() != previous
    previous = signatures()
    missing.mkdir()
    assert signatures() != previous
    missing.rmdir()
    assert signatures() == previous


@pytest.mark.parametrize('outcome,status', [('timeout', 'timeout'), ('crash', 'worker_crash')])
def test_worker_failure_is_recorded_and_does_not_reuse_stale_result(tmp_path, monkeypatch, outcome, status):
    entry = {'id': 'selection-01', 'fingerprint': 'new'}
    audit.write_json(tmp_path / 'results/selection-01.json', {'status': 'loaded', 'cache_key': 'stale'})

    def run(command, **kwargs):
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 9)

    monkeypatch.setattr(audit.subprocess, 'run', run)
    result = audit.execute(entry, tmp_path, 'pipeline', 1, True)
    assert result['status'] == status
    assert json.loads((tmp_path / 'results/selection-01.json').read_text()) == result


def test_browser_selection_keeps_library_coverage_and_excludes_backend_failures():
    from tools.audit_mod_browser import select_entries

    entries = [
        {'id': 'a', 'library': 'Library A', 'relative': 'plain'},
        {'id': 'b', 'library': 'Library A', 'relative': 'controlled'},
        {'id': 'c', 'library': 'Library B', 'relative': 'plain'},
        {'id': 'd', 'library': 'Library B', 'relative': 'broken'},
    ]
    manifest = {'entries': entries, 'libraries': [{'name': 'Library A'}, {'name': 'Library B'}]}
    results = {
        'a': {'status': 'loaded', 'metrics': {'geometry_bytes': 12}},
        'b': {'status': 'review', 'metrics': {'toggle_count': 2, 'geometry_bytes': 24}},
        'c': {'status': 'loaded', 'metrics': {'geometry_bytes': 12}},
        'd': {'status': 'load_error'},
    }
    assert [entry['id'] for entry in select_entries(manifest, results)] == ['b', 'c']
    assert [entry['id'] for entry in select_entries(manifest, results, True)] == ['a', 'b', 'c']


@pytest.mark.parametrize('menu,expected', [
    ({'values': ['0', '1']}, 2),
    ({'cycles': [{'values': ['0', '1']}, {'values': ['0', '1', '2']}]}, 6),
    ({'cycles': [{'values': ['0', '1']}, {'values': ['0', '1']}]}, 2),
    ({'kind': 'shape_slider'}, 3),
    ({'values': []}, 0),
])
def test_menu_probe_counts_complete_independently_wrapping_tuples(menu, expected):
    from tools.audit_mod_browser import menu_position_count

    assert menu_position_count(menu) == expected


@pytest.mark.parametrize('status,raw_draws,omissions,expected', [
    ('loaded', None, None, 'loaded'),
    ('load_error', 0, None, 'no_authored_draws'),
    ('load_error', 3, None, 'unrecognized_draws'),
    ('load_error', None, None, 'unrecognized_draws'),
    ('review', 3, [{'count': 0}], 'zero_count_draws'),
    ('review', 3, [{'count': 0}, {'count': 3}], 'partial_geometry_review'),
    ('timeout', None, None, 'timeout'),
    ('load_error', 0, None, 'empty_ini'),
    ('load_error', None, None, 'no_active_ini'),
    ('load_error', None, None, 'no_disabled_ini'),
])
def test_triage_preserves_uncertainty_and_separates_expected_empty_draws(status, raw_draws, omissions, expected):
    from tools.triage_mod_library import classify

    error = ('No disabled .ini files found in this folder.' if expected == 'no_disabled_ini'
             else 'No active .ini files found in this folder.' if expected == 'no_active_ini'
             else 'No mesh geometry found across 1 ini file(s).')
    result = {'status': status, 'error': error}
    assert classify(result, raw_draws, omissions, empty=expected == 'empty_ini') == expected
