// Weight-tab skinning runtime. Normal mesh loading never imports or invokes
// this module's bridge operation until the Weight tab asks.

import * as THREE from 'three';
import {
  camera, controls, getModelTransformState, invalidateCharacterShadowGeometry,
  renderer,
} from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';
import {
  applyWeightedNormalDeformationInto,
  applyWeightedTransformDeformationInto,
  buildForestTransformsFromLocalRotations,
} from './weight-deformation.js';
import {
  buildInferredRigRestFrames,
} from './weight-rig-frames.js';
import {
  buildModelRigReconciliation, orientModelRigForest, sourceBoneKey,
} from './weight-rig-reconcile.js';
import {GRAVITY_WORLD_DIRECTION} from './weight-physics.js';
import {
  buildSelectedWeightMask, normalizeBoneSelection,
  normalizeSelectedBoneIds, selectedBoneCount,
  serializeBoneSelection, sameBoneSelection,
  sampleSkinningAtIntersection,
} from './weight-selection.js';
import {
  aggregateInfluenceGraphs,
  buildInfluenceNodes as buildRigInfluenceNodes,
  buildInfluenceRelationships as buildRigInfluenceRelationships,
  buildInferredRigForest,
  buildSurfaceInfluenceGraph,
  inspectSurfaceTopology,
  jointPivotMap,
} from './weight-rig.js';
import { createWeightPickController } from '../scene/weight-pick-controller.js';
import { raycastModelAtClientPoint } from '../scene/model-picking.js';
import { computeModelBounds } from '../scene/model-bounds.js';
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
  resetWeightModelSession,
  setSelectedBones as setWeightModelSelectedBones,
} from './weight-model-session.js';
import {initializeWeightPhysicsController} from './weight-physics-controller.js';
import {initializeHumanoidPoseRuntime} from './humanoid-pose-runtime.js';
import {
  activePoseJointIds, aggregateModelBoneStats, EMPTY_ACTIVE_VERTICES,
  createRigRuntimeState, createWeightRuntimeState, matrixIsIdentity,
  RIG_LIMB_ROLES, RIG_ROTATION_SNAP_DEGREES,
} from './weight-runtime.js';
import {characterAxesFromOrientation} from './humanoid-orientation.js';
import {
  buildHumanoidDriverBaseTransforms,
  buildHumanoidSourceBoneDriverTransforms,
  buildHumanoidRigBinding,
} from './humanoid-rig-binding.js';
import {buildHumanoidControlRig} from './humanoid-control-rig.js';
import {mergeHumanoidLimbPose, solveHumanoidControlIk} from './humanoid-rig-ik.js';
import {buildHumanoidHeatBinding} from './humanoid-heat-binding.js';

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
let modelWeightGeneration = 0;
let humanoidControlRigCacheKey = '';
let humanoidControlRigSnapshotCache = null;
// Rig analysis can outlive a model switch or shape invalidation. Keep its
// identity separate from weight loading so a new Rig request may reuse an
// in-flight weight request without accepting stale rig state.
let modelRigLoadToken = null;
const RIG_IDENTITY_MATRIX = new THREE.Matrix4();

function invalidateHumanoidDetection() {
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  modelRigState.humanoidControlRig = null;
  modelRigState.humanoidPose = {};
  modelRigState.humanoidBendSigns = {};
  modelRigState.humanoidStructureRevision = null;
}

const physicsRuntime = createWeightPhysicsRuntime({
  states,
  sourcePhysicsRigs,
  getModelSkinningRig: () => modelSkinningRig,
  applyDeformation,
  finalizePhysicsGeometry,
  markFinalBoundsDirty,
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

initializeWeightPhysicsController({
  modelPhysicsSession,
  reset: () => resetModelPhysicsInternal(),
});

const modelPickController = createWeightPickController({
  canvas: renderer.domElement,
  camera,
  controls,
  getMeshes: modelPickMeshes,
  onPick: handleModelPickedIntersection,
  onStateChanged: (picking, {cancelled} = {}) => {
    modelWeightState.picking = picking;
    if (picking || cancelled) notifyModelWeightChanged();
  },
  requestRender,
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
  refreshModelWeightSummary,
  refreshSelectedWeightMask,
  updateModelWeightHeatmap,
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

const CANDIDATE_CONTAINMENT_THRESHOLD = 0.02;
const CANDIDATE_JACCARD_THRESHOLD = 0.01;
const WEIGHT_PICK_RADIUS_RATIO = 0.02;

const ERROR_MESSAGES = Object.freeze({
  skinning_source_identity_unavailable:
    'The skin-weight source identity is unavailable for this draw.',
});

export function getSkinningState(mesh) {
  return states.get(mesh) || null;
}

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
    bendSign: modelRigState.humanoidBendSigns?.[role] === -1 ? -1 : 1,
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

export function getModelRigState() {
  return rigSnapshot();
}

function notifyModelWeightChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mod-viewer-model-weight-changed', {
      detail: modelWeightSnapshot(),
    }));
  }
}

function modelPickMeshes() {
  return [...knownMeshes].filter(mesh => mesh?.userData?.assetFill !== true);
}

function pickRadiusWorld() {
  const box = computeModelBounds(modelPickMeshes());
  if (box.isEmpty()) return 0.0001;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const radius = Number(sphere.radius);
  return Number.isFinite(radius) && radius > 0
    ? Math.max(radius * WEIGHT_PICK_RADIUS_RATIO, 0.000001) : 0.0001;
}

function clearPickedPoint({notify = true} = {}) {
  if (!modelWeightState.pickedPoint && modelWeightState.pickerViewMode === 'all'
      && !modelWeightState.pickStatus && !modelPickController.isEnabled()) return false;
  if (modelPickController.isEnabled()) modelPickController.cancel();
  modelWeightState.pickedPoint = null;
  modelWeightState.pickerViewMode = 'all';
  modelWeightState.pickStatus = '';
  modelWeightState.picking = false;
  if (notify) notifyModelWeightChanged();
  return true;
}

export function sampleModelSkinningAtIntersection(intersection) {
  const mesh = intersection?.object;
  const state = states.get(mesh);
  if (!state?.loaded || !state.skinningSourceKey) return null;
  const radiusWorld = pickRadiusWorld();
  const sampled = sampleSkinningAtIntersection(
    intersection, mesh, state, {radius: radiusWorld});
  if (!sampled) return null;
  return sampled;
}

export function modelJointFromSkinningSample(sampled) {
  if (!sampled?.sourceKey || !Array.isArray(sampled.influences)) return null;
  const jointScores = new Map();
  for (const influence of sampled.influences) {
    const boneId = Number(influence?.boneId);
    const weight = Number(influence?.weight);
    const jointId = modelJointIdForSourceBone(sampled.sourceKey, boneId);
    if (!Number.isInteger(boneId) || !Number.isFinite(weight)
        || weight <= 0 || !Number.isInteger(jointId)) continue;
    const current = jointScores.get(jointId);
    if (!current) {
      jointScores.set(jointId, {weight, boneId, boneWeight: weight});
      continue;
    }
    current.weight += weight;
    if (weight > current.boneWeight
        || (weight === current.boneWeight && boneId < current.boneId)) {
      current.boneId = boneId;
      current.boneWeight = weight;
    }
  }
  const best = [...jointScores.entries()].sort((left, right) =>
    right[1].weight - left[1].weight || left[0] - right[0])[0];
  if (!best) return null;
  return {
    point: sampled.point,
    jointId: best[0],
    sourceKey: sampled.sourceKey,
    sourceFile: sampled.sourceFile,
    boneIdOffset: sampled.boneIdOffset,
    boneId: best[1].boneId,
    influences: sampled.influences,
  };
}

function handleModelPickedIntersection(intersection) {
  if (!intersection) {
    modelWeightState.pickStatus = 'No model surface was picked.';
    notifyModelWeightChanged();
    return null;
  }
  const sampled = sampleModelSkinningAtIntersection(intersection);
  if (!sampled) {
    modelWeightState.pickStatus = 'No skin weights are available for this part.';
    notifyModelWeightChanged();
    return null;
  }
  const mesh = intersection.object;
  const source = modelWeightState.sourceDescriptors.get(sampled.sourceKey);
  const radiusWorld = pickRadiusWorld();
  const pickedPoint = {
    point: sampled.point,
    sourceKey: sampled.sourceKey,
    sourceFile: source?.sourceFile || sampled.sourceFile,
    boneIdOffset: source?.boneIdOffset ?? sampled.boneIdOffset,
    meshKey: mesh.userData?.semanticKey || null,
    radiusWorld,
    influences: sampled.influences,
  };
  modelWeightState.pickedPoint = pickedPoint;
  modelWeightState.pickerViewMode = 'picked';
  modelWeightState.pickStatus = '';
  notifyModelWeightChanged();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mod-viewer-weight-point-picked', {
      detail: {
        sourceKey: sampled.sourceKey,
      },
    }));
  }
  return pickedPoint;
}

function setRigSurfacePickStatus(message) {
  modelRigState.pickStatus = message;
  notifyModelRigChanged();
  requestRender();
}

export function pickRigJointFromModelSurface(
    {clientX, clientY} = {}, intent = modelRigState.jointPickIntent) {
  const next = String(intent?.type || '') === 'selected-joint'
    ? {type: 'selected-joint'} : null;
  if (!next) return false;
  const intersection = raycastModelAtClientPoint({
    clientX, clientY, canvas: renderer.domElement, camera,
    meshes: modelPickMeshes(),
  });
  const sampled = sampleModelSkinningAtIntersection(intersection);
  const resolved = modelJointFromSkinningSample(sampled);
  if (!resolved) {
    setRigSurfacePickStatus('No usable Rig joint was found at this point.');
    return false;
  }
  return handleRigJointPicked(resolved.jointId, next);
}

export function beginWeightModelPicking() {
  if (modelRigState.jointPickIntent) cancelRigJointPicking();
  if (!modelWeightState.loaded) {
    modelWeightState.pickStatus = 'Load model weights before picking.';
    notifyModelWeightChanged();
    return false;
  }
  return modelPickController.begin();
}

export function cancelWeightModelPicking() {
  return modelPickController.cancel();
}

export function setWeightPickerViewMode(mode) {
  if (mode !== 'all' && mode !== 'picked') return modelWeightState.pickerViewMode;
  if (mode === 'picked' && !modelWeightState.pickedPoint) {
    return modelWeightState.pickerViewMode;
  }
  modelWeightState.pickerViewMode = mode;
  modelWeightState.pickStatus = '';
  notifyModelWeightChanged();
  return mode;
}

function sourceDescriptorForEntry(entry) {
  const source = entry?.source;
  if (source && typeof source === 'object'
      && typeof source.key === 'string' && source.key
      && typeof source.file === 'string' && source.file) {
    const offset = Number(source.bone_id_offset);
    if (Number.isInteger(offset) && offset >= 0) {
      return {
        sourceKey: source.key,
        sourceFile: source.file,
        boneIdOffset: offset,
      };
    }
  }
  return null;
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

function refreshModelBoneStats() {
  const nodesBySource = new Map();
  for (const mesh of knownMeshes) {
    const state = states.get(mesh);
    if (!state?.loaded || !state.skinningSourceKey) continue;
    const nodes = state.influenceNodes || [];
    const sourceNodes = nodesBySource.get(state.skinningSourceKey) || [];
    sourceNodes.push(nodes);
    nodesBySource.set(state.skinningSourceKey, sourceNodes);
  }
  modelWeightState.sources = modelWeightState.sources.map(source => ({
    ...source,
    boneStats: aggregateModelBoneStats(nodesBySource.get(source.key) || []),
  }));
}

function refreshModelWeightSummary({refreshStats = false} = {}) {
  const groups = new Map();
  const previousStats = new Map(modelWeightState.sources.map(source => [
    source.key, source.boneStats || {},
  ]));
  let loadedMeshCount = 0;
  let failedMeshCount = 0;
  knownMeshes.forEach(mesh => {
    const state = states.get(mesh);
    if (state?.loaded) {
      loadedMeshCount += 1;
      const source = modelWeightState.sourceDescriptors.get(
        state.skinningSourceKey);
      if (!source) return;
      const group = groups.get(state.skinningSourceKey) || {
        key: source.sourceKey,
        file: source.sourceFile,
        boneIdOffset: source.boneIdOffset,
        availableBoneIds: new Set(),
        boneStats: previousStats.get(state.skinningSourceKey) || {},
      };
      (state.boneIds || []).forEach(id => group.availableBoneIds.add(Number(id)));
      groups.set(state.skinningSourceKey, group);
    } else if (state?.error) {
      failedMeshCount += 1;
    }
  });
  modelWeightState.sources = [...groups.values()]
    .map(group => ({...group,
      availableBoneIds: [...group.availableBoneIds]
        .filter(Number.isFinite).sort((left, right) => left - right),
    }))
    .sort((left, right) => left.key.localeCompare(right.key));
  modelWeightState.loadedMeshCount = loadedMeshCount;
  modelWeightState.failedMeshCount = failedMeshCount;
  if (modelWeightState.loaded) {
    const availableBySource = new Map(modelWeightState.sources.map(source => [
      source.key, new Set(source.availableBoneIds),
    ]));
    // Keep the saved document intact for Load/status reporting. Applying a
    // saved selection below performs the same per-source availability filter
    // against the current model without migrating stale IDs.
    for (const map of [modelWeightState.selectedBonesBySource]) {
      for (const [sourceKey, ids] of map) {
        const available = availableBySource.get(sourceKey);
        if (!available) {
          map.delete(sourceKey);
          continue;
        }
        const filtered = new Set([...ids].filter(id => available.has(id)));
        if (filtered.size) map.set(sourceKey, filtered);
        else map.delete(sourceKey);
      }
    }
  }
  if (refreshStats) refreshModelBoneStats();
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

function setModelWeightLoadError(error) {
  modelWeightState.error = error instanceof Error
    ? error.message : String(error);
  modelWeightState.loaded = false;
  modelWeightState.noWeights = false;
}

function resetModelWeightState() {
  modelWeightGeneration += 1;
  humanoidControlRigCacheKey = '';
  humanoidControlRigSnapshotCache = null;
  modelRigLoadToken = null;
  resetRigPresetSession();
  modelPickController.cancel();
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

/** Return the game material owned by a loaded skinning rig. */
export function getSkinningBaseMaterial(mesh) {
  const state = states.get(mesh);
  return state ? (state.originalMaterial || mesh.material)
    : mesh?.material;
}

/** Run a material operation against the game material, not the heatmap. */
export function withSkinningBaseMaterial(mesh, operation) {
  const state = states.get(mesh);
  if (!state) return operation();

  const heatmapActive = state.heatmapMode
    && state.debugMaterial && mesh.material === state.debugMaterial;
  const displayedMaterial = mesh.material;
  if (heatmapActive) mesh.material = state.originalMaterial || mesh.material;
  try {
    const result = operation();
    state.originalMaterial = mesh.material;
    return result;
  } finally {
    if (heatmapActive) mesh.material = displayedMaterial;
  }
}

function memberArrayFingerprint(values) {
  if (!values) return 'none';
  const bytes = ArrayBuffer.isView(values)
    ? new Uint8Array(values.buffer, values.byteOffset, values.byteLength)
    : null;
  if (!bytes) return `array:${values.length}:${[...values].join(',')}`;
  let hash = 2166136261;
  for (const byte of bytes) {
    hash ^= byte;
    hash = Math.imul(hash, 16777619);
  }
  return `${values.constructor.name}:${values.length}:${hash >>> 0}`;
}

function memberArraysEqual(left, right) {
  if (left === right) return true;
  if (!left || !right || left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    if (!Object.is(left[index], right[index])) return false;
  }
  return true;
}

function completeMemberEvidenceEqual(left, right) {
  return left.state.influenceCount === right.state.influenceCount
    && memberArraysEqual(left.state.baselinePositions,
      right.state.baselinePositions)
    && memberArraysEqual(left.state.indices, right.state.indices)
    && memberArraysEqual(left.state.weights, right.state.weights)
    && memberArraysEqual(left.state.boneIds, right.state.boneIds)
    && memberArraysEqual(left.surfaceIndices, right.surfaceIndices);
}

function sourceMemberFingerprint(member) {
  const state = member.state;
  return [
    state.influenceCount,
    memberArrayFingerprint(state.baselinePositions),
    memberArrayFingerprint(state.indices),
    memberArrayFingerprint(state.weights),
    memberArrayFingerprint(state.boneIds),
    memberArrayFingerprint(member.surfaceIndices),
  ].join('|');
}

function normalizeSourceMembers(members) {
  const buckets = new Map();
  const uniqueMembers = [];
  const duplicateMemberKeys = [];
  const duplicateMembers = new Set();
  let duplicateMemberCount = 0;
  for (const member of members) {
    const fingerprint = sourceMemberFingerprint(member);
    const bucket = buckets.get(fingerprint) || [];
    const duplicate = bucket.find(candidate =>
      completeMemberEvidenceEqual(candidate, member));
    if (duplicate) {
      duplicateMemberCount += 1;
      duplicateMembers.add(member);
      duplicateMemberKeys.push(
        member.mesh.userData?.semanticKey || member.mesh.uuid || '');
      continue;
    }
    bucket.push(member);
    buckets.set(fingerprint, bucket);
    uniqueMembers.push(member);
  }
  return {
    members: uniqueMembers,
    duplicateMemberCount,
    duplicateMemberKeys: duplicateMemberKeys.sort(),
    duplicateMembers,
  };
}

function sourceMemberKey(member, index) {
  return member.mesh.userData?.semanticKey
    || member.mesh.uuid
    || `member-${index}`;
}

function sourceMemberVertexTokens(member) {
  const {baselinePositions, indices, weights, influenceCount} = member.state;
  if (!baselinePositions || !indices || !weights
      || !Number.isInteger(influenceCount) || influenceCount <= 0) {
    return new Set();
  }
  const vertexCount = Math.floor(Math.min(
    Math.floor(baselinePositions.length / 3),
    Math.min(indices.length, weights.length) / influenceCount));
  const tokens = new Set();
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const positionOffset = vertex * 3;
    const weightOffset = vertex * influenceCount;
    const position = [
      baselinePositions[positionOffset],
      baselinePositions[positionOffset + 1],
      baselinePositions[positionOffset + 2],
    ].map(value => String(value)).join(',');
    const influences = [];
    for (let influence = 0; influence < influenceCount; influence += 1) {
      influences.push(
        `${indices[weightOffset + influence]}:${weights[weightOffset + influence]}`);
    }
    tokens.add(`${position}|${influences.join(',')}`);
  }
  return tokens;
}

function sourceMemberRecords(members, normalized) {
  return members.map((member, index) => ({
    memberKey: sourceMemberKey(member, index),
    vertexCount: Math.floor((member.state.baselinePositions?.length || 0) / 3),
    triangleCount: member.surfaceEvidence.triangleCount,
    validTriangleCount: member.surfaceEvidence.validTriangleCount,
    degenerateTriangleCount: member.surfaceEvidence.degenerateTriangleCount,
    invalidTriangleCount: member.surfaceEvidence.invalidTriangleCount,
    totalSurfaceArea: member.surfaceEvidence.totalSurfaceArea,
    surfaceEvidenceAvailable: member.surfaceEvidence.surfaceEvidenceAvailable,
    duplicate: normalized.duplicateMembers.has(member),
  }));
}

function aggregateSourceInfluenceGraph(members) {
  const loadedMembers = members.map(mesh => {
    const state = states.get(mesh);
    return state?.loaded ? {
      mesh, state,
      surfaceIndices: mesh.geometry?.index?.array || null,
      surfaceEvidence: inspectSurfaceTopology(
        state.baselinePositions, mesh.geometry?.index?.array || null),
    } : null;
  }).filter(Boolean);
  const normalizedMembers = normalizeSourceMembers(loadedMembers);
  const memberDiagnostics = sourceMemberRecords(
    loadedMembers, normalizedMembers);
  const evidenceMode = loadedMembers.every(member =>
    member.surfaceEvidence.surfaceEvidenceAvailable) ? 'surface' : 'vertex';
  const graph = aggregateInfluenceGraphs(normalizedMembers.members.map(member =>
    ensureInfluenceGraph(member.mesh, member.state, evidenceMode,
      member.surfaceEvidence)));
  return {
    ...graph,
    memberCount: loadedMembers.length,
    uniqueMemberCount: normalizedMembers.members.length,
    duplicateMemberCount: normalizedMembers.duplicateMemberCount,
    duplicateMemberKeys: normalizedMembers.duplicateMemberKeys,
    memberDiagnostics,
    partialOverlapPairs: [],
  };
}

function createSourceSkinningRig(sourceKey, members) {
  const descriptor = modelWeightState.sourceDescriptors.get(sourceKey);
  const influenceGraph = aggregateSourceInfluenceGraph(members);
  const inferredForest = buildInferredRigForest(influenceGraph);
  const jointPivotByBoneId = jointPivotMap(
    inferredForest, influenceGraph.relationships);
  const rig = {
    key: sourceKey,
    sourceKey,
    sourceFile: descriptor?.sourceFile || '',
    boneIdOffset: descriptor?.boneIdOffset ?? 0,
    meshes: new Set(members),
    influenceGraph,
    boneIds: (influenceGraph.nodes || []).map(node => node.boneId),
    centerByBoneId: new Map((influenceGraph.nodes || []).map(node => [
      node.boneId, node.weightedCenter])),
    inferredForest,
    jointPivotByBoneId,
    restFrameByBoneId: new Map(),
    restDirectionByBoneId: new Map(),
    restFrameEvidenceByBoneId: new Map(),
    continuationChildByBoneId: new Map(),
    vertexEvidence: members.map(mesh => {
      const state = states.get(mesh);
      return state?.loaded ? {
        meshKey: mesh.userData?.semanticKey || '',
        positions: state.baselinePositions,
        indices: state.indices,
        weights: state.weights,
        influenceCount: state.influenceCount,
      } : null;
    }).filter(Boolean),
    structureRevision: 0,
    poseRotationByBoneId: new Map(),
    poseTransforms: new Map(),
    poseRotations: new Map(),
    poseTransformCache: new Map(),
    poseFrameCache: new Map(),
    poseRootOverrides: new Map(),
    physicsRig: null,
  };
  rebuildSourceRigRestFrames(rig);
  rig.defaultInferredForest = cloneSourceForest(rig.inferredForest);
  rig.defaultJointPivotByBoneId = new Map([...rig.jointPivotByBoneId]
    .map(([boneId, pivot]) => [boneId, [...pivot]]));
  return rig;
}

function sameMeshSet(left, right) {
  return left?.size === right?.length
    && right.every(mesh => left.has(mesh));
}

function resetSourceSkinningPose(rig) {
  rig.poseRotationByBoneId.clear();
  rig.poseTransforms.clear();
  rig.poseRotations.clear();
  rig.poseTransformCache.clear();
  rig.poseFrameCache.clear();
}

function refreshSourceSkinningRig(rig, members, {resetPose = true} = {}) {
  const refreshed = createSourceSkinningRig(rig.sourceKey, members);
  rig.meshes = refreshed.meshes;
  rig.influenceGraph = refreshed.influenceGraph;
  rig.boneIds = refreshed.boneIds;
  rig.centerByBoneId = refreshed.centerByBoneId;
  rig.inferredForest = refreshed.inferredForest;
  rig.jointPivotByBoneId = refreshed.jointPivotByBoneId;
  rig.defaultInferredForest = cloneSourceForest(refreshed.defaultInferredForest);
  rig.defaultJointPivotByBoneId = new Map(
    [...refreshed.defaultJointPivotByBoneId]
      .map(([boneId, pivot]) => [boneId, [...pivot]]));
  rig.restFrameByBoneId = refreshed.restFrameByBoneId;
  rig.restDirectionByBoneId = refreshed.restDirectionByBoneId;
  rig.restFrameEvidenceByBoneId = refreshed.restFrameEvidenceByBoneId;
  rig.continuationChildByBoneId = refreshed.continuationChildByBoneId;
  rig.vertexEvidence = refreshed.vertexEvidence;
  rig.structureRevision = refreshed.structureRevision;
  rig.poseFrameCache = refreshed.poseFrameCache;
  rig.poseRootOverrides = refreshed.poseRootOverrides;
  if (resetPose) resetSourceSkinningPose(rig);
  return rig;
}

function ensureSourceSkinningRig(sourceKey, members) {
  let rig = sourceSkinningRigs.get(sourceKey);
  if (!rig) {
    rig = createSourceSkinningRig(sourceKey, members);
    sourceSkinningRigs.set(sourceKey, rig);
  } else if (!sameMeshSet(rig.meshes, members)) {
    refreshSourceSkinningRig(rig, members);
  }
  return rig;
}

function loadedSourceMembers() {
  const groups = new Map();
  for (const mesh of knownMeshes) {
    const state = states.get(mesh);
    if (!state?.loaded || !state.skinningSourceKey) continue;
    const members = groups.get(state.skinningSourceKey) || [];
    members.push(mesh);
    groups.set(state.skinningSourceKey, members);
  }
  return groups;
}

function buildAllSourceSkinningRigs() {
  const groups = loadedSourceMembers();
  for (const [sourceKey, members] of groups) {
    ensureSourceSkinningRig(sourceKey, members);
  }
  for (const sourceKey of [...sourceSkinningRigs.keys()]) {
    if (!groups.has(sourceKey)) sourceSkinningRigs.delete(sourceKey);
  }
  return [...sourceSkinningRigs.values()];
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

function modelJointPairKey(left, right) {
  const a = Number(left);
  const b = Number(right);
  return `${Math.min(a, b)}:${Math.max(a, b)}`;
}

function finiteVectorArray(value) {
  const values = value?.toArray ? value.toArray()
    : Array.isArray(value) || ArrayBuffer.isView(value) ? [...value] : null;
  return values?.length >= 3 && values.slice(0, 3).every(Number.isFinite)
    ? values.slice(0, 3) : null;
}

function buildModelJointPivotByEdgeKey(rig) {
  const pivots = new Map();
  for (const edge of rig.edges || []) {
    const jointA = Number(edge.jointA);
    const jointB = Number(edge.jointB);
    if (!Number.isInteger(jointA) || !Number.isInteger(jointB)
        || jointA === jointB) continue;
    const observations = (edge.sourceEdges || [])
      .map(sourceEdge => ({
        point: finiteVectorArray(sourceEdge.jointCenter),
        weight: Number(sourceEdge.jointWeightTotal) || 0,
      }))
      .filter(observation => observation.point && observation.weight > 0);
    let pivot = observations.length
      ? observations.reduce((sum, observation) => {
        const weight = observation.weight;
        sum.weight += weight;
        sum.point[0] += observation.point[0] * weight;
        sum.point[1] += observation.point[1] * weight;
        sum.point[2] += observation.point[2] * weight;
        return sum;
      }, {point: [0, 0, 0], weight: 0}) : null;
    pivot = pivot?.weight > 0
      ? pivot.point.map(value => value / pivot.weight)
      : finiteVectorArray(edge.jointCenter);
    if (!pivot) {
      for (const component of rig.components || []) {
        const parentOfA = component.parentById?.[jointA];
        const parentOfB = component.parentById?.[jointB];
        const childId = parentOfA !== null && parentOfA !== undefined
          && Number(parentOfA) === jointB ? jointA
          : parentOfB !== null && parentOfB !== undefined
            && Number(parentOfB) === jointA ? jointB : null;
        if (childId === null) continue;
        pivot = finiteVectorArray(rig.jointPivotByJointId.get(childId)
          || rig.joints[childId]?.restPivot);
        if (pivot) break;
      }
    }
    if (!pivot) {
      pivot = finiteVectorArray(rig.joints[jointB]?.restPivot)
        || finiteVectorArray(rig.joints[jointA]?.restPivot);
    }
    if (pivot) pivots.set(modelJointPairKey(jointA, jointB), pivot);
  }
  return pivots;
}

export function rebuildModelRestFrames(rig, forest) {
  const edgePivots = rig.jointPivotByEdgeKey
    || buildModelJointPivotByEdgeKey(rig);
  const pivots = new Map();
  const rootIds = new Set((forest?.components || []).map(component =>
    Number(component.rootId)).filter(Number.isInteger));
  (forest?.components || []).forEach(component => {
    Object.entries(component.parentById || {}).forEach(([childValue, parentValue]) => {
      if (parentValue === null || parentValue === undefined) return;
      const childId = Number(childValue);
      const parentId = Number(parentValue);
      if (!Number.isInteger(childId) || !Number.isInteger(parentId)) return;
      const pivot = edgePivots.get(modelJointPairKey(childId, parentId));
      if (pivot) pivots.set(childId, [...pivot]);
    });
  });
  (rig.joints || []).forEach(joint => {
    const jointId = Number(joint.jointId);
    if (rootIds.has(jointId)) {
      pivots.set(jointId, finiteVectorArray(joint.restCenter)
        || [0, 0, 0]);
      return;
    }
    if (!pivots.has(jointId)) {
      pivots.set(jointId, finiteVectorArray(joint.restPivot)
        || finiteVectorArray(joint.restCenter) || [0, 0, 0]);
    }
  });
  rig.jointPivotByJointId = pivots;
  const frames = buildInferredRigRestFrames(
    forest, rig.centerByJointId, rig.jointPivotByJointId);
  rig.restFrameByJointId = frames.frameByBoneId;
  rig.restDirectionByJointId = frames.directionByBoneId;
  rig.restFrameEvidenceByJointId = frames.evidenceByBoneId;
  rig.restContinuationChildByJointId = frames.continuationChildByBoneId;
  (rig.joints || []).forEach(joint => {
    const jointId = Number(joint.jointId);
    const pivot = rig.jointPivotByJointId.get(jointId);
    const frame = frames.frameByBoneId.get(jointId);
    const direction = frames.directionByBoneId.get(jointId);
    if (pivot) joint.restPivot = [...pivot];
    if (frame) joint.restFrame = frame.toArray();
    if (direction) joint.restDirection = direction.toArray();
  });
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
  rig.jointPivotByEdgeKey = buildModelJointPivotByEdgeKey(rig);
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
  updateModelWeightHeatmap();
  modelRigState.structureRevision = rig.structureRevision;
  modelRigState.selectedJointId = Number.isInteger(previousSelectedJointId)
    && joints[previousSelectedJointId] ? previousSelectedJointId : null;
  updateModelPoseFrameCache(rig, rig.poseTransforms);
  return rig;
}

function buildPrimaryHumanoidRig(rig) {
  const orientationState = getModelTransformState?.();
  const controlRig = buildHumanoidControlRig({
    meshes: [...knownMeshes],
    axes: humanoidSemanticAxes(),
    orientationState,
  });
  const heatBinding = controlRig?.accepted
    ? buildHumanoidHeatBinding({
      controlRig, sourceRigs: rig.sourceRigs, modelRig: rig,
    }) : null;
  const binding = controlRig?.accepted
    ? buildHumanoidRigBinding({controlRig, modelRig: rig, heatBinding}) : null;
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

export function registerSkinningMesh(mesh) {
  if (!mesh) return;
  const wasKnown = knownMeshes.has(mesh);
  knownMeshes.add(mesh);
  if (!wasKnown) invalidateHumanoidDetection();
  if (!modelPhysicsSession.getState().enabled) return;
  const state = stateFor(mesh);
  if (!eligibleSkinningMesh(mesh)) {
    state.physicsParticipantStatus = 'unavailable';
    modelPhysicsSession.markUnavailable(mesh, 'skinning-unavailable');
    return;
  }
  if (state.loaded) syncPhysicsParticipants();
}

export function unregisterSkinningMesh(mesh) {
  if (modelWeightState.pickedPoint?.meshKey
      && modelWeightState.pickedPoint.meshKey === mesh?.userData?.semanticKey) {
    clearPickedPoint();
  }
  const wasKnown = knownMeshes.delete(mesh);
  if (wasKnown) invalidateHumanoidDetection();
  const sourceKey = states.get(mesh)?.skinningSourceKey;
  if (sourceKey) {
    modelPhysicsSession.detach(sourceKey);
    sourcePhysicsRigs.delete(sourceKey);
    sourceSkinningRigs.delete(sourceKey);
  }
  refreshModelWeightSummary({refreshStats: true});
  if (sourceKey && modelPhysicsSession.getState().enabled) {
    syncPhysicsParticipants(new Set([sourceKey]));
  }
  if (modelRigState.loaded) {
    buildAllSourceSkinningRigs();
    buildModelSkinningRig([...sourceSkinningRigs.values()]);
    modelRigState.loaded = true;
  }
  notifyModelRigChanged();
  notifyModelWeightChanged();
}

export function destroyModelPhysicsSession() {
  modelPickController.cancel();
  modelPhysicsSession.destroy();
  knownMeshes.clear();
  resetModelWeightState();
}

function disablePhysicsSession() {
  if (!modelPhysicsSession.getState().enabled) return false;
  modelPhysicsSession.disable();
  notifyModelRigChanged();
  return true;
}

function installSkinningEntry(mesh, entry, buffer) {
  const state = stateFor(mesh);
  const source = sourceDescriptorForEntry(entry);
  if (!source) throw new Error(ERROR_MESSAGES.skinning_source_identity_unavailable);
  state.skinningSourceKey = source.sourceKey;
  state.skinningSourceFile = source.sourceFile;
  state.skinningBoneOffset = source.boneIdOffset;
  modelWeightState.sourceDescriptors.set(source.sourceKey, source);
  const indices = typedView(buffer, entry.data?.indices, Uint32Array, 'u32');
  const weights = typedView(buffer, entry.data?.weights, Float32Array, 'f32');
  const position = mesh.geometry?.attributes?.position;
  const influenceCount = Number(entry.influence_count);
  if (!position || !Number.isInteger(influenceCount) || influenceCount <= 0
      || position.count !== Number(entry.vertex_count)
      || indices.length !== position.count * influenceCount
      || weights.length !== position.count * influenceCount) {
    throw new Error('Skin data does not match rendered vertices.');
  }
  captureBaseline(mesh, state);
  state.indices = indices;
  state.weights = weights;
  state.influenceCount = influenceCount;
  state.boneIds = Array.isArray(entry.bone_ids)
    ? [...entry.bone_ids].map(Number).filter(Number.isFinite)
      .sort((left, right) => left - right)
    : buildBoneIds(indices, weights, influenceCount);
  state.encoding = entry.encoding || null;
  state.diagnostics = entry.diagnostics || null;
  state.loaded = true;
  state.error = null;
  state.influenceNodes = buildRigInfluenceNodes(
    state.baselinePositions, state.indices, state.weights,
    state.influenceCount, state.boneIds);
  state.centerByBoneId = new Map(state.influenceNodes.map(node => [
    node.boneId, node.weightedCenter]));
  refreshSelectedWeightMask(mesh, state);
  return state;
}

/** Load all active model weights through one backend request and one blob. */
function ensureModelWeightsLoaded() {
  if (modelWeightState.loaded) return Promise.resolve(modelWeightSnapshot());
  if (modelWeightState.promise) return modelWeightState.promise;
  const meshes = [...knownMeshes].filter(eligibleSkinningMesh);
  const generation = modelWeightGeneration;
  if (!meshes.length) {
    modelWeightState.loaded = true;
    modelWeightState.noWeights = true;
    modelWeightState.savedSelectionApplied = true;
    refreshModelWeightSummary({refreshStats: true});
    syncPhysicsToSelection();
    notifyModelWeightChanged();
    return Promise.resolve(modelWeightSnapshot());
  }
  const folderPath = meshes[0].userData.modPath;
  modelWeightState.loading = true;
  modelWeightState.error = null;
  modelWeightState.noWeights = false;
  notifyModelWeightChanged();
  modelWeightState.promise = (async () => {
    const api = window.pywebview?.api?.get_model_skinning_preview;
    if (typeof api !== 'function') {
      throw new Error('Model skin-weight preview is unavailable.');
    }
    const preview = await api(folderPath);
    if (generation !== modelWeightGeneration) return modelWeightSnapshot();
    modelWeightState.savedBonesBySource = selectionMapFromEntries(
      preview?.saved_bones);
    const bufferResponse = preview?.data?.url
      ? await fetch(preview.data.url, {cache: 'no-store'}) : null;
    if (bufferResponse && !bufferResponse.ok) {
      throw new Error(`Skin data download failed (${bufferResponse.status}).`);
    }
    const buffer = bufferResponse ? await bufferResponse.arrayBuffer() : null;
    if (buffer && buffer.byteLength !== Number(preview.data.length)) {
      throw new Error('Skin data download was incomplete.');
    }
    for (const mesh of meshes) {
      if (generation !== modelWeightGeneration || !knownMeshes.has(mesh)) break;
      const state = stateFor(mesh);
      const entry = preview?.meshes?.[mesh.userData.semanticKey];
      if (!entry || entry.status !== 'ok') {
        state.error = entry?.error || 'No usable skin weights were returned.';
        state.loaded = false;
        continue;
      }
      try {
        installSkinningEntry(mesh, entry, buffer);
      } catch (error) {
        state.error = error instanceof Error ? error.message : String(error);
        state.loaded = false;
      }
    }
    if (generation !== modelWeightGeneration) return modelWeightSnapshot();
    modelWeightState.loaded = true;
    refreshModelWeightSummary({refreshStats: true});
    if (!modelWeightState.savedSelectionApplied) {
      modelWeightState.savedSelectionApplied = true;
      setWeightModelSelectedBones(sourceSelectionEntries(
        modelWeightState.savedBonesBySource));
    } else {
      syncPhysicsToSelection();
      notifyModelWeightChanged();
    }
    return modelWeightSnapshot();
  })();
  return modelWeightState.promise
    .catch(error => {
      if (generation === modelWeightGeneration) {
        setModelWeightLoadError(error);
        refreshModelWeightSummary({refreshStats: true});
        notifyModelWeightChanged();
      }
      return modelWeightSnapshot();
    })
    .finally(() => {
      if (generation === modelWeightGeneration) {
        modelWeightState.loading = false;
        modelWeightState.promise = null;
        notifyModelWeightChanged();
      }
    });
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

function restorePoseVertices(mesh, state, vertices) {
  const position = mesh.geometry?.attributes?.position;
  if (!position || !state.baselinePositions) return;
  const normal = mesh.geometry?.attributes?.normal;
  for (let index = 0; index < (vertices?.length || 0); index += 1) {
    const vertex = Number(vertices[index]);
    const offset = vertex * 3;
    if (offset < 0 || offset + 2 >= state.baselinePositions.length) continue;
    position.array[offset] = state.baselinePositions[offset];
    position.array[offset + 1] = state.baselinePositions[offset + 1];
    position.array[offset + 2] = state.baselinePositions[offset + 2];
    if (normal && state.baselineNormals
        && offset + 2 < state.baselineNormals.length) {
      normal.array[offset] = state.baselineNormals[offset];
      normal.array[offset + 1] = state.baselineNormals[offset + 1];
      normal.array[offset + 2] = state.baselineNormals[offset + 2];
    }
  }
  position.needsUpdate = true;
  if (normal) normal.needsUpdate = true;
}

function vertexDifference(previousVertices, currentVertices) {
  if (!previousVertices?.length) return new Uint32Array();
  const current = new Set(currentVertices || []);
  const removed = [];
  for (const vertex of previousVertices) {
    if (!current.has(vertex)) removed.push(vertex);
  }
  return Uint32Array.from(removed);
}

function combinedActiveVerticesForState(state) {
  const poseVertices = state.poseActiveVertices || EMPTY_ACTIVE_VERTICES;
  const physicsVertices = state.physicsEnabled
    ? state.physicsActiveVertices || EMPTY_ACTIVE_VERTICES
    : EMPTY_ACTIVE_VERTICES;
  if (state.combinedPoseVerticesRef === poseVertices
      && state.combinedPhysicsVerticesRef === physicsVertices
      && state.combinedActiveVertices) {
    return state.combinedActiveVertices;
  }
  const values = new Set();
  poseVertices.forEach(vertex => values.add(Number(vertex)));
  physicsVertices.forEach(vertex => values.add(Number(vertex)));
  state.combinedPoseVerticesRef = poseVertices;
  state.combinedPhysicsVerticesRef = physicsVertices;
  state.combinedActiveVertices = Uint32Array.from([...values].sort(
    (left, right) => left - right));
  return state.combinedActiveVertices;
}

function markFinalBoundsDirty(mesh, state) {
  if (state.preDeformationFrustumCulled === null
      || state.preDeformationFrustumCulled === undefined) {
    state.preDeformationFrustumCulled = mesh.frustumCulled;
  }
  mesh.frustumCulled = false;
  state.finalBoundsDirty = true;
}

function finalizeSourcePoseBounds(rig) {
  let finalized = false;
  forEachRigMesh(rig, (mesh, state) => {
    finalized = finalizeDeformationGeometry(mesh, state) || finalized;
  });
  if (finalized) invalidateCharacterShadowGeometry({request: false});
  return finalized;
}

function finalizeDeformationGeometry(mesh, state) {
  if (!state.finalBoundsDirty) return false;
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  state.finalBoundsDirty = false;
  if (state.preDeformationFrustumCulled !== null
      && state.preDeformationFrustumCulled !== undefined) {
    mesh.frustumCulled = state.preDeformationFrustumCulled;
    state.preDeformationFrustumCulled = null;
  }
  return true;
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
        changed = applyDeformation(mesh, state, {
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

function rigSourceFor(sourceKey) {
  return sourceSkinningRigs.get(String(sourceKey)) || null;
}

function modelJointPoseFrame(jointId) {
  const id = Number(jointId);
  const rig = modelSkinningRig;
  const joint = modelJointForId(id);
  if (!rig || !joint || !rig.componentByJointId.has(id)) return null;
  const cache = rig.poseFrameCache.get(id);
  const parentId = modelParentForJoint(id, rig);
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

export function getRigJointPoseFrame(jointId) {
  return modelJointPoseFrame(jointId);
}

export function ensureModelRigLoaded() {
  if (modelRigState.loaded) return Promise.resolve(rigSnapshot());
  if (modelRigState.promise) return modelRigState.promise;
  const generation = modelWeightGeneration;
  const token = {};
  modelRigLoadToken = token;
  modelRigState.loading = true;
  modelRigState.error = null;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  const promise = ensureModelWeightsLoaded()
    .then(() => {
      if (generation !== modelWeightGeneration
          || modelRigLoadToken !== token) return rigSnapshot();
      if (modelWeightState.error) throw new Error(modelWeightState.error);
      const rigs = buildAllSourceSkinningRigs();
      buildModelSkinningRig(rigs);
      modelRigState.loaded = true;
      return rigSnapshot();
    })
    .catch(error => {
      if (generation !== modelWeightGeneration
          || modelRigLoadToken !== token) return rigSnapshot();
      modelRigState.error = error instanceof Error ? error.message : String(error);
      modelRigState.loaded = false;
      return rigSnapshot();
    })
    .finally(() => {
      if (generation !== modelWeightGeneration
          || modelRigLoadToken !== token) return;
      modelRigState.loading = false;
      modelRigState.promise = null;
      modelRigLoadToken = null;
      notifyModelRigChanged();
    });
  modelRigState.promise = promise;
  return promise;
}

export function getRigRotationSnapDegrees() {
  return modelRigState.rotationSnapDegrees;
}

export function setRigRotationSnapDegrees(value) {
  const degrees = Number(value);
  const next = RIG_ROTATION_SNAP_DEGREES.includes(degrees) ? degrees : 0;
  if (next === modelRigState.rotationSnapDegrees) return next;
  modelRigState.rotationSnapDegrees = next;
  notifyModelRigChanged();
  requestRender();
  return next;
}

export function beginRigJointPicking(intent = {}) {
  const next = String(intent?.type || '') === 'selected-joint'
    ? {type: 'selected-joint'} : null;
  if (!next || !modelRigState.loaded || !modelSkinningRig) return false;
  if (modelRigState.jointPickIntent
      && modelRigState.jointPickIntent.type === next.type) {
    return cancelRigJointPicking();
  }
  if (modelPickController.isEnabled()) modelPickController.cancel();
  modelRigState.jointPickIntent = next;
  modelRigState.pickStatus = 'Pick a Rig joint.';
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function cancelRigJointPicking() {
  if (!modelRigState.jointPickIntent && !modelRigState.pickStatus) return false;
  modelRigState.jointPickIntent = null;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function handleRigJointPicked(jointId,
    intent = modelRigState.jointPickIntent) {
  if (String(intent?.type || '') !== 'selected-joint') return false;
  const changed = selectRigJoint(jointId);
  if (changed) cancelRigJointPicking();
  return changed;
}

function selectRigBoneInternal(sourceKey, boneId) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  if (!rig || !Number.isInteger(id) || !rig.boneIds.includes(id)
      || !Number.isInteger(jointId)) return false;
  modelRigState.selectedJointId = jointId;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function selectRigJoint(jointId) {
  const joint = modelJointForId(jointId);
  if (!joint) return false;
  modelRigState.selectedJointId = joint.jointId;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function clearRigJointSelection() {
  const hadSelection = modelRigState.selectedJointId !== null
    && modelRigState.selectedJointId !== undefined;
  const hadStatus = !!modelRigState.pickStatus;
  modelRigState.selectedJointId = null;
  modelRigState.pickStatus = '';
  if (!hadSelection && !hadStatus) return false;
  notifyModelRigChanged();
  requestRender();
  return true;
}

function setRigComponentRootForSource(sourceKey, boneId) {
  const rig = rigSourceFor(sourceKey);
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

function strictRigQuaternion(value) {
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

/** Apply several model-local rotations in one authoritative pose transaction. */
function setRigJointRotations(rotationsByJointId, options = {}) {
  if (!modelSkinningRig || !modelRigState.loaded) return false;
  const updates = [];
  const seen = new Set();
  for (const [rawId, value] of rotationEntries(rotationsByJointId)) {
    const jointId = Number(rawId);
    const joint = modelJointForId(jointId);
    const component = modelComponentForJoint(jointId);
    if (!Number.isInteger(jointId) || !joint || !component
        || seen.has(jointId)) {
      modelRigState.pickStatus = 'Could not apply the Rig pose.';
      notifyModelRigChanged();
      return false;
    }
    if (Number(component.rootId) === jointId) {
      modelRigState.pickStatus = 'Component root / anchor cannot be rotated.';
      notifyModelRigChanged();
      return false;
    }
    const quaternion = strictRigQuaternion(value);
    if (!quaternion) {
      modelRigState.pickStatus = 'Could not apply the Rig pose.';
      notifyModelRigChanged();
      return false;
    }
    seen.add(jointId);
    updates.push([jointId, quaternion]);
  }
  if (!updates.length) return false;

  // Validation is complete before the authoritative map is touched.
  updates.forEach(([jointId, quaternion]) => {
    modelSkinningRig.poseRotationByJointId.set(jointId, quaternion);
  });
  const selectedId = options?.selectedJointId === null
    || options?.selectedJointId === undefined
    ? null : Number(options.selectedJointId);
  if (Number.isInteger(selectedId) && modelJointForId(selectedId)) {
    modelRigState.selectedJointId = selectedId;
  }
  const dragging = options?.dragging === true;
  applyModelPose({dragging});
  modelRigState.pickStatus = '';
  if (dragging) {
    const selectedJoint = modelJointForId(modelRigState.selectedJointId);
    const member = selectedJoint?.representativeMember || selectedJoint?.members?.[0];
    if (member) notifyModelRigPoseChanged(
      rigSourceFor(member.sourceKey), member.boneId, updates.map(([id]) => id));
    else notifyModelRigChanged();
  } else {
    notifyModelRigChanged();
  }
  return true;
}

function setRigJointRotationForSource(sourceKey, boneId, quaternion, options = {}) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const component = rigComponentForBone(rig, id);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  const modelComponentId = modelSkinningRig?.componentByJointId?.get(jointId);
  const modelComponent = Number.isInteger(Number(modelComponentId))
    ? modelSkinningRig?.components?.[Number(modelComponentId)] : null;
  if (!rig || !component || !rig.boneIds.includes(id)
      || !Number.isInteger(jointId) || !modelComponent) return false;
  if (modelComponent.rootId === jointId) {
    modelRigState.pickStatus = 'Component root / anchor cannot be rotated.';
    notifyModelRigChanged();
    return false;
  }
  return setRigJointRotations(new Map([[jointId, quaternion]]), {
    ...options, selectedJointId: jointId,
  });
}

export function setRigJointRotation(jointId, quaternion, options = {}) {
  const joint = modelJointForId(jointId);
  const member = joint?.representativeMember || joint?.members?.[0];
  return member ? setRigJointRotationForSource(
    member.sourceKey, member.boneId, quaternion, options) : false;
}

function finishRigPoseForSource(sourceKey, boneId) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  if (!rig || !Number.isInteger(id) || !rig.boneIds.includes(id)
      || !Number.isInteger(jointId)) return false;
  if (!modelRigHasActivePhysics()) {
    (modelSkinningRig?.sourceRigs || []).forEach(finalizeSourcePoseBounds);
  }
  if (modelRigHasActivePhysics()) modelPhysicsSession.wake();
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function setRigPoseControlStatus(message = '') {
  modelRigState.pickStatus = String(message || '');
  notifyModelRigChanged();
  return modelRigState.pickStatus;
}

function resetRigBoneForSource(sourceKey, boneId) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  if (!rig || !rig.boneIds.includes(id) || !Number.isInteger(jointId)) return false;
  modelSkinningRig?.poseRotationByJointId.delete(jointId);
  applyModelPose();
  notifyModelRigChanged();
  return true;
}

export function setRigJointRoot(jointId) {
  const joint = modelJointForId(jointId);
  const member = joint?.representativeMember || joint?.members?.[0];
  return member ? setRigComponentRootForSource(
    member.sourceKey, member.boneId) : false;
}

export function finishRigJointPose(jointId) {
  const joint = modelJointForId(jointId);
  const member = joint?.representativeMember || joint?.members?.[0];
  return member ? finishRigPoseForSource(member.sourceKey, member.boneId)
    : false;
}

export function resetRigJoint(jointId) {
  const joint = modelJointForId(jointId);
  const member = joint?.representativeMember || joint?.members?.[0];
  return member ? resetRigBoneForSource(member.sourceKey, member.boneId)
    : false;
}

export function resetRigPose() {
  const changed = resetModelPose({request: false});
  const presetWasSelected = rigPresetState.selectedPresetId !== null
    || rigPresetState.lastApplyResult !== null;
  rigPresetState.selectedPresetId = null;
  rigPresetState.lastApplyResult = null;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  requestRender();
  return changed || presetWasSelected;
}

function syncPhysicsParticipants(changedSourceKeys = null) {
  if (!modelPhysicsSession.getState().enabled) return;
  if (!selectedBoneCount(modelWeightState.selectedBonesBySource)) {
    disablePhysicsSession();
    return;
  }
  const groups = new Map();
  for (const mesh of knownMeshes) {
    const state = states.get(mesh);
    if (!eligibleSkinningMesh(mesh)) {
      if (state?.error) {
        state.physicsParticipantStatus = 'failed';
        state.physicsParticipantError = state.error;
        modelPhysicsSession.markFailed(mesh, state.error);
      }
      continue;
    }
    if (!state?.loaded || !state.skinningSourceKey) {
      if (state?.error) {
        state.physicsParticipantStatus = 'failed';
        state.physicsParticipantError = state.error;
        modelPhysicsSession.markFailed(mesh, state.error);
      }
      continue;
    }
    modelPhysicsSession.clearStatus(mesh);
    const members = groups.get(state.skinningSourceKey) || [];
    members.push(mesh);
    groups.set(state.skinningSourceKey, members);
  }
  const affected = changedSourceKeys
    ? new Set(changedSourceKeys)
    : new Set([...groups.keys(), ...sourcePhysicsRigs.keys()]);
  for (const sourceKey of affected) {
    if (modelPhysicsSession.getParticipant(sourceKey)) {
      modelPhysicsSession.detach(sourceKey);
    }
    sourcePhysicsRigs.delete(sourceKey);
  }

  for (const sourceKey of [...sourcePhysicsRigs.keys()]) {
    if (!groups.has(sourceKey)
        || !modelWeightState.selectedBonesBySource.has(sourceKey)) {
      modelPhysicsSession.detach(sourceKey);
      sourcePhysicsRigs.delete(sourceKey);
    }
  }

  let attached = false;
  for (const [sourceKey, members] of groups) {
    const selected = modelWeightState.selectedBonesBySource.get(sourceKey);
    members.forEach(mesh => {
      const state = states.get(mesh);
      if (state) {
        state.physicsParticipantStatus = 'not-selected';
        state.physicsParticipantError = null;
      }
    });
    if (!selected?.size) continue;
    const rig = sourcePhysicsRigs.get(sourceKey)
      || createSourcePhysicsRig(sourceKey, members);
    sourcePhysicsRigs.set(sourceKey, rig);
    if (!rig.physicsForest) continue;
    if (!modelPhysicsSession.getParticipant(sourceKey)) {
      attached = modelPhysicsSession.attach(
        createSourcePhysicsParticipant(rig)) || attached;
    }
  }
  if (attached) {
    modelPhysicsSession.wake();
    invalidateCharacterShadowGeometry({request: false});
    requestRender();
  }
  notifyModelRigChanged();
}

function syncPhysicsToSelection(changedSourceKeys = null) {
  const shouldEnable = modelWeightState.loaded
    && selectedBoneCount(modelWeightState.selectedBonesBySource) > 0;
  const enabled = modelPhysicsSession.getState().enabled;
  if (!shouldEnable) {
    if (enabled) disablePhysicsSession();
    return false;
  }
  if (!enabled) {
    modelPhysicsSession.enable(getModelTransformState());
  }
  syncPhysicsParticipants(changedSourceKeys);
  return true;
}

function resetModelPhysicsInternal() {
  const defaults = DEFAULT_MODEL_PHYSICS_SETTINGS;
  return modelPhysicsSession.reset(getModelTransformState(), {
    settingsPatch: {
      frequencyHz: defaults.frequencyHz,
      dampingRatio: defaults.dampingRatio,
      angularResponse: defaults.angularResponse,
      translationResponse: defaults.translationResponse,
      velocityResponse: defaults.velocityResponse,
      gravityEnabled: defaults.gravityEnabled,
      gravityScale: defaults.gravityScale,
      constraintsEnabled: defaults.constraintsEnabled,
      maxBendDegrees: defaults.maxBendDegrees,
    },
  });
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

function weightForBone(indices, weights, influenceCount,
                              vertexIndex, boneId) {
  if (!indices || !weights || influenceCount <= 0) return 0;
  const start = vertexIndex * influenceCount;
  let total = 0;
  for (let influence = 0; influence < influenceCount; influence += 1) {
    if (indices[start + influence] === boneId) {
      const weight = weights[start + influence];
      if (Number.isFinite(weight) && weight > 0) total += weight;
    }
  }
  return total;
}

function buildBoneIds(indices, weights, influenceCount) {
  const result = new Set();
  if (!indices || !weights || influenceCount <= 0) return [];
  const count = Math.min(indices.length, weights.length);
  for (let offset = 0; offset < count; offset += 1) {
    if (Number.isFinite(weights[offset]) && weights[offset] > 0) {
      result.add(Number(indices[offset]));
    }
  }
  return [...result].sort((a, b) => a - b);
}


function typedView(buffer, descriptor, Type, typeName) {
  if (!descriptor || descriptor.type !== typeName) {
    throw new Error('Skin data has an unsupported binary layout.');
  }
  const offset = Number(descriptor.offset);
  const length = Number(descriptor.length);
  if (!Number.isInteger(offset) || !Number.isInteger(length)
      || offset < 0 || length < 0 || offset % Type.BYTES_PER_ELEMENT
      || length % Type.BYTES_PER_ELEMENT
      || offset + length > buffer.byteLength) {
    throw new Error('Skin data has an invalid binary range.');
  }
  return new Type(buffer, offset, length / Type.BYTES_PER_ELEMENT);
}

function captureBaseline(mesh, state) {
  const position = mesh.geometry?.attributes?.position;
  if (!position) throw new Error('The selected mesh has no position data.');
  const normal = mesh.geometry?.attributes?.normal;
  state.baselinePositions = new Float32Array(position.array);
  state.baselineNormals = normal ? new Float32Array(normal.array) : null;
  state.originalMaterial = mesh.material;
}

function restoreNormals(mesh, state) {
  if (!state.baselineNormals) {
    mesh.geometry.computeVertexNormals();
    return;
  }
  let normal = mesh.geometry.attributes.normal;
  if (!normal || normal.array.length !== state.baselineNormals.length) {
    normal = new THREE.BufferAttribute(
      new Float32Array(state.baselineNormals.length), 3);
    mesh.geometry.setAttribute('normal', normal);
  }
  normal.array.set(state.baselineNormals);
  normal.needsUpdate = true;
}

function buildInfluenceGraph(
    mesh, state, requestedEvidenceMode = 'vertex', surfaceEvidence = null) {
  if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
  const radius = Number(mesh.geometry.boundingSphere?.radius);
  const rawNodes = state.influenceNodes || buildRigInfluenceNodes(
    state.baselinePositions, state.indices, state.weights,
    state.influenceCount, state.boneIds);
  state.influenceNodes = rawNodes;
  const measure = surfaceEvidence || (requestedEvidenceMode === 'surface'
    ? inspectSurfaceTopology(
      state.baselinePositions, mesh.geometry?.index?.array || null) : null);
  const evidenceMode = requestedEvidenceMode === 'surface'
    && measure?.surfaceEvidenceAvailable ? 'surface' : 'vertex';
  if (evidenceMode === 'surface') {
    return buildSurfaceInfluenceGraph(
      state.baselinePositions, mesh.geometry?.index?.array || null,
      state.indices, state.weights, state.influenceCount, state.boneIds,
      Number.isFinite(radius) && radius > 0 ? radius : null);
  }
  const relationships = buildRigInfluenceRelationships(
    state.baselinePositions, state.indices, state.weights, state.influenceCount,
    rawNodes, Number.isFinite(radius) && radius > 0 ? radius : null, {});
  return {
    nodes: rawNodes,
    relationships,
    boundingSphereRadius: Number.isFinite(radius) && radius > 0 ? radius : null,
    evidenceMode,
    triangleCount: measure?.triangleCount || 0,
    validTriangleCount: measure?.validTriangleCount || 0,
    degenerateTriangleCount: measure?.degenerateTriangleCount || 0,
    invalidTriangleCount: measure?.invalidTriangleCount || 0,
    totalSurfaceArea: measure?.totalSurfaceArea || 0,
    measuredVertexCount: measure?.measuredVertexCount || 0,
    zeroMeasureVertexCount: measure?.zeroMeasureVertexCount || 0,
    fallbackReason: requestedEvidenceMode === 'surface'
      && evidenceMode === 'vertex'
      ? 'surface_evidence_unavailable'
      : requestedEvidenceMode === 'vertex'
        && measure && !measure.surfaceEvidenceAvailable
        ? 'surface_evidence_unavailable' : null,
  };
}

function ensureInfluenceGraph(
    mesh, state, evidenceMode = 'vertex', surfaceEvidence = null) {
  if (!state.influenceGraph
      || state.influenceGraph.evidenceMode !== evidenceMode) {
    state.influenceGraph = buildInfluenceGraph(
      mesh, state, evidenceMode, surfaceEvidence);
  }
  return state.influenceGraph;
}

/** Re-baseline loaded weights after the authoritative shape geometry changes. */
export function refreshSkinningAfterShapeChange(mesh) {
  const state = states.get(mesh);
  const position = mesh?.geometry?.attributes?.position;
  invalidateHumanoidDetection();
  clearPickedPoint();
  if (!state?.loaded || !position) return false;
  const preservedRootSignatures = new Set(
    modelRigState.explicitRootSignatures);
  const sourceKey = state.skinningSourceKey;
  // Capture the authoritative shaped geometry before physics detachment or
  // pose reset can restore the previous baseline onto this mesh.
  const shapedPositions = new Float32Array(position.array);
  const normal = mesh.geometry.attributes.normal;
  const shapedNormals = normal ? new Float32Array(normal.array) : null;
  const participant = sourceKey
    ? modelPhysicsSession.getParticipant(sourceKey) : null;
  const wasPhysicsEnabled = !!participant || state.physicsEnabled;
  if (participant) modelPhysicsSession.detach(sourceKey);
  if (sourceKey) sourcePhysicsRigs.delete(sourceKey);
  if (sourceKey) sourceSkinningRigs.delete(sourceKey);
  if (modelSkinningRig) resetModelPose({request: false});
  // A shape change invalidates the current model Rig even when weight data
  // remains reusable. Obsolete async continuations must not publish it.
  modelRigLoadToken = null;
  modelRigState.promise = null;
  modelRigState.loading = false;
  state.poseTransforms = null;
  state.poseRotations = new Map();
  state.poseActiveVertices = null;
  state.combinedActiveVertices = null;
  state.combinedPoseVerticesRef = null;
  state.combinedPhysicsVerticesRef = null;
  modelRigState.loaded = false;
  modelSkinningRig = null;
  modelRigState.selectedJointId = null;
  modelRigState.structureRevision = 0;
  modelRigState.jointPickIntent = null;
  modelRigState.ikEnabled = false;
  modelRigState.activeLimbRole = 'left_arm';
  modelRigState.explicitRootSignatures = preservedRootSignatures;
  rigPresetState.lastApplyResult = null;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  position.array.set(shapedPositions);
  position.needsUpdate = true;
  if (normal && shapedNormals && normal.array.length === shapedNormals.length) {
    normal.array.set(shapedNormals);
    normal.needsUpdate = true;
  }
  // resetModelPose() may have recomputed bounds for the old baseline. Restore
  // the bounds for the shaped arrays before publishing the new baseline.
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  state.baselinePositions = new Float32Array(position.array);
  state.baselineNormals = normal ? new Float32Array(normal.array) : null;
  state.influenceNodes = buildRigInfluenceNodes(
    state.baselinePositions, state.indices, state.weights,
    state.influenceCount, state.boneIds);
  state.centerByBoneId = new Map(state.influenceNodes.map(node => [
    node.boneId, node.weightedCenter]));
  state.influenceGraph = null;

  if (wasPhysicsEnabled && modelPhysicsSession.getState().enabled
      && sourceKey) {
    syncPhysicsParticipants(new Set([sourceKey]));
    modelPhysicsSession.wake();
  }
  if (state.heatmapMode) updateModelWeightHeatmap(new Set([sourceKey]));
  return true;
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

function applyDeformation(mesh, state, {
  request = true,
  invalidateShadow = true,
  skipHidden = false,
  composedTransforms = null,
  composedRotations = null,
} = {}) {
  if (!state.loaded || !state.baselinePositions) return;
  if (skipHidden && !mesh.visible) return false;
  const physicsActive = state.deformationMode === 'physics'
    && state.physicsEnabled && composedTransforms && composedRotations;
  const position = mesh.geometry.attributes.position;
  const finalTransforms = physicsActive
    ? composedTransforms
    : state.poseTransforms;
  const finalRotations = physicsActive
    ? composedRotations
    : state.poseRotations;
  const previousVertices = state.combinedActiveVertices || EMPTY_ACTIVE_VERTICES;
  const activeVertices = combinedActiveVerticesForState(state);
  const removedVertices = vertexDifference(previousVertices, activeVertices);
  if (removedVertices.length) restorePoseVertices(mesh, state, removedVertices);
  let deformedVertices = 0;
  if (activeVertices.length && finalTransforms) {
    deformedVertices = applyWeightedTransformDeformationInto(
      position.array, state.baselinePositions, state.indices, state.weights,
      state.influenceCount, finalTransforms, activeVertices);
    const normal = mesh.geometry.attributes.normal;
    if (normal && state.baselineNormals
        && normal.array.length === state.baselineNormals.length
        && finalRotations) {
      applyWeightedNormalDeformationInto(
        normal.array, state.baselineNormals, state.indices, state.weights,
        state.influenceCount, finalRotations, activeVertices);
      normal.needsUpdate = true;
    }
  }
  const changed = removedVertices.length > 0 || deformedVertices > 0;
  state.finalBoundsDirty = state.finalBoundsDirty || changed;
  if (changed) markFinalBoundsDirty(mesh, state);
  position.needsUpdate = changed || activeVertices.length > 0;
  if (invalidateShadow && changed) invalidateCharacterShadowGeometry({request});
  else if (request && changed) requestRender();
  return changed;
}

function finalizePhysicsGeometry(mesh, state) {
  finalizeDeformationGeometry(mesh, state);
}

function updateHeatmap(mesh, state, selectedMask = state.selectedWeightMask) {
  if (!state.heatmapMode || !selectedMask) return;
  const count = Math.floor(state.indices.length / state.influenceCount);
  const colors = new Float32Array(count * 3);
  for (let vertex = 0; vertex < count; vertex += 1) {
    const value = Math.max(0, Math.min(1,
      Number(selectedMask[vertex]) || 0));
    const offset = vertex * 3;
    // Blue at zero, yellow/red at high influence for quick spatial reading.
    colors[offset] = value;
    colors[offset + 1] = Math.min(1, value * 2);
    colors[offset + 2] = 1 - value;
  }
  mesh.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  mesh.geometry.attributes.color.needsUpdate = true;
  if (!state.debugMaterial) {
    state.debugMaterial = new THREE.MeshBasicMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
    });
  }
  mesh.material = state.debugMaterial;
}

function selectedWeightPresent(mask) {
  return !!mask && mask.some(value => value > 0);
}

function updateModelWeightHeatmap(changedSourceKeys = null) {
  knownMeshes.forEach(mesh => {
    const state = states.get(mesh);
    if (!state?.loaded) return;
    if (changedSourceKeys
        && !changedSourceKeys.has(state.skinningSourceKey)) return;
    const mask = state.selectedWeightMask;
    if (modelWeightState.heatmapEnabled && selectedWeightPresent(mask)) {
      state.heatmapMode = 'bone';
      updateHeatmap(mesh, state, mask);
    } else if (state.heatmapMode) {
      disableHeatmap(mesh, state);
    }
  });
}

function disableHeatmap(mesh, state) {
  state.heatmapMode = null;
  mesh.material = state.originalMaterial;
  mesh.geometry.deleteAttribute('color');
  if (state.debugMaterial) {
    state.debugMaterial.dispose();
    state.debugMaterial = null;
  }
}

export function disposeSkinningExperiment(mesh, {preserveRegistration = false} = {}) {
  const state = states.get(mesh);
  if (preserveRegistration) modelPhysicsSession.detach(mesh);
  else unregisterSkinningMesh(mesh);
  if (!state) return;
  state.disposed = true;
  if (state.debugMaterial) state.debugMaterial.dispose();
  mesh.geometry?.deleteAttribute?.('color');
  mesh.material = state.originalMaterial || mesh.material;
  states.delete(mesh);
}
