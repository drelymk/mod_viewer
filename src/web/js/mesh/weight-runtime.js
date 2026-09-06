// Shared mutable state for the Weight/Rig runtime.

export const EMPTY_ACTIVE_VERTICES = new Uint32Array();

export function createWeightRuntimeState() {
  const states = new WeakMap();
  const knownMeshes = new Set();
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

  return {
    states,
    knownMeshes,
    modelWeightState,
    newState,
    stateFor,
  };
}
