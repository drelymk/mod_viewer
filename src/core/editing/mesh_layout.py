"""Validation and lossless index-buffer layout helpers for mesh edits."""

from __future__ import annotations


class MeshLayoutError(ValueError):
    """Raised when viewer-provided triangle provenance is not a partition."""


def validate_triangle_partition(draw_count, index_size, parts):
    """Return normalized triangle ordinal lists after strict validation."""
    try:
        count = int(draw_count)
    except (TypeError, ValueError) as exc:
        raise MeshLayoutError("The authored draw count is invalid.") from exc
    if count <= 0 or count % 3:
        raise MeshLayoutError("The authored draw count must be a positive multiple of 3.")
    if index_size not in (2, 4):
        raise MeshLayoutError("Only 16-bit and 32-bit index buffers are supported.")
    if not isinstance(parts, (list, tuple)) or len(parts) < 2:
        raise MeshLayoutError("At least two loose parts are required.")

    triangle_count = count // 3
    normalized = []
    seen = set()
    for part in parts:
        if not isinstance(part, (list, tuple)) or not part:
            raise MeshLayoutError("Every loose part must contain triangles.")
        values = []
        for ordinal in part:
            if isinstance(ordinal, bool) or not isinstance(ordinal, int):
                raise MeshLayoutError("Triangle ordinals must be integers.")
            if ordinal < 0 or ordinal >= triangle_count:
                raise MeshLayoutError("A triangle ordinal is outside the authored draw.")
            if ordinal in seen:
                raise MeshLayoutError("Triangle ordinals must not overlap.")
            seen.add(ordinal)
            values.append(ordinal)
        normalized.append(tuple(values))

    if len(seen) != triangle_count:
        raise MeshLayoutError("Loose parts must cover every authored triangle exactly once.")
    return tuple(normalized)


def drawindexed_ranges(start, base, parts):
    """Return ``(count, start, base)`` triples in the requested part order."""
    cursor = int(start)
    result = []
    for part in parts:
        count = len(part) * 3
        result.append((count, cursor, int(base)))
        cursor += count
    return tuple(result)


def repack_index_bytes(data, start, count, index_size, parts):
    """Reorder only the authored draw's raw triangle records."""
    normalized = validate_triangle_partition(count, index_size, parts)
    start_byte = int(start) * index_size
    end_byte = start_byte + int(count) * index_size
    if start_byte < 0 or end_byte > len(data):
        raise MeshLayoutError("The authored draw range exceeds the index buffer.")

    original = bytes(data)
    result = bytearray(original)
    cursor = start_byte
    for part in normalized:
        for triangle in part:
            source = start_byte + triangle * 3 * index_size
            chunk = original[source:source + 3 * index_size]
            result[cursor:cursor + len(chunk)] = chunk
            cursor += len(chunk)
    return bytes(result), normalized


__all__ = [
    "MeshLayoutError", "validate_triangle_partition", "drawindexed_ranges",
    "repack_index_bytes",
]
