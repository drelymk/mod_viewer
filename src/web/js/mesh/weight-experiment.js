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
  serializeBoneSelection, sameBoneSelection, barycentricCoordinates,
  interpolateTriangleBoneWeights, sampleNearbyBoneWeights,
} from './weight-selection.js';
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
  getWeightPhysicsPerformanceStats, resetWeightPhysicsPerformanceStats,
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
  pickStatus: '',
  picking: false,
};

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

function worldPositionArray(mesh) {
  const position = mesh?.geometry?.attributes?.position;
  if (!position?.array) return null;
  mesh.updateWorldMatrix?.(true, false);
  const result = new Float32Array(position.array.length);
  const point = new THREE.Vector3();
  for (let offset = 0; offset < position.array.length; offset += 3) {
    point.set(
      Number(position.array[offset]),
      Number(position.array[offset + 1]),
      Number(position.array[offset + 2]));
    point.applyMatrix4(mesh.matrixWorld);
    result[offset] = point.x;
    result[offset + 1] = point.y;
    result[offset + 2] = point.z;
  }
  return result;
}

function triangleVertexIndices(mesh, faceIndex) {
  const triangle = Number(faceIndex);
  if (!Number.isInteger(triangle) || triangle < 0) return null;
  const index = mesh.geometry?.index?.array;
  const positions = mesh.geometry?.attributes?.position;
  const start = triangle * 3;
  if (index) {
    if (start + 2 >= index.length) return null;
    return [Number(index[start]), Number(index[start + 1]), Number(index[start + 2])];
  }
  if (!positions || start + 2 >= positions.count) return null;
  return [start, start + 1, start + 2];
}

function trianglePoint(worldPositions, vertices, vertex) {
  const offset = vertices[vertex] * 3;
  return [worldPositions[offset], worldPositions[offset + 1],
    worldPositions[offset + 2]];
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
  const positions = worldPositionArray(mesh);
  const point = intersection.point;
  const radiusWorld = pickRadiusWorld();
  if (!positions || !point || positions.length < 3) {
    modelWeightState.pickStatus = 'No skin weights are available for this part.';
    notifyModelWeightChanged();
    return null;
  }
  let influences = sampleNearbyBoneWeights(
    positions, state.indices, state.weights, state.influenceCount,
    point.toArray(), radiusWorld);
  if (!influences.length) {
    const vertices = triangleVertexIndices(mesh, intersection.faceIndex);
    if (vertices) {
      const first = trianglePoint(positions, vertices, 0);
      const second = trianglePoint(positions, vertices, 1);
      const third = trianglePoint(positions, vertices, 2);
      const barycentric = barycentricCoordinates(
        point.toArray(), first, second, third);
      if (barycentric) {
        influences = interpolateTriangleBoneWeights(
          state.indices, state.weights, state.influenceCount,
          vertices, barycentric);
      }
    }
  }
  if (!influences.length) {
    modelWeightState.pickStatus = 'No skin weights are available for this part.';
    notifyModelWeightChanged();
    return null;
  }
  const source = modelWeightState.sourceDescriptors.get(
    state.skinningSourceKey);
  modelWeightState.pickedPoint = {
    point: point.toArray(),
    sourceKey: state.skinningSourceKey,
    sourceFile: source?.sourceFile || state.skinningSourceFile,
    boneIdOffset: source?.boneIdOffset ?? state.skinningBoneOffset,
    meshKey: mesh.userData?.semanticKey || null,
    radiusWorld,
    influences,
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

function selectedWeightPresent(state) {
  return !!state?.selectedWeightMask
    && state.selectedWeightMask.some(value => value > 0);
}

function setModelWeightLoadError(error) {
  modelWeightState.error = error instanceof Error
    ? error.message : String(error);
  modelWeightState.loaded = false;
  modelWeightState.noWeights = false;
}

function resetModelWeightState() {
  modelWeightGeneration += 1;
  sourcePhysicsRigs.clear();
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
  modelWeightState.pickStatus = '';
  notifyModelWeightChanged();
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

function aggregateSourceInfluenceGraph(members) {
  const nodeTotals = new Map();
  const relationshipTotals = new Map();
  for (const mesh of members) {
    const state = states.get(mesh);
    if (!state?.loaded) continue;
    const graph = ensureInfluenceGraph(mesh, state);
    for (const node of graph.nodes || []) {
      const boneId = Number(node.boneId);
      const totalWeight = Number(node.totalWeight) || 0;
      if (!Number.isFinite(boneId) || totalWeight <= 0) continue;
      const center = node.weightedCenter || [0, 0, 0];
      const radius = Math.max(0, Number(node.weightedRadius) || 0);
      const entry = nodeTotals.get(boneId) || {
        boneId,
        totalWeight: 0,
        affectedVertexCount: 0,
        maxVertexWeight: 0,
        weightedX: 0,
        weightedY: 0,
        weightedZ: 0,
        secondMoment: 0,
      };
      entry.totalWeight += totalWeight;
      entry.affectedVertexCount += Number(node.affectedVertexCount) || 0;
      entry.maxVertexWeight = Math.max(
        entry.maxVertexWeight, Number(node.maxVertexWeight) || 0);
      entry.weightedX += totalWeight * (Number(center[0]) || 0);
      entry.weightedY += totalWeight * (Number(center[1]) || 0);
      entry.weightedZ += totalWeight * (Number(center[2]) || 0);
      entry.secondMoment += totalWeight * (
        radius * radius + (Number(center[0]) || 0) ** 2
        + (Number(center[1]) || 0) ** 2
        + (Number(center[2]) || 0) ** 2);
      nodeTotals.set(boneId, entry);
    }
    for (const relationship of graph.relationships || []) {
      const boneA = Number(relationship.boneA);
      const boneB = Number(relationship.boneB);
      if (!Number.isFinite(boneA) || !Number.isFinite(boneB)) continue;
      const key = pairKey(boneA, boneB);
      const entry = relationshipTotals.get(key) || {
        boneA: Math.min(boneA, boneB),
        boneB: Math.max(boneA, boneB),
        sharedVertexCount: 0,
        minOverlap: 0,
        productOverlap: 0,
      };
      entry.sharedVertexCount += Number(relationship.sharedVertexCount) || 0;
      entry.minOverlap += Number(relationship.minOverlap) || 0;
      entry.productOverlap += Number(relationship.productOverlap) || 0;
      relationshipTotals.set(key, entry);
    }
  }

  const nodes = [...nodeTotals.values()].map(entry => {
    const center = entry.totalWeight > 0 ? [
      entry.weightedX / entry.totalWeight,
      entry.weightedY / entry.totalWeight,
      entry.weightedZ / entry.totalWeight,
    ] : [0, 0, 0];
    const centerLengthSquared = center.reduce(
      (sum, value) => sum + value * value, 0);
    return {
      boneId: entry.boneId,
      totalWeight: entry.totalWeight,
      affectedVertexCount: entry.affectedVertexCount,
      maxVertexWeight: entry.maxVertexWeight,
      weightedCenter: center,
      weightedRadius: Math.sqrt(Math.max(0,
        entry.secondMoment / entry.totalWeight - centerLengthSquared)),
    };
  }).sort((left, right) => left.boneId - right.boneId);
  const nodeById = new Map(nodes.map(node => [node.boneId, node]));
  const totalWeight = nodes.reduce((sum, node) => sum + node.totalWeight, 0);
  const sourceCenter = nodes.length ? nodes.reduce((sum, node) => [
    sum[0] + node.weightedCenter[0] * node.totalWeight,
    sum[1] + node.weightedCenter[1] * node.totalWeight,
    sum[2] + node.weightedCenter[2] * node.totalWeight,
  ], [0, 0, 0]).map(value => totalWeight > 0 ? value / totalWeight : 0)
    : [0, 0, 0];
  const radius = nodes.length ? Math.max(...nodes.map(node => {
    const distance = centerDistance(node.weightedCenter, sourceCenter) || 0;
    return distance + (Number(node.weightedRadius) || 0);
  })) : null;
  const relationships = [...relationshipTotals.values()].map(relationship => {
    const nodeA = nodeById.get(relationship.boneA);
    const nodeB = nodeById.get(relationship.boneB);
    const supportA = Number(nodeA?.totalWeight) || 0;
    const supportB = Number(nodeB?.totalWeight) || 0;
    const containmentDenominator = Math.min(supportA, supportB);
    const containment = containmentDenominator > 0
      ? relationship.minOverlap / containmentDenominator : 0;
    const jaccardDenominator = supportA + supportB - relationship.minOverlap;
    const distance = centerDistance(
      nodeA?.weightedCenter, nodeB?.weightedCenter);
    const normalizedDistance = distance !== null && radius > 0
      ? distance / radius : null;
    const distancePenalty = Number.isFinite(normalizedDistance)
      ? 1 / (1 + Math.max(0, normalizedDistance)) : 1;
    return {
      ...relationship,
      containment,
      jaccard: jaccardDenominator > 0
        ? relationship.minOverlap / jaccardDenominator : 0,
      centerDistance: distance,
      normalizedDistance,
      treeEdgeScore: containment * distancePenalty,
    };
  });
  return {nodes, relationships, boundingSphereRadius: radius};
}

function createSourcePhysicsRig(sourceKey, members) {
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
    physicsTargetByBoneId: null,
    physicsEquilibriumByBoneId: null,
    physicsTransformCache: new Map(),
    transforms: new Map(),
    rotations: new Map(),
  };
  refreshSourcePhysicsRig(rig, members);
  addWeightPhysicsPerformance('sourcePhysicsRigCount');
  return rig;
}

function refreshSourcePhysicsRig(rig, members) {
  rig.meshes = new Set(members);
  rig.transformsDirty = true;
  rig.influenceGraph = aggregateSourceInfluenceGraph(members);
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
  const candidateEdges = candidateRelationshipEdges(graph);
  const selectedEdges = candidateEdges.filter(relationship =>
    selected.has(Number(relationship.boneA))
    && selected.has(Number(relationship.boneB)));
  const candidateTree = buildMaximumSpanningTree(selectedNodes, selectedEdges);
  const physicsEdges = pruneSelectedRelationshipEdges(
    candidateTree.edges, graph.relationships, selected);
  const selectedTree = buildMaximumSpanningTree(selectedNodes, physicsEdges);
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
    const orientation = orientTree(edges, rootId);
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
  }
  refreshModelWeightSummary({refreshStats: true});
  if (sourceKey && modelPhysicsSession.getState().enabled) {
    syncPhysicsParticipants(new Set([sourceKey]));
  }
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
  state.influenceNodes = buildInfluenceNodes(
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
}

function syncPhysicsToSelection(changedSourceKeys = null) {
  const shouldEnable = modelWeightState.loaded
    && selectedBoneCount(modelWeightState.selectedBonesBySource) > 0;
  const enabled = modelPhysicsSession.getState().enabled;
  if (!shouldEnable) {
    if (enabled) disableModelPhysics();
    return false;
  }
  if (!enabled) modelPhysicsSession.enable(getModelTransformState());
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

function compactVertexCount(indices, weights, influenceCount) {
  if (!indices || !weights || !Number.isInteger(influenceCount)
      || influenceCount <= 0) return 0;
  return Math.floor(Math.min(indices.length, weights.length) / influenceCount);
}

function positiveInfluencesForVertex(
    indices, weights, influenceCount, vertexIndex) {
  const result = new Map();
  const start = vertexIndex * influenceCount;
  for (let influence = 0; influence < influenceCount; influence += 1) {
    const boneId = Number(indices[start + influence]);
    const weight = weights[start + influence];
    if (!Number.isFinite(weight) || weight <= 0) continue;
    result.set(boneId, (result.get(boneId) || 0) + weight);
  }
  return result;
}

export function buildInfluenceNodes(
    baselinePositions, indices, weights, influenceCount, boneIds = null) {
  const vertexCount = compactVertexCount(indices, weights, influenceCount);
  const requested = boneIds === null || boneIds === undefined
    ? null : new Set([...boneIds].map(Number));
  const entries = new Map();
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const influences = positiveInfluencesForVertex(
      indices, weights, influenceCount, vertex);
    influences.forEach((weight, boneId) => {
      if (requested && !requested.has(boneId)) return;
      const entry = entries.get(boneId) || {
        boneId,
        totalWeight: 0,
        affectedVertexCount: 0,
        maxVertexWeight: 0,
        weightedX: 0,
        weightedY: 0,
        weightedZ: 0,
        squaredPositionWeight: 0,
        positionWeight: 0,
      };
      entry.totalWeight += weight;
      entry.affectedVertexCount += 1;
      entry.maxVertexWeight = Math.max(entry.maxVertexWeight, weight);
      if (baselinePositions
          && baselinePositions.length >= vertex * 3 + 3) {
        const offset = vertex * 3;
        const x = baselinePositions[offset];
        const y = baselinePositions[offset + 1];
        const z = baselinePositions[offset + 2];
        entry.weightedX += x * weight;
        entry.weightedY += y * weight;
        entry.weightedZ += z * weight;
        entry.squaredPositionWeight += (x * x + y * y + z * z) * weight;
        entry.positionWeight += weight;
      }
      entries.set(boneId, entry);
    });
  }

  const orderedIds = requested ? [...requested] : [...entries.keys()];
  return orderedIds.filter(boneId => entries.has(boneId)).map(boneId => {
    const entry = entries.get(boneId);
    const nodeCenter = entry.positionWeight > 0
      ? [entry.weightedX / entry.positionWeight,
        entry.weightedY / entry.positionWeight,
        entry.weightedZ / entry.positionWeight]
      : [0, 0, 0];
    const weightedRadius = entry.positionWeight > 0
      ? Math.sqrt(Math.max(0,
        entry.squaredPositionWeight / entry.positionWeight
        - (nodeCenter[0] ** 2 + nodeCenter[1] ** 2
          + nodeCenter[2] ** 2)))
      : null;
    return {
      boneId: entry.boneId,
      totalWeight: entry.totalWeight,
      affectedVertexCount: entry.affectedVertexCount,
      maxVertexWeight: entry.maxVertexWeight,
      weightedCenter: nodeCenter,
      weightedRadius,
    };
  });
}

function pairKey(boneA, boneB) {
  return boneA < boneB ? `${boneA}:${boneB}` : `${boneB}:${boneA}`;
}

function pairIds(boneA, boneB) {
  return boneA < boneB ? [boneA, boneB] : [boneB, boneA];
}

function centerDistance(centerA, centerB) {
  if (!centerA || !centerB
      || centerA.length < 3 || centerB.length < 3) return null;
  const dx = centerA[0] - centerB[0];
  const dy = centerA[1] - centerB[1];
  const dz = centerA[2] - centerB[2];
  return Math.hypot(dx, dy, dz);
}

export function buildInfluenceRelationships(
    indices, weights, influenceCount, nodes, boundingSphereRadius = null) {
  const nodeById = new Map((nodes || []).map(node => [
    Number(node.boneId), node]));
  const vertexCount = compactVertexCount(indices, weights, influenceCount);
  const relationships = new Map();
  const ids = [];
  const mergedWeights = [];
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    ids.length = 0;
    mergedWeights.length = 0;
    const start = vertex * influenceCount;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const boneId = Number(indices[start + influence]);
      const weight = Number(weights[start + influence]);
      if (!nodeById.has(boneId) || !Number.isFinite(weight) || weight <= 0) {
        continue;
      }
      const existing = ids.indexOf(boneId);
      if (existing >= 0) mergedWeights[existing] += weight;
      else {
        ids.push(boneId);
        mergedWeights.push(weight);
      }
    }
    for (let left = 0; left < ids.length; left += 1) {
      for (let right = left + 1; right < ids.length; right += 1) {
        const [boneA, boneB] = pairIds(ids[left], ids[right]);
        const key = pairKey(boneA, boneB);
        const weightA = mergedWeights[left];
        const weightB = mergedWeights[right];
        const relationship = relationships.get(key) || {
          boneA,
          boneB,
          sharedVertexCount: 0,
          minOverlap: 0,
          productOverlap: 0,
        };
        relationship.sharedVertexCount += 1;
        relationship.minOverlap += Math.min(weightA, weightB);
        relationship.productOverlap += weightA * weightB;
        relationships.set(key, relationship);
      }
    }
  }

  const radius = Number(boundingSphereRadius);
  return [...relationships.values()].map(relationship => {
    const nodeA = nodeById.get(relationship.boneA);
    const nodeB = nodeById.get(relationship.boneB);
    const supportA = Number(nodeA?.totalWeight) || 0;
    const supportB = Number(nodeB?.totalWeight) || 0;
    const containmentDenominator = Math.min(supportA, supportB);
    const jaccardDenominator = supportA + supportB
      - relationship.minOverlap;
    const distance = centerDistance(
      nodeA?.weightedCenter, nodeB?.weightedCenter);
    return {
      ...relationship,
      containment: containmentDenominator > 0
        ? relationship.minOverlap / containmentDenominator : 0,
      jaccard: jaccardDenominator > 0
        ? relationship.minOverlap / jaccardDenominator : 0,
      centerDistance: distance,
      normalizedDistance: distance !== null && radius > 0
        ? distance / radius : null,
    };
  });
}

function relationshipSort(a, b) {
  return (Number(b.containment) || 0) - (Number(a.containment) || 0)
    || (Number(b.jaccard) || 0) - (Number(a.jaccard) || 0)
    || (Number(a.normalizedDistance ?? Infinity)
      - Number(b.normalizedDistance ?? Infinity))
    || String(a.boneA).localeCompare(String(b.boneA))
    || String(a.boneB).localeCompare(String(b.boneB));
}

export function candidateRelationshipEdges(graph, options = {}) {
  const minSharedVertexCount = Number(
    options.minSharedVertexCount ?? 1);
  const containmentThreshold = Number(
    options.containmentThreshold ?? CANDIDATE_CONTAINMENT_THRESHOLD);
  const jaccardThreshold = Number(
    options.jaccardThreshold ?? CANDIDATE_JACCARD_THRESHOLD);
  return (graph?.relationships || [])
    .filter(relationship => relationship.sharedVertexCount
      >= minSharedVertexCount
      && (relationship.containment >= containmentThreshold
        || relationship.jaccard >= jaccardThreshold))
    .map(relationship => {
      const normalizedDistance = Number(relationship.normalizedDistance);
      const distancePenalty = Number.isFinite(normalizedDistance)
        ? 1 / (1 + Math.max(0, normalizedDistance)) : 1;
      return {
        ...relationship,
        treeEdgeScore: relationship.containment * distancePenalty,
      };
    })
    .sort(relationshipSort);
}

function treeEdgeScore(edge) {
  return Number(edge.treeEdgeScore ?? edge.score
    ?? edge.containment ?? edge.jaccard ?? 0) || 0;
}

function treeEdgeCompare(a, b) {
  return treeEdgeScore(b) - treeEdgeScore(a)
    || relationshipSort(a, b);
}

function treeSideForEdge(edges, startId, skippedEdge) {
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

function bestStaticAttachment(side, relationships, selected) {
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
    const left = treeSideForEdge(edges, boneA, edge);
    const right = treeSideForEdge(edges, boneB, edge);
    const leftAttachment = bestStaticAttachment(
      left, relationships, selected);
    const rightAttachment = bestStaticAttachment(
      right, relationships, selected);
    const bridgeOverlap = Number(edge.minOverlap) || 0;
    return !(leftAttachment && rightAttachment
      && Number(leftAttachment.minOverlap) > bridgeOverlap
      && Number(rightAttachment.minOverlap) > bridgeOverlap);
  });
}

export function buildMaximumSpanningTree(nodes, edges) {
  const nodeIds = [...new Set((nodes || []).map(node => Number(node.boneId)))];
  const parent = new Map(nodeIds.map(id => [id, id]));
  const rank = new Map(nodeIds.map(id => [id, 0]));
  const find = id => {
    let root = id;
    while (parent.get(root) !== root) root = parent.get(root);
    while (parent.get(id) !== id) {
      const next = parent.get(id);
      parent.set(id, root);
      id = next;
    }
    return root;
  };
  const union = (left, right) => {
    let rootLeft = find(left);
    let rootRight = find(right);
    if (rootLeft === rootRight) return false;
    if (rank.get(rootLeft) < rank.get(rootRight)) {
      [rootLeft, rootRight] = [rootRight, rootLeft];
    }
    parent.set(rootRight, rootLeft);
    if (rank.get(rootLeft) === rank.get(rootRight)) {
      rank.set(rootLeft, rank.get(rootLeft) + 1);
    }
    return true;
  };
  const orderedEdges = [...edges || []].sort(treeEdgeCompare);
  const selected = [];
  for (const edge of orderedEdges) {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!parent.has(boneA) || !parent.has(boneB) || boneA === boneB) {
      continue;
    }
    if (union(boneA, boneB)) selected.push(edge);
  }
  const components = new Map();
  nodeIds.forEach(id => {
    const root = find(id);
    const component = components.get(root) || [];
    component.push(id);
    components.set(root, component);
  });
  return {edges: selected, components: [...components.values()]};
}

export function orientTree(treeEdges, rootId) {
  const adjacency = new Map();
  const add = (from, to) => {
    const neighbors = adjacency.get(from) || [];
    neighbors.push(to);
    adjacency.set(from, neighbors);
  };
  (treeEdges || []).forEach(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!Number.isFinite(boneA) || !Number.isFinite(boneB)) return;
    add(boneA, boneB);
    add(boneB, boneA);
  });
  const root = Number(rootId);
  if (Number.isFinite(root) && !adjacency.has(root)) adjacency.set(root, []);
  const parentById = {};
  const childrenById = {};
  const depthById = {};
  adjacency.forEach((neighbors, boneId) => {
    parentById[boneId] = null;
    childrenById[boneId] = [];
    depthById[boneId] = null;
  });
  if (Number.isFinite(root)) {
    const queue = [root];
    depthById[root] = 0;
    while (queue.length) {
      const current = queue.shift();
      const depth = depthById[current];
      (adjacency.get(current) || []).forEach(neighbor => {
        if (depthById[neighbor] !== null) return;
        parentById[neighbor] = current;
        childrenById[current].push(neighbor);
        depthById[neighbor] = depth + 1;
        queue.push(neighbor);
      });
    }
  }
  return {rootId: root, parentById, childrenById, depthById};
}

function normalizedNodeIds(nodes) {
  return [...new Set((nodes || []).map(node => Number(
    node?.boneId ?? node)).filter(Number.isFinite))];
}

function connectedNodeComponents(nodes, edges) {
  const nodeIds = normalizedNodeIds(nodes);
  const nodeSet = new Set(nodeIds);
  const adjacency = new Map(nodeIds.map(id => [id, []]));
  (edges || []).forEach(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!nodeSet.has(boneA) || !nodeSet.has(boneB) || boneA === boneB) {
      return;
    }
    adjacency.get(boneA).push(boneB);
    adjacency.get(boneB).push(boneA);
  });
  const seen = new Set();
  const components = [];
  nodeIds.forEach(start => {
    if (seen.has(start)) return;
    const component = [];
    const queue = [start];
    seen.add(start);
    while (queue.length) {
      const current = queue.shift();
      component.push(current);
      adjacency.get(current).forEach(neighbor => {
        if (seen.has(neighbor)) return;
        seen.add(neighbor);
        queue.push(neighbor);
      });
    }
    components.push(component);
  });
  return components;
}

function graphNodeList(graph) {
  return Array.isArray(graph) ? graph : graph?.nodes || [];
}

export function chooseSecondaryComponentRoot(
    component, graph, primaryComponent, primaryRootId) {
  const componentIds = normalizedNodeIds(component?.nodeIds || component);
  if (!componentIds.length) return null;
  const nodes = graphNodeList(graph);
  const nodeById = new Map(nodes.map(node => [Number(node.boneId), node]));
  const primaryIds = normalizedNodeIds(
    primaryComponent?.nodeIds || primaryComponent);
  const primaryNodes = primaryIds.map(id => nodeById.get(id)).filter(Boolean);
  if (!primaryNodes.length && nodeById.has(Number(primaryRootId))) {
    primaryNodes.push(nodeById.get(Number(primaryRootId)));
  }
  if (!primaryNodes.length) return componentIds[0];

  let bestId = componentIds[0];
  let bestDistance = Infinity;
  componentIds.forEach((id, index) => {
    const node = nodeById.get(id);
    let nearest = Infinity;
    primaryNodes.forEach(primary => {
      const distance = centerDistance(
        node?.weightedCenter, primary?.weightedCenter);
      if (distance !== null) nearest = Math.min(nearest, distance);
    });
    // Preserve component input order for an exact-distance tie.  The
    // distance, rather than the numeric ID, chooses the attachment side.
    if (nearest < bestDistance || (nearest === bestDistance && index === 0)) {
      bestId = id;
      bestDistance = nearest;
    }
  });
  return bestId;
}

function completeOrientation(nodeIds, orientation) {
  nodeIds.forEach(id => {
    if (!Object.prototype.hasOwnProperty.call(orientation.parentById, id)) {
      orientation.parentById[id] = null;
      orientation.childrenById[id] = [];
      orientation.depthById[id] = null;
    }
  });
  return orientation;
}

export function orientForest(nodes, treeEdges, primaryRootId, options = {}) {
  const supplied = options.components;
  const rawComponents = supplied?.length
    ? supplied.map(component => component.nodeIds || component)
    : connectedNodeComponents(nodes, treeEdges);
  const components = rawComponents.map(component => normalizedNodeIds(component));
  const requestedRoot = Number(primaryRootId);
  let primaryComponentId = components.findIndex(component =>
    component.includes(requestedRoot));
  if (primaryComponentId < 0 && components.length) primaryComponentId = 0;

  const componentByBoneId = {};
  components.forEach((nodeIds, componentId) => {
    nodeIds.forEach(id => { componentByBoneId[id] = componentId; });
  });

  const rootOverrides = options.secondaryRootByComponent;
  const forestComponents = components.map((nodeIds, componentId) => {
    const primary = componentId === primaryComponentId;
    let rootId = primary && nodeIds.includes(requestedRoot)
      ? requestedRoot : null;
    if (rootId === null && primary) rootId = nodeIds[0] ?? null;
    if (rootId === null) {
      const override = rootOverrides instanceof Map
        ? Number(rootOverrides.get(componentId))
        : Number(rootOverrides?.[componentId]);
      rootId = nodeIds.includes(override) ? override
        : chooseSecondaryComponentRoot(
          nodeIds,
          nodes,
          components[primaryComponentId] || [],
          requestedRoot);
    }
    const nodeSet = new Set(nodeIds);
    const componentEdges = (treeEdges || []).filter(edge =>
      nodeSet.has(Number(edge.boneA)) && nodeSet.has(Number(edge.boneB)));
    const orientation = completeOrientation(
      nodeIds, orientTree(componentEdges, rootId));
    const depths = Object.values(orientation.depthById)
      .filter(depth => depth !== null).map(Number);
    return {
      componentId,
      nodeIds,
      rootId,
      parentById: orientation.parentById,
      childrenById: orientation.childrenById,
      depthById: orientation.depthById,
      edgeCount: componentEdges.length,
      maxDepth: Math.max(0, ...depths),
      primary,
    };
  });
  const primary = forestComponents[primaryComponentId];
  return {
    primaryRootId: primary?.rootId ?? null,
    primaryComponentId: primaryComponentId < 0 ? null : primaryComponentId,
    components: forestComponents,
    componentByBoneId,
  };
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
  const nodes = state.influenceNodes || buildInfluenceNodes(
    state.baselinePositions, state.indices, state.weights,
    state.influenceCount, state.boneIds);
  state.influenceNodes = nodes;
  const relationships = buildInfluenceRelationships(
    state.indices, state.weights, state.influenceCount, nodes,
    Number.isFinite(radius) && radius > 0 ? radius : null);
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
  const normal = mesh.geometry.attributes.normal;
  state.baselinePositions = new Float32Array(position.array);
  state.baselineNormals = normal ? new Float32Array(normal.array) : null;
  state.influenceNodes = buildInfluenceNodes(
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
  if (state.heatmapMode) updateHeatmap(mesh, state);
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

function updateHeatmap(mesh, state) {
  if (!state.heatmapMode || !state.selectedWeightMask) return;
  const count = Math.floor(state.indices.length / state.influenceCount);
  const colors = new Float32Array(count * 3);
  for (let vertex = 0; vertex < count; vertex += 1) {
    const value = Math.max(0, Math.min(1,
      Number(state.selectedWeightMask[vertex]) || 0));
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

function updateModelWeightHeatmap(changedSourceKeys = null) {
  knownMeshes.forEach(mesh => {
    const state = states.get(mesh);
    if (!state?.loaded) return;
    if (changedSourceKeys
        && !changedSourceKeys.has(state.skinningSourceKey)) return;
    if (modelWeightState.heatmapEnabled && selectedWeightPresent(state)) {
      state.heatmapMode = 'bone';
      updateHeatmap(mesh, state);
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
