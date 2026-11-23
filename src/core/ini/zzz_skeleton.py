"""Prove native ZZZ palettes from the canonical per-INI binding history."""

import re

from ..geometry.skinning import normalize_skinning_source_file
from .draw_scan import _EXTERNAL_TEXTURE_CHECKS
from .wwmi_skeleton import _PIXEL_HELPERS, _commands


# Stock helpers that only bind textures; they never touch vertex buffers.
_TEXTURE_HELPERS = {*_EXTERNAL_TEXTURE_CHECKS, r"commandlist\zzmi\settextures", *_PIXEL_HELPERS}
_SHADER_BINDING = re.compile(r"(?:vs(?:-(?:cb|t)\d+)?|cs-cb\d+) = ")


def _candidate_sections(section_info):
    for name, info in section_info.items():
        if not name.casefold().startswith("textureoverride") or info["draws"] or info["drawindexed_auto"]:
            continue
        match = info.get("geometry_match_at_end")
        history = info.get("vertex_binding_history", ())
        if match and match.hash and any(item["slot"] == 2 for item in history):
            yield name.casefold(), match.hash, history


def safe_call_sections(commands, helpers):
    """Sections whose call closure cannot rebind vertex or shader state.

    ``helpers`` are external command lists known to bind pixel state only.
    """
    safe, visiting = {}, set()

    def safe_calls(name):
        if name in safe:
            return safe[name]
        if name not in commands or name in visiting or len(visiting) >= 32:
            return False
        visiting.add(name)
        result = True
        for phase, text in commands[name]:
            # Menu shaders outside the model's call closure are irrelevant.
            if _SHADER_BINDING.match(text) or (phase and text.startswith(("vb", "checktextureoverride = "))):
                result = False
                break
            left, _, target = text.partition(" = ")
            if left == "run" and not (safe_calls(target) if target in commands else target in helpers):
                result = False
                break
        visiting.remove(name)
        safe[name] = result
        return result

    return {name for name in commands if safe_calls(name)}


def stock_zzz_palette_sources(sections, resources, section_info, resolve_vertex_info):
    """Retain native input hashes for file-backed, unaliased vb2 streams.

    Returns ``(blend_hashes, position_blends)``: native hashes per Blend file
    and proven Blend files per Position file. ZZZ skins a stream once, in the
    Blend pass, so only that override's call closure must not rebind vertex or
    shader state; the overrides drawing the skinned stream run after it.
    """
    candidates = list(_candidate_sections(section_info))
    commands = _commands(sections) if candidates else {}
    if not commands:
        return {}, {}

    # Any assignment to, or non-vb2 use of, a Blend resource can write through
    # the immutable file binding.
    written, referenced = set(), set()
    for rows in commands.values():
        for _, text in rows:
            left, sep, right = text.partition("=")
            if sep:
                written.add(left.strip())
                if left.strip() != "vb2":
                    referenced.update(right.split())

    safe_sections = safe_call_sections(commands, _TEXTURE_HELPERS)
    hashes, position_blends = {}, {}
    for name, native_hash, history in candidates:
        if name not in safe_sections:
            continue
        blends = {item["resource"] for item in history if item["slot"] == 2}
        positions = {item["resource"] for item in history if item["slot"] == 0 and item["resource"]}
        if None in blends:
            continue
        for blend in blends:
            resource = resources.get_ci(blend)
            blend_file = normalize_skinning_source_file(resource.get("filename"))
            types = [text for _, text in commands.get(blend.casefold(), ()) if text.startswith("type = ")]
            key = blend.casefold()
            # Any Blend layout the stream's shader reads (32-byte float/u32, 8/16-byte
            # packed or 4-byte rigid) indexes the same native palette.
            if not blend_file or types not in ([], ["type = buffer"]) or key in written or key in referenced:
                continue
            hashes.setdefault(blend_file, set()).add(native_hash)
            if resource.get("stride") != 32:
                continue  # Only stock 32-byte Blends may be inferred from a Position file alone.
            for position in positions:
                vertex = resolve_vertex_info(position)
                position_file = normalize_skinning_source_file(vertex.get("filename"))
                if position_file and vertex.get("stride") == 40:
                    position_blends.setdefault(position_file, set()).add(blend_file)
    return {file: tuple(sorted(values)) for file, values in hashes.items()}, position_blends
