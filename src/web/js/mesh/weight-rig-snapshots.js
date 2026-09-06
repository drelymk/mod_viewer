// Compact live Rig snapshots and opt-in diagnostic projections.

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

/** Build the source-level projection exposed only by Rig debug state. */
export function sourceRigSnapshot(rig, {
  modelRigState,
  modelJointIdForSourceBone,
  quaternionIsIdentity,
} = {}) {
  if (!rig) return null;
  const source = {
    sourceKey: rig.sourceKey,
    sourceFile: rig.sourceFile,
    boneIdOffset: rig.boneIdOffset,
    structureRevision: rig.structureRevision,
    boneCount: rig.influenceGraph?.nodes?.length || 0,
    boneIds: (rig.influenceGraph?.nodes || []).map(node => node.boneId),
    physicsActive: !!rig.physicsRig?.physicsState,
  };
  const components = (rig.inferredForest?.components || [])
    .map(componentSnapshot);
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
  source.components = components;
  source.nodes = (rig.influenceGraph?.nodes || []).map(node => ({
    ...node,
    weightedCenter: [...(node.weightedCenter || [0, 0, 0])],
  }));
  source.forestEdges = forestEdges;
  source.jointPivotByBoneId = jointPivotByBoneId;
  source.selectedJointId = modelRigState.selectedJointId;
  source.selectedBoneId = (rig.boneIds || []).find(boneId =>
    modelJointIdForSourceBone(rig.sourceKey, boneId)
      === modelRigState.selectedJointId) ?? null;
  source.modelJointIds = Object.fromEntries((rig.boneIds || []).map(boneId => [
    boneId, modelJointIdForSourceBone(rig.sourceKey, boneId) ?? null]));
  source.poseBoneIds = [...rig.poseRotationByBoneId.entries()]
    .filter(([, quaternion]) => !quaternionIsIdentity(quaternion))
    .map(([boneId]) => boneId);
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
  return source;
}

export function modelRigSnapshot(modelSkinningRig, {
  debug = false,
  modelRigState,
  quaternionIsIdentity,
} = {}) {
  if (!modelSkinningRig) return null;
  const components = (modelSkinningRig.components || []).map(componentSnapshot);
  const defaultComponents = (modelSkinningRig.defaultComponents || [])
    .map(componentSnapshot);
  const joints = (modelSkinningRig.joints || []).map(joint => ({
    jointId: joint.jointId,
    jointKey: joint.jointKey,
    signature: joint.signature,
    restCenter: [...(joint.restCenter || [0, 0, 0])],
    restPivot: [...(joint.restPivot || joint.restCenter || [0, 0, 0])],
    restDirection: joint.restDirection ? [...joint.restDirection] : null,
    restFrame: [...(joint.restFrame || [0, 0, 0, 1])],
    parentId: joint.parentId,
    childrenIds: [...(joint.childrenIds || [])],
    ...(debug ? {
      members: (joint.members || []).map(member => ({...member})),
      representativeMember: joint.representativeMember
        ? {...joint.representativeMember} : null,
      affectedVertexCount: Number(joint.evidence?.affectedVertexCount
        ?? joint.affectedVertexCount ?? 0),
      totalWeight: Number(joint.evidence?.totalWeight
        ?? joint.totalWeight ?? 0),
    } : {}),
    ...(debug ? {evidence: {...(joint.evidence || {})}} : {}),
  }));
  const forestEdges = (modelSkinningRig.edges || []).map(edge => {
    const copy = {...edge};
    if (debug) {
      copy.sourceEdges = (edge.sourceEdges || [])
        .map(sourceEdge => ({...sourceEdge}));
    } else {
      delete copy.sourceEdges;
    }
    return copy;
  });
  const snapshot = {
    key: modelSkinningRig.key || 'model-rig',
    structureRevision: modelSkinningRig.structureRevision,
    joints,
    forestEdges,
    components,
    poseRotationByJointId: Object.fromEntries(
      [...modelSkinningRig.poseRotationByJointId.entries()].map(
        ([jointId, quaternion]) => [jointId, quaternion.toArray()])),
    poseJointIds: [...modelSkinningRig.poseRotationByJointId.entries()]
      .filter(([, quaternion]) => !quaternionIsIdentity(quaternion))
      .map(([jointId]) => jointId),
  };
  if (debug) {
    snapshot.defaultComponents = defaultComponents;
    snapshot.explicitRootSignatures = [...modelRigState.explicitRootSignatures]
      .sort((left, right) => left.localeCompare(right));
    snapshot.defaultRestPivotByJointId = Object.fromEntries(
      [...(modelSkinningRig.defaultJointPivotByJointId || new Map())]
        .map(([jointId, pivot]) => [jointId, [...pivot]]));
    snapshot.defaultRestDirectionByJointId = Object.fromEntries(
      [...(modelSkinningRig.defaultRestDirectionByJointId || new Map())]
        .map(([jointId, direction]) => [jointId,
          direction ? [...direction] : null]));
    snapshot.defaultRestFrameByJointId = Object.fromEntries(
      [...(modelSkinningRig.defaultRestFrameByJointId || new Map())]
        .map(([jointId, frame]) => [jointId,
          frame ? (frame.toArray?.() || [...frame]) : null]));
    snapshot.sourceBoneToModelJointId = Object.fromEntries(
      modelSkinningRig.sourceBoneToModelJointId || []);
    snapshot.reconciliation = modelSkinningRig.reconciliation?.reconciliation
      || null;
  }
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
