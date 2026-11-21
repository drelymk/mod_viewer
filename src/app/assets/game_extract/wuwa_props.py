"""Wuthering Waves weapons shown during clip playback.

Captures never draw the weapon: the game attaches separate weapon models to battle
sockets of the character's skeleton. The game's model configuration
(``CDT_ModelConfig``) names every model's mesh, battle sockets and a developer
description. A weapon model whose description marks it as a character's signature
weapon (``<name>专武``) belongs to the character whose model rows carry exactly that
name; skin and deprecated variants never qualify. Without a signature weapon, a
five-star weapon of the character's weapon type is picked deterministically from the
verified mesh. The type comes from the game's role configuration (``db_role``): roles
whose model is one of the verified mesh's model rows, with an initial weapon whose id
encodes the role's own weapon type.

A weapon's models come in attachment order from the game's weapon table
(``db_weapon``): a scabbard, the other hand's gauntlet, or a second copy of one pistol.
Each goes to the battle socket of the same index on the verified mesh's own model rows,
which must agree. Each material section of a weapon
mesh becomes one prop, placed rigidly at its socket's rest transform and skinned fully
to that socket bone, so clips carry it with the hand. Textures come from the separate
quality-tier archives; which material parameter feeds which viewer role is learned from
byte-identical top mips on captured body parts, never assumed from parameter names.
"""

import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import struct

import numpy as np

from app.assets import paths
from app.assets.animations import MAX_PROP_INDICES, MAX_PROP_VERTICES, MAX_PROPS, PROP_LAYOUT, pack_prop
from app.assets.loader.wwmi import _metadata_paths
from . import unity_dds
from .native import read_limited, read_mesh
from .pipeline import document, package_name, rest_worlds
from .runtime import ExtractionCancelled, check_cancelled

MODEL_CONFIG = "Client/Content/Aki/Data/Entity/CDT_ModelConfig"
WEAPON_ROOT = "Client/Content/Aki/Character/Weapon/"
CHARACTER_ROOT = "Client/Content/Aki/Character/"
# Main weapon model ids (21 0 <type> ....); the paired model of a weapon is at id + SECONDARY.
MAIN_IDS = range(21000000, 21100000)
SECONDARY = 100000
# Developer description tags: signature weapon, and skin/deprecated variants.
SIGNATURE = "专武"
EXCLUDED = ("皮肤", "弃用", "废弃")
# roleinfo FlatBuffer fields: model id, initial weapon item (= model) id, weapon type.
ROLE_MODEL, ROLE_WEAPON, ROLE_TYPE = 23, 42, 43
# weaponconf FlatBuffer field: the weapon's models in attachment order.
WEAPON_MODELS = 7
MAX_ROLES = 4096
MAX_WEAPON_MODELS = 2
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


def _folder(mesh):
    """The weapon type folder of a weapon mesh package."""
    return mesh[len(WEAPON_ROOT):].split("/", 1)[0] if mesh.startswith(WEAPON_ROOT) else ""


def model_rows(client):
    """Model id -> mesh, description and battle sockets from the game's model table."""
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

        mesh, description, sockets = one("网格体"), one("描述"), one("BattleSockets")
        rows[int(key)] = dict(
            mesh=package_name(mesh.get("AssetPathName")) if isinstance(mesh, dict) else "",
            description=description if isinstance(description, str) else "",
            sockets=tuple(sockets) if isinstance(sockets, list) and all(isinstance(s, str) for s in sockets) else ())
    return rows


def _owners(description):
    """Character names a signature description names (``…-<name>[/<name>]专武…``)."""
    head = description.split(SIGNATURE, 1)[0]
    return {name for name in re.split(r"[-－]", head)[-1].split("/") if name}


def _flat_field(blob, number):
    """Byte position of a FlatBuffer root table field, or None when absent.

    Out-of-range reads raise ``struct.error``.
    """
    table = struct.unpack_from("<I", blob, 0)[0]
    vtable = table - struct.unpack_from("<i", blob, table)[0]
    size = struct.unpack_from("<H", blob, vtable)[0]
    offset = struct.unpack_from("<H", blob, vtable + 4 + 2 * number)[0] if 4 + 2 * number < size else 0
    return table + offset if offset else None


def flat_ints(blob, numbers):
    """Int32 fields of a FlatBuffer root table by field number; None when malformed."""
    try:
        positions = {number: _flat_field(blob, number) for number in numbers}
        return {number: None if position is None else struct.unpack_from("<i", blob, position)[0]
                for number, position in positions.items()}
    except (struct.error, TypeError):
        return None


def flat_vector(blob, number, limit=16):
    """An int32 vector field of a FlatBuffer root table; None when absent or malformed."""
    try:
        position = _flat_field(blob, number)
        if position is None:
            return None
        start = position + struct.unpack_from("<I", blob, position)[0]
        count = struct.unpack_from("<I", blob, start)[0]
        return list(struct.unpack_from(f"<{count}i", blob, start + 4)) if count <= limit else None
    except (struct.error, TypeError):
        return None


def _rows(client, name, query, parameters=()):
    """Rows of one read-only query against a game configuration database."""
    file = client.config(name, client.work / "config")
    try:
        connection = sqlite3.connect(f"{Path(file).resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            return connection.execute(query, parameters).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise ValueError("The game configuration is unavailable.") from error


def weapon_models(client, item):
    """The weapon item's models in attachment order from ``db_weapon``; None when unreadable."""
    try:
        rows = _rows(client, "db_weapon", "SELECT BinData FROM weaponconf WHERE ItemId = ?", (item,))
    except ExtractionCancelled:
        raise
    except (ValueError, OSError):
        return None
    models = flat_vector(rows[0][0], WEAPON_MODELS) if len(rows) == 1 and isinstance(rows[0][0], bytes) else None
    # The item's own model is always one of its models.
    return models if models and item in models and len(models) <= MAX_WEAPON_MODELS else None


def role_weapons(client, models):
    """(initial weapon id, weapon type) of every role whose model is one of ``models``.

    A role counts only when its initial weapon id encodes its own weapon type, so a
    changed configuration layout yields nothing rather than a wrong type.
    """
    blobs = _rows(client, "db_role", "SELECT BinData FROM roleinfo LIMIT ?", (MAX_ROLES,))
    found = set()
    for (blob,) in blobs:
        fields = flat_ints(blob, (ROLE_MODEL, ROLE_WEAPON, ROLE_TYPE)) if isinstance(blob, bytes) else None
        if (fields and fields[ROLE_MODEL] in models and fields[ROLE_WEAPON] in MAIN_IDS
                and fields[ROLE_TYPE] is not None and 0 < fields[ROLE_TYPE] < 10
                and str(fields[ROLE_WEAPON])[3] == str(fields[ROLE_TYPE])):
            found.add((fields[ROLE_WEAPON], fields[ROLE_TYPE]))
    return found


def choose(client, rows, mesh_package, character_root):
    """(weapon model ids, battle socket names, "signature" or "fallback") for the verified mesh."""
    own = {number: row for number, row in rows.items() if row["mesh"] == mesh_package}
    if not own:
        raise ValueError("No game model configuration uses the verified mesh.")
    names = {row["description"] for row in rows.values()
             if row["description"] and row["mesh"].startswith(character_root + "/")}
    weapons = {number: row for number, row in rows.items() if _folder(row["mesh"])}

    def usable(number):
        return number in MAIN_IDS and not any(word in weapons[number]["description"] for word in EXCLUDED)

    signature = sorted(number for number, row in weapons.items() if usable(number)
                       and SIGNATURE in row["description"] and _owners(row["description"]) & names)
    choice = "signature"
    if signature:
        main = signature[0]
    else:
        # No signature weapon: a deterministic five-star weapon of the character's type.
        choice = "fallback"
        folders = {_folder(weapons[weapon]["mesh"]) for weapon, _type in role_weapons(client, set(own))
                   if weapon in weapons}
        if len(folders) != 1:
            raise ValueError("The character's weapon type could not be determined.")
        folder = next(iter(folders))
        candidates = sorted(number for number, row in weapons.items() if usable(number)
                            and _folder(row["mesh"]) == folder and row["mesh"].rsplit("/", 1)[-1].startswith("R5"))
        if not candidates:
            raise ValueError("No weapon of the character's type is available.")
        main = candidates[int(hashlib.sha256(mesh_package.encode("utf-8")).hexdigest(), 16) % len(candidates)]
    folder = _folder(weapons[main]["mesh"])
    models = weapon_models(client, main)
    if not models or any(model not in weapons or _folder(weapons[model]["mesh"]) != folder for model in models):
        # Without the weapon table, the paired model of a weapon sits at id + SECONDARY.
        pair = weapons.get(main + SECONDARY)
        models = [main] + ([main + SECONDARY] if pair and _folder(pair["mesh"]) == folder else [])
    socket_lists = {row["sockets"] for row in own.values() if row["sockets"]}
    for count in sorted({len(models), 1}, reverse=True):
        prefixes = {sockets[:count] for sockets in socket_lists if len(sockets) >= count}
        if len(prefixes) > 1:
            break
        if prefixes:
            return models[:count], list(next(iter(prefixes))), choice
    raise ValueError("The character's weapon sockets are missing or ambiguous.")


def _geometry(mesh, first, count, socket, world):
    """``PROP_LAYOUT`` bytes of one mesh section placed rigidly at the socket's rest ``world``."""
    indices = np.asarray(mesh["indices"][first:first + count], dtype=np.int64)
    if not 0 < indices.size <= MAX_PROP_INDICES or indices.size % 3:
        raise ValueError("Unsupported weapon geometry.")
    used, triangles = np.unique(indices, return_inverse=True)
    positions = np.asarray(mesh["positions"], dtype=float).reshape(-1, 3)
    normals = np.asarray(mesh.get("normals") or [], dtype=float)
    texcoords = np.asarray(mesh["uv"][0] if mesh.get("uv") else [], dtype=float)
    if normals.shape != positions.shape or texcoords.shape != (len(positions), 2):
        raise ValueError("The weapon has no usable normals or UVs.")
    if not 0 < len(used) <= MAX_PROP_VERTICES:
        raise ValueError("Unsupported weapon geometry.")
    positions, normals, texcoords = positions[used], normals[used], texcoords[used]
    linear = world[:3, :3]
    determinant = np.linalg.det(linear)
    if not abs(determinant) > 1e-12:
        raise ValueError("The weapon socket transform is degenerate.")
    placed = positions @ linear.T + world[:3, 3]
    turned = normals @ np.linalg.inv(linear)
    lengths = np.linalg.norm(turned, axis=1)
    turned = np.divide(turned, lengths[:, None], out=np.zeros_like(turned), where=lengths[:, None] > 1e-12)
    triangles = triangles.reshape(-1, 3)
    # Captured parts keep native triangle order; a mirrored socket reverses the winding.
    if determinant < 0:
        triangles = triangles[:, ::-1]
    if not np.isfinite(placed).all() or not np.isfinite(turned).all() or not np.isfinite(texcoords).all():
        raise ValueError("Unsupported weapon geometry.")
    joints = np.zeros((len(used), 4), dtype=np.int64)
    joints[:, 0] = socket
    weights = np.zeros((len(used), 4))
    weights[:, 0] = 1
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
                raise ValueError("The weapon materials need too many texture reads.")
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
    """Give each written (entry, material) its proven ``textures``, or a ``texture_error``; returns the tier."""
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
        for candidate in TIERS:
            slots = _learn(reader, candidate, parts)
            if slots:
                tier = candidate
                break
        failure = None if slots else "No material parameter could be proven against the captured textures."
    except ValueError as error:
        tier, slots, failure = None, {}, str(error)
    for entry, material in props:
        try:
            if failure:
                raise ValueError(failure)
            if not material:
                raise ValueError("The weapon material is unavailable.")
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
                raise ValueError("The weapon material has no proven textures.")
            entry["textures"] = textures
        except ValueError as error:
            entry["texture_error"] = str(error)
    return tier


def extract(client, root, record, captured, palettes, mesh_package, body_exports, rig, character_root, library, cancel):
    """Write the character's weapon props under ``library/Props``.

    ``palettes`` maps proven captured component numbers of the verified mesh to their
    palettes. Returns (entries, skipped, weapon) where ``weapon`` describes the choice.
    """
    rows = model_rows(client)
    models, sockets, choice = choose(client, rows, mesh_package, character_root)
    names = [bone["name"] for bone in rig["bones"]]
    worlds = rest_worlds(rig["bones"], affine=True)
    folder = library / "Props"
    if folder.exists():
        shutil.rmtree(folder)
    entries, props, skipped, total = [], [], [], 0
    for model, socket in zip(models, sockets):
        check_cancelled(cancel)
        package = rows[model]["mesh"]
        try:
            if names.count(socket) != 1:
                raise ValueError("The weapon socket is not a unique bone of the verified rig.")
            mesh = read_mesh(client.export("mesh", package, client.work / "weapons", "ueformat")[0], normals=True)
            materials = _materials(document(client.export("json", package, client.work / "weapon-metadata")[0]))
            sections = [(first, count) for first, count in mesh["sections"] if count]
            # Each material section is drawn as its own prop with its own material.
            parts = [(first, materials.get(first), _geometry(mesh, first, count, names.index(socket), worlds[names.index(socket)]))
                     for first, count in sections]
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            skipped.append(dict(source=package, reason=str(error) if isinstance(error, ValueError)
                                else "The weapon mesh could not be read."))
            continue
        name = package.rsplit("/", 1)[-1]
        for number, (_first, material, (payload, vertices, indices)) in enumerate(parts):
            digest = hashlib.sha256(payload).hexdigest()
            if len(entries) == MAX_PROPS or total + len(payload) > MAX_TOTAL_BYTES:
                skipped.append(dict(source=package, reason="The prop budget is exhausted."))
                break
            if any(entry["sha256"] == digest for entry in entries):
                continue
            folder.mkdir(exist_ok=True)
            file = f"Props/{digest[:32]}.bin"
            (library / file).write_bytes(payload)
            total += len(payload)
            label = name if len(parts) == 1 else f"{name} {number + 1}"
            entry = dict(id=f"prop-{digest[:12]}", name=label[:128], file=file, layout=PROP_LAYOUT,
                         sha256=digest, vertex_count=vertices, index_count=indices, source=package)
            entries.append(entry)
            props.append((entry, material))
    tier = _attach_textures(client, root, record, captured, palettes, body_exports, props, library, cancel) \
        if props else None
    return entries, skipped, dict(choice=choice, models=models, sockets=sockets[:len(models)], texture_tier=tier)
