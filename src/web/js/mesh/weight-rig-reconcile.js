// Pure, conservative reconciliation of source-local inferred rigs. The
// authored skinning streams remain source-scoped; this module only builds a
// viewer-owned model graph from neutral geometry and source topology.

import {Quaternion, Vector3} from 'three';

export const CROSS_SOURCE_CANDIDATE_DISTANCE = 0.1;
export const CROSS_SOURCE_STRICT_DISTANCE = 0.04;
export const CROSS_SOURCE_PROPAGATION_DISTANCE = 0.06;
export const CROSS_SOURCE_AMBIGUITY_MARGIN = 0.05;
export const CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN = 0.08;

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

function collectSourceBoneEvidence(rig) {
  const result = new Map();
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
        };
        const leftMass = leftInfluence.weight * confidence;
        const rightMass = rightInfluence.weight * confidence;
        record.matchedVertexCount += 1;
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
  const crossScore = crossEvidence?.overlapScore ?? null;
  const features = [
    {value: clamp(1 - normalizedDistance / CROSS_SOURCE_CANDIDATE_DISTANCE), weight: .34},
    {value: crossScore, weight: .36},
    {value: directionAlignment, weight: .1},
    {value: radiusRatio, weight: .05},
    {value: topology.value, weight: .1},
    {value: incomingAlignment, weight: .05},
  ].filter(item => item.value !== null && item.value !== undefined);
  const weightTotal = features.reduce((sum, item) => sum + item.weight, 0);
  const score = features.reduce((sum, item) => sum + item.value * item.weight, 0)
    / Math.max(EPSILON, weightTotal);
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
    crossScore,
    matchedParent: false,
    matchedChildCount: 0,
    score,
    topology,
  };
}

function compareCandidate(left, right) {
  return left.score - right.score
    || right.normalizedDistance - left.normalizedDistance
    || right.left.sourceBoneKey.localeCompare(left.left.sourceBoneKey)
    || right.right.sourceBoneKey.localeCompare(left.right.sourceBoneKey);
}

function candidateAmbiguous(best, second) {
  return !!best && !!second
    && best.score - second.score < CROSS_SOURCE_AMBIGUITY_MARGIN;
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

function refFor(evidence, boneId) {
  return boneId === null || boneId === undefined
    ? null : sourceBoneKey(evidence.sourceKey, boneId);
}

function neighborMatches(candidate, evidenceByKey, unionFind) {
  const left = evidenceByKey.get(candidate.left.sourceBoneKey);
  const right = evidenceByKey.get(candidate.right.sourceBoneKey);
  if (!left || !right) return {matchedParent: false, matchedChildCount: 0};
  const leftParent = refFor(left, left.parentBoneId);
  const rightParent = refFor(right, right.parentBoneId);
  const matchedParent = !!leftParent && !!rightParent
    && unionFind.same(leftParent, rightParent);
  const matchedChildCount = left.childBoneIds.filter(leftChild =>
    right.childBoneIds.some(rightChild => unionFind.same(
      refFor(left, leftChild), refFor(right, rightChild)))).length;
  return {matchedParent, matchedChildCount};
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
    matchedParent: !!candidate.matchedParent,
    matchedChildCount: candidate.matchedChildCount || 0,
    crossScore: candidate.crossScore,
    matchedVertexCount: candidate.crossEvidence?.matchedVertexCount || 0,
    weightedMatchStrength: candidate.crossEvidence?.weightedMatchStrength || 0,
    crossContainment: candidate.crossEvidence?.crossContainment ?? null,
    crossJaccard: candidate.crossEvidence?.crossJaccard ?? null,
    score: candidate.score,
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
  candidates.forEach(candidate => {
    for (const endpoint of [candidate.left.sourceBoneKey,
      candidate.right.sourceBoneKey]) {
      const incident = endpointMap.get(endpoint) || [];
      incident.push(candidate);
      endpointMap.set(endpoint, incident);
    }
  });
  const otherEndpoint = (candidate, endpoint) =>
    candidate.left.sourceBoneKey === endpoint
      ? candidate.right.sourceBoneKey : candidate.left.sourceBoneKey;
  const bestFor = (endpoint, incident) => [...incident].sort((left, right) =>
    left.normalizedDistance - right.normalizedDistance
      || right.score - left.score
      || otherEndpoint(left, endpoint).localeCompare(
        otherEndpoint(right, endpoint)))[0] || null;
  candidates.forEach(candidate => {
    const leftCandidates = [...(endpointMap.get(candidate.left.sourceBoneKey)
      || [])].sort((left, right) => left.normalizedDistance
      - right.normalizedDistance || right.score - left.score
      || otherEndpoint(left, candidate.left.sourceBoneKey).localeCompare(
        otherEndpoint(right, candidate.left.sourceBoneKey)));
    const rightCandidates = [...(endpointMap.get(candidate.right.sourceBoneKey)
      || [])].sort((left, right) => left.normalizedDistance
      - right.normalizedDistance || right.score - left.score
      || otherEndpoint(left, candidate.right.sourceBoneKey).localeCompare(
        otherEndpoint(right, candidate.right.sourceBoneKey)));
    candidate.mutualBest = otherEndpoint(
      bestFor(candidate.left.sourceBoneKey, leftCandidates),
      candidate.left.sourceBoneKey) === candidate.right.sourceBoneKey
      && otherEndpoint(
        bestFor(candidate.right.sourceBoneKey, rightCandidates),
        candidate.right.sourceBoneKey) === candidate.left.sourceBoneKey;
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
  correspondenceStrength.set(left, Math.max(
    correspondenceStrength.get(left) || 0, candidate.score));
  correspondenceStrength.set(right, Math.max(
    correspondenceStrength.get(right) || 0, candidate.score));
  return true;
}

function runEquivalencePasses(candidates, evidenceByKey, unionFind) {
  const accepted = [];
  const diagnostics = [];
  const correspondenceStrength = new Map();
  markSpatialRelationships(candidates);
  candidates.forEach(candidate => {
    if (candidate.normalizedDistance > CROSS_SOURCE_STRICT_DISTANCE) {
      diagnostics.push(diagnosticCandidate(
        candidate, 'rejected', 'too_far'));
      return;
    }
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
    // Root status is a heuristic for choosing a source-local forest root, not
    // model-joint identity. A root may correspond to an internal joint in a
    // partial source, but that claim needs neutral vertex/weight evidence so
    // a nearby accessory root does not collapse into the body by proximity.
    if (candidate.topology.rootConflict
        && (candidate.crossScore === null || candidate.crossScore < .55)) {
      diagnostics.push(diagnosticCandidate(
        candidate, 'rejected', 'insufficient_cross_source_evidence'));
      return;
    }
    if (candidate.directionAlignment !== null
        && candidate.directionAlignment < .55
        && (candidate.crossScore === null || candidate.crossScore < .55)) {
      diagnostics.push(diagnosticCandidate(
        candidate, 'rejected', 'direction_conflict'));
      return;
    }
    if (candidate.score < .7) {
      diagnostics.push(diagnosticCandidate(
        candidate, 'rejected', 'topology_conflict'));
      return;
    }
    acceptedEquivalence(candidate, 'strict', unionFind, diagnostics,
      accepted, correspondenceStrength);
  });

  let changed = true;
  while (changed) {
    changed = false;
    const propagation = candidates.map(candidate => {
      const matches = neighborMatches(candidate, evidenceByKey, unionFind);
      candidate.matchedParent = matches.matchedParent;
      candidate.matchedChildCount = matches.matchedChildCount;
      const score = Math.min(1, candidate.score
        + (matches.matchedParent ? .2 : 0)
        + Math.min(.2, matches.matchedChildCount * .1));
      return {...candidate, propagationScore: score};
    }).filter(candidate => {
      if (unionFind.same(candidate.left.sourceBoneKey,
        candidate.right.sourceBoneKey)) return false;
      if (candidate.normalizedDistance > CROSS_SOURCE_PROPAGATION_DISTANCE) {
        return false;
      }
      return candidate.matchedParent || candidate.matchedChildCount > 0;
    });
    const endpointMap = new Map();
    propagation.forEach(candidate => {
      for (const endpoint of [candidate.left.sourceBoneKey,
        candidate.right.sourceBoneKey]) {
        const incident = endpointMap.get(endpoint) || [];
        incident.push(candidate);
        endpointMap.set(endpoint, incident);
      }
    });
    const otherEndpoint = (candidate, endpoint) =>
      candidate.left.sourceBoneKey === endpoint
        ? candidate.right.sourceBoneKey : candidate.left.sourceBoneKey;
    const rankedFor = (endpoint, candidatesForEndpoint) =>
      [...candidatesForEndpoint].sort((left, right) =>
        right.propagationScore - left.propagationScore
        || left.normalizedDistance - right.normalizedDistance
        || otherEndpoint(left, endpoint).localeCompare(
          otherEndpoint(right, endpoint)));
    const selected = propagation.filter(candidate => {
      const leftBest = rankedFor(candidate.left.sourceBoneKey,
        endpointMap.get(candidate.left.sourceBoneKey) || []);
      const rightBest = rankedFor(candidate.right.sourceBoneKey,
        endpointMap.get(candidate.right.sourceBoneKey) || []);
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
          || candidate.crossScore >= .55);
    }).sort((left, right) => right.propagationScore - left.propagationScore
      || compareCandidate(right, left));
    selected.forEach(candidate => {
      if (acceptedEquivalence(candidate, 'propagation', unionFind,
        diagnostics, accepted, correspondenceStrength)) changed = true;
    });
  }
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

function attachmentCandidates(joints, forest, referenceRadius) {
  const candidates = [];
  const components = forest.components;
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
          if (directionAlignment !== null && directionAlignment < .1) continue;
          const targetChildren = targetJoint.childrenIds || [];
          const targetDirection = directions.get(targetId);
          const targetTopology = targetChildren.length ? 1 : .5;
          const supportScore = targetSupport / Math.max(
            targetSupport, accessorySupport, EPSILON);
          const score = clamp(1 - normalizedDistance
            / CROSS_SOURCE_CANDIDATE_DISTANCE) * .55
            + (directionAlignment ?? .5) * .3
            + supportScore * .1 + targetTopology * .05;
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
            targetDirection: targetDirection?.toArray() || null,
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

function addAttachments(joints, sourceEdges, forest, referenceRadius) {
  const candidates = attachmentCandidates(joints, forest, referenceRadius);
  const diagnostics = [];
  const accepted = [];
  const usedAccessoryComponents = new Set();
  const candidatesByAccessory = new Map();
  candidates.forEach(candidate => {
    const entries = candidatesByAccessory.get(candidate.accessoryComponentId) || [];
    entries.push(candidate);
    candidatesByAccessory.set(candidate.accessoryComponentId, entries);
  });
  for (const candidate of candidates) {
    if (usedAccessoryComponents.has(candidate.accessoryComponentId)) {
      diagnostics.push({...candidate, decision: 'rejected',
        rejectionReason: 'attachment_cycle'});
      continue;
    }
    const competing = candidatesByAccessory.get(candidate.accessoryComponentId)
      || [];
    const best = competing[0];
    if (best !== candidate) {
      diagnostics.push({...candidate, decision: 'rejected',
        rejectionReason: 'attachment_competition'});
      continue;
    }
    const second = competing[1];
    if (second && candidate.score - second.score
        < CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN) {
      diagnostics.push({...candidate, decision: 'rejected',
        rejectionReason: 'attachment_ambiguous'});
      continue;
    }
    const edge = {
      jointA: candidate.jointA,
      jointB: candidate.jointB,
      sourceSupportCount: 0,
      sourceEdges: [],
      combinedTreeScore: candidate.score,
      relationshipType: 'attachment',
      weight: candidate.score,
      attachmentScore: candidate.score,
    };
    accepted.push(edge);
    usedAccessoryComponents.add(candidate.accessoryComponentId);
    diagnostics.push({...candidate, decision: 'accepted',
      rejectionReason: null, survivedFinalForest: false});
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
    model.joints, sourceForestEdges, sourceForest, referenceRadius);
  const finalEdges = maximumSpanningForest(model.joints, attachments.edges);
  const initialFinalForest = orientModelForest(model.joints, finalEdges, votes);
  const finalRootOverrides = new Map();
  initialFinalForest.components.forEach(component => {
    const members = new Set(component.nodeIds);
    const attachment = finalEdges.filter(edge =>
      edge.relationshipType === 'attachment'
      && members.has(edge.jointA) && members.has(edge.jointB))
      .sort((left, right) => right.attachmentScore - left.attachmentScore
        || left.jointA - right.jointA || left.jointB - right.jointB)[0];
    if (attachment) finalRootOverrides.set(
      component.componentId, attachment.jointA);
  });
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
      score: item.score, decision: item.decision,
      rejectionReason: item.rejectionReason,
    }))];
  const sourceBoneToModelJointId = Object.fromEntries(
    [...model.keyToJoint.entries()]);
  const unmatchedCount = [...unionFind.clusters().values()]
    .filter(members => members.length === 1).length;
  const ambiguousCount = rejectedCandidates.filter(item =>
    item.rejectionReason === 'ambiguous'
      || item.rejectionReason === 'attachment_ambiguous').length;
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
