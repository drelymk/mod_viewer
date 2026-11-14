import { Vector3 } from 'three';
import {
  HUMANOID_CONTROL_KEYS,
  humanoidControlPositionToSemantic,
  rebuildHumanoidControlPaths,
} from './humanoid-control-rig.js';

/** Use explicit Asset semantics, never bone-name or nearest-point guesses. */
export function buildAuthoredHumanoidControlRig(fittedRig, modelRig) {
  const mapping = modelRig?.authoredHumanoidControls;
  if (!modelRig?.gameRigId || !mapping || !fittedRig?.frame) return null;
  const points = {};
  const used = new Set();
  for (const key of HUMANOID_CONTROL_KEYS) {
    const id = mapping[key];
    const joint = modelRig.joints?.find((item) => item.jointId === id);
    const point = modelRig.authoredRestPivots?.get(id);
    if (!Number.isInteger(id) || used.has(id) || !joint || !point?.every(Number.isFinite)) return null;
    used.add(id);
    points[key] = [...point];
  }
  for (const keys of [
    ['pelvis', 'chest', 'neck', 'head'],
    ['chest', 'leftShoulder', 'leftElbow', 'leftHand'],
    ['chest', 'rightShoulder', 'rightElbow', 'rightHand'],
    ['pelvis', 'leftHip', 'leftKnee', 'leftFoot'],
    ['pelvis', 'rightHip', 'rightKnee', 'rightFoot'],
  ]) {
    for (let i = 1; i < keys.length; i += 1) {
      if (new Vector3(...points[keys[i]]).distanceToSquared(new Vector3(...points[keys[i - 1]])) <= 1e-8) return null;
    }
  }
  const result = JSON.parse(JSON.stringify(fittedRig));
  if (!(result.frame.height > 0)) {
    const up = new Vector3(...(result.frame.up || [0, 1, 0])).normalize();
    const heights = Object.values(points).map((point) => new Vector3(...point).dot(up));
    result.frame.lowHeight = Math.min(...heights);
    result.frame.highHeight = Math.max(...heights);
    result.frame.height = result.frame.highHeight - result.frame.lowHeight;
    result.frame.sideCenter = 0;
    result.frame.depthCenter = 0;
  }
  if (!(result.frame.height > 1e-8)) return null;
  Object.assign(result, {
    source: 'authored_skeleton',
    mode: 'authored_skeleton',
    available: true,
    accepted: true,
    confidence: 'verified',
    confidenceByRegion: Object.fromEntries(
      ['torso', 'head', 'arms', 'legs', 'overall'].map((key) => [key, 'verified']),
    ),
    authoredControlJointIds: { ...mapping },
  });
  result.controls = Object.fromEntries(
    HUMANOID_CONTROL_KEYS.map((key) => [
      key,
      {
        position: points[key],
        semantic: humanoidControlPositionToSemantic(points[key], result),
        source: 'authored_skeleton',
        confidence: 'verified',
        fitted: true,
        support: 0,
      },
    ]),
  );
  result.diagnostics = { reason: null, controlSource: 'authored_skeleton' };
  return rebuildHumanoidControlPaths(result);
}

/** Saved edits take precedence; untouched authored controls retain exact ownership. */
export function mergeAuthoredHumanoidMappings(automaticRig, savedMappings, savedOverrides) {
  const result = new Map(savedMappings);
  result.rejectedControlKeys = savedMappings.rejectedControlKeys || new Set();
  const reserved = new Set([...savedMappings.values()].map((mapping) => mapping.jointId));
  for (const [controlKey, jointId] of Object.entries(automaticRig?.authoredControlJointIds || {})) {
    if (savedOverrides?.controls?.[controlKey] || result.rejectedControlKeys.has(controlKey) || reserved.has(jointId))
      continue;
    result.set(controlKey, { controlKey, jointId, authored: true });
  }
  return result;
}
