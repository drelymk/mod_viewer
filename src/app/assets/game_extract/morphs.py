"""Transfer native morphs only through already verified captured vertices."""

import hashlib
import math
import struct


def sample_curves(sequence, names):
    if not names:
        return None
    properties = sequence.get("Properties", {})
    frames, duration = properties.get("NumFrames"), properties.get("SequenceLength")
    curves = sequence.get("CompressedCurveData", {}).get("FloatCurves", [])
    labels = sequence.get("CompressedCurveNames", [])
    if not curves:
        return None
    if (len(labels) != len(curves) or type(frames) is not int or not 2 <= frames <= 60000
            or type(duration) not in (int, float) or not 0 < duration <= 3600):
        raise ValueError("Morph curve timing is invalid.")
    sampled, used = [], set()
    for label, curve in zip(labels, curves):
        name = label.get("DisplayName")
        if name not in names:
            continue
        keys = curve.get("FloatCurve", {}).get("Keys", [])
        if not keys or name in used:
            raise ValueError("Morph curve identity is ambiguous.")
        used.add(name)
        for index, key in enumerate(keys):
            if (any(type(key.get(field)) not in (int, float) or not math.isfinite(key[field])
                    or abs(key[field]) > 1e6 for field in ("Time", "Value", "ArriveTangent", "LeaveTangent"))
                    or key["Time"] < 0 or key["Time"] > duration + 1e-4
                    or (index and key["Time"] <= keys[index - 1]["Time"])
                    or key.get("InterpMode") not in ("RCIM_Linear", "RCIM_Constant", "RCIM_Cubic")
                    or key.get("TangentWeightMode") != "RCTWM_WeightedNone"):
                raise ValueError("Unsupported morph curve keys.")
        if not any(key["Value"] or key["ArriveTangent"] or key["LeaveTangent"] for key in keys):
            continue
        values, index = [], 0
        for frame in range(frames):
            time = frame * duration / (frames - 1)
            while index + 1 < len(keys) and keys[index + 1]["Time"] <= time:
                index += 1
            left = keys[index]
            value = left["Value"]
            if time > left["Time"] and index + 1 < len(keys):
                right = keys[index + 1]
                span = right["Time"] - left["Time"]
                alpha = (time - left["Time"]) / span
                if left["InterpMode"] == "RCIM_Linear":
                    value += (right["Value"] - value) * alpha
                elif left["InterpMode"] == "RCIM_Cubic":
                    value = ((2 * alpha ** 3 - 3 * alpha ** 2 + 1) * value
                             + (alpha ** 3 - 2 * alpha ** 2 + alpha) * span * left["LeaveTangent"]
                             + (-2 * alpha ** 3 + 3 * alpha ** 2) * right["Value"]
                             + (alpha ** 3 - alpha ** 2) * span * right["ArriveTangent"])
            values.append(value)
        sampled.append((name, values))
    if not sampled:
        return None
    data = bytearray(frames * len(sampled) * 4)
    for index, (_, values) in enumerate(sampled):
        for frame, value in enumerate(values):
            struct.pack_into("<f", data, (frame * len(sampled) + index) * 4, value)
    return dict(names=[name for name, _ in sampled], data=bytes(data))


def export_morphs(mesh, captured, directory):
    if not mesh.get("morphs"):
        return None
    data, components = bytearray(), []
    for component in captured:
        resource = component["resources"]
        kind = {"R16_UINT": "H", "R32_UINT": "I"}[resource["fmt"].index_format.removeprefix("DXGI_FORMAT_")]
        indices = [row[0] for row in struct.iter_unpack("<" + kind, resource["ib"])]
        first, count = component["first_index"], component["index_count"]
        correspondence = dict(zip(indices, mesh["indices"][first:first + count]))
        inverse = {}
        for local, native in correspondence.items():
            inverse.setdefault(native, []).append(local)
        targets = []
        for name, rows in mesh.get("morphs", {}).items():
            offset = len(data)
            for row in rows:
                for local in inverse.get(row[-1], ()):
                    data.extend(struct.pack("<I6f", local, *row[:6]))
                    if len(data) > 128 * 1024 * 1024:
                        raise ValueError("Captured morph data exceeds its size limit.")
            if len(data) > offset:
                targets.append(dict(name=name, offset=offset, count=(len(data) - offset) // 28))
        if targets:
            components.append(dict(vertex_sha256=component["vertex_sha256"], index_sha256=component["index_sha256"],
                                   vertex_count=len(correspondence), targets=targets))
    if not components:
        return None
    digest = hashlib.sha256(data).hexdigest()
    filename = "morphs-" + digest[:24] + ".bin"
    file = directory / filename
    if file.exists() and (file.stat().st_size != len(data) or file.read_bytes() != data):
        raise ValueError("Morph output conflicts with a preserved resource.")
    file.write_bytes(data)
    return dict(file=filename, sha256=digest, components=components)
