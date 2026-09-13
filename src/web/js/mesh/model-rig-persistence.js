export const MODEL_RIG_VERSION = 1;
export const MODEL_RIG_BUILDER_VERSION = 1;

function stableNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Number(number.toPrecision(9));
}

function stableVector(value, length) {
  const values = value?.toArray ? value.toArray() : value;
  if (!Array.isArray(values) && !ArrayBuffer.isView(values)) return null;
  if (values.length !== length) return null;
  const vector = [...values].map(stableNumber);
  return vector.every((entry) => entry !== null) ? vector : null;
}

function serializedSourceTable(sourceRigs = []) {
  return [...sourceRigs]
    .map((sourceRig) => ({
      source_key: String(sourceRig?.sourceKey || ""),
      source_file: String(sourceRig?.sourceFile || ""),
      bone_id_offset: Number(sourceRig?.boneIdOffset || 0),
    }))
    .sort((left, right) => left.source_key.localeCompare(right.source_key));
}

function sourceBoneKey(sourceKey, boneId) {
  return `${String(sourceKey)}#bone=${Number(boneId)}`;
}

function sourceTableIndexByKey(sourceTable) {
  return new Map(sourceTable.map((source, index) => [source.source_key, index]));
}

function serializedMembers(joint, sourceTable) {
  const sourceIndexByKey = sourceTableIndexByKey(sourceTable);
  return (Array.isArray(joint?.members) ? joint.members : [])
    .map(member => {
      const sourceIndex = sourceIndexByKey.get(String(member?.sourceKey));
      const boneId = Number(member?.boneId);
      return Number.isInteger(sourceIndex) && Number.isInteger(boneId)
        && boneId >= 0 ? [sourceIndex, boneId] : null;
    }).filter(Boolean);
}

function modelJointPairKey(left, right) {
  const a = Number(left);
  const b = Number(right);
  return `${Math.min(a, b)}:${Math.max(a, b)}`;
}

function effectiveEdgeStrength(edge) {
  return stableNumber(edge?.treeEdgeScore ?? edge?.score
    ?? edge?.combinedTreeScore ?? edge?.attachmentScore
    ?? edge?.containment ?? edge?.jaccard ?? edge?.weight ?? 0) ?? 0;
}

function edgePivotFor(rig, edge) {
  const key = modelJointPairKey(edge?.jointA, edge?.jointB);
  const fromMap = rig?.jointPivotByEdgeKey instanceof Map
    ? rig.jointPivotByEdgeKey.get(key) : null;
  return stableVector(fromMap, 3)
    || stableVector(edge?.edgePivot, 3)
    || stableVector(edge?.jointCenter, 3)
    || stableVector(rig?.joints?.[Number(edge?.jointB)]?.restPivot, 3)
    || stableVector(rig?.joints?.[Number(edge?.jointA)]?.restPivot, 3)
    || [0, 0, 0];
}

export function serializeModelRig(rig, {
  sourceRigs = rig?.sourceRigs || [],
} = {}) {
  const sourceTable = serializedSourceTable(sourceRigs);
  const joints = (Array.isArray(rig?.joints) ? rig.joints : []).map((joint,
      fallbackId) => {
    const members = Array.isArray(joint?.members) ? joint.members : [];
    const compactMembers = serializedMembers(joint, sourceTable);
    const representative = joint?.representativeMember;
    const representativeKey = representative
      ? String(representative.sourceBoneKey
        || sourceBoneKey(representative.sourceKey, representative.boneId))
      : null;
    const representativeMemberIndex = representativeKey === null ? null
      : members.findIndex(member =>
        String(member?.sourceBoneKey
          || sourceBoneKey(member?.sourceKey, member?.boneId))
          === representativeKey);
    return {
      joint_id: Number.isInteger(joint?.jointId) ? joint.jointId : fallbackId,
      members: compactMembers,
      representative_member_index: representativeMemberIndex >= 0
        && representativeMemberIndex < compactMembers.length
        ? representativeMemberIndex : null,
      parent_id: Number.isInteger(joint?.parentId) ? joint.parentId : null,
      rest_center: stableVector(joint?.restCenter, 3) || [0, 0, 0],
      rest_pivot: stableVector(joint?.restPivot, 3) || [0, 0, 0],
      rest_frame: stableVector(joint?.restFrame, 4) || [0, 0, 0, 1],
    };
  });
  const edges = (Array.isArray(rig?.edges) ? rig.edges : [])
    .map(edge => {
      const jointA = Number(edge?.jointA);
      const jointB = Number(edge?.jointB);
      return {
        joint_a: Math.min(jointA, jointB),
        joint_b: Math.max(jointA, jointB),
        relationship_type: edge?.relationshipType === 'attachment'
          ? 'attachment' : 'source',
        edge_strength: effectiveEdgeStrength(edge),
        edge_pivot: edgePivotFor(rig, edge),
      };
    })
    .filter(edge => Number.isInteger(edge.joint_a)
      && Number.isInteger(edge.joint_b) && edge.joint_a !== edge.joint_b);
  const modelReferenceRadius = stableNumber(rig?.modelReferenceRadius
    ?? rig?.reconciliation?.modelReferenceRadius) || 0;
  return {
    version: MODEL_RIG_VERSION,
    builder_version: MODEL_RIG_BUILDER_VERSION,
    model_reference_radius: modelReferenceRadius,
    source_table: sourceTable,
    joints,
    edges,
  };
}

function validInteger(value, minimum = 0) {
  return Number.isInteger(value) && value >= minimum;
}

function validCompactMember(member, sourceTable) {
  return Array.isArray(member) && member.length === 2
    && validInteger(member[0]) && member[0] < sourceTable.length
    && validInteger(member[1]);
}

function validEdge(edge, jointCount) {
  return Boolean(edge && validInteger(edge.joint_a)
    && validInteger(edge.joint_b) && edge.joint_a < jointCount
    && edge.joint_b < jointCount && edge.joint_a !== edge.joint_b
    && (edge.relationship_type === 'source'
      || edge.relationship_type === 'attachment')
    && Number.isFinite(Number(edge.edge_strength))
    && Number(edge.edge_strength) >= 0
    && stableVector(edge.edge_pivot, 3));
}

function rebuildForest(joints, edges) {
  const childrenById = new Map();
  const parentById = new Map();
  joints.forEach((joint) => childrenById.set(joint.jointId, []));

  joints.forEach((joint) => {
    const parentId = joint.parentId;
    parentById.set(joint.jointId, parentId);
    if (parentId !== null) childrenById.get(parentId).push(joint.jointId);
  });
  childrenById.forEach((children) => children.sort((left, right) => left - right));

  const edgePairs = new Set(edges.map(edge =>
    modelJointPairKey(edge.jointA, edge.jointB)));
  if (joints.some(joint => joint.parentId !== null
      && !edgePairs.has(modelJointPairKey(joint.parentId, joint.jointId)))) {
    return null;
  }
  const roots = joints.filter((joint) => joint.parentId === null)
    .map((joint) => joint.jointId).sort((left, right) => left - right);
  const visited = new Set();
  const components = [];
  roots.forEach((rootId) => {
    if (visited.has(rootId)) return;
    const nodeIds = [];
    const stack = [rootId];
    while (stack.length > 0) {
      const jointId = stack.pop();
      if (visited.has(jointId)) continue;
      visited.add(jointId);
      nodeIds.push(jointId);
      const children = childrenById.get(jointId) || [];
      for (let index = children.length - 1; index >= 0; index -= 1) {
        stack.push(children[index]);
      }
    }
    nodeIds.sort((left, right) => left - right);
    const nodeSet = new Set(nodeIds);
    const depthById = {};
    const depthQueue = [{jointId: rootId, depth: 0}];
    for (let index = 0; index < depthQueue.length; index += 1) {
      const current = depthQueue[index];
      depthById[current.jointId] = current.depth;
      (childrenById.get(current.jointId) || []).forEach((childId) => {
        depthQueue.push({jointId: childId, depth: current.depth + 1});
      });
    }
    components.push({
      componentId: components.length,
      rootId,
      nodeIds,
      jointIds: nodeIds,
      parentById: Object.fromEntries(nodeIds.map((jointId) =>
        [jointId, parentById.get(jointId)])),
      childrenById: Object.fromEntries(nodeIds.map((jointId) =>
        [jointId, [...(childrenById.get(jointId) || [])]])),
      depthById,
      maxDepth: Object.values(depthById).reduce(
        (maximum, depth) => Math.max(maximum, depth), 0),
      edges: edges.filter((edge) =>
        nodeSet.has(Number(edge?.jointA)) && nodeSet.has(Number(edge?.jointB))),
    });
  });

  if (visited.size !== joints.length) return null;

  // Match the fresh reconciliation ordering, which is keyed by the smallest
  // joint ID in each component rather than by the chosen root.
  components.sort((left, right) => left.nodeIds[0] - right.nodeIds[0]);
  components.forEach((component, componentId) => {
    component.componentId = componentId;
  });
  const componentByJointId = new Map();
  components.forEach((component) => {
    component.nodeIds.forEach((jointId) =>
      componentByJointId.set(jointId, component.componentId));
  });
  joints.forEach((joint) => {
    joint.childrenIds = [...(childrenById.get(joint.jointId) || [])];
  });
  return {components, componentByJointId, parentById, childrenById};
}

export function hydrateModelRig(data, sourceRigs = []) {
  if (!data || Number(data.version) !== MODEL_RIG_VERSION) return null;
  if (Number(data.builder_version) !== MODEL_RIG_BUILDER_VERSION) return null;
  if (!Array.isArray(data.joints) || data.joints.length === 0
      || !Array.isArray(data.edges)) return null;
  if (!Number.isFinite(Number(data.model_reference_radius))
      || Number(data.model_reference_radius) <= 0) return null;
  const sourceTable = serializedSourceTable(sourceRigs);
  if (JSON.stringify(data.source_table) !== JSON.stringify(sourceTable)) return null;

  const joints = data.joints.map((rawJoint, index) => {
    if (!rawJoint || rawJoint.joint_id !== index) return null;
    if (!Array.isArray(rawJoint.members)
        || rawJoint.members.some(member =>
          !validCompactMember(member, sourceTable))) return null;
    const representativeIndex = rawJoint.representative_member_index;
    if (representativeIndex !== null
        && (!validInteger(representativeIndex)
          || representativeIndex >= rawJoint.members.length)) return null;
    const parentId = rawJoint.parent_id === null ? null : rawJoint.parent_id;
    if (parentId !== null
        && (!validInteger(parentId) || parentId >= data.joints.length
          || parentId === index)) return null;
    const restCenter = stableVector(rawJoint.rest_center, 3);
    const restPivot = stableVector(rawJoint.rest_pivot, 3);
    const restFrame = stableVector(rawJoint.rest_frame, 4);
    if (!restCenter || !restPivot || !restFrame) return null;
    const members = rawJoint.members.map(([sourceIndex, boneId]) => {
      const sourceKey = sourceTable[sourceIndex].source_key;
      return {
        sourceKey,
        sourceBoneKey: sourceBoneKey(sourceKey, boneId),
        boneId,
      };
    });
    return {
      jointId: index,
      jointKey: `joint=${index}`,
      signature: JSON.stringify(members.map(member =>
        member.sourceBoneKey).sort()),
      members,
      representativeMember: representativeIndex === null
        ? null : members[representativeIndex],
      parentId,
      childrenIds: [],
      restCenter,
      restPivot,
      restFrame,
    };
  });
  if (joints.some((joint) => !joint)) return null;

  const edges = data.edges.map(rawEdge => {
    if (!validEdge(rawEdge, joints.length)) return null;
    const jointA = Number(rawEdge.joint_a);
    const jointB = Number(rawEdge.joint_b);
    const strength = Number(rawEdge.edge_strength);
    const pivot = stableVector(rawEdge.edge_pivot, 3);
    return {
      jointA,
      jointB,
      relationshipType: rawEdge.relationship_type,
      weight: strength,
      treeEdgeScore: strength,
      combinedTreeScore: strength,
      attachmentScore: rawEdge.relationship_type === 'attachment'
        ? strength : undefined,
      jointCenter: pivot,
      edgePivot: pivot,
      sourceEdges: [],
    };
  });
  if (edges.some(edge => !edge)) return null;
  const edgeKeys = new Set();
  for (const edge of edges) {
    const key = modelJointPairKey(edge.jointA, edge.jointB);
    if (edgeKeys.has(key)) return null;
    edgeKeys.add(key);
  }

  const sourceBoneToModelJointId = new Map();
  for (const joint of joints) {
    for (const member of joint.members) {
      if (sourceBoneToModelJointId.has(member.sourceBoneKey)) return null;
      sourceBoneToModelJointId.set(member.sourceBoneKey, joint.jointId);
    }
  }

  const forest = rebuildForest(joints, edges);
  if (!forest) return null;
  const jointPivotByEdgeKey = new Map(edges.map(edge => [
    modelJointPairKey(edge.jointA, edge.jointB), [...edge.edgePivot],
  ]));
  const centerByJointId = new Map(joints.map(joint => [
    joint.jointId, [...joint.restCenter],
  ]));
  const jointPivotByJointId = new Map(joints.map(joint => [
    joint.jointId, [...joint.restPivot],
  ]));
  const restFrameByJointId = new Map(joints.map(joint => [
    joint.jointId, [...joint.restFrame],
  ]));
  return {
    modelReferenceRadius: Number(data.model_reference_radius),
    sourceRigs,
    joints,
    edges,
    forestEdges: edges,
    components: forest.components,
    componentByJointId: forest.componentByJointId,
    parentById: forest.parentById,
    childrenById: forest.childrenById,
    centerByJointId,
    jointPivotByJointId,
    jointPivotByEdgeKey,
    restFrameByJointId,
    sourceBoneToModelJointId,
    sourceBoneToModelJointMap: sourceBoneToModelJointId,
    sourceBoneEvidence: [],
    restAnchorBySourceBoneKey: new Map(),
    reconciliation: {
      hydrated: true,
      sourceCount: sourceRigs.length,
      sourceBoneCount: [...sourceBoneToModelJointId.keys()].length,
      modelJointCount: joints.length,
      componentCount: forest.components.length,
      acceptedAttachments: edges.filter(edge =>
        edge.relationshipType === 'attachment').length,
      modelReferenceRadius: Number(data.model_reference_radius),
    },
  };
}

/** Select the persisted ModelRig when it is valid, otherwise build one. */
export async function loadOrBuildModelRig({load, hydrate, build} = {}) {
  let saved = null;
  try {
    saved = await load?.();
  } catch (_error) {
    saved = null;
  }
  let restored = null;
  try {
    restored = saved ? hydrate?.(saved) : null;
  } catch (_error) {
    restored = null;
  }
  if (restored) return {modelRig: restored, hydratedFromCache: true};
  return {
    modelRig: await build?.(),
    hydratedFromCache: false,
  };
}
