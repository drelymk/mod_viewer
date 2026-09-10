import {Vector3} from 'three';
import {candidateRelationshipEdges} from './weight-rig.js';

export const HUMANOID_HEAT_LIMBS = Object.freeze({
  left_arm: Object.freeze({
    keys: Object.freeze(['leftShoulder', 'leftElbow', 'leftHand']),
    upperDriverId: 'left_upper_arm', lowerDriverId: 'left_lower_arm', side: -1,
  }),
  right_arm: Object.freeze({
    keys: Object.freeze(['rightShoulder', 'rightElbow', 'rightHand']),
    upperDriverId: 'right_upper_arm', lowerDriverId: 'right_lower_arm', side: 1,
  }),
  left_leg: Object.freeze({
    keys: Object.freeze(['leftHip', 'leftKnee', 'leftFoot']),
    upperDriverId: 'left_upper_leg', lowerDriverId: 'left_lower_leg', side: -1,
  }),
  right_leg: Object.freeze({
    keys: Object.freeze(['rightHip', 'rightKnee', 'rightFoot']),
    upperDriverId: 'right_upper_leg', lowerDriverId: 'right_lower_leg', side: 1,
  }),
});

const EPSILON = 1e-8;
const BASE_CORRIDOR_RATIO = 0.11;
const MAX_RADIUS_RATIO = 0.07;
const TERMINAL_EXTENSION_RATIO = 0.12;
const CENTER_EXCLUSION_RATIO = 0.025;
const SEED_RADIUS_RATIO = 0.09;
const SEED_MIN_WEIGHT = 0.05;
const SEED_RELATIVE_WEIGHT = 0.12;
const SEED_BACKTRACK_TOLERANCE = 0.08;
const SEED_LATERAL_TOLERANCE_RATIO = 0.02;
const SEED_MAX_PROGRESS = 0.45;
const BACKTRACK_TOLERANCE = 0.08;
const BRANCH_PROGRESS_TOLERANCE = 0.22;
const BRANCH_LATERAL_TOLERANCE_RATIO = 0.035;
const MAX_SEEDS = 8;
const MAX_SEARCH_STATES = 4096;

function number(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function integer(value) {
  const result = Number(value);
  return Number.isInteger(result) ? result : null;
}

function vector(value, fallback = [0, 0, 0]) {
  if (value?.isVector3) return value.clone();
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    return new Vector3(
      number(value[0], fallback[0]), number(value[1], fallback[1]),
      number(value[2], fallback[2]));
  }
  return new Vector3(number(value?.x, fallback[0]), number(value?.y, fallback[1]),
    number(value?.z, fallback[2]));
}

function pointForControl(controlRig, key) {
  const value = controlRig?.controls?.[key];
  return vector(value?.position ?? value);
}

function frameVector(controlRig, key, fallback) {
  const result = vector(controlRig?.frame?.[key], fallback);
  return result.lengthSq() > EPSILON ? result.normalize() : vector(fallback);
}

function sourceBoneKey(sourceKey, boneId) {
  return `${String(sourceKey)}#bone=${Number(boneId)}`;
}

function mapValue(collection, key) {
  if (collection instanceof Map) return collection.get(key) ?? collection.get(String(key));
  return collection?.[key];
}

function nodeId(value) {
  return integer(value?.boneId ?? value);
}

function nodeSupport(node) {
  return Math.max(0, number(node?.totalWeight)
    || number(node?.affectedMeasure)
    || number(node?.affectedVertexCount));
}

function polylineFor(controlRig, limb) {
  const info = HUMANOID_HEAT_LIMBS[limb];
  if (!info) return null;
  const points = info.keys.map(key => pointForControl(controlRig, key));
  const lengths = points.slice(1).map((point, index) =>
    point.distanceTo(points[index]));
  const totalLength = lengths.reduce((sum, value) => sum + value, 0);
  if (!points.length || totalLength <= EPSILON) return null;
  return {limb, info, points, lengths, totalLength};
}

/** Project a point onto a control-rig limb polyline using arc-length progress. */
export function projectPointToHumanoidLimb(pointValue, curve) {
  if (!curve?.points?.length || curve.totalLength <= EPSILON) return null;
  const point = vector(pointValue);
  let offset = 0;
  let best = null;
  curve.lengths.forEach((length, segmentIndex) => {
    const start = curve.points[segmentIndex];
    const end = curve.points[segmentIndex + 1];
    const direction = end.clone().sub(start);
    const lengthSq = direction.lengthSq();
    const rawProjection = lengthSq > EPSILON
      ? point.clone().sub(start).dot(direction) / lengthSq : 0;
    const projection = Math.max(0, Math.min(1, rawProjection));
    const closestPoint = start.clone().addScaledVector(direction, projection);
    const distance = point.distanceTo(closestPoint);
    if (!best || distance < best.distance - 1e-7
        || (Math.abs(distance - best.distance) <= 1e-7
          && segmentIndex > best.segmentIndex)) {
      best = {
        progress: Math.max(0, Math.min(1,
          (offset + projection * length) / curve.totalLength)),
        distance,
        segmentIndex,
        rawProjection,
        closestPoint,
      };
    }
    offset += length;
  });
  return best;
}

function projectionForNode(node, curve) {
  const projection = projectPointToHumanoidLimb(node?.weightedCenter, curve);
  if (!projection) return null;
  return {
    ...projection,
    endpointDistance: vector(node?.weightedCenter)
      .distanceTo(curve.points[curve.points.length - 1]),
  };
}
function normalizedNodeMetadata(graph) {
  return new Map((graph?.nodes || []).map(node => {
    const boneId = nodeId(node);
    return [boneId, {
      ...node,
      boneId,
      weightedCenter: vector(node?.weightedCenter),
      weightedRadius: Math.max(0, number(node?.weightedRadius)),
    }];
  }).filter(([boneId]) => boneId !== null));
}

function corridorFor(node, projection, curve, height) {
  if (!projection || !node) return false;
  const radius = Math.min(node.weightedRadius, height * MAX_RADIUS_RATIO);
  const terminal = projection.progress >= .995
    && projection.endpointDistance <= height * TERMINAL_EXTENSION_RATIO;
  if (projection.distance > height * BASE_CORRIDOR_RATIO + radius
      + (terminal ? height * TERMINAL_EXTENSION_RATIO : 0)) return false;
  const right = frameVector(curve.controlRig, 'right', [1, 0, 0]);
  const pelvis = pointForControl(curve.controlRig, 'pelvis');
  const lateral = node.weightedCenter.clone().sub(pelvis).dot(right);
  return curve.info.side * lateral >= -height * CENTER_EXCLUSION_RATIO;
}

function projectionMetadata(node, curve, height) {
  const projection = projectionForNode(node, curve);
  if (!projection) return null;
  return {
    ...projection,
    corridor: corridorFor(node, projection, curve, height),
  };
}
function edgeKey(left, right) {
  const a = Number(left);
  const b = Number(right);
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function edgeStrength(edge) {
  return Math.max(0, number(edge?.treeEdgeScore)
    || number(edge?.containment) || number(edge?.jaccard)
    || number(edge?.productOverlap));
}

function adjacencyFor(graph) {
  const nodes = normalizedNodeMetadata(graph);
  const edges = candidateRelationshipEdges(graph);
  const edgeByKey = new Map();
  const adjacency = new Map([...nodes.keys()].map(id => [id, []]));
  edges.forEach(edge => {
    const left = nodeId(edge?.boneA);
    const right = nodeId(edge?.boneB);
    if (left === null || right === null || left === right
        || !nodes.has(left) || !nodes.has(right)) return;
    const key = edgeKey(left, right);
    edgeByKey.set(key, edge);
    adjacency.get(left).push(right);
    adjacency.get(right).push(left);
  });
  adjacency.forEach(neighbors => neighbors.sort((left, right) => left - right));
  return {nodes, edges, edgeByKey, adjacency};
}

function relationshipProjection(edge, left, right, curve) {
  const center = edge?.jointCenter?.length >= 3
    ? vector(edge.jointCenter)
    : left.weightedCenter.clone().add(right.weightedCenter).multiplyScalar(.5);
  const projection = projectPointToHumanoidLimb(center, curve);
  return projection ? {...projection, sourcePoint: center} : null;
}

function usableHeatEdge(edge, left, right, curve, metadata, height) {
  if (!edge || !left || !right) return {accepted: false, reason: 'weak_heat_connection'};
  const leftProjection = metadata.get(left.boneId);
  const rightProjection = metadata.get(right.boneId);
  const overlapProjection = relationshipProjection(edge, left, right, curve);
  const overlapNode = {
    weightedCenter: overlapProjection?.sourcePoint || curve.points[0],
    weightedRadius: 0,
  };
  if (!leftProjection?.corridor || !rightProjection?.corridor
      || !corridorFor(overlapNode, overlapProjection, curve, height)) {
    return {accepted: false, reason: 'overlap_outside_corridor'};
  }
  return {accepted: true, strength: edgeStrength(edge), overlapProjection};
}

function seedWeightsFromSurface(sourceRig, anchor, height, nodes) {
  const totals = new Map();
  const radiusSquared = (height * SEED_RADIUS_RATIO) ** 2;
  (sourceRig?.vertexEvidence || []).forEach(evidence => {
    const positions = evidence?.positions;
    const indices = evidence?.indices;
    const weights = evidence?.weights;
    const influenceCount = integer(evidence?.influenceCount);
    if (!positions || !indices || !weights || !influenceCount
        || influenceCount <= 0) return;
    const vertexCount = Math.min(Math.floor(positions.length / 3),
      Math.floor(indices.length / influenceCount),
      Math.floor(weights.length / influenceCount));
    for (let vertex = 0; vertex < vertexCount; vertex += 1) {
      const position = vector([
        positions[vertex * 3], positions[vertex * 3 + 1],
        positions[vertex * 3 + 2],
      ]);
      if (position.distanceToSquared(anchor) > radiusSquared) continue;
      const offset = vertex * influenceCount;
      for (let influence = 0; influence < influenceCount; influence += 1) {
        const boneId = integer(indices[offset + influence]);
        const weight = number(weights[offset + influence]);
        if (boneId === null || weight <= 0 || !nodes.has(boneId)) continue;
        totals.set(boneId, (totals.get(boneId) || 0) + weight);
      }
    }
  });
  return totals;
}

function rawAnchorProjection(pointValue, curve) {
  const start = curve.points[0];
  const end = curve.points[1];
  const direction = end.clone().sub(start);
  const lengthSquared = direction.lengthSq();
  return lengthSquared > EPSILON
    ? vector(pointValue).clone().sub(start).dot(direction) / lengthSquared
    : 0;
}

function seedCandidates(sourceRig, nodes, metadata, curve, height) {
  const weights = seedWeightsFromSurface(sourceRig, curve.points[0], height, nodes);
  const maximum = [...weights.values()].reduce((max, value) =>
    Math.max(max, value), 0);
  const minimum = Math.max(SEED_MIN_WEIGHT, maximum * SEED_RELATIVE_WEIGHT);
  const anchorOutward = lateralDistance({weightedCenter: curve.points[0]}, curve);
  return [...weights.entries()].filter(([, weight]) => weight >= minimum)
    .map(([boneId, weight]) => nodes.get(boneId))
    .filter(node => {
      const projection = metadata.get(node.boneId);
      return projection?.corridor
        && projection.progress <= SEED_MAX_PROGRESS
        && rawAnchorProjection(node.weightedCenter, curve)
          >= -SEED_BACKTRACK_TOLERANCE
        && lateralDistance(node, curve)
          >= anchorOutward - height * SEED_LATERAL_TOLERANCE_RATIO;
    })
    .sort((left, right) => {
      const a = metadata.get(left.boneId);
      const b = metadata.get(right.boneId);
      return (weights.get(right.boneId) - weights.get(left.boneId))
        || (a.distance / height - b.distance / height)
        || left.boneId - right.boneId;
    }).slice(0, MAX_SEEDS);
}

function endpointFor(node, metadata, height) {
  const projection = metadata.get(node.boneId);
  return !!projection && (projection.progress >= .8
    || projection.endpointDistance <= height * (.16 + MAX_RADIUS_RATIO));
}

function lateralDistance(node, curve) {
  const right = frameVector(curve.controlRig, 'right', [1, 0, 0]);
  const pelvis = pointForControl(curve.controlRig, 'pelvis');
  return curve.info.side * node.weightedCenter.clone().sub(pelvis).dot(right);
}

function betterState(left, right) {
  return left.quality > right.quality
    || (left.quality === right.quality && left.path.join(',') < right.path.join(','));
}

function searchPath(seed, nodes, metadata, adjacency, edgeByKey, curve, height) {
  const queue = [{path: [seed.boneId], quality: 0, edgeStrength: 0,
    backtrack: 0}];
  const bestByNode = new Map();
  let best = null;
  let stateCount = 0;
  while (queue.length && stateCount < MAX_SEARCH_STATES) {
    const state = queue.shift();
    stateCount += 1;
    const currentId = state.path[state.path.length - 1];
    const currentNode = nodes.get(currentId);
    const currentProjection = metadata.get(currentId);
    if (!currentNode || !currentProjection) continue;
    if (endpointFor(currentNode, metadata, height)) {
      const candidate = {
        ...state,
        complete: currentProjection.progress >= .8,
        maxProgress: currentProjection.progress,
        quality: (currentProjection.progress * 10)
          + (currentProjection.progress >= .8 ? 100 : 0)
          + state.edgeStrength * 3
          - state.backtrack * 4,
      };
      if (!best || betterState(candidate, best)) best = candidate;
    }
    if (state.path.length >= nodes.size) continue;
    (adjacency.get(currentId) || []).forEach(nextId => {
      if (state.path.includes(nextId)) return;
      const nextNode = nodes.get(nextId);
      const nextProjection = metadata.get(nextId);
      const edge = edgeByKey.get(edgeKey(currentId, nextId));
      if (!nextNode || !nextProjection || !edge) return;
      const connection = usableHeatEdge(edge, currentNode, nextNode,
        curve, metadata, height);
      if (!connection.accepted) return;
      const delta = nextProjection.progress - currentProjection.progress;
      if (delta < -BACKTRACK_TOLERANCE || delta > .55
          || lateralDistance(nextNode, curve)
            < lateralDistance(currentNode, curve)
              - height * BRANCH_LATERAL_TOLERANCE_RATIO) return;
      const backtrack = state.backtrack + Math.max(0, -delta);
      const edgeScore = state.edgeStrength + connection.strength;
      const quality = nextProjection.progress * 10 + edgeScore * 3
        - backtrack * 4;
      const prior = bestByNode.get(nextId);
      if (prior !== undefined && prior >= quality) return;
      bestByNode.set(nextId, quality);
      queue.push({path: [...state.path, nextId], quality, edgeStrength: edgeScore,
        backtrack});
    });
  }
  return best || {path: [seed.boneId], complete: false,
    maxProgress: metadata.get(seed.boneId)?.progress || 0,
    quality: metadata.get(seed.boneId)?.progress || 0,
    edgeStrength: 0, backtrack: 0};
}

function absorbBranches(mainPath, nodes, metadata, adjacency, edgeByKey, curve, height) {
  const accepted = new Set(mainPath);
  const branchMembers = new Set();
  const queue = mainPath.map(boneId => ({boneId, hops: 0}));
  while (queue.length) {
    const current = queue.shift();
    if (current.hops >= 2) continue;
    const currentProjection = metadata.get(current.boneId);
    (adjacency.get(current.boneId) || []).forEach(nextId => {
      if (accepted.has(nextId)) return;
      const nextNode = nodes.get(nextId);
      const nextProjection = metadata.get(nextId);
      const edge = edgeByKey.get(edgeKey(current.boneId, nextId));
      if (!nextNode || !nextProjection || !edge) return;
      const progressDelta = nextProjection.progress - currentProjection.progress;
      if (progressDelta < -BACKTRACK_TOLERANCE
          || progressDelta > BRANCH_PROGRESS_TOLERANCE
          || lateralDistance(nextNode, curve)
            < lateralDistance(nodes.get(current.boneId), curve)
              - height * BRANCH_LATERAL_TOLERANCE_RATIO) return;
      const connection = usableHeatEdge(edge, nodes.get(current.boneId), nextNode,
        curve, metadata, height);
      if (!connection.accepted) return;
      accepted.add(nextId);
      branchMembers.add(nextId);
      queue.push({boneId: nextId, hops: current.hops + 1});
    });
  }
  return {accepted, branchMembers};
}

function driverForProjection(limb, projection) {
  return projection?.segmentIndex === 1
    ? limb.info.lowerDriverId : limb.info.upperDriverId;
}

function classifySource(sourceRig, controlRig) {
  const graph = sourceRig?.influenceGraph;
  const graphData = adjacencyFor(graph);
  const height = Math.max(number(controlRig?.frame?.height), EPSILON);
  const sourceResults = {};
  const nodeAssignments = new Map();
  Object.keys(HUMANOID_HEAT_LIMBS).forEach(limbName => {
    const curve = polylineFor(controlRig, limbName);
    if (!curve) return;
    curve.controlRig = controlRig;
    const metadata = new Map([...graphData.nodes.values()].map(node => [
      node.boneId, projectionMetadata(node, curve, height)]));
    const seeds = seedCandidates(sourceRig, graphData.nodes, metadata,
      curve, height);
    let bestPath = null;
    seeds.forEach(seed => {
      const path = searchPath(seed, graphData.nodes, metadata,
        graphData.adjacency, graphData.edgeByKey, curve, height);
      if (!bestPath || betterState(path, bestPath)) bestPath = path;
    });
    // A close center is not heat evidence by itself. Require at least one
    // accepted relationship before a source bone can claim a limb.
    const mainPath = bestPath?.path?.length >= 2 ? bestPath.path : [];
    const absorbed = absorbBranches(mainPath, graphData.nodes, metadata,
      graphData.adjacency, graphData.edgeByKey, curve, height);
    const maxProgress = mainPath.reduce((max, boneId) => Math.max(max,
      metadata.get(boneId)?.progress || 0), 0);
    const complete = !!bestPath?.complete && maxProgress >= .8;
    const rejected = [];
    graphData.nodes.forEach((node, boneId) => {
      if (absorbed.accepted.has(boneId)) return;
      const projection = metadata.get(boneId);
      rejected.push({boneId, reason: !seeds.length ? 'no_heat_seed'
        : projection?.corridor
          ? (mainPath.length ? 'wrong_progress_direction' : 'endpoint_not_reached')
          : 'outside_limb_corridor'});
    });
    const pathSet = new Set(mainPath);
    const assignments = [];
    absorbed.accepted.forEach(boneId => {
      if (!complete) return;
      const node = graphData.nodes.get(boneId);
      const projection = metadata.get(boneId);
      if (!node || !projection) return;
      const assignment = {
        sourceKey: String(sourceRig.sourceKey), boneId,
        sourceBoneKey: sourceBoneKey(sourceRig.sourceKey, boneId),
        limbRole: limbName,
        driverId: driverForProjection({info: HUMANOID_HEAT_LIMBS[limbName]}, projection),
        progress: projection.progress,
        segmentIndex: projection.segmentIndex,
        totalWeight: nodeSupport(node),
        confidence: complete ? 'high' : maxProgress >= .5 ? 'medium' : 'low',
        pathMember: pathSet.has(boneId),
        branchMember: absorbed.branchMembers.has(boneId),
      };
      assignments.push(assignment);
      const list = nodeAssignments.get(boneId) || [];
      list.push(assignment);
      nodeAssignments.set(boneId, list);
    });
    sourceResults[limbName] = {
      seedBoneIds: seeds.map(node => node.boneId),
      seedReason: seeds.length ? null : 'no_heat_seed',
      endpointBoneIds: [...graphData.nodes.values()]
        .filter(node => endpointFor(node, metadata, height)).map(node => node.boneId),
      mainPathBoneIds: mainPath,
      branchBoneIds: [...absorbed.branchMembers].sort((a, b) => a - b),
      rejectedBoneIds: rejected.map(item => item.boneId).sort((a, b) => a - b),
      rejected: rejected.sort((a, b) => a.boneId - b.boneId),
      maxProgress,
      complete,
      assignments,
    };
  });
  const sourceBoneAssignments = new Map();
  const conflicts = [];
  nodeAssignments.forEach((assignments, boneId) => {
    const roles = [...new Set(assignments.map(item => item.limbRole))];
    if (roles.length > 1) {
      conflicts.push({sourceKey: String(sourceRig.sourceKey), boneId,
        reason: 'mixed_primary_binding', limbRoles: roles.sort()});
      return;
    }
    const selected = [...assignments].sort((left, right) =>
      Number(right.pathMember) - Number(left.pathMember)
      || right.progress - left.progress || right.totalWeight - left.totalWeight
      || left.driverId.localeCompare(right.driverId))[0];
    sourceBoneAssignments.set(selected.sourceBoneKey, selected);
  });
  return {sourceResults, sourceBoneAssignments, conflicts, graphData};
}

function aggregateModelJointAssignments(sourceAssignments, modelRig) {
  const grouped = new Map();
  sourceAssignments.forEach(assignment => {
    const jointId = integer(mapValue(modelRig?.sourceBoneToModelJointId,
      assignment.sourceBoneKey));
    if (jointId === null) return;
    const entries = grouped.get(jointId) || [];
    entries.push(assignment);
    grouped.set(jointId, entries);
  });
  const assignments = new Map();
  const conflicts = [];
  grouped.forEach((entries, jointId) => {
    const primaryRoles = [...new Set(entries.map(entry => entry.limbRole))];
    if (primaryRoles.length > 1) {
      conflicts.push({jointId, reason: 'mixed_primary_binding',
        limbRoles: primaryRoles.sort(), sourceBoneKeys: entries.map(
          entry => entry.sourceBoneKey).sort()});
      return;
    }
    const ordered = [...entries].sort((left, right) => left.progress - right.progress
      || left.sourceBoneKey.localeCompare(right.sourceBoneKey));
    const total = ordered.reduce((sum, entry) => sum + Math.max(1,
      entry.totalWeight), 0);
    let accumulated = 0;
    let selected = ordered[0];
    ordered.some(entry => {
      accumulated += Math.max(1, entry.totalWeight);
      if (accumulated >= total * .5) {
        selected = entry;
        return true;
      }
      return false;
    });
    assignments.set(jointId, {
      ...selected,
      sourceBoneKeys: entries.map(entry => entry.sourceBoneKey).sort(),
      memberCount: entries.length,
    });
  });
  return {assignments, conflicts};
}

/** Classify each source's heat graph against the shared humanoid rest rig. */
export function buildHumanoidHeatBinding({controlRig, sourceRigs = [], modelRig} = {}) {
  const started = typeof performance !== 'undefined' && performance.now
    ? performance.now() : Date.now();
  const sourceResults = {};
  const sourceBoneAssignments = new Map();
  const conflicts = [];
  [...sourceRigs].sort((left, right) => String(left?.sourceKey)
    .localeCompare(String(right?.sourceKey))).forEach(sourceRig => {
    const result = classifySource(sourceRig, controlRig);
    sourceResults[String(sourceRig.sourceKey)] = result.sourceResults;
    result.sourceBoneAssignments.forEach((value, key) =>
      sourceBoneAssignments.set(key, value));
    conflicts.push(...result.conflicts);
  });
  const model = aggregateModelJointAssignments(sourceBoneAssignments, modelRig);
  conflicts.push(...model.conflicts);
  const runtimeMs = (typeof performance !== 'undefined' && performance.now
    ? performance.now() : Date.now()) - started;
  return {
    version: 1,
    sourceResults,
    sourceBoneAssignments,
    modelJointAssignments: model.assignments,
    conflicts,
    diagnostics: {
      sourceCount: sourceRigs.length,
      classifiedSourceBoneCount: sourceBoneAssignments.size,
      modelJointAssignmentCount: model.assignments.size,
      conflictCount: conflicts.length,
      runtimeMs: Math.max(0, runtimeMs),
    },
  };
}
