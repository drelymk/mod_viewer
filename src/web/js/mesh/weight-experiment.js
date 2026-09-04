// Explicit, removable skin-weight experiment. Normal mesh loading never
// imports or invokes this module's bridge operation until the Weight tab asks.

import * as THREE from 'three';
import {
  camera, controls, getModelTransformState, invalidateCharacterShadowGeometry,
  invalidateCharacterShadowMap,
  renderer,
  setPhysicsInteractionEnabled,
} from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';
import {
  applyWeightedNormalDeformationInto,
  applyWeightedTransformDeformationInto,
  buildForestTransformsFromLocalRotations,
} from './weight-deformation.js';
import {
  buildInferredRigRestFrames, eulerFromRestFrameDelta,
  poseToRestFrameDelta, restFrameDeltaToPose,
} from './weight-rig-frames.js';
import {
  buildModelRigReconciliation, orientModelRigForest, sourceBoneKey,
} from './weight-rig-reconcile.js';
import {
  GRAVITY_WORLD_DIRECTION, MIN_GRAVITY_LEVER_RATIO, STANDARD_GRAVITY,
  applyReferenceFrameAngularDelta,
  applyReferenceFrameLinearVelocityDelta,
  applyReferenceFrameTranslationDelta,
  applyPhysicsJointLimits, initializePhysicsState,
  buildGravityAngularAccelerations, buildPhysicsConstraintDiagnostics,
  buildPhysicsEquilibriumRotations, buildPhysicsJointLimits,
  buildPhysicsTargetRotations,
  isPhysicsSettled, resetPhysicsState,
  stepSpringPhysics,
} from './weight-physics.js';
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
  buildMaximumSpanningTree as buildRigMaximumSpanningTree,
  candidateRelationshipEdges as rigCandidateRelationshipEdges,
  jointPivotMap,
  orientForest as rigOrientForest,
  orientTree as rigOrientTree,
} from './weight-rig.js';
import { createWeightPickController } from '../scene/weight-pick-controller.js';
import { computeModelBounds } from '../scene/model-bounds.js';
import {
  DEFAULT_MODEL_PHYSICS_SETTINGS, MODEL_PHYSICS_STEP,
  createModelPhysicsSession,
} from './model-physics-session.js';
import {
  addWeightPhysicsPerformance, getWeightPhysicsPerformanceStats,
  performanceNow, resetWeightPhysicsPerformanceStats,
} from './weight-physics-performance.js';
export {
  buildForestTransformsFromLocalRotations, applyWeightedTransformDeformation,
} from './weight-deformation.js';
export {
  buildInferredRigRestFrames, eulerFromRestFrameDelta,
  poseToRestFrameDelta, restFrameDeltaToPose,
} from './weight-rig-frames.js';
export {
  buildModelRigReconciliation, orientModelRigForest, sourceBoneKey,
} from './weight-rig-reconcile.js';
export {
  getWeightPhysicsPerformanceStats, resetWeightPhysicsPerformanceStats,
};
export {
  buildRigInfluenceNodes as buildInfluenceNodes,
  buildRigInfluenceRelationships as buildInfluenceRelationships,
  rigCandidateRelationshipEdges as candidateRelationshipEdges,
  rigOrientForest as orientForest,
  rigOrientTree as orientTree,
  buildRigMaximumSpanningTree as buildMaximumSpanningTree,
};

export {
  DEFAULT_ANGLE_TOLERANCE, DEFAULT_PHYSICS_DAMPING_RATIO,
  DEFAULT_PHYSICS_FREQUENCY_HZ, DEFAULT_PHYSICS_MAX_BEND_DEGREES,
  DEFAULT_VELOCITY_TOLERANCE,
  GRAVITY_WORLD_DIRECTION, MIN_GRAVITY_LEVER_RATIO, STANDARD_GRAVITY,
  MAX_ANGULAR_VELOCITY, MAX_LOCAL_ANGLE,
  applyReferenceFrameAngularDelta,
  applyReferenceFrameLinearVelocityDelta,
  applyReferenceFrameTranslationDelta,
  applyPhysicsJointLimits,
  buildGravityAngularAccelerations, buildPhysicsConstraintDiagnostics,
  buildPhysicsEquilibriumRotations, buildPhysicsJointLimits,
  representativeComponentLever,
  buildPhysicsTargetRotations, initializePhysicsState, isPhysicsSettled,
  physicsRotationMap, resetPhysicsState, rotationVectorBetween,
  stepSpringPhysics,
} from './weight-physics.js';
const states = new WeakMap();
const knownMeshes = new Set();
const sourcePhysicsRigs = new Map();
const sourceSkinningRigs = new Map();
let modelSkinningRig = null;
let modelWeightGeneration = 0;
let selectedWeightMaskBuildCount = 0;
let modelBoneStatsBuildCount = 0;
let selectionSavePromise = null;
const modelWeightState = {
  loaded: false,
  loading: false,
  promise: null,
  error: null,
  noWeights: false,
  sources: [],
  selectedBonesBySource: new Map(),
  savedBonesBySource: new Map(),
  sourceDescriptors: new Map(),
  savedSelectionApplied: false,
  savingSelection: false,
  selectionSaveError: null,
  heatmapEnabled: false,
  loadedMeshCount: 0,
  failedMeshCount: 0,
  pickedPoint: null,
  pickerViewMode: 'all',
  weightViewMode: 'authored',
  weightViewJointId: null,
  pickStatus: '',
  picking: false,
};

const modelRigState = {
  loaded: false,
  loading: false,
  promise: null,
  error: null,
  visible: false,
  picking: false,
  activeSourceKey: null,
  selectedBoneBySource: new Map(),
  selectedJointId: null,
  structureRevision: 0,
  pickedPoint: null,
  pickStatus: '',
  rigAnalysisMs: 0,
  rigTransformMs: 0,
  rigDeformMs: 0,
  rigDeformedVertexCount: 0,
  rigReconcileMs: 0,
  rigCandidateCount: 0,
  rigEquivalentClusterCount: 0,
  rigAttachmentCount: 0,
  rigAmbiguousCount: 0,
  rotationSnapDegrees: 0,
};

const RIG_ROTATION_SNAP_DEGREES = Object.freeze([0, 5, 15, 30]);

let nextRigStructureRevision = 0;
const RIG_IDENTITY_MATRIX = new THREE.Matrix4();

const PHYSICS_DIAGNOSTIC_VECTOR_KEYS = Object.freeze([
  'lastRootAngularDeltaVector', 'lastRootTranslationDeltaWorld',
  'lastRootTranslationDeltaLocal', 'lastTranslationLagRotationVector',
  'lastRootLinearVelocityWorld', 'lastRootLinearVelocityLocal',
  'lastRootLinearVelocityDelta', 'physicsVirtualLinearVelocityLocal',
]);
const PHYSICS_DIAGNOSTIC_SCALAR_KEYS = Object.freeze([
  'lastRootAngularDeltaMagnitude', 'motionEventCount',
  'lastTranslationLagRotationMagnitude', 'translationEventCount',
]);

const modelPhysicsSession = createModelPhysicsSession({
  onInputOwnershipChanged: enabled => setPhysicsInteractionEnabled(enabled),
  onFrame: ({visibleParticipants}) => {
    if (!visibleParticipants?.length) return;
    invalidateCharacterShadowMap({request: false});
    addWeightPhysicsPerformance('dynamicShadowUpdateCount');
    requestRender();
  },
  onStateChanged: detail => {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent(
        'mod-viewer-model-physics-changed', {detail}));
    }
  },
  requestAnimationFrame: callback =>
    typeof window !== 'undefined' ? window.requestAnimationFrame(callback) : null,
  cancelAnimationFrame: frameId =>
    typeof window !== 'undefined' ? window.cancelAnimationFrame(frameId) : null,
});

const weightPickController = createWeightPickController({
  canvas: renderer.domElement,
  camera,
  controls,
  getMeshes: modelPickMeshes,
  onPick: handlePickedIntersection,
  onStateChanged: (picking, {cancelled} = {}) => {
    modelWeightState.picking = picking;
    if (picking || cancelled) notifyModelWeightChanged();
  },
  requestRender,
});

const rigPickController = createWeightPickController({
  canvas: renderer.domElement,
  camera,
  controls,
  getMeshes: modelPickMeshes,
  onPick: handleRigPickedIntersection,
  onStateChanged: (picking, {cancelled} = {}) => {
    modelRigState.picking = picking;
    if (picking || cancelled) notifyModelRigChanged();
  },
  requestRender,
});

export const CANDIDATE_CONTAINMENT_THRESHOLD = 0.02;
export const CANDIDATE_JACCARD_THRESHOLD = 0.01;
export const WEIGHT_PICK_RADIUS_RATIO = 0.02;

const ERROR_MESSAGES = Object.freeze({
  skinning_source_identity_unavailable:
    'The skin-weight source identity is unavailable for this draw.',
});

function newState() {
  return {
    loaded: false,
    error: null,
    influenceCount: 0,
    boneIds: [],
    indices: null,
    weights: null,
    deformationMode: null,
    physicsEnabled: false,
    physicsJointLimits: null,
    physicsConstraintDiagnostics: null,
    physicsGravityLocal: [...GRAVITY_WORLD_DIRECTION],
    physicsGravityAccelerations: null,
    physicsGravityDiagnostics: null,
    physicsTargetByBoneId: null,
    physicsEquilibriumByBoneId: null,
    lastRootAngularDeltaVector: [0, 0, 0],
    lastRootAngularDeltaMagnitude: 0,
    motionEventCount: 0,
    lastRootTranslationDeltaWorld: [0, 0, 0],
    lastRootTranslationDeltaLocal: [0, 0, 0],
    lastTranslationLagRotationVector: [0, 0, 0],
    lastTranslationLagRotationMagnitude: 0,
    translationEventCount: 0,
    lastRootLinearVelocityWorld: [0, 0, 0],
    lastRootLinearVelocityLocal: [0, 0, 0],
    lastRootLinearVelocityDelta: [0, 0, 0],
    physicsVirtualLinearVelocityLocal: [0, 0, 0],
    physicsState: null,
    physicsTransforms: null,
    physicsTransformCache: new Map(),
    physicsRotations: new Map(),
    physicsSettled: true,
    physicsParticipantStatus: 'not-attempted',
    physicsParticipantError: null,
    selectedWeightMask: null,
    physicsActiveVertices: null,
    physicsBoundsDirty: false,
    prePhysicsFrustumCulled: null,
    influenceNodes: null,
    influenceGraph: null,
    baselinePositions: null,
    baselineNormals: null,
    originalMaterial: null,
    debugMaterial: null,
    heatmapMode: null,
    diagnostics: null,
    encoding: null,
    centerByBoneId: null,
    physicsCenterByBoneId: null,
    physicsForest: null,
    physicsSelectionKey: '',
    poseTransforms: null,
    poseRotations: new Map(),
    poseActiveVertices: null,
    poseBoundsDirty: false,
    posePreFrustumCulled: null,
    skinningSourceKey: '',
    skinningSourceFile: '',
    skinningBoneOffset: 0,
  };
}

function stateFor(mesh) {
  if (!mesh) return null;
  let state = states.get(mesh);
  if (!state) {
    state = newState();
    states.set(mesh, state);
  }
  return state;
}

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
    weightViewMode: modelWeightState.weightViewMode,
    weightViewJointId: modelWeightState.weightViewJointId,
    modelJoints: modelWeightState.weightViewMode === 'model'
      ? (modelSkinningRig?.joints || []).map(joint => ({
        jointId: joint.jointId,
        signature: joint.signature,
        members: (joint.members || []).map(member => ({...member})),
        restCenter: [...(joint.restCenter || [0, 0, 0])],
      })) : [],
    modelJointsLoading: modelWeightState.weightViewMode === 'model'
      && modelRigState.loading,
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
  return Math.abs(Number(value?.x) || 0) < 1e-8
    && Math.abs(Number(value?.y) || 0) < 1e-8
    && Math.abs(Number(value?.z) || 0) < 1e-8
    && Math.abs((Number(value?.w) || 1) - 1) < 1e-8;
}

function rigComponentForBone(rig, boneId) {
  const componentId = rig?.inferredForest?.componentByBoneId?.[boneId];
  return Number.isInteger(Number(componentId))
    ? rig?.inferredForest?.components?.[Number(componentId)] || null : null;
}

function rigParentForBone(rig, boneId) {
  const component = rigComponentForBone(rig, boneId);
  const value = component?.parentById?.[boneId];
  if (value === null || value === undefined) return null;
  const parentId = Number(value);
  return Number.isFinite(parentId) ? parentId : null;
}

function rebuildSourceRigRestFrames(rig) {
  const frames = buildInferredRigRestFrames(
    rig.inferredForest, rig.centerByBoneId, rig.jointPivotByBoneId);
  rig.restFrameByBoneId = frames.frameByBoneId;
  rig.restDirectionByBoneId = frames.directionByBoneId;
  rig.restFrameEvidenceByBoneId = frames.evidenceByBoneId;
  rig.continuationChildByBoneId = frames.continuationChildByBoneId;
  rig.poseFrameCache?.clear();
  rig.structureRevision = ++nextRigStructureRevision;
  return frames;
}

function updateSourcePoseFrameCache(rig, transforms) {
  const seen = new Set();
  for (const boneId of rig.boneIds || []) {
    const id = Number(boneId);
    if (!Number.isInteger(id)) continue;
    const parentId = rigParentForBone(rig, id);
    const parentTransform = parentId === null
      ? RIG_IDENTITY_MATRIX : transforms.get(parentId) || RIG_IDENTITY_MATRIX;
    const pivotValues = rig.jointPivotByBoneId.get(id)
      || (parentId !== null ? rig.centerByBoneId.get(parentId) : null)
      || rig.centerByBoneId.get(id) || [0, 0, 0];
    const centerValues = rig.centerByBoneId.get(id) || [0, 0, 0];
    const frame = rig.poseFrameCache.get(id) || {
      center: new THREE.Vector3(),
      pivot: new THREE.Vector3(),
    };
    frame.center.fromArray(centerValues).applyMatrix4(
      transforms.get(id) || RIG_IDENTITY_MATRIX);
    frame.pivot.fromArray(pivotValues).applyMatrix4(parentTransform);
    rig.poseFrameCache.set(id, frame);
    seen.add(id);
  }
  for (const id of rig.poseFrameCache.keys()) {
    if (!seen.has(id)) rig.poseFrameCache.delete(id);
  }
}

function rigSourceSnapshot(rig, {debug = false} = {}) {
  if (!rig) return null;
  const components = (rig.inferredForest?.components || []).map(component => ({
    componentId: component.componentId,
    rootId: component.rootId,
    nodeIds: [...component.nodeIds],
    parentById: {...component.parentById},
    childrenById: Object.fromEntries(Object.entries(component.childrenById || {})
      .map(([id, children]) => [id, [...children]])),
    depthById: {...component.depthById},
    maxDepth: component.maxDepth,
  }));
  const nodes = (rig.influenceGraph?.nodes || []).map(node => ({
    boneId: node.boneId,
    weightedCenter: [...(node.weightedCenter || [0, 0, 0])],
  }));
  const jointPivotByBoneId = Object.fromEntries(
    [...(rig.jointPivotByBoneId || [])].map(([boneId, pivot]) => [
      boneId, [...pivot]]));
  const forestEdges = components.flatMap(component =>
    Object.entries(component.parentById || {}).flatMap(([childId, parentId]) => {
      if (parentId === null || parentId === undefined) return [];
      const child = Number(childId);
      const parent = Number(parentId);
      return [{
        boneA: parent,
        boneB: child,
        parentId: parent,
        childId: child,
        jointCenter: jointPivotByBoneId[child]
          ? [...jointPivotByBoneId[child]] : null,
      }];
    }));
  const source = {
    sourceKey: rig.sourceKey,
    sourceFile: rig.sourceFile,
    boneIdOffset: rig.boneIdOffset,
    structureRevision: rig.structureRevision,
    boneCount: rig.influenceGraph?.nodes?.length || 0,
    boneIds: (rig.influenceGraph?.nodes || []).map(node => node.boneId),
    components,
    nodes,
    forestEdges,
    jointPivotByBoneId,
    selectedBoneId: modelRigState.selectedBoneBySource.get(rig.sourceKey) ?? null,
    selectedJointId: modelRigState.selectedJointId,
    modelJointIds: Object.fromEntries((rig.boneIds || []).map(boneId => [
      boneId, modelJointIdForSourceBone(rig.sourceKey, boneId) ?? null])),
    poseBoneIds: [...rig.poseRotationByBoneId.entries()]
      .filter(([, quaternion]) => !quaternionIsIdentity(quaternion))
      .map(([boneId]) => boneId),
    poseRotationByBoneId: Object.fromEntries(
      [...rig.poseRotationByBoneId.entries()].map(([boneId, quaternion]) => [
        boneId, quaternion.toArray()])),
    physicsActive: !!rig.physicsRig?.physicsState,
  };
  if (debug) {
    source.nodes = (rig.influenceGraph?.nodes || []).map(node => ({
      ...node,
      weightedCenter: [...(node.weightedCenter || [0, 0, 0])],
    }));
    source.poseRotationByBoneId = Object.fromEntries(
      [...rig.poseRotationByBoneId.entries()].map(([boneId, quaternion]) => [
        boneId, quaternion.toArray()]));
    source.relationships = (rig.influenceGraph?.relationships || []).map(edge => ({
      boneA: edge.boneA,
      boneB: edge.boneB,
      sharedVertexCount: edge.sharedVertexCount,
      minOverlap: edge.minOverlap,
      productOverlap: edge.productOverlap,
      containment: edge.containment,
      jaccard: edge.jaccard,
      centerDistance: edge.centerDistance,
      normalizedDistance: edge.normalizedDistance,
      treeEdgeScore: edge.treeEdgeScore,
      jointWeightTotal: edge.jointWeightTotal,
      jointCenter: edge.jointCenter ? [...edge.jointCenter] : null,
    }));
  }
  return source;
}

function modelRigSnapshot() {
  if (!modelSkinningRig) return null;
  const components = (modelSkinningRig.components || []).map(component => ({
    componentId: component.componentId,
    rootId: component.rootId,
    nodeIds: [...(component.nodeIds || [])],
    parentById: {...(component.parentById || {})},
    childrenById: Object.fromEntries(Object.entries(
      component.childrenById || {}).map(([id, children]) => [id, [...children]])),
    depthById: {...(component.depthById || {})},
    maxDepth: component.maxDepth,
  }));
  const joints = (modelSkinningRig.joints || []).map(joint => ({
    jointId: joint.jointId,
    jointKey: joint.jointKey,
    signature: joint.signature,
    members: (joint.members || []).map(member => ({...member})),
    restCenter: [...(joint.restCenter || [0, 0, 0])],
    restPivot: [...(joint.restPivot || joint.restCenter || [0, 0, 0])],
    restDirection: joint.restDirection ? [...joint.restDirection] : null,
    restFrame: [...(joint.restFrame || [0, 0, 0, 1])],
    parentId: joint.parentId,
    childrenIds: [...(joint.childrenIds || [])],
    representativeMember: joint.representativeMember
      ? {...joint.representativeMember} : null,
    evidence: {...(joint.evidence || {})},
  }));
  const forestEdges = (modelSkinningRig.edges || []).map(edge => ({...edge,
    sourceEdges: (edge.sourceEdges || []).map(sourceEdge => ({...sourceEdge})),
  }));
  return {
    structureRevision: modelSkinningRig.structureRevision,
    joints,
    forestEdges,
    components,
    sourceBoneToModelJointId: Object.fromEntries(
      modelSkinningRig.sourceBoneToModelJointId || []),
    poseRotationByJointId: Object.fromEntries(
      [...modelSkinningRig.poseRotationByJointId.entries()].map(([jointId, quaternion]) => [
        jointId, quaternion.toArray()])),
    poseJointIds: [...modelSkinningRig.poseRotationByJointId.entries()]
      .filter(([, quaternion]) => !quaternionIsIdentity(quaternion))
      .map(([jointId]) => jointId),
    reconciliation: modelSkinningRig.reconciliation?.reconciliation || null,
  };
}

function rigSnapshot() {
  return {
    loaded: modelRigState.loaded,
    loading: modelRigState.loading,
    error: modelRigState.error,
    visible: modelRigState.visible,
    picking: modelRigState.picking,
    activeSourceKey: modelRigState.activeSourceKey,
    structureRevision: modelRigState.structureRevision,
    selectedJointId: modelRigState.selectedJointId,
    physicsActive: modelRigHasActivePhysics(),
    selectedBoneId: modelRigState.activeSourceKey
      ? modelRigState.selectedBoneBySource.get(modelRigState.activeSourceKey)
        ?? null : null,
    rotationSnapDegrees: modelRigState.rotationSnapDegrees,
    pickedPoint: modelRigState.pickedPoint
      ? {...modelRigState.pickedPoint,
        point: [...modelRigState.pickedPoint.point],
        influences: modelRigState.pickedPoint.influences.map(influence => ({...influence}))}
      : null,
    pickStatus: modelRigState.pickStatus,
    sources: [...sourceSkinningRigs.values()].map(rig => rigSourceSnapshot(rig)),
    metrics: {
      rigAnalysisMs: modelRigState.rigAnalysisMs || 0,
      rigTransformMs: modelRigState.rigTransformMs || 0,
      rigDeformMs: modelRigState.rigDeformMs || 0,
      rigDeformedVertexCount: modelRigState.rigDeformedVertexCount || 0,
      rigReconcileMs: modelRigState.rigReconcileMs || 0,
      rigCandidateCount: modelRigState.rigCandidateCount || 0,
      rigEquivalentClusterCount: modelRigState.rigEquivalentClusterCount || 0,
      rigAttachmentCount: modelRigState.rigAttachmentCount || 0,
      rigAmbiguousCount: modelRigState.rigAmbiguousCount || 0,
    },
    model: modelRigSnapshot(),
  };
}

function notifyModelRigChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mod-viewer-model-rig-changed', {
      detail: rigSnapshot(),
    }));
  }
}

function notifyModelRigPoseChanged(rig, boneId) {
  if (typeof window !== 'undefined') {
    const quaternion = rig?.poseRotationByBoneId?.get(boneId);
    const jointId = modelJointIdForSourceBone(rig?.sourceKey, boneId);
    const modelQuaternion = Number.isInteger(jointId)
      ? modelSkinningRig?.poseRotationByJointId?.get(jointId) : null;
    window.dispatchEvent(new CustomEvent('mod-viewer-model-rig-pose-changed', {
      detail: {
        sourceKey: rig?.sourceKey || null,
        boneId,
        jointId: Number.isInteger(jointId) ? jointId : null,
        quaternion: (modelQuaternion || quaternion)?.toArray()
          || [0, 0, 0, 1],
      },
    }));
  }
}

export function getModelRigState() {
  return rigSnapshot();
}

export function getModelRigDebugState(sourceKey = modelRigState.activeSourceKey) {
  const model = modelRigSnapshot();
  if (!model) return rigSourceSnapshot(sourceSkinningRigs.get(sourceKey), {debug: true});
  return {
    ...model,
    source: rigSourceSnapshot(sourceSkinningRigs.get(sourceKey), {debug: true}),
  };
}

function notifyModelWeightChanged() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mod-viewer-model-weight-changed', {
      detail: modelWeightSnapshot(),
    }));
  }
}

export function getModelWeightState() {
  return modelWeightSnapshot();
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
      && !modelWeightState.pickStatus) return false;
  modelWeightState.pickedPoint = null;
  modelWeightState.pickerViewMode = 'all';
  modelWeightState.pickStatus = '';
  modelWeightState.picking = false;
  if (notify) notifyModelWeightChanged();
  return true;
}

function handlePickedIntersection(intersection) {
  if (!intersection) {
    modelWeightState.pickStatus = 'No model surface was picked.';
    notifyModelWeightChanged();
    return null;
  }
  const mesh = intersection.object;
  const state = states.get(mesh);
  if (!state?.loaded || !state.skinningSourceKey) {
    modelWeightState.pickStatus = 'No skin weights are available for this part.';
    notifyModelWeightChanged();
    return null;
  }
  const radiusWorld = pickRadiusWorld();
  const sampled = sampleSkinningAtIntersection(
    intersection, mesh, state, {radius: radiusWorld});
  if (!sampled) {
    modelWeightState.pickStatus = 'No skin weights are available for this part.';
    notifyModelWeightChanged();
    return null;
  }
  const source = modelWeightState.sourceDescriptors.get(
    state.skinningSourceKey);
  modelWeightState.pickedPoint = {
    point: sampled.point,
    sourceKey: state.skinningSourceKey,
    sourceFile: source?.sourceFile || state.skinningSourceFile,
    boneIdOffset: source?.boneIdOffset ?? state.skinningBoneOffset,
    meshKey: mesh.userData?.semanticKey || null,
    radiusWorld,
    influences: sampled.influences,
  };
  modelWeightState.pickerViewMode = 'picked';
  modelWeightState.pickStatus = '';
  notifyModelWeightChanged();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mod-viewer-weight-point-picked', {
      detail: modelWeightSnapshot(),
    }));
  }
  return modelWeightState.pickedPoint;
}

function handleRigPickedIntersection(intersection) {
  if (!intersection) {
    modelRigState.pickStatus = 'No model surface was picked.';
    notifyModelRigChanged();
    return null;
  }
  const mesh = intersection.object;
  const state = states.get(mesh);
  if (!state?.loaded || !state.skinningSourceKey) {
    modelRigState.pickStatus = 'No skin weights are available for this part.';
    notifyModelRigChanged();
    return null;
  }
  const sampled = sampleSkinningAtIntersection(
    intersection, mesh, state, {radius: pickRadiusWorld()});
  if (!sampled) {
    modelRigState.pickStatus = 'No skin weights are available for this part.';
    notifyModelRigChanged();
    return null;
  }
  const source = modelWeightState.sourceDescriptors.get(state.skinningSourceKey);
  const pickedPoint = {
    ...sampled,
    sourceFile: source?.sourceFile || sampled.sourceFile,
    boneIdOffset: source?.boneIdOffset ?? sampled.boneIdOffset,
    meshKey: mesh.userData?.semanticKey || null,
  };
  modelRigState.pickedPoint = pickedPoint;
  modelRigState.activeSourceKey = sampled.sourceKey;
  const pickedBoneId = sampled.influences[0]?.boneId ?? null;
  modelRigState.selectedBoneBySource.set(sampled.sourceKey, pickedBoneId);
  modelRigState.selectedJointId = Number.isInteger(Number(pickedBoneId))
    ? modelJointIdForSourceBone(sampled.sourceKey, pickedBoneId) ?? null : null;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  return pickedPoint;
}

export function beginWeightPicking() {
  if (!modelWeightState.loaded) {
    modelWeightState.pickStatus = 'Load model weights before picking.';
    notifyModelWeightChanged();
    return false;
  }
  return weightPickController.begin();
}

export function cancelWeightPicking() {
  return weightPickController.cancel();
}

export function beginRigPicking() {
  if (!modelRigState.loaded) {
    modelRigState.pickStatus = 'Load the inferred rig before picking.';
    notifyModelRigChanged();
    return false;
  }
  weightPickController.cancel();
  return rigPickController.begin();
}

export function cancelRigPicking() {
  return rigPickController.cancel();
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

/** Change only the Weight panel's inspection scope; authored selections stay source-scoped. */
export function setWeightViewMode(mode) {
  if (mode !== 'authored' && mode !== 'model') {
    return modelWeightState.weightViewMode;
  }
  if (modelWeightState.weightViewMode === mode) return mode;
  modelWeightState.weightViewMode = mode;
  if (mode === 'authored') modelWeightState.weightViewJointId = null;
  updateModelWeightHeatmap();
  notifyModelWeightChanged();
  requestRender();
  return mode;
}

/** Select a reconciled model joint for visualization without changing authored weights. */
export function setModelWeightViewJoint(jointId) {
  const value = Number(jointId);
  if (!Number.isInteger(value) || !modelSkinningRig?.joints?.[value]) {
    return modelWeightState.weightViewJointId;
  }
  modelWeightState.weightViewJointId = value;
  updateModelWeightHeatmap();
  notifyModelWeightChanged();
  requestRender();
  return value;
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

export function aggregateModelBoneStats(nodeLists) {
  const totals = new Map();
  for (const nodes of nodeLists || []) {
    for (const node of nodes || []) {
      const boneId = Number(node?.boneId);
      const affectedVertexCount = Number(node?.affectedVertexCount);
      const totalWeight = Number(node?.totalWeight);
      if (!Number.isFinite(boneId) || !Number.isFinite(affectedVertexCount)
          || affectedVertexCount < 0 || !Number.isFinite(totalWeight)) {
        continue;
      }
      const entry = totals.get(boneId) || {
        affectedVertexCount: 0,
        totalWeight: 0,
      };
      entry.affectedVertexCount += affectedVertexCount;
      entry.totalWeight += totalWeight;
      totals.set(boneId, entry);
    }
  }
  return Object.fromEntries([...totals.entries()]
    .sort(([left], [right]) => Number(left) - Number(right))
    .map(([boneId, entry]) => [boneId, {
      affectedVertexCount: entry.affectedVertexCount,
      averageInfluence: entry.affectedVertexCount > 0
        ? entry.totalWeight / entry.affectedVertexCount : 0,
    }]));
}

function refreshModelBoneStats() {
  modelBoneStatsBuildCount += 1;
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

export function getModelBoneStatsBuildCount() {
  return modelBoneStatsBuildCount;
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
  selectedWeightMaskBuildCount += 1;
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

export function getSelectedWeightMaskBuildCount() {
  return selectedWeightMaskBuildCount;
}

function setModelWeightLoadError(error) {
  modelWeightState.error = error instanceof Error
    ? error.message : String(error);
  modelWeightState.loaded = false;
  modelWeightState.noWeights = false;
}

function resetModelWeightState() {
  modelWeightGeneration += 1;
  rigPickController.cancel();
  sourcePhysicsRigs.clear();
  sourceSkinningRigs.clear();
  modelSkinningRig = null;
  modelWeightState.loaded = false;
  modelWeightState.loading = false;
  modelWeightState.promise = null;
  modelWeightState.error = null;
  modelWeightState.noWeights = false;
  modelWeightState.sources = [];
  modelWeightState.selectedBonesBySource = new Map();
  modelWeightState.savedBonesBySource = new Map();
  modelWeightState.sourceDescriptors = new Map();
  modelWeightState.savedSelectionApplied = false;
  modelWeightState.savingSelection = false;
  modelWeightState.selectionSaveError = null;
  selectionSavePromise = null;
  modelWeightState.heatmapEnabled = false;
  modelWeightState.loadedMeshCount = 0;
  modelWeightState.failedMeshCount = 0;
  modelWeightState.pickedPoint = null;
  modelWeightState.pickerViewMode = 'all';
  modelWeightState.weightViewMode = 'authored';
  modelWeightState.weightViewJointId = null;
  modelWeightState.pickStatus = '';
  modelRigState.loaded = false;
  modelRigState.loading = false;
  modelRigState.promise = null;
  modelRigState.error = null;
  modelRigState.visible = false;
  modelRigState.picking = false;
  modelRigState.activeSourceKey = null;
  modelRigState.selectedBoneBySource = new Map();
  modelRigState.selectedJointId = null;
  modelRigState.structureRevision = 0;
  modelRigState.pickedPoint = null;
  modelRigState.pickStatus = '';
  modelRigState.rigAnalysisMs = 0;
  modelRigState.rigTransformMs = 0;
  modelRigState.rigDeformMs = 0;
  modelRigState.rigDeformedVertexCount = 0;
  modelRigState.rigReconcileMs = 0;
  modelRigState.rigCandidateCount = 0;
  modelRigState.rigEquivalentClusterCount = 0;
  modelRigState.rigAttachmentCount = 0;
  modelRigState.rigAmbiguousCount = 0;
  modelRigState.rotationSnapDegrees = 0;
  notifyModelWeightChanged();
  notifyModelRigChanged();
}

/** Return the game material owned by a loaded skinning experiment. */
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

function clearMotionDiagnostics(state) {
  state.lastRootAngularDeltaVector = [0, 0, 0];
  state.lastRootAngularDeltaMagnitude = 0;
  state.motionEventCount = 0;
  state.lastRootTranslationDeltaWorld = [0, 0, 0];
  state.lastRootTranslationDeltaLocal = [0, 0, 0];
  state.lastTranslationLagRotationVector = [0, 0, 0];
  state.lastTranslationLagRotationMagnitude = 0;
  state.translationEventCount = 0;
  state.lastRootLinearVelocityWorld = [0, 0, 0];
  state.lastRootLinearVelocityLocal = [0, 0, 0];
  state.lastRootLinearVelocityDelta = [0, 0, 0];
  state.physicsVirtualLinearVelocityLocal = [0, 0, 0];
}

export function gravityDirectionLocal(mesh, orientation = null) {
  const quaternion = quaternionFromArray(orientation) || mesh?.quaternion;
  if (!quaternion?.clone || quaternion.lengthSq() === 0) {
    return [...GRAVITY_WORLD_DIRECTION];
  }
  return new THREE.Vector3(...GRAVITY_WORLD_DIRECTION)
    .applyQuaternion(quaternion.clone().normalize().invert())
    .normalize().toArray();
}

function refreshConstraintState(state, settings = modelPhysicsSession.getSettings()) {
  if (!settings.constraintsEnabled || !state.physicsForest) {
    state.physicsJointLimits = null;
    state.physicsConstraintDiagnostics = null;
    return;
  }
  const result = buildPhysicsJointLimits(
    state.physicsForest,
    THREE.MathUtils.degToRad(settings.maxBendDegrees));
  state.physicsJointLimits = result.limitByBoneId;
  state.physicsConstraintDiagnostics = result.diagnostics;
}

export function getPhysicsConstraintDiagnostics(meshOrState) {
  const state = states.get(meshOrState) || meshOrState;
  const settings = modelPhysicsSession.getSettings();
  const enabled = !!settings.constraintsEnabled
    && state.physicsJointLimits instanceof Map;
  const dynamic = buildPhysicsConstraintDiagnostics(
    state?.physicsState, enabled ? state.physicsJointLimits : null,
    enabled ? state.physicsConstraintDiagnostics : null);
  return {
    enabled,
    maxComponentBend: Number(settings.maxBendDegrees) || 0,
    limitedJointCount: dynamic.limitedJointCount,
    atLimitCount: dynamic.atLimitCount,
    maxUsage: dynamic.maxUsage,
    components: dynamic.components.map(component => ({
      componentId: component.componentId,
      rootId: component.rootId,
      maxDepth: component.maxDepth,
      jointCount: component.jointCount,
      localLimitDegrees: THREE.MathUtils.radToDeg(
        component.localLimitRadians),
    })),
  };
}

function refreshGravityState(
    mesh, state, settings = modelPhysicsSession.getSettings(), orientation = null) {
  if (!settings.gravityEnabled) {
    state.physicsGravityAccelerations = null;
    state.physicsGravityDiagnostics = null;
    state.physicsGravityLocal = [...GRAVITY_WORLD_DIRECTION];
    return;
  }
  const localDirection = gravityDirectionLocal(mesh, orientation);
  const referenceRadius = Number(
    state.influenceGraph?.boundingSphereRadius);
  const gravity = buildGravityAngularAccelerations(
    state.physicsForest,
    state.physicsCenterByBoneId || state.centerByBoneId, localDirection, {
      referenceRadius,
      gravityScale: settings.gravityScale,
    });
  state.physicsGravityLocal = localDirection;
  state.physicsGravityAccelerations = gravity.accelerationByBoneId;
  state.physicsGravityDiagnostics = {
    ...gravity.diagnostics,
    enabled: true,
    scale: settings.gravityScale,
    worldDirection: [...GRAVITY_WORLD_DIRECTION],
    localDirection: [...localDirection],
  };
}

function quaternionFromArray(value) {
  if (!Array.isArray(value) || value.length < 4) return null;
  const quaternion = new THREE.Quaternion(...value.slice(0, 4).map(Number));
  return quaternion.lengthSq() > 1e-12 ? quaternion.normalize() : null;
}

function localVector(value, orientation) {
  const quaternion = quaternionFromArray(orientation);
  if (!quaternion) return [...value];
  return new THREE.Vector3(...value)
    .applyQuaternion(quaternion.invert()).toArray();
}

function physicsReferenceRadius(mesh, state) {
  const graphRadius = Number(state.influenceGraph?.boundingSphereRadius);
  if (Number.isFinite(graphRadius) && graphRadius > 0) return graphRadius;
  if (!mesh.geometry?.boundingSphere) mesh.geometry?.computeBoundingSphere?.();
  const geometryRadius = Number(mesh.geometry?.boundingSphere?.radius);
  return Number.isFinite(geometryRadius) && geometryRadius > 0
    ? geometryRadius : 1;
}

function refreshPhysicsEquilibrium(state, settings) {
  state.physicsTargetByBoneId = buildPhysicsTargetRotations(
    state.physicsForest, [0, 0, 0]);
  state.physicsEquilibriumByBoneId = buildPhysicsEquilibriumRotations(
    state.physicsForest, [0, 0, 0], settings.frequencyHz,
    settings.gravityEnabled ? state.physicsGravityAccelerations : null,
    settings.constraintsEnabled ? state.physicsJointLimits : null);
}

function refreshParticipantDerivedState(mesh, state, settings) {
  if (!state.physicsForest) return;
  refreshConstraintState(state, settings);
  refreshGravityState(mesh, state, settings);
  refreshPhysicsEquilibrium(state, settings);
}

function beginPhysicsGeometryUpdate(mesh, state) {
  if (state.prePhysicsFrustumCulled === null) {
    state.prePhysicsFrustumCulled = mesh.frustumCulled;
  }
  mesh.frustumCulled = false;
}

function buildSourcePhysicsTransforms(rig) {
  const started = performanceNow();
  rig.transforms = buildForestTransformsFromLocalRotations(
    rig.physicsForest,
    rig.physicsCenterByBoneId || rig.centerByBoneId, {
      getRotation: boneId => rig.physicsState?.joints.get(boneId)
        ?.rotationVector,
      rotationOutput: rig.rotations,
      transformCache: rig.physicsTransformCache,
    });
  addWeightPhysicsPerformance('sourceTransformBuildCount');
  addWeightPhysicsPerformance('sourceTransformMs', performanceNow() - started);
  rig.transformsDirty = false;
  return rig.transforms;
}

function ensureSourcePhysicsTransforms(rig) {
  if (rig.transformsDirty) buildSourcePhysicsTransforms(rig);
  return rig.transforms;
}

function forEachRigMesh(rig, callback) {
  rig.meshes.forEach(mesh => {
    const state = states.get(mesh);
    if (state?.loaded) callback(mesh, state);
  });
}

function syncRigAliases(rig) {
  forEachRigMesh(rig, (mesh, state) => {
    syncMeshPhysicsAlias(mesh, state, rig, !!rig.physicsState);
    state.lastPhysicsStepMetrics = rig.lastPhysicsStepMetrics || null;
  });
}

function syncRigDiagnostics(rig) {
  forEachRigMesh(rig, (mesh, state) => {
    for (const key of PHYSICS_DIAGNOSTIC_VECTOR_KEYS) {
      if (rig[key]) state[key] = [...rig[key]];
    }
    for (const key of PHYSICS_DIAGNOSTIC_SCALAR_KEYS) {
      if (rig[key] !== undefined) state[key] = rig[key];
    }
    state.physicsGravityLocal = [
      ...(rig.physicsGravityLocal || GRAVITY_WORLD_DIRECTION)];
  });
}

function applySourceDeformation(rig, {visibleOnly = true, meshes = null} = {}) {
  if (!rig.physicsState || !rig.physicsForest) return false;
  const transforms = ensureSourcePhysicsTransforms(rig);
  let changed = false;
  const target = meshes ? new Set(meshes) : null;
  forEachRigMesh(rig, (mesh, state) => {
    if (target && !target.has(mesh)) return;
    if (visibleOnly && !mesh.visible) return;
    if (!state.physicsActiveVertices?.length) return;
    beginPhysicsGeometryUpdate(mesh, state);
    syncMeshPhysicsAlias(mesh, state, rig, true);
    changed = applyDeformation(mesh, state, {
      request: false, invalidateShadow: false, skipHidden: false,
      physicsTransforms: transforms, physicsRotations: rig.rotations,
    }) || changed;
    addWeightPhysicsPerformance('participatingPhysicsMeshCount');
  });
  return changed;
}

function createSourcePhysicsParticipant(rig) {
  return {
    key: rig.sourceKey,
    getMeshCount: () => rig.meshes.size,
    onSessionAttached(settings) {
      // Manual pose and physics are intentionally mutually exclusive. Return
      // the whole model to its authored baseline before physics takes over.
      clearModelManualPose({request: false});
      rig.physicsState = initializePhysicsState(rig.physicsForest);
      rig.physicsSettled = false;
      refreshParticipantDerivedState(
        [...rig.meshes][0], rig, settings);
      rig.transformsDirty = true;
      syncRigAliases(rig);
      syncRigDiagnostics(rig);
      applySourceDeformation(rig, {visibleOnly: false});
    },
    onSessionDetached() {
      forEachRigMesh(rig, (mesh, state) => {
        state.physicsEnabled = false;
        state.deformationMode = null;
        state.physicsState = null;
        state.physicsTransforms = null;
        state.physicsRotations = new Map();
        state.poseTransforms = null;
        state.poseRotations = new Map();
        state.poseActiveVertices = null;
        state.physicsJointLimits = null;
        state.physicsConstraintDiagnostics = null;
        state.physicsGravityAccelerations = null;
        state.physicsGravityDiagnostics = null;
        state.physicsTargetByBoneId = null;
        state.physicsEquilibriumByBoneId = null;
        state.physicsSettled = true;
        state.physicsParticipantStatus = 'not-selected';
        state.physicsParticipantError = null;
        state.physicsGravityLocal = [...GRAVITY_WORLD_DIRECTION];
        state.lastPhysicsStepMetrics = null;
        clearMotionDiagnostics(state);
        applyDeformation(mesh, state, {
          request: false, invalidateShadow: false, skipHidden: false,
        });
        finalizePhysicsGeometry(mesh, state);
      });
      rig.physicsState = null;
      rig.transforms.clear();
      rig.rotations.clear();
      rig.transformsDirty = true;
      rig.physicsSettled = true;
      clearMotionDiagnostics(rig);
      rig.lastPhysicsStepMetrics = null;
      invalidateCharacterShadowGeometry({request: false});
      requestRender();
    },
    onSettingsChanged(settings) {
      refreshParticipantDerivedState(
        [...rig.meshes][0], rig, settings);
      if (settings.constraintsEnabled) {
        applyPhysicsJointLimits(rig.physicsState, rig.physicsJointLimits);
      }
      rig.transformsDirty = true;
      rig.physicsSettled = false;
      syncRigAliases(rig);
      syncRigDiagnostics(rig);
    },
    onModelMotion(motion) {
      if (!rig.physicsState || !rig.physicsForest) return false;
      const representative = [...rig.meshes][0];
      const rotationMagnitude = Math.hypot(...motion.rotationVector);
      rig.lastRootAngularDeltaVector = [...motion.rotationVector];
      rig.lastRootAngularDeltaMagnitude = rotationMagnitude;
      rig.motionEventCount = (rig.motionEventCount || 0) + 1;
      let physicsChanged = false;
      let immediateDeformation = false;
      const settings = motion.settings;
      if (rotationMagnitude >= 1e-10) {
        refreshGravityState(
          representative, rig, settings, motion.modelOrientation);
        refreshPhysicsEquilibrium(rig, settings);
      }
      if (rotationMagnitude >= 1e-10 && settings.angularResponse > 0) {
        applyReferenceFrameAngularDelta(
          rig.physicsState, rig.physicsForest,
          motion.rotationVector, settings.angularResponse,
          settings.constraintsEnabled ? rig.physicsJointLimits : null);
        physicsChanged = true;
        immediateDeformation = true;
      }
      if (motion.deltaLinearVelocityWorld) {
        const deltaVelocityLocal = localVector(
          motion.deltaLinearVelocityWorld, motion.previousModelOrientation);
        rig.lastRootLinearVelocityWorld = [...motion.linearVelocityWorld];
        rig.lastRootLinearVelocityLocal = [...localVector(
          motion.linearVelocityWorld, motion.previousModelOrientation)];
        rig.lastRootLinearVelocityDelta = deltaVelocityLocal;
        const diagnostics = {};
        applyReferenceFrameLinearVelocityDelta(
          rig.physicsState, rig.physicsForest,
          rig.physicsCenterByBoneId || rig.centerByBoneId,
          deltaVelocityLocal, settings.velocityResponse, diagnostics,
          settings.constraintsEnabled ? rig.physicsJointLimits : null);
        physicsChanged = diagnostics.maxDeltaAngularVelocityMagnitude >= 1e-10
          || physicsChanged;
      } else if (Math.hypot(...motion.translationDeltaWorld) >= 1e-10) {
        const translationLocal = localVector(
          motion.translationDeltaWorld, motion.previousModelOrientation);
        rig.lastRootTranslationDeltaWorld = [...motion.translationDeltaWorld];
        rig.lastRootTranslationDeltaLocal = [...translationLocal];
        rig.translationEventCount = (rig.translationEventCount || 0) + 1;
        const diagnostics = {};
        applyReferenceFrameTranslationDelta(
          rig.physicsState, rig.physicsForest,
          rig.physicsCenterByBoneId || rig.centerByBoneId,
          translationLocal, settings.translationResponse, diagnostics,
          settings.constraintsEnabled ? rig.physicsJointLimits : null);
        rig.lastTranslationLagRotationVector = [
          ...(diagnostics.maxLagRotationVector || [0, 0, 0])];
        rig.lastTranslationLagRotationMagnitude = Number(
          diagnostics.maxLagRotationMagnitude) || 0;
        if (rig.lastTranslationLagRotationMagnitude >= 1e-10
            && settings.translationResponse > 0) {
          physicsChanged = true;
          immediateDeformation = true;
        }
      }
      syncRigDiagnostics(rig);
      if (!physicsChanged) return false;
      rig.physicsSettled = false;
      rig.transformsDirty = true;
      syncRigAliases(rig);
      if (immediateDeformation) applySourceDeformation(rig);
      return true;
    },
    onVirtualMotion(motion) {
      const representative = [...rig.meshes][0];
      if (!rig.physicsState || !rig.physicsForest || !representative
          || !motion.modelOrientation) return false;
      const currentVelocityLocal = localVector(
        motion.velocityWorld, motion.modelOrientation)
        .map(value => value * physicsReferenceRadius(representative, rig));
      const deltaVelocityLocal = localVector(
        motion.deltaVelocityWorld, motion.modelOrientation)
        .map(value => value * physicsReferenceRadius(representative, rig));
      rig.physicsVirtualLinearVelocityLocal = motion.active === false
        ? [0, 0, 0] : [...currentVelocityLocal];
      const diagnostics = {};
      applyReferenceFrameLinearVelocityDelta(
        rig.physicsState, rig.physicsForest,
        rig.physicsCenterByBoneId || rig.centerByBoneId,
        deltaVelocityLocal, motion.settings.velocityResponse, diagnostics,
        motion.settings.constraintsEnabled ? rig.physicsJointLimits : null);
      if (motion.active === false) rig.physicsSettled = false;
      const physicsChanged = diagnostics.maxDeltaAngularVelocityMagnitude >= 1e-10
        || motion.active === false;
      if (physicsChanged) rig.transformsDirty = true;
      syncRigDiagnostics(rig);
      syncRigAliases(rig);
      return physicsChanged;
    },
    step(dt, settings) {
      if (!rig.physicsState || !rig.physicsForest) return;
      rig.lastPhysicsStepMetrics = stepSpringPhysics(
        rig.physicsState, rig.physicsForest, dt, {
          frequencyHz: settings.frequencyHz,
          dampingRatio: settings.dampingRatio,
          targetRotationByBoneId: rig.physicsTargetByBoneId,
          constrainedTargetRotationByBoneId: rig.physicsTargetByBoneId,
          equilibriumRotationByBoneId: rig.physicsEquilibriumByBoneId,
          externalAngularAccelerationByBoneId: settings.gravityEnabled
            ? rig.physicsGravityAccelerations : null,
          jointLimitByBoneId: settings.constraintsEnabled
            ? rig.physicsJointLimits : null,
          maxDt: MODEL_PHYSICS_STEP,
        });
      rig.transformsDirty = true;
      addWeightPhysicsPerformance('sourcePhysicsStepCount');
    },
    updateSettled(settings) {
      if (!rig.physicsState || !rig.physicsForest) return;
      rig.physicsSettled = isPhysicsSettled(
        rig.physicsState, rig.physicsForest, [0, 0, 0], {
          frequencyHz: settings.frequencyHz,
          targetRotationByBoneId: rig.physicsTargetByBoneId,
          constrainedTargetRotationByBoneId: rig.physicsTargetByBoneId,
          equilibriumRotationByBoneId: rig.physicsEquilibriumByBoneId,
          externalAngularAccelerationByBoneId: settings.gravityEnabled
            ? rig.physicsGravityAccelerations : null,
          jointLimitByBoneId: settings.constraintsEnabled
            ? rig.physicsJointLimits : null,
        });
      syncRigAliases(rig);
    },
    onSettled() {
      forEachRigMesh(rig, (mesh, state) => finalizePhysicsGeometry(mesh, state));
      invalidateCharacterShadowGeometry({request: false});
    },
    isSettled: () => rig.physicsSettled,
    isVisible: () => [...rig.meshes].some(mesh => {
      const state = states.get(mesh);
      return mesh.visible && !!state?.physicsActiveVertices?.length;
    }),
    onMeshStateChanged(changedMeshes) {
      const affected = changedMeshes.filter(mesh => rig.meshes.has(mesh)
        && mesh.visible
        && states.get(mesh)?.physicsActiveVertices?.length);
      if (!affected.length) return false;
      return applySourceDeformation(rig, {meshes: affected, visibleOnly: false});
    },
    deform() {
      return applySourceDeformation(rig);
    },
    reset(settings) {
      resetPhysicsState(rig.physicsState);
      refreshParticipantDerivedState(
        [...rig.meshes][0], rig, settings);
      rig.physicsSettled = !settings.gravityEnabled;
      clearMotionDiagnostics(rig);
      rig.lastPhysicsStepMetrics = null;
      rig.transformsDirty = true;
      forEachRigMesh(rig, (mesh, state) => clearMotionDiagnostics(state));
      syncRigAliases(rig);
      syncRigDiagnostics(rig);
      applySourceDeformation(rig, {visibleOnly: false});
      if (rig.physicsSettled) {
        forEachRigMesh(rig, (mesh, state) => finalizePhysicsGeometry(mesh, state));
        invalidateCharacterShadowGeometry({request: false});
      }
    },
  };
}

function averageSelectedCenter(centerByBoneId, ids) {
  const centers = ids.map(id => centerByBoneId?.get(id))
    .filter(center => Array.isArray(center) && center.length >= 3);
  if (!centers.length) return [0, 0, 0];
  return centers.reduce((sum, center) => [
    sum[0] + Number(center[0] || 0),
    sum[1] + Number(center[1] || 0),
    sum[2] + Number(center[2] || 0),
  ], [0, 0, 0]).map(value => value / centers.length);
}

function attachmentRelationshipSort(a, b) {
  return (Number(b.minOverlap) || 0) - (Number(a.minOverlap) || 0)
    || (Number(b.sharedVertexCount) || 0)
      - (Number(a.sharedVertexCount) || 0)
    || (Number(b.containment) || 0) - (Number(a.containment) || 0)
    || (Number(b.jaccard) || 0) - (Number(a.jaccard) || 0)
    || (Number(a.normalizedDistance ?? Infinity)
      - Number(b.normalizedDistance ?? Infinity))
    || Number(a.boneA) - Number(b.boneA)
    || Number(a.boneB) - Number(b.boneB);
}

export function selectAttachmentRelationship(relationships) {
  return [...relationships || []].sort(attachmentRelationshipSort)[0] || null;
}

function physicsTreeSideForEdge(edges, startId, skippedEdge) {
  const adjacency = new Map();
  for (const edge of edges || []) {
    if (edge === skippedEdge) continue;
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!Number.isFinite(boneA) || !Number.isFinite(boneB)) continue;
    if (!adjacency.has(boneA)) adjacency.set(boneA, []);
    if (!adjacency.has(boneB)) adjacency.set(boneB, []);
    adjacency.get(boneA).push(boneB);
    adjacency.get(boneB).push(boneA);
  }
  const side = new Set([startId]);
  const pending = [startId];
  while (pending.length) {
    const boneId = pending.pop();
    for (const neighbor of adjacency.get(boneId) || []) {
      if (side.has(neighbor)) continue;
      side.add(neighbor);
      pending.push(neighbor);
    }
  }
  return side;
}

function physicsBestStaticAttachment(side, relationships, selected) {
  return selectAttachmentRelationship((relationships || []).filter(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    const leftInside = side.has(boneA);
    const rightInside = side.has(boneB);
    if (leftInside === rightInside) return false;
    const outside = leftInside ? boneB : boneA;
    return !selected.has(outside);
  }));
}

/** Cut only tree bridges whose two sides have stronger static attachments. */
export function pruneSelectedRelationshipEdges(
    treeEdges, relationships = [], selectedBoneIds = []) {
  const edges = [...treeEdges || []];
  const selected = new Set(normalizeSelectedBoneIds(selectedBoneIds));
  if (!selected.size) {
    edges.forEach(edge => {
      selected.add(Number(edge.boneA));
      selected.add(Number(edge.boneB));
    });
  }
  return edges.filter(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    const left = physicsTreeSideForEdge(edges, boneA, edge);
    const right = physicsTreeSideForEdge(edges, boneB, edge);
    const leftAttachment = physicsBestStaticAttachment(
      left, relationships, selected);
    const rightAttachment = physicsBestStaticAttachment(
      right, relationships, selected);
    const bridgeOverlap = Number(edge.minOverlap) || 0;
    return !(leftAttachment && rightAttachment
      && Number(leftAttachment.minOverlap) > bridgeOverlap
      && Number(rightAttachment.minOverlap) > bridgeOverlap);
  });
}

function aggregateSourceInfluenceGraph(members) {
  return aggregateInfluenceGraphs(members.map(mesh => {
    const state = states.get(mesh);
    return state?.loaded ? ensureInfluenceGraph(mesh, state) : null;
  }).filter(Boolean));
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
    graph: influenceGraph,
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
    poseActiveVerticesByMesh: new Map(),
    poseAffectedBoneIds: new Set(),
    poseActiveBoneKey: '',
    poseRootOverrides: new Map(),
    physicsRig: null,
  };
  rebuildSourceRigRestFrames(rig);
  return rig;
}

function sameMeshSet(left, right) {
  return left?.size === right?.length
    && right.every(mesh => left.has(mesh));
}

function resetSourceSkinningPose(rig, {preserveActive = false} = {}) {
  rig.poseRotationByBoneId.clear();
  rig.poseTransforms.clear();
  rig.poseRotations.clear();
  rig.poseTransformCache.clear();
  rig.poseFrameCache.clear();
  rig.poseAffectedBoneIds = new Set();
  rig.poseActiveBoneKey = '';
  if (!preserveActive) rig.poseActiveVerticesByMesh.clear();
}

function refreshSourceSkinningRig(rig, members, {resetPose = true} = {}) {
  const refreshed = createSourceSkinningRig(rig.sourceKey, members);
  rig.meshes = refreshed.meshes;
  rig.graph = refreshed.graph;
  rig.influenceGraph = refreshed.influenceGraph;
  rig.boneIds = refreshed.boneIds;
  rig.centerByBoneId = refreshed.centerByBoneId;
  rig.inferredForest = refreshed.inferredForest;
  rig.jointPivotByBoneId = refreshed.jointPivotByBoneId;
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
    let pivot = finiteVectorArray(edge.jointCenter);
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

function rebuildModelRestFrames(rig, forest) {
  const edgePivots = rig.jointPivotByEdgeKey
    || buildModelJointPivotByEdgeKey(rig);
  const pivots = new Map();
  (forest?.components || []).forEach(component => {
    Object.entries(component.parentById || {}).forEach(([childValue, parentValue]) => {
      const childId = Number(childValue);
      const parentId = Number(parentValue);
      if (!Number.isInteger(childId) || !Number.isInteger(parentId)) return;
      const pivot = edgePivots.get(modelJointPairKey(childId, parentId));
      if (pivot) pivots.set(childId, [...pivot]);
    });
  });
  (rig.joints || []).forEach(joint => {
    const jointId = Number(joint.jointId);
    if (!pivots.has(jointId)) {
      pivots.set(jointId, finiteVectorArray(joint.restPivot)
        || finiteVectorArray(joint.restCenter) || [0, 0, 0]);
    }
  });
  rig.jointPivotByJointId = pivots;
  const frames = buildInferredRigRestFrames(
    forest, rig.centerByJointId, rig.jointPivotByJointId);
  rig.restFrameByJointId = frames.frameByBoneId;
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

function buildModelSkinningRig(sourceRigs = [...sourceSkinningRigs.values()]) {
  const started = performanceNow();
  const previousSelectedJointId = modelRigState.selectedJointId;
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
    sourceBoneToModelJointId: reconciliation.sourceBoneToModelJointMap
      || new Map(),
    sourceTransformAliases: new Map(),
    sourceRotationAliases: new Map(),
    poseRotationByJointId: new Map(),
    poseTransforms: new Map(),
    poseRotations: new Map(),
    poseTransformCache: new Map(),
    poseFrameCache: new Map(),
    poseAffectedJointIds: new Set(),
    poseActiveJointKey: '',
    poseActiveVerticesByMesh: new Map(),
    poseSourceBoneIdsByMesh: new Map(),
    structureRevision: ++nextRigStructureRevision,
  };
  rig.jointPivotByEdgeKey = buildModelJointPivotByEdgeKey(rig);
  sourceRigs.forEach(sourceRig => {
    rig.sourceTransformAliases.set(sourceRig.sourceKey, new Map());
    rig.sourceRotationAliases.set(sourceRig.sourceKey, new Map());
    sourceRig.poseRotationByBoneId.clear();
    sourceRig.poseTransforms = new Map();
    sourceRig.poseRotations = new Map();
    sourceRig.poseTransformCache.clear();
    sourceRig.poseFrameCache.clear();
    sourceRig.poseAffectedBoneIds = new Set();
    sourceRig.poseActiveBoneKey = '';
    sourceRig.poseActiveVerticesByMesh.clear();
  });
  modelSkinningRig = rig;
  if (!modelSkinningRig.joints[modelWeightState.weightViewJointId]) {
    modelWeightState.weightViewJointId = null;
  }
  updateModelWeightHeatmap();
  modelRigState.structureRevision = rig.structureRevision;
  modelRigState.selectedJointId = Number.isInteger(previousSelectedJointId)
    && joints[previousSelectedJointId] ? previousSelectedJointId : null;
  modelRigState.rigReconcileMs = performanceNow() - started;
  modelRigState.rigCandidateCount = reconciliation.reconciliation
    ?.candidateCount || 0;
  modelRigState.rigEquivalentClusterCount = reconciliation.reconciliation
    ?.equivalentSourceBoneCount || 0;
  modelRigState.rigAttachmentCount = reconciliation.reconciliation
    ?.attachmentCount || 0;
  modelRigState.rigAmbiguousCount = reconciliation.reconciliation
    ?.ambiguousCount || 0;
  addWeightPhysicsPerformance('rigReconcileMs', modelRigState.rigReconcileMs);
  addWeightPhysicsPerformance('rigCandidateCount', modelRigState.rigCandidateCount);
  addWeightPhysicsPerformance(
    'rigEquivalentClusterCount', modelRigState.rigEquivalentClusterCount);
  addWeightPhysicsPerformance('rigAttachmentCount', modelRigState.rigAttachmentCount);
  addWeightPhysicsPerformance('rigAmbiguousCount', modelRigState.rigAmbiguousCount);
  updateModelPoseFrameCache(rig, rig.poseTransforms);
  return rig;
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
    transforms.clear();
    rotations.clear();
    for (const boneId of sourceRig.boneIds || []) {
      const jointId = rig.sourceBoneToModelJointId.get(
        sourceBoneKey(sourceRig.sourceKey, boneId));
      if (!Number.isInteger(jointId)) continue;
      transforms.set(Number(boneId), rig.poseTransforms.get(jointId)
        || RIG_IDENTITY_MATRIX);
      rotations.set(Number(boneId), rig.poseRotations.get(jointId)
        || new THREE.Quaternion());
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
    physicsTargetByBoneId: null,
    physicsEquilibriumByBoneId: null,
    physicsTransformCache: new Map(),
    transforms: new Map(),
    rotations: new Map(),
    skinRig,
  };
  skinRig.physicsRig = rig;
  refreshSourcePhysicsRig(rig, members);
  addWeightPhysicsPerformance('sourcePhysicsRigCount');
  return rig;
}

function refreshSourcePhysicsRig(rig, members) {
  rig.meshes = new Set(members);
  rig.transformsDirty = true;
  rig.skinRig = ensureSourceSkinningRig(rig.sourceKey, members);
  rig.skinRig.physicsRig = rig;
  rig.influenceGraph = rig.skinRig.influenceGraph;
  rig.centerByBoneId = new Map((rig.influenceGraph.nodes || []).map(node => [
    node.boneId, node.weightedCenter]));
  const selected = modelWeightState.selectedBonesBySource.get(rig.sourceKey)
    || new Set();
  rig.physicsForest = selectedPhysicsForest(
    rig.influenceGraph, rig.centerByBoneId,
    (rig.influenceGraph.nodes || []).map(node => node.boneId), selected);
  const selectedIds = rig.physicsForest?.selectedBoneIds || [];
  rig.selectionKey = selectedIds.join(',');
  rig.physicsCenterByBoneId = rig.physicsForest?.centers || rig.centerByBoneId;
  return rig;
}

function syncMeshPhysicsAlias(mesh, state, rig, enabled) {
  state.physicsEnabled = !!enabled;
  state.deformationMode = enabled ? 'physics' : null;
  state.physicsState = enabled ? rig.physicsState : null;
  state.physicsForest = enabled ? rig.physicsForest : null;
  state.physicsCenterByBoneId = enabled
    ? rig.physicsCenterByBoneId : state.centerByBoneId;
  state.physicsJointLimits = enabled ? rig.physicsJointLimits : null;
  state.physicsConstraintDiagnostics = enabled
    ? rig.physicsConstraintDiagnostics : null;
  state.physicsGravityAccelerations = enabled
    ? rig.physicsGravityAccelerations : null;
  state.physicsGravityDiagnostics = enabled
    ? rig.physicsGravityDiagnostics : null;
  state.physicsTargetByBoneId = enabled ? rig.physicsTargetByBoneId : null;
  state.physicsEquilibriumByBoneId = enabled
    ? rig.physicsEquilibriumByBoneId : null;
  state.physicsTransforms = enabled ? rig.transforms : null;
  state.physicsRotations = enabled ? rig.rotations : new Map();
  state.physicsTransformCache = enabled ? rig.physicsTransformCache : new Map();
  state.physicsSelectionKey = enabled ? rig.selectionKey : '';
  state.physicsSettled = enabled ? rig.physicsSettled : true;
  state.physicsParticipantStatus = enabled ? 'participating' : 'not-selected';
  state.physicsParticipantError = null;
}

function selectedPhysicsForest(graph, centerByBoneId, boneIds, selectedBoneIds) {
  const selected = new Set(
    normalizeSelectedBoneIds(selectedBoneIds)
      .filter(id => boneIds.includes(id)));
  if (!selected.size) return null;
  const selectedNodes = (graph.nodes || []).filter(node =>
    selected.has(Number(node.boneId)));
  const candidateEdges = rigCandidateRelationshipEdges(graph);
  const selectedEdges = candidateEdges.filter(relationship =>
    selected.has(Number(relationship.boneA))
    && selected.has(Number(relationship.boneB)));
  const candidateTree = buildRigMaximumSpanningTree(selectedNodes, selectedEdges);
  const physicsEdges = pruneSelectedRelationshipEdges(
    candidateTree.edges, graph.relationships, selected);
  const selectedTree = buildRigMaximumSpanningTree(selectedNodes, physicsEdges);
  const centers = new Map(centerByBoneId || []);
  const components = [];
  const componentByBoneId = {};

  selectedTree.components.forEach((componentIds, componentIndex) => {
    const componentSet = new Set(componentIds);
    const boundary = selectAttachmentRelationship((graph.relationships || [])
      .filter(relationship => {
        const boneA = Number(relationship.boneA);
        const boneB = Number(relationship.boneB);
        const leftSelected = componentSet.has(boneA);
        const rightSelected = componentSet.has(boneB);
        if (leftSelected === rightSelected) return false;
        const other = leftSelected ? boneB : boneA;
        return !selected.has(other);
      }));
    let rootId;
    let attachment = 'authored';
    let attachmentEdge;
    if (boundary) {
      rootId = componentSet.has(Number(boundary.boneA))
        ? Number(boundary.boneB) : Number(boundary.boneA);
      attachmentEdge = {
        boneA: rootId,
        boneB: componentSet.has(Number(boundary.boneA))
          ? Number(boundary.boneA) : Number(boundary.boneB),
        containment: boundary.containment,
        jaccard: boundary.jaccard,
        minOverlap: boundary.minOverlap,
        sharedVertexCount: boundary.sharedVertexCount,
        treeEdgeScore: boundary.treeEdgeScore,
        attachment: 'authored',
      };
    } else {
      rootId = -1 - componentIndex;
      attachment = 'synthetic';
      const attachmentBone = [...componentIds].sort((left, right) => {
        const leftCenter = centers.get(left) || [0, 0, 0];
        const rightCenter = centers.get(right) || [0, 0, 0];
        return Math.hypot(...leftCenter) - Math.hypot(...rightCenter);
      })[0];
      centers.set(rootId, centers.get(attachmentBone)
        || averageSelectedCenter(centerByBoneId, componentIds));
      attachmentEdge = {
        boneA: rootId,
        boneB: attachmentBone,
        containment: 0,
        jaccard: 0,
        treeEdgeScore: 0,
        attachment: 'synthetic',
      };
    }
    const edges = selectedTree.edges.filter(edge =>
      componentSet.has(Number(edge.boneA))
      && componentSet.has(Number(edge.boneB)));
    edges.push(attachmentEdge);
    const nodeIds = [rootId, ...componentIds];
    const orientation = rigOrientTree(edges, rootId);
    const depths = Object.values(orientation.depthById)
      .filter(depth => depth !== null).map(Number);
    const component = {
      componentId: componentIndex,
      nodeIds,
      dynamicNodeIds: [...componentIds],
      rootId,
      parentById: orientation.parentById,
      childrenById: orientation.childrenById,
      depthById: orientation.depthById,
      edgeCount: edges.length,
      maxDepth: Math.max(0, ...depths),
      primary: componentIndex === 0,
      attachment,
    };
    components.push(component);
    nodeIds.forEach(id => { componentByBoneId[id] = componentIndex; });
  });
  return {
    primaryRootId: components[0]?.rootId ?? null,
    primaryComponentId: components.length ? 0 : null,
    components,
    componentByBoneId,
    selectedBoneIds: [...selected],
    centers,
  };
}

export function isPhysicsScheduled() {
  return modelPhysicsSession.isScheduled();
}

export function registerSkinningMesh(mesh) {
  if (!mesh) return;
  knownMeshes.add(mesh);
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
  knownMeshes.delete(mesh);
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
  if (sourceKey && modelRigState.activeSourceKey === sourceKey) {
    modelRigState.activeSourceKey = null;
  }
  if (modelRigState.loaded) {
    buildAllSourceSkinningRigs();
    buildModelSkinningRig([...sourceSkinningRigs.values()]);
    modelRigState.loaded = true;
  }
  notifyModelRigChanged();
  notifyModelWeightChanged();
}

export function getModelPhysicsState() {
  return modelPhysicsSession.getState();
}

export function destroyModelPhysicsSession() {
  weightPickController.cancel();
  modelPhysicsSession.destroy();
  knownMeshes.clear();
  resetModelWeightState();
}

export function disableModelPhysics() {
  if (!modelPhysicsSession.getState().enabled) return false;
  modelPhysicsSession.disable();
  notifyModelRigChanged();
  return false;
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
export function ensureModelWeightsLoaded() {
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
      setSelectedBones(sourceSelectionEntries(
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

function poseDescendantIds(rig, posedBoneIds) {
  const affected = new Set();
  const pending = [...posedBoneIds].map(Number).filter(Number.isFinite);
  while (pending.length) {
    const boneId = pending.pop();
    if (affected.has(boneId)) continue;
    affected.add(boneId);
    const component = rigComponentForBone(rig, boneId);
    for (const child of component?.childrenById?.[boneId] || []) {
      pending.push(Number(child));
    }
  }
  return affected;
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

function buildSourcePoseTransforms(rig) {
  const started = performanceNow();
  rig.poseTransforms = buildForestTransformsFromLocalRotations(
    rig.inferredForest, rig.centerByBoneId, {
      getQuaternion: boneId => rig.poseRotationByBoneId.get(boneId)
        || new THREE.Quaternion(),
      jointPivotByBoneId: rig.jointPivotByBoneId,
      rotationOutput: rig.poseRotations,
      transformCache: rig.poseTransformCache,
    });
  updateSourcePoseFrameCache(rig, rig.poseTransforms);
  modelRigState.rigTransformMs = performanceNow() - started;
  addWeightPhysicsPerformance('rigTransformMs', modelRigState.rigTransformMs);
  return rig.poseTransforms;
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

function markPoseBoundsDirty(mesh, state) {
  if (state.posePreFrustumCulled === null
      || state.posePreFrustumCulled === undefined) {
    state.posePreFrustumCulled = mesh.frustumCulled;
  }
  mesh.frustumCulled = false;
  state.poseBoundsDirty = true;
}

function finalizeSourcePoseBounds(rig) {
  let finalized = false;
  forEachRigMesh(rig, (mesh, state) => {
    if (!state.poseBoundsDirty) return;
    mesh.geometry.computeBoundingBox();
    mesh.geometry.computeBoundingSphere();
    state.poseBoundsDirty = false;
    if (state.posePreFrustumCulled !== null
        && state.posePreFrustumCulled !== undefined) {
      mesh.frustumCulled = state.posePreFrustumCulled;
      state.posePreFrustumCulled = null;
    }
    finalized = true;
  });
  if (finalized) invalidateCharacterShadowGeometry({request: false});
  return finalized;
}

function buildModelPoseTransforms() {
  if (!modelSkinningRig) return new Map();
  const started = performanceNow();
  modelSkinningRig.poseTransforms = buildForestTransformsFromLocalRotations(
    modelSkinningRig.inferredForest, modelSkinningRig.centerByJointId, {
      getQuaternion: jointId => modelSkinningRig.poseRotationByJointId.get(
        jointId) || new THREE.Quaternion(),
      jointPivotByBoneId: modelSkinningRig.jointPivotByJointId,
      rotationOutput: modelSkinningRig.poseRotations,
      transformCache: modelSkinningRig.poseTransformCache,
    });
  updateModelPoseFrameCache(modelSkinningRig,
    modelSkinningRig.poseTransforms);
  updateModelSourceAliases(modelSkinningRig);
  modelRigState.rigTransformMs = performanceNow() - started;
  addWeightPhysicsPerformance('rigTransformMs', modelRigState.rigTransformMs);
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
  const posedJointIds = [...rig.poseRotationByJointId.entries()]
    .filter(([, quaternion]) => !quaternionIsIdentity(quaternion))
    .map(([jointId]) => Number(jointId));
  const poseJointKey = posedJointIds.sort((left, right) => left - right).join(',');
  const affectedJointIds = rig.poseActiveJointKey === poseJointKey
    ? rig.poseAffectedJointIds : modelPoseDescendantIds(rig, posedJointIds);
  const affectedSetChanged = rig.poseActiveJointKey !== poseJointKey;
  const deformStarted = performanceNow();
  let changed = false;
  let deformedVertexCount = 0;
  for (const sourceRig of rig.sourceRigs || []) {
    syncDerivedSourcePose(sourceRig, rig);
    const transformsByBoneId = rig.sourceTransformAliases.get(
      sourceRig.sourceKey) || new Map();
    const rotationsByBoneId = rig.sourceRotationAliases.get(
      sourceRig.sourceKey) || new Map();
    const affectedBoneIds = sourceBoneIdsForModelJoints(
      sourceRig, affectedJointIds);
    forEachRigMesh(sourceRig, (mesh, state) => {
      const previousVertices = rig.poseActiveVerticesByMesh.get(mesh)
        || state.poseActiveVertices || new Uint32Array();
      const previousBoneKey = rig.poseSourceBoneIdsByMesh.get(mesh) || '';
      const boneKey = sourceBoneKeyForSet(affectedBoneIds);
      const activeVertices = !affectedSetChanged && previousBoneKey === boneKey
        && rig.poseActiveVerticesByMesh.has(mesh)
        ? rig.poseActiveVerticesByMesh.get(mesh)
        : affectedBoneIds.size
          ? poseVerticesForState(state, affectedBoneIds) : new Uint32Array();
      const removedVertices = affectedSetChanged || previousBoneKey !== boneKey
        ? vertexDifference(previousVertices, activeVertices) : new Uint32Array();
      if (removedVertices.length) {
        restorePoseVertices(mesh, state, removedVertices);
        markPoseBoundsDirty(mesh, state);
        changed = true;
      }
      if (activeVertices.length) {
        deformedVertexCount += applyWeightedTransformDeformationInto(
          mesh.geometry.attributes.position.array, state.baselinePositions,
          state.indices, state.weights, state.influenceCount,
          transformsByBoneId, activeVertices);
        const normal = mesh.geometry.attributes.normal;
        if (normal && state.baselineNormals
            && normal.array.length === state.baselineNormals.length) {
          applyWeightedNormalDeformationInto(
            normal.array, state.baselineNormals, state.indices, state.weights,
            state.influenceCount, rotationsByBoneId, activeVertices);
          normal.needsUpdate = true;
        }
        markPoseBoundsDirty(mesh, state);
        changed = true;
      }
      mesh.geometry.attributes.position.needsUpdate = true;
      state.poseActiveVertices = activeVertices;
      state.poseTransforms = transformsByBoneId;
      state.poseRotations = rotationsByBoneId;
      rig.poseActiveVerticesByMesh.set(mesh, activeVertices);
      rig.poseSourceBoneIdsByMesh.set(mesh, boneKey);
    });
  }
  rig.poseAffectedJointIds = affectedJointIds;
  rig.poseActiveJointKey = poseJointKey;
  modelRigState.rigDeformMs = performanceNow() - deformStarted;
  modelRigState.rigDeformedVertexCount = deformedVertexCount;
  addWeightPhysicsPerformance('rigDeformMs', modelRigState.rigDeformMs);
  addWeightPhysicsPerformance('rigDeformedVertexCount', deformedVertexCount);
  if (changed) invalidateCharacterShadowGeometry({request: false});
  if (!dragging) {
    for (const sourceRig of rig.sourceRigs || []) finalizeSourcePoseBounds(sourceRig);
  }
  if (request) requestRender();
  return changed;
}

function resetModelPose({request = true} = {}) {
  if (!modelSkinningRig) return false;
  if (modelRigHasActivePhysics()) return false;
  modelSkinningRig.poseRotationByJointId.clear();
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

function applySourcePose(rig, {request = true, dragging = false} = {}) {
  if (!rig || rig.physicsRig?.physicsState) return false;
  const transforms = buildSourcePoseTransforms(rig);
  const posedBones = [...rig.poseRotationByBoneId.entries()]
    .filter(([, quaternion]) => !quaternionIsIdentity(quaternion))
    .map(([boneId]) => boneId);
  const poseBoneKey = posedBones.map(Number).sort((left, right) => left - right)
    .join(',');
  const affectedBoneIds = rig.poseActiveBoneKey === poseBoneKey
    ? rig.poseAffectedBoneIds : poseDescendantIds(rig, posedBones);
  const affectedSetChanged = rig.poseActiveBoneKey !== poseBoneKey;
  const deformStarted = performanceNow();
  let changed = false;
  let deformedVertexCount = 0;
  forEachRigMesh(rig, (mesh, state) => {
    const previousVertices = rig.poseActiveVerticesByMesh.get(mesh)
      || state.poseActiveVertices || new Uint32Array();
    const hasCachedVertices = rig.poseActiveVerticesByMesh.has(mesh);
    const activeVertices = !affectedSetChanged && hasCachedVertices
      ? rig.poseActiveVerticesByMesh.get(mesh)
      : affectedBoneIds.size
        ? poseVerticesForState(state, affectedBoneIds) : new Uint32Array();
    const removedVertices = affectedSetChanged
      ? vertexDifference(previousVertices, activeVertices) : new Uint32Array();
    if (removedVertices.length) {
      restorePoseVertices(mesh, state, removedVertices);
      markPoseBoundsDirty(mesh, state);
      changed = true;
    }
    if (activeVertices.length) {
      deformedVertexCount += applyWeightedTransformDeformationInto(
        mesh.geometry.attributes.position.array, state.baselinePositions,
        state.indices, state.weights, state.influenceCount, transforms,
        activeVertices);
      const normal = mesh.geometry.attributes.normal;
      if (normal && state.baselineNormals
          && normal.array.length === state.baselineNormals.length) {
        applyWeightedNormalDeformationInto(
          normal.array, state.baselineNormals, state.indices, state.weights,
          state.influenceCount, rig.poseRotations, activeVertices);
        normal.needsUpdate = true;
      }
      markPoseBoundsDirty(mesh, state);
      changed = true;
    }
    mesh.geometry.attributes.position.needsUpdate = true;
    state.poseActiveVertices = activeVertices;
    state.poseTransforms = transforms;
    state.poseRotations = rig.poseRotations;
    rig.poseActiveVerticesByMesh.set(mesh, activeVertices);
  });
  rig.poseAffectedBoneIds = affectedBoneIds;
  rig.poseActiveBoneKey = poseBoneKey;
  modelRigState.rigDeformMs = performanceNow() - deformStarted;
  modelRigState.rigDeformedVertexCount = deformedVertexCount;
  addWeightPhysicsPerformance('rigDeformMs', modelRigState.rigDeformMs);
  addWeightPhysicsPerformance('rigDeformedVertexCount', deformedVertexCount);
  if (changed) invalidateCharacterShadowGeometry({request: false});
  if (!dragging) finalizeSourcePoseBounds(rig);
  if (request) requestRender();
  return changed;
}

function rigSourceFor(sourceKey) {
  return sourceSkinningRigs.get(String(sourceKey)) || null;
}

function rigBonePoseFrame(rig, boneId) {
  const id = Number(boneId);
  if (!rig || !Number.isInteger(id)) return null;
  const componentId = rig.inferredForest?.componentByBoneId?.[id];
  const component = Number.isInteger(Number(componentId))
    ? rig.inferredForest?.components?.[Number(componentId)] : null;
  if (!component) return null;

  const cache = rig.poseFrameCache.get(id);
  const parentId = rigParentForBone(rig, id);
  const parentTransform = parentId === null
    ? RIG_IDENTITY_MATRIX : rig.poseTransforms.get(parentId)
      || RIG_IDENTITY_MATRIX;
  const pivotValues = rig.jointPivotByBoneId.get(id)
    || (parentId !== null ? rig.centerByBoneId.get(parentId) : null)
    || rig.centerByBoneId.get(id) || [0, 0, 0];
  const pivot = cache?.pivot?.clone() || new THREE.Vector3(...pivotValues)
    .applyMatrix4(parentTransform);
  const centerValues = cache?.center?.toArray()
    || new THREE.Vector3(...(rig.centerByBoneId.get(id) || [0, 0, 0]))
      .applyMatrix4(rig.poseTransforms.get(id) || RIG_IDENTITY_MATRIX)
      .toArray();
  const parentRotation = parentId === null
    ? new THREE.Quaternion() : rig.poseRotations.get(parentId)
      ?.clone() || new THREE.Quaternion();
  const boneRotation = rig.poseRotations.get(id)?.clone()
    || new THREE.Quaternion();
  const restRotation = rig.restFrameByBoneId.get(id)?.clone()
    || new THREE.Quaternion();
  const gizmoRotation = boneRotation.clone().multiply(restRotation).normalize();
  return {
    pivot: pivot.toArray(),
    center: centerValues,
    parentRotation: parentRotation.normalize().toArray(),
    boneRotation: boneRotation.normalize().toArray(),
    restRotation: restRotation.normalize().toArray(),
    gizmoRotation: gizmoRotation.toArray(),
  };
}

export function getRigBonePoseFrame(sourceKey, boneId) {
  const id = Number(boneId);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  const frame = Number.isInteger(jointId)
    ? modelJointPoseFrame(jointId) : rigBonePoseFrame(rigSourceFor(sourceKey), id);
  return frame ? {...frame, sourceKey: String(sourceKey), boneId: id,
    jointId: Number.isInteger(jointId) ? jointId : null} : null;
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
  modelRigState.loading = true;
  modelRigState.error = null;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  modelRigState.promise = ensureModelWeightsLoaded()
    .then(() => {
      if (modelWeightState.error) throw new Error(modelWeightState.error);
      const analysisStarted = performanceNow();
      const rigs = buildAllSourceSkinningRigs();
      buildModelSkinningRig(rigs);
      modelRigState.loaded = true;
      modelRigState.activeSourceKey = modelRigState.activeSourceKey
        && sourceSkinningRigs.has(modelRigState.activeSourceKey)
        ? modelRigState.activeSourceKey : rigs[0]?.sourceKey || null;
      modelRigState.rigAnalysisMs = performanceNow() - analysisStarted;
      addWeightPhysicsPerformance('rigAnalysisMs', modelRigState.rigAnalysisMs);
      return rigSnapshot();
    })
    .catch(error => {
      modelRigState.error = error instanceof Error ? error.message : String(error);
      modelRigState.loaded = false;
      return rigSnapshot();
    })
    .finally(() => {
      modelRigState.loading = false;
      modelRigState.promise = null;
      notifyModelRigChanged();
    });
  return modelRigState.promise;
}

export function setRigVisible(enabled) {
  modelRigState.visible = !!enabled;
  notifyModelRigChanged();
  requestRender();
  return modelRigState.visible;
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

export function setActiveRigSource(sourceKey) {
  const rig = rigSourceFor(sourceKey);
  if (!rig) return false;
  modelRigState.activeSourceKey = rig.sourceKey;
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function selectRigBone(sourceKey, boneId) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  if (!rig || !Number.isInteger(id) || !rig.boneIds.includes(id)
      || !Number.isInteger(jointId)) return false;
  modelRigState.activeSourceKey = rig.sourceKey;
  modelRigState.selectedBoneBySource.set(rig.sourceKey, id);
  modelRigState.selectedJointId = jointId;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function selectRigJoint(jointId) {
  const joint = modelJointForId(jointId);
  if (!joint) return false;
  const member = joint.representativeMember || joint.members?.[0];
  modelRigState.selectedJointId = joint.jointId;
  if (member) {
    modelRigState.activeSourceKey = member.sourceKey;
    modelRigState.selectedBoneBySource.set(
      member.sourceKey, Number(member.boneId));
  }
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function setRigComponentRoot(sourceKey, boneId) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const component = rigComponentForBone(rig, id);
  if (!rig || !component || !component.nodeIds.includes(id)) return false;
  if (modelRigHasActivePhysics()) {
    modelRigState.pickStatus = 'Disable Character Physics before posing.';
    notifyModelRigChanged();
    return false;
  }
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  if (!Number.isInteger(jointId) || !modelSkinningRig) return false;
  resetModelPose({request: false});
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
  const modelComponentId = modelSkinningRig.componentByJointId.get(jointId);
  const modelForest = orientModelRigForest(
    modelSkinningRig.joints, modelSkinningRig.edges, {
      [modelComponentId]: jointId,
    });
  modelSkinningRig.components = modelForest.components;
  modelSkinningRig.componentByJointId = modelForest.componentByJointId;
  modelSkinningRig.inferredForest = {
    components: modelForest.components,
    componentByBoneId: modelForest.componentByJointId,
  };
  rebuildModelRestFrames(modelSkinningRig, modelForest);
  modelSkinningRig.poseTransformCache.clear();
  modelSkinningRig.poseFrameCache.clear();
  modelSkinningRig.structureRevision = ++nextRigStructureRevision;
  modelRigState.structureRevision = modelSkinningRig.structureRevision;
  selectRigBone(sourceKey, id);
  buildModelPoseTransforms();
  notifyModelRigChanged();
  return true;
}

export function setRigBoneRotation(sourceKey, boneId, quaternion, options = {}) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const component = rigComponentForBone(rig, id);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  const modelComponentId = modelSkinningRig?.componentByJointId?.get(jointId);
  const modelComponent = Number.isInteger(Number(modelComponentId))
    ? modelSkinningRig?.components?.[Number(modelComponentId)] : null;
  if (!rig || !component || !rig.boneIds.includes(id)
      || !Number.isInteger(jointId) || !modelComponent) return false;
  if (modelRigHasActivePhysics()) {
    modelRigState.pickStatus = 'Disable Character Physics before posing.';
    notifyModelRigChanged();
    return false;
  }
  if (modelComponent.rootId === jointId) {
    modelRigState.pickStatus = 'Component root / anchor cannot be rotated.';
    notifyModelRigChanged();
    return false;
  }
  modelSkinningRig.poseRotationByJointId.set(jointId, cloneRigQuaternion(quaternion));
  modelRigState.activeSourceKey = rig.sourceKey;
  modelRigState.selectedBoneBySource.set(rig.sourceKey, id);
  modelRigState.selectedJointId = jointId;
  const dragging = options?.dragging === true;
  applyModelPose({dragging});
  modelRigState.pickStatus = '';
  if (dragging) notifyModelRigPoseChanged(rig, id);
  else notifyModelRigChanged();
  return true;
}

export function setRigJointRotation(jointId, quaternion, options = {}) {
  if (modelRigHasActivePhysics()) {
    modelRigState.pickStatus = 'Disable Character Physics before posing.';
    notifyModelRigChanged();
    return false;
  }
  const joint = modelJointForId(jointId);
  const member = joint?.representativeMember || joint?.members?.[0];
  return member ? setRigBoneRotation(
    member.sourceKey, member.boneId, quaternion, options) : false;
}

export function finishRigPose(sourceKey, boneId) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  if (!rig || !Number.isInteger(id) || !rig.boneIds.includes(id)
      || !Number.isInteger(jointId)) return false;
  if (modelRigHasActivePhysics()) return false;
  (modelSkinningRig?.sourceRigs || []).forEach(finalizeSourcePoseBounds);
  notifyModelRigChanged();
  requestRender();
  return true;
}

export function setRigPoseControlStatus(message = '') {
  modelRigState.pickStatus = String(message || '');
  notifyModelRigChanged();
  return modelRigState.pickStatus;
}

export function resetRigBone(sourceKey, boneId) {
  const rig = rigSourceFor(sourceKey);
  const id = Number(boneId);
  const jointId = modelJointIdForSourceBone(sourceKey, id);
  if (!rig || !rig.boneIds.includes(id) || !Number.isInteger(jointId)) return false;
  if (modelRigHasActivePhysics()) return false;
  modelSkinningRig?.poseRotationByJointId.delete(jointId);
  applyModelPose();
  notifyModelRigChanged();
  return true;
}

export function resetRigPose(sourceKey = null) {
  const changed = resetModelPose();
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  requestRender();
  return changed;
}

function syncPhysicsParticipants(changedSourceKeys = null) {
  if (!modelPhysicsSession.getState().enabled) return;
  if (!selectedBoneCount(modelWeightState.selectedBonesBySource)) {
    disableModelPhysics();
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
    if (enabled) disableModelPhysics();
    return false;
  }
  if (!enabled) {
    clearModelManualPose({request: false});
    modelPhysicsSession.enable(getModelTransformState());
  }
  syncPhysicsParticipants(changedSourceKeys);
  return true;
}

export function enableModelPhysics() {
  if (modelPhysicsSession.getState().enabled) {
    return Promise.resolve(getModelPhysicsState());
  }
  if (!selectedBoneCount(modelWeightState.selectedBonesBySource)) {
    return Promise.resolve(getModelPhysicsState());
  }
  clearModelManualPose({request: false});
  const generation = modelPhysicsSession.enable(getModelTransformState());
  syncPhysicsParticipants();
  return Promise.resolve({...getModelPhysicsState(), generation});
}

export function resetModelPhysicsMotion() {
  return modelPhysicsSession.reset(getModelTransformState());
}

export function resetModelPhysics() {
  const defaults = DEFAULT_MODEL_PHYSICS_SETTINGS;
  return modelPhysicsSession.reset(getModelTransformState(), {
    settingsPatch: {
      frequencyHz: defaults.frequencyHz,
      dampingRatio: defaults.dampingRatio,
      angularResponse: defaults.angularResponse,
      translationResponse: defaults.translationResponse,
      velocityResponse: defaults.velocityResponse,
      gravityScale: defaults.gravityScale,
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

function handleVirtualModelMotion(event) {
  modelPhysicsSession.handleVirtualMotion(event.detail);
}

export function weightForBone(indices, weights, influenceCount,
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

export function buildBoneIds(indices, weights, influenceCount) {
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

function buildInfluenceGraph(mesh, state) {
  if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
  const radius = Number(mesh.geometry.boundingSphere?.radius);
  const nodes = state.influenceNodes || buildRigInfluenceNodes(
    state.baselinePositions, state.indices, state.weights,
    state.influenceCount, state.boneIds);
  state.influenceNodes = nodes;
  const relationships = buildRigInfluenceRelationships(
    state.baselinePositions, state.indices, state.weights, state.influenceCount,
    nodes, Number.isFinite(radius) && radius > 0 ? radius : null);
  return {
    nodes,
    relationships,
    boundingSphereRadius: Number.isFinite(radius) && radius > 0 ? radius : null,
  };
}

function ensureInfluenceGraph(mesh, state) {
  if (!state.influenceGraph) state.influenceGraph = buildInfluenceGraph(mesh, state);
  return state.influenceGraph;
}

/** Re-baseline loaded weights after the authoritative shape geometry changes. */
export function refreshSkinningAfterShapeChange(mesh) {
  const state = states.get(mesh);
  const position = mesh?.geometry?.attributes?.position;
  clearPickedPoint();
  if (!state?.loaded || !position) return false;
  const sourceKey = state.skinningSourceKey;
  const participant = sourceKey
    ? modelPhysicsSession.getParticipant(sourceKey) : null;
  const wasPhysicsEnabled = !!participant || state.physicsEnabled;
  if (participant) modelPhysicsSession.detach(sourceKey);
  if (sourceKey) sourcePhysicsRigs.delete(sourceKey);
  if (sourceKey) sourceSkinningRigs.delete(sourceKey);
  if (modelSkinningRig) resetModelPose({request: false});
  state.poseTransforms = null;
  state.poseRotations = new Map();
  state.poseActiveVertices = null;
  modelRigState.loaded = false;
  modelSkinningRig = null;
  modelRigState.selectedJointId = null;
  modelRigState.structureRevision = 0;
  modelRigState.visible = false;
  modelRigState.activeSourceKey = null;
  modelRigState.selectedBoneBySource = new Map();
  modelRigState.pickedPoint = null;
  modelRigState.pickStatus = '';
  notifyModelRigChanged();
  const normal = mesh.geometry.attributes.normal;
  state.baselinePositions = new Float32Array(position.array);
  state.baselineNormals = normal ? new Float32Array(normal.array) : null;
  state.influenceNodes = buildRigInfluenceNodes(
    state.baselinePositions, state.indices, state.weights,
    state.influenceCount, state.boneIds);
  state.centerByBoneId = new Map(state.influenceNodes.map(node => [
    node.boneId, node.weightedCenter]));
  state.influenceGraph = null;
  state.physicsTransforms = null;
  state.physicsForest = null;
  state.physicsCenterByBoneId = state.centerByBoneId;

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
  physicsTransforms = null,
  physicsRotations = null,
} = {}) {
  if (!state.loaded || !state.baselinePositions) return;
  if (skipHidden && !mesh.visible) return false;
  const physicsActive = state.deformationMode === 'physics'
    && state.physicsEnabled && state.physicsState && state.physicsForest;
  const position = mesh.geometry.attributes.position;
  if (physicsActive) {
    state.physicsTransforms = physicsTransforms || buildForestTransformsFromLocalRotations(
      state.physicsForest,
      state.physicsCenterByBoneId || state.centerByBoneId, {
        getRotation: boneId => state.physicsState.joints.get(boneId)
          ?.rotationVector,
        rotationOutput: state.physicsRotations,
        transformCache: state.physicsTransformCache,
      });
    const deformStarted = performanceNow();
    const deformedVertices = applyWeightedTransformDeformationInto(
      position.array, state.baselinePositions, state.indices, state.weights,
      state.influenceCount, state.physicsTransforms,
      state.physicsActiveVertices);
    addWeightPhysicsPerformance('physicsDeformCount');
    addWeightPhysicsPerformance('physicsDeformedVertexCount', deformedVertices);
    addWeightPhysicsPerformance(
      'physicsDeformMs', performanceNow() - deformStarted);
    const normal = mesh.geometry.attributes.normal;
    if (normal && state.baselineNormals
        && normal.array.length === state.baselineNormals.length) {
      const normalStarted = performanceNow();
      applyWeightedNormalDeformationInto(
        normal.array, state.baselineNormals, state.indices, state.weights,
        state.influenceCount, physicsRotations || state.physicsRotations,
        state.physicsActiveVertices);
      normal.needsUpdate = true;
      addWeightPhysicsPerformance('physicsNormalUpdateCount');
      addWeightPhysicsPerformance(
        'physicsNormalMs', performanceNow() - normalStarted);
    }
    state.physicsBoundsDirty = deformedVertices > 0;
  } else {
    state.physicsTransforms = null;
    state.physicsRotations.clear();
    position.array.set(state.baselinePositions);
    restoreNormals(mesh, state);
  }
  position.needsUpdate = true;
  if (invalidateShadow) invalidateCharacterShadowGeometry({request});
  else if (request) requestRender();
  return true;
}

function finalizePhysicsGeometry(mesh, state) {
  if (state.physicsBoundsDirty) {
    const started = performanceNow();
    mesh.geometry.computeBoundingBox();
    mesh.geometry.computeBoundingSphere();
    state.physicsBoundsDirty = false;
    addWeightPhysicsPerformance('physicsBoundsUpdateCount');
    addWeightPhysicsPerformance('physicsBoundsMs', performanceNow() - started);
  }
  if (state.prePhysicsFrustumCulled !== null) {
    mesh.frustumCulled = state.prePhysicsFrustumCulled;
    state.prePhysicsFrustumCulled = null;
  }
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

function modelJointWeightMask(state) {
  if (modelWeightState.weightViewMode !== 'model'
      || !modelSkinningRig
      || !Number.isInteger(modelWeightState.weightViewJointId)) return null;
  const joint = modelSkinningRig.joints?.[modelWeightState.weightViewJointId];
  if (!joint) return null;
  const selected = new Set((joint.members || [])
    .filter(member => member.sourceKey === state.skinningSourceKey)
    .map(member => Number(member.boneId))
    .filter(Number.isInteger));
  if (!selected.size) return null;
  return buildSelectedWeightMask(
    state.indices, state.weights, state.influenceCount, selected);
}

function heatmapMaskFor(state) {
  return modelWeightState.weightViewMode === 'model'
    ? modelJointWeightMask(state) : state.selectedWeightMask;
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
    const mask = heatmapMaskFor(state);
    if (modelWeightState.heatmapEnabled && selectedWeightPresent(mask)) {
      state.heatmapMode = modelWeightState.weightViewMode === 'model'
        ? 'model-joint' : 'bone';
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

export function setSelectedBones(selection) {
  refreshModelWeightSummary();
  const next = selectionMapFromEntries(selection);
  if (modelWeightState.loaded) {
    const available = new Map(modelWeightState.sources.map(source => [
      source.key, new Set(source.availableBoneIds),
    ]));
    for (const [sourceKey, ids] of next) {
      const valid = available.get(sourceKey);
      if (!valid) {
        next.delete(sourceKey);
        continue;
      }
      const filtered = new Set([...ids].filter(id => valid.has(id)));
      if (filtered.size) next.set(sourceKey, filtered);
      else next.delete(sourceKey);
    }
  }
  const previousEntries = sourceSelectionEntries(
    modelWeightState.selectedBonesBySource);
  const nextEntries = sourceSelectionEntries(next);
  if (sameBoneSelection(previousEntries, nextEntries)) {
    syncPhysicsToSelection();
    return modelWeightSnapshot();
  }
  const changedSourceKeys = new Set([
    ...modelWeightState.selectedBonesBySource.keys(), ...next.keys(),
  ].filter(sourceKey => !sameBoneSelection(
    sourceSelectionEntries(new Map([
      [sourceKey, modelWeightState.selectedBonesBySource.get(sourceKey)
        || new Set()],
    ])),
    sourceSelectionEntries(new Map([
      [sourceKey, next.get(sourceKey) || new Set()],
    ])),
  )));
  modelWeightState.selectedBonesBySource = next;
  knownMeshes.forEach(mesh => {
    const state = states.get(mesh);
    if (changedSourceKeys.has(state?.skinningSourceKey)) {
      refreshSelectedWeightMask(mesh, state);
    }
  });
  if (modelWeightState.heatmapEnabled) {
    updateModelWeightHeatmap(changedSourceKeys);
  }
  syncPhysicsToSelection(changedSourceKeys);
  notifyModelWeightChanged();
  requestRender();
  return modelWeightSnapshot();
}

export function setBoneSelected(sourceKey, boneId, selected) {
  const descriptor = modelWeightState.sourceDescriptors.get(sourceKey);
  const id = Number(boneId);
  if (!descriptor || !Number.isInteger(id) || id < 0) {
    return modelWeightSnapshot();
  }
  const entries = sourceSelectionEntries(modelWeightState.selectedBonesBySource)
    .filter(entry => entry.sourceKey !== sourceKey);
  const ids = new Set(modelWeightState.selectedBonesBySource.get(sourceKey));
  if (selected) ids.add(id);
  else ids.delete(id);
  if (ids.size) entries.push({...descriptor, boneIds: [...ids]});
  return setSelectedBones(entries);
}

export function clearSelectedBones() {
  return setSelectedBones([]);
}

export function loadSavedBoneSelection() {
  return setSelectedBones(sourceSelectionEntries(
    modelWeightState.savedBonesBySource));
}

export function saveModelWeightSelection() {
  if (selectionSavePromise) return selectionSavePromise;
  const selectedBones = serializeBoneSelection(sourceSelectionEntries(
    modelWeightState.selectedBonesBySource));
  const mesh = [...knownMeshes].find(eligibleSkinningMesh);
  const api = window.pywebview?.api?.save_weight_selection;
  if (!selectedBones.length || !mesh || typeof api !== 'function') {
    return Promise.resolve(modelWeightSnapshot());
  }
  const generation = modelWeightGeneration;
  modelWeightState.savingSelection = true;
  modelWeightState.selectionSaveError = null;
  notifyModelWeightChanged();
  selectionSavePromise = Promise.resolve(
    api(mesh.userData.modPath, selectedBones))
    .then(result => {
      if (generation !== modelWeightGeneration) return modelWeightSnapshot();
      if (!result?.saved) throw new Error('The bone selection was not saved.');
      modelWeightState.savedBonesBySource = selectionMapFromEntries(
        result.selected_bones ?? selectedBones);
      return modelWeightSnapshot();
    })
    .catch(error => {
      if (generation === modelWeightGeneration) {
        modelWeightState.selectionSaveError = error instanceof Error
          ? error.message : String(error);
      }
      return modelWeightSnapshot();
    })
    .finally(() => {
      if (generation === modelWeightGeneration) {
        modelWeightState.savingSelection = false;
        selectionSavePromise = null;
        notifyModelWeightChanged();
      }
    });
  return selectionSavePromise;
}

export function setPhysicsFrequency(frequencyHz) {
  const value = Number(frequencyHz);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({frequencyHz: value});
  return true;
}

export function setPhysicsDamping(dampingRatio) {
  const value = Number(dampingRatio);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({dampingRatio: value});
  return true;
}

export function setPhysicsMotionStrength(strength) {
  const value = Number(strength);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({angularResponse: value});
  return true;
}

export function setPhysicsLinearMotionStrength(strength) {
  const value = Number(strength);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({translationResponse: value});
  return true;
}

export function setPhysicsContinuousLinearResponse(strength) {
  const value = Number(strength);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({velocityResponse: value});
  return true;
}

export function setPhysicsGravityEnabled(enabled) {
  modelPhysicsSession.setSettings({gravityEnabled: !!enabled});
  return !!enabled;
}

export function setPhysicsGravityScale(scale) {
  const value = Number(scale);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({gravityScale: value});
  return true;
}

export function setPhysicsConstraintsEnabled(enabled) {
  modelPhysicsSession.setSettings({constraintsEnabled: !!enabled});
  return !!enabled;
}

export function setPhysicsMaxBendDegrees(degrees) {
  const value = Number(degrees);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({maxBendDegrees: value});
  return true;
}

export function setModelWeightHeatmap(enabled) {
  modelWeightState.heatmapEnabled = !!enabled;
  updateModelWeightHeatmap();
  notifyModelWeightChanged();
  requestRender();
  return modelWeightState.heatmapEnabled;
}

export function resetSkinningExperiment(mesh) {
  const state = stateFor(mesh);
  if (!state?.loaded) return;
  if (state.physicsEnabled) {
    resetModelPhysicsMotion();
    if (state.heatmapMode || state.debugMaterial) disableHeatmap(mesh, state);
    requestRender();
    return;
  }
  state.deformationMode = null;
  if (!state.physicsEnabled) state.physicsTransforms = null;
  applyDeformation(mesh, state);
  if (state.heatmapMode || state.debugMaterial) disableHeatmap(mesh, state);
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  invalidateCharacterShadowGeometry();
  requestRender();
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
