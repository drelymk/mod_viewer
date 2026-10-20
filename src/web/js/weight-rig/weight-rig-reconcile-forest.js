// Model joints, deterministic spanning forests and synchronous root orientation.
import { EPSILON, evidenceVector, sourceBoneKey, edgeScore, number } from './weight-rig-reconcile-evidence.js';
import { CROSS_SOURCE_STRICT_DISTANCE } from './weight-rig-reconcile-matching.js';

class ComponentUnionFind {
  constructor(ids) {
    this.parent = new Map(ids.map((id) => [id, id]));
  }

  find(id) {
    const parent = this.parent.get(id);
    if (parent === undefined || parent === id) return parent;
    const root = this.find(parent);
    this.parent.set(id, root);
    return root;
  }

  union(left, right) {
    const leftRoot = this.find(left);
    const rightRoot = this.find(right);
    if (leftRoot === rightRoot) return false;
    this.parent.set(rightRoot, leftRoot);
    return true;
  }
}

function weightedAverage(points) {
  const valid = points.filter((item) => item.point);
  if (!valid.length) return null;
  const total = valid.reduce((sum, item) => sum + Math.max(EPSILON, item.weight), 0);
  return valid.reduce(
    (sum, item) => {
      const weight = Math.max(EPSILON, item.weight) / total;
      return [sum[0] + item.point[0] * weight, sum[1] + item.point[1] * weight, sum[2] + item.point[2] * weight];
    },
    [0, 0, 0],
  );
}

function medoid(points) {
  if (!points.length) return null;
  return points
    .map((point, index) => ({
      point,
      index,
      distance: points.reduce((sum, other) => sum + point.distanceTo(other), 0),
    }))
    .sort((left, right) => left.distance - right.distance || left.index - right.index)[0]
    .point.clone();
}

export function buildModelJoints(
  unionFind,
  evidenceByKey,
  strengthByKey,
  referenceRadius,
  { clusterOrder = null } = {},
) {
  const clusters = [...unionFind.clusters().values()]
    .map((members) => members.sort())
    .sort((left, right) =>
      clusterOrder
        ? clusterOrder.get(left[0]) - clusterOrder.get(right[0]) || left[0].localeCompare(right[0])
        : left[0].localeCompare(right[0]),
    );
  const keyToJoint = new Map();
  const joints = clusters.map((memberKeys, jointId) => {
    memberKeys.forEach((key) => keyToJoint.set(key, jointId));
    const members = memberKeys.map((key) => evidenceByKey.get(key)).filter(Boolean);
    const signature = JSON.stringify(memberKeys);
    const center = weightedAverage(
      members.map((evidence) => ({
        point: evidenceVector(evidence, '_weightedCenterVector', 'weightedCenter')?.toArray(),
        weight: Math.max(evidence.totalWeight, evidence.affectedVertexCount, 1),
      })),
    ) || [0, 0, 0];
    const pivots = members
      .map(
        (evidence) =>
          evidenceVector(evidence, '_jointPivotVector', 'jointPivot') ||
          (evidence.isRoot ? null : evidenceVector(evidence, '_restAnchorVector', 'restAnchor')),
      )
      .filter(Boolean);
    const pivotMedoid = medoid(pivots);
    const pivotTolerance = referenceRadius * CROSS_SOURCE_STRICT_DISTANCE;
    const acceptedPivots = pivotMedoid ? pivots.filter((pivot) => pivot.distanceTo(pivotMedoid) <= pivotTolerance) : [];
    const restPivot = weightedAverage(
      (acceptedPivots.length ? acceptedPivots : pivots).map((point) => ({ point: point.toArray(), weight: 1 })),
    );
    const representative =
      [...members].sort(
        (left, right) =>
          (strengthByKey.get(right.sourceBoneKey) || 0) - (strengthByKey.get(left.sourceBoneKey) || 0) ||
          right.affectedVertexCount - left.affectedVertexCount ||
          right.totalWeight - left.totalWeight ||
          left.sourceBoneKey.localeCompare(right.sourceBoneKey),
      )[0] || null;
    return {
      jointId,
      jointKey: `joint=${jointId}`,
      signature,
      members: members.map((evidence) => ({
        sourceKey: evidence.sourceKey,
        boneId: evidence.boneId,
        sourceBoneKey: evidence.sourceBoneKey,
      })),
      restCenter: center,
      restPivot: restPivot || center,
      restDirection: representative?.restDirection || null,
      restFrame: representative?.restFrame || [0, 0, 0, 1],
      parentId: null,
      childrenIds: [],
      representativeMember: representative
        ? {
            sourceKey: representative.sourceKey,
            boneId: representative.boneId,
            sourceBoneKey: representative.sourceBoneKey,
          }
        : null,
      evidence: {
        totalWeight: members.reduce((sum, item) => sum + item.totalWeight, 0),
        affectedVertexCount: members.reduce((sum, item) => sum + item.affectedVertexCount, 0),
        memberCount: members.length,
      },
    };
  });
  return { joints, keyToJoint };
}

function sourceEdgeIndex(edges) {
  const index = new Map();
  for (const edge of edges || []) {
    const key = unorderedPairKey(Number(edge.boneA), Number(edge.boneB));
    if (!index.has(key)) index.set(key, edge);
  }
  return index;
}

export function sourceModelEdges(sourceRigs, keyToJoint) {
  const edgeMap = new Map();
  for (const rig of sourceRigs) {
    const relationships = sourceEdgeIndex(rig.influenceGraph?.relationships);
    for (const component of rig?.inferredForest?.components || []) {
      const sourceEdges = sourceEdgeIndex(component.edges);
      const parentById = component.parentById || {};
      for (const [childValue, parentValue] of Object.entries(parentById)) {
        if (parentValue === null || parentValue === undefined) continue;
        const childBoneId = Number(childValue);
        const parentBoneId = Number(parentValue);
        const parentKey = sourceBoneKey(rig.sourceKey, parentBoneId);
        const childKey = sourceBoneKey(rig.sourceKey, childBoneId);
        const jointA = keyToJoint.get(parentKey);
        const jointB = keyToJoint.get(childKey);
        if (!Number.isInteger(jointA) || !Number.isInteger(jointB) || jointA === jointB) continue;
        const left = Math.min(jointA, jointB);
        const right = Math.max(jointA, jointB);
        const key = `${left}:${right}`;
        const edge = edgeMap.get(key) || {
          jointA: left,
          jointB: right,
          sourceSupportCount: 0,
          sourceEdges: [],
          combinedTreeScore: 0,
          relationshipType: 'source',
        };
        const pair = unorderedPairKey(parentBoneId, childBoneId);
        const sourceEdge = sourceEdges.get(pair);
        const sourceRelationship = relationships.get(pair);
        const treeScore = edgeScore(sourceEdge);
        edge.sourceEdges.push({
          sourceKey: String(rig.sourceKey),
          parentBoneId,
          childBoneId,
          treeEdgeScore: treeScore,
          jointCenter: sourceRelationship?.jointCenter ? [...sourceRelationship.jointCenter] : null,
          jointWeightTotal: Number(sourceRelationship?.jointWeightTotal) || 0,
        });
        edge.combinedTreeScore += treeScore;
        edgeMap.set(key, edge);
      }
    }
  }
  edgeMap.forEach((edge) => {
    edge.sourceSupportCount = new Set(edge.sourceEdges.map((item) => item.sourceKey)).size;
    edge.weight = edge.sourceSupportCount * 2 + edge.combinedTreeScore;
  });
  return [...edgeMap.values()];
}

export function compareForestEdges(left, right) {
  return (
    right.weight - left.weight ||
    right.sourceSupportCount - left.sourceSupportCount ||
    right.combinedTreeScore - left.combinedTreeScore ||
    left.jointA - right.jointA ||
    left.jointB - right.jointB
  );
}

export function maximumSpanningForest(joints, edges) {
  const unionFind = new ComponentUnionFind(joints.map((joint) => joint.jointId));
  return [...edges].sort(compareForestEdges).filter((edge) => unionFind.union(edge.jointA, edge.jointB));
}

export function rootVotes(sourceRigs, keyToJoint) {
  const votes = new Map();
  for (const rig of sourceRigs) {
    for (const component of rig?.inferredForest?.components || []) {
      const root = Number(component.rootId);
      const jointId = keyToJoint.get(sourceBoneKey(rig.sourceKey, root));
      if (!Number.isInteger(jointId)) continue;
      votes.set(jointId, (votes.get(jointId) || 0) + Math.max(1, component.nodeIds?.length || 1));
    }
  }
  return votes;
}

export function orientModelForest(joints, edges, votes, rootOverrides = new Map()) {
  const adjacency = new Map(joints.map((joint) => [joint.jointId, []]));
  edges.forEach((edge) => {
    adjacency.get(edge.jointA)?.push({ edge, other: edge.jointB });
    adjacency.get(edge.jointB)?.push({ edge, other: edge.jointA });
  });
  adjacency.forEach((items) => items.sort((left, right) => left.other - right.other));
  const edgeByPair = new Map(edges.map((edge) => [unorderedPairKey(edge.jointA, edge.jointB), edge]));
  const componentById = new Map();
  const components = [];
  const unseen = new Set(joints.map((joint) => joint.jointId));
  while (unseen.size) {
    const start = Math.min(...unseen);
    const members = [];
    const queue = [start];
    unseen.delete(start);
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const current = queue[cursor];
      members.push(current);
      (adjacency.get(current) || []).forEach((item) => {
        if (!unseen.has(item.other)) return;
        unseen.delete(item.other);
        queue.push(item.other);
      });
    }
    members.sort((left, right) => left - right);
    const override = Number(rootOverrides.get(components.length));
    const rootId = members.includes(override)
      ? override
      : [...members].sort((left, right) => (votes.get(right) || 0) - (votes.get(left) || 0) || right - left)[0];
    const parentById = { [rootId]: null };
    const childrenById = { [rootId]: [] };
    const depthById = { [rootId]: 0 };
    const walk = [rootId];
    const visited = new Set([rootId]);
    for (let cursor = 0; cursor < walk.length; cursor += 1) {
      const parent = walk[cursor];
      (adjacency.get(parent) || []).forEach((item) => {
        if (visited.has(item.other)) return;
        visited.add(item.other);
        parentById[item.other] = parent;
        childrenById[parent] = childrenById[parent] || [];
        childrenById[parent].push(item.other);
        childrenById[item.other] = [];
        depthById[item.other] = depthById[parent] + 1;
        walk.push(item.other);
      });
    }
    const componentId = components.length;
    members.forEach((jointId) => componentById.set(jointId, componentId));
    components.push({
      componentId,
      nodeIds: members,
      rootId,
      parentById,
      childrenById,
      depthById,
      maxDepth: Math.max(...Object.values(depthById)),
      edges: members
        .flatMap((jointId) =>
          (adjacency.get(jointId) || [])
            .filter((item) => jointId < item.other)
            .map((item) => edgeByPair.get(`${jointId}:${item.other}`)),
        )
        .filter(Boolean),
    });
  }
  joints.forEach((joint) => {
    const componentId = componentById.get(joint.jointId);
    const component = components[componentId];
    joint.parentId = component?.parentById?.[joint.jointId] ?? null;
    joint.childrenIds = [...(component?.childrenById?.[joint.jointId] || [])];
  });
  return { components, componentByJointId: componentById };
}

export function orientModelForestWithAttachments(joints, sourceForest, edges) {
  const accessories = new Set(
    edges.filter((edge) => edge.relationshipType === 'attachment').map((edge) => edge.accessoryComponentId),
  );
  // Accepted attachments have one parent per accessory and decreasing parent
  // rank. Each resulting tree therefore retains exactly one source host root.
  const hostRoots = new Map(
    sourceForest.components
      .filter((component) => !accessories.has(component.componentId))
      .map((component) => [component.rootId, 1]),
  );
  const forest = accessories.size ? orientModelForest(joints, edges, hostRoots) : sourceForest;
  forest.components.forEach((component) => {
    component.edges = [];
  });
  // Keep accepted edge order in the published components, independently of
  // the traversal order used to determine parents and depths.
  edges.forEach((edge) => {
    const componentId = forest.componentByJointId.get(edge.jointA);
    if (componentId === forest.componentByJointId.get(edge.jointB)) {
      forest.components[componentId].edges.push(edge);
    }
  });
  return forest;
}

export function orientModelRigForest(joints, edges, rootOverrides = {}) {
  const overrides =
    rootOverrides instanceof Map
      ? rootOverrides
      : new Map(
          Object.entries(rootOverrides || {}).map(([componentId, jointId]) => [Number(componentId), Number(jointId)]),
        );
  return orientModelForest(joints || [], edges || [], new Map(), overrides);
}

export function componentSupport(component, joints) {
  return component.nodeIds.reduce((sum, id) => sum + number(joints[id]?.evidence?.totalWeight, 0), 0);
}

export function unorderedPairKey(leftId, rightId) {
  return `${Math.min(leftId, rightId)}:${Math.max(leftId, rightId)}`;
}
