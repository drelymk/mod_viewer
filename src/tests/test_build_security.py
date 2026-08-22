"""Security contracts for build-time downloaded and extracted inputs."""

import io
import tarfile

import pytest

import build


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
    r"C:\absolute.txt",
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
