"""Game textures for ZZZ props, with texture roles learned from the captured body.

Renderers route to a native mesh only through their complete ordered bone paths and an
Animator Avatar among the verified copies (the facial-routing proof). Which material
slot feeds which viewer role is learned, not assumed: a slot takes a role only when its
top mip is byte-identical to a captured texture of that role on a proven body part,
and no other slot or role competes. Prop textures then come from those slots of the
prop's own routed materials, which must agree on each texture's content.
"""

import hashlib
import json
from pathlib import Path
import zlib

from . import unity_dds, zzz_index
from .runtime import ExtractionCancelled, check_cancelled
from .zzz_morphs import _references

# Captured hash.json role names -> viewer texture roles extraction can prove byte-exactly.
CAPTURED_ROLES = {"Diffuse": "diffuse", "LightMap": "light_map", "MaterialMap": "material_map"}
MAX_TEXTURE_READS = 96
MAX_RENDERERS = 8
MAX_COPIES = 4
MAX_CONTEXT_READS = 256


class Reader:
    """Cached native reads for one extraction's props: routed renderers, renderer reads and
    textures (a texture keeps only its top mip, and differing copies resolve to None)."""

    def __init__(self, client, index, cancel, route=None):
        self.client, self.index, self.files, self.cancel = client, index, getattr(index, "files", {}), cancel
        # A game may route renderers itself: route(chosen, mesh, source identity) -> renderer rows.
        self.route = route
        self.cache, self.reads, self.routes, self.avatars = {}, 0, None, None
        self.routed_rows, self.renderer_reads = {}, {}

    def routed(self, chosen, mesh, source):
        """The renderer rows proven to draw ``mesh`` (the game's ``route`` or :func:`renderers`)."""
        if source not in self.routed_rows:
            check_cancelled(self.cancel)
            self.routed_rows[source] = self.route(chosen, mesh, source) if self.route else renderers(self, chosen, mesh)
        return self.routed_rows[source]

    def renderer(self, row):
        """One renderer row's native read, or None when it cannot be read."""
        key = zzz_index.identity(row)
        if key not in self.renderer_reads:
            check_cancelled(self.cancel)
            try:
                value = self.client.read(row)
            except ExtractionCancelled:
                raise
            except (ValueError, KeyError, TypeError):
                value = None
            self.renderer_reads[key] = value if isinstance(value, dict) else None
        return self.renderer_reads[key]

    def locate(self, owner, pointer):
        if not isinstance(pointer, dict) or not isinstance(pointer.get("cab"), str) or type(pointer.get("path_id")) is not int:
            return []
        if "file_ordinal" in pointer:
            return [dict(block=owner["block"], offset=pointer["offset"], cab=pointer["cab"],
                         file_ordinal=pointer["file_ordinal"], path_id=pointer["path_id"])]
        return [dict(block=block, offset=offset, cab=cab, file_ordinal=ordinal, path_id=pointer["path_id"])
                for block, offset, ordinal, cab in self.files.get(pointer["cab"].casefold(), [])]

    def read(self, locator):
        key = zzz_index.identity(locator)
        if key not in self.cache:
            check_cancelled(self.cancel)
            if self.reads >= MAX_TEXTURE_READS:
                raise ValueError("The prop materials need too many texture reads.")
            self.reads += 1
            try:
                value = self.client.read(locator)
            except ExtractionCancelled:
                raise
            except (ValueError, KeyError, TypeError):
                value = None
            if isinstance(value, dict) and isinstance(value.get("file"), str):
                file = Path(self.client.work) / Path(value["file"]).name
                try:
                    data = file.read_bytes()
                finally:
                    file.unlink(missing_ok=True)
                try:
                    size = unity_dds.mip0_size(value["width"], value["height"], value["format"])
                except (KeyError, TypeError):
                    size = len(data) + 1
                # The top mip is the one level compared with captures and written for the viewer.
                value = {**value, "data": data[:size]} if len(data) >= size else None
            self.cache[key] = value
        return self.cache[key]

    def _agreed(self, owner, pointer, comparable):
        copies = self.locate(owner, pointer)
        if not 1 <= len(copies) <= MAX_COPIES:
            return None, None
        values = [self.read(copy) for copy in copies]
        if any(value is None for value in values) or len({comparable(value) for value in values}) != 1:
            return None, None
        return copies[0], values[0]

    def material(self, owner, entry):
        """A routed renderer's material description and its locator (for its texture pointers)."""
        if "textures" in entry:
            # The native read already described this in-bundle material.
            return self.locate(owner, entry["pointer"])[0], entry
        locator, value = self._agreed(owner, entry.get("pointer"), lambda value: json.dumps(value.get("textures")))
        return (locator, value) if isinstance(value, dict) and isinstance(value.get("textures"), list) else (None, None)

    def texture(self, owner, pointer):
        return self._agreed(owner, pointer, lambda value: (value["format"], value["width"], value["height"], value["data"]))[1]


def _routing(reader):
    """One pass over the index: renderers keyed by their CRC32 bone paths, and Avatar rows."""
    if reader.routes is None:
        routes, avatars = {}, {}
        for row in reader.index:
            if row["type"] == "Avatar":
                avatars.setdefault(("Avatar", row["cab"].casefold(), row["path_id"]), []).append(row)
            renderer = row.get("renderer") if row["type"] == "SkinnedMeshRenderer" else None
            bones = renderer.get("bone_paths") if isinstance(renderer, dict) else None
            if (not isinstance(renderer.get("path") if renderer else None, str) or not isinstance(bones, list)
                    or not bones or any(not isinstance(bone, str) for bone in bones)):
                continue
            routes.setdefault(tuple(zlib.crc32(bone.encode("utf-8")) for bone in bones), []).append(row)
        reader.routes, reader.avatars = routes, avatars
    return reader.routes, reader.avatars


def renderers(reader, chosen, mesh):
    """Renderer rows proven to draw ``mesh`` under one of the verified Avatar copies."""
    hashes = mesh.get("bone_hashes")
    if not isinstance(hashes, list) or not hashes:
        return []
    routes, avatars = _routing(reader)
    paths = {bone["hash"] for bone in chosen["avatar"]["bones"]}
    found = []
    for row in routes.get(tuple(hashes), ()):
        renderer = row["renderer"]
        if zlib.crc32(renderer["path"].encode("utf-8")) not in paths:
            continue
        sources = _references(avatars, row, renderer.get("avatar"), "Avatar")
        if sources and sources <= chosen["avatar_sources"]:
            found.append(row)
    return sorted(found, key=zzz_index.identity)


def _named_segments(name, rows):
    """{renderer object name: 2 for the exact name, 1 for a legacy ``..._Model_<name>``} that ``name`` matches."""
    result = {}
    for row in rows:
        segment = row["renderer"]["path"].rsplit("/", 1)[-1]
        if segment and (name == segment or name.endswith("_Model_" + segment)):
            result[segment] = max(result.get(segment, 0), 2 if name == segment else 1)
    return result


def alternates(reader, chosen, candidates):
    """{name: reason} of the candidates to drop as another outfit's meshes.

    ``candidates`` holds proven props as (name, mesh, source identity), one per name. Renderers
    route by complete bone-path tuples, so candidates compete exactly when they route to the
    same renderers. A name only breaks that tie: for each renderer object name, the candidates
    matching it best (see ``_named_segments``: the exact name over a legacy ``..._Model_<name>``
    copy) stay, and a competitor matching none of its renderers best is dropped; a group whose
    renderers no candidate names is unchanged. A candidate no verified renderer routes to is
    never drawn by the captured outfit's prefabs (another outfit's piece with its own bone
    list), so it is dropped without consulting names.
    """
    groups, result = {}, {}
    for name, mesh, source in candidates:
        rows = reader.routed(chosen, mesh, source)
        if not rows:
            result[name] = "The captured outfit's renderers never draw the mesh."
            continue
        groups.setdefault(frozenset(zzz_index.identity(row) for row in rows), []).append((name, rows))
    for group in groups.values():
        named = {name: _named_segments(name, rows) for name, rows in group}
        best = {}
        for segments in named.values():
            for segment, strength in segments.items():
                best[segment] = max(best.get(segment, 0), strength)
        if len(group) < 2 or not best:
            continue
        for name, segments in named.items():
            if not any(strength == best[segment] for segment, strength in segments.items()):
                result[name] = ("Another copy of the mesh its renderer is named for is kept." if segments else
                                "Another outfit's mesh: the renderers it routes to are named for a different mesh.")
    return result


def contexts(reader, chosen, meshes):
    """{controller key: draws props} for the renderers that route to ``meshes``.

    ``meshes`` holds (mesh, source identity) pairs. At most ``MAX_CONTEXT_READS`` routed renderer
    rows are read; a read without an ``animator`` whose controller ``path_id`` is non-zero is
    ignored. The key is (controller CAB casefold, path_id), and a context draws props when any
    renderer read under it reports ``visible is True``.
    """
    rows = {}
    for mesh, source in meshes:
        for row in reader.routed(chosen, mesh, source):
            rows.setdefault(zzz_index.identity(row), row)
    result = {}
    for row in list(rows.values())[:MAX_CONTEXT_READS]:
        value = reader.renderer(row) or {}
        animator = value.get("animator")
        controller = animator.get("controller") if isinstance(animator, dict) else None
        if (not isinstance(controller, dict) or not isinstance(controller.get("cab"), str)
                or type(controller.get("path_id")) is not int or controller["path_id"] == 0):
            continue
        key = (controller["cab"].casefold(), controller["path_id"])
        result[key] = result.get(key, False) or value.get("visible") is True
    return result


def _materials(reader, chosen, mesh, source):
    """Per routed renderer, the (locator, description) of each of ``mesh``'s section materials."""
    sections = len(mesh.get("sections") or [])
    result = []
    for row in reader.routed(chosen, mesh, source)[:MAX_RENDERERS]:
        entries = (reader.renderer(row) or {}).get("materials")
        if not isinstance(entries, list) or len(entries) < sections or not all(isinstance(entry, dict) for entry in entries):
            continue
        described = [reader.material(row, entry) for entry in entries[:sections]]
        if all(value is not None for _locator, value in described):
            result.append(described)
    return result


def _captured_payloads(asset, part):
    """Top-mip bytes digest -> viewer role for one captured part's DDS textures."""
    try:
        data = json.loads((asset / part["metadata_file"]).read_text(encoding="utf-8-sig"))
        components = data["components"] if isinstance(data, dict) else data
        groups = components[part["metadata_entry"]].get("texture_hashes") or []
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {}
    folder = (asset / part["metadata_file"]).parent
    result = {}
    for group in groups if isinstance(groups, list) else []:
        for item in group if isinstance(group, list) else []:
            if not isinstance(item, list) or len(item) != 4 or item[0] not in CAPTURED_ROLES or item[1] != ".dds":
                continue
            try:
                raw = (folder / Path(str(item[3])).name).read_bytes()
            except OSError:
                continue
            payload = unity_dds.top_mip(raw)
            if payload is not None:
                result[hashlib.sha256(payload).hexdigest()] = CAPTURED_ROLES[item[0]]
    return result


def _learn_slots(reader, chosen, captured, asset, payloads=_captured_payloads):
    """Material slot -> viewer role, proven byte-exactly on captured body parts."""
    votes = {}
    for number, palette in sorted(chosen["matched"].items()):
        proven = {} if "attachment" in palette else payloads(asset, captured[number])
        if not proven:
            continue
        native = chosen["native"][number][0]
        for locator, material in (item for described in _materials(reader, chosen, native["mesh"],
                                                                     zzz_index.identity(native["row"])) for item in described):
            for slot in material["textures"]:
                texture = reader.texture(locator, slot.get("texture")) if isinstance(slot, dict) else None
                role = proven.get(hashlib.sha256(texture["data"]).hexdigest()) if texture else None
                if role and isinstance(slot.get("name"), str):
                    votes.setdefault(slot["name"], set()).add(role)
    slots = {slot: next(iter(roles)) for slot, roles in votes.items() if len(roles) == 1}
    # A role fed by two different slots stays unproven.
    claimed = list(slots.values())
    return {slot: role for slot, role in slots.items() if claimed.count(role) == 1}


def _prop_textures(reader, chosen, mesh, source, slots, library, section):
    """Write the prop's learned-role textures as DDS files; returns ({role: entry}, reason).

    A prop split from one ``section`` of a multi-material mesh uses that section's material.
    """
    if not slots:
        return {}, "No material slot could be proven against the captured textures."
    # The viewer draws a prop with one material; an unsplit multi-material mesh has several.
    if section is None and len(mesh.get("sections") or []) != 1:
        return {}, "The prop uses several materials."
    materials = [described[section or 0] for described in _materials(reader, chosen, mesh, source)]
    if not materials:
        return {}, "No renderer material could be proven for the prop."
    result = {}
    for slot, role in sorted(slots.items()):
        textures = []
        for locator, material in materials:
            entry = next((item for item in material["textures"] if isinstance(item, dict) and item.get("name") == slot), None)
            textures.append(reader.texture(locator, entry.get("texture")) if entry else None)
        if any(texture is None for texture in textures) or len({
                (texture["format"], texture["width"], texture["height"], texture["data"]) for texture in textures}) != 1:
            continue
        texture = textures[0]
        try:
            data = unity_dds.encode(texture["width"], texture["height"], texture["format"], texture["data"],
                                    srgb=role == "diffuse")
        except ValueError:
            continue
        digest = hashlib.sha256(data).hexdigest()
        file = f"Props/{digest[:32]}.dds"
        (library / file).write_bytes(data)
        result[role] = dict(file=file, sha256=digest)
    return result, None if result else "The prop materials have no proven textures."


def attach(entries, meshes, client, chosen, index, library, cancel, captured, asset, *, route=None,
           payloads=_captured_payloads, reader=None):
    """Give each written prop entry its proven ``textures``, or a ``texture_error`` saying why not.

    ``payloads(asset, part)`` maps a captured part's texture digests to viewer roles; ``reader``
    may share an extraction's routed renderers and reads.
    """
    reader = reader or Reader(client, index, cancel, route)
    try:
        slots = _learn_slots(reader, chosen, captured, asset, payloads)
    except ValueError as error:
        slots, failure = None, str(error)
    for entry, mesh in zip(entries, meshes):
        try:
            textures, reason = (_prop_textures(reader, chosen, mesh, entry["source"], slots, library, entry.get("section"))
                                if slots is not None else ({}, failure))
        except ValueError as error:
            textures, reason = {}, str(error)
        if textures:
            entry["textures"] = textures
        else:
            entry["texture_error"] = reason
