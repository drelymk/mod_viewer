"""ZZZ archive validation and the shared bounded native extraction transport."""

import hashlib
import json
from pathlib import Path

from . import runtime
from .bundle import runtime_directory

PARSER_COMMIT = "76234e9f6f5a839504f41f9612719529fe2309a5"
VERSION = "zzz-9-" + PARSER_COMMIT[:12]
# Runtime clip decoding can change without changing cached discovery metadata.
INDEX_VERSION = "zzz-4-" + PARSER_COMMIT[:12]
BLOCK_DIRS = ("ZenlessZoneZero_Data/StreamingAssets/Blocks", "ZenlessZoneZero_Data/Persistent/Blocks")


def validate_folder(folder):
    requested = Path(folder).absolute()
    root = requested.resolve()
    if root != requested or not (root / BLOCK_DIRS[0]).is_dir():
        raise ValueError("Choose the Zenless Zone Zero game folder containing ZenlessZoneZero_Data/StreamingAssets/Blocks.")
    archives(root)
    return root


def archives(root):
    root = Path(root).resolve()
    files = []
    for name in BLOCK_DIRS:
        directory = root / name
        if not directory.exists():
            continue
        for member in (directory, *directory.parents):
            if member.is_symlink() or member.is_junction():
                raise ValueError("Game archives must not use linked directories.")
            if member == root:
                break
        for file in directory.glob("*.blk"):
            if file.is_symlink() or not file.resolve().is_relative_to(root) or not file.is_file():
                raise ValueError("Game archives must remain inside the selected game folder.")
            files.append(file)
    if not files:
        raise ValueError("The selected ZZZ installation contains no block archives.")
    return sorted(files)


def ensure_runtime(cancel, bundle=None):
    root = Path(bundle) if bundle else runtime_directory("zzz-extract", cancel)
    runtime.check_cancelled(cancel)
    try:
        record = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("Unsupported extraction runtime manifest.")
        files = record.get("files")
        required = {"ModViewer.ZzzExtract.exe", "UnityReader.dll", "AnimeStudio.ACL.ZZZV2.dll", "AnimeStudio.ACL.DB.dll", "oodle-data-shared.dll",
                    "ModViewer.ZzzExtract.deps.json", "ModViewer.ZzzExtract.runtimeconfig.json"}
        if (record.get("version") != VERSION or record.get("parser") != PARSER_COMMIT
                or not isinstance(files, dict) or not required.issubset(files) or len(files) > 2000):
            raise ValueError("Unsupported extraction runtime manifest.")
        for relative, digest in files.items():
            runtime.check_cancelled(cancel)
            file = (root / relative).resolve()
            if not file.is_relative_to(root.resolve()) or not file.is_file():
                raise ValueError("Unsafe or missing extraction runtime file.")
            with file.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("Extraction runtime checksum verification failed.")
    except (OSError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("The bundled game reader is unavailable. Build it with tools/build_zzz_extract.py or reinstall the viewer.") from error
    return root / "ModViewer.ZzzExtract.exe"


class Client(runtime.Client):
    # ZZZ also indexes files holding materials or textures to locate prop textures.
    index_files = True

    def __init__(self, executable, folder, work, cancel):
        self.root = validate_folder(folder)
        self.executable, self.work, self.cancel = Path(executable), Path(work), cancel
        self.serial, self.process = 0, None

    def command(self):
        return [str(self.executable), "--zzz", str(self.root), "--work", str(self.work)]

    def scan(self, block):
        return self.call("scan", block=Path(block).relative_to(self.root).as_posix())["rows"]

    def scan_many(self, blocks):
        names = [Path(block).relative_to(self.root).as_posix() for block in blocks]
        items = self.call("scan_batch", blocks=names, files=self.index_files)["items"]
        if len(items) != len(names) or [item.get("block") for item in items] != names:
            raise ValueError("The native metadata scan is incomplete.")
        return [{**row, "block": item["block"]} for item in items for row in item["rows"]]

    def read(self, locator):
        return self.call("read", **{key: locator[key] for key in ("block", "offset", "cab", "file_ordinal", "path_id")})
