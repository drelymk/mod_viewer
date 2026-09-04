// Pure, conservative reconciliation of source-local inferred rigs. The
// authored skinning streams remain source-scoped; this module only builds a
// viewer-owned model graph from neutral geometry and source topology.

import {Quaternion, Vector3} from 'three';

export const CROSS_SOURCE_CANDIDATE_DISTANCE = 0.1;
export const CROSS_SOURCE_STRICT_DISTANCE = 0.04;
export const CROSS_SOURCE_PROPAGATION_DISTANCE = 0.06;
export const CROSS_SOURCE_AMBIGUITY_MARGIN = 0.05;
export const CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN = 0.08;
export const CROSS_SOURCE_STRONG_VERTEX_COUNT = 8;
export const CROSS_SOURCE_STRONG_WEIGHT_STRENGTH = 0.5;
export const CROSS_SOURCE_GRAPH_ALIGNMENT_MARGIN = 0.1;
export const CROSS_SOURCE_GRAPH_ALIGNMENT_MIN_SCORE = 0.6;

const EPSILON = 1e-8;

function number(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function vectorFrom(value) {
  if (value?.isVector3) return value.clone();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? value : [value?.x, value?.y, value?.z];
  if (!values || values.length < 3) return null;
  const vector = new Vector3(
    number(values[0], NaN), number(values[1], NaN), number(values[2], NaN));
  return vector.toArray().every(Number.isFinite) ? vector : null;
}

function vectorArray(value, fallback = [0, 0, 0]) {
  return vectorFrom(value)?.toArray() || [...fallback];
}

function quaternionArray(value) {
  if (value?.isQuaternion) return value.clone().normalize().toArray();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 4).map(Number)
    : [value?.x, value?.y, value?.z, value?.w].map(Number);
  return values.length === 4 && values.every(Number.isFinite)
    ? new Quaternion(...values).normalize().toArray() : [0, 0, 0, 1];
}

function mapValue(collection, boneId) {
  if (collection instanceof Map) {
    return collection.get(boneId) ?? collection.get(String(boneId));
  }
  return collection?.[boneId];
}

function sourceBoneKey(sourceKey, boneId) {
  return `${String(sourceKey)}#bone=${Number(boneId)}`;
}

export {sourceBoneKey};

function componentFor(rig, boneId) {
  const componentId = mapValue(rig?.inferredForest?.componentByBoneId, boneId);
  const components = rig?.inferredForest?.components || [];
  return Number.isInteger(Number(componentId))
    ? components[Number(componentId)] || null : null;
}

function parentFor(component, boneId) {
  const parent = mapValue(component?.parentById, boneId);
  if (parent === null || parent === undefined) return null;
  const id = Number(parent);
  return Number.isFinite(id) ? id : null;
}

function childrenFor(component, boneId) {
  const children = mapValue(component?.childrenById, boneId) || [];
  return children.map(Number).filter(Number.isFinite);
}

function edgeScore(edge) {
  return number(edge?.treeEdgeScore ?? edge?.score
    ?? edge?.containment ?? edge?.jaccard, 0);
}

function sourceEdgeEvidence(rig, component, boneId) {
  return (component?.edges || []).filter(edge => {
    const left = Number(edge.boneA);
    const right = Number(edge.boneB);
    return left === boneId || right === boneId;
  }).map(edge => ({
    boneA: Number(edge.boneA),
    boneB: Number(edge.boneB),
    treeEdgeScore: edgeScore(edge),
    sharedVertexCount: number(edge.sharedVertexCount),
    centerDistance: edge.centerDistance === null
      ? null : number(edge.centerDistance, 0),
    sourceKey: String(rig.sourceKey),
  }));
}

function directionAvailable(evidence) {
  return !!evidence?.restDirection
    && evidence.directionSource !== 'canonical-y';
}

function anchorFor(parentId, center, pivot) {
  return parentId === null ? center : pivot || center;
}

function sourceAdjacencyByBoneId(rig) {
  const adjacency = new Map();
  const addEdge = (left, right) => {
    const leftNeighbors = adjacency.get(left) || new Set();
    const rightNeighbors = adjacency.get(right) || new Set();
    leftNeighbors.add(right);
    rightNeighbors.add(left);
    adjacency.set(left, leftNeighbors);
    adjacency.set(right, rightNeighbors);
  };
  (rig?.inferredForest?.components || []).forEach(component => {
    Object.entries(component.parentById || {}).forEach(([childId, parentId]) => {
      if (parentId !== null && parentId !== undefined) {
        addEdge(Number(childId), Number(parentId));
      }
    });
    Object.entries(component.childrenById || {}).forEach(([parentId, children]) =>
      (children || []).forEach(childId => addEdge(Number(parentId), Number(childId))));
  });
  return new Map([...adjacency.entries()].map(([boneId, neighbors]) => [
    Number(boneId), [...neighbors].filter(Number.isFinite)
      .sort((left, right) => left - right),
  ]));
}

function collectSourceBoneEvidence(rig) {
  const result = new Map();
  const sourceAdjacency = sourceAdjacencyByBoneId(rig);
  const boneIds = [...(rig?.boneIds || rig?.influenceGraph?.nodes || [])]
    .map(value => Number(value?.boneId ?? value)).filter(Number.isFinite)
    .sort((left, right) => left - right);
  const nodes = new Map((rig?.influenceGraph?.nodes || []).map(node => [
    Number(node.boneId), node,
  ]));
  boneIds.forEach(boneId => {
    const component = componentFor(rig, boneId);
    const parentBoneId = parentFor(component, boneId);
    const childBoneIds = childrenFor(component, boneId);
    const neighborBoneIds = sourceAdjacency.get(boneId) || [];
    const node = nodes.get(boneId);
    const center = vectorArray(
      mapValue(rig?.centerByBoneId, boneId) || node?.weightedCenter);
    const pivot = vectorFrom(mapValue(rig?.jointPivotByBoneId, boneId));
    const direction = vectorFrom(mapValue(rig?.restDirectionByBoneId, boneId));
    const key = sourceBoneKey(rig.sourceKey, boneId);
    result.set(key, {
      sourceKey: String(rig.sourceKey),
      boneId,
      sourceBoneKey: key,
      weightedCenter: center,
      weightedRadius: number(node?.weightedRadius),
      jointPivot: pivot?.toArray() || null,
      restAnchor: anchorFor(parentBoneId, center, pivot?.toArray() || null),
      restDirection: direction?.normalize().toArray() || null,
      directionSource: mapValue(rig?.restFrameEvidenceByBoneId, boneId)
        ?.directionSource || null,
      restFrame: quaternionArray(mapValue(rig?.restFrameByBoneId, boneId)),
      parentBoneId,
      childBoneIds,
      neighborBoneIds,
      depth: number(mapValue(component?.depthById, boneId)),
      degree: childBoneIds.length + (parentBoneId === null ? 0 : 1),
      isRoot: parentBoneId === null,
      totalWeight: number(node?.totalWeight),
      affectedVertexCount: number(node?.affectedVertexCount),
      sourceEdgeEvidence: sourceEdgeEvidence(rig, component, boneId),
    });
  });
  return result;
}

function modelReferenceRadius(evidence) {
  const points = [...evidence.values()].map(item =>
    vectorFrom(item.weightedCenter)).filter(Boolean);
  if (!points.length) return 1;
  const center = points.reduce((sum, point) => sum.add(point), new Vector3())
    .multiplyScalar(1 / points.length);
  return Math.max(EPSILON, ...points.map(point => point.distanceTo(center)));
}

function clamp(value, minimum = 0, maximum = 1) {
  return Math.max(minimum, Math.min(maximum, value));
}

function neighborDirection(evidence, allEvidence, incoming) {
  if (!evidence) return null;
  if (incoming) {
    const parent = allEvidence.get(sourceBoneKey(
      evidence.sourceKey, evidence.parentBoneId));
    const vector = parent && vectorFrom(evidence.restAnchor)
      && vectorFrom(parent.restAnchor)
      ? vectorFrom(evidence.restAnchor).sub(vectorFrom(parent.restAnchor))
      : null;
    if (vector?.length() > EPSILON) return vector.normalize();
  }
  return directionAvailable(evidence)
    ? vectorFrom(evidence.restDirection) : null;
}

function topologyFeature(left, right) {
  const parent = left.parentBoneId === null && right.parentBoneId === null
    || left.parentBoneId !== null && right.parentBoneId !== null;
  const leftChildren = left.childBoneIds.length;
  const rightChildren = right.childBoneIds.length;
  const childSimilarity = 1 - clamp(
    Math.abs(leftChildren - rightChildren)
      / Math.max(1, leftChildren, rightChildren));
  const degreeSimilarity = 1 - clamp(
    Math.abs(left.degree - right.degree) / Math.max(1, left.degree, right.degree));
  return {
    parentPresent: parent,
    childCount: childSimilarity,
    degree: degreeSimilarity,
    value: (Number(parent) * .35 + childSimilarity * .4
      + degreeSimilarity * .25),
    rootConflict: left.isRoot !== right.isRoot,
  };
}

function crossPairKey(leftSourceBoneKey, rightSourceBoneKey) {
  return [leftSourceBoneKey, rightSourceBoneKey].sort().join('|');
}

function vertexSamplesForRig(rig) {
  const samples = [];
  (rig?.vertexEvidence || []).forEach((entry, entryIndex) => {
    const positions = entry.positions || entry.baselinePositions;
    const indices = entry.indices;
    const weights = entry.weights;
    const influenceCount = Number(entry.influenceCount);
    if (!positions || !indices || !weights || !Number.isInteger(influenceCount)
        || influenceCount <= 0) return;
    const vertexCount = Math.floor(Math.min(
      positions.length / 3, indices.length / influenceCount,
      weights.length / influenceCount));
    for (let vertexIndex = 0; vertexIndex < vertexCount; vertexIndex += 1) {
      const offset = vertexIndex * 3;
      const point = vectorFrom([
        positions[offset], positions[offset + 1], positions[offset + 2],
      ]);
      if (!point) continue;
      const influenceMap = new Map();
      const start = vertexIndex * influenceCount;
      for (let influenceIndex = 0; influenceIndex < influenceCount;
           influenceIndex += 1) {
        const boneId = Number(indices[start + influenceIndex]);
        const weight = Number(weights[start + influenceIndex]);
        if (!Number.isInteger(boneId) || boneId < 0
            || !Number.isFinite(weight) || weight <= 0) continue;
        influenceMap.set(boneId, (influenceMap.get(boneId) || 0) + weight);
      }
      if (!influenceMap.size) continue;
      samples.push({
        sampleKey: `${String(entry.meshKey || entryIndex)}#vertex=${vertexIndex}`,
        point,
        influences: [...influenceMap.entries()].map(([boneId, weight]) => ({
          boneId, weight,
        })),
      });
    }
  });
  return samples.sort((left, right) =>
    left.sampleKey.localeCompare(right.sampleKey));
}

function cellKey(point, cellSize) {
  return [point.x, point.y, point.z].map(value =>
    Math.floor(value / cellSize)).join(':');
}

function crossSourceWeightEvidence(leftRig, rightRig, referenceRadius) {
  const leftSamples = vertexSamplesForRig(leftRig);
  const rightSamples = vertexSamplesForRig(rightRig);
  if (!leftSamples.length || !rightSamples.length) return new Map();
  const cellSize = Math.max(referenceRadius * 0.01, EPSILON);
  const matchDistance = Math.max(referenceRadius * 0.02, EPSILON);
  const leftCells = new Map();
  leftSamples.forEach(sample => {
    const key = cellKey(sample.point, cellSize);
    const entries = leftCells.get(key) || [];
    entries.push(sample);
    leftCells.set(key, entries);
  });
  const nearestLeftByRight = new Map();
  const nearestRightByLeft = new Map();
  rightSamples.forEach(rightSample => {
    const [x, y, z] = cellKey(rightSample.point, cellSize)
      .split(':').map(Number);
    let best = null;
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dz = -1; dz <= 1; dz += 1) {
          const entries = leftCells.get(`${x + dx}:${y + dy}:${z + dz}`) || [];
          entries.forEach(leftSample => {
            const distance = rightSample.point.distanceTo(leftSample.point);
            if (distance > matchDistance) return;
            if (!best || distance < best.distance
                || distance === best.distance
                  && leftSample.sampleKey.localeCompare(
                    best.leftSample.sampleKey) < 0) {
              best = {leftSample, distance};
            }
          });
        }
      }
    }
    if (!best) return;
    nearestLeftByRight.set(rightSample, best);
    const previous = nearestRightByLeft.get(best.leftSample);
    if (!previous || best.distance < previous.distance
        || best.distance === previous.distance
          && rightSample.sampleKey.localeCompare(
            previous.rightSample.sampleKey) < 0) {
      nearestRightByLeft.set(best.leftSample, {
        rightSample, distance: best.distance,
      });
    }
  });

  const evidence = new Map();
  nearestLeftByRight.forEach(({leftSample, distance}, rightSample) => {
    const reverse = nearestRightByLeft.get(leftSample);
    if (!reverse || reverse.rightSample !== rightSample) return;
    const confidence = clamp(1 - distance / matchDistance);
    for (const leftInfluence of leftSample.influences) {
      for (const rightInfluence of rightSample.influences) {
        const leftKey = sourceBoneKey(leftRig.sourceKey,
          leftInfluence.boneId);
        const rightKey = sourceBoneKey(rightRig.sourceKey,
          rightInfluence.boneId);
        const key = crossPairKey(leftKey, rightKey);
        const record = evidence.get(key) || {
          leftSourceBoneKey: leftKey,
          rightSourceBoneKey: rightKey,
          matchedVertexCount: 0,
          weightedMatchStrength: 0,
          leftMass: 0,
          rightMass: 0,
          matchedVertexKeys: new Set(),
        };
        const leftMass = leftInfluence.weight * confidence;
        const rightMass = rightInfluence.weight * confidence;
        const vertexPairKey = `${leftSample.sampleKey}|${rightSample.sampleKey}`;
        if (!record.matchedVertexKeys.has(vertexPairKey)) {
          record.matchedVertexKeys.add(vertexPairKey);
          record.matchedVertexCount += 1;
        }
        record.weightedMatchStrength += leftInfluence.weight
          * rightInfluence.weight * confidence;
        record.leftMass += leftMass;
        record.rightMass += rightMass;
        evidence.set(key, record);
      }
    }
  });
  evidence.forEach(record => {
    const minimumMass = Math.max(EPSILON,
      Math.min(record.leftMass, record.rightMass));
    const unionMass = Math.max(EPSILON,
      record.leftMass + record.rightMass - record.weightedMatchStrength);
    record.crossContainment = clamp(
      record.weightedMatchStrength / minimumMass);
    record.crossJaccard = clamp(record.weightedMatchStrength / unionMass);
    record.overlapScore = clamp(record.crossContainment * .55
      + record.crossJaccard * .45);
    record.supportReliability = Math.min(
      clamp(record.matchedVertexCount / CROSS_SOURCE_STRONG_VERTEX_COUNT),
      clamp(record.weightedMatchStrength
        / CROSS_SOURCE_STRONG_WEIGHT_STRENGTH));
    delete record.matchedVertexKeys;
  });
  return evidence;
}

function candidateFor(left, right, allEvidence, crossEvidenceByPair, gate) {
  // Equivalence compares like-for-like neutral regions. The parent joint pivot
  // is a structural anchor, not a replacement for a source bone's region.
  const leftCenter = vectorFrom(left.weightedCenter);
  const rightCenter = vectorFrom(right.weightedCenter);
  const distance = leftCenter?.distanceTo(rightCenter);
  if (!Number.isFinite(distance)) return null;
  const normalizedDistance = distance / gate.referenceRadius;
  if (normalizedDistance > CROSS_SOURCE_CANDIDATE_DISTANCE) return null;
  const directionAlignment = directionAvailable(left)
    && directionAvailable(right)
    ? Math.abs(vectorFrom(left.restDirection)
      .dot(vectorFrom(right.restDirection))) : null;
  const radiusRatio = left.weightedRadius > EPSILON
    && right.weightedRadius > EPSILON
    ? Math.min(left.weightedRadius, right.weightedRadius)
      / Math.max(left.weightedRadius, right.weightedRadius) : null;
  const leftIncoming = neighborDirection(left, allEvidence, true);
  const rightIncoming = neighborDirection(right, allEvidence, true);
  const incomingAlignment = leftIncoming && rightIncoming
    ? Math.abs(leftIncoming.dot(rightIncoming)) : null;
  const topology = topologyFeature(left, right);
  const crossEvidence = crossEvidenceByPair.get(crossPairKey(
    left.sourceBoneKey, right.sourceBoneKey)) || null;
  const crossQuality = crossEvidence?.overlapScore ?? null;
  const geometryFeatures = [
    {value: clamp(1 - normalizedDistance / CROSS_SOURCE_CANDIDATE_DISTANCE), weight: .34},
    {value: directionAlignment, weight: .1},
    {value: radiusRatio, weight: .05},
    {value: topology.value, weight: .1},
    {value: incomingAlignment, weight: .05},
  ].filter(item => item.value !== null && item.value !== undefined);
  const geometryWeightTotal = geometryFeatures.reduce(
    (sum, item) => sum + item.weight, 0);
  const geometryConfidence = geometryFeatures.reduce(
    (sum, item) => sum + item.value * item.weight, 0)
    / Math.max(EPSILON, geometryWeightTotal);
  const supportReliability = crossEvidence?.supportReliability || 0;
  const crossConfidence = crossQuality === null
    ? 0 : crossQuality * supportReliability;
  // Cross-source evidence is corroborating evidence. This combination is
  // monotonic in both inputs, so partial overlap cannot punish a good shape
  // and a one-vertex coincidence cannot become a seed by itself.
  const combinedConfidence = 1 - (1 - geometryConfidence)
    * (1 - crossConfidence);
  const strongCrossEvidence = !!crossEvidence
    && crossEvidence.matchedVertexCount >= CROSS_SOURCE_STRONG_VERTEX_COUNT
    && crossEvidence.weightedMatchStrength
      >= CROSS_SOURCE_STRONG_WEIGHT_STRENGTH
    && crossQuality >= .7;
  const exactRootAnchor = normalizedDistance <= .01
    && geometryConfidence >= .8
    && topology.degree >= .75
    && (directionAlignment === null || directionAlignment >= .75);
  const geometrySeed = normalizedDistance <= CROSS_SOURCE_STRICT_DISTANCE
    && geometryConfidence >= .7
    && (!topology.rootConflict || exactRootAnchor)
    && (directionAlignment === null || directionAlignment >= .55);
  const confidenceClass = strongCrossEvidence ? 2 : geometrySeed ? 1 : 0;
  return {
    left: {sourceKey: left.sourceKey, boneId: left.boneId,
      sourceBoneKey: left.sourceBoneKey},
    right: {sourceKey: right.sourceKey, boneId: right.boneId,
      sourceBoneKey: right.sourceBoneKey},
    normalizedDistance,
    anchorDistance: distance,
    directionAlignment,
    radiusRatio,
    incomingAlignment,
    crossEvidence,
    crossScore: crossQuality,
    crossQuality,
    supportReliability,
    matchedVertexCount: crossEvidence?.matchedVertexCount || 0,
    weightedMatchStrength: crossEvidence?.weightedMatchStrength || 0,
    geometryConfidence,
    crossConfidence,
    combinedConfidence,
    strongCrossEvidence,
    geometrySeed,
    confidenceClass,
    score: combinedConfidence,
    topology,
  };
}

function compareCandidate(left, right) {
  return (left.confidenceClass || 0) - (right.confidenceClass || 0)
    || left.combinedConfidence - right.combinedConfidence
    || left.supportReliability - right.supportReliability
    || (left.crossQuality || 0) - (right.crossQuality || 0)
    || left.geometryConfidence - right.geometryConfidence
    || right.normalizedDistance - left.normalizedDistance
    || right.left.sourceBoneKey.localeCompare(left.left.sourceBoneKey)
    || right.right.sourceBoneKey.localeCompare(left.right.sourceBoneKey);
}

function candidateAmbiguous(best, second) {
  return !!best && !!second
    && (best.confidenceClass || 0) === (second.confidenceClass || 0)
    && (best.propagationScore ?? best.combinedConfidence ?? best.score)
      - (second.propagationScore ?? second.combinedConfidence ?? second.score)
      < CROSS_SOURCE_AMBIGUITY_MARGIN;
}

function endpointDescriptor(candidate, endpoint) {
  return candidate.left.sourceBoneKey === endpoint
    ? {otherSource: candidate.right.sourceKey,
      otherKey: candidate.right.sourceBoneKey}
    : {otherSource: candidate.left.sourceKey,
      otherKey: candidate.left.sourceBoneKey};
}

function endpointCompetitionKey(endpoint, otherSource) {
  return JSON.stringify([endpoint, String(otherSource)]);
}

class GuardedUnionFind {
  constructor(keys) {
    this.parent = new Map(keys.map(key => [key, key]));
    this.sources = new Map(keys.map(key => [key,
      new Set([key.split('#bone=')[0]])]));
  }

  find(key) {
    const parent = this.parent.get(key);
    if (parent === undefined || parent === key) return parent;
    const root = this.find(parent);
    this.parent.set(key, root);
    return root;
  }

  same(left, right) {
    return !!left && !!right && this.find(left) === this.find(right);
  }

  union(left, right) {
    const leftRoot = this.find(left);
    const rightRoot = this.find(right);
    if (!leftRoot || !rightRoot || leftRoot === rightRoot) {
      return {accepted: leftRoot === rightRoot, reason: null};
    }
    const leftSources = this.sources.get(leftRoot) || new Set();
    const rightSources = this.sources.get(rightRoot) || new Set();
    if ([...leftSources].some(source => rightSources.has(source))) {
      return {accepted: false, reason: 'cluster_source_conflict'};
    }
    this.parent.set(rightRoot, leftRoot);
    this.sources.set(leftRoot, new Set([...leftSources, ...rightSources]));
    this.sources.delete(rightRoot);
    return {accepted: true, reason: null};
  }

  clusters() {
    const result = new Map();
    for (const key of this.parent.keys()) {
      const root = this.find(key);
      const members = result.get(root) || [];
      members.push(key);
      result.set(root, members);
    }
    return result;
  }
}

function neighborMatches(candidate, evidenceByKey, unionFind) {
  const left = evidenceByKey.get(candidate.left.sourceBoneKey);
  const right = evidenceByKey.get(candidate.right.sourceBoneKey);
  if (!left || !right) {
    return {matchedNeighborCount: 0, matchedNeighborPairs: []};
  }
  const leftNeighbors = left.neighborBoneIds || [];
  const rightNeighbors = right.neighborBoneIds || [];
  const usedRightNeighbors = new Set();
  const matchedNeighborPairs = [];
  leftNeighbors.forEach(leftNeighborId => {
    const rightNeighborId = rightNeighbors.find(candidateId =>
      !usedRightNeighbors.has(candidateId)
      && unionFind.same(sourceBoneKey(left.sourceKey, leftNeighborId),
        sourceBoneKey(right.sourceKey, candidateId)));
    if (rightNeighborId === undefined) return;
    usedRightNeighbors.add(rightNeighborId);
    matchedNeighborPairs.push({leftBoneId: leftNeighborId, rightBoneId: rightNeighborId});
  });
  return {
    matchedNeighborCount: matchedNeighborPairs.length,
    matchedNeighborPairs,
  };
}

function average(values, fallback = null) {
  return values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : fallback;
}

function graphAlignmentFeatures(candidate, evidenceByKey, unionFind) {
  const matches = neighborMatches(candidate, evidenceByKey, unionFind);
  const left = evidenceByKey.get(candidate.left.sourceBoneKey);
  const right = evidenceByKey.get(candidate.right.sourceBoneKey);
  const edgeAlignments = [];
  const edgeLengthRatios = [];
  matches.matchedNeighborPairs.forEach(pair => {
    const leftNeighbor = evidenceByKey.get(sourceBoneKey(
      left.sourceKey, pair.leftBoneId));
    const rightNeighbor = evidenceByKey.get(sourceBoneKey(
      right.sourceKey, pair.rightBoneId));
    const leftVector = vectorFrom(leftNeighbor?.weightedCenter)
      ?.sub(vectorFrom(left.weightedCenter));
    const rightVector = vectorFrom(rightNeighbor?.weightedCenter)
      ?.sub(vectorFrom(right.weightedCenter));
    if (!leftVector || !rightVector
        || leftVector.length() <= EPSILON || rightVector.length() <= EPSILON) {
      return;
    }
    const leftLength = leftVector.length();
    const rightLength = rightVector.length();
    edgeAlignments.push(Math.abs(leftVector.normalize().dot(
      rightVector.normalize())));
    edgeLengthRatios.push(Math.min(leftLength, rightLength)
      / Math.max(leftLength, rightLength));
  });
  const relativeEdgeAlignment = average(edgeAlignments, .5);
  const edgeLengthRatio = average(edgeLengthRatios, .5);
  const distanceQuality = clamp(1 - candidate.normalizedDistance
    / CROSS_SOURCE_CANDIDATE_DISTANCE);
  const crossSignal = candidate.crossQuality === null
    ? .5 : candidate.crossConfidence;
  const graphAlignmentScore = clamp(
    clamp(matches.matchedNeighborCount / 2) * .4
    + crossSignal * .15
    + (candidate.directionAlignment ?? .5) * .1
    + relativeEdgeAlignment * .15
    + edgeLengthRatio * .1
    + distanceQuality * .05
    + (candidate.topology?.degree ?? .5) * .05);
  return {
    matchedNeighborCount: matches.matchedNeighborCount,
    matchedNeighborPairs: matches.matchedNeighborPairs,
    relativeEdgeAlignment,
    edgeLengthRatio,
    graphAlignmentScore,
  };
}

function diagnosticCandidate(candidate, decision, rejectionReason = null) {
  return {
    left: {...candidate.left},
    right: {...candidate.right},
    anchorDistance: candidate.anchorDistance,
    normalizedDistance: candidate.normalizedDistance,
    mutualBest: !!candidate.mutualBest,
    directionAlignment: candidate.directionAlignment,
    radiusRatio: candidate.radiusRatio,
    geometryConfidence: candidate.geometryConfidence,
    crossScore: candidate.crossQuality ?? candidate.crossScore ?? null,
    crossQuality: candidate.crossQuality,
    crossConfidence: candidate.crossConfidence,
    supportReliability: candidate.supportReliability,
    matchedNeighborCount: candidate.matchedNeighborCount || 0,
    matchedNeighborPairs: (candidate.matchedNeighborPairs || [])
      .map(pair => ({...pair})),
    relativeEdgeAlignment: candidate.relativeEdgeAlignment,
    edgeLengthRatio: candidate.edgeLengthRatio,
    graphAlignmentScore: candidate.graphAlignmentScore,
    graphAlignmentPathLength: candidate.graphAlignmentPathLength || null,
    matchedVertexCount: candidate.matchedVertexCount
      ?? candidate.crossEvidence?.matchedVertexCount ?? 0,
    weightedMatchStrength: candidate.weightedMatchStrength
      ?? candidate.crossEvidence?.weightedMatchStrength ?? 0,
    crossContainment: candidate.crossEvidence?.crossContainment ?? null,
    crossJaccard: candidate.crossEvidence?.crossJaccard ?? null,
    strongCrossEvidence: !!candidate.strongCrossEvidence,
    geometrySeed: !!candidate.geometrySeed,
    confidenceClass: candidate.confidenceClass || 0,
    rootConflict: !!candidate.topology?.rootConflict,
    score: candidate.combinedConfidence ?? candidate.score,
    decision,
    rejectionReason,
  };
}

function buildCrossSourceWeightEvidence(sourceRigs, referenceRadius) {
  const evidence = new Map();
  const rigs = [...sourceRigs].sort((left, right) =>
    String(left.sourceKey).localeCompare(String(right.sourceKey)));
  for (let leftIndex = 0; leftIndex < rigs.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < rigs.length; rightIndex += 1) {
      crossSourceWeightEvidence(rigs[leftIndex], rigs[rightIndex], referenceRadius)
        .forEach((record, key) => evidence.set(key, record));
    }
  }
  return evidence;
}

function buildCandidates(evidenceByKey, referenceRadius, sourceRigs = []) {
  const crossEvidenceByPair = buildCrossSourceWeightEvidence(
    sourceRigs, referenceRadius);
  const bySource = new Map();
  for (const evidence of evidenceByKey.values()) {
    const entries = bySource.get(evidence.sourceKey) || [];
    entries.push(evidence);
    bySource.set(evidence.sourceKey, entries);
  }
  const candidates = [];
  const sources = [...bySource.keys()].sort();
  for (let leftIndex = 0; leftIndex < sources.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < sources.length; rightIndex += 1) {
      const leftEntries = bySource.get(sources[leftIndex]);
      const rightEntries = bySource.get(sources[rightIndex]);
      for (const left of leftEntries) {
        for (const right of rightEntries) {
          const candidate = candidateFor(left, right, evidenceByKey,
            crossEvidenceByPair, {
            referenceRadius,
          });
          if (candidate) candidates.push(candidate);
        }
      }
    }
  }
  return {candidates, crossEvidenceByPair};
}

function markSpatialRelationships(candidates) {
  const endpointMap = new Map();
  const competitionKey = (endpoint, otherSource) => JSON.stringify([
    endpoint, String(otherSource),
  ]);
  const endpointDescriptor = (candidate, endpoint) =>
    candidate.left.sourceBoneKey === endpoint
      ? {otherSource: candidate.right.sourceKey,
        otherKey: candidate.right.sourceBoneKey}
      : {otherSource: candidate.left.sourceKey,
        otherKey: candidate.left.sourceBoneKey};
  candidates.forEach(candidate => {
    for (const endpoint of [candidate.left.sourceBoneKey,
      candidate.right.sourceBoneKey]) {
      const descriptor = endpointDescriptor(candidate, endpoint);
      const key = competitionKey(endpoint, descriptor.otherSource);
      const incident = endpointMap.get(key) || [];
      incident.push(candidate);
      endpointMap.set(key, incident);
    }
  });
  const rankFor = (endpoint, candidatesForEndpoint) => {
    const ranked = [...candidatesForEndpoint];
    ranked.sort((left, right) => {
      const leftDescriptor = endpointDescriptor(left, endpoint);
      const rightDescriptor = endpointDescriptor(right, endpoint);
      return (right.confidenceClass || 0) - (left.confidenceClass || 0)
        || right.combinedConfidence - left.combinedConfidence
        || right.supportReliability - left.supportReliability
        || (right.crossQuality || 0) - (left.crossQuality || 0)
        || right.geometryConfidence - left.geometryConfidence
        || left.normalizedDistance - right.normalizedDistance
        || leftDescriptor.otherKey.localeCompare(rightDescriptor.otherKey);
    });
    return ranked;
  };
  candidates.forEach(candidate => {
    const leftDescriptor = endpointDescriptor(
      candidate, candidate.left.sourceBoneKey);
    const rightDescriptor = endpointDescriptor(
      candidate, candidate.right.sourceBoneKey);
    const leftCandidates = rankFor(candidate.left.sourceBoneKey,
      endpointMap.get(competitionKey(candidate.left.sourceBoneKey,
        leftDescriptor.otherSource)) || []);
    const rightCandidates = rankFor(candidate.right.sourceBoneKey,
      endpointMap.get(competitionKey(candidate.right.sourceBoneKey,
        rightDescriptor.otherSource)) || []);
    candidate.mutualBest = leftCandidates[0] === candidate
      && rightCandidates[0] === candidate;
    candidate.leftAmbiguous = candidateAmbiguous(
      leftCandidates[0], leftCandidates[1]);
    candidate.rightAmbiguous = candidateAmbiguous(
      rightCandidates[0], rightCandidates[1]);
  });
}

function acceptedEquivalence(candidate, pass, unionFind, diagnostics,
    accepted, correspondenceStrength) {
  const left = candidate.left.sourceBoneKey;
  const right = candidate.right.sourceBoneKey;
  if (unionFind.same(left, right)) {
    diagnostics.push(diagnosticCandidate(
      candidate, 'rejected', 'already_equivalent'));
    return false;
  }
  const union = unionFind.union(left, right);
  if (!union.accepted) {
    diagnostics.push(diagnosticCandidate(
      candidate, 'rejected', union.reason || 'cluster_source_conflict'));
    return false;
  }
  const record = diagnosticCandidate(candidate, 'accepted', null);
  record.pass = pass;
  accepted.push(record);
  const strength = Math.max(candidate.score || 0,
    candidate.propagationScore || 0, candidate.graphAlignmentScore || 0);
  correspondenceStrength.set(left, Math.max(
    correspondenceStrength.get(left) || 0, strength));
  correspondenceStrength.set(right, Math.max(
    correspondenceStrength.get(right) || 0, strength));
  return true;
}

function compareGraphAlignmentCandidate(left, right) {
  return right.matchedNeighborCount - left.matchedNeighborCount
    || right.crossConfidence - left.crossConfidence
    || (right.directionAlignment ?? .5) - (left.directionAlignment ?? .5)
    || right.relativeEdgeAlignment - left.relativeEdgeAlignment
    || right.edgeLengthRatio - left.edgeLengthRatio
    || right.graphAlignmentScore - left.graphAlignmentScore
    || right.topology.degree - left.topology.degree
    || right.geometryConfidence - left.geometryConfidence
    || left.normalizedDistance - right.normalizedDistance
    || left.left.sourceBoneKey.localeCompare(right.left.sourceBoneKey)
    || left.right.sourceBoneKey.localeCompare(right.right.sourceBoneKey);
}

function graphAlignmentCandidatesFor(candidates, endpoint, otherSource,
    unionFind) {
  return candidates.filter(candidate => {
    if (unionFind.same(candidate.left.sourceBoneKey,
      candidate.right.sourceBoneKey)) return false;
    const descriptor = endpointDescriptor(candidate, endpoint);
    return descriptor.otherSource === otherSource
      && descriptor.otherKey !== endpoint
      && candidate.matchedNeighborCount > 0
      && candidate.graphAlignmentScore
        >= CROSS_SOURCE_GRAPH_ALIGNMENT_MIN_SCORE
      && candidate.topology.degree >= .5;
  }).sort(compareGraphAlignmentCandidate);
}

function uniqueGraphWinner(candidate, ranked, tier) {
  if (ranked[0] !== candidate) return false;
  const second = ranked[1];
  if (!second) return true;
  if (tier === 1 && candidate.matchedNeighborCount
      > second.matchedNeighborCount) return true;
  return candidate.graphAlignmentScore - second.graphAlignmentScore
    >= CROSS_SOURCE_GRAPH_ALIGNMENT_MARGIN;
}

function graphEvidenceContradicts(candidate) {
  return candidate.crossQuality !== null
    && candidate.supportReliability >= .75
    && candidate.crossQuality < .35;
}

function runGraphAlignment(candidates, evidenceByKey, unionFind,
    diagnostics, accepted, correspondenceStrength) {
  let changed = true;
  while (changed) {
    changed = false;
    candidates.forEach(candidate => Object.assign(candidate,
      graphAlignmentFeatures(candidate, evidenceByKey, unionFind)));
    const available = candidates.filter(candidate =>
      !graphEvidenceContradicts(candidate)
      && candidate.matchedNeighborCount > 0
      && candidate.graphAlignmentScore >= CROSS_SOURCE_GRAPH_ALIGNMENT_MIN_SCORE
      && candidate.topology.degree >= .5
      && !unionFind.same(candidate.left.sourceBoneKey,
        candidate.right.sourceBoneKey));
    const rankFor = candidate => {
      const leftDescriptor = endpointDescriptor(
        candidate, candidate.left.sourceBoneKey);
      const rightDescriptor = endpointDescriptor(
        candidate, candidate.right.sourceBoneKey);
      return {
        left: graphAlignmentCandidatesFor(available,
          candidate.left.sourceBoneKey, leftDescriptor.otherSource, unionFind),
        right: graphAlignmentCandidatesFor(available,
          candidate.right.sourceBoneKey, rightDescriptor.otherSource, unionFind),
      };
    };
    const selected = available.filter(candidate => {
      const ranked = rankFor(candidate);
      const tier = candidate.matchedNeighborCount >= 2 ? 1 : 2;
      return uniqueGraphWinner(candidate, ranked.left, tier)
        && uniqueGraphWinner(candidate, ranked.right, tier);
    }).sort(compareGraphAlignmentCandidate);
    for (const candidate of selected) {
      const tier = candidate.matchedNeighborCount >= 2 ? 1 : 2;
      if (acceptedEquivalence(candidate, `graph-alignment-${tier}`,
        unionFind, diagnostics, accepted, correspondenceStrength)) {
        changed = true;
      }
    }
  }
}

function sourceKeyFromBoneKey(key) {
  return String(key).split('#bone=')[0];
}

function pathBetweenSourceBones(startKey, endKey, evidenceByKey) {
  if (sourceKeyFromBoneKey(startKey) !== sourceKeyFromBoneKey(endKey)) {
    return null;
  }
  const previous = new Map([[startKey, null]]);
  const queue = [startKey];
  while (queue.length) {
    const current = queue.shift();
    if (current === endKey) break;
    const evidence = evidenceByKey.get(current);
    for (const neighborId of evidence?.neighborBoneIds || []) {
      const neighborKey = sourceBoneKey(evidence.sourceKey, neighborId);
      if (previous.has(neighborKey)) continue;
      previous.set(neighborKey, current);
      queue.push(neighborKey);
    }
  }
  if (!previous.has(endKey)) return null;
  const path = [];
  for (let current = endKey; current !== null;
       current = previous.get(current)) path.push(current);
  return path.reverse();
}

function matchedSourcePairs(unionFind) {
  const result = new Map();
  unionFind.clusters().forEach(members => {
    const bySource = new Map();
    members.forEach(key => {
      const sourceKey = sourceKeyFromBoneKey(key);
      const entries = bySource.get(sourceKey) || [];
      entries.push(key);
      bySource.set(sourceKey, entries);
    });
    const sources = [...bySource.keys()].sort();
    for (let leftIndex = 0; leftIndex < sources.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1;
           rightIndex < sources.length; rightIndex += 1) {
        const left = bySource.get(sources[leftIndex])?.[0];
        const right = bySource.get(sources[rightIndex])?.[0];
        if (!left || !right) continue;
        const key = [sources[leftIndex], sources[rightIndex]].join('|');
        const pairs = result.get(key) || [];
        pairs.push({left, right});
        result.set(key, pairs);
      }
    }
  });
  return result;
}

function runPathAlignment(candidates, evidenceByKey, unionFind,
    diagnostics, accepted, correspondenceStrength) {
  const candidateByPair = new Map(candidates.map(candidate => [
    crossPairKey(candidate.left.sourceBoneKey, candidate.right.sourceBoneKey),
    candidate,
  ]));
  let changed = true;
  while (changed) {
    changed = false;
    const alignments = new Map();
    matchedSourcePairs(unionFind).forEach(anchors => {
      for (let leftIndex = 0; leftIndex < anchors.length; leftIndex += 1) {
        for (let rightIndex = leftIndex + 1;
             rightIndex < anchors.length; rightIndex += 1) {
          const first = anchors[leftIndex];
          const second = anchors[rightIndex];
          const leftPath = pathBetweenSourceBones(
            first.left, second.left, evidenceByKey);
          const rightPath = pathBetweenSourceBones(
            first.right, second.right, evidenceByKey);
          if (!leftPath || !rightPath || leftPath.length !== rightPath.length
              || leftPath.length < 3) continue;
          const internal = [];
          for (let index = 1; index < leftPath.length - 1; index += 1) {
            const candidate = candidateByPair.get(crossPairKey(
              leftPath[index], rightPath[index]));
            if (!candidate || graphEvidenceContradicts(candidate)
                || unionFind.same(candidate.left.sourceBoneKey,
                  candidate.right.sourceBoneKey)) {
              internal.length = 0;
              break;
            }
            internal.push(candidate);
          }
          if (!internal.length) continue;
          const alignmentKey = internal.map(candidate => crossPairKey(
            candidate.left.sourceBoneKey, candidate.right.sourceBoneKey))
            .join('|');
          if (alignments.has(alignmentKey)) continue;
          alignments.set(alignmentKey, {
            candidates: internal,
            pathLength: leftPath.length,
            score: average(internal.map(candidate =>
              candidate.graphAlignmentScore ?? candidate.combinedConfidence), 0),
            leftAnchor: first.left,
            rightAnchor: first.right,
          });
        }
      }
    });
    const ordered = [...alignments.values()].sort((left, right) =>
      right.pathLength - left.pathLength || right.score - left.score
      || left.leftAnchor.localeCompare(right.leftAnchor)
      || left.rightAnchor.localeCompare(right.rightAnchor));
    for (const alignment of ordered) {
      if (alignment.candidates.some(candidate => unionFind.same(
        candidate.left.sourceBoneKey, candidate.right.sourceBoneKey))) continue;
      let acceptedPath = false;
      for (const candidate of alignment.candidates) {
        candidate.graphAlignmentPathLength = alignment.pathLength;
        candidate.graphAlignmentScore = Math.max(
          candidate.graphAlignmentScore || 0, alignment.score);
        if (acceptedEquivalence(candidate, 'graph-alignment-3', unionFind,
          diagnostics, accepted, correspondenceStrength)) {
          acceptedPath = true;
          changed = true;
        }
      }
      if (acceptedPath) break;
    }
  }
}

function runEquivalencePasses(candidates, evidenceByKey, unionFind) {
  const accepted = [];
  const diagnostics = [];
  const correspondenceStrength = new Map();
  markSpatialRelationships(candidates);
  candidates.forEach(candidate => {
    if (!candidate.mutualBest) {
      diagnostics.push(diagnosticCandidate(
        candidate, 'rejected', 'not_mutual'));
      return;
    }
    if (candidate.leftAmbiguous || candidate.rightAmbiguous) {
      diagnostics.push(diagnosticCandidate(
        candidate, 'rejected', 'ambiguous'));
      return;
    }
    // A source root is not an identity signal. Strong cross-source evidence
    // may seed a root-to-internal match, while the close geometric lane keeps
    // conservative old behavior for ordinary source-local matches.
    if (!candidate.strongCrossEvidence && !candidate.geometrySeed) {
      diagnostics.push(diagnosticCandidate(
        candidate, 'rejected', candidate.normalizedDistance
          > CROSS_SOURCE_STRICT_DISTANCE
          ? 'too_far' : 'insufficient_seed_evidence'));
      return;
    }
    if (candidate.combinedConfidence < .7) {
      diagnostics.push(diagnosticCandidate(
        candidate, 'rejected', 'insufficient_confidence'));
      return;
    }
    acceptedEquivalence(candidate, 'strict', unionFind, diagnostics,
      accepted, correspondenceStrength);
  });

  let changed = true;
  while (changed) {
    changed = false;
    const propagation = candidates.map(candidate => {
      const matches = graphAlignmentFeatures(
        candidate, evidenceByKey, unionFind);
      Object.assign(candidate, matches);
      const score = Math.min(1, candidate.score
        + Math.min(.3, matches.matchedNeighborCount * .15));
      return {...candidate, propagationScore: score};
    }).filter(candidate => {
      if (unionFind.same(candidate.left.sourceBoneKey,
        candidate.right.sourceBoneKey)) return false;
      if (candidate.normalizedDistance > CROSS_SOURCE_PROPAGATION_DISTANCE) {
        return false;
      }
      return candidate.matchedNeighborCount > 0;
    });
    const endpointMap = new Map();
    const competitionKey = (endpoint, otherSource) => JSON.stringify([
      endpoint, String(otherSource),
    ]);
    const endpointDescriptor = (candidate, endpoint) =>
      candidate.left.sourceBoneKey === endpoint
        ? {otherSource: candidate.right.sourceKey,
          otherKey: candidate.right.sourceBoneKey}
        : {otherSource: candidate.left.sourceKey,
          otherKey: candidate.left.sourceBoneKey};
    propagation.forEach(candidate => {
      for (const endpoint of [candidate.left.sourceBoneKey,
        candidate.right.sourceBoneKey]) {
        const descriptor = endpointDescriptor(candidate, endpoint);
        const key = competitionKey(endpoint, descriptor.otherSource);
        const incident = endpointMap.get(key) || [];
        incident.push(candidate);
        endpointMap.set(key, incident);
      }
    });
    const rankedFor = (endpoint, candidatesForEndpoint) =>
      [...candidatesForEndpoint].sort((left, right) =>
        right.propagationScore - left.propagationScore
        || (right.confidenceClass || 0) - (left.confidenceClass || 0)
        || right.combinedConfidence - left.combinedConfidence
        || right.supportReliability - left.supportReliability
        || left.normalizedDistance - right.normalizedDistance
        || endpointDescriptor(left, endpoint).otherKey.localeCompare(
          endpointDescriptor(right, endpoint).otherKey));
    const selected = propagation.filter(candidate => {
      const leftDescriptor = endpointDescriptor(
        candidate, candidate.left.sourceBoneKey);
      const rightDescriptor = endpointDescriptor(
        candidate, candidate.right.sourceBoneKey);
      const leftBest = rankedFor(candidate.left.sourceBoneKey,
        endpointMap.get(competitionKey(candidate.left.sourceBoneKey,
          leftDescriptor.otherSource)) || []);
      const rightBest = rankedFor(candidate.right.sourceBoneKey,
        endpointMap.get(competitionKey(candidate.right.sourceBoneKey,
          rightDescriptor.otherSource)) || []);
      const leftAmbiguous = candidateAmbiguous(
        leftBest[0] && {...leftBest[0], score: leftBest[0].propagationScore},
        leftBest[1] && {...leftBest[1], score: leftBest[1].propagationScore});
      const rightAmbiguous = candidateAmbiguous(
        rightBest[0] && {...rightBest[0], score: rightBest[0].propagationScore},
        rightBest[1] && {...rightBest[1], score: rightBest[1].propagationScore});
      return leftBest[0] === candidate && rightBest[0] === candidate
        && !leftAmbiguous && !rightAmbiguous
        && candidate.propagationScore >= .7
        && (candidate.directionAlignment === null
          || candidate.directionAlignment >= .55
          || candidate.crossScore >= .55
          || candidate.relativeEdgeAlignment >= .6);
    }).sort((left, right) => right.propagationScore - left.propagationScore
      || compareCandidate(right, left));
    selected.forEach(candidate => {
      if (acceptedEquivalence(candidate, 'propagation', unionFind,
        diagnostics, accepted, correspondenceStrength)) changed = true;
    });
  }
  runGraphAlignment(candidates, evidenceByKey, unionFind, diagnostics,
    accepted, correspondenceStrength);
  runPathAlignment(candidates, evidenceByKey, unionFind, diagnostics,
    accepted, correspondenceStrength);
  return {accepted, diagnostics, correspondenceStrength};
}

class ComponentUnionFind {
  constructor(ids) {
    this.parent = new Map(ids.map(id => [id, id]));
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
  const valid = points.filter(item => item.point);
  if (!valid.length) return null;
  const total = valid.reduce((sum, item) => sum + Math.max(EPSILON, item.weight), 0);
  return valid.reduce((sum, item) => {
    const weight = Math.max(EPSILON, item.weight) / total;
    return [sum[0] + item.point[0] * weight,
      sum[1] + item.point[1] * weight,
      sum[2] + item.point[2] * weight];
  }, [0, 0, 0]);
}

function medoid(points) {
  if (!points.length) return null;
  return points.map((point, index) => ({
    point, index,
    distance: points.reduce((sum, other) => sum + point.distanceTo(other), 0),
  })).sort((left, right) => left.distance - right.distance
    || left.index - right.index)[0].point.clone();
}

function buildModelJoints(unionFind, evidenceByKey, strengthByKey,
    referenceRadius) {
  const clusters = [...unionFind.clusters().values()]
    .map(members => members.sort())
    .sort((left, right) => left[0].localeCompare(right[0]));
  const keyToJoint = new Map();
  const joints = clusters.map((memberKeys, jointId) => {
    memberKeys.forEach(key => keyToJoint.set(key, jointId));
    const members = memberKeys.map(key => evidenceByKey.get(key)).filter(Boolean);
    const center = weightedAverage(members.map(evidence => ({
      point: vectorFrom(evidence.weightedCenter)?.toArray(),
      weight: Math.max(evidence.totalWeight, evidence.affectedVertexCount, 1),
    }))) || [0, 0, 0];
    const pivots = members.map(evidence => vectorFrom(
      evidence.jointPivot || (evidence.isRoot ? null : evidence.restAnchor)))
      .filter(Boolean);
    const pivotMedoid = medoid(pivots);
    const pivotTolerance = referenceRadius * CROSS_SOURCE_STRICT_DISTANCE;
    const acceptedPivots = pivotMedoid
      ? pivots.filter(pivot => pivot.distanceTo(pivotMedoid) <= pivotTolerance)
      : [];
    const restPivot = weightedAverage((acceptedPivots.length
      ? acceptedPivots : pivots).map(point => ({point: point.toArray(), weight: 1})));
    const representative = [...members].sort((left, right) =>
      (strengthByKey.get(right.sourceBoneKey) || 0)
        - (strengthByKey.get(left.sourceBoneKey) || 0)
      || right.affectedVertexCount - left.affectedVertexCount
      || right.totalWeight - left.totalWeight
      || left.sourceBoneKey.localeCompare(right.sourceBoneKey))[0] || null;
    return {
      jointId,
      jointKey: `joint=${jointId}`,
      members: members.map(evidence => ({
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
      representativeMember: representative ? {
        sourceKey: representative.sourceKey,
        boneId: representative.boneId,
        sourceBoneKey: representative.sourceBoneKey,
      } : null,
      evidence: {
        totalWeight: members.reduce((sum, item) => sum + item.totalWeight, 0),
        affectedVertexCount: members.reduce(
          (sum, item) => sum + item.affectedVertexCount, 0),
        memberCount: members.length,
      },
    };
  });
  return {joints, keyToJoint};
}

function sourceModelEdges(sourceRigs, keyToJoint) {
  const edgeMap = new Map();
  for (const rig of sourceRigs) {
    for (const component of rig?.inferredForest?.components || []) {
      const parentById = component.parentById || {};
      for (const [childValue, parentValue] of Object.entries(parentById)) {
        if (parentValue === null || parentValue === undefined) continue;
        const childBoneId = Number(childValue);
        const parentBoneId = Number(parentValue);
        const parentKey = sourceBoneKey(rig.sourceKey, parentBoneId);
        const childKey = sourceBoneKey(rig.sourceKey, childBoneId);
        const jointA = keyToJoint.get(parentKey);
        const jointB = keyToJoint.get(childKey);
        if (!Number.isInteger(jointA) || !Number.isInteger(jointB)
            || jointA === jointB) continue;
        const left = Math.min(jointA, jointB);
        const right = Math.max(jointA, jointB);
        const key = `${left}:${right}`;
        const edge = edgeMap.get(key) || {
          jointA: left, jointB: right, sourceSupportCount: 0,
          sourceEdges: [], combinedTreeScore: 0,
          relationshipType: 'source',
        };
        const sourceEdge = (component.edges || []).find(candidate => {
          const a = Number(candidate.boneA);
          const b = Number(candidate.boneB);
          return (a === parentBoneId && b === childBoneId)
            || (a === childBoneId && b === parentBoneId);
        });
        const treeScore = edgeScore(sourceEdge);
        edge.sourceEdges.push({
          sourceKey: String(rig.sourceKey),
          parentBoneId,
          childBoneId,
          treeEdgeScore: treeScore,
        });
        edge.combinedTreeScore += treeScore;
        edgeMap.set(key, edge);
      }
    }
  }
  edgeMap.forEach(edge => {
    edge.sourceSupportCount = new Set(edge.sourceEdges.map(item =>
      item.sourceKey)).size;
    edge.weight = edge.sourceSupportCount * 2 + edge.combinedTreeScore;
  });
  return [...edgeMap.values()];
}

function maximumSpanningForest(joints, edges) {
  const unionFind = new ComponentUnionFind(joints.map(joint => joint.jointId));
  return [...edges].sort((left, right) => right.weight - left.weight
    || right.sourceSupportCount - left.sourceSupportCount
    || right.combinedTreeScore - left.combinedTreeScore
    || left.jointA - right.jointA || left.jointB - right.jointB)
    .filter(edge => unionFind.union(edge.jointA, edge.jointB));
}

function rootVotes(sourceRigs, keyToJoint) {
  const votes = new Map();
  for (const rig of sourceRigs) {
    for (const component of rig?.inferredForest?.components || []) {
      const root = Number(component.rootId);
      const jointId = keyToJoint.get(sourceBoneKey(rig.sourceKey, root));
      if (!Number.isInteger(jointId)) continue;
      votes.set(jointId, (votes.get(jointId) || 0)
        + Math.max(1, component.nodeIds?.length || 1));
    }
  }
  return votes;
}

function orientModelForest(joints, edges, votes, rootOverrides = new Map()) {
  const adjacency = new Map(joints.map(joint => [joint.jointId, []]));
  edges.forEach(edge => {
    adjacency.get(edge.jointA)?.push({edge, other: edge.jointB});
    adjacency.get(edge.jointB)?.push({edge, other: edge.jointA});
  });
  adjacency.forEach(items => items.sort((left, right) =>
    left.other - right.other));
  const componentById = new Map();
  const components = [];
  const unseen = new Set(joints.map(joint => joint.jointId));
  while (unseen.size) {
    const start = Math.min(...unseen);
    const members = [];
    const queue = [start];
    unseen.delete(start);
    while (queue.length) {
      const current = queue.shift();
      members.push(current);
      (adjacency.get(current) || []).forEach(item => {
        if (!unseen.has(item.other)) return;
        unseen.delete(item.other);
        queue.push(item.other);
      });
    }
    members.sort((left, right) => left - right);
    const override = Number(rootOverrides.get(components.length));
    const rootId = members.includes(override) ? override : [...members].sort((left, right) =>
      (votes.get(right) || 0) - (votes.get(left) || 0)
      || right - left)[0];
    const parentById = {[rootId]: null};
    const childrenById = {[rootId]: []};
    const depthById = {[rootId]: 0};
    const edgeByPair = new Map(edges.map(edge => [
      `${Math.min(edge.jointA, edge.jointB)}:${Math.max(edge.jointA, edge.jointB)}`,
      edge,
    ]));
    const walk = [rootId];
    const visited = new Set([rootId]);
    while (walk.length) {
      const parent = walk.shift();
      (adjacency.get(parent) || []).forEach(item => {
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
    members.forEach(jointId => componentById.set(jointId, componentId));
    components.push({
      componentId, nodeIds: members, rootId, parentById, childrenById,
      depthById, maxDepth: Math.max(...Object.values(depthById)),
      edges: members.flatMap(jointId => (adjacency.get(jointId) || [])
        .filter(item => jointId < item.other)
        .map(item => edgeByPair.get(`${jointId}:${item.other}`)))
        .filter(Boolean),
    });
  }
  joints.forEach(joint => {
    const componentId = componentById.get(joint.jointId);
    const component = components[componentId];
    joint.parentId = component?.parentById?.[joint.jointId] ?? null;
    joint.childrenIds = [...(component?.childrenById?.[joint.jointId] || [])];
  });
  return {components, componentByJointId: componentById};
}

export function orientModelRigForest(joints, edges, rootOverrides = {}) {
  const overrides = rootOverrides instanceof Map
    ? rootOverrides
    : new Map(Object.entries(rootOverrides || {}).map(([componentId, jointId]) => [
      Number(componentId), Number(jointId)]));
  return orientModelForest(joints || [], edges || [], new Map(), overrides);
}

function componentSupport(component, joints) {
  return component.nodeIds.reduce((sum, id) =>
    sum + number(joints[id]?.evidence?.totalWeight, 0), 0);
}

function rootOverridesForFinalForest(finalComponents, sourceComponents,
    joints, votes) {
  const sourceComponentByJointId = new Map();
  (sourceComponents || []).forEach(component => {
    (component.nodeIds || []).forEach(jointId => {
      sourceComponentByJointId.set(jointId, component);
    });
  });
  const overrides = new Map();
  (finalComponents || []).forEach(finalComponent => {
    const sourceComponentsById = new Map();
    (finalComponent.nodeIds || []).forEach(jointId => {
      const sourceComponent = sourceComponentByJointId.get(jointId);
      if (sourceComponent) {
        sourceComponentsById.set(sourceComponent.componentId, sourceComponent);
      }
    });
    const trunk = [...sourceComponentsById.values()].sort((left, right) =>
      componentSupport(right, joints) - componentSupport(left, joints)
      || right.nodeIds.length - left.nodeIds.length
      || (votes.get(right.rootId) || 0) - (votes.get(left.rootId) || 0)
      || number(joints[right.rootId]?.evidence?.totalWeight, 0)
        - number(joints[left.rootId]?.evidence?.totalWeight, 0)
      || left.rootId - right.rootId
      || left.componentId - right.componentId)[0];
    if (trunk) overrides.set(finalComponent.componentId, trunk.rootId);
  });
  return overrides;
}

function jointPairKey(leftId, rightId) {
  return `${Math.min(leftId, rightId)}:${Math.max(leftId, rightId)}`;
}

function aggregateJointCrossEvidence(left, right, crossEvidenceByPair) {
  const records = [];
  (left?.members || []).forEach(leftMember => {
    (right?.members || []).forEach(rightMember => {
      if (leftMember.sourceKey === rightMember.sourceKey) return;
      const record = crossEvidenceByPair.get(crossPairKey(
        leftMember.sourceBoneKey, rightMember.sourceBoneKey));
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
  const totalWeight = records.reduce((sum, record) => sum
    + Math.max(EPSILON, record.weightedMatchStrength), 0);
  return {
    matchedVertexCount: records.reduce((sum, record) =>
      sum + record.matchedVertexCount, 0),
    weightedMatchStrength: records.reduce((sum, record) =>
      sum + record.weightedMatchStrength, 0),
    crossQuality: records.reduce((sum, record) => sum
      + record.overlapScore * Math.max(EPSILON, record.weightedMatchStrength), 0)
      / totalWeight,
    supportedPairCount: records.filter(record =>
      record.matchedVertexCount > 0).length,
  };
}

function aggregateComponentCrossEvidence(component, target, joints,
    crossEvidenceByPair, referenceRadius) {
  const jointEvidence = [];
  component.nodeIds.forEach(accessoryId => {
    target.nodeIds.forEach(targetId => {
      const evidence = aggregateJointCrossEvidence(joints[accessoryId],
        joints[targetId], crossEvidenceByPair);
      const accessoryCenter = vectorFrom(joints[accessoryId]?.restCenter);
      const targetCenter = vectorFrom(joints[targetId]?.restCenter);
      const distance = accessoryCenter && targetCenter
        ? accessoryCenter.distanceTo(targetCenter) / referenceRadius : Infinity;
      if (evidence.matchedVertexCount > 0 || Number.isFinite(distance)) {
        jointEvidence.push({accessoryId, targetId, distance, evidence});
      }
    });
  });
  const supported = jointEvidence.filter(item =>
    item.evidence.matchedVertexCount > 0);
  const supportedTargetCountByAccessory = new Map();
  supported.forEach(item => supportedTargetCountByAccessory.set(item.accessoryId,
    (supportedTargetCountByAccessory.get(item.accessoryId) || 0) + 1));
  const totalWeight = supported.reduce((sum, item) => sum
    + Math.max(EPSILON, item.evidence.weightedMatchStrength), 0);
  return {
    matchedVertexCount: supported.reduce((sum, item) =>
      sum + item.evidence.matchedVertexCount, 0),
    weightedMatchStrength: supported.reduce((sum, item) =>
      sum + item.evidence.weightedMatchStrength, 0),
    crossQuality: totalWeight > EPSILON
      ? supported.reduce((sum, item) => sum
        + item.evidence.crossQuality
        * Math.max(EPSILON, item.evidence.weightedMatchStrength), 0)
        / totalWeight : 0,
    supportedJointPairCount: supported.length,
    nearestSupportedDistance: supported.length
      ? Math.min(...supported.map(item => item.distance)) : null,
    supportedTargetCountByAccessory,
  };
}

function attachmentCandidates(joints, forest, referenceRadius,
    crossEvidenceByPair = new Map()) {
  const candidates = [];
  const components = forest.components;
  const componentEvidenceByPair = new Map();
  const componentEvidenceFor = (accessory, target) => {
    const key = `${accessory.componentId}:${target.componentId}`;
    if (!componentEvidenceByPair.has(key)) {
      componentEvidenceByPair.set(key, aggregateComponentCrossEvidence(
        accessory, target, joints, crossEvidenceByPair, referenceRadius));
    }
    return componentEvidenceByPair.get(key);
  };
  const directions = new Map(joints.map(joint => [joint.jointId,
    vectorFrom(joint.restDirection)]));
  // The model joint direction is represented by the selected source member's
  // rest frame +Y. Keeping this derivation here avoids inventing labels or
  // changing the source-local rest-frame contract.
  joints.forEach(joint => {
    if (directions.get(joint.jointId)) return;
    const frame = new Quaternion(...quaternionArray(joint.restFrame));
    directions.set(joint.jointId, new Vector3(0, 1, 0)
      .applyQuaternion(frame).normalize());
  });
  for (const accessory of components) {
    const accessorySupport = componentSupport(accessory, joints);
    for (const target of components) {
      if (target.componentId === accessory.componentId) continue;
      const targetSupport = componentSupport(target, joints);
      // Attach smaller inferred components to a larger body. This also makes
      // the direction of an attachment deterministic when two disconnected
      // components have identical synthetic support in a test fixture.
      if (targetSupport < accessorySupport
          || targetSupport === accessorySupport
            && target.nodeIds.length < accessory.nodeIds.length
          || targetSupport === accessorySupport
            && target.nodeIds.length === accessory.nodeIds.length
            && target.componentId > accessory.componentId) continue;
      const componentEvidence = componentEvidenceFor(accessory, target);
      for (const targetId of target.nodeIds) {
        const targetJoint = joints[targetId];
        const targetAnchor = vectorFrom(targetJoint?.restCenter);
        if (!targetAnchor) continue;
        for (const accessoryId of accessory.nodeIds) {
          const accessoryJoint = joints[accessoryId];
          const accessoryAnchor = vectorFrom(accessoryJoint?.restCenter);
          if (!accessoryAnchor) continue;
          const towardAccessory = accessoryAnchor.clone().sub(targetAnchor);
          const distance = towardAccessory.length();
          if (distance <= EPSILON) continue;
          const normalizedDistance = distance / referenceRadius;
          if (normalizedDistance > CROSS_SOURCE_CANDIDATE_DISTANCE) continue;
          const accessoryDirection = directions.get(accessoryId);
          const directionAlignment = accessoryDirection
            ? Math.abs(accessoryDirection.dot(towardAccessory.normalize()))
            : null;
          const pairEvidence = aggregateJointCrossEvidence(targetJoint,
            accessoryJoint, crossEvidenceByPair);
          const nearbyTargetAgreement = Math.max(0,
            (componentEvidence.supportedTargetCountByAccessory.get(accessoryId)
              || 0) - (pairEvidence.matchedVertexCount > 0 ? 1 : 0));
          const targetChildren = targetJoint.childrenIds || [];
          const targetDirection = directions.get(targetId);
          const targetTopology = targetChildren.length ? 1 : .5;
          const supportScore = targetSupport / Math.max(
            targetSupport, accessorySupport, EPSILON);
          const distanceScore = clamp(1 - normalizedDistance
            / CROSS_SOURCE_CANDIDATE_DISTANCE);
          const componentSupportScore = clamp(
            componentEvidence.matchedVertexCount / 32) * .35
            + clamp(componentEvidence.weightedMatchStrength / 2) * .25
            + componentEvidence.crossQuality * .2
            + clamp(componentEvidence.supportedJointPairCount / 3) * .2;
          const endpointEvidenceScore = clamp(pairEvidence.matchedVertexCount
            / 8) * .35
            + clamp(pairEvidence.weightedMatchStrength / .5) * .25
            + pairEvidence.crossQuality * .2
            + clamp(nearbyTargetAgreement / 3) * .2;
          const endpointScore = distanceScore * .55
            + (directionAlignment ?? .5) * .3
            + supportScore * .1 + targetTopology * .05
            + (accessory.rootId === accessoryId ? .15 : 0);
          const endpointCombinedScore = endpointScore * .65
            + endpointEvidenceScore * .35;
          const score = componentSupportScore * .45
            + endpointCombinedScore * .55;
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
            componentWeightedMatchStrength:
              componentEvidence.weightedMatchStrength,
            componentCrossQuality: componentEvidence.crossQuality,
            componentSupportedJointPairCount:
              componentEvidence.supportedJointPairCount,
            nearestSupportedDistance: componentEvidence.nearestSupportedDistance,
            accessoryRoot: accessory.rootId === accessoryId,
            endpointMatchedVertexCount: pairEvidence.matchedVertexCount,
            endpointWeightedMatchStrength: pairEvidence.weightedMatchStrength,
            endpointCrossQuality: pairEvidence.crossQuality,
            nearbyTargetAgreement,
          });
        }
      }
    }
  }
  return candidates.sort((left, right) => right.score - left.score
    || left.normalizedDistance - right.normalizedDistance
    || left.jointA - right.jointA || left.jointB - right.jointB
    || left.accessoryComponentId - right.accessoryComponentId
    || left.targetComponentId - right.targetComponentId);
}

function addAttachments(joints, sourceEdges, forest, referenceRadius,
    crossEvidenceByPair = new Map()) {
  const candidates = attachmentCandidates(joints, forest, referenceRadius,
    crossEvidenceByPair);
  const diagnostics = [];
  const accepted = [];
  const usedAccessoryComponents = new Set();
  const candidatesByAccessory = new Map();
  candidates.forEach(candidate => {
    const byTarget = candidatesByAccessory.get(
      candidate.accessoryComponentId) || new Map();
    const entries = byTarget.get(candidate.targetComponentId) || [];
    entries.push(candidate);
    byTarget.set(candidate.targetComponentId, entries);
    candidatesByAccessory.set(candidate.accessoryComponentId, byTarget);
  });
  const compareEndpoint = (left, right) =>
    right.endpointEvidenceScore - left.endpointEvidenceScore
    || right.nearbyTargetAgreement - left.nearbyTargetAgreement
    || right.endpointScore - left.endpointScore
    || right.score - left.score
    || left.normalizedDistance - right.normalizedDistance
    || left.jointA - right.jointA || left.jointB - right.jointB;
  const compareComponent = (left, right) =>
    right.componentScore - left.componentScore
    || right.componentMatchedVertexCount - left.componentMatchedVertexCount
    || right.componentWeightedMatchStrength
      - left.componentWeightedMatchStrength
    || compareEndpoint(left, right);
  const groupScore = group => [...group].sort(compareComponent)[0];
  for (const [accessoryComponentId, byTarget] of candidatesByAccessory) {
    const groups = [...byTarget.entries()].map(([targetComponentId, group]) => ({
      targetComponentId,
      candidates: group,
      best: groupScore(group),
    })).sort((left, right) => compareComponent(left.best, right.best)
      || left.targetComponentId - right.targetComponentId);
    const bestGroup = groups[0];
    if (!bestGroup) continue;
    if (usedAccessoryComponents.has(accessoryComponentId)) {
      bestGroup.candidates.forEach(candidate => diagnostics.push({...candidate,
        decision: 'rejected', rejectionReason: 'attachment_cycle'}));
      continue;
    }
    const secondGroup = groups[1];
    if (secondGroup
        && bestGroup.best.componentScore - secondGroup.best.componentScore
          < CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN
        && bestGroup.best.score - secondGroup.best.score
          < CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN) {
      groups.flatMap(group => group.candidates).forEach(candidate =>
        diagnostics.push({...candidate, decision: 'rejected',
          rejectionReason: candidate === bestGroup.best
            ? 'attachment_ambiguous' : 'attachment_component_competition'}));
      continue;
    }
    const competing = [...bestGroup.candidates].sort(compareEndpoint);
    const best = competing[0];
    const second = competing[1];
    const evidenceWinner = second && (
      best.endpointMatchedVertexCount > second.endpointMatchedVertexCount
      || best.nearbyTargetAgreement > second.nearbyTargetAgreement
      || best.endpointEvidenceScore - second.endpointEvidenceScore >= .1
      || best.accessoryRoot && !second.accessoryRoot
        && best.componentMatchedVertexCount >= 8);
    if (second && best.score - second.score
        < CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN && !evidenceWinner) {
      competing.forEach(candidate => diagnostics.push({...candidate,
        decision: 'rejected', rejectionReason: candidate === best
          ? 'attachment_ambiguous' : 'attachment_competition'}));
      continue;
    }
    const edge = {
      jointA: best.jointA,
      jointB: best.jointB,
      targetComponentId: best.targetComponentId,
      accessoryComponentId: best.accessoryComponentId,
      sourceSupportCount: 0,
      sourceEdges: [],
      combinedTreeScore: best.score,
      relationshipType: 'attachment',
      weight: best.score,
      attachmentScore: best.score,
    };
    accepted.push(edge);
    usedAccessoryComponents.add(accessoryComponentId);
    candidates.forEach(candidate => {
      if (candidate.accessoryComponentId !== accessoryComponentId) return;
      diagnostics.push({...candidate, decision: candidate === best
        ? 'accepted' : 'rejected', rejectionReason: candidate === best
        ? null : candidate.targetComponentId === best.targetComponentId
          ? 'attachment_competition' : 'attachment_component_competition',
        survivedFinalForest: false});
    });
  }
  return {edges: [...sourceEdges, ...accepted], accepted, diagnostics};
}

function evidenceSnapshot(evidence) {
  return [...evidence.entries()].map(([key, item]) => [key, {
    ...item,
    weightedCenter: [...item.weightedCenter],
    jointPivot: item.jointPivot ? [...item.jointPivot] : null,
    restAnchor: [...item.restAnchor],
    restDirection: item.restDirection ? [...item.restDirection] : null,
    restFrame: [...item.restFrame],
    childBoneIds: [...item.childBoneIds],
    sourceEdgeEvidence: item.sourceEdgeEvidence.map(edge => ({...edge})),
  }]);
}

export function buildModelRigReconciliation(sourceRigs = [], options = {}) {
  const rigs = [...sourceRigs].filter(rig => rig?.sourceKey !== undefined)
    .sort((left, right) => String(left.sourceKey)
      .localeCompare(String(right.sourceKey)));
  const evidenceByKey = new Map();
  rigs.forEach(rig => collectSourceBoneEvidence(rig).forEach((evidence, key) =>
    evidenceByKey.set(key, evidence)));
  const referenceRadius = Math.max(EPSILON, number(options.modelReferenceRadius,
    modelReferenceRadius(evidenceByKey)));
  const candidateBuild = buildCandidates(
    evidenceByKey, referenceRadius, rigs);
  const candidates = candidateBuild.candidates;
  const unionFind = new GuardedUnionFind([...evidenceByKey.keys()]);
  const equivalence = runEquivalencePasses(
    candidates, evidenceByKey, unionFind);
  const model = buildModelJoints(unionFind, evidenceByKey,
    equivalence.correspondenceStrength, referenceRadius);
  const sourceEdges = sourceModelEdges(rigs, model.keyToJoint);
  const sourceForestEdges = maximumSpanningForest(model.joints, sourceEdges);
  const votes = rootVotes(rigs, model.keyToJoint);
  const sourceForest = orientModelForest(model.joints, sourceForestEdges, votes);
  const attachments = addAttachments(
    model.joints, sourceForestEdges, sourceForest, referenceRadius,
    candidateBuild.crossEvidenceByPair);
  const finalEdges = maximumSpanningForest(model.joints, attachments.edges);
  // Attachments define the boundary between pre-oriented forests. They must
  // never choose the global posing root: preserve the dominant pre-attachment
  // component's root so every attached branch inherits the trunk transform.
  const finalRootOverrides = rootOverridesForFinalForest(
    orientModelForest(model.joints, finalEdges, votes).components,
    sourceForest.components, model.joints, votes);
  const finalForest = orientModelForest(
    model.joints, finalEdges, votes, finalRootOverrides);
  const survivingAttachments = finalEdges.filter(edge =>
    edge.relationshipType === 'attachment');
  attachments.diagnostics.forEach(diagnostic => {
    if (diagnostic.decision !== 'accepted') return;
    diagnostic.survivedFinalForest = survivingAttachments.some(edge =>
      edge.jointA === diagnostic.jointA && edge.jointB === diagnostic.jointB);
  });
  const acceptedEquivalences = equivalence.accepted.map(item => ({...item}));
  const rejectedCandidates = [...equivalence.diagnostics,
    ...attachments.diagnostics.map(item => ({
      left: {jointId: item.jointA}, right: {jointId: item.jointB},
      normalizedDistance: item.normalizedDistance,
      directionAlignment: item.directionAlignment,
      score: item.score,
      componentScore: item.componentScore,
      componentMatchedVertexCount: item.componentMatchedVertexCount,
      componentWeightedMatchStrength: item.componentWeightedMatchStrength,
      componentCrossQuality: item.componentCrossQuality,
      componentSupportedJointPairCount: item.componentSupportedJointPairCount,
      accessoryRoot: item.accessoryRoot,
      endpointScore: item.endpointScore,
      endpointMatchedVertexCount: item.endpointMatchedVertexCount,
      endpointWeightedMatchStrength: item.endpointWeightedMatchStrength,
      endpointCrossQuality: item.endpointCrossQuality,
      nearbyTargetAgreement: item.nearbyTargetAgreement,
      decision: item.decision,
      rejectionReason: item.rejectionReason,
    }))];
  const sourceBoneToModelJointId = Object.fromEntries(
    [...model.keyToJoint.entries()]);
  const unmatchedCount = [...unionFind.clusters().values()]
    .filter(members => members.length === 1).length;
  const ambiguousCount = rejectedCandidates.filter(item =>
    item.rejectionReason === 'ambiguous'
      || item.rejectionReason === 'attachment_ambiguous').length;
  const mainComponent = [...finalForest.components].sort((left, right) =>
    componentSupport(right, model.joints) - componentSupport(left, model.joints)
    || right.nodeIds.length - left.nodeIds.length
    || left.componentId - right.componentId)[0] || null;
  const mainComponentId = mainComponent?.componentId ?? null;
  const unresolvedComponents = finalForest.components.filter(component =>
    component.componentId !== mainComponentId);
  const unresolvedSourceKeys = [...new Set(unresolvedComponents.flatMap(component =>
    component.nodeIds.flatMap(jointId => (model.joints[jointId]?.members || [])
      .map(member => member.sourceKey))))].sort();
  const reconciliation = {
    sourceCount: rigs.length,
    sourceBoneCount: evidenceByKey.size,
    candidateCount: candidates.length,
    modelJointCount: model.joints.length,
    equivalenceClusterCount: [...unionFind.clusters().values()]
      .filter(members => members.length > 1).length,
    equivalentSourceBoneCount: [...unionFind.clusters().values()]
      .filter(members => members.length > 1)
      .reduce((sum, members) => sum + members.length, 0),
    attachmentCount: survivingAttachments.length,
    unmatchedCount,
    ambiguousCount,
    componentCount: finalForest.components.length,
    mainComponentId,
    unresolvedComponentCount: unresolvedComponents.length,
    unresolvedJointCount: unresolvedComponents.reduce((sum, component) =>
      sum + component.nodeIds.length, 0),
    unresolvedSourceKeys,
    modelReferenceRadius: referenceRadius,
    joints: model.joints,
    acceptedEquivalences,
    acceptedAttachments: survivingAttachments,
    rejectedCandidates,
  };
  return {
    modelReferenceRadius: referenceRadius,
    sourceBoneEvidence: evidenceSnapshot(evidenceByKey),
    restAnchorBySourceBoneKey: Object.fromEntries(
      [...evidenceByKey.entries()].map(([key, item]) => [
        key, [...item.restAnchor]])),
    sourceBoneToModelJointId,
    sourceBoneToModelJointMap: model.keyToJoint,
    joints: model.joints,
    edges: finalEdges,
    forestEdges: finalEdges,
    components: finalForest.components,
    componentByJointId: finalForest.componentByJointId,
    reconciliation,
  };
}
