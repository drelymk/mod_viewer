// Shared mutable state for the Weight and Rig runtime.

export const EMPTY_ACTIVE_VERTICES = new Uint32Array();
export const RIG_ROTATION_SNAP_DEGREES = Object.freeze([0, 5, 15, 30]);
export const RIG_LIMB_ROLES = Object.freeze([
  'left_arm', 'right_arm', 'left_leg', 'right_leg',
]);

function collectionEntries(collection) {
  if (collection instanceof Map) return [...collection.entries()];
  return Object.entries(collection || {});
}

export function matrixIsIdentity(value, tolerance = 1e-5) {
  const elements = value?.elements || value;
  if (!elements || elements.length < 16) return false;
  const identity = [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ];
  return identity.every((expected, index) =>
    Math.abs(Number(elements[index]) - expected) <= tolerance);
}

export function activePoseJointIds({manualRotations, driverTransforms,
    quaternionIsIdentity} = {}) {
  const ids = new Set();
  collectionEntries(manualRotations).forEach(([jointId, quaternion]) => {
    if (typeof quaternionIsIdentity !== 'function'
        || !quaternionIsIdentity(quaternion)) {
      ids.add(Number(jointId));
    }
  });
  collectionEntries(driverTransforms).forEach(([jointId, matrix]) => {
    if (!matrixIsIdentity(matrix)) ids.add(Number(jointId));
  });
  return [...ids].filter(Number.isFinite).sort((left, right) => left - right);
}

function createModelRigDefaults() {
  return {
    loaded: false,
    loading: false,
    promise: null,
    error: null,
    jointPickIntent: null,
    selectedJointId: null,
    structureRevision: 0,
    pickStatus: '',
    humanoidStructureRevision: null,
    humanoidControlRig: null,
    humanoidPose: {},
    rotationSnapDegrees: 0,
    ikEnabled: false,
    activeLimbRole: 'left_arm',
    explicitRootSignatures: new Set(),
  };
}

function createRigPresetDefaults() {
  return {
    loaded: false,
    loading: false,
    error: null,
    presets: [],
    selectedPresetId: null,
    lastApplyResult: null,
  };
}

export function createRigRuntimeState() {
  const modelRigState = createModelRigDefaults();
  const rigPresetState = createRigPresetDefaults();
  return {
    modelRigState,
    rigPresetState,
    structureRevision: 0,
    resetModelRigState() {
      Object.assign(modelRigState, createModelRigDefaults());
    },
    resetRigPresetState() {
      Object.assign(rigPresetState, createRigPresetDefaults());
    },
  };
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

export function createWeightRuntimeState() {
  const states = new WeakMap();
  const knownMeshes = new Set();
  const createModelWeightDefaults = () => ({
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
  });
  const modelWeightState = createModelWeightDefaults();

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
      physicsParticipantStatus: 'not-attempted',
      physicsParticipantError: null,
      selectedWeightMask: null,
      physicsActiveVertices: null,
      combinedActiveVertices: null,
      combinedPoseVerticesRef: null,
      combinedPhysicsVerticesRef: null,
      finalBoundsDirty: false,
      preDeformationFrustumCulled: null,
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
      poseTransforms: null,
      poseRotations: new Map(),
      poseActiveVertices: null,
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

  function resetModelWeightState() {
    Object.assign(modelWeightState, createModelWeightDefaults());
  }

  return {
    states,
    knownMeshes,
    modelWeightState,
    newState,
    stateFor,
    resetModelWeightState,
  };
}
