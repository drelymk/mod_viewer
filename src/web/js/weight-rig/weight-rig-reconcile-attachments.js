// Cross-component attachment evidence, ranking and cycle-free decisions.
import { Quaternion, Vector3 } from 'three';
import { EPSILON, crossPairKey, vectorFrom, quaternionArray, clamp } from './weight-rig-reconcile-evidence.js';
import { CROSS_SOURCE_CANDIDATE_DISTANCE } from './weight-rig-reconcile-matching.js';
import { unorderedPairKey, componentSupport, compareForestEdges } from './weight-rig-reconcile-forest.js';

export const CROSS_SOURCE_ATTACHMENT_AMBIGUITY_MARGIN = 0.08;

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

export async function addAttachments(joints, sourceEdges, forest, referenceRadius, crossEvidenceByPair, checkpoint) {
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
