// Conservative residual surface-boundary evidence for source-local Rigs.
//
// This module is deliberately independent from Three.js and runtime state. It
// only consumes normalized member arrays and the already inferred base forest.
// Boundary evidence is a separate class from triangle-domain influence
// overlap: it can only join components which the base forest left separate.

import {collectSurfaceTriangles} from './weight-rig.js';

const EPSILON = 0;

function componentIdFor(forest, boneId) {
  const value = forest?.componentByBoneId?.[boneId];
  return value === undefined || value === null ? null : Number(value);
}

function pairKey(left, right) {
  const a = Number(left);
  const b = Number(right);
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function componentPairKey(left, right) {
  const a = Number(left);
  const b = Number(right);
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function pointKey(point) {
  return JSON.stringify([Number(point[0]), Number(point[1]), Number(point[2])]);
}

function exactEdgeKey(first, second) {
  const left = pointKey(first);
  const right = pointKey(second);
  return left < right ? `${left}|${right}` : `${right}|${left}`;
}

function topologyEdgeKey(first, second) {
  const left = Number(first);
  const right = Number(second);
  return left < right ? `${left}:${right}` : `${right}:${left}`;
}

function positiveInfluences(indices, weights, influenceCount, vertex) {
  const result = new Map();
  if (!indices || !weights || !Number.isInteger(influenceCount)
      || influenceCount <= 0) return result;
  const start = vertex * influenceCount;
  for (let offset = 0; offset < influenceCount; offset += 1) {
    const boneId = Number(indices[start + offset]);
    const weight = Number(weights[start + offset]);
    if (!Number.isInteger(boneId) || boneId < 0
        || !Number.isFinite(weight) || weight <= EPSILON) continue;
    result.set(boneId, (result.get(boneId) || 0) + weight);
  }
  return result;
}

function getMemberArrays(member) {
  return {
    memberKey: String(member?.memberKey || member?.key || ''),
    positions: member?.baselinePositions || member?.positions || [],
    triangleIndices: member?.triangleIndices
      ?? member?.surfaceIndices ?? member?.indicesBuffer ?? null,
    skinIndices: member?.skinIndices || member?.indices || [],
    weights: member?.weights || [],
    influenceCount: Number(member?.influenceCount),
  };
}

function sideVector(edgeStart, edgeEnd, third) {
  const edge = [
    edgeEnd[0] - edgeStart[0],
    edgeEnd[1] - edgeStart[1],
    edgeEnd[2] - edgeStart[2],
  ];
  const fromStart = [
    third[0] - edgeStart[0],
    third[1] - edgeStart[1],
    third[2] - edgeStart[2],
  ];
  return [
    edge[1] * fromStart[2] - edge[2] * fromStart[1],
    edge[2] * fromStart[0] - edge[0] * fromStart[2],
    edge[0] * fromStart[1] - edge[1] * fromStart[0],
  ];
}

function dot(left, right) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
}

function edgeLength(first, second) {
  return Math.hypot(
    second[0] - first[0], second[1] - first[1], second[2] - first[2]);
}

function weightedMean(left, right, length) {
  return length * (left + right) / 2;
}

function edgeRecord(member, forest, triangle, edgeOffset, componentId) {
  const [first, second, third] = [0, 1, 2].map(index => ({
    vertex: triangle.indices[(edgeOffset + index) % 3],
    point: triangle.points[(edgeOffset + index) % 3],
  }));
  const firstInfluences = positiveInfluences(
    member.skinIndices, member.weights, member.influenceCount, first.vertex);
  const secondInfluences = positiveInfluences(
    member.skinIndices, member.weights, member.influenceCount, second.vertex);
  const edgeBones = new Map();
  for (const [boneId, value] of firstInfluences) {
    if (componentIdFor(forest, boneId) === componentId) {
      edgeBones.set(boneId, [value, 0]);
    }
  }
  for (const [boneId, value] of secondInfluences) {
    if (componentIdFor(forest, boneId) !== componentId) continue;
    const values = edgeBones.get(boneId) || [0, 0];
    values[1] = value;
    edgeBones.set(boneId, values);
  }
  return {
    memberKey: member.memberKey,
    componentId,
    triangleIndex: triangle.triangleIndex,
    vertexA: first.vertex,
    vertexB: second.vertex,
    pointA: [...first.point],
    pointB: [...second.point],
    thirdPoint: [...third.point],
    edgeKey: exactEdgeKey(first.point, second.point),
    topologyKey: topologyEdgeKey(first.vertex, second.vertex),
    pointKeyA: pointKey(first.point),
    pointKeyB: pointKey(second.point),
    length: edgeLength(first.point, second.point),
    bones: Object.fromEntries([...edgeBones].map(([boneId, values]) => [
      boneId, values])),
  };
}

function collectBoundaryRecords(member, forest) {
  const topology = collectSurfaceTriangles(
    member.positions, member.triangleIndices, true);
  const usage = new Map();
  let pureTriangleCount = 0;
  for (let triangleIndex = 0; triangleIndex < topology.triangles.length;
      triangleIndex += 1) {
    const triangleBase = topology.triangles[triangleIndex];
    const triangle = {...triangleBase,
      triangleIndex};
    const cornerInfluences = triangle.indices.map(vertex => positiveInfluences(
      member.skinIndices, member.weights, member.influenceCount, vertex));
    const componentIds = new Set();
    let pure = true;
    for (const influences of cornerInfluences) {
      for (const boneId of influences.keys()) {
        const componentId = componentIdFor(forest, boneId);
        if (componentId === null) {
          pure = false;
          break;
        }
        componentIds.add(componentId);
      }
      if (!pure) break;
    }
    if (!pure || componentIds.size !== 1) continue;
    pureTriangleCount += 1;
    const componentId = [...componentIds][0];
    for (let edgeOffset = 0; edgeOffset < 3; edgeOffset += 1) {
      const record = edgeRecord(member, forest, triangle, edgeOffset, componentId);
      const list = usage.get(`${componentId}|${record.topologyKey}`) || [];
      list.push(record);
      usage.set(`${componentId}|${record.topologyKey}`, list);
    }
  }
  const boundaries = [];
  for (const records of usage.values()) {
    if (records.length === 1) boundaries.push(records[0]);
  }
  return {
    boundaries,
    boundaryEdgeCount: boundaries.length,
    pureTriangleCount,
    topology,
  };
}

function addSupport(target, record) {
  const supports = target || new Map();
  const length = Number(record.length) || 0;
  Object.entries(record.bones || {})
    .sort(([left], [right]) => Number(left) - Number(right))
    .forEach(([boneId, values]) => {
      const support = weightedMean(Number(values[0]) || 0,
        Number(values[1]) || 0, length);
      if (support > 0) supports.set(Number(boneId),
        (supports.get(Number(boneId)) || 0) + support);
    });
  return supports;
}

function selectMutualBonePair(supportA, supportB) {
  const pairSupport = {};
  const bestForA = new Map();
  const bestForB = new Map();
  const valuesA = [...supportA.entries()].filter(([, value]) => value > 0);
  const valuesB = [...supportB.entries()].filter(([, value]) => value > 0);
  valuesA.forEach(([boneA, valueA]) => valuesB.forEach(([boneB, valueB]) => {
    const value = valueA * valueB;
    pairSupport[pairKey(boneA, boneB)] = value;
    const currentA = bestForA.get(boneA);
    if (!currentA || value > currentA.value) {
      bestForA.set(boneA, {boneB, value, tie: false});
    } else if (Object.is(value, currentA.value)) {
      currentA.tie = true;
    }
    const currentB = bestForB.get(boneB);
    if (!currentB || value > currentB.value) {
      bestForB.set(boneB, {boneA, value, tie: false});
    } else if (Object.is(value, currentB.value)) {
      currentB.tie = true;
    }
  }));
  const mutual = [];
  bestForA.forEach((best, boneA) => {
    const reverse = bestForB.get(best.boneB);
    if (best.tie || reverse?.tie
        || reverse?.boneA !== boneA) return;
    mutual.push({boneA, boneB: best.boneB, support: best.value});
  });
  return {pairSupport, mutual};
}

function oppositeSides(left, right) {
  const canonical = pointKey(left.pointA) < pointKey(left.pointB)
    ? [left.pointA, left.pointB] : [left.pointB, left.pointA];
  const sideA = sideVector(canonical[0], canonical[1], left.thirdPoint);
  const sideB = sideVector(canonical[0], canonical[1], right.thirdPoint);
  const value = dot(sideA, sideB);
  return Number.isFinite(value) && value < 0;
}

function connectedChains(records) {
  const adjacency = new Map();
  const add = (from, to) => {
    const values = adjacency.get(from) || new Set();
    values.add(to);
    adjacency.set(from, values);
  };
  records.forEach(record => {
    add(record.pointKeyA, record.pointKeyB);
    add(record.pointKeyB, record.pointKeyA);
  });
  const seen = new Set();
  const chains = [];
  for (const point of adjacency.keys()) {
    if (seen.has(point)) continue;
    const queue = [point];
    const points = new Set();
    while (queue.length) {
      const current = queue.shift();
      if (seen.has(current)) continue;
      seen.add(current);
      points.add(current);
      for (const next of adjacency.get(current) || []) {
        if (!seen.has(next)) queue.push(next);
      }
    }
    chains.push(points);
  }
  return chains;
}

function emptyResult(reason = null, enabled = !reason) {
  return {
    enabled,
    boundaryEvidenceEnabled: enabled,
    boundaryEvidenceReason: reason,
    boundaryEdgeCount: 0,
    exactMatchedEdgeCount: 0,
    validSeamSampleCount: 0,
    boundaryComponentPairCount: 0,
    candidates: [],
    acceptedBridges: [],
    boundaryRejectedCounts: {},
  };
}

/**
 * Extract exact indexed surface seams between residual base-forest components.
 * Every member must already be known to use surface evidence. A missing index
 * buffer is intentionally unsupported: inferring adjacency from positions
 * would turn this conservative operation into implicit vertex welding.
 */
export function buildResidualBoundaryEvidence({
  members = [], influenceGraph = null, baseForest = null,
} = {}) {
  if (influenceGraph?.evidenceMode !== 'surface') {
    return emptyResult('source_not_surface');
  }
  if (!members.length) return emptyResult('no_members');
  const normalizedMembers = members.map((member, index) => ({
    ...getMemberArrays(member),
    memberKey: String(member?.memberKey || member?.key || `member-${index}`),
  }));
  if (normalizedMembers.some(member => member.triangleIndices === null
      || member.triangleIndices === undefined)) {
    return emptyResult('unsupported_nonindexed_boundary');
  }
  if ((baseForest?.components || []).length < 2) {
    return emptyResult(null, true);
  }
  const allRecords = [];
  let boundaryEdgeCount = 0;
  for (const member of normalizedMembers) {
    const collected = collectBoundaryRecords(member, baseForest);
    boundaryEdgeCount += collected.boundaryEdgeCount;
    allRecords.push(...collected.boundaries);
  }
  const rejected = {};
  const reject = reason => { rejected[reason] = (rejected[reason] || 0) + 1; };
  const byEdge = new Map();
  allRecords.forEach(record => {
    const list = byEdge.get(`${record.memberKey}|${record.edgeKey}`) || [];
    list.push(record);
    byEdge.set(`${record.memberKey}|${record.edgeKey}`, list);
  });
  const matched = [];
  let exactMatchedEdgeCount = 0;
  for (const records of byEdge.values()) {
    const components = new Set(records.map(record => record.componentId));
    if (records.length !== 2 || components.size !== 2) {
      if (records.length > 2) reject('non_manifold');
      continue;
    }
    exactMatchedEdgeCount += 1;
    if (!oppositeSides(records[0], records[1])) {
      reject('non_continuing_surface');
      continue;
    }
    matched.push(records);
  }
  const pairGroups = new Map();
  matched.forEach(records => {
    const [left, right] = records;
    const key = `${left.memberKey}|${componentPairKey(
      left.componentId, right.componentId)}`;
    const group = pairGroups.get(key) || {
      memberKey: left.memberKey,
      componentA: Math.min(left.componentId, right.componentId),
      componentB: Math.max(left.componentId, right.componentId),
      segments: [],
    };
    group.segments.push(records);
    pairGroups.set(key, group);
  });
  const candidates = [];
  for (const group of pairGroups.values()) {
    const segments = [...group.segments].sort((left, right) =>
      left[0].edgeKey.localeCompare(right[0].edgeKey));
    const matchedRecords = segments.flat();
    const chains = connectedChains(matchedRecords);
    if (chains.length !== 1) {
      reject('multiple_seams');
      continue;
    }
    if (segments.length < 2 || chains[0].size < 3) {
      reject('insufficient_chain');
      continue;
    }
    const supportA = new Map();
    const supportB = new Map();
    segments.forEach(records => {
      const first = records.find(record => record.componentId === group.componentA);
      const second = records.find(record => record.componentId === group.componentB);
      addSupport(supportA, first);
      addSupport(supportB, second);
    });
    const pairSelection = selectMutualBonePair(supportA, supportB);
    if (pairSelection.mutual.length !== 1) {
      reject('bone_pair_ambiguous');
      continue;
    }
    const selectedPair = pairSelection.mutual[0];
    const length = segments.reduce((sum, records) =>
      sum + (Number(records[0].length) || 0), 0);
    const center = segments.reduce((sum, records) => {
      const edge = records[0];
      const midpoint = [
        (edge.pointA[0] + edge.pointB[0]) / 2,
        (edge.pointA[1] + edge.pointB[1]) / 2,
        (edge.pointA[2] + edge.pointB[2]) / 2,
      ];
      const weight = Number(edge.length) || 0;
      return [sum[0] + midpoint[0] * weight,
        sum[1] + midpoint[1] * weight,
        sum[2] + midpoint[2] * weight];
    }, [0, 0, 0]).map(value => length > 0 ? value / length : 0);
    const bonePairSupport = pairSelection.pairSupport;
    candidates.push({
      boneA: selectedPair.boneA,
      boneB: selectedPair.boneB,
      evidenceType: 'mesh_boundary',
      jointCenter: center,
      matchedEdgeCount: segments.length,
      matchedLength: length,
      memberKey: group.memberKey,
      componentA: group.componentA,
      componentB: group.componentB,
      bonePairSupport,
      seamSupport: {
        [selectedPair.boneA]: supportA.get(selectedPair.boneA),
        [selectedPair.boneB]: supportB.get(selectedPair.boneB),
      },
      segments: segments.map(records => records.map(record => ({...record}))),
    });
  }
  const ordered = candidates.sort((left, right) =>
    right.matchedEdgeCount - left.matchedEdgeCount
    || right.matchedLength - left.matchedLength
    || (right.bonePairSupport[pairKey(right.boneA, right.boneB)]
      - left.bonePairSupport[pairKey(left.boneA, left.boneB)])
    || left.componentA - right.componentA || left.componentB - right.componentB
    || left.boneA - right.boneA || left.boneB - right.boneB
    || left.memberKey.localeCompare(right.memberKey));
  const parent = new Map((baseForest?.components || [])
    .map(component => [Number(component.componentId), Number(component.componentId)]));
  const find = value => {
    let current = value;
    while (parent.has(current) && parent.get(current) !== current) {
      current = parent.get(current);
    }
    return current;
  };
  const acceptedBridges = [];
  ordered.forEach(candidate => {
    const left = find(candidate.componentA);
    const right = find(candidate.componentB);
    if (left === right) {
      reject('cycle');
      return;
    }
    parent.set(left, right);
    acceptedBridges.push({...candidate});
  });
  return {
    enabled: true,
    boundaryEvidenceEnabled: true,
    boundaryEvidenceReason: null,
    boundaryEdgeCount,
    exactMatchedEdgeCount,
    validSeamSampleCount: matched.length,
    boundaryComponentPairCount: pairGroups.size,
    candidates: ordered,
    acceptedBridges,
    boundaryRejectedCounts: rejected,
  };
}

function componentEvidence(component, graph) {
  return (component?.nodeIds || []).reduce((sum, boneId) => {
    const node = (graph?.nodes || []).find(item => Number(item.boneId) === Number(boneId));
    return sum + (Number(node?.totalWeight) || 0);
  }, 0);
}

function componentMeasure(component, graph) {
  return (component?.nodeIds || []).reduce((sum, boneId) => {
    const node = (graph?.nodes || []).find(item => Number(item.boneId) === Number(boneId));
    return sum + (Number(node?.affectedMeasure) || 0);
  }, 0);
}

/** Add accepted bridges while retaining base component roots and orientation. */
export function mergeResidualBoundaryBridges(baseForest, acceptedBridges = [], graph = null) {
  if (!baseForest || !acceptedBridges.length) return baseForest;
  const baseComponents = baseForest.components || [];
  const groups = new Map();
  const parent = new Map(baseComponents.map(component => [
    Number(component.componentId), Number(component.componentId)]));
  const find = value => {
    let current = value;
    while (parent.get(current) !== current) current = parent.get(current);
    return current;
  };
  acceptedBridges.forEach(bridge => {
    const left = find(Number(bridge.componentA));
    const right = find(Number(bridge.componentB));
    if (left !== right) parent.set(left, right);
  });
  baseComponents.forEach(component => {
    const root = find(Number(component.componentId));
    const group = groups.get(root) || [];
    group.push(component);
    groups.set(root, group);
  });
  const merged = [];
  for (const components of groups.values()) {
    const componentIds = new Set(components.map(component => Number(component.componentId)));
    const bridges = acceptedBridges.filter(bridge => componentIds.has(Number(bridge.componentA))
      && componentIds.has(Number(bridge.componentB)));
    let host = components.find(component =>
      Number(component.componentId) === Number(baseForest.primaryComponentId));
    if (!host) host = components.find(component => component.primary);
    if (!host) {
      host = [...components].sort((left, right) =>
        componentEvidence(right, graph) - componentEvidence(left, graph)
        || componentMeasure(right, graph) - componentMeasure(left, graph)
        || (right.nodeIds?.length || 0) - (left.nodeIds?.length || 0)
        || Number(left.rootId) - Number(right.rootId))[0];
    }
    const nodeIds = components.flatMap(component => component.nodeIds || [])
      .sort((left, right) => Number(left) - Number(right));
    const edges = components.flatMap(component => {
      if (component.edges?.length) return component.edges;
      const nodeSet = new Set(component.nodeIds || []);
      return (baseForest.edges || []).filter(edge =>
        nodeSet.has(Number(edge.boneA)) && nodeSet.has(Number(edge.boneB)));
    }).map(edge => ({...edge}));
    bridges.forEach(bridge => edges.push({...bridge}));
    const adjacency = new Map(nodeIds.map(id => [Number(id), []]));
    edges.forEach(edge => {
      const left = Number(edge.boneA);
      const right = Number(edge.boneB);
      if (!adjacency.has(left) || !adjacency.has(right)) return;
      adjacency.get(left).push(right);
      adjacency.get(right).push(left);
    });
    adjacency.forEach(values => values.sort((left, right) => left - right));
    const rootId = Number(host.rootId);
    const parentById = Object.fromEntries(nodeIds.map(id => [id, null]));
    const childrenById = Object.fromEntries(nodeIds.map(id => [id, []]));
    const depthById = Object.fromEntries(nodeIds.map(id => [id, null]));
    const queue = [rootId];
    depthById[rootId] = 0;
    while (queue.length) {
      const current = queue.shift();
      for (const neighbor of adjacency.get(current) || []) {
        if (depthById[neighbor] !== null) continue;
        parentById[neighbor] = current;
        childrenById[current].push(neighbor);
        depthById[neighbor] = depthById[current] + 1;
        queue.push(neighbor);
      }
    }
    merged.push({
      componentId: Math.min(...components.map(component => Number(component.componentId))),
      nodeIds,
      rootId,
      parentById,
      childrenById,
      depthById,
      edges,
      edgeCount: edges.length,
      maxDepth: Math.max(0, ...Object.values(depthById)
        .filter(value => value !== null).map(Number)),
      primary: components.some(component => component.primary),
    });
  }
  merged.sort((left, right) => left.componentId - right.componentId);
  const componentByBoneId = {};
  merged.forEach(component => component.nodeIds.forEach(id => {
    componentByBoneId[id] = component.componentId;
  }));
  const primary = merged.find(component => component.primary);
  return {
    ...baseForest,
    primaryRootId: primary?.rootId ?? baseForest.primaryRootId,
    primaryComponentId: primary?.componentId ?? baseForest.primaryComponentId,
    components: merged,
    componentByBoneId,
    edges: merged.flatMap(component => component.edges || []),
    nodeIds: [...(baseForest.nodeIds || [])],
  };
}
