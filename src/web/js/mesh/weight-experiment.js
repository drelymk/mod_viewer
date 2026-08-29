// Explicit, removable skin-weight experiment. Normal mesh loading never
// imports or invokes this module's bridge operation until the Weight tab asks.

import * as THREE from 'three';
import {
  getModelTransformState, invalidateCharacterShadowGeometry,
  setPhysicsInteractionEnabled,
} from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';
import {
  buildForestTransformsFromLocalRotations, applyWeightedTransformDeformation,
} from './weight-deformation.js';
import {
  GRAVITY_WORLD_DIRECTION, MIN_GRAVITY_LEVER_RATIO, STANDARD_GRAVITY,
  applyReferenceFrameAngularDelta,
  applyReferenceFrameLinearVelocityDelta,
  applyReferenceFrameTranslationDelta,
  applyPhysicsJointLimits, initializePhysicsState,
  buildGravityAngularAccelerations, buildPhysicsConstraintDiagnostics,
  buildPhysicsJointLimits,
  isPhysicsSettled, physicsRotationMap, resetPhysicsState,
  stepSpringPhysics,
} from './weight-physics.js';
import {
  buildSelectedWeightMask, normalizeSelectedBoneIds,
} from './weight-selection.js';
import {
  MODEL_PHYSICS_STEP, createModelPhysicsSession,
} from './model-physics-session.js';
export {
  buildForestTransformsFromLocalRotations, applyWeightedTransformDeformation,
} from './weight-deformation.js';

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
let activeExperimentHelperMesh = null;
let modelWeightGeneration = 0;
let selectedWeightMaskBuildCount = 0;
const modelWeightState = {
  loaded: false,
  loading: false,
  promise: null,
  error: null,
  noWeights: false,
  availableBoneIds: [],
  selectedBoneIds: new Set(),
  heatmapEnabled: false,
  loadedMeshCount: 0,
  failedMeshCount: 0,
};

const modelPhysicsSession = createModelPhysicsSession({
  onInputOwnershipChanged: enabled => setPhysicsInteractionEnabled(enabled),
  onFrame: ({visibleParticipants}) => {
    if (!visibleParticipants?.length) return;
    invalidateCharacterShadowGeometry({request: false});
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
    deformationMode: null,
    physicsEnabled: false,
    physicsJointLimits: null,
    physicsConstraintDiagnostics: null,
    physicsGravityLocal: [...GRAVITY_WORLD_DIRECTION],
    physicsGravityAccelerations: null,
    physicsGravityDiagnostics: null,
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
    physicsSettled: true,
    physicsParticipantStatus: 'not-attempted',
    physicsParticipantError: null,
    selectedWeightMask: null,
    influenceNodes: null,
    influenceGraph: null,
    candidateRootId: null,
    candidateTree: null,
    candidateForest: null,
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
    physicsCenterByBoneId: null,
    physicsForest: null,
    physicsSelectionKey: '',
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
  return {
    loaded: modelWeightState.loaded,
    loading: modelWeightState.loading,
    generation: modelWeightGeneration,
    error: modelWeightState.error,
    noWeights: modelWeightState.noWeights,
    availableBoneIds: [...modelWeightState.availableBoneIds],
    selectedBoneIds: normalizeSelectedBoneIds(
      modelWeightState.selectedBoneIds),
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

function eligibleSkinningMesh(mesh) {
  return mesh?.userData?.skinningAvailable === true
    && mesh.userData?.assetFill !== true
    && !!mesh.userData?.modPath
    && !!mesh.userData?.semanticKey;
}

function refreshModelWeightSummary() {
  const ids = new Set();
  let loadedMeshCount = 0;
  let failedMeshCount = 0;
  knownMeshes.forEach(mesh => {
    const state = states.get(mesh);
    if (state?.loaded) {
      loadedMeshCount += 1;
      (state.boneIds || []).forEach(id => ids.add(Number(id)));
    } else if (state?.error) {
      failedMeshCount += 1;
    }
  });
  modelWeightState.availableBoneIds = [...ids]
    .filter(Number.isFinite).sort((left, right) => left - right);
  modelWeightState.loadedMeshCount = loadedMeshCount;
  modelWeightState.failedMeshCount = failedMeshCount;
  if (modelWeightState.loaded) {
    modelWeightState.selectedBoneIds = new Set(
      normalizeSelectedBoneIds(modelWeightState.selectedBoneIds)
        .filter(id => ids.has(id)));
  }
}

function refreshSelectedWeightMask(mesh, state) {
  if (!state?.loaded) return null;
  selectedWeightMaskBuildCount += 1;
  state.selectedWeightMask = buildSelectedWeightMask(
    state.indices, state.weights, state.influenceCount,
    modelWeightState.selectedBoneIds);
  state.selectedBone = normalizeSelectedBoneIds(
    modelWeightState.selectedBoneIds)[0] ?? null;
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
  modelWeightState.loaded = false;
  modelWeightState.loading = false;
  modelWeightState.promise = null;
  modelWeightState.error = null;
  modelWeightState.noWeights = false;
  modelWeightState.availableBoneIds = [];
  modelWeightState.selectedBoneIds = new Set();
  modelWeightState.heatmapEnabled = false;
  modelWeightState.loadedMeshCount = 0;
  modelWeightState.failedMeshCount = 0;
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

function refreshParticipantDerivedState(mesh, state, settings) {
  if (!state.physicsForest) return;
  refreshSelectedWeightMask(mesh, state);
  refreshConstraintState(state, settings);
  refreshGravityState(mesh, state, settings);
}

function createPhysicsParticipant(mesh, state) {
  return {
    mesh,
    onSessionAttached(settings) {
      state.physicsEnabled = true;
      state.deformationMode = 'physics';
      state.physicsParticipantStatus = 'participating';
      state.physicsParticipantError = null;
      state.physicsState = initializePhysicsState(state.physicsForest);
      state.physicsSettled = false;
      refreshParticipantDerivedState(mesh, state, settings);
      applyDeformation(mesh, state, {
        request: false, invalidateShadow: false, skipHidden: true,
      });
    },
    onSessionDetached() {
      state.physicsEnabled = false;
      state.deformationMode = null;
      state.physicsState = null;
      state.physicsTransforms = null;
      state.physicsJointLimits = null;
      state.physicsConstraintDiagnostics = null;
      state.physicsGravityAccelerations = null;
      state.physicsGravityDiagnostics = null;
      state.physicsSettled = true;
      clearMotionDiagnostics(state);
      applyDeformation(mesh, state, {
        request: false, invalidateShadow: false, skipHidden: false,
      });
    },
    onSettingsChanged(settings) {
      refreshParticipantDerivedState(mesh, state, settings);
      if (settings.constraintsEnabled) {
        applyPhysicsJointLimits(state.physicsState, state.physicsJointLimits);
      }
      state.physicsSettled = false;
    },
    onModelMotion(motion) {
      if (!state.physicsState || !state.physicsForest) return false;
      const rotationMagnitude = Math.hypot(...motion.rotationVector);
      state.lastRootAngularDeltaVector = [...motion.rotationVector];
      state.lastRootAngularDeltaMagnitude = rotationMagnitude;
      state.motionEventCount += 1;
      let physicsChanged = false;
      let immediateDeformation = false;
      const settings = motion.settings;
      if (rotationMagnitude >= 1e-10) {
        refreshGravityState(
          mesh, state, settings, motion.modelOrientation);
      }
      if (rotationMagnitude >= 1e-10 && settings.angularResponse > 0) {
        applyReferenceFrameAngularDelta(
          state.physicsState, state.physicsForest,
          motion.rotationVector, settings.angularResponse,
          settings.constraintsEnabled ? state.physicsJointLimits : null);
        physicsChanged = true;
        immediateDeformation = true;
      }
      if (motion.deltaLinearVelocityWorld) {
        const deltaVelocityLocal = localVector(
          motion.deltaLinearVelocityWorld, motion.previousModelOrientation);
        state.lastRootLinearVelocityWorld = [...motion.linearVelocityWorld];
        state.lastRootLinearVelocityLocal = [...localVector(
          motion.linearVelocityWorld, motion.previousModelOrientation)];
        state.lastRootLinearVelocityDelta = deltaVelocityLocal;
        const diagnostics = {};
        applyReferenceFrameLinearVelocityDelta(
          state.physicsState, state.physicsForest,
          state.physicsCenterByBoneId || state.centerByBoneId,
          deltaVelocityLocal, settings.velocityResponse, diagnostics,
          settings.constraintsEnabled ? state.physicsJointLimits : null);
        physicsChanged = diagnostics.maxDeltaAngularVelocityMagnitude >= 1e-10
          || physicsChanged;
      } else if (Math.hypot(...motion.translationDeltaWorld) >= 1e-10) {
        const translationLocal = localVector(
          motion.translationDeltaWorld, motion.previousModelOrientation);
        state.lastRootTranslationDeltaWorld = [
          ...motion.translationDeltaWorld];
        state.lastRootTranslationDeltaLocal = [...translationLocal];
        state.translationEventCount += 1;
        const diagnostics = {};
        applyReferenceFrameTranslationDelta(
          state.physicsState, state.physicsForest,
          state.physicsCenterByBoneId || state.centerByBoneId,
          translationLocal, settings.translationResponse, diagnostics,
          settings.constraintsEnabled ? state.physicsJointLimits : null);
        state.lastTranslationLagRotationVector = [
          ...(diagnostics.maxLagRotationVector || [0, 0, 0])];
        state.lastTranslationLagRotationMagnitude = Number(
          diagnostics.maxLagRotationMagnitude) || 0;
        if (state.lastTranslationLagRotationMagnitude >= 1e-10
            && settings.translationResponse > 0) {
          physicsChanged = true;
          immediateDeformation = true;
        }
      }
      if (!physicsChanged) return false;
      state.physicsSettled = false;
      if (immediateDeformation) applyDeformation(mesh, state, {
        request: false, invalidateShadow: false, skipHidden: true,
      });
      return true;
    },
    onVirtualMotion(motion) {
      if (!state.physicsState || !state.physicsForest
          || !motion.modelOrientation) return false;
      const currentVelocityLocal = localVector(
        motion.velocityWorld, motion.modelOrientation)
        .map(value => value * physicsReferenceRadius(mesh, state));
      const deltaVelocityLocal = localVector(
        motion.deltaVelocityWorld, motion.modelOrientation)
        .map(value => value * physicsReferenceRadius(mesh, state));
      state.physicsVirtualLinearVelocityLocal = motion.active === false
        ? [0, 0, 0] : [...currentVelocityLocal];
      const diagnostics = {};
      applyReferenceFrameLinearVelocityDelta(
        state.physicsState, state.physicsForest,
        state.physicsCenterByBoneId || state.centerByBoneId,
        deltaVelocityLocal, motion.settings.velocityResponse, diagnostics,
        motion.settings.constraintsEnabled ? state.physicsJointLimits : null);
      if (motion.active === false) state.physicsSettled = false;
      return diagnostics.maxDeltaAngularVelocityMagnitude >= 1e-10
        || motion.active === false;
    },
    step(dt, settings) {
      if (!state.physicsState || !state.physicsForest) return;
      stepSpringPhysics(
        state.physicsState, state.physicsForest, dt, {
          frequencyHz: settings.frequencyHz,
          dampingRatio: settings.dampingRatio,
          targetRotation: [0, 0, 0],
          externalAngularAccelerationByBoneId: settings.gravityEnabled
            ? state.physicsGravityAccelerations : null,
          jointLimitByBoneId: settings.constraintsEnabled
            ? state.physicsJointLimits : null,
          maxDt: MODEL_PHYSICS_STEP,
        });
      state.physicsSettled = isPhysicsSettled(
        state.physicsState, state.physicsForest, [0, 0, 0], {
          frequencyHz: settings.frequencyHz,
          externalAngularAccelerationByBoneId: settings.gravityEnabled
            ? state.physicsGravityAccelerations : null,
          jointLimitByBoneId: settings.constraintsEnabled
            ? state.physicsJointLimits : null,
        });
    },
    isSettled: () => state.physicsSettled,
    isVisible: () => mesh.visible,
    deform: options => applyDeformation(mesh, state, options),
    reset(settings) {
      resetPhysicsState(state.physicsState);
      refreshParticipantDerivedState(mesh, state, settings);
      state.physicsSettled = !settings.gravityEnabled;
      clearMotionDiagnostics(state);
      applyDeformation(mesh, state, {
        request: false, invalidateShadow: false, skipHidden: true,
      });
    },
  };
}

function averageSelectedCenter(state, ids) {
  const centers = ids.map(id => state.centerByBoneId?.get(id))
    .filter(center => Array.isArray(center) && center.length >= 3);
  if (!centers.length) return [0, 0, 0];
  return centers.reduce((sum, center) => [
    sum[0] + Number(center[0] || 0),
    sum[1] + Number(center[1] || 0),
    sum[2] + Number(center[2] || 0),
  ], [0, 0, 0]).map(value => value / centers.length);
}

function selectedPhysicsForest(mesh, state) {
  const graph = ensureInfluenceGraph(mesh, state);
  const selected = new Set(
    normalizeSelectedBoneIds(modelWeightState.selectedBoneIds)
      .filter(id => state.boneIds.includes(id)));
  if (!selected.size) return null;
  const selectedNodes = (graph.nodes || []).filter(node =>
    selected.has(Number(node.boneId)));
  const candidateEdges = candidateRelationshipEdges(graph);
  const selectedEdges = candidateEdges.filter(relationship =>
    selected.has(Number(relationship.boneA))
    && selected.has(Number(relationship.boneB)));
  const selectedTree = buildMaximumSpanningTree(selectedNodes, selectedEdges);
  const centers = new Map(state.centerByBoneId || []);
  const components = [];
  const componentByBoneId = {};

  selectedTree.components.forEach((componentIds, componentIndex) => {
    const componentSet = new Set(componentIds);
    const boundary = (graph.relationships || [])
      .filter(relationship => {
        const boneA = Number(relationship.boneA);
        const boneB = Number(relationship.boneB);
        const leftSelected = componentSet.has(boneA);
        const rightSelected = componentSet.has(boneB);
        if (leftSelected === rightSelected) return false;
        const other = leftSelected ? boneB : boneA;
        return !selected.has(other);
      })
      .sort(relationshipSort)[0];
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
        || averageSelectedCenter(state, componentIds));
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

function refreshPhysicsSelectionState(mesh, state) {
  if (!state?.loaded) return false;
  const nextForest = selectedPhysicsForest(mesh, state);
  const selectedIds = nextForest?.selectedBoneIds || [];
  const nextKey = selectedIds.join(',');
  const changed = state.physicsSelectionKey !== nextKey;
  state.physicsForest = nextForest;
  state.physicsCenterByBoneId = nextForest?.centers || state.centerByBoneId;
  state.physicsSelectionKey = nextKey;
  refreshParticipantDerivedState(mesh, state, modelPhysicsSession.getSettings());
  return changed;
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
  knownMeshes.delete(mesh);
  modelPhysicsSession.detach(mesh);
  refreshModelWeightSummary();
  notifyModelWeightChanged();
}

export function getModelPhysicsState() {
  return modelPhysicsSession.getState();
}

export function destroyModelPhysicsSession() {
  modelPhysicsSession.destroy();
  knownMeshes.clear();
  resetModelWeightState();
}

export function disableModelPhysics() {
  modelPhysicsSession.disable();
  return false;
}

function installSkinningEntry(mesh, entry, buffer) {
  const state = stateFor(mesh);
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
  state.selectedBone = null;
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
    refreshModelWeightSummary();
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
    refreshModelWeightSummary();
    syncPhysicsParticipants();
    notifyModelWeightChanged();
    return modelWeightSnapshot();
  })();
  return modelWeightState.promise
    .catch(error => {
      if (generation === modelWeightGeneration) {
        setModelWeightLoadError(error);
        refreshModelWeightSummary();
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

function syncPhysicsParticipants() {
  if (!modelPhysicsSession.getState().enabled) return;
  if (!modelWeightState.selectedBoneIds.size) {
    disableModelPhysics();
    return;
  }
  let attached = false;
  [...knownMeshes].forEach(mesh => {
    if (!eligibleSkinningMesh(mesh)) return;
    const state = states.get(mesh);
    if (!state?.loaded) {
      if (state?.error) {
        state.physicsParticipantStatus = 'failed';
        state.physicsParticipantError = state.error;
        modelPhysicsSession.markFailed(mesh, state.error);
      }
      return;
    }
    const changed = refreshPhysicsSelectionState(mesh, state);
    const participant = modelPhysicsSession.getParticipant(mesh);
    if (!state.physicsForest) {
      if (participant) modelPhysicsSession.detach(mesh);
      state.physicsParticipantStatus = 'not-selected';
      state.physicsState = null;
      state.physicsEnabled = false;
      return;
    }
    if (changed && participant) modelPhysicsSession.detach(mesh);
    if (!modelPhysicsSession.getParticipant(mesh)) {
      attached = modelPhysicsSession.attach(
        createPhysicsParticipant(mesh, state)) || attached;
    }
  });
  if (attached) {
    modelPhysicsSession.wake();
    invalidateCharacterShadowGeometry({request: false});
    requestRender();
  }
}

export function enableModelPhysics() {
  if (modelPhysicsSession.getState().enabled) {
    return Promise.resolve(getModelPhysicsState());
  }
  if (!modelWeightState.selectedBoneIds.size) {
    return Promise.resolve(getModelPhysicsState());
  }
  const generation = modelPhysicsSession.enable(getModelTransformState());
  syncPhysicsParticipants();
  return Promise.resolve({...getModelPhysicsState(), generation});
}

export function resetModelPhysicsMotion() {
  return modelPhysicsSession.reset(getModelTransformState());
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

function removeExperimentHelpers(mesh, state) {
  if (!state) return;
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

function transformedInfluenceCenter(node, state, mode) {
  const point = vectorFromCenter(node.weightedCenter);
  if (mode === 'tree') {
    const transforms = state.deformationMode === 'physics'
      ? state.physicsTransforms : null;
    const transform = transforms?.get(node.boneId);
    if (transform) point.applyMatrix4(transform);
  }
  return point;
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
  if (activeExperimentHelperMesh === visualization.mesh) {
    activeExperimentHelperMesh = null;
  }
}

function createInfluenceVisualization(mesh, state, mode) {
  removeInfluenceVisualization(state);
  activateExperimentHelpers(mesh);
  const graph = state.influenceGraph;
  const nodes = mode === 'center'
    ? (state.influenceNodes || []).filter(node =>
      node.boneId === state.selectedBone)
    : (graph?.nodes || []);
  if (!nodes.length || (mode !== 'center' && mode !== 'tree')) return null;
  const nodeById = new Map(nodes.map(node => [node.boneId, node]));
  const componentRoots = new Set((state.candidateForest?.components || [])
    .map(component => component.rootId));
  const radius = Math.max(
    (mesh.geometry.boundingSphere?.radius || 1) * 0.012, 0.001);
  const group = new THREE.Group();
  group.name = mode === 'center'
    ? 'Influence Center' : 'Inferred Influence Hierarchy';
  const nodeGeometry = new THREE.SphereGeometry(1, 8, 6);
  const nodeMaterial = new THREE.MeshBasicMaterial({color: 0xffb86c});
  const nodeMarkers = new Map();
  nodes.forEach(node => {
    const marker = new THREE.Mesh(nodeGeometry, nodeMaterial);
    marker.scale.setScalar(radius * (componentRoots.has(node.boneId) ? 1.35 : 1));
    marker.position.copy(transformedInfluenceCenter(node, state, mode));
    marker.userData.influenceBoneId = node.boneId;
    marker.userData.influenceComponentRoot = componentRoots.has(node.boneId);
    group.add(marker);
    nodeMarkers.set(node.boneId, marker);
  });

  const relationships = mode === 'tree' ? state.candidateTree?.edges || [] : [];
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
      opacity: 0.8,
    });
    const line = new THREE.Line(geometry, material);
    line.userData.influenceBoneA = relationship.boneA;
    line.userData.influenceBoneB = relationship.boneB;
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
  const nodes = state.influenceVisualizationMode === 'center'
    ? (state.influenceNodes || []) : (state.influenceGraph?.nodes || []);
  const nodeById = new Map(nodes
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
  const settings = modelPhysicsSession.getSettings();
  const participant = modelPhysicsSession.getParticipant(mesh);
  if (participant) modelPhysicsSession.detach(mesh);
  refreshParticipantDerivedState(mesh, state, settings);
  if (state.influenceVisualizationMode) {
    createInfluenceVisualization(
      mesh, state, state.influenceVisualizationMode);
  }
  if (participant && modelPhysicsSession.getState().enabled) {
    modelPhysicsSession.attach(createPhysicsParticipant(mesh, state));
    modelPhysicsSession.wake();
  }
  applyDeformation(mesh, state, {
    request: false, invalidateShadow: false, skipHidden: true,
  });
  requestRender();
}

function ensureInfluenceGraph(mesh, state) {
  if (!state.influenceGraph) state.influenceGraph = buildInfluenceGraph(mesh, state);
  return state.influenceGraph;
}

export function ensureCandidateForest(mesh) {
  const state = stateFor(mesh);
  if (!state?.loaded) return null;
  if (state.candidateForest && state.candidateTree) return state.candidateForest;
  const graph = ensureInfluenceGraph(mesh, state);
  const requestedRoot = Number(
    state.candidateRootId ?? state.selectedBone ?? state.boneIds[0]);
  const root = state.boneIds.includes(requestedRoot)
    ? requestedRoot : state.boneIds[0];
  const candidateEdges = candidateRelationshipEdges(graph);
  const tree = buildMaximumSpanningTree(
    graph.nodes, candidateEdges);
  state.candidateRootId = root ?? null;
  const forest = orientForest(
    graph.nodes, tree.edges, state.candidateRootId,
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
  return state.candidateForest;
}

/** Re-baseline loaded weights after the authoritative shape geometry changes. */
export function refreshSkinningAfterShapeChange(mesh) {
  const state = states.get(mesh);
  const position = mesh?.geometry?.attributes?.position;
  if (!state?.loaded || !position) return false;
  const normal = mesh.geometry.attributes.normal;
  state.baselinePositions = new Float32Array(position.array);
  state.baselineNormals = normal ? new Float32Array(normal.array) : null;
  state.influenceNodes = buildInfluenceNodes(
    state.baselinePositions, state.indices, state.weights,
    state.influenceCount, state.boneIds);
  state.centerByBoneId = new Map(state.influenceNodes.map(node => [
    node.boneId, node.weightedCenter]));
  state.influenceGraph = null;
  state.candidateTree = null;
  state.candidateForest = null;
  const wasPhysicsEnabled = state.physicsEnabled;
  const participant = modelPhysicsSession.getParticipant(mesh);
  if (participant) modelPhysicsSession.detach(mesh);
  state.physicsTransforms = null;
  state.physicsForest = null;
  state.physicsCenterByBoneId = state.centerByBoneId;

  if (wasPhysicsEnabled) {
    refreshPhysicsSelectionState(mesh, state);
    if (state.physicsForest && modelPhysicsSession.getState().enabled) {
      modelPhysicsSession.attach(createPhysicsParticipant(mesh, state));
      modelPhysicsSession.wake();
    }
  } else if (state.influenceVisualizationMode === 'tree') {
    ensureCandidateForest(mesh);
  } else if (state.influenceVisualizationMode === 'center') {
    createInfluenceVisualization(mesh, state, 'center');
  }
  if (state.heatmapMode) updateHeatmap(mesh, state);
  return true;
}

export function setCandidateTreeRoot(mesh, rootId) {
  const state = stateFor(mesh);
  const id = Number(rootId);
  if (!state?.loaded || !state.boneIds.includes(id)) return false;
  ensureCandidateForest(mesh);
  if (!state.candidateTree) return false;
  state.candidateRootId = id;
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
  refreshAfterForestTopologyChange(mesh, state);
  return id;
}

export function setInfluenceVisualizationMode(mesh, mode) {
  const state = stateFor(mesh);
  if (!state?.loaded) return null;
  if (mode !== null && mode !== 'center' && mode !== 'tree') {
    return state.influenceVisualizationMode;
  }
  if (mode === 'tree') ensureCandidateForest(mesh);
  if (mode === null) removeInfluenceVisualization(state);
  else createInfluenceVisualization(mesh, state, mode);
  requestRender();
  return state.influenceVisualizationMode;
}

export function setCandidateTreeVisible(mesh, visible) {
  return setInfluenceVisualizationMode(mesh, visible ? 'tree' : null)
    === 'tree';
}

if (typeof window !== 'undefined') {
  window.addEventListener('mod-viewer-model-transform-changed',
    handleModelTransformChanged);
  window.addEventListener('mod-viewer-virtual-model-motion',
    handleVirtualModelMotion);
  window.addEventListener('mod-viewer-mesh-state-changed', event => {
    modelPhysicsSession.handleMeshStateChanged(event.detail?.meshes || []);
  });
  window.addEventListener('mod-viewer-mesh-selected', event => {
    const mesh = event.detail?.mesh || null;
    if (activeExperimentHelperMesh && activeExperimentHelperMesh !== mesh) {
      const previous = activeExperimentHelperMesh;
      removeExperimentHelpers(previous, states.get(previous));
    }
  });
}

function applyDeformation(mesh, state, {
  request = true,
  invalidateShadow = true,
  skipHidden = false,
} = {}) {
  if (!state.loaded || !state.baselinePositions) return;
  const physicsActive = state.deformationMode === 'physics'
    && state.physicsEnabled && state.physicsState && state.physicsForest;
  let result;
  if (physicsActive) {
    state.physicsTransforms = buildForestTransformsFromLocalRotations(
      state.physicsForest,
      state.physicsCenterByBoneId || state.centerByBoneId, {
        rotationByBoneId: physicsRotationMap(state.physicsState),
      });
    result = applyWeightedTransformDeformation(
      state.baselinePositions, state.indices, state.weights,
        state.influenceCount, state.physicsTransforms);
  } else {
    state.physicsTransforms = null;
    result = state.baselinePositions;
  }
  if (skipHidden && !mesh.visible) return false;
  const position = mesh.geometry.attributes.position;
  position.array.set(result);
  position.needsUpdate = true;
  if (!physicsActive) {
    restoreNormals(mesh, state);
  } else {
    mesh.geometry.computeVertexNormals();
    mesh.geometry.attributes.normal.needsUpdate = true;
  }
  updateInfluenceVisualization(mesh, state);
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  if (invalidateShadow) invalidateCharacterShadowGeometry({request});
  else if (request) requestRender();
  return true;
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

function updateModelWeightHeatmap() {
  knownMeshes.forEach(mesh => {
    const state = states.get(mesh);
    if (!state?.loaded) return;
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
    state.selectedBone = null;
    state.encoding = preview.encoding || null;
    state.diagnostics = preview.diagnostics || null;
    state.loaded = true;
    state.influenceNodes = buildInfluenceNodes(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, state.boneIds);
    state.centerByBoneId = new Map(state.influenceNodes.map(node => [
      node.boneId, node.weightedCenter]));
    refreshSelectedWeightMask(mesh, state);
    refreshModelWeightSummary();
    notifyModelWeightChanged();
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

export function setSelectedBoneIds(ids) {
  refreshModelWeightSummary();
  const available = new Set(modelWeightState.availableBoneIds);
  const normalized = normalizeSelectedBoneIds(ids)
    .filter(id => !available.size || available.has(id));
  const previous = normalizeSelectedBoneIds(modelWeightState.selectedBoneIds);
  if (previous.length === normalized.length
      && previous.every((id, index) => id === normalized[index])) {
    return modelWeightSnapshot();
  }
  modelWeightState.selectedBoneIds = new Set(normalized);
  knownMeshes.forEach(mesh => refreshSelectedWeightMask(mesh, states.get(mesh)));
  if (modelWeightState.heatmapEnabled) updateModelWeightHeatmap();
  if (modelPhysicsSession.getState().enabled) syncPhysicsParticipants();
  notifyModelWeightChanged();
  requestRender();
  return modelWeightSnapshot();
}

export function clearSelectedBoneIds() {
  return setSelectedBoneIds([]);
}

/** Compatibility helper for callers that previously selected one mesh bone. */
export function setSelectedBone(mesh, boneId) {
  const state = stateFor(mesh);
  if (!state?.loaded || !state.boneIds.includes(Number(boneId))) return;
  return setSelectedBoneIds([boneId]);
}

export function setPhysicsFrequency(mesh, frequencyHz) {
  const value = Number(frequencyHz);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({frequencyHz: value});
  return true;
}

export function setPhysicsDamping(mesh, dampingRatio) {
  const value = Number(dampingRatio);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({dampingRatio: value});
  return true;
}

export function setPhysicsMotionStrength(mesh, strength) {
  const value = Number(strength);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({angularResponse: value});
  return true;
}

export function setPhysicsLinearMotionStrength(mesh, strength) {
  const value = Number(strength);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({translationResponse: value});
  return true;
}

export function setPhysicsContinuousLinearResponse(mesh, strength) {
  const value = Number(strength);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({velocityResponse: value});
  return true;
}

export function setPhysicsGravityEnabled(mesh, enabled) {
  modelPhysicsSession.setSettings({gravityEnabled: !!enabled});
  return !!enabled;
}

export function setPhysicsGravityScale(mesh, scale) {
  const value = Number(scale);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({gravityScale: value});
  return true;
}

export function setPhysicsConstraintsEnabled(mesh, enabled) {
  modelPhysicsSession.setSettings({constraintsEnabled: !!enabled});
  return !!enabled;
}

export function setPhysicsMaxBendDegrees(mesh, degrees) {
  const value = Number(degrees);
  if (!Number.isFinite(value)) return false;
  modelPhysicsSession.setSettings({maxBendDegrees: value});
  return true;
}

export function setPhysicsEnabled(mesh, enabled) {
  if (!enabled) return disableModelPhysics();
  void enableModelPhysics();
  return true;
}

export function resetPhysicsMotion(mesh) {
  return resetModelPhysicsMotion();
}

export function setSkinningHeatmapMode(mesh, mode) {
  if (mode !== null && mode !== 'bone') return false;
  return setModelWeightHeatmap(mode === 'bone');
}

export function setSkinningHeatmap(mesh, enabled) {
  return setModelWeightHeatmap(enabled);
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
    removeInfluenceVisualization(state);
    if (state.heatmapMode || state.debugMaterial) disableHeatmap(mesh, state);
    requestRender();
    return;
  }
  state.deformationMode = null;
  if (!state.physicsEnabled) state.physicsTransforms = null;
  removeInfluenceVisualization(state);
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
  removeInfluenceVisualization(state);
  if (state.debugMaterial) state.debugMaterial.dispose();
  mesh.geometry?.deleteAttribute?.('color');
  mesh.material = state.originalMaterial || mesh.material;
  states.delete(mesh);
}
