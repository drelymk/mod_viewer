"""Offline ActorX conversion and verified reference-pose layer composition."""

import hashlib
import json
import math
import struct


def _near(left, right):
    # Float32 reference poses of distant bones differ by magnitude-scaled rounding.
    return all(abs(a - b) <= 0.001 + 1e-4 * max(abs(a), abs(b)) for a, b in zip(left, right))


def convert_clip(data, rig, duration):
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
    for index, bone in enumerate(rig["bones"]):
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
        elif (
            min(
                sum((a - sign * b) ** 2 for a, b in zip(rotation, bone["rotation"]))
                for sign in (-1, 1)
            )
            > 0.00001
        ):
            field = "rotation"
        else:
            continue
        mismatches.append((field, bone["name"]))
    if mismatches:
        field, name = mismatches[0]
        raise ValueError(
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
        root = rig["bones"][index % len(rig["bones"])]["parent"] < 0
        output.extend(
            struct.pack(
                "<7f",
                x,
                -y,
                z,
                qx / length,
                -qy / length,
                qz / length,
                (-qw if root else qw) / length,
            )
        )
    return frames, bytes(output)


def merge_reference_layer(base, layer, rig, bone_ids):
    """Fill explicitly selected reference-pose tracks with a baked local layer.

    CUE4Parse expands reference-pose additive clips to absolute local transforms.
    Restrict replacement to unchanged reference bones so body motion and existing
    secondary motion cannot be overwritten or have an additive applied twice.
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
        or len(base) != len(layer)
        or len(base) % stride
    ):
        raise ValueError("Layer tracks do not match the base animation.")
    output = bytearray(base)
    for frame in range(len(base) // stride):
        for index in bone_ids:
            offset = frame * stride + index * 28
            row = struct.unpack_from("<7f", base, offset)
            bone = bones[index]
            if (
                base[offset : offset + 28] != base[index * 28 : (index + 1) * 28]
                or not _near(row[:3], bone["translation"])
                or min(
                    sum((a - sign * b) ** 2 for a, b in zip(row[3:], bone["rotation"]))
                    for sign in (-1, 1)
                )
                > 0.00001
            ):
                raise ValueError("A selected layer bone already has base motion.")
            output[offset : offset + 28] = layer[offset : offset + 28]
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
