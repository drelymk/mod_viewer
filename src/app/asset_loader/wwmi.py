"""Metadata-driven WWMI direct Asset loading."""

import json
import math
import os
import struct

from core.geometry_conventions import geometry_convention_for
from core.geometry_transport import canonicalize_uvs
from core.migoto_format import MigotoFormatError, parse_fmt
from core.vertex_attributes import VertexAttributeSource, decode_normals

from .. import asset_paths, asset_textures
from ..wuwa_texture_names import texture_component_ordinals
from .hash_asset import _file_list
from .models import (AssetAdapterResult, AssetLoadError, AssetMeshPart,
                     AssetTexture)


_IMAGE_EXTENSIONS = (".dds", ".png", ".jpg", ".jpeg", ".tga")
_MAX_BINARY_BYTES = 512 * 1024 * 1024
_MAX_VERTEX_COUNT = 5_000_000
_MAX_INDEX_COUNT = 15_000_000


def _integer(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _format(value):
    return str(value or "").strip().upper().removeprefix("DXGI_FORMAT_")


def _metadata_paths(record):
    paths = []
    for geometry in record.get("geometry", ()):
        if not isinstance(geometry, dict):
            continue
        candidates = geometry.get("metadataPaths")
        if not isinstance(candidates, list):
            candidates = []
        for path in [*candidates, geometry.get("metadata")]:
            if path and path not in paths:
                paths.append(path)
    return paths


def _metadata_geometry(record, metadata_relative):
    for geometry in record.get("geometry", ()):
        if (isinstance(geometry, dict)
                and geometry.get("metadata") == metadata_relative):
            return geometry
    return {}


def _read_binary(path, label):
    if not path:
        raise AssetLoadError(f"{label} geometry resource is missing.")
    try:
        if os.path.getsize(path) > _MAX_BINARY_BYTES:
            raise AssetLoadError(f"{label} geometry resource is too large.")
        with open(path, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise AssetLoadError(f"Could not read {label} geometry resource: {error}") from error


def _sibling_file(root, directory, name):
    """Resolve a known Component N.* sibling without leaving the Asset root."""
    direct = os.path.join(directory, name)
    relative = os.path.relpath(direct, root)
    safe = asset_paths.safe_asset_path(root, relative)
    if safe:
        return safe
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.casefold() != name.casefold():
                    continue
                if not entry.is_file(follow_symlinks=False):
                    return None
                relative = os.path.relpath(entry.path, root)
                return asset_paths.safe_asset_path(root, relative)
    except OSError:
        return None
    return None


def _component_files(root, metadata_directory, ordinal):
    stem = f"Component {ordinal}"
    return {
        "fmt": _sibling_file(root, metadata_directory, stem + ".fmt"),
        "vb": _sibling_file(root, metadata_directory, stem + ".vb"),
        "ib": _sibling_file(root, metadata_directory, stem + ".ib"),
    }


def _decode_positions(data, count, stride, element):
    fmt = _format(element.format)
    if fmt == "R32G32B32_FLOAT":
        width, unpack = 12, "<fff"
    elif fmt == "R32G32B32A32_FLOAT":
        width, unpack = 16, "<ffff"
    else:
        raise AssetLoadError(f"Unsupported POSITION format: {element.format}.")
    if element.offset + width > stride:
        raise AssetLoadError("POSITION lies outside the declared vertex stride.")
    result = bytearray(count * 12)
    for index in range(count):
        source = index * stride + element.offset
        if source + width > len(data):
            raise AssetLoadError("WWMI position stream is truncated.")
        values = struct.unpack_from(unpack, data, source)[:3]
        if not all(math.isfinite(value) for value in values):
            raise AssetLoadError("WWMI position stream contains non-finite values.")
        struct.pack_into("<fff", result, index * 12, *values)
    return bytes(result)


def _decode_normals(data, count, stride, element, path):
    fmt = _format(element.format)
    if fmt == "R32G32B32_FLOAT":
        return _decode_positions(data, count, stride, element)
    if fmt not in {"R8G8B8_SNORM", "R8G8B8A8_SNORM"}:
        raise AssetLoadError(f"Unsupported NORMAL format: {element.format}.")
    if element.offset + 3 > stride:
        raise AssetLoadError("NORMAL lies outside the declared vertex stride.")
    source = VertexAttributeSource(path, stride, element.offset, "snorm8x3")
    decoded = decode_normals(source, data, range(count))
    # A small number of exported vertices can contain zero SNORM normals.
    # Keep the component renderable and let the viewer reconstruct geometric
    # normals for the invalid stream rather than dropping the whole component.
    return bytes(decoded) if decoded is not None else None


def _decode_uvs(data, count, stride, element):
    fmt = _format(element.format)
    if fmt == "R16G16_FLOAT":
        width, unpack = 4, "<ee"
    elif fmt == "R32G32_FLOAT":
        width, unpack = 8, "<ff"
    else:
        raise AssetLoadError(f"Unsupported TEXCOORD0 format: {element.format}.")
    if element.offset + width > stride:
        raise AssetLoadError("TEXCOORD0 lies outside the declared vertex stride.")
    result = bytearray(count * 8)
    for index in range(count):
        source = index * stride + element.offset
        if source + width > len(data):
            raise AssetLoadError("WWMI texture-coordinate stream is truncated.")
        values = struct.unpack_from(unpack, data, source)
        if not all(math.isfinite(value) for value in values):
            raise AssetLoadError("WWMI texture-coordinate stream is invalid.")
        struct.pack_into("<ff", result, index * 8, *values)
    return canonicalize_uvs(result)


def _decode_indices(data, index_format):
    fmt = _format(index_format)
    if fmt == "R16_UINT":
        size, unpack = 2, "H"
    elif fmt == "R32_UINT":
        size, unpack = 4, "I"
    else:
        raise AssetLoadError(f"Unsupported WWMI index format: {index_format}.")
    if len(data) < size or len(data) % size:
        raise AssetLoadError("WWMI index stream is truncated.")
    count = len(data) // size
    if count > _MAX_INDEX_COUNT:
        raise AssetLoadError("WWMI index count exceeds the safety limit.")
    return list(struct.unpack_from(f"<{count}{unpack}", data, 0))


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

    from core.migoto_dump import pack_indices
    return select(positions, 12), select(normals, 12), select(uvs, 8), \
        pack_indices(remapped)


def _component_texture_candidates(files, root, ordinal, texture_source):
    result = []
    seen = set()
    for path in files:
        if not path.casefold().endswith(_IMAGE_EXTENSIONS):
            continue
        components = texture_component_ordinals(path)
        if components is None or ordinal not in components:
            continue
        # Candidate-only associations deliberately carry no semantic role.
        key = asset_textures.asset_texture_key(root, path, "diffuse")
        if key in seen:
            continue
        uri = texture_source(path, "diffuse") if texture_source else key
        if uri:
            seen.add(key)
            result.append(AssetTexture(
                None, path, key, os.path.splitext(os.path.basename(path))[0],
                "candidate", uri))
    return tuple(result)


def _warning(component, ordinal, reason, message):
    return {"component": component, "component_ordinal": ordinal,
            "reason": reason, "message": message}


def load_wwmi_asset(root, record, *, texture_source=None):
    parts = []
    warnings = []
    convention = geometry_convention_for("wuwa")
    metadata_paths = _metadata_paths(record)
    if not metadata_paths:
        raise AssetLoadError("WWMI Asset has no indexed Metadata.json.")

    for metadata_relative in metadata_paths:
        try:
            metadata_file = asset_paths.safe_asset_path(root, metadata_relative)
            if not metadata_file:
                raise AssetLoadError(
                    "WWMI Metadata.json is missing from this Asset.")
            with open(metadata_file, encoding="utf-8") as stream:
                raw = json.load(stream)
            if not isinstance(raw, dict):
                raise AssetLoadError("WWMI Metadata.json must contain an object.")
            components = raw.get("components")
            if components is None:
                components = [{}]
            if not isinstance(components, list):
                raise AssetLoadError(
                    "WWMI Metadata.json components must be a list.")
        except (AssetLoadError, OSError, UnicodeError,
                json.JSONDecodeError) as error:
            warnings.append(_warning(
                None, None, "metadata_invalid",
                f"{metadata_relative} skipped: {error}"))
            continue

        metadata_directory = os.path.dirname(metadata_file)
        component_files = _file_list(metadata_directory)
        geometry = _metadata_geometry(record, metadata_relative)
        geometry_hash = geometry.get("hash") or raw.get("vb0_hash")
        for ordinal, component in enumerate(components):
            if not isinstance(component, dict):
                warnings.append(_warning(
                    None, ordinal, "component_metadata_invalid",
                    f"Component {ordinal} metadata is not an object."))
                continue
            name = component.get("component_name") or component.get(
                "componentName") or component.get("name")
            name = str(name) if name else None
            files = _component_files(root, metadata_directory, ordinal)
            missing = next((key for key, path in files.items() if not path), None)
            if missing:
                warnings.append(_warning(
                    name, ordinal, f"{missing}_missing",
                    f"{name or f'Component {ordinal}'} skipped: "
                    f"Component {ordinal}.{missing} is missing."))
                continue
            try:
                layout = parse_fmt(files["fmt"])
                vertex_data = _read_binary(files["vb"], "WWMI vertex")
                index_data = _read_binary(files["ib"], "WWMI index")
                stride = layout.stride
                vertex_count = _integer(component.get("vertex_count"))
                if vertex_count is None and len(components) == 1:
                    vertex_count = _integer(raw.get("vertex_count"))
                vertex_count = vertex_count or len(vertex_data) // stride
                if not vertex_count or vertex_count > _MAX_VERTEX_COUNT:
                    raise AssetLoadError("WWMI vertex count exceeds the safety limit.")
                if len(vertex_data) < vertex_count * stride:
                    raise AssetLoadError("WWMI vertex stream is truncated.")
                positions_element = layout.semantic("POSITION", 0)
                if positions_element is None:
                    raise AssetLoadError("WWMI .fmt has no POSITION0 element.")
                positions = _decode_positions(
                    vertex_data, vertex_count, stride, positions_element)
                normal_element = layout.semantic("NORMAL", 0)
                normals = (_decode_normals(
                    vertex_data, vertex_count, stride, normal_element, files["vb"])
                    if normal_element else None)
                uv_element = layout.semantic("TEXCOORD", 0)
                uvs = (_decode_uvs(
                    vertex_data, vertex_count, stride, uv_element)
                    if uv_element else None)
                indices = _decode_indices(index_data, layout.index_format)
                expected_count = _integer(component.get("index_count"))
                if expected_count is not None and len(indices) != expected_count:
                    raise AssetLoadError(
                        f"WWMI index count is {len(indices)}, expected {expected_count}.")
                if convention.reverse_winding:
                    for position in range(0, len(indices) - 2, 3):
                        indices[position + 1], indices[position + 2] = \
                            indices[position + 2], indices[position + 1]
                pos, normal, uv, packed_indices = _remap(
                    positions, normals, uvs, indices, vertex_count)
            except (AssetLoadError, MigotoFormatError, OSError, struct.error) as error:
                warnings.append(_warning(
                    name, ordinal, "component_invalid",
                    f"{name or f'Component {ordinal}'} skipped: {error}"))
                continue

            first_index = _integer(component.get("index_offset"), 0)
            index_count = _integer(component.get("index_count"), len(indices))
            label = name or f"Part {ordinal + 1}"
            source_path = record.get("path")
            key = f"{source_path}::{label}::{geometry_hash or 'unknown'}::{ordinal}"
            candidates = _component_texture_candidates(
                component_files, root, ordinal, texture_source)
            parts.append(AssetMeshPart(
                key=key, label=label, asset_type="WWMI", asset_path=source_path,
                geometry_hash=geometry_hash, component_name=name,
                classification=(str(component.get("classification"))
                                if component.get("classification") else name),
                component_ordinal=ordinal, first_index=first_index,
                index_count=index_count, positions=pos, indices=packed_indices,
                uvs=uv, normals=normal, texture_candidates=candidates))
    if not parts:
        raise AssetLoadError("WWMI Asset contains no renderable components.")
    return AssetAdapterResult(tuple(parts), tuple(warnings))


__all__ = ["load_wwmi_asset"]
