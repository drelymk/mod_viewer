// Compact live Rig snapshots and stable topology comparison projections.

function componentSnapshot(component) {
  return {
    componentId: component.componentId,
    rootId: component.rootId,
    nodeIds: [...(component.nodeIds || [])],
    parentById: {...(component.parentById || {})},
    childrenById: Object.fromEntries(Object.entries(
      component.childrenById || {}).map(([id, children]) => [id, [...children]])),
    depthById: {...(component.depthById || {})},
    maxDepth: component.maxDepth,
  };
}

function sourceBoneSignature(sourceKey, boneId) {
  return `${sourceKey}#bone=${Number(boneId)}`;
}

function sourcePair(left, right) {
  return left < right ? [left, right] : [right, left];
}

/**
 * Return topology identity keyed by source and bone rather than array IDs.
 * This is intentionally compact so diagnostics can compare Rig rebuilds.
 */
export function sourceTopologyComparisonSnapshot(source) {
  const sourceKey = String(source?.sourceKey || '');
  const signature = boneId => sourceBoneSignature(sourceKey, boneId);
  const components = source?.components || [];
  const rootSignatures = components.map(component =>
    signature(component.rootId)).sort();
  const componentMembership = components.map(component =>
    (component.nodeIds || []).map(signature).sort()).sort((left, right) =>
      JSON.stringify(left).localeCompare(JSON.stringify(right)));
  const directedParentEdges = components.flatMap(component =>
    Object.entries(component.parentById || {}).flatMap(([childId, parentId]) =>
      parentId === null || parentId === undefined ? [] : [[
        signature(parentId), signature(childId),
      ]])).sort((left, right) => JSON.stringify(left)
    .localeCompare(JSON.stringify(right)));
  const undirectedTreeEdges = directedParentEdges.map(([parent, child]) =>
    sourcePair(parent, child)).sort((left, right) => JSON.stringify(left)
    .localeCompare(JSON.stringify(right)));
  const pivotBySourceBonePair = (source?.relationships || [])
    .filter(edge => edge.jointCenter?.length >= 3)
    .map(edge => ({
      pair: sourcePair(signature(edge.boneA), signature(edge.boneB)),
      pivot: [...edge.jointCenter],
    }))
    .sort((left, right) => JSON.stringify(left.pair)
      .localeCompare(JSON.stringify(right.pair)));
  return {
    rootSignatures,
    undirectedTreeEdges,
    directedParentEdges,
    componentMembership,
    pivotBySourceBonePair,
  };
}

/** Build the compact model-level view model used by Rig UI surfaces. */
export function modelRigSnapshot(modelSkinningRig, {
  quaternionIsIdentity,
  matrixIsIdentity,
} = {}) {
  if (!modelSkinningRig) return null;
  const components = (modelSkinningRig.components || []).map(componentSnapshot);
  const joints = (modelSkinningRig.joints || []).map(joint => ({
    jointId: joint.jointId,
    restCenter: [...(joint.restCenter || [0, 0, 0])],
    restPivot: [...(joint.restPivot || joint.restCenter || [0, 0, 0])],
  }));
  const forestEdges = (modelSkinningRig.edges || []).map(edge => ({
    jointA: edge.jointA,
    jointB: edge.jointB,
    parentId: edge.parentId ?? edge.jointA,
    childId: edge.childId ?? edge.jointB,
    relationshipType: edge.relationshipType,
  }));
  const manualPoseJointIds = [...modelSkinningRig.poseRotationByJointId.entries()]
    .filter(([, quaternion]) => !quaternionIsIdentity(quaternion))
    .map(([jointId]) => Number(jointId));
  const humanoidPoseJointIds = [...(
    modelSkinningRig.humanoidDriverTransforms instanceof Map
      ? modelSkinningRig.humanoidDriverTransforms.entries()
      : Object.entries(modelSkinningRig.humanoidDriverTransforms || {}))]
    .filter(([, matrix]) => typeof matrixIsIdentity === 'function'
      ? !matrixIsIdentity(matrix) : false)
    .map(([jointId]) => Number(jointId));
  const snapshot = {
    key: modelSkinningRig.key || 'model-rig',
    structureRevision: modelSkinningRig.structureRevision,
    joints,
    forestEdges,
    components,
    poseRotationByJointId: Object.fromEntries(
      [...modelSkinningRig.poseRotationByJointId.entries()].map(
        ([jointId, quaternion]) => [jointId, quaternion.toArray()])),
    poseJointIds: [...new Set([
      ...manualPoseJointIds, ...humanoidPoseJointIds,
    ])].sort((left, right) => left - right),
    humanoidPoseJointIds: humanoidPoseJointIds.sort(
      (left, right) => left - right),
  };
  return snapshot;
}

export function rigPresetSnapshot(rigPresetState) {
  return {
    loaded: rigPresetState.loaded,
    loading: rigPresetState.loading,
    error: rigPresetState.error,
    presets: rigPresetState.presets.map(preset => ({
      id: preset.id,
      name: preset.name,
    })),
    selectedPresetId: rigPresetState.selectedPresetId,
    lastApplyResult: rigPresetState.lastApplyResult,
  };
}
