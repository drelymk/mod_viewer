"""Pinned native extraction tool, private game configuration and bounded calls."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.request import Request, urlopen
import zipfile

from app.settings import paths


VERSION = "1.2.0"
ARCHIVE_SHA256 = "4f1e546141797dd85ab6de8642712292318abad53a57e0cafbda6e30bb59999e"
ARCHIVE_URL = f"https://github.com/sinnafuls/cue4parse-mcp/releases/download/v{VERSION}/cue4parse-mcp-{VERSION}-windows-x64.zip"
_INSTALL_LOCK = threading.Lock()


class ExtractionCancelled(ValueError):
    pass


class SearchTruncated(ValueError):
    pass


def check_cancelled(cancel):
    if cancel.is_set():
        raise ExtractionCancelled("Extraction cancelled; existing Asset files were preserved.")


def cache_directory():
    base = os.environ.get("LOCALAPPDATA") or os.path.dirname(paths.config_path())
    return Path(base) / "3DMigotoModViewer" / "extraction"


def ensure_runtime(cancel, cache=None):
    cache = Path(cache) if cache else cache_directory()
    destination = cache / f"cue4parse-{VERSION}"
    executable = destination / "Cue4ParseMcp.exe"
    with _INSTALL_LOCK:
        check_cancelled(cancel)
        marker = destination / ".viewer-runtime.json"
        if executable.is_file() and marker.is_file():
            record = json.loads(marker.read_text(encoding="utf-8"))
            if record == {"version": VERSION, "archive_sha256": ARCHIVE_SHA256}:
                return executable
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="install-", dir=cache) as temporary:
            stage = Path(temporary)
            archive = stage / "runtime.zip"
            request = Request(ARCHIVE_URL, headers={"User-Agent": "ModViewer-AssetExtraction"})
            digest, count = hashlib.sha256(), 0
            with urlopen(request, timeout=45) as response, archive.open("wb") as output:
                while block := response.read(1024 * 1024):
                    check_cancelled(cancel)
                    count += len(block)
                    if count > 512 * 1024 * 1024:
                        raise ValueError("The extraction runtime download is too large.")
                    digest.update(block)
                    output.write(block)
            if digest.hexdigest() != ARCHIVE_SHA256:
                raise ValueError("Extraction runtime checksum verification failed.")
            unpacked = stage / "unpacked"
            with zipfile.ZipFile(archive) as bundle:
                if len(bundle.infolist()) > 20000 or sum(x.file_size for x in bundle.infolist()) > 2 * 1024 ** 3:
                    raise ValueError("Extraction runtime archive exceeds its limits.")
                for member in bundle.infolist():
                    target = (unpacked / member.filename.replace("\\", "/")).resolve()
                    if not target.is_relative_to(unpacked.resolve()) or member.external_attr >> 16 & 0o170000 == 0o120000:
                        raise ValueError("Extraction runtime contains an unsafe archive entry.")
                bundle.extractall(unpacked)
            if not (unpacked / executable.name).is_file():
                raise ValueError("Extraction runtime executable is missing.")
            (unpacked / marker.name).write_text(json.dumps({"version": VERSION, "archive_sha256": ARCHIVE_SHA256}), encoding="utf-8")
            if destination.exists():
                raise ValueError("The cached extraction runtime is incomplete. Remove that cache and retry.")
            unpacked.rename(destination)
    return executable


def find_paks(game_folder):
    requested = Path(game_folder).absolute()
    root = requested.resolve()
    if requested != root:
        raise ValueError("The selected game folder changed. Choose it again through the native picker.")
    candidates = [root / "Client" / "Content" / "Paks", root / "Content" / "Paks", root]
    for candidate in candidates:
        if candidate.is_dir() and candidate.resolve().is_relative_to(root):
            archives = [p for p in candidate.iterdir() if p.suffix.lower() in (".pak", ".utoc") and p.is_file()]
            if any(not p.resolve().is_relative_to(root) for p in archives):
                raise ValueError("Game archives must remain inside the selected game folder.")
            if archives:
                return candidate
    raise ValueError("Choose the Wuthering Waves game folder containing Client/Content/Paks, or the Paks folder itself.")


class Client:
    def __init__(self, executable, game_folder, work, cancel):
        self.executable, self.work, self.cancel = Path(executable), Path(work), cancel
        self.config = self.work / "games.json"
        self.config.write_text(json.dumps({"games": {"wuwa": {
            "paksDir": str(find_paks(game_folder)), "ueVersion": "GAME_WutheringWaves",
            "aesUrl": "https://yarik0chka.github.io/wuwa-keys/keys.json",
        }}}), encoding="utf-8")
        self.serial = 0

    def call(self, *arguments, timeout=600, allow_partial=False):
        check_cancelled(self.cancel)
        self.serial += 1
        output = self.work / f"command-{self.serial}.json"
        error = self.work / f"command-{self.serial}.log"
        environment = {**os.environ, "CUE4PARSE_MCP_LOG_LEVEL": "Warning"}
        with output.open("wb") as stdout, error.open("wb") as stderr:
            process = subprocess.Popen([str(self.executable), *arguments, "--config", str(self.config)],
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    check_cancelled(self.cancel)
                    if time.monotonic() > deadline:
                        raise ValueError("The game extraction tool timed out.")
                    time.sleep(.1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        if output.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("The game extraction response is too large.")
        try:
            result = json.loads(output.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise ValueError("The game extraction tool returned an invalid response.") from error
        # Native logs/configuration may contain keys. Never forward them to the UI.
        partial = allow_partial and isinstance(result, dict) and result.get("state") == "completed_with_errors"
        if (process.returncode and not partial) or not isinstance(result, dict) or result.get("failures") or (result.get("error") and not partial):
            raise ValueError("Game data could not be extracted. Check that the selected installation is complete and its version is supported.")
        return result

    def search(self, query, kind, under):
        if query and not query[0].isalnum():
            raise ValueError("Use a character name beginning with a letter or number.")
        result = self.call("search", "wuwa", query, "--type", kind, "--under", under, "--limit", "500")
        if result.get("truncated"):
            raise SearchTruncated("The game search is too broad. Use a more specific character name.")
        packages = [row["path"] for row in result.get("results", [])]
        if any(not isinstance(package, str) or not package.startswith("Client/Content/") for package in packages):
            raise ValueError("The game search returned unsupported package identities.")
        return packages

    def folders(self, folder):
        result = self.call("ls", "wuwa", folder)
        names = result.get("folders", [])
        if not isinstance(names, list) or any(not isinstance(name, str) or "/" in name or name in ("", ".", "..") for name in names):
            raise ValueError("The game folder listing returned unsupported names.")
        return names

    def export(self, kind, package, destination, format=None):
        arguments = [f"export-{kind}", "wuwa", package, "--out", str(destination)]
        if format:
            arguments += ["--format", format]
        result = self.call(*arguments)
        files = [Path(file).resolve() for file in result.get("rootFiles", [])]
        if not files or any(not p.is_file() or not p.is_relative_to(Path(destination).resolve()) for p in files):
            raise ValueError("The game export did not produce a contained output file.")
        return files

    def batch(self, kind, packages, destination, format=None):
        files = {}
        for start in range(0, len(packages), 500):
            request = {"kind": kind, "paths": packages[start:start + 500], "outDir": str(destination)}
            if format:
                request["format"] = format
            request_file = self.work / "batch-request.json"
            request_file.write_text(json.dumps(request), encoding="utf-8")
            result = self.call("batch", "wuwa", "--request", str(request_file), "--run", allow_partial=True)
            # The CLI bounds inline items; the complete manifest is authoritative.
            manifest = Path(result.get("manifestPath", "")).resolve()
            if not manifest.is_relative_to(Path(destination).resolve()):
                raise ValueError("The game batch manifest escaped its output folder.")
            result = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if result.get("state") not in ("completed", "completed_with_errors"):
                raise ValueError("Some game exports failed; no Asset files were changed.")
            for item in result.get("items", []):
                files[item["path"]] = item
        return files
