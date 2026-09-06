// Runtime-owned Rig state and topology utilities.

import {normalizeSelectedBoneIds} from './weight-selection.js';

export const RIG_ROTATION_SNAP_DEGREES = Object.freeze([0, 5, 15, 30]);

export function createRigRuntimeState() {
  return {
    modelRigState: {
      loaded: false,
      loading: false,
      promise: null,
      error: null,
      visible: false,
      picking: false,
      activeSourceKey: null,
      selectedBoneBySource: new Map(),
      selectedJointId: null,
      structureRevision: 0,
      pickedPoint: null,
      pickStatus: '',
      rigAnalysisMs: 0,
      rigTransformMs: 0,
      rigDeformMs: 0,
      rigDeformedVertexCount: 0,
      rigReconcileMs: 0,
      rigCandidateCount: 0,
      rigEquivalentClusterCount: 0,
      rigAttachmentCount: 0,
      rigAmbiguousCount: 0,
      rotationSnapDegrees: 0,
      overlayScope: 'selection',
      explicitRootSignatures: new Set(),
    },
    rigPresetState: {
      loaded: false,
      loading: false,
      error: null,
      presets: [],
      builtInPresets: [],
      selectedPresetId: null,
      lastApplyResult: null,
    },
    proceduralPoseCache: null,
    structureRevision: 0,
  };
}

function attachmentRelationshipSort(a, b) {
  return (Number(b.minOverlap) || 0) - (Number(a.minOverlap) || 0)
    || (Number(b.sharedVertexCount) || 0)
      - (Number(a.sharedVertexCount) || 0)
    || (Number(b.containment) || 0) - (Number(a.containment) || 0)
    || (Number(b.jaccard) || 0) - (Number(a.jaccard) || 0)
    || (Number(a.normalizedDistance ?? Infinity)
      - Number(b.normalizedDistance ?? Infinity))
    || Number(a.boneA) - Number(b.boneA)
    || Number(a.boneB) - Number(b.boneB);
}

export function selectAttachmentRelationship(relationships) {
  return [...relationships || []].sort(attachmentRelationshipSort)[0] || null;
}

function physicsTreeSideForEdge(edges, startId, skippedEdge) {
  const adjacency = new Map();
  for (const edge of edges || []) {
    if (edge === skippedEdge) continue;
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!Number.isFinite(boneA) || !Number.isFinite(boneB)) continue;
    if (!adjacency.has(boneA)) adjacency.set(boneA, []);
    if (!adjacency.has(boneB)) adjacency.set(boneB, []);
    adjacency.get(boneA).push(boneB);
    adjacency.get(boneB).push(boneA);
  }
  const side = new Set([startId]);
  const pending = [startId];
  while (pending.length) {
    const boneId = pending.pop();
    for (const neighbor of adjacency.get(boneId) || []) {
      if (side.has(neighbor)) continue;
      side.add(neighbor);
      pending.push(neighbor);
    }
  }
  return side;
}

function physicsBestStaticAttachment(side, relationships, selected) {
  return selectAttachmentRelationship((relationships || []).filter(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    const leftInside = side.has(boneA);
    const rightInside = side.has(boneB);
    if (leftInside === rightInside) return false;
    const outside = leftInside ? boneB : boneA;
    return !selected.has(outside);
  }));
}

/** Cut only tree bridges whose two sides have stronger static attachments. */
export function pruneSelectedRelationshipEdges(
    treeEdges, relationships = [], selectedBoneIds = []) {
  const edges = [...treeEdges || []];
  const selected = new Set(normalizeSelectedBoneIds(selectedBoneIds));
  if (!selected.size) {
    edges.forEach(edge => {
      selected.add(Number(edge.boneA));
      selected.add(Number(edge.boneB));
    });
  }
  return edges.filter(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    const left = physicsTreeSideForEdge(edges, boneA, edge);
    const right = physicsTreeSideForEdge(edges, boneB, edge);
    const leftAttachment = physicsBestStaticAttachment(
      left, relationships, selected);
    const rightAttachment = physicsBestStaticAttachment(
      right, relationships, selected);
    const bridgeOverlap = Number(edge.minOverlap) || 0;
    return !(leftAttachment && rightAttachment
      && Number(leftAttachment.minOverlap) > bridgeOverlap
      && Number(rightAttachment.minOverlap) > bridgeOverlap);
  });
}

export function aggregateModelBoneStats(nodeLists) {
  const totals = new Map();
  for (const nodes of nodeLists || []) {
    for (const node of nodes || []) {
      const boneId = Number(node?.boneId);
      const affectedVertexCount = Number(node?.affectedVertexCount);
      const totalWeight = Number(node?.totalWeight);
      if (!Number.isFinite(boneId) || !Number.isFinite(affectedVertexCount)
          || affectedVertexCount < 0 || !Number.isFinite(totalWeight)) {
        continue;
      }
      const entry = totals.get(boneId) || {
        affectedVertexCount: 0,
        totalWeight: 0,
      };
      entry.affectedVertexCount += affectedVertexCount;
      entry.totalWeight += totalWeight;
      totals.set(boneId, entry);
    }
  }
  return Object.fromEntries([...totals.entries()]
    .sort(([left], [right]) => Number(left) - Number(right))
    .map(([boneId, entry]) => [boneId, {
      affectedVertexCount: entry.affectedVertexCount,
      averageInfluence: entry.affectedVertexCount > 0
        ? entry.totalWeight / entry.affectedVertexCount : 0,
    }]));
}
