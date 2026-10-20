// Conservative candidate scoring and guarded cross-source joint equivalence.
import {
  EPSILON,
  sourceBoneKey,
  directionAvailable,
  evidenceVector,
  clamp,
  CROSS_SOURCE_STRONG_VERTEX_COUNT,
  CROSS_SOURCE_STRONG_WEIGHT_STRENGTH,
  crossPairKey,
} from './weight-rig-reconcile-evidence.js';

export const CROSS_SOURCE_CANDIDATE_DISTANCE = 0.1;

export const CROSS_SOURCE_STRICT_DISTANCE = 0.04;

export const CROSS_SOURCE_PROPAGATION_DISTANCE = 0.06;

export const CROSS_SOURCE_AMBIGUITY_MARGIN = 0.05;

export const CROSS_SOURCE_GRAPH_ALIGNMENT_MARGIN = 0.1;

export const CROSS_SOURCE_GRAPH_ALIGNMENT_MIN_SCORE = 0.6;

function neighborDirection(evidence, allEvidence, incoming) {
  if (!evidence) return null;
  if (incoming) {
    const parent = allEvidence.get(sourceBoneKey(evidence.sourceKey, evidence.parentBoneId));
    const vector =
      parent &&
      evidenceVector(evidence, '_restAnchorVector', 'restAnchor') &&
      evidenceVector(parent, '_restAnchorVector', 'restAnchor')
        ? evidenceVector(evidence, '_restAnchorVector', 'restAnchor').sub(
            evidenceVector(parent, '_restAnchorVector', 'restAnchor'),
          )
        : null;
    if (vector?.length() > EPSILON) return vector.normalize();
  }
  return directionAvailable(evidence) ? evidenceVector(evidence, '_restDirectionVector', 'restDirection') : null;
}

function topologyFeature(left, right) {
  const parent =
    (left.parentBoneId === null && right.parentBoneId === null) ||
    (left.parentBoneId !== null && right.parentBoneId !== null);
  const leftChildren = left.childBoneIds.length;
  const rightChildren = right.childBoneIds.length;
  const childSimilarity = 1 - clamp(Math.abs(leftChildren - rightChildren) / Math.max(1, leftChildren, rightChildren));
  const degreeSimilarity = 1 - clamp(Math.abs(left.degree - right.degree) / Math.max(1, left.degree, right.degree));
  return {
    parentPresent: parent,
    childCount: childSimilarity,
    degree: degreeSimilarity,
    value: Number(parent) * 0.35 + childSimilarity * 0.4 + degreeSimilarity * 0.25,
    rootConflict: left.isRoot !== right.isRoot,
  };
}

function candidateFor(left, right, allEvidence, crossEvidenceByPair, gate) {
  // Equivalence compares like-for-like neutral regions. The parent joint pivot
  // is a structural anchor, not a replacement for a source bone's region.
  const leftCenter = evidenceVector(left, '_weightedCenterVector', 'weightedCenter');
  const rightCenter = evidenceVector(right, '_weightedCenterVector', 'weightedCenter');
  const distance = leftCenter?.distanceTo(rightCenter);
  if (!Number.isFinite(distance)) return null;
  const normalizedDistance = distance / gate.referenceRadius;
  if (normalizedDistance > CROSS_SOURCE_CANDIDATE_DISTANCE) return null;
  const directionAlignment =
    directionAvailable(left) && directionAvailable(right)
      ? Math.abs(
          evidenceVector(left, '_restDirectionVector', 'restDirection').dot(
            evidenceVector(right, '_restDirectionVector', 'restDirection'),
          ),
        )
      : null;
  const radiusRatio =
    left.weightedRadius > EPSILON && right.weightedRadius > EPSILON
      ? Math.min(left.weightedRadius, right.weightedRadius) / Math.max(left.weightedRadius, right.weightedRadius)
      : null;
  const leftIncoming = neighborDirection(left, allEvidence, true);
  const rightIncoming = neighborDirection(right, allEvidence, true);
  const incomingAlignment = leftIncoming && rightIncoming ? Math.abs(leftIncoming.dot(rightIncoming)) : null;
  const topology = topologyFeature(left, right);
  const crossEvidence = crossEvidenceByPair.get(crossPairKey(left.sourceBoneKey, right.sourceBoneKey)) || null;
  const crossQuality = crossEvidence?.overlapScore ?? null;
  const geometryFeatures = [
    { value: clamp(1 - normalizedDistance / CROSS_SOURCE_CANDIDATE_DISTANCE), weight: 0.34 },
    { value: directionAlignment, weight: 0.1 },
    { value: radiusRatio, weight: 0.05 },
    { value: topology.value, weight: 0.1 },
    { value: incomingAlignment, weight: 0.05 },
  ].filter((item) => item.value !== null && item.value !== undefined);
  const geometryWeightTotal = geometryFeatures.reduce((sum, item) => sum + item.weight, 0);
  const geometryConfidence =
    geometryFeatures.reduce((sum, item) => sum + item.value * item.weight, 0) / Math.max(EPSILON, geometryWeightTotal);
  const supportReliability = crossEvidence?.supportReliability || 0;
  const crossConfidence = crossQuality === null ? 0 : crossQuality * supportReliability;
  // Cross-source evidence is corroborating evidence. This combination is
  // monotonic in both inputs, so partial overlap cannot punish a good shape
  // and a one-vertex coincidence cannot become a seed by itself.
  const combinedConfidence = 1 - (1 - geometryConfidence) * (1 - crossConfidence);
  const strongCrossEvidence =
    !!crossEvidence &&
    crossEvidence.matchedVertexCount >= CROSS_SOURCE_STRONG_VERTEX_COUNT &&
    crossEvidence.weightedMatchStrength >= CROSS_SOURCE_STRONG_WEIGHT_STRENGTH &&
    crossQuality >= 0.7;
  const exactRootAnchor =
    normalizedDistance <= 0.01 &&
    geometryConfidence >= 0.8 &&
    topology.degree >= 0.75 &&
    (directionAlignment === null || directionAlignment >= 0.75);
  const geometrySeed =
    normalizedDistance <= CROSS_SOURCE_STRICT_DISTANCE &&
    geometryConfidence >= 0.7 &&
    (!topology.rootConflict || exactRootAnchor) &&
    (directionAlignment === null || directionAlignment >= 0.55);
  const confidenceClass = strongCrossEvidence ? 2 : geometrySeed ? 1 : 0;
  return {
    left: { sourceKey: left.sourceKey, boneId: left.boneId, sourceBoneKey: left.sourceBoneKey },
    right: { sourceKey: right.sourceKey, boneId: right.boneId, sourceBoneKey: right.sourceBoneKey },
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
  return (
    (left.confidenceClass || 0) - (right.confidenceClass || 0) ||
    left.combinedConfidence - right.combinedConfidence ||
    left.supportReliability - right.supportReliability ||
    (left.crossQuality || 0) - (right.crossQuality || 0) ||
    left.geometryConfidence - right.geometryConfidence ||
    right.normalizedDistance - left.normalizedDistance ||
    right.left.sourceBoneKey.localeCompare(left.left.sourceBoneKey) ||
    right.right.sourceBoneKey.localeCompare(left.right.sourceBoneKey)
  );
}

function candidateAmbiguous(best, second) {
  return (
    !!best &&
    !!second &&
    (best.confidenceClass || 0) === (second.confidenceClass || 0) &&
    (best.propagationScore ?? best.combinedConfidence ?? best.score) -
      (second.propagationScore ?? second.combinedConfidence ?? second.score) <
      CROSS_SOURCE_AMBIGUITY_MARGIN
  );
}

function endpointDescriptor(candidate, endpoint) {
  return candidate.left.sourceBoneKey === endpoint
    ? { otherSource: candidate.right.sourceKey, otherKey: candidate.right.sourceBoneKey }
    : { otherSource: candidate.left.sourceKey, otherKey: candidate.left.sourceBoneKey };
}

function endpointCompetitionKey(endpoint, otherSource) {
  return JSON.stringify([endpoint, String(otherSource)]);
}

export class GuardedUnionFind {
  constructor(evidenceByKey) {
    const keys = [...evidenceByKey.keys()];
    this.parent = new Map(keys.map((key) => [key, key]));
    this.sources = new Map(keys.map((key) => [key, new Set([evidenceByKey.get(key).sourceKey])]));
  }

  find(key) {
    const parent = this.parent.get(key);
    if (parent === undefined || parent === key) return parent;
    const root = this.find(parent);
    this.parent.set(key, root);
    return root;
  }

  same(left, right) {
    const leftRoot = this.find(left);
    const rightRoot = this.find(right);
    return leftRoot !== undefined && rightRoot !== undefined && leftRoot === rightRoot;
  }

  hasSourceConflict(left, right) {
    const leftSources = this.sources.get(this.find(left)) || new Set();
    const rightSources = this.sources.get(this.find(right)) || new Set();
    return [...leftSources].some((source) => rightSources.has(source));
  }

  union(left, right) {
    const leftRoot = this.find(left);
    const rightRoot = this.find(right);
    if (!leftRoot || !rightRoot || leftRoot === rightRoot) {
      return { accepted: leftRoot === rightRoot, reason: null };
    }
    const leftSources = this.sources.get(leftRoot) || new Set();
    const rightSources = this.sources.get(rightRoot) || new Set();
    if (this.hasSourceConflict(leftRoot, rightRoot)) {
      return { accepted: false, reason: 'cluster_source_conflict' };
    }
    this.parent.set(rightRoot, leftRoot);
    this.sources.set(leftRoot, new Set([...leftSources, ...rightSources]));
    this.sources.delete(rightRoot);
    return { accepted: true, reason: null };
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

export function buildModelWideBoneIdentity(evidenceByKey) {
  const unionFind = new GuardedUnionFind(evidenceByKey);
  const byBoneId = new Map();
  evidenceByKey.forEach((evidence, sourceBoneKey) => {
    const boneId = Number(evidence.boneId);
    const members = byBoneId.get(boneId) || [];
    members.push(sourceBoneKey);
    byBoneId.set(boneId, members);
  });
  const sortedBuckets = [...byBoneId.entries()].sort((left, right) => left[0] - right[0]);
  const clusterOrder = new Map();
  sortedBuckets.forEach((bucket, order) => {
    const members = bucket[1];
    members.sort((left, right) => left.localeCompare(right));
    clusterOrder.set(members[0], order);
    for (let index = 1; index < members.length; index += 1) {
      unionFind.union(members[0], members[index]);
    }
  });
  return {
    unionFind,
    clusterOrder,
    uniqueModelBoneIdCount: sortedBuckets.length,
  };
}

function neighborMatches(candidate, evidenceByKey, unionFind) {
  const left = evidenceByKey.get(candidate.left.sourceBoneKey);
  const right = evidenceByKey.get(candidate.right.sourceBoneKey);
  if (!left || !right) {
    return { matchedNeighborCount: 0, matchedNeighborPairs: [] };
  }
  const leftNeighbors = left.neighborBoneIds || [];
  const rightNeighbors = right.neighborBoneIds || [];
  const leftNeighborKeys =
    left._neighborBoneKeys || leftNeighbors.map((boneId) => sourceBoneKey(left.sourceKey, boneId));
  const rightNeighborKeys =
    right._neighborBoneKeys || rightNeighbors.map((boneId) => sourceBoneKey(right.sourceKey, boneId));
  const usedRightNeighbors = new Set();
  const matchedNeighborPairs = [];
  leftNeighbors.forEach((leftNeighborId) => {
    const leftNeighborIndex = leftNeighbors.indexOf(leftNeighborId);
    const rightNeighborIndex = rightNeighborKeys.findIndex(
      (candidateKey, index) =>
        !usedRightNeighbors.has(rightNeighbors[index]) &&
        unionFind.same(leftNeighborKeys[leftNeighborIndex], candidateKey),
    );
    const rightNeighborId = rightNeighborIndex < 0 ? undefined : rightNeighbors[rightNeighborIndex];
    if (rightNeighborId === undefined) return;
    usedRightNeighbors.add(rightNeighborId);
    matchedNeighborPairs.push({ leftBoneId: leftNeighborId, rightBoneId: rightNeighborId });
  });
  return {
    matchedNeighborCount: matchedNeighborPairs.length,
    matchedNeighborPairs,
  };
}

function average(values, fallback = null) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : fallback;
}

function graphAlignmentFeatures(candidate, evidenceByKey, unionFind) {
  const matches = neighborMatches(candidate, evidenceByKey, unionFind);
  const left = evidenceByKey.get(candidate.left.sourceBoneKey);
  const right = evidenceByKey.get(candidate.right.sourceBoneKey);
  const edgeAlignments = [];
  const edgeLengthRatios = [];
  matches.matchedNeighborPairs.forEach((pair) => {
    const leftNeighbor = evidenceByKey.get(sourceBoneKey(left.sourceKey, pair.leftBoneId));
    const rightNeighbor = evidenceByKey.get(sourceBoneKey(right.sourceKey, pair.rightBoneId));
    const leftVector = evidenceVector(leftNeighbor, '_weightedCenterVector', 'weightedCenter')?.sub(
      evidenceVector(left, '_weightedCenterVector', 'weightedCenter'),
    );
    const rightVector = evidenceVector(rightNeighbor, '_weightedCenterVector', 'weightedCenter')?.sub(
      evidenceVector(right, '_weightedCenterVector', 'weightedCenter'),
    );
    if (!leftVector || !rightVector || leftVector.length() <= EPSILON || rightVector.length() <= EPSILON) {
      return;
    }
    const leftLength = leftVector.length();
    const rightLength = rightVector.length();
    edgeAlignments.push(Math.abs(leftVector.normalize().dot(rightVector.normalize())));
    edgeLengthRatios.push(Math.min(leftLength, rightLength) / Math.max(leftLength, rightLength));
  });
  const relativeEdgeAlignment = average(edgeAlignments, 0.5);
  const edgeLengthRatio = average(edgeLengthRatios, 0.5);
  const distanceQuality = clamp(1 - candidate.normalizedDistance / CROSS_SOURCE_CANDIDATE_DISTANCE);
  const crossSignal = candidate.crossQuality === null ? 0.5 : candidate.crossConfidence;
  const graphAlignmentScore = clamp(
    clamp(matches.matchedNeighborCount / 2) * 0.4 +
      crossSignal * 0.15 +
      (candidate.directionAlignment ?? 0.5) * 0.1 +
      relativeEdgeAlignment * 0.15 +
      edgeLengthRatio * 0.1 +
      distanceQuality * 0.05 +
      (candidate.topology?.degree ?? 0.5) * 0.05,
  );
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
    left: { ...candidate.left },
    right: { ...candidate.right },
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
    matchedNeighborPairs: (candidate.matchedNeighborPairs || []).map((pair) => ({ ...pair })),
    relativeEdgeAlignment: candidate.relativeEdgeAlignment,
    edgeLengthRatio: candidate.edgeLengthRatio,
    graphAlignmentScore: candidate.graphAlignmentScore,
    matchedVertexCount: candidate.matchedVertexCount ?? candidate.crossEvidence?.matchedVertexCount ?? 0,
    weightedMatchStrength: candidate.weightedMatchStrength ?? candidate.crossEvidence?.weightedMatchStrength ?? 0,
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

export async function buildCandidates(evidenceByKey, referenceRadius, options, checkpoint) {
  const crossEvidenceByPair = options.crossEvidenceByPair || new Map();
  const bySource = new Map();
  for (const evidence of evidenceByKey.values()) {
    const entries = bySource.get(evidence.sourceKey) || [];
    entries.push(evidence);
    bySource.set(evidence.sourceKey, entries);
  }
  const candidates = [];
  let pairCount = 0;
  const sources = [...bySource.keys()].sort();
  for (let leftIndex = 0; leftIndex < sources.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < sources.length; rightIndex += 1) {
      const leftEntries = bySource.get(sources[leftIndex]);
      const rightEntries = bySource.get(sources[rightIndex]);
      for (const left of leftEntries) {
        for (const right of rightEntries) {
          if ((pairCount++ & 255) === 0) await checkpoint();
          const candidate = candidateFor(left, right, evidenceByKey, crossEvidenceByPair, { referenceRadius });
          if (candidate) candidates.push(candidate);
        }
      }
    }
  }
  return { candidates, crossEvidenceByPair };
}

function rankEndpointCandidates(candidates, compare) {
  const endpointMap = new Map();
  candidates.forEach((candidate) => {
    for (const endpoint of [candidate.left.sourceBoneKey, candidate.right.sourceBoneKey]) {
      const descriptor = endpointDescriptor(candidate, endpoint);
      const key = endpointCompetitionKey(endpoint, descriptor.otherSource);
      const incident = endpointMap.get(key) || [];
      incident.push(candidate);
      endpointMap.set(key, incident);
    }
  });
  endpointMap.forEach((candidatesForEndpoint, key) => {
    const [endpoint] = JSON.parse(key);
    candidatesForEndpoint.sort((left, right) => compare(left, right, endpoint));
  });
  return (candidate) => {
    const leftDescriptor = endpointDescriptor(candidate, candidate.left.sourceBoneKey);
    const rightDescriptor = endpointDescriptor(candidate, candidate.right.sourceBoneKey);
    return {
      left: endpointMap.get(endpointCompetitionKey(candidate.left.sourceBoneKey, leftDescriptor.otherSource)) || [],
      right: endpointMap.get(endpointCompetitionKey(candidate.right.sourceBoneKey, rightDescriptor.otherSource)) || [],
    };
  };
}

function markSpatialRelationships(candidates) {
  const rankFor = rankEndpointCandidates(candidates, (left, right, endpoint) => {
    const leftDescriptor = endpointDescriptor(left, endpoint);
    const rightDescriptor = endpointDescriptor(right, endpoint);
    return (
      (right.confidenceClass || 0) - (left.confidenceClass || 0) ||
      right.combinedConfidence - left.combinedConfidence ||
      right.supportReliability - left.supportReliability ||
      (right.crossQuality || 0) - (left.crossQuality || 0) ||
      right.geometryConfidence - left.geometryConfidence ||
      left.normalizedDistance - right.normalizedDistance ||
      leftDescriptor.otherKey.localeCompare(rightDescriptor.otherKey)
    );
  });
  candidates.forEach((candidate) => {
    const { left: leftCandidates, right: rightCandidates } = rankFor(candidate);
    candidate.mutualBest = leftCandidates[0] === candidate && rightCandidates[0] === candidate;
    candidate.leftAmbiguous = candidateAmbiguous(leftCandidates[0], leftCandidates[1]);
    candidate.rightAmbiguous = candidateAmbiguous(rightCandidates[0], rightCandidates[1]);
  });
}

function acceptedEquivalence(candidate, pass, unionFind, diagnostics, accepted, correspondenceStrength) {
  const left = candidate.left.sourceBoneKey;
  const right = candidate.right.sourceBoneKey;
  if (unionFind.same(left, right)) {
    diagnostics.push(diagnosticCandidate(candidate, 'rejected', 'already_equivalent'));
    return false;
  }
  const union = unionFind.union(left, right);
  if (!union.accepted) {
    diagnostics.push(diagnosticCandidate(candidate, 'rejected', union.reason || 'cluster_source_conflict'));
    return false;
  }
  const record = diagnosticCandidate(candidate, 'accepted', null);
  record.pass = pass;
  accepted.push(record);
  const strength = Math.max(candidate.score || 0, candidate.propagationScore || 0, candidate.graphAlignmentScore || 0);
  correspondenceStrength.set(left, Math.max(correspondenceStrength.get(left) || 0, strength));
  correspondenceStrength.set(right, Math.max(correspondenceStrength.get(right) || 0, strength));
  return true;
}

function compareGraphAlignmentCandidate(left, right) {
  return (
    right.matchedNeighborCount - left.matchedNeighborCount ||
    right.crossConfidence - left.crossConfidence ||
    (right.directionAlignment ?? 0.5) - (left.directionAlignment ?? 0.5) ||
    right.relativeEdgeAlignment - left.relativeEdgeAlignment ||
    right.edgeLengthRatio - left.edgeLengthRatio ||
    right.graphAlignmentScore - left.graphAlignmentScore ||
    right.topology.degree - left.topology.degree ||
    right.geometryConfidence - left.geometryConfidence ||
    left.normalizedDistance - right.normalizedDistance ||
    left.left.sourceBoneKey.localeCompare(right.left.sourceBoneKey) ||
    left.right.sourceBoneKey.localeCompare(right.right.sourceBoneKey)
  );
}

function uniqueGraphWinner(candidate, ranked, tier) {
  if (ranked[0] !== candidate) return false;
  const second = ranked[1];
  if (!second) return true;
  if (tier === 1 && candidate.matchedNeighborCount > second.matchedNeighborCount) return true;
  return candidate.graphAlignmentScore - second.graphAlignmentScore >= CROSS_SOURCE_GRAPH_ALIGNMENT_MARGIN;
}

function graphEvidenceContradicts(candidate) {
  return candidate.crossQuality !== null && candidate.supportReliability >= 0.75 && candidate.crossQuality < 0.35;
}

function unresolvedCandidates(candidates, unionFind) {
  const pending = candidates.filter(
    (candidate) => !unionFind.same(candidate.left.sourceBoneKey, candidate.right.sourceBoneKey),
  );
  // Source conflicts cannot become mergeable in a later round. Keep their
  // competition policy while an admissible pair remains, then stop graph work.
  return pending.some(
    (candidate) => !unionFind.hasSourceConflict(candidate.left.sourceBoneKey, candidate.right.sourceBoneKey),
  )
    ? pending
    : [];
}

async function refreshGraphEvidence(candidates, evidenceByKey, unionFind, checkpoint) {
  for (let index = 0; index < candidates.length; index += 1) {
    if ((index & 63) === 0) await checkpoint();
    Object.assign(candidates[index], graphAlignmentFeatures(candidates[index], evidenceByKey, unionFind));
  }
}

async function runGraphAlignment(
  candidates,
  evidenceByKey,
  unionFind,
  diagnostics,
  accepted,
  correspondenceStrength,
  checkpoint,
) {
  let changed = true;
  while (changed) {
    changed = false;
    const pending = unresolvedCandidates(candidates, unionFind);
    if (!pending.length) break;
    await refreshGraphEvidence(pending, evidenceByKey, unionFind, checkpoint);
    const available = pending.filter(
      (candidate) =>
        !graphEvidenceContradicts(candidate) &&
        candidate.matchedNeighborCount > 0 &&
        candidate.graphAlignmentScore >= CROSS_SOURCE_GRAPH_ALIGNMENT_MIN_SCORE &&
        candidate.topology.degree >= 0.5 &&
        !unionFind.same(candidate.left.sourceBoneKey, candidate.right.sourceBoneKey),
    );
    const rankFor = rankEndpointCandidates(available, compareGraphAlignmentCandidate);
    const selected = available
      .filter((candidate) => {
        const ranked = rankFor(candidate);
        const tier = candidate.matchedNeighborCount >= 2 ? 1 : 2;
        return uniqueGraphWinner(candidate, ranked.left, tier) && uniqueGraphWinner(candidate, ranked.right, tier);
      })
      .sort(compareGraphAlignmentCandidate);
    for (const candidate of selected) {
      const tier = candidate.matchedNeighborCount >= 2 ? 1 : 2;
      if (
        acceptedEquivalence(
          candidate,
          `graph-alignment-${tier}`,
          unionFind,
          diagnostics,
          accepted,
          correspondenceStrength,
        )
      ) {
        changed = true;
      }
    }
  }
}

export async function runEquivalencePasses(candidates, evidenceByKey, unionFind, checkpoint) {
  const accepted = [];
  const diagnostics = [];
  const correspondenceStrength = new Map();
  await checkpoint();
  markSpatialRelationships(candidates);
  candidates.forEach((candidate) => {
    if (!candidate.mutualBest) {
      diagnostics.push(diagnosticCandidate(candidate, 'rejected', 'not_mutual'));
      return;
    }
    if (candidate.leftAmbiguous || candidate.rightAmbiguous) {
      diagnostics.push(diagnosticCandidate(candidate, 'rejected', 'ambiguous'));
      return;
    }
    // A source root is not an identity signal. Strong cross-source evidence
    // may seed a root-to-internal match, while the close geometric lane keeps
    // conservative matching for ordinary source-local matches.
    if (!candidate.strongCrossEvidence && !candidate.geometrySeed) {
      diagnostics.push(
        diagnosticCandidate(
          candidate,
          'rejected',
          candidate.normalizedDistance > CROSS_SOURCE_STRICT_DISTANCE ? 'too_far' : 'insufficient_seed_evidence',
        ),
      );
      return;
    }
    if (candidate.combinedConfidence < 0.7) {
      diagnostics.push(diagnosticCandidate(candidate, 'rejected', 'insufficient_confidence'));
      return;
    }
    acceptedEquivalence(candidate, 'strict', unionFind, diagnostics, accepted, correspondenceStrength);
  });

  let changed = true;
  while (changed) {
    changed = false;
    const pending = unresolvedCandidates(candidates, unionFind);
    if (!pending.length) break;
    await refreshGraphEvidence(pending, evidenceByKey, unionFind, checkpoint);
    const propagation = pending
      .map((candidate) => {
        const score = Math.min(1, candidate.score + Math.min(0.3, candidate.matchedNeighborCount * 0.15));
        return { ...candidate, propagationScore: score };
      })
      .filter((candidate) => {
        if (unionFind.same(candidate.left.sourceBoneKey, candidate.right.sourceBoneKey)) return false;
        if (candidate.normalizedDistance > CROSS_SOURCE_PROPAGATION_DISTANCE) {
          return false;
        }
        return candidate.matchedNeighborCount > 0;
      });
    const rankFor = rankEndpointCandidates(
      propagation,
      (left, right, endpoint) =>
        right.propagationScore - left.propagationScore ||
        (right.confidenceClass || 0) - (left.confidenceClass || 0) ||
        right.combinedConfidence - left.combinedConfidence ||
        right.supportReliability - left.supportReliability ||
        left.normalizedDistance - right.normalizedDistance ||
        endpointDescriptor(left, endpoint).otherKey.localeCompare(endpointDescriptor(right, endpoint).otherKey),
    );
    const selected = propagation
      .filter((candidate) => {
        const { left: leftBest, right: rightBest } = rankFor(candidate);
        const leftAmbiguous = candidateAmbiguous(leftBest[0], leftBest[1]);
        const rightAmbiguous = candidateAmbiguous(rightBest[0], rightBest[1]);
        return (
          leftBest[0] === candidate &&
          rightBest[0] === candidate &&
          !leftAmbiguous &&
          !rightAmbiguous &&
          candidate.propagationScore >= 0.7 &&
          (candidate.directionAlignment === null ||
            candidate.directionAlignment >= 0.55 ||
            candidate.crossScore >= 0.55 ||
            candidate.relativeEdgeAlignment >= 0.6)
        );
      })
      .sort((left, right) => right.propagationScore - left.propagationScore || compareCandidate(right, left));
    selected.forEach((candidate) => {
      if (acceptedEquivalence(candidate, 'propagation', unionFind, diagnostics, accepted, correspondenceStrength))
        changed = true;
    });
  }
  await runGraphAlignment(
    candidates,
    evidenceByKey,
    unionFind,
    diagnostics,
    accepted,
    correspondenceStrength,
    checkpoint,
  );
  return { accepted, diagnostics, correspondenceStrength };
}
