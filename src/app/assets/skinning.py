"""Private captured-weight manifests for direct, indexed Asset previews."""

from dataclasses import dataclass

from core.geometry.skinning import SkinningSource, decode_skinning
from . import paths


@dataclass(frozen=True, slots=True)
class CapturedSkinning:
    file: str
    stride: int
    channels: tuple
    vertices: tuple


def capture_skinning(root, file, layout, vertices):
    channels = []
    for semantic in (0, 1):
        indices = layout.semantic("BLENDINDICES", semantic)
        weights = layout.semantic("BLENDWEIGHTS", semantic) or layout.semantic("BLENDWEIGHT", semantic)
        if indices is None and weights is None:
            break
        if indices is None or weights is None:
            return None
        if (indices.format.upper().removeprefix("DXGI_FORMAT_") != "R8G8B8A8_UINT"
                or weights.format.upper().removeprefix("DXGI_FORMAT_") != "R8G8B8A8_UNORM"
                or max(indices.offset, weights.offset) + 4 > layout.stride):
            return None
        channels.append((indices.offset, weights.offset))
    relative = paths.relative_asset_path(root, file)
    return CapturedSkinning(relative, layout.stride, tuple(channels), tuple(vertices)) if channels and relative else None


def decode_captured_skinning(root, manifest):
    file = paths.safe_asset_path(root, manifest.file)
    if not file:
        raise ValueError("The captured skin-weight resource is unavailable.")
    with open(file, "rb") as stream:
        data = stream.read(512 * 1024 * 1024 + 1)
    if len(data) > 512 * 1024 * 1024:
        raise ValueError("The captured skin-weight resource is too large.")
    influences = len(manifest.channels) * 4
    packed = bytearray(len(manifest.vertices) * influences * 2)
    for compact, vertex in enumerate(manifest.vertices):
        start = vertex * manifest.stride
        if start < 0 or start + manifest.stride > len(data):
            raise ValueError("The captured skin-weight stream is truncated.")
        for channel, (indices, weights) in enumerate(manifest.channels):
            target = compact * influences * 2 + channel * 4
            packed[target:target + 4] = data[start + indices:start + indices + 4]
            packed[target + influences:target + influences + 4] = data[start + weights:start + weights + 4]
    source = SkinningSource(manifest.file, influences * 2, influences,
                            "wwmi_u8_4" if influences == 4 else "wwmi_u8_8")
    return source, decode_skinning(source, packed, range(len(manifest.vertices)))
