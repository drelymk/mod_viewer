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
      const initial = new Map([[5, new THREE.Quaternion()]]);
      const solved = ik.solveLimbIk({
        forest: {components: [component]}, centers: points,
        jointPivots: points, localRotations: initial,
        anchorJointId: detected.anchorJointId,
        bendJointId: detected.bendJointId,
        endJointId: detected.endJointId,
        pathJointIds: detected.pathJointIds,
        bendDirection: detected.bendDirection,
        target: [1.5, .8, 0],
      });
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
      controller.dispose();
      return {initial, selectedRoot, afterTransform, shownAgain};
    }""")
    assert result["initial"]["staticObjectCount"] == 4
    assert result["initial"]["nodeCount"] == 3
    assert result["initial"]["edgeCount"] == 2
    assert result["initial"]["jointCount"] == 3
    assert result["initial"]["rebuildCount"] == 1
    assert result["selectedRoot"]["selectedJointId"] == 1
    assert result["selectedRoot"]["rebuildCount"] == 1
    assert result["afterTransform"]["rebuildCount"] == 1
    assert result["afterTransform"]["modelFrameUpdateCount"] == \
        result["initial"]["modelFrameUpdateCount"] + 2
    assert result["shownAgain"]["rebuildCount"] == 1
    assert result["shownAgain"]["selectedJointId"] is None


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


def test_cross_source_reconciliation_keeps_accessory_root_as_attachment(module_page):
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
          && first.components[0].rootId === bodyTarget,
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
    assert result["orderInvariant"]


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
