"""Uncaptured Genshin Impact props skinned to the verified rig, shown only during clip playback.

A prop takes the ZZZ attachment proof (a mesh in the verified Avatar's bundle that no
captured component came from, whose inverse binds prove a placement against that Avatar
and whose positive skin weight lies only on bones no captured palette uses). Genshin
clips carry no visibility curves, so the timing evidence is the game's own prefabs:
every renderer that draws the prop under a verified Avatar copy must be enabled with its
GameObject chain active. A prop any such prefab hides waits for gameplay code (skill
objects, burst forms) and is skipped rather than shown in every clip. Names never decide.
Textures follow ZZZ's byte-exact slot learning; renderers route through these explicit
pointers, and captured roles come from the GIMI Asset's own resolved texture files.
"""

import hashlib
from pathlib import Path
import zlib

from app.assets.loader.hash_asset import load_hash_asset
from . import zzz_index, zzz_prop_textures, zzz_props
from .runtime import ExtractionCancelled, check_cancelled
from .zzz_morphs import _references

MAX_RENDERERS = 64
MAX_TEXTURE_BYTES = 512 * 1024 * 1024


def routes(index, sources, avatar):
    """Mesh identity -> renderer rows that draw exactly that mesh under an Avatar of ``sources``
    (row identities), on a path under ``avatar``."""
    lookup = {}
    for row in index:
        if row["type"] in ("Mesh", "Avatar"):
            lookup.setdefault((row["type"], row["cab"].casefold(), row["path_id"]), []).append(row)
    paths = {bone["hash"] for bone in avatar["bones"]}
    found = {}
    for row in index:
        renderer = row.get("renderer") if row["type"] == "SkinnedMeshRenderer" else None
        if not isinstance(renderer, dict) or not isinstance(renderer.get("path"), str):
            continue
        if zlib.crc32(renderer["path"].encode("utf-8")) not in paths:
            continue
        avatars = _references(lookup, row, renderer.get("avatar"), "Avatar")
        meshes = _references(lookup, row, renderer.get("mesh"), "Mesh")
        if avatars and avatars <= sources and len(meshes) == 1:
            found.setdefault(next(iter(meshes)), []).append(row)
    return found


def admission(client, routed, cancel):
    """``admit`` callback: a prop needs every routed prefab to draw it by default."""

    def admit(row, _mesh):
        renderers = sorted(routed.get(zzz_index.identity(row), ()), key=zzz_index.identity)
        if not renderers:
            return "No prefab under the verified Avatar draws the prop."
        if len(renderers) > MAX_RENDERERS:
            return "Too many prefabs draw the prop to verify its visibility."
        for renderer in renderers:
            check_cancelled(cancel)
            try:
                visible = client.read(renderer).get("visible")
            except ExtractionCancelled:
                raise
            except (ValueError, KeyError, TypeError, AttributeError):
                visible = None
            if visible is False:
                return "A prefab under the verified Avatar hides the prop until gameplay code shows it."
            if visible is not True:
                return "The prop's prefab visibility could not be proven."
        return None

    return admit


def captured_payloads(root, record):
    """``payloads(asset, part)`` for GIMI: digests of a captured part's resolved role textures."""
    roles = set(zzz_prop_textures.CAPTURED_ROLES.values())
    files = None

    def payloads(_asset, captured):
        nonlocal files
        if files is None:
            # Loaded once, only when a prop needs textures; a load failure leaves props untextured.
            files = {(part.geometry_hash, part.metadata_entry, part.component_ordinal):
                     {role: texture.path for role, texture in part.textures.items() if role in roles}
                     for part in load_hash_asset("GIMI", root, record).parts}
        key = (captured.get("geometry_hash"), captured.get("metadata_entry"), captured.get("component_ordinal"))
        result = {}
        for role, file in sorted(files.get(key, {}).items()):
            try:
                path = Path(file)
                if path.stat().st_size > MAX_TEXTURE_BYTES:
                    continue
                payload = zzz_prop_textures.dds_payload(path.read_bytes())
            except OSError:
                continue
            if payload is not None:
                # A payload two roles share proves neither.
                digest = hashlib.sha256(payload).hexdigest()
                result[digest] = role if result.get(digest, role) == role else None
        return {digest: role for digest, role in result.items() if role}

    return payloads


def extract(client, chosen, rig, index, library, cancel, mesh_hint, placement_tolerance, captured, routed, payloads):
    """Write verified, default-visible props under ``library/Props``; returns (entries, skipped).

    ``routed`` is :func:`routes` for the verified Avatar copies and ``payloads`` :func:`captured_payloads`.
    """
    textures = dict(route=lambda _chosen, _mesh, source: sorted(routed.get(source, ()), key=zzz_index.identity),
                    payloads=payloads)
    return zzz_props.extract(client, chosen, rig, index, library, cancel, mesh_hint, captured, None,
                             placement_tolerance=placement_tolerance,
                             admit=admission(client, routed, cancel), textures=textures)
