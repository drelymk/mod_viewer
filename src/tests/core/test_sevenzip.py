"""Contracts for the optional 7-Zip subprocess archive backend."""

from types import SimpleNamespace
import os
import zipfile

import pytest

from core import mod_source
from core import sevenzip
from core.mod_discovery import discover_ini_paths
from core.mod_source import ModSourceError, SevenZipModSource


LISTING = (
    "Path = sample.7z\n"
    "Type = 7z\n"
    "Physical Size = 123\n"
    "----------\n"
    "Path = Wrapper\\deep\\Möd.ini\n"
    "Size = 7\n"
    "Folder = -\n"
    "Attributes = A\n"
    "Encrypted = -\n"
    "----------\n"
    "Path = Wrapper\\deep\\body.buf\n"
    "Size = 4\n"
    "Folder = -\n"
    "Attributes = A\n"
    "Encrypted = -\n"
    "----------\n"
    "Path = Wrapper\\deep\\nested\\\n"
    "Size = 0\n"
    "Folder = +\n"
    "Attributes = D\n"
    "Encrypted = -\n"
    "----------\n"
    "Path = Wrapper\\secret.bin\n"
    "Size = 10\n"
    "Folder = -\n"
    "Attributes = A\n"
    "Encrypted = +\n"
)


def _entries(*items):
    return [sevenzip.SevenZipEntry(*item) for item in items]


class FakeClient:
    def __init__(self, entries, data=None):
        self.entries = list(entries)
        self.data = data if data is not None else {
            entry.raw_name: b"\0" * entry.size
            for entry in self.entries if not entry.is_dir
        }
        self.calls = []

    def list_members(self, archive_path):
        self.calls.append(("list", archive_path))
        return self.entries

    def extract_all(self, archive_path, output_dir):
        self.calls.append(("extract", archive_path, output_dir))
        for raw_name, member_data in self.data.items():
            member_path = os.path.join(
                output_dir, *raw_name.replace("\\", "/").split("/"))
            os.makedirs(os.path.dirname(member_path), exist_ok=True)
            with open(member_path, "wb") as stream:
                stream.write(member_data)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("mod.zip", "zip"),
        ("mod.7Z", "7z"),
        ("mod.RAR", "rar"),
        ("mod.tar", None),
    ],
)
def test_archive_path_helpers(path, expected):
    assert mod_source.archive_kind_for_path(path) == expected
    assert mod_source.is_archive_path(path) is (expected is not None)


def test_find_7zip_prefers_path_candidates_and_excludes_7zr():
    calls = []

    def which(candidate):
        calls.append(candidate)
        return "C:/tools/7zz.exe" if candidate == "7zz.exe" else None

    assert sevenzip.find_7zip(which=which, environ={}, is_file=lambda _: False) \
        == "C:/tools/7zz.exe"
    assert calls == ["7z.exe", "7z", "7zz.exe"]


def test_find_7zip_checks_standard_program_files_locations():
    program_files = "C:/Program Files"
    expected = os.path.join(program_files, "7-Zip", "7z.exe")

    assert sevenzip.find_7zip(
        which=lambda _candidate: None,
        environ={"ProgramFiles": program_files},
        is_file=lambda path: path == expected) == expected


def test_find_7zip_returns_none_when_no_supported_executable_exists():
    calls = []

    def which(candidate):
        calls.append(candidate)
        return None

    assert sevenzip.find_7zip(
        which=which, environ={"ProgramFiles": "C:/missing"},
        is_file=lambda _path: False) is None
    assert "7zr.exe" not in calls


def test_sevenzip_cli_reports_missing_installation(monkeypatch):
    monkeypatch.setattr(sevenzip, "find_7zip", lambda: None)

    with pytest.raises(sevenzip.SevenZipError, match="7-Zip is not installed"):
        sevenzip.SevenZipCLI()


def test_parse_listing_ignores_archive_record_and_directories():
    entries = sevenzip.parse_listing(LISTING)

    assert entries == [
        sevenzip.SevenZipEntry("Wrapper\\deep\\Möd.ini", 7),
        sevenzip.SevenZipEntry("Wrapper\\deep\\body.buf", 4),
        sevenzip.SevenZipEntry("Wrapper\\deep\\nested\\", 0, True),
        sevenzip.SevenZipEntry("Wrapper\\secret.bin", 10, False, True),
    ]


def test_parse_listing_accepts_blank_line_record_separators():
    listing = (
        "Path = Wrapper\\mod.ini\n"
        "Folder = -\n"
        "Size = 12\n"
        "Attributes = A\n"
        "\n"
        "Path = Wrapper\\nested\n"
        "Folder = +\n"
        "Size = 0\n"
        "Attributes = D\n")

    assert sevenzip.parse_listing(listing) == [
        sevenzip.SevenZipEntry("Wrapper\\mod.ini", 12),
        sevenzip.SevenZipEntry("Wrapper\\nested", 0, True),
    ]


def test_sevenzip_cli_extracts_to_a_private_directory_without_shell(monkeypatch):
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=b"payload", stderr=b"")

    monkeypatch.setattr(sevenzip.subprocess, "run", run)
    cli = sevenzip.SevenZipCLI("7z.exe")

    cli.extract_all("mod.7z", "C:/temp/private output")
    assert seen["command"] == [
        "7z.exe", "x", "-y", "-bd", "-bb0", "-sccUTF-8", "-sns-",
        "-oC:/temp/private output", "--", "mod.7z"]
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["stdin"] is sevenzip.subprocess.DEVNULL
    assert seen["kwargs"]["stdout"] is sevenzip.subprocess.PIPE


@pytest.mark.parametrize(
    "stderr, message",
    [
        (b"ERROR: Can not open encrypted archive. Wrong password?",
         "Password-protected archives are not supported."),
        (b"ERROR: Data Error", "ERROR: Data Error"),
    ],
)
def test_sevenzip_cli_translates_nonzero_exit(monkeypatch, stderr, message):
    monkeypatch.setattr(
        sevenzip.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2, stdout=b"partial", stderr=stderr))
    cli = sevenzip.SevenZipCLI("7z.exe")

    with pytest.raises(sevenzip.SevenZipError, match=message):
        cli.extract_all("mod.7z", "C:/temp")


def test_sevenzip_source_reuses_virtual_archive_contract_for_7z_and_rar(
        tmp_path):
    entries = _entries(
        ("Wrapper/deep/mod.ini", 7, False, False),
        ("Wrapper/deep/body.buf", 4, False, False),
        ("Wrapper/-odd@name.bin", 4, False, False),
    )
    data = {
        "Wrapper/deep/mod.ini": b"mod.ini",
        "Wrapper/deep/body.buf": b"body",
        "Wrapper/-odd@name.bin": b"name",
    }
    client = FakeClient(entries, data)

    for extension, kind in ((".7z", "7z"), (".rar", "rar")):
        path = tmp_path / f"sample{extension}"
        path.write_bytes(b"placeholder")
        source = SevenZipModSource(path, client=client)

        assert source.kind == kind
        assert source.virtual is True
        assert source.read_only is True
        assert source.wrapper_root == "Wrapper"
        assert source.list_files() == [
            "-odd@name.bin", "deep/body.buf", "deep/mod.ini"]
        member = source.resolve_resource("deep/body.buf")
        assert source.read_text(source.resolve_resource("deep/mod.ini")) == (
            "mod.ini")
        assert source.read(member) == b"body"
        assert source.same_reference(member, "deep/body.buf")
        assert source.read(source.resolve_resource("-odd@name.bin")) == b"name"

    assert [call[0] for call in client.calls] == [
        "list", "extract", "list", "extract"]


@pytest.mark.parametrize("extension", [".7z", ".rar"])
def test_virtual_archive_discovery_keeps_all_active_and_disabled_inis(
        tmp_path, extension):
    path = tmp_path / f"many{extension}"
    path.write_bytes(b"placeholder")
    names = [f"Wrapper/{index:02}.ini" for index in range(11)]
    names += ["Wrapper/DISABLED-one.ini", "Wrapper/DISABLED-two.ini"]
    entries = _entries(*[(name, 1, False, False) for name in names])
    source = SevenZipModSource(path, client=FakeClient(entries))

    active = discover_ini_paths(str(path), source=source)
    disabled = discover_ini_paths(str(path), source=source, disabled=True)

    assert len(active) == 11
    assert [source.logical_path(item) for item in active] == [
        f"{index:02}.ini" for index in range(11)]
    assert [source.logical_path(item) for item in disabled] == [
        "DISABLED-one.ini", "DISABLED-two.ini"]


@pytest.mark.parametrize("raw_name", [
    "../escape.buf", "..\\escape.buf", "/absolute.buf", "C:\\absolute.buf",
    "bad\x00name.buf",
])
def test_sevenzip_source_rejects_unsafe_member_names(tmp_path, raw_name):
    path = tmp_path / "unsafe.7z"
    path.write_bytes(b"placeholder")

    with pytest.raises(ModSourceError, match="unsafe member"):
        SevenZipModSource(
            path,
            client=FakeClient(_entries((raw_name, 1, False, False)),
                              {raw_name: b"x"}))


@pytest.mark.parametrize(
    "entries, message",
    [
        ([
            ("body.buf", 1, False, False),
            ("body.buf", 1, False, False),
        ], "duplicate member"),
        ([
            ("Body.buf", 1, False, False),
            ("body.buf", 1, False, False),
        ], "case-ambiguous"),
    ],
)
def test_sevenzip_source_rejects_duplicate_or_ambiguous_members(
        tmp_path, entries, message):
    path = tmp_path / "ambiguous.rar"
    path.write_bytes(b"placeholder")
    values = {name: b"x" for name, _size, _directory, _encrypted in entries}

    with pytest.raises(ModSourceError, match=message):
        SevenZipModSource(path, client=FakeClient(_entries(*entries), values))


def test_sevenzip_source_reads_and_prefixes_from_memory_after_extraction(
        tmp_path):
    path = tmp_path / "sample.7z"
    path.write_bytes(b"placeholder")
    entries = _entries(("Wrapper/body.dds", 8, False, False))
    client = FakeClient(entries, {"Wrapper/body.dds": b"12345678"})
    source = SevenZipModSource(path, client=client)
    member = source.resolve_resource("body.dds")

    assert source.read_prefix(member, 3) == b"123"
    assert [call[0] for call in client.calls] == ["list", "extract"]
    assert source.read_bytes(member) == b"12345678"
    assert source.read_bytes(member) == b"12345678"
    assert [call[0] for call in client.calls] == [
        "list", "extract"]


def test_sevenzip_source_rejects_extraction_symlink_escape(tmp_path):
    path = tmp_path / "escape.7z"
    path.write_bytes(b"placeholder")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"data")

    class EscapingClient:
        def list_members(self, _archive_path):
            return _entries(("body.bin", 4, False, False))

        def extract_all(self, _archive_path, output_dir):
            try:
                os.symlink(outside, os.path.join(output_dir, "body.bin"))
            except OSError:
                pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(ModSourceError, match="escapes the extraction root"):
        SevenZipModSource(path, client=EscapingClient())


def test_sevenzip_source_repeated_reads_do_not_call_backend(
        tmp_path, monkeypatch):
    path = tmp_path / "repeat.7z"
    path.write_bytes(b"placeholder")
    client = FakeClient(_entries(("body.buf", 5, False, False)),
                        {"body.buf": b"12345"})
    source = SevenZipModSource(path, client=client)
    monkeypatch.setattr(mod_source, "_MAX_ARCHIVE_READ_BYTES", 5)

    member = source.resolve_resource("body.buf")
    assert source.read_bytes(member) == b"12345"
    assert source.read_bytes(member) == b"12345"
    assert [call[0] for call in client.calls] == ["list", "extract"]


def test_sevenzip_source_rejects_advertised_oversize_member(tmp_path, monkeypatch):
    path = tmp_path / "large.7z"
    path.write_bytes(b"placeholder")
    monkeypatch.setattr(mod_source, "_MAX_ARCHIVE_MEMBER_BYTES", 4)
    with pytest.raises(ModSourceError, match="too large"):
        SevenZipModSource(
            path,
            client=FakeClient(_entries(("body.buf", 5, False, False)),
                              {"body.buf": b"12345"}))


def test_sevenzip_source_rejects_encrypted_and_wrong_sized_members(tmp_path):
    encrypted_path = tmp_path / "encrypted.rar"
    encrypted_path.write_bytes(b"placeholder")
    with pytest.raises(ModSourceError, match="Encrypted rar members"):
        SevenZipModSource(
            encrypted_path,
            client=FakeClient(_entries(("secret.bin", 4, False, True)),
                              {"secret.bin": b"data"}))

    wrong_path = tmp_path / "wrong.7z"
    wrong_path.write_bytes(b"placeholder")
    with pytest.raises(ModSourceError, match="invalid size"):
        SevenZipModSource(
            wrong_path,
            client=FakeClient(_entries(("body.buf", 5, False, False)),
                              {"body.buf": b"data"}))


def test_sevenzip_source_applies_archive_safety_limits(tmp_path, monkeypatch):
    path = tmp_path / "limited.7z"
    path.write_bytes(b"placeholder")
    client = FakeClient(_entries(
        ("one.bin", 5, False, False), ("two.bin", 5, False, False)),
        {"one.bin": b"12345", "two.bin": b"67890"})
    monkeypatch.setattr(mod_source, "_MAX_ARCHIVE_READ_BYTES", 6)

    with pytest.raises(ModSourceError, match="2 GiB safety limit"):
        SevenZipModSource(path, client=client)

    monkeypatch.setattr(mod_source, "_MAX_ARCHIVE_MEMBERS", 1)
    with pytest.raises(ModSourceError, match="too many members"):
        SevenZipModSource(
            path,
            client=FakeClient(_entries(
                ("one.bin", 1, False, False), ("two.bin", 1, False, False))))


def test_sevenzip_source_factory_dispatches_without_startup_dependency(
        tmp_path, monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod_source, "SevenZipCLI", lambda: client)
    folder = tmp_path / "folder"
    folder.mkdir()
    zip_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("mod.ini", b"[Constants]\n")
    seven_path = tmp_path / "sample.7z"
    seven_path.write_bytes(b"placeholder")
    rar_path = tmp_path / "sample.rar"
    rar_path.write_bytes(b"placeholder")

    assert isinstance(mod_source.mod_source_for_path(folder),
                      mod_source.DirectoryModSource)
    assert isinstance(mod_source.mod_source_for_path(zip_path),
                      mod_source.ZipModSource)
    assert mod_source.mod_source_for_path(seven_path).kind == "7z"
    assert mod_source.mod_source_for_path(rar_path).kind == "rar"
