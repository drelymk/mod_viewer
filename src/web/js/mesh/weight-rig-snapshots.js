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

/** Build the source-level projection exposed only by Rig debug state. */
export function sourceRigSnapshot(rig, {
  modelRigState,
  modelJointIdForSourceBone,
  quaternionIsIdentity,
  memberDiagnostics = null,
  partialOverlapPairs = null,
} = {}) {
  if (!rig) return null;
  const source = {
    sourceKey: rig.sourceKey,
    sourceFile: rig.sourceFile,
    boneIdOffset: rig.boneIdOffset,
    structureRevision: rig.structureRevision,
    evidenceMode: rig.influenceGraph?.evidenceMode || 'vertex',
    triangleCount: rig.influenceGraph?.triangleCount || 0,
    validTriangleCount: rig.influenceGraph?.validTriangleCount || 0,
    degenerateTriangleCount: rig.influenceGraph?.degenerateTriangleCount || 0,
    invalidTriangleCount: rig.influenceGraph?.invalidTriangleCount || 0,
    totalSurfaceArea: rig.influenceGraph?.totalSurfaceArea || 0,
    measuredVertexCount: rig.influenceGraph?.measuredVertexCount || 0,
    zeroMeasureVertexCount: rig.influenceGraph?.zeroMeasureVertexCount || 0,
    surfaceEvidenceAvailable: rig.influenceGraph?.evidenceMode === 'surface',
    fallbackReason: rig.influenceGraph?.fallbackReason || null,
    boundaryEvidenceEnabled: rig.boundaryAnalysis?.boundaryEvidenceEnabled === true,
    boundaryEvidenceReason: rig.boundaryAnalysis?.boundaryEvidenceReason || null,
    baseComponentCount: rig.baseInferredForest?.components?.length
      ?? rig.inferredForest?.components?.length ?? 0,
    finalComponentCount: rig.inferredForest?.components?.length || 0,
    boundaryEdgeCount: rig.boundaryAnalysis?.boundaryEdgeCount || 0,
    exactMatchedEdgeCount: rig.boundaryAnalysis?.exactMatchedEdgeCount || 0,
    validSeamSampleCount: rig.boundaryAnalysis?.validSeamSampleCount || 0,
    boundaryComponentPairCount:
      rig.boundaryAnalysis?.boundaryComponentPairCount || 0,
    acceptedBoundaryBridgeCount: rig.boundaryBridges?.length || 0,
    boundaryAnalysisMs: rig.boundaryAnalysis?.analysisMs || 0,
    boundaryRejectedCounts: {
      ...(rig.boundaryAnalysis?.boundaryRejectedCounts || {}),
    },
    memberCount: rig.influenceGraph?.memberCount || 0,
    uniqueMemberCount: rig.influenceGraph?.uniqueMemberCount || 0,
    duplicateMemberCount: rig.influenceGraph?.duplicateMemberCount || 0,
    duplicateMemberKeys: [
      ...(rig.influenceGraph?.duplicateMemberKeys || []),
    ],
    memberDiagnostics: (memberDiagnostics
      || rig.influenceGraph?.memberDiagnostics || [])
      .map(member => ({...member})),
    partialOverlapPairs: (partialOverlapPairs
      || rig.influenceGraph?.partialOverlapPairs || [])
      .map(pair => ({...pair})),
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
    evidenceType: 'triangle_overlap',
    sharedVertexCount: edge.sharedVertexCount,
    sharedMeasure: edge.sharedMeasure,
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
  source.boundaryBridges = (rig.boundaryBridges || []).map(edge => ({
    boneA: edge.boneA,
    boneB: edge.boneB,
    evidenceType: 'mesh_boundary',
    jointCenter: edge.jointCenter ? [...edge.jointCenter] : null,
    matchedEdgeCount: edge.matchedEdgeCount,
    matchedLength: edge.matchedLength,
    memberKey: edge.memberKey,
    componentA: edge.componentA,
    componentB: edge.componentB,
    bonePairSupport: {...(edge.bonePairSupport || {})},
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
  const joints = (modelSkinningRig.joints || []).map(joint => ({
    jointId: joint.jointId,
    restCenter: [...(joint.restCenter || [0, 0, 0])],
    restPivot: [...(joint.restPivot || joint.restCenter || [0, 0, 0])],
    ...(debug ? {
      jointKey: joint.jointKey,
      signature: joint.signature,
      restDirection: joint.restDirection ? [...joint.restDirection] : null,
      restFrame: [...(joint.restFrame || [0, 0, 0, 1])],
      parentId: joint.parentId,
      childrenIds: [...(joint.childrenIds || [])],
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
      return copy;
    }
    return {
      jointA: edge.jointA,
      jointB: edge.jointB,
      parentId: edge.parentId ?? edge.jointA,
      childId: edge.childId ?? edge.jointB,
      relationshipType: edge.relationshipType,
    };
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
    snapshot.defaultComponents = (modelSkinningRig.defaultComponents || [])
      .map(componentSnapshot);
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
    snapshot.restContinuationChildByJointId = Object.fromEntries(
      [...(modelSkinningRig.restContinuationChildByJointId || new Map())]
        .map(([jointId, childId]) => [jointId, childId]));
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
