"""Offline ActorX conversion and verified reference-pose layer composition."""

import hashlib
import json
import math
import struct


def _near(left, right):
    # Float32 reference poses of distant bones differ by magnitude-scaled rounding.
    return all(abs(a - b) <= 0.001 + 1e-4 * max(abs(a), abs(b)) for a, b in zip(left, right))


def _same_rotation(left, right):
    return min(sum((a - sign * b) ** 2 for a, b in zip(left, right)) for sign in (-1, 1)) <= 0.00001


class ReferencePoseMismatch(ValueError):
    """A PSA reference pose needs independent source-skeleton verification."""


def _animation_reference(rig, reference, authored_bones):
    """Admit reference-pose differences with the same ordered skeleton and scales.

    The extraction adapter separately proves the mesh/sequence skeleton link and
    Animation retargeting mode. Authored keys are absolute local transforms;
    omitted tracks must fall back to the mesh's own reference transform.
    """
    source, target = reference["bones"], rig["bones"]
    if len(source) != len(target) or not isinstance(authored_bones, (list, set, frozenset)):
        raise ValueError("Animation source skeleton or authored track map is invalid.")
    if any(type(index) is not int or not 0 <= index < len(target) for index in authored_bones):
        raise ValueError("Animation authored track map is invalid.")
    authored = set(authored_bones)
    if len(authored) != len(authored_bones):
        raise ValueError("Animation authored track map is invalid.")
    fallback = set()
    for index, (left, right) in enumerate(zip(source, target)):
        if (left["name"] != right["name"] or left["parent"] != right["parent"]
                or len(left["translation"]) != 3 or len(left.get("scale", [1, 1, 1])) != 3
                or any(not math.isfinite(value) or abs(value) > 1e6 for value in left["translation"])
                or any(not math.isfinite(a) or abs(a - b) > 1e-5 * max(abs(a), abs(b))
                       for a, b in zip(left.get("scale", [1, 1, 1]), right.get("scale", [1, 1, 1])))
                or len(left["rotation"]) != 4
                or any(not math.isfinite(value) for value in left["rotation"])
                or abs(sum(value * value for value in left["rotation"]) - 1) > 0.002):
            raise ValueError("Animation source skeleton is incompatible with the Asset.")
        if index not in authored and (
            not _same_rotation(left["rotation"], right["rotation"])
            or not _near(left["translation"], right["translation"])
        ):
            fallback.add(index)
    return source, fallback


def convert_clip(data, rig, duration, *, reference=None, authored_bones=None):
    expected, fallback = rig["bones"], set()
    if reference is not None:
        expected, fallback = _animation_reference(rig, reference, authored_bones)
    chunks = {}
    offset = 0
    while offset < len(data):
        name, _, size, count = struct.unpack_from("<20siii", data, offset)
        offset += 32
        if size < 0 or count < 0 or offset + size * count > len(data):
            raise ValueError("Incomplete ActorX chunk.")
        chunks[name.rstrip(b"\0")] = (size, count, data[offset : offset + size * count])
        offset += size * count
    size, count, bones = chunks[b"BONENAMES"]
    if size != 120 or count != len(rig["bones"]):
        raise ValueError("Animation skeleton size differs from the Asset.")
    mismatches = []
    for index, bone in enumerate(expected):
        row = bones[index * size : (index + 1) * size]
        parent = struct.unpack_from("<i", row, 72)[0]
        name = row[:64].split(b"\0")[0].decode("utf-8")
        rotation = struct.unpack_from("<4f", row, 76)
        translation = struct.unpack_from("<3f", row, 92)
        # Some ActorX writers use parent zero for the root. Poses are unconverted.
        if name != bone["name"]:
            field = "name"
        elif parent not in ({-1, 0} if bone["parent"] < 0 else {bone["parent"]}):
            field = "parent"
        elif not _near(translation, bone["translation"]):
            field = "translation"
        elif not _same_rotation(rotation, bone["rotation"]):
            field = "rotation"
        else:
            continue
        mismatches.append((field, bone["name"]))
    if mismatches:
        field, name = mismatches[0]
        raise ReferencePoseMismatch(
            f"Animation reference skeleton differs from the Asset: {len(mismatches)} bones, "
            f"first is the {field} of {name}."
        )
    size, count, info = chunks[b"ANIMINFO"]
    if size != 168 or count != 1:
        raise ValueError("Expected one animation sequence.")
    frames = struct.unpack_from("<i", info, 164)[0]
    fps = struct.unpack_from("<f", info, 152)[0]
    if (
        not 2 <= frames <= 60000
        or not math.isfinite(duration)
        or not 0 < duration <= 3600
        or not math.isfinite(fps)
        or fps <= 0
        or frames * len(rig["bones"]) * 28 > 128 * 1024 * 1024
        or abs(frames / fps - duration) > 0.001
    ):
        raise ValueError("Animation timing is inconsistent.")
    size, count, keys = chunks[b"ANIMKEYS"]
    if size != 32 or count != frames * len(rig["bones"]):
        raise ValueError("Animation frame keys are incomplete.")
    output = bytearray()
    for index, row in enumerate(struct.iter_unpack("<8f", keys)):
        x, y, z, qx, qy, qz, qw, _ = row
        if not all(math.isfinite(v) and abs(v) <= 1e6 for v in row):
            raise ValueError("Animation contains invalid transforms.")
        length = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
        if abs(length - 1) > 0.01:
            raise ValueError("Animation rotation is invalid.")
        # Undo the CUE4Parse ActorX key conversion, including the root W flip.
        bone_index = index % len(rig["bones"])
        root = rig["bones"][bone_index]["parent"] < 0
        translation = (x, -y, z)
        rotation = (qx / length, -qy / length, qz / length, (-qw if root else qw) / length)
        if bone_index in fallback:
            if (not _same_rotation(rotation, expected[bone_index]["rotation"])
                    or not _near(translation, expected[bone_index]["translation"])):
                raise ValueError("An undeclared animation track differs from its source reference pose.")
            translation = rig["bones"][bone_index]["translation"]
            rotation = rig["bones"][bone_index]["rotation"]
        output.extend(
            struct.pack(
                "<7f",
                *translation,
                *rotation,
            )
        )
    return frames, bytes(output)


def reference_layer_timing(frames, duration, layer_frames, layer_duration):
    """Align zero-based, endpoint-inclusive samples without changing their spacing."""
    if (any(type(count) is not int or not 2 <= count <= 60000 for count in (frames, layer_frames))
            or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 3600
                   for value in (duration, layer_duration))
            or not math.isclose(duration / (frames - 1), layer_duration / (layer_frames - 1), rel_tol=1e-6, abs_tol=0.0)):
        raise ValueError("Secondary clip sample spacing differs from the body animation.")
    return dict(mode="hold-last" if layer_frames < frames else "trim-end" if layer_frames > frames else "exact",
                body_frames=frames, layer_frames=layer_frames, body_duration=duration, layer_duration=layer_duration,
                held_frames=max(0, frames - layer_frames), trimmed_frames=max(0, layer_frames - frames))


def merge_reference_layer(base, layer, rig, bone_ids, *, duration=None, layer_duration=None):
    """Fill explicitly selected reference-pose tracks with a baked local layer.

    CUE4Parse expands reference-pose additive clips to absolute local transforms.
    Restrict replacement to unchanged reference bones so body motion and existing
    secondary motion cannot be overwritten or have an additive applied twice.
    Explicit durations permit zero-based endpoint alignment at the same sample
    spacing: trim a longer layer or hold its final pose without stretching time.
    """
    bones = rig["bones"]
    stride = len(bones) * 28
    if (
        not isinstance(bone_ids, list)
        or not bone_ids
        or len(set(bone_ids)) != len(bone_ids)
        or any(
            type(index) is not int or not 0 <= index < len(bones) for index in bone_ids
        )
        or not base or not layer or len(base) % stride or len(layer) % stride
    ):
        raise ValueError("Layer tracks do not match the base animation.")
    frames, layer_frames = len(base) // stride, len(layer) // stride
    if duration is not None or layer_duration is not None:
        reference_layer_timing(frames, duration, layer_frames, layer_duration)
    elif frames != layer_frames:
        raise ValueError("Layer tracks do not match the base animation.")
    output = bytearray(base)
    for frame in range(frames):
        for index in bone_ids:
            offset = frame * stride + index * 28
            row = struct.unpack_from("<7f", base, offset)
            bone = bones[index]
            if (
                base[offset : offset + 28] != base[index * 28 : (index + 1) * 28]
                or not _near(row[:3], bone["translation"])
                or not _same_rotation(row[3:], bone["rotation"])
            ):
                raise ValueError("A selected layer bone already has base motion.")
            source_offset = min(frame, layer_frames - 1) * stride + index * 28
            output[offset : offset + 28] = layer[source_offset : source_offset + 28]
    return bytes(output)


def apply_layer(base, rig, frames, duration, entry, directory):
    def read_file(key):
        file = (directory / entry[key]).resolve()
        if not file.is_relative_to(directory.resolve()):
            raise ValueError(
                "Layer resources must remain inside their manifest directory."
            )
        return file.read_bytes()

    data = read_file("file")
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("Layer source checksum differs.")
    metadata = json.loads(read_file("metadata_file"))
    sequences = [obj for obj in metadata if obj.get("Type") == "AnimSequence"]
    if len(sequences) != 1:
        raise ValueError("Expected one layer sequence.")
    properties = sequences[0]["Properties"]
    declared = {
        track["BoneTreeIndex"] for track in properties["TrackToSkeletonMapTable"]
    }
    layer_duration = properties.get("SequenceLength")
    if (
        properties.get("AdditiveAnimType") != "AAT_LocalSpaceBase"
        or properties.get("RefPoseType") != "ABPT_RefPose"
        or properties.get("NumFrames") != frames
        or type(layer_duration) not in (int, float)
        or not math.isfinite(layer_duration)
        or abs(layer_duration - duration) > 0.00001
        or not set(entry["bone_ids"]) <= declared
    ):
        raise ValueError(
            "Layer reference, timing or authored bone mask is incompatible."
        )
    layer_frames, layer = convert_clip(data, rig, duration)
    if layer_frames != frames:
        raise ValueError("Layer frame count differs from the base animation.")
    return merge_reference_layer(base, layer, rig, entry["bone_ids"])
