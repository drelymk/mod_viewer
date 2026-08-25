"""Metadata-driven WWMI direct Asset loading."""

import json
import math
import os
import re
import struct

from core.geometry_conventions import geometry_convention_for
from core.migoto_dump import MigotoDumpError, pack_indices
from core.vertex_attributes import VertexAttributeSource, decode_normals

from .. import asset_paths, asset_textures
from .hash_asset import _file_list
from .models import AssetLoadError, AssetMeshPart, AssetTexture, make_texture


_BINARY_RE = re.compile(r"(?:^|[-_])(?P<kind>vb\d*|ib)=(?P<hash>[0-9a-f]+)", re.I)
_IMAGE_EXTENSIONS = (".dds", ".png", ".jpg", ".jpeg", ".tga")


def _integer(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _hash(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lower().removeprefix("0x")
    return value if re.fullmatch(r"[0-9a-f]{8,}", value) else None


def _metadata_value(raw, *keys, default=None):
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return default


def _layout(raw, name, default=None):
    for container_name in ("vertex_layout", "vertexLayout", "layout", "attributes"):
        container = raw.get(container_name)
        if not isinstance(container, dict):
            continue
        value = container.get(name) or container.get(name.lower())
        if isinstance(value, dict):
            return value
    value = raw.get(name) or raw.get(name.lower())
    return value if isinstance(value, dict) else default


def _find_binary(files, kind, hash_value, raw, keys):
    hash_value = _hash(hash_value)
    explicit = _metadata_value(raw, *keys)
    if isinstance(explicit, str):
        for path in files:
            if os.path.basename(path).casefold() == os.path.basename(explicit).casefold():
                return path
    for path in files:
        match = _BINARY_RE.search(os.path.splitext(os.path.basename(path))[0])
        if not match:
            continue
        file_kind = match.group("kind").casefold()
        if file_kind.startswith("vb") and kind == "vb":
            file_kind = "vb"
        if file_kind == kind and hash_value == _hash(match.group("hash")):
            return path
    return None


def _read_binary(path, label):
    if not path:
        raise AssetLoadError(f"{label} geometry resource is missing.")
    try:
        if os.path.getsize(path) > 512 * 1024 * 1024:
            raise AssetLoadError(f"{label} geometry resource is too large.")
        with open(path, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise AssetLoadError(f"Could not read {label} geometry resource: {error}") from error


def _attribute(raw, name, default_offset, default_encoding):
    value = _layout(raw, name, {}) or {}
    raw_offset = value.get("offset") if "offset" in value else \
        value.get("aligned_byte_offset")
    offset = _integer(raw_offset, default_offset)
    stride = _integer(value.get("stride"), None)
    encoding = str(value.get("encoding") or value.get("format") or
                   default_encoding).casefold()
    if "snorm" in encoding and "8" in encoding:
        encoding = "snorm8x3"
    else:
        encoding = "f32x3" if name != "uv" else "f32x2"
    return offset, stride, encoding


def _positions(data, count, stride, offset):
    result = bytearray(count * 12)
    for index in range(count):
        source = index * stride + offset
        if source + 12 > len(data):
            raise AssetLoadError("WWMI position stream is truncated.")
        values = struct.unpack_from("<fff", data, source)
        if not all(math.isfinite(value) for value in values):
            raise AssetLoadError("WWMI position stream contains non-finite values.")
        struct.pack_into("<fff", result, index * 12, *values)
    return bytes(result)


def _uvs(data, count, stride, offset, encoding):
    result = bytearray(count * 8)
    fmt = "<ee" if "16" in encoding or "half" in encoding else "<ff"
    width = struct.calcsize(fmt)
    for index in range(count):
        source = index * stride + offset
        if source + width > len(data):
            raise AssetLoadError("WWMI texture-coordinate stream is truncated.")
        values = struct.unpack_from(fmt, data, source)
        if not all(math.isfinite(value) for value in values):
            raise AssetLoadError("WWMI texture-coordinate stream is invalid.")
        struct.pack_into("<ff", result, index * 8, *values)
    return bytes(result)


def _indices(data, index_size):
    if (index_size not in (2, 4) or len(data) < index_size
            or len(data) // index_size > 15_000_000):
        raise AssetLoadError("WWMI index format is unsupported.")
    total = len(data) // index_size
    fmt = "H" if index_size == 2 else "I"
    return list(struct.unpack_from(f"<{total}{fmt}", data, 0))


def _remap(positions, normals, uvs, indices, vertex_count):
    mapping = {}
    unique = []
    remapped = []
    for start in range(0, len(indices) - 2, 3):
        triangle = indices[start:start + 3]
        if any(item < 0 or item >= vertex_count for item in triangle):
            continue
        for item in triangle:
            if item not in mapping:
                mapping[item] = len(unique)
                unique.append(item)
        remapped.extend(mapping[item] for item in triangle)
    if not remapped:
        raise AssetLoadError("WWMI Asset contains no complete valid triangles.")

    def select(data, width):
        if data is None:
            return None
        return b"".join(data[item * width:(item + 1) * width] for item in unique)

    return select(positions, 12), select(normals, 12), select(uvs, 8), \
        pack_indices(remapped)


def _candidates(files, root, texture_source):
    result = []
    for path in files:
        if not path.casefold().endswith(_IMAGE_EXTENSIONS):
            continue
        # Candidate-only associations deliberately carry no semantic role.
        key = asset_textures.asset_texture_key(root, path, "diffuse")
        uri = texture_source(path, "diffuse") if texture_source else key
        if uri:
            result.append(AssetTexture(
                None, path, key, os.path.splitext(os.path.basename(path))[0],
                "candidate", uri))
    return tuple(result)


def _metadata_paths(root, record):
    paths = []
    for geometry in record.get("geometry", ()):
        path = geometry.get("metadata") if isinstance(geometry, dict) else None
        if path and path not in paths:
            paths.append(path)
    return paths


def load_wwmi_asset(root, record, *, texture_source=None):
    files = _file_list(asset_paths.safe_asset_dir(root, record.get("path")) or root)
    parts = []
    vb_cache = {}
    ib_cache = {}
    convention = geometry_convention_for("wuwa")
    for metadata_relative in _metadata_paths(root, record):
        metadata_file = asset_paths.safe_asset_path(root, metadata_relative)
        if not metadata_file:
            raise AssetLoadError("WWMI Metadata.json is missing from this Asset.")
        try:
            with open(metadata_file, encoding="utf-8") as stream:
                raw = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AssetLoadError(f"Metadata.json could not be parsed: {error}") from error
        if not isinstance(raw, dict):
            raise AssetLoadError("WWMI Metadata.json must contain an object.")
        vb_hash = _metadata_value(raw, "vb0_hash", "vb0Hash", "vb_hash")
        ib_hash = _metadata_value(raw, "ib_hash", "ibHash", "geometry_hash")
        if not vb_hash or not ib_hash:
            # The index's geometry hash is the explicit fallback for the IB;
            # the VB hash remains required because guessing a buffer is unsafe.
            geometry = next((item for item in record.get("geometry", ())
                             if item.get("metadata") == metadata_relative), {})
            ib_hash = ib_hash or geometry.get("hash")
        asset_dir = asset_paths.safe_asset_dir(root, record.get("path"))
        vb_file = _find_binary(
            files, "vb", vb_hash, raw,
            ("vb0_file", "vb0Filename", "vertex_buffer_file"))
        ib_file = _find_binary(
            files, "ib", ib_hash, raw,
            ("ib_file", "ibFilename", "index_buffer_file"))
        if not vb_file or not ib_file:
            raise AssetLoadError("Unsupported WWMI Asset geometry layout: "
                                 "known vb0/ib resources were not found.")
        if vb_file not in vb_cache:
            vb_cache[vb_file] = _read_binary(vb_file, "WWMI vertex")
        if ib_file not in ib_cache:
            ib_cache[ib_file] = _indices(
                _read_binary(ib_file, "WWMI index"),
                _integer(_metadata_value(raw, "index_size", "indexSize"), 4))
        vb = vb_cache[vb_file]
        all_indices = ib_cache[ib_file]
        stride = _integer(_metadata_value(
            raw, "vb0_stride", "vertex_stride", "vertexStride", "stride"))
        if not stride:
            raise AssetLoadError("Unsupported WWMI Asset geometry layout: vertex stride is missing.")
        vertex_count = _integer(_metadata_value(
            raw, "vertex_count", "vertexCount"), len(vb) // stride)
        if not vertex_count or vertex_count > 5_000_000:
            raise AssetLoadError("WWMI vertex count exceeds the safety limit.")
        pos_offset, _, _ = _attribute(raw, "position", 0, "f32x3")
        uv_offset, uv_stride, uv_encoding = _attribute(raw, "uv", 4, "f16x2")
        normal_offset, normal_stride, normal_encoding = _attribute(
            raw, "normal", 12, "f32x3")
        positions = _positions(vb, vertex_count, stride, pos_offset)
        uvs = _uvs(vb, vertex_count, uv_stride or stride, uv_offset, uv_encoding)
        normals = None
        normal_source = VertexAttributeSource(
            vb_file, normal_stride or stride, normal_offset, normal_encoding)
        if normal_source.encoding == "snorm8x3":
            normals = decode_normals(normal_source, vb, range(vertex_count))
        elif normal_offset + 12 <= stride:
            normals = _positions(vb, vertex_count, normal_stride or stride,
                                 normal_offset)
        components = raw.get("components")
        if not isinstance(components, list) or not components:
            components = [{}]
        candidates = _candidates(files, asset_dir or root, texture_source)
        for ordinal, component in enumerate(components):
            if not isinstance(component, dict):
                continue
            first = _integer(component.get("index_offset"), 0)
            count = _integer(component.get("index_count"))
            end = len(all_indices) if count is None else first + count
            selected = list(all_indices[first:min(end, len(all_indices))])
            if convention.reverse_winding:
                for position in range(0, len(selected) - 2, 3):
                    selected[position + 1], selected[position + 2] = \
                        selected[position + 2], selected[position + 1]
            pos, normal, uv, packed_indices = _remap(
                positions, normals, uvs, selected, vertex_count)
            name = component.get("component_name") or component.get(
                "componentName") or component.get("name")
            label = str(name or f"Part {ordinal + 1}")
            geometry_hash = _hash(vb_hash)
            source_path = record.get("path")
            key = f"{source_path}::{label}::{geometry_hash or 'unknown'}::{ordinal}"
            parts.append(AssetMeshPart(
                key=key, label=label, asset_type="WWMI", asset_path=source_path,
                geometry_hash=geometry_hash, component_name=str(name) if name else None,
                classification=str(name) if name else None,
                component_ordinal=ordinal, first_index=first,
                index_count=count, positions=pos, indices=packed_indices,
                uvs=uv, normals=normal, texture_candidates=candidates))
    if not parts:
        raise AssetLoadError("WWMI Asset contains no renderable components.")
    return tuple(parts)


__all__ = ["load_wwmi_asset"]
