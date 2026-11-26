"""Policy for the settings of Wuthering Waves effect notifies, groups and mesh models.

Every setting an effect object carries is classified by name. *Handled* settings are read by
the extraction code (core facts and transforms). *Ignored* settings never change where, when or
whether a mesh shows: export bookkeeping, replay restarts (the viewer restarts a prop with its
clip anyway), actor-disable propagation, collision, level-of-detail and animation-update flags. *Blocking*
settings change placement, timing or visibility in a way the viewer does not reproduce, so they
refuse the prop unless they hold their neutral value. Any other setting is *unverified*: the prop
is still exported and the setting's name is listed on it in the extraction report. Transforms
(``Location``, ``Rotation``, ``Scale``) are reproduced. A notify's transform is constant (Unreal
``Location`` {X, Y, Z}, ``Rotation`` {Pitch, Yaw, Roll} in degrees, ``Scale`` {X, Y, Z}). A group's
or model's transform is a struct: ``Constant`` {X, Y, Z}, and per-axis ``Curve``/``Curve[1]``/
``Curve[2]`` only when ``bUseCurve`` is true; its Rotation axes are Roll, Pitch and Yaw, and curve
time is seconds since that level started. Offsets compose socket x notify x group x model and are
converted from Unreal space to asset space by negating Y.
"""

import numpy as np

NOTIFY, GROUP, MODEL = "notify", "group", "mesh model"
# Read by extraction code (core facts and transforms).
HANDLED = {
    NOTIFY: frozenset(("EffectDataAssetRef", "SocketName", "Attached", "FasterStop", "SyncEventTimeToEffectTime",
                       "SyncEventTimeContinuous", "EffectSlotName", "EndInnerAnimNotifyStatesOnStop",
                       "DetachWhenSkillEnd", "WhenSkillEnd", "Location", "Rotation", "Scale")),
    GROUP: frozenset(("EffectData", "StartTime", "LoopTime", "EndTime", "ContinuousLoop", "Location", "Rotation", "Scale")),
    MODEL: frozenset(("SkeletalMeshRef", "AnimationRef", "Looping", "StartTime", "LoopTime", "EndTime",
                      "ContinuousLoop", "Location", "Rotation", "Scale")),
}
# Never change where, when or whether a mesh shows.
IGNORED = frozenset(("exportIndex", "CurrentTimeLength", "NativeClass", "bRestartWithReplay",
                     "NeedDisableWithActor", "EnableCollision", "bForceLod0", "bStopAnimWhenHidden"))
# Settings that change placement, timing or visibility, with the neutral value at which they change nothing.
BLOCKING = {
    NOTIFY: {"NotRealAttach": False, "AttachLocationOnly": False, "AttachLocationTime": 0, "PlayNeedTags": []},
    GROUP: {},
    MODEL: {"HideFrames": 0},
}
# Stands for a notify object that cannot be read; it always blocks.
UNREADABLE = "(unreadable)"
BAD_OFFSET = "The effect offset is unreadable."
BAD_TRANSFORM = "The effect transform is unsupported."
BAD_INTERP = "The effect curve interpolation is unsupported."
_STRUCT_KEYS = frozenset(("Constant", "bUseCurve", "Curve", "Curve[1]", "Curve[2]"))
_CURVES = ("Curve", "Curve[1]", "Curve[2]")
_INTERP = ("RCIM_Linear", "RCIM_Constant")
_MAX_LOCATION = 1e5
_MAX_SCALE = 1e3
_MIRROR = np.diag([1.0, -1.0, 1.0, 1.0])


def _neutral(value, neutral):
    """Whether a blocking setting's ``value`` is its neutral value (``neutral``); a flag never
    equals a number."""
    return value is None or (value == neutral and isinstance(value, bool) == isinstance(neutral, bool))


def _is_number(value):
    return type(value) in (int, float) and bool(np.isfinite(value))


def classify(level, properties):
    """(blocking names, unverified names), both sorted, of one level's settings dict."""
    if not isinstance(properties, dict):
        return [UNREADABLE], []
    blocking, unverified = [], []
    for name, value in properties.items():
        if name == UNREADABLE:
            blocking.append(name)
        elif name in HANDLED[level] or name in IGNORED:
            continue
        elif name in BLOCKING[level]:
            if not _neutral(value, BLOCKING[level][name]):
                blocking.append(name)
        else:
            unverified.append(name)
    return sorted(blocking), sorted(unverified)


def unsupported(level, names):
    """The report message for blocking settings ``names`` of one level."""
    return f"The effect {level} has settings the viewer does not support: {', '.join(names)}."


def _axes(value, names, defaults, error):
    """(3,) floats read from a struct of axis ``names``; a missing struct or axis takes its default."""
    if value is None:
        return np.asarray(defaults, dtype=float)
    if not isinstance(value, dict):
        raise ValueError(error)
    axes = []
    for name, default in zip(names, defaults):
        number = value.get(name)
        if number is None:
            number = default
        if not _is_number(number):
            raise ValueError(error)
        axes.append(float(number))
    return np.asarray(axes, dtype=float)


def _ue_matrix(location, pitch, yaw, roll, scale):
    """Unreal-space column-vector matrices ``translate(location) @ R @ diag(scale)`` with the rotation
    of (pitch, yaw, roll) degrees. Angles and scale may carry a leading axis of samples."""
    pitch, yaw, roll = (np.radians(np.asarray(angle, dtype=float)) for angle in (pitch, yaw, roll))
    sp, cp, sy, cy, sr, cr = np.sin(pitch), np.cos(pitch), np.sin(yaw), np.cos(yaw), np.sin(roll), np.cos(roll)
    # Rows are the rotated X, Y and Z axes; the column-vector rotation is their transpose.
    rows = np.stack([
        np.stack([cp * cy, cp * sy, sp], -1),
        np.stack([sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp], -1),
        np.stack([-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp], -1),
    ], -2)
    rotation = np.swapaxes(rows, -1, -2)
    matrix = np.zeros(rotation.shape[:-2] + (4, 4))
    matrix[..., :3, :3] = rotation * np.asarray(scale, dtype=float)[..., None, :]
    matrix[..., :3, 3] = location
    matrix[..., 3, 3] = 1
    return matrix


def _to_asset(matrix):
    """The Unreal-space matrices (..., 4, 4) in asset space, which negates Y."""
    return _MIRROR @ matrix @ _MIRROR


def notify_offset(properties):
    """The asset-space 4x4 offset of an effect notify's constant transform (``settings`` dict)."""
    if not isinstance(properties, dict):
        raise ValueError(BAD_OFFSET)
    location = _axes(properties.get("Location"), ("X", "Y", "Z"), (0, 0, 0), BAD_OFFSET)
    pitch, yaw, roll = _axes(properties.get("Rotation"), ("Pitch", "Yaw", "Roll"), (0, 0, 0), BAD_OFFSET)
    scale = _axes(properties.get("Scale"), ("X", "Y", "Z"), (1, 1, 1), BAD_OFFSET)
    if np.abs(location).max() > _MAX_LOCATION or ((scale <= 0) | (scale > _MAX_SCALE)).any():
        raise ValueError(BAD_OFFSET)
    return _to_asset(_ue_matrix(location, pitch, yaw, roll, scale))


def _curve(curve):
    """Sampler of one axis curve's values at moments in seconds: linear or constant keys, held
    before the first and after the last key."""
    data = curve.get("EditorCurveData") if isinstance(curve, dict) else None
    keys = data.get("Keys") if isinstance(data, dict) else None
    if not isinstance(keys, list) or not keys or not all(isinstance(key, dict) for key in keys):
        raise ValueError(BAD_TRANSFORM)
    times, values, linear = [], [], []
    for key in keys:
        time, value = key.get("Time"), key.get("Value")
        if not (_is_number(time) and _is_number(value)):
            raise ValueError(BAD_TRANSFORM)
        if key.get("InterpMode") not in _INTERP:
            raise ValueError(BAD_INTERP)
        times.append(float(time))
        values.append(float(value))
        linear.append(key["InterpMode"] == "RCIM_Linear")
    if any(later <= earlier for earlier, later in zip(times, times[1:])):
        raise ValueError(BAD_TRANSFORM)
    times, values, linear = np.asarray(times), np.asarray(values), np.asarray(linear)
    last = len(times) - 1

    def sample(moments):
        index = np.clip(np.searchsorted(times, moments, side="right") - 1, 0, last)
        after = np.minimum(index + 1, last)
        span = times[after] - times[index]
        fraction = np.where(span > 0, (moments - times[index]) / np.where(span > 0, span, 1), 0)
        blended = values[index] + (values[after] - values[index]) * np.clip(fraction, 0, 1)
        return np.where(linear[index], blended, values[index])

    return sample


def _transform(value, defaults):
    """Sampler of one group or model transform struct, (moments) -> (count, 3); a missing struct
    holds ``defaults``."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or not set(value) <= _STRUCT_KEYS:
        raise ValueError(BAD_TRANSFORM)
    use_curve = value.get("bUseCurve")
    if use_curve is not None and not isinstance(use_curve, bool):
        raise ValueError(BAD_TRANSFORM)
    constant = _axes(value.get("Constant"), ("X", "Y", "Z"), defaults, BAD_TRANSFORM)
    # Curves only drive their axis when bUseCurve is true; otherwise they are unused.
    curves = [_curve(value[name]) if use_curve and value.get(name) is not None else None for name in _CURVES]

    def sample(moments):
        out = np.tile(constant, (len(moments), 1))
        for axis, curve in enumerate(curves):
            if curve is not None:
                out[:, axis] = curve(moments)
        return out

    return sample


def level_transform(properties):
    """Sampler (moments in seconds since the level started) -> (count, 4, 4) asset-space matrices of
    a group's or mesh model's transform, or None when the level has none of Location, Rotation
    or Scale. A ValueError when the transform is unsupported or out of range."""
    if not isinstance(properties, dict):
        raise ValueError(BAD_TRANSFORM)
    if not any(name in properties for name in ("Location", "Rotation", "Scale")):
        return None
    location_at, rotation_at, scale_at = (_transform(properties.get(name), defaults) for name, defaults in
                                 (("Location", (0, 0, 0)), ("Rotation", (0, 0, 0)), ("Scale", (1, 1, 1))))

    def sample(moments):
        moments = np.asarray(moments, dtype=float)
        location, rotation, scale = location_at(moments), rotation_at(moments), scale_at(moments)
        if np.abs(location).max(initial=0) > _MAX_LOCATION or ((scale < 0) | (scale > _MAX_SCALE)).any():
            raise ValueError(BAD_TRANSFORM)
        # Rotation axes are Roll, Pitch and Yaw.
        return _to_asset(_ue_matrix(location, rotation[:, 1], rotation[:, 2], rotation[:, 0], scale))

    sample(np.zeros(1))
    return sample


def combined(notify_properties, group_properties, model_properties, delay):
    """Sampler (moments in seconds since the mesh started playing) -> (count, 4, 4) offsets of notify x
    group x model, the group sampled ``delay`` seconds after its start; None when all are identity."""
    offset = notify_offset(notify_properties)
    group, model = level_transform(group_properties), level_transform(model_properties)
    if group is None and model is None and np.abs(offset - np.eye(4)).max() <= 1e-9:
        return None

    def sample(moments):
        moments = np.asarray(moments, dtype=float)
        result = np.broadcast_to(offset, (len(moments), 4, 4)).copy()
        if group is not None:
            result = result @ group(moments + delay)
        if model is not None:
            result = result @ model(moments)
        return result

    return sample
