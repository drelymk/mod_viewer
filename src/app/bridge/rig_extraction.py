"""Native game-folder grants, extraction jobs and atomic Asset sidecar installs."""

import hashlib
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

import webview

from app.assets import paths as asset_paths
from app.assets.game_extract import game_index, pipeline
from app.assets.game_extract.native import capture
from app.assets.game_extract.runtime import Client, ExtractionCancelled, check_cancelled, ensure_runtime, find_paks
from app.assets.permissions import inherit_directory_permissions
from app.settings import config, game_folders

_OUTPUTS = ("Rig.json", "Animation", "RigExtraction.json")


def sidecar_revision(asset):
    result = {}
    for name in _OUTPUTS:
        path = Path(asset) / name
        if path.is_symlink():
            raise ValueError("Rig extraction cannot replace linked sidecars.")
        files = list(path.rglob("*")) if path.is_dir() else [path] if path.exists() else []
        if len(files) > 4096:
            raise ValueError("The existing rig/animation library is too large.")
        for file in files:
            if file.is_symlink():
                raise ValueError("Rig extraction cannot replace linked sidecars.")
            if file.is_file():
                with file.open("rb") as stream:
                    result[str(file.relative_to(asset))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def install_package(asset, output, stage):
    """Rollback all sidecars together, retaining recovery data if rollback fails."""
    asset, output, stage = Path(asset).resolve(), Path(output).resolve(), Path(stage).resolve()
    if not output.is_relative_to(stage) or stage.parent != asset.parent:
        raise ValueError("Rig staging must be beside its Asset on the destination volume.")
    backup = stage / "previous"
    backup.mkdir()
    installed, saved = [], []
    try:
        for name in _OUTPUTS:
            target = asset / name
            if target.is_symlink() or not (output / name).exists():
                raise ValueError("Rig extraction output changed before installation.")
            if target.exists():
                target.rename(backup / name)
                saved.append(name)
            (output / name).rename(target)
            installed.append(name)
            inherit_directory_permissions(target)
    except BaseException:
        try:
            for name in reversed(installed):
                (asset / name).rename(output / name)
            for name in reversed(saved):
                (backup / name).rename(asset / name)
        except OSError as error:
            raise ValueError(f"Rig installation could not be rolled back. Recovery files were retained in {stage}.") from error
        raise


class RigExtraction:
    def __init__(self, selection):
        self._selection = selection
        self._lock = threading.RLock()
        self._games, self._jobs = {}, {}
        self._active = None

    def _grant(self, folder):
        folder = Path(folder).resolve()
        find_paks(folder)
        token = uuid.uuid4().hex
        with self._lock:
            if len(self._games) >= 16:
                self._games.pop(next(iter(self._games)))
            self._games[token] = str(folder)
        return {"token": token, "path": str(folder)}

    def pick_game(self, window):
        result = window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return None
        granted = self._grant(result[0])
        try:
            # A folder chosen for extraction becomes the Settings folder, so it is added or updated there.
            game_folders.save_game_folder("WWMI", granted["path"])
        except (OSError, ValueError):
            pass
        return granted

    def saved_game(self):
        """Grant the folder saved in Settings, which was chosen through the native picker."""
        try:
            folder = game_folders.load_game_folders().get("WWMI")
            return self._grant(folder) if folder and Path(folder).is_dir() else None
        except (OSError, ValueError):
            return None

    def index_status(self):
        try:
            folder = game_folders.load_game_folders().get("WWMI")
            if not folder or not Path(folder).is_dir():
                return {"state": "none", "meshes": 0}
            return game_index.status(folder)
        except (OSError, ValueError):
            return {"state": "none", "meshes": 0}

    def _begin(self):
        with self._lock:
            if self._active:
                raise ValueError("A game extraction or indexing job is already running.")
            if len(self._jobs) >= 32:
                self._jobs.pop(next(iter(self._jobs)))
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = dict(status="running", phase="prepare", current=0, total=0, cancel=threading.Event())
            self._active = job_id
            return job_id

    def start_index(self):
        """Build the index of the Settings game folder in the background."""
        try:
            folder = game_folders.load_game_folders().get("WWMI")
            if not folder or not Path(folder).is_dir():
                raise ValueError("Choose the Wuthering Waves game folder in Settings first.")
            game = self._grant(folder)["path"]
            job_id = self._begin()
            threading.Thread(target=self._run_index, args=(job_id, game), daemon=True).start()
            return {"job_id": job_id, "status": "running"}
        except (OSError, ValueError, TypeError) as error:
            return {"error": str(error)}

    def _run_index(self, job_id, game):
        stage = None
        with self._lock:
            cancel = self._jobs[job_id]["cancel"]

        def progress(phase, current=0, total=0):
            with self._lock:
                self._jobs[job_id].update(phase=phase, current=current, total=total)
            check_cancelled(cancel)

        try:
            progress("tools")
            executable = ensure_runtime(cancel)
            stage = Path(tempfile.mkdtemp(prefix="mod-viewer-index-"))
            _, count = game_index.build(Client(executable, game, stage, cancel), game, progress, cancel)
            with self._lock:
                self._jobs[job_id].update(status="complete", phase="complete", result={"meshes": count})
        except ExtractionCancelled as error:
            with self._lock:
                self._jobs[job_id].update(status="cancelled", error=str(error))
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "The game index could not be built."
            with self._lock:
                self._jobs[job_id].update(status="error", error=message)
        finally:
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
            with self._lock:
                self._active = None

    def start(self, folder, game_token, query=None):
        try:
            selected, entry, _, record = self._selection(folder)
            if entry["type"] != "WWMI":
                raise ValueError("Game rig/animation extraction currently supports Wuthering Waves Assets.")
            target = Path(asset_paths.safe_asset_dir(entry["path"], record["path"]) or "")
            if not target.is_absolute() or not target.is_dir():
                raise ValueError("Select an indexed Asset folder.")
            query = target.name if query is None else query
            if (not isinstance(query, str) or not 1 <= len(query.strip()) <= 100
                    or not query.strip()[0].isalnum() or any(ord(c) < 32 for c in query)):
                raise ValueError("Enter a character name beginning with a letter or number, at most 100 characters long.")
            with self._lock:
                game = self._games.get(game_token) if isinstance(game_token, str) else None
                if not game:
                    raise ValueError("Choose the game folder through the native picker first.")
                if target.resolve().is_relative_to(Path(game)) or Path(game).is_relative_to(target.resolve()):
                    raise ValueError("The Asset and game installation must be separate folders.")
                if self._active:
                    raise ValueError("A rig extraction is already running.")
                job_id = self._begin()
            threading.Thread(target=self._run, args=(job_id, selected, entry, record, game, query.strip()), daemon=True).start()
            return {"job_id": job_id, "status": "running"}
        except (OSError, ValueError, PermissionError, TypeError) as error:
            return {"error": str(error)}

    def status(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id) if isinstance(job_id, str) else None
            return {key: value for key, value in job.items() if key != "cancel"} if job else {"error": "That extraction job is unavailable."}

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id) if isinstance(job_id, str) else None
            if not job or job["status"] != "running" or job["phase"] == "install":
                return {"cancelled": False}
            job["cancel"].set()
            return {"cancelled": True}

    def _run(self, job_id, selected, entry, record, game, query):
        stage = None
        committed = False
        with self._lock:
            cancel = self._jobs[job_id]["cancel"]

        def progress(phase, current=0, total=0):
            with self._lock:
                self._jobs[job_id].update(phase=phase, current=current, total=total)
            check_cancelled(cancel)

        try:
            asset = Path(asset_paths.safe_asset_dir(entry["path"], record["path"]) or "")
            if not asset.is_absolute():
                raise ValueError("The selected Asset is no longer available.")
            original = sidecar_revision(asset)
            _, geometry = capture(entry["path"], record)
            progress("tools")
            executable = ensure_runtime(cancel)
            stage = Path(tempfile.mkdtemp(prefix=".rig-extract-", dir=asset.parent))
            work = stage / "work"
            work.mkdir()
            output = stage / "output"
            client = Client(executable, game, work, cancel)
            try:
                index = game_index.ensure(client, game, progress, cancel)
            except ExtractionCancelled:
                raise
            except Exception:
                # The index is only an accelerator; native searches remain correct without it.
                index = None
            result, fingerprints = pipeline.build(client, entry["path"], record, output, query, progress, cancel, index)
            check_cancelled(cancel)
            with config.transaction():
                current, current_entry, _, current_record = self._selection(selected)
                if (current != selected or current_entry != entry or current_record != record
                        or sidecar_revision(asset) != original or fingerprints != geometry):
                    raise ValueError("The Asset changed during extraction. Reload it and retry.")
                for file, digest in geometry.items():
                    with open(file, "rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                            raise ValueError("The captured Asset geometry changed during extraction.")
                progress("install")
                install_package(asset, output, stage)
                committed = True
            with self._lock:
                self._jobs[job_id].update(status="complete", phase="complete", result={
                    key: result[key] for key in ("bones", "clips", "imported", "secondary", "skipped")}, path=selected)
        except ExtractionCancelled as error:
            with self._lock:
                self._jobs[job_id].update(status="cancelled", error=str(error))
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Do not expose raw native logs, configuration or credentials.
            message = str(error) if isinstance(error, ValueError) else "Rig extraction failed; existing Asset files were preserved."
            with self._lock:
                self._jobs[job_id].update(status="error", error=message)
        except Exception:
            with self._lock:
                self._jobs[job_id].update(status="error", error="Rig extraction failed; existing Asset files were preserved.")
        finally:
            try:
                if (stage is not None and stage.name.startswith(".rig-extract-")
                        and stage.resolve().parent == asset.resolve().parent
                        and (committed or not (stage / "previous").exists() or not any((stage / "previous").iterdir()))):
                    shutil.rmtree(stage, ignore_errors=True)
            except OSError:
                pass
            finally:
                with self._lock:
                    self._active = None
