"""Frontend module contracts that do not require a running GPU viewer."""

import math

import pytest


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
      return {
        pivot: relationships[0].jointCenter,
        jointWeight: relationships[0].jointWeightTotal,
        aggregatePivot: aggregate.relationships[0].jointCenter,
        components: forest.components.map(component => component.nodeIds),
        roots: forest.components.map(component => component.rootId),
      };
    }""")
    assert result["pivot"] == pytest.approx([2, 0, 0])
    assert result["jointWeight"] == pytest.approx(.625)
    assert result["aggregatePivot"] == pytest.approx([7, 0, 0])
    assert sorted(result["components"]) == [[1, 2, 3], [4, 5]]
    assert len(set(result["roots"])) == 2


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
