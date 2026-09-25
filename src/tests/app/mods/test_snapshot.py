"""Ownership and equivalence checks for authoritative INI snapshots."""

import zipfile

from app.mods.analysis import analyze_mod_inis, build_mod_ini_snapshot
from app.mods.loader import ModLoadContext
from core.ini.document import IniDocument
from core.mod_source import ZipModSource


def test_staged_snapshot_uses_exact_document_without_disk_read(tmp_path,
                                                              monkeypatch):
    path = tmp_path / "nested" / "mod.ini"
    path.parent.mkdir()
    path.write_text("[Constants]\nglobal $value = 0\n", encoding="utf-8")
    staged = IniDocument.from_string(
        "namespace = Outfit\n[Constants]\nglobal $value = 1\n",
        path=str(path))
    monkeypatch.setattr(IniDocument, "load", lambda _path: (_ for _ in ()).throw(
        AssertionError("snapshot reread disk")))

    snapshot = build_mod_ini_snapshot(
        [str(path)], str(tmp_path), {str(path): staged},
        require_documents=True)
    record = snapshot.records[0]
    assert record.document is staged
    assert record.relative_path == "nested/mod.ini"
    assert record.namespace == "Outfit"
    assert record.canonical_vars["value"] == "value"
    assert str(record.sections["Constants"][0]) == "global $value = 1"
    assert record.sections["Constants"][0].source() == {
        "ini_path": str(path), "line_no": 3, "section": "Constants"}
    context = ModLoadContext(str(tmp_path), ini=snapshot)
    assert context.docs[str(path)] is staged
    assert context.ini_paths == [str(path)]


def test_snapshot_keeps_sibling_sections_separate_and_matches_legacy(tmp_path):
    paths = []
    documents = {}
    for name in ("body", "hair"):
        path = tmp_path / "nested" / f"{name}.ini"
        path.parent.mkdir(exist_ok=True)
        text = ("[Constants]\nglobal persist $style = 0\n"
                "[KeyStyle]\ntype = cycle\n$style = 0,1\n"
                f"[ResourceTexture]\nfilename = {name}.dds\n")
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
        documents[str(path)] = IniDocument.from_string(text, path=str(path))
    snapshot = build_mod_ini_snapshot(paths, str(tmp_path), documents)
    assert [record.relative_path for record in snapshot.records] == [
        "nested/body.ini", "nested/hair.ini"]
    assert [record.sections["ResourceTexture"][0] for record in
            snapshot.records] == ["filename = body.dds", "filename = hair.dds"]
    assert [record.var_prefix for record in snapshot.records] == [
        "nested/body::", "nested/hair::"]
    parsed = analyze_mod_inis(snapshot)
    legacy = analyze_mod_inis(paths, str(tmp_path), documents=documents)
    assert parsed.toggles == legacy.toggles
    assert parsed.menu == legacy.menu
    assert parsed.defaults == legacy.defaults
    assert parsed.groups == legacy.groups


def test_archive_and_directory_snapshots_have_same_logical_records(tmp_path):
    text = "namespace = Sample\n[Constants]\nglobal $value = 1\n"
    folder = tmp_path / "directory"
    (folder / "nested").mkdir(parents=True)
    disk_path = folder / "nested" / "mod.ini"
    disk_path.write_text(text, encoding="utf-8")
    archive_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("SampleMod/nested/mod.ini", text)
    source = ZipModSource(archive_path)
    virtual_path = source.document_path("nested/mod.ini")
    disk = build_mod_ini_snapshot(
        [str(disk_path)], str(folder),
        {str(disk_path): IniDocument.load(str(disk_path))})
    virtual = build_mod_ini_snapshot(
        [virtual_path], str(archive_path),
        {virtual_path: IniDocument.from_string(text, path=virtual_path)},
        source=source, require_documents=True)
    left, right = disk.records[0], virtual.records[0]
    assert left.relative_path == right.relative_path == "nested/mod.ini"
    assert left.namespace == right.namespace == "Sample"
    assert left.var_prefix == right.var_prefix
    assert left.sections == right.sections


def test_new_staged_document_gives_new_snapshot_view(tmp_path):
    path = str(tmp_path / "mod.ini")
    before = IniDocument.from_string("[Constants]\nglobal $value = 0\n",
                                     path=path)
    after = IniDocument.from_string("[Constants]\nglobal $value = 1\n",
                                    path=path)
    first = build_mod_ini_snapshot(
        [path], str(tmp_path), {path: before}, revision=1)
    second = build_mod_ini_snapshot(
        [path], str(tmp_path), {path: after}, revision=2)
    assert first.revision == 1
    assert second.revision == 2
    assert first.records[0].document is before
    assert second.records[0].document is after
    assert first.records[0].sections != second.records[0].sections
