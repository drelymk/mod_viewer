// ModelJoint-native character limb IK. Detection may retain helper joints in
// a path, but the solver is deliberately limited to an anchor and bend.

import * as THREE from 'three';
import {buildForestTransformsFromLocalRotations} from './weight-deformation.js';

const EPSILON = 1e-8;
const MAX_PATH_DEPTH = 64;
const MAX_SOLVE_PASSES = 2;
const TOLERANCE_SCALE = 1e-4;
export const LIMB_BEND_RATIO_THRESHOLD = 0.025;

export const RIG_LIMB_ROLES = Object.freeze([
  'left_arm', 'right_arm', 'left_leg', 'right_leg',
]);

export const RIG_LIMB_ROLE_INFO = Object.freeze({
  left_arm: Object.freeze({label: 'Left Arm', anchor: 'Shoulder', bend: 'Elbow', end: 'Hand'}),
  right_arm: Object.freeze({label: 'Right Arm', anchor: 'Shoulder', bend: 'Elbow', end: 'Hand'}),
  left_leg: Object.freeze({label: 'Left Leg', anchor: 'Hip', bend: 'Knee', end: 'Foot'}),
  right_leg: Object.freeze({label: 'Right Leg', anchor: 'Hip', bend: 'Knee', end: 'Foot'}),
});

function valueFor(collection, id) {
  if (collection instanceof Map) {
    return collection.get(id) ?? collection.get(String(id));
  }
  return collection?.[id] ?? collection?.[String(id)];
}

function numberId(value) {
  if (value === null || value === undefined || value === '') return null;
  const id = Number(value);
  return Number.isInteger(id) ? id : null;
}

function finiteVector(value) {
  if (value?.isVector3) {
    const result = value.clone();
    return result.toArray().every(Number.isFinite) ? result : null;
  }
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 3).map(Number)
    : [value?.x, value?.y, value?.z].map(Number);
  if (values.length !== 3 || !values.every(Number.isFinite)) return null;
  return new THREE.Vector3(...values);
}

function quaternionFrom(value) {
  if (value?.isQuaternion) {
    const result = value.clone();
    return Number.isFinite(result.lengthSq()) && result.lengthSq() > EPSILON
      ? result.normalize() : new THREE.Quaternion();
  }
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 4).map(Number)
    : [value?.x, value?.y, value?.z, value?.w].map(Number);
  if (values.length !== 4 || !values.every(Number.isFinite)) {
    return new THREE.Quaternion();
  }
  const result = new THREE.Quaternion(...values);
  return Number.isFinite(result.lengthSq()) && result.lengthSq() > EPSILON
    ? result.normalize() : new THREE.Quaternion();
}

function parentFor(component, id) {
  const parent = valueFor(component?.parentById, id);
  if (parent === null || parent === undefined || parent === '') return null;
  return numberId(parent);
}

function childrenFor(component, id) {
  return (valueFor(component?.childrenById, id) || [])
    .map(numberId).filter(Number.isInteger);
}

function componentFor(rig, id) {
  const componentId = valueFor(rig?.componentByJointId, id)
    ?? valueFor(rig?.inferredForest?.componentByBoneId, id);
  const components = rig?.components || rig?.inferredForest?.components || [];
  return Number.isInteger(Number(componentId))
    ? components[Number(componentId)] || null : null;
}

function jointFor(rig, id) {
  return (rig?.joints || []).find(joint => Number(joint?.jointId) === id)
    || null;
}

function pointFor(rig, id) {
  return finiteVector(valueFor(rig?.jointPivotByJointId, id))
    || finiteVector(jointFor(rig, id)?.restPivot)
    || finiteVector(valueFor(rig?.centerByJointId, id))
    || finiteVector(jointFor(rig, id)?.restCenter);
}

function edgeFor(component, parentId, childId) {
  return (component?.edges || []).find(edge => {
    const left = Number(edge?.jointA ?? edge?.boneA);
    const right = Number(edge?.jointB ?? edge?.boneB);
    return (left === parentId && right === childId)
      || (left === childId && right === parentId);
  }) || null;
}

function edgeSupport(component, parentId, childId) {
  const edge = edgeFor(component, parentId, childId);
  const score = Number(edge?.combinedTreeScore ?? edge?.treeEdgeScore
    ?? edge?.score ?? edge?.attachmentScore ?? edge?.jaccard ?? 0);
  return Number.isFinite(score) ? score : 0;
}

function continuationFor(rig, id) {
  return numberId(valueFor(
    rig?.restContinuationChildByJointId
      || rig?.continuationChildByJointId,
    id));
}

function restFrameFor(rig, id) {
  const value = valueFor(rig?.restFrameByJointId, id)
    || jointFor(rig, id)?.restFrame;
  return quaternionFrom(value);
}

function projectedDirection(vector, axis) {
  const result = vector.clone().addScaledVector(axis, -vector.dot(axis));
  return result.lengthSq() > EPSILON ? result.normalize() : null;
}

export function resolveLimbBendDirection({
  rig, pathJointIds = [], anchorJointId, bendJointId, endJointId, role,
  characterForward,
} = {}) {
  const path = pathJointIds.map(numberId).filter(Number.isInteger);
  const anchorId = numberId(anchorJointId ?? path[0]);
  const endId = numberId(endJointId ?? path.at(-1));
  const bendId = numberId(bendJointId);
  const anchor = pointFor(rig, anchorId);
  const bend = pointFor(rig, bendId);
  const end = pointFor(rig, endId);
  if (!anchor || !bend || !end) return {
    bendDirection: null,
    bendDirectionSource: 'unavailable',
    bendDirectionStrength: 0,
  };
  const axis = end.clone().sub(anchor);
  if (axis.lengthSq() > EPSILON) {
    const limbLength = axis.length();
    axis.normalize();
    const bendOffset = bend.clone().sub(anchor)
      .addScaledVector(axis, -bend.clone().sub(anchor).dot(axis));
    const bendOffsetLength = bendOffset.length();
    const bendRatio = limbLength > EPSILON
      ? bendOffsetLength / limbLength : 0;
    if (bendRatio >= LIMB_BEND_RATIO_THRESHOLD) {
      return {
        bendDirection: bendOffset.normalize(),
        bendDirectionSource: 'rest-offset',
        bendDirectionStrength: bendRatio,
      };
    }

    const semantic = finiteVector(characterForward);
    if (semantic && semantic.lengthSq() > EPSILON) {
      const direction = semantic.normalize();
      if (role?.endsWith('_leg')) direction.negate();
      const projected = projectedDirection(direction, axis);
      if (projected) return {
        bendDirection: projected,
        bendDirectionSource: 'semantic-character-facing',
        bendDirectionStrength: bendRatio,
      };
    }

    const frame = restFrameFor(rig, bendId);
    const candidates = [
      new THREE.Vector3(1, 0, 0).applyQuaternion(frame),
      new THREE.Vector3(0, 0, 1).applyQuaternion(frame),
      new THREE.Vector3(0, 1, 0),
    ];
    const fallback = candidates.map(candidate =>
      projectedDirection(candidate, axis)).find(Boolean);
    if (fallback) return {
      bendDirection: fallback,
      bendDirectionSource: 'rest-frame',
      bendDirectionStrength: bendRatio,
    };

    return {
      bendDirection: null,
      bendDirectionSource: 'ambiguous-straight',
      bendDirectionStrength: bendRatio,
    };
  }

  const frame = restFrameFor(rig, bendId);
  const candidates = [
    new THREE.Vector3(1, 0, 0).applyQuaternion(frame),
    new THREE.Vector3(0, 0, 1).applyQuaternion(frame),
    new THREE.Vector3(0, 1, 0),
  ];
  const fallbackAxis = end.clone().sub(anchor);
  if (fallbackAxis.lengthSq() <= EPSILON) fallbackAxis.set(0, 1, 0);
  fallbackAxis.normalize();
  const direction = candidates.map(candidate =>
    projectedDirection(candidate, fallbackAxis)).find(Boolean)
    || new THREE.Vector3(1, 0, 0);
  return {
    bendDirection: direction,
    bendDirectionSource: 'rest-frame',
    bendDirectionStrength: 0,
  };
}

function confidenceFor(path, reason, branchHub) {
  if (path.length < 3) return 'low';
  if (branchHub && path.length >= 4) return 'high';
  if (path.length >= 4 && !reason) return 'high';
  return 'medium';
}

export function selectLimbBendJoint(rig, pathJointIds = []) {
  if (pathJointIds.length < 3) return null;
  const distances = [0];
  for (let index = 1; index < pathJointIds.length; index += 1) {
    const first = pointFor(rig, pathJointIds[index - 1]);
    const second = pointFor(rig, pathJointIds[index]);
    if (!first || !second) return null;
    distances.push(distances.at(-1) + first.distanceTo(second));
  }
  const total = distances.at(-1);
  if (!Number.isFinite(total) || total <= EPSILON) return null;
  const middle = total / 2;
  let best = null;
  for (let index = 1; index < pathJointIds.length - 1; index += 1) {
    const candidate = {
      jointId: pathJointIds[index],
      distanceFromMiddle: Math.abs(distances[index] - middle),
      edgePenalty: Math.min(distances[index], total - distances[index]),
      support: edgeSupport(componentFor(rig, pathJointIds[index]),
        pathJointIds[index - 1], pathJointIds[index]),
    };
    const closer = !best || candidate.distanceFromMiddle
      < best.distanceFromMiddle - EPSILON;
    const tie = best && Math.abs(candidate.distanceFromMiddle
      - best.distanceFromMiddle) <= EPSILON;
    if (closer || tie && candidate.edgePenalty > best.edgePenalty + EPSILON
        || tie && Math.abs(candidate.edgePenalty - best.edgePenalty) <= EPSILON
          && candidate.support > best.support + EPSILON
        || tie && Math.abs(candidate.edgePenalty - best.edgePenalty) <= EPSILON
          && Math.abs(candidate.support - best.support) <= EPSILON
          && candidate.jointId < best.jointId) best = candidate;
  }
  return best?.jointId ?? null;
}

function detectionResult(role, anchorJointId, pathJointIds, reason,
    branchHub, rig, characterForward) {
  const bendJointId = selectLimbBendJoint(rig, pathJointIds);
  const endJointId = pathJointIds.at(-1) ?? null;
  const component = componentFor(rig, anchorJointId);
  const available = pathJointIds.length >= 3
    && Number.isInteger(bendJointId)
    && Number.isInteger(endJointId)
    && bendJointId !== anchorJointId
    && endJointId !== anchorJointId
    && Number(component?.rootId) !== anchorJointId;
  const finalReason = available ? null : reason || 'short_or_invalid_limb_path';
  const directionEvidence = available
    ? resolveLimbBendDirection({
      rig, pathJointIds, anchorJointId, bendJointId, endJointId, role,
      characterForward,
    })
    : {bendDirection: null, bendDirectionSource: 'unavailable',
      bendDirectionStrength: 0};
  return {
    available,
    role: RIG_LIMB_ROLES.includes(role) ? role : null,
    anchorJointId,
    pathJointIds: [...pathJointIds],
    bendJointId: available ? bendJointId : null,
    endJointId: available ? endJointId : null,
    confidence: available ? confidenceFor(pathJointIds, reason, branchHub) : 'low',
    reason: finalReason,
    bendDirection: directionEvidence.bendDirection?.toArray() || null,
    bendDirectionSource: directionEvidence.bendDirectionSource,
    bendDirectionStrength: directionEvidence.bendDirectionStrength,
  };
}

/** Detect one primary descendant limb path without searching sideways. */
export function detectLimbPath({rig, anchorJointId, role, characterForward} = {}) {
  const anchor = numberId(anchorJointId);
  const component = componentFor(rig, anchor);
  if (!component || anchor === null
      || !component.nodeIds?.map(Number).includes(anchor)) {
    return detectionResult(role, anchor, [], 'anchor_not_found', false, rig,
      characterForward);
  }
  if (!pointFor(rig, anchor)) {
    return detectionResult(role, anchor, [anchor], 'anchor_pivot_invalid', false,
      rig, characterForward);
  }
  if (Number(component.rootId) === anchor) {
    return detectionResult(role, anchor, [anchor], 'anchor_is_component_root',
      false, rig, characterForward);
  }

  const path = [anchor];
  const visited = new Set(path);
  let reason = null;
  let branchHub = false;
  for (let depth = 0; depth < MAX_PATH_DEPTH; depth += 1) {
    const current = path.at(-1);
    const children = childrenFor(component, current)
      .filter(childId => !visited.has(childId) && pointFor(rig, childId));
    const continuation = continuationFor(rig, current);
    const branchChildren = children.filter(childId => childId !== continuation);
    // Once two useful limb sections exist, a multi-child node is most likely
    // a hand/foot hub. Do not follow one arbitrary finger or toe.
    // A single accessory child is not enough: preserve the continuation and
    // let the existing rest-frame ranking decide which child to follow.
    if (path.length >= 3
        && (branchChildren.length >= 2
          || continuation === null && children.length >= 2)) {
      branchHub = true;
      reason = 'terminal_branch_hub';
      break;
    }
    const next = continuation;
    if (next === null || !children.includes(next)) {
      reason = children.length ? 'continuation_missing' : 'leaf';
      break;
    }
    if (visited.has(next)) {
      reason = 'cycle';
      break;
    }
    const previous = pointFor(rig, path.length > 1 ? path.at(-2) : current);
    const currentPoint = pointFor(rig, current);
    const nextPoint = pointFor(rig, next);
    if (!previous || !currentPoint || !nextPoint) {
      reason = 'pivot_invalid';
      break;
    }
    if (path.length >= 4) {
      const incoming = currentPoint.clone().sub(previous).normalize();
      const outgoing = nextPoint.clone().sub(currentPoint).normalize();
      if (incoming.lengthSq() > EPSILON && outgoing.lengthSq() > EPSILON
          && incoming.dot(outgoing) < 0.1) {
        reason = 'direction_break';
        break;
      }
    }
    visited.add(next);
    path.push(next);
  }
  if (path.length >= MAX_PATH_DEPTH && !reason) reason = 'safety_depth';
  return detectionResult(role, anchor, path, reason, branchHub, rig,
    characterForward);
}

function cloneRotations(localRotations) {
  const result = new Map();
  if (localRotations instanceof Map) {
    localRotations.forEach((rotation, id) => {
      const jointId = numberId(id);
      if (jointId !== null) result.set(jointId, quaternionFrom(rotation));
    });
  } else Object.entries(localRotations || {}).forEach(([id, rotation]) => {
    const jointId = numberId(id);
    if (jointId !== null) result.set(jointId, quaternionFrom(rotation));
  });
  return result;
}

export function characterForwardFromOrientation({orientation, userRotation} = {}) {
  if (!orientation || !userRotation) return null;
  const baseOrientation = quaternionFrom(userRotation).invert()
    .multiply(quaternionFrom(orientation)).normalize();
  const forward = new THREE.Vector3(0, 0, 1)
    .applyQuaternion(baseOrientation.clone().invert());
  if (!Number.isFinite(forward.lengthSq()) || forward.lengthSq() <= EPSILON) {
    return null;
  }
  return forward.normalize();
}

function buildEvaluation({forest, centers, pivots, rotations}) {
  const rotationOutput = new Map();
  const transforms = buildForestTransformsFromLocalRotations(
    forest, centers, {
      getQuaternion: id => rotations.get(Number(id)) || new THREE.Quaternion(),
      jointPivotByBoneId: pivots,
      rotationOutput,
      transformCache: new Map(),
    });
  return {transforms, rotations: rotationOutput};
}

function pointAt(id, transforms, centers, pivots) {
  const point = finiteVector(valueFor(pivots, id))
    || finiteVector(valueFor(centers, id));
  if (!point) return null;
  const transform = transforms.get(id);
  return transform?.isMatrix4 ? point.applyMatrix4(transform) : point;
}

function parentAcrossForest(forest, id) {
  const component = (forest?.components || []).find(item =>
    item?.nodeIds?.map(Number).includes(id));
  return parentFor(component, id);
}

function shortestArc(from, to) {
  if (from.lengthSq() <= EPSILON || to.lengthSq() <= EPSILON) {
    return new THREE.Quaternion();
  }
  const source = from.clone().normalize();
  const target = to.clone().normalize();
  const dot = THREE.MathUtils.clamp(source.dot(target), -1, 1);
  if (dot > 1 - EPSILON) return new THREE.Quaternion();
  if (dot < -1 + EPSILON) {
    const axis = Math.abs(source.x) < Math.abs(source.y)
      ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    axis.cross(source).normalize();
    return new THREE.Quaternion().setFromAxisAngle(axis, Math.PI);
  }
  return new THREE.Quaternion().setFromUnitVectors(source, target).normalize();
}

function applyWorldDelta({jointId, delta, forest, evaluation, working}) {
  const parentId = parentAcrossForest(forest, jointId);
  const parentRotation = parentId === null
    ? new THREE.Quaternion()
    : evaluation.rotations.get(parentId)?.clone() || new THREE.Quaternion();
  const localDelta = parentRotation.clone().invert()
    .multiply(delta).multiply(parentRotation).normalize();
  const next = localDelta.multiply(
    working.get(jointId)?.clone() || new THREE.Quaternion()).normalize();
  if (![next.x, next.y, next.z, next.w].every(Number.isFinite)) return false;
  working.set(jointId, next);
  return true;
}

function preferredDirectionFromInput(value) {
  const direction = finiteVector(value);
  return direction && direction.lengthSq() > EPSILON ? direction.normalize() : null;
}

function solvePass({forest, centers, pivots, working, anchorJointId,
    bendJointId, endJointId, targetPoint, bendDirection}) {
  let evaluation = buildEvaluation({forest, centers, pivots, rotations: working});
  const anchor = pointAt(anchorJointId, evaluation.transforms, centers, pivots);
  let bend = pointAt(bendJointId, evaluation.transforms, centers, pivots);
  let end = pointAt(endJointId, evaluation.transforms, centers, pivots);
  if (!anchor || !bend || !end) {
    return {evaluation, residual: Infinity, limbScale: 0};
  }

  const upper = bend.clone().sub(anchor);
  const lower = end.clone().sub(bend);
  const l1 = upper.length();
  const l2 = lower.length();
  const limbScale = l1 + l2;
  if (l1 <= EPSILON || l2 <= EPSILON) return {
    evaluation, residual: end.distanceTo(targetPoint), limbScale,
  };
  const targetVector = targetPoint.clone().sub(anchor);
  const distance = targetVector.length();
  if (distance <= EPSILON) return {
    evaluation, residual: end.distanceTo(targetPoint), limbScale,
  };
  const direction = targetVector.multiplyScalar(1 / distance);
  const clampedDistance = THREE.MathUtils.clamp(
    distance, Math.abs(l1 - l2) + EPSILON, l1 + l2 - EPSILON);
  const x = (l1 * l1 - l2 * l2 + clampedDistance * clampedDistance)
    / (2 * clampedDistance);
  const h = Math.sqrt(Math.max(l1 * l1 - x * x, 0));
  let plane = preferredDirectionFromInput(bendDirection);
  plane = plane ? projectedDirection(plane, direction) : null;
  if (!plane) plane = projectedDirection(upper, direction);
  if (!plane) {
    const fallback = Math.abs(direction.x) < 0.8
      ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    plane = projectedDirection(fallback, direction)
      || new THREE.Vector3(0, 0, 1);
  }
  const desiredBend = anchor.clone()
    .addScaledVector(direction, x).addScaledVector(plane, h);

  applyWorldDelta({jointId: anchorJointId,
    delta: shortestArc(upper, desiredBend.clone().sub(anchor)),
    forest, evaluation, working});
  evaluation = buildEvaluation({forest, centers, pivots, rotations: working});
  bend = pointAt(bendJointId, evaluation.transforms, centers, pivots);
  end = pointAt(endJointId, evaluation.transforms, centers, pivots);
  if (!bend || !end) return {evaluation, residual: Infinity, limbScale};
  applyWorldDelta({jointId: bendJointId,
    delta: shortestArc(end.clone().sub(bend), targetPoint.clone().sub(bend)),
    forest, evaluation, working});
  evaluation = buildEvaluation({forest, centers, pivots, rotations: working});
  end = pointAt(endJointId, evaluation.transforms, centers, pivots);
  return {
    evaluation,
    residual: end ? end.distanceTo(targetPoint) : Infinity,
    limbScale,
  };
}

/** Solve a detected limb while changing only its anchor and bend controls. */
export function solveLimbIk({forest, centers, jointPivots, localRotations,
    anchorJointId, bendJointId, endJointId, pathJointIds, target,
    bendDirection, bendSign = 1} = {}) {
  const anchor = numberId(anchorJointId);
  const bend = numberId(bendJointId);
  const end = numberId(endJointId);
  const path = (pathJointIds || []).map(numberId).filter(Number.isInteger);
  const targetPoint = finiteVector(target);
  const component = (forest?.components || []).find(item =>
    item?.nodeIds?.map(Number).includes(anchor));
  if (!forest || !targetPoint || anchor === null || bend === null || end === null
      || !component || Number(component.rootId) === anchor
      || path.length < 3 || path[0] !== anchor || path.at(-1) !== end
      || !path.includes(bend)) {
    return {rotations: new Map(), iterations: 0, residual: Infinity, reached: false};
  }
  const working = cloneRotations(localRotations);
  const original = cloneRotations(localRotations);
  const direction = preferredDirectionFromInput(bendDirection);
  if (direction && Number(bendSign) < 0) direction.negate();
  let result = solvePass({forest, centers, pivots: jointPivots,
    working, anchorJointId: anchor, bendJointId: bend, endJointId: end,
    targetPoint, bendDirection: direction});
  const tolerance = Math.max(Number(result.limbScale) || 0, 1e-6)
    * TOLERANCE_SCALE;
  let iterations = 1;
  while (iterations < MAX_SOLVE_PASSES && result.residual > tolerance) {
    result = solvePass({forest, centers, pivots: jointPivots,
      working, anchorJointId: anchor, bendJointId: bend, endJointId: end,
      targetPoint, bendDirection: direction});
    iterations += 1;
  }
  const rotations = new Map();
  for (const jointId of [anchor, bend]) {
    const before = original.get(jointId) || new THREE.Quaternion();
    const after = working.get(jointId) || new THREE.Quaternion();
    if (Math.abs(Math.abs(before.dot(after)) - 1) > 1e-7) {
      rotations.set(jointId, after.clone().normalize());
    }
  }
  const residual = Number.isFinite(result.residual) ? result.residual : Infinity;
  return {rotations, iterations, residual,
    reached: residual <= tolerance};
}
