"""Frontend module contracts that do not require a running GPU viewer."""

import math

import pytest


def _call_module(page, module_path, export_name, *args):
    return page.evaluate("""async ({modulePath, exportName, args}) => {
      const module = await import(modulePath);
      const operation = module[exportName];
      if (typeof operation !== 'function') {
        throw new TypeError(`${exportName} is not callable`);
      }
      return operation(...args);
    }""", {
        "modulePath": module_path,
        "exportName": export_name,
        "args": list(args),
    })


def test_baked_animation_updates_existing_attributes_and_wraps_frames(module_page):
    result = module_page.evaluate("""async () => {
      const pending = new Map();
      let nextFrameId = 1;
      const previousRequest = window.requestAnimationFrame;
      const previousCancel = window.cancelAnimationFrame;
      window.requestAnimationFrame = callback => {
        const id = nextFrameId++;
        pending.set(id, callback);
        return id;
      };
      window.cancelAnimationFrame = id => pending.delete(id);
      const encode = values => {
        const bytes = new Uint8Array(values.buffer);
        let text = '';
        for (const value of bytes) text += String.fromCharCode(value);
        return btoa(text);
      };
      try {
        const {setControlValue} = await import('./js/editing/control-state.js');
        const {setCharacterShadowMapInvalidator} = await import(
          './js/scene/shadow-invalidation.js');
        const runtime = await import('./js/mesh/animation-runtime.js');
        const first = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
        const second = new Float32Array([0, 0, 1, 1, 0, 1, 0, 1, 1]);
        const frames = new Float32Array(first.length + second.length);
        frames.set(first);
        frames.set(second, first.length);
        const position = {array: new Float32Array(first), needsUpdate: false};
        let boundingBoxCalls = 0;
        let boundingSphereCalls = 0;
        let shadowInvalidations = 0;
        const geometry = {
          attributes: {position},
          boundingBox: {
            min: {set() {}}, max: {set() {}},
          },
          boundingSphere: {center: {set() {}}, radius: 0},
          computeBoundingBox() { boundingBoxCalls += 1; },
          computeBoundingSphere() { boundingSphereCalls += 1; },
        };
        const mesh = {
          userData: {basePositions: new Float32Array(first)}, geometry,
        };
        const originalAttribute = position;
        setCharacterShadowMapInvalidator(() => { shadowInvalidations += 1; });
        setControlValue('anim', '1');
        runtime.registerAnimatedMesh(mesh, 'clock', {
          positions: encode(frames), position_frame_bytes: first.byteLength,
          frames: 2, bounds: {min: [0, 0, 0], max: [1, 1, 1]},
        }, {clock: {
          frame_var: 'frame', fps_var: null, fps: 1,
          frame_start: 0, frame_end: 1,
          conditions: [[{var: 'anim', value: '1', negate: false}]],
        }});
        const initialId = Math.min(...pending.keys());
        pending.get(initialId)(0);
        pending.delete(initialId);
        const firstFrame = Array.from(position.array);
        const nextId = Math.max(...pending.keys());
        pending.get(nextId)(1000);
        const secondFrame = Array.from(position.array);
        setControlValue('anim', '0');
        runtime.wakeAnimationRuntime();
        const offId = Math.max(...pending.keys());
        pending.get(offId)(2000);
        const restoredFrame = Array.from(position.array);
        const snapshot = runtime.animationRuntimeSnapshot();
        runtime.resetAnimationRuntime();
        setCharacterShadowMapInvalidator(() => {});
        return {
          firstFrame, secondFrame, restoredFrame,
          sameAttribute: position === originalAttribute,
          boundingBoxCalls, boundingSphereCalls, shadowInvalidations,
          snapshot,
        };
      } finally {
        window.requestAnimationFrame = previousRequest;
        window.cancelAnimationFrame = previousCancel;
      }
    }""")
    assert result["firstFrame"] == [0, 0, 0, 1, 0, 0, 0, 1, 0]
    assert result["secondFrame"] == [0, 0, 1, 1, 0, 1, 0, 1, 1]
    assert result["restoredFrame"] == result["firstFrame"]
    assert result["sameAttribute"]
    assert result["boundingBoxCalls"] == 1
    assert result["boundingSphereCalls"] == 1
    assert result["shadowInvalidations"] == 3
    assert result["snapshot"] == {"clocks": 1, "meshes": 1, "rafActive": False}


def test_gimi_compute_animation_reuses_attributes_and_honours_pause(module_page):
    result = module_page.evaluate("""async () => {
      const pending = new Map();
      let nextRequest = 1;
      const oldRequest = window.requestAnimationFrame;
      const oldCancel = window.cancelAnimationFrame;
      window.requestAnimationFrame = callback => {
        const id = nextRequest++;
        pending.set(id, callback);
        return id;
      };
      window.cancelAnimationFrame = id => pending.delete(id);
      const encode = values => {
        const bytes = new Uint8Array(values.buffer, values.byteOffset, values.byteLength);
        let text = '';
        for (const value of bytes) text += String.fromCharCode(value);
        return btoa(text);
      };
      try {
        const {setControlValue} = await import('./js/editing/control-state.js');
        const runtime = await import('./js/mesh/animation-runtime.js');
        const base = new Float32Array([0, 0, 0, 1, 0, 0]);
        const normals = new Float32Array([0, 2, 0, 0, 2, 0]);
        const deltas = new Float32Array([
          1, 0, 0, 0, 1, 0,
          2, 0, 0, 0, 1, 0,
        ]);
        const weights = new Float32Array([
          1, 0, 0, 0, 1, 0, 0, 0,
        ]);
          const indices = new Int32Array([
            0, 0, 0, 0, 0, 0, 0, 0,
          ]);
        const poseActive = new Float32Array([1, 1]);
        const pose = new Float32Array(2 * 2 * 14);
        for (let frame = 0; frame < 2; frame += 1) {
          for (let bone = 0; bone < 2; bone += 1) {
            const offset = (frame * 2 + bone) * 14;
            pose[offset] = pose[offset + 1] = pose[offset + 2] = 1;
            pose[offset + 9] = 1;
          }
        }
        const position = {array: new Float32Array(base), needsUpdate: false};
        const normal = {array: new Float32Array(normals), needsUpdate: false};
        const mesh = {
          visible: true,
          userData: {basePositions: new Float32Array(base)},
          geometry: {attributes: {position, normal}},
        };
        setControlValue('pause', '0');
        setControlValue('anime_state', '0');
        runtime.registerAnimatedMesh(mesh, 'gimi-test', {
          kind: 'gimi_compute', vertex_count: 2,
          base_normals: encode(normals),
          shape_passes: [{deltas: encode(deltas), phase_offset: 0}],
          pose_blend: {weights: encode(weights), indices: encode(indices),
            active: encode(poseActive)},
          pose_frames: encode(pose), pose_bone_count: 2,
          pose_frame_count: 2, state_ranges: [{state: 0, start: 0, end: 1}],
          shape_frequency_var: 'freq_key', pose_frequency_var: 'freq_pose',
          pause_var: 'pause', state_var: 'anime_state',
          shape_speed: 0, pose_speed: 0, shape_wrap: 5.236,
        });
        const firstId = Math.min(...pending.keys());
        pending.get(firstId)(0);
        const first = Array.from(position.array);
        const firstNormals = Array.from(normal.array);
        setControlValue('pause', '1');
        const nextId = Math.max(...pending.keys());
        pending.get(nextId)(1000);
        const paused = Array.from(position.array);
        runtime.resetAnimationRuntime();
        return {first, firstNormals, paused};
      } finally {
        window.requestAnimationFrame = oldRequest;
        window.cancelAnimationFrame = oldCancel;
      }
    }""")
    assert result["first"] == [0.5, 0, 0, 2, 0, 0]
    assert result["firstNormals"] == [0, 1, 0, 0, 1, 0]
    assert result["paused"] == result["first"]


def test_baked_animation_shared_track_selects_active_clock_range(module_page):
    result = module_page.evaluate("""async () => {
      const pending = new Map();
      let nextFrameId = 1;
      const previousRequest = window.requestAnimationFrame;
      const previousCancel = window.cancelAnimationFrame;
      window.requestAnimationFrame = callback => {
        const id = nextFrameId++;
        pending.set(id, callback);
        return id;
      };
      window.cancelAnimationFrame = id => pending.delete(id);
      const encode = values => {
        const bytes = new Uint8Array(values.buffer);
        let text = '';
        for (const value of bytes) text += String.fromCharCode(value);
        return btoa(text);
      };
      try {
        const {setControlValue} = await import('./js/editing/control-state.js');
        const runtime = await import('./js/mesh/animation-runtime.js');
        const frames = new Float32Array([
          0, 0, 0, 1, 0, 0, 0, 1, 0,
          0, 0, 1, 1, 0, 1, 0, 1, 1,
          0, 0, 2, 1, 0, 2, 0, 1, 2,
        ]);
        const position = {array: new Float32Array(frames.slice(0, 9))};
        const mesh = {
          visible: true, userData: {basePositions: new Float32Array(position.array)},
          geometry: {attributes: {position}},
        };
        setControlValue('anim', '1');
        runtime.registerAnimatedMesh(mesh, 'track', {
          positions: encode(frames), position_frame_bytes: 36,
          frames: 3, frame_start: 0, clock_ids: ['short', 'long'],
        }, {
          short: {fps: 1, frame_start: 0, frame_end: 1,
            conditions: [[{var: 'anim', value: '1', negate: false}]]},
          long: {fps: 1, frame_start: 0, frame_end: 2,
            conditions: [[{var: 'anim', value: '4', negate: false}]]},
        });
        pending.get(Math.min(...pending.keys()))(0);
        pending.delete(1);
        const shortFirst = Array.from(position.array);
        const shortTick = Math.max(...pending.keys());
        pending.get(shortTick)(1000);
        const shortSecond = Array.from(position.array);
        setControlValue('anim', '4');
        const switchTick = Math.max(...pending.keys());
        pending.get(switchTick)(2000);
        const longFirst = Array.from(position.array);
        const longTick = Math.max(...pending.keys());
        pending.get(longTick)(4000);
        const longThird = Array.from(position.array);
        runtime.resetAnimationRuntime();
        return {shortFirst, shortSecond, longFirst, longThird};
      } finally {
        window.requestAnimationFrame = previousRequest;
        window.cancelAnimationFrame = previousCancel;
      }
    }""")
    assert result["shortFirst"][2] == 0
    assert result["shortSecond"][2] == 1
    assert result["longFirst"][2] == 0
    assert result["longThird"][2] == 2


def test_baked_animation_resume_restores_bounds_and_wakes_after_ownership(
        module_page):
    result = module_page.evaluate("""async () => {
      const pending = new Map();
      let nextFrameId = 1;
      const previousRequest = window.requestAnimationFrame;
      const previousCancel = window.cancelAnimationFrame;
      window.requestAnimationFrame = callback => {
        const id = nextFrameId++;
        pending.set(id, callback);
        return id;
      };
      window.cancelAnimationFrame = id => pending.delete(id);
      const encode = values => {
        const bytes = new Uint8Array(values.buffer);
        let text = '';
        for (const value of bytes) text += String.fromCharCode(value);
        return btoa(text);
      };
      try {
        const {setControlValue} = await import('./js/editing/control-state.js');
        const {setCharacterShadowGeometryInvalidator} = await import(
          './js/scene/shadow-invalidation.js');
        const runtime = await import('./js/mesh/animation-runtime.js');
        const {createSkinningRuntime} = await import(
          './js/mesh/skinning-runtime.js');
        let geometryInvalidations = 0;
        let boxMin = null;
        let boxMax = null;
        const position = {array: new Float32Array([0, 0, 0])};
        const geometry = {
          attributes: {position},
          boundingBox: {
            min: {set(x, y, z) { boxMin = [x, y, z]; }},
            max: {set(x, y, z) { boxMax = [x, y, z]; }},
          },
          boundingSphere: {center: {set() {}}, radius: 0},
          computeBoundingBox() {}, computeBoundingSphere() {},
        };
        const mesh = {
          visible: true, userData: {basePositions: new Float32Array([0, 0, 0])},
          geometry,
        };
        setCharacterShadowGeometryInvalidator(
          () => { geometryInvalidations += 1; });
        setControlValue('anim', '1');
        runtime.registerAnimatedMesh(mesh, 'clock', {
          positions: encode(new Float32Array([0, 0, 0, 0, 0, 1])),
          position_frame_bytes: 12, frames: 2,
          bounds: {min: [-2, -3, -4], max: [5, 6, 7]},
        }, {clock: {
          fps: 1, frame_start: 0, frame_end: 1,
          conditions: [[{var: 'anim', value: '1', negate: false}]],
        }});
        pending.get(Math.min(...pending.keys()))(0);
        mesh.userData.animationSuspended = true;
        const suspendedTick = Math.max(...pending.keys());
        pending.get(suspendedTick)(100);
        pending.clear();
        mesh.geometry.attributes.position.array[0] = 9;
        const resumed = runtime.resumeAnimatedMesh(mesh);
        const pendingAfterResume = pending.size;
        const restored = Array.from(position.array);
        const geometryInvalidationsAfterResume = geometryInvalidations;
        mesh.userData.animationSuspended = false;
        const resumeTick = Math.max(...pending.keys());
        pending.get(resumeTick)(1000);
        const resumedFrame = Array.from(position.array);
        const skinningState = {
          loaded: true,
          baselinePositions: new Float32Array([0, 0, 0]),
          baselineNormals: null,
          poseActiveVertices: null,
          physicsActiveVertices: null,
          physicsEnabled: false,
          deformationMode: null,
          combinedActiveVertices: null,
          combinedPoseVerticesRef: null,
          combinedPhysicsVerticesRef: null,
          finalBoundsDirty: true,
          preDeformationFrustumCulled: true,
        };
        const skinningStates = new Map([[mesh, skinningState]]);
        const skinningRuntime = createSkinningRuntime({
          states: skinningStates,
          knownMeshes: new Set([mesh]),
          stateFor: candidate => skinningStates.get(candidate),
          getModelRigState: () => ({explicitRootSignatures: new Set()}),
          getRigPresetState: () => ({}),
        });
        mesh.frustumCulled = true;
        mesh.userData.animationSuspended = true;
        skinningState.preDeformationFrustumCulled = true;
        mesh.frustumCulled = false;
        skinningRuntime.applyDeformation(mesh, skinningState, {
          request: false, invalidateShadow: false,
        });
        const frustumCulledAfterResume = mesh.frustumCulled;
        runtime.resetAnimationRuntime();
        setCharacterShadowGeometryInvalidator(() => {});
        return {resumed, pendingAfterResume, restored, resumedFrame,
          geometryInvalidations, geometryInvalidationsAfterResume,
          boxMin, boxMax, frustumCulledAfterResume};
      } finally {
        window.requestAnimationFrame = previousRequest;
        window.cancelAnimationFrame = previousCancel;
      }
    }""")
    assert result["resumed"] is True
    assert result["pendingAfterResume"] == 1
    assert result["restored"] == [0, 0, 0]
    assert result["resumedFrame"] == [0, 0, 1]
    assert result["geometryInvalidationsAfterResume"] == 1
    assert result["boxMin"] == [-2, -3, -4]
    assert result["boxMax"] == [5, 6, 7]
    assert result["frustumCulledAfterResume"] is True


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


def test_rig_pose_runtime_reuses_affected_vertices_and_clears_to_rest(
        module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createRigPoseRuntime} = await import(
        './js/mesh/rig-pose-runtime.js');
      const sourceKey = 'body|offset=0';
      const mesh = {};
      const skinningState = {
        indices: new Uint32Array([0, 1, 1, 2, 0, 0]),
        weights: new Float32Array([.2, .8, .3, .7, 1, 0]),
        influenceCount: 2,
      };
      const component = {
        componentId: 0, rootId: 0, nodeIds: [0, 1, 2],
        parentById: {0: null, 1: 0, 2: 1},
        childrenById: {0: [1], 1: [2], 2: []},
      };
      const forest = {
        components: [component],
        componentByBoneId: {0: 0, 1: 0, 2: 0},
      };
      const sourceRig = {
        sourceKey, boneIds: [0, 1, 2], meshes: new Set([mesh]),
        inferredForest: forest,
        poseRotationByBoneId: new Map(), poseTransforms: new Map(),
        poseRotations: new Map(), physicsRig: null,
        poseFrameCache: new Map(),
      };
      const rig = {
        joints: [0, 1, 2].map(jointId => ({jointId, members: [
          {sourceKey, boneId: jointId},
        ]})),
        components: [component], componentByJointId: new Map([
          [0, 0], [1, 0], [2, 0],
        ]),
        inferredForest: forest,
        sourceRigs: [sourceRig],
        sourceBoneToModelJointId: new Map([0, 1, 2].map(jointId => [
          `${sourceKey}#bone=${jointId}`, jointId,
        ])),
        centerByJointId: new Map([[0, [0, 0, 0]], [1, [1, 0, 0]],
          [2, [2, 0, 0]]]),
        jointPivotByJointId: new Map([[0, [0, 0, 0]], [1, [0, 0, 0]],
          [2, [1, 0, 0]]]),
        poseRotationByJointId: new Map(), poseTransforms: new Map(),
        poseRotations: new Map(), manualPoseTransforms: new Map(),
        humanoidDriverTransforms: new Map(), poseTransformCache: new Map(),
        poseFrameCache: new Map(), sourceTransformAliases: new Map(),
        sourceRotationAliases: new Map(), poseAffectedJointIds: new Set(),
        poseActiveJointKey: '', poseActiveVerticesByMesh: new Map(),
        poseSourceBoneIdsByMesh: new Map(), poseRevision: 0,
        structureRevision: 7,
      };
      const state = {loaded: true, humanoidPose: {}, selectedJointId: null,
        pickStatus: '', humanoidRigEditPhysicsSuspended: false};
      let deformationCalls = 0;
      let rootCall = null;
      const runtime = createRigPoseRuntime({
        state, getRig: () => rig,
        sourceSkinningRigs: new Map([[sourceKey, sourceRig]]),
        skinningRuntime: {
          applyDeformation() { deformationCalls += 1; return true; },
          finalizeDeformationGeometry() { return false; },
        },
        physicsRuntime: {
          forEachRigMesh(source, callback) {
            for (const candidate of source.meshes || []) {
              callback(candidate, skinningState);
            }
          },
          refreshParticipantDerivedState() {},
          applySourceDeformation() { return true; },
          syncRigParticipantState() {},
        },
        modelPhysicsSession: {
          getSettings: () => ({}), getState: () => ({enabled: false}),
          setSuspended() {}, reset() {}, wake() {},
        },
        getModelTransformState: () => ({}), invalidateShadow: () => {},
        getModelJointId: (key, boneId) => key === sourceKey
          ? Number(boneId) : undefined,
        hasActivePhysics: () => false,
        quaternionIsIdentity: value => Math.abs(value.x) < 1e-8
          && Math.abs(value.y) < 1e-8 && Math.abs(value.z) < 1e-8
          && Math.abs(Math.abs(value.w) - 1) < 1e-8,
        setComponentRoot: (key, boneId) => {
          rootCall = [key, boneId];
          return true;
        },
        resetModelPose: () => false,
        notifyChanged: () => {}, notifyPoseChanged: () => {},
        requestRender: () => {},
        rigPresetState: {selectedPresetId: null, lastApplyResult: null},
      });
      const rotation = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 0, 1), Math.PI / 2);
      const posed = runtime.setRotation(1, rotation, {dragging: true});
      const firstVertices = skinningState.poseActiveVertices;
      const activeVertices = [...firstVertices];
      const descendantTransform = [...sourceRig.poseTransforms.get(2).elements];
      const repeated = runtime.setRotation(1, rotation, {dragging: true});
      const reusedVertices = firstVertices === skinningState.poseActiveVertices;
      const cleared = runtime.clearManualPose({request: false});
      const restTransform = [...sourceRig.poseTransforms.get(2).elements];
      state.humanoidPose = {head: [1, 2, 3]};
      const structureBeforeEditReset = rig.structureRevision;
      const resetForEdit = runtime.resetForHumanoidEdit({request: false});
      const rooted = runtime.setRoot(1);
      return {
        posed, repeated, reusedVertices, activeVertices, descendantTransform,
        deformationCalls, cleared, restTransform, resetForEdit,
        humanoidPose: state.humanoidPose,
        structureBeforeEditReset, structureAfterEditReset: rig.structureRevision,
        rooted, rootCall,
      };
    }""")
    assert result["posed"]
    assert result["repeated"]
    assert result["reusedVertices"]
    assert result["activeVertices"] == [0, 1]
    assert result["descendantTransform"] != pytest.approx(
        [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    assert result["deformationCalls"] >= 2
    assert result["cleared"]
    assert result["restTransform"] == pytest.approx(
        [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    assert result["resetForEdit"]
    assert result["humanoidPose"] == {}
    assert result["structureBeforeEditReset"] == \
        result["structureAfterEditReset"] == 7
    assert result["rooted"]
    assert result["rootCall"] == ["body|offset=0", 1]


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


def test_rig_surface_pick_miss_preserves_selection_and_pick_intent(module_page):
    result = module_page.evaluate("""async () => {
      const {createRigModelSession} = await import(
        './js/mesh/rig-model-session.js');
      const {createRigRuntimeState} = await import('./js/mesh/weight-runtime.js');
      const {modelRigState: state} = createRigRuntimeState();
      state.loaded = true;
      state.selectedJointId = 1;
      let hit = null;
      const session = createRigModelSession({
        state,
        getSnapshot: () => ({model: {joints: [{jointId: 0}, {jointId: 1}]}}),
        pickFromSurface: () => hit,
        notifyChanged: () => {}, requestRender: () => {},
      });
      session.beginJointPicking({type: 'selected-joint'});
      const missed = session.pickJointFromSurface({clientX: 10, clientY: 20});
      const afterMiss = {
        selected: state.selectedJointId, intent: state.jointPickIntent,
        status: state.pickStatus.messageKey,
      };
      hit = 0;
      const picked = session.pickJointFromSurface({clientX: 30, clientY: 40});
      return {missed, afterMiss, picked, selected: state.selectedJointId,
        intent: state.jointPickIntent, status: state.pickStatus};
    }""")
    assert result == {
        "missed": False,
        "afterMiss": {
            "selected": 1, "intent": {"type": "selected-joint"},
            "status": "weightRig.status.noRigJointAtPoint",
        },
        "picked": True, "selected": 0, "intent": None, "status": "",
    }


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
        getBoundingClientRect: () => new DOMRect(10, 20, 200, 200),
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


def test_rig_overlay_reuses_structure_across_state_lifecycle(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createRigOverlayController} = await import(
        './js/scene/rig-overlay-controller.js');
      const scene = new THREE.Scene();
      const model = new THREE.Object3D();
      scene.add(model);
      const pose = new Map([
        [1, {center: [0, 0, 0], pivot: [0, 0, 0]}],
        [2, {center: [1, 0, 0], pivot: [.5, 0, 0]}],
        [3, {center: [2, 0, 0], pivot: [1.5, 0, 0]}],
      ]);
      const source = {
        sourceKey: 'model-rig', structureRevision: 1,
        joints: [1, 2, 3].map((jointId, index) => ({
          jointId, restCenter: [index, 0, 0],
          restPivot: [index ? index - .5 : 0, 0, 0],
        })),
        components: [{componentId: 0, rootId: 1, nodeIds: [1, 2, 3],
          parentById: {1: null, 2: 1, 3: 2},
          childrenById: {1: [2], 2: [3], 3: []}}],
        forestEdges: [
          {jointA: 1, jointB: 2, parentId: 1, childId: 2},
          {jointA: 2, jointB: 3, parentId: 2, childId: 3},
        ],
        poseRotationByJointId: {},
      };
      let state = {selectedJointId: null, jointPickIntent: null, model: source};
      const controller = createRigOverlayController({
        scene, getMeshes: () => [model], getRigState: () => state,
        getRigJointPoseFrame: id => pose.get(Number(id)),
      });

      controller.refresh(state);
      const initial = controller.getDebugState();
      state = {...state, selectedJointId: 2,
        jointPickIntent: {type: 'selected-joint'}};
      controller.refresh(state);
      const picking = controller.getDebugState();
      pose.set(2, {center: [1.5, 0, 0], pivot: [1, 0, 0]});
      state = {...state, model: {...source, poseRevision: 2}};
      controller.refresh(state);
      const posed = controller.getDebugState();
      model.position.x = 4;
      window.dispatchEvent(new CustomEvent(
        'mod-viewer-model-transform-changed', {detail: {}}));
      const transformed = controller.getDebugState();
      state = {...state, jointPickIntent: null,
        model: {...state.model, structureRevision: 2}};
      controller.refresh(state);
      const rebuilt = controller.getDebugState();
      state = {...state, model: null};
      controller.refresh(state);
      const unavailable = controller.getDebugState();
      controller.dispose();
      return {initial, picking, posed, transformed, rebuilt, unavailable};
    }""")
    assert result["initial"]["rebuildCount"] == 1
    assert result["initial"]["nodeCount"] == 3
    assert result["initial"]["edgeCount"] == 2
    assert result["picking"]["staticVisible"] is True
    assert result["picking"]["rebuildCount"] == 1
    assert result["posed"]["rebuildCount"] == 1
    assert result["transformed"]["rebuildCount"] == 1
    assert result["transformed"]["modelFrameUpdateCount"] > \
        result["initial"]["modelFrameUpdateCount"]
    assert result["rebuilt"]["rebuildCount"] == 2
    assert result["rebuilt"]["nodeCount"] == 3
    assert result["unavailable"]["groupVisible"] is False
    assert result["unavailable"]["staticVisible"] is False


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


def test_rig_overlay_selects_humanoid_controls_and_keeps_orbit_available(
        module_page):
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
      canvas.getBoundingClientRect = () => new DOMRect(10, 20, 200, 200);
      document.body.appendChild(canvas);
      const positions = {
        chest: [0, 1.8, 0], pelvis: [0, 1, 0],
        neck: [0, 2.1, 0], head: [0, 2.4, 0],
        leftShoulder: [-.2, 1.6, 0], leftElbow: [-.5, 1.4, 0],
        leftHand: [-.8, 1.2, 0], rightShoulder: [.2, 1.6, 0],
        rightElbow: [.5, 1.4, 0], rightHand: [.8, 1.2, 0],
        leftHip: [-.15, 1, 0], leftKnee: [-.2, .5, 0],
        leftFoot: [-.2, 0, 0], rightHip: [.15, 1, 0],
        rightKnee: [.2, .5, 0], rightFoot: [.2, 0, 0],
      };
      const source = {key: 'model-rig', structureRevision: 1,
        joints: [], components: [], forestEdges: [],
        humanoidControlRig: {available: true,
          controls: Object.fromEntries(Object.entries(positions).map(
            ([key, position]) => [key, {position}]))}};
      let state = {model: source, jointPickIntent: null,
        humanoidRigEdit: {editing: false},
        ik: {enabled: true, available: true, activeLimbRole: 'left_arm',
          selectedHumanoidControlKey: null,
          controlKeys: ['leftShoulder', 'leftElbow', 'leftHand']}};
      const selected = [];
      let leftDown = 0;
      let rightDown = 0;
      canvas.addEventListener('pointerdown', event => {
        if (event.button === 0) leftDown += 1;
        if (event.button === 2) rightDown += 1;
      });
      const controller = createRigOverlayController({
        scene, camera, canvas, getMeshes: () => [],
        getRigState: () => state,
        onHumanoidControlSelected: key => {
          selected.push(key);
          const role = key.startsWith('right') ? 'right_leg' : 'left_arm';
          state = {...state, ik: {...state.ik,
            activeLimbRole: key === 'head' ? state.ik.activeLimbRole : role,
            selectedHumanoidControlKey: key}};
        },
      });
      controller.refresh(state);
      const head = projectRigPointToClient({point: positions.head,
        camera, canvas});
      const rightFoot = projectRigPointToClient({point: positions.rightFoot,
        camera, canvas});
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 0, clientX: head.x, clientY: head.y,
      }));
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 0, clientX: rightFoot.x, clientY: rightFoot.y,
      }));
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 0, clientX: 20, clientY: 30,
      }));
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 2, clientX: head.x, clientY: head.y,
      }));
      const result = {selected, state, leftDown, rightDown};
      controller.dispose();
      canvas.remove();
      return result;
    }""")
    assert result["selected"] == ["head", "rightFoot"]
    assert result["state"]["ik"]["selectedHumanoidControlKey"] == "rightFoot"
    assert result["leftDown"] == 1
    assert result["rightDown"] == 1


def test_rig_overlay_humanoid_edit_has_priority_and_uses_sticky_clicks(module_page):
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
      canvas.getBoundingClientRect = () => new DOMRect(10, 20, 200, 200);
      document.body.appendChild(canvas);
      const positions = {
        chest: [0, 1.8, 0], pelvis: [0, 1, 0],
        neck: [0, 2.1, 0], head: [0, 2.4, 0],
        leftShoulder: [-.2, 1.6, 0], leftElbow: [-.5, 1.4, 0],
        leftHand: [-.8, 1.2, 0], rightShoulder: [.2, 1.6, 0],
        rightElbow: [.5, 1.4, 0], rightHand: [.8, 1.2, 0],
        leftHip: [-.15, 1, 0], leftKnee: [-.2, .5, 0], leftFoot: [-.2, 0, 0],
        rightHip: [.15, 1, 0], rightKnee: [.2, .5, 0], rightFoot: [.2, 0, 0],
      };
      const controls = Object.fromEntries(Object.entries(positions).map(
        ([key, position]) => [key, {position}]));
      const source = {key: 'model-rig', structureRevision: 1,
        joints: [{jointId: 7, restCenter: [-.2, 1.6, 0],
            restPivot: [-.2, 1.6, 0]},
          {jointId: 0, restCenter: [0, 0, 0], restPivot: [0, 0, 0]}],
        components: [], forestEdges: []};
      let state = {
        selectedJointId: null, jointPickIntent: {type: 'selected-joint'},
        ik: {enabled: true, available: true,
          controlKeys: ['leftShoulder', 'leftElbow', 'leftHand']},
        model: source,
        humanoidRigEdit: {editing: true, saving: false, dirty: false,
          selectedControlKey: null, carryingControlKey: null,
          candidateJointId: null, controls, mappedJointIdByControl: {}},
      };
      const arcball = {
        enabled: true, actions: {0: 'ROTATE', 2: 'PAN'}, calls: [],
        unsetMouseAction: button => {
          arcball.calls.push(['unset', button]);
          delete arcball.actions[button];
        },
        setMouseAction: (action, button) => {
          arcball.calls.push(['set', action, button]);
          arcball.actions[button] = action;
        },
      };
      const events = {began: [], updates: [], finished: 0, cancelled: 0,
        picked: 0, emptyPointerdowns: 0};
      canvas.addEventListener('pointerdown', event => {
        if (event.button === 0) events.emptyPointerdowns += 1;
      });
      const controller = createRigOverlayController({
        scene, camera, canvas, arcballControls: arcball,
        getMeshes: () => [], getRigState: () => state,
        getRigJointPoseFrame: jointId => Number(jointId) === 0
          ? {center: [0, 0, 0], pivot: [0, 0, 0]}
          : Number(jointId) === 7
            ? {center: [-.2, 1.3, 0], pivot: [-.2, 1.2, 0]} : null,
        beginHumanoidControlCarry: key => {
          events.began.push(key);
          state.humanoidRigEdit = {...state.humanoidRigEdit,
            selectedControlKey: key, carryingControlKey: key};
          return true;
        },
        updateHumanoidControlDraft: (key, position) => {
          events.updates.push({key, position});
          state.humanoidRigEdit = {...state.humanoidRigEdit,
            controls: {...state.humanoidRigEdit.controls,
              [key]: {position}}, candidateJointId: 7};
        },
        finishHumanoidControlCarry: () => {
          events.finished += 1;
          state.humanoidRigEdit = {...state.humanoidRigEdit,
            carryingControlKey: null};
        },
        cancelHumanoidControlCarry: () => {
          events.cancelled += 1;
          state.humanoidRigEdit = {...state.humanoidRigEdit,
            carryingControlKey: null};
        },
        onRigJointPicked: () => { events.picked += 1; },
      });
      controller.refresh(state);
      const initial = controller.getDebugState();
      const initialCandidateVisible = controller.group.getObjectByName(
        'viewer-humanoid-candidate-marker')?.visible;
      state = {...state, humanoidRigEdit: {...state.humanoidRigEdit,
        candidateJointId: 0}};
      controller.refresh(state);
      const zeroCandidateVisible = controller.group.getObjectByName(
        'viewer-humanoid-candidate-marker')?.visible;
      state = {...state, humanoidRigEdit: {...state.humanoidRigEdit,
        candidateJointId: null}};
      controller.refresh(state);
      const screen = projectRigPointToClient({point: positions.leftShoulder,
        camera, canvas});
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 0, clientX: 20, clientY: 30,
      }));
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 0, clientX: screen.x, clientY: screen.y,
      }));
      const carrying = controller.getDebugState();
      canvas.dispatchEvent(new PointerEvent('pointermove', {
        clientX: screen.x, clientY: screen.y, buttons: 1,
      }));
      const beforePan = events.updates.length;
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 2, clientX: screen.x, clientY: screen.y, buttons: 2,
      }));
      canvas.dispatchEvent(new PointerEvent('pointermove', {
        clientX: screen.x + 20, clientY: screen.y + 10, buttons: 2,
      }));
      canvas.dispatchEvent(new PointerEvent('pointerup', {
        button: 2, clientX: screen.x + 20, clientY: screen.y + 10,
      }));
      const afterPan = events.updates.length;
      canvas.dispatchEvent(new PointerEvent('pointermove', {
        clientX: screen.x + 10, clientY: screen.y, buttons: 1,
      }));
      const afterPanMove = events.updates.length;
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));
      const cancelled = controller.getDebugState();
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 0, clientX: screen.x, clientY: screen.y,
      }));
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        button: 0, clientX: screen.x, clientY: screen.y,
      }));
      const released = controller.getDebugState();
      const pointSprite = controller.group.getObjectByName(
        'viewer-humanoid-point-leftShoulder');
      const haloSprite = controller.group.getObjectByName(
        'viewer-humanoid-halo-leftShoulder');
      const modelJointMarker = controller.group.getObjectByName(
        'viewer-inferred-rig-model-joint-markers');
      const markerMatrix = new THREE.Matrix4();
      modelJointMarker?.getMatrixAt(0, markerMatrix);
      const markerPosition = new THREE.Vector3()
        .setFromMatrixPosition(markerMatrix).toArray();
      const humanoidChildren = controller.group.getObjectByName(
        'viewer-humanoid-control-rig-overlay')?.children.map(child => child.type);
      controller.dispose();
      return {initial, carrying, cancelled, released, events, arcball: arcball.enabled,
        arcballActions: arcball.actions, arcballCalls: arcball.calls,
        beforePan, afterPan, afterPanMove,
        pointType: pointSprite?.type, haloType: haloSprite?.type,
        initialCandidateVisible, zeroCandidateVisible,
        pointScale: pointSprite?.scale?.x || 0,
        haloScale: haloSprite?.scale?.x || 0, markerPosition, humanoidChildren};
    }""")
    assert result["initial"]["staticVisible"] is True
    assert result["initial"]["humanoidOverlayVisible"] is True
    assert result["initial"]["humanoidHaloCount"] == 16
    assert result["initial"]["humanoidPointSpriteCount"] == 16
    assert result["initial"]["humanoidHaloSpriteCount"] == 16
    assert result["initial"]["humanoidMarkerTextureReady"] is True
    assert result["pointType"] == "Sprite"
    assert result["haloType"] == "Sprite"
    assert "Points" not in result["humanoidChildren"]
    assert result["initialCandidateVisible"] is False
    assert result["zeroCandidateVisible"] is True
    assert result["pointScale"] > 0
    assert result["haloScale"] > result["pointScale"]
    assert result["markerPosition"] == pytest.approx([-.2, 1.2, 0])
    assert result["initial"]["controlsAttached"] is False
    assert result["carrying"]["carryingControlKey"] == "leftShoulder"
    assert result["carrying"]["arcballEnabled"] is True
    assert result["events"]["emptyPointerdowns"] == 1
    assert result["arcballActions"].get("2") == "PAN"
    assert result["arcballActions"].get("0") == "ROTATE"
    assert result["beforePan"] == result["afterPan"]
    assert result["afterPanMove"] > result["afterPan"]
    assert result["cancelled"]["carryingControlKey"] is None
    assert result["events"]["cancelled"] == 1
    assert len(result["events"]["began"]) == 2
    assert result["events"]["began"][0] == "leftShoulder"
    assert result["events"]["updates"]
    assert result["events"]["picked"] == 0
    assert result["released"]["carryingControlKey"] is None
    assert result["events"]["finished"] == 1
    assert result["arcball"] is True


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
    assert result["root"]["selectedJointIndicatorVisible"] is True
    assert result["root"]["selectedJointIndicatorPosition"] == pytest.approx(
        [.5, .5, 0])
    assert result["rootAgain"]["controlsAttached"] is False
    assert result["rootAgain"]["selectedJointIndicatorVisible"] is True
    assert result["nonRoot"]["controlsCreated"] is True
    assert result["nonRoot"]["controlsAttached"] is True
    assert result["nonRoot"]["selectedJointIndicatorVisible"] is True
    assert result["nonRoot"]["helperInScene"] is True
    assert result["nonRoot"]["controlsCreateCount"] == 1
    assert result["cleared"]["proxyVisible"] is False
    assert result["cleared"]["controlsAttached"] is False
    assert result["cleared"]["selectedJointIndicatorVisible"] is False
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
    assert result["picking"]["selectedJointIndicatorVisible"] is False
    assert result["picking"]["arcballEnabled"] is True
    assert result["picked"]["controlsAttached"] is True
    assert result["picked"]["staticVisible"] is False
    assert result["picked"]["selectedJointIndicatorVisible"] is True
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


def test_model_picker_capture_blocks_view_selection_before_bubble_listener(
        module_page):
    page = module_page
    # Selection is initialized before the lazily-created picker. Keep this
    # regression focused on the capture-phase ordering contract.
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


def test_barycentric_third_moment_matches_reference_for_all_index_triples(
        module_page):
    result = module_page.evaluate("""async () => {
      const {barycentricThirdMoment} = await import(
        './js/mesh/weight-rig.js');
      const reference = (first, second, third, area) => {
        const counts = [first, second, third].reduce((map, index) => {
          map.set(index, (map.get(index) || 0) + 1);
          return map;
        }, new Map());
        const multiplicities = [...counts.values()];
        if (multiplicities.length === 1) return area / 10;
        if (multiplicities.length === 2) return area / 30;
        return area / 60;
      };
      const areas = [0, 1, .5, .123456789, 1234.56789, 1e-12];
      const mismatches = [];
      let caseCount = 0;
      for (let first = 0; first < 3; first += 1) {
        for (let second = 0; second < 3; second += 1) {
          for (let third = 0; third < 3; third += 1) {
            for (const area of areas) {
              caseCount += 1;
              const actual = barycentricThirdMoment(
                first, second, third, area);
              const expected = reference(first, second, third, area);
              if (!Object.is(actual, expected)) {
                mismatches.push({first, second, third, area, actual, expected});
              }
            }
          }
        }
      }
      return {caseCount, mismatches};
    }""")
    assert result == {"caseCount": 162, "mismatches": []}


def test_surface_integrators_match_reference_third_moment_order(module_page):
    result = module_page.evaluate("""async () => {
      const {integrateLinearSecondMoment, integratePositionProduct} =
        await import('./js/mesh/weight-rig.js');
      const referenceThirdMoment = (first, second, third, area) => {
        const counts = [first, second, third].reduce((map, index) => {
          map.set(index, (map.get(index) || 0) + 1);
          return map;
        }, new Map());
        const multiplicities = [...counts.values()];
        if (multiplicities.length === 1) return area / 10;
        if (multiplicities.length === 2) return area / 30;
        return area / 60;
      };
      const dot3 = (left, right) => left[0] * right[0]
        + left[1] * right[1] + left[2] * right[2];
      const referenceSecondMoment = (points, weights, area) => {
        let result = 0;
        for (let left = 0; left < 3; left += 1) {
          for (let right = 0; right < 3; right += 1) {
            for (let weight = 0; weight < 3; weight += 1) {
              result += dot3(points[left], points[right])
                * weights[weight]
                * referenceThirdMoment(left, right, weight, area);
            }
          }
        }
        return result;
      };
      const referencePositionProduct = (
          points, leftWeights, rightWeights, area) => {
        const result = [0, 0, 0];
        for (let point = 0; point < 3; point += 1) {
          for (let left = 0; left < 3; left += 1) {
            for (let right = 0; right < 3; right += 1) {
              const contribution = leftWeights[left] * rightWeights[right]
                * referenceThirdMoment(point, left, right, area);
              result[0] += points[point][0] * contribution;
              result[1] += points[point][1] * contribution;
              result[2] += points[point][2] * contribution;
            }
          }
        }
        return result;
      };
      const cases = [
        {
          points: [[0, 0, 0], [2, 0, 0], [0, 2, 0]],
          weights: [.25, .5, .75],
          left: [.2, .3, .5], right: [.8, .1, .4], area: 2,
        },
        {
          points: [[1, -2, .5], [-.25, 3, 1.75], [2.5, .25, -1]],
          weights: [1, 0, 0],
          left: [1, 0, 0], right: [0, 0, 1], area: .123456789,
        },
        {
          points: [[-.7, .2, 1.1], [1.3, -1.4, .6], [2.2, .9, -2.5]],
          weights: [1 / 3, 2 / 3, 1 / 7],
          left: [.25, .5, .25], right: [.6, .2, .2], area: 1e-12,
        },
      ];
      return cases.map(item => {
        const actualSecond = integrateLinearSecondMoment(
          item.points, item.weights, item.area);
        const expectedSecond = referenceSecondMoment(
          item.points, item.weights, item.area);
        const actualPosition = integratePositionProduct(
          item.points, item.left, item.right, item.area);
        const expectedPosition = referencePositionProduct(
          item.points, item.left, item.right, item.area);
        return {
          secondEqual: Object.is(actualSecond, expectedSecond),
          positionEqual: actualPosition.every((value, index) =>
            Object.is(value, expectedPosition[index])),
          actualSecond, expectedSecond, actualPosition, expectedPosition,
        };
      });
    }""")
    assert all(item["secondEqual"] and item["positionEqual"]
               for item in result)
    for item in result:
        assert item["actualSecond"] == item["expectedSecond"]
        assert item["actualPosition"] == item["expectedPosition"]


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


def test_cooperative_surface_evidence_matches_sync_and_supports_cancellation(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const positions = new Float32Array([
        0, 0, 0, 2, 0, 0, 0, 2, 0,
        1, 0, 0, 1, 1, 0, 0, 1, 0,
      ]);
      const triangles = new Uint32Array([
        0, 1, 2, 1, 3, 2,
      ]);
      const indices = new Uint32Array([
        0, 1, 0, 1, 0, 1,
        0, 1, 0, 1, 0, 1,
      ]);
      const weights = new Float32Array([
        0, 1, .5, .5, 1, 0,
        0, 1, .5, .5, 1, 0,
      ]);
      const args = [positions, triangles, indices, weights, 2, [0, 1], 4];
      const synchronous = rig.buildSurfaceInfluenceGraph(...args);
      const cooperative = await rig.buildSurfaceInfluenceGraphCooperative(
        ...args, {triangleBatch: 1, budgetMs: .01});
      let checks = 0;
      const cancelled = await rig.buildSurfaceInfluenceGraphCooperative(
        ...args, {triangleBatch: 1, budgetMs: .01,
          isCurrent: () => checks++ < 2});
      return {
        same: JSON.stringify(cooperative) === JSON.stringify(synchronous),
        cancelled: cancelled === null,
        cooperative,
      };
    }""")
    assert result["same"]
    assert result["cancelled"]
    assert result["cooperative"]["evidenceMode"] == "surface"
    assert result["cooperative"]["validTriangleCount"] == 2


def test_cooperative_topology_and_vertex_graphs_match_sync(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const topologyPositions = new Float32Array([
        0, 0, 0, 1, 0, 0, 0, 1, 0,
        0, 0, 0, 1, 0, 0, 2, 0, 0,
      ]);
      const topologyIndices = new Uint32Array([
        0, 1, 2, 3, 4, 5, 0, 1, 9,
      ]);
      let topologyCheckpoints = 0;
      const topology = rig.inspectSurfaceTopology(
        topologyPositions, topologyIndices);
      const cooperativeTopology = await rig.inspectSurfaceTopologyCooperative(
        topologyPositions, topologyIndices, {
          triangleBatch: 1,
          budget: {checkpoint: async () => { topologyCheckpoints += 1; }},
        });

      const positions = new Float32Array([
        0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0,
      ]);
      const indices = new Uint32Array([
        0, 1, 0, 1, 2, 3, 2, 1, 2, 0, 1, 2,
      ]);
      const weights = new Float32Array([
        .5, .25, .25, .5, .5, 0, .2, .3, .5, 0, -1, .5,
      ]);
      const requested = [2, 0, 1, 99];
      const nodes = rig.buildInfluenceNodes(
        positions, indices, weights, 3, requested);
      let nodeCheckpoints = 0;
      const cooperativeNodes = await rig.buildInfluenceNodesCooperative(
        positions, indices, weights, 3, requested, {
          vertexBatch: 1,
          budget: {checkpoint: async () => { nodeCheckpoints += 1; }},
        });
      const relationships = rig.buildInfluenceRelationships(
        positions, indices, weights, 3, nodes, 3);
      let relationshipCheckpoints = 0;
      const cooperativeRelationships =
        await rig.buildInfluenceRelationshipsCooperative(
          positions, indices, weights, 3, nodes, 3, {
            vertexBatch: 1,
            budget: {checkpoint: async () => {
              relationshipCheckpoints += 1;
            }},
          });
      let nodeChecks = 0;
      const cancelledNodes = await rig.buildInfluenceNodesCooperative(
        positions, indices, weights, 3, requested, {
          vertexBatch: 1,
          isCurrent: () => nodeChecks++ < 2,
          budget: {checkpoint: async () => {}},
        });
      let relationshipChecks = 0;
      const cancelledRelationships =
        await rig.buildInfluenceRelationshipsCooperative(
          positions, indices, weights, 3, nodes, 3, {
            vertexBatch: 1,
            isCurrent: () => relationshipChecks++ < 2,
            budget: {checkpoint: async () => {}},
          });
      return {
        topologySame: JSON.stringify(cooperativeTopology)
          === JSON.stringify(topology),
        nodesSame: JSON.stringify(cooperativeNodes) === JSON.stringify(nodes),
        relationshipsSame: JSON.stringify(cooperativeRelationships)
          === JSON.stringify(relationships),
        topologyCheckpoints, nodeCheckpoints, relationshipCheckpoints,
        cancelledNodes: cancelledNodes === null,
        cancelledRelationships: cancelledRelationships === null,
      };
    }""")
    assert result == {
        "topologySame": True,
        "nodesSame": True,
        "relationshipsSame": True,
        "topologyCheckpoints": 4,
        "nodeCheckpoints": 5,
        "relationshipCheckpoints": 5,
        "cancelledNodes": True,
        "cancelledRelationships": True,
    }


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


def test_surface_eligibility_probe_matches_topology_diagnostics(module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const rig = await import('./js/mesh/weight-rig.js');
      const cases = [
        {
          name: 'indexed',
          positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
          indices: [0, 1, 2],
        },
        {
          name: 'nonIndexed',
          positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        },
        {
          name: 'laterValidTriangle',
          positions: [0, 0, 0, 1, 0, 0, 0, 1, 0, NaN, 0, 0],
          indices: [0, 1, 3, 0, 1, 2],
        },
        {
          name: 'degenerate',
          positions: [0, 0, 0, 1, 0, 0, 2, 0, 0],
          indices: [0, 1, 2],
        },
        {
          name: 'invalidIndex',
          positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
          indices: [0, 1, 9],
        },
        {
          name: 'empty',
          positions: [],
        },
      ];
      return cases.map(item => {
        const positions = new Float32Array(item.positions);
        const indices = item.indices === undefined
          ? null : new Uint32Array(item.indices);
        const diagnostics = rig.inspectSurfaceTopology(positions, indices);
        return {
          name: item.name,
          usable: rig.hasUsableSurfaceTopology(positions, indices),
          diagnosed: diagnostics.surfaceEvidenceAvailable,
          validTriangleCount: diagnostics.validTriangleCount,
        };
      });
    }""")
    assert result == [
        {"name": "indexed", "usable": True, "diagnosed": True,
         "validTriangleCount": 1},
        {"name": "nonIndexed", "usable": True, "diagnosed": True,
         "validTriangleCount": 1},
        {"name": "laterValidTriangle", "usable": True, "diagnosed": True,
         "validTriangleCount": 1},
        {"name": "degenerate", "usable": False, "diagnosed": False,
         "validTriangleCount": 0},
        {"name": "invalidIndex", "usable": False, "diagnosed": False,
         "validTriangleCount": 0},
        {"name": "empty", "usable": False, "diagnosed": False,
         "validTriangleCount": 0},
    ]


@pytest.mark.parametrize("cooperative", [False, True])
def test_rig_source_session_deduplicates_exact_evidence_and_falls_back_source_wide(
        module_page, cooperative):
    page = module_page
    result = page.evaluate("""async cooperative => {
      const {createRigSourceSession} = await import(
        './js/mesh/rig-model-session.js');
      const states = new Map();
      const knownMeshes = new Set();
      const sourceSkinningRigs = new Map();
      const graphCalls = [];
      const makeMesh = (name, valid, drawStart = 0) => {
        const mesh = {
          userData: {semanticKey: name, identity: {
            draw: {count: 3, start: drawStart, base: 0},
            geometry_state: {ib_file: 'indices.buf', index_size: 2,
              position_file: 'positions.buf', position_stride: 12,
              texcoord_file: 'uv.buf', texcoord_stride: 8},
          }},
          geometry: {
            attributes: {position: {count: 3}},
            index: {array: new Uint32Array([0, 1, 2]), count: 3},
          },
        };
        states.set(mesh, {
          loaded: true, skinningSourceKey: 'source', influenceCount: 1,
          encoding: 'compact',
          baselinePositions: new Float32Array(valid
            ? [0, 0, 0, 1, 0, 0, 0, 1, 0]
            : [0, 0, 0, 1, 0, 0, 2, 0, 0]),
          indices: new Uint32Array([0, 1, 2]),
          weights: new Float32Array([1, 1, 1]),
          boneIds: new Uint16Array([1, 1, 1]),
        });
        knownMeshes.add(mesh);
        return mesh;
      };
      const first = makeMesh('first', true);
      const duplicate = makeMesh('duplicate', true, 2);
      // Provenance may differ even when the retained Rig evidence is equal.
      duplicate.userData.identity.geometry_state.ib_file = 'other-indices.buf';
      const invalid = makeMesh('invalid', false, 1);
      const graphFor = (mesh, state, evidenceMode, surfaceEvidence) => {
        graphCalls.push({mesh: mesh.userData.semanticKey, evidenceMode,
          surfaceAvailable: surfaceEvidence?.surfaceEvidenceAvailable ?? null});
        return {
          nodes: [{boneId: 1, totalWeight: 1, affectedVertexCount: 3,
            affectedMeasure: evidenceMode === 'surface' ? 1 : null,
            maxVertexWeight: 1, weightedCenter: [0, 0, 0],
            weightedRadius: 0}], relationships: [], evidenceMode,
          triangleCount: evidenceMode === 'surface' ? 1 : 0,
          validTriangleCount: evidenceMode === 'surface' ? 1 : 0,
        };
      };
      const session = createRigSourceSession({
        states, knownMeshes, modelWeightState: {
          sourceDescriptors: new Map([['source', {
            sourceFile: 'weights.buf', boneIdOffset: 0,
            boneIdsModelWide: true,
          }]]),
        }, sourceSkinningRigs,
        ensureRigMeshPrepared: () => true,
        ensureInfluenceGraph: graphFor,
        rebuildRestFrames: () => {},
        cloneForest: forest => forest,
      });
      const build = () => cooperative
        ? session.buildAllCooperative() : session.buildAll();
      sourceSkinningRigs.set('removed-source', {});
      const [fallbackRig] = await build();
      const fallback = {
        memberCount: fallbackRig.influenceGraph.memberCount,
        uniqueMemberCount: fallbackRig.influenceGraph.uniqueMemberCount,
        evidenceMode: fallbackRig.influenceGraph.evidenceMode,
        boneIdsModelWide: fallbackRig.boneIdsModelWide,
        graphCalls: [...graphCalls],
      };
      graphCalls.length = 0;
      knownMeshes.delete(invalid);
      const [surfaceRig] = await build();
      return {
        fallback,
        surface: {
          memberCount: surfaceRig.influenceGraph.memberCount,
          uniqueMemberCount: surfaceRig.influenceGraph.uniqueMemberCount,
          evidenceMode: surfaceRig.influenceGraph.evidenceMode,
          graphCalls,
        },
        exactOutput: {
          nodes: surfaceRig.influenceGraph.nodes,
          relationships: surfaceRig.influenceGraph.relationships,
        },
      };
    }""", cooperative)
    assert result["fallback"] == {
        "memberCount": 3,
        "uniqueMemberCount": 2,
        "evidenceMode": "vertex",
        "boneIdsModelWide": True,
        "graphCalls": [
            {"mesh": "first", "evidenceMode": "vertex",
             "surfaceAvailable": True},
            {"mesh": "invalid", "evidenceMode": "vertex",
             "surfaceAvailable": False},
        ],
    }
    assert result["surface"] == {
        "memberCount": 2,
        "uniqueMemberCount": 1,
        "evidenceMode": "surface",
        "graphCalls": [{"mesh": "first", "evidenceMode": "surface",
                         "surfaceAvailable": True}],
    }
    assert result["exactOutput"] == {
        "nodes": [{"boneId": 1, "totalWeight": 1,
                    "affectedVertexCount": 3, "affectedMeasure": 1,
                    "maxVertexWeight": 1,
                    "weightedCenter": [0, 0, 0], "weightedRadius": 0}],
        "relationships": [],
    }


def test_cooperative_rig_source_session_cancels_during_member_deduplication(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {createRigSourceSession} = await import(
        './js/mesh/rig-model-session.js');
      const states = new Map();
      const knownMeshes = new Set();
      const sourceSkinningRigs = new Map();
      let cancelled = false;
      const largeValues = () => new Array(4096).fill(1);
      const makeMesh = (name, boneIds = largeValues()) => {
        const mesh = {
          userData: {semanticKey: name},
          geometry: {index: {array: largeValues()}},
        };
        states.set(mesh, {
          loaded: true, skinningSourceKey: 'source', influenceCount: 1,
          baselinePositions: new Array(4096 * 3).fill(0),
          indices: largeValues(), weights: largeValues(), boneIds,
        });
        knownMeshes.add(mesh);
        return mesh;
      };
      const duplicateBoneIds = new Proxy(largeValues(), {
        get(target, property, receiver) {
          if (property === '0') cancelled = true;
          return Reflect.get(target, property, receiver);
        },
      });
      makeMesh('first');
      makeMesh('duplicate', duplicateBoneIds);
      const session = createRigSourceSession({
        states, knownMeshes, sourceSkinningRigs,
        modelWeightState: {
          sourceDescriptors: new Map([['source', {
            sourceFile: 'weights.buf', boneIdOffset: 0,
            boneIdsModelWide: true,
          }]]),
        },
        ensureRigMeshPrepared: () => true,
        ensureInfluenceGraph: () => ({nodes: [], relationships: []}),
        rebuildRestFrames: () => {},
        cloneForest: forest => forest,
      });
      const result = await session.buildAllCooperative({
        generation: 1, isCurrent: () => !cancelled,
      });
      return {
        cancelled,
        returnedNull: result === null,
        sourceRigInstalled: sourceSkinningRigs.has('source'),
      };
    }""")
    assert result == {
        "cancelled": True,
        "returnedNull": True,
        "sourceRigInstalled": False,
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const result = await buildModelRigReconciliationCooperative([body, legs, far]);
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
          edge.relationshipType === 'source')
          .map(edge => edge.sourceSupportCount),
        identityMode: result.reconciliation.identityMode,
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
    assert result["identityMode"] == "geometric-reconciliation"


def test_model_wide_bone_ids_build_dense_model_rig_and_skip_matching(
        module_page):
    result = module_page.evaluate("""async () => {
      const {buildModelRigReconciliationCooperative} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const makeRig = (sourceKey, boneIds, links = [], modelWide = true,
          guardModelWide = false) => {
        const parentById = Object.fromEntries(boneIds.map(id => [id, null]));
        const childrenById = Object.fromEntries(boneIds.map(id => [id, []]));
        links.forEach(([parent, child]) => {
          parentById[child] = parent;
          childrenById[parent].push(child);
        });
        const roots = boneIds.filter(id => parentById[id] === null);
        const componentByBoneId = Object.fromEntries(boneIds.map(id => {
          let root = id;
          while (parentById[root] !== null) root = parentById[root];
          return [id, roots.indexOf(root)];
        }));
        const component = rootId => ({componentId: roots.indexOf(rootId), rootId,
          nodeIds: boneIds.filter(id => {
            let current = id;
            while (parentById[current] !== null) current = parentById[current];
            return current === rootId;
          }), parentById, childrenById,
          depthById: Object.fromEntries(boneIds.map(id => [id, 0])),
          edges: links.map(([parent, child, score = 1]) => ({
            boneA: parent, boneB: child, treeEdgeScore: score,
          }))});
        const rig = {
          sourceKey, boneIds, boneIdsModelWide: modelWide,
          influenceGraph: {
            nodes: boneIds.map((boneId, index) => ({boneId,
              weightedCenter: [index, 0, 0], weightedRadius: .1,
              totalWeight: 1, affectedVertexCount: 10})),
            relationships: links.map(([boneA, boneB, score = 1]) => ({
              boneA, boneB, treeEdgeScore: score,
              jointCenter: [(boneA + boneB) / 2, 0, 0],
            })),
          },
          centerByBoneId: new Map(boneIds.map((boneId, index) =>
            [boneId, [index, 0, 0]])),
          jointPivotByBoneId: new Map(boneIds
            .filter(id => parentById[id] !== null)
            .map(id => [id, [id, 0, 0]])),
          restDirectionByBoneId: new Map(boneIds.map(id =>
            [id, [1, 0, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(boneIds.map(id => [id, {
            directionSource: 'child-weighted-center'}])),
          inferredForest: {
            components: roots.map(component),
            componentByBoneId,
          },
        };
        if (guardModelWide) Object.defineProperty(rig, 'vertexEvidence', {
          get() { throw new Error('model-wide mode must not build samples'); },
        });
        return rig;
      };
      const rigs = [
        makeRig('a', [1, 5, 10], [[1, 5, .9], [5, 10, .8]], true, true),
        makeRig('b', [1, 5, 20], [[1, 5, .7], [5, 20, .6]], true, true),
        makeRig('c', [1, 30], [[1, 30, .5]], true, true),
      ];
      const timings = {};
      const model = await buildModelRigReconciliationCooperative(rigs, {}, {
        timings,
      });
      const snapshot = value => ({
        jointIds: value.joints.map(joint => joint.jointId),
        members: value.joints.map(joint => joint.members.map(member =>
          member.sourceBoneKey)),
        edges: value.edges.map(edge => [edge.jointA, edge.jointB,
          edge.relationshipType]),
        components: value.components.map(component => ({
          rootId: component.rootId, nodeIds: component.nodeIds,
          parentById: component.parentById, childrenById: component.childrenById,
        })),
        sourceMap: value.sourceBoneToModelJointId,
      });
      const samePositionA = makeRig('same-a', [10]);
      const samePositionB = makeRig('same-b', [11]);
      samePositionB.centerByBoneId.set(11, [0, 0, 0]);
      samePositionB.influenceGraph.nodes[0].weightedCenter = [0, 0, 0];
      const distinct = await buildModelRigReconciliationCooperative([
        samePositionA, samePositionB]);
      const sparse = await buildModelRigReconciliationCooperative([
        makeRig('sparse', [0, 4, 57, 376])]);
      const mixed = await buildModelRigReconciliationCooperative([
        makeRig('mixed-wide', [1], [], true),
        makeRig('mixed-local', [1], [], false),
      ]);
      return {
        snapshot: snapshot(model),
        identityMode: model.reconciliation.identityMode,
        uniqueModelBoneIdCount: model.reconciliation.uniqueModelBoneIdCount,
        candidateCount: model.reconciliation.candidateCount,
        skippedTimings: [timings.sampleBuildMs, timings.spatialIndexMs,
          timings.crossSourceMatchMs],
        samePositionCount: distinct.joints.length,
        samePositionIds: Object.values(distinct.sourceBoneToModelJointId),
        sparseIds: sparse.joints.map(joint => joint.jointId),
        sparseCount: sparse.joints.length,
        mixedIdentityMode: mixed.reconciliation.identityMode,
      };
    }""")
    assert result["identityMode"] == "model-wide-bone-id"
    assert result["uniqueModelBoneIdCount"] == 5
    assert result["candidateCount"] == 0
    assert result["skippedTimings"] == [0, 0, 0]
    assert result["snapshot"]["jointIds"] == [0, 1, 2, 3, 4]
    assert result["snapshot"]["members"] == [
        ["a#bone=1", "b#bone=1", "c#bone=1"],
        ["a#bone=5", "b#bone=5"],
        ["a#bone=10"], ["b#bone=20"], ["c#bone=30"],
    ]
    assert result["snapshot"]["sourceMap"] == {
        "a#bone=1": 0, "b#bone=1": 0, "c#bone=1": 0,
        "a#bone=5": 1, "b#bone=5": 1,
        "a#bone=10": 2, "b#bone=20": 3, "c#bone=30": 4,
    }
    assert result["samePositionCount"] == 2
    assert len(set(result["samePositionIds"])) == 2
    assert result["sparseIds"] == [0, 1, 2, 3]
    assert result["sparseCount"] == 4
    assert result["mixedIdentityMode"] == "geometric-reconciliation"


def test_cross_source_neutral_sampling_uses_radius_and_true_mutual_nearest(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {crossSourceWeightEvidenceCooperative} = await import(
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
      const spatial = await crossSourceWeightEvidenceCooperative(
        make('left', [.0099, 0, 0], [0]),
        make('right', [.0201, 0, 0], [1]), 1);
      const mutual = await crossSourceWeightEvidenceCooperative(
        make('mutual-left', [0, 0, 0, 0, .018, 0], [10, 11]),
        make('mutual-right', [-.018, 0, 0, 0, .0095, 0], [20, 21]), 1);
      let checks = 0;
      const cancelled = await crossSourceWeightEvidenceCooperative(
        make('cancel-left', [0, 0, 0, 0, .018, 0], [10, 11]),
        make('cancel-right', [-.018, 0, 0, 0, .0095, 0], [20, 21]), 1,
        null, null, null, null, {
          isCurrent: () => checks++ < 1,
          budget: {checkpoint: async () => {}},
        });
      return {
        spatialMatches: spatial.get('left#bone=0|right#bone=1')
          ?.matchedVertexCount || 0,
        mutualPairs: [...mutual.values()].map(item => [
          item.leftSourceBoneKey, item.rightSourceBoneKey,
        ]).sort(),
        mutualStrength: [...mutual.values()].map(item => [
          item.matchedVertexCount, item.weightedMatchStrength,
        ]),
        cancelled: cancelled === null,
      };
    }""")
    assert result["spatialMatches"] == 1
    assert result["mutualPairs"] == [[
        "mutual-left#bone=11", "mutual-right#bone=21"]]
    assert result["mutualStrength"] == [[1, pytest.approx(.575)]]
    assert result["cancelled"]


def test_model_rig_reconciliation_ignores_main_rig_metadata(module_page):
    result = module_page.evaluate("""async () => {
      const {buildModelRigReconciliationCooperative} = await import(
        './js/mesh/weight-rig-reconcile.js');
      const makeRig = mainRigMetadata => {
        const nodes = [0, 1, 2].map(boneId => ({
          boneId, weightedCenter: [0, boneId, 0], weightedRadius: .1,
          totalWeight: 1, affectedVertexCount: 10,
        }));
        const parentById = {0: null, 1: 0, 2: 1};
        const childrenById = {0: [1], 1: [2], 2: []};
        const edges = [
          {boneA: 0, boneB: 1, treeEdgeScore: .9, jointCenter: [0, .5, 0]},
          {boneA: 1, boneB: 2, treeEdgeScore: .8, jointCenter: [0, 1.5, 0]},
        ];
        return {
          sourceKey: 'body', boneIds: [0, 1, 2], mainRigMetadata,
          influenceGraph: {nodes, relationships: edges},
          centerByBoneId: new Map(nodes.map(node =>
            [node.boneId, node.weightedCenter])),
          jointPivotByBoneId: new Map([[1, [0, .5, 0]], [2, [0, 1.5, 0]]]),
          restDirectionByBoneId: new Map(nodes.map(node =>
            [node.boneId, [0, 1, 0]])),
          restFrameByBoneId: new Map(),
          restFrameEvidenceByBoneId: new Map(nodes.map(node =>
            [node.boneId, {directionSource: 'child-weighted-center'}])),
          inferredForest: {
            components: [{componentId: 0, rootId: 0, nodeIds: [0, 1, 2],
              parentById, childrenById, depthById: {0: 0, 1: 1, 2: 2}, edges}],
            componentByBoneId: {0: 0, 1: 0, 2: 0},
            edges,
          },
        };
      };
      const summarize = rig => ({
        joints: rig.joints.map(joint => ({
          jointId: joint.jointId, signature: joint.signature,
          members: joint.members.map(member => member.sourceBoneKey).sort(),
          restPivot: joint.restPivot, restFrame: joint.restFrame,
        })),
        sourceBoneToModelJointId: rig.sourceBoneToModelJointId,
        edges: rig.edges,
        components: rig.components,
      });
      const withoutMetadata = summarize(await buildModelRigReconciliationCooperative([
        makeRig(null)]));
      const withMetadata = summarize(await buildModelRigReconciliationCooperative([
        makeRig({version: 1, controls: {
          leftShoulder: {semantic: {sideN: -.4, height01: .8, depthN: .2},
            joint_signature: '[\"old#bone=99\"]'},
        }}),
      ]));
      return {
        equal: JSON.stringify(withoutMetadata) === JSON.stringify(withMetadata),
        withoutMetadata, withMetadata,
      };
    }""")
    assert result["equal"]
    assert result["withoutMetadata"] == result["withMetadata"]


def test_cross_source_reconciliation_preserves_host_root_and_reroots_accessory(
        module_page):
    page = module_page
    result = page.evaluate("""async () => {
      const {buildModelRigReconciliationCooperative} = await import(
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
      const result = await buildModelRigReconciliationCooperative([body, wing]);
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const first = await buildModelRigReconciliationCooperative([main, partial, accessory]);
      const second = await buildModelRigReconciliationCooperative([accessory, partial, main]);
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const first = await buildModelRigReconciliationCooperative(
        [host, upper, lower], {modelReferenceRadius: 1});
      const second = await buildModelRigReconciliationCooperative(
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const first = await buildModelRigReconciliationCooperative(
        [host, accessoryA, accessoryB], {modelReferenceRadius: 1});
      const second = await buildModelRigReconciliationCooperative(
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const first = await buildModelRigReconciliationCooperative([
        make('alpha', [[0, [0, 0, 0]], [1, [0, .05, 0]]], 0),
        make('zeta', [[10, [0, .1, 0]], [11, [0, .15, 0]]], 10),
      ], {modelReferenceRadius: 1});
      const second = await buildModelRigReconciliationCooperative([
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const moderate = await buildModelRigReconciliationCooperative([
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
      const strongDistance = await buildModelRigReconciliationCooperative([
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
      const strongestEvidenceWins = await buildModelRigReconciliationCooperative([
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
      const oneVertex = await buildModelRigReconciliationCooperative([
        make('single-a', [[0, [0, 0, 0]]], [
          oneInfluence('neutral', [[0, 0, 0]], [0]),
        ]),
        make('single-b', [[1, [.052, 0, 0]]], [
          oneInfluence('neutral', [[0, 0, 0]], [1]),
        ]),
      ], {modelReferenceRadius: 1});
      const threeSources = await buildModelRigReconciliationCooperative([
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const twoAnchors = await buildModelRigReconciliationCooperative(
        [chainA, reversedChain], {modelReferenceRadius: 1});
      const centerA = make('center-a', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
      ], [[0, 1], [1, 2]]);
      const centerB = make('center-b', [
        [10, [0, 0, 0]], [11, [.09, 1, 0]],
      ], [[10, 11]]);
      const oneAnchor = await buildModelRigReconciliationCooperative(
        [centerA, centerB], {modelReferenceRadius: 1});
      const pathA = make('path-a', [
        [0, [0, 0, 0]], [1, [0, 1, 0]], [2, [0, 2, 0]],
        [3, [0, 3, 0]],
      ], [[0, 1], [1, 2], [2, 3]]);
      const pathB = make('path-b', [
        [10, [0, 0, 0]], [11, [.09, 1, 0]], [12, [.09, 2, 0]],
        [13, [0, 3, 0]], [14, [.09, 1, 0]], [15, [.09, 2, 0]],
      ], [[10, 11], [11, 12], [12, 13], [10, 14], [13, 15]]);
      const pathAligned = await buildModelRigReconciliationCooperative(
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const both = await buildModelRigReconciliationCooperative([left, rightBoth],
        {modelReferenceRadius: 1});
      const one = await buildModelRigReconciliationCooperative([left, rightOne],
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const result = await buildModelRigReconciliationCooperative([rig],
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
      const {buildModelRigReconciliationCooperative} = await import(
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
      const value = await buildModelRigReconciliationCooperative([target, wings], {
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


def test_model_physics_session_suspension_pauses_without_resetting_settings(
        module_page):
    result = module_page.evaluate("""async () => {
      const {createModelPhysicsSession} = await import(
        './js/mesh/model-physics-session.js');
      const callbacks = [];
      const canceled = [];
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
      });
      let steps = 0;
      let motions = 0;
      let resets = 0;
      const transform = {
        orientation: [0, 0, 0, 1], translation: [0, 0, 0],
      };
      session.enable(transform);
      session.attach({
        mesh: {},
        onModelMotion: () => { motions += 1; return true; },
        step: () => { steps += 1; },
        reset: () => { resets += 1; },
        isSettled: () => false,
        isVisible: () => false,
      });
      callbacks.shift()(0);
      session.setSettings({frequencyHz: 7});
      session.setSuspended(true);
      const suspended = session.getState();
      session.handleModelTransform({modelTransform: {
        orientation: [0, .2, 0, .98], translation: [.1, 0, 0],
      }});
      session.advance(1000);
      const paused = session.getState();
      session.reset(transform);
      session.setSuspended(false);
      const resumed = session.getState();
      callbacks.shift()(1000);
      callbacks.shift()(2000);
      return {suspended, paused, resumed, final: session.getState(),
        canceled: canceled.length, steps, motions, resets};
    }""")
    assert result["suspended"]["suspended"] is True
    assert result["suspended"]["frequencyHz"] == 7
    assert result["paused"]["scheduled"] is False
    assert result["resumed"]["suspended"] is False
    assert result["resumed"]["frequencyHz"] == 7
    assert result["final"]["scheduled"] is True
    assert result["steps"] == 6
    assert result["motions"] == 0
    assert result["resets"] == 1
    assert result["canceled"] >= 1


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
            "chest", "pelvis", "neck", "head", "leftShoulder", "leftElbow", "leftHand",
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
        "head": pytest.approx(result["first"]["diagnostics"]["templatePoints"]["head"]),
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


def test_proportional_humanoid_template_uses_exact_ratios_and_midpoints(
        module_page):
    axes = {"up": [0, 1, 0], "right": [1, 0, 0], "forward": [0, 0, 1]}

    def build(height):
        return _call_module(
            module_page, "./js/mesh/humanoid-proportional-template.js",
            "buildProportionalHumanoidRig", {
                "characterHeight": height,
                "leftFoot": [-.1 * height / 2, 0, -.02],
                "rightFoot": [.1 * height / 2, 0, .03],
                "semanticAxes": axes,
            })

    rig, small, large = (build(height) for height in (2, .2, 20))
    assert rig["detectedLeftFoot"][1] == pytest.approx(0)
    assert rig["detectedRightFoot"][1] == pytest.approx(0)
    assert rig["leftFoot"][1] == pytest.approx(.03)
    assert rig["rightFoot"][1] == pytest.approx(.03)
    assert rig["leftHip"][0] == pytest.approx(rig["leftFoot"][0])
    assert rig["rightHip"][2] == pytest.approx(rig["rightFoot"][2])
    assert rig["leftKnee"] == pytest.approx([
        left + (foot - left) * .40
        for left, foot in zip(rig["leftHip"], rig["leftFoot"])])
    assert rig["rightKnee"] == pytest.approx([
        right + (foot - right) * .40
        for right, foot in zip(rig["rightHip"], rig["rightFoot"])])
    assert rig["pelvis"] == pytest.approx([
        (left + right) / 2
        for left, right in zip(rig["leftHip"], rig["rightHip"])])
    assert rig["neck"][1] == pytest.approx(1.6)
    assert math.dist(rig["rightShoulder"], rig["rightHand"]) == pytest.approx(.66)
    assert math.dist(rig["neck"], rig["rightShoulder"]) == pytest.approx(.11)
    assert math.dist(rig["rightHip"], rig["rightFoot"]) == pytest.approx(1.03)
    assert math.degrees(math.atan2(
        abs(rig["rightHand"][1] - rig["rightShoulder"][1]),
        abs(rig["rightHand"][0] - rig["rightShoulder"][0]))) == pytest.approx(55)
    assert small["proportions"] == large["proportions"]
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
    assert result["soles"]["depthN"] == pytest.approx(.01)
    assert result["soles"]["support"] == 3
    assert result["soles"]["validSliceCount"] == 2
    assert result["soles"]["rejectedSliceCount"] == 0
    assert result["soles"]["spread"] == pytest.approx(.02)
    assert result["soles"]["diagnostics"] == {
        "method": "bottom_foot_band",
        "heightRange": [0, .02],
        "sideMinimum": .025,
        "fromBackFraction": .30,
    }
    assert result["soles"]["sliceCenters"][0] == {
        "side": "left", "heightRange": [0, .02], "height01": .01,
        "backDepth": pytest.approx(-.03), "frontDepth": pytest.approx(.07),
        "center": pytest.approx(0), "thickness": pytest.approx(.1),
        "support": 3,
    }
    assert result["soles"]["sliceCenters"][1]["center"] == pytest.approx(.02)
    assert result["decorated"]["depthN"] == pytest.approx(.01)
    assert result["dense"]["depthN"] == pytest.approx(result["soles"]["depthN"])
    assert result["fallback"]["depthN"] == 0
    assert result["fallback"]["support"] == 0
    assert result["fallback"]["validSliceCount"] == 0
    assert result["fallback"]["rejectedSliceCount"] == 2
    assert result["fallback"]["fallbackUsed"] is True
    assert result["fallback"]["reason"] == "foot_depth_unavailable"


def test_humanoid_overrides_resolve_semantics_and_manual_binding(module_page):
    result = module_page.evaluate("""async () => {
      const control = await import('./js/mesh/humanoid-control-rig.js');
      const binding = await import('./js/mesh/humanoid-rig-binding.js');
      const keys = control.HUMANOID_CONTROL_KEYS;
      const controls = Object.fromEntries(keys.map((key, index) => [key, {
        position: [index * .1, index * .2, 0],
        semantic: {sideN: index / 10, height01: index / 20, depthN: 0},
      }]));
      const automatic = {version: 1, available: true, accepted: true,
        frame: {up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1],
          lowHeight: 0, highHeight: 10, height: 10,
          sideCenter: 0, depthCenter: 0}, controls, paths: {}};
      const model = {joints: [
        {jointId: 17, signature: '["body#bone=7"]', restPivot: [2, 3, 4],
          members: [{sourceKey: 'body', boneId: 7,
            sourceBoneKey: 'body#bone=7'}]},
        {jointId: 18, signature: '["body#bone=8"]', restPivot: [0, 5, 0],
          members: [{sourceKey: 'body', boneId: 8,
            sourceBoneKey: 'body#bone=8'}]},
        {jointId: 19, signature: '["body#bone=9"]', restPivot: [0, 6, 0],
          members: [{sourceKey: 'body', boneId: 9,
            sourceBoneKey: 'body#bone=9'}]},
      ], sourceBoneToModelJointId: new Map([
        ['body#bone=7', 17], ['body#bone=8', 18], ['body#bone=9', 19],
      ])};
      const overrides = {version: 2, model_rig_builder_version: 1, controls: {
        leftShoulder: {semantic: {sideN: -.2, height01: .7, depthN: .1},
          joint_id: 17},
        neck: {semantic: {sideN: 0, height01: .8, depthN: 0},
          joint_id: 18},
        head: {semantic: {sideN: 0, height01: .9, depthN: 0},
          joint_id: 19},
      }};
      const resolved = control.resolveHumanoidControlMappings({
        savedOverrides: overrides, modelRig: model});
      const applied = control.applyHumanoidControlRigOverrides({
        automaticRig: automatic, savedOverrides: overrides,
        modelRig: model, resolvedMappings: resolved});
      const rigBinding = binding.buildHumanoidRigBinding({
        controlRig: applied, modelRig: model, controlMappings: resolved});
      const untouched = control.applyHumanoidControlRigOverrides({
        automaticRig: automatic, savedOverrides: null, modelRig: model});
      const entry = rigBinding.jointBindings.get(17);
      const neckEntry = rigBinding.jointBindings.get(18);
      const headEntry = rigBinding.jointBindings.get(19);
      return {
        untouched: keys.every(key => JSON.stringify(
          untouched.controls[key].position) === JSON.stringify(controls[key].position)),
        resolvedJoint: resolved.get('leftShoulder')?.jointId || null,
        exactPivot: applied.controls.leftShoulder.position,
         bindingDriver: entry?.driverId,
        bindingMethod: entry?.bindingMethod,
        controlKey: entry?.controlKey,
        neckBinding: {driverId: neckEntry?.driverId,
          bindingMethod: neckEntry?.bindingMethod,
          controlKey: neckEntry?.controlKey},
        headBinding: {driverId: headEntry?.driverId,
          bindingMethod: headEntry?.bindingMethod,
          controlKey: headEntry?.controlKey},
      };
    }""")
    assert result["untouched"] is True
    assert result["resolvedJoint"] == 17
    assert result["exactPivot"] == [2, 3, 4]
    assert result["bindingDriver"] == "left_upper_arm"
    assert result["bindingMethod"] == "manual_control_mapping"
    assert result["controlKey"] == "leftShoulder"
    assert result["neckBinding"] == {
        "driverId": "neck", "bindingMethod": "manual_control_mapping",
        "controlKey": "neck"}
    assert result["headBinding"] == {
        "driverId": "head", "bindingMethod": "manual_control_mapping",
        "controlKey": "head"}


def test_model_rig_sidecar_serialization_and_hydration(module_page):
    result = module_page.evaluate("""async () => {
      const persistence = await import('./js/mesh/model-rig-persistence.js');
      const frames = await import('./js/mesh/weight-rig-frames.js');
      const sourceRigs = [{
        sourceKey: 'body|offset=0', sourceFile: 'Body/Body.buf',
        boneIdOffset: 0,
      }, {
        sourceKey: 'cloth|offset=2', sourceFile: 'Cloth/Cloth.buf',
        boneIdOffset: 2,
      }];
      const member = (sourceKey, boneId) => ({sourceKey,
        sourceBoneKey: `${sourceKey}#bone=${boneId}`, boneId});
      const joint = (jointId, parentId, members, representativeMember,
          restCenter) => ({jointId, parentId, members, representativeMember,
        restCenter, restPivot: [...restCenter],
        restFrame: [0, 0, 0, 1], restDirection: [0, 1, 0]});
      const joints = [
        joint(0, null, [member('body|offset=0', 7),
          member('cloth|offset=2', 70)], member('cloth|offset=2', 70),
        [0, 0, 0]),
        joint(1, 0, [member('body|offset=0', 8)],
        member('body|offset=0', 8), [0, 1, 0]),
        joint(2, 0, [member('cloth|offset=2', 80)],
        member('cloth|offset=2', 80), [1, 1, 0]),
        joint(3, 1, [member('cloth|offset=2', 81)],
        member('cloth|offset=2', 81), [0, 2, 0]),
      ];
      joints[0].childrenIds = [1, 2];
      joints[1].childrenIds = [3];
      joints[2].childrenIds = [];
      joints[3].childrenIds = [];
      const edges = [
        {jointA: 0, jointB: 1, relationshipType: 'source',
          treeEdgeScore: .7, jointCenter: [0, .5, 0]},
        {jointA: 0, jointB: 2, relationshipType: 'source',
          treeEdgeScore: .2, jointCenter: [.5, .5, 0]},
        {jointA: 1, jointB: 3, relationshipType: 'attachment',
          attachmentScore: .9, jointCenter: [0, 1.5, 0]},
      ];
      const sourceBoneToModelJointMap = new Map([
        ['body|offset=0#bone=7', 0], ['cloth|offset=2#bone=70', 0],
        ['body|offset=0#bone=8', 1], ['cloth|offset=2#bone=80', 2],
        ['cloth|offset=2#bone=81', 3],
      ]);
      const components = [{componentId: 0, rootId: 0, nodeIds: [0, 1, 2, 3],
        parentById: {0: null, 1: 0, 2: 0, 3: 1},
        childrenById: {0: [1, 2], 1: [3], 2: [], 3: []}, edges}];
      const rig = {
        sourceRigs, modelReferenceRadius: 2,
        joints, edges, components,
        childrenById: new Map([[0, [1, 2]], [1, [3]], [2, []], [3, []]]),
        centerByJointId: new Map(joints.map(joint =>
          [joint.jointId, [...joint.restCenter]])),
        jointPivotByJointId: new Map(joints.map(joint =>
          [joint.jointId, [...joint.restPivot]])),
        sourceBoneToModelJointId: sourceBoneToModelJointMap,
        sourceBoneToModelJointMap,
      };
      const forest = {components};
      frames.rebuildModelRestFrames(rig, forest);
      const saved = persistence.serializeModelRig(rig, {sourceRigs});
      const hydrated = persistence.hydrateModelRig(saved, sourceRigs);
      frames.rebuildModelRestFrames(hydrated, hydrated);
      const mapValues = map => [...(map || new Map()).entries()].map(
        ([id, value]) => [typeof id === 'string' && id.includes(':')
          ? id : Number(id), value?.toArray ? value.toArray()
          : Array.isArray(value) ? value
          : value === null || value === undefined ? value : Number(value)]);
      const state = value => ({
        joints: value.joints.map(item => [item.jointId, item.parentId,
          item.childrenIds]),
        representatives: value.joints.map(item =>
          item.representativeMember?.sourceBoneKey || null),
        edgeTypes: value.edges.map(edge => [edge.jointA, edge.jointB,
          edge.relationshipType]),
        jointPivots: mapValues(value.jointPivotByJointId),
        edgePivots: mapValues(value.jointPivotByEdgeKey),
        restFrames: mapValues(value.restFrameByJointId),
        restDirections: mapValues(value.restDirectionByJointId),
        continuations: mapValues(value.restContinuationChildByJointId),
        sourceMap: [...value.sourceBoneToModelJointId].sort(),
      });
      let stored = null;
      let buildCount = 0;
      const loadOrBuild = () => persistence.loadOrBuildModelRig({
        load: async () => stored,
        hydrate: value => persistence.hydrateModelRig(value, sourceRigs),
        build: async () => {
          buildCount += 1;
          stored = saved;
          return stored;
        },
      });
      const firstLoad = await loadOrBuild();
      const secondLoad = await loadOrBuild();
      return {
        version: saved.version,
        fields: Object.keys(saved).sort(),
        sourceTable: saved.source_table,
        compactMembers: saved.joints[0].members,
        representativeMemberIndex: saved.joints[0].representative_member_index,
        edgeStrengths: saved.edges.map(edge => edge.edge_strength),
        edgePivots: saved.edges.map(edge => edge.edge_pivot),
        equivalent: JSON.stringify(state(rig)) === JSON.stringify(state(hydrated)),
        fresh: state(rig), cached: state(hydrated),
        loadLifecycle: {
          firstFromCache: firstLoad.hydratedFromCache,
          secondFromCache: secondLoad.hydratedFromCache,
          buildCount,
          sameIds: secondLoad.modelRig.joints.map(joint => joint.jointId),
        },
      };
    }""")
    assert result["version"] == 1
    assert result["fields"] == ["builder_version", "edges",
                                 "joints", "model_reference_radius",
                                 "source_table", "version"]
    assert result["sourceTable"] == ["body|offset=0", "cloth|offset=2"]
    assert result["compactMembers"] == [[0, 7], [1, 70]]
    assert result["representativeMemberIndex"] == 1
    assert result["edgeStrengths"] == pytest.approx([.7, .2, .9])
    assert result["edgePivots"] == [[0, .5, 0], [.5, .5, 0], [0, 1.5, 0]]
    assert result["equivalent"] is True
    assert result["fresh"] == result["cached"]
    assert result["loadLifecycle"] == {
        "firstFromCache": False, "secondFromCache": True,
        "buildCount": 1, "sameIds": [0, 1, 2, 3],
    }

@pytest.mark.parametrize(
    ("builder_version", "joint_id"),
    [(1, 999), (0, 4)],
    ids=("invalid-joint-id", "stale-builder-version"),
)
def test_humanoid_overrides_never_fall_back_to_proximity(
        module_page, builder_version, joint_id):
    result = module_page.evaluate("""async config => {
      const control = await import('./js/mesh/humanoid-control-rig.js');
      const binding = await import('./js/mesh/humanoid-rig-binding.js');
      const controls = Object.fromEntries(control.HUMANOID_CONTROL_KEYS.map(
        key => [key, {position: [0, 0, 0],
          semantic: {sideN: 0, height01: 0, depthN: 0}}]));
      const automatic = {accepted: true, frame: {up: [0, 1, 0],
        right: [1, 0, 0], forward: [0, 0, 1], height: 1}, controls};
      const model = {joints: [{jointId: 4, restPivot: [0, 0, 0],
        restFrame: [0, 0, 0, 1]}]};
      const overrides = {version: 2, controls: {
        leftHand: {semantic: {sideN: 0, height01: 0, depthN: 0},
          joint_id: config.jointId},
      }, model_rig_builder_version: config.builderVersion};
      const mappings = control.resolveHumanoidControlMappings({
        savedOverrides: overrides, modelRig: model});
      const rigBinding = binding.buildHumanoidRigBinding({
        controlRig: automatic, modelRig: model, controlMappings: mappings,
        options: {pointDistanceRatio: 10},
      });
      return {
        rejected: [...mappings.rejectedControlKeys],
        mapped: [...mappings.keys()],
        leftHand: rigBinding.diagnostics.bindingsByControl.leftHand,
      };
    }""", {"builderVersion": builder_version, "jointId": joint_id})
    assert result["rejected"] == ["leftHand"]
    assert result["mapped"] == []
    assert result["leftHand"]["source"] == "unresolved"
    assert result["leftHand"]["rootJointId"] is None

def test_humanoid_edit_session_snapping_uses_hysteresis_and_releases(module_page):
    result = module_page.evaluate("""async () => {
      const control = await import('./js/mesh/humanoid-control-rig.js');
      const edit = await import('./js/mesh/humanoid-rig-edit-session.js');
      const controls = Object.fromEntries(control.HUMANOID_CONTROL_KEYS.map(key =>
        [key, {position: [0, 0, 0], semantic: {sideN: 0, height01: 0,
          depthN: 0}}]));
      const rig = {humanoidControlRig: {
        version: 1, accepted: true, available: true,
        frame: {up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1],
          lowHeight: 0, highHeight: 1, height: 1,
          sideCenter: 0, depthCenter: 0}, controls, paths: {},
      }, joints: [{jointId: 9, signature: '["body#bone=9"]',
        restPivot: [.4, .5, .6], members: []}]};
      const modelRigState = {
        explicitRootSignatures: new Set(['custom-root']),
        humanoidPose: {head: [1, 0, 0]},
      };
      let notifications = 0;
      let persisted = null;
      let resets = 0;
      const session = edit.createHumanoidRigEditSession({
        modelRigState,
        getModelRig: () => rig,
        getAutomaticRig: () => rig.humanoidControlRig,
        resetCurrentPoseForHumanoidRigEdit: () => {
          resets += 1;
          modelRigState.humanoidPose = {};
          return true;
        },
        getKnownMeshes: () => [{userData: {modPath: 'mod'}}],
        resolveMappings: control.resolveHumanoidControlMappings,
        persist: async (_path, value) => {
          persisted = value;
          return {saved: true};
        },
        clearPersist: async () => ({saved: true}),
        notifyChanged: () => { notifications += 1; },
      });
      session.begin();
      const initial = session.snapshot();
      const rootsAfterBegin = [...modelRigState.explicitRootSignatures];
      const poseAfterBegin = {...modelRigState.humanoidPose};
      session.beginCarry('head');
      const notificationsBeforeSilentUpdate = notifications;
      const silentUpdated = session.updateDraft('head', [.3, .4, .5], {
        candidateJointId: null, candidateDistance: Infinity,
        notifyState: false, request: false,
      });
      const silentNotificationDelta =
        notifications - notificationsBeforeSilentUpdate;
      session.finishCarry();
      const allCarryResults = control.HUMANOID_CONTROL_KEYS.map((key, index) => {
        const target = [.1 * (index + 1), .2 * (index + 1),
          .03 * (index + 1)];
        const began = session.beginCarry(key);
        const updated = session.updateDraft(key, target, {
          candidateJointId: null, candidateDistance: Infinity});
        const position = session.snapshot().controls[key].position;
        const finished = session.finishCarry();
        return {key, began, updated, finished, position};
      });
      session.beginCarry('leftShoulder');
      session.updateDraft('leftShoulder', [1, 1, 1], {
        candidateJointId: 9, candidateDistance: 10});
      const snapped = session.snapshot();
      session.updateDraft('leftShoulder', [1, 1, 1], {
        candidateJointId: 9, candidateDistance: 19, mappedDistance: 19});
      const sticky = session.snapshot();
      session.updateDraft('leftShoulder', [1, 1, 1], {
        candidateJointId: null, candidateDistance: Infinity});
      const free = session.snapshot();
      session.finishCarry();
      const saved = await session.save();
      const expectedSemantic = control.humanoidControlPositionToSemantic(
        [1, 1, 1], rig.humanoidControlRig);
      return {initial, rootsAfterBegin, poseAfterBegin, silentUpdated,
        silentNotificationDelta, allCarryResults,
        snapped, sticky, free, saved, persisted, expectedSemantic,
        notifications, resets};
    }""")
    assert result["initial"]["controls"]["leftShoulder"]["position"] == [0, 0, 0]
    assert result["rootsAfterBegin"] == ["custom-root"]
    assert result["poseAfterBegin"] == {}
    assert result["silentUpdated"] is True
    assert result["silentNotificationDelta"] == 0
    assert len(result["allCarryResults"]) == 16
    assert all(item["began"] and item["updated"] and item["finished"]
               and item["position"] != [0, 0, 0]
               for item in result["allCarryResults"])
    assert result["snapped"]["mappedJointIdByControl"] == {"leftShoulder": 9}
    assert result["snapped"]["controls"]["leftShoulder"]["position"] == [.4, .5, .6]
    assert result["sticky"]["mappedJointIdByControl"] == {"leftShoulder": 9}
    assert result["sticky"]["controls"]["leftShoulder"]["position"] == [.4, .5, .6]
    assert result["free"]["mappedJointIdByControl"] == {}
    assert result["free"]["controls"]["leftShoulder"]["position"] == [1, 1, 1]
    assert result["saved"]["saved"] is True
    assert result["persisted"]["controls"]["leftShoulder"]["semantic"] == \
        result["expectedSemantic"]
    assert result["notifications"] >= 5
    assert result["resets"] == 1


def test_humanoid_edit_save_and_reset_refresh_without_model_rig_rebuild(
        module_page):
    result = module_page.evaluate("""async () => {
      const control = await import('./js/mesh/humanoid-control-rig.js');
      const edit = await import('./js/mesh/humanoid-rig-edit-session.js');
      const controls = Object.fromEntries(control.HUMANOID_CONTROL_KEYS.map(
        (key, index) => [key, {position: [0, index / 20, 0],
          semantic: {sideN: 0, height01: index / 20, depthN: 0}}]));
      const automatic = {version: 1, available: true, accepted: true,
        frame: {up: [0, 1, 0], right: [1, 0, 0], forward: [0, 0, 1],
          lowHeight: 0, highHeight: 1, height: 1,
          sideCenter: 0, depthCenter: 0}, controls, paths: {}};
      const rig = {
        humanoidControlRig: automatic,
        humanoidAutomaticControlRig: automatic,
        joints: [{jointId: 0, signature: '[0]', restPivot: [0, 0, 0]},
          {jointId: 1, signature: '[1]', restPivot: [0, 1, 0]}],
        components: [{componentId: 0, rootId: 0, nodeIds: [0, 1],
          parentById: {0: null, 1: 0}, childrenById: {0: [1], 1: []}}],
        componentByJointId: new Map([[0, 0], [1, 0]]),
        sourceBoneToModelJointId: new Map([['body#bone=0', 0],
          ['body#bone=1', 1]]),
      };
      const modelRigState = {humanoidPose: {head: [1, 0, 0]}};
      const topology = () => JSON.stringify({
        joints: rig.joints.map(joint => [joint.jointId, joint.signature]),
        components: rig.components.map(component => ({
          nodeIds: component.nodeIds, parentById: component.parentById,
          childrenById: component.childrenById,
        })),
        sourceBoneToModelJointId: [...rig.sourceBoneToModelJointId],
      });
      const beforeTopology = topology();
      const modelRigSidecar = JSON.stringify({version: 1, builder_version: 1,
        source_table: ['body|offset=0'], joints: [{joint_id: 0}]});
      const beforeModelRigSidecar = modelRigSidecar;
      const refreshes = [];
      let legacyRebuilds = 0;
      const session = edit.createHumanoidRigEditSession({
        modelRigState,
        getModelRig: () => rig,
        getAutomaticRig: () => rig.humanoidAutomaticControlRig,
        resetCurrentPoseForHumanoidRigEdit: () => {
          modelRigState.humanoidPose = {};
        },
        getKnownMeshes: () => [{userData: {modPath: 'mod'}}],
        resolveMappings: control.resolveHumanoidControlMappings,
        persist: async () => ({saved: true}),
        clearPersist: async () => ({saved: true}),
        refreshHumanoidRig: async savedOverrides => {
          refreshes.push(savedOverrides ? 'save' : 'reset');
          const mappings = control.resolveHumanoidControlMappings({
            savedOverrides, modelRig: rig,
          });
          rig.humanoidControlRig = control.applyHumanoidControlRigOverrides({
            automaticRig: rig.humanoidAutomaticControlRig, savedOverrides,
            modelRig: rig, resolvedMappings: mappings,
          });
          modelRigState.humanoidPose = {};
        },
        // This is the removed callback. It must remain unused.
        rebuildActiveRig: async () => { legacyRebuilds += 1; },
      });
      session.begin();
      session.beginCarry('head');
      session.updateDraft('head', [.25, .8, .1], {
        candidateJointId: null, candidateDistance: Infinity,
      });
      session.finishCarry();
      const saved = await session.save();
      const savedHead = [...rig.humanoidControlRig.controls.head.position];
      const afterSaveTopology = topology();
      session.begin();
      const reset = await session.reset();
      const resetHead = [...rig.humanoidControlRig.controls.head.position];
      return {saved, reset, refreshes, legacyRebuilds, beforeTopology,
        afterSaveTopology, afterResetTopology: topology(), savedHead, resetHead,
        automaticHead: automatic.controls.head.position, beforeModelRigSidecar,
        afterModelRigSidecar: modelRigSidecar};
    }""")
    assert result["saved"]["saved"] is True
    assert result["reset"]["saved"] is True
    assert result["refreshes"] == ["save", "reset"]
    assert result["legacyRebuilds"] == 0
    assert result["beforeTopology"] == result["afterSaveTopology"]
    assert result["beforeTopology"] == result["afterResetTopology"]
    assert result["savedHead"] != result["automaticHead"]
    assert result["resetHead"] == result["automaticHead"]
    assert result["beforeModelRigSidecar"] == result["afterModelRigSidecar"]


def test_humanoid_ik_selection_uses_control_keys_and_resets_default(module_page):
    result = module_page.evaluate("""async () => {
      const pose = await import('./js/mesh/humanoid-pose-runtime.js');
      const controls = Object.fromEntries([
        'chest', 'pelvis', 'neck', 'head',
        'leftShoulder', 'leftElbow', 'leftHand',
        'rightShoulder', 'rightElbow', 'rightHand',
        'leftHip', 'leftKnee', 'leftFoot',
        'rightHip', 'rightKnee', 'rightFoot',
      ].map(key => [key, {position: [0, 0, 0]}]));
      const state = {
        ikEnabled: false, activeLimbRole: 'right_leg',
        selectedHumanoidControlKey: null, humanoidPose: {},
      };
      const notifications = [];
      const session = pose.createHumanoidPoseRuntime({
        modelRigState: state,
        getModelRig: () => ({humanoidControlRig: {accepted: true, controls}}),
        getPrimaryLimb: role => ({role, available: role === 'left_arm', keys: []}),
        solveControlIk: () => ({positions: {}}),
        mergeLimbPose: value => value,
        applyPose: () => false,
        notifyChanged: () => notifications.push('changed'),
        requestRender: () => notifications.push('render'),
      });
      const enabled = session.setIkEnabled(true);
      const afterEnable = {...state};
      const selectedArm = session.selectControl('rightElbow');
      const afterArm = {...state};
      const selectedCentral = session.selectControl('neck');
      const afterCentral = {...state};
      session.setIkEnabled(false);
      session.setIkEnabled(true);
      return {enabled, afterEnable, selectedArm, afterArm,
        selectedCentral, afterCentral, final: {...state}, notifications};
    }""")
    assert result["enabled"] is True
    assert result["afterEnable"]["activeLimbRole"] == "left_arm"
    assert result["afterEnable"]["selectedHumanoidControlKey"] == "leftHand"
    assert result["selectedArm"] is True
    assert result["afterArm"]["activeLimbRole"] == "right_arm"
    assert result["afterArm"]["selectedHumanoidControlKey"] == "rightElbow"
    assert result["selectedCentral"] is True
    assert result["afterCentral"]["activeLimbRole"] == "right_arm"
    assert result["afterCentral"]["selectedHumanoidControlKey"] == "neck"
    assert result["final"]["activeLimbRole"] == "left_arm"
    assert result["final"]["selectedHumanoidControlKey"] == "leftHand"


def test_humanoid_pose_factories_isolate_state(module_page):
    result = module_page.evaluate("""async () => {
      const {createHumanoidPoseRuntime} = await import(
        './js/mesh/humanoid-pose-runtime.js');
      const makeState = () => ({
        ikEnabled: false, activeLimbRole: 'right_leg',
        selectedHumanoidControlKey: null, humanoidPose: {},
      });
      const makeSession = modelRigState => createHumanoidPoseRuntime({
        modelRigState,
        getModelRig: () => ({humanoidControlRig: {accepted: true, controls: {}}}),
        getPrimaryLimb: role => ({role, available: role === 'left_arm', keys: []}),
        solveControlIk: () => ({positions: {}}),
        mergeLimbPose: value => value,
        applyPose: () => false,
        notifyChanged: () => {}, requestRender: () => {},
      });
      const firstState = makeState();
      const secondState = makeState();
      const first = makeSession(firstState);
      const second = makeSession(secondState);
      first.setIkEnabled(true);
      first.selectControl('rightElbow');
      return {first: {...firstState}, second: {...secondState}};
    }""")
    assert result["first"]["ikEnabled"] is True
    assert result["first"]["activeLimbRole"] == "right_arm"
    assert result["first"]["selectedHumanoidControlKey"] == "rightElbow"
    assert result["second"] == {
        "ikEnabled": False,
        "activeLimbRole": "right_leg",
        "selectedHumanoidControlKey": None,
        "humanoidPose": {},
    }


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
        assert rig["skeletonDepth"]["depthN"] == pytest.approx(.02294, abs=.001)
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


def test_camera_frame_known_game_orientation_policy(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createCameraFrame, shouldApplyUprightRotation} = await import(
        './js/scene/camera-frame.js');
      const rawSize = {x: 4, y: .8, z: 2};
      const decisions = {
        zzz: shouldApplyUprightRotation({gameId: 'zzz', rawSize}),
        wuwa: shouldApplyUprightRotation({gameId: 'WuWa', rawSize}),
        recognizedOther: shouldApplyUprightRotation({
          gameId: 'genshin', rawSize}),
        unknown: shouldApplyUprightRotation({gameId: null, rawSize}),
        unknownId: shouldApplyUprightRotation({
          gameId: 'unknown', rawSize}),
        ordinaryUnknown: shouldApplyUprightRotation({gameId: null,
          rawSize: {x: 1, y: 2, z: 1}}),
      };
      const createFrame = onOrientationChanged => {
        const camera = new THREE.PerspectiveCamera(45, 4 / 3, .01, 100);
        const controls = {
          target: new THREE.Vector3(), update() {}, setCamera() {},
          saveState() {},
        };
        const renderer = {
          domElement: {getBoundingClientRect: () => ({
            width: 800, height: 600, left: 0, right: 800,
          })}, setSize() {},
        };
        const grid = {scale: new THREE.Vector3(1, 1, 1),
          position: new THREE.Vector3()};
        return createCameraFrame({camera, renderer, controls, grid,
          cancelViewSnap() {}, onOrientationChanged});
      };
      const zzzFrame = createFrame();
      const zzzMesh = new THREE.Mesh(new THREE.BoxGeometry(4, .8, 2));
      zzzFrame.fitTo([zzzMesh], {gameId: 'zzz'});
      const zzzSize = new THREE.Box3().setFromObject(zzzMesh)
        .getSize(new THREE.Vector3());
      const wuwaFrame = createFrame();
      const wuwaMesh = new THREE.Mesh(new THREE.BoxGeometry(4, .8, 2));
      wuwaFrame.fitTo([wuwaMesh], {
        gameId: 'wuwa', initialRotationY: Math.PI,
      });
      const events = [];
      const stateFrame = createFrame(state => events.push(state));
      const stateBefore = stateFrame.getModelTransformState();
      const stateMesh = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1));
      stateFrame.fitTo([stateMesh], {initialRotationY: Math.PI / 2});
      const stateFitted = stateFrame.getModelTransformState();
      stateFrame.rotateModelQuarterTurn([stateMesh]);
      const stateTurned = stateFrame.getModelTransformState();
      const arrays = state => ({
        orientation: state.orientation.toArray(),
        baseOrientation: state.baseOrientation.toArray(),
        userRotation: state.userRotation.toArray(),
        orientationInitialized: state.orientationInitialized,
        modelOrientationRevision: state.modelOrientationRevision,
      });
      return {
        decisions,
        zzzHeight: zzzSize.y,
        zzzOrientation: zzzFrame.getModelTransformState()
          .baseOrientation.toArray(),
        wuwaOrientation: wuwaFrame.getModelTransformState()
          .baseOrientation.toArray(),
        state: {
          before: arrays(stateBefore), fitted: arrays(stateFitted),
          turned: arrays(stateTurned), eventCount: events.length,
        },
      };
    }""")
    assert result["decisions"] == {
        "zzz": True,
        "wuwa": True,
        "recognizedOther": False,
        "unknown": False,
        "unknownId": False,
        "ordinaryUnknown": False,
    }
    assert result["zzzHeight"] == pytest.approx(2)
    assert result["zzzOrientation"] == pytest.approx(
        [-2 ** -0.5, 0, 0, 2 ** -0.5])
    assert result["wuwaOrientation"] == pytest.approx(
        [0, 2 ** -0.5, 2 ** -0.5, 0])
    assert result["state"]["before"] == {
        "orientation": [0, 0, 0, 1],
        "baseOrientation": [0, 0, 0, 1],
        "userRotation": [0, 0, 0, 1],
        "orientationInitialized": False,
        "modelOrientationRevision": 0,
    }
    assert result["state"]["fitted"]["orientationInitialized"]
    assert result["state"]["fitted"]["modelOrientationRevision"] == 1
    assert result["state"]["fitted"]["baseOrientation"] == pytest.approx(
        [0, 2 ** -0.5, 0, 2 ** -0.5])
    assert result["state"]["turned"]["modelOrientationRevision"] == 1
    assert result["state"]["turned"]["baseOrientation"] == pytest.approx(
        result["state"]["fitted"]["baseOrientation"])
    assert result["state"]["turned"]["userRotation"] != pytest.approx(
        result["state"]["fitted"]["userRotation"])
    assert result["state"]["eventCount"] == 1


@pytest.mark.parametrize("case", [
    {"name": "short-arm", "hand": .41, "elbow": .30},
    {"name": "overlong-decoy", "hand": .48, "elbow": .34, "decoy": True},
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
      const binding = bindingModule.buildHumanoidRigBinding({
        controlRig, modelRig,
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
        unboundJointCount: binding.unboundJointIds.length,
        resetError,
        posedFoot: lowerFootTarget?.elements.slice(12, 15) || null,
            bindingDiagnostics: binding.diagnostics,
      };
    }""")
    assert result["lowerKneeDriver"] == "left_lower_leg"
    assert result["lowerFootDriver"] == "left_lower_leg"
    assert result["directBindings"] >= 10
    assert result["unboundJointCount"] == 0
    assert result["resetError"] < 1e-5
    assert result["posedFoot"] is not None
    assert result["posedFoot"] != pytest.approx([-.1, .05, 0])


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


def test_humanoid_binding_mapped_control_does_not_claim_nearby_joint(
        module_page):
    result = module_page.evaluate("""async () => {
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
      const points = [controls.leftHand, [-.57, 1.08, 0]];
      const joints = points.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const modelRig = {joints, jointPivotByJointId: new Map(
        joints.map(joint => [joint.jointId, joint.restPivot]))};
      const controlRig = {frame: {height: 1.5, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls: Object.fromEntries(
        Object.entries(controls).map(([key, position]) => [key, {position}]))};
      const binding = bindingModule.buildHumanoidRigBinding({controlRig, modelRig,
        controlMappings: new Map([['leftHand', {
          controlKey: 'leftHand', jointId: 0}]]),
        options: {pointDistanceRatio: .02}});
      return {
        mapped: [binding.jointBindings.get(0)?.driverId || null,
          binding.jointBindings.get(0)?.bindingMethod || null],
        nearby: binding.jointBindings.get(1) || null,
        mappedSeeds: binding.diagnostics.bindingsByControl.leftHand.directSeedCount,
        unowned: binding.unboundJointIds,
      };
    }""")
    assert result["mapped"] == ["left_lower_arm", "manual_control_mapping"]
    assert result["nearby"] is None
    assert result["mappedSeeds"] == 1
    assert result["unowned"] == [1]


def test_humanoid_binding_claims_remaining_in_radius_joints_as_direct_seeds(
        module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidRigBinding, HUMANOID_DRIVER_SEGMENTS} = await import(
        './js/mesh/humanoid-rig-binding.js');
      const positions = {
        chest: [0, 1.5, 0], pelvis: [0, .5, 0], neck: [0, 1.8, 0],
        head: [0, 2.1, 0], leftShoulder: [-.2, 1.4, 0],
        leftElbow: [-.5, 1.2, 0], leftHand: [-.8, 1, 0],
        rightShoulder: [.2, 1.4, 0], rightElbow: [.5, 1.2, 0],
        rightHand: [.8, 1, 0], leftHip: [-.25, .5, 0],
        leftKnee: [-.25, .25, 0], leftFoot: [-.25, 0, 0],
        rightHip: [.25, .5, 0], rightKnee: [.25, .25, 0],
        rightFoot: [.25, 0, 0],
      };
      const points = [positions.leftShoulder, [-.21, 1.4, 0],
        [-.19, 1.39, 0], positions.leftElbow, [-.51, 1.2, 0],
        positions.leftHand];
      const joints = points.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const modelRig = {
        joints,
        jointPivotByJointId: new Map(joints.map(joint =>
          [joint.jointId, joint.restPivot])),
        components: [{componentId: 0, rootId: 0, nodeIds: [0, 1, 2, 3, 4, 5],
          parentById: {0: null, 1: 0, 2: 1, 3: 0, 4: 3, 5: 4},
          childrenById: {0: [1, 3], 1: [2], 2: [], 3: [4], 4: [5], 5: []}}],
      };
      const controlRig = {frame: {height: 2.1, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls: Object.fromEntries(
        Object.entries(positions).map(([key, position]) => [key, {position}]))};
      const allowed = new Set(['leftShoulder', 'leftElbow', 'leftHand']);
      const mappings = new Map();
      mappings.rejectedControlKeys = new Set(
        Object.keys(positions).filter(key => !allowed.has(key)));
      const binding = buildHumanoidRigBinding({controlRig, modelRig,
        controlMappings: mappings, options: {pointDistanceRatio: .03}});
      const owner = jointId => {
        const entry = binding.jointBindings.get(jointId);
        return [entry?.controlKey || null, entry?.bindingMethod || null,
          entry?.anchorJointId ?? null];
      };
      return {
        owners: joints.map(joint => owner(joint.jointId)),
        shoulder: binding.diagnostics.bindingsByControl.leftShoulder,
        elbow: binding.diagnostics.bindingsByControl.leftElbow,
        hand: binding.diagnostics.bindingsByControl.leftHand,
        directSeedCount: binding.diagnostics.directSeedCount,
      };
    }""")
    assert result["owners"] == [
        ["leftShoulder", "automatic_control_mapping", 0],
        ["leftShoulder", "automatic_control_mapping", 0],
        ["leftShoulder", "automatic_control_mapping", 0],
        ["leftElbow", "automatic_control_mapping", 3],
        ["leftElbow", "automatic_control_mapping", 3],
        ["leftHand", "automatic_control_mapping", 5],
    ]
    assert result["shoulder"]["directSeedCount"] == 3
    assert result["elbow"]["directSeedCount"] == 2
    assert result["hand"]["directSeedCount"] == 1
    assert result["directSeedCount"] == 6


def test_humanoid_binding_overlapping_extra_seed_uses_closest_control(
        module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidRigBinding} = await import(
        './js/mesh/humanoid-rig-binding.js');
      const positions = {
        chest: [100, 100, 100], pelvis: [100, 100, 100],
        neck: [100, 100, 100], head: [100, 100, 100],
        leftShoulder: [-.2, 1.4, 0], leftElbow: [-.5, 1.2, 0],
        leftHand: [100, 100, 100], rightShoulder: [100, 100, 100],
        rightElbow: [100, 100, 100], rightHand: [100, 100, 100],
        leftHip: [100, 100, 100], leftKnee: [100, 100, 100],
        leftFoot: [100, 100, 100], rightHip: [100, 100, 100],
        rightKnee: [100, 100, 100], rightFoot: [100, 100, 100],
      };
      const points = [positions.leftShoulder, positions.leftElbow,
        [-.34, 1.25, 0]];
      const joints = points.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const modelRig = {joints, jointPivotByJointId: new Map(joints.map(joint =>
        [joint.jointId, joint.restPivot]))};
      const controlRig = {frame: {height: 2.1, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls: Object.fromEntries(
        Object.entries(positions).map(([key, position]) => [key, {position}]))};
      const mappings = new Map();
      mappings.rejectedControlKeys = new Set(Object.keys(positions).filter(key =>
        !['leftShoulder', 'leftElbow'].includes(key)));
      const binding = buildHumanoidRigBinding({controlRig, modelRig,
        controlMappings: mappings, options: {pointDistanceRatio: .1}});
      const extra = binding.jointBindings.get(2);
      return {owner: [extra?.controlKey || null, extra?.anchorJointId ?? null],
        shoulder: binding.diagnostics.bindingsByControl.leftShoulder,
        elbow: binding.diagnostics.bindingsByControl.leftElbow};
    }""")
    assert result["owner"] == ["leftElbow", 1]
    assert result["shoulder"]["directSeedCount"] == 1
    assert result["elbow"]["directSeedCount"] == 2


def test_humanoid_binding_resolves_automatic_anchors_before_inheritance(
        module_page):
    result = module_page.evaluate("""async () => {
      const bindingModule = await import('./js/mesh/humanoid-rig-binding.js');
      const positions = {
        chest: [0, 1.5, 0], pelvis: [0, .5, 0], neck: [0, 1.8, 0],
        head: [0, 2.1, 0], leftShoulder: [-.2, 1.4, 0],
        leftElbow: [-.5, 1.2, 0], leftHand: [-.8, 1, 0],
        rightShoulder: [.2, 1.4, 0], rightElbow: [.5, 1.2, 0],
        rightHand: [.8, 1, 0], leftHip: [-.25, .5, 0],
        leftKnee: [-.25, .25, 0], leftFoot: [-.25, 0, 0],
        rightHip: [.25, .5, 0], rightKnee: [.25, .25, 0],
        rightFoot: [.25, 0, 0],
      };
      const points = [positions.leftShoulder, positions.leftElbow,
        positions.leftHand, [-.79, .99, 0]];
      const joints = points.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const modelRig = {
        joints,
        jointPivotByJointId: new Map(joints.map(joint =>
          [joint.jointId, joint.restPivot])),
        components: [{componentId: 0, rootId: 0, nodeIds: [0, 1, 2, 3],
          parentById: {0: null, 1: 0, 2: 1, 3: 2},
          childrenById: {0: [1], 1: [2], 2: [3], 3: []}}],
      };
      const controlRig = {frame: {height: 2.1, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls: Object.fromEntries(
        Object.entries(positions).map(([key, position]) => [key, {position}]))};
      const binding = bindingModule.buildHumanoidRigBinding({controlRig, modelRig,
        controlMappings: new Map([
          ['leftShoulder', {controlKey: 'leftShoulder', jointId: 0}],
          ['leftHand', {controlKey: 'leftHand', jointId: 2}],
        ]), options: {pointDistanceRatio: .02}});
      return [0, 1, 2, 3].map(jointId => {
        const entry = binding.jointBindings.get(jointId);
        return [entry?.controlKey || null, entry?.bindingMethod || null,
          entry?.inheritedFromJointId ?? null];
      });
    }""")
    assert result == [
        ["leftShoulder", "manual_control_mapping", None],
        ["leftElbow", "automatic_control_mapping", None],
        ["leftHand", "manual_control_mapping", None],
        ["leftHand", "inherited_control", 2],
    ]


def test_humanoid_binding_automatic_anchor_conflicts_are_deterministic(
        module_page):
    result = module_page.evaluate("""async () => {
      const bindingModule = await import('./js/mesh/humanoid-rig-binding.js');
      const controls = {
        chest: [0, 1.5, 0], pelvis: [0, .5, 0],
        leftShoulder: [-.2, 1.4, 0], leftElbow: [-.5, 1.2, 0],
        leftHand: [-.8, 1, 0], rightShoulder: [.2, 1.4, 0],
        rightElbow: [.5, 1.2, 0], rightHand: [.8, 1, 0],
        leftHip: [-.25, .5, 0], leftKnee: [-.25, .25, 0],
        leftFoot: [-.25, 0, 0], rightHip: [.25, .5, 0],
        rightKnee: [.25, .25, 0], rightFoot: [.25, 0, 0],
      };
      const points = [[-.35, 1.3, 0], controls.leftElbow];
      const joints = points.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const modelRig = {joints, jointPivotByJointId: new Map(
        joints.map(joint => [joint.jointId, joint.restPivot]))};
      const controlRig = {frame: {height: 2.1, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls: Object.fromEntries(
        Object.entries(controls).map(([key, position]) => [key, {position}]))};
      const binding = bindingModule.buildHumanoidRigBinding({controlRig, modelRig,
        options: {pointDistanceRatio: .1}});
      return {
        owners: [0, 1].map(jointId => {
          const entry = binding.jointBindings.get(jointId);
          return [entry?.controlKey || null, entry?.bindingMethod || null,
            entry?.anchorJointId ?? null];
        }),
        shoulder: binding.diagnostics.bindingsByControl.leftShoulder,
        elbow: binding.diagnostics.bindingsByControl.leftElbow,
      };
    }""")
    assert result["owners"] == [
        ["leftShoulder", "automatic_control_mapping", 0],
        ["leftElbow", "automatic_control_mapping", 1],
    ]
    assert result["shoulder"]["distanceRatio"] == pytest.approx(
        ((.15 ** 2 + .1 ** 2) ** .5) / 2.1)
    assert result["elbow"]["distanceRatio"] == pytest.approx(0)


def test_humanoid_binding_uses_one_root_and_expands_recursive_boundaries(
        module_page):
    result = module_page.evaluate("""async () => {
      const bindingModule = await import('./js/mesh/humanoid-rig-binding.js');
      const controls = {
        chest: [100, 100, 100], pelvis: [100, 100, 100],
        neck: [100, 100, 100], head: [100, 100, 100],
        leftShoulder: [0, 0, 0], leftElbow: [3, 0, 0],
        leftHand: [6, 0, 0], rightShoulder: [100, 100, 100],
        rightElbow: [100, 100, 100], rightHand: [100, 100, 100],
        leftHip: [100, 100, 100], leftKnee: [100, 100, 100],
        leftFoot: [100, 100, 100], rightHip: [100, 100, 100],
        rightKnee: [100, 100, 100], rightFoot: [100, 100, 100],
      };
      const points = [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0],
        [4, 0, 0], [5, 0, 0], [6, 0, 0], [7, 0, 0], [8, 0, 0],
        [30, 0, 0]];
      const joints = points.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const modelRig = {
        joints,
        jointPivotByJointId: new Map(joints.map(joint =>
          [joint.jointId, joint.restPivot])),
        components: [{componentId: 0, rootId: 0, nodeIds: [0, 1, 2, 3, 4,
          5, 6, 7, 8],
          parentById: {0: null, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4,
            6: 3, 7: 6, 8: 7},
          childrenById: {0: [1], 1: [2], 2: [3], 3: [4, 6],
            4: [5], 5: [], 6: [7], 7: [8], 8: []}},
          {componentId: 1, rootId: 9, nodeIds: [9],
            parentById: {9: null}, childrenById: {9: []}}],
      };
      const controlRig = {frame: {height: 10, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls: Object.fromEntries(
        Object.entries(controls).map(([key, position]) => [key, {position}]))};
      const binding = bindingModule.buildHumanoidRigBinding({
        controlRig, modelRig,
        controlMappings: new Map([
          ['leftShoulder', {controlKey: 'leftShoulder', jointId: 0}],
          ['leftHand', {controlKey: 'leftHand', jointId: 6}],
        ]),
        options: {pointDistanceRatio: .02},
      });
      const ownerFor = jointId => {
        const entry = binding.jointBindings.get(jointId);
        return [entry?.controlKey || null, entry?.bindingMethod || null,
          entry?.inheritedFromJointId ?? null];
      };
      return {
        owners: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(ownerFor),
        directSeedCount: binding.diagnostics.directSeedCount,
        leftElbow: binding.diagnostics.bindingsByControl.leftElbow,
      };
    }""")
    assert result["owners"] == [
        ["leftShoulder", "manual_control_mapping", None],
        ["leftShoulder", "inherited_control", 0],
        ["leftShoulder", "inherited_control", 1],
        ["leftElbow", "automatic_control_mapping", None],
        ["leftElbow", "inherited_control", 3],
        ["leftElbow", "inherited_control", 4],
        ["leftHand", "manual_control_mapping", None],
        ["leftHand", "inherited_control", 6],
        ["leftHand", "inherited_control", 7],
        [None, None, None],
    ]
    assert result["directSeedCount"] == 3
    assert result["leftElbow"]["rootJointId"] == 3
    assert result["leftElbow"]["descendantCount"] == 2


def test_humanoid_binding_does_not_cross_disconnected_components_or_claim_nearby_branch(
        module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidRigBinding} = await import(
        './js/mesh/humanoid-rig-binding.js');
      const controls = Object.fromEntries([
        'chest', 'pelvis', 'neck', 'head', 'leftShoulder', 'leftElbow',
        'leftHand', 'rightShoulder', 'rightElbow', 'rightHand', 'leftHip',
        'leftKnee', 'leftFoot', 'rightHip', 'rightKnee', 'rightFoot',
      ].map(key => [key, {position: [100, 100, 100]}]));
      controls.leftHand.position = [0, 0, 0];
      // The second component is deliberately close to the mapped hand but is
      // not part of its descendant hierarchy.
      const points = [[0, 0, 0], [1, 0, 0], [.02, 0, 0], [1.02, 0, 0]];
      const joints = points.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const modelRig = {
        joints,
        jointPivotByJointId: new Map(joints.map(joint =>
          [joint.jointId, joint.restPivot])),
        components: [
          {componentId: 0, rootId: 0, nodeIds: [0, 1],
            parentById: {0: null, 1: 0}, childrenById: {0: [1], 1: []}},
          {componentId: 1, rootId: 2, nodeIds: [2, 3],
            parentById: {2: null, 3: 2}, childrenById: {2: [3], 3: []}},
        ],
      };
      const controlRig = {frame: {height: 10, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls};
      const binding = buildHumanoidRigBinding({controlRig, modelRig,
        controlMappings: new Map([['leftHand', {
          controlKey: 'leftHand', jointId: 0}]]),
        options: {pointDistanceRatio: .02}});
      const entry = jointId => binding.jointBindings.get(jointId);
      return {
        firstComponent: [entry(0)?.controlKey || null,
          entry(1)?.controlKey || null],
        secondComponent: [entry(2)?.controlKey || null,
          entry(3)?.controlKey || null],
        unowned: binding.unboundJointIds,
      };
    }""")
    assert result == {
        "firstComponent": ["leftHand", "leftHand"],
        "secondComponent": [None, None],
        "unowned": [2, 3],
    }


def test_humanoid_binding_inherits_through_attachment_descendants(module_page):
    result = module_page.evaluate("""async () => {
      const {buildHumanoidRigBinding} = await import(
        './js/mesh/humanoid-rig-binding.js');
      const controls = Object.fromEntries([
        'chest', 'pelvis', 'neck', 'head', 'leftShoulder', 'leftElbow',
        'leftHand', 'rightShoulder', 'rightElbow', 'rightHand', 'leftHip',
        'leftKnee', 'leftFoot', 'rightHip', 'rightKnee', 'rightFoot',
      ].map(key => [key, {position: [100, 100, 100]}]));
      controls.leftShoulder.position = [0, 2, 0];
      controls.leftElbow.position = [0, 1, 0];
      controls.leftHand.position = [0, 0, 0];
      const points = [[0, 0, 0], [0, -.2, 0], [0, -.4, 0]];
      const joints = points.map((restPivot, jointId) => ({jointId,
        restPivot, restCenter: restPivot, restFrame: [0, 0, 0, 1]}));
      const modelRig = {
        joints,
        jointPivotByJointId: new Map(joints.map(joint =>
          [joint.jointId, joint.restPivot])),
        components: [{componentId: 0, rootId: 0, nodeIds: [0, 1, 2],
          parentById: {0: null, 1: 0, 2: 1},
          childrenById: {0: [1], 1: [2], 2: []},
          edges: [{jointA: 0, jointB: 1, relationshipType: 'attachment'},
            {jointA: 1, jointB: 2, relationshipType: 'attachment'}]}],
        edges: [{jointA: 0, jointB: 1, relationshipType: 'attachment'},
          {jointA: 1, jointB: 2, relationshipType: 'attachment'}],
      };
      const controlRig = {frame: {height: 2, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls};
      const mappings = new Map([['leftHand', {
        controlKey: 'leftHand', jointId: 0}]]);
      const binding = buildHumanoidRigBinding({controlRig, modelRig,
        controlMappings: mappings, options: {pointDistanceRatio: .01}});
      return [0, 1, 2].map(jointId => {
        const entry = binding.jointBindings.get(jointId);
        return [entry?.controlKey || null, entry?.bindingMethod || null,
          entry?.inheritedFromJointId ?? null];
      });
    }""")
    assert result == [
        ["leftHand", "manual_control_mapping", None],
        ["leftHand", "inherited_control", 0],
        ["leftHand", "inherited_control", 1],
    ]


def test_humanoid_binding_does_not_use_segment_geometry_for_anchors(module_page):
    result = module_page.evaluate("""async () => {
      const bindingModule = await import('./js/mesh/humanoid-rig-binding.js');
      const controls = {
        chest: [0, 1.5, 0], pelvis: [0, .5, 0],
        leftShoulder: [-.2, 1.4, 0], leftElbow: [-.5, 1.2, 0],
        leftHand: [-.8, 1, 0], rightShoulder: [.2, 1.4, 0],
        rightElbow: [.5, 1.2, 0], rightHand: [.8, 1, 0],
        leftHip: [-.25, .5, 0], leftKnee: [-.25, .25, 0],
        leftFoot: [-.25, 0, 0], rightHip: [.25, .5, 0],
        rightKnee: [.25, .25, 0], rightFoot: [.25, 0, 0],
      };
      const point = [-.65, 1.1, 0];
      const joints = [{jointId: 0, restPivot: point, restCenter: point,
        restFrame: [0, 0, 0, 1]}];
      const modelRig = {joints, jointPivotByJointId: new Map([[0, point]])};
      const controlRig = {frame: {height: 2.1, right: [1, 0, 0],
        forward: [0, 0, 1]}, controls: Object.fromEntries(
        Object.entries(controls).map(([key, position]) => [key, {position}]))};
      const binding = bindingModule.buildHumanoidRigBinding({controlRig, modelRig,
        options: {pointDistanceRatio: .05}});
      return {binding: binding.jointBindings.get(0) || null,
        diagnostics: binding.diagnostics};
    }""")
    assert result["binding"] is None
    assert result["diagnostics"]["unownedJointCount"] == 1


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
        buildHumanoidDriverBaseTransforms} = await import(
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
          restCenter: controls.leftHand, restFrame: [0, 0, 0, 1],
          members: [{sourceKey: 'body', boneId: 0, sourceBoneKey: 'body#bone=0'}]}],
        jointPivotByJointId: new Map([[0, controls.leftHand]]),
        restFrameByJointId: new Map([[0, new THREE.Quaternion()]]),
        components: [{componentId: 0, rootId: 0, nodeIds: [0],
          parentById: {0: null}, childrenById: {0: []}}],
        componentByJointId: new Map([[0, 0]]),
      };
      const binding = buildHumanoidRigBinding({controlRig, modelRig});
      const solved = solveHumanoidControlIk({
        controlRig, role: 'left_arm', target: [-.55, 1, 0],
      });
      const driverLayer = buildHumanoidDriverBaseTransforms({
        binding, controlRig, modelRig, posedControls: solved.positions,
      });
      const driver = new Map([[0, driverLayer.result.get(0)]]);
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
