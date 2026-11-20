"""Convert native ZZZ ACL, dense, streamed and constant channels to bounded TQS clips."""

import math
from pathlib import Path

import numpy as np


def dimension(binding):
    return {1: 3, 2: 4, 3: 3, 4: 3}.get(binding["attribute"], 1) if binding["type"] == "Transform" else 1


def _sample(times, values, targets, *, quaternion=False, tolerance=.001):
    times, values = np.asarray(times, dtype=float), np.asarray(values, dtype=float)
    if (times.ndim != 1 or len(times) != len(values) or not len(times) or not np.isfinite(times).all()
            or not np.isfinite(values).all() or np.any(np.diff(times) <= 0)):
        raise ValueError("Native animation samples are invalid.")
    if not quaternion:
        return np.column_stack([np.interp(targets, times, values[:, i]) for i in range(values.shape[1])])
    lengths = np.linalg.norm(values, axis=1)
    if np.any(np.abs(lengths - 1) > tolerance):
        raise ValueError("Native animation rotations are not normalized.")
    values = values / lengths[:, None]
    upper = np.clip(np.searchsorted(times, targets, side="right"), 1, max(1, len(times) - 1))
    if len(times) == 1:
        return np.broadcast_to(values[0], (len(targets), 4)).copy()
    lower = upper - 1
    alpha = np.clip((targets - times[lower]) / (times[upper] - times[lower]), 0, 1)
    left, right = values[lower], values[upper].copy()
    dot = np.sum(left * right, axis=1)
    right[dot < 0] *= -1
    dot = np.abs(dot).clip(0, 1)
    theta = np.arccos(dot)
    sine = np.sin(theta)
    curved = sine > 1e-5
    a, b = 1 - alpha, alpha.copy()
    a[curved] = np.sin((1 - alpha[curved]) * theta[curved]) / sine[curved]
    b[curved] = np.sin(alpha[curved] * theta[curved]) / sine[curved]
    result = left * a[:, None] + right * b[:, None]
    return result / np.linalg.norm(result, axis=1)[:, None]


def sample_channels(metadata, work, targets, reserved_bytes, quaternion_tolerance=.001):
    """Sample every native channel family onto the target frame times.

    Rotation samples must be unit length within ``quaternion_tolerance`` before
    they are normalized and interpolated.
    """
    frames = len(targets)
    times = metadata["times"]
    acl_count = metadata["acl_count"]
    filename = metadata["file"]
    file = (Path(work) / filename).resolve()
    if not file.is_relative_to(Path(work).resolve()) or file.stat().st_size != len(times) * acl_count * 4:
        raise ValueError("The native ACL sample file is incomplete.")
    dense = metadata["dense"]
    streamed_count, dense_count = metadata["streamed_count"], dense["count"]
    total = acl_count + streamed_count + dense_count + len(metadata["constant"])
    if total > 65536 or total < 1 or acl_count < 0:
        raise ValueError("The native curve table exceeds its limits.")
    # Bound intermediate Float64 sampling as well as the published TQS data.
    # Constant/scalar channels can vastly outnumber the skeleton's transforms.
    if frames * total * 8 + file.stat().st_size + reserved_bytes > 128 * 1024 * 1024:
        raise ValueError("Native animation exceeds the sampling memory budget.")
    values = np.fromfile(file, dtype="<f4").reshape(len(times), acl_count)
    sampled = np.empty((frames, total), dtype=float)
    if acl_count:
        sampled[:, :acl_count] = _sample(times, values, targets)
    cursor = 0
    for binding in metadata["bindings"]:
        width = dimension(binding)
        if cursor + width > acl_count:
            break
        if binding["type"] == "Transform" and binding["attribute"] == 2:
            sampled[:, cursor:cursor + width] = _sample(times, values[:, cursor:cursor + width], targets, quaternion=True,
                                                         tolerance=quaternion_tolerance)
        cursor += width
    if dense_count:
        if not 0 < dense["rate"] <= 1000:
            raise ValueError("The native dense curve timing is invalid.")
        dense_values = np.asarray(dense["values"]).reshape(dense["frames"], dense_count)
        dense_times = dense["start"] + np.arange(dense["frames"]) / dense["rate"]
        sampled[:, acl_count + streamed_count:acl_count + streamed_count + dense_count] = _sample(dense_times, dense_values, targets)
        cursor = 0
        dense_start = acl_count + streamed_count
        for binding in metadata["bindings"]:
            width = dimension(binding)
            if (binding["type"] == "Transform" and binding["attribute"] == 2
                    and dense_start <= cursor and cursor + width <= dense_start + dense_count):
                offset = cursor - dense_start
                sampled[:, cursor:cursor + width] = _sample(dense_times, dense_values[:, offset:offset + width], targets,
                                                         quaternion=True, tolerance=quaternion_tolerance)
            cursor += width
    curves = [[] for _ in range(streamed_count)]
    for frame in metadata.get("streamed", []):
        for key in frame["keys"]:
            if type(key["index"]) is not int or not 0 <= key["index"] < streamed_count:
                raise ValueError("The native streamed curve index is invalid.")
            if math.isfinite(frame["time"]) and abs(frame["time"]) < 1e10:
                curves[key["index"]].append((frame["time"], key["value"], key["incoming"], key["outgoing"]))
    for index, curve in enumerate(curves):
        if not curve:
            raise ValueError("The native streamed curve is incomplete.")
        keys = np.asarray(curve, dtype=float)
        if not np.isfinite(keys[:, :2]).all() or np.isnan(keys).any() or np.any(np.diff(keys[:, 0]) <= 0):
            raise ValueError("The native streamed curve is invalid.")
        result = np.interp(targets, keys[:, 0], keys[:, 1])
        for left, right in zip(keys, keys[1:]):
            mask = (targets > left[0]) & (targets < right[0])
            span = right[0] - left[0]
            t = (targets[mask] - left[0]) / span
            if not np.isfinite(left[3]) or not np.isfinite(right[2]):
                result[mask] = left[1]
                continue
            result[mask] = ((2*t**3 - 3*t*t + 1)*left[1] + (t**3 - 2*t*t + t)*span*left[3]
                            + (-2*t**3 + 3*t*t)*right[1] + (t**3 - t*t)*span*right[2])
        sampled[:, acl_count + index] = result
    if metadata["constant"]:
        sampled[:, total - len(metadata["constant"]):] = metadata["constant"]
    return sampled


def convert(metadata, work, rig, avatar, morphs=None):
    start, stop = metadata["start"], metadata["stop"]
    duration = stop - start
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, stop)) or not 0 < duration <= 3600:
        raise ValueError("Native clip timing is unsupported.")
    frames = max(2, round(duration * 60) + 1)
    if frames > 60000 or frames * len(rig["bones"]) * 40 > 128 * 1024 * 1024:
        raise ValueError("Native animation exceeds playback limits.")
    targets = np.linspace(start, stop, frames)
    sampled = sample_channels(metadata, work, targets, frames * len(rig["bones"]) * 40)
    total = sampled.shape[1]
    poses = np.array([[*b["translation"], *b["rotation"], *b["scale"]] for b in rig["bones"]], dtype=float)
    output = np.broadcast_to(poses, (frames, *poses.shape)).copy()
    hash_to_bone = {bone["hash"]: index + 1 for index, bone in enumerate(avatar["bones"])}
    cursor, written, unsupported = 0, set(), 0
    morph_columns = {}
    for binding in metadata["bindings"]:
        width = dimension(binding)
        if cursor + width > total:
            raise ValueError("The native curve bindings are incomplete.")
        channel = sampled[:, cursor:cursor + width]
        cursor += width
        if binding["type"] != "Transform":
            target = (morphs or {}).get((binding["path"], binding["attribute"]))
            if (target is not None and binding["type"] == "SkinnedMeshRenderer"
                    and binding["custom"] == 20 and not binding["pointer"]):
                name, full_weight = target
                if name in morph_columns:
                    raise ValueError("The native clip has duplicate morph tracks.")
                morph_columns[name] = channel[:, 0] / full_weight
            else:
                unsupported += 1
            continue
        bone = hash_to_bone.get(binding["path"])
        attribute = binding["attribute"]
        if bone is None or attribute not in (1, 2, 3) or binding["pointer"]:
            raise ValueError("The native clip has unsupported transform bindings.")
        identity = bone, attribute
        if identity in written:
            raise ValueError("The native clip has duplicate transform tracks.")
        written.add(identity)
        offset = {1: 0, 2: 3, 3: 7}[attribute]
        # Interpolating sampled ACL quaternion components follows shortest-arc
        # continuity; normalize only rotations, preserving authored float weights.
        if attribute == 2:
            lengths = np.linalg.norm(channel, axis=1)
            if np.any(lengths < 1e-6):
                raise ValueError("The native clip contains a zero quaternion.")
            channel = channel / lengths[:, None]
        output[:, bone, offset:offset + width] = channel
    if cursor != total or not written or not np.isfinite(output).all() or np.any(np.abs(output) > 1e6):
        raise ValueError("The native animation transform table is invalid.")
    morph = None
    binary = output.astype("<f4").tobytes()
    if morph_columns:
        clamp = metadata.get("blend_shape_clamp")
        if type(clamp) is not bool:
            raise ValueError("The native blend-shape weight policy is unavailable.")
        weights = np.column_stack(list(morph_columns.values())).astype("<f4")
        if not np.isfinite(weights).all() or np.any(np.abs(weights) > 1e6):
            raise ValueError("The native clip has invalid morph weights.")
        if clamp:
            np.clip(weights, 0, 1, out=weights)
        morph = dict(names=list(morph_columns), weights=weights, clamp=clamp)
        binary += weights.tobytes()
    if len(binary) > 128 * 1024 * 1024:
        raise ValueError("Native animation exceeds playback limits.")
    keyed = sorted({bone for bone, _attribute in written})
    return binary, frames, duration, unsupported, morph, keyed
