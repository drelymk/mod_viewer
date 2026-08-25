"""WuWa component texture candidate discovery."""

from dataclasses import dataclass
import os
import re

from core.ini_parser import TextureOverrideIndex
from core.resource_paths import safe_resource_path
from .wuwa_texture_names import texture_component_ordinals


_COMPONENT_RE = re.compile(
    r"^Component(?P<ordinal>\d+)(?:_\d+)?$", re.I)
_FILENAME_SOURCE = "wuwa_filename"
_SLOT_SOURCE = "wuwa_ps_slot"


@dataclass(frozen=True, slots=True)
class _Candidate:
    file: str
    source: str


def _component_ordinal(group):
    name = group.get("display_name") or group.get("name")
    match = _COMPONENT_RE.fullmatch(str(name or ""))
    return int(match.group("ordinal")) if match else None


def _file_key(path):
    return os.path.normcase(os.path.normpath(path))


def _filename_matches_component(replacement, ordinal):
    filename = replacement.file
    if not isinstance(filename, str):
        return False
    components = texture_component_ordinals(filename)
    return components is not None and ordinal in components


def _filename_candidates(ordinal, texture_index):
    result = []
    for replacements in (texture_index.replacements_by_hash or {}).values():
        for replacement in replacements:
            if (not replacement.file
                    or not _filename_matches_component(replacement, ordinal)):
                continue
            result.append(_Candidate(
                file=replacement.file,
                source=_FILENAME_SOURCE,
            ))
    return result


def _slot_candidates(group):
    result = []
    for draw in group.get("draws", []) or []:
        for binding in getattr(draw, "slot_textures", ()) or ():
            filename = binding.file
            if not isinstance(filename, str) or not filename:
                continue
            result.append(_Candidate(
                file=filename,
                source=_SLOT_SOURCE,
            ))
    return result


def _record_discovered(group, candidates, mod_dir):
    """Store safe, associated files for the viewer's manual texture pool."""
    discovered = {}
    for candidate in candidates:
        path = safe_resource_path(mod_dir, candidate.file)
        if path is None or not os.path.isfile(path):
            continue
        key = _file_key(path)
        discovered.setdefault(key, {
            "file": candidate.file,
            "source": candidate.source,
        })

    group["discovered_textures"] = list(discovered.values())


def apply(groups, mod_dir):
    """Discover WuWa texture files without assigning semantic texture roles."""
    for group in groups or ():
        candidates = _slot_candidates(group)

        ordinal = _component_ordinal(group)
        texture_index = group.get("_texture_override_index")
        if ordinal is not None and isinstance(
                texture_index, TextureOverrideIndex):
            candidates.extend(_filename_candidates(ordinal, texture_index))

        _record_discovered(group, candidates, mod_dir)
