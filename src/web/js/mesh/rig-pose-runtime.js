// Owns model pose validation, transform construction and deformation.

import * as THREE from 'three';
import {buildHumanoidDriverBaseTransforms} from './humanoid-rig-binding.js';
import {sourceBoneKey} from './weight-rig-reconcile.js';
import {weightRigStatus} from './weight-rig-status.js';
import {buildForestTransformsFromLocalRotations} from './weight-deformation.js';
import {buildSelectedWeightMask} from './weight-selection.js';
import {activePoseJointIds} from './weight-runtime.js';

const RIG_IDENTITY_MATRIX = new THREE.Matrix4();

function strictQuaternion(value) {
  const values = value?.isQuaternion
    ? [value.x, value.y, value.z, value.w]
    : Array.isArray(value) || ArrayBuffer.isView(value)
      ? [...value].slice(0, 4).map(Number)
      : [value?.x, value?.y, value?.z, value?.w].map(Number);
  if (values.length !== 4 || !values.every(Number.isFinite)) return null;
  const quaternion = new THREE.Quaternion(...values);
  if (!Number.isFinite(quaternion.lengthSq())
      || quaternion.lengthSq() <= 1e-12) return null;
  return quaternion.normalize();
}

function rotationEntries(rotationsByJointId) {
  if (rotationsByJointId instanceof Map) return [...rotationsByJointId.entries()];
  return Object.entries(rotationsByJointId || {});
}

export function createRigPoseRuntime({state, getRig, sourceSkinningRigs,
    skinningRuntime, physicsRuntime, modelPhysicsSession,
    getModelTransformState, invalidateShadow, quaternionIsIdentity,
    setComponentRoot, resetModelPose,
    notifyChanged, notifyPoseChanged, requestRender, rigPresetState} = {}) {
  function jointForId(jointId) {
    const id = Number(jointId);
    const rig = getRig();
    return Number.isInteger(id) ? rig?.joints?.[id] || null : null;
  }

  function componentForJoint(jointId, rig = getRig()) {
    const id = Number(jointId);
    const componentId = rig?.componentByJointId?.get?.(id);
    return Number.isInteger(Number(componentId))
      ? rig?.components?.[Number(componentId)] || null : null;
  }

  function parentForJoint(jointId, rig = getRig()) {
    const component = componentForJoint(jointId, rig);
    const parent = component?.parentById?.[Number(jointId)];
    return parent === null || parent === undefined ? null : Number(parent);
  }

  function sourceRigForKey(sourceKeyValue) {
    return sourceSkinningRigs?.get(String(sourceKeyValue)) || null;
  }

  function sourceComponentForBone(rig, boneId) {
    const componentId = rig?.inferredForest?.componentByBoneId?.[boneId];
    return Number.isInteger(Number(componentId))
      ? rig?.inferredForest?.components?.[Number(componentId)] || null : null;
  }

  function representativeMember(joint) {
    return joint?.representativeMember || joint?.members?.[0] || null;
  }

  function modelJointIdForSourceBone(sourceKeyValue, boneId) {
    return getRig()?.sourceBoneToModelJointId?.get(
      sourceBoneKey(sourceKeyValue, boneId));
  }

  function hasActivePhysics() {
    return !!getRig()?.sourceRigs?.some(sourceRig =>
      sourceRig.physicsRig?.physicsState);
  }

  function updatePoseFrameCache(rig = getRig(), transforms = rig?.poseTransforms) {
    if (!rig || !transforms) return false;
    const seen = new Set();
    for (const joint of rig.joints || []) {
      const jointId = Number(joint.jointId);
      if (!Number.isInteger(jointId)) continue;
      const parentId = parentForJoint(jointId, rig);
      const parentTransform = parentId === null
        ? RIG_IDENTITY_MATRIX : transforms.get(parentId) || RIG_IDENTITY_MATRIX;
      const pivotValues = rig.jointPivotByJointId.get(jointId)
        || (parentId !== null ? rig.centerByJointId.get(parentId) : null)
        || rig.centerByJointId.get(jointId) || [0, 0, 0];
      const centerValues = rig.centerByJointId.get(jointId) || [0, 0, 0];
      const frame = rig.poseFrameCache.get(jointId) || {
        center: new THREE.Vector3(),
        pivot: new THREE.Vector3(),
      };
      frame.center.fromArray(centerValues).applyMatrix4(
        transforms.get(jointId) || RIG_IDENTITY_MATRIX);
      frame.pivot.fromArray(pivotValues).applyMatrix4(parentTransform);
      rig.poseFrameCache.set(jointId, frame);
      seen.add(jointId);
    }
    for (const jointId of rig.poseFrameCache.keys()) {
      if (!seen.has(jointId)) rig.poseFrameCache.delete(jointId);
    }
    return true;
  }

  function updateModelSourceAliases(rig) {
    for (const sourceRig of rig.sourceRigs || []) {
      const transforms = rig.sourceTransformAliases.get(sourceRig.sourceKey)
        || new Map();
      const rotations = rig.sourceRotationAliases.get(sourceRig.sourceKey)
        || new Map();
      const manualTransforms = rig.manualPoseTransforms || new Map();
      transforms.clear();
      rotations.clear();
      for (const boneId of sourceRig.boneIds || []) {
        const jointId = modelJointIdForSourceBone(sourceRig.sourceKey, boneId);
        const manual = Number.isInteger(jointId)
          ? manualTransforms.get(jointId) : null;
        const modelTransform = Number.isInteger(jointId)
          ? rig.poseTransforms.get(jointId) : null;
        const transform = modelTransform || manual;
        if (!transform) continue;
        transforms.set(Number(boneId), transform);
        rotations.set(Number(boneId),
          new THREE.Quaternion().setFromRotationMatrix(transform).normalize());
      }
      rig.sourceTransformAliases.set(sourceRig.sourceKey, transforms);
      rig.sourceRotationAliases.set(sourceRig.sourceKey, rotations);
      sourceRig.modelTransformAliasByBoneId = transforms;
      sourceRig.modelRotationAliasByBoneId = rotations;
    }
  }

  function buildModelPoseTransforms() {
    const rig = getRig();
    if (!rig) return new Map();
    const manualTransforms = buildForestTransformsFromLocalRotations(
      rig.inferredForest, rig.centerByJointId, {
        getQuaternion: jointId => rig.poseRotationByJointId.get(
          jointId) || new THREE.Quaternion(),
        jointPivotByBoneId: rig.jointPivotByJointId,
        transformCache: rig.poseTransformCache,
      });
    const driverLayer = buildHumanoidDriverBaseTransforms({
      binding: rig.humanoidBinding,
      controlRig: rig.humanoidControlRig,
      modelRig: rig,
      posedControls: state.humanoidPose,
    });
    rig.manualPoseTransforms = manualTransforms;
    rig.humanoidDriverTransforms = driverLayer.result;
    rig.humanoidDriverWorldByJointId = driverLayer.driverWorldByJointId;
    const composed = new Map();
    manualTransforms.forEach((matrix, jointId) => composed.set(jointId, matrix));
    driverLayer.result.forEach((driverDelta, jointId) => {
      const manual = manualTransforms.get(jointId) || RIG_IDENTITY_MATRIX;
      composed.set(jointId, driverDelta.clone().multiply(manual));
    });
    rig.poseTransforms = composed;
    rig.poseRotations.clear();
    composed.forEach((matrix, jointId) => {
      rig.poseRotations.set(jointId,
        new THREE.Quaternion().setFromRotationMatrix(matrix).normalize());
    });
    updatePoseFrameCache(rig, rig.poseTransforms);
    updateModelSourceAliases(rig);
    return rig.poseTransforms;
  }

  function modelPoseDescendantIds(rig, posedJointIds) {
    const affected = new Set();
    const pending = [...posedJointIds].map(Number).filter(Number.isFinite);
    while (pending.length) {
      const jointId = pending.pop();
      if (affected.has(jointId)) continue;
      affected.add(jointId);
      const component = componentForJoint(jointId, rig);
      for (const child of component?.childrenById?.[jointId] || []) {
        pending.push(Number(child));
      }
    }
    return affected;
  }

  function sourceBoneIdsForModelJoints(sourceRig, jointIds) {
    const affected = new Set();
    for (const boneId of sourceRig.boneIds || []) {
      const jointId = modelJointIdForSourceBone(sourceRig.sourceKey, boneId);
      if (Number.isInteger(jointId) && jointIds.has(jointId)) {
        affected.add(Number(boneId));
      }
    }
    return affected;
  }

  function sourceBoneKeyForSet(ids) {
    return [...ids].sort((left, right) => left - right).join(',');
  }

  function syncDerivedSourcePose(sourceRig, modelRig) {
    sourceRig.poseRotationByBoneId.clear();
    for (const boneId of sourceRig.boneIds || []) {
      const jointId = modelJointIdForSourceBone(sourceRig.sourceKey, boneId);
      const quaternion = Number.isInteger(jointId)
        ? modelRig.poseRotationByJointId.get(jointId) : null;
      if (quaternion && !quaternionIsIdentity(quaternion)) {
        sourceRig.poseRotationByBoneId.set(Number(boneId), quaternion.clone());
      }
    }
    sourceRig.poseTransforms = modelRig.sourceTransformAliases.get(
      sourceRig.sourceKey) || new Map();
    sourceRig.poseRotations = modelRig.sourceRotationAliases.get(
      sourceRig.sourceKey) || new Map();
  }

  function poseVerticesForState(stateForMesh, affectedBoneIds) {
    const mask = buildSelectedWeightMask(
      stateForMesh.indices, stateForMesh.weights, stateForMesh.influenceCount,
      affectedBoneIds);
    const vertices = [];
    mask.forEach((weight, vertex) => {
      if (weight > 0) vertices.push(vertex);
    });
    return Uint32Array.from(vertices);
  }

  function finalizeSourcePoseBounds(rig) {
    let finalized = false;
    physicsRuntime.forEachRigMesh(rig, (mesh, stateForMesh) => {
      finalized = skinningRuntime.finalizeDeformationGeometry(mesh, stateForMesh)
        || finalized;
    });
    if (finalized) invalidateShadow({request: false});
    return finalized;
  }

  function applyPose({request = true, dragging = false} = {}) {
    const rig = getRig();
    if (!rig) return false;
    buildModelPoseTransforms();
    rig.poseRevision = (rig.poseRevision || 0) + 1;
    const posedJointIds = activePoseJointIds({
      manualRotations: rig.poseRotationByJointId,
      driverTransforms: rig.humanoidDriverTransforms,
      quaternionIsIdentity,
    });
    const poseJointKey = posedJointIds.sort((left, right) => left - right)
      .join(',');
    const affectedJointIds = rig.poseActiveJointKey === poseJointKey
      ? rig.poseAffectedJointIds : modelPoseDescendantIds(rig, posedJointIds);
    const affectedSetChanged = rig.poseActiveJointKey !== poseJointKey;
    let changed = false;
    for (const sourceRig of rig.sourceRigs || []) {
      syncDerivedSourcePose(sourceRig, rig);
      const transformsByBoneId = rig.sourceTransformAliases.get(
        sourceRig.sourceKey) || new Map();
      const rotationsByBoneId = rig.sourceRotationAliases.get(
        sourceRig.sourceKey) || new Map();
      const affectedBoneIds = sourceBoneIdsForModelJoints(
        sourceRig, affectedJointIds);
      physicsRuntime.forEachRigMesh(sourceRig, (mesh, stateForMesh) => {
        const previousBoneKey = rig.poseSourceBoneIdsByMesh.get(mesh) || '';
        const boneKey = sourceBoneKeyForSet(affectedBoneIds);
        const activeVertices = !affectedSetChanged
            && previousBoneKey === boneKey
            && rig.poseActiveVerticesByMesh.has(mesh)
          ? rig.poseActiveVerticesByMesh.get(mesh)
          : affectedBoneIds.size
            ? poseVerticesForState(stateForMesh, affectedBoneIds)
            : new Uint32Array();
        stateForMesh.poseActiveVertices = activeVertices;
        stateForMesh.poseTransforms = transformsByBoneId;
        stateForMesh.poseRotations = rotationsByBoneId;
        rig.poseActiveVerticesByMesh.set(mesh, activeVertices);
        rig.poseSourceBoneIdsByMesh.set(mesh, boneKey);
      });
      const physicsRig = sourceRig.physicsRig;
      if (physicsRig?.physicsState) {
        physicsRuntime.refreshParticipantDerivedState(
          [...physicsRig.meshes][0], physicsRig,
          modelPhysicsSession.getSettings());
        changed = physicsRuntime.applySourceDeformation(physicsRig,
          {visibleOnly: false}) || changed;
      } else {
        physicsRuntime.forEachRigMesh(sourceRig, (mesh, stateForMesh) => {
          changed = skinningRuntime.applyDeformation(mesh, stateForMesh, {
            request: false, invalidateShadow: false, skipHidden: false,
          }) || changed;
        });
      }
    }
    rig.poseAffectedJointIds = affectedJointIds;
    rig.poseActiveJointKey = poseJointKey;
    if (changed) invalidateShadow({request: false});
    if (!dragging && !hasActivePhysics()) {
      for (const sourceRig of rig.sourceRigs || []) finalizeSourcePoseBounds(sourceRig);
    }
    if (hasActivePhysics() && !state.humanoidRigEditPhysicsSuspended) {
      modelPhysicsSession.wake();
    }
    if (request) requestRender();
    return changed;
  }

  function setHumanoidEditPhysicsSuspended(value) {
    const suspended = !!value;
    state.humanoidRigEditPhysicsSuspended = suspended;
    modelPhysicsSession.setSuspended?.(suspended);
    if (suspended && modelPhysicsSession.getState().enabled) {
      // Clear pre-edit spring offsets while the session is already suspended;
      // otherwise resuming could briefly reapply stale posed deformation.
      modelPhysicsSession.reset(getModelTransformState());
    }
    const rig = getRig();
    if (!rig) return suspended;
    for (const sourceRig of rig.sourceRigs || []) {
      const physicsRig = sourceRig.physicsRig;
      if (!physicsRig?.physicsState) continue;
      if (suspended) {
        physicsRuntime.forEachRigMesh(sourceRig, (mesh, stateForMesh) => {
          stateForMesh.physicsEnabled = false;
          stateForMesh.deformationMode = null;
          stateForMesh.combinedActiveVerticesRef = null;
          stateForMesh.combinedPoseVerticesRef = null;
          stateForMesh.combinedPhysicsVerticesRef = null;
          skinningRuntime.applyDeformation(mesh, stateForMesh, {
            request: false, invalidateShadow: false, skipHidden: false,
          });
        });
      } else {
        physicsRuntime.syncRigParticipantState(physicsRig);
        physicsRuntime.applySourceDeformation(physicsRig, {visibleOnly: false});
      }
    }
    if (!suspended && hasActivePhysics()) modelPhysicsSession.wake();
    return suspended;
  }

  function resetForHumanoidEdit({request = false} = {}) {
    const rig = getRig();
    if (!rig) return false;
    const hadPose = rig.poseRotationByJointId.size > 0
      || Object.keys(state.humanoidPose || {}).length > 0
      || rig.poseActiveJointKey !== '';
    rig.poseRotationByJointId.clear();
    state.humanoidPose = {};
    const changed = applyPose({request});
    rig.poseActiveVerticesByMesh.clear();
    rig.poseSourceBoneIdsByMesh.clear();
    rig.poseTransformCache.clear();
    rig.poseFrameCache.clear();
    rig.poseActiveJointKey = '';
    rig.poseAffectedJointIds = new Set();
    return changed || hadPose;
  }

  function clearManualPose({request = false} = {}) {
    const rig = getRig();
    if (!rig) return false;
    const hadPose = rig.poseRotationByJointId.size > 0
      || rig.poseActiveJointKey !== '';
    rig.poseRotationByJointId.clear();
    if (!hadPose) return false;
    const changed = applyPose({request});
    rig.poseActiveVerticesByMesh.clear();
    rig.poseSourceBoneIdsByMesh.clear();
    return changed || hadPose;
  }
  function getFrame(jointId) {
    const id = Number(jointId);
    const rig = getRig();
    const joint = jointForId(id);
    if (!rig || !joint || !rig.componentByJointId.has(id)) return null;
    const cache = rig.poseFrameCache.get(id);
    const parentId = parentForJoint(id, rig);
    const parentRotation = parentId === null ? new THREE.Quaternion()
      : rig.poseRotations.get(parentId)?.clone() || new THREE.Quaternion();
    const boneRotation = rig.poseRotations.get(id)?.clone()
      || new THREE.Quaternion();
    const restRotation = rig.restFrameByJointId.get(id)?.clone()
      || new THREE.Quaternion();
    return {
      pivot: (cache?.pivot || new THREE.Vector3(...(
        rig.jointPivotByJointId.get(id) || joint.restPivot || [0, 0, 0])))
        .toArray(),
      center: (cache?.center || new THREE.Vector3(...(
        rig.centerByJointId.get(id) || joint.restCenter || [0, 0, 0])))
        .toArray(),
      parentRotation: parentRotation.normalize().toArray(),
      boneRotation: boneRotation.normalize().toArray(),
      restRotation: restRotation.normalize().toArray(),
      gizmoRotation: boneRotation.clone().multiply(restRotation).normalize()
        .toArray(),
    };
  }

  function setRotations(rotationsByJointId, options = {}) {
    const rig = getRig();
    if (!rig || !state.loaded) return false;
    const updates = [];
    const seen = new Set();
    for (const [rawId, value] of rotationEntries(rotationsByJointId)) {
      const jointId = Number(rawId);
      const joint = jointForId(jointId);
      const component = componentForJoint(jointId);
      if (!Number.isInteger(jointId) || !joint || !component
          || seen.has(jointId)) {
        state.pickStatus = weightRigStatus(
          'weightRig.status.couldNotApplyRigPose');
        notifyChanged();
        return false;
      }
      if (Number(component.rootId) === jointId) {
        state.pickStatus = weightRigStatus(
          'weightRig.status.rootCannotRotate');
        notifyChanged();
        return false;
      }
      const quaternion = strictQuaternion(value);
      if (!quaternion) {
        state.pickStatus = weightRigStatus(
          'weightRig.status.couldNotApplyRigPose');
        notifyChanged();
        return false;
      }
      seen.add(jointId);
      updates.push([jointId, quaternion]);
    }
    if (!updates.length) return false;

    updates.forEach(([jointId, quaternion]) => {
      rig.poseRotationByJointId.set(jointId, quaternion);
    });
    const selectedId = options?.selectedJointId === null
      || options?.selectedJointId === undefined
      ? null : Number(options.selectedJointId);
    if (Number.isInteger(selectedId) && jointForId(selectedId)) {
      state.selectedJointId = selectedId;
    }
    const dragging = options?.dragging === true;
    applyPose({dragging});
    state.pickStatus = '';
    if (dragging) {
      const selectedJoint = jointForId(state.selectedJointId);
      const member = representativeMember(selectedJoint);
      if (member) notifyPoseChanged(
        sourceRigForKey(member.sourceKey), member.boneId,
        updates.map(([id]) => id));
      else notifyChanged();
    } else {
      notifyChanged();
    }
    return true;
  }

  function setRotation(jointId, quaternion, options = {}) {
    const joint = jointForId(jointId);
    const member = representativeMember(joint);
    const sourceRig = member && sourceRigForKey(member.sourceKey);
    const sourceComponent = member && sourceComponentForBone(
      sourceRig, member.boneId);
    const resolvedJointId = member && modelJointIdForSourceBone(
      member.sourceKey, member.boneId);
    const componentId = Number(componentForJoint(resolvedJointId)?.componentId);
    const modelComponent = Number.isInteger(componentId)
      ? getRig()?.components?.[componentId] : null;
    if (!sourceRig || !sourceComponent || !Number.isInteger(resolvedJointId)
        || !modelComponent || !sourceRig.boneIds.includes(Number(member.boneId))) {
      return false;
    }
    if (Number(modelComponent.rootId) === resolvedJointId) {
      state.pickStatus = weightRigStatus(
        'weightRig.status.rootCannotRotate');
      notifyChanged();
      return false;
    }
    return setRotations(new Map([[resolvedJointId, quaternion]]), {
      ...options, selectedJointId: resolvedJointId,
    });
  }

  return {
    getFrame,
    updatePoseFrameCache,
    applyPose,
    hasActivePhysics,
    getModelJointId: modelJointIdForSourceBone,
    finishPose(jointId) {
      const member = representativeMember(jointForId(jointId));
      if (!member) return false;
      const rig = sourceRigForKey(member.sourceKey);
      const id = Number(member.boneId);
      const resolvedJointId = modelJointIdForSourceBone(member.sourceKey, id);
      if (!rig || !Number.isInteger(id) || !rig.boneIds.includes(id)
          || !Number.isInteger(resolvedJointId)) return false;
      if (!hasActivePhysics()) {
        for (const sourceRig of getRig()?.sourceRigs || []) {
          finalizeSourcePoseBounds(sourceRig);
        }
      }
      if (hasActivePhysics()) modelPhysicsSession.wake();
      notifyChanged();
      requestRender();
      return true;
    },
    resetJoint(jointId) {
      const member = representativeMember(jointForId(jointId));
      if (!member) return false;
      const rig = sourceRigForKey(member.sourceKey);
      const id = Number(member.boneId);
      const resolvedJointId = modelJointIdForSourceBone(member.sourceKey, id);
      if (!rig || !rig.boneIds.includes(id)
          || !Number.isInteger(resolvedJointId)) return false;
      getRig()?.poseRotationByJointId.delete(resolvedJointId);
      applyPose();
      notifyChanged();
      return true;
    },
    resetPose() {
      const changed = resetModelPose({request: false});
      const presetWasSelected = rigPresetState.selectedPresetId !== null
        || rigPresetState.lastApplyResult !== null;
      rigPresetState.selectedPresetId = null;
      rigPresetState.lastApplyResult = null;
      state.pickStatus = '';
      notifyChanged();
      requestRender();
      return changed || presetWasSelected;
    },
    setRoot(jointId) {
      const member = representativeMember(jointForId(jointId));
      return member ? setComponentRoot(member.sourceKey, member.boneId) : false;
    },
    setHumanoidEditPhysicsSuspended,
    resetForHumanoidEdit,
    clearManualPose,
    setRotation,
    setStatus(message = '') {
      state.pickStatus = message && typeof message === 'object'
        && message.messageKey
        ? message : String(message || '');
      notifyChanged();
      return state.pickStatus;
    },
  };
}
