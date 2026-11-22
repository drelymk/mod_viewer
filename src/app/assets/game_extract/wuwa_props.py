"""Wuthering Waves props shown during clip playback.

Captures never draw them. Only props with evidence of both attachment and timing are
exported: blueprint construction-script components attached to a character socket whose
own animation pairs with a clip (see wuwa_blueprint_props), and animated meshes a clip's
own effect notify spawns on a socket for its window (see wuwa_effect_props). The
character's regular weapon is not exported: the game attaches it to battle sockets, but
nothing extraction reads says when it is drawn, so showing it would be a guess (always
on hand).

This module holds the shared parts: model table rows, prop geometry, the Writer, and
textures from quality-tier archives. Which material parameter feeds which viewer role
is learned from byte-identical top mips on captured body parts, never assumed from
parameter names.
"""

import hashlib
import json
from pathlib import Path
import re
import shutil

import numpy as np

from app.assets import paths
from app.assets.animations import (MAX_PROP_INDICES, MAX_PROP_TRACK_BYTES, MAX_PROP_VERTICES, MAX_PROPS, PROP_LAYOUT,
                                   pack_prop)
from app.assets.loader.wwmi import _metadata_paths
from . import unity_dds
from .native import read_limited, read_mesh
from .pipeline import document, package_name, rest_worlds
from .runtime import ExtractionCancelled, check_cancelled

MODEL_CONFIG = "Client/Content/Aki/Data/Entity/CDT_ModelConfig"
CHARACTER_ROOT = "Client/Content/Aki/Character/"
# Captured texture labels -> viewer texture roles extraction can prove byte-exactly.
CAPTURED_ROLES = {"Diffuse": "diffuse", "LightMap": "light_map", "MaterialMap": "material_map"}
TIERS = ("HD", "UHD")
# Unreal pixel format -> (DXGI unorm, DXGI sRGB, bytes per 4x4 block or None for 4 bytes per pixel).
FORMATS = {
    "PF_DXT1": (71, 72, 8), "PF_DXT5": (77, 78, 16), "PF_BC4": (80, 80, 8), "PF_BC5": (83, 83, 16),
    "PF_BC6H": (95, 95, 16), "PF_BC7": (98, 99, 16), "PF_B8G8R8A8": (87, 91, None), "PF_R8G8B8A8": (28, 29, None),
}
MAX_TEXTURE_READS = 96
MAX_TEXTURE_BYTES = 96 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
_SUFFIX = re.compile(r"_\d+_[0-9A-F]{32}$")


def model_rows(client):
    """Model id -> mesh and blueprint packages from the game's model table."""
    file = client.export("json", MODEL_CONFIG, client.work / "model-config")[0]
    exports = document(file)
    tables = [item for item in exports if isinstance(item, dict) and isinstance(item.get("Rows"), dict)] \
        if isinstance(exports, list) else []
    if len(tables) != 1:
        raise ValueError("The game model configuration is unavailable.")
    rows = {}
    for key, row in tables[0]["Rows"].items():
        if not isinstance(key, str) or not key.isdigit() or not isinstance(row, dict):
            continue
        fields = {}
        for name, value in row.items():
            fields.setdefault(_SUFFIX.sub("", name), []).append(value)

        def one(name):
            values = fields.get(name, [])
            return values[0] if len(values) == 1 else None

        mesh, blueprint = one("网格体"), one("蓝图")
        rows[int(key)] = dict(
            mesh=package_name(mesh.get("AssetPathName")) if isinstance(mesh, dict) else "",
            blueprint=package_name(blueprint.get("AssetPathName")) if isinstance(blueprint, dict) else "")
    return rows


def _skin(mesh, used):
    """Per-vertex u16x4 joints and normalized f32x4 weights from the mesh's own skeleton."""
    joints, weights = np.zeros((len(used), 4), dtype=np.int64), np.zeros((len(used), 4))
    for row, vertex in enumerate(used):
        influences = sorted(mesh["weights"][vertex], key=lambda pair: -pair[1])[:4]
        total = sum(weight for _bone, weight in influences)
        if not total:
            raise ValueError("The prop has unweighted vertices.")
        for slot, (bone, weight) in enumerate(influences):
            joints[row, slot], weights[row, slot] = bone, weight / total
    return joints, weights


def _geometry(mesh, first, count, world):
    """``PROP_LAYOUT`` bytes of one mesh section placed at a socket's rest ``world``.

    The section keeps the mesh's own skin (joints index the mesh's own skeleton).
    """
    indices = np.asarray(mesh["indices"][first:first + count], dtype=np.int64)
    if not 0 < indices.size <= MAX_PROP_INDICES or indices.size % 3:
        raise ValueError("Unsupported prop geometry.")
    used, triangles = np.unique(indices, return_inverse=True)
    positions = np.asarray(mesh["positions"], dtype=float).reshape(-1, 3)
    normals = np.asarray(mesh.get("normals") or [], dtype=float)
    texcoords = np.asarray(mesh["uv"][0] if mesh.get("uv") else [], dtype=float).copy()
    if normals.shape != positions.shape or texcoords.shape != (len(positions), 2):
        raise ValueError("The prop has no usable normals or UVs.")
    if not 0 < len(used) <= MAX_PROP_VERTICES:
        raise ValueError("Unsupported prop geometry.")
    positions, normals, texcoords = positions[used], normals[used], texcoords[used]
    # The viewer draws every mesh with flipped V, captured parts included.
    texcoords[:, 1] = 1 - texcoords[:, 1]
    linear = world[:3, :3]
    determinant = np.linalg.det(linear)
    if not abs(determinant) > 1e-12:
        raise ValueError("The prop socket transform is degenerate.")
    placed = positions @ linear.T + world[:3, 3]
    turned = normals @ np.linalg.inv(linear)
    lengths = np.linalg.norm(turned, axis=1)
    turned = np.divide(turned, lengths[:, None], out=np.zeros_like(turned), where=lengths[:, None] > 1e-12)
    triangles = triangles.reshape(-1, 3)
    # Captured parts keep native triangle order; a mirrored socket reverses the winding.
    if determinant < 0:
        triangles = triangles[:, ::-1]
    if not np.isfinite(placed).all() or not np.isfinite(turned).all() or not np.isfinite(texcoords).all():
        raise ValueError("Unsupported prop geometry.")
    joints, weights = _skin(mesh, used)
    return pack_prop(placed, turned, texcoords, joints, weights, triangles), len(used), int(indices.size)


class _Textures:
    """Bounded, cached tier texture reads keeping each texture's stored top mip."""

    def __init__(self, client, cancel):
        self.client, self.cancel, self.cache, self.reads = client, cancel, {}, 0

    def read(self, tier, package):
        key = (tier, package)
        if key not in self.cache:
            check_cancelled(self.cancel)
            if self.reads >= MAX_TEXTURE_READS:
                raise ValueError("The prop materials need too many texture reads.")
            self.reads += 1
            value = None
            try:
                file, texture = self.client.texture(package, self.client.work / "textures", tier)
            except ExtractionCancelled:
                raise
            except (ValueError, KeyError, TypeError, OSError):
                pass
            else:
                try:
                    value = {**texture, "data": read_limited(file, MAX_TEXTURE_BYTES, "Native texture")}
                except ValueError:
                    value = None
                finally:
                    Path(file).unlink(missing_ok=True)
            self.cache[key] = value
        return self.cache[key]


def _materials(exports):
    """Section first index -> material package of one exported skeletal mesh document."""
    meshes = [item for item in exports if isinstance(item, dict) and item.get("Type") == "SkeletalMesh"] \
        if isinstance(exports, list) else []
    if len(meshes) != 1:
        return {}
    mesh = meshes[0]
    properties = mesh.get("Properties") if isinstance(mesh.get("Properties"), dict) else {}
    materials = mesh.get("SkeletalMaterials") or properties.get("SkeletalMaterials") or []
    lods = mesh.get("LODModels")
    sections = lods[0].get("Sections") if isinstance(lods, list) and lods and isinstance(lods[0], dict) else None
    result = {}
    for section in sections if isinstance(sections, list) else []:
        index = section.get("MaterialIndex") if isinstance(section, dict) else None
        first = section.get("BaseIndex") if isinstance(section, dict) else None
        if type(index) is not int or type(first) is not int or not 0 <= index < len(materials):
            continue
        material = materials[index].get("Material") if isinstance(materials[index], dict) else None
        package = package_name(material.get("ObjectPath")) if isinstance(material, dict) else ""
        if package:
            result[first] = package
    return result


def _parameters(client, package, cache):
    """Texture parameter name -> character texture package of one material instance."""
    if package not in cache:
        found = {}
        try:
            exports = document(client.export("json", package, client.work / "materials")[0], 16 * 1024 * 1024)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError, IndexError, OSError):
            exports = []
        for item in exports if isinstance(exports, list) else []:
            properties = item.get("Properties") if isinstance(item, dict) else None
            values = properties.get("TextureParameterValues") if isinstance(properties, dict) else None
            for value in values if isinstance(values, list) else []:
                info = value.get("ParameterInfo") if isinstance(value, dict) else None
                texture = value.get("ParameterValue") if isinstance(value, dict) else None
                name = info.get("Name") if isinstance(info, dict) else None
                texture = package_name(texture.get("ObjectPath")) if isinstance(texture, dict) else ""
                if isinstance(name, str) and texture.startswith(CHARACTER_ROOT):
                    found.setdefault(name, set()).add(texture)
        # A parameter set twice to different textures is never guessed between.
        cache[package] = {name: next(iter(values)) for name, values in found.items() if len(values) == 1}
    return cache[package]


def _captured_payloads(root, record, part):
    """Top-mip bytes digest -> viewer role for one captured WWMI component's DDS textures."""
    for relative in _metadata_paths(record):
        file = paths.safe_asset_path(str(root), relative)
        if not file:
            continue
        try:
            metadata = json.loads(read_limited(file, 4 * 1024 * 1024, "Captured metadata").decode("utf-8-sig"))
            if metadata.get("vb0_hash") != part["geometry_hash"]:
                continue
            groups = metadata["components"][part["component_ordinal"]].get("texture_hashes") or []
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            continue
        result = {}
        for group in groups if isinstance(groups, list) else []:
            for item in group if isinstance(group, list) else []:
                if (not isinstance(item, list) or len(item) != 4 or item[0] not in CAPTURED_ROLES
                        or item[1] != ".dds" or not isinstance(item[3], str)):
                    continue
                try:
                    raw = read_limited(Path(file).parent / Path(item[3]).name, MAX_TEXTURE_BYTES + 148, "Captured texture")
                except (OSError, ValueError):
                    continue
                if raw[:4] == b"DDS " and len(raw) >= 148:
                    payload = raw[148:] if raw[84:88] == b"DX10" else raw[128:]
                    result[hashlib.sha256(payload).hexdigest()] = CAPTURED_ROLES[item[0]]
        return result
    return {}


def _learn(reader, tier, parts):
    """Material parameter -> viewer role, proven byte-exactly on captured body parts."""
    votes = {}
    for payloads, parameters in parts:
        for name, package in sorted(parameters.items()):
            texture = reader.read(tier, package)
            role = payloads.get(hashlib.sha256(texture["data"]).hexdigest()) if texture else None
            if role:
                votes.setdefault(name, set()).add(role)
    slots = {name: next(iter(roles)) for name, roles in votes.items() if len(roles) == 1}
    # A role fed by two different parameters stays unproven.
    claimed = list(slots.values())
    return {name: role for name, role in slots.items() if claimed.count(role) == 1}


def _dds(texture, srgb):
    if texture["format"] not in FORMATS:
        raise ValueError("Unsupported texture format.")
    unorm, srgb_format, block = FORMATS[texture["format"]]
    width, height = texture["width"], texture["height"]
    size = ((width + 3) // 4) * ((height + 3) // 4) * block if block else width * height * 4
    if len(texture["data"]) != size:
        raise ValueError("The texture data is incomplete.")
    return unity_dds.wrap(width, height, srgb_format if srgb else unorm, texture["data"])


def _attach_textures(client, root, record, captured, palettes, body_exports, props, library, cancel):
    """Give each written (entry, material) its proven ``textures``, or a ``texture_error``."""
    reader, cache = _Textures(client, cancel), {}
    try:
        sections = _materials(body_exports)
        parts = []
        for number, palette in sorted(palettes.items()):
            payloads = _captured_payloads(root, record, captured[number])
            material = sections.get(palette.get("native_first_index"))
            if payloads and material:
                parts.append((payloads, _parameters(client, material, cache)))
        tier, slots = None, {}
        for tier in TIERS:
            slots = _learn(reader, tier, parts)
            if slots:
                break
        failure = None if slots else "No material parameter could be proven against the captured textures."
    except ValueError as error:
        tier, slots, failure = None, {}, str(error)
    for entry, material in props:
        try:
            if failure:
                raise ValueError(failure)
            if not material:
                raise ValueError("The prop material is unavailable.")
            parameters = _parameters(client, material, cache)
            textures = {}
            for name, role in sorted(slots.items()):
                texture = reader.read(tier, parameters[name]) if name in parameters else None
                if texture is None:
                    continue
                try:
                    data = _dds(texture, srgb=role == "diffuse")
                except ValueError:
                    continue
                digest = hashlib.sha256(data).hexdigest()
                file = f"Props/{digest[:32]}.dds"
                (library / file).write_bytes(data)
                textures[role] = dict(file=file, sha256=digest)
            if not textures:
                raise ValueError("The prop material has no proven textures.")
            entry["textures"] = textures
        except ValueError as error:
            entry["texture_error"] = str(error)


class Writer:
    """Content-addressed prop files under ``library/Props`` within the prop budgets.

    ``entries`` are the manifest props (plus report-only ``source``/``texture_error``),
    ``materials`` pairs each entry with its section's material for texturing.
    """

    def __init__(self, library):
        self.library, self.folder = library, library / "Props"
        if self.folder.exists():
            shutil.rmtree(self.folder)
        self.entries, self.materials, self.skipped, self.total, self.tracks = [], [], [], 0, 0

    def write(self, data, *, track=False):
        """Write one content-addressed file; returns its manifest path and digest."""
        if track:
            if self.tracks + len(data) > MAX_PROP_TRACK_BYTES:
                raise ValueError("The prop animation budget is exhausted.")
            self.tracks += len(data)
        digest = hashlib.sha256(data).hexdigest()
        self.folder.mkdir(exist_ok=True)
        file = f"Props/{digest[:32]}.bin"
        (self.library / file).write_bytes(data)
        return file, digest

    def add(self, package, parts, **extra):
        """Add each (material, (payload, vertices, indices)) section of one mesh as a prop."""
        name = package.rsplit("/", 1)[-1]
        for number, (material, (payload, vertices, indices)) in enumerate(parts):
            # A prop is its geometry plus how it moves; equal props are kept once.
            identity = hashlib.sha256(payload + json.dumps(extra, sort_keys=True).encode("utf-8")).hexdigest()
            if any(entry["id"] == f"prop-{identity[:12]}" for entry in self.entries):
                continue
            if len(self.entries) == MAX_PROPS or self.total + len(payload) > MAX_TOTAL_BYTES:
                self.skipped.append(dict(source=package, reason="The prop budget is exhausted."))
                return
            file, digest = self.write(payload)
            self.total += len(payload)
            label = name if len(parts) == 1 else f"{name} {number + 1}"
            entry = dict(id=f"prop-{identity[:12]}", name=label[:128], file=file, layout=PROP_LAYOUT,
                         sha256=digest, vertex_count=vertices, index_count=indices, **extra, source=package)
            self.entries.append(entry)
            self.materials.append((entry, material))


def mesh_parts(client, package, world):
    """(native mesh, [(material, geometry)]) of every material section of one prop mesh."""
    mesh = read_mesh(client.export("mesh", package, client.work / "props", "ueformat")[0], normals=True)
    exports = document(client.export("json", package, client.work / "prop-metadata")[0])
    materials = _materials(exports)
    # Each material section is drawn as its own prop with its own material.
    return mesh, exports, [(materials.get(first), _geometry(mesh, first, count, world))
                           for first, count in mesh["sections"] if count]


def extract(client, root, record, captured, palettes, mesh_package, body_exports, rig, clips, effects, library,
            cancel):
    """Write the character's blueprint and effect props under ``library/Props``.

    ``palettes`` maps proven captured component numbers of the verified mesh to their
    palettes; ``clips`` maps the Asset's own clip names to their (id, frame count, length)s;
    ``effects`` maps the Asset's own clip ids to (length, effect notify facts). Returns
    (entries, skipped). Only props with clip tracks are exported; the regular weapon is
    never exported (see the module docstring).
    """
    from . import wuwa_blueprint_props, wuwa_effect_props

    rows = model_rows(client)
    own = {number: row for number, row in rows.items() if row["mesh"] == mesh_package}
    if not own:
        raise ValueError("No game model configuration uses the verified mesh.")
    names = [bone["name"] for bone in rig["bones"]]
    worlds = rest_worlds(rig["bones"], affine=True)
    writer = Writer(library)
    # Effects come first: an animation a clip's effect plays is never a blueprint prop's.
    resolved = wuwa_effect_props.resolve(client, effects, cancel)
    wuwa_blueprint_props.extract(client, own, names, worlds, clips, writer, cancel,
                                 wuwa_effect_props.claimed(resolved[0]))
    wuwa_effect_props.extract(client, resolved, rig, library, writer, cancel)
    if writer.materials:
        _attach_textures(client, root, record, captured, palettes, body_exports, writer.materials, library, cancel)
    return writer.entries, writer.skipped
