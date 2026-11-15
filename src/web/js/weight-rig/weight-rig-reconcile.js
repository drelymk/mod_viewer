// Build the viewer-owned model graph from source-local rigs. Only validated
// bone identities can join sources; geometry never establishes equivalence.

import { createWorkBudget } from './cooperative-scheduler.js';
import {
  EPSILON,
  number,
  clockNow,
  collectSourceBoneEvidence,
  prepareSourceBoneEvidence,
  modelReferenceRadius,
} from './weight-rig-reconcile-evidence.js';
import {
  buildModelJoints,
  sourceModelEdges,
  maximumSpanningForest,
  rootVotes,
  orientModelForest,
} from './weight-rig-reconcile-forest.js';
export { sourceBoneKey } from './weight-rig-reconcile-evidence.js';
export { orientModelRigForest } from './weight-rig-reconcile-forest.js';

export const MODEL_RIG_BUILDER_VERSION = 6;

function boneIdentityGroups(evidenceByKey, modelWide) {
  const groups = new Map();
  evidenceByKey.forEach((evidence, sourceBoneKey) => {
    const identity = modelWide ? evidence.boneId : sourceBoneKey;
    const members = groups.get(identity) || [];
    members.push(sourceBoneKey);
    groups.set(identity, members);
  });
  return [...groups.entries()]
    .sort((left, right) => (modelWide ? left[0] - right[0] : left[0].localeCompare(right[0])))
    .map(([, members]) => members.sort());
}

/** Assemble source-local or validated shared-ID joints with cancellable publication. */
export async function buildModelRigCooperative(
  sourceRigs = [],
  options = {},
  { budget = createWorkBudget(), isCurrent = () => true, timings = null } = {},
) {
  const rigs = [...sourceRigs]
    .filter((rig) => rig?.sourceKey !== undefined)
    .sort((left, right) => String(left.sourceKey).localeCompare(String(right.sourceKey)));
  const modelWide =
    rigs.length > 0 &&
    rigs.every((rig) => rig.boneIdsModelWide === true) &&
    new Set(rigs.map((rig) => rig.boneIdDomain || 'model')).size === 1;
  const stale = Symbol('stale model rig');
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
    await checkpoint();
    const graphStartedAt = clockNow();
    const groups = boneIdentityGroups(evidenceByKey, modelWide);
    const model = buildModelJoints(groups, evidenceByKey, referenceRadius);
    await checkpoint();
    const edges = maximumSpanningForest(model.joints, sourceModelEdges(rigs, model.keyToJoint));
    const forest = orientModelForest(model.joints, edges, rootVotes(rigs, model.keyToJoint));
    await checkpoint();
    const sharedGroups = groups.filter((members) => members.length > 1);
    const result = {
      sourceBindingMode: modelWide ? 'shared' : 'independent',
      modelReferenceRadius: referenceRadius,
      sourceBoneToModelJointMap: model.keyToJoint,
      joints: model.joints,
      edges,
      components: forest.components,
      componentByJointId: forest.componentByJointId,
      reconciliation: {
        identityMode: modelWide ? 'model-wide-bone-id' : 'source-local',
        uniqueModelBoneIdCount: modelWide ? groups.length : null,
        sourceCount: rigs.length,
        sourceBoneCount: evidenceByKey.size,
        modelJointCount: model.joints.length,
        equivalenceClusterCount: sharedGroups.length,
        equivalentSourceBoneCount: sharedGroups.reduce((sum, members) => sum + members.length, 0),
        componentCount: forest.components.length,
        modelReferenceRadius: referenceRadius,
      },
    };
    await checkpoint();
    if (timings) timings.graphBuildMs = clockNow() - graphStartedAt;
    return result;
  } catch (error) {
    if (error === stale) return null;
    throw error;
  }
}
