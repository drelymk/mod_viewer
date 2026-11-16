"""Portable build prerequisite checks and explicit SDK selection."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("installed,explicit,expected", [
    ("10.0.100", None, "installed"),
    (None, None, None),
    ("10.0.101", None, "installed"),
    ("11.0.100", None, "installed"),
    ("9.0.100", None, None),
    ("11.0.100-preview.1", None, None),
    (None, "10.0.100", "explicit"),
    ("10.0.100", "10.0.101", "explicit"),
    ("11.0.100", "9.0.100", None),
])
def test_native_sdk_selection_accepts_stable_roll_forward_and_explicit_override(
        tmp_path, monkeypatch, installed, explicit, expected):
    script = Path(__file__).resolve().parents[3] / "tools/build_game_extract.py"
    spec = importlib.util.spec_from_file_location("native_sdk_build", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "global.json").write_text(json.dumps(dict(sdk=dict(version="10.0.100", rollForward="major", allowPrerelease=False))))
    name = "dotnet.exe" if builder.os.name == "nt" else "dotnet"
    executables = dict(installed=tmp_path / "installed" / name,
                       portable=tmp_path / ".quality-reports/toolchains/dotnet-10.0.100" / name,
                       explicit=tmp_path / "chosen-sdk" / name)
    # An existing local SDK cache must never become an implicit prerequisite.
    executables["portable"].parent.mkdir(parents=True)
    executables["portable"].write_bytes(b"unused cached SDK")
    versions = {str(executables[key]): version for key, version in
                (("installed", installed), ("portable", "10.0.100"), ("explicit", explicit))}
    probes = []
    def which(candidate):
        return str(executables["installed"]) if candidate == "dotnet" else candidate if candidate in versions else None
    def probe(command, **options):
        assert command[1:] == ["--version"] and options["cwd"] == project
        probes.append(command[0])
        version = versions[command[0]]
        return SimpleNamespace(returncode=0 if version else 2147516561, stdout=(version or "") + "\n")
    monkeypatch.setattr(builder.shutil, "which", which)
    monkeypatch.setattr(builder.subprocess, "run", probe)
    requested = str(executables["explicit"]) if explicit else "dotnet"
    if expected:
        assert builder.resolve_dotnet(project, requested) == str(executables[expected])
    else:
        with pytest.raises(builder.MissingDotnetSdk, match=".NET SDK 10.0.100 or a newer stable SDK"):
            builder.resolve_dotnet(project, requested)
    assert str(executables["portable"]) not in probes
    if explicit:
        assert probes == [str(executables["explicit"])]


@pytest.mark.parametrize("script,arguments", [
    ("tools/build_game_extract.py", []),
    ("tools/build_game_extract.py", ["--check-sdk"]),
    ("tools/build_zzz_extract.py", []),
    ("tools/build_zzz_extract.py", ["--check-sdk"]),
    ("src/build.py", []),
])
def test_missing_sdk_cli_warns_before_build_work(tmp_path, script, arguments):
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, str(root / script), *arguments,
                             "--dotnet", str(tmp_path / "missing-sdk" / "dotnet")],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    message = result.stdout + result.stderr
    assert result.returncode == 1
    assert "WARNING:" in message and ".NET SDK 10.0.100 or a newer stable SDK" in message
    assert "Install a compatible SDK" in message and "PATH" in message
    assert "Traceback" not in message and "pip install" not in message
    assert "Determining projects to restore" not in message
