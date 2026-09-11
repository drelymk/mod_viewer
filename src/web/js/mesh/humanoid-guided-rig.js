// Optional semantic guidance for the viewer-owned ModelRig build.
//
// This module only changes the source-local relationship forest when a saved
// Humanoid Control Rig is present.  It does not change influence weights,
// source graphs, or the legacy relationship classifier.

import {
  buildInferredRigForest,
  candidateRelationshipEdges,
} from './weight-rig.js';
import {
  classifyHumanoidPoint,
  HUMANOID_DRIVER_SEGMENTS,
} from './humanoid-rig-binding.js';

const CONFIDENT_LEVELS = new Set(['high', 'medium']);
const CENTRAL_ROLES = new Set(['torso']);
const ADJACENT_PROJECTION_MARGIN = 0.2;

function number(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function vector(value) {
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? value : [value?.x, value?.y, value?.z];
  const result = [0, 1, 2].map(index => Number(values?.[index]));
  return result.every(Number.isFinite) ? result : null;
}

function distance(left, right) {
  const a = vector(left);
  const b = vector(right);
  if (!a || !b) return Infinity;
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

function sourceBoneKey(sourceKey, boneId) {
  return `${String(sourceKey)}#bone=${Number(boneId)}`;
}

const SEGMENTS_BY_ID = new Map(HUMANOID_DRIVER_SEGMENTS.map(segment => [
  segment.id, segment,
]));
const SEGMENT_ORDER = new Map(HUMANOID_DRIVER_SEGMENTS.map((segment, index) => [
  segment.id, index,
]));

function classificationFor(classifications, boneId) {
  return classifications.get(Number(boneId)) || null;
}

function isConfident(classification) {
  return !!classification?.classified
    && CONFIDENT_LEVELS.has(classification.confidence);
}

function adjacentSegments(left, right) {
  const leftSegment = SEGMENTS_BY_ID.get(left?.driverId);
  const rightSegment = SEGMENTS_BY_ID.get(right?.driverId);
  if (!leftSegment || !rightSegment
      || leftSegment.id === rightSegment.id) return false;
  const leftToRight = leftSegment.end === rightSegment.start
    && left.projection >= 1 - ADJACENT_PROJECTION_MARGIN
    && right.projection <= ADJACENT_PROJECTION_MARGIN;
  const rightToLeft = rightSegment.end === leftSegment.start
    && right.projection >= 1 - ADJACENT_PROJECTION_MARGIN
    && left.projection <= ADJACENT_PROJECTION_MARGIN;
  return leftToRight || rightToLeft;
}

function semanticRelationship(left, right) {
  if (!isConfident(left) || !isConfident(right)) {
    return {kind: 'unclassified', score: 0, reject: false};
  }
  if (left.driverId === right.driverId) {
    return {kind: 'same_segment', score: 0.35, reject: false};
  }
  if (adjacentSegments(left, right)) {
    return {kind: 'adjacent_articulation', score: 0.24, reject: false};
  }
  if (left.role && left.role === right.role) {
    return {kind: 'same_region', score: 0.06, reject: false};
  }
  // A confident torso-to-limb or left-to-right correspondence is a strong
  // semantic contradiction.  The edge is rejected only when both endpoints
  // have a confident classification; accessory/unclassified branches keep
  // their ordinary weight-derived score.
  return {kind: 'semantic_mismatch', score: 0, reject: true};
}

function edgeBaseScore(edge) {
  return number(edge?.treeEdgeScore ?? edge?.score
    ?? edge?.containment ?? edge?.jaccard, 0);
}

function guidedEdges(graph, classifications) {
  const metrics = {
    candidateEdges: 0,
    guidedEdges: 0,
    rejectedEdges: 0,
    sameSegmentEdges: 0,
    adjacentArticulationEdges: 0,
    sameRegionEdges: 0,
    semanticMismatchEdges: 0,
  };
  const edges = candidateRelationshipEdges(graph).map(edge => {
    metrics.candidateEdges += 1;
    const left = classificationFor(classifications, edge.boneA);
    const right = classificationFor(classifications, edge.boneB);
    const semantic = semanticRelationship(left, right);
    if (semantic.reject) {
      metrics.rejectedEdges += 1;
      metrics.semanticMismatchEdges += 1;
      return null;
    }
    if (semantic.kind === 'same_segment') metrics.sameSegmentEdges += 1;
    if (semantic.kind === 'adjacent_articulation') {
      metrics.adjacentArticulationEdges += 1;
    }
    if (semantic.kind === 'same_region') metrics.sameRegionEdges += 1;
    metrics.guidedEdges += 1;
    return {
      ...edge,
      treeEdgeScore: edgeBaseScore(edge) + semantic.score,
      humanoidSemantic: {
        kind: semantic.kind,
        leftDriverId: left?.driverId || null,
        rightDriverId: right?.driverId || null,
        leftConfidence: left?.confidence || null,
        rightConfidence: right?.confidence || null,
      },
    };
  }).filter(Boolean);
  return {edges, metrics};
}

function rootOverrideForComponent(component, classifications, controlRig) {
  const entries = (component?.nodeIds || []).map(boneId => ({
    boneId: Number(boneId),
    classification: classificationFor(classifications, boneId),
  })).filter(entry => isConfident(entry.classification));
  if (entries.length < 2) return null;
  const roles = new Set(entries.map(entry => entry.classification.role));
  const central = entries.filter(entry =>
    CENTRAL_ROLES.has(entry.classification.role));
  if (central.length) {
    const pelvis = controlRig?.controls?.pelvis?.position
      || controlRig?.controls?.pelvis;
    return central.sort((left, right) =>
      distance(left.position, pelvis) - distance(right.position, pelvis)
      || left.boneId - right.boneId)[0].boneId;
  }
  if (roles.size !== 1) return null;
  // For an isolated limb source, orient the component from the proximal end
  // of the semantic chain.  This affects only components with at least two
  // confident, same-region points; unrelated accessory components retain the
  // legacy root choice.
  return entries.sort((left, right) =>
    (SEGMENT_ORDER.get(left.classification.driverId)
      ?? HUMANOID_DRIVER_SEGMENTS.length)
      + number(left.classification.projection)
      - ((SEGMENT_ORDER.get(right.classification.driverId)
        ?? HUMANOID_DRIVER_SEGMENTS.length)
        + number(right.classification.projection))
    || left.boneId - right.boneId)[0].boneId;
}

function rootOverridesForForest(forest, classifications, controlRig) {
  const overrides = new Map();
  (forest?.components || []).forEach(component => {
    const root = rootOverrideForComponent(component, classifications, controlRig);
    if (Number.isFinite(root)) overrides.set(Number(component.componentId), root);
  });
  return overrides;
}

export function classifyHumanoidSourceBones(rig, controlRig, options = {}) {
  const byBoneId = new Map();
  const bySourceBoneKey = new Map();
  const counts = {
    total: 0, classified: 0, confident: 0, high: 0, medium: 0,
    low: 0, unclassified: 0,
  };
  (rig?.influenceGraph?.nodes || []).forEach(node => {
    const boneId = Number(node?.boneId);
    if (!Number.isFinite(boneId)) return;
    const classification = classifyHumanoidPoint(
      node.weightedCenter, controlRig, options);
    const entry = {
      boneId, position: vector(node.weightedCenter), ...classification,
    };
    byBoneId.set(boneId, entry);
    bySourceBoneKey.set(sourceBoneKey(rig.sourceKey, boneId), entry);
    counts.total += 1;
    if (classification.classified) counts.classified += 1;
    else counts.unclassified += 1;
    if (classification.confident) counts.confident += 1;
    if (classification.confidence === 'high') counts.high += 1;
    if (classification.confidence === 'medium') counts.medium += 1;
    if (classification.confidence === 'low') counts.low += 1;
  });
  return {byBoneId, bySourceBoneKey, counts};
}

export function buildHumanoidGuidedSourceForest(rig, controlRig, options = {}) {
  if (!rig?.influenceGraph || !controlRig?.accepted) return null;
  const classifications = classifyHumanoidSourceBones(rig, controlRig, options);
  const guided = guidedEdges(rig.influenceGraph, classifications.byBoneId);
  const firstForest = buildInferredRigForest(rig.influenceGraph, {
    edges: guided.edges,
  });
  const rootOverrides = rootOverridesForForest(
    firstForest, classifications.byBoneId, controlRig);
  const forest = rootOverrides.size
    ? buildInferredRigForest(rig.influenceGraph, {
      edges: guided.edges, rootOverrides,
    }) : firstForest;
  return {
    forest,
    classifications,
    diagnostics: {
      ...guided.metrics,
      ...classifications.counts,
      rootOverrideCount: rootOverrides.size,
      componentCount: forest.components.length,
    },
  };
}

export function buildHumanoidJointGuide(sourceRigs, controlRig, options = {}) {
  const sourceResults = new Map();
  const sourceBoneClassifications = new Map();
  const diagnostics = {
    sourceCount: 0,
    total: 0,
    classified: 0,
    confident: 0,
    high: 0,
    medium: 0,
    low: 0,
    unclassified: 0,
    candidateEdges: 0,
    guidedEdges: 0,
    rejectedEdges: 0,
    sameSegmentEdges: 0,
    adjacentArticulationEdges: 0,
    sameRegionEdges: 0,
    semanticMismatchEdges: 0,
    rootOverrideCount: 0,
  };
  for (const rig of sourceRigs || []) {
    const result = buildHumanoidGuidedSourceForest(rig, controlRig, options);
    if (!result) continue;
    const key = String(rig.sourceKey);
    sourceResults.set(key, result);
    result.classifications.bySourceBoneKey.forEach((entry, boneKey) =>
      sourceBoneClassifications.set(boneKey, entry));
    diagnostics.sourceCount += 1;
    Object.keys(result.diagnostics).forEach(field => {
      if (field === 'componentCount') return;
      diagnostics[field] = (diagnostics[field] || 0)
        + (Number(result.diagnostics[field]) || 0);
    });
  }
  return {
    controlRig,
    sourceResults,
    sourceBoneClassifications,
    diagnostics,
  };
}
