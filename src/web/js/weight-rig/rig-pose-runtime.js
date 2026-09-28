// Owns model pose validation, transform construction and deformation.

import * as THREE from 'three';
import { buildHumanoidDriverBaseTransforms } from './humanoid-rig-binding.js';
import { buildInferredRigRestFrames, rebuildModelRestFrames } from './weight-rig-frames.js';
import { orientModelRigForest } from './weight-rig-reconcile.js';
import { buildJointSignatureIndex } from './weight-rig-presets.js';
import { weightRigStatus } from './weight-rig-status.js';
import { buildForestTransformsFromLocalRotations } from './weight-deformation.js';
import { buildSelectedWeightMask } from './weight-selection.js';
import { activePoseJointIds, RIG_LIMB_ROLES } from './weight-runtime.js';
import { HUMANOID_CONTROL_KEYS, HUMANOID_CONTROL_LIMB_ROLES } from './humanoid-control-rig.js';
import { buildInferredRigForest, jointPivotMap } from './weight-rig.js';

const RIG_IDENTITY_MATRIX = new THREE.Matrix4();

function strictQuaternion(value) {
  const values = value?.isQuaternion
    ? [value.x, value.y, value.z, value.w]
    : Array.isArray(value) || ArrayBuffer.isView(value)
      ? [...value].slice(0, 4).map(Number)
      : [value?.x, value?.y, value?.z, value?.w].map(Number);
  if (values.length !== 4 || !values.every(Number.isFinite)) return null;
  const quaternion = new THREE.Quaternion(...values);
  if (!Number.isFinite(quaternion.lengthSq()) || quaternion.lengthSq() <= 1e-12) return null;
  return quaternion.normalize();
}

function rotationEntries(rotationsByJointId) {
  if (rotationsByJointId instanceof Map) return [...rotationsByJointId.entries()];
  return Object.entries(rotationsByJointId || {});
}

export function rebuildSourceRigRestFrames(rig, nextStructureRevision = () => (rig.structureRevision || 0) + 1) {
  const frames = buildInferredRigRestFrames(rig.inferredForest, rig.centerByBoneId, rig.jointPivotByBoneId);
  rig.restFrameByBoneId = frames.frameByBoneId;
  rig.restDirectionByBoneId = frames.directionByBoneId;
  rig.restFrameEvidenceByBoneId = frames.evidenceByBoneId;
  rig.continuationChildByBoneId = frames.continuationChildByBoneId;
  rig.poseFrameCache?.clear();
  rig.structureRevision = nextStructureRevision();
  return frames;
}

export function cloneModelComponent(component) {
  return {
    componentId: component.componentId,
    rootId: component.rootId,
    nodeIds: [...(component.nodeIds || [])],
    parentById: { ...(component.parentById || {}) },
    childrenById: Object.fromEntries(
      Object.entries(component.childrenById || {}).map(([id, children]) => [id, [...children]]),
    ),
    depthById: { ...(component.depthById || {}) },
    maxDepth: component.maxDepth,
    edges: (component.edges || []).map((edge) => ({ ...edge })),
  };
}

export function cloneSourceForest(forest) {
  return {
    ...forest,
    components: (forest?.components || []).map(cloneModelComponent),
    componentByBoneId: { ...(forest?.componentByBoneId || {}) },
    edges: (forest?.edges || []).map((edge) => ({ ...edge })),
    nodeIds: [...(forest?.nodeIds || [])],
  };
}

export function createRigPoseRuntime({
  state,
  getRig,
  sourceSkinningRigs,
  skinningRuntime,
  physicsRuntime,
  modelPhysicsSession,
  getModelTransformState,
  invalidateShadow,
  quaternionIsIdentity,
  getModelJointId,
  hasActivePhysics,
  nextStructureRevision,
  notifyChanged,
  notifyPoseChanged,
  requestRender,
  getPrimaryLimb,
  solveControlIk,
  mergeLimbPose,
} = {}) {
  const allocateStructureRevision =
    typeof nextStructureRevision === 'function' ? nextStructureRevision : () => (getRig()?.structureRevision || 0) + 1;

  function jointForId(jointId) {
    const id = Number(jointId);
    const rig = getRig();
    return Number.isInteger(id) ? rig?.joints?.[id] || null : null;
  }

  function componentForJoint(jointId, rig = getRig()) {
    const id = Number(jointId);
    const componentId = rig?.componentByJointId?.get?.(id);
    return Number.isInteger(Number(componentId)) ? rig?.components?.[Number(componentId)] || null : null;
  }

  function sourceRigForKey(sourceKeyValue) {
    return sourceSkinningRigs?.get(String(sourceKeyValue)) || null;
  }

  function sourceComponentForBone(rig, boneId) {
    const componentId = rig?.inferredForest?.componentByBoneId?.[boneId];
    return Number.isInteger(Number(componentId))
      ? rig?.inferredForest?.components?.[Number(componentId)] || null
      : null;
  }

  function clearSourcePose(rig) {
    rig?.poseRotationByBoneId?.clear();
    rig?.poseTransforms?.clear();
    rig?.poseRotations?.clear();
    rig?.poseTransformCache?.clear();
    rig?.poseFrameCache?.clear();
  }

  function restoreDefaultSourceRigOrientation(rig) {
    if (!rig?.defaultInferredForest) return false;
    rig.inferredForest = cloneSourceForest(rig.defaultInferredForest);
    rig.jointPivotByBoneId = new Map([...rig.defaultJointPivotByBoneId].map(([boneId, pivot]) => [boneId, [...pivot]]));
    rig.poseRootOverrides = new Map();
    rebuildSourceRigRestFrames(rig, allocateStructureRevision);
    return true;
  }

  function restoreDefaultSourceRigOrientations() {
    return (getRig()?.sourceRigs || []).map(restoreDefaultSourceRigOrientation).some(Boolean);
  }

  function defaultRootOverrides(rig) {
    return new Map(
      (rig.defaultComponents || []).map((component) => [Number(component.componentId), Number(component.rootId)]),
    );
  }

  function installModelForest(rig, forest, { restoreDefaults = false } = {}) {
    rig.components = forest.components;
    rig.componentByJointId = forest.componentByJointId;
    rig.inferredForest = {
      components: forest.components,
      componentByBoneId: forest.componentByJointId,
    };
    rebuildModelRestFrames(rig, forest);
    if (restoreDefaults) {
      rig.jointPivotByJointId = new Map(
        [...rig.defaultJointPivotByJointId].map(([jointId, pivot]) => [jointId, [...pivot]]),
      );
      rig.restFrameByJointId = new Map(
        [...rig.defaultRestFrameByJointId].map(([jointId, frame]) => [jointId, frame.clone()]),
      );
      rig.restDirectionByJointId = new Map(
        [...rig.defaultRestDirectionByJointId].map(([jointId, direction]) => [
          jointId,
          direction ? [...direction] : null,
        ]),
      );
      rig.restContinuationChildByJointId = new Map(rig.defaultRestContinuationChildByJointId);
      (rig.joints || []).forEach((joint) => {
        const jointId = Number(joint.jointId);
        const pivot = rig.defaultJointPivotByJointId.get(jointId);
        const frame = rig.defaultRestFrameByJointId.get(jointId);
        const direction = rig.defaultRestDirectionByJointId.get(jointId);
        if (pivot) joint.restPivot = [...pivot];
        if (frame) joint.restFrame = frame.toArray();
        if (direction) joint.restDirection = [...direction];
      });
    }
    rig.poseTransformCache.clear();
    rig.poseFrameCache.clear();
    rig.poseActiveJointKey = null;
    rig.poseAffectedJointIds = new Set();
  }

  function applyRootSignatures(rig, signatures = [], { restoreDefaults = false, updateRevision = true } = {}) {
    const oldRoots = new Map(
      (rig.components || []).map((component) => [Number(component.componentId), Number(component.rootId)]),
    );
    const overrides = defaultRootOverrides(rig);
    const usedComponents = new Set();
    const appliedRoots = [];
    const skipped = [];
    const signatureIndex = buildJointSignatureIndex(rig);
    for (const signature of signatures) {
      const jointId = signatureIndex.resolvedBySignature.get(signature);
      if (!Number.isInteger(jointId) || signatureIndex.ambiguousSignatures.has(signature)) {
        skipped.push({ type: 'root', jointSignature: signature, reason: 'root_not_found' });
        continue;
      }
      const componentId = rig.defaultComponentByJointId.get(jointId);
      if (!Number.isInteger(Number(componentId))) {
        skipped.push({ type: 'root', jointSignature: signature, reason: 'root_not_found' });
        continue;
      }
      if (usedComponents.has(Number(componentId))) {
        skipped.push({ type: 'root', jointSignature: signature, reason: 'duplicate_root_entry' });
        continue;
      }
      usedComponents.add(Number(componentId));
      overrides.set(Number(componentId), jointId);
      appliedRoots.push(signature);
    }
    const forest = orientModelRigForest(rig.joints, rig.edges, overrides);
    const newRoots = new Map(
      (forest.components || []).map((component) => [Number(component.componentId), Number(component.rootId)]),
    );
    const rootChanged = [...new Set([...oldRoots.keys(), ...newRoots.keys()])].some(
      (componentId) => oldRoots.get(componentId) !== newRoots.get(componentId),
    );
    installModelForest(rig, forest, { restoreDefaults });
    if (updateRevision && rootChanged) {
      rig.structureRevision = allocateStructureRevision();
      state.structureRevision = rig.structureRevision;
    }
    return { forest, overrides, appliedRoots, skipped, rootChanged };
  }

  function setActiveLimbRole(role) {
    const next = RIG_LIMB_ROLES.includes(role) ? role : null;
    if (!next || state.activeLimbRole === next) return next || false;
    state.activeLimbRole = next;
    notifyChanged();
    requestRender();
    return next;
  }

  function setIkEnabled(enabled) {
    const wasEnabled = state.ikEnabled === true;
    const validationRole = enabled && !wasEnabled ? 'left_arm' : state.activeLimbRole;
    const primary = getPrimaryLimb(validationRole);
    if (enabled && !primary.available) {
      state.ikEnabled = false;
      notifyChanged();
      requestRender();
      return false;
    }
    const next = !!enabled && primary.available;
    if (state.ikEnabled === next) return next;
    state.ikEnabled = next;
    if (next && !wasEnabled) {
      state.activeLimbRole = 'left_arm';
      state.selectedHumanoidControlKey = 'leftHand';
    }
    notifyChanged();
    requestRender();
    return next;
  }

  function selectControl(controlKey) {
    if (!state.ikEnabled || !HUMANOID_CONTROL_KEYS.includes(controlKey)) return false;
    const role = HUMANOID_CONTROL_LIMB_ROLES[controlKey] || null;
    const changed = state.selectedHumanoidControlKey !== controlKey || (role && state.activeLimbRole !== role);
    state.selectedHumanoidControlKey = controlKey;
    if (role) state.activeLimbRole = role;
    if (changed) {
      notifyChanged();
      requestRender();
    }
    return true;
  }

  function solveTarget(target, options = {}) {
    const rig = getRig();
    const primary = getPrimaryLimb();
    if (!rig?.humanoidControlRig?.accepted || !state.ikEnabled || !primary.available) return false;
    const previousPose = state.humanoidPose || {};
    const solved = solveControlIk({
      controlRig: rig.humanoidControlRig,
      posedControls: previousPose,
      role: primary.role,
      target,
      bendSign: primary.bendSign,
    });
    if (!solved.positions) return solved;
    state.humanoidPose = mergeLimbPose(previousPose, solved.positions, primary.keys);
    const applied = applyPose({ dragging: options?.dragging === true });
    if (!options?.dragging) notifyChanged();
    return { ...solved, applied, controlRig: 'humanoid' };
  }

  function representativeMember(joint) {
    return joint?.representativeMember || joint?.members?.[0] || null;
  }

  function buildModelPoseTransforms() {
    const rig = getRig();
    const manualTransforms = buildForestTransformsFromLocalRotations(rig.inferredForest, rig.centerByJointId, {
      getQuaternion: (jointId) => rig.poseRotationByJointId.get(jointId) || new THREE.Quaternion(),
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
      rig.poseRotations.set(jointId, new THREE.Quaternion().setFromRotationMatrix(matrix).normalize());
    });
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

  function syncDerivedSourcePose(sourceRig, modelRig, affectedJointIds) {
    const transforms = modelRig.sourceTransformAliases.get(sourceRig.sourceKey) || new Map();
    const rotations = modelRig.sourceRotationAliases.get(sourceRig.sourceKey) || new Map();
    const manualTransforms = modelRig.manualPoseTransforms || new Map();
    const affectedBoneIds = new Set();
    transforms.clear();
    rotations.clear();
    sourceRig.poseRotationByBoneId.clear();
    for (const boneId of sourceRig.boneIds || []) {
      const jointId = getModelJointId(sourceRig.sourceKey, boneId);
      const quaternion = Number.isInteger(jointId) ? modelRig.poseRotationByJointId.get(jointId) : null;
      if (quaternion && !quaternionIsIdentity(quaternion)) {
        sourceRig.poseRotationByBoneId.set(Number(boneId), quaternion.clone());
      }
      const manual = Number.isInteger(jointId) ? manualTransforms.get(jointId) : null;
      const modelTransform = Number.isInteger(jointId) ? modelRig.poseTransforms.get(jointId) : null;
      const transform = modelTransform || manual;
      if (transform) {
        transforms.set(Number(boneId), transform);
        rotations.set(Number(boneId), new THREE.Quaternion().setFromRotationMatrix(transform).normalize());
      }
      if (Number.isInteger(jointId) && affectedJointIds.has(jointId)) {
        affectedBoneIds.add(Number(boneId));
      }
    }
    modelRig.sourceTransformAliases.set(sourceRig.sourceKey, transforms);
    modelRig.sourceRotationAliases.set(sourceRig.sourceKey, rotations);
    sourceRig.modelTransformAliasByBoneId = transforms;
    sourceRig.modelRotationAliasByBoneId = rotations;
    sourceRig.poseTransforms = transforms;
    sourceRig.poseRotations = rotations;
    return affectedBoneIds;
  }

  function poseVerticesForState(stateForMesh, affectedBoneIds) {
    const mask = buildSelectedWeightMask(
      stateForMesh.indices,
      stateForMesh.weights,
      stateForMesh.influenceCount,
      affectedBoneIds,
    );
    const vertices = [];
    mask.forEach((weight, vertex) => {
      if (weight > 0) vertices.push(vertex);
    });
    return Uint32Array.from(vertices);
  }

  function finalizeSourcePoseBounds(rig) {
    let finalized = false;
    physicsRuntime.forEachRigMesh(rig, (mesh, stateForMesh) => {
      finalized = skinningRuntime.finalizeDeformationGeometry(mesh, stateForMesh) || finalized;
    });
    if (finalized) invalidateShadow({ request: false });
    return finalized;
  }

  function applyPose({ request = true, dragging = false } = {}) {
    const rig = getRig();
    if (!rig) return false;
    buildModelPoseTransforms();
    rig.poseRevision = (rig.poseRevision || 0) + 1;
    const posedJointIds = activePoseJointIds({
      manualRotations: rig.poseRotationByJointId,
      driverTransforms: rig.humanoidDriverTransforms,
      quaternionIsIdentity,
    });
    const poseJointKey = posedJointIds.sort((left, right) => left - right).join(',');
    const affectedJointIds =
      rig.poseActiveJointKey === poseJointKey ? rig.poseAffectedJointIds : modelPoseDescendantIds(rig, posedJointIds);
    const affectedSetChanged = rig.poseActiveJointKey !== poseJointKey;
    let changed = false;
    for (const sourceRig of rig.sourceRigs || []) {
      const affectedBoneIds = syncDerivedSourcePose(sourceRig, rig, affectedJointIds);
      const transformsByBoneId = rig.sourceTransformAliases.get(sourceRig.sourceKey) || new Map();
      const rotationsByBoneId = rig.sourceRotationAliases.get(sourceRig.sourceKey) || new Map();
      physicsRuntime.forEachRigMesh(sourceRig, (mesh, stateForMesh) => {
        const previousBoneKey = rig.poseSourceBoneIdsByMesh.get(mesh) || '';
        const boneKey = [...affectedBoneIds].sort((left, right) => left - right).join(',');
        const activeVertices =
          !affectedSetChanged && previousBoneKey === boneKey && rig.poseActiveVerticesByMesh.has(mesh)
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
          [...physicsRig.meshes][0],
          physicsRig,
          modelPhysicsSession.getSettings(),
        );
        changed = physicsRuntime.applySourceDeformation(physicsRig, { visibleOnly: false }) || changed;
      } else {
        physicsRuntime.forEachRigMesh(sourceRig, (mesh, stateForMesh) => {
          changed =
            skinningRuntime.applyDeformation(mesh, stateForMesh, {
              request: false,
              invalidateShadow: false,
              skipHidden: false,
            }) || changed;
        });
      }
    }
    rig.poseAffectedJointIds = affectedJointIds;
    rig.poseActiveJointKey = poseJointKey;
    if (changed) invalidateShadow({ request: false });
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
            request: false,
            invalidateShadow: false,
            skipHidden: false,
          });
        });
      } else {
        physicsRuntime.syncRigParticipantState(physicsRig);
        physicsRuntime.applySourceDeformation(physicsRig, { visibleOnly: false });
      }
    }
    if (!suspended && hasActivePhysics()) modelPhysicsSession.wake();
    return suspended;
  }

  function resetForHumanoidEdit({ request = false } = {}) {
    const rig = getRig();
    if (!rig) return false;
    const hadPose =
      rig.poseRotationByJointId.size > 0 ||
      Object.keys(state.humanoidPose || {}).length > 0 ||
      rig.poseActiveJointKey !== '';
    rig.poseRotationByJointId.clear();
    state.humanoidPose = {};
    const changed = applyPose({ request });
    rig.poseActiveVerticesByMesh.clear();
    rig.poseSourceBoneIdsByMesh.clear();
    rig.poseTransformCache.clear();
    rig.poseFrameCache.clear();
    rig.poseActiveJointKey = '';
    rig.poseAffectedJointIds = new Set();
    return changed || hadPose;
  }

  function clearManualPose({ request = false } = {}) {
    const rig = getRig();
    if (!rig) return false;
    const hadPose = rig.poseRotationByJointId.size > 0 || rig.poseActiveJointKey !== '';
    rig.poseRotationByJointId.clear();
    if (!hadPose) return false;
    const changed = applyPose({ request });
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
    const component = componentForJoint(id, rig);
    const parent = component?.parentById?.[id];
    const parentId = parent === null || parent === undefined ? null : Number(parent);
    const parentRotation =
      parentId === null ? new THREE.Quaternion() : rig.poseRotations.get(parentId)?.clone() || new THREE.Quaternion();
    const boneRotation = rig.poseRotations.get(id)?.clone() || new THREE.Quaternion();
    const restRotation = rig.restFrameByJointId.get(id)?.clone() || new THREE.Quaternion();
    return {
      pivot: (
        cache?.pivot || new THREE.Vector3(...(rig.jointPivotByJointId.get(id) || joint.restPivot || [0, 0, 0]))
      ).toArray(),
      center: (
        cache?.center || new THREE.Vector3(...(rig.centerByJointId.get(id) || joint.restCenter || [0, 0, 0]))
      ).toArray(),
      parentRotation: parentRotation.normalize().toArray(),
      boneRotation: boneRotation.normalize().toArray(),
      restRotation: restRotation.normalize().toArray(),
      gizmoRotation: boneRotation.clone().multiply(restRotation).normalize().toArray(),
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
      if (!Number.isInteger(jointId) || !joint || !component || seen.has(jointId)) {
        state.pickStatus = weightRigStatus('weightRig.status.couldNotApplyRigPose');
        notifyChanged();
        return false;
      }
      if (Number(component.rootId) === jointId) {
        state.pickStatus = weightRigStatus('weightRig.status.rootCannotRotate');
        notifyChanged();
        return false;
      }
      const quaternion = strictQuaternion(value);
      if (!quaternion) {
        state.pickStatus = weightRigStatus('weightRig.status.couldNotApplyRigPose');
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
    const selectedId =
      options?.selectedJointId === null || options?.selectedJointId === undefined
        ? null
        : Number(options.selectedJointId);
    if (Number.isInteger(selectedId) && jointForId(selectedId)) {
      state.selectedJointId = selectedId;
    }
    const dragging = options?.dragging === true;
    applyPose({ dragging });
    state.pickStatus = '';
    if (dragging) {
      const selectedJoint = jointForId(state.selectedJointId);
      const member = representativeMember(selectedJoint);
      if (member)
        notifyPoseChanged(
          sourceRigForKey(member.sourceKey),
          member.boneId,
          updates.map(([id]) => id),
        );
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
    const sourceComponent =
      sourceRig?.inferredForest?.components?.[sourceRig?.inferredForest?.componentByBoneId?.[member?.boneId]];
    const resolvedJointId = member && getModelJointId(member.sourceKey, member.boneId);
    const componentId = Number(componentForJoint(resolvedJointId)?.componentId);
    const modelComponent = Number.isInteger(componentId) ? getRig()?.components?.[componentId] : null;
    if (
      !sourceRig ||
      !sourceComponent ||
      !Number.isInteger(resolvedJointId) ||
      !modelComponent ||
      !sourceRig.boneIds.includes(Number(member.boneId))
    ) {
      return false;
    }
    if (Number(modelComponent.rootId) === resolvedJointId) {
      state.pickStatus = weightRigStatus('weightRig.status.rootCannotRotate');
      notifyChanged();
      return false;
    }
    return setRotations(new Map([[resolvedJointId, quaternion]]), {
      ...options,
      selectedJointId: resolvedJointId,
    });
  }

  function resetPose({ request = true } = {}) {
    const rig = getRig();
    if (!rig) return false;
    rig.poseRotationByJointId.clear();
    state.humanoidPose = {};
    restoreDefaultSourceRigOrientations();
    applyRootSignatures(rig, [], { restoreDefaults: true });
    state.explicitRootSignatures.clear();
    const changed = applyPose({ request });
    rig.poseActiveVerticesByMesh.clear();
    rig.poseSourceBoneIdsByMesh.clear();
    state.pickStatus = '';
    return changed;
  }

  function applyResolvedPreset(resolvedPreset, options = {}) {
    const rig = getRig();
    if (!rig || !state.loaded || !resolvedPreset?.success) return null;

    restoreDefaultSourceRigOrientations();
    const rootRestore = applyRootSignatures(
      rig,
      (resolvedPreset.roots || []).map((root) => root.jointSignature).filter(Boolean),
    );
    const currentJointIndex = buildJointSignatureIndex(rig).resolvedBySignature;
    state.explicitRootSignatures = new Set(
      rootRestore.appliedRoots.filter((signature) => {
        const jointId = currentJointIndex.get(signature);
        const componentId = rig.defaultComponentByJointId.get(jointId);
        return (
          Number.isInteger(Number(componentId)) && rig.defaultRootIdByComponent.get(Number(componentId)) !== jointId
        );
      }),
    );

    rig.poseRotationByJointId.clear();
    for (const entry of resolvedPreset.joints || []) {
      const rotation = entry.rotation;
      if (!Array.isArray(rotation) || rotation.length !== 4 || rotation.some((value) => !Number.isFinite(value)))
        continue;
      rig.poseRotationByJointId.set(Number(entry.jointId), strictQuaternion(rotation));
    }
    const changed = applyPose({ request: false, dragging: false });
    const skipped = [...(resolvedPreset.skipped || []), ...rootRestore.skipped];
    const result = {
      success: true,
      preset: resolvedPreset.preset,
      appliedJointCount: resolvedPreset.joints?.length || 0,
      skippedJointCount: resolvedPreset.skippedJointCount || 0,
      appliedRootCount: rootRestore.appliedRoots.length,
      skippedRootCount: (resolvedPreset.skippedRootCount || 0) + rootRestore.skipped.length,
      skipped,
      changed,
      ...(options?.presetId ? { presetId: options.presetId } : {}),
    };
    state.pickStatus = '';
    requestRender();
    return result;
  }

  function setRoot(jointId) {
    const member = representativeMember(jointForId(jointId));
    const sourceRig = member && sourceRigForKey(member.sourceKey);
    const id = Number(member?.boneId);
    const component = sourceComponentForBone(sourceRig, id);
    const rig = getRig();
    if (!sourceRig || !component || !component.nodeIds.includes(id) || !rig) {
      return false;
    }
    const resolvedJointId = getModelJointId(member.sourceKey, id);
    if (!Number.isInteger(resolvedJointId)) return false;

    clearManualPose({ request: false });
    clearSourcePose(sourceRig);
    const overrides = new Map(sourceRig.inferredForest.components.map((item) => [item.componentId, item.rootId]));
    overrides.set(component.componentId, id);
    sourceRig.inferredForest = buildInferredRigForest(sourceRig.influenceGraph, {
      rootOverrides: overrides,
    });
    sourceRig.jointPivotByBoneId = jointPivotMap(sourceRig.inferredForest, sourceRig.influenceGraph.relationships);
    rebuildSourceRigRestFrames(sourceRig, allocateStructureRevision);
    sourceRig.poseRootOverrides = overrides;

    const signature = rig.joints[resolvedJointId]?.signature;
    const targetComponentId = rig.defaultComponentByJointId.get(resolvedJointId);
    const currentIndex = buildJointSignatureIndex(rig).resolvedBySignature;
    const desiredRootSignatures = [...state.explicitRootSignatures].filter((existingSignature) => {
      const existingJointId = currentIndex.get(existingSignature);
      const existingComponentId = rig.defaultComponentByJointId.get(existingJointId);
      return Number(existingComponentId) !== Number(targetComponentId);
    });
    if (signature && rig.defaultRootIdByComponent.get(Number(targetComponentId)) !== resolvedJointId) {
      desiredRootSignatures.push(signature);
    }
    const rootRestore = applyRootSignatures(rig, desiredRootSignatures);
    const defaultComponentId = rig.defaultComponentByJointId.get(resolvedJointId);
    const appliedSignatures = new Set(rootRestore.appliedRoots);
    state.explicitRootSignatures = new Set(
      rootRestore.appliedRoots.filter((appliedSignature) => {
        const appliedJointId = currentIndex.get(appliedSignature);
        const appliedComponentId = rig.defaultComponentByJointId.get(appliedJointId);
        return rig.defaultRootIdByComponent.get(Number(appliedComponentId)) !== appliedJointId;
      }),
    );
    if (signature && Number(defaultComponentId) === Number(targetComponentId) && !appliedSignatures.has(signature)) {
      state.explicitRootSignatures.delete(signature);
    }
    state.selectedJointId = resolvedJointId;
    state.pickStatus = '';
    applyPose({ request: false });
    notifyChanged();
    requestRender();
    return true;
  }

  return {
    getFrame,
    applyPose,
    finishPose(jointId) {
      const member = representativeMember(jointForId(jointId));
      if (!member) return false;
      const rig = sourceRigForKey(member.sourceKey);
      const id = Number(member.boneId);
      const resolvedJointId = getModelJointId(member.sourceKey, id);
      if (!rig || !Number.isInteger(id) || !rig.boneIds.includes(id) || !Number.isInteger(resolvedJointId))
        return false;
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
      const resolvedJointId = getModelJointId(member.sourceKey, id);
      if (!rig || !rig.boneIds.includes(id) || !Number.isInteger(resolvedJointId)) return false;
      getRig()?.poseRotationByJointId.delete(resolvedJointId);
      applyPose();
      notifyChanged();
      return true;
    },
    resetPose,
    applyResolvedPreset,
    applyRootSignatures,
    setRoot,
    setHumanoidEditPhysicsSuspended,
    resetForHumanoidEdit,
    clearManualPose,
    setRotation,
    setActiveLimbRole,
    setIkEnabled,
    selectControl,
    solveTarget,
    setStatus(message = '') {
      state.pickStatus = message && typeof message === 'object' && message.messageKey ? message : String(message || '');
      notifyChanged();
      return state.pickStatus;
    },
  };
}
