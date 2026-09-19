// Shared ModelRig engine and Weight/Rig composition root. This module is
// imported by the optional Weight/Rig feature boundary on first use.

import * as THREE from 'three';
import {
  camera, controls, getModelTransformState, invalidateCharacterShadowGeometry,
  renderer,
} from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';
import {
  buildInferredRigRestFrames, rebuildModelRestFrames,
} from './weight-rig-frames.js';
import {
  buildModelRigReconciliationCooperative,
  orientModelRigForest,
} from './weight-rig-reconcile.js';
import {weightRigStatus} from './weight-rig-status.js';
import {
  hydrateModelRig, loadOrBuildModelRig, serializeModelRig,
} from './model-rig-persistence.js';
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
  createRigPresetSession,
} from './rig-preset-session.js';
import {
  createWeightModelSession, createWeightPickingSession,
} from './weight-model-session.js';
import {
  createWeightPhysicsController, createWeightPhysicsCoordinator,
} from './weight-physics-controller.js';
import {createHumanoidPoseRuntime} from './humanoid-pose-runtime.js';
import {createSkinningRuntime} from './skinning-runtime.js';
import {
  createRigModelSession, createRigSourceSession,
} from './rig-model-session.js';
import {createRigPoseRuntime} from './rig-pose-runtime.js';
import {
  createRigRuntimeState, createWeightRuntimeState, matrixIsIdentity,
  EMPTY_ACTIVE_VERTICES, RIG_LIMB_ROLES, RIG_ROTATION_SNAP_DEGREES,
} from './weight-runtime.js';
import {createWorkBudget} from './cooperative-scheduler.js';
import {characterAxesFromOrientation} from './humanoid-orientation.js';
import {
  buildHumanoidRigBinding,
} from './humanoid-rig-binding.js';
import {
  applyHumanoidControlRigOverrides, buildHumanoidControlRig,
  resolveHumanoidControlMappings,
} from './humanoid-control-rig.js';
import {mergeHumanoidLimbPose, solveHumanoidControlIk} from './humanoid-rig-ik.js';
import {
  createHumanoidRigEditSession,
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
let weightModelSession = null;
let weightPickingSession = null;
let rigPresetSession = null;
let humanoidPoseRuntime = null;
let weightPhysicsController = null;
let modelWeightGeneration = 0;
let humanoidControlRigCacheKey = '';
let humanoidControlRigSnapshotCache = null;
let humanoidRigEditSession = null;

function clockNow() {
  return typeof globalThis.performance?.now === 'function'
    ? globalThis.performance.now() : Date.now();
}

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
  getGeneration: () => modelWeightGeneration,
  setRigLoading: loading => {
    if (modelRigState.loaded && !loading) return;
    if (!loading && modelRigState.promise) return;
    if (modelRigState.loading === !!loading) return;
    modelRigState.loading = !!loading;
    notifyModelRigChanged();
  },
});

skinningRuntime = createSkinningRuntime({
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
  setSelectedBones: (...args) => weightModelSession?.setSelectedBones(...args),
  syncPhysicsToSelection,
  refreshModelWeightSummary: (...args) =>
    weightModelSession?.refreshModelWeightSummary(...args),
  refreshSelectedWeightMask,
  eligibleSkinningMesh,
  getGeneration: () => modelWeightGeneration,
  getModelRigState: () => modelRigState,
  getRigPresetState: () => rigPresetState,
  getModelSkinningRig: () => modelSkinningRig,
  setModelSkinningRig: value => { modelSkinningRig = value; },
  invalidateHumanoidDetection,
  invalidateModelRigLoad: () => rigModelSession?.invalidateLoad(),
  clearPickedPoint: (...args) => weightPickingSession?.clearPickedPoint(...args),
  resetModelPose,
  syncPhysicsParticipants,
  buildAllSourceSkinningRigs,
  buildAllSourceSkinningRigsCooperatively,
  buildModelSkinningRig: (...args) => buildModelSkinningRig(...args),
  resetModelState: resetModelWeightState,
  notifyModelWeightChanged,
  notifyModelRigChanged,
  requestRender,
  invalidateShadow: invalidateCharacterShadowGeometry,
});

rigSourceSession = createRigSourceSession({
  states,
  knownMeshes,
  modelWeightState,
  sourceSkinningRigs,
  ensureRigMeshPrepared: (...args) =>
    skinningRuntime.ensureRigMeshPrepared(...args),
  ensureRigMeshPreparedCooperative: (...args) =>
    skinningRuntime.ensureRigMeshPreparedCooperative(...args),
  ensureInfluenceGraph: (...args) =>
    skinningRuntime.ensureInfluenceGraph(...args),
  ensureInfluenceGraphCooperative: (...args) =>
    skinningRuntime.ensureInfluenceGraphCooperative(...args),
  rebuildRestFrames: rebuildSourceRigRestFrames,
  cloneForest: cloneSourceForest,
});

weightPhysicsController = createWeightPhysicsController({
  modelPhysicsSession,
  reset: () => physicsCoordinator.reset(),
});

weightPickingSession = createWeightPickingSession({
  modelWeightState,
  modelRigState,
  states,
  knownMeshes,
  canvas: renderer.domElement,
  camera,
  controls,
  notifyChanged: notifyModelWeightChanged,
  requestRender,
  cancelRigPicking: (...args) => rigModelSession?.cancelJointPicking(...args),
});

rigPresetSession = createRigPresetSession({
  state: rigPresetState,
  getModelRig: () => modelSkinningRig,
  getModelRigState: () => modelRigState,
  getKnownMeshes: () => knownMeshes,
  resolveRigPreset,
  applyResolvedPreset: (resolved, options) =>
    applyRigPosePreset(resolved, options),
  notifyChanged: () => notifyModelRigChanged(),
});

weightModelSession = createWeightModelSession({
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
  ensureModelWeightsLoaded: () => skinningRuntime.loadModelWeights(),
});

rigPoseRuntime = createRigPoseRuntime({
  state: modelRigState,
  getRig: () => modelSkinningRig,
  sourceSkinningRigs,
  skinningRuntime,
  physicsRuntime,
  modelPhysicsSession,
  getModelTransformState,
  invalidateShadow: invalidateCharacterShadowGeometry,
  quaternionIsIdentity,
  setComponentRoot: setRigComponentRootForSource,
  resetModelPose,
  notifyChanged: notifyModelRigChanged,
  notifyPoseChanged: notifyModelRigPoseChanged,
  requestRender,
  rigPresetState,
});

humanoidPoseRuntime = createHumanoidPoseRuntime({
  modelRigState,
  getModelRig: () => modelSkinningRig,
  getPrimaryLimb: role => primaryHumanoidLimb(role),
  solveControlIk: solveHumanoidControlIk,
  mergeLimbPose: mergeHumanoidLimbPose,
  applyPose: options => rigPoseRuntime?.applyPose(options) || false,
  notifyChanged: () => notifyModelRigChanged(),
  requestRender,
});

humanoidRigEditSession = createHumanoidRigEditSession({
  modelRigState,
  getModelRig: () => modelSkinningRig,
  getAutomaticRig: () => modelSkinningRig?.humanoidAutomaticControlRig,
  resetCurrentPoseForHumanoidRigEdit: (...args) =>
    rigPoseRuntime?.resetForHumanoidEdit(...args) || false,
  setPhysicsSuspended: (...args) =>
    rigPoseRuntime?.setHumanoidEditPhysicsSuspended(...args) || false,
  getKnownMeshes: () => knownMeshes,
  resolveMappings: resolveHumanoidControlMappings,
  persist: (path, value) => window.pywebview?.api
    ?.save_humanoid_control_rig?.(path, value),
  clearPersist: path => window.pywebview?.api
    ?.clear_humanoid_control_rig?.(path),
  cancelWeightPicking: (...args) => weightPickingSession?.cancel(...args),
  cancelRigPicking: (...args) => rigModelSession?.cancelJointPicking(...args),
  refreshHumanoidRig: async savedOverrides => {
    if (!modelSkinningRig) return;
    applySavedHumanoidRig(modelSkinningRig, savedOverrides);
    modelRigState.humanoidPose = {};
    rigPoseRuntime?.applyPose({request: false});
  },
  notifyChanged: notifyModelRigChanged,
  requestRender,
});

rigModelSession = createRigModelSession({
  state: modelRigState,
  modelWeightState,
  getGeneration: () => modelWeightGeneration,
  ensureModelWeightsLoaded: () => skinningRuntime.loadModelWeights(),
  buildAllSourceSkinningRigs,
  buildAllSourceSkinningRigsCooperatively,
  buildModelSkinningRig,
  syncPhysicsToSelection,
  getSnapshot: () => rigSnapshot(),
  notifyChanged: notifyModelRigChanged,
  requestRender,
  cancelWeightPicking: (...args) => weightPickingSession?.cancel(...args),
  getModelJointId: (sourceKey, boneId) =>
    rigPoseRuntime?.getModelJointId(sourceKey, boneId),
  pickFromSurface: ({clientX, clientY} = {}) => {
    const intersection = raycastModelAtClientPoint({
      clientX, clientY, canvas: renderer.domElement, camera,
      meshes: [...knownMeshes].filter(mesh => mesh?.userData?.assetFill !== true),
    });
    const sampled = weightPickingSession?.sampleAtIntersection(intersection);
    return rigModelSession?.modelJointFromSkinningSample(sampled)?.jointId ?? null;
  },
  rotationSnapValues: RIG_ROTATION_SNAP_DEGREES,
});

export const weightRigApi = Object.freeze({
  getModelWeightState: weightModelSession.getState,
  ensureModelWeightsLoaded: weightModelSession.ensureLoaded,
  setSelectedBones: weightModelSession.setSelectedBones,
  setBoneSelected: weightModelSession.setBoneSelected,
  clearSelectedBones: weightModelSession.clearSelectedBones,
  loadSavedBoneSelection: weightModelSession.loadSavedBoneSelection,
  saveModelWeightSelection: weightModelSession.saveSelection,
  setModelWeightHeatmap: weightModelSession.setHeatmap,

  beginWeightModelPicking: weightPickingSession.begin,
  cancelWeightModelPicking: weightPickingSession.cancel,
  setWeightPickerViewMode: weightPickingSession.setViewMode,
  sampleModelSkinningAtIntersection: weightPickingSession.sampleAtIntersection,

  getModelRigState: rigModelSession.getState,
  ensureModelRigLoaded: rigModelSession.ensureLoaded,
  beginRigJointPicking: rigModelSession.beginJointPicking,
  cancelRigJointPicking: rigModelSession.cancelJointPicking,
  clearRigJointSelection: rigModelSession.clearJointSelection,
  selectRigJoint: rigModelSession.selectJoint,
  handleRigJointPicked: rigModelSession.handleJointPicked,
  modelJointFromSkinningSample: rigModelSession.modelJointFromSkinningSample,
  pickRigJointFromModelSurface: rigModelSession.pickJointFromSurface,
  setRigRotationSnapDegrees: rigModelSession.setRotationSnapDegrees,

  finishRigJointPose: rigPoseRuntime.finishPose,
  getRigJointPoseFrame: rigPoseRuntime.getFrame,
  resetRigJoint: rigPoseRuntime.resetJoint,
  resetRigPose: rigPoseRuntime.resetPose,
  setRigJointRoot: rigPoseRuntime.setRoot,
  setRigJointRotation: rigPoseRuntime.setRotation,
  setRigPoseControlStatus: rigPoseRuntime.setStatus,

  setRigActiveLimbRole: humanoidPoseRuntime.setActiveLimbRole,
  setRigIkEnabled: humanoidPoseRuntime.setIkEnabled,
  selectHumanoidControl: humanoidPoseRuntime.selectControl,
  solveRigIkTarget: humanoidPoseRuntime.solveTarget,

  getHumanoidRigEditSnapshot: humanoidRigEditSession.snapshot,
  setHumanoidRigMetadata: humanoidRigEditSession.setMetadata,
  beginHumanoidRigEdit: humanoidRigEditSession.begin,
  cancelHumanoidRigEdit: humanoidRigEditSession.cancel,
  saveHumanoidRigEdit: humanoidRigEditSession.save,
  resetHumanoidRig: humanoidRigEditSession.reset,
  beginHumanoidControlCarry: humanoidRigEditSession.beginCarry,
  updateHumanoidControlDraft: humanoidRigEditSession.updateDraft,
  finishHumanoidControlCarry: humanoidRigEditSession.finishCarry,
  cancelHumanoidControlCarry: humanoidRigEditSession.cancelCarry,

  getRigPresetSnapshot: rigPresetSession.snapshot,
  setRigMetadata: rigPresetSession.setMetadata,
  applyRigPosePresetById: rigPresetSession.applyById,
  saveRigPosePreset: rigPresetSession.save,
  renameRigPosePreset: rigPresetSession.rename,
  deleteRigPosePreset: rigPresetSession.remove,

  getModelPhysicsState: weightPhysicsController.getState,
  resetModelPhysics: weightPhysicsController.reset,
  setPhysicsFrequency: weightPhysicsController.setFrequency,
  setPhysicsDamping: weightPhysicsController.setDamping,
  setPhysicsMotionStrength: weightPhysicsController.setMotionStrength,
  setPhysicsLinearMotionStrength: weightPhysicsController.setLinearMotionStrength,
  setPhysicsContinuousLinearResponse:
    weightPhysicsController.setContinuousLinearResponse,
  setPhysicsGravityEnabled: weightPhysicsController.setGravityEnabled,
  setPhysicsGravityScale: weightPhysicsController.setGravityScale,
  setPhysicsConstraintsEnabled: weightPhysicsController.setConstraintsEnabled,
  setPhysicsMaxBendDegrees: weightPhysicsController.setMaxBendDegrees,
});

export {skinningRuntime as weightRigSkinningRuntime};

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
  if (snapshot) {
    snapshot.humanoidControlRig = humanoidControlRigSnapshot();
    snapshot.jointBuild = modelSkinningRig?.jointBuildDiagnostics
      ? {...modelSkinningRig.jointBuildDiagnostics} : null;
  }
  return snapshot;
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
    role, keys: keys || [], available, bendSign: 1,
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
    selectedHumanoidControlKey: modelRigState.selectedHumanoidControlKey || null,
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
    selectedHumanoidControlKey: modelRigState.selectedHumanoidControlKey || null,
    physicsActive: rigPoseRuntime?.hasActivePhysics() || false,
    rotationSnapDegrees: modelRigState.rotationSnapDegrees,
    ik: ikSnapshot(),
    pickStatus: modelRigState.pickStatus,
    rigPresets: rigPresetSession?.snapshot() || null,
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
    const jointId = rigPoseRuntime?.getModelJointId(rig?.sourceKey, boneId);
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
    const sourceKeyText = String(sourceKey);
    const nextSegment = sourceKeyText.indexOf('|', separator + 8);
    const fallbackOffset = Number(sourceKeyText.slice(
      separator + 8, nextSegment < 0 ? undefined : nextSegment));
    const descriptor = modelWeightState.sourceDescriptors.get(sourceKey)
      || (separator > 0 && Number.isInteger(fallbackOffset)
        ? {
          sourceKey,
          sourceFile: sourceKeyText.slice(0, separator),
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
  if (!selected.size) {
    state.selectedWeightMask = null;
    state.physicsActiveVertices = EMPTY_ACTIVE_VERTICES;
    state.combinedPhysicsVerticesRef = null;
    state.combinedActiveVertices = null;
    return null;
  }
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
  rigPresetSession?.reset();
  humanoidRigEditSession?.resetSession();
  weightPickingSession?.reset();
  sourcePhysicsRigs.clear();
  sourceSkinningRigs.clear();
  rigSourceSession?.reset?.();
  modelSkinningRig = null;
  weightRuntime.resetModelWeightState();
  weightModelSession?.reset();
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

function ensureSourceSkinningRigCooperatively(sourceKey, members, options) {
  return rigSourceSession?.ensureCooperative(sourceKey, members, options)
    || Promise.resolve(null);
}

function buildAllSourceSkinningRigsCooperatively(options) {
  const startedAt = clockNow();
  return (rigSourceSession?.buildAllCooperative(options)
    || Promise.resolve([])).then(result => {
      if (!options?.isCurrent || options.isCurrent()) {
        modelRigState.performance.sourceRigPreparationMs =
          clockNow() - startedAt;
        const stats = (result || []).map(rig => rig.__cooperativeStats || {});
        const timings = (result || []).map((rig, index) => {
          const timing = rig.__cooperativeTimings;
          const stat = rig.__cooperativeStats || {};
          return timing ? {
            ...timing,
            largestChunkMs: Number(stat?.largestChunkMs) || 0,
            yieldCount: Number(stat?.yieldCount) || 0,
          } : null;
        }).filter(Boolean);
        modelRigState.performance.sourceRigLargestChunkMs = Math.max(
          0, ...stats.map(item => Number(item.largestChunkMs) || 0));
        modelRigState.performance.sourceRigYieldCount = stats.reduce(
          (sum, item) => sum + (Number(item.yieldCount) || 0), 0);
        modelRigState.performance.sourceRigDetails = timings;
      }
      return result;
    });
}

function resetSourceSkinningPose(rig) {
  return rigSourceSession?.resetPose(rig);
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

function modelRigFolderPath() {
  return [...knownMeshes]
    .find(mesh => mesh?.userData?.modPath)?.userData?.modPath || null;
}

async function loadPersistedModelRig() {
  const api = globalThis.window?.pywebview?.api;
  const path = modelRigFolderPath();
  if (!path || typeof api?.load_model_rig !== 'function') return null;
  try {
    return await api.load_model_rig(path);
  } catch (_error) {
    // An unreadable cache is equivalent to a cache miss. The normal
    // reconciliation path remains the source of truth.
    return null;
  }
}

async function savePersistedModelRig(value) {
  const api = globalThis.window?.pywebview?.api;
  const path = modelRigFolderPath();
  if (!path || typeof api?.save_model_rig !== 'function') return false;
  try {
    const result = await api.save_model_rig(path, value);
    return result?.saved === true;
  } catch (_error) {
    return false;
  }
}

async function buildModelSkinningRig(sourceRigs = [...sourceSkinningRigs.values()], {
    generation = null, isCurrent = () => true,
  } = {}) {
  const startedAt = clockNow();
  const performance = modelRigState.performance || {};
  modelRigState.performance = performance;
  const budget = createWorkBudget();
  const checkpoint = async () => {
    if (generation !== null && !isCurrent()) return false;
    await budget.checkpoint();
    return generation === null || isCurrent();
  };
  if (!(await checkpoint())) return null;
  const previousSelectedJointId = modelRigState.selectedJointId;
  const previousRootSignatures = new Set(
    modelRigState.explicitRootSignatures);
  if (modelSkinningRig) resetModelPose({request: false});
  const lifecycle = await loadOrBuildModelRig({
    load: () => loadPersistedModelRig(),
    hydrate: saved => hydrateModelRig(saved?.model_rig || saved, sourceRigs),
    build: () => buildModelRigReconciliationCooperative(sourceRigs, {}, {
      budget,
      isCurrent: () => generation === null || isCurrent(),
      timings: performance,
    }),
  });
  const reconciliation = lifecycle.modelRig;
  const hydratedFromCache = lifecycle.hydratedFromCache;
  if (!reconciliation) return null;
  performance.modelRigCacheHit = hydratedFromCache;
  performance.modelRigHydrationMs = hydratedFromCache
    ? clockNow() - startedAt : 0;
  performance.reconciliationMs = hydratedFromCache
    ? 0 : clockNow() - startedAt;
  if (!(await checkpoint())) return null;
  const joints = reconciliation.joints || [];
  const rig = {
    key: 'model-rig',
    sourceKey: 'model-rig',
    sourceRigs: [...sourceRigs],
    modelReferenceRadius: reconciliation.modelReferenceRadius
      || reconciliation.reconciliation?.modelReferenceRadius || 0,
    reconciliation,
    joints,
    edges: reconciliation.edges || [],
    inferredForest: {
      components: reconciliation.components || [],
      componentByBoneId: reconciliation.componentByJointId || new Map(),
    },
    components: reconciliation.components || [],
    componentByJointId: reconciliation.componentByJointId || new Map(),
    centerByJointId: reconciliation.centerByJointId
      || new Map(joints.map(joint => [
        joint.jointId, joint.restCenter || [0, 0, 0]])),
    jointPivotByJointId: reconciliation.jointPivotByJointId
      || new Map(joints.map(joint => [
        joint.jointId, joint.restPivot || joint.restCenter || [0, 0, 0]])),
    jointPivotByEdgeKey: reconciliation.jointPivotByEdgeKey || null,
    restFrameByJointId: reconciliation.restFrameByJointId
      ? new Map([...reconciliation.restFrameByJointId].map(([jointId, frame]) =>
        [jointId, cloneRigQuaternion(frame)]))
      : new Map(joints.map(joint => [
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
    poseTransformCache: new Map(),
    poseFrameCache: new Map(),
    poseAffectedJointIds: new Set(),
    poseActiveJointKey: '',
    poseActiveVerticesByMesh: new Map(),
    poseSourceBoneIdsByMesh: new Map(),
    poseRevision: 0,
    structureRevision: ++rigRuntime.structureRevision,
    jointBuildDiagnostics: {
      mergeCount: reconciliation.reconciliation?.equivalenceClusterCount || 0,
      sourceBoneCount: reconciliation.reconciliation?.sourceBoneCount
        || reconciliation.sourceBoneEvidence?.length || 0,
      componentCount: (reconciliation.components || []).length,
      edgeCount: (reconciliation.edges || []).length,
    },
  };
  if (!(await checkpoint())) return null;
  // Install provenance-derived edge pivots before capturing the default
  // orientation. Reset Pose must restore the same pivots used on first load.
  rebuildModelRestFrames(rig, rig.inferredForest);
  const persistedModelRig = !hydratedFromCache
    ? serializeModelRig(rig, {sourceRigs}) : null;
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
  if (!(await checkpoint())) return null;
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
  if (!(await checkpoint())) return null;
  modelSkinningRig = rig;
  if (persistedModelRig) {
    performance.modelRigCacheSaved = await savePersistedModelRig(
      persistedModelRig);
  }
  buildPrimaryHumanoidRig(rig);
  skinningRuntime.updateModelWeightHeatmap();
  modelRigState.structureRevision = rig.structureRevision;
  modelRigState.selectedJointId = Number.isInteger(previousSelectedJointId)
    && joints[previousSelectedJointId] ? previousSelectedJointId : null;
  rigPoseRuntime?.updatePoseFrameCache(rig, rig.poseTransforms);
  performance.totalRigBuildMs = clockNow() - startedAt;
  const modelStats = budget.getStats();
  performance.modelRigLargestChunkMs = modelStats.largestChunkMs;
  performance.modelRigYieldCount = modelStats.yieldCount;
  return rig;
}

function buildPrimaryHumanoidRig(rig) {
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  const orientationState = getModelTransformState?.();
  const automaticRig = buildHumanoidControlRig({
    meshes: [...knownMeshes],
    axes: humanoidSemanticAxes(),
    orientationState,
  });
  rig.humanoidAutomaticControlRig = automaticRig;
  applySavedHumanoidRig(rig,
    humanoidRigEditSession?.getSavedOverrides?.());
}

function applySavedHumanoidRig(rig, savedOverrides = null) {
  if (!rig) return;
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  const orientationState = getModelTransformState?.();
  const automaticRig = rig.humanoidAutomaticControlRig;
  const controlMappings = automaticRig?.accepted
    ? resolveHumanoidControlMappings({
      savedOverrides, modelRig: rig,
    }) : new Map();
  const controlRig = automaticRig?.accepted
    ? applyHumanoidControlRigOverrides({
      automaticRig, savedOverrides, modelRig: rig,
      resolvedMappings: controlMappings,
    }) : automaticRig;
  const binding = controlRig?.accepted
    ? buildHumanoidRigBinding({controlRig, modelRig: rig, controlMappings}) : null;
  rig.humanoidControlRig = controlRig;
  rig.humanoidControlMappings = controlMappings;
  rig.humanoidBinding = binding;
  rig.humanoidOrientationRevision = Number(
    orientationState?.modelOrientationRevision) || 0;
  modelRigState.humanoidControlRig = controlRig;
  modelRigState.humanoidStructureRevision = rig.structureRevision;
}

function sourceMembersMatch(rig, members) {
  return rig?.meshes?.size === members.length
    && members.every(mesh => rig.meshes.has(mesh));
}

function buildSourcePhysicsRig(sourceKey, members, skinRig) {
  const descriptor = modelWeightState.sourceDescriptors.get(sourceKey);
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

function createSourcePhysicsRig(sourceKey, members, options = {}) {
  const cached = sourceSkinningRigs.get(sourceKey);
  if (cached && sourceMembersMatch(cached, members)) {
    // A prepared source rig is safe to consume synchronously. This preserves
    // immediate participant updates while first-time preparation remains
    // cooperative below.
    return buildSourcePhysicsRig(sourceKey, members, cached);
  }
  return (async () => {
    const skinRig = await ensureSourceSkinningRigCooperatively(
      sourceKey, members, options);
    if (!skinRig) return null;
    return buildSourcePhysicsRig(sourceKey, members, skinRig);
  })();
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
  const changed = rigPoseRuntime?.applyPose({request}) || false;
  modelSkinningRig.poseActiveVerticesByMesh.clear();
  modelSkinningRig.poseSourceBoneIdsByMesh.clear();
  return changed;
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
    modelRigState.pickStatus = weightRigStatus(
      'weightRig.status.invalidSavedPose');
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
    modelRigState.pickStatus = weightRigStatus(
      resolved.skipped?.length
        ? 'weightRig.status.noMatchingJoints'
        : 'weightRig.status.invalidSavedPose');
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
  const changed = rigPoseRuntime?.applyPose({request: false, dragging: false})
    || false;
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
  const jointId = rigPoseRuntime?.getModelJointId(sourceKey, id);
  if (!Number.isInteger(jointId) || !modelSkinningRig) return false;
  rigPoseRuntime?.clearManualPose({request: false});
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
  rigPoseRuntime?.applyPose({request: false});
  notifyModelRigChanged();
  requestRender();
  return true;
}

function syncPhysicsParticipants(...args) {
  const result = physicsCoordinator?.syncParticipants(...args);
  return result?.catch?.(() => false) || result;
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
    rigPoseRuntime?.applyPose({request: false});
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
