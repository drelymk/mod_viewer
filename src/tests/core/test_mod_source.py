"""ZIP-backed mod source and discovery safety contracts."""

import zipfile

import pytest

from core.mod_discovery import discover_ini_paths
from core.mod_source import ModSourceError, ZipModSource


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_zip_source_strips_one_wrapper_and_keeps_logical_paths(tmp_path):
    archive_path = _write_zip(tmp_path / "sample.zip", {
        "SampleMod/main.ini": b"[TextureOverrideBody]\n"
        b"drawindexed = 3, 0, 0\n",
        "SampleMod\\nested\\mesh.buf": b"mesh-data",
        "SampleMod/textures/body.dds": b"texture-data",
    })

    source = ZipModSource(archive_path)

    assert source.wrapper_root == "SampleMod"
    assert source.list_files() == [
        "main.ini", "nested/mesh.buf", "textures/body.dds"]
    ini = source.document_path("main.ini")
    mesh = source.resolve_resource("nested\\mesh.buf")
    assert source.is_file(ini)
    assert source.exists("nested/mesh.buf")
    assert source.read_text(ini).startswith("[TextureOverrideBody]")
    assert source.read(mesh) == b"mesh-data"
    assert source.size(mesh) == 9
    assert source.logical_path(mesh) == "nested/mesh.buf"
    casefolded = source.resolve("NESTED/MESH.BUF")
    assert source.logical_path(casefolded) == "nested/mesh.buf"
    assert source.same_reference(mesh, casefolded)


def test_zip_discovery_uses_wrapper_relative_depth_and_disabled_selection(tmp_path):
    archive_path = _write_zip(tmp_path / "sample.zip", {
        "Export/mod.ini": b"[TextureOverrideBody]\n"
        b"drawindexed = 3, 0, 0\n",
        "Export/variants/extra.ini": b"[KeyExtra]\nkey = F1\n",
        "Export/variants/deep/third.ini": b"[KeyThird]\nkey = F2\n",
        "Export/variants/deep/too-deep/fourth.ini": b"[KeyFourth]\n",
        "Export/DISABLED-old.ini": b"[TextureOverrideBody]\n"
        b"drawindexed = 3, 0, 0\n",
        "Export/variants/deep/too-deep/DISABLED-fourth.ini":
            b"[KeyFourth]\nkey = F4\n",
    })
    source = ZipModSource(archive_path)

    active = discover_ini_paths(str(archive_path), source=source)
    disabled = discover_ini_paths(
        str(archive_path), disabled=True, source=source)

    assert [source.logical_path(path) for path in active] == [
        "mod.ini", "variants/deep/third.ini",
        "variants/deep/too-deep/fourth.ini", "variants/extra.ini"]
    assert [source.logical_path(path) for path in disabled] == [
        "DISABLED-old.ini", "variants/deep/too-deep/DISABLED-fourth.ini"]


def test_zip_discovery_does_not_drop_late_geometry_ini(tmp_path):
    members = {
        f"Export/{index:02}.ini": b"[KeyUnused]\nkey = F1\n"
        for index in range(10)
    }
    members["Export/zz-geometry.ini"] = (
        b"[TextureOverrideBody]\n"
        b"drawindexed = 3, 0, 0\n")
    source = ZipModSource(_write_zip(tmp_path / "many-inis.zip", members))

    paths = discover_ini_paths(str(tmp_path / "many-inis.zip"), source=source)

    assert len(paths) == 11
    assert source.logical_path(paths[-1]) == "zz-geometry.ini"


@pytest.mark.parametrize("member", [
    "../escape.buf", "..\\escape.buf", "/absolute.buf", "C:\\absolute.buf",
])
def test_zip_source_rejects_traversal_and_absolute_members(tmp_path, member):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, b"bad")

    with pytest.raises(ModSourceError, match="unsafe member"):
        ZipModSource(archive_path)


def test_zip_source_rejects_case_ambiguous_members(tmp_path):
    archive_path = tmp_path / "ambiguous.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Body.dds", b"one")
        archive.writestr("body.dds", b"two")

    with pytest.raises(ModSourceError, match="case-ambiguous"):
        ZipModSource(archive_path)


def test_zip_source_enforces_member_and_aggregate_limits(tmp_path, monkeypatch):
    archive_path = _write_zip(tmp_path / "limited.zip", {
        "one.bin": b"12345",
        "two.bin": b"67890",
    })
    source = ZipModSource(archive_path)
    monkeypatch.setattr("core.mod_source._MAX_ZIP_MEMBER_BYTES", 5)
    monkeypatch.setattr("core.mod_source._MAX_ZIP_READ_BYTES", 6)

    assert source.read_bytes("one.bin") == b"12345"
    with pytest.raises(ModSourceError, match="2 GiB safety limit"):
        source.read_bytes("two.bin")


def test_zip_source_does_not_recount_member_reads_across_reloads(
        tmp_path, monkeypatch):
    archive_path = _write_zip(tmp_path / "reloads.zip", {
        "one.bin": b"12345",
        "two.bin": b"67890",
    })
    source = ZipModSource(archive_path)
    monkeypatch.setattr("core.mod_source._MAX_ZIP_MEMBER_BYTES", 5)
    monkeypatch.setattr("core.mod_source._MAX_ZIP_READ_BYTES", 6)

    for _ in range(3):
        assert source.read_bytes("one.bin") == b"12345"
    with pytest.raises(ModSourceError, match="2 GiB safety limit"):
        source.read_bytes("two.bin")


def test_zip_source_enforces_member_count_limit(tmp_path, monkeypatch):
    archive_path = _write_zip(tmp_path / "many.zip", {
        "one.bin": b"1", "two.bin": b"2",
    })
    monkeypatch.setattr("core.mod_source._MAX_ZIP_MEMBERS", 1)

    with pytest.raises(ModSourceError, match="too many members"):
        ZipModSource(archive_path)


def test_zip_source_rejects_non_zip_extension(tmp_path):
    path = tmp_path / "sample.rar"
    path.write_bytes(b"not a supported archive")

    with pytest.raises(ModSourceError, match=r"Only \.zip"):
        ZipModSource(path)


def test_bad_zip_is_reported_as_a_source_error(tmp_path):
    path = tmp_path / "broken.zip"
    path.write_bytes(b"not a zip")

    with pytest.raises(ModSourceError, match="Could not read ZIP"):
        ZipModSource(path)
