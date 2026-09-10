"""Authoritative, atomic BC7 DDS color saving."""

from __future__ import annotations

import logging
import os
from core.textures.dds import inspect_dds_layout

from app.mods import metadata

from .errors import TextureSaveError, error_result as _error
from .progress import SaveProgressReporter
from .request import affected_texture_keys, texture_details
from .coverage import prepare_texture_save
from .bc7_recolor import _save_bc7_blocks
from . import transaction


_LOGGER = logging.getLogger(__name__)


def _unique_strings(values):
    return list(dict.fromkeys(value for value in values
                              if isinstance(value, str) and value))


def _clear_committed_color_adjustments(folder_path, targets, saved_meshes):
    """Compare only committed targets before clearing their metadata."""
    captured = {}
    for target in targets if isinstance(targets, list) else []:
        if not isinstance(target, dict):
            continue
        semantic_key = target.get("semantic_key")
        metadata_key = target.get("metadata_key")
        if (isinstance(semantic_key, str) and semantic_key
                and isinstance(metadata_key, str) and metadata_key):
            captured.setdefault((semantic_key, metadata_key), []).append(target)

    expected = {}
    failed = []
    invalid_saved_identity = False
    for saved in saved_meshes if isinstance(saved_meshes, list) else []:
        if not isinstance(saved, dict):
            invalid_saved_identity = True
            continue
        semantic_key = saved.get("semantic_key")
        metadata_key = saved.get("metadata_key")
        if not (isinstance(semantic_key, str) and semantic_key
                and isinstance(metadata_key, str) and metadata_key):
            invalid_saved_identity = True
            if isinstance(metadata_key, str) and metadata_key:
                failed.append(metadata_key)
            continue
        if metadata_key in failed:
            continue
        candidates = captured.get((semantic_key, metadata_key), [])
        if len(candidates) != 1:
            failed.append(metadata_key)
            continue
        if metadata_key in expected:
            expected.pop(metadata_key, None)
            failed.append(metadata_key)
            continue
        expected[metadata_key] = candidates[0].get("adjustment")

    receipt = {"cleared": [], "preserved": [], "failed": failed}
    helper_failed = False
    if expected:
        try:
            reset = metadata.clear_mesh_color_adjustments_if_unchanged(
                folder_path, expected)
        except Exception:
            reset = None
            helper_failed = True
        if not isinstance(reset, dict):
            helper_failed = True
        else:
            for status in ("cleared", "preserved", "failed"):
                receipt[status].extend(reset.get(status, []))
            if reset.get("error") and not reset.get("failed"):
                helper_failed = True

        if helper_failed:
            receipt["cleared"] = [
                key for key in receipt["cleared"] if key not in expected]
            receipt["preserved"] = [
                key for key in receipt["preserved"] if key not in expected]
            receipt["failed"].extend(expected)

    for status in receipt:
        receipt[status] = _unique_strings(receipt[status])
    return receipt, invalid_saved_identity or helper_failed


def save_texture_color(
        context, overrides, active_mesh_keys, selected_texture_key, targets,
        texture_usage, progress_callback=None):
    """Save captured Color changes by editing authorized BC7 blocks."""
    committed = False
    success_result = None
    stage = "prepare"
    progress = (SaveProgressReporter(progress_callback)
                if progress_callback is not None else None)
    if progress is not None:
        progress.stage("preparing")
    try:
        prepared = prepare_texture_save(
            context, overrides, active_mesh_keys, selected_texture_key, targets,
            texture_usage)

        stage = "read"
        if progress is not None:
            progress.stage("reading")
        original = transaction._read_source(prepared.selected_path)
        original_hash = transaction._sha256_bytes(original)
        affected = affected_texture_keys(context, prepared)

        stage = "processing"
        candidate, _stats = _save_bc7_blocks(
            original, prepared, progress_reporter=progress)
        stage = "write"
        if progress is not None:
            progress.stage("writing")
        temporary = transaction._write_temp(prepared.selected_path, candidate)
        try:
            candidate_layout = inspect_dds_layout(temporary)
            if candidate_layout != prepared.layout:
                raise TextureSaveError(
                    "texture_validation_failed",
                    "The saved DDS changed the source texture layout.")
            backup_path = transaction.replace_source(
                prepared.selected_path, temporary, original, original_hash,
                candidate)
            success_result = {
                "status": "ok",
                "tex_key": selected_texture_key,
                "affected_tex_keys": affected,
                "saved_meshes": [
                    {"semantic_key": target.semantic_key,
                     "metadata_key": target.metadata_key}
                    for target in prepared.targets],
                "texture": texture_details(
                    prepared.selected_path, prepared.info),
                "backup": {"file": os.path.basename(backup_path)},
            }
            committed = True
            temporary = None
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)
        receipt, cleanup_failed = _clear_committed_color_adjustments(
            context.mod_dir, targets, success_result.get("saved_meshes"))
        success_result["metadata_reset"] = receipt
        if cleanup_failed or receipt["failed"]:
            success_result["warning"] = "color_state_reset_failed"
        if progress is not None:
            progress.stage("complete")
        return success_result
    except TextureSaveError as error:
        if committed and success_result is not None:
            success_result["warning"] = "post_save_cleanup_failed"
            return success_result
        return _error(error.code, error.message, error.status,
                      **({"details": error.details} if error.details else {}))
    except Exception:
        if committed and success_result is not None:
            success_result["warning"] = "post_save_cleanup_failed"
            return success_result
        _LOGGER.exception("Unexpected error while saving texture (stage=%s)",
                          stage)
        return _error(
            "texture_write_failed", "The DDS could not be saved safely.",
            details={"stage": stage})


__all__ = [
    "TextureSaveError", "save_texture_color",
]
