// Explicit, removable skin-weight experiment. Normal mesh loading never
// imports or invokes this module's bridge operation until the Inspector asks.

import * as THREE from 'three';
import { invalidateCharacterShadowGeometry } from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';
import {
  applyWeightedRotation, buildChainTransforms, buildForestTransforms,
  buildForestTransformsFromLocalAngles, applyWeightedTransformDeformation,
  applyWeightedChainDeformation,
} from './weight-deformation.js';
import {
  DEFAULT_PHYSICS_DAMPING_RATIO, DEFAULT_PHYSICS_FREQUENCY_HZ,
  applyReferenceFrameAngularDelta,
  applyReferenceFrameTranslationDelta,
  applyPhysicsKick as applyPhysicsKickState, initializePhysicsState,
  isPhysicsSettled, physicsAngleMap, resetPhysicsState,
  stepSpringPhysics,
} from './weight-physics.js';

export {
  applyWeightedRotation, buildChainTransforms, buildForestTransforms,
  buildForestTransformsFromLocalAngles, applyWeightedTransformDeformation,
  applyWeightedChainDeformation,
} from './weight-deformation.js';

export {
  DEFAULT_ANGLE_TOLERANCE, DEFAULT_PHYSICS_DAMPING_RATIO,
  DEFAULT_PHYSICS_FREQUENCY_HZ, DEFAULT_VELOCITY_TOLERANCE,
  MAX_ANGULAR_VELOCITY, MAX_LOCAL_ANGLE,
  applyReferenceFrameAngularDelta,
  applyReferenceFrameTranslationDelta,
  buildPhysicsTargetAngles, initializePhysicsState, isPhysicsSettled,
  physicsAngleMap, resetPhysicsState, stepSpringPhysics,
} from './weight-physics.js';
export {applyPhysicsKick as applyPhysicsKickSolver} from './weight-physics.js';

const states = new WeakMap();
let activeExperimentHelperMesh = null;
const PHYSICS_STEP = 1 / 120;
const MAX_PHYSICS_FRAME_DELTA = 0.05;
const MAX_PHYSICS_SUBSTEPS = 6;
const PHYSICS_KICK_SPEED = Math.PI;
let physicsFrameId = null;
const activePhysicsMeshes = new Set();

const FULL_COVERAGE_THRESHOLD = 0.9999;
const COVERAGE_99_THRESHOLD = 0.99;
const COVERAGE_95_THRESHOLD = 0.95;
const AUTHOR_WEIGHT_HIGH_THRESHOLD = 1.001;
const AUTHOR_WEIGHT_LOW_THRESHOLD = 0.999;
export const SIGNIFICANT_RESIDUAL_RATIO = 0.02;
export const SIGNIFICANT_VERTEX_WEIGHT = 0.25;
export const INFLUENCE_GRAPH_CONTAINMENT_THRESHOLD = 0.05;
export const INFLUENCE_GRAPH_TOP_K = 4;
export const CANDIDATE_CONTAINMENT_THRESHOLD = 0.02;
export const CANDIDATE_JACCARD_THRESHOLD = 0.01;

const ERROR_MESSAGES = Object.freeze({
  mesh_not_found: 'The selected mesh could not be found.',
  skinning_not_available: 'No skin-weight stream was found for this draw.',
  ambiguous_skinning_source:
    'More than one possible Blend stream is active for this draw.',
  unsupported_skinning_layout:
    'This Blend format is not supported by the experiment.',
  skinning_buffer_truncated: 'The skin-weight buffer is truncated.',
  geometry_not_available:
    'The rendered draw geometry could not be prepared.',
});

function newState() {
  return {
    loaded: false,
    loading: false,
    promise: null,
    error: null,
    influenceCount: 0,
    boneIds: [],
    indices: null,
    weights: null,
    selectedBone: null,
    axis: 'Z',
    angle: 0,
    chainText: '',
    chainIds: [],
    chainAxis: 'Z',
    chainAngle: 0,
    deformationMode: null,
    forestAxis: 'Z',
    forestAngle: 0,
    physicsEnabled: false,
    physicsAxis: 'Z',
    physicsTargetAngle: 0,
    physicsFrequencyHz: DEFAULT_PHYSICS_FREQUENCY_HZ,
    physicsDampingRatio: DEFAULT_PHYSICS_DAMPING_RATIO,
    physicsMotionStrength: 0.35,
    physicsLinearMotionStrength: 0.35,
    physicsReferenceQuaternion: null,
    lastRootAngularDelta: 0,
    lastProjectedAngularDelta: 0,
    motionEventCount: 0,
    lastRootTranslationDeltaWorld: [0, 0, 0],
    lastRootTranslationDeltaLocal: [0, 0, 0],
    lastTranslationLag: 0,
    translationEventCount: 0,
    physicsState: null,
    physicsTransforms: null,
    physicsAccumulator: 0,
    physicsLastTimestamp: null,
    physicsSettled: true,
    chainError: null,
    chainHelpersVisible: false,
    chainHelpers: null,
    chainCoverage: null,
    missingInfluences: [],
    influenceGraph: null,
    candidateRootId: null,
    candidateTree: null,
    candidateForest: null,
    forestTransforms: null,
    influenceVisualizationMode: null,
    influenceVisualization: null,
    baselinePositions: null,
    baselineNormals: null,
    originalMaterial: null,
    debugMaterial: null,
    heatmapMode: null,
    diagnostics: null,
    encoding: null,
    centerByBoneId: null,
    centers: new Map(),
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

function clearMotionDiagnostics(state) {
  state.lastRootAngularDelta = 0;
  state.lastProjectedAngularDelta = 0;
  state.motionEventCount = 0;
  state.lastRootTranslationDeltaWorld = [0, 0, 0];
  state.lastRootTranslationDeltaLocal = [0, 0, 0];
  state.lastTranslationLag = 0;
  state.translationEventCount = 0;
}

function motionAxisVector(axis) {
  if (axis === 'X') return new THREE.Vector3(1, 0, 0);
  if (axis === 'Y') return new THREE.Vector3(0, 1, 0);
  return new THREE.Vector3(0, 0, 1);
}

function shortestQuaternionDelta(previousQuaternion, currentQuaternion) {
  if (!previousQuaternion?.clone || !currentQuaternion?.clone) return null;
  const previous = previousQuaternion.clone();
  const current = currentQuaternion.clone();
  if (!previous.isQuaternion || !current.isQuaternion
      || previous.lengthSq() === 0 || current.lengthSq() === 0) return null;
  previous.normalize();
  current.normalize();
  const delta = previous.clone().invert().multiply(current).normalize();
  // q and -q are equivalent orientations. Canonicalizing w selects the
  // shortest representation and prevents a fake near-360-degree impulse.
  if (delta.w < 0) {
    delta.set(-delta.x, -delta.y, -delta.z, -delta.w);
  }
  const vector = new THREE.Vector3(delta.x, delta.y, delta.z);
  const vectorLength = vector.length();
  if (vectorLength < 1e-12) {
    return {angle: 0, rotationVector: new THREE.Vector3()};
  }
  const angle = 2 * Math.atan2(vectorLength,
    Math.max(-1, Math.min(1, delta.w)));
  return {
    angle,
    rotationVector: vector.multiplyScalar(angle / vectorLength),
  };
}

function signedQuaternionDeltaAngle(previousQuaternion, currentQuaternion) {
  const delta = shortestQuaternionDelta(previousQuaternion, currentQuaternion);
  if (!delta?.angle) return 0;
  const components = [
    delta.rotationVector.x,
    delta.rotationVector.y,
    delta.rotationVector.z,
  ];
  const dominant = components.reduce((best, value) =>
    Math.abs(value) > Math.abs(best) ? value : best, 0);
  return delta.angle * Math.sign(dominant || 1);
}

/** Project a shortest relative quaternion rotation into the previous local
 * frame and return its signed scalar component on the selected axis. */
export function projectQuaternionDeltaOntoAxis(
    previousQuaternion, currentQuaternion, axis) {
  const delta = shortestQuaternionDelta(previousQuaternion, currentQuaternion);
  if (!delta) return 0;
  return delta.rotationVector.dot(motionAxisVector(axis));
}

function handleModelTransformChanged(event) {
  const meshes = event.detail?.meshes;
  if (!Array.isArray(meshes)) return;
  meshes.forEach(mesh => {
    const state = states.get(mesh);
    if (!state?.physicsReferenceQuaternion || !mesh?.quaternion) return;
    const previous = state.physicsReferenceQuaternion.clone();
    const current = mesh.quaternion;
    const rootDelta = shortestQuaternionDelta(previous, current);
    const rootAngularDelta = signedQuaternionDeltaAngle(previous, current);
    const projectedDelta = projectQuaternionDeltaOntoAxis(
      previous, current, state.physicsAxis);
    const translationValues = event.detail?.translationDeltaWorld;
    const translationWorld = Array.isArray(translationValues)
      && translationValues.length >= 3
      ? translationValues.slice(0, 3).map(Number) : null;
    const hasTranslation = translationWorld?.every(Number.isFinite)
      && translationWorld.some(value => Math.abs(value) > 1e-10);
    const translationLocal = hasTranslation
      ? new THREE.Vector3(...translationWorld).applyQuaternion(
        previous.clone().invert()).toArray()
      : null;
    // Keep the reference current even when this event is not active physics
    // input, so ignored transforms cannot accumulate into a later impulse.
    state.physicsReferenceQuaternion.copy(current);
    if (!state.physicsEnabled || state.deformationMode !== 'physics'
        || !state.physicsState || !state.candidateForest) return;
    state.lastRootAngularDelta = rootDelta ? rootAngularDelta : 0;
    state.lastProjectedAngularDelta = projectedDelta;
    state.motionEventCount += 1;
    let physicsChanged = false;
    const angularStrength = Number(state.physicsMotionStrength) || 0;
    if (Math.abs(projectedDelta) >= 1e-10 && angularStrength > 0) {
      applyReferenceFrameAngularDelta(
        state.physicsState, state.candidateForest,
        projectedDelta, angularStrength);
      physicsChanged = true;
    }
    if (hasTranslation) {
      state.lastRootTranslationDeltaWorld = translationWorld;
      state.lastRootTranslationDeltaLocal = translationLocal;
      state.translationEventCount += 1;
      const translationDiagnostics = {};
      applyReferenceFrameTranslationDelta(
        state.physicsState, state.candidateForest, state.centerByBoneId,
        translationLocal, state.physicsAxis,
        state.physicsLinearMotionStrength, translationDiagnostics);
      state.lastTranslationLag = Number(
        translationDiagnostics.maxAbsLag) || 0;
      if (Math.abs(state.lastTranslationLag) >= 1e-10
          && Number(state.physicsLinearMotionStrength) > 0) {
        physicsChanged = true;
      }
    }
    if (!physicsChanged) return;
    state.physicsSettled = false;
    // Render the lag immediately; the fixed-step scheduler owns all recovery
    // frames after this first response.
    applyDeformation(mesh, state);
    wakePhysics(mesh, state);
  });
}

function removePhysicsMesh(mesh) {
  activePhysicsMeshes.delete(mesh);
  if (!activePhysicsMeshes.size && physicsFrameId !== null
      && typeof window !== 'undefined'
      && typeof window.cancelAnimationFrame === 'function') {
    window.cancelAnimationFrame(physicsFrameId);
    physicsFrameId = null;
  }
}

function schedulePhysicsFrame() {
  if (physicsFrameId !== null || !activePhysicsMeshes.size
      || typeof window === 'undefined'
      || typeof window.requestAnimationFrame !== 'function') return;
  physicsFrameId = window.requestAnimationFrame(timestamp => {
    physicsFrameId = null;
    activePhysicsMeshes.forEach(mesh => advancePhysicsMesh(mesh, timestamp));
    if (activePhysicsMeshes.size) schedulePhysicsFrame();
  });
}

export function isPhysicsScheduled(mesh) {
  return activePhysicsMeshes.has(mesh);
}

function stopPhysics(mesh, state) {
  removePhysicsMesh(mesh);
  state.physicsEnabled = false;
  state.physicsState = null;
  state.physicsTransforms = null;
  state.physicsAccumulator = 0;
  state.physicsLastTimestamp = null;
  state.physicsSettled = true;
  state.physicsTargetAngle = 0;
  state.physicsReferenceQuaternion = null;
  clearMotionDiagnostics(state);
  if (state.deformationMode === 'physics') state.deformationMode = null;
}

function wakePhysics(mesh, state) {
  if (!state.physicsEnabled || !state.candidateForest) return false;
  if (!state.physicsState) {
    state.physicsState = initializePhysicsState(state.candidateForest);
  }
  if (!activePhysicsMeshes.has(mesh)) {
    state.physicsAccumulator = 0;
    state.physicsLastTimestamp = null;
  }
  state.physicsSettled = false;
  activePhysicsMeshes.add(mesh);
  schedulePhysicsFrame();
  return true;
}

function advancePhysicsMesh(mesh, timestamp) {
  const state = states.get(mesh);
  if (!state || !state.physicsEnabled
      || state.deformationMode !== 'physics'
      || !state.candidateForest || !state.physicsState) {
    removePhysicsMesh(mesh);
    return;
  }
  const currentTimestamp = Number(timestamp);
  if (!Number.isFinite(currentTimestamp)) {
    removePhysicsMesh(mesh);
    return;
  }
  if (state.physicsLastTimestamp === null) {
    state.physicsLastTimestamp = currentTimestamp;
    return;
  }
  const elapsed = Math.max(0, Math.min(
    MAX_PHYSICS_FRAME_DELTA,
    (currentTimestamp - state.physicsLastTimestamp) / 1000));
  state.physicsLastTimestamp = currentTimestamp;
  state.physicsAccumulator += elapsed;
  let steps = 0;
  while (state.physicsAccumulator >= PHYSICS_STEP
      && steps < MAX_PHYSICS_SUBSTEPS) {
    stepSpringPhysics(
      state.physicsState, state.candidateForest, PHYSICS_STEP, {
        frequencyHz: state.physicsFrequencyHz,
        dampingRatio: state.physicsDampingRatio,
        targetAngleRadians: THREE.MathUtils.degToRad(
          state.physicsTargetAngle),
        maxDt: PHYSICS_STEP,
      });
    state.physicsAccumulator -= PHYSICS_STEP;
    steps += 1;
  }
  if (steps === MAX_PHYSICS_SUBSTEPS
      && state.physicsAccumulator >= PHYSICS_STEP) {
    state.physicsAccumulator = PHYSICS_STEP;
  }
  if (!steps) return;

  // Integrate all fixed substeps first, then update the mesh once per frame.
  applyDeformation(mesh, state);
  state.physicsSettled = isPhysicsSettled(
    state.physicsState, state.candidateForest,
    THREE.MathUtils.degToRad(state.physicsTargetAngle));
  if (state.physicsSettled) {
    state.physicsLastTimestamp = null;
    removePhysicsMesh(mesh);
  }
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

export function parseChainIds(text, validBoneIds) {
  if (typeof text !== 'string' || !text.trim()) {
    throw new Error('A chain requires at least 2 unique bone IDs.');
  }
  const valid = new Set([...validBoneIds || []].map(Number));
  const ids = [];
  for (const raw of text.split(',')) {
    const token = raw.trim();
    if (!/^\d+$/.test(token)) {
      throw new Error('Chain IDs must be comma-separated integer IDs.');
    }
    const id = Number(token);
    if (!Number.isSafeInteger(id)) {
      throw new Error('Chain IDs must be comma-separated integer IDs.');
    }
    if (!valid.has(id)) throw new Error(`Unknown Bone ID: ${id}`);
    if (ids.includes(id)) throw new Error(`Duplicate Bone ID: ${id}`);
    ids.push(id);
  }
  if (ids.length < 2) {
    throw new Error('A chain requires at least 2 unique bone IDs.');
  }
  return ids;
}

function chainMembership(chainIds) {
  return chainIds instanceof Set
    ? chainIds
    : new Set([...chainIds || []].map(Number));
}

function compactVertexCount(indices, weights, influenceCount) {
  if (!indices || !weights || !Number.isInteger(influenceCount)
      || influenceCount <= 0) return 0;
  return Math.floor(Math.min(indices.length, weights.length) / influenceCount);
}

function authoredWeightForVertex(indices, weights, influenceCount, vertexIndex) {
  const start = vertexIndex * influenceCount;
  let total = 0;
  for (let influence = 0; influence < influenceCount; influence += 1) {
    const weight = weights[start + influence];
    if (Number.isFinite(weight) && weight > 0) total += weight;
  }
  return total;
}

function chainWeightForVertexWithMembership(
    indices, weights, influenceCount, vertexIndex, membership) {
  const start = vertexIndex * influenceCount;
  let total = 0;
  for (let influence = 0; influence < influenceCount; influence += 1) {
    const weight = weights[start + influence];
    if (membership.has(Number(indices[start + influence]))
        && Number.isFinite(weight) && weight > 0) total += weight;
  }
  return total;
}

export function chainWeightForVertex(
    indices, weights, influenceCount, vertexIndex, chainIds) {
  if (!indices || !weights || !Number.isInteger(influenceCount)
      || influenceCount <= 0 || !Number.isInteger(vertexIndex)
      || vertexIndex < 0) return 0;
  return chainWeightForVertexWithMembership(
    indices, weights, influenceCount, vertexIndex, chainMembership(chainIds));
}

export function residualWeightForVertex(
    indices, weights, influenceCount, vertexIndex, chainIds) {
  return Math.max(0, 1 - chainWeightForVertex(
    indices, weights, influenceCount, vertexIndex, chainIds));
}

export function computeChainCoverage(
    indices, weights, influenceCount, chainIds, positions = null) {
  const vertexCount = compactVertexCount(indices, weights, influenceCount);
  const membership = chainMembership(chainIds);
  let coverageTotal = 0;
  let minCoverage = vertexCount ? Infinity : 0;
  let maxResidual = 0;
  let totalResidual = 0;
  let residualWeightTotal = 0;
  let residualX = 0;
  let residualY = 0;
  let residualZ = 0;
  let fullyCoveredVertices = 0;
  let covered99Vertices = 0;
  let covered95Vertices = 0;
  let lowCoverageVertices = 0;
  let overweightVertices = 0;
  let underweightVertices = 0;

  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const coverage = chainWeightForVertexWithMembership(
      indices, weights, influenceCount, vertex, membership);
    const residual = Math.max(0, 1 - coverage);
    const authoredWeight = authoredWeightForVertex(
      indices, weights, influenceCount, vertex);
    coverageTotal += coverage;
    minCoverage = Math.min(minCoverage, coverage);
    maxResidual = Math.max(maxResidual, residual);
    totalResidual += residual;
    if (coverage >= FULL_COVERAGE_THRESHOLD) fullyCoveredVertices += 1;
    if (coverage >= COVERAGE_99_THRESHOLD) covered99Vertices += 1;
    if (coverage >= COVERAGE_95_THRESHOLD) covered95Vertices += 1;
    if (coverage < COVERAGE_95_THRESHOLD) lowCoverageVertices += 1;
    if (authoredWeight > AUTHOR_WEIGHT_HIGH_THRESHOLD) overweightVertices += 1;
    if (authoredWeight < AUTHOR_WEIGHT_LOW_THRESHOLD) underweightVertices += 1;
    if (positions && positions.length >= vertex * 3 + 3 && residual > 0) {
      const offset = vertex * 3;
      residualX += positions[offset] * residual;
      residualY += positions[offset + 1] * residual;
      residualZ += positions[offset + 2] * residual;
      residualWeightTotal += residual;
    }
  }

  if (residualWeightTotal > 0) {
    residualX /= residualWeightTotal;
    residualY /= residualWeightTotal;
    residualZ /= residualWeightTotal;
  }
  return {
    vertexCount,
    averageCoverage: vertexCount ? coverageTotal / vertexCount : 0,
    minCoverage,
    maxResidual,
    totalResidual,
    fullyCoveredVertices,
    covered99Vertices,
    covered95Vertices,
    lowCoverageVertices,
    overweightVertices,
    underweightVertices,
    residualCenter: [residualX, residualY, residualZ],
  };
}

export function rankMissingInfluences(
    indices, weights, influenceCount, chainIds, positions = null) {
  const vertexCount = compactVertexCount(indices, weights, influenceCount);
  const membership = chainMembership(chainIds);
  const entries = new Map();
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const chainWeight = chainWeightForVertexWithMembership(
      indices, weights, influenceCount, vertex, membership);
    const residualRegion = chainWeight < COVERAGE_95_THRESHOLD;
    const start = vertex * influenceCount;
    const vertexWeights = new Map();
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const boneId = Number(indices[start + influence]);
      const weight = weights[start + influence];
      if (membership.has(boneId) || !Number.isFinite(weight) || weight <= 0) {
        continue;
      }
      vertexWeights.set(boneId, (vertexWeights.get(boneId) || 0) + weight);
    }
    vertexWeights.forEach((weight, boneId) => {
      const entry = entries.get(boneId) || {
        boneId,
        totalWeight: 0,
        residualContribution: 0,
        affectedVertexCount: 0,
        maxVertexWeight: 0,
      };
      entry.totalWeight += weight;
      entry.affectedVertexCount += 1;
      entry.maxVertexWeight = Math.max(entry.maxVertexWeight, weight);
      if (residualRegion) entry.residualContribution += weight;
      if (positions && positions.length >= vertex * 3 + 3) {
        const offset = vertex * 3;
        entry.centerX = (entry.centerX || 0) + positions[offset] * weight;
        entry.centerY = (entry.centerY || 0) + positions[offset + 1] * weight;
        entry.centerZ = (entry.centerZ || 0) + positions[offset + 2] * weight;
        entry.centerWeight = (entry.centerWeight || 0) + weight;
      }
      entries.set(boneId, entry);
    });
  }
  return [...entries.values()]
    .map(entry => {
      const weightedCenter = entry.centerWeight > 0
        ? [entry.centerX / entry.centerWeight,
          entry.centerY / entry.centerWeight,
          entry.centerZ / entry.centerWeight]
        : [0, 0, 0];
      const {
        centerX, centerY, centerZ, centerWeight, ...publicEntry
      } = entry;
      return {...publicEntry, weightedCenter};
    })
    .sort((a, b) => b.residualContribution - a.residualContribution
      || b.totalWeight - a.totalWeight || a.boneId - b.boneId);
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
        entry.squaredPositionWeight += (x * x + y * y + z * z) * weight;
        entry.positionWeight += weight;
      }
      entries.set(boneId, entry);
    });
  }

  const orderedIds = requested ? [...requested] : [...entries.keys()];
  return orderedIds.filter(boneId => entries.has(boneId)).map(boneId => {
    const entry = entries.get(boneId);
    const nodeCenter = weightedCenter(
      baselinePositions, indices, weights, influenceCount, boneId);
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
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const influences = positiveInfluencesForVertex(
      indices, weights, influenceCount, vertex);
    const ids = [...influences.keys()].filter(id => nodeById.has(id));
    for (let left = 0; left < ids.length; left += 1) {
      for (let right = left + 1; right < ids.length; right += 1) {
        const [boneA, boneB] = pairIds(ids[left], ids[right]);
        const key = pairKey(boneA, boneB);
        const weightA = influences.get(boneA);
        const weightB = influences.get(boneB);
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
  const orderedEdges = [...edges || []].sort((a, b) =>
    treeEdgeScore(b) - treeEdgeScore(a)
      || relationshipSort(a, b));
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

export function weightedCenter(positions, indices, weights, influenceCount,
                               boneId) {
  const center = [0, 0, 0];
  if (!positions || !indices || !weights || influenceCount <= 0) return center;
  let total = 0;
  const vertexCount = Math.floor(positions.length / 3);
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const weight = weightForBone(
      indices, weights, influenceCount, vertex, boneId);
    if (!weight) continue;
    const offset = vertex * 3;
    center[0] += positions[offset] * weight;
    center[1] += positions[offset + 1] * weight;
    center[2] += positions[offset + 2] * weight;
    total += weight;
  }
  if (total > 0) {
    center[0] /= total;
    center[1] /= total;
    center[2] /= total;
  }
  return center;
}

function previewError(result) {
  const code = result?.code;
  return new Error(ERROR_MESSAGES[code] || result?.error
    || 'Could not load skin weights.');
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
  state.centers.clear();
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

function vectorFromCenter(center) {
  if (center?.isVector3) return center.clone();
  return new THREE.Vector3(
    Number(center?.[0]) || 0,
    Number(center?.[1]) || 0,
    Number(center?.[2]) || 0,
  );
}

function centerFor(mesh, state) {
  return centerForBone(mesh, state, state.selectedBone);
}

function centerForBone(mesh, state, boneId) {
  if (!state.centers.has(boneId)) {
    state.centers.set(boneId, weightedCenter(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, boneId));
  }
  return state.centers.get(boneId);
}

function chainCentersFor(mesh, state) {
  return state.chainIds.map(boneId => centerForBone(mesh, state, boneId));
}

function refreshChainCoverage(state) {
  if (state.chainIds.length < 2) {
    state.chainCoverage = null;
    state.missingInfluences = [];
    return;
  }
  state.chainCoverage = computeChainCoverage(
    state.indices, state.weights, state.influenceCount, state.chainIds,
    state.baselinePositions);
  state.missingInfluences = rankMissingInfluences(
    state.indices, state.weights, state.influenceCount, state.chainIds,
    state.baselinePositions);
}

function removeVirtualChainHelpers(mesh, state) {
  const helpers = state?.chainHelpers;
  if (!helpers) {
    if (activeExperimentHelperMesh === mesh
        && !state?.influenceVisualization) {
      activeExperimentHelperMesh = null;
    }
    if (state) state.chainHelpersVisible = false;
    return;
  }
  helpers.group.removeFromParent();
  helpers.markerGeometry.dispose();
  helpers.markerMaterial.dispose();
  helpers.lineGeometry.dispose();
  helpers.lineMaterial.dispose();
  state.chainHelpers = null;
  state.chainHelpersVisible = false;
  if (activeExperimentHelperMesh === mesh
      && !state.influenceVisualization) {
    activeExperimentHelperMesh = null;
  }
}

function removeExperimentHelpers(mesh, state) {
  if (!state) return;
  removeVirtualChainHelpers(mesh, state);
  removeInfluenceVisualization(state);
  if (activeExperimentHelperMesh === mesh) {
    activeExperimentHelperMesh = null;
  }
}

function activateExperimentHelpers(mesh) {
  if (activeExperimentHelperMesh && activeExperimentHelperMesh !== mesh) {
    const activeState = states.get(activeExperimentHelperMesh);
    removeExperimentHelpers(activeExperimentHelperMesh, activeState);
  }
  activeExperimentHelperMesh = mesh;
}

function activateVirtualChain(mesh) {
  activateExperimentHelpers(mesh);
}

function createVirtualChainHelpers(mesh, state) {
  const radius = Math.max(
    (mesh.geometry.boundingSphere?.radius || 1) * 0.012, 0.001);
  const group = new THREE.Group();
  group.name = 'Experimental Virtual Chain';
  const markerGeometry = new THREE.SphereGeometry(1, 8, 6);
  const markerMaterial = new THREE.MeshBasicMaterial({color: 0xffb86c});
  const markers = state.chainIds.map(boneId => {
    const marker = new THREE.Mesh(markerGeometry, markerMaterial);
    marker.scale.setScalar(radius);
    marker.userData.virtualBoneId = boneId;
    group.add(marker);
    return marker;
  });
  const lineGeometry = new THREE.BufferGeometry();
  lineGeometry.setAttribute('position', new THREE.BufferAttribute(
    new Float32Array(state.chainIds.length * 3), 3));
  const lineMaterial = new THREE.LineBasicMaterial({color: 0xff7b72});
  const line = new THREE.Line(lineGeometry, lineMaterial);
  group.add(line);
  mesh.add(group);
  state.chainHelpers = {
    group, markers, line, markerGeometry, markerMaterial,
    lineGeometry, lineMaterial,
  };
  return state.chainHelpers;
}

function updateVirtualChainHelpers(mesh, state) {
  const helpers = state.chainHelpers;
  if (!helpers || state.chainIds.length < 2) return;
  const centers = chainCentersFor(mesh, state);
  const transforms = buildChainTransforms(
    centers, state.chainAxis, state.chainAngle);
  const linePositions = helpers.line.geometry.attributes.position;
  centers.forEach((center, index) => {
    const point = vectorFromCenter(center).applyMatrix4(transforms[index]);
    helpers.markers[index].position.copy(point);
    linePositions.setXYZ(index, point.x, point.y, point.z);
  });
  linePositions.needsUpdate = true;
  helpers.line.geometry.computeBoundingSphere();
}

function transformedInfluenceCenter(node, state, mode) {
  const point = vectorFromCenter(node.weightedCenter);
  if (mode === 'tree') {
    const transforms = state.deformationMode === 'forest'
      ? state.forestTransforms
      : state.deformationMode === 'physics'
        ? state.physicsTransforms : null;
    const transform = transforms?.get(node.boneId);
    if (transform) point.applyMatrix4(transform);
  }
  return point;
}

function buildInfluenceGraph(mesh, state) {
  if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
  const radius = Number(mesh.geometry.boundingSphere?.radius);
  const nodes = buildInfluenceNodes(
    state.baselinePositions, state.indices, state.weights,
    state.influenceCount, state.boneIds);
  const relationships = buildInfluenceRelationships(
    state.indices, state.weights, state.influenceCount, nodes,
    Number.isFinite(radius) && radius > 0 ? radius : null);
  return {
    nodes,
    relationships,
    boundingSphereRadius: Number.isFinite(radius) && radius > 0 ? radius : null,
  };
}

function visualGraphRelationships(graph) {
  const byNode = new Map();
  (graph?.relationships || []).forEach(relationship => {
    if (relationship.sharedVertexCount <= 0
        || relationship.containment < INFLUENCE_GRAPH_CONTAINMENT_THRESHOLD) {
      return;
    }
    [relationship.boneA, relationship.boneB].forEach(boneId => {
      const relationships = byNode.get(boneId) || [];
      relationships.push(relationship);
      byNode.set(boneId, relationships);
    });
  });
  const selected = new Map();
  byNode.forEach(relationships => {
    relationships.sort(relationshipSort)
      .slice(0, INFLUENCE_GRAPH_TOP_K)
      .forEach(relationship => {
        selected.set(pairKey(relationship.boneA, relationship.boneB),
          relationship);
      });
  });
  return [...selected.values()].sort(relationshipSort);
}

function relationshipStrength(relationship) {
  return Math.max(0, Math.min(1, Number(relationship?.containment) || 0));
}

function removeInfluenceVisualization(state) {
  const visualization = state.influenceVisualization;
  if (!visualization) {
    state.influenceVisualizationMode = null;
    return;
  }
  visualization.group.removeFromParent();
  visualization.nodeGeometry.dispose();
  visualization.nodeMaterial.dispose();
  visualization.lineGeometries.forEach(geometry => geometry.dispose());
  visualization.lineMaterials.forEach(material => material.dispose());
  state.influenceVisualization = null;
  state.influenceVisualizationMode = null;
  if (activeExperimentHelperMesh === visualization.mesh
      && !state.chainHelpers) {
    activeExperimentHelperMesh = null;
  }
}

function createInfluenceVisualization(mesh, state, mode) {
  removeInfluenceVisualization(state);
  activateExperimentHelpers(mesh);
  const graph = state.influenceGraph;
  if (!graph || (mode !== 'graph' && mode !== 'tree')) return null;
  const nodeById = new Map(graph.nodes.map(node => [node.boneId, node]));
  const componentRoots = new Set((state.candidateForest?.components || [])
    .map(component => component.rootId));
  const radius = Math.max(
    (mesh.geometry.boundingSphere?.radius || 1) * 0.012, 0.001);
  const group = new THREE.Group();
  group.name = mode === 'graph'
    ? 'Experimental Influence Graph' : 'Experimental Candidate Tree';
  const nodeGeometry = new THREE.SphereGeometry(1, 8, 6);
  const nodeMaterial = new THREE.MeshBasicMaterial({color: 0xffb86c});
  const nodeMarkers = new Map();
  graph.nodes.forEach(node => {
    const marker = new THREE.Mesh(nodeGeometry, nodeMaterial);
    marker.scale.setScalar(radius * (componentRoots.has(node.boneId) ? 1.35 : 1));
    marker.position.copy(transformedInfluenceCenter(node, state, mode));
    marker.userData.influenceBoneId = node.boneId;
    marker.userData.influenceComponentRoot = componentRoots.has(node.boneId);
    group.add(marker);
    nodeMarkers.set(node.boneId, marker);
  });

  const relationships = mode === 'graph'
    ? visualGraphRelationships(graph)
    : state.candidateTree?.edges || [];
  const lineGeometries = [];
  const lineMaterials = [];
  const lineEntries = [];
  relationships.forEach(relationship => {
    const nodeA = nodeById.get(Number(relationship.boneA));
    const nodeB = nodeById.get(Number(relationship.boneB));
    if (!nodeA || !nodeB) return;
    const position = new Float32Array([
      ...transformedInfluenceCenter(nodeA, state, mode).toArray(),
      ...transformedInfluenceCenter(nodeB, state, mode).toArray(),
    ]);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
    const material = new THREE.LineBasicMaterial({
      color: 0xff7b72,
      transparent: true,
      opacity: 0.2 + 0.7 * relationshipStrength(relationship),
    });
    const line = new THREE.Line(geometry, material);
    line.userData.influenceBoneA = relationship.boneA;
    line.userData.influenceBoneB = relationship.boneB;
    line.userData.relationshipStrength = relationshipStrength(relationship);
    group.add(line);
    lineGeometries.push(geometry);
    lineMaterials.push(material);
    lineEntries.push({line, relationship});
  });
  mesh.add(group);
  state.influenceVisualization = {
    mesh, group, nodeGeometry, nodeMaterial, nodeMarkers,
    lineGeometries, lineMaterials, lineEntries,
  };
  state.influenceVisualizationMode = mode;
  return state.influenceVisualization;
}

function updateInfluenceVisualization(mesh, state) {
  const visualization = state.influenceVisualization;
  if (!visualization || !state.influenceVisualizationMode) return;
  const mode = state.influenceVisualizationMode;
  const graph = state.influenceGraph;
  const nodeById = new Map((graph?.nodes || [])
    .map(node => [node.boneId, node]));
  visualization.nodeMarkers?.forEach((marker, boneId) => {
    const node = nodeById.get(boneId);
    if (node) marker.position.copy(
      transformedInfluenceCenter(node, state, mode));
  });
  visualization.lineEntries?.forEach(({line, relationship}) => {
    const nodeA = nodeById.get(Number(relationship.boneA));
    const nodeB = nodeById.get(Number(relationship.boneB));
    if (!nodeA || !nodeB) return;
    const positions = line.geometry.attributes.position;
    const centerA = transformedInfluenceCenter(nodeA, state, mode);
    const centerB = transformedInfluenceCenter(nodeB, state, mode);
    positions.setXYZ(0, centerA.x, centerA.y, centerA.z);
    positions.setXYZ(1, centerB.x, centerB.y, centerB.z);
    positions.needsUpdate = true;
    line.geometry.computeBoundingSphere();
  });
}

function refreshAfterForestTopologyChange(mesh, state) {
  if (state.physicsEnabled && state.deformationMode === 'physics') {
    state.physicsState = initializePhysicsState(state.candidateForest);
    state.physicsReferenceQuaternion = mesh.quaternion.clone();
    clearMotionDiagnostics(state);
    state.physicsTransforms = null;
    state.physicsAccumulator = 0;
    state.physicsLastTimestamp = null;
    state.physicsSettled = false;
    activePhysicsMeshes.add(mesh);
    schedulePhysicsFrame();
  }
  if (state.influenceVisualizationMode) {
    createInfluenceVisualization(
      mesh, state, state.influenceVisualizationMode);
  }
  if (state.physicsEnabled && state.deformationMode === 'physics') {
    applyDeformation(mesh, state);
  } else if (state.deformationMode === 'forest') {
    applyDeformation(mesh, state);
  } else {
    requestRender();
  }
}

export function buildCandidateTree(mesh, rootId = null) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.influenceGraph) return null;
  const requestedRoot = Number(
    rootId ?? state.candidateRootId ?? state.selectedBone
      ?? state.boneIds[0]);
  const root = state.boneIds.includes(requestedRoot)
    ? requestedRoot : state.boneIds[0];
  const candidateEdges = candidateRelationshipEdges(state.influenceGraph);
  const tree = buildMaximumSpanningTree(
    state.influenceGraph.nodes, candidateEdges);
  state.candidateRootId = root ?? null;
  const forest = orientForest(
    state.influenceGraph.nodes, tree.edges, state.candidateRootId,
    {components: tree.components});
  state.candidateForest = forest;
  const primaryComponent = forest.components[forest.primaryComponentId];
  state.candidateTree = {
    ...tree,
    rootId: state.candidateRootId,
    candidateEdges,
    orientation: primaryComponent ? {
      rootId: primaryComponent.rootId,
      parentById: primaryComponent.parentById,
      childrenById: primaryComponent.childrenById,
      depthById: primaryComponent.depthById,
    } : orientTree(tree.edges, state.candidateRootId),
    forest,
  };
  refreshAfterForestTopologyChange(mesh, state);
  return state.candidateTree;
}

export function setCandidateTreeRoot(mesh, rootId) {
  const state = stateFor(mesh);
  const id = Number(rootId);
  if (!state?.loaded || !state.influenceGraph
      || !state.boneIds.includes(id)) return false;
  state.candidateRootId = id;
  if (state.candidateTree) {
    state.candidateTree.rootId = id;
    const forest = orientForest(
      state.influenceGraph.nodes, state.candidateTree.edges, id,
      {components: state.candidateTree.components});
    state.candidateForest = forest;
    state.candidateTree.forest = forest;
    const primaryComponent = forest.components[forest.primaryComponentId];
    state.candidateTree.orientation = primaryComponent ? {
      rootId: primaryComponent.rootId,
      parentById: primaryComponent.parentById,
      childrenById: primaryComponent.childrenById,
      depthById: primaryComponent.depthById,
    } : orientTree(state.candidateTree.edges, id);
  }
  refreshAfterForestTopologyChange(mesh, state);
  return id;
}

export function setInfluenceVisualizationMode(mesh, mode) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.influenceGraph) return null;
  if (mode !== null && mode !== 'graph' && mode !== 'tree') {
    return state.influenceVisualizationMode;
  }
  if (mode === 'tree' && !state.candidateTree) {
    return state.influenceVisualizationMode;
  }
  if (mode === null) removeInfluenceVisualization(state);
  else createInfluenceVisualization(mesh, state, mode);
  requestRender();
  return state.influenceVisualizationMode;
}

export function setInfluenceGraphVisible(mesh, visible) {
  return setInfluenceVisualizationMode(mesh, visible ? 'graph' : null)
    === 'graph';
}

export function setCandidateTreeVisible(mesh, visible) {
  return setInfluenceVisualizationMode(mesh, visible ? 'tree' : null)
    === 'tree';
}

if (typeof window !== 'undefined') {
  window.addEventListener('mod-viewer-model-transform-changed',
    handleModelTransformChanged);
  window.addEventListener('mod-viewer-mesh-selected', event => {
    const mesh = event.detail?.mesh || null;
    if (activeExperimentHelperMesh && activeExperimentHelperMesh !== mesh) {
      const previous = activeExperimentHelperMesh;
      removeExperimentHelpers(previous, states.get(previous));
    }
  });
}

function applyDeformation(mesh, state) {
  if (!state.loaded || !state.baselinePositions) return;
  const position = mesh.geometry.attributes.position;
  const chainActive = state.deformationMode === 'chain'
    && state.chainAngle !== 0 && state.chainIds.length >= 2;
  const forestActive = state.deformationMode === 'forest'
    && state.forestAngle !== 0 && state.candidateForest;
  const physicsActive = state.deformationMode === 'physics'
    && state.physicsEnabled && state.physicsState && state.candidateForest;
  let result;
  if (physicsActive) {
    state.physicsTransforms = buildForestTransformsFromLocalAngles(
      state.candidateForest, state.centerByBoneId, {
        axis: state.physicsAxis,
        angleByBoneId: physicsAngleMap(state.physicsState),
      });
    state.forestTransforms = null;
    result = applyWeightedTransformDeformation(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, state.physicsTransforms);
  } else if (forestActive) {
    state.forestTransforms = buildForestTransforms(
      state.candidateForest,
      state.centerByBoneId,
      {axis: state.forestAxis, totalAngle: state.forestAngle});
    result = applyWeightedTransformDeformation(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, state.forestTransforms);
  } else if (chainActive) {
    state.forestTransforms = null;
    state.physicsTransforms = null;
    result = applyWeightedChainDeformation(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, state.chainIds, buildChainTransforms(
        chainCentersFor(mesh, state), state.chainAxis, state.chainAngle));
  } else if (state.deformationMode === 'single' && state.angle !== 0) {
    state.forestTransforms = null;
    state.physicsTransforms = null;
    result = applyWeightedRotation(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, state.selectedBone, centerFor(mesh, state),
      state.axis, state.angle);
  } else {
    state.forestTransforms = null;
    state.physicsTransforms = null;
    result = state.baselinePositions;
  }
  position.array.set(result);
  position.needsUpdate = true;
  if (!chainActive && !forestActive && !physicsActive && state.angle === 0) {
    restoreNormals(mesh, state);
  } else {
    mesh.geometry.computeVertexNormals();
    mesh.geometry.attributes.normal.needsUpdate = true;
  }
  updateVirtualChainHelpers(mesh, state);
  updateInfluenceVisualization(mesh, state);
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  invalidateCharacterShadowGeometry();
  requestRender();
}

function updateHeatmap(mesh, state) {
  if (!state.heatmapMode) return;
  const count = Math.floor(state.indices.length / state.influenceCount);
  const colors = new Float32Array(count * 3);
  for (let vertex = 0; vertex < count; vertex += 1) {
    const value = state.heatmapMode === 'chain-residual'
      ? residualWeightForVertex(
        state.indices, state.weights, state.influenceCount, vertex,
        state.chainIds)
      : Math.max(0, Math.min(1, weightForBone(
        state.indices, state.weights, state.influenceCount,
        vertex, state.selectedBone)));
    const offset = vertex * 3;
    if (state.heatmapMode === 'chain-residual') {
      // Keep covered vertices dark while making omitted influence obvious.
      colors[offset] = value;
      colors[offset + 1] = value * 0.25;
      colors[offset + 2] = 0.02;
    } else {
      // Blue at zero, yellow/red at high influence for quick spatial reading.
      colors[offset] = value;
      colors[offset + 1] = Math.min(1, value * 2);
      colors[offset + 2] = 1 - value;
    }
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

function disableHeatmap(mesh, state) {
  state.heatmapMode = null;
  mesh.material = state.originalMaterial;
  mesh.geometry.deleteAttribute('color');
  if (state.debugMaterial) {
    state.debugMaterial.dispose();
    state.debugMaterial = null;
  }
}

export async function loadSkinningWeights(mesh) {
  const state = stateFor(mesh);
  if (!state) throw new Error('No mesh was selected.');
  if (state.loaded) return state;
  if (state.promise) return state.promise;

  state.loading = true;
  state.error = null;
  state.promise = (async () => {
    captureBaseline(mesh, state);
    const api = window.pywebview?.api?.get_skinning_preview;
    if (typeof api !== 'function') {
      throw new Error('Skin-weight preview is unavailable.');
    }
    const preview = await api(
      mesh.userData.modPath, mesh.userData.semanticKey);
    if (!preview || preview.status !== 'ok') throw previewError(preview);
    const response = await fetch(preview.data?.url, {cache: 'no-store'});
    if (!response.ok) {
      throw new Error(`Skin data download failed (${response.status}).`);
    }
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength !== Number(preview.data.length)) {
      throw new Error('Skin data download was incomplete.');
    }
    const indices = typedView(
      buffer, preview.data.indices, Uint32Array, 'u32');
    const weights = typedView(
      buffer, preview.data.weights, Float32Array, 'f32');
    if (states.get(mesh) !== state) {
      throw new Error('The skin-weight experiment was reset.');
    }
    const positionCount = mesh.geometry.attributes.position.count;
    if (positionCount !== Number(preview.vertex_count)) {
      throw new Error(
        `Skin data does not match rendered vertices. Expected ${positionCount.toLocaleString()}, received ${Number(preview.vertex_count).toLocaleString()}.`);
    }
    const influenceCount = Number(preview.influence_count);
    if (!Number.isInteger(influenceCount) || influenceCount <= 0
        || indices.length !== positionCount * influenceCount
        || weights.length !== positionCount * influenceCount) {
      throw new Error('Skin data does not match rendered vertices.');
    }
    state.indices = indices;
    state.weights = weights;
    state.influenceCount = influenceCount;
    state.boneIds = Array.isArray(preview.bone_ids)
      ? [...preview.bone_ids].map(Number).filter(Number.isFinite)
        .sort((a, b) => a - b)
      : buildBoneIds(indices, weights, influenceCount);
    state.selectedBone = state.boneIds[0] ?? 0;
    state.encoding = preview.encoding || null;
    state.diagnostics = preview.diagnostics || null;
    state.loaded = true;
    refreshChainCoverage(state);
    state.influenceGraph = buildInfluenceGraph(mesh, state);
    state.centerByBoneId = new Map(state.influenceGraph.nodes.map(node => [
      node.boneId, node.weightedCenter]));
    state.candidateRootId = state.boneIds[0] ?? null;
    return state;
  })();
  try {
    return await state.promise;
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
    throw error;
  } finally {
    state.loading = false;
    state.promise = null;
  }
}

export function setSelectedBone(mesh, boneId) {
  const state = stateFor(mesh);
  if (!state?.loaded) return;
  state.selectedBone = Number(boneId);
  if (state.heatmapMode === 'bone') updateHeatmap(mesh, state);
  applyDeformation(mesh, state);
}

export function setSkinningAxis(mesh, axis) {
  const state = stateFor(mesh);
  if (!state?.loaded || !['X', 'Y', 'Z'].includes(axis)) return;
  if (state.physicsEnabled) stopPhysics(mesh, state);
  state.axis = axis;
  applyDeformation(mesh, state);
}

export function setSkinningAngle(mesh, angle) {
  const state = stateFor(mesh);
  if (!state?.loaded) return;
  if (state.physicsEnabled) stopPhysics(mesh, state);
  state.angle = Math.max(-45, Math.min(45, Number(angle) || 0));
  state.chainAngle = 0;
  state.forestAngle = 0;
  state.deformationMode = state.angle === 0 ? null : 'single';
  if (state.chainHelpers) removeVirtualChainHelpers(mesh, state);
  applyDeformation(mesh, state);
}

export function setSkinningChainText(mesh, text) {
  const state = stateFor(mesh);
  if (!state?.loaded) return false;
  if (state.physicsEnabled) stopPhysics(mesh, state);
  state.chainText = String(text ?? '');
  if (state.deformationMode === 'forest') {
    state.forestAngle = 0;
    state.deformationMode = null;
  }
  const wasVisible = state.chainHelpersVisible;
  try {
    state.chainIds = parseChainIds(state.chainText, state.boneIds);
    state.chainError = null;
    state.angle = 0;
  } catch (error) {
    state.chainIds = [];
    state.chainAngle = 0;
    state.chainError = error instanceof Error ? error.message : String(error);
    refreshChainCoverage(state);
    if (state.heatmapMode === 'chain-residual') disableHeatmap(mesh, state);
    removeVirtualChainHelpers(mesh, state);
    applyDeformation(mesh, state);
    return false;
  }
  refreshChainCoverage(state);
  if (state.chainHelpers) {
    removeVirtualChainHelpers(mesh, state);
    if (wasVisible) {
      activateVirtualChain(mesh);
      createVirtualChainHelpers(mesh, state);
      state.chainHelpersVisible = true;
      updateVirtualChainHelpers(mesh, state);
    }
  }
  applyDeformation(mesh, state);
  if (state.heatmapMode) updateHeatmap(mesh, state);
  return true;
}

export function setSkinningChainAxis(mesh, axis) {
  const state = stateFor(mesh);
  if (!state?.loaded || !['X', 'Y', 'Z'].includes(axis)) return;
  if (state.physicsEnabled) stopPhysics(mesh, state);
  state.chainAxis = axis;
  state.angle = 0;
  state.forestAngle = 0;
  if (state.deformationMode === 'forest') state.deformationMode = null;
  applyDeformation(mesh, state);
}

export function setSkinningChainAngle(mesh, angle) {
  const state = stateFor(mesh);
  if (!state?.loaded || state.chainIds.length < 2) return false;
  if (state.physicsEnabled) stopPhysics(mesh, state);
  state.chainAngle = Math.max(-60, Math.min(60, Number(angle) || 0));
  state.angle = 0;
  state.forestAngle = 0;
  state.deformationMode = state.chainAngle === 0 ? null : 'chain';
  applyDeformation(mesh, state);
  return true;
}

export function setForestAxis(mesh, axis) {
  const state = stateFor(mesh);
  if (!state?.loaded || !['X', 'Y', 'Z'].includes(axis)) return;
  if (state.physicsEnabled) stopPhysics(mesh, state);
  state.forestAxis = axis;
  applyDeformation(mesh, state);
}

export function setForestAngle(mesh, angle) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.candidateForest) return false;
  if (state.physicsEnabled) stopPhysics(mesh, state);
  state.forestAngle = Math.max(-60, Math.min(60, Number(angle) || 0));
  state.angle = 0;
  state.chainAngle = 0;
  state.deformationMode = state.forestAngle === 0 ? null : 'forest';
  applyDeformation(mesh, state);
  return true;
}

export function setPhysicsAxis(mesh, axis) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.candidateForest
      || !['X', 'Y', 'Z'].includes(axis)) return;
  state.physicsAxis = axis;
  if (state.physicsEnabled) applyDeformation(mesh, state);
}

export function setPhysicsTargetAngle(mesh, angle) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.candidateForest) return false;
  state.physicsTargetAngle = Math.max(
    -40, Math.min(40, Number(angle) || 0));
  if (state.physicsEnabled) {
    applyDeformation(mesh, state);
    wakePhysics(mesh, state);
  }
  return true;
}

export function setPhysicsFrequency(mesh, frequencyHz) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.candidateForest) return false;
  const value = Number(frequencyHz);
  if (!Number.isFinite(value)) return false;
  state.physicsFrequencyHz = Math.max(0.1, Math.min(10, value));
  if (state.physicsEnabled) wakePhysics(mesh, state);
  return true;
}

export function setPhysicsDamping(mesh, dampingRatio) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.candidateForest) return false;
  const value = Number(dampingRatio);
  if (!Number.isFinite(value)) return false;
  state.physicsDampingRatio = Math.max(0, Math.min(2, value));
  if (state.physicsEnabled) wakePhysics(mesh, state);
  return true;
}

export function setPhysicsMotionStrength(mesh, strength) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.candidateForest) return false;
  const value = Number(strength);
  if (!Number.isFinite(value)) return false;
  state.physicsMotionStrength = Math.max(0, Math.min(1, value));
  return true;
}

export function setPhysicsLinearMotionStrength(mesh, strength) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.candidateForest) return false;
  const value = Number(strength);
  if (!Number.isFinite(value)) return false;
  state.physicsLinearMotionStrength = Math.max(0, Math.min(1, value));
  return true;
}

export function setPhysicsEnabled(mesh, enabled) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.candidateForest) return false;
  if (!enabled) {
    stopPhysics(mesh, state);
    applyDeformation(mesh, state);
    return false;
  }
  state.angle = 0;
  state.chainAngle = 0;
  state.forestAngle = 0;
  if (state.chainHelpers) removeVirtualChainHelpers(mesh, state);
  state.deformationMode = 'physics';
  state.physicsEnabled = true;
  state.physicsReferenceQuaternion = mesh.quaternion.clone();
  clearMotionDiagnostics(state);
  state.physicsState = initializePhysicsState(state.candidateForest);
  state.physicsTransforms = null;
  state.physicsAccumulator = 0;
  state.physicsLastTimestamp = null;
  state.physicsSettled = false;
  applyDeformation(mesh, state);
  wakePhysics(mesh, state);
  return true;
}

export function applyPhysicsKick(mesh, direction = 1) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.physicsEnabled
      || state.deformationMode !== 'physics' || !state.candidateForest) {
    return false;
  }
  if (!state.physicsState) {
    state.physicsState = initializePhysicsState(state.candidateForest);
  }
  const sign = Number(direction) < 0 ? -1 : 1;
  applyPhysicsKickState(
    state.physicsState, state.candidateForest, sign * PHYSICS_KICK_SPEED);
  wakePhysics(mesh, state);
  return true;
}

export function resetPhysicsMotion(mesh) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.physicsEnabled
      || state.deformationMode !== 'physics') return false;
  resetPhysicsState(state.physicsState);
  state.physicsTargetAngle = 0;
  state.physicsReferenceQuaternion = mesh.quaternion.clone();
  clearMotionDiagnostics(state);
  state.physicsTransforms = null;
  state.physicsAccumulator = 0;
  state.physicsLastTimestamp = null;
  state.physicsSettled = true;
  removePhysicsMesh(mesh);
  applyDeformation(mesh, state);
  return true;
}

export function setVirtualChainVisible(mesh, visible) {
  const state = stateFor(mesh);
  if (!state?.loaded) return false;
  if (visible && state.chainIds.length < 2) return false;
  if (!visible) {
    removeVirtualChainHelpers(mesh, state);
  } else {
    activateVirtualChain(mesh);
    if (!state.chainHelpers) createVirtualChainHelpers(mesh, state);
    updateVirtualChainHelpers(mesh, state);
    state.chainHelpersVisible = true;
  }
  requestRender();
  return state.chainHelpersVisible;
}

export function setSkinningHeatmapMode(mesh, mode) {
  const state = stateFor(mesh);
  if (!state?.loaded) return false;
  if (mode !== null && mode !== 'bone' && mode !== 'chain-residual') {
    return state.heatmapMode;
  }
  if (mode === 'chain-residual'
      && (!state.chainCoverage || state.chainIds.length < 2)) {
    return state.heatmapMode;
  }
  state.heatmapMode = mode;
  if (state.heatmapMode) updateHeatmap(mesh, state);
  else disableHeatmap(mesh, state);
  requestRender();
  return state.heatmapMode;
}

export function setSkinningHeatmap(mesh, enabled) {
  return setSkinningHeatmapMode(mesh, enabled ? 'bone' : null) === 'bone';
}

export function resetSkinningExperiment(mesh) {
  const state = stateFor(mesh);
  if (!state?.loaded) return;
  if (state.physicsEnabled) stopPhysics(mesh, state);
  state.angle = 0;
  state.chainAngle = 0;
  state.forestAngle = 0;
  state.deformationMode = null;
  state.forestTransforms = null;
  state.physicsTransforms = null;
  removeVirtualChainHelpers(mesh, state);
  removeInfluenceVisualization(state);
  applyDeformation(mesh, state);
  if (state.heatmapMode || state.debugMaterial) disableHeatmap(mesh, state);
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  invalidateCharacterShadowGeometry();
  requestRender();
}

export function disposeSkinningExperiment(mesh) {
  const state = states.get(mesh);
  if (!state) return;
  state.disposed = true;
  removePhysicsMesh(mesh);
  removeVirtualChainHelpers(mesh, state);
  removeInfluenceVisualization(state);
  if (state.debugMaterial) state.debugMaterial.dispose();
  mesh.geometry?.deleteAttribute?.('color');
  mesh.material = state.originalMaterial || mesh.material;
  states.delete(mesh);
}
