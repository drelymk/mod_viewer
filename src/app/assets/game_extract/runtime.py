"""Bundled, pinned direct CUE4Parse helper and one bounded extraction session."""

import hashlib
import json
from pathlib import Path
import subprocess
import time

from app.settings import paths


PARSER_COMMIT = "cb909733773daea9015a67f6955d6f2ec24d3629"
VERSION = "direct-1-" + PARSER_COMMIT[:12]
MAX_RESPONSE = 32 * 1024 * 1024


class ExtractionCancelled(ValueError):
    pass


class SearchTruncated(ValueError):
    pass


def check_cancelled(cancel):
    if cancel.is_set():
        raise ExtractionCancelled("Extraction cancelled; existing Asset files were preserved.")


def ensure_runtime(cancel, bundle=None):
    root = Path(bundle) if bundle else Path(paths.vendor_dir()) / "game-extract"
    executable = root / "ModViewer.GameExtract.exe"
    check_cancelled(cancel)
    try:
        record = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
        if record.get("version") != VERSION or record.get("parser") != PARSER_COMMIT:
            raise ValueError("Unsupported extraction runtime version.")
        files = record.get("files", {})
        required = {executable.name, "CUE4Parse.dll", "CUE4Parse-Conversion.dll", "CUE4Parse-Natives.dll",
                    "oodle-data-shared.dll", "zlib-ng2.dll", "ModViewer.GameExtract.deps.json", "ModViewer.GameExtract.runtimeconfig.json"}
        if not isinstance(files, dict) or not required.issubset(files) or len(files) > 2000:
            raise ValueError("Incomplete extraction runtime manifest.")
        for relative, digest in files.items():
            check_cancelled(cancel)
            file = (root / relative).resolve()
            if not file.is_relative_to(root.resolve()) or not file.is_file():
                raise ValueError("Unsafe or missing extraction runtime file.")
            with file.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("Extraction runtime checksum verification failed.")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("The bundled extraction helper is missing or incomplete. Rebuild it with tools/build_game_extract.py or reinstall the viewer.") from error
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
        self.paks = find_paks(game_folder)
        self.serial = 0
        self.process = None

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdin:
                process.stdin.close()

    def call(self, operation, *, timeout=600, **arguments):
        check_cancelled(self.cancel)
        self.serial += 1
        output = self.work / f"command-{self.serial}.json"
        try:
            if self.process is None:
                self.process = subprocess.Popen(
                    [str(self.executable), "--paks", str(self.paks), "--work", str(self.work)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            request = json.dumps(dict(id=self.serial, op=operation, **arguments)).encode("utf-8") + b"\n"
            if len(request) > 256 * 1024:
                raise ValueError("The game extraction request is too large.")
            self.process.stdin.write(request)
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while not output.is_file():
                check_cancelled(self.cancel)
                if self.process.poll() is not None:
                    raise ValueError("The game extraction helper stopped unexpectedly.")
                if time.monotonic() > deadline:
                    raise ValueError("The game extraction tool timed out.")
                time.sleep(.05)
            check_cancelled(self.cancel)
            if output.stat().st_size > MAX_RESPONSE:
                raise ValueError("The game extraction response is too large.")
            result = json.loads(output.read_text(encoding="utf-8-sig"))
            if not isinstance(result, dict) or result.get("error"):
                raise ValueError("Game data could not be extracted. Check that the selected installation is complete and its version is supported.")
            return result
        except (OSError, json.JSONDecodeError) as error:
            self.close()
            raise ValueError("The game extraction helper returned an invalid response.") from error
        except BaseException:
            self.close()
            raise
        finally:
            output.unlink(missing_ok=True)

    def search(self, query, kind, under):
        if query and not query[0].isalnum():
            raise ValueError("Use a character name beginning with a letter or number.")
        result = self.call("search", query=query, type=kind, under=under)
        if result.get("truncated"):
            raise SearchTruncated("The game search is too broad. Use a more specific character name.")
        packages = [row["path"] for row in result.get("results", [])]
        if any(not isinstance(package, str) or not package.startswith("Client/Content/") for package in packages):
            raise ValueError("The game search returned unsupported package identities.")
        return packages

    def folders(self, folder):
        result = self.call("folders", folder=folder)
        names = result.get("folders", [])
        if not isinstance(names, list) or any(not isinstance(name, str) or "/" in name or "\\" in name or name in ("", ".", "..") for name in names):
            raise ValueError("The game folder listing returned unsupported names.")
        return names

    @staticmethod
    def _files(result, destination):
        files = [Path(file).resolve() for file in result.get("rootFiles", [])]
        if not files or any(not p.is_file() or not p.is_relative_to(Path(destination).resolve()) for p in files):
            raise ValueError("The game export did not produce a contained output file.")
        return files

    def export(self, kind, package, destination, format=None):
        self._format(kind, format)
        result = self.call("export", kind=kind, path=package, outDir=str(destination))
        return self._files(result, destination)

    def batch(self, kind, packages, destination, format=None):
        self._format(kind, format)
        files = {}
        for start in range(0, len(packages), 500):
            chunk = packages[start:start + 500]
            result = self.call("batch", kind=kind, paths=chunk, outDir=str(destination))
            items = result.get("items", [])
            if len(items) != len(chunk) or {item.get("path") for item in items} != set(chunk):
                raise ValueError("The game batch returned incomplete package identities.")
            for item in items:
                if item.get("state") == "completed":
                    self._files(item, destination)
                files[item["path"]] = item
        return files

    @staticmethod
    def _format(kind, format):
        if kind not in ("json", "mesh", "anim") or format not in (None, {"mesh": "ueformat", "anim": "psa"}.get(kind)):
            raise ValueError("Unsupported game extraction format.")
