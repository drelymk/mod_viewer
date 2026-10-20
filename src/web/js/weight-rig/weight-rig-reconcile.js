// Pure, conservative reconciliation of source-local inferred rigs. The
// authored skinning streams remain source-scoped; this module only builds a
// viewer-owned model graph from neutral geometry and source topology.

import { Quaternion, Vector3 } from 'three';
import { createWorkBudget } from './cooperative-scheduler.js';
import {
  EPSILON,
  evidenceVector,
  sourceBoneKey,
  edgeScore,
  number,
  crossPairKey,
  vectorFrom,
  quaternionArray,
  clamp,
  clockNow,
  collectSourceBoneEvidence,
  prepareSourceBoneEvidence,
  modelReferenceRadius,
  buildCrossSourceWeightEvidenceCooperative,
} from './weight-rig-reconcile-evidence.js';
import {
  CROSS_SOURCE_STRICT_DISTANCE,
  CROSS_SOURCE_CANDIDATE_DISTANCE,
  GuardedUnionFind,
  buildModelWideBoneIdentity,
  buildCandidates,
  runEquivalencePasses,
} from './weight-rig-reconcile-matching.js';
export {
  CROSS_SOURCE_STRONG_VERTEX_COUNT,
  CROSS_SOURCE_STRONG_WEIGHT_STRENGTH,
  sourceBoneKey,
} from './weight-rig-reconcile-evidence.js';
export {
  CROSS_SOURCE_CANDIDATE_DISTANCE,
  CROSS_SOURCE_STRICT_DISTANCE,
  CROSS_SOURCE_PROPAGATION_DISTANCE,
  CROSS_SOURCE_AMBIGUITY_MARGIN,
  CROSS_SOURCE_GRAPH_ALIGNMENT_MARGIN,
  CROSS_SOURCE_GRAPH_ALIGNMENT_MIN_SCORE,
} from './weight-rig-reconcile-matching.js';

export const MODEL_RIG_BUILDER_VERSION = 4;

export const CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN = 0.08;

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

function buildModelJoints(unionFind, evidenceByKey, strengthByKey, referenceRadius, { clusterOrder = null } = {}) {
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

function sourceModelEdges(sourceRigs, keyToJoint) {
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

function compareForestEdges(left, right) {
  return (
    right.weight - left.weight ||
    right.sourceSupportCount - left.sourceSupportCount ||
    right.combinedTreeScore - left.combinedTreeScore ||
    left.jointA - right.jointA ||
    left.jointB - right.jointB
  );
}

function maximumSpanningForest(joints, edges) {
  const unionFind = new ComponentUnionFind(joints.map((joint) => joint.jointId));
  return [...edges].sort(compareForestEdges).filter((edge) => unionFind.union(edge.jointA, edge.jointB));
}

function rootVotes(sourceRigs, keyToJoint) {
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

function orientModelForest(joints, edges, votes, rootOverrides = new Map()) {
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

function orientModelForestWithAttachments(joints, sourceForest, edges) {
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

function componentSupport(component, joints) {
  return component.nodeIds.reduce((sum, id) => sum + number(joints[id]?.evidence?.totalWeight, 0), 0);
}

function unorderedPairKey(leftId, rightId) {
  return `${Math.min(leftId, rightId)}:${Math.max(leftId, rightId)}`;
}

function aggregateJointCrossEvidence(left, right, crossEvidenceByPair) {
  const records = [];
  (left?.members || []).forEach((leftMember) => {
    (right?.members || []).forEach((rightMember) => {
      if (leftMember.sourceKey === rightMember.sourceKey) return;
      const record = crossEvidenceByPair.get(crossPairKey(leftMember.sourceBoneKey, rightMember.sourceBoneKey));
      if (record) records.push(record);
    });
  });
  if (!records.length) {
    return {
      matchedVertexCount: 0,
      weightedMatchStrength: 0,
      crossQuality: 0,
      supportedPairCount: 0,
    };
  }
  const totalWeight = records.reduce((sum, record) => sum + Math.max(EPSILON, record.weightedMatchStrength), 0);
  return {
    matchedVertexCount: records.reduce((sum, record) => sum + record.matchedVertexCount, 0),
    weightedMatchStrength: records.reduce((sum, record) => sum + record.weightedMatchStrength, 0),
    crossQuality:
      records.reduce((sum, record) => sum + record.overlapScore * Math.max(EPSILON, record.weightedMatchStrength), 0) /
      totalWeight,
    supportedPairCount: records.filter((record) => record.matchedVertexCount > 0).length,
  };
}

async function aggregateComponentCrossEvidence(
  component,
  target,
  joints,
  crossEvidenceByPair,
  referenceRadius,
  jointEvidenceByPair = null,
  checkpoint,
) {
  if (!crossEvidenceByPair.size) {
    return {
      matchedVertexCount: 0,
      weightedMatchStrength: 0,
      crossQuality: 0,
      supportedJointPairCount: 0,
      nearestSupportedDistance: null,
      supportedTargetCountByAccessory: new Map(),
    };
  }
  const supported = [];
  let pairCount = 0;
  for (const accessoryId of component.nodeIds) {
    for (const targetId of target.nodeIds) {
      if ((pairCount++ & 255) === 0) await checkpoint();
      const key = unorderedPairKey(accessoryId, targetId);
      const evidence =
        jointEvidenceByPair?.get(key) ||
        aggregateJointCrossEvidence(joints[accessoryId], joints[targetId], crossEvidenceByPair);
      jointEvidenceByPair?.set(key, evidence);
      if (!(evidence.matchedVertexCount > 0)) continue;
      const accessoryCenter = vectorFrom(joints[accessoryId]?.restCenter);
      const targetCenter = vectorFrom(joints[targetId]?.restCenter);
      const distance =
        accessoryCenter && targetCenter ? accessoryCenter.distanceTo(targetCenter) / referenceRadius : Infinity;
      supported.push({ accessoryId, targetId, distance, evidence });
    }
  }
  const supportedTargetCountByAccessory = new Map();
  supported.forEach((item) =>
    supportedTargetCountByAccessory.set(
      item.accessoryId,
      (supportedTargetCountByAccessory.get(item.accessoryId) || 0) + 1,
    ),
  );
  const totalWeight = supported.reduce((sum, item) => sum + Math.max(EPSILON, item.evidence.weightedMatchStrength), 0);
  return {
    matchedVertexCount: supported.reduce((sum, item) => sum + item.evidence.matchedVertexCount, 0),
    weightedMatchStrength: supported.reduce((sum, item) => sum + item.evidence.weightedMatchStrength, 0),
    crossQuality:
      totalWeight > EPSILON
        ? supported.reduce(
            (sum, item) => sum + item.evidence.crossQuality * Math.max(EPSILON, item.evidence.weightedMatchStrength),
            0,
          ) / totalWeight
        : 0,
    supportedJointPairCount: supported.length,
    nearestSupportedDistance: supported.length ? Math.min(...supported.map((item) => item.distance)) : null,
    supportedTargetCountByAccessory,
  };
}

function sourceKeysForJoint(joint) {
  return [...new Set((joint?.members || []).map((member) => String(member.sourceKey ?? '')).filter(Boolean))].sort();
}

function sourceWitnessesForJoints(targetJoint, accessoryJoint, sourceKeysByJointId = null) {
  const witnesses = new Set();
  const targetSourceKeys = sourceKeysByJointId?.get(targetJoint?.jointId) || sourceKeysForJoint(targetJoint);
  const accessorySourceKeys = sourceKeysByJointId?.get(accessoryJoint?.jointId) || sourceKeysForJoint(accessoryJoint);
  for (const targetSourceKey of targetSourceKeys) {
    for (const accessorySourceKey of accessorySourceKeys) {
      if (targetSourceKey === accessorySourceKey) {
        continue;
      }
      witnesses.add(`${targetSourceKey}\u0000${accessorySourceKey}`);
    }
  }
  return [...witnesses].sort().map((value) => {
    const separator = value.indexOf('\u0000');
    return {
      targetSourceKey: value.slice(0, separator),
      accessorySourceKey: value.slice(separator + 1),
    };
  });
}

async function attachmentCandidates(joints, forest, referenceRadius, crossEvidenceByPair, checkpoint) {
  const candidates = [];
  const components = forest.components;
  const componentEvidenceByPair = new Map();
  const jointEvidenceByPair = new Map();
  const sourceKeysByJointId = new Map(joints.map((joint) => [joint.jointId, sourceKeysForJoint(joint)]));
  const sourceWitnessesByJointPair = new Map();
  const supportByComponentId = new Map(
    components.map((component) => [component.componentId, componentSupport(component, joints)]),
  );
  const rankByComponentId = new Map(
    [...components]
      .sort(
        (left, right) =>
          supportByComponentId.get(right.componentId) - supportByComponentId.get(left.componentId) ||
          right.nodeIds.length - left.nodeIds.length ||
          left.componentId - right.componentId,
      )
      .map((component, rank) => [component.componentId, rank]),
  );
  const jointDescriptorById = new Map(
    joints.map((joint) => [
      joint.jointId,
      {
        anchor: vectorFrom(joint.restCenter),
        direction: vectorFrom(joint.restDirection),
      },
    ]),
  );
  const componentEvidenceFor = async (accessory, target) => {
    const key = `${accessory.componentId}:${target.componentId}`;
    if (!componentEvidenceByPair.has(key)) {
      componentEvidenceByPair.set(
        key,
        await aggregateComponentCrossEvidence(
          accessory,
          target,
          joints,
          crossEvidenceByPair,
          referenceRadius,
          jointEvidenceByPair,
          checkpoint,
        ),
      );
    }
    return componentEvidenceByPair.get(key);
  };
  const directions = new Map(
    [...jointDescriptorById.entries()].map(([jointId, descriptor]) => [jointId, descriptor.direction]),
  );
  // The model joint direction is represented by the selected source member's
  // rest frame +Y. Keeping this derivation here avoids inventing labels or
  // changing the source-local rest-frame contract.
  joints.forEach((joint) => {
    if (directions.get(joint.jointId)) return;
    const frame = new Quaternion(...quaternionArray(joint.restFrame));
    directions.set(joint.jointId, new Vector3(0, 1, 0).applyQuaternion(frame).normalize());
  });
  let pairCount = 0;
  for (const accessory of components) {
    await checkpoint();
    const accessorySupport = supportByComponentId.get(accessory.componentId) || 0;
    for (const target of components) {
      if (target.componentId === accessory.componentId) continue;
      const targetSupport = supportByComponentId.get(target.componentId) || 0;
      // Attach smaller inferred components to a larger body. This also makes
      // the direction of an attachment deterministic when two disconnected
      // components have identical synthetic support in a test fixture.
      if (rankByComponentId.get(target.componentId) >= rankByComponentId.get(accessory.componentId)) continue;
      const componentEvidence = await componentEvidenceFor(accessory, target);
      for (const targetId of target.nodeIds) {
        const targetJoint = joints[targetId];
        const targetAnchor = jointDescriptorById.get(targetId)?.anchor;
        if (!targetAnchor) continue;
        for (const accessoryId of accessory.nodeIds) {
          if ((pairCount++ & 255) === 0) await checkpoint();
          const accessoryJoint = joints[accessoryId];
          const accessoryAnchor = jointDescriptorById.get(accessoryId)?.anchor;
          if (!accessoryAnchor) continue;
          const witnessKey = unorderedPairKey(targetId, accessoryId);
          let sourceWitnesses = sourceWitnessesByJointPair.get(witnessKey);
          if (!sourceWitnesses) {
            sourceWitnesses = sourceWitnessesForJoints(targetJoint, accessoryJoint, sourceKeysByJointId);
            sourceWitnessesByJointPair.set(witnessKey, sourceWitnesses);
          }
          if (!sourceWitnesses.length) continue;
          const towardAccessory = accessoryAnchor.clone().sub(targetAnchor);
          const distance = towardAccessory.length();
          if (distance <= EPSILON) continue;
          const normalizedDistance = distance / referenceRadius;
          if (normalizedDistance > CROSS_SOURCE_CANDIDATE_DISTANCE) continue;
          const accessoryDirection = directions.get(accessoryId);
          const directionAlignment = accessoryDirection
            ? Math.abs(accessoryDirection.dot(towardAccessory.normalize()))
            : null;
          const pairEvidence =
            jointEvidenceByPair.get(witnessKey) ||
            aggregateJointCrossEvidence(targetJoint, accessoryJoint, crossEvidenceByPair);
          jointEvidenceByPair.set(witnessKey, pairEvidence);
          const nearbyTargetAgreement = Math.max(
            0,
            (componentEvidence.supportedTargetCountByAccessory.get(accessoryId) || 0) -
              (pairEvidence.matchedVertexCount > 0 ? 1 : 0),
          );
          const targetChildren = targetJoint.childrenIds || [];
          const targetDirection = directions.get(targetId);
          const targetTopology = targetChildren.length ? 1 : 0.5;
          const supportScore = targetSupport / Math.max(targetSupport, accessorySupport, EPSILON);
          const distanceScore = clamp(1 - normalizedDistance / CROSS_SOURCE_CANDIDATE_DISTANCE);
          const componentSupportScore =
            clamp(componentEvidence.matchedVertexCount / 32) * 0.35 +
            clamp(componentEvidence.weightedMatchStrength / 2) * 0.25 +
            componentEvidence.crossQuality * 0.2 +
            clamp(componentEvidence.supportedJointPairCount / 3) * 0.2;
          const endpointEvidenceScore =
            clamp(pairEvidence.matchedVertexCount / 8) * 0.35 +
            clamp(pairEvidence.weightedMatchStrength / 0.5) * 0.25 +
            pairEvidence.crossQuality * 0.2 +
            clamp(nearbyTargetAgreement / 3) * 0.2;
          const endpointScore =
            distanceScore * 0.55 +
            (directionAlignment ?? 0.5) * 0.3 +
            supportScore * 0.1 +
            targetTopology * 0.05 +
            (accessory.rootId === accessoryId ? 0.15 : 0);
          const endpointCombinedScore = endpointScore * 0.65 + endpointEvidenceScore * 0.35;
          const score = componentSupportScore * 0.45 + endpointCombinedScore * 0.55;
          candidates.push({
            targetComponentId: target.componentId,
            accessoryComponentId: accessory.componentId,
            jointA: targetId,
            jointB: accessoryId,
            targetJointId: targetId,
            accessoryJointId: accessoryId,
            normalizedDistance,
            directionAlignment,
            score,
            componentScore: componentSupportScore,
            endpointScore: endpointCombinedScore,
            endpointEvidenceScore,
            targetDirection: targetDirection?.toArray() || null,
            componentMatchedVertexCount: componentEvidence.matchedVertexCount,
            componentWeightedMatchStrength: componentEvidence.weightedMatchStrength,
            componentCrossQuality: componentEvidence.crossQuality,
            componentSupportedJointPairCount: componentEvidence.supportedJointPairCount,
            nearestSupportedDistance: componentEvidence.nearestSupportedDistance,
            accessoryRoot: accessory.rootId === accessoryId,
            endpointMatchedVertexCount: pairEvidence.matchedVertexCount,
            endpointWeightedMatchStrength: pairEvidence.weightedMatchStrength,
            endpointCrossQuality: pairEvidence.crossQuality,
            nearbyTargetAgreement,
            sourceWitnesses,
          });
        }
      }
    }
  }
  candidates.sort(
    (left, right) =>
      right.score - left.score ||
      left.normalizedDistance - right.normalizedDistance ||
      left.jointA - right.jointA ||
      left.jointB - right.jointB ||
      left.accessoryComponentId - right.accessoryComponentId ||
      left.targetComponentId - right.targetComponentId,
  );
  return { candidates, rankByComponentId };
}

async function addAttachments(joints, sourceEdges, forest, referenceRadius, crossEvidenceByPair, checkpoint) {
  const { candidates, rankByComponentId } = await attachmentCandidates(
    joints,
    forest,
    referenceRadius,
    crossEvidenceByPair,
    checkpoint,
  );
  const diagnostics = [];
  const accepted = [];
  const candidatesByAccessory = new Map();
  candidates.forEach((candidate) => {
    const byTarget = candidatesByAccessory.get(candidate.accessoryComponentId) || new Map();
    const entries = byTarget.get(candidate.targetComponentId) || [];
    entries.push(candidate);
    byTarget.set(candidate.targetComponentId, entries);
    candidatesByAccessory.set(candidate.accessoryComponentId, byTarget);
  });
  const compareEndpoint = (left, right) =>
    right.endpointEvidenceScore - left.endpointEvidenceScore ||
    right.nearbyTargetAgreement - left.nearbyTargetAgreement ||
    right.endpointScore - left.endpointScore ||
    right.score - left.score ||
    left.normalizedDistance - right.normalizedDistance ||
    left.jointA - right.jointA ||
    left.jointB - right.jointB;
  const compareComponent = (left, right) =>
    right.componentScore - left.componentScore ||
    right.componentMatchedVertexCount - left.componentMatchedVertexCount ||
    right.componentWeightedMatchStrength - left.componentWeightedMatchStrength ||
    compareEndpoint(left, right);
  const groupScore = (group) => [...group].sort(compareComponent)[0];
  for (const [accessoryComponentId, byTarget] of candidatesByAccessory) {
    await checkpoint();
    const groups = [...byTarget.entries()]
      .map(([targetComponentId, group]) => ({
        targetComponentId,
        candidates: group,
        best: groupScore(group),
      }))
      .sort(
        (left, right) => compareComponent(left.best, right.best) || left.targetComponentId - right.targetComponentId,
      );
    const bestGroup = groups[0];
    if (!bestGroup) continue;
    const secondGroup = groups[1];
    if (
      secondGroup &&
      bestGroup.best.componentScore - secondGroup.best.componentScore < CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN &&
      bestGroup.best.score - secondGroup.best.score < CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN
    ) {
      groups
        .flatMap((group) => group.candidates)
        .forEach((candidate) =>
          diagnostics.push({
            ...candidate,
            decision: 'rejected',
            rejectionReason: candidate === bestGroup.best ? 'attachment_ambiguous' : 'attachment_component_competition',
          }),
        );
      continue;
    }
    const competing = [...bestGroup.candidates].sort(compareEndpoint);
    const best = competing[0];
    const second = competing[1];
    const evidenceWinner =
      second &&
      (best.endpointMatchedVertexCount > second.endpointMatchedVertexCount ||
        best.nearbyTargetAgreement > second.nearbyTargetAgreement ||
        best.endpointEvidenceScore - second.endpointEvidenceScore >= 0.1 ||
        (best.accessoryRoot && !second.accessoryRoot && best.componentMatchedVertexCount >= 8));
    if (second && best.score - second.score < CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN && !evidenceWinner) {
      competing.forEach((candidate) =>
        diagnostics.push({
          ...candidate,
          decision: 'rejected',
          rejectionReason: candidate === best ? 'attachment_ambiguous' : 'attachment_competition',
        }),
      );
      continue;
    }
    // One selected parent per accessory and strictly decreasing parent ranks
    // make the attachment graph a forest without a second spanning-tree pass.
    if (rankByComponentId.get(best.targetComponentId) >= rankByComponentId.get(accessoryComponentId)) {
      diagnostics.push({ ...best, decision: 'rejected', rejectionReason: 'attachment_cycle' });
      continue;
    }
    const edge = {
      jointA: best.jointA,
      jointB: best.jointB,
      targetComponentId: best.targetComponentId,
      accessoryComponentId: best.accessoryComponentId,
      targetJointId: best.targetJointId,
      accessoryJointId: best.accessoryJointId,
      sourceSupportCount: 0,
      sourceEdges: [],
      combinedTreeScore: best.score,
      relationshipType: 'attachment',
      weight: best.score,
      attachmentScore: best.score,
    };
    accepted.push(edge);
    candidates.forEach((candidate) => {
      if (candidate.accessoryComponentId !== accessoryComponentId) return;
      diagnostics.push({
        ...candidate,
        decision: candidate === best ? 'accepted' : 'rejected',
        rejectionReason:
          candidate === best
            ? null
            : candidate.targetComponentId === best.targetComponentId
              ? 'attachment_competition'
              : 'attachment_component_competition',
        survivedFinalForest: candidate === best,
      });
    });
  }
  return { edges: [...sourceEdges, ...accepted].sort(compareForestEdges), diagnostics };
}

async function assembleModelRigReconciliation(
  sourceRigs,
  evidenceByKey,
  referenceRadius,
  { useModelWideBoneIds = false, options = {}, checkpoint },
) {
  const identity = useModelWideBoneIds ? buildModelWideBoneIdentity(evidenceByKey) : null;
  const candidateBuild = identity
    ? {
        candidates: [],
        crossEvidenceByPair: new Map(),
      }
    : await buildCandidates(evidenceByKey, referenceRadius, options, checkpoint);
  const candidates = candidateBuild.candidates;
  const unionFind = identity?.unionFind || new GuardedUnionFind(evidenceByKey);
  const equivalence = identity
    ? {
        correspondenceStrength: new Map(),
        accepted: [],
        diagnostics: [],
      }
    : await runEquivalencePasses(candidates, evidenceByKey, unionFind, checkpoint);
  await checkpoint();
  const model = buildModelJoints(unionFind, evidenceByKey, equivalence.correspondenceStrength, referenceRadius, {
    clusterOrder: identity?.clusterOrder || null,
  });
  const sourceEdges = sourceModelEdges(sourceRigs, model.keyToJoint);
  const sourceForestEdges = maximumSpanningForest(model.joints, sourceEdges);
  const votes = rootVotes(sourceRigs, model.keyToJoint);
  const sourceForest = orientModelForest(model.joints, sourceForestEdges, votes);
  const attachments = await addAttachments(
    model.joints,
    sourceForestEdges,
    sourceForest,
    referenceRadius,
    candidateBuild.crossEvidenceByPair,
    checkpoint,
  );
  const finalEdges = attachments.edges;
  const finalForest = orientModelForestWithAttachments(model.joints, sourceForest, finalEdges);
  const survivingAttachments = finalEdges.filter((edge) => edge.relationshipType === 'attachment');
  await checkpoint();
  const attachmentDiagnostics = attachments.diagnostics.map((item) => ({
    ...item,
    left: { jointId: item.jointA },
    right: { jointId: item.jointB },
  }));
  const rejectedCandidates = [
    ...equivalence.diagnostics,
    ...attachmentDiagnostics.filter((item) => item.decision === 'rejected'),
  ];
  const unmatchedCount = [...unionFind.clusters().values()].filter((members) => members.length === 1).length;
  const ambiguousCount = rejectedCandidates.filter(
    (item) => item.rejectionReason === 'ambiguous' || item.rejectionReason === 'attachment_ambiguous',
  ).length;
  const mainComponent =
    [...finalForest.components].sort(
      (left, right) =>
        componentSupport(right, model.joints) - componentSupport(left, model.joints) ||
        right.nodeIds.length - left.nodeIds.length ||
        left.componentId - right.componentId,
    )[0] || null;
  const mainComponentId = mainComponent?.componentId ?? null;
  const unresolvedComponents = finalForest.components.filter((component) => component.componentId !== mainComponentId);
  const unresolvedSourceKeys = [
    ...new Set(
      unresolvedComponents.flatMap((component) =>
        component.nodeIds.flatMap((jointId) =>
          (model.joints[jointId]?.members || []).map((member) => member.sourceKey),
        ),
      ),
    ),
  ].sort();
  const reconciliation = {
    identityMode: identity ? 'model-wide-bone-id' : 'geometric-reconciliation',
    uniqueModelBoneIdCount: identity?.uniqueModelBoneIdCount ?? null,
    sourceCount: sourceRigs.length,
    sourceBoneCount: evidenceByKey.size,
    candidateCount: candidates.length,
    sampleBuildMs: identity ? 0 : null,
    spatialIndexMs: identity ? 0 : null,
    crossSourceMatchMs: identity ? 0 : null,
    modelJointCount: model.joints.length,
    equivalenceClusterCount: [...unionFind.clusters().values()].filter((members) => members.length > 1).length,
    equivalentSourceBoneCount: [...unionFind.clusters().values()]
      .filter((members) => members.length > 1)
      .reduce((sum, members) => sum + members.length, 0),
    attachmentCount: survivingAttachments.length,
    unmatchedCount,
    ambiguousCount,
    componentCount: finalForest.components.length,
    mainComponentId,
    unresolvedComponentCount: unresolvedComponents.length,
    unresolvedJointCount: unresolvedComponents.reduce((sum, component) => sum + component.nodeIds.length, 0),
    unresolvedSourceKeys,
    modelReferenceRadius: referenceRadius,
    joints: model.joints,
    acceptedEquivalences: equivalence.accepted.map((item) => ({ ...item })),
    acceptedAttachments: survivingAttachments,
    attachmentDiagnostics,
    rejectedCandidates,
  };
  return {
    modelReferenceRadius: referenceRadius,
    sourceBoneToModelJointMap: model.keyToJoint,
    joints: model.joints,
    edges: finalEdges,
    components: finalForest.components,
    componentByJointId: finalForest.componentByJointId,
    reconciliation,
  };
}

/**
 * Build Model Rig reconciliation with one cancellable work budget for vertex
 * matching and graph decisions. Identity mode only controls which evidence is
 * needed; both modes share the same guarded assembly and publication path.
 */
export async function buildModelRigReconciliationCooperative(
  sourceRigs = [],
  options = {},
  { budget = createWorkBudget(), isCurrent = () => true, timings = null } = {},
) {
  const rigs = [...sourceRigs]
    .filter((rig) => rig?.sourceKey !== undefined)
    .sort((left, right) => String(left.sourceKey).localeCompare(String(right.sourceKey)));
  const useModelWideBoneIds = rigs.length > 0 && rigs.every((rig) => rig.boneIdsModelWide === true);
  const stale = Symbol('stale reconciliation');
  const checkpoint = async () => {
    if (!isCurrent()) throw stale;
    await budget.checkpoint();
    if (!isCurrent()) throw stale;
  };
  try {
    await checkpoint();
    const evidenceByKey = new Map();
    for (const rig of rigs) {
      collectSourceBoneEvidence(rig).forEach((evidence, key) => evidenceByKey.set(key, evidence));
      await checkpoint();
    }
    prepareSourceBoneEvidence(evidenceByKey);
    const referenceRadius = Math.max(
      EPSILON,
      number(options.modelReferenceRadius, modelReferenceRadius(evidenceByKey)),
    );
    const activeSources = new Set([...evidenceByKey.values()].map((evidence) => evidence.sourceKey));
    const matchingRigs = rigs.filter((rig) => activeSources.has(String(rig.sourceKey)));
    if (timings && (useModelWideBoneIds || matchingRigs.length < 2)) {
      timings.sampleBuildMs = 0;
      timings.spatialIndexMs = 0;
      timings.crossSourceMatchMs = 0;
    }
    const crossEvidenceByPair = useModelWideBoneIds
      ? new Map()
      : await buildCrossSourceWeightEvidenceCooperative(matchingRigs, referenceRadius, checkpoint, timings);
    await checkpoint();
    const graphStartedAt = clockNow();
    const result = await assembleModelRigReconciliation(rigs, evidenceByKey, referenceRadius, {
      useModelWideBoneIds,
      options: { ...options, crossEvidenceByPair },
      checkpoint,
    });
    await checkpoint();
    if (timings) timings.graphBuildMs = clockNow() - graphStartedAt;
    return result;
  } catch (error) {
    if (error === stale) return null;
    throw error;
  }
}
