"""Synthetic Mecanim Humanoid solver contracts."""

from copy import deepcopy

import numpy as np
import pytest

from app.assets.game_extract import mecanim
from app.assets.game_extract.mecanim import BONE, FINGERS, GOALS, MOTION, MUSCLES, ROOT, HumanSolver
from tests.support.humanoid_data import axis_angle, humanoid

def root_channels(frames, rng, motion=True):
    channels = {}
    root_t = rng.normal(scale=.2, size=(frames, 3)) + [0, .9, 0]
    root_q = np.array([axis_angle(rng.normal(size=3), rng.uniform(0, .6)) for _ in range(frames)])
    for i in range(3):
        channels[ROOT + i] = root_t[:, i]
    for i in range(4):
        channels[ROOT + 3 + i] = root_q[:, i]
    if motion:
        motion_q = np.array([axis_angle((0, 1, 0), rng.uniform(-1, 1)) for _ in range(frames)])
        motion_t = np.column_stack([rng.normal(size=frames), np.zeros(frames), rng.normal(size=frames)])
        for i in range(3):
            channels[MOTION + i] = motion_t[:, i]
        for i in range(4):
            channels[MOTION + 3 + i] = motion_q[:, i]
    return channels


def body_muscles(frames, rng):
    return {MUSCLES + dof: rng.uniform(-1.1, 1.1, frames) for dof in range(55)}


def test_zyroll_and_axis_limits_follow_mecanim_axes():
    rng = np.random.default_rng(1)
    for x, y, z in rng.normal(size=(20, 3)):
        swing = mecanim.qnormalize(np.array([0, y, z, 1.0]))
        twist = mecanim.qnormalize(np.array([x, 0, 0, 1.0]))
        assert np.allclose(mecanim.zyroll(np.array(x), np.array(y), np.array(z)), mecanim.qnormalize(mecanim.qmul(swing, twist)))
    solver = HumanSolver(humanoid())
    node = solver.node("LeftUpperArm")
    axes = solver.axes[solver.axes_ids[node]]
    solver.frames = 3
    # Zero muscles give the axes' neutral rotation; +-1 reach each limit along that axis.
    neutral = solver._local(node, [np.zeros(3)] * 3)
    assert np.allclose(neutral, mecanim.qmul(axes["pre"], mecanim.qconj(axes["post"])))
    for axis in range(3):
        dof = [None, None, None]
        dof[axis] = np.array([1.0, -1.0, .5])
        local = solver._local(node, dof)
        relative = mecanim.qmul(mecanim.qmul(mecanim.qconj(axes["pre"]), local), axes["post"])
        expected = np.array([axes["high"][axis], axes["low"][axis], .5 * axes["high"][axis]]) * axes["sign"][axis]
        unit = np.zeros(3)
        unit[axis] = 1
        for q, angle in zip(relative, expected):
            assert np.allclose(np.abs(np.dot(q, axis_angle(unit, angle))), 1, atol=1e-9)


def test_solve_places_the_body_frame_and_matches_single_frame_solves():
    solver = HumanSolver(humanoid())
    rng = np.random.default_rng(2)
    frames = 6
    channels = {**root_channels(frames, rng), **body_muscles(frames, rng)}
    root_t = np.column_stack([channels[ROOT + i] for i in range(3)]) * solver.scale
    root_q = np.column_stack([channels[ROOT + 3 + i] for i in range(4)])
    local_t, local_q, errors = solver.solve(channels, frames, in_place=False)
    assert errors is None
    centre, orientation = solver.body_frame(*solver.forward(local_t, local_q))
    assert np.allclose(centre, root_t, atol=1e-9)
    assert np.allclose(np.abs(np.sum(orientation * root_q, axis=1)), 1, atol=1e-9)
    # In place, the body frame is the Root frame relative to Motion.
    motion_t = np.column_stack([channels[MOTION + i] for i in range(3)]) * solver.scale
    motion_q = np.column_stack([channels[MOTION + 3 + i] for i in range(4)])
    _t, placed_q, _errors = solver.solve(channels, frames)
    centre, orientation = solver.body_frame(*solver.forward(_t, placed_q))
    inverse = mecanim.qconj(motion_q)
    assert np.allclose(centre, mecanim.qrot(inverse, root_t - motion_t), atol=1e-9)
    assert np.allclose(np.abs(np.sum(orientation * mecanim.qmul(inverse, root_q), axis=1)), 1, atol=1e-9)
    # Non-hips joints keep their reference translations; batch and per-frame solves agree.
    hips = solver.node("Hips")
    others = [node for node in range(solver.count) if node != hips]
    assert np.allclose(local_t[:, others], solver.translations[others])
    for frame in range(frames):
        single = {key: value[frame:frame + 1] for key, value in channels.items()}
        one_t, one_q, _ = solver.solve(single, 1, in_place=False)
        assert np.allclose(one_t[0], local_t[frame])
        assert np.allclose(np.abs(np.sum(one_q[0] * local_q[frame], axis=-1)), 1)


def test_mirrored_muscles_produce_reflected_poses():
    solver = HumanSolver(humanoid())
    rng = np.random.default_rng(3)
    frames = 5
    channels = root_channels(frames, rng, motion=False)
    limbs = {MUSCLES + dof: rng.uniform(-1, 1, frames) for dof in range(21, 55)}
    channels.update(limbs)
    mirrored = dict(channels)
    for i in range(3):
        mirrored[ROOT + i] = channels[ROOT + i] * (-1 if i == 0 else 1)
    for i, sign in zip(range(4), (1, -1, -1, 1)):
        mirrored[ROOT + 3 + i] = channels[ROOT + 3 + i] * sign
    # Swap left/right leg (21-28 <-> 29-36) and arm (37-45 <-> 46-54) muscles.
    for left, right, count in ((21, 29, 8), (37, 46, 9)):
        for k in range(count):
            mirrored[MUSCLES + left + k], mirrored[MUSCLES + right + k] = limbs[MUSCLES + right + k], limbs[MUSCLES + left + k]
    positions, _ = solver.forward(*solver.solve(channels, frames, in_place=False)[:2])
    reflected, _ = solver.forward(*solver.solve(mirrored, frames, in_place=False)[:2])
    flip = np.array([-1.0, 1.0, 1.0])
    for name in mecanim.BONES:
        node = solver.node(name)
        if node < 0:
            continue
        partner = "Right" + name[4:] if name.startswith("Left") else "Left" + name[5:] if name.startswith("Right") else name
        assert np.allclose(positions[:, node], reflected[:, solver.node(partner)] * flip, atol=1e-9), name


def test_goal_errors_measure_hands_at_joints_and_feet_at_soles():
    solver = HumanSolver(humanoid())
    rng = np.random.default_rng(4)
    frames = 4
    channels = {**root_channels(frames, rng, motion=False), **body_muscles(frames, rng)}
    root_t = np.column_stack([channels[ROOT + i] for i in range(3)]) * solver.scale
    root_q = np.column_stack([channels[ROOT + 3 + i] for i in range(4)])
    positions, rotations = solver.forward(*solver.solve(channels, frames, in_place=False)[:2])
    for limb, name in enumerate(mecanim.LIMB_GOALS):
        point = solver._point(positions, rotations, solver.node(name), 1.0 if "Foot" in name else 0.0)
        goal = mecanim.qrot(mecanim.qconj(root_q), point - root_t) / solver.scale
        offset = .1 if name == "LeftHand" else 0.0
        for i in range(3):
            channels[GOALS + 7 * limb + i] = goal[:, i] + (offset if i == 0 else 0)
    errors = solver.solve(channels, frames, in_place=False)[2]
    assert set(errors) == set(mecanim.LIMB_GOALS)
    for name, error in errors.items():
        assert np.allclose(error, .1 if name == "LeftHand" else 0, atol=1e-9), name


def test_rotations_stay_hemisphere_continuous_and_fingers_are_optional():
    human = humanoid()
    solver = HumanSolver(human)
    rng = np.random.default_rng(5)
    channels = root_channels(4, rng, motion=False)
    for i in range(4):
        channels[ROOT + 3 + i] = channels[ROOT + 3 + i] * np.array([1, -1, 1, -1])
    channels[FINGERS] = np.zeros(4)  # Fingers without a mapped hand are ignored.
    _t, rotations, _ = solver.solve(channels, 4, in_place=False)
    assert np.all(np.sum(rotations[1:] * rotations[:-1], axis=-1) > 0)


@pytest.mark.parametrize("change, match", [
    (lambda h: h["axes"][0].update(type=2), "ZYRoll"),
    (lambda h: h["bone_index"].__setitem__(BONE["Hips"], -1), "body frame"),
    (lambda h: h["skeleton"]["parent"].__setitem__(2, 5), "topologically"),
    (lambda h: h["axes"][1].update(pre_q=[0, 0, 0, 2]), "unit rotation"),
    (lambda h: h["axes"][1].update(min=[.1, 0, 0]), "limits"),
    (lambda h: h.update(bone_index=h["bone_index"][:-1]), "bone map"),
    (lambda h: h.update(scale=0), "scale"),
    (lambda h: h["pose"].pop(), "reference pose"),
])
def test_malformed_humanoid_definitions_are_rejected(change, match):
    human = deepcopy(humanoid())
    change(human)
    with pytest.raises(ValueError, match=match):
        HumanSolver(human)


def test_malformed_curves_are_rejected():
    solver = HumanSolver(humanoid())
    rng = np.random.default_rng(6)
    channels = root_channels(3, rng)
    for bad, match in (({**channels, MUSCLES: np.array([0, np.nan, 0])}, "curves"),
                       ({**channels, MUSCLES: np.zeros(2)}, "curves"),
                       ({**channels, 400: np.zeros(3)}, "curves"),
                       ({key: value for key, value in channels.items() if key != ROOT}, "root curves"),
                       ({key: value for key, value in channels.items() if key != MOTION}, "root curves")):
        with pytest.raises(ValueError, match=match):
            solver.solve(bad, 3)
    # Without Motion curves the pose can still be solved in Root space.
    root_only = {key: value for key, value in channels.items() if key >= ROOT}
    assert solver.solve(root_only, 3, in_place=False)[1].shape == (3, solver.count, 4)
