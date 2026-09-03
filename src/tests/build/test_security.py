"""Security contracts for build-time downloaded and extracted inputs."""

import io
import os
import tarfile

import pytest

import build


class _Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.data


def _archive(tmp_path, members):
    filename = tmp_path / "fixture.tar"
    with tarfile.open(filename, "w") as archive:
        for member in members:
            archive.addfile(member[0], io.BytesIO(member[1]))
    return filename


def _file(name, data=b"content"):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def _directory(name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    return info, b""


def test_safe_extract_tar_allows_regular_files_and_directories(tmp_path):
    archive = _archive(tmp_path, [_directory("pkg"), _file("pkg/file.txt")])
    destination = tmp_path / "extract"
    destination.mkdir()

    with tarfile.open(archive) as stream:
        build.safe_extract_tar(stream, destination)

    assert (destination / "pkg" / "file.txt").read_bytes() == b"content"


@pytest.mark.parametrize("name", [
    "../outside.txt",
    "/absolute.txt",
    r"X:\fixture\absolute.txt",
])
def test_safe_extract_tar_rejects_path_escape(tmp_path, name):
    archive = _archive(tmp_path, [_file(name)])
    destination = tmp_path / "extract"
    destination.mkdir()

    with tarfile.open(archive) as stream:
        with pytest.raises(RuntimeError, match="unsafe archive member"):
            build.safe_extract_tar(stream, destination)

    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("member_type", [
    tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE,
])
def test_safe_extract_tar_rejects_links_and_special_files(tmp_path, member_type):
    info = tarfile.TarInfo("bad-entry")
    info.type = member_type
    info.linkname = "outside" if member_type != tarfile.FIFOTYPE else ""
    archive = _archive(tmp_path, [(info, b"")])
    destination = tmp_path / "extract"
    destination.mkdir()

    with tarfile.open(archive) as stream:
        with pytest.raises(RuntimeError, match="unsupported archive member"):
            build.safe_extract_tar(stream, destination)


def test_hash_helpers_are_sha256(tmp_path):
    data = b"security fixture"
    assert build.sha256_bytes(data) == (
        "e029e559fb16a5d205decc4fc424ecc89923e1e78c7241834299d22376f1abca")
    filename = tmp_path / "x"
    filename.write_bytes(data)
    assert build.sha256_file(filename) == build.sha256_bytes(data)


def test_pyinstaller_command_bundles_third_party_notices(monkeypatch):
    commands = []
    monkeypatch.setattr(build, "write_baked_features", lambda _features: None)
    monkeypatch.setattr(build, "clean_baked_features", lambda: None)
    monkeypatch.setattr(build, "write_version_file", lambda: "version.txt")
    monkeypatch.setattr(build, "run", lambda command: commands.append(command))

    build.build(python="python", onedir=True, console=True)

    command = commands[0]
    notice_data = f"{build.THIRD_PARTY_NOTICES}{os.pathsep}."
    first_data = command.index("--add-data")
    notice_index = command.index("--add-data", first_data + 1)
    assert command[notice_index:notice_index + 2] == [
        "--add-data", notice_data]


def test_fetch_assets_rejects_tampered_cached_asset(tmp_path, monkeypatch):
    data = b"verified asset" * 100
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "asset.js").write_bytes(b"tampered")
    monkeypatch.setattr(build, "ASSETS", str(assets))
    monkeypatch.setattr(build, "ASSET_FILES", {
        "asset.js": {"url": "https://example.invalid/asset.js",
                      "sha256": build.sha256_bytes(data)},
    })

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        build.fetch_assets()
    assert (assets / "asset.js").read_bytes() == b"tampered"


def test_fetch_assets_does_not_replace_asset_after_bad_refresh(tmp_path,
                                                                monkeypatch):
    good = b"verified asset" * 100
    bad = b"untrusted asset" * 100
    assets = tmp_path / "assets"
    assets.mkdir()
    destination = assets / "asset.js"
    destination.write_bytes(b"known good")
    monkeypatch.setattr(build, "ASSETS", str(assets))
    monkeypatch.setattr(build, "ASSET_FILES", {
        "asset.js": {"url": "https://example.invalid/asset.js",
                      "sha256": build.sha256_bytes(good)},
    })
    monkeypatch.setattr(build.urllib.request, "urlopen",
                        lambda *args, **kwargs: _Response(bad))

    with pytest.raises(RuntimeError, match="downloaded asset.*SHA-256 mismatch"):
        build.fetch_assets(refresh=True)
    assert destination.read_bytes() == b"known good"
