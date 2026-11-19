"""Prove native GIMI palettes from a mod's stock skinning-pass Blend overrides.

GIMI skins each model once, in a pass whose override binds the Blend stream at
vb1, skips the original draw and issues ``draw = <vertices>, 0``. A Blend file
bound only there, unchanged and unaliased, uses the native renderer palette of
that pass's input hash.
"""

import re

from ..geometry.skinning import normalize_skinning_source_file
from .wwmi_skeleton import _commands
from .zzz_skeleton import _TEXTURE_HELPERS, safe_call_sections

# GIMI's stock pixel helpers, the counterparts of ZZMI's SetTextures: ORFix/NNFix
# and GIMI texture binding only touch pixel-shader state.
_HELPERS = {*_TEXTURE_HELPERS, r"commandlist\global\orfix\orfix", r"commandlist\global\orfix\nnfix",
            r"commandlist\gimi\settextures"}
# Multi-variant mods take the vertex count from a variable; the pass is the same.
_SKIN_DRAW = re.compile(r"draw = (?:\d+|\$[\w\\.]+) ?, ?0$")
_BLEND_SLOT = 1


def _candidate_sections(section_info):
    for name, info in section_info.items():
        if not name.casefold().startswith("textureoverride") or info["draws"] or info["drawindexed_auto"]:
            continue
        match = info.get("geometry_match_at_end")
        history = info.get("vertex_binding_history", ())
        if match and match.hash and any(item["slot"] == _BLEND_SLOT for item in history):
            yield name.casefold(), match.hash, history


def _closure_texts(commands, name):
    """Every command reachable from ``name`` through local ``run`` calls."""
    texts, pending, seen = [], [name], set()
    while pending and len(seen) < 64:
        current = pending.pop()
        if current in seen or current not in commands:
            continue
        seen.add(current)
        for _phase, text in commands[current]:
            texts.append(text)
            left, _, target = text.partition(" = ")
            if left == "run":
                pending.append(target)
    return texts


def stock_gimi_palette_sources(sections, resources, section_info):
    """Retain native input hashes for file-backed, unremapped skinning-pass vb1 streams.

    Returns ``(blend_hashes, safe_sections)``: native hashes per Blend file and
    the lowercase sections whose call closure cannot rebind vertex state.
    """
    candidates = list(_candidate_sections(section_info))
    commands = _commands(sections) if candidates else {}
    if not commands:
        return {}, set()
    safe_sections = safe_call_sections(commands, _HELPERS)
    # Any assignment to, or non-vb1 use of, a Blend resource can write through
    # the immutable file binding.
    written, referenced = set(), set()
    for rows in commands.values():
        for _, text in rows:
            left, sep, right = text.partition("=")
            if sep:
                written.add(left.strip())
                if left.strip() != f"vb{_BLEND_SLOT}":
                    referenced.update(right.split())
    hashes = {}
    for name, native_hash, history in candidates:
        if name not in safe_sections:
            continue
        texts = _closure_texts(commands, name)
        # Only the skinning pass (skip the original draw, re-issue it unindexed) is proof.
        if "handling = skip" not in texts or not any(_SKIN_DRAW.match(text) for text in texts):
            continue
        blends = {item["resource"] for item in history if item["slot"] == _BLEND_SLOT}
        if None in blends:
            continue
        for blend in blends:
            resource = resources.get_ci(blend)
            blend_file = normalize_skinning_source_file(resource.get("filename"))
            types = [text for _, text in commands.get(blend.casefold(), ()) if text.startswith("type = ")]
            key = blend.casefold()
            if (not blend_file or resource.get("stride") != 32 or types not in ([], ["type = buffer"])
                    or key in written or key in referenced):
                continue
            hashes.setdefault(blend_file, set()).add(native_hash)
    return {file: tuple(sorted(values)) for file, values in hashes.items()}, safe_sections
