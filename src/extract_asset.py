"""Headless, local Manager integration: extract a completed shared-memory capture."""

import argparse
import json
import os
from pathlib import Path
import sys

from app.assets import folders, index
from app.assets.extraction import extract_asset
from app.assets.extractor.export import safe_name
from app.assets.extractor.memory_capture import MemoryCapture
from app.settings import config
from app.settings import paths


def install_capture(request):
    if (
        not isinstance(request, dict)
        or type(request.get("version")) is not int
        or request["version"] != 1
    ):
        raise ValueError("Unsupported extraction request")
    folder, asset_type, name = (
        request.get("folder"),
        request.get("asset_type"),
        request.get("name"),
    )
    if not isinstance(folder, str) or not os.path.isabs(folder):
        raise ValueError("Select an absolute Asset Folder in Manager")
    if not isinstance(name, str) or len(name) > 100 or safe_name(name) != name:
        raise ValueError("Invalid asset name")
    # The local CLI is a separate native entry point. Its explicit configured
    # output root authorizes registration; browser paths never reach this route.
    entry = folders.validate_folder_entry(asset_type, folder)
    with MemoryCapture(request.get("capture", {})) as as_source:
        return _install(entry, name, as_source)


def _install(entry, name, source):
    with config.transaction():
        before = folders.load_registry()
        exact = next((e for e in before if e["path"] == entry["path"]), None)
        if exact and exact["type"] != entry["type"]:
            raise ValueError("Asset Folder is registered for another game type")
        if not exact and any(
            folders.is_within(entry["path"], e["path"])
            or folders.is_within(e["path"], entry["path"])
            for e in before
        ):
            raise ValueError(
                "Choose a separate Asset Folder; registered roots must not overlap"
            )
        added = exact is None
        if added:
            folders.add_folder(entry["type"], entry["path"])
        try:
            destination = Path(entry["path"]) / name
            original = str(destination) if os.path.lexists(destination) else None
            if original:
                current = index.load_index(entry["type"], entry["path"])
                if index.find_asset_by_path(current, name) is None:
                    raise ValueError(
                        "Existing folder is not an indexed Asset; choose another name"
                    )
            return extract_asset(entry["path"], name, source, original)
        except BaseException:
            if added:
                folders.write_entries(before)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help="Explicit Viewer configuration for a headless session",
    )
    args = parser.parse_args()
    if args.config:
        paths.config_path = lambda: str(args.config.resolve())
    try:
        raw = sys.stdin.buffer.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("Extraction manifest is too large")
        request = json.loads(raw)
        print(json.dumps({"ok": True, "result": install_capture(request)}))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
