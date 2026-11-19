"""Unity Mecanim Humanoid muscle curves to human-skeleton transforms.

Genshin Impact stores playable-character body motion only as Humanoid muscle
curves. This reproduces Mecanim's muscle-to-rotation mapping and body (centre of
mass) placement from an Avatar's exported Humanoid definition. Quaternions are
``(x, y, z, w)`` in Unity/Avatar space; all arrays are vectorized over frames.
Twist distribution, translation DoF and IK passes are not applied.
"""

import math

import numpy as np

BONES = ("Hips", "LeftUpperLeg", "RightUpperLeg", "LeftLowerLeg", "RightLowerLeg", "LeftFoot", "RightFoot",
         "Spine", "Chest", "UpperChest", "Neck", "Head", "LeftShoulder", "RightShoulder", "LeftUpperArm",
         "RightUpperArm", "LeftLowerArm", "RightLowerArm", "LeftHand", "RightHand", "LeftToes", "RightToes",
         "LeftEye", "RightEye", "Jaw")
BONE = {name: index for index, name in enumerate(BONES)}

# Animator binding attributes (customType 8).
MOTION, ROOT, GOALS, MUSCLES, FINGERS, TDOF, LAST = 0, 7, 14, 42, 97, 137, 200
LIMB_GOALS = ("LeftFoot", "RightFoot", "LeftHand", "RightHand")

# Body DoF offsets (from MUSCLES) driving each bone's (x = twist, y, z) axes; None = no DoF.
# The foot's twist muscle drives its y axis: its x (roll) limit range is zero.
_BODY_DOF = {
    "Spine": (2, 1, 0), "Chest": (5, 4, 3), "UpperChest": (8, 7, 6), "Neck": (11, 10, 9), "Head": (14, 13, 12),
    "LeftEye": (None, 16, 15), "RightEye": (None, 18, 17), "Jaw": (None, 20, 19),
    "LeftUpperLeg": (23, 22, 21), "LeftLowerLeg": (25, None, 24), "LeftFoot": (None, 27, 26), "LeftToes": (None, None, 28),
    "RightUpperLeg": (31, 30, 29), "RightLowerLeg": (33, None, 32), "RightFoot": (None, 35, 34), "RightToes": (None, None, 36),
    "LeftShoulder": (None, 38, 37), "LeftUpperArm": (41, 40, 39), "LeftLowerArm": (43, None, 42), "LeftHand": (None, 45, 44),
    "RightShoulder": (None, 47, 46), "RightUpperArm": (50, 49, 48), "RightLowerArm": (52, None, 51), "RightHand": (None, 54, 53),
}
# Hand bones are thumb, index, middle, ring, little x proximal, intermediate, distal.
# Finger DoF per finger: 1-stretched (proximal z), spread (proximal y), 2- and 3-stretched.
_FINGER_DOF = tuple(dof for finger in range(5) for dof in (
    (None, 4 * finger + 1, 4 * finger), (None, None, 4 * finger + 2), (None, None, 4 * finger + 3)))


def qmul(a, b):
    ax, ay, az, aw = np.moveaxis(a, -1, 0)
    bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz], axis=-1)


def qconj(q):
    return q * np.array([-1.0, -1.0, -1.0, 1.0])


def qnormalize(q):
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def qrot(q, v):
    u, w = q[..., :3], q[..., 3:]
    t = 2 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def qfrom_basis(x, y, z):
    """Rotation whose columns are the orthonormal axes ``x``, ``y``, ``z`` (arrays of shape (..., 3))."""
    m00, m10, m20 = np.moveaxis(x, -1, 0)
    m01, m11, m21 = np.moveaxis(y, -1, 0)
    m02, m12, m22 = np.moveaxis(z, -1, 0)
    candidates = np.stack([1 + m00 - m11 - m22, 1 - m00 + m11 - m22, 1 - m00 - m11 + m22, 1 + m00 + m11 + m22])
    axis = np.argmax(candidates, axis=0)
    s = np.sqrt(np.maximum(np.take_along_axis(candidates, axis[None], 0)[0], 1e-12)) * 2
    q = np.stack([
        np.choose(axis, [s / 4, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s]),
        np.choose(axis, [(m01 + m10) / s, s / 4, (m12 + m21) / s, (m02 - m20) / s]),
        np.choose(axis, [(m02 + m20) / s, (m12 + m21) / s, s / 4, (m10 - m01) / s]),
        np.choose(axis, [(m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, s / 4]),
    ], axis=-1)
    return qnormalize(q)


def zyroll(x, y, z):
    """Swing (y, z) followed by twist x; every component is tan(angle / 2)."""
    return qnormalize(np.stack([x, y + x * z, z - x * y, np.ones_like(x)], axis=-1))


def _vector(value, size, label):
    array = np.asarray(value, dtype=float)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError(f"The Humanoid {label} is invalid.")
    return array


def _rotation(value, label):
    q = _vector(value, 4, label)
    if abs(np.linalg.norm(q) - 1) > 1e-3:
        raise ValueError(f"The Humanoid {label} is not a unit rotation.")
    return q / np.linalg.norm(q)


def _indices(value, count, length, label):
    if (not isinstance(value, list) or (length is not None and len(value) != length)
            or any(type(item) is not int or not -1 <= item < count for item in value)):
        raise ValueError(f"The Humanoid {label} is invalid.")
    return value


class HumanSolver:
    """Validated Humanoid definition from the native Avatar ``human`` export."""

    def __init__(self, human):
        if not isinstance(human, dict):
            raise ValueError("The Avatar has no Humanoid definition.")
        skeleton = human["skeleton"]
        parents, axes_ids = skeleton["parent"], skeleton["axes_id"]
        count = len(parents) if isinstance(parents, list) else 0
        if not 1 <= count <= 4096 or not isinstance(axes_ids, list) or len(axes_ids) != count:
            raise ValueError("The Humanoid skeleton is invalid.")
        axes = human["axes"]
        if not isinstance(axes, list) or len(axes) > count:
            raise ValueError("The Humanoid axes are invalid.")
        _indices(parents, count, count, "hierarchy")
        _indices(axes_ids, len(axes), count, "axes table")
        if any(parent >= index for index, parent in enumerate(parents)):
            raise ValueError("The Humanoid hierarchy is not topologically ordered.")
        self.parents, self.count = parents, count
        self.axes = []
        for item in axes:
            if item.get("type") != 1:
                raise ValueError("Only ZYRoll Humanoid axes are supported.")
            low, high = _vector(item["min"], 3, "limit"), _vector(item["max"], 3, "limit")
            sign = _vector(item["sgn"], 3, "axis sign")
            length = float(item["length"])
            if (np.any(low > 0) or np.any(high < 0) or np.any(np.abs(np.concatenate([low, high])) > math.pi)
                    or not np.all(np.isin(sign, (-1.0, 1.0))) or not math.isfinite(length) or length < 0):
                raise ValueError("The Humanoid axis limits are invalid.")
            self.axes.append(dict(pre=_rotation(item["pre_q"], "axis rotation"), post=_rotation(item["post_q"], "axis rotation"),
                                  low=low, high=high, sign=sign, length=length))
        self.axes_ids = axes_ids
        pose = human["pose"]
        if not isinstance(pose, list) or len(pose) != count:
            raise ValueError("The Humanoid reference pose is invalid.")
        self.translations = np.array([_vector(item["translation"], 3, "reference pose") for item in pose])
        self.rotations = np.array([_rotation(item["rotation"], "reference pose") for item in pose])
        self.bones = _indices(human["bone_index"], count, len(BONES), "bone map")
        self.hands = [_indices(human[key], count, None, "hand map") for key in ("left_hand", "right_hand")]
        if any(len(hand) not in (0, 15) for hand in self.hands):
            raise ValueError("The Humanoid hand map is invalid.")
        self.mass = _vector(human["mass"], len(BONES), "bone masses")
        self.scale = float(human["scale"])
        if not math.isfinite(self.scale) or self.scale <= 0 or np.any(self.mass < 0):
            raise ValueError("The Humanoid scale or masses are invalid.")
        required = ("Hips", "LeftUpperLeg", "RightUpperLeg", "LeftUpperArm", "RightUpperArm")
        if any(self.node(name) < 0 for name in required):
            raise ValueError("The Humanoid lacks the bones that define its body frame.")
        hips = self.node("Hips")
        # The body frame must move rigidly with the hips subtree.
        for name in BONES:
            node = self.node(name)
            if node >= 0 and name != "Hips" and (self.mass[BONE[name]] > 0 or name in required) and not self._descends(node, hips):
                raise ValueError("The Humanoid body bones must descend from the hips.")
        if self.mass[[BONE[name] for name in BONES if self.node(name) >= 0]].sum() <= 0:
            raise ValueError("The Humanoid has no body mass.")
        self.hips = hips

    def node(self, name):
        return self.bones[BONE[name]]

    def _descends(self, node, ancestor):
        while node > ancestor:
            node = self.parents[node]
        return node == ancestor

    def _local(self, node, dof):
        """Local rotations (F, 4) of one human node from per-axis muscle arrays (None = 0)."""
        axes = self.axes[self.axes_ids[node]]
        halves = []
        for axis in range(3):
            muscle = dof[axis]
            if muscle is None:
                halves.append(np.zeros(self.frames))
                continue
            angle = muscle * np.where(muscle >= 0, axes["high"][axis], -axes["low"][axis])
            halves.append(np.tan(angle * axes["sign"][axis] * 0.5))
        q = zyroll(*halves)
        return qnormalize(qmul(qmul(axes["pre"], q), qconj(axes["post"])))

    def _point(self, positions, rotations, node, fraction):
        """Joint position plus a fraction of its axis-length along the bone (+x after postQ)."""
        if self.axes_ids[node] < 0:
            return positions[:, node]
        axes = self.axes[self.axes_ids[node]]
        return positions[:, node] + qrot(qmul(rotations[:, node], axes["post"]), np.array([axes["length"] * fraction, 0, 0]))

    def body_frame(self, positions, rotations):
        """Centre of mass and Mecanim body orientation of posed root-space globals."""
        total, centre = 0.0, np.zeros((positions.shape[0], 3))
        for name in BONES:
            node, mass = self.node(name), self.mass[BONE[name]]
            if node >= 0 and mass > 0:
                centre += mass * self._point(positions, rotations, node, 0.5)
                total += mass
        centre /= total
        left_leg, right_leg = positions[:, self.node("LeftUpperLeg")], positions[:, self.node("RightUpperLeg")]
        left_arm, right_arm = positions[:, self.node("LeftUpperArm")], positions[:, self.node("RightUpperArm")]
        # Up is exact; the right axis is orthogonalized against it.
        up = (left_arm + right_arm - left_leg - right_leg) * 0.5
        up /= np.linalg.norm(up, axis=-1, keepdims=True)
        forward = np.cross(right_leg - left_leg + right_arm - left_arm, up)
        forward /= np.linalg.norm(forward, axis=-1, keepdims=True)
        return centre, qfrom_basis(np.cross(up, forward), up, forward)

    def forward(self, local_t, local_q):
        """Root-space globals; node 0 is the Avatar root, so its own transform is excluded."""
        positions, rotations = np.zeros_like(local_t), np.zeros_like(local_q)
        rotations[:, 0] = (0, 0, 0, 1)
        for node in range(1, self.count):
            parent = self.parents[node]
            positions[:, node] = positions[:, parent] + qrot(rotations[:, parent], local_t[:, node])
            rotations[:, node] = qnormalize(qmul(rotations[:, parent], local_q[:, node]))
        return positions, rotations

    def solve(self, channels, frames, *, in_place=True):
        """Human-skeleton local transforms for each frame.

        ``channels`` maps Animator attributes to arrays of length ``frames``.
        Root T/Q are required (and Motion T/Q in place); absent muscles are 0.
        Returns local translations (F, N, 3), local rotations (F, N, 4) and
        per-frame goal errors divided by the human scale (``None`` without goals).
        """
        self.frames = int(frames)
        if not 1 <= self.frames <= 100000:
            raise ValueError("The Humanoid frame count is invalid.")
        values = {}
        for attribute, array in channels.items():
            array = np.asarray(array, dtype=float)
            if type(attribute) is not int or not 0 <= attribute < LAST or array.shape != (self.frames,) or not np.isfinite(array).all():
                raise ValueError("The Humanoid curves are invalid.")
            values[attribute] = array
        required = range(ROOT, ROOT + 7) if not in_place else range(MOTION, ROOT + 7)
        if any(attribute not in values for attribute in required):
            raise ValueError("The Humanoid clip has no complete root curves.")

        def channel(attribute):
            return values.get(attribute)

        local_t = np.broadcast_to(self.translations, (self.frames, self.count, 3)).copy()
        local_q = np.broadcast_to(self.rotations, (self.frames, self.count, 4)).copy()
        for name, dof in _BODY_DOF.items():
            node = self.node(name)
            if node >= 0 and self.axes_ids[node] >= 0:
                local_q[:, node] = self._local(node, [None if d is None else channel(MUSCLES + d) for d in dof])
        for side, hand in enumerate(self.hands):
            for bone, node in enumerate(hand):
                if node >= 0 and self.axes_ids[node] >= 0:
                    dof = _FINGER_DOF[bone]
                    local_q[:, node] = self._local(node, [None if d is None else channel(FINGERS + 20 * side + d) for d in dof])

        positions, rotations = self.forward(local_t, local_q)
        centre, orientation = self.body_frame(positions, rotations)
        root_t = np.stack([values[ROOT + i] for i in range(3)], axis=-1) * self.scale
        root_q = qnormalize(np.stack([values[ROOT + 3 + i] for i in range(4)], axis=-1))
        # Rigidly move the hips subtree so its body frame equals the clip's Root frame.
        correction = qnormalize(qmul(root_q, qconj(orientation)))
        hips_q = qnormalize(qmul(correction, rotations[:, self.hips]))
        hips_t = root_t + qrot(correction, positions[:, self.hips] - centre)
        errors = self._goal_errors(values, positions, rotations, correction, centre, root_t, root_q)
        if in_place:
            motion_t = np.stack([values[MOTION + i] for i in range(3)], axis=-1) * self.scale
            inverse = qconj(qnormalize(np.stack([values[MOTION + 3 + i] for i in range(4)], axis=-1)))
            hips_t = qrot(inverse, hips_t - motion_t)
            hips_q = qnormalize(qmul(inverse, hips_q))
        parent = self.parents[self.hips]
        parent_t, parent_q = positions[:, parent], rotations[:, parent]
        local_t[:, self.hips] = qrot(qconj(parent_q), hips_t - parent_t)
        local_q[:, self.hips] = qnormalize(qmul(qconj(parent_q), hips_q))
        # Keep each track hemisphere-continuous for interpolation.
        flips = np.sum(local_q[1:] * local_q[:-1], axis=-1) < 0
        signs = np.concatenate([np.ones((1, self.count)), np.cumprod(np.where(flips, -1.0, 1.0), axis=0)])
        local_q *= signs[..., None]
        if not (np.isfinite(local_t).all() and np.isfinite(local_q).all()):
            raise ValueError("The Humanoid pose is not finite.")
        return local_t, local_q, errors

    def _goal_errors(self, values, positions, rotations, correction, centre, root_t, root_q):
        """Distance of hands/feet from the clip's IK goals, relative to the human scale."""
        errors = {}
        for limb, name in enumerate(LIMB_GOALS):
            base, node = GOALS + 7 * limb, self.node(name)
            if node < 0 or any(base + i not in values for i in range(3)):
                continue
            goal = root_t + qrot(root_q, np.stack([values[base + i] for i in range(3)], axis=-1) * self.scale)
            # Foot goals sit at the sole: the foot axis ends at the ground below the ankle.
            point = self._point(positions, rotations, node, 1.0 if "Foot" in name else 0.0)
            placed = root_t + qrot(correction, point - centre)
            errors[name] = np.linalg.norm(placed - goal, axis=-1) / self.scale
        return errors or None
