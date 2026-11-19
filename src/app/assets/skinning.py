"""Private captured-weight manifests for direct, indexed Asset previews."""

from dataclasses import dataclass
import struct

from core.geometry.skinning import SkinningSource, decode_skinning
from core.geometry.migoto_dump import parse_vertex_dump
from . import paths
from .rig import ATTACHMENT_NAMESPACE


@dataclass(frozen=True, slots=True)
class CapturedSkinning:
    file: str
    stride: int
    channels: tuple
    vertices: tuple


@dataclass(frozen=True, slots=True)
class CapturedDumpSkinning:
    file: str
    vertices: tuple
    index_file: str = ""


@dataclass(frozen=True, slots=True)
class CapturedAttachment:
    """A weightless captured part that an extracted rig may prove against native weights."""
    file: str
    vertices: tuple
    index_file: str


def capture_dump_skinning(root, file, layout, vertices, index_file=None):
    relative = paths.relative_asset_path(root, file)
    declared = any(item.name == "BLENDINDICES" and item.index == 0 for item in layout.semantics)
    index_relative = paths.relative_asset_path(root, index_file) if index_file else ""
    return CapturedDumpSkinning(relative, tuple(vertices), index_relative or "") if relative and declared else None


def capture_attachment(root, file, layout, vertices, index_file):
    relative = paths.relative_asset_path(root, file)
    index_relative = paths.relative_asset_path(root, index_file) if index_file else ""
    if not relative or not index_relative or any(item.name in ("BLENDINDICES", "BLENDWEIGHT", "BLENDWEIGHTS")
                                                 for item in layout.semantics):
        return None
    return CapturedAttachment(relative, tuple(vertices), index_relative)


def attachment_skinning(manifest, attachment):
    """Native weights proven for a weightless capture, in captured compact vertex order.

    Callers take ``attachment`` only from a validated Rig.json palette that the
    part's own captured fingerprints admitted.
    """
    count = len(manifest.vertices)
    weights, slots = attachment["weights"], attachment["slots"]
    if len(weights) != count * 4 or len(slots) != count * 4:
        raise ValueError("The proven attachment does not match the captured part.")
    raw = b"".join(struct.pack("<4f4I", *weights[i:i + 4], *slots[i:i + 4]) for i in range(0, count * 4, 4))
    source = SkinningSource(manifest.file, 32, 4, "gimi_f32_u32_4", bone_id_namespace=ATTACHMENT_NAMESPACE)
    return source, decode_skinning(source, raw, range(count))


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
    if isinstance(manifest, CapturedDumpSkinning):
        dump = parse_vertex_dump(file, include_skinning=True)
        if dump.blend_data is None:
            raise ValueError("The captured vertex dump has no supported skin weights.")
        source = SkinningSource(manifest.file, 32, 4, "gimi_f32_u32_4")
        return source, decode_skinning(source, dump.blend_data, manifest.vertices)
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
