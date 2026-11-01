"""Extract compact assets and replace their directory/index as one transaction."""

import os
import json
from pathlib import Path
import shutil
import tempfile

from app.settings import config
from . import folders, index
from .paths import relative_asset_path
from .extractor.discovery import analyze
from .extractor.export import Selection, export, extract, safe_name


def _target(folder_path, name, original_path):
    if not isinstance(name, str) or len(name) > 100:
        raise ValueError("Asset name must be at most 100 characters.")
    requested = folders.normalize_path(folder_path)
    entries = folders.load_registry()
    roots = [item for item in entries
             if folders.is_within(requested, item["path"])]
    if not roots:
        raise ValueError("That folder is not inside a registered Asset Folder.")
    entry = max(roots, key=lambda item: len(item["path"]))
    parent = Path(requested)
    if not parent.is_dir():
        raise ValueError("Asset Folder does not exist.")
    original = None
    if original_path is not None:
        original = Path(folders.normalize_path(original_path))
        relative = relative_asset_path(entry["path"], str(original))
        current = index.load_index(entry["type"], entry["path"])
        if (original.parent != parent or not relative or relative == "."
                or index.find_asset_by_path(current, relative) is None
                or any(folders.is_within(item["path"], str(original))
                       for item in entries)):
            raise ValueError("Choose an indexed asset inside this Asset Folder.")
    destination = parent / safe_name(name)
    if (not folders.is_within(str(destination), entry["path"])
            or destination.is_symlink()):
        raise ValueError("Asset destination escapes its registered folder.")
    if os.path.lexists(destination) and destination != original:
        raise ValueError("An asset or file with that name already exists.")
    return entries, entry, destination, original


@config.transaction()
def extract_asset(folder_path, name, dump_path, original_path=None):
    entries, entry, destination, original = _target(
        folder_path, name, original_path)
    dump = folders.normalize_path(dump_path)
    if (folders.is_within(dump, entry["path"])
            or folders.is_within(entry["path"], dump)):
        raise ValueError("The dump must be outside the Asset Folder.")
    # Stage on the destination volume so committing uses directory renames.
    asset_type = entry["type"]
    previous_index = index.snapshot_index(asset_type, entry["path"])
    stage = Path(tempfile.mkdtemp(prefix=".asset-extract-", dir=entry["path"]))
    payload = stage / "payload"
    backup = stage / "previous"
    installed = False
    committed = False
    warnings = []
    try:
        payload.mkdir()
        package = extract(analyze(dump), Selection(destination.name))
        report = export(package, payload)
        report_file = Path(report.report_path)
        details = json.loads(report_file.read_text(encoding="utf-8"))
        details["report"].update(
            output=str(destination), report_path=str(destination / "extraction_report.json"))
        report_file.write_text(json.dumps(details, indent=2), encoding="utf-8")
        report_file.rename(Path(report.output) / "extraction_report.json")
        if original is not None:
            original.rename(backup)
        try:
            Path(report.output).rename(destination)
            installed = True
            rebuilt = index.build_index(asset_type, entry["path"], exclude_paths=(stage,))
            relative = relative_asset_path(entry["path"], str(destination))
            if index.find_asset_by_path(rebuilt, relative) is None:
                raise ValueError("Extracted asset could not be indexed.")
            index.save_index(rebuilt)
            committed = True
        except BaseException:
            if installed:
                destination.rename(stage / "failed")
            if backup.exists():
                backup.rename(original)
            index.restore_index(asset_type, entry["path"], previous_index)
            raise
        return {"entries": entries, "path": str(destination), "warnings": warnings,
                "extraction": {
                    "components": report.components, "parts": report.parts,
                    "partial": report.partial,
                    "skipped": sum(component.requires_review for component in package.analysis.components),
                }}
    finally:
        # Retain the backup if a rollback itself failed; never delete recovery data.
        if committed or not backup.exists():
            try:
                shutil.rmtree(stage)
            except OSError:
                if committed:
                    warnings.append(f"Asset saved; temporary files could not be removed: {stage}")
