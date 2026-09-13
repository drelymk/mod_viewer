export const MODEL_RIG_VERSION = 1;
export const MODEL_RIG_BUILDER_VERSION = 1;

function stableNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Number(number.toPrecision(9));
}

function stableVector(value, length) {
  if (!Array.isArray(value) || value.length !== length) return null;
  const vector = value.map(stableNumber);
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

function serializedMember(member) {
  return {
    source_key: String(member?.sourceKey || ""),
    source_bone_key: String(member?.sourceBoneKey || ""),
    bone_id: Number(member?.boneId),
  };
}

export function serializeModelRig(rig, {
  sourceRigs = rig?.sourceRigs || [],
} = {}) {
  return {
    version: MODEL_RIG_VERSION,
    builder_version: MODEL_RIG_BUILDER_VERSION,
    source_table: serializedSourceTable(sourceRigs),
    joints: (Array.isArray(rig?.joints) ? rig.joints : []).map((joint, fallbackId) => ({
      joint_id: Number.isInteger(joint?.jointId) ? joint.jointId : fallbackId,
      members: Array.isArray(joint?.members) ? joint.members.map(serializedMember) : [],
      parent_id: Number.isInteger(joint?.parentId) ? joint.parentId : null,
      rest_center: stableVector(joint?.restCenter, 3) || [0, 0, 0],
      rest_pivot: stableVector(joint?.restPivot, 3) || [0, 0, 0],
      rest_frame: stableVector(joint?.restFrame, 4) || [0, 0, 0, 1],
    })),
  };
}

function validInteger(value, minimum = 0) {
  return Number.isInteger(value) && value >= minimum;
}

function validMember(member) {
  return Boolean(
    member &&
      typeof member.source_key === "string" &&
      typeof member.source_bone_key === "string" &&
      member.source_key.length > 0 &&
      member.source_bone_key.length > 0 &&
      validInteger(member.bone_id),
  );
}

function deriveEdges(joints) {
  return joints
    .filter((joint) => joint.parentId !== null)
    .map((joint) => ({
      jointA: joint.parentId,
      jointB: joint.jointId,
      parentId: joint.parentId,
      childId: joint.jointId,
      relationshipType: "forest",
    }));
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
  if (!Array.isArray(data.joints) || data.joints.length === 0) return null;
  if (JSON.stringify(data.source_table) !==
      JSON.stringify(serializedSourceTable(sourceRigs))) return null;

  const joints = data.joints.map((rawJoint, index) => {
    if (!rawJoint || rawJoint.joint_id !== index) return null;
    if (!Array.isArray(rawJoint.members)
        || rawJoint.members.some((member) => !validMember(member))) return null;
    const parentId = rawJoint.parent_id === null ? null : rawJoint.parent_id;
    if (parentId !== null
        && (!validInteger(parentId) || parentId >= data.joints.length
          || parentId === index)) return null;
    const restCenter = stableVector(rawJoint.rest_center, 3);
    const restPivot = stableVector(rawJoint.rest_pivot, 3);
    const restFrame = stableVector(rawJoint.rest_frame, 4);
    if (!restCenter || !restPivot || !restFrame) return null;
    const members = rawJoint.members.map((member) => ({
      sourceKey: member.source_key,
      sourceBoneKey: member.source_bone_key,
      boneId: member.bone_id,
    }));
    return {
      jointId: index,
      jointKey: `joint=${index}`,
      signature: JSON.stringify(members.map((member) => member.sourceBoneKey).sort()),
      members,
      representativeMember: members[0] || null,
      parentId,
      childrenIds: [],
      restCenter,
      restPivot,
      restFrame,
    };
  });
  if (joints.some((joint) => !joint)) return null;

  const sourceBoneToModelJointId = new Map();
  for (const joint of joints) {
    for (const member of joint.members) {
      if (sourceBoneToModelJointId.has(member.sourceBoneKey)) return null;
      sourceBoneToModelJointId.set(member.sourceBoneKey, joint.jointId);
    }
  }

  const edges = deriveEdges(joints);
  const forest = rebuildForest(joints, edges);
  if (!forest) return null;

  return {
    modelReferenceRadius: 0,
    sourceRigs,
    joints,
    edges,
    forestEdges: edges,
    components: forest.components,
    componentByJointId: forest.componentByJointId,
    parentById: forest.parentById,
    childrenById: forest.childrenById,
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
      acceptedAttachments: 0,
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
