// Shared ModelRig engine and Weight/Rig composition root. This module is
// imported at application startup; backend Weight/Rig loading remains lazy
// until the Weight tab asks for it.

import * as THREE from 'three';
import {
  camera, controls, getModelTransformState, invalidateCharacterShadowGeometry,
  renderer,
} from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';
import {
  buildForestTransformsFromLocalRotations,
} from './weight-deformation.js';
import {
  buildInferredRigRestFrames, rebuildModelRestFrames,
} from './weight-rig-frames.js';
import {
  buildModelRigReconciliation, orientModelRigForest, sourceBoneKey,
} from './weight-rig-reconcile.js';
import {GRAVITY_WORLD_DIRECTION} from './weight-physics.js';
import {
  buildSelectedWeightMask, normalizeBoneSelection,
  normalizeSelectedBoneIds, selectedBoneCount,
  serializeBoneSelection, sameBoneSelection,
} from './weight-selection.js';
import {
  buildInfluenceNodes as buildRigInfluenceNodes,
  buildInfluenceRelationships as buildRigInfluenceRelationships,
  buildInferredRigForest,
  jointPivotMap,
} from './weight-rig.js';
import { raycastModelAtClientPoint } from '../scene/model-picking.js';
import {DEFAULT_MODEL_PHYSICS_SETTINGS} from './model-physics-session.js';
import {
  createWeightPhysicsRuntime,
} from './weight-physics-runtime.js';
import {
  modelRigSnapshot,
} from './weight-rig-snapshots.js';
import {
  buildJointSignatureIndex, resolveRigPreset,
} from './weight-rig-presets.js';
import {
  getRigPresetSnapshot, initializeRigPresetSession,
  resetRigPresetSession,
} from './rig-preset-session.js';
import {
  initializeWeightModelSession,
  initializeWeightPickingSession,
  resetWeightPickingSession,
  resetWeightModelSession,
  cancelWeightModelPicking,
  clearPickedPoint,
  refreshModelWeightSummary,
  sampleModelSkinningAtIntersection,
  setSelectedBones as setWeightModelSelectedBones,
} from './weight-model-session.js';
import {
  createWeightPhysicsCoordinator, initializeWeightPhysicsController,
} from './weight-physics-controller.js';
import {initializeHumanoidPoseRuntime} from './humanoid-pose-runtime.js';
import {initializeSkinningRuntime} from './skinning-runtime.js';
import {
  cancelRigJointPicking, initializeRigModelSession, initializeRigSourceSession,
  modelJointFromSkinningSample,
} from './rig-model-session.js';
import {initializeRigPoseRuntime} from './rig-pose-runtime.js';
import {
  activePoseJointIds,
  createRigRuntimeState, createWeightRuntimeState, matrixIsIdentity,
  RIG_LIMB_ROLES, RIG_ROTATION_SNAP_DEGREES,
} from './weight-runtime.js';
import {characterAxesFromOrientation} from './humanoid-orientation.js';
import {
  buildHumanoidDriverBaseTransforms,
  buildHumanoidSourceBoneDriverTransforms,
  buildHumanoidRigBinding,
} from './humanoid-rig-binding.js';
import {
  applyHumanoidControlRigOverrides, buildHumanoidControlRig,
  resolveHumanoidControlMappings,
} from './humanoid-control-rig.js';
import {mergeHumanoidLimbPose, solveHumanoidControlIk} from './humanoid-rig-ik.js';
import {buildHumanoidHeatBinding} from './humanoid-heat-binding.js';
import {
  initializeHumanoidRigEditSession, resetHumanoidRigEditSession,
} from './humanoid-rig-edit-session.js';

const weightRuntime = createWeightRuntimeState();
const {states, knownMeshes, modelWeightState, stateFor} = weightRuntime;

function humanoidSemanticAxes({requireReady = false} = {}) {
  const orientationState = getModelTransformState?.();
  if (requireReady && orientationState?.orientationInitialized !== true) return null;
  const axes = characterAxesFromOrientation(orientationState);
  if (axes) return axes;
  if (requireReady) return null;
  return {
    up: new THREE.Vector3(0, 1, 0),
    forward: new THREE.Vector3(0, 0, 1),
    right: new THREE.Vector3(1, 0, 0),
  };
}
const rigRuntime = createRigRuntimeState();
const {modelRigState, rigPresetState} = rigRuntime;
const sourcePhysicsRigs = new Map();
const sourceSkinningRigs = new Map();
let modelSkinningRig = null;
let skinningRuntime = null;
let rigModelSession = null;
let rigPoseRuntime = null;
let physicsCoordinator = null;
let rigSourceSession = null;
let modelWeightGeneration = 0;
let humanoidControlRigCacheKey = '';
let humanoidControlRigSnapshotCache = null;
const RIG_IDENTITY_MATRIX = new THREE.Matrix4();
let humanoidRigEditSession = null;

function invalidateHumanoidDetection() {
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  modelRigState.humanoidControlRig = null;
  modelRigState.humanoidPose = {};
  modelRigState.humanoidStructureRevision = null;
}

const physicsRuntime = createWeightPhysicsRuntime({
  states,
  sourcePhysicsRigs,
  getModelSkinningRig: () => modelSkinningRig,
  applyDeformation: (...args) => skinningRuntime?.applyDeformation(...args),
  finalizePhysicsGeometry: (...args) =>
    skinningRuntime?.finalizeDeformationGeometry(...args),
  markFinalBoundsDirty: (...args) => skinningRuntime?.markFinalBoundsDirty(...args),
});
const {
  modelPhysicsSession,
  buildSelectedPhysicsForest,
  createSourcePhysicsParticipant,
  forEachRigMesh,
  refreshParticipantDerivedState,
  applySourceDeformation,
  syncRigParticipantState,
} = physicsRuntime;

physicsCoordinator = createWeightPhysicsCoordinator({
  modelPhysicsSession,
  modelWeightState,
  states,
  knownMeshes,
  sourcePhysicsRigs,
  selectedBoneCount,
  eligibleSkinningMesh,
  createSourcePhysicsRig,
  createSourcePhysicsParticipant,
  getModelTransformState,
  invalidateCharacterShadowGeometry,
  notifyModelRigChanged,
  requestRender,
  defaults: DEFAULT_MODEL_PHYSICS_SETTINGS,
});

skinningRuntime = initializeSkinningRuntime({
  states,
  knownMeshes,
  stateFor,
  modelWeightState,
  modelPhysicsSession,
  sourcePhysicsRigs,
  sourceSkinningRigs,
  modelWeightSnapshot,
  selectionMapFromEntries,
  sourceSelectionEntries,
  setSelectedBones: entries => setWeightModelSelectedBones(entries),
  syncPhysicsToSelection,
  refreshModelWeightSummary,
  refreshSelectedWeightMask,
  eligibleSkinningMesh,
  getGeneration: () => modelWeightGeneration,
  getModelRigState: () => modelRigState,
  getRigPresetState: () => rigPresetState,
  getModelSkinningRig: () => modelSkinningRig,
  setModelSkinningRig: value => { modelSkinningRig = value; },
  invalidateHumanoidDetection,
  invalidateModelRigLoad: () => rigModelSession?.invalidateLoad(),
  clearPickedPoint,
  resetModelPose,
  syncPhysicsParticipants,
  buildAllSourceSkinningRigs,
  buildModelSkinningRig: (...args) => buildModelSkinningRig(...args),
  resetModelState: resetModelWeightState,
  notifyModelWeightChanged,
  notifyModelRigChanged,
  requestRender,
  invalidateShadow: invalidateCharacterShadowGeometry,
});

rigSourceSession = initializeRigSourceSession({
  states,
  knownMeshes,
  modelWeightState,
  sourceSkinningRigs,
  ensureInfluenceGraph: (...args) =>
    skinningRuntime.ensureInfluenceGraph(...args),
  rebuildRestFrames: rebuildSourceRigRestFrames,
  cloneForest: cloneSourceForest,
});

initializeWeightPhysicsController({
  modelPhysicsSession,
  reset: () => physicsCoordinator.reset(),
});

initializeWeightPickingSession({
  modelWeightState,
  modelRigState,
  states,
  knownMeshes,
  canvas: renderer.domElement,
  camera,
  controls,
  notifyChanged: notifyModelWeightChanged,
  requestRender,
  cancelRigPicking: cancelRigJointPicking,
});

initializeRigPresetSession({
  state: rigPresetState,
  getModelRig: () => modelSkinningRig,
  getModelRigState: () => modelRigState,
  getKnownMeshes: () => knownMeshes,
  resolveRigPreset,
  applyResolvedPreset: (resolved, options) =>
    applyRigPosePreset(resolved, options),
  notifyChanged: () => notifyModelRigChanged(),
});

initializeWeightModelSession({
  modelWeightState,
  states,
  knownMeshes,
  modelWeightSnapshot,
  selectionMapFromEntries,
  sourceSelectionEntries,
  refreshSelectedWeightMask,
  updateModelWeightHeatmap: (...args) =>
    skinningRuntime.updateModelWeightHeatmap(...args),
  syncPhysicsToSelection,
  sameBoneSelection,
  serializeBoneSelection,
  eligibleSkinningMesh,
  notifyChanged: () => notifyModelWeightChanged(),
  requestRender,
  getGeneration: () => modelWeightGeneration,
});

initializeHumanoidPoseRuntime({
  modelRigState,
  getModelRig: () => modelSkinningRig,
  getPrimaryLimb: () => primaryHumanoidLimb(),
  solveControlIk: solveHumanoidControlIk,
  mergeLimbPose: mergeHumanoidLimbPose,
  applyPose: options => applyModelPose(options),
  notifyChanged: () => notifyModelRigChanged(),
  requestRender,
});

humanoidRigEditSession = initializeHumanoidRigEditSession({
  modelRigState,
  getModelRig: () => modelSkinningRig,
  getAutomaticRig: () => modelSkinningRig?.humanoidAutomaticControlRig,
  getCurrentHumanoidControlRig: () => humanoidControlRigSnapshot(),
  getModelJointPosePosition: jointId => modelSkinningRig?.poseFrameCache
    ?.get(Number(jointId))?.pivot?.toArray?.() || null,
  getKnownMeshes: () => knownMeshes,
  resolveMappings: resolveHumanoidControlMappings,
  persist: (path, value) => window.pywebview?.api
    ?.save_humanoid_control_rig?.(path, value),
  clearPersist: path => window.pywebview?.api
    ?.clear_humanoid_control_rig?.(path),
  cancelWeightPicking: cancelWeightModelPicking,
  cancelRigPicking: cancelRigJointPicking,
  rebuildActiveRig: async () => {
    if (!modelSkinningRig) return;
    buildPrimaryHumanoidRig(modelSkinningRig);
    // The old IK targets are authored against the previous control rig. Keep
    // manual ModelJoint pose intact and clear only the virtual humanoid pose.
    modelRigState.humanoidPose = {};
    applyModelPose({request: false});
  },
  notifyChanged: notifyModelRigChanged,
  requestRender,
});

rigModelSession = initializeRigModelSession({
  state: modelRigState,
  modelWeightState,
  getGeneration: () => modelWeightGeneration,
  loadModelWeights: () => skinningRuntime.loadModelWeights(),
  buildAllSourceSkinningRigs,
  buildModelSkinningRig,
  getSnapshot: () => rigSnapshot(),
  notifyChanged: notifyModelRigChanged,
  requestRender,
  cancelWeightPicking: cancelWeightModelPicking,
  getModelJointId: (sourceKey, boneId) =>
    modelJointIdForSourceBone(sourceKey, boneId),
  pickFromSurface: ({clientX, clientY} = {}) => {
    const intersection = raycastModelAtClientPoint({
      clientX, clientY, canvas: renderer.domElement, camera,
      meshes: [...knownMeshes].filter(mesh => mesh?.userData?.assetFill !== true),
    });
    const sampled = sampleModelSkinningAtIntersection(intersection);
    return modelJointFromSkinningSample(sampled)?.jointId ?? null;
  },
  rotationSnapValues: RIG_ROTATION_SNAP_DEGREES,
});

rigPoseRuntime = initializeRigPoseRuntime({
  state: modelRigState,
  getRig: () => modelSkinningRig,
  getJoint: modelJointForId,
  getParent: modelParentForJoint,
  getComponentForJoint: modelComponentForJoint,
  getSourceRig: sourceKey => sourceSkinningRigs.get(String(sourceKey)) || null,
  getSourceComponent: rigComponentForBone,
  getModelJointId: modelJointIdForSourceBone,
  getRepresentativeMember: joint =>
    joint?.representativeMember || joint?.members?.[0] || null,
  applyPose: options => applyModelPose(options),
  setComponentRoot: setRigComponentRootForSource,
  resetModelPose,
  hasActivePhysics: modelRigHasActivePhysics,
  finalizeSourcePoseBounds,
  finalizeAllPoseBounds: () =>
    (modelSkinningRig?.sourceRigs || []).forEach(finalizeSourcePoseBounds),
  wakePhysics: () => modelPhysicsSession.wake(),
  notifyChanged: notifyModelRigChanged,
  notifyPoseChanged: notifyModelRigPoseChanged,
  requestRender,
  rigPresetState,
});

function modelWeightSnapshot() {
  const selectedBones = selectionRecordsFromMap(
    modelWeightState.selectedBonesBySource);
  const savedBones = selectionRecordsFromMap(
    modelWeightState.savedBonesBySource);
  return {
    loaded: modelWeightState.loaded,
    loading: modelWeightState.loading,
    generation: modelWeightGeneration,
    error: modelWeightState.error,
    noWeights: modelWeightState.noWeights,
    sources: modelWeightState.sources.map(source => ({
      ...source,
      availableBoneIds: [...source.availableBoneIds],
      boneStats: Object.fromEntries(Object.entries(source.boneStats || {})
        .map(([id, stats]) => [id, {...stats}])),
    })),
    selectedBones,
    savedBones,
    selectedBoneCount: selectedBoneCount(selectedBones),
    pickedPoint: modelWeightState.pickedPoint
      ? {...modelWeightState.pickedPoint,
        point: [...modelWeightState.pickedPoint.point],
        influences: modelWeightState.pickedPoint.influences
          .map(influence => ({...influence}))}
      : null,
    pickerViewMode: modelWeightState.pickerViewMode,
    pickStatus: modelWeightState.pickStatus,
    picking: modelWeightState.picking,
    savedSelectionApplied: modelWeightState.savedSelectionApplied,
    savingSelection: modelWeightState.savingSelection,
    selectionSaveError: modelWeightState.selectionSaveError,
    heatmapEnabled: modelWeightState.heatmapEnabled,
    loadedMeshCount: modelWeightState.loadedMeshCount,
    failedMeshCount: modelWeightState.failedMeshCount,
  };
}

function cloneRigQuaternion(value) {
  if (value?.isQuaternion) return value.clone().normalize();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 4).map(Number)
    : [value?.x, value?.y, value?.z, value?.w].map(Number);
  return values.length === 4 && values.every(Number.isFinite)
    ? new THREE.Quaternion(...values).normalize() : new THREE.Quaternion();
}

function quaternionIsIdentity(value) {
  const x = Number(value?.x);
  const y = Number(value?.y);
  const z = Number(value?.z);
  const w = Number(value?.w);
  return Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)
    && Number.isFinite(w)
    && Math.abs(x) < 1e-8
    && Math.abs(y) < 1e-8
    && Math.abs(z) < 1e-8
    && Math.abs(Math.abs(w) - 1) < 1e-8;
}

function rigComponentForBone(rig, boneId) {
  const componentId = rig?.inferredForest?.componentByBoneId?.[boneId];
  return Number.isInteger(Number(componentId))
    ? rig?.inferredForest?.components?.[Number(componentId)] || null : null;
}

function rebuildSourceRigRestFrames(rig) {
  const frames = buildInferredRigRestFrames(
    rig.inferredForest, rig.centerByBoneId, rig.jointPivotByBoneId);
  rig.restFrameByBoneId = frames.frameByBoneId;
  rig.restDirectionByBoneId = frames.directionByBoneId;
  rig.restFrameEvidenceByBoneId = frames.evidenceByBoneId;
  rig.continuationChildByBoneId = frames.continuationChildByBoneId;
  rig.poseFrameCache?.clear();
  rig.structureRevision = ++rigRuntime.structureRevision;
  return frames;
}

function modelRigSnapshotForState() {
  const snapshot = modelRigSnapshot(modelSkinningRig, {
    quaternionIsIdentity,
    matrixIsIdentity,
  });
  if (snapshot) snapshot.humanoidControlRig = humanoidControlRigSnapshot();
  return snapshot;
}

function modelComponentForJoint(jointId) {
  const id = Number(jointId);
  const componentId = modelSkinningRig?.componentByJointId?.get(id);
  return Number.isInteger(Number(componentId))
    ? modelSkinningRig?.components?.[Number(componentId)] || null : null;
}

function humanoidControlRigSnapshot() {
  const rig = modelSkinningRig?.humanoidControlRig
    || modelRigState.humanoidControlRig;
  const orientationState = getModelTransformState?.();
  const orientationRevision = Number.isFinite(
    Number(orientationState?.modelOrientationRevision))
    ? Number(orientationState.modelOrientationRevision) : 0;
  const cacheKey = `${modelRigState.humanoidStructureRevision ?? 'none'}:${orientationRevision}:${
    modelSkinningRig?.poseRevision || 0}`;
  if (humanoidControlRigSnapshotCache && humanoidControlRigCacheKey === cacheKey) {
    return humanoidControlRigSnapshotCache;
  }
  if (!rig) {
    humanoidControlRigSnapshotCache = {
      version: 1, source: 'humanoid_control_rig', mode: 'primary_driver',
      available: false, accepted: false, confidence: 'low', controls: {},
      diagnostics: {reason: 'rig_not_loaded'},
    };
  } else {
    const controls = Object.fromEntries(Object.entries(rig.controls || {})
      .map(([key, control]) => [key, {
        ...control,
        position: Array.isArray(modelRigState.humanoidPose?.[key])
          ? [...modelRigState.humanoidPose[key]] : [...(control.position || [0, 0, 0])],
      }]));
    humanoidControlRigSnapshotCache = {
      version: Number(rig.version) || 1,
      source: 'humanoid_control_rig',
      mode: 'primary_driver',
      available: rig.available !== false,
      accepted: rig.accepted === true,
      confidence: rig.confidence || 'low',
      confidenceByRegion: {...(rig.confidenceByRegion || {})},
      controls,
      diagnostics: {
        reason: rig.diagnostics?.reason || null,
        templatePoints: {...(rig.diagnostics?.templatePoints || {})},
        structureRevision: modelRigState.humanoidStructureRevision,
        modelOrientationRevision: orientationRevision,
      },
    };
  }
  humanoidControlRigCacheKey = cacheKey;
  modelRigState.humanoidControlRig = humanoidControlRigSnapshotCache;
  return humanoidControlRigSnapshotCache;
}

function primaryHumanoidLimb(role = modelRigState.activeLimbRole) {
  const keys = {
    left_arm: ['leftShoulder', 'leftElbow', 'leftHand'],
    right_arm: ['rightShoulder', 'rightElbow', 'rightHand'],
    left_leg: ['leftHip', 'leftKnee', 'leftFoot'],
    right_leg: ['rightHip', 'rightKnee', 'rightFoot'],
  }[role];
  const rig = modelSkinningRig?.humanoidControlRig;
  const available = !!(keys && rig?.accepted && keys.every(key =>
    rig.controls?.[key]?.position));
  return {
    role, keys: keys || [], available,
    bendSign: 1,
  };
}

function ikSnapshot() {
  const active = primaryHumanoidLimb();
  const mappings = Object.fromEntries(RIG_LIMB_ROLES.map(role => {
    const limb = primaryHumanoidLimb(role);
    return [role, {
      role,
      available: limb.available,
      confidence: limb.available ? 'high' : 'low',
      bendSign: limb.bendSign,
      controlKeys: [...limb.keys],
      reason: limb.available ? null : 'control_unavailable',
    }];
  }));
  return {
    enabled: !!modelRigState.ikEnabled,
    activeLimbRole: modelRigState.activeLimbRole,
    available: active.available,
    controlKeys: [...active.keys],
    confidence: active.available ? 'high' : 'low',
    reason: active.available ? null : 'control_unavailable',
    bendSign: active.bendSign,
    mappings,
  };
}

function rigSnapshot() {
  return {
    loaded: modelRigState.loaded,
    loading: modelRigState.loading,
    error: modelRigState.error,
    jointPickIntent: modelRigState.jointPickIntent
      ? {...modelRigState.jointPickIntent} : null,
    structureRevision: modelRigState.structureRevision,
    selectedJointId: modelRigState.selectedJointId,
    physicsActive: modelRigHasActivePhysics(),
    rotationSnapDegrees: modelRigState.rotationSnapDegrees,
    ik: ikSnapshot(),
    pickStatus: modelRigState.pickStatus,
    rigPresets: getRigPresetSnapshot(),
    humanoidRigEdit: humanoidRigEditSession?.snapshot(),
    humanoidControlRig: humanoidControlRigSnapshot(),
    model: modelRigSnapshotForState(),
  };
}

function notifyModelRigChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mod-viewer-model-rig-changed', {
      detail: rigSnapshot(),
    }));
  }
}

function notifyModelRigPoseChanged(rig, boneId, changedJointIds = null) {
  if (typeof window !== 'undefined') {
    const quaternion = rig?.poseRotationByBoneId?.get(boneId);
    const jointId = modelJointIdForSourceBone(rig?.sourceKey, boneId);
    const modelQuaternion = Number.isInteger(jointId)
      ? modelSkinningRig?.poseRotationByJointId?.get(jointId) : null;
    const jointIds = Array.isArray(changedJointIds)
      ? changedJointIds.map(Number).filter(Number.isInteger)
      : Number.isInteger(jointId) ? [jointId] : [];
    window.dispatchEvent(new CustomEvent('mod-viewer-model-rig-pose-changed', {
      detail: {
        jointId: Number.isInteger(jointId) ? jointId : null,
        jointIds,
        quaternion: (modelQuaternion || quaternion)?.toArray()
          || [0, 0, 0, 1],
      },
    }));
  }
}

function notifyModelWeightChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mod-viewer-model-weight-changed', {
      detail: modelWeightSnapshot(),
    }));
  }
}

function selectionRecordsFromMap(selectionMap) {
  return [...(selectionMap || [])].map(([sourceKey, boneIds]) => {
    const separator = String(sourceKey).lastIndexOf('|offset=');
    const fallbackOffset = Number(String(sourceKey).slice(separator + 8));
    const descriptor = modelWeightState.sourceDescriptors.get(sourceKey)
      || (separator > 0 && Number.isInteger(fallbackOffset)
        ? {
          sourceKey,
          sourceFile: String(sourceKey).slice(0, separator),
          boneIdOffset: fallbackOffset,
        } : null);
    return descriptor ? {
      ...descriptor,
      boneIds: normalizeSelectedBoneIds(boneIds),
    } : null;
  }).filter(entry => entry?.boneIds.length);
}

function selectionMapFromEntries(entries) {
  const map = new Map();
  for (const entry of normalizeBoneSelection(entries)) {
    map.set(entry.sourceKey, new Set(entry.boneIds));
    modelWeightState.sourceDescriptors.set(entry.sourceKey, {
      sourceKey: entry.sourceKey,
      sourceFile: entry.sourceFile,
      boneIdOffset: entry.boneIdOffset,
    });
  }
  return map;
}

function sourceSelectionEntries(selectionMap) {
  return selectionRecordsFromMap(selectionMap).map(entry => ({
    sourceKey: entry.sourceKey,
    sourceFile: entry.sourceFile,
    boneIdOffset: entry.boneIdOffset,
    boneIds: entry.boneIds,
  }));
}

function eligibleSkinningMesh(mesh) {
  return mesh?.userData?.skinningAvailable === true
    && mesh.userData?.assetFill !== true
    && !!mesh.userData?.modPath
    && !!mesh.userData?.semanticKey;
}

function refreshSelectedWeightMask(mesh, state) {
  if (!state?.loaded) return null;
  const selected = modelWeightState.selectedBonesBySource.get(
    state.skinningSourceKey) || new Set();
  state.selectedWeightMask = buildSelectedWeightMask(
    state.indices, state.weights, state.influenceCount,
    selected);
  const activeVertices = [];
  state.selectedWeightMask.forEach((weight, vertex) => {
    if (weight > 0) activeVertices.push(vertex);
  });
  state.physicsActiveVertices = Uint32Array.from(activeVertices);
  return state.selectedWeightMask;
}

function resetModelWeightState() {
  modelWeightGeneration += 1;
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  resetRigPresetSession();
  resetHumanoidRigEditSession();
  resetWeightPickingSession();
  sourcePhysicsRigs.clear();
  sourceSkinningRigs.clear();
  modelSkinningRig = null;
  weightRuntime.resetModelWeightState();
  resetWeightModelSession();
  rigRuntime.resetModelRigState();
  rigRuntime.resetRigPresetState();
  notifyModelWeightChanged();
  notifyModelRigChanged();
}

function ensureSourceSkinningRig(sourceKey, members) {
  return rigSourceSession?.ensure(sourceKey, members) || null;
}

function buildAllSourceSkinningRigs() {
  return rigSourceSession?.buildAll() || [];
}

function resetSourceSkinningPose(rig) {
  return rigSourceSession?.resetPose(rig);
}

function modelJointIdForSourceBone(sourceKey, boneId) {
  return modelSkinningRig?.sourceBoneToModelJointId?.get(
    sourceBoneKey(sourceKey, boneId));
}

function modelJointForId(jointId) {
  const id = Number(jointId);
  return Number.isInteger(id) ? modelSkinningRig?.joints?.[id] || null : null;
}

function modelRigHasActivePhysics() {
  return !!modelSkinningRig?.sourceRigs?.some(sourceRig =>
    sourceRig.physicsRig?.physicsState);
}

function cloneModelComponent(component) {
  return {
    componentId: component.componentId,
    rootId: component.rootId,
    nodeIds: [...(component.nodeIds || [])],
    parentById: {...(component.parentById || {})},
    childrenById: Object.fromEntries(Object.entries(
      component.childrenById || {}).map(([id, children]) => [id, [...children]])),
    depthById: {...(component.depthById || {})},
    maxDepth: component.maxDepth,
    edges: (component.edges || []).map(edge => ({...edge})),
  };
}

function cloneSourceForest(forest) {
  return {
    ...forest,
    components: (forest?.components || []).map(cloneModelComponent),
    componentByBoneId: {...(forest?.componentByBoneId || {})},
    edges: (forest?.edges || []).map(edge => ({...edge})),
    nodeIds: [...(forest?.nodeIds || [])],
  };
}

function restoreDefaultSourceRigOrientation(rig) {
  if (!rig?.defaultInferredForest) return false;
  rig.inferredForest = cloneSourceForest(rig.defaultInferredForest);
  rig.jointPivotByBoneId = new Map([...rig.defaultJointPivotByBoneId]
    .map(([boneId, pivot]) => [boneId, [...pivot]]));
  rig.poseRootOverrides = new Map();
  rebuildSourceRigRestFrames(rig);
  return true;
}

function restoreDefaultSourceRigOrientations() {
  return (modelSkinningRig?.sourceRigs || [])
    .map(restoreDefaultSourceRigOrientation).some(Boolean);
}

function defaultRootOverrides(rig) {
  return new Map((rig.defaultComponents || []).map(component => [
    Number(component.componentId), Number(component.rootId),
  ]));
}

function modelForestWithRootSignatures(rig, signatures = []) {
  const overrides = defaultRootOverrides(rig);
  const usedComponents = new Set();
  const appliedRoots = [];
  const skipped = [];
  const signatureIndex = buildJointSignatureIndex(rig);
  for (const signature of signatures) {
    const jointId = signatureIndex.resolvedBySignature.get(signature);
    if (!Number.isInteger(jointId)
        || signatureIndex.ambiguousSignatures.has(signature)) {
      skipped.push({type: 'root', jointSignature: signature,
        reason: 'root_not_found'});
      continue;
    }
    const componentId = rig.defaultComponentByJointId.get(jointId);
    if (!Number.isInteger(Number(componentId))) {
      skipped.push({type: 'root', jointSignature: signature,
        reason: 'root_not_found'});
      continue;
    }
    if (usedComponents.has(Number(componentId))) {
      skipped.push({type: 'root', jointSignature: signature,
        reason: 'duplicate_root_entry'});
      continue;
    }
    usedComponents.add(Number(componentId));
    overrides.set(Number(componentId), jointId);
    appliedRoots.push(signature);
  }
  return {forest: orientModelRigForest(rig.joints, rig.edges, overrides),
    overrides, appliedRoots, skipped};
}

function installModelForest(rig, forest, {restoreDefaults = false} = {}) {
  rig.components = forest.components;
  rig.componentByJointId = forest.componentByJointId;
  rig.inferredForest = {
    components: forest.components,
    componentByBoneId: forest.componentByJointId,
  };
  rebuildModelRestFrames(rig, forest);
  if (restoreDefaults) {
    rig.jointPivotByJointId = new Map([...rig.defaultJointPivotByJointId]
      .map(([jointId, pivot]) => [jointId, [...pivot]]));
    rig.restFrameByJointId = new Map([...rig.defaultRestFrameByJointId]
      .map(([jointId, frame]) => [jointId, frame.clone()]));
    rig.restDirectionByJointId = new Map([...rig.defaultRestDirectionByJointId]
      .map(([jointId, direction]) => [jointId,
        direction ? [...direction] : null]));
    rig.restContinuationChildByJointId = new Map(
      rig.defaultRestContinuationChildByJointId);
    (rig.joints || []).forEach(joint => {
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

function restoreDefaultModelRigOrientation(rig) {
  if (!rig) return false;
  const {forest} = modelForestWithRootSignatures(rig, []);
  installModelForest(rig, forest, {restoreDefaults: true});
  return true;
}

function buildModelSkinningRig(sourceRigs = [...sourceSkinningRigs.values()]) {
  const previousSelectedJointId = modelRigState.selectedJointId;
  const previousRootSignatures = new Set(
    modelRigState.explicitRootSignatures);
  if (modelSkinningRig) resetModelPose({request: false});
  const reconciliation = buildModelRigReconciliation(sourceRigs);
  const joints = reconciliation.joints || [];
  const rig = {
    key: 'model-rig',
    sourceKey: 'model-rig',
    sourceRigs: [...sourceRigs],
    reconciliation,
    joints,
    edges: reconciliation.edges || [],
    inferredForest: {
      components: reconciliation.components || [],
      componentByBoneId: reconciliation.componentByJointId || new Map(),
    },
    components: reconciliation.components || [],
    componentByJointId: reconciliation.componentByJointId || new Map(),
    centerByJointId: new Map(joints.map(joint => [
      joint.jointId, joint.restCenter || [0, 0, 0]])),
    jointPivotByJointId: new Map(joints.map(joint => [
      joint.jointId, joint.restPivot || joint.restCenter || [0, 0, 0]])),
    jointPivotByEdgeKey: null,
    restFrameByJointId: new Map(joints.map(joint => [
      joint.jointId, cloneRigQuaternion(joint.restFrame)])),
    restDirectionByJointId: new Map(),
    restFrameEvidenceByJointId: new Map(),
    restContinuationChildByJointId: new Map(),
    sourceBoneToModelJointId: reconciliation.sourceBoneToModelJointMap
      || new Map(),
    sourceTransformAliases: new Map(),
    sourceRotationAliases: new Map(),
    poseRotationByJointId: new Map(),
    poseTransforms: new Map(),
    poseRotations: new Map(),
    manualPoseTransforms: new Map(),
    humanoidSourceBoneTransforms: new Map(),
    poseTransformCache: new Map(),
    poseFrameCache: new Map(),
    poseAffectedJointIds: new Set(),
    poseActiveJointKey: '',
    poseActiveVerticesByMesh: new Map(),
    poseSourceBoneIdsByMesh: new Map(),
    poseRevision: 0,
    structureRevision: ++rigRuntime.structureRevision,
  };
  // Install provenance-derived edge pivots before capturing the default
  // orientation. Reset Pose must restore the same pivots used on first load.
  rebuildModelRestFrames(rig, rig.inferredForest);
  rig.defaultComponents = rig.components.map(cloneModelComponent);
  rig.defaultComponentByJointId = new Map(rig.componentByJointId);
  rig.defaultRootIdByComponent = new Map(rig.defaultComponents.map(component => [
    Number(component.componentId), Number(component.rootId),
  ]));
  rig.defaultJointPivotByJointId = new Map([...rig.jointPivotByJointId]
    .map(([jointId, pivot]) => [jointId, [...pivot]]));
  rig.defaultRestFrameByJointId = new Map([...rig.restFrameByJointId]
    .map(([jointId, frame]) => [jointId, frame.clone()]));
  rig.defaultRestDirectionByJointId = new Map([...rig.restDirectionByJointId]
    .map(([jointId, direction]) => [jointId, direction.toArray?.() || [...direction]]));
  rig.defaultRestContinuationChildByJointId = new Map(
    rig.restContinuationChildByJointId);
  let restoredRootSignatures = new Set();
  if (previousRootSignatures.size) {
    const rootRestore = modelForestWithRootSignatures(
      rig, [...previousRootSignatures]);
    installModelForest(rig, rootRestore.forest);
    const signatureIndex = buildJointSignatureIndex(rig).resolvedBySignature;
    restoredRootSignatures = new Set(rootRestore.appliedRoots.filter(signature => {
      const jointId = signatureIndex.get(signature);
      const componentId = rig.defaultComponentByJointId.get(jointId);
      return Number.isInteger(Number(componentId))
        && rig.defaultRootIdByComponent.get(Number(componentId)) !== jointId;
    }));
    if (restoredRootSignatures.size) {
      rig.structureRevision = ++rigRuntime.structureRevision;
    }
  }
  modelRigState.explicitRootSignatures = restoredRootSignatures;
  rigPresetState.lastApplyResult = null;
  sourceRigs.forEach(sourceRig => {
    rig.sourceTransformAliases.set(sourceRig.sourceKey, new Map());
    rig.sourceRotationAliases.set(sourceRig.sourceKey, new Map());
    sourceRig.poseRotationByBoneId.clear();
    sourceRig.poseTransforms = new Map();
    sourceRig.poseRotations = new Map();
    sourceRig.poseTransformCache.clear();
    sourceRig.poseFrameCache.clear();
  });
  modelSkinningRig = rig;
  buildPrimaryHumanoidRig(rig);
  skinningRuntime.updateModelWeightHeatmap();
  modelRigState.structureRevision = rig.structureRevision;
  modelRigState.selectedJointId = Number.isInteger(previousSelectedJointId)
    && joints[previousSelectedJointId] ? previousSelectedJointId : null;
  updateModelPoseFrameCache(rig, rig.poseTransforms);
  return rig;
}

function buildPrimaryHumanoidRig(rig) {
  const orientationState = getModelTransformState?.();
  const automaticRig = buildHumanoidControlRig({
    meshes: [...knownMeshes],
    axes: humanoidSemanticAxes(),
    orientationState,
  });
  const savedOverrides = humanoidRigEditSession?.getSavedOverrides?.();
  const resolvedMappings = automaticRig?.accepted
    ? resolveHumanoidControlMappings({
      savedOverrides, modelRig: rig,
    }) : new Map();
  const controlRig = automaticRig?.accepted
    ? applyHumanoidControlRigOverrides({
      automaticRig, savedOverrides, modelRig: rig, resolvedMappings,
    }) : automaticRig;
  const heatBinding = controlRig?.accepted
    ? buildHumanoidHeatBinding({
      controlRig, sourceRigs: rig.sourceRigs, modelRig: rig,
      controlMappings: resolvedMappings,
    }) : null;
  const binding = controlRig?.accepted
    ? buildHumanoidRigBinding({controlRig, modelRig: rig, heatBinding,
      controlMappings: resolvedMappings}) : null;
  rig.humanoidAutomaticControlRig = automaticRig;
  rig.humanoidControlRig = controlRig;
  rig.humanoidHeatBinding = heatBinding;
  rig.humanoidBinding = binding;
  rig.humanoidOrientationRevision = Number(
    orientationState?.modelOrientationRevision) || 0;
  modelRigState.humanoidControlRig = controlRig;
  modelRigState.humanoidStructureRevision = rig.structureRevision;
}

function modelParentForJoint(jointId, rig = modelSkinningRig) {
  const componentId = rig?.componentByJointId?.get?.(Number(jointId));
  const component = Number.isInteger(Number(componentId))
    ? rig.components?.[Number(componentId)] : null;
  const parent = component?.parentById?.[Number(jointId)];
  return parent === null || parent === undefined ? null : Number(parent);
}

function updateModelPoseFrameCache(rig, transforms) {
  const seen = new Set();
  for (const joint of rig?.joints || []) {
    const jointId = Number(joint.jointId);
    if (!Number.isInteger(jointId)) continue;
    const parentId = modelParentForJoint(jointId, rig);
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
}

function updateModelSourceAliases(rig) {
  for (const sourceRig of rig.sourceRigs || []) {
    const transforms = rig.sourceTransformAliases.get(sourceRig.sourceKey)
      || new Map();
    const rotations = rig.sourceRotationAliases.get(sourceRig.sourceKey)
      || new Map();
    const manualTransforms = rig.manualPoseTransforms || new Map();
    const humanoidTransforms = rig.humanoidSourceBoneTransforms?.get(
      sourceRig.sourceKey) || new Map();
    const jointBindings = rig.humanoidBinding?.jointBindings;
    transforms.clear();
    rotations.clear();
    for (const boneId of sourceRig.boneIds || []) {
      const jointId = rig.sourceBoneToModelJointId.get(
        sourceBoneKey(sourceRig.sourceKey, boneId));
      const manual = Number.isInteger(jointId)
        ? manualTransforms.get(jointId) : null;
      const humanoid = humanoidTransforms.get(Number(boneId))?.matrix;
      const modelBinding = Number.isInteger(jointId)
        ? jointBindings?.get?.(jointId) : null;
      const modelTransform = Number.isInteger(jointId)
        ? rig.poseTransforms.get(jointId) : null;
      const transform = humanoid
        ? humanoid.clone().multiply(manual || RIG_IDENTITY_MATRIX)
        : modelBinding?.bindingMethod === 'heat_connectivity'
          ? manual
          : modelTransform || manual;
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

function createSourcePhysicsRig(sourceKey, members) {
  const descriptor = modelWeightState.sourceDescriptors.get(sourceKey);
  const skinRig = ensureSourceSkinningRig(sourceKey, members);
  const rig = {
    key: sourceKey,
    sourceKey,
    sourceFile: descriptor?.sourceFile || '',
    boneIdOffset: descriptor?.boneIdOffset ?? 0,
    meshes: new Set(members),
    influenceGraph: null,
    centerByBoneId: null,
    physicsCenterByBoneId: null,
    physicsForest: null,
    selectionKey: '',
    physicsState: null,
    physicsSettled: true,
    physicsJointLimits: null,
    physicsConstraintDiagnostics: null,
    physicsGravityAccelerations: null,
    physicsGravityDiagnostics: null,
    physicsGravityLocal: [...GRAVITY_WORLD_DIRECTION],
    physicsBaseCenterByBoneId: null,
    physicsTargetByBoneId: null,
    physicsEquilibriumByBoneId: null,
    composedTransformCache: new Map(),
    composedTransforms: new Map(),
    composedRotations: new Map(),
    basePoseRevision: -1,
    skinRig,
  };
  skinRig.physicsRig = rig;
  refreshSourcePhysicsRig(rig, members);
  return rig;
}

function refreshSourcePhysicsRig(rig, members) {
  rig.meshes = new Set(members);
  rig.composedTransformsDirty = true;
  rig.skinRig = ensureSourceSkinningRig(rig.sourceKey, members);
  rig.skinRig.physicsRig = rig;
  rig.influenceGraph = rig.skinRig.influenceGraph;
  rig.centerByBoneId = new Map((rig.influenceGraph.nodes || []).map(node => [
    node.boneId, node.weightedCenter]));
  const selected = modelWeightState.selectedBonesBySource.get(rig.sourceKey)
    || new Set();
  rig.physicsForest = buildSelectedPhysicsForest(
    rig.influenceGraph, rig.centerByBoneId,
    (rig.influenceGraph.nodes || []).map(node => node.boneId), selected);
  const selectedIds = rig.physicsForest?.selectedBoneIds || [];
  rig.selectionKey = selectedIds.join(',');
  rig.physicsCenterByBoneId = rig.physicsForest?.centers || rig.centerByBoneId;
  return rig;
}

function disablePhysicsSession() {
  return physicsCoordinator?.disable() || false;
}

function poseVerticesForState(state, affectedBoneIds) {
  const mask = buildSelectedWeightMask(
    state.indices, state.weights, state.influenceCount, affectedBoneIds);
  const vertices = [];
  mask.forEach((weight, vertex) => {
    if (weight > 0) vertices.push(vertex);
  });
  return Uint32Array.from(vertices);
}

function finalizeSourcePoseBounds(rig) {
  let finalized = false;
  forEachRigMesh(rig, (mesh, state) => {
    finalized = skinningRuntime.finalizeDeformationGeometry(mesh, state)
      || finalized;
  });
  if (finalized) invalidateCharacterShadowGeometry({request: false});
  return finalized;
}

function buildModelPoseTransforms() {
  if (!modelSkinningRig) return new Map();
  const manualTransforms = buildForestTransformsFromLocalRotations(
    modelSkinningRig.inferredForest, modelSkinningRig.centerByJointId, {
      getQuaternion: jointId => modelSkinningRig.poseRotationByJointId.get(
        jointId) || new THREE.Quaternion(),
      jointPivotByBoneId: modelSkinningRig.jointPivotByJointId,
      transformCache: modelSkinningRig.poseTransformCache,
    });
  const driverLayer = buildHumanoidDriverBaseTransforms({
    binding: modelSkinningRig.humanoidBinding,
    controlRig: modelSkinningRig.humanoidControlRig,
    modelRig: modelSkinningRig,
    posedControls: modelRigState.humanoidPose,
  });
  modelSkinningRig.manualPoseTransforms = manualTransforms;
  modelSkinningRig.humanoidSourceBoneTransforms =
    buildHumanoidSourceBoneDriverTransforms({
      heatBinding: modelSkinningRig.humanoidHeatBinding,
      controlRig: modelSkinningRig.humanoidControlRig,
      posedControls: modelRigState.humanoidPose,
    });
  modelSkinningRig.humanoidDriverTransforms = driverLayer.result;
  modelSkinningRig.humanoidDriverWorldByJointId =
    driverLayer.driverWorldByJointId;
  const composed = new Map();
  manualTransforms.forEach((matrix, jointId) => composed.set(jointId, matrix));
  driverLayer.result.forEach((driverDelta, jointId) => {
    const manual = manualTransforms.get(jointId) || RIG_IDENTITY_MATRIX;
    composed.set(jointId, driverDelta.clone().multiply(manual));
  });
  modelSkinningRig.poseTransforms = composed;
  modelSkinningRig.poseRotations.clear();
  composed.forEach((matrix, jointId) => {
    modelSkinningRig.poseRotations.set(jointId,
      new THREE.Quaternion().setFromRotationMatrix(matrix).normalize());
  });
  updateModelPoseFrameCache(modelSkinningRig,
    modelSkinningRig.poseTransforms);
  updateModelSourceAliases(modelSkinningRig);
  return modelSkinningRig.poseTransforms;
}

function modelPoseDescendantIds(rig, posedJointIds) {
  const affected = new Set();
  const pending = [...posedJointIds].map(Number).filter(Number.isFinite);
  while (pending.length) {
    const jointId = pending.pop();
    if (affected.has(jointId)) continue;
    affected.add(jointId);
    const componentId = rig.componentByJointId.get(jointId);
    const component = Number.isInteger(Number(componentId))
      ? rig.components[Number(componentId)] : null;
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

function sourceBoneIdsForHumanoidTransforms(rig) {
  const affected = new Map();
  rig?.humanoidSourceBoneTransforms?.forEach((entries, sourceKey) => {
    const boneIds = new Set();
    entries.forEach((entry, boneId) => {
      if (entry?.matrix?.isMatrix4 && !matrixIsIdentity(entry.matrix)) {
        boneIds.add(Number(boneId));
      }
    });
    if (boneIds.size) affected.set(sourceKey, boneIds);
  });
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

function applyModelPose({request = true, dragging = false} = {}) {
  const rig = modelSkinningRig;
  if (!rig) return false;
  const transforms = buildModelPoseTransforms();
  const humanoidSourceBones = sourceBoneIdsForHumanoidTransforms(rig);
  rig.poseRevision = (rig.poseRevision || 0) + 1;
  const posedJointIds = activePoseJointIds({
    manualRotations: rig.poseRotationByJointId,
    driverTransforms: rig.humanoidDriverTransforms,
    quaternionIsIdentity,
  });
  const poseJointKey = posedJointIds.sort((left, right) => left - right).join(',');
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
    (humanoidSourceBones.get(sourceRig.sourceKey) || []).forEach(boneId =>
      affectedBoneIds.add(Number(boneId)));
    forEachRigMesh(sourceRig, (mesh, state) => {
      const previousBoneKey = rig.poseSourceBoneIdsByMesh.get(mesh) || '';
      const boneKey = sourceBoneKeyForSet(affectedBoneIds);
      const activeVertices = !affectedSetChanged && previousBoneKey === boneKey
        && rig.poseActiveVerticesByMesh.has(mesh)
        ? rig.poseActiveVerticesByMesh.get(mesh)
        : affectedBoneIds.size
          ? poseVerticesForState(state, affectedBoneIds) : new Uint32Array();
      // Update the manual layer first. The common writer below then unions it
      // with the Physics-selected vertices and skins the authored baseline
      // exactly once.
      state.poseActiveVertices = activeVertices;
      state.poseTransforms = transformsByBoneId;
      state.poseRotations = rotationsByBoneId;
      rig.poseActiveVerticesByMesh.set(mesh, activeVertices);
      rig.poseSourceBoneIdsByMesh.set(mesh, boneKey);
    });
    const physicsRig = sourceRig.physicsRig;
    if (physicsRig?.physicsState) {
      refreshParticipantDerivedState(
        [...physicsRig.meshes][0], physicsRig,
        modelPhysicsSession.getSettings());
      changed = applySourceDeformation(physicsRig, {visibleOnly: false})
        || changed;
    } else {
      forEachRigMesh(sourceRig, (mesh, state) => {
        changed = skinningRuntime.applyDeformation(mesh, state, {
          request: false, invalidateShadow: false, skipHidden: false,
        }) || changed;
      });
    }
  }
  rig.poseAffectedJointIds = affectedJointIds;
  rig.poseActiveJointKey = poseJointKey;
  if (changed) invalidateCharacterShadowGeometry({request: false});
  if (!dragging && !modelRigHasActivePhysics()) {
    for (const sourceRig of rig.sourceRigs || []) finalizeSourcePoseBounds(sourceRig);
  }
  if (modelRigHasActivePhysics()) modelPhysicsSession.wake();
  if (request) requestRender();
  return changed;
}

function resetModelPose({request = true} = {}) {
  if (!modelSkinningRig) return false;
  const hadRootOverrides = modelRigState.explicitRootSignatures.size > 0;
  modelSkinningRig.poseRotationByJointId.clear();
  modelRigState.humanoidPose = {};
  restoreDefaultSourceRigOrientations();
  restoreDefaultModelRigOrientation(modelSkinningRig);
  modelRigState.explicitRootSignatures.clear();
  if (hadRootOverrides) {
    modelSkinningRig.structureRevision = ++rigRuntime.structureRevision;
    modelRigState.structureRevision = modelSkinningRig.structureRevision;
  }
  const changed = applyModelPose({request});
  modelSkinningRig.poseActiveVerticesByMesh.clear();
  modelSkinningRig.poseSourceBoneIdsByMesh.clear();
  return changed;
}

function clearModelManualPose({request = false} = {}) {
  if (!modelSkinningRig) return false;
  const hadPose = modelSkinningRig.poseRotationByJointId.size > 0
    || modelSkinningRig.poseActiveJointKey !== '';
  modelSkinningRig.poseRotationByJointId.clear();
  if (!hadPose) return false;
  const changed = applyModelPose({request});
  modelSkinningRig.poseActiveVerticesByMesh.clear();
  modelSkinningRig.poseSourceBoneIdsByMesh.clear();
  return changed || hadPose;
}

function unavailableRigPresetResult(reason, preset = null) {
  return {
    success: false,
    preset,
    appliedJointCount: 0,
    skippedJointCount: 0,
    appliedRootCount: 0,
    skippedRootCount: 0,
    skipped: [{type: 'preset', reason}],
  };
}

/** Apply a resolved preset in one hierarchy/deformation transaction. */
function applyRigPosePreset(resolvedPreset, options = {}) {
  const rig = modelSkinningRig;
  if (!rig || !modelRigState.loaded) {
    const result = unavailableRigPresetResult('rig_not_loaded');
    rigPresetState.lastApplyResult = result;
    return result;
  }
  const resolved = resolvedPreset?.preset
    ? resolvedPreset : resolveRigPreset(rig, resolvedPreset);
  if (!resolved?.success) {
    const result = resolved || unavailableRigPresetResult('invalid_preset');
    rigPresetState.lastApplyResult = result;
    modelRigState.pickStatus = 'This saved pose is invalid and could not be applied.';
    notifyModelRigChanged();
    return result;
  }
  if (!(resolved.joints?.length || 0) && !(resolved.roots?.length || 0)) {
    const result = {
      success: false,
      preset: resolved.preset,
      appliedJointCount: 0,
      skippedJointCount: resolved.skippedJointCount || 0,
      appliedRootCount: 0,
      skippedRootCount: resolved.skippedRootCount || 0,
      skipped: [...(resolved.skipped || [])],
      failureReason: 'no_matches',
    };
    rigPresetState.lastApplyResult = result;
    modelRigState.pickStatus = resolved.skipped?.length
      ? 'No saved joints matched the current inferred Rig.'
      : 'This saved pose is invalid and could not be applied.';
    notifyModelRigChanged();
    return result;
  }

  restoreDefaultSourceRigOrientations();
  const rootSignatures = (resolved.roots || [])
    .map(root => root.jointSignature).filter(Boolean);
  const rootRestore = modelForestWithRootSignatures(rig, rootSignatures);
  const oldRoots = new Map((rig.components || []).map(component => [
    Number(component.componentId), Number(component.rootId),
  ]));
  const newRoots = new Map((rootRestore.forest.components || []).map(component => [
    Number(component.componentId), Number(component.rootId),
  ]));
  const rootChanged = [...new Set([...oldRoots.keys(), ...newRoots.keys()])]
    .some(componentId => oldRoots.get(componentId) !== newRoots.get(componentId));
  installModelForest(rig, rootRestore.forest);
  const currentJointIndex = buildJointSignatureIndex(rig).resolvedBySignature;
  modelRigState.explicitRootSignatures = new Set(rootRestore.appliedRoots.filter(signature => {
    const jointId = currentJointIndex.get(signature);
    const componentId = rig.defaultComponentByJointId.get(jointId);
    return Number.isInteger(Number(componentId))
      && rig.defaultRootIdByComponent.get(Number(componentId)) !== jointId;
  }));
  if (rootChanged) {
    rig.structureRevision = ++rigRuntime.structureRevision;
    modelRigState.structureRevision = rig.structureRevision;
  }

  rig.poseRotationByJointId.clear();
  for (const entry of resolved.joints || []) {
    const rotation = entry.rotation;
    if (!Array.isArray(rotation) || rotation.length !== 4
        || rotation.some(value => !Number.isFinite(value))) continue;
    rig.poseRotationByJointId.set(Number(entry.jointId),
      cloneRigQuaternion(rotation));
  }
  const changed = applyModelPose({request: false, dragging: false});
  const skipped = [...(resolved.skipped || []), ...rootRestore.skipped];
  const result = {
    success: true,
    preset: resolved.preset,
    appliedJointCount: resolved.joints?.length || 0,
    skippedJointCount: resolved.skippedJointCount || 0,
    appliedRootCount: rootRestore.appliedRoots.length,
    skippedRootCount: (resolved.skippedRootCount || 0)
      + rootRestore.skipped.length,
    skipped,
    changed,
    ...(options?.presetId ? {presetId: options.presetId} : {}),
  };
  rigPresetState.lastApplyResult = result;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  requestRender();
  return result;
}

function setRigComponentRootForSource(sourceKey, boneId) {
  const rig = sourceSkinningRigs.get(String(sourceKey)) || null;
  const id = Number(boneId);
  const component = rigComponentForBone(rig, id);
  if (!rig || !component || !component.nodeIds.includes(id)) return false;
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  if (!Number.isInteger(jointId) || !modelSkinningRig) return false;
  clearModelManualPose({request: false});
  resetSourceSkinningPose(rig);
  const overrides = new Map(rig.inferredForest.components.map(item => [
    item.componentId, item.rootId]));
  overrides.set(component.componentId, id);
  rig.inferredForest = buildInferredRigForest(rig.influenceGraph, {
    rootOverrides: overrides,
  });
  rig.jointPivotByBoneId = jointPivotMap(
    rig.inferredForest, rig.influenceGraph.relationships);
  rebuildSourceRigRestFrames(rig);
  rig.poseRootOverrides = overrides;
  const signature = modelSkinningRig.joints[jointId]?.signature;
  const targetComponentId = modelSkinningRig.defaultComponentByJointId
    .get(jointId);
  const currentIndex = buildJointSignatureIndex(modelSkinningRig)
    .resolvedBySignature;
  const desiredRootSignatures = [...modelRigState.explicitRootSignatures]
    .filter(existingSignature => {
      const existingJointId = currentIndex.get(existingSignature);
      const existingComponentId = modelSkinningRig.defaultComponentByJointId
        .get(existingJointId);
      return Number(existingComponentId) !== Number(targetComponentId);
    });
  if (signature && modelSkinningRig.defaultRootIdByComponent.get(
      Number(targetComponentId)) !== jointId) {
    desiredRootSignatures.push(signature);
  }
  const rootRestore = modelForestWithRootSignatures(
    modelSkinningRig, desiredRootSignatures);
  installModelForest(modelSkinningRig, rootRestore.forest);
  const defaultComponentId = modelSkinningRig.defaultComponentByJointId
    .get(jointId);
  const appliedSignatures = new Set(rootRestore.appliedRoots);
  modelRigState.explicitRootSignatures = new Set(
    rootRestore.appliedRoots.filter(appliedSignature => {
      const appliedJointId = currentIndex.get(appliedSignature);
      const appliedComponentId = modelSkinningRig.defaultComponentByJointId
        .get(appliedJointId);
      return modelSkinningRig.defaultRootIdByComponent.get(
        Number(appliedComponentId)) !== appliedJointId;
    }));
  if (signature && Number(defaultComponentId) === Number(targetComponentId)
      && !appliedSignatures.has(signature)) {
    modelRigState.explicitRootSignatures.delete(signature);
  }
  modelSkinningRig.structureRevision = ++rigRuntime.structureRevision;
  modelRigState.structureRevision = modelSkinningRig.structureRevision;
  selectRigBoneInternal(sourceKey, id);
  applyModelPose({request: false});
  notifyModelRigChanged();
  requestRender();
  return true;
}

function syncPhysicsParticipants(...args) {
  return physicsCoordinator?.syncParticipants(...args);
}

function syncPhysicsToSelection(...args) {
  return physicsCoordinator?.syncToSelection(...args) || false;
}

function handleModelTransformChanged(event) {
  const detail = event.detail || {};
  const fallbackMesh = detail.meshes?.[0];
  const fallbackTransform = fallbackMesh?.quaternion?.toArray
    ? {
      orientation: fallbackMesh.quaternion.toArray(),
      translation: fallbackMesh.position?.toArray?.() || [0, 0, 0],
    } : getModelTransformState();
  modelPhysicsSession.handleModelTransform({
    ...detail,
    modelTransform: detail.modelTransform || fallbackTransform,
  });
}

function handleModelOrientationChanged() {
  const pose = {...(modelRigState.humanoidPose || {})};
  invalidateHumanoidDetection();
  modelRigState.humanoidPose = pose;
  if (modelSkinningRig && modelRigState.loaded) {
    buildPrimaryHumanoidRig(modelSkinningRig);
    applyModelPose({request: false});
  }
  notifyModelRigChanged();
}

function handleVirtualModelMotion(event) {
  modelPhysicsSession.handleVirtualMotion(event.detail);
}

if (typeof window !== 'undefined') {
  window.addEventListener('mod-viewer-model-transform-changed',
    handleModelTransformChanged);
  window.addEventListener('mod-viewer-model-orientation-changed',
    handleModelOrientationChanged);
  window.addEventListener('mod-viewer-virtual-model-motion',
    handleVirtualModelMotion);
  window.addEventListener('mod-viewer-mesh-state-changed', event => {
    modelPhysicsSession.handleMeshStateChanged(event.detail?.meshes || []);
  });
}
