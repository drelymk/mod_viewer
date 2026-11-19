"""Synthetic mirrored Humanoid definitions for solver and extraction tests."""

import math

import numpy as np

from app.assets.game_extract import mecanim
from app.assets.game_extract.mecanim import BONE

IDENTITY = [0.0, 0.0, 0.0, 1.0]


def mirror(q):
    """Reflection across the YZ plane, expressed on a rotation (an automorphism)."""
    return [q[0], -q[1], -q[2], q[3]]


def axis_angle(axis, angle):
    axis = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    return [*(axis * math.sin(angle / 2)), math.cos(angle / 2)]


def humanoid():
    """A small left/right mirrored humanoid with generic axes on every mapped bone."""
    nodes = [("root", -1, (0, 0, 0)), ("Hips", 0, (0, 1, 0)), ("Spine", 1, (0, .1, 0)), ("Chest", 2, (0, .15, 0)),
             ("Neck", 3, (0, .2, 0)), ("Head", 4, (0, .1, 0))]
    for side, sign in (("Left", -1), ("Right", 1)):
        start = len(nodes)
        nodes += [(side + "Shoulder", 3, (.05 * sign, .15, 0)), (side + "UpperArm", start, (.1 * sign, 0, 0)),
                  (side + "LowerArm", start + 1, (.25 * sign, 0, 0)), (side + "Hand", start + 2, (.25 * sign, 0, 0))]
    for side, sign in (("Left", -1), ("Right", 1)):
        start = len(nodes)
        nodes += [(side + "UpperLeg", 1, (.1 * sign, -.05, 0)), (side + "LowerLeg", start, (0, -.45, 0)),
                  (side + "Foot", start + 1, (0, -.42, 0)), (side + "Toes", start + 2, (0, -.08, .12))]
    rng = np.random.default_rng(7)
    left_axes = {}
    axes, axes_id = [], []
    for name, _parent, _t in nodes:
        if name == "root":
            axes_id.append(-1)
            continue
        if name.startswith("Right"):
            # Mirroring keeps +x pointing at the child only with a half turn about z,
            # which flips the twist and z axis signs (as in Unity's right-side axes).
            pre, post, sgn, low, high = left_axes["Left" + name[5:]]
            half_turn = np.array([0.0, 0.0, 1.0, 0.0])
            pre = list(mecanim.qmul(np.array(mirror(pre)), half_turn))
            post = list(mecanim.qmul(np.array(mirror(post)), half_turn))
            sgn = [-sgn[0], sgn[1], -sgn[2]]
        elif name.startswith("Left"):
            pre = list(np.asarray(axis_angle(rng.normal(size=3), rng.uniform(.2, 1.2))))
            post = list(np.asarray(axis_angle(rng.normal(size=3), rng.uniform(.2, 1.2))))
            sgn = [float(value) for value in rng.choice([-1.0, 1.0], 3)]
            low, high = list(-rng.uniform(.2, 1.4, 3)), list(rng.uniform(.2, 1.4, 3))
            left_axes[name] = pre, post, sgn, low, high
        else:
            # Centre bone axes point up (in the mirror plane); unmoved by muscles here.
            pre = post = axis_angle((0, 0, 1), math.pi / 2)
            sgn, low, high = [1.0, 1.0, 1.0], [-.6, -.6, -.6], [.6, .6, .6]
        axes_id.append(len(axes))
        axes.append(dict(pre_q=pre, post_q=post, sgn=sgn, min=low, max=high, length=.1, type=1))
    index = {name: number for number, (name, _p, _t) in enumerate(nodes)}
    mass = [0.0] * len(mecanim.BONES)
    for name in index:
        if name in BONE:
            mass[BONE[name]] = .05
    return dict(
        skeleton=dict(parent=[parent for _n, parent, _t in nodes], axes_id=axes_id), axes=axes,
        pose=[dict(translation=list(t), rotation=IDENTITY, scale=[1, 1, 1]) for _n, _p, t in nodes],
        bone_index=[index.get(name, -1) for name in mecanim.BONES], left_hand=[], right_hand=[], mass=mass,
        scale=1.1, arm_twist=.5, forearm_twist=.5, upperleg_twist=.5, leg_twist=.5, has_tdof=False,
        skeleton_index=list(range(len(nodes))), root=dict(translation=[0, 1, 0], rotation=IDENTITY, scale=[1, 1, 1]))
