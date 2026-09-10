"""Frontend module contracts that do not require a running GPU viewer."""

import math

import pytest


def test_vendored_transform_controls_exposes_scene_helper(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three');
      const {TransformControls} = await import(
        'three/addons/controls/TransformControls.js');
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera();
      const canvas = document.createElement('canvas');
      const controls = new TransformControls(camera, canvas);
      const helper = controls.getHelper();
      scene.add(helper);
      const attached = helper.parent === scene;
      const api = {
        hasGetHelper: typeof controls.getHelper === 'function',
        helperIsObject3D: helper.isObject3D === true,
        attached,
      };
      controls.dispose();
      scene.remove(helper);
      return api;
    }""")
    assert result == {
        "hasGetHelper": True,
        "helperIsObject3D": True,
        "attached": True,
    }


def test_rig_pose_presets_use_exact_stable_signatures_and_partial_resolution(
        module_page):
    result = module_page.evaluate("""async () => {
      const presets = await import('./js/mesh/weight-rig-presets.js');
      const first = '["body|offset=0#bone=7"]';
      const second = '["body|offset=0#bone=8","legs|offset=0#bone=1"]';
      const rig = {
        joints: [{jointId: 41, signature: first},
          {jointId: 9, signature: second}],
        poseRotationByJointId: new Map([
          [41, [0, 0, Math.sin(Math.PI / 4), Math.cos(Math.PI / 4)]],
          [9, [0, 0, 0, 1]],
        ]),
      };
      const serialized = presets.serializeRigPose(rig,
        {explicitRootSignatures: new Set([first])});
      const identitySerialized = presets.serializeRigPose({
        joints: [{jointId: 1, signature: first},
          {jointId: 2, signature: second}],
        poseRotationByJointId: new Map([
          [1, [0, 0, 0, 1]], [2, [0, 0, 0, -1]],
        ]),
      });
      const resolved = presets.resolveRigPreset({
        joints: [{jointId: 100, signature: first},
          {jointId: 200, signature: second}],
      }, {
        id: 'pose-1', name: 'Look Left',
        roots: [{joint_signature: first},
          {joint_signature: '["missing|offset=0#bone=4"]'}],
        joints: [
          {joint_signature: first, rotation: [0, 0, 2, 0]},
          {joint_signature: second, rotation: [NaN, 0, 0, 1]},
        ],
      });
      return {
        serialized,
        identitySerialized,
        resolved: {
          roots: resolved.roots,
          joints: resolved.joints,
          skipped: resolved.skipped,
        },
      };
    }""")
    assert result["serialized"] == {
        "roots": [{"joint_signature":
                   '["body|offset=0#bone=7"]'}],
        "joints": [{"joint_signature":
                    '["body|offset=0#bone=7"]',
                    "rotation": pytest.approx(
                        [0, 0, 2 ** -0.5, 2 ** -0.5])}],
    }
    assert result["identitySerialized"] == {"roots": [], "joints": []}
    assert result["resolved"]["roots"] == [{
        "jointId": 100,
        "jointSignature": '["body|offset=0#bone=7"]',
    }]
    assert result["resolved"]["joints"] == [{
        "jointId": 100,
        "jointSignature": '["body|offset=0#bone=7"]',
        "rotation": [0, 0, 1, 0],
    }]
    assert {item["reason"] for item in result["resolved"]["skipped"]} == {
        "root_not_found", "invalid_rotation",
    }


def test_runtime_resets_keep_live_state_and_fresh_mutable_defaults(module_page):
    result = module_page.evaluate("""async () => {
      const runtime = await import('./js/mesh/weight-runtime.js');
      const weight = runtime.createWeightRuntimeState();
      const rig = runtime.createRigRuntimeState();
      const weightState = weight.modelWeightState;
      const modelRigState = rig.modelRigState;
      const presetState = rig.rigPresetState;
      const oldSelected = weightState.selectedBonesBySource;
      const oldRoots = modelRigState.explicitRootSignatures;
      const oldPresets = presetState.presets;
      weightState.selectedBonesBySource.set('source', new Set([1]));
      modelRigState.explicitRootSignatures.add('root');
      presetState.presets.push({id: 'pose', name: 'Pose'});
      rig.structureRevision = 7;
      weight.resetModelWeightState();
      rig.resetModelRigState();
      rig.resetRigPresetState();
      return {
        stableReferences: weight.modelWeightState === weightState
          && rig.modelRigState === modelRigState
          && rig.rigPresetState === presetState,
        freshWeightMap: weightState.selectedBonesBySource !== oldSelected
          && weightState.selectedBonesBySource.size === 0,
        freshRootSet: modelRigState.explicitRootSignatures !== oldRoots
          && modelRigState.explicitRootSignatures.size === 0,
        freshPresetArray: presetState.presets !== oldPresets
          && presetState.presets.length === 0,
        resetDefaults: !weightState.loaded && !modelRigState.loaded
          && !presetState.loaded && presetState.lastApplyResult === null,
        structureRevisionPreserved: rig.structureRevision === 7,
      };
    }""")
    assert result == {
        "stableReferences": True,
        "freshWeightMap": True,
        "freshRootSet": True,
        "freshPresetArray": True,
        "resetDefaults": True,
        "structureRevisionPreserved": True,
    }


def test_rig_limb_detection_and_two_control_solver(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const ik = await import('./js/mesh/weight-rig-ik.js');
      const component = {
        rootId: 0, nodeIds: [0, 1, 2, 3, 4, 5, 6, 7, 8],
        parentById: {0: null, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4,
          6: 5, 7: 5, 8: 5},
        childrenById: {0: [1], 1: [2], 2: [3], 3: [4], 4: [5],
          5: [6, 7, 8], 6: [], 7: [], 8: []},
      };
      const points = new Map([
        [0, [-1, 0, 0]], [1, [0, 0, 0]], [2, [.5, 0, 0]],
        [3, [1, 0, 0]], [4, [1.5, 0, 0]], [5, [2, 0, 0]],
        [6, [2, .2, 0]], [7, [2, 0, .2]], [8, [2, -.2, 0]],
      ]);
      const rig = {
        components: [component], componentByJointId: new Map(
          component.nodeIds.map(id => [id, 0])),
        jointPivotByJointId: points, centerByJointId: points,
        restContinuationChildByJointId: new Map([
          [0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6],
        ]),
        restFrameByJointId: new Map(),
      };
      const detected = ik.detectLimbPath({
        rig, anchorJointId: 1, role: 'left_arm',
      });
      const solve = target => ik.solveLimbIk({
        forest: {components: [component]}, centers: points,
        jointPivots: points, localRotations: new Map([[5, new THREE.Quaternion()]]),
        anchorJointId: detected.anchorJointId,
        bendJointId: detected.bendJointId,
        endJointId: detected.endJointId,
        pathJointIds: detected.pathJointIds,
        bendDirection: detected.bendDirection,
        target,
      });
      const solved = solve([1.5, .8, 0]);
      const reachable = solve([2, 0, 0]);
      const unreachable = solve([10, 0, 0]);
      const finite = [...solved.rotations.values()].every(rotation =>
        [rotation.x, rotation.y, rotation.z, rotation.w].every(Number.isFinite)
        && Math.abs(rotation.length() - 1) < 1e-6);
      return {
        detected: {
          available: detected.available,
          path: detected.pathJointIds,
          bend: detected.bendJointId,
          end: detected.endJointId,
          confidence: detected.confidence,
        },
        solved: {
          iterations: solved.iterations,
          residual: solved.residual,
          finite,
          ids: [...solved.rotations.keys()],
          endDistance: solved.residual,
        },
        reachable: {
          iterations: reachable.iterations,
          reached: reachable.reached,
        },
        unreachable: {
          reached: unreachable.reached,
        },
      };
    }""")
    assert result["detected"] == {
        "available": True,
        "path": [1, 2, 3, 4, 5],
        "bend": 3,
        "end": 5,
        "confidence": "high",
    }
    assert result["solved"]["finite"]
    assert result["solved"]["ids"] == [1, 3]
    assert result["solved"]["endDistance"] < 0.1
    assert result["reachable"] == {"iterations": 1, "reached": True}
    assert result["unreachable"]["reached"] is False


def test_humanoid_limb_suggestions_keep_primary_backbone_over_secondary_appendage(module_page):
    result = module_page.evaluate("""async () => {
      const humanoid = await import('./js/mesh/weight-rig-humanoid.js');
      const points = new Map([
        [0, [0, 1, 0]],
        [1, [-.55, 1.55, 0]], [2, [-1.05, 1.35, 0]], [3, [-1.5, 1.1, 0]],
        [4, [.55, 1.55, 0]], [5, [1.05, 1.35, 0]], [6, [1.5, 1.1, 0]],
        [7, [-.35, .8, 0]], [8, [-.4, .2, 0]], [9, [-.42, -.4, 0]],
        [10, [.35, .8, 0]], [11, [.4, .2, 0]], [12, [.42, -.4, 0]],
        [13, [-.8, 1.8, -1]], [14, [-1.6, 1.8, -1.5]], [15, [-2.4, 1.8, -1.8]],
      ]);
      const parentById = {0: null, 1: 0, 2: 1, 3: 2, 4: 0, 5: 4, 6: 5,
        7: 0, 8: 7, 9: 8, 10: 0, 11: 10, 12: 11, 13: 0, 14: 13, 15: 14};
      const shiftedCenters = new Map([...points].map(([id, point]) => [id,
        [point[0] + .8, point[1] - .3, point[2] + .2]]));
      const childrenById = Object.fromEntries(Object.keys(parentById).map(id => [id, []]));
      Object.entries(parentById).forEach(([child, parent]) => {
        if (parent !== null) childrenById[parent].push(Number(child));
      });
      const component = {rootId: 0, nodeIds: [...points.keys()], parentById, childrenById};
      const rig = {
        joints: [...points.keys()].reverse().map(jointId => ({jointId,
          restPivot: points.get(jointId), restCenter: shiftedCenters.get(jointId)})),
        components: [component], componentByJointId: new Map([...points.keys()].map(id => [id, 0])),
        centerByJointId: shiftedCenters, jointPivotByJointId: points,
        restContinuationChildByJointId: new Map([
          [1, 2], [2, 3], [4, 5], [5, 6], [7, 8], [8, 9],
          [10, 11], [11, 12], [13, 14], [14, 15],
        ]),
      };
      const result = humanoid.suggestHumanoidLimbMappings({
        rig, characterForward: [0, 0, 1], debug: true,
      });
      const frame = humanoid.buildHumanoidSemanticFrame({
        rig, characterForward: [0, 0, 1],
      });
      const pivotSample = humanoid.collectHumanoidSamples({rig, frame})
        .find(sample => sample.jointId === 1);
      const scaledPoints = new Map([...points].map(([id, point]) => [id,
        point.map(value => value * 10)]));
      const scaledRig = {...rig, joints: rig.joints.map(joint => ({...joint,
        restPivot: scaledPoints.get(joint.jointId),
        restCenter: scaledPoints.get(joint.jointId).map(value => value + .4)})),
        centerByJointId: new Map([...scaledPoints].map(([id, point]) => [id,
          point.map(value => value + .4)])), jointPivotByJointId: scaledPoints};
      const scaled = humanoid.suggestHumanoidLimbMappings({
        rig: scaledRig, characterForward: [0, 0, 1]});
      return {roles: result.roles, scaledRoles: scaled.roles,
        pivotSample: {point: pivotSample.point.toArray(),
          influenceCenter: pivotSample.influenceCenter.toArray()},
        rejectedSecondary: result.debug.rejected
        .filter(item => item.anchorJointId === 13).map(item => item.reason)};
    }""")
    roles = result["roles"]
    assert roles["left_arm"]["available"]
    assert roles["right_arm"]["available"]
    assert roles["left_leg"]["available"]
    assert roles["right_leg"]["available"]
    assert roles["left_arm"]["anchorJointId"] == 1
    assert roles["right_arm"]["anchorJointId"] == 4
    assert roles["left_leg"]["anchorJointId"] == 7
    assert roles["right_leg"]["anchorJointId"] == 10
    assert 13 not in [roles[role]["anchorJointId"] for role in roles]
    assert result["scaledRoles"]["left_arm"]["anchorJointId"] == 1
    assert result["scaledRoles"]["right_arm"]["anchorJointId"] == 4
    assert result["pivotSample"]["point"] == [-0.55, 1.55, 0]
    assert result["pivotSample"]["influenceCenter"] == [0.25, 1.25, 0.2]


def test_humanoid_detector_collapses_dense_helper_joint_families(module_page):
    result = module_page.evaluate("""async () => {
      const {suggestHumanoidLimbMappings} = await import(
        './js/mesh/weight-rig-humanoid.js');
      const points = new Map([
        [0, [0, 1, 0]],
        [1, [-.55, 1.55, 0]], [2, [-.8, 1.48, 0]], [3, [-1.05, 1.35, 0]],
        [4, [-1.25, 1.23, 0]], [5, [-1.5, 1.1, 0]], [6, [-1.65, 1.04, 0]],
        [7, [-1.8, .98, 0]],
        [8, [.55, 1.55, 0]], [9, [.8, 1.48, 0]], [10, [1.05, 1.35, 0]],
        [11, [1.25, 1.23, 0]], [12, [1.5, 1.1, 0]], [13, [1.65, 1.04, 0]],
        [14, [1.8, .98, 0]],
        [15, [-.35, .8, 0]], [16, [-.4, .2, 0]], [17, [-.42, -.4, 0]],
        [18, [.35, .8, 0]], [19, [.4, .2, 0]], [20, [.42, -.4, 0]],
        [21, [-.7, 1.25, -.25]], [22, [-.85, 1.25, -.25]], [23, [-1.0, 1.25, -.25]],
        [24, [.7, 1.25, -.25]], [25, [.85, 1.25, -.25]], [26, [1.0, 1.25, -.25]],
      ]);
      const parentById = {0: null, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6,
        8: 0, 9: 8, 10: 9, 11: 10, 12: 11, 13: 12, 14: 13,
        15: 0, 16: 15, 17: 16, 18: 0, 19: 18, 20: 19,
        21: 0, 22: 21, 23: 22, 24: 0, 25: 24, 26: 25};
      const childrenById = Object.fromEntries(Object.keys(parentById).map(id => [id, []]));
      Object.entries(parentById).forEach(([child, parent]) => {
        if (parent !== null) childrenById[parent].push(Number(child));
      });
      const component = {rootId: 0, nodeIds: [...points.keys()], parentById, childrenById};
      const centers = new Map([...points].map(([id, point]) => [id,
        [point[0] + .9, point[1] - .4, point[2] + .1]]));
      const continuation = new Map([
        [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [6, 7],
        [8, 9], [9, 10], [10, 11], [11, 12], [12, 13], [13, 14],
        [15, 16], [16, 17], [18, 19], [19, 20], [21, 22], [22, 23],
        [24, 25], [25, 26],
      ]);
      const rig = {
        joints: [...points.keys()].reverse().map(jointId => ({jointId,
          restPivot: points.get(jointId), restCenter: centers.get(jointId)})),
        components: [component], componentByJointId: new Map([...points.keys()].map(id => [id, 0])),
        centerByJointId: centers, jointPivotByJointId: points,
        restContinuationChildByJointId: continuation,
      };
      const withoutSecondary = {...rig,
        joints: rig.joints.filter(joint => joint.jointId < 21)};
      const detected = suggestHumanoidLimbMappings({
        rig: withoutSecondary, characterForward: [0, 0, 1], debug: true,
      });
      const withSecondary = suggestHumanoidLimbMappings({
        rig, characterForward: [0, 0, 1], debug: true,
      });
      return {
        roles: detected.roles,
        secondaryRoles: withSecondary.roles,
        families: withSecondary.debug.families,
        pairs: withSecondary.debug.pairs.arms,
        top: detected.debug.topCandidatesByRole.left_arm,
      };
    }""")
    assert result["roles"]["left_arm"]["available"]
    assert result["roles"]["right_arm"]["available"]
    assert result["roles"]["left_arm"]["anchorJointId"] == 1
    assert result["roles"]["right_arm"]["anchorJointId"] == 8
    assert len(result["families"]["left_arm"]) == 2
    assert len(result["families"]["right_arm"]) == 2
    arm_family = next(family for family in result["families"]["left_arm"]
                      if family["representative"]["anchorJointId"] == 1)
    assert len(arm_family["alternatives"]) >= 3
    assert arm_family["representative"]["anchorJointId"] == 1
    assert result["top"][0]["forwardOffset"] <= .3
    assert result["secondaryRoles"]["left_arm"]["available"]
    assert result["secondaryRoles"]["right_arm"]["available"]
    assert result["secondaryRoles"]["left_arm"]["anchorJointId"] == 1
    assert result["secondaryRoles"]["right_arm"]["anchorJointId"] == 8
    assert result["pairs"]["best"]["left"]["anchorJointId"] == 1
    assert result["pairs"]["best"]["right"]["anchorJointId"] == 8
    assert result["pairs"]["runnerUp"]["leftAnchorJointId"] == 21
    assert result["pairs"]["runnerUp"]["rightAnchorJointId"] == 24
    assert result["pairs"]["margin"] > .065


def test_shared_limb_resolver_handles_wrong_hints_narrow_anchors_and_secondary_branches(module_page):
    result = module_page.evaluate("""async () => {
      const {resolveLimbPathCandidates} = await import('./js/mesh/weight-rig-ik.js');
      const {suggestHumanoidLimbMappings} = await import(
        './js/mesh/weight-rig-humanoid.js');
      const points = new Map([
        [0, [0, 1, 0]],
        [1, [-.07, 1.45, 0]], [2, [-.3, 1.4, 0]], [3, [-.55, 1.3, 0]],
        [4, [-.8, 1.1, 0]], [5, [-1.0, 1.0, 0]], [6, [-1.15, .95, 0]],
        [7, [-1.2, .95, 0]], [8, [-1.1, .95, 0]],
        [10, [.07, 1.45, 0]], [11, [.3, 1.4, 0]], [12, [.55, 1.3, 0]],
        [13, [.8, 1.1, 0]], [14, [1.0, 1.0, 0]], [15, [1.15, .95, 0]],
        [16, [1.2, .95, 0]], [17, [1.1, .95, 0]],
        [20, [-.32, 1.42, .35]], [21, [-.6, 1.28, .3]], [22, [-.9, 1.18, .25]],
        [30, [-.04, .8, 0]], [31, [-.1, .55, 0]], [32, [-.12, .2, 0]],
        [33, [-.14, -.25, 0]], [34, [-.15, -.45, 0]], [35, [-.2, -.48, 0]],
        [40, [.04, .8, 0]], [41, [.1, .55, 0]], [42, [.12, .2, 0]],
        [43, [.14, -.25, 0]], [44, [.15, -.45, 0]], [45, [.2, -.48, 0]],
        [50, [-.7, 1.4, -.8]], [51, [-1.4, 1.45, -1.3]], [52, [-2.1, 1.5, -1.6]],
        [60, [.7, 1.4, -.8]], [61, [1.4, 1.45, -1.3]], [62, [2.1, 1.5, -1.6]],
      ]);
      const parentById = {
        0: null, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 6,
        10: 0, 11: 10, 12: 11, 13: 12, 14: 13, 15: 14, 16: 15, 17: 15,
        20: 2, 21: 3, 22: 21,
        30: 0, 31: 30, 32: 31, 33: 32, 34: 33, 35: 34,
        40: 0, 41: 40, 42: 41, 43: 42, 44: 43, 45: 44,
        50: 0, 51: 50, 52: 51, 60: 0, 61: 60, 62: 61,
      };
      const continuation = new Map([
        [1, 2], [2, 20], [3, 21], [4, 5], [5, 6], [10, 11], [11, 12],
        [12, 13], [13, 14], [14, 15], [30, 31], [31, 32], [32, 33],
        [33, 34], [34, 35], [40, 41], [41, 42], [42, 43], [43, 44],
        [44, 45], [50, 51], [51, 52], [60, 61], [61, 62],
      ]);
      const correctedContinuation = new Map([
        ...continuation, [2, 3], [3, 4],
      ]);
      const makeRig = (shuffled, wrongHint = true) => {
        const ids = [...points.keys()];
        const ordered = shuffled ? ids.reverse() : ids;
        const childrenById = Object.fromEntries(ids.map(id => [id, []]));
        Object.entries(parentById).forEach(([child, parent]) => {
          if (parent !== null) childrenById[parent].push(Number(child));
        });
        if (shuffled) Object.values(childrenById).forEach(children => children.reverse());
        const component = {
          rootId: 0, nodeIds: ordered, parentById, childrenById,
        };
        return {
          joints: ordered.map(jointId => ({jointId, restPivot: points.get(jointId)})),
          components: [component],
          componentByJointId: new Map(ids.map(id => [id, 0])),
          centerByJointId: points, jointPivotByJointId: points,
          restContinuationChildByJointId: wrongHint
            ? continuation : correctedContinuation,
        };
      };
      const baseline = suggestHumanoidLimbMappings({rig: makeRig(false),
        characterForward: [0, 0, 1], debug: true});
      const shuffled = suggestHumanoidLimbMappings({rig: makeRig(true),
        characterForward: [0, 0, 1], debug: true});
      const corrected = suggestHumanoidLimbMappings({rig: makeRig(false, false),
        characterForward: [0, 0, 1], debug: true});
      const wrongHint = resolveLimbPathCandidates({rig: makeRig(false),
        anchorJointId: 1, role: 'left_arm', characterForward: [0, 0, 1]});
      return {
        baseline: baseline.roles,
        shuffled: shuffled.roles,
        corrected: corrected.roles,
        paths: wrongHint.candidates.slice(0, 3).map(candidate => ({
          path: candidate.pathJointIds, end: candidate.endJointId,
          metrics: candidate.pathMetrics,
        })),
        pairs: baseline.debug.pairs,
      };
    }""")
    expected = {
        "left_arm": 1, "right_arm": 10, "left_leg": 30, "right_leg": 40,
    }
    for result_name in ("baseline", "shuffled"):
        roles = result[result_name]
        assert all(roles[role]["available"] for role in expected)
        assert {role: roles[role]["anchorJointId"] for role in expected} == expected
    assert result["baseline"]["left_arm"]["pathJointIds"] == [1, 2, 3, 4, 5, 6]
    assert result["baseline"]["left_leg"]["pathJointIds"] == [30, 31, 32, 33, 34, 35]
    assert result["shuffled"]["left_arm"]["pathJointIds"] == result["baseline"]["left_arm"]["pathJointIds"]
    assert result["shuffled"]["left_leg"]["pathJointIds"] == result["baseline"]["left_leg"]["pathJointIds"]
    assert result["corrected"]["left_arm"]["pathJointIds"] == result["baseline"]["left_arm"]["pathJointIds"]
    assert result["corrected"]["left_leg"]["pathJointIds"] == result["baseline"]["left_leg"]["pathJointIds"]
    assert result["paths"][0]["end"] == 6
    assert result["pairs"]["arms"]["best"]["left"]["anchorJointId"] == 1
    assert result["pairs"]["arms"]["best"]["right"]["anchorJointId"] == 10
    assert result["pairs"]["arms"]["runnerUp"]["leftAnchorJointId"] == 50
    assert result["pairs"]["arms"]["runnerUp"]["rightAnchorJointId"] == 60
    assert (result["pairs"]["legs"]["best"]["left"]["anchorJointId"],
            result["pairs"]["legs"]["best"]["right"]["anchorJointId"]) == (30, 40)


def test_humanoid_suggestions_report_missing_and_ambiguous_pairs(module_page):
    result = module_page.evaluate("""async () => {
      const {suggestHumanoidLimbMappings} = await import(
        './js/mesh/weight-rig-humanoid.js');
      const makeRig = items => {
        const points = new Map(items.map(item => [item.id, item.point]));
        const parentById = Object.fromEntries(items.map(item => [item.id, item.parent]));
        const childrenById = Object.fromEntries(items.map(item => [item.id, []]));
        items.forEach(item => { if (item.parent !== null) childrenById[item.parent].push(item.id); });
        const component = {rootId: 0, nodeIds: items.map(item => item.id), parentById, childrenById};
        return {joints: items.map(item => ({jointId: item.id, restCenter: item.point})),
          components: [component], componentByJointId: new Map(items.map(item => [item.id, 0])),
          centerByJointId: points, jointPivotByJointId: points,
          restContinuationChildByJointId: new Map(items.filter(item => item.child !== null)
            .map(item => [item.id, item.child]))};
      };
      const arm = [
        {id: 0, parent: null, child: 1, point: [0, 1, 0]},
        {id: 1, parent: 0, child: 2, point: [-.6, 1.5, 0]},
        {id: 2, parent: 1, child: 3, point: [-1.1, 1.3, 0]},
        {id: 3, parent: 2, child: null, point: [-1.5, 1.1, 0]},
        {id: 4, parent: 0, child: 5, point: [.6, 1.5, 0]},
        {id: 5, parent: 4, child: 6, point: [1.1, 1.3, 0]},
        {id: 6, parent: 5, child: null, point: [1.5, 1.1, 0]},
      ];
      const complete = suggestHumanoidLimbMappings({rig: makeRig(arm),
        characterForward: [0, 0, 1]});
      const missing = suggestHumanoidLimbMappings({
        rig: makeRig(arm.filter(item => item.id < 4)), characterForward: [0, 0, 1]});
      const duplicate = arm.concat([
        {id: 7, parent: 0, child: 8, point: [-.6, 1.5, 0]},
        {id: 8, parent: 7, child: 9, point: [-1.1, 1.3, 0]},
        {id: 9, parent: 8, child: null, point: [-1.5, 1.1, 0]},
        {id: 10, parent: 0, child: 11, point: [0, .7, 0]},
        {id: 11, parent: 10, child: 12, point: [0, .1, 0]},
        {id: 12, parent: 11, child: null, point: [0, -.5, 0]},
      ]);
      const ambiguous = suggestHumanoidLimbMappings({
        rig: makeRig(duplicate), characterForward: [0, 0, 1]});
      return {complete: complete.roles, missing: missing.roles,
        ambiguous: ambiguous.roles};
    }""")
    assert result["complete"]["left_arm"]["available"]
    assert result["complete"]["right_arm"]["available"]
    assert not result["missing"]["left_arm"]["available"]
    assert not result["missing"]["right_arm"]["available"]
    assert result["missing"]["left_arm"]["reasons"][0] == "no_bilateral_pair"
    assert result["missing"]["right_arm"]["reasons"][0] == "no_bilateral_pair"
    assert result["ambiguous"]["left_arm"]["reasons"][0] == "ambiguous_pair"
    assert result["ambiguous"]["right_arm"]["reasons"][0] == "ambiguous_pair"


def test_humanoid_leg_fitter_prefers_complete_foot_reaching_paths(module_page):
    result = module_page.evaluate("""async () => {
      const {suggestHumanoidLimbMappings} = await import(
        './js/mesh/weight-rig-humanoid.js');
      const points = new Map([
        [0, [0, 1, 0]],
        [1, [-.45, .8, 0]], [2, [-.48, .3, 0]], [3, [-.5, -.1, 0]],
        [4, [-.52, -.5, 0]],
        [5, [.45, .8, 0]], [6, [.48, .3, 0]], [7, [.5, -.1, 0]],
        [8, [.52, -.5, 0]],
      ]);
      const parentById = {0: null, 1: 0, 2: 1, 3: 2, 4: 3,
        5: 0, 6: 5, 7: 6, 8: 7};
      const childrenById = Object.fromEntries(Object.keys(parentById)
        .map(id => [id, []]));
      Object.entries(parentById).forEach(([child, parent]) => {
        if (parent !== null) childrenById[parent].push(Number(child));
      });
      const component = {rootId: 0, nodeIds: [...points.keys()],
        parentById, childrenById};
      const rig = {
        joints: [...points.keys()].map(jointId => ({jointId,
          restPivot: points.get(jointId)})),
        components: [component],
        componentByJointId: new Map([...points.keys()].map(id => [id, 0])),
        centerByJointId: points, jointPivotByJointId: points,
        restContinuationChildByJointId: new Map([
          [1, 2], [2, 3], [3, 4], [5, 6], [6, 7], [7, 8],
        ]),
      };
      const detection = suggestHumanoidLimbMappings({
        rig, characterForward: [0, 0, 1], debug: true,
      });
      const candidates = detection.debug.candidates.filter(candidate =>
        candidate.role === 'left_leg' && candidate.anchorJointId === 1);
      const short = candidates.find(candidate => candidate.endJointId === 3);
      const complete = candidates.find(candidate => candidate.endJointId === 4);
      return {short, complete, selected: detection.roles.left_leg};
    }""")
    assert result["short"]["pathJointIds"] == [1, 2, 3]
    assert result["complete"]["pathJointIds"] == [1, 2, 3, 4]
    assert result["complete"]["score"] > result["short"]["score"]
    assert result["complete"]["pathMetrics"]["insideCorridorFraction"] > .9
    assert result["complete"]["footJointId"] == 4
    assert result["complete"]["kneeJointId"] in (2, 3)


def test_humanoid_legs_reject_symmetric_central_stomach_decoys(module_page):
    result = module_page.evaluate("""async () => {
      const {suggestHumanoidLimbMappings} = await import(
        './js/mesh/weight-rig-humanoid.js');
      const points = new Map([
        [0, [0, 1.8, 0]],
        [100, [-.02, 1.45, 0]], [101, [-.05, 1.1, 0]],
        [1, [-.55, .8, 0]], [2, [-.58, .15, 0]], [3, [-.6, -.5, 0]],
        [200, [.02, 1.45, 0]], [201, [.05, 1.1, 0]],
        [4, [.55, .8, 0]], [5, [.58, .15, 0]], [6, [.6, -.5, 0]],
        [10, [-.1, .75, 0]], [11, [-.12, .05, 0]],
        [12, [.1, .75, 0]], [13, [.12, .05, 0]],
      ]);
      const parentById = {0: null, 100: 0, 101: 100, 1: 101, 2: 1, 3: 2,
        200: 0, 201: 200, 4: 201, 5: 4, 6: 5,
        10: 0, 11: 10, 12: 0, 13: 12};
      const childrenById = Object.fromEntries(Object.keys(parentById)
        .map(id => [id, []]));
      Object.entries(parentById).forEach(([child, parent]) => {
        if (parent !== null) childrenById[parent].push(Number(child));
      });
      const component = {rootId: 0, nodeIds: [...points.keys()],
        parentById, childrenById};
      const rig = {
        joints: [...points.keys()].map(jointId => ({jointId,
          restPivot: points.get(jointId),
          evidence: [10, 11, 12, 13].includes(jointId)
            ? {affectedVertexCount: 500} : {affectedVertexCount: 2}})),
        components: [component],
        componentByJointId: new Map([...points.keys()].map(id => [id, 0])),
        centerByJointId: points, jointPivotByJointId: points,
        restContinuationChildByJointId: new Map([
          [100, 101], [101, 1], [1, 2], [2, 3],
          [200, 201], [201, 4], [4, 5], [5, 6], [10, 11], [12, 13],
        ]),
      };
      const detection = suggestHumanoidLimbMappings({
        rig, characterForward: [0, 0, 1], debug: true,
      });
      const leftAncestry = detection.debug.candidates.find(candidate =>
        candidate.role === 'left_leg' && candidate.anchorJointId === 1);
      return {
        legs: {
          left: detection.roles.left_leg,
          right: detection.roles.right_leg,
        },
        leftAncestry: leftAncestry && {
          ancestors: leftAncestry.ancestorJointIds,
          hip: leftAncestry.hipJointId,
          knee: leftAncestry.kneeJointId,
          foot: leftAncestry.footJointId,
        },
        rejected: detection.debug.rejectionCounts,
      };
    }""")
    assert result["legs"]["left"]["available"]
    assert result["legs"]["right"]["available"]
    assert result["legs"]["left"]["hipJointId"] == 1
    assert result["legs"]["right"]["hipJointId"] == 4
    assert result["legs"]["left"]["kneeJointId"] == 2
    assert result["legs"]["right"]["kneeJointId"] == 5
    assert result["legs"]["left"]["footJointId"] == 3
    assert result["legs"]["right"]["footJointId"] == 6
    assert result["leftAncestry"]["ancestors"][:3] == [101, 100, 0]
    assert result["rejected"]["left_leg:leg_quality_too_low"] > 0
    assert result["rejected"]["right_leg:leg_quality_too_low"] > 0


def test_limb_forward_conversion_inverts_non_identity_base_orientation(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {characterForwardFromOrientation} = await import(
        './js/mesh/weight-rig-ik.js');
      const upright = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(1, 0, 0), -Math.PI / 2);
      const facing = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 1, 0), Math.PI / 2);
      const base = facing.clone().multiply(upright);
      const turns = [0, 90, 180, 270].map(degrees => {
        const user = new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(degrees));
        const orientation = user.clone().multiply(base);
        const forward = characterForwardFromOrientation({
          orientation, userRotation: user,
        });
        return forward?.toArray() || null;
      });
      return {turns, orientation: base.toArray()};
    }""")
    expected = [-1, 0, 0]
    assert len(result["turns"]) == 4
    for turn in result["turns"]:
        assert turn == pytest.approx(expected)


def test_humanoid_detection_is_coordinate_invariant(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {characterAxesFromOrientation} = await import('./js/mesh/weight-rig-ik.js');
      const {suggestHumanoidLimbMappings} = await import(
        './js/mesh/weight-rig-humanoid.js');
      const canonical = new Map([
        [0, [0, 1, 0]],
        [1, [-.55, 1.55, 0]], [2, [-1.05, 1.35, 0]], [3, [-1.5, 1.1, 0]],
        [4, [.55, 1.55, 0]], [5, [1.05, 1.35, 0]], [6, [1.5, 1.1, 0]],
        [7, [-.35, .8, 0]], [8, [-.4, .2, 0]], [9, [-.42, -.4, 0]],
        [10, [.35, .8, 0]], [11, [.4, .2, 0]], [12, [.42, -.4, 0]],
      ]);
      const parentById = {0: null, 1: 0, 2: 1, 3: 2, 4: 0, 5: 4, 6: 5,
        7: 0, 8: 7, 9: 8, 10: 0, 11: 10, 12: 11};
      const continuation = new Map([
        [1, 2], [2, 3], [4, 5], [5, 6], [7, 8], [8, 9], [10, 11], [11, 12],
      ]);
      const makeRig = rotation => {
        const points = new Map([...canonical].map(([id, point]) => [id,
          new THREE.Vector3(...point).applyQuaternion(rotation).toArray()]));
        const centers = new Map([...points].map(([id, point]) => [id,
          point.map((value, index) => value + [.4, -.2, .15][index])]));
        const childrenById = Object.fromEntries(Object.keys(parentById).map(id => [id, []]));
        Object.entries(parentById).forEach(([child, parent]) => {
          if (parent !== null) childrenById[parent].push(Number(child));
        });
        const component = {rootId: 0, nodeIds: [...points.keys()], parentById, childrenById};
        return {
          joints: [...points.keys()].reverse().map(jointId => ({jointId,
            restPivot: points.get(jointId), restCenter: centers.get(jointId)})),
          components: [component], componentByJointId: new Map([...points.keys()].map(id => [id, 0])),
          centerByJointId: centers, jointPivotByJointId: points,
          restContinuationChildByJointId: continuation,
        };
      };
      const identity = new THREE.Quaternion();
      const sourceRotation = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(1, 0, 0), Math.PI / 2);
      const upright = sourceRotation.clone().invert();
      const userTurn = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 1, 0), Math.PI / 2);
      const canonicalAxes = characterAxesFromOrientation({
        orientation: identity, userRotation: identity,
      });
      const sourceAxes = characterAxesFromOrientation({
        orientation: upright, userRotation: identity,
      });
      const turnedAxes = characterAxesFromOrientation({
        orientation: userTurn, userRotation: userTurn,
      });
      const detect = (rig, axes) => suggestHumanoidLimbMappings({
        rig, axes, characterForward: axes.forward, debug: true,
      });
      const first = detect(makeRig(identity), canonicalAxes);
      const second = detect(makeRig(sourceRotation), sourceAxes);
      const third = detect(makeRig(identity), turnedAxes);
      const structure = item => ({
        families: Object.fromEntries(['left_arm', 'right_arm', 'left_leg', 'right_leg']
          .map(role => [role, item.debug.families[role].map(family => ({
            representative: family.representative.anchorJointId,
            alternatives: family.alternatives.map(candidate => candidate.anchorJointId),
          }))])),
        pairs: Object.fromEntries(['arms', 'legs'].map(kind => {
          const pair = item.debug.pairs[kind];
          return [kind, {
            best: pair.best && [pair.best.left.anchorJointId, pair.best.right.anchorJointId],
            runnerUp: pair.runnerUp && [pair.runnerUp.leftAnchorJointId,
              pair.runnerUp.rightAnchorJointId],
          }];
        })),
      });
      return {
        axes: {
          canonical: {up: canonicalAxes.up.toArray(), forward: canonicalAxes.forward.toArray(),
            right: canonicalAxes.right.toArray()},
          source: {up: sourceAxes.up.toArray(), forward: sourceAxes.forward.toArray(),
            right: sourceAxes.right.toArray()},
        },
        roles: [first, second, third].map(item => item.roles),
        confidences: [first, second, third].map(item =>
          ['left_arm', 'right_arm', 'left_leg', 'right_leg']
            .map(role => item.roles[role].confidence)),
        structures: [first, second, third].map(structure),
      };
    }""")
    assert result["axes"]["source"]["up"] == pytest.approx([0, 0, 1])
    assert result["axes"]["source"]["forward"] == pytest.approx([0, -1, 0])
    assert result["axes"]["source"]["right"] == pytest.approx([1, 0, 0])
    expected_anchors = {
        "left_arm": 1, "right_arm": 4, "left_leg": 7, "right_leg": 10,
    }
    for roles in result["roles"]:
        assert all(roles[role]["available"] for role in expected_anchors)
        assert {role: roles[role]["anchorJointId"] for role in expected_anchors} == expected_anchors
    assert result["confidences"][0] == result["confidences"][1] == result["confidences"][2]
    assert result["structures"][0] == result["structures"][1] == result["structures"][2]


def test_humanoid_scaffold_and_debug_scores_are_model_wide(module_page):
    result = module_page.evaluate("""async () => {
      const humanoid = await import('./js/mesh/weight-rig-humanoid.js');
      const points = new Map([
        [0, [0, 1, 0]],
        [1, [-.6, 1.5, 0]], [2, [-1.1, 1.3, 0]], [3, [-1.5, 1.1, 0]],
        [4, [.6, 1.5, 0]], [5, [1.1, 1.3, 0]], [6, [1.5, 1.1, 0]],
        [7, [-.35, .8, 0]], [8, [-.4, .2, 0]], [9, [-.42, -.4, 0]],
        [10, [.35, .8, 0]], [11, [.4, .2, 0]], [12, [.42, -.4, 0]],
        [20, [0, .9, .4]], [21, [0, .7, .4]],
      ]);
      const parentById = {0: null, 1: 0, 2: 1, 3: 2, 4: 0, 5: 4, 6: 5,
        7: 0, 8: 7, 9: 8, 10: 0, 11: 10, 12: 11,
        20: null, 21: 20};
      const childrenById = Object.fromEntries(Object.keys(parentById).map(id => [id, []]));
      Object.entries(parentById).forEach(([child, parent]) => {
        if (parent !== null) childrenById[parent].push(Number(child));
      });
      const components = [
        {rootId: 0, nodeIds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
          parentById, childrenById},
        {rootId: 20, nodeIds: [20, 21], parentById: {20: null, 21: 20},
          childrenById: {20: [21], 21: []}},
      ];
      const rig = {
        joints: [...points.keys()].map(jointId => ({jointId,
          restPivot: points.get(jointId)})),
        components,
        componentByJointId: new Map([
          ...[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(id => [id, 0]),
          [20, 1], [21, 1],
        ]),
        jointPivotByJointId: points,
        centerByJointId: points,
        restContinuationChildByJointId: new Map([
          [1, 2], [2, 3], [4, 5], [5, 6], [7, 8], [8, 9],
          [10, 11], [11, 12], [20, 21],
        ]),
      };
      const frame = humanoid.buildHumanoidSemanticFrame({
        rig, characterForward: [0, 0, 1],
      });
      const scaffold = humanoid.buildHumanoidScaffold({rig, frame});
      const suggestion = humanoid.suggestHumanoidLimbMappings({
        rig, characterForward: [0, 0, 1], debug: true,
      });
      const candidate = suggestion.debug.topCandidatesByRole.left_arm[0];
      return {
        frame: {low: frame.lowHeight, high: frame.highHeight, height: frame.height},
        scaffold: {
          centerline: scaffold.centerline,
          shoulderBand: scaffold.shoulderBand,
          hipBand: scaffold.hipBand,
          bodyDepth: scaffold.bodyDepth,
        },
          debug: {
            hasWholeBody: suggestion.debug.wholeBody.combinationCount > 0,
            candidateKeys: ['ancestryScore', 'componentBodyScore', 'corridorScore',
              'poseAngleScore', 'pathSupportScore', 'proximalSupport',
              'branchDominanceScore']
            .every(key => Number.isFinite(candidate[key])),
        },
        removedPoseApi: typeof humanoid.aPoseDirectionFromAxes === 'undefined',
      };
    }""")
    assert result["frame"]["height"] > 1.5
    assert result["frame"]["low"] < 1
    assert result["frame"]["high"] > 1.4
    assert result["scaffold"]["shoulderBand"]["minHeight01"] < \
        result["scaffold"]["shoulderBand"]["expectedHeight01"] < \
        result["scaffold"]["shoulderBand"]["maxHeight01"]
    assert result["scaffold"]["hipBand"]["minHeight01"] < \
        result["scaffold"]["hipBand"]["expectedHeight01"] < \
        result["scaffold"]["hipBand"]["maxHeight01"]
    assert result["debug"] == {"hasWholeBody": True, "candidateKeys": True}
    assert result["removedPoseApi"]


def test_rig_path_between_joint_ids_is_undirected_and_component_scoped(module_page):
    result = module_page.evaluate("""async () => {
      const {rigPathBetweenJointIds} = await import(
        './js/mesh/weight-rig-ik.js');
      const first = {
        rootId: 0, nodeIds: [0, 1, 2, 3],
        parentById: {0: null, 1: 0, 2: 1, 3: 2},
      };
      const second = {rootId: 9, nodeIds: [9, 10],
        parentById: {9: null, 10: 9}};
      const rig = {
        components: [first, second],
        componentByJointId: new Map([[0, 0], [1, 0], [2, 0], [3, 0],
          [9, 1], [10, 1]]),
      };
      return {
        forward: rigPathBetweenJointIds({rig, jointA: 1, jointB: 3}),
        reverse: rigPathBetweenJointIds({rig, jointA: 3, jointB: 1}),
        disconnected: rigPathBetweenJointIds({rig, jointA: 1, jointB: 10}),
      };
    }""")
    assert result["forward"] == {
        "connected": True, "jointIds": [1, 2, 3], "edgeCount": 2,
    }
    assert result["reverse"] == {
        "connected": True, "jointIds": [3, 2, 1], "edgeCount": 2,
    }
    assert result["disconnected"] == {
        "connected": False, "jointIds": [], "edgeCount": 0,
        "reason": "not_connected",
    }


def test_limb_path_descriptors_measure_forward_backtracking_symmetrically(module_page):
    result = module_page.evaluate("""async () => {
      const {resolveLimbPathCandidates} = await import('./js/mesh/weight-rig-ik.js');
      const makeRig = depthSign => {
        const points = new Map([
          [0, [0, 1, 0]], [1, [-.55, 1.5, 0]],
          [2, [-.9, 1.4, .3 * depthSign]],
          [3, [-1.25, 1.25, 0]], [4, [-1.5, 1.1, 0]],
        ]);
        const component = {
          rootId: 0, nodeIds: [...points.keys()],
          parentById: {0: null, 1: 0, 2: 1, 3: 2, 4: 3},
          childrenById: {0: [1], 1: [2], 2: [3], 3: [4], 4: []},
        };
        return {
          joints: [...points.keys()].map(jointId => ({jointId,
            restPivot: points.get(jointId)})),
          components: [component],
          componentByJointId: new Map([...points.keys()].map(id => [id, 0])),
          jointPivotByJointId: points, centerByJointId: points,
          restContinuationChildByJointId: new Map([[1, 2], [2, 3], [3, 4]]),
        };
      };
      const inspect = depthSign => {
        const resolved = resolveLimbPathCandidates({
          rig: makeRig(depthSign), anchorJointId: 1, role: 'left_arm',
          characterForward: [0, 0, 1],
        });
        const metrics = resolved.candidates[0].pathMetrics;
        return {
          path: resolved.candidates[0].pathJointIds,
          score: resolved.candidates[0].score,
          netForward: metrics.netForward,
          absoluteForwardTravel: metrics.absoluteForwardTravel,
          forwardBacktracking: metrics.forwardBacktracking,
          endpointDepth: metrics.endpointDepth,
          corridorScore: metrics.corridorScore,
        };
      };
      return {positive: inspect(1), negative: inspect(-1)};
    }""")
    positive = result["positive"]
    negative = result["negative"]
    assert positive["path"] == negative["path"] == [1, 2, 3, 4]
    assert positive["netForward"] == pytest.approx(0)
    assert negative["netForward"] == pytest.approx(0)
    assert positive["absoluteForwardTravel"] > 0
    assert positive["forwardBacktracking"] == pytest.approx(
        positive["absoluteForwardTravel"])
    assert negative["forwardBacktracking"] == pytest.approx(
        negative["absoluteForwardTravel"])
    assert positive["absoluteForwardTravel"] == pytest.approx(
        negative["absoluteForwardTravel"])
    assert positive["corridorScore"] == pytest.approx(negative["corridorScore"])


def test_compact_limb_detection_stops_at_terminal_branch(module_page):
    result = module_page.evaluate("""async () => {
      const ik = await import('./js/mesh/weight-rig-ik.js');
      const component = {
        rootId: 0, nodeIds: [0, 1, 2, 3, 4, 5, 6],
        parentById: {0: null, 1: 0, 2: 1, 3: 2,
          4: 3, 5: 3, 6: 3},
        childrenById: {0: [1], 1: [2], 2: [3], 3: [4, 5, 6],
          4: [], 5: [], 6: []},
      };
      const points = new Map([
        [0, [-1, 0, 0]], [1, [0, 0, 0]], [2, [1, 0, 0]],
        [3, [2, 0, 0]], [4, [2, .2, 0]], [5, [2, 0, .2]],
        [6, [2, -.2, 0]],
      ]);
      const rig = {
        components: [component], componentByJointId: new Map(
          component.nodeIds.map(id => [id, 0])),
        jointPivotByJointId: points, centerByJointId: points,
        restContinuationChildByJointId: new Map([
          [0, 1], [1, 2], [2, 3], [3, 4],
        ]),
        restFrameByJointId: new Map(),
      };
      const arm = ik.detectLimbPath({
        rig, anchorJointId: 1, role: 'left_arm',
      });
      const leg = ik.detectLimbPath({
        rig, anchorJointId: 1, role: 'left_leg',
      });
      return {
        arm: {path: arm.pathJointIds, bend: arm.bendJointId, end: arm.endJointId},
        leg: {path: leg.pathJointIds, bend: leg.bendJointId, end: leg.endJointId},
      };
    }""")
    assert result == {
        "arm": {"path": [1, 2, 3], "bend": 2, "end": 3},
        "leg": {"path": [1, 2, 3], "bend": 2, "end": 3},
    }


def test_end_override_can_recover_a_different_descendant_branch(module_page):
    result = module_page.evaluate("""async () => {
      const ik = await import('./js/mesh/weight-rig-ik.js');
      const component = {
        rootId: 0, nodeIds: [0, 1, 2, 3, 4, 5, 6],
        parentById: {0: null, 1: 0, 2: 1, 3: 2, 4: 3,
          5: 1, 6: 5},
        childrenById: {0: [1], 1: [2, 5], 2: [3], 3: [4],
          4: [], 5: [6], 6: []},
      };
      const points = new Map([
        [0, [-1, 0, 0]], [1, [0, 0, 0]], [2, [1, 0, 0]],
        [3, [2, 0, 0]], [4, [3, 0, 0]], [5, [1, 0, 1]],
        [6, [2, 0, 1]],
      ]);
      const rig = {
        components: [component], componentByJointId: new Map(
          component.nodeIds.map(id => [id, 0])),
        jointPivotByJointId: points, centerByJointId: points,
        restContinuationChildByJointId: new Map([
          [0, 1], [1, 2], [2, 3], [3, 4], [5, 6],
        ]),
        restFrameByJointId: new Map(),
      };
      const automatic = ik.detectLimbPath({
        rig, anchorJointId: 1, role: 'left_arm',
      });
      const recoveredPath = [1, 5, 6];
      return {
        automaticPath: automatic.pathJointIds,
        recoveredBend: ik.selectLimbBendJoint(rig, recoveredPath),
        recoveredPath,
      };
    }""")
    assert result == {
        "automaticPath": [1, 2, 3, 4],
        "recoveredPath": [1, 5, 6],
        "recoveredBend": 5,
    }


def test_limb_bend_direction_uses_scale_relative_evidence_and_role_fallback(
        module_page):
    result = module_page.evaluate("""async () => {
      const ik = await import('./js/mesh/weight-rig-ik.js');
      const component = {
        rootId: 0, nodeIds: [0, 1, 2, 3],
        parentById: {0: null, 1: 0, 2: 1, 3: 2},
        childrenById: {0: [1], 1: [2], 2: [3], 3: []},
      };
      const rigFor = bendZ => {
        const points = new Map([
          [0, [-1, 0, 0]], [1, [0, 0, 0]],
          [2, [1, 0, bendZ]], [3, [2, 0, 0]],
        ]);
        return {
          components: [component], componentByJointId: new Map(
            component.nodeIds.map(id => [id, 0])),
          jointPivotByJointId: points, centerByJointId: points,
          restContinuationChildByJointId: new Map([[0, 1], [1, 2], [2, 3]]),
          restFrameByJointId: new Map(),
        };
      };
      const detect = (bendZ, role) => ik.detectLimbPath({
        rig: rigFor(bendZ), anchorJointId: 1, role,
        characterForward: [0, 0, 1],
      });
      const meaningful = detect(.2, 'left_arm');
      const noisyPositive = detect(.000001, 'left_arm');
      const noisyNegative = detect(-.000001, 'right_arm');
      const leftLeg = detect(.000001, 'left_leg');
      const rightLeg = detect(-.000001, 'right_leg');
      return {meaningful, noisyPositive, noisyNegative, leftLeg, rightLeg};
    }""")
    assert result["meaningful"]["bendDirectionSource"] == "rest-offset"
    assert result["meaningful"]["bendDirection"] == pytest.approx([0, 0, 1])
    assert result["meaningful"]["bendDirectionStrength"] == pytest.approx(.1)
    assert result["noisyPositive"]["bendDirectionSource"] == (
        "semantic-character-facing")
    assert result["noisyPositive"]["bendDirection"] == pytest.approx([0, 0, 1])
    assert result["noisyNegative"]["bendDirection"] == pytest.approx([0, 0, 1])
    assert result["leftLeg"]["bendDirection"] == pytest.approx([0, 0, -1])
    assert result["rightLeg"]["bendDirection"] == pytest.approx([0, 0, -1])


def test_limb_bend_direction_helper_uses_final_override_controls(module_page):
    result = module_page.evaluate("""async () => {
      const ik = await import('./js/mesh/weight-rig-ik.js');
      const points = new Map([
        [1, [0, 0, 0]], [2, [1, 0, .2]], [3, [1, 0, -.3]],
        [4, [2, 0, 0]], [5, [3, 0, 0]],
      ]);
      const rig = {
        jointPivotByJointId: points, centerByJointId: points,
        restFrameByJointId: new Map(),
      };
      const automatic = ik.resolveLimbBendDirection({
        rig, pathJointIds: [1, 2, 3, 4], anchorJointId: 1,
        bendJointId: 2, endJointId: 4, role: 'left_arm',
      });
      const bendOverride = ik.resolveLimbBendDirection({
        rig, pathJointIds: [1, 2, 3, 4], anchorJointId: 1,
        bendJointId: 3, endJointId: 4, role: 'left_arm',
      });
      const endOverride = ik.resolveLimbBendDirection({
        rig, pathJointIds: [1, 2, 3, 4, 5], anchorJointId: 1,
        bendJointId: 2, endJointId: 5, role: 'left_arm',
      });
      return {
        automatic: {...automatic,
          bendDirection: automatic.bendDirection?.toArray() || null},
        bendOverride: {...bendOverride,
          bendDirection: bendOverride.bendDirection?.toArray() || null},
        endOverride: {...endOverride,
          bendDirection: endOverride.bendDirection?.toArray() || null},
      };
    }""")
    assert result["automatic"]["bendDirection"] == pytest.approx([0, 0, 1])
    assert result["bendOverride"]["bendDirection"] == pytest.approx(
        [0, 0, -1])
    assert result["endOverride"]["bendDirection"] == pytest.approx(
        [0, 0, 1])
    assert result["automatic"]["bendDirectionSource"] == "rest-offset"
    assert result["bendOverride"]["bendDirectionSource"] == "rest-offset"


def test_rig_joint_picker_projects_current_pivots_and_uses_nearest_hit(
        module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {findNearestRigJoint, projectRigPointToClient} = await import(
        './js/scene/rig-overlay-controller.js');
      const camera = new THREE.PerspectiveCamera(90, 1, .1, 100);
      camera.position.set(0, 0, 5);
      camera.lookAt(0, 0, 0);
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld(true);
      const canvas = {
        clientWidth: 200, clientHeight: 200,
        getBoundingClientRect: () => ({left: 10, top: 20, width: 200, height: 200}),
      };
      const center = projectRigPointToClient({
        point: [0, 0, 0], camera, canvas,
      });
      const posed = projectRigPointToClient({
        point: [.2, .1, 0], camera, canvas,
      });
      const near = findNearestRigJoint({
        candidates: [
          {jointId: 12, pivot: [.24, .1, 0]},
          {jointId: 13, pivot: [.2, .1, 0]},
          {jointId: 14, pivot: [2, 0, 0]},
        ], pointer: posed, camera, canvas, hitRadius: 14,
      });
      const outside = findNearestRigJoint({
        candidates: [{jointId: 13, pivot: [.2, .1, 0]}],
        pointer: {x: posed.x + 20, y: posed.y}, camera, canvas,
        hitRadius: 14,
      });
      return {center, posed, nearest: near?.jointId || null,
        candidateCount: near?.candidates?.length || 0, outside};
    }""")
    assert result["center"]["x"] == pytest.approx(110)
    assert result["center"]["y"] == pytest.approx(120)
    assert result["nearest"] == 13
    assert result["candidateCount"] == 2
    assert result["outside"] is None


def test_rig_overlay_reuses_forest_buffers_and_model_frame(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const model = new THREE.Object3D();
      scene.add(model);
      let state = {
        visible: true, selectedJointId: null,
        model: {
          key: 'model-rig', structureRevision: 1,
          joints: [1, 2, 3].map((jointId, index) => ({
            jointId, restCenter: [index, 0, 0],
            restPivot: [Math.max(0, index - .5), 0, 0],
          })),
          components: [{componentId: 0, rootId: 1, nodeIds: [1, 2, 3]}],
          forestEdges: [
            {jointA: 1, jointB: 2, parentId: 1, childId: 2},
            {jointA: 2, jointB: 3, parentId: 2, childId: 3},
          ],
          humanoidControlRig: {confidence: 'high', controls: Object.fromEntries([
            ['chest', [0, 2, 0]], ['pelvis', [0, 1, 0]],
            ['leftShoulder', [-.2, 1.8, 0]], ['leftElbow', [-.5, 1.6, 0]],
            ['leftHand', [-.9, 1.5, 0]], ['rightShoulder', [.2, 1.8, 0]],
            ['rightElbow', [.5, 1.6, 0]], ['rightHand', [.9, 1.5, 0]],
            ['leftHip', [-.15, 1, 0]], ['leftKnee', [-.2, .5, 0]],
            ['leftFoot', [-.25, 0, 0]], ['rightHip', [.15, 1, 0]],
            ['rightKnee', [.2, .5, 0]], ['rightFoot', [.25, 0, 0]],
          ].map(([key, position]) => [key, {position}])), paths: {}},
          poseRotationByJointId: {},
        },
      };
      const controller = createRigOverlayController({
        scene, getMeshes: () => [model], getRigState: () => state,
      });
      controller.refresh(state);
      const initial = controller.getDebugState();
      state = {...state, selectedJointId: 1};
      controller.refresh(state);
      const selectedRoot = controller.getDebugState();
      model.position.x = 4;
      window.dispatchEvent(new CustomEvent(
        'mod-viewer-model-transform-changed', {detail: {}}));
      const afterTransform = controller.getDebugState();
      state = {...state, visible: false, selectedJointId: null};
      controller.refresh(state);
      state = {...state, visible: true};
      controller.refresh(state);
      const shownAgain = controller.getDebugState();
      state = {...state, model: {...state.model,
        humanoidControlRig: {...state.model.humanoidControlRig, available: false}}};
      controller.refresh(state);
      const unavailable = controller.getDebugState();
      controller.dispose();
      return {initial, selectedRoot, afterTransform, shownAgain, unavailable};
    }""")
    assert result["initial"]["staticObjectCount"] == 4
    assert result["initial"]["nodeCount"] == 3
    assert result["initial"]["edgeCount"] == 2
    assert result["initial"]["jointCount"] == 3
    assert result["initial"]["humanoidOverlayVisible"]
    assert result["initial"]["humanoidSegmentCount"] == 13
    assert result["initial"]["humanoidLandmarkCount"] == 14
    assert result["initial"]["rebuildCount"] == 1
    assert result["selectedRoot"]["selectedJointId"] == 1
    assert result["selectedRoot"]["rebuildCount"] == 1
    assert result["afterTransform"]["rebuildCount"] == 1
    assert result["afterTransform"]["modelFrameUpdateCount"] == \
        result["initial"]["modelFrameUpdateCount"] + 2
    assert result["shownAgain"]["rebuildCount"] == 1
    assert result["shownAgain"]["selectedJointId"] is None
    assert not result["unavailable"]["humanoidOverlayVisible"]


def test_rig_overlay_builds_all_joints_and_toggles_visibility(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const model = new THREE.Object3D();
      scene.add(model);
      const canvas = document.createElement('canvas');
      document.body.appendChild(canvas);
      let state = {
        selectedJointId: 2,
        jointPickIntent: null,
        model: {
          key: 'model-rig', structureRevision: 1,
          joints: [1, 2, 3, 4].map((jointId, index) => ({
            jointId, restCenter: [index, 0, 0], restPivot: [index, 0, 0],
          })),
          components: [{componentId: 0, rootId: 1, nodeIds: [1, 2, 3, 4],
            parentById: {1: null, 2: 1, 3: 2, 4: 3},
            childrenById: {1: [2], 2: [3], 3: [4], 4: []}}],
          forestEdges: [
            {parentId: 1, childId: 2}, {parentId: 2, childId: 3},
            {parentId: 3, childId: 4},
          ], poseRotationByJointId: {},
        },
      };
      const controller = createRigOverlayController({
        scene, canvas, getMeshes: () => [model], getRigState: () => state,
      });
      controller.refresh(state);
      const before = controller.getDebugState();
      state = {...state, jointPickIntent: {type: 'limb-anchor', role: 'left_arm'}};
      controller.refresh(state);
      const during = controller.getDebugState();
      state = {...state, jointPickIntent: null};
      controller.refresh(state);
      const after = controller.getDebugState();
      controller.dispose();
      canvas.remove();
      return {before, during, after};
    }""")
    assert result["before"]["nodeCount"] == 4
    assert result["before"]["staticVisible"] is False
    assert result["during"]["nodeCount"] == 4
    assert result["during"]["staticVisible"] is True
    assert result["after"]["nodeCount"] == 4
    assert result["after"]["staticVisible"] is False


def test_rig_joint_picker_owns_plain_left_and_allows_alt_orbit(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController, projectRigPointToClient} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(90, 1, .1, 100);
      camera.position.set(0, 0, 5);
      camera.lookAt(0, 0, 0);
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld(true);
      const canvas = document.createElement('canvas');
      canvas.getBoundingClientRect = () => ({left: 10, top: 20,
        width: 200, height: 200});
      document.body.appendChild(canvas);
      const pivots = new Map([[1, [0, 0, 0]], [2, [.3, 0, 0]]]);
      const source = {
        key: 'model-rig', structureRevision: 1,
        joints: [1, 2].map(jointId => ({jointId,
          restCenter: pivots.get(jointId), restPivot: pivots.get(jointId)})),
        components: [{componentId: 0, rootId: 1, nodeIds: [1, 2],
          parentById: {1: null, 2: 1}, childrenById: {1: [2], 2: []}}],
        forestEdges: [{parentId: 1, childId: 2}],
        poseRotationByJointId: {},
      };
      let state = {selectedJointId: null,
        jointPickIntent: {type: 'limb-anchor', role: 'left_arm'},
        model: source};
      const picked = [];
      const surface = [];
      let arcballDown = 0;
      let arcballUp = 0;
      const controller = createRigOverlayController({
        scene, camera, canvas, getMeshes: () => [], getRigState: () => state,
        getRigJointPoseFrame: id => ({pivot: pivots.get(Number(id))}),
        onRigJointPicked: id => picked.push(id),
        onRigSurfacePickRequested: (point, intent) => surface.push({point, intent}),
      });
      controller.refresh(state);
      canvas.addEventListener('pointerdown', () => { arcballDown += 1; });
      canvas.addEventListener('pointerup', () => { arcballUp += 1; });
      const center = projectRigPointToClient({point: [0, 0, 0], camera, canvas});
      const dispatch = (type, id, x, y, altKey = false) => canvas.dispatchEvent(
        new PointerEvent(type, {bubbles: true, button: 0, pointerId: id,
          clientX: x, clientY: y, altKey}));
      dispatch('pointerdown', 1, center.x, center.y);
      dispatch('pointerup', 1, center.x, center.y);
      dispatch('pointerdown', 2, center.x, center.y, true);
      dispatch('pointermove', 2, center.x + 20, center.y, true);
      dispatch('pointerup', 2, center.x + 20, center.y, true);
      dispatch('pointerdown', 3, center.x, center.y);
      dispatch('pointerup', 3, center.x + 8, center.y);
      dispatch('pointermove', 4, center.x + 18, center.y);
      const outside = controller.getDebugState();
      dispatch('pointermove', 5, center.x, center.y);
      const firstHover = controller.getDebugState();
      dispatch('pointermove', 6, center.x + 4, center.y);
      const hysteresis = controller.getDebugState();
      dispatch('pointermove', 7, center.x + 10, center.y);
      const switched = controller.getDebugState();
      dispatch('pointermove', 8, center.x + 30, center.y);
      dispatch('pointerdown', 9, center.x + 50, center.y);
      dispatch('pointerup', 9, center.x + 50, center.y);
      const cleared = controller.getDebugState();
      controller.dispose();
      canvas.remove();
      return {picked, surface, arcballDown, arcballUp, outside, firstHover,
        hysteresis, switched, cleared};
    }""")
    assert result["picked"] == [1]
    assert result["surface"] == [{
        "point": {"clientX": 160, "clientY": 120},
        "intent": {"type": "limb-anchor", "role": "left_arm"},
    }]
    assert result["arcballDown"] == 1
    assert result["arcballUp"] == 1
    assert result["outside"]["hoveredJointId"] is None
    assert result["firstHover"]["hoveredJointId"] == 1
    assert result["hysteresis"]["hoveredJointId"] == 1
    assert result["switched"]["hoveredJointId"] == 2
    assert result["cleared"]["hoveredJointId"] is None


def test_rig_overlay_hides_static_geometry_until_joint_picking(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const model = new THREE.Object3D();
      scene.add(model);
      const source = {
        sourceKey: 'model-rig',
        joints: [0, 1, 2, 3].map(jointId => ({
          jointId, restCenter: [jointId === 3 ? 10 : jointId, 0, 0],
          restPivot: [jointId === 3 ? 10 : jointId, 0, 0],
        })),
        components: [
          {componentId: 0, rootId: 0, nodeIds: [0, 1, 2],
            parentById: {0: null, 1: 0, 2: 1},
            childrenById: {0: [1], 1: [2], 2: []}},
          {componentId: 1, rootId: 3, nodeIds: [3],
            parentById: {3: null}, childrenById: {3: []}},
        ],
        forestEdges: [
          {jointA: 0, jointB: 1, parentId: 0, childId: 1},
          {jointA: 1, jointB: 2, parentId: 1, childId: 2},
        ],
      };
      let state = {selectedJointId: 1, jointPickIntent: null, model: source};
      const controller = createRigOverlayController({
        scene, getMeshes: () => [model], getRigState: () => state,
        getRigJointPoseFrame: () => null, setRigJointRotation: () => true,
      });
      controller.refresh(state);
      const hidden = controller.getDebugState();
      state = {...state, jointPickIntent: {type: 'selected-joint'}};
      controller.refresh(state);
      const picking = controller.getDebugState();
      state = {...state, jointPickIntent: null};
      controller.refresh(state);
      const hiddenAgain = controller.getDebugState();
      controller.dispose();
      return {hidden, picking, hiddenAgain};
    }""")
    assert result["hidden"]["nodeCount"] == 4
    assert result["hidden"]["edgeCount"] == 2
    assert result["hidden"]["staticVisible"] is False
    assert result["picking"]["nodeCount"] == 4
    assert result["picking"]["edgeCount"] == 2
    assert result["picking"]["staticVisible"] is True
    assert result["hiddenAgain"]["staticVisible"] is False


def test_rig_overlay_controls_detach_for_root_but_survive_hidden_overlay(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController, isRigTransformInteractionActive} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera();
      const canvas = document.createElement('canvas');
      const arcballActions = [];
      const arcballControls = {
        enabled: true,
        unsetMouseAction: button => arcballActions.push(['unset', button]),
        setMouseAction: (action, button) =>
          arcballActions.push(['set', action, button]),
      };
      const poseCalls = [];
      const finishCalls = [];
      const source = {
        key: 'model-rig', structureRevision: 1,
        joints: [1, 2].map((jointId, index) => ({
          jointId, restCenter: [index, 0, 0],
          restPivot: [index ? .5 : 0, 0, 0],
        })),
        components: [{componentId: 0, rootId: 1, nodeIds: [1, 2],
          parentById: {1: null, 2: 1}, childrenById: {1: [2], 2: []}}],
        forestEdges: [{jointA: 1, jointB: 2, parentId: 1, childId: 2}],
        poseRotationByJointId: {},
      };
      let state = {selectedJointId: null, rotationSnapDegrees: 15, model: source};
      const controller = createRigOverlayController({
        scene, camera, canvas, getRigState: () => state,
        getMeshes: () => [],
          getRigJointPoseFrame: () => ({
            pivot: [.5, .5, 0],
            parentRotation: [0, 0, Math.sin(Math.PI / 8),
              Math.cos(Math.PI / 8)],
            boneRotation: [0, 0, Math.sin(Math.PI / 8),
              Math.cos(Math.PI / 8)],
            restRotation: [0, 0, Math.sin(Math.PI / 8),
              Math.cos(Math.PI / 8)],
          }),
        arcballControls,
        setRigJointRotation: (...args) => poseCalls.push(args),
        finishRigJointPose: (...args) => finishCalls.push(args),
      });
      controller.refresh(state);
      const noSelection = controller.getDebugState();
      state = {...state, selectedJointId: 1};
      controller.refresh(state);
      await controller.ensureTransformControls();
      const root = controller.getDebugState();
      state = {...state, selectedJointId: 2};
      controller.refresh(state);
      const controls = await controller.ensureTransformControls();
      const nonRoot = controller.getDebugState();
      state = {...state, selectedJointId: null};
      controller.refresh(state);
      const cleared = controller.getDebugState();
      state = {...state, selectedJointId: 2};
      controller.refresh(state);
      const reselected = controller.getDebugState();
      controls.dispatchEvent({type: 'change'});
      const hoverPoseCount = poseCalls.length;
      controls.dispatchEvent({type: 'dragging-changed', value: true});
      const dragStarted = controller.getDebugState();
      const interactionDuringGizmo = isRigTransformInteractionActive();
      controls.object.quaternion.setFromAxisAngle(
        new THREE.Vector3(0, 0, 1), Math.PI * 2 / 3);
      controls.dispatchEvent({type: 'objectChange'});
      const objectChangePoseCount = poseCalls.length;
      const objectChangeLocal = poseCalls[0][1].toArray();
      controls.dispatchEvent({type: 'mouseUp'});
      controls.dispatchEvent({type: 'dragging-changed', value: false});
      await Promise.resolve();
      const dragFinished = controller.getDebugState();
      const interactionAfterGizmo = isRigTransformInteractionActive();
      const {createWeightPickController} = await import(
        './js/scene/weight-pick-controller.js');
      const picker = createWeightPickController({
        canvas, camera, controls: arcballControls, getMeshes: () => [],
        onStateChanged: () => {
          controller.refresh(state);
        },
      });
      picker.begin();
      const duringPick = controller.getDebugState();
      const pickerActions = arcballActions.slice();
      picker.cancel();
      const afterPick = controller.getDebugState();
      picker.dispose();
      state = {...state, selectedJointId: 1};
      controller.refresh(state);
      const rootAgain = controller.getDebugState();
      state = {...state, selectedJointId: 2,
        jointPickIntent: {type: 'selected-joint'}};
      controller.refresh(state);
      const picking = controller.getDebugState();
      state = {...state, jointPickIntent: null};
      controller.refresh(state);
      const picked = controller.getDebugState();
      controller.dispose();
      return {
        noSelection, root, nonRoot, cleared, reselected, rootAgain,
        picking, picked,
        dragStarted, dragFinished, duringPick, afterPick,
        pickerActions, arcballActions,
        hoverPoseCount, objectChangePoseCount, poseCalls, finishCalls,
        objectChangeLocal, interactionDuringGizmo, interactionAfterGizmo,
        rotationSnap: controls.rotationSnap,
      };
    }""")
    assert result["noSelection"]["controlsCreated"] is False
    assert result["root"]["controlsAttached"] is False
    assert result["rootAgain"]["controlsAttached"] is False
    assert result["nonRoot"]["controlsCreated"] is True
    assert result["nonRoot"]["controlsAttached"] is True
    assert result["nonRoot"]["helperInScene"] is True
    assert result["nonRoot"]["controlsCreateCount"] == 1
    assert result["cleared"]["proxyVisible"] is False
    assert result["cleared"]["controlsAttached"] is False
    assert result["cleared"]["controlsCreateCount"] == 1
    assert result["reselected"]["proxyVisible"] is True
    assert result["reselected"]["controlsAttached"] is True
    assert result["reselected"]["controlsCreateCount"] == 1
    assert result["nonRoot"]["arcballEnabled"] is True
    assert result["hoverPoseCount"] == 0
    assert result["objectChangePoseCount"] == 1
    assert result["rotationSnap"] == pytest.approx(math.radians(15))
    assert result["poseCalls"][0][0] == 2
    assert result["objectChangeLocal"] == pytest.approx(
        [0, 0, math.sin(math.pi / 12), math.cos(math.pi / 12)])
    assert result["poseCalls"][0][2] == {"dragging": True}
    assert result["dragStarted"]["arcballEnabled"] is False
    assert result["dragStarted"]["arcballWasEnabled"] is True
    assert result["dragStarted"]["poseDragActive"] is True
    assert result["dragFinished"]["arcballEnabled"] is True
    assert result["dragFinished"]["arcballWasEnabled"] is None
    assert len(result["finishCalls"]) == 1
    assert result["duringPick"]["controlsAttached"] is True
    assert result["duringPick"]["arcballEnabled"] is True
    assert result["pickerActions"] == [["unset", 0]]
    assert result["afterPick"]["controlsAttached"] is True
    assert result["afterPick"]["arcballEnabled"] is True
    assert result["arcballActions"] == [["unset", 0], ["set", "ROTATE", 0]]
    assert result["picking"]["controlsAttached"] is False
    assert result["picking"]["staticVisible"] is True
    assert result["picking"]["arcballEnabled"] is True
    assert result["picked"]["controlsAttached"] is True
    assert result["picked"]["staticVisible"] is False
    assert result["picked"]["arcballEnabled"] is True
    assert result["interactionDuringGizmo"] is True
    assert result["interactionAfterGizmo"] is False


def test_rig_overlay_switches_between_fk_and_ik_target_modes(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera();
      const canvas = document.createElement('canvas');
      const model = new THREE.Object3D();
      scene.add(model);
      const source = {
        key: 'model-rig', structureRevision: 1,
        joints: [1, 2, 3].map((jointId, index) => ({
          jointId, restCenter: [index, 0, 0], restPivot: [index, 0, 0],
        })),
        components: [{componentId: 0, rootId: 1, nodeIds: [1, 2, 3],
          parentById: {1: null, 2: 1, 3: 2},
          childrenById: {1: [2], 2: [3], 3: []}}],
        forestEdges: [
          {parentId: 1, childId: 2}, {parentId: 2, childId: 3},
        ],
      };
      let state = {
        visible: true, selectedJointId: 3, picking: false,
        rotationSnapDegrees: 15, model: source,
        ik: {enabled: true, available: true, endJointId: 3,
          activeLimbRole: 'left_arm', pathJointIds: [1, 2, 3],
          anchorJointId: 1, bendJointId: 2, bendSign: 1},
      };
      const solveCalls = [];
      const finishCalls = [];
      const controller = createRigOverlayController({
        scene, camera, canvas, getMeshes: () => [model],
        getRigState: () => state, getRigJointPoseFrame: () => ({
          pivot: [2, 0, 0], center: [2, 0, 0],
          parentRotation: [0, 0, 0, 1], boneRotation: [0, 0, 0, 1],
          restRotation: [0, 0, 0, 1], gizmoRotation: [0, 0, 0, 1],
        }), solveRigIkTarget: (...args) => solveCalls.push(args),
        finishRigJointPose: (...args) => finishCalls.push(args),
      });
      controller.refresh(state);
      const controls = await controller.ensureTransformControls();
      const ikBefore = controller.getDebugState();
      const ikMode = controls.getMode?.();
      const ikSpace = controls.space;
      const ikSnap = controls.rotationSnap;
      controls.dispatchEvent({type: 'dragging-changed', value: true});
      controls.object.position.x += 0.5;
      controls.dispatchEvent({type: 'objectChange'});
      controls.dispatchEvent({type: 'dragging-changed', value: false});
      await Promise.resolve();
      state = {...state, ik: {...state.ik, enabled: false}};
      controller.refresh(state);
      const fkMode = controls.getMode?.();
      const fkSpace = controls.space;
      const fk = controller.getDebugState();
      controller.dispose();
      return {ikBefore, ikMode, ikSpace, ikSnap, solveCalls: solveCalls.length,
        solveTarget: solveCalls[0]?.[0], finishCalls, fkMode, fkSpace, fk};
    }""")
    assert result["ikBefore"]["ikTargetVisible"]
    assert result["ikBefore"]["controlsAttachedTo"] == "ik-target"
    assert result["ikMode"] == "translate"
    assert result["ikSpace"] == "world"
    assert result["ikSnap"] is None
    assert result["solveCalls"] == 1
    assert result["solveTarget"] == pytest.approx([2.5, 0, 0])
    assert result["finishCalls"] == [[3]]
    assert result["fkMode"] == "rotate"
    assert result["fkSpace"] == "local"
    assert not result["fk"]["ikTargetVisible"]
    assert result["fk"]["controlsAttachedTo"] == "fk-proxy"


def test_rig_overlay_exposes_primary_humanoid_ik_target_without_joint_selection(
        module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera();
      const canvas = document.createElement('canvas');
      const model = new THREE.Object3D();
      scene.add(model);
      const source = {
        key: 'model-rig', structureRevision: 7,
        joints: [{jointId: 42, restPivot: [0, 2, 0]}],
        humanoidControlRig: {controls: {
          leftShoulder: {position: [0, 1, 0]},
          leftElbow: {position: [.4, .8, 0]},
          leftHand: {position: [.8, .7, 0]},
        }},
      };
      const state = {visible: true, selectedJointId: 42, model: source,
        ik: {enabled: true, available: true, activeLimbRole: 'left_arm',
          controlKeys: ['leftShoulder', 'leftElbow', 'leftHand']}};
      const solveCalls = [];
      const finishCalls = [];
      const controller = createRigOverlayController({
        scene, camera, canvas, getMeshes: () => [model],
        getRigState: () => state,
        getRigJointPoseFrame: () => ({pivot: [0, 2, 0]}),
        solveRigIkTarget: (...args) => solveCalls.push(args),
        finishRigJointPose: (...args) => finishCalls.push(args),
      });
      controller.refresh(state);
      const controls = await controller.ensureTransformControls();
      const before = controller.getDebugState();
      const attachedPosition = controls.object.position.toArray();
      const mode = controls.getMode?.();
      controls.dispatchEvent({type: 'dragging-changed', value: true});
      controls.object.position.x += .2;
      controls.dispatchEvent({type: 'objectChange'});
      controls.dispatchEvent({type: 'dragging-changed', value: false});
      controller.dispose();
      return {before, mode, solveCalls: solveCalls.length,
        finishCalls: finishCalls.length,
        attachedPosition, target: solveCalls[0]?.[0]};
    }""")
    assert result["before"]["ikTargetVisible"]
    assert result["before"]["controlsAttachedTo"] == "ik-target"
    assert result["attachedPosition"] == pytest.approx([.8, .7, 0])
    assert result["mode"] == "translate"
    assert result["solveCalls"] == 2
    assert result["finishCalls"] == 0
    assert result["target"] == pytest.approx([1.0, .7, 0])


def test_rig_overlay_updates_posed_buffers_without_rebuilding(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const pose = {
        1: {center: [0, 0, 0], pivot: [0, 0, 0]},
        2: {center: [1, 0, 0], pivot: [.5, 0, 0]},
      };
      const source = {
        key: 'model-rig', structureRevision: 4,
        joints: [1, 2].map((jointId, index) => ({
          jointId, restCenter: [index, 0, 0],
          restPivot: [index ? .5 : 0, 0, 0],
        })),
        components: [{componentId: 0, rootId: 1, nodeIds: [1, 2],
          parentById: {1: null, 2: 1}, childrenById: {1: [2], 2: []}}],
        forestEdges: [{jointA: 1, jointB: 2, parentId: 1, childId: 2}],
        poseRotationByJointId: {},
      };
      const state = {visible: true, selectedJointId: 2,
        picking: false, model: source};
      const controller = createRigOverlayController({
        scene, getRigState: () => state, getMeshes: () => [],
        getRigJointPoseFrame: jointId => pose[jointId],
      });
      controller.refresh(state);
      const staticGroup = controller.group.children[0];
      const line = staticGroup.children[0];
      const centers = staticGroup.children[1];
      const joints = staticGroup.children[2];
      const initial = {
        rebuildCount: controller.getDebugState().rebuildCount,
        centerAttribute: centers.geometry.getAttribute('position'),
        lineAttribute: line.geometry.getAttribute('position'),
        jointAttribute: joints.geometry.getAttribute('position'),
      };
      pose[2] = {center: [1, 2, 0], pivot: [.5, 1, 0]};
      window.dispatchEvent(new CustomEvent(
        'mod-viewer-model-rig-pose-changed',
        {detail: {jointId: 2,
          quaternion: [0, 0, 0, 1]}}));
      const after = controller.getDebugState();
      return {
        rebuildCount: after.rebuildCount,
        posedUpdates: after.posedOverlayUpdateCount,
        sameCenterAttribute: initial.centerAttribute ===
          centers.geometry.getAttribute('position'),
        sameLineAttribute: initial.lineAttribute ===
          line.geometry.getAttribute('position'),
        sameJointAttribute: initial.jointAttribute ===
          joints.geometry.getAttribute('position'),
        center: [...initial.centerAttribute.array],
        line: [...initial.lineAttribute.array],
        joint: [...initial.jointAttribute.array],
        dynamicUsage: initial.centerAttribute.usage === THREE.DynamicDrawUsage
          && initial.lineAttribute.usage === THREE.DynamicDrawUsage
          && initial.jointAttribute.usage === THREE.DynamicDrawUsage,
      };
    }""")
    assert result["rebuildCount"] == 1
    assert result["posedUpdates"] >= 2
    assert result["sameCenterAttribute"]
    assert result["sameLineAttribute"]
    assert result["sameJointAttribute"]
    assert result["center"] == pytest.approx([0, 0, 0, 1, 2, 0])
    assert result["line"] == pytest.approx([0, 0, 0, 1, 2, 0])
    assert result["joint"] == pytest.approx([0, 0, 0, .5, 1, 0])
    assert result["dynamicUsage"]


def test_model_picker_blocks_view_selection_before_bubble_listener(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const canvasContainer = document.createElement('div');
      canvasContainer.id = 'canvas-container';
      const openButton = document.createElement('button');
      openButton.id = 'open-btn';
      const rendererError = document.createElement('div');
      rendererError.id = 'renderer-error';
      rendererError.innerHTML = '<span class="renderer-error-detail"></span>';
      const viewGizmo = document.createElement('div');
      viewGizmo.id = 'view-gizmo';
      viewGizmo.innerHTML = '<svg></svg>';
      document.body.append(
        canvasContainer, openButton, rendererError, viewGizmo);
      const {renderer, camera, controls} = await import('./js/scene/scene.js');
      const {createWeightPickController} = await import(
        './js/scene/weight-pick-controller.js');
      const {initSelection} = await import('./js/scene/selection.js');
      const canvas = renderer.domElement;
      const picker = createWeightPickController({
        canvas, camera, controls, getMeshes: () => [],
      });
      initSelection();
      let selectionEvents = 0;
      const onSelection = () => { selectionEvents += 1; };
      window.addEventListener('mod-viewer-mesh-selected', onSelection);
      picker.begin();
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        bubbles: true, button: 0, pointerId: 12, clientX: 20, clientY: 20,
      }));
      canvas.dispatchEvent(new PointerEvent('pointerup', {
        bubbles: true, button: 0, pointerId: 12, clientX: 20, clientY: 20,
      }));
      const duringPicker = {selectionEvents};
      window.removeEventListener('mod-viewer-mesh-selected', onSelection);
      picker.dispose();
      return duringPicker;
    }""")
    assert result == {
        "selectionEvents": 0,
    }


def test_inferred_rig_pivots_aggregate_and_keep_disconnected_components(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const nodes = rig.buildInfluenceNodes(
        new Float32Array([0, 0, 0, 1, 0, 0, 2, 0, 0, 3, 0, 0, 4, 0, 0]),
        new Uint32Array([1, 2, 1, 2, 1, 2, 1, 2, 1, 2]),
        new Float32Array([1, 0, .75, .25, .5, .5, .25, .75, 0, 1]), 2);
      const relationships = rig.buildInfluenceRelationships(
        new Float32Array([0, 0, 0, 1, 0, 0, 2, 0, 0, 3, 0, 0, 4, 0, 0]),
        new Uint32Array([1, 2, 1, 2, 1, 2, 1, 2, 1, 2]),
        new Float32Array([1, 0, .75, .25, .5, .5, .25, .75, 0, 1]), 2,
        nodes, 4);
      const aggregate = rig.aggregateInfluenceGraphs([
        {nodes, relationships},
        {nodes, relationships: relationships.map(edge => ({...edge,
          jointCenter: [edge.jointCenter[0] + 10, 0, 0]}))},
      ]);
      const forest = rig.buildInferredRigForest({
        nodes: [{boneId: 1}, {boneId: 2}, {boneId: 3}, {boneId: 4}, {boneId: 5}],
        relationships: [
          {boneA: 1, boneB: 2, sharedVertexCount: 2,
            containment: .8, jaccard: .3, treeEdgeScore: .8},
          {boneA: 2, boneB: 3, sharedVertexCount: 2,
            containment: .7, jaccard: .2, treeEdgeScore: .7},
          {boneA: 4, boneB: 5, sharedVertexCount: 2,
            containment: .9, jaccard: .4, treeEdgeScore: .9},
        ],
      });
      const nonZeroRootPivots = rig.jointPivotMap({components: [{
        rootId: 1,
        parentById: {1: null, 0: 1, 2: 1},
      }]}, [
        {boneA: 0, boneB: 1, jointCenter: [.5, 0, 0]},
        {boneA: 1, boneB: 2, jointCenter: [1.5, 0, 0]},
      ]);
      return {
        pivot: relationships[0].jointCenter,
        jointWeight: relationships[0].jointWeightTotal,
        aggregatePivot: aggregate.relationships[0].jointCenter,
        components: forest.components.map(component => component.nodeIds),
        roots: forest.components.map(component => component.rootId),
        nonZeroRootPivotKeys: [...nonZeroRootPivots.keys()],
      };
    }""")
    assert result["pivot"] == pytest.approx([2, 0, 0])
    assert result["jointWeight"] == pytest.approx(.625)
    assert result["aggregatePivot"] == pytest.approx([7, 0, 0])
    assert sorted(result["components"]) == [[1, 2, 3], [4, 5]]
    assert len(set(result["roots"])) == 2
    assert result["nonZeroRootPivotKeys"] == [0, 2]


def test_source_topology_comparison_uses_stable_bone_signatures(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {sourceTopologyComparisonSnapshot} = await import(
        './js/mesh/weight-rig-snapshots.js');
      const first = {
        sourceKey: 'body|offset=0',
        components: [
          {rootId: 0, nodeIds: [0, 1],
            parentById: {0: null, 1: 0}},
          {rootId: 3, nodeIds: [3, 4],
            parentById: {3: null, 4: 3}},
        ],
        relationships: [
          {boneA: 1, boneB: 0, jointCenter: [1, 0, 0]},
          {boneA: 4, boneB: 3, jointCenter: [3, 0, 0]},
        ],
      };
      const second = {
        ...first,
        components: [...first.components].reverse(),
        relationships: [...first.relationships].reverse(),
      };
      return {
        first: sourceTopologyComparisonSnapshot(first),
        second: sourceTopologyComparisonSnapshot(second),
      };
    }""")
    assert result["first"] == result["second"]
    assert result["first"] == {
        "rootSignatures": [
            "body|offset=0#bone=0",
            "body|offset=0#bone=3",
        ],
        "undirectedTreeEdges": [
            ["body|offset=0#bone=0", "body|offset=0#bone=1"],
            ["body|offset=0#bone=3", "body|offset=0#bone=4"],
        ],
        "directedParentEdges": [
            ["body|offset=0#bone=0", "body|offset=0#bone=1"],
            ["body|offset=0#bone=3", "body|offset=0#bone=4"],
        ],
        "componentMembership": [
            ["body|offset=0#bone=0", "body|offset=0#bone=1"],
            ["body|offset=0#bone=3", "body|offset=0#bone=4"],
        ],
        "pivotBySourceBonePair": [
            {"pair": [
                "body|offset=0#bone=0", "body|offset=0#bone=1",
            ], "pivot": [1, 0, 0]},
            {"pair": [
                "body|offset=0#bone=3", "body|offset=0#bone=4",
            ], "pivot": [3, 0, 0]},
        ],
    }


def test_surface_evidence_weights_rig_nodes_relationships_and_aggregation(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const positions = new Float32Array([
        0, 0, 0, 2, 0, 0, 0, 2, 0,
      ]);
      const triangles = new Uint32Array([0, 1, 2]);
      const weights = new Float32Array([
        0, 1, .5, .5, 1, 0,
      ]);
      const indices = new Uint32Array([0, 1, 0, 1, 0, 1]);
      const rawNodes = rig.buildInfluenceNodes(
        positions, indices, weights, 2, [0, 1]);
      const rawRelationships = rig.buildInfluenceRelationships(
        positions, indices, weights, 2, rawNodes, 4);
      const surfaceGraph = rig.buildSurfaceInfluenceGraph(
        positions, triangles, indices, weights, 2, [0, 1], 4);
      const aggregate = rig.aggregateInfluenceGraphs([
        surfaceGraph, surfaceGraph,
      ]);
      let mixedModeError = null;
      try {
        rig.aggregateInfluenceGraphs([
          surfaceGraph,
          {nodes: rawNodes, relationships: rawRelationships,
            evidenceMode: 'vertex'},
        ]);
      } catch (error) {
        mixedModeError = error.message;
      }
      return {
        rawNodes,
        rawRelationship: rawRelationships[0],
        surfaceNode: surfaceGraph.nodes[0],
        surfaceRelationship: surfaceGraph.relationships[0],
        aggregate: {
          evidenceMode: aggregate.evidenceMode,
          affectedMeasure: aggregate.nodes[0].affectedMeasure,
          sharedMeasure: aggregate.relationships[0].sharedMeasure,
          totalSurfaceArea: aggregate.totalSurfaceArea,
        },
        mixedModeError,
      };
    }""")
    assert result["rawNodes"][0]["totalWeight"] == pytest.approx(1.5)
    assert result["rawNodes"][0]["affectedVertexCount"] == 2
    assert result["rawNodes"][0]["affectedMeasure"] is None
    assert result["surfaceNode"]["totalWeight"] == pytest.approx(1)
    assert result["surfaceNode"]["affectedVertexCount"] == 2
    assert result["surfaceNode"]["affectedMeasure"] == pytest.approx(2)
    assert result["surfaceNode"]["weightedCenter"] == pytest.approx(
        [2 / 3, 5 / 6, 0])
    assert result["rawRelationship"]["sharedMeasure"] is None
    assert result["rawRelationship"]["minOverlap"] == pytest.approx(.5)
    assert result["surfaceRelationship"]["sharedMeasure"] == pytest.approx(2)
    assert result["surfaceRelationship"]["minOverlap"] == pytest.approx(2 / 3)
    assert result["surfaceRelationship"]["productOverlap"] == pytest.approx(
        5 / 12)
    assert result["surfaceRelationship"]["jointCenter"] == pytest.approx(
        [18 / 25, 16 / 25, 0])
    assert result["aggregate"] == {
        "evidenceMode": "surface",
        "affectedMeasure": pytest.approx(4),
        "sharedMeasure": pytest.approx(4),
        "totalSurfaceArea": pytest.approx(4),
    }
    assert result["mixedModeError"] == (
        "Cannot aggregate incompatible Rig evidence modes.")


def test_triangle_surface_evidence_is_invariant_for_varying_weight_tessellation(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const weights = new Float32Array([
        1, 0, 0, 1, 1, 0,
      ]);
      const indices = new Uint32Array([0, 1, 0, 1, 0, 1]);
      const coarse = rig.buildSurfaceInfluenceGraph(
        new Float32Array([0, 0, 0, 2, 0, 0, 0, 2, 0]),
        new Uint32Array([0, 1, 2]), indices, weights, 2, [0, 1], 4);
      const denseWeights = new Float32Array([
        1, 0, 0, 1, 1, 0,
        .5, .5, .5, .5, 1, 0,
      ]);
      const denseIndices = new Uint32Array([
        0, 1, 0, 1, 0, 1,
        0, 1, 0, 1, 0, 1,
      ]);
      const dense = rig.buildSurfaceInfluenceGraph(
        new Float32Array([
          0, 0, 0, 2, 0, 0, 0, 2, 0,
          1, 0, 0, 1, 1, 0, 0, 1, 0,
        ]),
        new Uint32Array([
          0, 3, 5, 3, 1, 4, 5, 4, 2, 3, 4, 5,
        ]),
        denseIndices, denseWeights, 2, [0, 1], 4);
      const equalWeights = rig.buildSurfaceInfluenceGraph(
        new Float32Array([0, 0, 0, 2, 0, 0, 0, 2, 0]),
        new Uint32Array([0, 1, 2]),
        new Uint32Array([0, 1, 0, 1, 0, 1]), new Float32Array([
          .5, .5, .5, .5, .5, .5,
        ]), 2, [0, 1], 4);
      const continuousOverlap = rig.buildSurfaceInfluenceGraph(
        new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
        new Uint32Array([0, 1, 2]),
        new Uint32Array([0, 1, 1, 2, 0, 2]), new Float32Array([
          .8, .2, .7, .3, .6, .4,
        ]), 2, [0, 1, 2], 2);
      const summarize = graph => {
        const relationship = graph.relationships[0];
        const forest = rig.buildInferredRigForest(graph);
        return {
          nodes: graph.nodes.map(node => ({
            boneId: node.boneId,
            totalWeight: node.totalWeight,
            weightedCenter: node.weightedCenter,
            weightedRadius: node.weightedRadius,
          })),
          relationship: {
            sharedVertexCount: relationship.sharedVertexCount,
            productOverlap: relationship.productOverlap,
            minOverlap: relationship.minOverlap,
            jointCenter: relationship.jointCenter,
          },
          candidateCount: rig.candidateRelationshipEdges(graph).length,
          components: forest.components.map(component => ({
            rootId: component.rootId,
            nodeIds: [...component.nodeIds].sort((a, b) => a - b),
            parentById: component.parentById,
          })),
        };
      };
      return {
        coarse: summarize(coarse),
        dense: summarize(dense),
        equal: summarize(equalWeights),
        continuousOverlap: {
          relationshipCount: continuousOverlap.relationships.length,
          candidateCount: rig.candidateRelationshipEdges(continuousOverlap).length,
        },
      };
    }""")
    for coarse_node, dense_node in zip(
            result["coarse"]["nodes"], result["dense"]["nodes"]):
        assert coarse_node["boneId"] == dense_node["boneId"]
        assert coarse_node["totalWeight"] == pytest.approx(
            dense_node["totalWeight"])
        assert coarse_node["weightedCenter"] == pytest.approx(
            dense_node["weightedCenter"])
        assert coarse_node["weightedRadius"] == pytest.approx(
            dense_node["weightedRadius"])
    assert result["coarse"]["relationship"]["productOverlap"] == pytest.approx(
        result["dense"]["relationship"]["productOverlap"])
    assert result["coarse"]["relationship"]["minOverlap"] == pytest.approx(
        result["dense"]["relationship"]["minOverlap"])
    assert result["coarse"]["relationship"]["jointCenter"] == pytest.approx(
        result["dense"]["relationship"]["jointCenter"])
    assert result["equal"]["relationship"]["minOverlap"] == pytest.approx(1)
    assert result["continuousOverlap"] == {
        "relationshipCount": 3,
        "candidateCount": 3,
    }
    assert result["coarse"]["relationship"]["sharedVertexCount"] == 0
    assert result["dense"]["relationship"]["sharedVertexCount"] == 6
    assert result["coarse"]["candidateCount"] == 1
    assert result["dense"]["candidateCount"] == 1
    assert result["coarse"]["components"] == result["dense"]["components"]


def test_surface_rig_topology_is_scale_and_input_order_invariant(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const basePositions = [
        0, 0, 0, 2, 0, 0, 0, 2, 0,
      ];
      const triangles = new Uint32Array([0, 1, 2]);
      const indices = new Uint32Array([0, 1, 0, 1, 0, 1]);
      const weights = new Float32Array([1, 0, .5, .5, 0, 1]);
      const build = (scale, reverse = false) => {
        const positions = new Float32Array(
          basePositions.map(value => value * scale));
        const graph = rig.buildSurfaceInfluenceGraph(
          positions, triangles, indices, weights, 2, [0, 1], 4 * scale);
        return rig.buildInferredRigForest({
          nodes: reverse ? [...graph.nodes].reverse() : graph.nodes,
          relationships: reverse
            ? [...graph.relationships].reverse() : graph.relationships,
        });
      };
      const normal = build(1);
      const scaled = build(100);
      const reordered = build(1, true);
      return {
        normal: normal.components.map(component => ({
          rootId: component.rootId,
          nodeIds: [...component.nodeIds].sort((a, b) => a - b),
          parentById: component.parentById,
        })),
        scaled: scaled.components.map(component => ({
          rootId: component.rootId,
          nodeIds: [...component.nodeIds].sort((a, b) => a - b),
          parentById: component.parentById,
        })),
        reordered: reordered.components.map(component => ({
          rootId: component.rootId,
          nodeIds: [...component.nodeIds].sort((a, b) => a - b),
          parentById: component.parentById,
        })),
      };
    }""")
    assert result["scaled"] == result["normal"]
    assert result["reordered"] == result["normal"]


def test_surface_topology_keeps_split_and_coincident_vertices_distinct(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {inspectSurfaceTopology} = await import(
        './js/mesh/weight-rig.js');
      const positions = new Float32Array([
        0, 0, 0, 1, 0, 0, 0, 1, 0,
        0, 0, 0, 1, 0, 0, 0, 1, 0,
      ]);
      const topology = inspectSurfaceTopology(
        positions, new Uint32Array([0, 1, 2, 3, 4, 5]));
      return {
        measuredVertexCount: topology.measuredVertexCount,
        totalSurfaceArea: topology.totalSurfaceArea,
      };
    }""")
    assert result["measuredVertexCount"] == 6
    assert result["totalSurfaceArea"] == pytest.approx(1)


def test_surface_topology_preserves_area_across_tessellation_and_scale(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const coarsePositions = new Float32Array([
        0, 0, 0, 2, 0, 0, 0, 2, 0,
      ]);
      const coarseTriangles = new Uint32Array([0, 1, 2]);
      const densePositions = new Float32Array([
        0, 0, 0, 2, 0, 0, 0, 2, 0,
        1, 0, 0, 1, 1, 0, 0, 1, 0,
      ]);
      const denseTriangles = new Uint32Array([
        0, 3, 5, 3, 1, 4, 5, 4, 2, 3, 4, 5,
      ]);
      const evidence = (positions, triangles, weights) => {
        const topology = rig.inspectSurfaceTopology(positions, triangles);
        const graph = rig.buildSurfaceInfluenceGraph(
          positions, triangles, new Uint32Array(weights.length),
          new Float32Array(weights), 1, [0]);
        const node = graph.nodes[0];
        return {
          ...topology,
          support: node.totalWeight,
          center: node.weightedCenter.slice(0, 2),
        };
      };
      const coarse = evidence(coarsePositions, coarseTriangles,
        [.5, .5, .5]);
      const dense = evidence(densePositions, denseTriangles,
        [.5, .5, .5, .5, .5, .5]);
      const gradient = evidence(densePositions, denseTriangles,
        [0, 1, 0, .5, .5, 0]);
      const scaledPositions = densePositions.map(value => value * 100);
      const scaled = evidence(scaledPositions, denseTriangles,
        [.5, .5, .5, .5, .5, .5]);
      const nonIndexed = rig.inspectSurfaceTopology(new Float32Array([
        0, 0, 0, 1, 0, 0, 0, 1, 0,
        0, 0, 0, 0, 1, 0, -1, 0, 0,
      ]));
      return {
        coarse, dense, gradient,
        scaled: {
          totalSurfaceArea: scaled.totalSurfaceArea,
          support: scaled.support,
          center: scaled.center,
        },
        nonIndexed: {
          triangleCount: nonIndexed.triangleCount,
          validTriangleCount: nonIndexed.validTriangleCount,
          totalSurfaceArea: nonIndexed.totalSurfaceArea,
        },
        duplicateAreas: [
          rig.inspectSurfaceTopology(
            densePositions, denseTriangles).totalSurfaceArea,
          rig.inspectSurfaceTopology(
            densePositions, denseTriangles).totalSurfaceArea,
        ],
      };
    }""")
    assert result["coarse"]["triangleCount"] == 1
    assert result["dense"]["triangleCount"] == 4
    assert result["coarse"]["validTriangleCount"] == 1
    assert result["dense"]["validTriangleCount"] == 4
    assert result["coarse"]["totalSurfaceArea"] == pytest.approx(2)
    assert result["dense"]["totalSurfaceArea"] == pytest.approx(2)
    assert result["coarse"]["support"] == pytest.approx(1)
    assert result["dense"]["support"] == pytest.approx(1)
    assert result["gradient"]["support"] == pytest.approx(2 / 3)
    assert result["coarse"]["center"] == pytest.approx([2 / 3, 2 / 3])
    assert result["dense"]["center"] == pytest.approx([2 / 3, 2 / 3])
    assert result["scaled"]["totalSurfaceArea"] == pytest.approx(20000)
    assert result["scaled"]["support"] == pytest.approx(10000)
    assert result["scaled"]["center"] == pytest.approx([200 / 3, 200 / 3])
    assert result["nonIndexed"] == {
        "triangleCount": 2,
        "validTriangleCount": 2,
        "totalSurfaceArea": pytest.approx(1),
    }
    assert result["duplicateAreas"] == pytest.approx([2, 2])


def test_surface_topology_diagnostics_reject_bad_triangles_without_nan(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const positions = new Float32Array([
        0, 0, 0, 1, 0, 0, 0, 1, 0, NaN, 0, 0,
      ]);
      const malformed = rig.inspectSurfaceTopology(positions,
        new Uint32Array([0, 1, 2, 0, 2, 3, 0, 0, 1, 0, 1, 9, 0, 1]));
      const unusable = rig.inspectSurfaceTopology(
        new Float32Array([0, 0, 0, 1, 0, 0, 2, 0, 0]),
        new Uint32Array([0, 1, 2]));
      return {
        malformed: {
          triangleCount: malformed.triangleCount,
          validTriangleCount: malformed.validTriangleCount,
          degenerateTriangleCount: malformed.degenerateTriangleCount,
          invalidTriangleCount: malformed.invalidTriangleCount,
          totalSurfaceArea: malformed.totalSurfaceArea,
          measuredVertexCount: malformed.measuredVertexCount,
          zeroMeasureVertexCount: malformed.zeroMeasureVertexCount,
          surfaceEvidenceAvailable: malformed.surfaceEvidenceAvailable,
        },
        unusable: {
          triangleCount: unusable.triangleCount,
          validTriangleCount: unusable.validTriangleCount,
          degenerateTriangleCount: unusable.degenerateTriangleCount,
          invalidTriangleCount: unusable.invalidTriangleCount,
          totalSurfaceArea: unusable.totalSurfaceArea,
          measuredVertexCount: unusable.measuredVertexCount,
          zeroMeasureVertexCount: unusable.zeroMeasureVertexCount,
          surfaceEvidenceAvailable: unusable.surfaceEvidenceAvailable,
        },
      };
    }""")
    assert result["malformed"] == {
        "triangleCount": 5,
        "validTriangleCount": 1,
        "degenerateTriangleCount": 1,
        "invalidTriangleCount": 3,
        "totalSurfaceArea": pytest.approx(.5),
        "measuredVertexCount": 3,
        "zeroMeasureVertexCount": 1,
        "surfaceEvidenceAvailable": True,
    }
    assert result["unusable"] == {
        "triangleCount": 1,
        "validTriangleCount": 0,
        "degenerateTriangleCount": 1,
        "invalidTriangleCount": 0,
        "totalSurfaceArea": 0,
        "measuredVertexCount": 0,
        "zeroMeasureVertexCount": 3,
        "surfaceEvidenceAvailable": False,
    }


def test_inferred_rig_rest_frames_are_deterministic_and_transport_axes(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three');
      const frames = await import('./js/mesh/weight-rig-frames.js');
      const forest = {components: [{
        componentId: 0, rootId: 0, nodeIds: [0, 1, 2, 3],
        parentById: {0: null, 1: 0, 2: 1, 3: 1},
        childrenById: {0: [1], 1: [2, 3], 2: [], 3: []},
        edges: [
          {boneA: 0, boneB: 1, treeEdgeScore: .9},
          {boneA: 1, boneB: 2, treeEdgeScore: .8},
          {boneA: 1, boneB: 3, treeEdgeScore: .7},
        ],
      }]};
      const centers = new Map([
        [0, [0, 0, 0]], [1, [0, 1, 0]],
        [2, [0, 3, 0]], [3, [-1, 1, 0]],
      ]);
      const pivots = new Map([
        [1, [0, 1, 0]], [2, [0, 3, 0]], [3, [-1, 1, 0]],
      ]);
      const first = frames.buildInferredRigRestFrames(
        forest, centers, pivots);
      const second = frames.buildInferredRigRestFrames(
        forest, centers, pivots);
      const modelForest = {components: [{
        componentId: 0, rootId: 0, nodeIds: [0, 1, 2, 3],
        parentById: {0: null, 1: 0, 2: 1, 3: 1},
        childrenById: {0: [1], 1: [2, 3], 2: [], 3: []},
        edges: [
          {jointA: 0, jointB: 1, combinedTreeScore: .9},
          {jointA: 1, jointB: 2, combinedTreeScore: .2},
          {jointA: 1, jointB: 3, combinedTreeScore: .9},
        ],
      }]};
      const modelCenters = new Map([
        [0, [0, 0, 0]], [1, [0, 1, 0]],
        [2, [0, 3, 0]], [3, [0, 3, 0]],
      ]);
      const modelPivots = new Map([
        [1, [0, 1, 0]], [2, [0, 3, 0]], [3, [0, 3, 0]],
      ]);
      const modelFrames = frames.buildInferredRigRestFrames(
        modelForest, modelCenters, modelPivots);
      const disconnected = {
        componentId: 1, rootId: 4, nodeIds: [4, 5],
        parentById: {4: null, 5: 4}, childrenById: {4: [5], 5: []},
        edges: [{jointA: 4, jointB: 5, combinedTreeScore: .5}],
      };
      const twoComponentCenters = new Map([
        ...modelCenters, [4, [10, 0, 0]], [5, [10, 1, 0]],
      ]);
      const twoComponentPivots = new Map([
        ...modelPivots, [5, [10, 1, 0]],
      ]);
      const disconnectedBefore = frames.buildInferredRigRestFrames({
        components: [modelForest.components[0], disconnected],
      }, twoComponentCenters, twoComponentPivots);
      const rerootedModelComponent = {
        ...modelForest.components[0], rootId: 3,
        parentById: {3: null, 1: 3, 0: 1, 2: 1},
        childrenById: {3: [1], 1: [0, 2], 0: [], 2: []},
      };
      const disconnectedAfter = frames.buildInferredRigRestFrames({
        components: [rerootedModelComponent, disconnected],
      }, twoComponentCenters, twoComponentPivots);
      const disconnectedStable = [4, 5].every(id =>
        disconnectedBefore.frameByBoneId.get(id).equals(
          disconnectedAfter.frameByBoneId.get(id)));
      const yFor = id => new THREE.Vector3(0, 1, 0)
        .applyQuaternion(first.frameByBoneId.get(id)).toArray();
      const xFor = id => new THREE.Vector3(1, 0, 0)
        .applyQuaternion(first.frameByBoneId.get(id)).toArray();
      const rest = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 0, 1), Math.PI / 2);
      const pose = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(1, 0, 0), Math.PI / 3);
      const delta = frames.poseToRestFrameDelta(pose, rest);
      const roundTrip = frames.restFrameDeltaToPose(delta, rest);
      return {
        directions: [0, 1, 2, 3].map(yFor),
        continuation: [...first.continuationChildByBoneId.entries()],
        modelContinuation: [...modelFrames.continuationChildByBoneId.entries()],
        disconnectedStable,
        evidence: [...first.evidenceByBoneId.entries()],
        xDot: xFor(1).reduce((sum, value, index) =>
          sum + value * xFor(0)[index], 0),
        normalized: [0, 1, 2, 3].map(id =>
          first.frameByBoneId.get(id).length()),
        deterministic: [0, 1, 2, 3].every(id =>
          first.frameByBoneId.get(id).equals(second.frameByBoneId.get(id))),
        deltaRoundTrip: roundTrip.toArray(),
      };
    }""")
    assert result["directions"][0] == pytest.approx([0, 1, 0])
    assert result["directions"][1] == pytest.approx([0, 1, 0])
    assert result["directions"][2] == pytest.approx([0, 1, 0])
    assert result["directions"][3] == pytest.approx([-1, 0, 0])
    assert result["continuation"] == [[0, 1], [1, 2], [2, None], [3, None]]
    assert result["modelContinuation"] == [[0, 1], [1, 3], [2, None], [3, None]]
    assert result["disconnectedStable"]
    assert result["xDot"] > 0
    assert result["normalized"] == pytest.approx([1, 1, 1, 1])
    assert result["deterministic"]
    assert result["deltaRoundTrip"] == pytest.approx(
        [math.sin(math.pi / 6), 0, 0, math.cos(math.pi / 6)])


def test_cross_source_reconciliation_uses_geometry_and_guards_clusters(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const rig = (sourceKey, entries, edges = []) => {
        const nodeIds = entries.map(item => item[0]);
        const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
        edges.forEach(([parent, child, score = 1]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        const roots = nodeIds.filter(id => parentById[id] === null);
        return {
          sourceKey, boneIds: nodeIds,
          influenceGraph: {nodes: entries.map(([boneId, center, radius = .1]) => ({
            boneId, weightedCenter: center, weightedRadius: radius,
            totalWeight: 1, affectedVertexCount: 10,
          }))},
          centerByBoneId: new Map(entries.map(([id, center]) => [id, center])),
          jointPivotByBoneId: new Map(entries
            .filter(([id]) => parentById[id] !== null)
            .map(([id, center]) => [id, center])),
          restDirectionByBoneId: new Map(entries.map(([id, center]) => [
            id, id === nodeIds[0] ? [0, 1, 0] : [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(entries.map(([id]) => [id, {
            directionSource: 'child-weighted-center',
          }])),
          inferredForest: {
            components: roots.map((rootId, componentId) => ({
              componentId, rootId, nodeIds: nodeIds.filter(id => {
                let current = id;
                while (parentById[current] !== null) current = parentById[current];
                return current === rootId;
              }), parentById, childrenById,
              depthById: Object.fromEntries(nodeIds.map(id => [id, 0])),
              edges: edges.map(([parent, child, score = 1]) => ({
                boneA: parent, boneB: child, treeEdgeScore: score,
              })),
            })),
            componentByBoneId: Object.fromEntries(nodeIds.map(id => [id, 0])),
          },
        };
      };
      const body = rig('body', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [.02, 0, 0]],
      ], [[0, 1, .9], [0, 2, .2]]);
      const legs = rig('legs', [
        [4, [0, 0, 0]], [43, [0, 1.01, 0]],
      ], [[4, 43, .8]]);
      const far = rig('far', [[0, [10, 0, 0]]]);
      const result = buildModelRigReconciliation([body, legs, far]);
      const bodyJoint = result.sourceBoneToModelJointId['body#bone=0'];
      const legsJoint = result.sourceBoneToModelJointId['legs#bone=4'];
      const bodyChild = result.sourceBoneToModelJointId['body#bone=1'];
      const legsChild = result.sourceBoneToModelJointId['legs#bone=43'];
      return {
        bodyJoint, legsJoint, bodyChild, legsChild,
        sameRoot: bodyJoint === legsJoint,
        sameChild: bodyChild === legsChild,
        numericCollisionSeparate:
          result.sourceBoneToModelJointId['body#bone=0'] !==
          result.sourceBoneToModelJointId['far#bone=0'],
        clusterSizes: result.joints.map(joint => joint.members.length),
        jointSignatures: result.joints.map(joint => [
          joint.signature,
          JSON.stringify(joint.members.map(member => member.sourceBoneKey)
            .sort()),
        ]),
        rejected: result.reconciliation.rejectedCandidates
          .map(item => item.rejectionReason).filter(Boolean),
        sourceEdgeSupport: result.edges.filter(edge =>
          edge.relationshipType === 'source').map(edge => edge.sourceSupportCount),
      };
    }""")
    assert result["sameRoot"]
    assert result["sameChild"]
    assert result["numericCollisionSeparate"]
    assert sorted(result["clusterSizes"], reverse=True)[:2] == [2, 2]
    assert "topology_conflict" in result["rejected"] or "not_mutual" in result["rejected"]
    assert 2 in result["sourceEdgeSupport"]
    assert all(signature == expected
               for signature, expected in result["jointSignatures"])


def test_cross_source_neutral_sampling_uses_radius_and_true_mutual_nearest(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {crossSourceWeightEvidence} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, positions, ids) => ({
        sourceKey,
        vertexEvidence: [{
          meshKey: `${sourceKey}/neutral`,
          positions: new Float32Array(positions),
          indices: new Uint32Array(ids),
          weights: new Float32Array(ids.map(() => 1)),
          influenceCount: 1,
        }],
      });
      const spatial = crossSourceWeightEvidence(
        make('left', [.0099, 0, 0], [0]),
        make('right', [.0201, 0, 0], [1]), 1);
      const mutual = crossSourceWeightEvidence(
        make('mutual-left', [0, 0, 0, 0, .018, 0], [10, 11]),
        make('mutual-right', [-.018, 0, 0, 0, .0095, 0], [20, 21]), 1);
      return {
        spatialMatches: spatial.get('left#bone=0|right#bone=1')
          ?.matchedVertexCount || 0,
        mutualPairs: [...mutual.values()].map(item => [
          item.leftSourceBoneKey, item.rightSourceBoneKey,
        ]).sort(),
      };
    }""")
    assert result["spatialMatches"] == 1
    assert result["mutualPairs"] == [[
        "mutual-left#bone=11", "mutual-right#bone=21"]]


def test_cross_source_reconciliation_preserves_host_root_and_reroots_accessory(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, rootId, edgeList) => {
        const parentById = Object.fromEntries(entries.map(([id]) => [id, null]));
        const childrenById = Object.fromEntries(entries.map(([id]) => [id, []]));
        edgeList.forEach(([parent, child, score = 1]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        return {
          sourceKey, boneIds: entries.map(([id]) => id),
          influenceGraph: {nodes: entries.map(([boneId, center]) => ({
            boneId, weightedCenter: center, weightedRadius: .2,
            totalWeight: 10, affectedVertexCount: 20,
          }))},
          centerByBoneId: new Map(entries),
          jointPivotByBoneId: new Map(entries.filter(([id]) => id !== rootId)),
          restDirectionByBoneId: new Map(entries.map(([id]) => [id,
            id === rootId ? [1, 0, 0] : [1, 0, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(entries.map(([id]) => [id, {
            directionSource: 'child-weighted-center',
          }])),
          inferredForest: {
            components: [{componentId: 0, rootId,
              nodeIds: entries.map(([id]) => id), parentById, childrenById,
              depthById: Object.fromEntries(entries.map(([id]) => [id, 0])),
              edges: edgeList.map(([parent, child, score = 1]) => ({
                boneA: parent, boneB: child, treeEdgeScore: score,
              }))}],
            componentByBoneId: Object.fromEntries(entries.map(([id]) => [id, 0])),
          },
        };
      };
      const body = make('body', [[0, [0, 0, 0]], [1, [0, 1, 0]]],
        0, [[0, 1, .9]]);
      const wing = make('wing', [[7, [.02, 1, 0]], [8, [1.02, 1, 0]]],
        7, [[7, 8, .8]]);
      const result = buildModelRigReconciliation([body, wing]);
      const wingJoint = result.sourceBoneToModelJointId['wing#bone=7'];
      const bodyJoint = result.sourceBoneToModelJointId['body#bone=1'];
      const attachment = result.edges.find(edge =>
        edge.relationshipType === 'attachment');
      return {
        jointCount: result.joints.length,
        attachment: attachment ? {
          relationshipType: attachment.relationshipType,
          jointA: attachment.jointA, jointB: attachment.jointB,
        } : null,
        distinct: wingJoint !== bodyJoint,
        forestEdgeCount: result.forestEdges.length,
        componentCount: result.components.length,
      };
    }""")
    assert result["jointCount"] == 4
    assert result["distinct"]
    assert result["attachment"] is not None
    assert result["attachment"]["relationshipType"] == "attachment"
    assert result["forestEdgeCount"] == 3
    assert result["componentCount"] == 1


def test_cross_source_reconciliation_uses_neutral_weights_and_attachment_boundary(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, edgeList, rootId,
          vertexPositions = null, vertexIds = null) => {
        const nodeIds = entries.map(([id]) => id);
        const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
        edgeList.forEach(([parent, child, score = 1]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        const rig = {
          sourceKey, boneIds: nodeIds,
          influenceGraph: {nodes: entries.map(([boneId, center]) => ({
            boneId, weightedCenter: center, weightedRadius: .1,
            totalWeight: 1, affectedVertexCount: 10,
          }))},
          centerByBoneId: new Map(entries),
          // Deliberately make the source-root/internal-joint pivots disagree;
          // neutral weighted centers and correspondence must carry identity.
          jointPivotByBoneId: new Map(entries.filter(([id]) => id !== rootId)
            .map(([id, center]) => [id, [center[0] + 4, center[1], center[2]]])),
          restDirectionByBoneId: new Map(entries.map(([id]) => [id, [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(entries.map(([id]) => [id, {
            directionSource: 'child-weighted-center',
          }])),
          inferredForest: {
            components: [{componentId: 0, rootId, nodeIds,
              parentById, childrenById,
              depthById: Object.fromEntries(nodeIds.map(id => [id, 0])),
              edges: edgeList.map(([parent, child, score = 1]) => ({
                boneA: parent, boneB: child, treeEdgeScore: score,
              }))}],
            componentByBoneId: Object.fromEntries(nodeIds.map(id => [id, 0])),
          },
        };
        if (vertexPositions && vertexIds) {
          rig.vertexEvidence = [{
            meshKey: `${sourceKey}/neutral`,
            positions: new Float32Array(vertexPositions.flat()),
            indices: new Uint16Array(vertexIds),
            weights: new Float32Array(vertexIds.map(() => 1)),
            influenceCount: 1,
          }];
        }
        return rig;
      };
      const main = make('main', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
        [3, [0, 3, 0]],
      ], [[0, 1, .9], [1, 2, .9], [2, 3, .9]], 0,
      [[0, 0, 0], [0, 1, 0], [0, 2, 0], [0, 3, 0]], [0, 1, 2, 3]);
      const partial = make('partial', [
        [7, [0, 1, 0]], [8, [0, 2, 0]], [9, [0, 3, 0]],
      ], [[7, 8, .9], [8, 9, .9]], 7,
      [[0, 1, 0], [0, 2, 0], [0, 3, 0]], [7, 8, 9]);
      const accessory = make('accessory', [
        [20, [0, 2.08, 0]], [21, [.3, 2.37, 0]],
        [22, [.6, 2.67, 0]], [23, [.9, 2.97, 0]],
      ], [[22, 21, .9], [21, 20, .9], [21, 23, .8]], 22);
      const first = buildModelRigReconciliation([main, partial, accessory]);
      const second = buildModelRigReconciliation([accessory, partial, main]);
      const id = (result, key) => result.sourceBoneToModelJointId[key];
      const attachment = first.edges.find(edge =>
        edge.relationshipType === 'attachment');
      const attachmentBoundary = id(first, 'accessory#bone=20');
      const bodyTarget = id(first, 'main#bone=2');
      const bodyRoot = id(first, 'main#bone=0');
      const bodyParent = id(first, 'main#bone=1');
      const accessoryChild = id(first, 'accessory#bone=21');
      const accessoryRoot = id(first, 'accessory#bone=22');
      const accessoryBranch = id(first, 'accessory#bone=23');
      const component = first.components[0];
      const mapKeys = [
        'main#bone=0', 'main#bone=1', 'main#bone=2', 'main#bone=3',
        'partial#bone=7', 'partial#bone=8', 'partial#bone=9',
        'accessory#bone=20', 'accessory#bone=21',
        'accessory#bone=22', 'accessory#bone=23',
      ];
      return {
        partialMatches: [
          id(first, 'main#bone=1') === id(first, 'partial#bone=7'),
          id(first, 'main#bone=2') === id(first, 'partial#bone=8'),
          id(first, 'main#bone=3') === id(first, 'partial#bone=9'),
        ],
        rootInternalEvidence: first.reconciliation.acceptedEquivalences
          .some(item => item.left.sourceBoneKey === 'main#bone=1'
            && item.right.sourceBoneKey === 'partial#bone=7'),
        attachment: attachment ? {
          jointA: attachment.jointA,
          jointB: attachment.jointB,
          survives: first.reconciliation.attachmentCount === 1,
          boundary: attachment.jointB === attachmentBoundary,
          target: attachment.jointA === bodyTarget,
        } : null,
        attachedRoot: first.components.length === 1
          && component.rootId === bodyRoot,
        hostOrientation: component.parentById[bodyTarget] === bodyParent
          && component.parentById[bodyParent] === bodyRoot,
        accessoryOrientation: component.parentById[attachmentBoundary] === bodyTarget
          && component.parentById[accessoryChild] === attachmentBoundary
          && component.parentById[accessoryRoot] === accessoryChild
          && component.parentById[accessoryBranch] === accessoryChild,
        orderInvariant: mapKeys.every(key =>
          id(first, key) === id(second, key))
          && JSON.stringify(first.edges.map(edge => [
            edge.relationshipType, edge.jointA, edge.jointB,
          ])) === JSON.stringify(second.edges.map(edge => [
            edge.relationshipType, edge.jointA, edge.jointB,
          ])),
        finalEdgeOrder: first.edges.map(edge => [
          edge.relationshipType, edge.jointA, edge.jointB,
        ]),
      };
    }""")
    assert result["partialMatches"] == [True, True, True]
    assert result["rootInternalEvidence"]
    assert result["attachment"] is not None
    assert result["attachment"]["survives"]
    assert result["attachment"]["boundary"]
    assert result["attachment"]["target"]
    assert result["attachedRoot"]
    assert result["hostOrientation"]
    assert result["accessoryOrientation"]
    assert result["orderInvariant"]


def test_cross_source_reconciliation_preserves_host_for_multiple_attachments(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, rootId, edgeList) => {
        const nodeIds = entries.map(([id]) => id);
        const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
        edgeList.forEach(([parent, child]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        return {
          sourceKey, boneIds: nodeIds,
          influenceGraph: {nodes: entries.map(([boneId, weightedCenter]) => ({
            boneId, weightedCenter, weightedRadius: .1,
            totalWeight: 10, affectedVertexCount: 20,
          }))},
          centerByBoneId: new Map(entries),
          jointPivotByBoneId: new Map(entries.filter(([id]) => id !== rootId)),
          restDirectionByBoneId: new Map(entries.map(([id]) => [id, [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(),
          inferredForest: {
            components: [{componentId: 0, rootId, nodeIds,
              parentById, childrenById,
              depthById: Object.fromEntries(nodeIds.map(id => [id, 0])),
              edges: edgeList.map(([boneA, boneB]) => ({
                boneA, boneB, treeEdgeScore: 1,
              }))}],
            componentByBoneId: Object.fromEntries(nodeIds.map(id => [id, 0])),
          },
        };
      };
      const host = make('host', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
      ], 0, [[0, 1], [1, 2]]);
      const upper = make('upper', [
        [10, [.05, 2, 0]], [11, [.35, 2.3, 0]],
      ], 10, [[10, 11]]);
      const lower = make('lower', [
        [20, [.05, 0, 0]], [21, [-.3, -.3, 0]],
      ], 20, [[20, 21]]);
      const first = buildModelRigReconciliation(
        [host, upper, lower], {modelReferenceRadius: 1});
      const second = buildModelRigReconciliation(
        [lower, host, upper], {modelReferenceRadius: 1});
      const id = (value, source, bone) =>
        value.sourceBoneToModelJointId[`${source}#bone=${bone}`];
      const hostRoot = id(first, 'host', 0);
      const hostMiddle = id(first, 'host', 1);
      const hostTop = id(first, 'host', 2);
      const upperRoot = id(first, 'upper', 10);
      const lowerRoot = id(first, 'lower', 20);
      const component = first.components.find(item =>
        item.nodeIds.includes(hostRoot));
      const hierarchy = value => value.components.map(item => ({
        root: item.rootId,
        parent: Object.entries(item.parentById).sort(),
      }));
      return {
        attachmentCount: first.reconciliation.attachmentCount,
        root: component?.rootId,
        hostParent: [component?.parentById[hostMiddle],
          component?.parentById[hostTop]],
        accessoryParents: [component?.parentById[upperRoot],
          component?.parentById[lowerRoot]],
        orderInvariant: JSON.stringify(hierarchy(first)) ===
          JSON.stringify(hierarchy(second)),
      };
    }""")
    assert result["attachmentCount"] == 2
    assert result["root"] == 0
    assert result["hostParent"] == [0, 1]
    assert result["accessoryParents"] == [2, 0]
    assert result["orderInvariant"]


def test_cross_source_reconciliation_preserves_attachment_chain_order(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, rootId, edgeList, totalWeight) => {
        const nodeIds = entries.map(([id]) => id);
        const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
        edgeList.forEach(([parent, child]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        return {
          sourceKey, boneIds: nodeIds,
          influenceGraph: {nodes: entries.map(([boneId, weightedCenter]) => ({
            boneId, weightedCenter, weightedRadius: .1,
            totalWeight, affectedVertexCount: 20,
          }))},
          centerByBoneId: new Map(entries),
          jointPivotByBoneId: new Map(entries.filter(([id]) => id !== rootId)),
          restDirectionByBoneId: new Map(nodeIds.map(id => [id, [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(),
          inferredForest: {
            components: [{componentId: 0, rootId, nodeIds,
              parentById, childrenById,
              depthById: Object.fromEntries(nodeIds.map(id => [id, 0])),
              edges: edgeList.map(([boneA, boneB]) => ({
                boneA, boneB, treeEdgeScore: 1,
              }))}],
            componentByBoneId: Object.fromEntries(nodeIds.map(id => [id, 0])),
          },
        };
      };
      const host = make('host', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
      ], 0, [[0, 1], [1, 2]], 10);
      const accessoryA = make('accessory-a', [
        [10, [0, 2.04, 0]], [11, [0, 2.09, 0]],
      ], 10, [[10, 11]], 10);
      const accessoryB = make('accessory-b', [
        [20, [0, 2.14, 0]], [21, [.3, 2.4, 0]],
      ], 20, [[20, 21]], 5);
      const first = buildModelRigReconciliation(
        [host, accessoryA, accessoryB], {modelReferenceRadius: 1});
      const second = buildModelRigReconciliation(
        [accessoryB, host, accessoryA], {modelReferenceRadius: 1});
      const id = (value, source, bone) =>
        value.sourceBoneToModelJointId[`${source}#bone=${bone}`];
      const hostRoot = id(first, 'host', 0);
      const hostMiddle = id(first, 'host', 1);
      const hostTop = id(first, 'host', 2);
      const accessoryARoot = id(first, 'accessory-a', 10);
      const accessoryAEnd = id(first, 'accessory-a', 11);
      const accessoryBRoot = id(first, 'accessory-b', 20);
      const component = first.components.find(item =>
        item.nodeIds.includes(hostRoot));
      const attachments = first.edges.filter(edge =>
        edge.relationshipType === 'attachment');
      const attachmentPairs = attachments.map(edge => [
        edge.targetJointId, edge.accessoryJointId,
      ]);
      const secondPairs = second.edges.filter(edge =>
        edge.relationshipType === 'attachment').map(edge => [
        edge.targetJointId, edge.accessoryJointId,
      ]);
      const hierarchy = value => value.components.map(item => ({
        root: item.rootId,
        parent: Object.entries(item.parentById).sort(),
      }));
      return {
        attachmentCount: attachments.length,
        hostRoot, hostMiddle, hostTop, accessoryARoot, accessoryAEnd,
        root: component?.rootId,
        hostParent: [component?.parentById[hostMiddle],
          component?.parentById[hostTop]],
        accessoryAParent: [component?.parentById[accessoryARoot],
          component?.parentById[accessoryAEnd]],
        accessoryBParent: component?.parentById[accessoryBRoot],
        targetChain: attachmentPairs.some(pair =>
          pair[0] === hostTop && pair[1] === accessoryARoot)
          && attachmentPairs.some(pair =>
            pair[0] === accessoryAEnd && pair[1] === accessoryBRoot),
        orderInvariant: JSON.stringify(hierarchy(first)) ===
          JSON.stringify(hierarchy(second)),
        edgeOrderInvariant: JSON.stringify(attachmentPairs) ===
          JSON.stringify(secondPairs),
      };
    }""")
    assert result["attachmentCount"] == 2, result
    assert result["root"] == result["hostRoot"]
    assert result["hostParent"] == [result["hostRoot"], result["hostMiddle"]]
    assert result["accessoryAParent"] == [
        result["hostTop"], result["accessoryARoot"],
    ]
    assert result["accessoryBParent"] == result["accessoryAEnd"]
    assert result["targetChain"]
    assert result["orderInvariant"]
    assert result["edgeOrderInvariant"]


def test_cross_source_reconciliation_equal_support_is_order_invariant(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, rootId) => ({
        sourceKey, boneIds: entries.map(([id]) => id),
        influenceGraph: {nodes: entries.map(([boneId, weightedCenter]) => ({
          boneId, weightedCenter, weightedRadius: .1,
          totalWeight: 10, affectedVertexCount: 20,
        }))},
        centerByBoneId: new Map(entries),
        jointPivotByBoneId: new Map(entries.filter(([id]) => id !== rootId)),
        restDirectionByBoneId: new Map(entries.map(([id]) => [
          id, [0, 1, 0],
        ])),
        restFrameByBoneId: new Map(),
        restFrameEvidenceByBoneId: new Map(),
        inferredForest: {
          components: [{
            componentId: 0, rootId, nodeIds: entries.map(([id]) => id),
            parentById: Object.fromEntries(entries.map(([id], index) => [
              id, index ? entries[index - 1][0] : null,
            ])),
            childrenById: Object.fromEntries(entries.map(([id], index) => [
              id, index < entries.length - 1 ? [entries[index + 1][0]] : [],
            ])),
            depthById: Object.fromEntries(entries.map(([id]) => [id, 0])),
            edges: [[entries[0][0], entries[1][0]]].map(([boneA, boneB]) => ({
              boneA, boneB, treeEdgeScore: 1,
            })),
          }],
          componentByBoneId: Object.fromEntries(entries.map(([id]) => [id, 0])),
        },
      });
      const first = buildModelRigReconciliation([
        make('alpha', [[0, [0, 0, 0]], [1, [0, .05, 0]]], 0),
        make('zeta', [[10, [0, .1, 0]], [11, [0, .15, 0]]], 10),
      ], {modelReferenceRadius: 1});
      const second = buildModelRigReconciliation([
        make('zeta', [[10, [0, .1, 0]], [11, [0, .15, 0]]], 10),
        make('alpha', [[0, [0, 0, 0]], [1, [0, .05, 0]]], 0),
      ], {modelReferenceRadius: 1});
      const hierarchy = value => value.components.map(item => ({
        root: item.rootId,
        parent: Object.entries(item.parentById).sort(),
      }));
      const attachments = value => value.edges
        .filter(edge => edge.relationshipType === 'attachment')
        .map(edge => [edge.targetJointId, edge.accessoryJointId]);
      return {
        attachmentCount: first.reconciliation.attachmentCount,
        hierarchy: JSON.stringify(hierarchy(first)) ===
          JSON.stringify(hierarchy(second)),
        edges: JSON.stringify(attachments(first)) ===
          JSON.stringify(attachments(second)),
        root: first.components[0]?.rootId,
      };
    }""")
    assert result["attachmentCount"] == 1, result
    assert result["hierarchy"]
    assert result["edges"]
    assert result["root"] == 0


def test_cross_source_reconciliation_confidence_lanes_and_support(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, vertexEntries = [], links = []) => {
        const nodeIds = entries.map(([id]) => id);
        const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
        links.forEach(([parent, child, score = 1]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        const roots = nodeIds.filter(id => parentById[id] === null);
        const inComponent = root => {
          const seen = new Set([root]);
          const queue = [root];
          while (queue.length) {
            const parent = queue.shift();
            (childrenById[parent] || []).forEach(child => {
              if (!seen.has(child)) {
                seen.add(child);
                queue.push(child);
              }
            });
          }
          return [...seen];
        };
        const componentByBoneId = Object.fromEntries(nodeIds.map(id => [
          id, roots.findIndex(root => inComponent(root).includes(id)),
        ]));
        return {
          sourceKey, boneIds: nodeIds,
          influenceGraph: {nodes: entries.map(([boneId, center]) => ({
            boneId, weightedCenter: center, weightedRadius: .1,
            totalWeight: 1, affectedVertexCount: 10,
          }))},
          centerByBoneId: new Map(entries),
          jointPivotByBoneId: new Map(),
          restDirectionByBoneId: new Map(entries.map(([id]) => [id,
            [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(entries.map(([id]) => [id, {
            directionSource: 'child-weighted-center',
          }])),
          inferredForest: {
            components: roots.map((rootId, componentId) => ({
              componentId, rootId, nodeIds: inComponent(rootId),
              parentById, childrenById,
              depthById: Object.fromEntries(nodeIds.map(id => [id, 0])),
              edges: links.map(([parent, child, score = 1]) => ({
                boneA: parent, boneB: child, treeEdgeScore: score,
              })),
            })),
            componentByBoneId,
          },
          vertexEvidence: vertexEntries.map(entry => ({
            meshKey: entry.meshKey,
            positions: new Float32Array(entry.positions.flat()),
            indices: new Uint16Array(entry.ids),
            weights: new Float32Array(entry.weights),
            influenceCount: entry.influenceCount,
          })),
        };
      };
      const oneInfluence = (meshKey, positions, ids, weights = null) => ({
        meshKey, positions, ids,
        weights: weights || ids.map(() => 1), influenceCount: 1,
      });
      const repeated = (count, id, weight = 1) => ({
        meshKey: 'neutral',
        positions: Array.from({length: count}, (_, index) =>
          [index * .001, 0, 0]),
        ids: Array.from({length: count}, () => id),
        weights: Array.from({length: count}, () => weight),
        influenceCount: 1,
      });
      const moderate = buildModelRigReconciliation([
        make('moderate-a', [[0, [0, 0, 0]]], [{
          ...repeated(36, 0),
          ids: Array.from({length: 36}, () => [0, 99]).flat(),
          weights: Array.from({length: 36}, () => [.4, .6]).flat(),
          influenceCount: 2,
        }]),
        make('moderate-b', [[1, [0, 0, 0]]], [{
          ...repeated(36, 1),
          ids: Array.from({length: 36}, () => [1, 98]).flat(),
          weights: Array.from({length: 36}, () => [.4, .6]).flat(),
          influenceCount: 2,
        }]),
      ], {modelReferenceRadius: 1});
      const strongPositions = Array.from({length: 12}, (_, index) =>
        [index * .001, 0, 0]);
      const strongDistance = buildModelRigReconciliation([
        make('strong-a', [[0, [0, 0, 0]]], [
          oneInfluence('neutral', strongPositions, strongPositions.map(() => 0)),
        ]),
        make('strong-b', [[1, [.052, 0, 0]]], [
          oneInfluence('neutral', strongPositions, strongPositions.map(() => 1)),
        ]),
      ], {modelReferenceRadius: 1});
      const winnerPositions = Array.from({length: 23}, (_, index) =>
        [index * .002, 0, 0]);
      const nearPositions = winnerPositions.map(([x, y, z]) => [x + .015, y, z]);
      const strongestEvidenceWins = buildModelRigReconciliation([
        make('winner-a', [[0, [0, 0, 0]]], [
          oneInfluence('neutral', winnerPositions,
            winnerPositions.map(() => 0)),
        ]),
        make('winner-b', [[1, [.037, 0, 0]], [2, [.054, 0, 0]]], [
          oneInfluence('near', nearPositions, nearPositions.map(() => 1)),
          oneInfluence('strong', winnerPositions,
            winnerPositions.map(() => 2)),
        ]),
      ], {modelReferenceRadius: 1});
      const oneVertex = buildModelRigReconciliation([
        make('single-a', [[0, [0, 0, 0]]], [
          oneInfluence('neutral', [[0, 0, 0]], [0]),
        ]),
        make('single-b', [[1, [.052, 0, 0]]], [
          oneInfluence('neutral', [[0, 0, 0]], [1]),
        ]),
      ], {modelReferenceRadius: 1});
      const threeSources = buildModelRigReconciliation([
        make('three-a', [[0, [0, 0, 0]]], [
          oneInfluence('neutral', winnerPositions,
            winnerPositions.map(() => 0)),
        ]),
        make('three-b', [[1, [0, 0, 0]]], [
          oneInfluence('neutral', winnerPositions,
            winnerPositions.map(() => 1)),
        ]),
        make('three-c', [[2, [0, 0, 0]]], [
          oneInfluence('neutral', winnerPositions,
            winnerPositions.map(() => 2)),
        ]),
      ], {modelReferenceRadius: 1});
      const id = (value, key) => value.sourceBoneToModelJointId[key];
      const accepted = value => value.reconciliation.acceptedEquivalences;
      const oneVertexCandidate = oneVertex.reconciliation.rejectedCandidates
        .find(item => item.left.sourceBoneKey === 'single-a#bone=0');
      return {
        moderateAccepted: id(moderate, 'moderate-a#bone=0') ===
          id(moderate, 'moderate-b#bone=1'),
        moderateEvidence: accepted(moderate).find(item =>
          item.left.sourceBoneKey === 'moderate-a#bone=0'),
        strongDistanceAccepted: id(strongDistance, 'strong-a#bone=0') ===
          id(strongDistance, 'strong-b#bone=1'),
        strongDistanceEvidence: accepted(strongDistance).find(item =>
          item.left.sourceBoneKey === 'strong-a#bone=0'),
        strongestEvidenceWins: id(strongestEvidenceWins, 'winner-a#bone=0') ===
          id(strongestEvidenceWins, 'winner-b#bone=2')
          && id(strongestEvidenceWins, 'winner-a#bone=0') !==
            id(strongestEvidenceWins, 'winner-b#bone=1'),
        oneVertexSeparated: id(oneVertex, 'single-a#bone=0') !==
          id(oneVertex, 'single-b#bone=1'),
        oneVertexReason: oneVertexCandidate?.rejectionReason || null,
        threeSourceMembers: threeSources.joints.find(joint =>
          joint.members.length === 3)?.members || [],
      };
    }""")
    assert result["moderateAccepted"]
    assert result["moderateEvidence"]["crossQuality"] < .7
    assert result["moderateEvidence"]["matchedVertexCount"] == 36
    assert result["strongDistanceAccepted"]
    assert result["strongDistanceEvidence"]["normalizedDistance"] > .04
    assert result["strongDistanceEvidence"]["strongCrossEvidence"]
    assert result["strongDistanceEvidence"]["supportReliability"] == 1
    assert result["strongestEvidenceWins"]
    assert result["oneVertexSeparated"]
    assert result["oneVertexReason"] in {
        "too_far", "insufficient_seed_evidence", "insufficient_confidence",
    }
    assert sorted((member["sourceKey"], member["boneId"])
                  for member in result["threeSourceMembers"]) == [
        ("three-a", 0), ("three-b", 1), ("three-c", 2),
    ]


def test_cross_source_reconciliation_aligns_undirected_palette_graphs(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, links) => {
        const nodeIds = entries.map(([id]) => id);
        const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
        links.forEach(([parent, child]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        const roots = nodeIds.filter(id => parentById[id] === null);
        return {
          sourceKey, boneIds: nodeIds,
          influenceGraph: {nodes: entries.map(([boneId, center]) => ({
            boneId, weightedCenter: center, weightedRadius: .1,
            totalWeight: 1, affectedVertexCount: 10,
          }))},
          centerByBoneId: new Map(entries),
          jointPivotByBoneId: new Map(entries.filter(([id]) =>
            parentById[id] !== null)),
          restDirectionByBoneId: new Map(entries.map(([id]) => [id,
            [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(entries.map(([id]) => [id, {
            directionSource: 'child-weighted-center',
          }])),
          inferredForest: {
            components: roots.map((rootId, componentId) => ({
              componentId, rootId, nodeIds, parentById, childrenById,
              depthById: Object.fromEntries(nodeIds.map(id => [id, 0])),
              edges: links.map(([boneA, boneB]) => ({
                boneA, boneB, treeEdgeScore: 1,
              })),
            })),
            componentByBoneId: Object.fromEntries(nodeIds.map(id => [id, 0])),
          },
        };
      };
      const chainA = make('chain-a', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
      ], [[0, 1], [1, 2]]);
      const reversedChain = make('chain-b', [
        [10, [0, 2, 0]], [11, [.09, 1, 0]], [12, [0, 0, 0]],
      ], [[10, 11], [11, 12]]);
      const twoAnchors = buildModelRigReconciliation(
        [chainA, reversedChain], {modelReferenceRadius: 1});
      const centerA = make('center-a', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
      ], [[0, 1], [1, 2]]);
      const centerB = make('center-b', [
        [10, [0, 0, 0]], [11, [.09, 1, 0]],
      ], [[10, 11]]);
      const oneAnchor = buildModelRigReconciliation(
        [centerA, centerB], {modelReferenceRadius: 1});
      const pathA = make('path-a', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
        [3, [0, 3, 0]],
      ], [[0, 1], [1, 2], [2, 3]]);
      const pathB = make('path-b', [
        [10, [0, 0, 0]], [11, [.09, 1, 0]], [12, [.09, 2, 0]],
        [13, [0, 3, 0]], [14, [.09, 1, 0]], [15, [.09, 2, 0]],
      ], [[10, 11], [11, 12], [12, 13], [10, 14], [13, 15]]);
      const pathAligned = buildModelRigReconciliation(
        [pathA, pathB], {modelReferenceRadius: 1});
      const key = (sourceKey, boneId) => `${sourceKey}#bone=${boneId}`;
      const id = (value, sourceKey, boneId) =>
        value.sourceBoneToModelJointId[key(sourceKey, boneId)];
      const graphAccepted = value => value.reconciliation.acceptedEquivalences
        .filter(item => item.pass?.startsWith('graph-alignment'));
      const middle = graphAccepted(twoAnchors).find(item =>
        item.left.sourceBoneKey === key('chain-a', 1));
      const leaf = graphAccepted(oneAnchor).find(item =>
        item.left.sourceBoneKey === key('center-a', 1));
      const pathMiddle = graphAccepted(pathAligned).find(item =>
        item.left.sourceBoneKey === key('path-a', 1));
      const pathEnd = graphAccepted(pathAligned).find(item =>
        item.left.sourceBoneKey === key('path-a', 2));
      return {
        middleMerged: id(twoAnchors, 'chain-a', 1) ===
          id(twoAnchors, 'chain-b', 11),
        middle,
        middleDistance: middle?.normalizedDistance || 0,
        leafMerged: id(oneAnchor, 'center-a', 1) ===
          id(oneAnchor, 'center-b', 11),
        leaf,
        leafDistance: leaf?.normalizedDistance || 0,
        pathMerged: id(pathAligned, 'path-a', 1) ===
          id(pathAligned, 'path-b', 11)
          && id(pathAligned, 'path-a', 2) ===
            id(pathAligned, 'path-b', 12),
        pathMiddle,
        pathEnd,
      };
    }""")
    assert result["middleMerged"]
    assert result["middle"]["pass"] == "graph-alignment-1"
    assert result["middle"]["matchedNeighborCount"] == 2
    assert result["middleDistance"] > .06
    assert result["leafMerged"]
    assert result["leaf"]["pass"] == "graph-alignment-2"
    assert result["leaf"]["matchedNeighborCount"] == 1
    assert result["leafDistance"] > .06
    assert result["pathMerged"]
    assert result["pathMiddle"]["pass"] == "graph-alignment-3"
    assert result["pathEnd"]["pass"] == "graph-alignment-3"


def test_graph_alignment_does_not_compete_across_unrelated_branches(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, links) => {
        const nodeIds = entries.map(([id]) => id);
        const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
        links.forEach(([parent, child]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        const roots = nodeIds.filter(id => parentById[id] === null);
        return {
          sourceKey, boneIds: nodeIds,
          influenceGraph: {nodes: entries.map(([boneId, center]) => ({
            boneId, weightedCenter: center, weightedRadius: .1,
            totalWeight: 1, affectedVertexCount: 10,
          }))},
          centerByBoneId: new Map(entries),
          jointPivotByBoneId: new Map(),
          restDirectionByBoneId: new Map(entries.map(([id]) => [id,
            [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(entries.map(([id]) => [id, {
            directionSource: 'child-weighted-center',
          }])),
          inferredForest: {
            components: roots.map((rootId, componentId) => ({
              componentId, rootId, nodeIds, parentById, childrenById,
              depthById: Object.fromEntries(nodeIds.map(id => [id, 0])),
              edges: links.map(([boneA, boneB]) => ({
                boneA, boneB, treeEdgeScore: 1,
              })),
            })),
            componentByBoneId: Object.fromEntries(nodeIds.map(id => [id, 0])),
          },
        };
      };
      const left = make('left', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, -1, 0]],
      ], [[0, 1], [0, 2]]);
      const rightBoth = make('right-both', [
        [10, [0, 0, 0]], [11, [.09, 1, 0]], [12, [.09, -1, 0]],
      ], [[10, 11], [10, 12]]);
      const rightOne = make('right-one', [
        [10, [0, 0, 0]], [11, [.09, 1, 0]],
      ], [[10, 11]]);
      const both = buildModelRigReconciliation([left, rightBoth],
        {modelReferenceRadius: 1});
      const one = buildModelRigReconciliation([left, rightOne],
        {modelReferenceRadius: 1});
      const key = (source, bone) => `${source}#bone=${bone}`;
      const graphPairs = value => value.reconciliation.acceptedEquivalences
        .filter(item => item.pass?.startsWith('graph-alignment'))
        .map(item => [item.left.sourceBoneKey, item.right.sourceBoneKey]);
      return {
        bothPairs: graphPairs(both), onePairs: graphPairs(one),
        bothLeaves: both.joints.filter(joint => joint.members.some(member =>
          member.sourceBoneKey === key('left', 1)
          || member.sourceBoneKey === key('left', 2))).length,
        oneLeafMerged: one.sourceBoneToModelJointId[key('left', 1)] ===
          one.sourceBoneToModelJointId[key('right-one', 11)],
      };
    }""")
    assert result["oneLeafMerged"]
    assert result["bothLeaves"] == 2
    assert all(
        pair[0] != "left#bone=1" or pair[1] == "right-both#bone=11"
        for pair in result["bothPairs"])
    assert all(
        pair[0] != "left#bone=2" or pair[1] == "right-both#bone=12"
        for pair in result["bothPairs"])


def test_same_source_attachment_proximity_does_not_join_components(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const entries = [
        [0, [0, 0, 0]], [1, [0, 1, 0]],
        [10, [.01, 0, 0]], [11, [.01, 1, 0]],
      ];
      const parentById = {0: null, 1: 0, 10: null, 11: 10};
      const childrenById = {0: [1], 1: [], 10: [11], 11: []};
      const rig = {
        sourceKey: 'single', boneIds: entries.map(([id]) => id),
        influenceGraph: {nodes: entries.map(([boneId, weightedCenter]) => ({
          boneId, weightedCenter, weightedRadius: .1,
          totalWeight: 1, affectedVertexCount: 10,
        }))},
        centerByBoneId: new Map(entries),
        jointPivotByBoneId: new Map(),
        restDirectionByBoneId: new Map(entries.map(([id]) => [id, [0, 1, 0]])),
        restFrameByBoneId: new Map(),
        restFrameEvidenceByBoneId: new Map(),
        inferredForest: {
          components: [
            {componentId: 0, rootId: 0, nodeIds: [0, 1], parentById,
              childrenById, depthById: {0: 0, 1: 1},
              edges: [{boneA: 0, boneB: 1, treeEdgeScore: 1}]},
            {componentId: 1, rootId: 10, nodeIds: [10, 11], parentById,
              childrenById, depthById: {10: 0, 11: 1},
              edges: [{boneA: 10, boneB: 11, treeEdgeScore: 1}]},
          ],
          componentByBoneId: {0: 0, 1: 0, 10: 1, 11: 1},
        },
      };
      const result = buildModelRigReconciliation([rig],
        {modelReferenceRadius: 1});
      return {
        componentCount: result.components.length,
        attachments: result.edges.filter(edge =>
          edge.relationshipType === 'attachment').length,
      };
    }""")
    assert result == {"componentCount": 2, "attachments": 0}


def test_cross_source_reconciliation_aggregates_component_attachments(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliation} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const make = (sourceKey, entries, links, vertexEntries = []) => {
        const nodeIds = entries.map(([id]) => id);
        const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
        links.forEach(([parent, child]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        const roots = nodeIds.filter(id => parentById[id] === null);
        return {
          sourceKey, boneIds: nodeIds,
          influenceGraph: {nodes: entries.map(([boneId, center]) => ({
            boneId, weightedCenter: center, weightedRadius: .1,
            totalWeight: 1, affectedVertexCount: 10,
          }))},
          centerByBoneId: new Map(entries),
          jointPivotByBoneId: new Map(),
          restDirectionByBoneId: new Map(entries.map(([id]) => [id,
            [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(entries.map(([id]) => [id, {
            directionSource: 'child-weighted-center',
          }])),
          inferredForest: {
            components: roots.map((rootId, componentId) => ({
              componentId, rootId, nodeIds, parentById, childrenById,
              depthById: Object.fromEntries(nodeIds.map(id => [id, 0])),
              edges: links.map(([boneA, boneB]) => ({
                boneA, boneB, treeEdgeScore: 1,
              })),
            })),
            componentByBoneId: Object.fromEntries(nodeIds.map(id => [id, 0])),
          },
          vertexEvidence: vertexEntries.map(entry => ({
            meshKey: entry.meshKey,
            positions: new Float32Array(entry.positions.flat()),
            indices: new Uint16Array(entry.ids),
            weights: new Float32Array(entry.ids.map(() => 1)),
            influenceCount: 1,
          })),
        };
      };
      const positions = Array.from({length: 9}, (_, index) =>
        [index * .001, 0, 0]);
      const target = make('target', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
        [3, [0, 3, 0]],
      ], [[0, 1], [1, 2], [2, 3]], [{
        meshKey: 'target/neutral', positions, ids: positions.map(() => 0),
      }]);
      const wings = make('wings', [
        [10, [.08, 0, 0]], [11, [.08, .01, 0]], [12, [.08, .02, 0]],
      ], [[10, 11], [11, 12]], [{
        meshKey: 'wings/neutral', positions,
        ids: [10, 10, 10, 11, 11, 11, 12, 12, 12],
      }]);
      const value = buildModelRigReconciliation([target, wings], {
        modelReferenceRadius: 1,
      });
      const targetJoint = value.sourceBoneToModelJointId['target#bone=0'];
      const wingJoint = value.sourceBoneToModelJointId['wings#bone=10'];
      const attachment = value.edges.find(edge =>
        edge.relationshipType === 'attachment');
      const diagnostic = value.reconciliation.attachmentDiagnostics.find(item =>
        item.decision === 'accepted' && item.left?.jointId === targetJoint
        && item.right?.jointId === wingJoint);
      const acceptedInRejected = value.reconciliation.rejectedCandidates.some(
        item => item.decision === 'accepted'
          && item.left?.jointId === targetJoint
          && item.right?.jointId === wingJoint);
      return {
        equivalent: targetJoint === wingJoint,
        attachment: attachment ? [attachment.jointA, attachment.jointB] : null,
        diagnostic,
        acceptedInRejected,
      };
    }""")
    assert not result["equivalent"]
    assert not result["acceptedInRejected"]
    assert result["attachment"] == [0, 4]
    assert result["diagnostic"]["componentMatchedVertexCount"] == 9
    assert result["diagnostic"]["componentSupportedJointPairCount"] == 3
    assert result["diagnostic"]["endpointMatchedVertexCount"] == 3
    assert result["diagnostic"]["accessoryRoot"]
    assert result["diagnostic"]["sourceWitnesses"] == [{
        "targetSourceKey": "target", "accessorySourceKey": "wings",
    }]


def test_pose_deformation_uses_joint_pivot_and_updates_normals(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three');
      const deformation = await import('./js/mesh/weight-deformation.js');
      const forest = {components: [{rootId: 0, nodeIds: [0, 1],
        childrenById: {0: [1]}}]};
      const rotation = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 0, 1), Math.PI / 2);
      const transforms = deformation.buildForestTransformsFromLocalRotations(
        forest, new Map([[0, [0, 0, 0]], [1, [1, 0, 0]]]), {
          quaternionByBoneId: new Map([[1, rotation]]),
          jointPivotByBoneId: new Map([[1, [1, 0, 0]]]),
        });
      const positions = new Float32Array([2, 0, 0, 0, 0, 0]);
      const normals = new Float32Array([1, 0, 0, 0, 0, 1]);
      const indices = new Uint32Array([1, 0, 0, 0]);
      const weights = new Float32Array([1, 0, 1, 0]);
      deformation.applyWeightedTransformDeformationInto(
        positions, positions.slice(), indices, weights, 2, transforms,
        new Uint32Array([0]));
      deformation.applyWeightedNormalDeformationInto(
        normals, new Float32Array([1, 0, 0, 0, 0, 1]), indices, weights, 2,
        new Map([[1, rotation]]), new Uint32Array([0]));
      return {position: [...positions], normal: [...normals]};
    }""")
    assert result["position"] == pytest.approx([1, 1, 0, 0, 0, 0])
    assert result["normal"] == pytest.approx([0, 1, 0, 0, 0, 1])


def test_secondary_pose_composition_preserves_base_and_propagates_offsets(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three');
      const deformation = await import('./js/mesh/weight-deformation.js');
      const forest = {components: [{rootId: 0, nodeIds: [0, 1, 2, 3],
        childrenById: {0: [1], 1: [2], 2: [3]}}]};
      const centers = new Map([
        [0, [0, 0, 0]], [1, [1, 0, 0]], [2, [2, 0, 0]],
        [3, [3, 0, 0]],
      ]);
      const manualParent = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 0, 1), Math.PI / 6);
      const manualChild = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(1, 0, 0), Math.PI / 6);
      const manualGrandchild = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 1, 0), Math.PI / 8);
      const baseRotations = new Map();
      const baseTransforms = deformation.buildForestTransformsFromLocalRotations(
        forest, centers, {quaternionByBoneId: new Map([
          [1, manualParent], [2, manualChild], [3, manualGrandchild],
        ]),
          rotationOutput: baseRotations});
      const physicsRotations = new Map([[1, [0, Math.PI / 18, 0]]]);
      const childPhysicsRotations = new Map([[2, [0, 0, Math.PI / 24]]]);
      const physicsOnly = deformation.buildForestTransformsFromLocalRotations(
        forest, centers, {rotationByBoneId: physicsRotations});
      const identityBaseRotations = new Map();
      const identityBaseTransforms = new Map([
        [0, new THREE.Matrix4()], [1, new THREE.Matrix4()],
        [2, new THREE.Matrix4()], [3, new THREE.Matrix4()],
      ]);
      const identityComposedRotations = new Map();
      const identityComposed = deformation.composeBasePoseWithPhysicsOffsets({
        forest, nodeCenters: centers,
        baseTransformByBoneId: identityBaseTransforms,
        baseRotationByBoneId: identityBaseRotations,
        getOffsetRotation: boneId => physicsRotations.get(boneId),
        rotationOutput: identityComposedRotations,
      });
      const manualOnlyRotations = new Map();
      const manualOnly = deformation.composeBasePoseWithPhysicsOffsets({
        forest, nodeCenters: centers,
        baseTransformByBoneId: baseTransforms,
        baseRotationByBoneId: baseRotations,
        rotationOutput: manualOnlyRotations,
      });
      const parentPhysicsRotations = new Map();
      const parentPhysics = deformation.composeBasePoseWithPhysicsOffsets({
        forest, nodeCenters: centers,
        baseTransformByBoneId: baseTransforms,
        baseRotationByBoneId: baseRotations,
        rotationByBoneId: physicsRotations,
        rotationOutput: parentPhysicsRotations,
      });
      const childPhysicsRotationsOutput = new Map();
      deformation.composeBasePoseWithPhysicsOffsets({
        forest, nodeCenters: centers,
        baseTransformByBoneId: baseTransforms,
        baseRotationByBoneId: baseRotations,
        rotationByBoneId: childPhysicsRotations,
        rotationOutput: childPhysicsRotationsOutput,
      });
      const quaternionMatches = (left, right) =>
        left.angleTo(right) < 1e-6;
      const matrixMatches = (left, right) => left.elements.every(
        (value, index) => Math.abs(value - right.elements[index]) < 1e-6);
      const manualMatches = [1, 2, 3].every(id =>
        matrixMatches(manualOnly.get(id), baseTransforms.get(id))
        && quaternionMatches(
          manualOnlyRotations.get(id), baseRotations.get(id)));
      const transformCache = new Map();
      const cachedFirst = deformation.composeBasePoseWithPhysicsOffsets({
        forest, nodeCenters: centers,
        baseTransformByBoneId: baseTransforms,
        baseRotationByBoneId: baseRotations,
        transformCache,
      });
      const cachedFirstElements = [...cachedFirst.get(2).elements];
      baseTransforms.get(2).elements[12] += 0.25;
      const changedBaseElements = [...baseTransforms.get(2).elements];
      const cachedSecond = deformation.composeBasePoseWithPhysicsOffsets({
        forest, nodeCenters: centers,
        baseTransformByBoneId: baseTransforms,
        baseRotationByBoneId: baseRotations,
        transformCache,
      });
      const expectedParent = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 1, 0), Math.PI / 18)
        .multiply(baseRotations.get(1)).normalize();
      const parentDelta = parentPhysicsRotations.get(1).clone()
        .multiply(baseRotations.get(1).clone().invert()).normalize();
      const expectedChild = parentDelta.clone()
        .multiply(baseRotations.get(2)).normalize();
      const expectedGrandchild = parentDelta.clone()
        .multiply(baseRotations.get(3)).normalize();
      const expectedChildPhysics = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 0, 1), Math.PI / 24)
        .multiply(baseRotations.get(2)).normalize();
      const normalBaseline = new Float32Array([1, 0, 0]);
      const normalOutput = normalBaseline.slice();
      deformation.applyWeightedNormalDeformationInto(
        normalOutput, normalBaseline, new Uint32Array([3]),
        new Float32Array([1]), 1, parentPhysicsRotations,
        new Uint32Array([0]));
      const expectedNormal = new THREE.Vector3(1, 0, 0)
        .applyQuaternion(parentPhysicsRotations.get(3)).normalize();
      return {
        identityMatches: [1, 2, 3].every(id =>
          matrixMatches(identityComposed.get(id), physicsOnly.get(id))),
        manualMatches,
        rootMatches: matrixMatches(parentPhysics.get(0), baseTransforms.get(0)),
        parentPhysicsMatches: quaternionMatches(
          parentPhysicsRotations.get(1), expectedParent),
        parentPhysicsPropagatesOnce: quaternionMatches(
          parentPhysicsRotations.get(2), expectedChild)
          && quaternionMatches(parentPhysicsRotations.get(3), expectedGrandchild),
        childPhysicsMatches: quaternionMatches(
          childPhysicsRotationsOutput.get(2), expectedChildPhysics),
        cacheRefreshesInPlace: Math.abs(
          cachedSecond.get(2).elements[12] - cachedFirstElements[12])
          > 0.2,
        cacheDoesNotMutateInput: changedBaseElements.every((value, index) =>
          Math.abs(value - baseTransforms.get(2).elements[index]) < 1e-6),
        normalsMatch: [...normalOutput].every((value, index) =>
          Math.abs(value - expectedNormal.getComponent(index)) < 1e-6),
      };
    }""")
    assert result["identityMatches"]
    assert result["manualMatches"]
    assert result["rootMatches"]
    assert result["parentPhysicsMatches"]
    assert result["parentPhysicsPropagatesOnce"]
    assert result["childPhysicsMatches"]
    assert result["cacheRefreshesInPlace"]
    assert result["cacheDoesNotMutateInput"]
    assert result["normalsMatch"]


def test_gravity_offset_stays_in_model_frame_after_manual_twist(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three');
      const physics = await import('./js/mesh/weight-physics.js');
      const deformation = await import('./js/mesh/weight-deformation.js');
      const forest = {components: [{rootId: 0, nodeIds: [0, 1],
        maxDepth: 1, depthById: {0: 0, 1: 1},
        childrenById: {0: [1]}}]};
      const restCenters = new Map([
        [0, [0, 0, 0]], [1, [0, 0, 1]],
      ]);
      return [0, Math.PI / 2, -Math.PI / 2].map(angle => {
        const manualRotation = new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(0, 0, 1), angle);
        const baseRotations = new Map();
        const baseTransforms = deformation.buildForestTransformsFromLocalRotations(
          forest, restCenters, {
            quaternionByBoneId: new Map([[1, manualRotation]]),
            rotationOutput: baseRotations,
          });
        const posedCenters = new Map([...restCenters.entries()].map(
          ([boneId, center]) => [boneId, new THREE.Vector3(...center)
            .applyMatrix4(baseTransforms.get(boneId)).toArray()]));
        const gravity = physics.buildGravityAngularAccelerations(
          forest, posedCenters, [0, -1, 0], {
            referenceRadius: 1, gravityScale: 1,
          });
        const acceleration = gravity.accelerationByBoneId.get(1);
        const composed = deformation.composeBasePoseWithPhysicsOffsets({
          forest,
          nodeCenters: posedCenters,
          baseTransformByBoneId: baseTransforms,
          baseRotationByBoneId: baseRotations,
          getOffsetRotation: boneId => boneId === 1
            ? acceleration.map(value => value * 0.01) : null,
        });
        const point = new THREE.Vector3(0, 0, 1)
          .applyMatrix4(composed.get(1));
        return {angle, acceleration, point: point.toArray()};
      });
    }""")
    assert len(result) == 3
    for sample in result:
        assert sample["acceleration"][0] > 0
        assert sample["point"][0] == pytest.approx(0, abs=1e-5)
        assert sample["point"][1] < -0.005


def test_skinning_physics_solver_uses_true_3d_vectors_and_quaternions(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const physics = await import('./js/mesh/weight-physics.js');
      const deformation = await import('./js/mesh/weight-deformation.js');
      const THREE = await import('three');
      const forest = {
        components: [{
          componentId: 0, rootId: 0, nodeIds: [0, 1, 2, 3],
          maxDepth: 2, depthById: {0: 0, 1: 1, 2: 2, 3: 2},
          childrenById: {0: [1], 1: [2, 3]},
        }],
      };
      const initial = physics.initializePhysicsState(forest);
      const initialShape = [...initial.joints.values()].map(joint => ({
        rotationVector: joint.rotationVector,
        angularVelocity: joint.angularVelocity,
      }));
      const targets = physics.buildPhysicsTargetRotations(
        forest, [.4, .2, -.2]);
      const kicked = physics.initializePhysicsState(forest);
      physics.applyReferenceFrameAngularDelta(
        kicked, forest, [.3, .4, .5], 1);
      physics.applyPhysicsKick(kicked, forest, [1, 2, 3]);
      const spring = physics.initializePhysicsState(forest);
      const targetRotationByBoneId = new Map([
        [1, [.15, .1, -.05]], [2, [.15, .1, -.05]], [3, [.15, .1, -.05]],
      ]);
      for (let index = 0; index < 1800; index += 1) {
        physics.stepSpringPhysics(spring, forest, 1 / 120, {
          targetRotationByBoneId, frequencyHz: 2, dampingRatio: 1,
        });
      }
      const settled = physics.isPhysicsSettled(
        spring, forest, [0, 0, 0], {
          targetRotationByBoneId, rotationTolerance: .002,
          velocityTolerance: .002,
        });

      const centers = new Map([
        [0, [0, 0, 0]], [1, [1, 1, 0]],
        [2, [2, 1, 0]], [3, [1, 2, 0]],
      ]);
      const translated = physics.initializePhysicsState(forest);
      const translationDiagnostics = {};
      physics.applyReferenceFrameTranslationDelta(
        translated, forest, centers, [.2, .1, .3], 1,
        translationDiagnostics);
      const velocity = physics.initializePhysicsState(forest);
      const velocityDiagnostics = {};
      physics.applyReferenceFrameLinearVelocityDelta(
        velocity, forest, centers, [.2, .1, .3], 1,
        velocityDiagnostics);

      const gravityForest = {
        components: [{rootId: 0, nodeIds: [0, 1], maxDepth: 1,
          depthById: {0: 0, 1: 1}}],
      };
      const gravity = physics.buildGravityAngularAccelerations(
        gravityForest,
        new Map([[0, [0, 0, 0]], [1, [0, 1, 0]]]),
        [1, 0, 0], {referenceRadius: 1});
      const limits = new Map([[1, .5]]);
      const limited = physics.initializePhysicsState(gravityForest);
      limited.joints.get(1).rotationVector = [.4, .3, 0];
      limited.joints.get(1).angularVelocity = [.8, .6, 1];
      physics.applyPhysicsJointLimits(limited, limits);
      const zeroLimited = physics.initializePhysicsState(gravityForest);
      zeroLimited.joints.get(1).rotationVector = [.1, .2, .3];
      zeroLimited.joints.get(1).angularVelocity = [1, 2, 3];
      physics.applyPhysicsJointLimits(zeroLimited, new Map([[1, 0]]));

      const rotations = new Map([
        [1, [0, 0, Math.PI / 2]],
        [2, [Math.PI / 2, 0, 0]],
      ]);
      const transforms = deformation.buildForestTransformsFromLocalRotations(
        forest, centers, {rotationByBoneId: rotations});
      const opposite = physics.rotationVectorBetween([1, 1, 0], [-1, -1, 0]);
      return {
        initialShape,
        targets: [...targets.entries()],
        kicked: [...kicked.joints.values()],
        spring: [...spring.joints.values()],
        settled,
        translated: [...translated.joints.values()]
          .map(joint => joint.rotationVector),
        translationDiagnostics,
        velocity: [...velocity.joints.values()]
          .map(joint => joint.angularVelocity),
        velocityDiagnostics,
        gravity: gravity.accelerationByBoneId.get(1),
        gravityDiagnostic: gravity.diagnostics.components[0],
        limited: limited.joints.get(1),
        zeroLimited: zeroLimited.joints.get(1),
        opposite,
        rootIdentity: transforms.get(0).equals(new THREE.Matrix4()),
        childMatrix: transforms.get(1).elements,
        grandchildMatrix: transforms.get(2).elements,
      };
    }""")
    assert all(
        entry["rotationVector"] == [0, 0, 0]
        and entry["angularVelocity"] == [0, 0, 0]
        for entry in result["initialShape"])
    assert [entry[0] for entry in result["targets"]] == [1, 2, 3]
    assert result["targets"][0][1] == pytest.approx([.2, .1, -.1])
    assert result["targets"][1][1] == pytest.approx([.2, .1, -.1])
    assert result["kicked"][0]["rotationVector"] == pytest.approx(
        [-.15, -.2, -.25])
    assert result["kicked"][1]["angularVelocity"] == pytest.approx(
        [1, 2, 3])
    assert result["spring"][0]["rotationVector"] == pytest.approx(
        [.15, .1, -.05], abs=.002)
    assert result["settled"]
    assert any(
        abs(value) > 1e-4
        for value in result["translated"][0])
    assert all(abs(value) > 1e-4 for value in result["velocity"][0])
    assert result["gravity"] == pytest.approx([0, 0, -9.81])
    assert result["gravityDiagnostic"]["totalAngularAccelerationVector"] == pytest.approx(
        [0, 0, -9.81])
    limited = result["limited"]
    assert math.sqrt(sum(value * value for value in limited["rotationVector"])) == pytest.approx(.5)
    assert limited["angularVelocity"][2] == pytest.approx(1)
    assert limited["angularVelocity"][0] == pytest.approx(0)
    assert limited["angularVelocity"][1] == pytest.approx(0)
    assert result["zeroLimited"] == {
        "rotationVector": [0, 0, 0], "angularVelocity": [0, 0, 0]}
    assert math.sqrt(sum(value * value for value in result["opposite"])) == pytest.approx(
        math.pi)
    assert result["rootIdentity"]
    assert result["childMatrix"] != result["grandchildMatrix"]


def test_skinning_physics_drag_controller_owns_only_rmb(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const THREE = await import('three');
      const controllerModule = await import(
        './js/scene/physics-drag-controller.js');
      const canvas = document.createElement('canvas');
      canvas.width = 100;
      canvas.height = 100;
      canvas.getBoundingClientRect = () => ({
        left: 0, top: 0, width: 100, height: 100,
      });
      document.body.appendChild(canvas);
      const camera = new THREE.PerspectiveCamera();
      const actions = [];
      const motions = [];
      const controls = {
        unsetMouseAction: button => actions.push(['unset', button]),
        setMouseAction: (action, button) =>
          actions.push(['set', action, button]),
      };
      const controller = controllerModule.createPhysicsDragController({
        canvas, camera, controls,
        onMotion: detail => motions.push(detail),
      });
      const timestamped = (type, init, timeStamp) => {
        const event = new PointerEvent(type, {
          bubbles: true, ...init,
        });
        Object.defineProperty(event, 'timeStamp', {value: timeStamp});
        return event;
      };
      const dispatch = event => canvas.dispatchEvent(event);
      controller.setEnabled(true);
      dispatch(timestamped('pointerdown', {
        pointerId: 3, button: 2, clientX: 30, clientY: 30,
      }, 2000));
      dispatch(timestamped('pointerup', {
        pointerId: 3, button: 2, clientX: 30, clientY: 30,
      }, 2010));
      const plainClickMotionCount = motions.length;
      dispatch(timestamped('pointerdown', {
        pointerId: 1, button: 0, clientX: 10, clientY: 10,
      }, 0));
      dispatch(timestamped('pointerdown', {
        pointerId: 2, button: 2, clientX: 10, clientY: 10,
      }, 0));
      dispatch(timestamped('pointermove', {
        pointerId: 2, buttons: 2, clientX: 11, clientY: 11,
      }, 10));
      dispatch(timestamped('pointermove', {
        pointerId: 2, buttons: 2, clientX: 20, clientY: 0,
      }, 30));
      dispatch(timestamped('pointerup', {
        pointerId: 2, button: 2, clientX: 20, clientY: 0,
      }, 1020));
      const activeMotions = motions.filter(motion => motion.active);
      const release = motions[motions.length - 1];
      controller.setEnabled(false);
      const disabledActions = actions.slice();
      controller.dispose();
      canvas.remove();
      return {
        actions, disabledActions, activeMotions, release,
        plainClickMotionCount,
        lmbActive: motions.some(motion => motion.source !== 'rmb-drag'),
      };
    }""")
    assert result["actions"][0] == ["unset", 2]
    assert result["disabledActions"][-1] == ["set", "PAN", 2]
    assert result["activeMotions"]
    assert result["plainClickMotionCount"] == 0
    velocity = result["activeMotions"][0]["normalizedLinearVelocityWorld"]
    assert math.hypot(velocity[0], velocity[1]) == pytest.approx(4)
    assert velocity[0] > 0 and velocity[1] > 0 and velocity[2] == 0
    assert result["release"]["active"] is False
    assert result["release"]["normalizedLinearVelocityWorld"] == [0, 0, 0]
    assert result["lmbActive"] is False


def test_active_vertex_deformation_updates_positions_and_authored_normals(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const deformation = await import('./js/mesh/weight-deformation.js');
      const THREE = await import('three');
      const baselinePositions = new Float32Array([
        1, 0, 0, 2, 0, 0, 3, 0, 0,
      ]);
      const outputPositions = new Float32Array(baselinePositions);
      const baselineNormals = new Float32Array([
        1, 0, 0, 1, 0, 0, 1, 0, 0,
      ]);
      const outputNormals = new Float32Array(baselineNormals);
      const indices = new Uint32Array([1, 2, 1, 2, 2, 0]);
      const weights = new Float32Array([1, 0, .5, .5, 1, 0]);
      const matrix = new THREE.Matrix4().makeRotationZ(Math.PI / 2)
        .setPosition(5, 7, 0);
      const rotation = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 0, 1), Math.PI / 2);
      const active = new Uint32Array([0, 1]);
      const positionCount = deformation.applyWeightedTransformDeformationInto(
        outputPositions, baselinePositions, indices, weights, 2,
        new Map([[1, matrix]]), active);
      const normalCount = deformation.applyWeightedNormalDeformationInto(
        outputNormals, baselineNormals, indices, weights, 2,
        new Map([[1, rotation]]), active);
      return {
        positions: [...outputPositions], normals: [...outputNormals],
        positionCount, normalCount,
      };
    }""")
    assert result["positionCount"] == 2
    assert result["normalCount"] == 2
    assert result["positions"][:3] == pytest.approx([5, 8, 0])
    assert result["positions"][3:6] == pytest.approx([3.5, 4.5, 0])
    assert result["positions"][6:] == [3, 0, 0]
    assert result["normals"][:3] == pytest.approx([0, 1, 0], abs=1e-6)
    assert result["normals"][3:6] == pytest.approx(
        [math.sqrt(.5), math.sqrt(.5), 0], abs=1e-6)
    assert result["normals"][6:] == [1, 0, 0]


def test_model_physics_session_owns_fixed_clock_and_generation(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {createModelPhysicsSession} = await import(
        './js/mesh/model-physics-session.js');
      const callbacks = [];
      const canceled = [];
      const events = [];
      const session = createModelPhysicsSession({
        requestAnimationFrame: callback => {
          callbacks.push(callback);
          return callback;
        },
        cancelAnimationFrame: callback => {
          canceled.push(callback);
          const index = callbacks.indexOf(callback);
          if (index >= 0) callbacks.splice(index, 1);
        },
        onInputOwnershipChanged: value => events.push(['input', value]),
      });
      const mesh = {};
      let steps = 0;
      let motions = 0;
      let detached = 0;
      let settledUpdates = 0;
      const participant = {
        mesh,
        onSessionDetached: () => { detached += 1; },
        onModelMotion: () => { motions += 1; return true; },
        step: () => { steps += 1; },
        updateSettled: () => { settledUpdates += 1; },
        isSettled: () => false,
        isVisible: () => false,
      };
      const transform = {
        orientation: [0, 0, 0, 1], translation: [0, 0, 0],
      };
      const generation = session.enable(transform);
      session.attach(participant);
      const firstFrame = callbacks.shift();
      firstFrame(0);
      const fixedFrame = callbacks.shift();
      fixedFrame(1000);
      session.handleModelTransform({
        modelTransform: {
          orientation: [0, .2, 0, .98], translation: [.1, 0, 0],
        },
      });
      const active = session.getState();
      session.disable();
      return {
        generation, activeGeneration: active.generation,
        steps, settledUpdates, motions, participantCount: active.participantCount,
        detached, canceled: canceled.length,
        disabled: session.getState(),
        inputEvents: events,
      };
    }""")
    assert result["generation"] == 1
    assert result["activeGeneration"] == 1
    assert result["steps"] == 6
    assert result["settledUpdates"] == 1
    assert result["motions"] == 1
    assert result["participantCount"] == 1
    assert result["detached"] == 1
    assert result["canceled"] >= 1
    assert result["disabled"]["enabled"] is False
    assert result["disabled"]["participantCount"] == 0
    assert result["disabled"]["generation"] == 2
    assert result["inputEvents"] == [["input", True], ["input", False]]


def test_model_physics_reset_updates_numeric_defaults_once_and_keeps_toggles(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const physics = await import('./js/mesh/model-physics-session.js');
      let notifications = 0;
      let participantSettings = null;
      const session = physics.createModelPhysicsSession({
        onStateChanged: () => { notifications += 1; },
      });
      const transform = {
        orientation: [0, 0, 0, 1], translation: [0, 0, 0],
      };
      session.enable(transform);
      session.attach({
        mesh: {}, reset: settings => { participantSettings = {...settings}; },
        isSettled: () => true,
      });
      session.setSettings({
        frequencyHz: 7, dampingRatio: 1.2, angularResponse: .9,
        translationResponse: .8, velocityResponse: .7,
        gravityEnabled: true, gravityScale: 1.8,
        constraintsEnabled: true, maxBendDegrees: 12,
      });
      const before = notifications;
      const defaults = physics.DEFAULT_MODEL_PHYSICS_SETTINGS;
      const settingsPatch = {
        frequencyHz: defaults.frequencyHz,
        dampingRatio: defaults.dampingRatio,
        angularResponse: defaults.angularResponse,
        translationResponse: defaults.translationResponse,
        velocityResponse: defaults.velocityResponse,
        gravityScale: defaults.gravityScale,
        maxBendDegrees: defaults.maxBendDegrees,
      };
      session.reset(transform, {settingsPatch});
      const state = session.getState();
      const activeNotificationCount = notifications - before;
      session.disable();
      session.setSettings({frequencyHz: 7, dampingRatio: 1.2});
      const beforeDisabledReset = notifications;
      session.reset(transform, {settingsPatch});
      const disabledResetState = session.getState();
      const disabledResetNotifications = notifications - beforeDisabledReset;
      session.setSettings({frequencyHz: 7, gravityEnabled: true});
      session.destroy();
      return {
        notificationCount: activeNotificationCount,
        state, participantSettings, defaults,
        disabledResetNotifications, disabledResetState,
        destroyedState: session.getState(),
      };
    }""")
    assert result["notificationCount"] == 1
    assert result["state"]["gravityEnabled"] is True
    assert result["state"]["constraintsEnabled"] is True
    assert result["disabledResetNotifications"] == 1
    assert result["disabledResetState"]["enabled"] is False
    assert result["disabledResetState"]["frequencyHz"] == 2
    assert result["disabledResetState"]["dampingRatio"] == pytest.approx(.35)
    assert result["destroyedState"]["frequencyHz"] == 2
    assert result["destroyedState"]["gravityEnabled"] is False
    for key in (
            "frequencyHz", "dampingRatio", "angularResponse",
            "translationResponse", "velocityResponse", "gravityScale",
            "maxBendDegrees"):
        assert result["state"][key] == result["defaults"][key]
        assert result["participantSettings"][key] == result["defaults"][key]


def test_selected_weight_mask_aggregates_authored_influences(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const selection = await import('./js/mesh/weight-selection.js');
      const mask = selection.buildSelectedWeightMask(
        new Uint32Array([0, 1, 2, 3, 4, 5]),
        new Float32Array([.2, .3, .5, .6, .1, .9]), 2, [1, 2]);
      return [...mask];
    }""")
    assert result == pytest.approx([.3, .5, 0])


def test_weight_picker_sampling_uses_smooth_distance_falloff_and_exact_fallback(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const selection = await import('./js/mesh/weight-selection.js');
      const nearby = selection.sampleNearbyBoneWeights(
        new Float32Array([0, 0, 0, .5, 0, 0, 1, 0, 0]),
        new Uint32Array([1, 2, 1, 2, 1, 2]),
        new Float32Array([.8, .2, .4, .6, .1, .9]), 2,
        [0, 0, 0], 1);
      const exact = selection.interpolateTriangleBoneWeights(
        new Uint32Array([1, 2, 1, 2, 1, 2]),
        new Float32Array([.8, .2, .4, .6, .1, .9]), 2,
        [0, 1, 2], [0.25, 0.5, 0.25]);
      return {nearby, exact};
    }""")
    assert [entry["boneId"] for entry in result["nearby"]] == [1, 2]
    assert [entry["weight"] for entry in result["nearby"]] == pytest.approx(
        [.6666667, .3333333])
    assert [entry["boneId"] for entry in result["exact"]] == [2, 1]
    assert [entry["weight"] for entry in result["exact"]] == pytest.approx(
        [.575, .425])


def test_asset_texture_identity_is_reserved_to_the_canonical_namespace(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {isAssetTextureKey} = await import('./js/textures/texture-key.js');
      return [
        isAssetTextureKey('diffuse::BodyDiffuse.dds'),
        isAssetTextureKey('diffuse::Textures/Body.dds'),
        isAssetTextureKey('diffuse::asset/abc123/BodyDiffuse.dds'),
        isAssetTextureKey('normal_map::asset/abc123/BodyNormal.dds'),
        isAssetTextureKey('diffuse::textures/my_asset_copy.dds'),
        isAssetTextureKey('invalid'),
      ];
    }""")
    assert result == [False, False, True, True, False, False]


def test_cached_model_bounds_do_not_rescan_positions(module_page):
    page = module_page
    state = page.evaluate("""async () => {
      const THREE = await import('three');
      const {expandByModelMesh} = await import('./js/scene/model-bounds.js');
      const geometry = new THREE.BufferGeometry();
      const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
      const initial = new THREE.Box3();
      expandByModelMesh(initial, mesh);
      positions[0] = Infinity;
      const cached = new THREE.Box3();
      expandByModelMesh(cached, mesh);
      return {
        initialEmpty: initial.isEmpty(),
        cachedEmpty: cached.isEmpty(),
        boundsShared: cached.equals(initial),
      };
    }""")
    assert state == {
        "initialEmpty": False, "cachedEmpty": False, "boundsShared": True,
    }


def test_selected_weight_topology_filters_weak_edges_and_pivots_synthetic_roots(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const weight = await import('./js/mesh/weight-rig.js');
      const deformation = await import('./js/mesh/weight-deformation.js');
      const weak = {
        boneA: 7, boneB: 8, sharedVertexCount: 1,
        containment: .001, jaccard: .001, normalizedDistance: 0,
      };
      const candidateEdges = weight.candidateRelationshipEdges({
        relationships: [weak],
      });
      const tree = weight.buildMaximumSpanningTree(
        [{boneId: 7}, {boneId: 8}], candidateEdges);
      const forest = {
        components: [{
          rootId: -1, nodeIds: [-1, 7, 8],
          childrenById: {'-1': [7], '7': [8]},
        }],
      };
      const centers = new Map([
        [-1, [2, 0, 0]], [7, [2, 1, 0]], [8, [2, 2, 0]],
      ]);
      const transforms = deformation.buildForestTransformsFromLocalRotations(
        forest, centers, {
          rotationByBoneId: new Map([[7, [0, 0, Math.PI / 2]]]),
        });
      const THREE = await import('three');
      const point = new THREE.Vector3(2, 1, 0).applyMatrix4(
        transforms.get(7));
      return {
        candidateCount: candidateEdges.length,
        componentCount: tree.components.length,
        pivotedPoint: point.toArray(),
      };
    }""")
    assert result == {
        "candidateCount": 0,
        "componentCount": 2,
        "pivotedPoint": pytest.approx([1, 0, 0]),
    }


def test_model_bone_stats_sum_same_ids_before_averaging(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const weight = await import('./js/mesh/weight-runtime.js');
      return weight.aggregateModelBoneStats([
        [{boneId: 45, affectedVertexCount: 2, totalWeight: .6}],
        [
          {boneId: 45, affectedVertexCount: 4, totalWeight: 2},
          {boneId: 7, affectedVertexCount: 1, totalWeight: .25},
        ],
      ]);
    }""")
    assert result["45"] == {
        "affectedVertexCount": 6,
        "averageInfluence": pytest.approx(2.6 / 6),
    }
    assert result["7"] == {
        "affectedVertexCount": 1,
        "averageInfluence": pytest.approx(.25),
    }


def test_geometry_humanoid_control_rig_is_rest_owned_and_density_invariant(module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidControlRig} = await import(
        './js/mesh/humanoid-control-rig.js');
      const points = [];
      const addBox = (minX, maxX, minY, maxY, minZ, maxZ, step = .08) => {
        for (let y = minY; y <= maxY + .001; y += step) {
          for (let x = minX; x <= maxX + .001; x += step) {
            points.push(x, y, minZ, x, y, maxZ);
          }
        }
      };
      const addLimb = (a, b, radius = .06) => {
        for (let t = 0; t <= 1.001; t += .04) {
          const x = a[0] + (b[0] - a[0]) * t;
          const y = a[1] + (b[1] - a[1]) * t;
          points.push(x - radius, y, -.04, x + radius, y, .04,
            x, y - radius, -.04, x, y + radius, .04);
        }
      };
      addBox(-.22, .22, .45, 1.7, -.08, .08);
      addLimb([-.18, 1.48], [-.52, 1.30]);
      addLimb([-.52, 1.30], [-.92, 1.20]);
      addLimb([.18, 1.48], [.52, 1.30]);
      addLimb([.52, 1.30], [.92, 1.20]);
      addLimb([-.14, .55], [-.2, .28]);
      addLimb([-.2, .28], [-.24, .02]);
      addLimb([.14, .55], [.2, .28]);
      addLimb([.2, .28], [.24, .02]);
      const makeMesh = values => ({
        userData: {humanoidRestPositions: new Float32Array(values)},
        geometry: {attributes: {position: {array: new Float32Array(
          values.map(value => value * 10))}}},
      });
      const axes = {up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1]};
      const first = buildHumanoidControlRig({meshes: [makeMesh(points)], axes});
      const dense = buildHumanoidControlRig({
        meshes: [makeMesh(points.concat(points, points))], axes,
      });
      const withAccessory = buildHumanoidControlRig({meshes: [
        makeMesh(points), {userData: {assetFill: true,
          humanoidRestPositions: new Float32Array([100, 100, 100, 120, 100, 100])}},
      ], axes});
      const sameRestWhilePosed = buildHumanoidControlRig({
        meshes: [makeMesh(points)], axes,
      });
      return {first, dense, withAccessory, sameRestWhilePosed};
    }""")
    for name in ("first", "dense", "withAccessory", "sameRestWhilePosed"):
        rig = result[name]
        assert rig["source"] == "proportional_template"
        assert rig["mode"] == "proportional_template"
        assert set(rig["controls"]) == {
            "chest", "pelvis", "leftShoulder", "leftElbow", "leftHand",
            "rightShoulder", "rightElbow", "rightHand", "leftHip",
            "leftKnee", "leftFoot", "rightHip", "rightKnee", "rightFoot",
        }
        assert all("jointId" not in control for control in rig["controls"].values())
        assert rig["diagnostics"]["voxelCount"] == 0
        assert rig["diagnostics"]["failureReasons"] == []
    assert result["first"]["controls"]["leftHand"]["position"][0] < \
        result["first"]["controls"]["leftShoulder"]["position"][0]
    height = result["first"]["diagnostics"]["characterHeight"]
    assert result["first"]["diagnostics"]["proportionalTemplate"] == {
        "characterHeight": pytest.approx(height),
        "footLiftN": .015, "legLengthN": .515, "hipToNeckLengthN": .27,
        "shoulderHalfWidthN": .055, "armLengthN": .33,
        "armDropAngleDeg": 55, "kneeFraction": .40,
        "elbowFraction": .50, "chestFraction": .50,
        "leftFoot": pytest.approx(result["first"]["controls"]["leftFoot"]["position"]),
        "rightFoot": pytest.approx(result["first"]["controls"]["rightFoot"]["position"]),
        "neck": pytest.approx(result["first"]["diagnostics"]["templatePoints"]["neck"]),
    }
    assert result["first"]["controls"]["leftHand"]["position"][0] < \
        result["first"]["controls"]["leftShoulder"]["position"][0]
    assert result["first"]["controls"]["rightHand"]["position"][0] > \
        result["first"]["controls"]["rightShoulder"]["position"][0]
    assert result["first"]["controls"]["leftFoot"]["position"] == pytest.approx(
        [-.24, .02, 0], abs=.08)
    assert result["first"]["controls"]["rightFoot"]["position"] == pytest.approx(
        [.24, .02, 0], abs=.08)
    assert result["first"]["controls"]["leftHip"]["semantic"]["sideN"] == \
        pytest.approx(result["first"]["controls"]["leftFoot"]["semantic"]["sideN"])
    assert result["first"]["controls"]["rightHip"]["semantic"]["sideN"] == \
        pytest.approx(result["first"]["controls"]["rightFoot"]["semantic"]["sideN"])
    assert result["first"]["controls"]["leftFoot"]["position"][1] < \
        result["first"]["controls"]["leftHip"]["position"][1]
    assert result["dense"]["controls"]["leftHand"]["position"] == pytest.approx(
        result["first"]["controls"]["leftHand"]["position"], abs=.05)
    assert result["withAccessory"]["controls"]["rightFoot"]["position"] == pytest.approx(
        result["first"]["controls"]["rightFoot"]["position"], abs=.05)
    assert result["sameRestWhilePosed"]["controls"]["chest"]["position"] == \
        pytest.approx(result["first"]["controls"]["chest"]["position"], abs=.001)


def test_proportional_humanoid_template_uses_exact_ratios_and_midpoints(module_page):
    result = module_page.evaluate("""async () => {
      const {buildProportionalHumanoidRig,
        DEFAULT_HUMANOID_PROPORTIONS} = await import(
          './js/mesh/humanoid-proportional-template.js');
      const axes = {up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1]};
      const build = height => buildProportionalHumanoidRig({
        characterHeight: height, leftFoot: [-.1 * height / 2, 0, -.02],
        rightFoot: [.1 * height / 2, 0, .03], semanticAxes: axes,
      });
      const rig = build(2);
      const small = build(.2);
      const large = build(20);
      const distance = (a, b) => Math.hypot(...a.map((value, index) =>
        value - b[index]));
      return {rig, small, large, defaults: DEFAULT_HUMANOID_PROPORTIONS,
        lengths: {
          arm: distance(rig.rightShoulder, rig.rightHand),
          shoulderOffset: distance(rig.neck, rig.rightShoulder),
          leg: distance(rig.rightHip, rig.rightFoot),
          armDropAngleDeg: Math.atan2(
            Math.abs(rig.rightHand[1] - rig.rightShoulder[1]),
            Math.abs(rig.rightHand[0] - rig.rightShoulder[0])) * 180 / Math.PI,
        }};
    }""")
    rig = result["rig"]
    assert result["defaults"] == {
        "footLift": .015, "legLength": .515, "hipToNeckLength": .27,
        "shoulderHalfWidth": .055, "armLength": .33,
        "armDropAngleDeg": 55, "kneeFraction": .40,
        "elbowFraction": .50, "chestFraction": .50,
    }
    assert rig["detectedLeftFoot"][1] == pytest.approx(0)
    assert rig["detectedRightFoot"][1] == pytest.approx(0)
    assert rig["leftFoot"][1] == pytest.approx(.03)
    assert rig["rightFoot"][1] == pytest.approx(.03)
    assert rig["leftHip"][0] == pytest.approx(rig["leftFoot"][0])
    assert rig["rightHip"][2] == pytest.approx(rig["rightFoot"][2])
    assert rig["leftKnee"] == pytest.approx([
        rig["leftHip"][index] + (rig["leftFoot"][index]
        - rig["leftHip"][index]) * .40 for index in range(3)])
    assert rig["rightKnee"] == pytest.approx([
        rig["rightHip"][index] + (rig["rightFoot"][index]
        - rig["rightHip"][index]) * .40 for index in range(3)])
    assert rig["pelvis"] == pytest.approx([
        (rig["leftHip"][index] + rig["rightHip"][index]) / 2 for index in range(3)])
    assert rig["neck"][1] == pytest.approx(1.6)
    assert result["lengths"] == {
        "arm": pytest.approx(.66), "shoulderOffset": pytest.approx(.11),
        "leg": pytest.approx(1.03), "armDropAngleDeg": pytest.approx(55),
    }
    assert result["small"]["proportions"] == result["large"]["proportions"]


def test_estimate_foot_depth_uses_bottom_band(module_page):
    result = module_page.evaluate("""async () => {
      const {estimateFootDepth} = await import(
        './js/mesh/humanoid-control-rig.js');
      const sole = (side, center) => [
        {x: side * .08, y: 0, z: center - .05},
        {x: side * .12, y: .015, z: center + .05},
        {x: side * .09, y: .02, z: center},
      ];
      const soles = sole(-1, .02).concat(sole(1, .04));
      const torso = [];
      for (let index = 0; index < 400; index += 1) {
        torso.push({x: index % 2 ? -.1 : .1, y: .4 + index % 10 * .04,
          z: index % 2 ? -1.2 : 1.2});
      }
      const rearHair = Array.from({length: 200}, (_, index) => ({
        x: index % 2 ? -.4 : .4, y: .12 + index % 20 * .02, z: -2,
      }));
      const forwardFootUpper = [
        {x: -.1, y: .04, z: 2}, {x: .1, y: .04, z: 2},
      ];
      const denseSoles = soles.concat(Array.from({length: 200}, (_, index) => ({
        ...soles[index % soles.length],
      })));
      const fallback = estimateFootDepth([{x: 0, y: 0, z: .2}]);
      return {
        soles: estimateFootDepth(soles),
        decorated: estimateFootDepth(
          soles.concat(torso, rearHair, forwardFootUpper)),
        dense: estimateFootDepth(denseSoles),
        fallback,
      };
    }""")
    assert result["soles"]["depthN"] == pytest.approx(.03)
    assert result["soles"]["support"] == 3
    assert result["soles"]["validSliceCount"] == 2
    assert result["soles"]["rejectedSliceCount"] == 0
    assert result["soles"]["spread"] == pytest.approx(.02)
    assert result["soles"]["diagnostics"] == {
        "method": "bottom_foot_band",
        "heightRange": [0, .02],
        "sideMinimum": .025,
    }
    assert result["soles"]["sliceCenters"][0] == {
        "side": "left", "heightRange": [0, .02], "height01": .01,
        "backDepth": pytest.approx(-.03), "frontDepth": pytest.approx(.07),
        "center": pytest.approx(.02), "thickness": pytest.approx(.1),
        "support": 3,
    }
    assert result["soles"]["sliceCenters"][1]["center"] == pytest.approx(.04)
    assert result["decorated"]["depthN"] == pytest.approx(.03)
    assert result["dense"]["depthN"] == pytest.approx(result["soles"]["depthN"])
    assert result["fallback"]["depthN"] == 0
    assert result["fallback"]["support"] == 0
    assert result["fallback"]["validSliceCount"] == 0
    assert result["fallback"]["rejectedSliceCount"] == 2
    assert result["fallback"]["fallbackUsed"] is True
    assert result["fallback"]["reason"] == "foot_depth_unavailable"


def test_geometry_humanoid_control_rig_uses_common_depth_plane(module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidControlRig} = await import(
        './js/mesh/humanoid-control-rig.js');
      const makeMesh = scale => {
        const points = [];
        for (let index = 0; index <= 20; index += 1) {
          const y = .30 + index * .07;
          for (const x of [-.12, 0, .12]) {
            points.push(x, y, 0, x, y, 0);
          }
        }
        for (const side of [-1, 1]) {
          const center = side < 0 ? .02 * 1.7 : .04 * 1.7;
          for (const y of [0, .01]) {
            points.push(side * .24, y, center - .03,
              side * .20, y, center + .03);
          }
        }
        for (const side of [-1, 1]) {
          for (const y of [.04, .08]) {
            points.push(side * .24, y, side < 0 ? 1.7 : -1.7,
              side * .20, y, side < 0 ? 1.7 : -1.7);
          }
        }
        return {userData: {humanoidRestPositions: new Float32Array(
          points.map(value => value * scale))}};
      };
      const build = scale => buildHumanoidControlRig({
        meshes: [makeMesh(scale)],
        axes: {up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1]},
      });
      const rigs = {small: build(.1), normal: build(1), large: build(10)};
      return Object.fromEntries(Object.entries(rigs).map(([name, rig]) => [name, {
        characterHeight: rig.diagnostics.characterHeight,
        bodyDepth: rig.diagnostics.bodyDepth,
        controls: rig.controls,
        detectedFeet: rig.diagnostics.detectedFeet,
        skeletonDepth: rig.diagnostics.skeletonDepth,
        controlDepthN: rig.diagnostics.controlDepthN,
      }]));
    }""")
    for name, rig in result.items():
        assert rig["skeletonDepth"]["fallbackUsed"] is False
        assert rig["skeletonDepth"]["support"] == 4
        assert rig["skeletonDepth"]["depthN"] == pytest.approx(.03, abs=.011)
        assert rig["skeletonDepth"]["diagnostics"]["method"] == "bottom_foot_band"
        assert rig["bodyDepth"] == pytest.approx(
            rig["skeletonDepth"]["depthN"] * rig["characterHeight"], abs=.02)
        assert rig["skeletonDepth"]["spread"] == pytest.approx(.02, abs=.011)
        assert rig["controls"]["leftFoot"]["position"][0] == pytest.approx(
            rig["detectedFeet"]["left"][0], abs=1e-6)
        assert rig["controls"]["rightFoot"]["position"][0] == pytest.approx(
            rig["detectedFeet"]["right"][0], abs=1e-6)
        assert rig["controls"]["leftFoot"]["position"][1] == pytest.approx(
            rig["detectedFeet"]["left"][1] + .015 * rig["characterHeight"], abs=1e-6)
        assert rig["controls"]["rightFoot"]["position"][1] == pytest.approx(
            rig["detectedFeet"]["right"][1] + .015 * rig["characterHeight"], abs=1e-6)
        assert rig["controls"]["leftFoot"]["position"][2] == pytest.approx(
            rig["bodyDepth"], abs=.011)
        assert rig["controls"]["rightFoot"]["position"][2] == pytest.approx(
            rig["bodyDepth"], abs=.011)
        assert all(depth == pytest.approx(rig["skeletonDepth"]["depthN"], abs=1e-6)
                   for depth in rig["controlDepthN"].values())
        assert all(control["semantic"]["depthN"] == pytest.approx(
            rig["skeletonDepth"]["depthN"], abs=1e-6)
                   for control in rig["controls"].values())
    for name in ("small", "large"):
        assert result[name]["skeletonDepth"]["depthN"] == pytest.approx(
            result["normal"]["skeletonDepth"]["depthN"], abs=.001)
        assert result[name]["skeletonDepth"]["validSliceCount"] == \
            result["normal"]["skeletonDepth"]["validSliceCount"]
        assert result[name]["skeletonDepth"]["rejectedSliceCount"] == \
            result["normal"]["skeletonDepth"]["rejectedSliceCount"]


def test_geometry_humanoid_control_rig_respects_source_orientation_and_readiness(
        module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidControlRig} = await import(
        './js/mesh/humanoid-control-rig.js');
      const points = [];
      const addBox = (minX, maxX, minY, maxY, minZ, maxZ, step = .08) => {
        for (let y = minY; y <= maxY + .001; y += step) {
          for (let x = minX; x <= maxX + .001; x += step) {
            points.push(x, y, minZ, x, y, maxZ);
          }
        }
      };
      const addLimb = (a, b, radius = .06) => {
        for (let t = 0; t <= 1.001; t += .04) {
          const x = a[0] + (b[0] - a[0]) * t;
          const y = a[1] + (b[1] - a[1]) * t;
          points.push(x - radius, y, -.04, x + radius, y, .04,
            x, y - radius, -.04, x, y + radius, .04);
        }
      };
      addBox(-.22, .22, .45, 1.7, -.08, .08);
      addLimb([-.18, 1.48], [-.52, 1.30]);
      addLimb([-.52, 1.30], [-.92, 1.20]);
      addLimb([.18, 1.48], [.52, 1.30]);
      addLimb([.52, 1.30], [.92, 1.20]);
      addLimb([-.14, .55], [-.2, .28]);
      addLimb([-.2, .28], [-.24, .02]);
      addLimb([.14, .55], [.2, .28]);
      addLimb([.2, .28], [.24, .02]);
      const makeMesh = values => ({
        userData: {humanoidRestPositions: new Float32Array(values)},
      });
      // A Z-up source is what the camera's -90 degree X base orientation
      // converts to the viewer's Y-up frame: (x, y, z) -> (x, -z, y).
      const zUpPoints = [];
      for (let index = 0; index < points.length; index += 3) {
        zUpPoints.push(points[index], -points[index + 2], points[index + 1]);
      }
      const canonicalAxes = {
        up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1],
      };
      const zUpAxes = {
        up: [0, 0, 1], right: [1, 0, 0], forward: [0, -1, 0],
      };
      const canonical = buildHumanoidControlRig({
        meshes: [makeMesh(points)], axes: canonicalAxes,
        orientationState: {orientationInitialized: true,
          baseOrientation: [0, 0, 0, 1], modelOrientationRevision: 1},
      });
      const zUp = buildHumanoidControlRig({
        meshes: [makeMesh(zUpPoints)], axes: zUpAxes,
        orientationState: {orientationInitialized: true,
          baseOrientation: [-Math.SQRT1_2, 0, 0, Math.SQRT1_2],
          modelOrientationRevision: 2},
      });
      const unavailable = buildHumanoidControlRig({
        meshes: [makeMesh(zUpPoints)], axes: zUpAxes,
        orientationState: {orientationInitialized: false,
          baseOrientation: [-Math.SQRT1_2, 0, 0, Math.SQRT1_2],
          modelOrientationRevision: 1},
      });
      const semantic = rig => Object.fromEntries(Object.entries(rig.controls)
        .map(([key, control]) => [key, control.semantic]));
      return {
        canonical: {available: canonical.available, accepted: canonical.accepted,
          controls: semantic(canonical), spans: canonical.diagnostics.semanticSpans,
          axes: canonical.diagnostics.semanticAxes},
        zUp: {available: zUp.available, accepted: zUp.accepted,
          controls: semantic(zUp), spans: zUp.diagnostics.semanticSpans,
          axes: zUp.diagnostics.semanticAxes,
          baseOrientation: zUp.diagnostics.baseOrientation},
        unavailable: {available: unavailable.available,
          failureReasons: unavailable.diagnostics.failureReasons,
          fitted: Object.values(unavailable.controls).some(control => control.fitted)},
      };
    }""")
    assert result["canonical"]["available"]
    assert result["canonical"]["accepted"]
    assert result["zUp"]["available"]
    assert result["zUp"]["accepted"]
    for key, semantic in result["canonical"]["controls"].items():
        assert result["zUp"]["controls"][key] == pytest.approx(semantic, abs=.001)
    assert result["zUp"]["spans"] == pytest.approx(result["canonical"]["spans"], abs=.001)
    assert result["zUp"]["axes"] == {
        "up": pytest.approx([0, 0, 1]),
        "right": pytest.approx([1, 0, 0]),
        "forward": pytest.approx([0, -1, 0]),
    }
    assert result["zUp"]["baseOrientation"] == pytest.approx(
        [-2 ** -0.5, 0, 0, 2 ** -0.5])
    assert result["unavailable"] == {
        "available": False,
        "failureReasons": ["orientation_not_ready"],
        "fitted": False,
    }


def test_camera_frame_exposes_stable_base_orientation_state(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createCameraFrame} = await import('./js/scene/camera-frame.js');
      const camera = new THREE.PerspectiveCamera(45, 4 / 3, .01, 100);
      const controls = {
        target: new THREE.Vector3(),
        update() {},
        setCamera() {},
        saveState() {},
      };
      const renderer = {
        domElement: {getBoundingClientRect: () => ({
          width: 800, height: 600, left: 0, right: 800,
        })},
        setSize() {},
      };
      const grid = {scale: new THREE.Vector3(1, 1, 1), position: new THREE.Vector3()};
      const events = [];
      const frame = createCameraFrame({camera, renderer, controls, grid,
        cancelViewSnap() {}, onOrientationChanged: state => events.push(state)});
      const before = frame.getModelTransformState();
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1));
      frame.fitTo([mesh], {initialRotationY: Math.PI / 2});
      const fitted = frame.getModelTransformState();
      frame.rotateModelQuarterTurn([mesh]);
      const turned = frame.getModelTransformState();
      mesh.geometry.dispose();
      const arrays = state => ({
        orientation: state.orientation.toArray(),
        baseOrientation: state.baseOrientation.toArray(),
        userRotation: state.userRotation.toArray(),
        orientationInitialized: state.orientationInitialized,
        modelOrientationRevision: state.modelOrientationRevision,
      });
      return {before: arrays(before), fitted: arrays(fitted),
        turned: arrays(turned), eventCount: events.length};
    }""")
    assert result["before"] == {
        "orientation": [0, 0, 0, 1],
        "baseOrientation": [0, 0, 0, 1],
        "userRotation": [0, 0, 0, 1],
        "orientationInitialized": False,
        "modelOrientationRevision": 0,
    }
    assert result["fitted"]["orientationInitialized"]
    assert result["fitted"]["modelOrientationRevision"] == 1
    assert result["fitted"]["baseOrientation"] == pytest.approx(
        [0, 2 ** -0.5, 0, 2 ** -0.5])
    assert result["turned"]["modelOrientationRevision"] == 1
    assert result["turned"]["baseOrientation"] == pytest.approx(
        result["fitted"]["baseOrientation"])
    assert result["turned"]["userRotation"] != pytest.approx(
        result["fitted"]["userRotation"])
    assert result["eventCount"] == 1


@pytest.mark.parametrize("arm_drop", [.03, .25, .5],
                         ids=["near-horizontal", "moderate", "steep"])
def test_geometry_humanoid_control_rig_uses_fixed_arm_angle(module_page, arm_drop):
    result = module_page.evaluate("""async armDrop => {
      const {buildHumanoidControlRig} = await import(
        './js/mesh/humanoid-control-rig.js');
      const points = [];
      for (let y = .4; y <= 1.7; y += .1) {
        for (let x = -.2; x <= .2; x += .1) {
          points.push(x, y, -.08, x, y, .08);
        }
      }
      const limb = (a, b) => {
        for (let t = 0; t <= 1.001; t += .04) {
          const x = a[0] + (b[0] - a[0]) * t;
          const y = a[1] + (b[1] - a[1]) * t;
          points.push(x - .06, y, -.04, x + .06, y, .04);
        }
      };
      for (const side of [-1, 1]) {
        limb([side * .2, 1.5], [side * .52, 1.5 - armDrop]);
        limb([side * .52, 1.5 - armDrop], [side * .9, 1.5 - armDrop * 2]);
        limb([side * .15, .55], [side * .25, .02]);
      }
      return buildHumanoidControlRig({
        meshes: [{userData: {humanoidRestPositions: new Float32Array(points)}}],
        axes: {up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1]},
      });
    }""", arm_drop)
    assert result["diagnostics"]["failureReasons"] == [], repr(
        result["diagnostics"])
    assert result["diagnostics"]["proportionalTemplate"]["armDropAngleDeg"] == 55
    height = result["diagnostics"]["characterHeight"]
    assert math.dist(result["controls"]["rightShoulder"]["position"],
                     result["controls"]["rightHand"]["position"]) == pytest.approx(
                         .33 * height, abs=1e-6)
    assert result["controls"]["leftHand"]["position"][0] < \
        result["controls"]["leftShoulder"]["position"][0]
    assert result["controls"]["rightHand"]["position"][0] > \
        result["controls"]["rightShoulder"]["position"][0]


@pytest.mark.parametrize("case", [
    {"name": "short-arm", "hand": .41, "elbow": .30},
    {"name": "boundary-0.27H", "hand": .45, "elbow": .31},
    {"name": "preferred-arm", "hand": .48, "elbow": .34},
    {"name": "boundary-0.42H", "hand": .60, "elbow": .43},
    {"name": "long-preferred-edge", "hand": .66, "elbow": .48},
    {"name": "overlong-decoy", "hand": .48, "elbow": .34, "decoy": True},
    {"name": "premature-bridge", "hand": .48, "elbow": .34,
     "centralBridge": True},
    {"name": "wide-skirt", "hand": .48, "elbow": .34, "skirt": True},
], ids=lambda case: case["name"])
def test_geometry_humanoid_control_rig_ignores_extra_geometry_after_anchors(module_page, case):
    result = module_page.evaluate("""async config => {
      const {buildHumanoidControlRig} = await import(
        './js/mesh/humanoid-control-rig.js');
      const points = [];
      const addBox = (minX, maxX, minY, maxY, step = .04) => {
        for (let y = minY; y <= maxY + .001; y += step) {
          for (let x = minX; x <= maxX + .001; x += step) {
            points.push(x, y, -.06, x, y, .06);
          }
        }
      };
      const addLimb = (a, b, radius = .035) => {
        for (let t = 0; t <= 1.001; t += .035) {
          const x = a[0] + (b[0] - a[0]) * t;
          const y = a[1] + (b[1] - a[1]) * t;
          points.push(x - radius, y, -.04, x + radius, y, .04,
            x, y - radius, -.04, x, y + radius, .04);
        }
      };
      addBox(-.18, .18, .38, 1.0);
      for (const side of [-1, 1]) {
        addLimb([side * .18, .82], [side * config.elbow, .75]);
        addLimb([side * config.elbow, .75], [side * config.hand, .68]);
        addLimb([side * .13, .54], [side * .16, .28], .04);
        addLimb([side * .16, .28], [side * .18, .02], .04);
      }
      if (config.centralBridge) addBox(-.08, .08, .28, .40, .05);
      if (config.skirt) addBox(-.38, .38, .44, .64, .05);
      if (config.decoy) for (const side of [-1, 1]) {
        addLimb([side * .64, .75], [side * .82, .73], .02);
      }
      const rig = buildHumanoidControlRig({
        meshes: [{userData: {humanoidRestPositions: new Float32Array(points)}}],
        axes: {up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1]},
        options: {debugSearchRegions: true},
      });
      return {
        controls: Object.fromEntries(['leftShoulder', 'rightShoulder', 'leftHand',
          'rightHand', 'leftHip', 'rightHip', 'leftFoot', 'rightFoot']
          .map(key => [key, rig.controls[key]])),
        template: rig.template,
        diagnostics: rig.diagnostics,
      };
    }""", case)
    assert result["diagnostics"]["failureReasons"] == []
    height = result["diagnostics"]["characterHeight"]
    shoulder = result["controls"]["rightShoulder"]["position"]
    hand = result["controls"]["rightHand"]["position"]
    assert result["diagnostics"]["mode"] == "proportional_template"
    assert math.dist(shoulder, hand) == pytest.approx(.33 * height, abs=1e-6)
    assert math.dist(result["controls"]["leftShoulder"]["position"],
                     result["controls"]["leftHand"]["position"]) == pytest.approx(.33 * height, abs=1e-6)
    assert abs(result["controls"]["rightShoulder"]["position"][0]
               - result["diagnostics"]["templatePoints"]["neck"][0]) == \
        pytest.approx(.055 * height, abs=1e-6)
    assert result["controls"]["leftFoot"]["position"] == pytest.approx(
        [-.18, .02, 0], abs=.08)
    assert result["controls"]["rightFoot"]["position"] == pytest.approx(
        [.18, .02, 0], abs=.08)
    assert result["controls"]["leftHip"]["semantic"]["sideN"] == pytest.approx(
        result["controls"]["leftFoot"]["semantic"]["sideN"])
    assert result["controls"]["rightHip"]["semantic"]["sideN"] == pytest.approx(
        result["controls"]["rightFoot"]["semantic"]["sideN"])


def test_humanoid_driver_binding_preserves_rest_offsets_and_avoids_double_transform(
        module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const bindingModule = await import('./js/mesh/humanoid-rig-binding.js');
      const positions = {
        chest: [0, 1.4, 0], pelvis: [0, .55, 0],
        leftShoulder: [-.1, 1.35, 0], leftElbow: [-.35, 1.18, 0],
        leftHand: [-.58, 1.08, 0], rightShoulder: [.1, 1.35, 0],
        rightElbow: [.35, 1.18, 0], rightHand: [.58, 1.08, 0],
        leftHip: [-.1, .55, 0], leftKnee: [-.1, .30, 0],
        leftFoot: [-.1, .05, 0], rightHip: [.1, .55, 0],
        rightKnee: [.1, .30, 0], rightFoot: [.1, .05, 0],
      };
      const ids = Object.keys(positions);
      const joints = ids.map((key, jointId) => ({
        jointId, restPivot: positions[key], restCenter: positions[key],
        restFrame: [0, 0, 0, 1],
      }));
      const parentById = Object.fromEntries(ids.map((key, id) => [
        id, id === 0 ? null : id - 1]));
      const childrenById = Object.fromEntries(ids.map((key, id) => [
        id, id + 1 < ids.length ? [id + 1] : []]));
      const modelRig = {
        joints, jointPivotByJointId: new Map(joints.map(joint =>
          [joint.jointId, joint.restPivot])),
        restFrameByJointId: new Map(joints.map(joint =>
          [joint.jointId, new THREE.Quaternion()])),
        components: [{componentId: 0, rootId: 0, nodeIds: joints.map(joint =>
          joint.jointId), parentById, childrenById}],
        componentByJointId: new Map(joints.map(joint => [joint.jointId, 0])),
      };
      const controlRig = {
        frame: {height: 1, forward: [0, 0, 1], right: [1, 0, 0]},
        controls: Object.fromEntries(ids.map(key => [key,
          {position: positions[key]}])),
      };
      const driverFor = key => key === 'chest' || key === 'pelvis'
        ? 'torso' : key === 'leftShoulder' ? 'left_chest_shoulder'
          : key === 'rightShoulder' ? 'right_chest_shoulder'
            : key === 'leftElbow' ? 'left_upper_arm'
              : key === 'leftHand' ? 'left_lower_arm'
                : key === 'rightElbow' ? 'right_upper_arm'
                  : key === 'rightHand' ? 'right_lower_arm'
                    : key === 'leftHip' ? 'left_pelvis_hip'
                      : key === 'leftKnee' || key === 'leftFoot'
                        ? 'left_lower_leg'
                        : key === 'rightHip' ? 'right_pelvis_hip'
                          : 'right_lower_leg';
      const heatBinding = {modelJointAssignments: new Map(ids.map((key, jointId) => [
        jointId, {driverId: driverFor(key), limbRole: key.startsWith('left')
          ? 'left_leg' : key.startsWith('right') ? 'right_leg' : null,
          progress: .5, confidence: 'high', sourceBoneKeys: [], memberCount: 1},
      ]))};
      const binding = bindingModule.buildHumanoidRigBinding({
        controlRig, modelRig, heatBinding,
      });
      const reset = bindingModule.buildHumanoidDriverBaseTransforms({
        binding, controlRig, modelRig,
      });
      const posedControls = {...positions,
        leftKnee: [-.16, .30, 0], leftFoot: [-.38, .12, 0]};
      const posed = bindingModule.buildHumanoidDriverBaseTransforms({
        binding, controlRig, modelRig, posedControls,
      });
      const lowerKnee = binding.jointBindings.get(ids.indexOf('leftKnee'));
      const lowerFoot = binding.jointBindings.get(ids.indexOf('leftFoot'));
      const resetError = [...reset.result.values()].reduce((max, matrix) =>
        Math.max(max, matrix.elements.reduce((sum, value, index) =>
          sum + Math.abs(value - new THREE.Matrix4().elements[index]), 0)), 0);
      const lowerFootTarget = posed.driverWorldByJointId.get(ids.indexOf('leftFoot'));
      return {
        lowerKneeDriver: lowerKnee?.driverId,
        lowerFootDriver: lowerFoot?.driverId,
        directBindings: binding.jointBindings.size,
        secondaryRoots: binding.secondaryAttachments.length,
        resetError,
        posedFoot: lowerFootTarget?.elements.slice(12, 15) || null,
            bindingDiagnostics: binding.diagnostics,
      };
    }""")
    assert result["lowerKneeDriver"] == "left_lower_leg"
    assert result["lowerFootDriver"] == "left_lower_leg"
    assert result["directBindings"] >= 10
    assert result["secondaryRoots"] == 0
    assert result["resetError"] < 1e-5
    assert result["posedFoot"] is None


def test_humanoid_control_ik_solves_virtual_two_bone_limb(module_page):
    result = module_page.evaluate("""async () => {
      const {solveHumanoidControlIk} = await import(
        './js/mesh/humanoid-rig-ik.js');
      const controlRig = {
        frame: {forward: [0, 0, 1], right: [1, 0, 0]},
        controls: {
          leftShoulder: {position: [0, 1, 0]},
          leftElbow: {position: [.5, .7, 0]},
          leftHand: {position: [1, .7, 0]},
        },
      };
      const solved = solveHumanoidControlIk({
        controlRig, role: 'left_arm', target: [.2, .7, 0],
      });
      const hand = solved.positions.leftHand;
      return {reached: solved.reached, residual: solved.residual,
        hand, elbow: solved.positions.leftElbow,
        distance: Math.hypot(hand[0] - .2, hand[1] - .7)};
    }""")
    assert result["reached"]
    assert result["residual"] < 1e-5
    assert result["distance"] < 1e-5


def test_humanoid_ik_pose_merge_accumulates_limb_solutions(module_page):
    result = module_page.evaluate("""async () => {
      const {mergeHumanoidLimbPose, solveHumanoidControlIk} = await import(
        './js/mesh/humanoid-rig-ik.js');
      const controlRig = {
        frame: {forward: [0, 0, 1], right: [1, 0, 0]},
        controls: {
          leftShoulder: {position: [-.2, 1.3, 0]},
          leftElbow: {position: [-.5, 1.1, 0]},
          leftHand: {position: [-.8, 1, 0]},
          rightShoulder: {position: [.2, 1.3, 0]},
          rightElbow: {position: [.5, 1.1, 0]},
          rightHand: {position: [.8, 1, 0]},
          leftHip: {position: [-.15, .55, 0]},
          leftKnee: {position: [-.15, .3, 0]},
          leftFoot: {position: [-.15, .05, 0]},
          rightHip: {position: [.15, .55, 0]},
          rightKnee: {position: [.15, .3, 0]},
          rightFoot: {position: [.15, .05, 0]},
        },
      };
      const leftArm = solveHumanoidControlIk({controlRig, role: 'left_arm',
        target: [-.55, 1, 0]});
      const afterLeftArm = mergeHumanoidLimbPose({}, leftArm.positions,
        leftArm.keys);
      const leftLeg = solveHumanoidControlIk({controlRig,
        posedControls: afterLeftArm, role: 'left_leg', target: [-.05, .2, 0]});
      const afterLeftLeg = mergeHumanoidLimbPose(afterLeftArm, leftLeg.positions,
        leftLeg.keys);
      const rightArm = solveHumanoidControlIk({controlRig,
        posedControls: afterLeftLeg, role: 'right_arm', target: [.55, 1, 0]});
      const finalPose = mergeHumanoidLimbPose(afterLeftLeg, rightArm.positions,
        rightArm.keys);
      const firstLeg = solveHumanoidControlIk({controlRig, role: 'left_leg',
        target: [-.05, .2, 0]});
      const legFirstPose = mergeHumanoidLimbPose({}, firstLeg.positions,
        firstLeg.keys);
      const secondArm = solveHumanoidControlIk({controlRig,
        posedControls: legFirstPose, role: 'left_arm', target: [-.55, 1, 0]});
      const reversePose = mergeHumanoidLimbPose(legFirstPose,
        secondArm.positions, secondArm.keys);
      return {
        leftElbow: finalPose.leftElbow,
        leftHand: finalPose.leftHand,
        leftKnee: finalPose.leftKnee,
        leftFoot: finalPose.leftFoot,
        rightElbow: finalPose.rightElbow,
        rightHand: finalPose.rightHand,
        reverseLeftHand: reversePose.leftHand,
        reverseLeftFoot: reversePose.leftFoot,
      };
    }""")
    assert result["leftElbow"] != pytest.approx([-.5, 1.1, 0])
    assert result["leftHand"] == pytest.approx([-.55, 1, 0], abs=1e-5)
    assert result["leftKnee"] != pytest.approx([-.15, .3, 0])
    assert result["leftFoot"] == pytest.approx([-.05, .2, 0], abs=1e-5)
    assert result["rightElbow"] != pytest.approx([.5, 1.1, 0])
    assert result["rightHand"] == pytest.approx([.55, 1, 0], abs=1e-5)
    assert result["reverseLeftHand"] == pytest.approx([-.55, 1, 0], abs=1e-5)
    assert result["reverseLeftFoot"] == pytest.approx([-.05, .2, 0], abs=1e-5)


def test_humanoid_binding_uses_semantic_domains_and_bounded_terminals(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const bindingModule = await import('./js/mesh/humanoid-rig-binding.js');
      const controls = {
        chest: [0, 1.4, 0], pelvis: [0, .55, 0],
        leftShoulder: [-.1, 1.35, 0], leftElbow: [-.35, 1.18, 0],
        leftHand: [-.58, 1.08, 0], rightShoulder: [.1, 1.35, 0],
        rightElbow: [.35, 1.18, 0], rightHand: [.58, 1.08, 0],
        leftHip: [-.1, .55, 0], leftKnee: [-.1, .3, 0],
        leftFoot: [-.1, .05, 0], rightHip: [.1, .55, 0],
        rightKnee: [.1, .3, 0], rightFoot: [.1, .05, 0],
      };
      const beyondHand = [-.69, 1.04, 0];
      const positions = [
        controls.chest, beyondHand, [0, 1.58, 0], [0, 1.63, 0],
        [0, 1.35, .01],
      ];
      const joints = positions.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const parentById = {0: null, 1: null, 2: null, 3: 2, 4: null};
      const childrenById = {0: [], 1: [], 2: [3], 3: [], 4: []};
      const modelRig = {
        joints,
        jointPivotByJointId: new Map(joints.map(joint =>
          [joint.jointId, joint.restPivot])),
        restFrameByJointId: new Map(joints.map(joint =>
          [joint.jointId, new THREE.Quaternion()])),
        components: [{componentId: 0, rootId: 0, nodeIds: joints.map(joint =>
          joint.jointId), parentById, childrenById}],
        componentByJointId: new Map(joints.map(joint => [joint.jointId, 0])),
      };
      const controlRig = {frame: {height: 1.5, up: [0, 1, 0],
        forward: [0, 0, 1], right: [1, 0, 0]},
        controls: Object.fromEntries(Object.entries(controls).map(
          ([key, position]) => [key, {position}]))};
      const heatBinding = {modelJointAssignments: new Map([[1, {
        driverId: 'left_lower_arm', limbRole: 'left_arm', progress: 1,
        confidence: 'high', sourceBoneKeys: ['body#bone=1'], memberCount: 1,
      }]])};
      const binding = bindingModule.buildHumanoidRigBinding({controlRig, modelRig,
        heatBinding});
      const noHeatBinding = bindingModule.buildHumanoidRigBinding({controlRig,
        modelRig});
      const diagnostic = jointId => bindingModule.getHumanoidJointBindingDiagnostics(
        binding, jointId);
      return {
        terminal: binding.jointBindings.get(1)?.driverId,
        terminalMethod: binding.jointBindings.get(1)?.bindingMethod,
        noHeatTerminal: noHeatBinding.jointBindings.get(1)?.driverId || null,
        head: diagnostic(2), child: diagnostic(3), neighbor: diagnostic(4),
      };
    }""")
    assert result["terminal"] == "left_lower_arm"
    assert result["terminalMethod"] == "heat_connectivity"
    assert result["noHeatTerminal"] is None
    assert result["head"]["bindingType"] == "secondary"
    assert result["head"]["driverId"] == "torso"
    assert result["head"]["secondaryRootId"] == 2
    assert result["head"]["secondarySubtreeSize"] == 2
    assert result["neighbor"]["driverId"] not in {"left_upper_arm", "left_lower_arm"}


def test_humanoid_heat_binding_follows_overlap_chain_and_rejects_torso_leg_branches(
        module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidHeatBinding} = await import(
        './js/mesh/humanoid-heat-binding.js');
      const controls = {
        chest: [0, 1.4, 0], pelvis: [0, .5, 0],
        leftShoulder: [-.2, 1.3, 0], leftElbow: [-.5, 1, 0],
        leftHand: [-.8, .8, 0], rightShoulder: [.2, 1.3, 0],
        rightElbow: [.5, 1, 0], rightHand: [.8, .8, 0],
        leftHip: [-.2, .5, 0], leftKnee: [-.2, .25, 0],
        leftFoot: [-.2, 0, 0], rightHip: [.2, .5, 0],
        rightKnee: [.2, .25, 0], rightFoot: [.2, 0, 0],
      };
      const centers = {
        1: [-.2, 1.3, 0], 2: [-.35, 1.15, 0], 3: [-.5, 1, 0],
        4: [-.65, .9, 0], 5: [-.9, .75, 0], 6: [0, 1.4, 0],
        7: [-.2, .4, 0],
      };
      const edge = (boneA, boneB, jointCenter) => ({boneA, boneB,
        productOverlap: 1, minOverlap: .4, containment: .2, jaccard: .1,
        treeEdgeScore: .2, jointCenter});
      const relationships = [
        edge(1, 2, [-.275, 1.225, 0]), edge(2, 3, [-.425, 1.075, 0]),
        edge(3, 4, [-.575, .95, 0]), edge(4, 5, [-.725, .85, 0]),
        edge(1, 6, [-.1, 1.35, 0]), edge(1, 7, [-.2, .85, 0]),
      ];
      const sourceRig = {sourceKey: 'body', influenceGraph: {
        evidenceMode: 'surface', nodes: Object.entries(centers).map(
          ([boneId, weightedCenter]) => ({boneId: Number(boneId), weightedCenter,
            weightedRadius: .01, totalWeight: 1, affectedMeasure: 1,
          affectedVertexCount: 10})), relationships,
      }, vertexEvidence: [{positions: controls.leftShoulder,
        indices: [1], weights: [1], influenceCount: 1}]};
      const partialRig = {sourceKey: 'cloth', influenceGraph: {
        evidenceMode: 'vertex',
        nodes: [
          {boneId: 20, weightedCenter: centers[4], weightedRadius: .01,
            totalWeight: 1, affectedVertexCount: 10},
          {boneId: 21, weightedCenter: centers[5], weightedRadius: .01,
            totalWeight: 1, affectedVertexCount: 10},
        ],
        relationships: [edge(20, 21, [-.725, .85, 0])].map(item => ({
          ...item, productOverlap: 0, sharedVertexCount: 3,
        })),
      }, vertexEvidence: [{positions: [...centers[4], ...centers[5]],
        indices: [20, 21], weights: [1, 1], influenceCount: 1}]};
      const sourceBoneToModelJointId = new Map(Object.keys(centers).map(
        boneId => [`body#bone=${boneId}`, Number(boneId)]));
      sourceBoneToModelJointId.set('cloth#bone=20', 20);
      sourceBoneToModelJointId.set('cloth#bone=21', 21);
      const controlRig = {frame: {height: 1.4, up: [0, 1, 0],
        right: [1, 0, 0], forward: [0, 0, 1]},
        controls: Object.fromEntries(Object.entries(controls).map(
          ([key, position]) => [key, {position}]))};
      const binding = buildHumanoidHeatBinding({controlRig,
        sourceRigs: [sourceRig, partialRig], modelRig: {sourceBoneToModelJointId}});
      const source = binding.sourceResults.body.left_arm;
      const partial = binding.sourceResults.cloth.left_arm;
      return {
        mainPath: source.mainPathBoneIds,
        complete: source.complete,
        maxProgress: source.maxProgress,
        partialPath: partial.mainPathBoneIds,
        partialComplete: partial.complete,
        partialSeedReason: partial.seedReason,
        armAssignments: source.assignments.map(item => [
          item.boneId, item.driverId]).sort((left, right) => left[0] - right[0]),
        assignments: [...binding.sourceBoneAssignments.values()].map(item => [
          item.boneId, item.driverId]).sort((left, right) => left[0] - right[0]),
        modelDrivers: [...binding.modelJointAssignments.values()].map(item => [
          item.boneId, item.driverId]).sort((left, right) => left[0] - right[0]),
        conflicts: binding.conflicts,
      };
    }""")
    assert result["mainPath"] == [1, 2, 3, 4, 5]
    assert result["complete"]
    assert result["maxProgress"] >= .8
    assert result["partialPath"] == []
    assert not result["partialComplete"]
    assert result["partialSeedReason"] == "no_heat_seed"
    assert result["armAssignments"] == [
        [1, "left_upper_arm"], [2, "left_upper_arm"],
        [3, "left_lower_arm"], [4, "left_lower_arm"],
        [5, "left_lower_arm"],
    ]
    assert [item for item in result["assignments"] if item[0] <= 5] == \
        result["armAssignments"]
    assert result["conflicts"] == []


def test_humanoid_heat_binding_marks_cross_limb_model_joint_conflicts(module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidHeatBinding} = await import(
        './js/mesh/humanoid-heat-binding.js');
      const controls = {
        chest: [0, 1.4, 0], pelvis: [0, .5, 0],
        leftShoulder: [-.2, 1.3, 0], leftElbow: [-.5, 1, 0],
        leftHand: [-.8, .8, 0], rightShoulder: [.2, 1.3, 0],
        rightElbow: [.5, 1, 0], rightHand: [.8, .8, 0],
        leftHip: [-.2, .5, 0], leftKnee: [-.2, .25, 0],
        leftFoot: [-.2, 0, 0], rightHip: [.2, .5, 0],
        rightKnee: [.2, .25, 0], rightFoot: [.2, 0, 0],
      };
      const sourceRig = {sourceKey: 'body', influenceGraph: {
        evidenceMode: 'surface',
        nodes: [
          {boneId: 1, weightedCenter: controls.leftHand, weightedRadius: .01,
            totalWeight: 1, affectedMeasure: 1},
          {boneId: 2, weightedCenter: controls.leftFoot, weightedRadius: .01,
            totalWeight: 1, affectedMeasure: 1},
          {boneId: 3, weightedCenter: controls.leftElbow, weightedRadius: .01,
            totalWeight: 1, affectedMeasure: 1},
          {boneId: 4, weightedCenter: controls.leftKnee, weightedRadius: .01,
            totalWeight: 1, affectedMeasure: 1},
          {boneId: 5, weightedCenter: controls.leftShoulder, weightedRadius: .01,
            totalWeight: 1, affectedMeasure: 1},
          {boneId: 6, weightedCenter: controls.leftHip, weightedRadius: .01,
            totalWeight: 1, affectedMeasure: 1},
        ], relationships: [
          {boneA: 1, boneB: 3, productOverlap: 1, minOverlap: .4,
            containment: .2, jaccard: .1, treeEdgeScore: .2,
            jointCenter: [-.65, .9, 0]},
          {boneA: 2, boneB: 4, productOverlap: 1, minOverlap: .4,
            containment: .2, jaccard: .1, treeEdgeScore: .2,
            jointCenter: [-.2, .125, 0]},
          {boneA: 5, boneB: 3, productOverlap: 1, minOverlap: .4,
            containment: .2, jaccard: .1, treeEdgeScore: .2,
            jointCenter: [-.35, 1.15, 0]},
          {boneA: 6, boneB: 4, productOverlap: 1, minOverlap: .4,
            containment: .2, jaccard: .1, treeEdgeScore: .2,
            jointCenter: [-.2, .375, 0]},
        ],
      }, vertexEvidence: [{positions: [...controls.leftShoulder,
        ...controls.leftHip], indices: [5, 6], weights: [1, 1],
        influenceCount: 1}]};
      const controlRig = {frame: {height: 1.4, up: [0, 1, 0],
        right: [1, 0, 0], forward: [0, 0, 1]},
        controls: Object.fromEntries(Object.entries(controls).map(
          ([key, position]) => [key, {position}]))};
      const sourceBoneToModelJointId = new Map([
        ['body#bone=1', 7], ['body#bone=2', 7],
        ['body#bone=3', 8], ['body#bone=4', 9],
        ['body#bone=5', 10], ['body#bone=6', 11],
      ]);
      const binding = buildHumanoidHeatBinding({controlRig,
        sourceRigs: [sourceRig], modelRig: {sourceBoneToModelJointId}});
      return {
        assignment: binding.modelJointAssignments.get(7) || null,
        conflicts: binding.conflicts,
      };
    }""")
    assert result["assignment"] is None
    assert any(conflict["reason"] == "mixed_primary_binding"
               and conflict.get("jointId") == 7
               for conflict in result["conflicts"])


def test_humanoid_heat_binding_rejects_close_centers_without_edges(module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidHeatBinding} = await import(
        './js/mesh/humanoid-heat-binding.js');
      const controls = {
        chest: [0, 1.4, 0], pelvis: [0, .5, 0],
        leftShoulder: [-.2, 1.3, 0], leftElbow: [-.5, 1, 0],
        leftHand: [-.8, .8, 0], rightShoulder: [.2, 1.3, 0],
        rightElbow: [.5, 1, 0], rightHand: [.8, .8, 0],
        leftHip: [-.2, .5, 0], leftKnee: [-.2, .25, 0],
        leftFoot: [-.2, 0, 0], rightHip: [.2, .5, 0],
        rightKnee: [.2, .25, 0], rightFoot: [.2, 0, 0],
      };
      const sourceRig = {sourceKey: 'body', influenceGraph: {
        evidenceMode: 'surface',
        nodes: [{boneId: 1, weightedCenter: controls.leftHand,
          weightedRadius: .01, totalWeight: 1, affectedMeasure: 1}],
        relationships: [],
      }};
      const controlRig = {frame: {height: 1.4, up: [0, 1, 0],
        right: [1, 0, 0], forward: [0, 0, 1]},
        controls: Object.fromEntries(Object.entries(controls).map(
          ([key, position]) => [key, {position}]))};
      const binding = buildHumanoidHeatBinding({controlRig,
        sourceRigs: [sourceRig], modelRig: {
          sourceBoneToModelJointId: new Map([['body#bone=1', 1]]),
        }});
      return {
        sourceAssignments: binding.sourceBoneAssignments.size,
        modelAssignments: binding.modelJointAssignments.size,
      };
    }""")
    assert result == {"sourceAssignments": 0, "modelAssignments": 0}


def test_humanoid_heat_driver_transforms_are_source_local(module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidSourceBoneDriverTransforms} = await import(
        './js/mesh/humanoid-rig-binding.js');
      const {applyWeightedTransformDeformation} = await import(
        './js/mesh/weight-deformation.js');
      const controls = {
        chest: [0, 1.4, 0], pelvis: [0, .5, 0],
        leftShoulder: [-.2, 1.3, 0], leftElbow: [-.5, 1, 0],
        leftHand: [-.8, .8, 0], rightShoulder: [.2, 1.3, 0],
        rightElbow: [.5, 1, 0], rightHand: [.8, .8, 0],
        leftHip: [-.2, .5, 0], leftKnee: [-.2, .25, 0],
        leftFoot: [-.2, 0, 0], rightHip: [.2, .5, 0],
        rightKnee: [.2, .25, 0], rightFoot: [.2, 0, 0],
      };
      const controlRig = {frame: {height: 1.4, up: [0, 1, 0],
        right: [1, 0, 0], forward: [0, 0, 1]},
        controls: Object.fromEntries(Object.entries(controls).map(
          ([key, position]) => [key, {position}]))};
      const heatBinding = {sourceBoneAssignments: new Map([
        ['body#bone=2', {sourceKey: 'body', boneId: 2,
          driverId: 'left_lower_arm'}],
      ])};
      const layers = buildHumanoidSourceBoneDriverTransforms({heatBinding,
        controlRig, posedControls: {leftHand: [-.6, .8, 0]}});
      const body = new Map([[2, layers.get('body').get(2).matrix]]);
      const hair = layers.get('hair') || new Map();
      const baseline = new Float32Array([-.8, .8, 0]);
      const bodyOutput = applyWeightedTransformDeformation(
        baseline, new Uint32Array([2]), new Float32Array([1]), 1, body);
      const hairOutput = applyWeightedTransformDeformation(
        baseline, new Uint32Array([7]), new Float32Array([1]), 1, hair);
      return {
        bodyBound: layers.get('body')?.has(2) || false,
        hairBound: layers.get('hair')?.has(7) || false,
        bodyOutput: [...bodyOutput], hairOutput: [...hairOutput],
      };
    }""")
    assert result["bodyBound"]
    assert not result["hairBound"]
    assert result["bodyOutput"] != pytest.approx([-.8, .8, 0])
    assert result["hairOutput"] == pytest.approx([-.8, .8, 0])


def test_driver_translation_counts_as_active_pose_joint(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {activePoseJointIds} = await import('./js/mesh/weight-runtime.js');
      const ids = activePoseJointIds({
        manualRotations: new Map([[1, {x: 0, y: 0, z: 0, w: 1}]]),
        driverTransforms: new Map([
          [1, new THREE.Matrix4()],
          [2, new THREE.Matrix4().makeTranslation(.2, 0, 0)],
        ]),
        quaternionIsIdentity: value => Math.abs(value.w) === 1
          && value.x === 0 && value.y === 0 && value.z === 0,
      });
      return ids;
    }""")
    assert result == [2]


def test_humanoid_ik_driver_changes_a_weighted_mesh_vertex(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {buildHumanoidRigBinding,
        buildHumanoidSourceBoneDriverTransforms} = await import(
          './js/mesh/humanoid-rig-binding.js');
      const {solveHumanoidControlIk} = await import(
        './js/mesh/humanoid-rig-ik.js');
      const {applyWeightedTransformDeformationInto} = await import(
        './js/mesh/weight-deformation.js');
      const controls = {
        chest: [0, 1.5, 0], pelvis: [0, .5, 0],
        leftShoulder: [-.2, 1.3, 0], leftElbow: [-.5, 1.1, 0],
        leftHand: [-.8, 1, 0], rightShoulder: [.2, 1.3, 0],
        rightElbow: [.5, 1.1, 0], rightHand: [.8, 1, 0],
        leftHip: [-.15, .5, 0], leftKnee: [-.15, .25, 0],
        leftFoot: [-.15, 0, 0], rightHip: [.15, .5, 0],
        rightKnee: [.15, .25, 0], rightFoot: [.15, 0, 0],
      };
      const controlRig = {
        accepted: true, frame: {height: 1.5, forward: [0, 0, 1],
          right: [1, 0, 0]},
        controls: Object.fromEntries(Object.entries(controls).map(
          ([key, position]) => [key, {position}])),
      };
      const modelRig = {
        joints: [{jointId: 0, restPivot: controls.leftHand,
          restCenter: controls.leftHand, restFrame: [0, 0, 0, 1]}],
        jointPivotByJointId: new Map([[0, controls.leftHand]]),
        restFrameByJointId: new Map([[0, new THREE.Quaternion()]]),
        components: [{componentId: 0, rootId: 0, nodeIds: [0],
          parentById: {0: null}, childrenById: {0: []}}],
        componentByJointId: new Map([[0, 0]]),
      };
      const heatBinding = {
        modelJointAssignments: new Map([[0, {
          driverId: 'left_lower_arm', limbRole: 'left_arm', progress: 1,
          confidence: 'high', sourceBoneKeys: ['body#bone=0'], memberCount: 1,
        }]]),
        sourceBoneAssignments: new Map([['body#bone=0', {
          sourceKey: 'body', boneId: 0, driverId: 'left_lower_arm',
        }]]),
      };
      const binding = buildHumanoidRigBinding({controlRig, modelRig,
        heatBinding});
      const solved = solveHumanoidControlIk({
        controlRig, role: 'left_arm', target: [-.55, 1, 0],
      });
      const sourceDrivers = buildHumanoidSourceBoneDriverTransforms({
        heatBinding, controlRig, posedControls: solved.positions,
      });
      const driver = new Map([[0,
        sourceDrivers.get('body').get(0).matrix]]);
      const baseline = new Float32Array(controls.leftHand);
      const output = new Float32Array(baseline);
      const changedVertexCount = applyWeightedTransformDeformationInto(
        output, baseline, new Uint32Array([0]), new Float32Array([1]), 1,
        driver, new Uint32Array([0]));
      return {
        driverId: binding.jointBindings.get(0)?.driverId,
        solved: solved.reached,
        changedVertexCount,
        output: [...output],
        baseline: [...baseline],
      };
    }""")
    assert result["driverId"] == "left_lower_arm"
    assert result["solved"]
    assert result["changedVertexCount"] == 1
    assert result["output"] != pytest.approx(result["baseline"])
