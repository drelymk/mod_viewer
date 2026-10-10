// Shared ModelRig engine and Weight/Rig composition root. This module is
// imported by the optional Weight/Rig feature boundary on first use.

import * as THREE from 'three';
import { bridgeReady } from '../app/bridge.js';
import {
  camera,
  controls,
  getModelTransformState,
  invalidateCharacterShadowGeometry,
  renderer,
} from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';
import { rebuildModelRestFrames, rebuildSourceRigRestFrames } from './weight-rig-frames.js';
import { buildModelRigReconciliationCooperative, sourceBoneKey } from './weight-rig-reconcile.js';
import {
  selectionMapFromEntries,
  selectionRecordsFromMap,
  sourceSelectionEntries,
  selectedBoneCount,
  serializeBoneSelection,
} from './weight-selection.js';
import { raycastModelAtClientPoint } from '../scene/model-picking.js';
import { createWeightPhysicsRuntime } from './weight-physics-runtime.js';
import { modelRigSnapshot } from './weight-rig-snapshots.js';
import { buildJointSignatureIndex, resolveRigPreset } from './weight-rig-presets.js';
import { createRigPresetSession } from './rig-preset-session.js';
import { createWeightModelSession, createWeightPickingSession } from './weight-model-session.js';
import { createSkinningRuntime } from './skinning-runtime.js';
import { createRigModelSession, createRigSourceSession } from './rig-model-session.js';
import { createWeightRigActivationSession } from './weight-rig-activation-session.js';
import { createRigPoseRuntime } from './rig-pose-runtime.js';
import {
  createRigRuntimeState,
  createWeightRuntimeState,
  matrixIsIdentity,
  RIG_LIMB_ROLES,
  RIG_ROTATION_SNAP_DEGREES,
} from './weight-runtime.js';
import { createWorkBudget } from './cooperative-scheduler.js';
import { characterAxesFromOrientation } from './humanoid-orientation.js';
import { buildHumanoidRigBinding } from './humanoid-rig-binding.js';
import {
  applyHumanoidControlRigOverrides,
  buildHumanoidControlRig,
  resolveHumanoidControlMappings,
} from './humanoid-control-rig.js';
import { mergeHumanoidLimbPose, solveHumanoidControlIk } from './humanoid-rig-ik.js';
import { createHumanoidRigEditSession } from './humanoid-rig-edit-session.js';
import { fitHumanoidGeometryRig } from './humanoid-geometry-fit.js';

const weightRuntime = createWeightRuntimeState();
const { states, knownMeshes, modelWeightState, stateFor } = weightRuntime;

function humanoidSemanticAxes({ requireReady = false } = {}) {
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
const { modelRigState, rigPresetState } = rigRuntime;
const sourceSkinningRigs = new Map();
let modelSkinningRig = null;
let skinningRuntime = null;
let rigModelSession = null;
let rigPoseRuntime = null;
let rigSourceSession = null;
let weightModelSession = null;
let weightPickingSession = null;
let rigPresetSession = null;
let weightRigActivationSession = null;
let modelWeightGeneration = 0;
let humanoidControlRigCacheKey = '';
let humanoidControlRigSnapshotCache = null;
let humanoidRigEditSession = null;
let geometryRigPreview = null;
let geometryPreviewSerial = 0;

function clockNow() {
  return typeof globalThis.performance?.now === 'function' ? globalThis.performance.now() : Date.now();
}

function invalidateHumanoidDetection() {
  geometryRigPreview = null;
  geometryPreviewSerial += 1;
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  modelRigState.humanoidControlRig = null;
  modelRigState.humanoidPose = {};
  modelRigState.humanoidStructureRevision = null;
}

function cloneModelComponent(component) {
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

function cloneSourceForest(forest) {
  return {
    ...forest,
    components: (forest?.components || []).map(cloneModelComponent),
    componentByBoneId: { ...(forest?.componentByBoneId || {}) },
    edges: (forest?.edges || []).map((edge) => ({ ...edge })),
    nodeIds: [...(forest?.nodeIds || [])],
  };
}

const physicsRuntime = createWeightPhysicsRuntime({
  states,
  knownMeshes,
  modelWeightState,
  selectedBoneCount,
  eligibleSkinningMesh,
  getSourceSkinningRig: (sourceKey) => sourceSkinningRigs.get(sourceKey),
  ensureSourceSkinningRigCooperatively,
  getModelTransformState,
  notifyModelRigChanged,
  getGeneration: () => modelWeightGeneration,
  getModelSkinningRig: () => modelSkinningRig,
  applyDeformation: (...args) => skinningRuntime?.applyDeformation(...args),
  finalizePhysicsGeometry: (...args) => skinningRuntime?.finalizeDeformationGeometry(...args),
  markFinalBoundsDirty: (...args) => skinningRuntime?.markFinalBoundsDirty(...args),
});
const { modelPhysicsSession } = physicsRuntime;

skinningRuntime = createSkinningRuntime({
  states,
  knownMeshes,
  stateFor,
  requestRender,
});

rigSourceSession = createRigSourceSession({
  states,
  knownMeshes,
  modelWeightState,
  sourceSkinningRigs,
  ensureRigMeshPrepared: (...args) => skinningRuntime.ensureRigMeshPrepared(...args),
  ensureInfluenceGraphCooperative: (...args) => skinningRuntime.ensureInfluenceGraphCooperative(...args),
  rebuildRestFrames: (rig) => rebuildSourceRigRestFrames(rig, () => ++rigRuntime.structureRevision),
  cloneForest: cloneSourceForest,
});

weightPickingSession = createWeightPickingSession({
  modelWeightState,
  states,
  knownMeshes,
  canvas: renderer.domElement,
  camera,
  controls,
  notifyChanged: notifyModelWeightChanged,
  requestRender,
});

rigPresetSession = createRigPresetSession({
  state: rigPresetState,
  getModelRig: () => modelSkinningRig,
  getModelRigState: () => modelRigState,
  getKnownMeshes: () => knownMeshes,
  resolveRigPreset,
  applyResolvedPreset: (resolved, options) => rigPoseRuntime?.applyResolvedPreset(resolved, options),
  notifyChanged: () => notifyModelRigChanged(),
});

weightModelSession = createWeightModelSession({
  modelWeightState,
  states,
  stateFor,
  knownMeshes,
  modelWeightSnapshot,
  selectionMapFromEntries,
  sourceSelectionEntries: (selectionMap) => sourceSelectionEntries(selectionMap, modelWeightState.sourceDescriptors),
  refreshSelectedWeightMask: (...args) => skinningRuntime.refreshSelectedWeightMask(...args),
  updateModelWeightHeatmap: (...args) => skinningRuntime.updateModelWeightHeatmap(...args),
  installSkinningEntry: (...args) => skinningRuntime.installSkinningEntry(...args),
  syncPhysicsToSelection: physicsRuntime.syncToSelection,
  serializeBoneSelection,
  eligibleSkinningMesh,
  notifyChanged: () => notifyModelWeightChanged(),
  requestRender,
  getGeneration: () => modelWeightGeneration,
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
  getModelJointId: modelJointIdForSourceBone,
  hasActivePhysics: physicsRuntime.hasActivePhysics,
  cloneForest: cloneSourceForest,
  nextStructureRevision: () => ++rigRuntime.structureRevision,
  notifyChanged: notifyModelRigChanged,
  notifyPoseChanged: notifyModelRigPoseChanged,
  requestRender,
  getPrimaryLimb: (role) => primaryHumanoidLimb(role),
  solveControlIk: solveHumanoidControlIk,
  mergeLimbPose: mergeHumanoidLimbPose,
});

humanoidRigEditSession = createHumanoidRigEditSession({
  getModelRig: () => modelSkinningRig,
  getAutomaticRig: () => modelSkinningRig?.humanoidAutomaticControlRig,
  resetCurrentPoseForHumanoidRigEdit: (...args) => rigPoseRuntime?.resetForHumanoidEdit(...args) || false,
  setPhysicsSuspended: (...args) => rigPoseRuntime?.setHumanoidEditPhysicsSuspended(...args) || false,
  getKnownMeshes: () => knownMeshes,
  resolveMappings: resolveHumanoidControlMappings,
  persist: async (path, value) => (await bridgeReady()).save_humanoid_control_rig(path, value),
  clearPersist: async (path) => (await bridgeReady()).clear_humanoid_control_rig(path),
  cancelWeightPicking: (...args) => weightPickingSession?.cancel(...args),
  cancelRigPicking: (...args) => rigModelSession?.cancelJointPicking(...args),
  refreshHumanoidRig: async (savedOverrides) => {
    if (!modelSkinningRig) return;
    applySavedHumanoidRig(modelSkinningRig, savedOverrides);
    modelRigState.humanoidPose = {};
    rigPoseRuntime?.applyPose({ request: false });
  },
  notifyChanged: notifyModelRigChanged,
  requestRender,
});

rigModelSession = createRigModelSession({
  state: modelRigState,
  modelWeightState,
  getGeneration: () => modelWeightGeneration,
  ensureModelWeightsLoaded: () => weightModelSession.ensureLoaded(),
  buildAllSourceSkinningRigsCooperatively,
  buildModelSkinningRig,
  getSnapshot: () => rigSnapshot(),
  notifyChanged: notifyModelRigChanged,
  requestRender,
  getModelJointId: (sourceKey, boneId) => modelJointIdForSourceBone(sourceKey, boneId),
  pickFromSurface: ({ clientX, clientY } = {}) => {
    const intersection = raycastModelAtClientPoint({
      clientX,
      clientY,
      canvas: renderer.domElement,
      camera,
      meshes: [...knownMeshes].filter((mesh) => mesh?.userData?.assetFill !== true),
    });
    const sampled = weightPickingSession?.sampleAtIntersection(intersection);
    return rigModelSession?.modelJointFromSkinningSample(sampled)?.jointId ?? null;
  },
  rotationSnapValues: RIG_ROTATION_SNAP_DEGREES,
});

weightRigActivationSession = createWeightRigActivationSession({
  getGeneration: () => modelWeightGeneration,
  ensureWeightsLoaded: weightModelSession.ensureLoaded,
  restoreSavedSelection: weightModelSession.restoreSavedSelection,
  ensureRigLoaded: rigModelSession.ensureLoaded,
  syncPhysicsToSelection: physicsRuntime.syncToSelection,
});

function beginWeightModelPicking(...args) {
  rigModelSession?.cancelJointPicking();
  return weightPickingSession?.begin(...args) || false;
}

function beginRigJointPicking(...args) {
  weightPickingSession?.cancel();
  return rigModelSession?.beginJointPicking(...args) || false;
}

async function previewGeometryRig() {
  const serial = ++geometryPreviewSerial;
  geometryRigPreview = { busy: true, mode: 'geometry', revision: serial, rig: null, result: null, errorKey: null };
  notifyModelRigChanged();
  requestRender();
  const meshes = [...knownMeshes];
  // Preview only a rest-pose model. Never reset a pose, edit, or simulation on
  // the user's behalf, and never acquire a deformation/animation owner.
  const atRest = meshes
    .filter((mesh) => mesh.visible)
    .every((mesh) => {
      const rest = mesh.userData?.humanoidRestPositions || mesh.userData?.basePositions;
      const positions = mesh.geometry?.attributes?.position?.array;
      return (
        rest && positions && rest.length === positions.length && rest.every((v, i) => Math.abs(v - positions[i]) < 1e-6)
      );
    });
  if (
    !atRest ||
    humanoidRigEditSession.snapshot()?.editing ||
    physicsRuntime.getState().enabled ||
    modelSkinningRig?.poseRotationByJointId?.size
  ) {
    geometryRigPreview.busy = false;
    geometryRigPreview.errorKey = 'weightRig.geometryRequiresRest';
    notifyModelRigChanged();
    return false;
  }
  const visibility = meshes.map((mesh) => mesh.visible);
  const isCurrent = () =>
    serial === geometryPreviewSerial &&
    meshes.every((mesh, i) => knownMeshes.has(mesh) && mesh.visible === visibility[i]);
  try {
    const result = await fitHumanoidGeometryRig({ meshes, orientationState: getModelTransformState(), isCurrent });
    if (!isCurrent()) {
      if (serial === geometryPreviewSerial) clearGeometryRigPreview();
      return false;
    }
    geometryRigPreview.result = result;
    geometryRigPreview.rig = result?.available ? result : null;
    geometryRigPreview.errorKey = result?.available ? null : 'weightRig.geometryInsufficient';
  } catch {
    if (!isCurrent()) return false;
    geometryRigPreview.errorKey = 'weightRig.geometryInsufficient';
  }
  geometryRigPreview.busy = false;
  notifyModelRigChanged();
  requestRender();
  return !!geometryRigPreview.rig;
}

function setGeometryRigPreviewMode(mode) {
  if (mode === 'current') return clearGeometryRigPreview();
  if (!geometryRigPreview?.result?.available || !['geometry', 'proportional'].includes(mode)) return false;
  geometryRigPreview.mode = mode;
  geometryRigPreview.rig = mode === 'geometry' ? geometryRigPreview.result : geometryRigPreview.result.proportionalRig;
  geometryRigPreview.revision += 1;
  notifyModelRigChanged();
  requestRender();
  return true;
}

function clearGeometryRigPreview() {
  geometryPreviewSerial += 1;
  geometryRigPreview = null;
  notifyModelRigChanged();
  requestRender();
  return true;
}

export const weightRigApi = Object.freeze({
  getModelWeightState: weightModelSession.getState,
  activateWeightRig: weightRigActivationSession.activate,
  setBoneSelected: weightModelSession.setBoneSelected,
  clearSelectedBones: weightModelSession.clearSelectedBones,
  loadSavedBoneSelection: weightModelSession.loadSavedBoneSelection,
  saveModelWeightSelection: weightModelSession.saveSelection,
  setModelWeightHeatmap: weightModelSession.setHeatmap,

  beginWeightModelPicking,
  cancelWeightModelPicking: weightPickingSession.cancel,
  setWeightPickerViewMode: weightPickingSession.setViewMode,

  getModelRigState: rigModelSession.getState,
  previewGeometryRig,
  setGeometryRigPreviewMode,
  clearGeometryRigPreview,
  beginRigJointPicking,
  cancelRigJointPicking: rigModelSession.cancelJointPicking,
  clearRigJointSelection: rigModelSession.clearJointSelection,
  selectRigJoint: rigModelSession.selectJoint,
  handleRigJointPicked: rigModelSession.handleJointPicked,
  pickRigJointFromModelSurface: rigModelSession.pickJointFromSurface,
  setRigRotationSnapDegrees: rigModelSession.setRotationSnapDegrees,

  finishRigJointPose: rigPoseRuntime.finishPose,
  getRigJointPoseFrame: rigPoseRuntime.getFrame,
  resetRigJoint: rigPoseRuntime.resetJoint,
  resetRigPose,
  setRigJointRoot: rigPoseRuntime.setRoot,
  setRigJointRotation: rigPoseRuntime.setRotation,
  setRigPoseControlStatus: rigPoseRuntime.setStatus,

  setRigActiveLimbRole: rigPoseRuntime.setActiveLimbRole,
  setRigIkEnabled: rigPoseRuntime.setIkEnabled,
  selectHumanoidControl: rigPoseRuntime.selectControl,
  solveRigIkTarget: rigPoseRuntime.solveTarget,

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

  setRigMetadata: rigPresetSession.setMetadata,
  applyRigPosePresetById: rigPresetSession.applyById,
  saveRigPosePreset: rigPresetSession.save,
  renameRigPosePreset: rigPresetSession.rename,
  deleteRigPosePreset: rigPresetSession.remove,

  getModelPhysicsState: physicsRuntime.getState,
  resetModelPhysics: physicsRuntime.reset,
  setPhysicsFrequency: physicsRuntime.setFrequency,
  setPhysicsDamping: physicsRuntime.setDamping,
  setPhysicsMotionStrength: physicsRuntime.setMotionStrength,
  setPhysicsLinearMotionStrength: physicsRuntime.setLinearMotionStrength,
  setPhysicsContinuousLinearResponse: physicsRuntime.setContinuousLinearResponse,
  setPhysicsGravityEnabled: physicsRuntime.setGravityEnabled,
  setPhysicsGravityScale: physicsRuntime.setGravityScale,
  setPhysicsConstraintsEnabled: physicsRuntime.setConstraintsEnabled,
  setPhysicsMaxBendDegrees: physicsRuntime.setMaxBendDegrees,
});

export { skinningRuntime as weightRigSkinningRuntime };

function modelWeightSnapshot() {
  const selectedBones = selectionRecordsFromMap(
    modelWeightState.selectedBonesBySource,
    modelWeightState.sourceDescriptors,
  );
  const savedBones = selectionRecordsFromMap(modelWeightState.savedBonesBySource, modelWeightState.sourceDescriptors);
  return {
    loaded: modelWeightState.loaded,
    loading: modelWeightState.loading,
    generation: modelWeightGeneration,
    error: modelWeightState.error,
    noWeights: modelWeightState.noWeights,
    sources: modelWeightState.sources.map((source) => ({
      ...source,
      availableBoneIds: [...source.availableBoneIds],
      boneStats: Object.fromEntries(Object.entries(source.boneStats || {}).map(([id, stats]) => [id, { ...stats }])),
    })),
    selectedBones,
    savedBones,
    selectedBoneCount: selectedBoneCount(selectedBones),
    pickedPoint: modelWeightState.pickedPoint
      ? {
          ...modelWeightState.pickedPoint,
          point: [...modelWeightState.pickedPoint.point],
          influences: modelWeightState.pickedPoint.influences.map((influence) => ({ ...influence })),
        }
      : null,
    pickerViewMode: modelWeightState.pickerViewMode,
    pickStatus: modelWeightState.pickStatus,
    picking: modelWeightState.picking,
    savedSelectionApplied: modelWeightState.savedSelectionApplied,
    savedSelectionMasksRestored: modelWeightState.savedSelectionMasksRestored,
    savingSelection: modelWeightState.savingSelection,
    selectionSaveError: modelWeightState.selectionSaveError,
    heatmapEnabled: modelWeightState.heatmapEnabled,
    loadedMeshCount: modelWeightState.loadedMeshCount,
    failedMeshCount: modelWeightState.failedMeshCount,
  };
}

function cloneRigQuaternion(value) {
  if (value?.isQuaternion) return value.clone().normalize();
  const values =
    Array.isArray(value) || ArrayBuffer.isView(value)
      ? [...value].slice(0, 4).map(Number)
      : [value?.x, value?.y, value?.z, value?.w].map(Number);
  return values.length === 4 && values.every(Number.isFinite)
    ? new THREE.Quaternion(...values).normalize()
    : new THREE.Quaternion();
}

function quaternionIsIdentity(value) {
  const x = Number(value?.x);
  const y = Number(value?.y);
  const z = Number(value?.z);
  const w = Number(value?.w);
  return (
    Number.isFinite(x) &&
    Number.isFinite(y) &&
    Number.isFinite(z) &&
    Number.isFinite(w) &&
    Math.abs(x) < 1e-8 &&
    Math.abs(y) < 1e-8 &&
    Math.abs(z) < 1e-8 &&
    Math.abs(Math.abs(w) - 1) < 1e-8
  );
}

function modelRigSnapshotForState() {
  const snapshot = modelRigSnapshot(modelSkinningRig, {
    quaternionIsIdentity,
    matrixIsIdentity,
  });
  if (snapshot) {
    snapshot.humanoidControlRig = humanoidControlRigSnapshot();
    snapshot.jointBuild = modelSkinningRig?.jointBuildDiagnostics
      ? { ...modelSkinningRig.jointBuildDiagnostics }
      : null;
  }
  return snapshot;
}

function humanoidControlRigSnapshot() {
  const rig = modelSkinningRig?.humanoidControlRig || modelRigState.humanoidControlRig;
  const orientationState = getModelTransformState?.();
  const orientationRevision = Number.isFinite(Number(orientationState?.modelOrientationRevision))
    ? Number(orientationState.modelOrientationRevision)
    : 0;
  const cacheKey = `${modelRigState.humanoidStructureRevision ?? 'none'}:${orientationRevision}:${
    modelSkinningRig?.poseRevision || 0
  }`;
  if (humanoidControlRigSnapshotCache && humanoidControlRigCacheKey === cacheKey) {
    return humanoidControlRigSnapshotCache;
  }
  if (!rig) {
    humanoidControlRigSnapshotCache = {
      version: 1,
      source: 'humanoid_control_rig',
      mode: 'primary_driver',
      available: false,
      accepted: false,
      confidence: 'low',
      controls: {},
      diagnostics: { reason: 'rig_not_loaded' },
    };
  } else {
    const controls = Object.fromEntries(
      Object.entries(rig.controls || {}).map(([key, control]) => [
        key,
        {
          ...control,
          position: Array.isArray(modelRigState.humanoidPose?.[key])
            ? [...modelRigState.humanoidPose[key]]
            : [...(control.position || [0, 0, 0])],
        },
      ]),
    );
    humanoidControlRigSnapshotCache = {
      version: Number(rig.version) || 1,
      source: 'humanoid_control_rig',
      mode: 'primary_driver',
      available: rig.available !== false,
      accepted: rig.accepted === true,
      confidence: rig.confidence || 'low',
      confidenceByRegion: { ...(rig.confidenceByRegion || {}) },
      controls,
      diagnostics: {
        reason: rig.diagnostics?.reason || null,
        templatePoints: { ...(rig.diagnostics?.templatePoints || {}) },
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
  const available = !!(keys && rig?.accepted && keys.every((key) => rig.controls?.[key]?.position));
  return {
    role,
    keys: keys || [],
    available,
    bendSign: 1,
  };
}

function ikSnapshot() {
  const active = primaryHumanoidLimb();
  const mappings = Object.fromEntries(
    RIG_LIMB_ROLES.map((role) => {
      const limb = primaryHumanoidLimb(role);
      return [
        role,
        {
          role,
          available: limb.available,
          confidence: limb.available ? 'high' : 'low',
          bendSign: limb.bendSign,
          controlKeys: [...limb.keys],
          reason: limb.available ? null : 'control_unavailable',
        },
      ];
    }),
  );
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
    geometryRigPreview: geometryRigPreview && {
      busy: geometryRigPreview.busy,
      mode: geometryRigPreview.mode,
      revision: geometryRigPreview.revision,
      rig: geometryRigPreview.rig,
      errorKey: geometryRigPreview.errorKey,
      diagnostics: geometryRigPreview.result?.diagnostics,
    },
    loaded: modelRigState.loaded,
    loading: modelRigState.loading,
    error: modelRigState.error,
    sourceErrors: rigSourceSession.getErrors(),
    jointPickIntent: modelRigState.jointPickIntent ? { ...modelRigState.jointPickIntent } : null,
    structureRevision: modelRigState.structureRevision,
    selectedJointId: modelRigState.selectedJointId,
    selectedHumanoidControlKey: modelRigState.selectedHumanoidControlKey || null,
    physicsActive: physicsRuntime.hasActivePhysics(),
    rotationSnapDegrees: modelRigState.rotationSnapDegrees,
    ik: ikSnapshot(),
    pickStatus: modelRigState.pickStatus,
    rigPresets: rigPresetSession?.snapshot() || null,
    humanoidRigEdit: humanoidRigEditSession?.snapshot(),
    humanoidControlRig: modelRigState.loaded ? humanoidControlRigSnapshot() : null,
    model: modelRigState.loaded ? modelRigSnapshotForState() : null,
  };
}

function notifyModelRigChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('mod-viewer-model-rig-changed', {
        detail: rigSnapshot(),
      }),
    );
  }
}

function notifyModelRigPoseChanged(rig, boneId, changedJointIds = null) {
  if (typeof window !== 'undefined') {
    const quaternion = rig?.poseRotationByBoneId?.get(boneId);
    const jointId = modelJointIdForSourceBone(rig?.sourceKey, boneId);
    const modelQuaternion = Number.isInteger(jointId) ? modelSkinningRig?.poseRotationByJointId?.get(jointId) : null;
    const jointIds = Array.isArray(changedJointIds)
      ? changedJointIds.map(Number).filter(Number.isInteger)
      : Number.isInteger(jointId)
        ? [jointId]
        : [];
    window.dispatchEvent(
      new CustomEvent('mod-viewer-model-rig-pose-changed', {
        detail: {
          jointId: Number.isInteger(jointId) ? jointId : null,
          jointIds,
          quaternion: (modelQuaternion || quaternion)?.toArray() || [0, 0, 0, 1],
        },
      }),
    );
  }
}

function notifyModelWeightChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('mod-viewer-model-weight-changed', {
        detail: modelWeightSnapshot(),
      }),
    );
  }
}

function eligibleSkinningMesh(mesh) {
  return (
    mesh?.userData?.skinningAvailable === true &&
    mesh.userData?.assetFill !== true &&
    !!mesh.userData?.modPath &&
    !!mesh.userData?.semanticKey
  );
}

function resetRigPose() {
  const presetWasSelected = rigPresetSession?.clearApplicationState?.() || false;
  const changed = rigPoseRuntime?.resetPose({ request: false }) || false;
  notifyModelRigChanged();
  requestRender();
  return changed || presetWasSelected;
}

function resetModelWeightState() {
  weightRigActivationSession?.invalidate();
  modelWeightGeneration += 1;
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  rigPresetSession?.reset();
  humanoidRigEditSession?.resetSession();
  weightPickingSession?.reset();
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

export function registerWeightRigMesh(mesh) {
  if (!mesh) return;
  const wasKnown = knownMeshes.has(mesh);
  knownMeshes.add(mesh);
  if (!wasKnown) {
    invalidateHumanoidDetection();
  }
  if (modelWeightState.loaded) weightModelSession?.refreshModelWeightSummary({ refreshStats: true });
  if (!physicsRuntime.getState().enabled) return;
  const state = stateFor(mesh);
  if (!eligibleSkinningMesh(mesh)) {
    state.physicsParticipantStatus = 'unavailable';
    physicsRuntime.markUnavailable(mesh, 'skinning-unavailable');
    return;
  }
  if (state.loaded) physicsRuntime.syncParticipants();
}

export function unregisterWeightRigMesh(mesh) {
  if (modelWeightState.pickedPoint?.meshKey && modelWeightState.pickedPoint.meshKey === mesh?.userData?.semanticKey) {
    weightPickingSession?.clearPickedPoint();
  }
  const wasKnown = knownMeshes.delete(mesh);
  if (wasKnown) {
    invalidateHumanoidDetection();
    weightRigActivationSession?.invalidate();
  }
  const sourceKey = states.get(mesh)?.skinningSourceKey;
  if (sourceKey) {
    physicsRuntime.invalidateSource(sourceKey);
    sourceSkinningRigs.delete(sourceKey);
  }
  weightModelSession?.refreshModelWeightSummary({ refreshStats: true });
  if (sourceKey && physicsRuntime.getState().enabled) {
    physicsRuntime.syncParticipants(new Set([sourceKey]));
  }
  if (rigModelSession?.isActive()) void rigModelSession.rebuild();
  notifyModelRigChanged();
  notifyModelWeightChanged();
}

export function refreshWeightRigAfterShapeChange(mesh) {
  const state = states.get(mesh);
  const position = mesh?.geometry?.attributes?.position;
  invalidateHumanoidDetection();
  weightPickingSession?.clearPickedPoint();
  if (!state?.loaded || !position) return false;
  const preservedRootSignatures = new Set(modelRigState.explicitRootSignatures);
  weightRigActivationSession?.invalidate();
  const sourceKey = state.skinningSourceKey;
  const shapedPositions = new Float32Array(position.array);
  const normal = mesh.geometry.attributes.normal;
  const shapedNormals = normal ? new Float32Array(normal.array) : null;
  const wasPhysicsEnabled = physicsRuntime.invalidateSource(sourceKey) || state.physicsEnabled;
  if (sourceKey) sourceSkinningRigs.delete(sourceKey);
  if (modelSkinningRig) rigPoseRuntime?.resetPose({ request: false });
  rigModelSession?.invalidate();
  modelSkinningRig = null;
  modelRigState.selectedJointId = null;
  modelRigState.structureRevision = 0;
  modelRigState.ikEnabled = false;
  modelRigState.activeLimbRole = 'left_arm';
  modelRigState.selectedHumanoidControlKey = null;
  modelRigState.explicitRootSignatures = preservedRootSignatures;
  rigPresetSession?.clearLastApplyResult?.();
  notifyModelRigChanged();
  const rebased = skinningRuntime.rebaseAfterShapeChange(mesh, {
    positions: shapedPositions,
    normals: shapedNormals,
  });
  if (wasPhysicsEnabled && physicsRuntime.getState().enabled && sourceKey) {
    physicsRuntime.syncParticipants(new Set([sourceKey]));
    physicsRuntime.wake();
  }
  if (state.heatmapMode) {
    skinningRuntime.updateModelWeightHeatmap(new Set([sourceKey]), modelWeightState.heatmapEnabled);
  }
  return rebased;
}

export function disposeWeightRigMesh(mesh, { preserveRegistration = false } = {}) {
  if (preserveRegistration) {
    const sourceKey = states.get(mesh)?.skinningSourceKey;
    if (sourceKey) physicsRuntime.detachSource(sourceKey);
  } else {
    unregisterWeightRigMesh(mesh);
  }
  skinningRuntime.disposeMesh(mesh);
}

export function destroyWeightRigModel() {
  geometryRigPreview = null;
  geometryPreviewSerial += 1;
  physicsRuntime.destroy();
  for (const mesh of knownMeshes) skinningRuntime.disposeMesh(mesh);
  knownMeshes.clear();
  resetModelWeightState();
}

function ensureSourceSkinningRigCooperatively(sourceKey, members, options) {
  return rigSourceSession?.ensureCooperative(sourceKey, members, options) || Promise.resolve(null);
}

function buildAllSourceSkinningRigsCooperatively(options) {
  const startedAt = clockNow();
  return (rigSourceSession?.buildAllCooperative(options) || Promise.resolve([])).then((result) => {
    if (!options?.isCurrent || options.isCurrent()) {
      modelRigState.performance.sourceRigPreparationMs = clockNow() - startedAt;
      const stats = (result || []).map((rig) => rig.__cooperativeStats || {});
      const timings = (result || [])
        .map((rig) => {
          const timing = rig.__cooperativeTimings;
          const stat = rig.__cooperativeStats || {};
          return timing
            ? {
                ...timing,
                largestChunkMs: Number(stat?.largestChunkMs) || 0,
                yieldCount: Number(stat?.yieldCount) || 0,
              }
            : null;
        })
        .filter(Boolean);
      modelRigState.performance.sourceRigLargestChunkMs = Math.max(
        0,
        ...stats.map((item) => Number(item.largestChunkMs) || 0),
      );
      modelRigState.performance.sourceRigYieldCount = stats.reduce(
        (sum, item) => sum + (Number(item.yieldCount) || 0),
        0,
      );
      modelRigState.performance.sourceRigDetails = timings;
    }
    return result;
  });
}

function modelJointIdForSourceBone(sourceKeyValue, boneId) {
  return modelSkinningRig?.sourceBoneToModelJointId?.get(sourceBoneKey(sourceKeyValue, boneId));
}

async function buildModelSkinningRig(
  sourceRigs = [...sourceSkinningRigs.values()],
  { generation = null, isCurrent = () => true } = {},
) {
  const startedAt = clockNow();
  const performance = { ...(modelRigState.performance || {}) };
  const budget = createWorkBudget();
  const checkpoint = async () => {
    if (generation !== null && !isCurrent()) return false;
    await budget.checkpoint();
    return generation === null || isCurrent();
  };
  if (!(await checkpoint())) return null;
  const previousSelectedJointId = modelRigState.selectedJointId;
  const previousRootSignatures = new Set(modelRigState.explicitRootSignatures);
  const reconciliation = await buildModelRigReconciliationCooperative(
    sourceRigs,
    {},
    {
      budget,
      isCurrent: () => generation === null || isCurrent(),
      timings: performance,
    },
  );
  if (!reconciliation) return null;
  performance.reconciliationMs = clockNow() - startedAt;
  if (!(await checkpoint())) return null;
  const joints = reconciliation.joints || [];
  const rig = {
    key: 'model-rig',
    sourceKey: 'model-rig',
    sourceRigs: [...sourceRigs],
    modelReferenceRadius:
      reconciliation.modelReferenceRadius || reconciliation.reconciliation?.modelReferenceRadius || 0,
    reconciliation,
    joints,
    edges: reconciliation.edges || [],
    inferredForest: {
      components: reconciliation.components || [],
      componentByBoneId: reconciliation.componentByJointId || new Map(),
    },
    components: reconciliation.components || [],
    componentByJointId: reconciliation.componentByJointId || new Map(),
    centerByJointId:
      reconciliation.centerByJointId || new Map(joints.map((joint) => [joint.jointId, joint.restCenter || [0, 0, 0]])),
    jointPivotByJointId:
      reconciliation.jointPivotByJointId ||
      new Map(joints.map((joint) => [joint.jointId, joint.restPivot || joint.restCenter || [0, 0, 0]])),
    jointPivotByEdgeKey: reconciliation.jointPivotByEdgeKey || null,
    restFrameByJointId: reconciliation.restFrameByJointId
      ? new Map([...reconciliation.restFrameByJointId].map(([jointId, frame]) => [jointId, cloneRigQuaternion(frame)]))
      : new Map(joints.map((joint) => [joint.jointId, cloneRigQuaternion(joint.restFrame)])),
    restDirectionByJointId: new Map(),
    restFrameEvidenceByJointId: new Map(),
    restContinuationChildByJointId: new Map(),
    sourceBoneToModelJointId: reconciliation.sourceBoneToModelJointMap || new Map(),
    sourceTransformAliases: new Map(),
    sourceRotationAliases: new Map(),
    poseRotationByJointId: new Map(),
    poseTransforms: new Map(),
    poseRotations: new Map(),
    manualPoseTransforms: new Map(),
    poseTransformCache: new Map(),
    poseAffectedJointIds: new Set(),
    poseActiveJointKey: '',
    poseActiveVerticesByMesh: new Map(),
    poseSourceBoneIdsByMesh: new Map(),
    poseRevision: 0,
    structureRevision: 0,
    jointBuildDiagnostics: {
      mergeCount: reconciliation.reconciliation?.equivalenceClusterCount || 0,
      sourceBoneCount: reconciliation.reconciliation?.sourceBoneCount || 0,
      componentCount: (reconciliation.components || []).length,
      edgeCount: (reconciliation.edges || []).length,
    },
  };
  if (!(await checkpoint())) return null;
  // Install provenance-derived edge pivots before capturing the default
  // orientation. Reset Pose must restore the same pivots used on first load.
  rebuildModelRestFrames(rig, rig.inferredForest);
  rig.defaultComponents = rig.components.map(cloneModelComponent);
  rig.defaultComponentByJointId = new Map(rig.componentByJointId);
  rig.defaultRootIdByComponent = new Map(
    rig.defaultComponents.map((component) => [Number(component.componentId), Number(component.rootId)]),
  );
  rig.defaultJointPivotByJointId = new Map(
    [...rig.jointPivotByJointId].map(([jointId, pivot]) => [jointId, [...pivot]]),
  );
  rig.defaultRestFrameByJointId = new Map(
    [...rig.restFrameByJointId].map(([jointId, frame]) => [jointId, frame.clone()]),
  );
  rig.defaultRestDirectionByJointId = new Map(
    [...rig.restDirectionByJointId].map(([jointId, direction]) => [jointId, direction.toArray?.() || [...direction]]),
  );
  rig.defaultRestContinuationChildByJointId = new Map(rig.restContinuationChildByJointId);
  let restoredRootSignatures = new Set();
  if (previousRootSignatures.size) {
    const rootRestore = rigPoseRuntime.applyRootSignatures(rig, [...previousRootSignatures], { updateRevision: false });
    const signatureIndex = buildJointSignatureIndex(rig).resolvedBySignature;
    restoredRootSignatures = new Set(
      rootRestore.appliedRoots.filter((signature) => {
        const jointId = signatureIndex.get(signature);
        const componentId = rig.defaultComponentByJointId.get(jointId);
        return (
          Number.isInteger(Number(componentId)) && rig.defaultRootIdByComponent.get(Number(componentId)) !== jointId
        );
      }),
    );
  }
  if (!(await checkpoint())) return null;
  if (modelSkinningRig) rigPoseRuntime?.resetPose({ request: false });
  rigRuntime.structureRevision += 1;
  rig.structureRevision = rigRuntime.structureRevision;
  if (restoredRootSignatures.size) rig.structureRevision = ++rigRuntime.structureRevision;
  modelRigState.performance = performance;
  modelRigState.explicitRootSignatures = restoredRootSignatures;
  rigPresetSession?.clearLastApplyResult?.();
  sourceRigs.forEach((sourceRig) => {
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
  skinningRuntime.updateModelWeightHeatmap(null, modelWeightState.heatmapEnabled);
  modelRigState.structureRevision = rig.structureRevision;
  modelRigState.selectedJointId =
    Number.isInteger(previousSelectedJointId) && joints[previousSelectedJointId] ? previousSelectedJointId : null;
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
  applySavedHumanoidRig(rig, humanoidRigEditSession?.getSavedOverrides?.());
}

function applySavedHumanoidRig(rig, savedOverrides = null) {
  if (!rig) return;
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  const orientationState = getModelTransformState?.();
  const automaticRig = rig.humanoidAutomaticControlRig;
  const controlMappings = automaticRig?.accepted
    ? resolveHumanoidControlMappings({
        savedOverrides,
        modelRig: rig,
      })
    : new Map();
  const controlRig = automaticRig?.accepted
    ? applyHumanoidControlRigOverrides({
        automaticRig,
        savedOverrides,
        modelRig: rig,
        resolvedMappings: controlMappings,
      })
    : automaticRig;
  const binding = controlRig?.accepted ? buildHumanoidRigBinding({ controlRig, modelRig: rig, controlMappings }) : null;
  rig.humanoidControlRig = controlRig;
  rig.humanoidControlMappings = controlMappings;
  rig.humanoidBinding = binding;
  rig.humanoidOrientationRevision = Number(orientationState?.modelOrientationRevision) || 0;
  modelRigState.humanoidControlRig = controlRig;
  modelRigState.humanoidStructureRevision = rig.structureRevision;
}

function handleModelTransformChanged(event) {
  const detail = event.detail || {};
  modelPhysicsSession.handleModelTransform({
    ...detail,
    modelTransform: detail.modelTransform || getModelTransformState(),
  });
}

function handleModelOrientationChanged() {
  const pose = { ...(modelRigState.humanoidPose || {}) };
  invalidateHumanoidDetection();
  modelRigState.humanoidPose = pose;
  if (modelSkinningRig && modelRigState.loaded) {
    buildPrimaryHumanoidRig(modelSkinningRig);
    rigPoseRuntime?.applyPose({ request: false });
  }
  notifyModelRigChanged();
}

function handleVirtualModelMotion(event) {
  modelPhysicsSession.handleVirtualMotion(event.detail);
}

if (typeof window !== 'undefined') {
  window.addEventListener('mod-viewer-model-transform-changed', handleModelTransformChanged);
  window.addEventListener('mod-viewer-model-orientation-changed', handleModelOrientationChanged);
  window.addEventListener('mod-viewer-virtual-model-motion', handleVirtualModelMotion);
  window.addEventListener('mod-viewer-mesh-state-changed', (event) => {
    modelPhysicsSession.handleMeshStateChanged(event.detail?.meshes || []);
  });
}
