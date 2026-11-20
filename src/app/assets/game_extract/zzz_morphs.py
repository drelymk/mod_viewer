"""Verified Unity renderer routes and contained facial GLBs for ZZZ playback."""

import functools
import hashlib
import json
import math
import struct
import zlib

import numpy as np

from . import zzz_index

MAX_BYTES = 128 * 1024 * 1024
# Equivalent native copies of one renderer mesh may differ by float export noise.
TOLERANCE = 1e-6


def _references(lookup, owner, reference, kind):
    if not isinstance(reference, dict) or not isinstance(reference.get("cab"), str):
        return set()
    # External pointers may spell CAB names in another case (Genshin lowercases them).
    matches = lookup.get((kind, reference["cab"].casefold(), reference.get("path_id")), ())
    if "file_ordinal" in reference:
        matches = [row for row in matches if (row["block"], row["offset"], row["file_ordinal"])
                   == (owner["block"], reference.get("offset"), reference["file_ordinal"])]
    return {zzz_index.identity(row) for row in matches}


def _channels(mesh):
    raw = mesh.get("blend_shapes")
    if not raw:
        return {}
    channels, frames, weights, vertices = (raw[key] for key in ("channels", "frames", "weights", "vertices"))
    if len(channels) > 256 or len(vertices) > 4000000 or len(weights) != len(frames):
        raise ValueError("Native facial targets exceed their limits.")
    result = {}
    for channel in channels:
        name, key, first, count = (channel[field] for field in ("name", "hash", "first", "count"))
        if (not isinstance(name, str) or not 1 <= len(name) <= 256 or type(key) is not int
                or not 0 <= key < 2 ** 32 or key in result or type(first) is not int
                or type(count) is not int or first < 0 or count < 1 or first + count > len(frames)):
            raise ValueError("Native facial target identity is invalid.")
        # In-between shapes need piecewise target expansion; never interpret them
        # as a single linear target or fabricate an unverified neutral shape.
        if count != 1:
            continue
        weight, frame = weights[first], frames[first]
        if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
            raise ValueError("Native facial target weight is invalid.")
        begin, size = frame["first"], frame["count"]
        if (type(begin) is not int or type(size) is not int or begin < 0 or size < 0
                or begin + size > len(vertices)):
            raise ValueError("Native facial target range is invalid.")
        rows = {}
        for row in vertices[begin:begin + size]:
            vertex = row["index"]
            delta = [*row["position"], *(row["normal"] if frame["normals"] else [0, 0, 0])]
            if (type(vertex) is not int or not 0 <= vertex < len(mesh["vertices"]) // 3 or vertex in rows
                    or len(delta) != 6 or any(type(v) not in (int, float) or not math.isfinite(v)
                                            or abs(v) > 1e6 for v in delta)):
                raise ValueError("Native facial target vertex is invalid.")
            rows[vertex] = delta
        result[key] = dict(name=name, weight=weight, rows=rows)
    return result


def _mapped(item, channels):
    """Each channel's deltas on the captured part's vertices, in capture axes."""
    signs = [*item["signs"], *item["signs"]]
    return {key: {compact: (np.asarray(channel["rows"][original]) * signs).tolist()
                  for compact, original in item["vertices"].items() if original in channel["rows"]}
            for key, channel in channels.items()}


def _targets_agree(first, other):
    """Same targets on the same vertices, with every delta within the tolerance of the representative."""
    return first.keys() == other.keys() and all(
        target.keys() == other[name].keys()
        and np.allclose(list(target.values()), [other[name][vertex] for vertex in target], rtol=0, atol=TOLERANCE)
        for name, target in first.items())


def prepare(chosen, captured, rows, expressions=None):
    """Adopt routes only through native renderer mesh and Animator Avatar pointers.

    ``expressions`` maps a captured part number to the native blend-shape weights
    (by channel hash) its capture already shows; they become per-target offsets.
    """
    native = chosen["native"]
    sources = {zzz_index.identity(item["row"]) for items in native.values() for item in items}
    source_meshes = {zzz_index.identity(item["row"]): item["mesh"] for items in native.values() for item in items}
    allowed_avatars = chosen["avatar_sources"]
    paths = {bone["hash"]: bone["path"] for bone in chosen["avatar"]["bones"]}
    channels_of = functools.cache(lambda source: _channels(source_meshes[source]))
    mapped = {}

    def mapped_of(item):
        if id(item) not in mapped:
            mapped[id(item)] = _mapped(item, channels_of(zzz_index.identity(item["row"])))
        return mapped[id(item)]

    def equivalent(copies):
        """Copies agree with one fixed representative, never another tolerated copy, on every captured part."""
        if len(copies) == 1:
            return True
        # Copies may list the same channels in another order.
        identities = [{key: (channel["name"], channel["weight"]) for key, channel in channels_of(source).items()}
                      for source in copies]
        first, *others = [[(number, mapped_of(item)) for number, items in native.items() for item in items
                           if zzz_index.identity(item["row"]) == source] for source in copies]
        return all(other == identities[0] for other in identities[1:]) and all(len(other) == len(first) and all(
            number == other_number and _targets_agree(targets, other_targets)
            for (number, targets), (other_number, other_targets) in zip(first, other)) for other in others)

    lookup = {}
    for row in rows:
        if row["type"] in ("Mesh", "Avatar"):
            lookup.setdefault((row["type"], row["cab"].casefold(), row["path_id"]), []).append(row)
    routes = {}
    for owner in rows:
        renderer = owner.get("renderer") if owner["type"] == "SkinnedMeshRenderer" else None
        if not isinstance(renderer, dict) or not isinstance(renderer.get("path"), str):
            continue
        meshes = _references(lookup, owner, renderer.get("mesh"), "Mesh")
        avatars = _references(lookup, owner, renderer.get("avatar"), "Avatar")
        path = renderer["path"]
        key = zlib.crc32(path.encode("utf-8"))
        if isinstance(renderer.get("mesh"), dict) and renderer["mesh"].get("path_id") == 0:
            # Some native prefabs stream their mesh and serialize a null Mesh
            # pointer and an empty default-weight array. Their explicit ordered
            # bone pointers must still prove the verified mesh. Several proven
            # copies may share that palette (for example a SeparateMesh export);
            # they form one route only when they place the same identities and
            # targets on every captured part they matched. A supplied nonempty
            # default-weight count must also agree.
            bones = renderer.get("bone_paths")
            count = renderer.get("blend_shape_count")
            if (not isinstance(bones, list) or not bones or len(bones) > 4096
                    or type(count) is not int or not 0 <= count <= 256
                    or any(not isinstance(bone, str) or paths.get(zlib.crc32(bone.encode("utf-8"))) != bone for bone in bones)):
                continue
            hashes = [zlib.crc32(bone.encode("utf-8")) for bone in bones]
            meshes = {source for source, mesh in source_meshes.items() if mesh["bone_hashes"] == hashes
                      and (not count or len((mesh.get("blend_shapes") or {}).get("channels", [])) == count)}
            if not meshes or not equivalent(sorted(meshes)):
                continue
        if (not meshes or not meshes <= sources or not avatars or not avatars <= allowed_avatars
                or paths.get(key) != path):
            continue
        for source in meshes:
            routes.setdefault(source, set()).add(key)
    bindings, components, offsets = {}, [], {}
    for number, items in native.items():
        part = captured[number]
        shown = (expressions or {}).get(number, {})
        candidates = []
        for item in items:
            source = zzz_index.identity(item["row"])
            if source not in routes:
                continue
            channels = channels_of(source)
            if not channels:
                continue
            deltas = mapped_of(item)
            targets = {}
            for path in sorted(routes[source]):
                for key, channel in channels.items():
                    name = f"{path:08x}:{key:08x}:" + channel["name"][:238]
                    identity = path, key
                    binding = name, channel["weight"]
                    if identity in bindings and bindings[identity] != binding:
                        raise ValueError("Native facial curve routing is ambiguous.")
                    bindings[identity] = binding
                    offset = float(shown.get(str(key), 0.0))
                    # One rendered target is shared by its parts; their captures must agree.
                    if abs(offsets.setdefault(name, offset) - offset) > 0.02:
                        raise ValueError("Captured facial expressions disagree.")
                    if deltas[key]:
                        targets[name] = deltas[key]
            candidates.append(targets)
        if not candidates:
            continue
        # The first proven copy is the representative whose targets are emitted.
        if any(not _targets_agree(candidates[0], candidate) for candidate in candidates[1:]):
            raise ValueError("Equivalent native meshes disagree about facial targets.")
        if candidates[0]:
            components.append(dict(part=part, targets=candidates[0]))
    if len(bindings) > 256:
        raise ValueError("Native facial curves exceed playback limits.")
    return dict(bindings=bindings, components=components, offsets=offsets)


def export_glb(facial, morph, frames, duration, directory):
    """Package a playable GLB and its sparse runtime view in one checked file."""
    names = morph["names"]
    blob, sparse = bytearray(), bytearray()
    document = dict(asset={"version": "2.0", "generator": "mod-viewer"},
                    buffers=[], bufferViews=[], accessors=[], meshes=[], nodes=[],
                    scenes=[{"nodes": []}], scene=0, animations=[])

    def view(data):
        blob.extend(b"\0" * (-len(blob) % 4))
        document["bufferViews"].append(dict(buffer=0, byteOffset=len(blob), byteLength=len(data)))
        blob.extend(data)
        if len(blob) > MAX_BYTES:
            raise ValueError("Facial GLB exceeds its size limit.")
        return len(document["bufferViews"]) - 1

    def accessor(values, kind, *, integer=False, bounds=False):
        values = np.asarray(values, dtype="<u4" if integer else "<f4")
        raw = dict(bufferView=view(values.tobytes()), componentType=5125 if integer else 5126,
                   count=len(values), type=kind)
        if bounds:
            raw.update(min=np.atleast_1d(values.min(axis=0)).tolist(), max=np.atleast_1d(values.max(axis=0)).tolist())
        document["accessors"].append(raw)
        return len(document["accessors"]) - 1

    grouped = {}
    for component in facial["components"]:
        part, targets = component["part"], component["targets"]
        if not set(names) & targets.keys():
            continue
        count = len(part["positions"])
        attributes = dict(POSITION=accessor(part["positions"], "VEC3", bounds=True))
        if part["normals"] is not None:
            attributes["NORMAL"] = accessor(part["normals"], "VEC3")
        if part["uv"] is not None:
            uv = part["uv"].copy()
            uv[:, 1] = 1 - uv[:, 1]
            attributes["TEXCOORD_0"] = accessor(uv, "VEC2")
        gltf_targets = []
        identity = part["vertex_sha256"], part["index_sha256"]
        group = grouped.setdefault(identity, dict(vertex_count=part["source_vertex_count"], targets={}))
        for name in names:
            rows = targets.get(name, {})
            vertices = sorted(rows)
            gltf_target = {}
            for attribute, offset in (("POSITION", 0), ("NORMAL", 3)):
                if attribute == "NORMAL" and "NORMAL" not in attributes:
                    continue
                raw = dict(componentType=5126, count=count, type="VEC3")
                if vertices:
                    values = np.asarray([rows[v][offset:offset + 3] for v in vertices], dtype="<f4")
                    raw["sparse"] = dict(count=len(vertices),
                        indices=dict(bufferView=view(np.asarray(vertices, dtype="<u4").tobytes()), componentType=5125),
                        values=dict(bufferView=view(values.tobytes())))
                    if attribute == "POSITION":
                        raw.update(min=np.minimum(values.min(axis=0), 0).tolist(), max=np.maximum(values.max(axis=0), 0).tolist())
                elif attribute == "POSITION":
                    raw.update(min=[0, 0, 0], max=[0, 0, 0])
                document["accessors"].append(raw)
                gltf_target[attribute] = len(document["accessors"]) - 1
            gltf_targets.append(gltf_target)
            merged = group["targets"].setdefault(name, {})
            for vertex, delta in rows.items():
                original = part["source_vertices"][vertex]
                if original in merged and merged[original] != delta:
                    raise ValueError("Captured facial targets conflict on a shared vertex.")
                merged[original] = delta
        primitive = dict(attributes=attributes, indices=accessor(part["indices"].ravel(), "SCALAR", integer=True), targets=gltf_targets)
        index = len(document["meshes"])
        document["meshes"].append(dict(primitives=[primitive], weights=[0] * len(names), extras={"targetNames": names}))
        document["nodes"].append(dict(mesh=index))
        document["scenes"][0]["nodes"].append(index)
    components = []
    for (vertex_hash, index_hash), group in grouped.items():
        targets = []
        for name, rows in group["targets"].items():
            if not rows:
                continue
            offset = len(sparse)
            for vertex, delta in sorted(rows.items()):
                sparse.extend(struct.pack("<I6f", vertex, *delta))
            targets.append(dict(name=name, offset=offset, count=len(rows)))
        if targets:
            components.append(dict(vertex_sha256=vertex_hash, index_sha256=index_hash,
                                   vertex_count=group["vertex_count"], targets=targets))
    if not components:
        raise ValueError("No captured facial targets match this animation.")
    times = accessor(np.linspace(0, duration, frames), "SCALAR", bounds=True)
    weights = accessor(morph["weights"].ravel(), "SCALAR")
    document["animations"] = [dict(samplers=[dict(input=times, output=weights, interpolation="LINEAR")],
        channels=[dict(sampler=0, target={"node": index, "path": "weights"}) for index in range(len(document["nodes"]))])]
    runtime_view = view(sparse)
    document["extras"] = dict(mod_viewer_morphs=dict(version=1, buffer_view=runtime_view, components=components, names=names))
    document["buffers"] = [dict(byteLength=len(blob))]
    metadata = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    if len(metadata) > 4 * 1024 * 1024:
        raise ValueError("Facial GLB metadata exceeds its size limit.")
    metadata += b" " * (-len(metadata) % 4)
    blob.extend(b"\0" * (-len(blob) % 4))
    size = 28 + len(metadata) + len(blob)
    if size > MAX_BYTES:
        raise ValueError("Facial GLB exceeds its size limit.")
    data = (struct.pack("<5I", 0x46546C67, 2, size, len(metadata), 0x4E4F534A) + metadata
            + struct.pack("<2I", len(blob), 0x004E4942) + blob)
    digest = hashlib.sha256(data).hexdigest()
    filename = "facial-" + digest[:24] + ".glb"
    file = directory / filename
    if file.exists() and file.read_bytes() != data:
        raise ValueError("Facial GLB conflicts with a preserved resource.")
    file.write_bytes(data)
    return dict(format="glb", file=filename, sha256=digest, names=names, components=components)
