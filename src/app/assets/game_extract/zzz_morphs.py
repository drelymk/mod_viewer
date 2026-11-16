"""Verified Unity renderer routes and contained facial GLBs for ZZZ playback."""

import hashlib
import json
import math
import struct
import zlib

import numpy as np

from . import zzz_index

MAX_BYTES = 128 * 1024 * 1024


def _references(lookup, owner, reference, kind):
    if not isinstance(reference, dict):
        return set()
    matches = lookup.get((kind, reference.get("cab"), reference.get("path_id")), ())
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


def prepare(chosen, captured, rows):
    """Adopt routes only through native renderer mesh and Animator Avatar pointers."""
    native = chosen["native"]
    sources = {zzz_index.identity(item["row"]) for items in native.values() for item in items}
    source_meshes = {zzz_index.identity(item["row"]): item["mesh"] for items in native.values() for item in items}
    allowed_avatars = chosen["avatar_sources"]
    paths = {bone["hash"]: bone["path"] for bone in chosen["avatar"]["bones"]}
    lookup = {}
    for row in rows:
        if row["type"] in ("Mesh", "Avatar"):
            lookup.setdefault((row["type"], row["cab"], row["path_id"]), []).append(row)
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
            # bone pointers must still prove one unique verified mesh. A supplied
            # nonempty default-weight count must also agree.
            bones = renderer.get("bone_paths")
            count = renderer.get("blend_shape_count")
            if (not isinstance(bones, list) or not bones or len(bones) > 4096
                    or type(count) is not int or not 0 <= count <= 256
                    or any(not isinstance(bone, str) or paths.get(zlib.crc32(bone.encode("utf-8"))) != bone for bone in bones)):
                continue
            hashes = [zlib.crc32(bone.encode("utf-8")) for bone in bones]
            meshes = {source for source, mesh in source_meshes.items() if mesh["bone_hashes"] == hashes
                      and (not count or len((mesh.get("blend_shapes") or {}).get("channels", [])) == count)}
            identities = {hashlib.sha256(json.dumps(source_meshes[source], sort_keys=True, separators=(",", ":")).encode()).digest()
                          for source in meshes}
            if len(identities) != 1:
                continue
        if (not meshes or not meshes <= sources or not avatars or not avatars <= allowed_avatars
                or paths.get(key) != path):
            continue
        for source in meshes:
            routes.setdefault(source, set()).add(key)
    bindings, components = {}, []
    channel_cache = {}
    for number, items in native.items():
        part = captured[number]
        candidates = []
        for item in items:
            source = zzz_index.identity(item["row"])
            if source not in routes:
                continue
            mesh, signs = item["mesh"], item["signs"]
            if source not in channel_cache:
                channel_cache[source] = _channels(mesh)
            channels = channel_cache[source]
            if not channels:
                continue
            mapping = item["vertices"]
            targets = {}
            for path in sorted(routes[source]):
                for key, channel in channels.items():
                    name = f"{path:08x}:{key:08x}:" + channel["name"][:238]
                    identity = path, key
                    binding = name, channel["weight"]
                    if identity in bindings and bindings[identity] != binding:
                        raise ValueError("Native facial curve routing is ambiguous.")
                    bindings[identity] = binding
                    target = {}
                    for compact, original in mapping.items():
                        if original in channel["rows"]:
                            delta = np.asarray(channel["rows"][original]) * [*signs, *signs]
                            target[compact] = delta.tolist()
                    if target:
                        targets[name] = target
            candidates.append(targets)
        if not candidates:
            continue
        if any(candidate != candidates[0] for candidate in candidates[1:]):
            raise ValueError("Equivalent native meshes disagree about facial targets.")
        if candidates[0]:
            components.append(dict(part=part, targets=candidates[0]))
    if len(bindings) > 256:
        raise ValueError("Native facial curves exceed playback limits.")
    return dict(bindings=bindings, components=components)


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
