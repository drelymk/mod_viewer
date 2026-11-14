"""Read native UEFormat meshes and prove captured component palette mappings."""

import gzip
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import struct

from core.geometry.migoto_format import parse_fmt
from app.assets import paths
from app.assets.loader.wwmi import _metadata_paths
from app.assets.rig import validate_rig, _humanoid_controls as humanoid_hierarchy


MAX_MESH_BYTES = 64 * 1024 * 1024
MAX_VERTICES = 250000
MAX_INDICES = 1500000
MAX_UV_VALUES = 1000000
MAX_INFLUENCES = 8


def read_limited(file, limit, label):
    file = Path(file)
    if file.stat().st_size > limit:
        raise ValueError(f"{label} exceeds its size limit.")
    with file.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{label} exceeds its size limit.")
    return data


class Reader(BytesIO):
    def take(self, size):
        if not 0 <= size <= MAX_MESH_BYTES:
            raise ValueError("Native game resource exceeds its size limit.")
        result = self.read(size)
        if len(result) != size:
            raise ValueError("Native game resource is truncated.")
        return result

    def integer(self):
        return struct.unpack("<i", self.take(4))[0]

    def count(self, limit):
        value = self.integer()
        if not 0 <= value <= limit:
            raise ValueError("Native game resource count exceeds its limit.")
        return value

    def string(self):
        return self.take(self.count(65536)).decode("utf-8")

    def attributes(self):
        result = {}
        for _ in range(self.count(128)):
            name = self.string()
            if name in result:
                raise ValueError("Duplicate native game attribute.")
            result[name] = self.take(self.count(MAX_MESH_BYTES))
        return result


def read_mesh(file):
    reader = Reader(read_limited(file, MAX_MESH_BYTES, "Native mesh"))
    if reader.take(8) != b"UEFORMAT" or reader.string() != "UEMODEL" or reader.take(1) != b"\x0a":
        raise ValueError("Unsupported native mesh format.")
    name, package = reader.string(), reader.string()
    if reader.take(1) == b"\x01":
        codec = reader.string()
        size = reader.count(MAX_MESH_BYTES)
        packed = reader.take(reader.count(MAX_MESH_BYTES))
        if codec != "GZIP":
            raise ValueError("Unsupported native mesh compression.")
        with gzip.GzipFile(fileobj=BytesIO(packed)) as stream:
            unpacked = stream.read(size + 1)
        if len(unpacked) != size:
            raise ValueError("Native mesh decompression size differs.")
        reader = Reader(unpacked)
    attributes = reader.attributes()
    lods = Reader(attributes["LODS"])
    if not lods.count(16):
        raise ValueError("Native mesh has no geometry.")
    lods.string()
    level = lods.attributes()
    skeleton = Reader(attributes["SKELETON"]).attributes()
    source = Reader(skeleton["BONES"])
    bones = []
    for _ in range(source.count(4096)):
        bone_name, parent = source.string(), source.integer()
        values = struct.unpack("<3f4f3f", source.take(40))
        bones.append(dict(name=bone_name, parent=parent, translation=list(values[:3]),
                          rotation=list(values[3:7]), scale=list(values[7:])))
    vertices = Reader(level["VERTICES"])
    positions = [struct.unpack("<3f", vertices.take(12)) for _ in range(vertices.count(MAX_VERTICES))]
    if not positions or not all(math.isfinite(v) for row in positions for v in row):
        raise ValueError("Native positions are invalid.")
    source = Reader(level["INDICES"])
    indices = [source.integer() for _ in range(source.count(MAX_INDICES))]
    if any(not 0 <= index < len(positions) for index in indices):
        raise ValueError("Native mesh indices are invalid.")
    source = Reader(level["TEXCOORDS"])
    uv = []
    for _ in range(source.count(16)):
        source.string()
        count = source.count(MAX_VERTICES)
        if count != len(positions) or (len(uv) + 1) * count > MAX_UV_VALUES:
            raise ValueError("Native texture coordinates exceed their geometry limits.")
        uv.append([struct.unpack("<2f", source.take(8)) for _ in range(count)])
    source = Reader(level["MATERIALS"])
    sections = []
    for _ in range(source.count(4096)):
        source.string()
        source.string()
        sections.append((source.integer(), source.count(MAX_INDICES // 3) * 3))
    weights = [[] for _ in positions]
    source = Reader(level["WEIGHTS"])
    influences = bytearray(len(positions))
    for _ in range(source.count(MAX_VERTICES * MAX_INFLUENCES)):
        bone, vertex, weight = struct.unpack("<Hif", source.take(10))
        if not 0 <= vertex < len(positions) or bone >= len(bones) or not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError("Native mesh weights are invalid.")
        if influences[vertex] == MAX_INFLUENCES:
            raise ValueError("Native mesh has too many influences per vertex.")
        influences[vertex] += 1
        quantized = round(weight * 255)
        if quantized:
            weights[vertex].append((bone, quantized))
    return dict(name=name, package=package, bones=bones, positions=positions,
                indices=indices, uv=uv, sections=sections, weights=weights)


def capture(root, record):
    components, files = [], {}
    for relative in _metadata_paths(record):
        metadata_file = paths.safe_asset_path(root, relative)
        if not metadata_file:
            raise ValueError("Asset geometry metadata is unavailable.")
        metadata_file = Path(metadata_file)
        data = read_limited(metadata_file, 4 * 1024 * 1024, "Captured metadata")
        metadata = json.loads(data.decode("utf-8-sig"))
        files[str(metadata_file)] = hashlib.sha256(data).hexdigest()
        for ordinal, component in enumerate(metadata["components"]):
            resources = {}
            for extension in ("fmt", "vb", "ib"):
                file = metadata_file.parent / f"Component {ordinal}.{extension}"
                relative_file = paths.relative_asset_path(root, str(file))
                safe = paths.safe_asset_path(root, relative_file) if relative_file else None
                if not safe:
                    raise ValueError("Rig extraction requires every captured component buffer.")
                limit = 1024 * 1024 if extension == "fmt" else 128 * 1024 * 1024
                data = read_limited(safe, limit, "Captured component")
                files[str(file)] = hashlib.sha256(data).hexdigest()
                resources[extension] = data if extension != "fmt" else parse_fmt(file)
            components.append(dict(geometry_hash=metadata["vb0_hash"], component_ordinal=ordinal,
                first_index=component["index_offset"], index_count=component["index_count"],
                vertex_sha256=hashlib.sha256(resources["vb"]).hexdigest(),
                index_sha256=hashlib.sha256(resources["ib"]).hexdigest(), resources=resources))
    if not components:
        raise ValueError("Asset has no captured components.")
    return components, files


def match_components(captured, mesh):
    palettes = []
    for component in captured:
        first, count = component["first_index"], component["index_count"]
        if (first, count) not in mesh["sections"]:
            raise ValueError("Native mesh component ranges differ from the Asset.")
        resource = component["resources"]
        layout, vertices = resource["fmt"], resource["vb"]
        if not layout.stride or len(vertices) % layout.stride:
            raise ValueError("Captured vertex stride is invalid.")
        vertex_count = len(vertices) // layout.stride
        position = layout.semantic("POSITION")
        if not position or position.format.removeprefix("DXGI_FORMAT_") != "R32G32B32_FLOAT":
            raise ValueError("Unsupported captured position layout.")
        index_format = layout.index_format.removeprefix("DXGI_FORMAT_")
        kind = {"R16_UINT": "H", "R32_UINT": "I"}.get(index_format)
        if not kind:
            raise ValueError("Unsupported captured index layout.")
        indices = [row[0] for row in struct.iter_unpack("<" + kind, resource["ib"])]
        if len(indices) != count or first + count > len(mesh["indices"]):
            raise ValueError("Native mesh triangle counts differ from the Asset.")
        correspondence = {}
        for local, native in zip(indices, mesh["indices"][first:first + count]):
            if not 0 <= local < vertex_count or (local in correspondence and correspondence[local] != native):
                raise ValueError("Captured vertex correspondence is ambiguous.")
            correspondence[local] = native
        if len(correspondence) != vertex_count:
            raise ValueError("Captured vertices are not completely covered by native geometry.")
        channels = []
        for index in (0, 1):
            ids = layout.semantic("BLENDINDICES", index)
            amounts = layout.semantic("BLENDWEIGHTS", index) or layout.semantic("BLENDWEIGHT", index)
            if ids is None and amounts is None:
                break
            if (not ids or not amounts or ids.format.removeprefix("DXGI_FORMAT_") != "R8G8B8A8_UINT"
                    or amounts.format.removeprefix("DXGI_FORMAT_") != "R8G8B8A8_UNORM"):
                raise ValueError("Unsupported captured skin layout.")
            channels.append((ids.offset, amounts.offset))
        if not channels:
            raise ValueError("Captured component has no skin weights.")
        candidates = {}
        captured_weights = []
        for local in range(vertex_count):
            native, offset = correspondence[local], local * layout.stride
            if struct.unpack_from("<3f", vertices, offset + position.offset) != mesh["positions"][native]:
                raise ValueError("Native mesh positions differ from the Asset.")
            for channel in range(16):
                element = layout.semantic("TEXCOORD", channel)
                if element is None:
                    break
                uv_format = {"R16G16_FLOAT": "<2e", "R32G32_FLOAT": "<2f"}.get(element.format.removeprefix("DXGI_FORMAT_"))
                if not uv_format or channel >= len(mesh["uv"]) or struct.unpack_from(uv_format, vertices, offset + element.offset) != mesh["uv"][channel][native]:
                    raise ValueError("Native texture coordinates differ from the Asset.")
            influences = [(bone, weight) for ids, amounts in channels
                          for bone, weight in zip(vertices[offset + ids:offset + ids + 4], vertices[offset + amounts:offset + amounts + 4]) if weight]
            expected = mesh["weights"][native]
            if sorted(weight for _, weight in influences) != sorted(weight for _, weight in expected):
                raise ValueError("Native weight values differ from the Asset.")
            for bone, weight in influences:
                possible = {target for target, value in expected if value == weight}
                candidates[bone] = candidates.get(bone, possible) & possible
            captured_weights.append((influences, expected))
        if not candidates or set(candidates) != set(range(max(candidates) + 1)) or any(len(v) != 1 for v in candidates.values()):
            raise ValueError("Captured bone palettes cannot be mapped unambiguously.")
        mapping = [next(iter(candidates[i])) for i in range(len(candidates))]
        if len(set(mapping)) != len(mapping):
            raise ValueError("Captured bone palettes contain conflicting aliases.")
        for influences, expected in captured_weights:
            if sorted((mapping[bone], weight) for bone, weight in influences) != sorted(expected):
                raise ValueError("Native skin influence pairs differ from the Asset.")
        palettes.append({key: value for key, value in component.items() if key != "resources"} | {"bone_map": mapping})
    return palettes


_HUMANOID_NAMES = dict(pelvis="Bip001Pelvis", chest="Bip001Spine2", neck="Bip001Neck", head="Bip001Head")
for _side, _key in (("L", "left"), ("R", "right")):
    _HUMANOID_NAMES.update({
        _key + "Shoulder": f"Bip001{_side}UpperArm", _key + "Elbow": f"Bip001{_side}Forearm",
        _key + "Hand": f"Bip001{_side}Hand", _key + "Hip": f"Bip001{_side}Thigh",
        _key + "Knee": f"Bip001{_side}Calf", _key + "Foot": f"Bip001{_side}Foot"})


def humanoid_controls(raw):
    """Map the exact game Bip001 biped joints; any missing name or hierarchy mismatch yields None."""
    index = {bone["name"]: number for number, bone in enumerate(raw["bones"])}
    if not all(name in index for name in _HUMANOID_NAMES.values()):
        return None
    controls = {key: index[name] for key, name in _HUMANOID_NAMES.items()}
    try:
        humanoid_hierarchy(controls, raw["bones"])
    except ValueError:
        return None
    return controls


def make_rig(mesh, palettes, previous=None):
    raw = dict(format="mod-viewer-rig", version=1, coordinate_space="asset",
               bones=mesh["bones"], components=palettes, skin_bindings=[])
    merged, cursors = [], {}
    for palette in palettes:
        geometry = palette["geometry_hash"]
        merged.append(dict(geometry_hash=geometry, component_ordinal=palette["component_ordinal"],
                           vg_offset=cursors.get(geometry, 0)))
        cursors[geometry] = cursors.get(geometry, 0) + len(palette["bone_map"])
    raw["wwmi_merged_palette"] = merged
    controls = humanoid_controls(raw)
    if controls:
        raw["humanoid_controls"] = controls
    normalized = validate_rig(raw)
    if previous:
        old = validate_rig(previous)
        compatible = len(old["bones"]) == len(normalized["bones"]) and old["components"] == normalized["components"]
        for left, right in zip(old["bones"], normalized["bones"]):
            compatible = compatible and left["name"] == right["name"] and left["parent"] == right["parent"]
            compatible = compatible and all(abs(a - b) <= 1e-5 * abs(b) for a, b in zip(left["scale"], right["scale"]))
            compatible = compatible and max(abs(a - b) for a, b in zip(left["translation"], right["translation"])) < .0001
            compatible = compatible and min(sum((a - sign * b) ** 2 for a, b in zip(left["rotation"], right["rotation"])) for sign in (-1, 1)) < .00000001
        if compatible:
            if "humanoid_controls" in old or not controls:
                return old
            return validate_rig({key: value for key, value in old.items() if key != "id"} | dict(humanoid_controls=controls))
    return normalized
