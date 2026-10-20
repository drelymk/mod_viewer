// Pure, conservative reconciliation of source-local inferred rigs. The
// authored skinning streams remain source-scoped; this module only builds a
// viewer-owned model graph from neutral geometry and source topology.

import { createWorkBudget } from './cooperative-scheduler.js';
import {
  EPSILON,
  number,
  clockNow,
  collectSourceBoneEvidence,
  prepareSourceBoneEvidence,
  modelReferenceRadius,
  buildCrossSourceWeightEvidenceCooperative,
} from './weight-rig-reconcile-evidence.js';
import {
  GuardedUnionFind,
  buildModelWideBoneIdentity,
  buildCandidates,
  runEquivalencePasses,
} from './weight-rig-reconcile-matching.js';
import {
  buildModelJoints,
  sourceModelEdges,
  maximumSpanningForest,
  rootVotes,
  orientModelForest,
  orientModelForestWithAttachments,
  componentSupport,
} from './weight-rig-reconcile-forest.js';
import { addAttachments } from './weight-rig-reconcile-attachments.js';
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
export { orientModelRigForest } from './weight-rig-reconcile-forest.js';
export { CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN } from './weight-rig-reconcile-attachments.js';

export const MODEL_RIG_BUILDER_VERSION = 4;

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
