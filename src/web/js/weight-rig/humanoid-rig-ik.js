import * as THREE from 'three';

export const HUMANOID_LIMB_CONTROLS = Object.freeze({
  left_arm: Object.freeze(['leftShoulder', 'leftElbow', 'leftHand']),
  right_arm: Object.freeze(['rightShoulder', 'rightElbow', 'rightHand']),
  left_leg: Object.freeze(['leftHip', 'leftKnee', 'leftFoot']),
  right_leg: Object.freeze(['rightHip', 'rightKnee', 'rightFoot']),
});

/** Keep solved controls for the active limb while retaining other limb poses. */
export function mergeHumanoidLimbPose(previousPose, solvedPositions, keys) {
  const next = { ...(previousPose || {}) };
  (keys || []).slice(1).forEach((key) => {
    if (key && solvedPositions?.[key] !== undefined) {
      next[key] = Array.isArray(solvedPositions[key]) ? solvedPositions[key].slice(0, 3) : solvedPositions[key];
    }
  });
  return next;
}

const EPSILON = 1e-8;

function vector(value) {
  if (value?.isVector3) return value.clone();
  const source = value?.position ?? value;
  return new THREE.Vector3(
    Number(source?.[0] ?? source?.x) || 0,
    Number(source?.[1] ?? source?.y) || 0,
    Number(source?.[2] ?? source?.z) || 0,
  );
}

function controlsFor(controlRig, posedControls) {
  const result = {};
  Object.keys(controlRig?.controls || {}).forEach((key) => {
    result[key] = vector(posedControls?.[key] ?? controlRig.controls[key]);
  });
  return result;
}

function projectedDirection(value, direction) {
  const result = vector(value).addScaledVector(direction, -vector(value).dot(direction));
  return result.lengthSq() > EPSILON ? result.normalize() : null;
}

function fallbackPlane(direction, frame) {
  const preferred = projectedDirection(frame?.forward, direction) || projectedDirection(frame?.right, direction);
  if (preferred) return preferred;
  const fallback = Math.abs(direction.x) < 0.8 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
  return projectedDirection(fallback, direction) || new THREE.Vector3(0, 0, 1);
}

/**
 * Solve a guaranteed two-bone control limb.  The returned positions are
 * control transforms, not ModelJoint rotations or topology paths.
 */
export function solveHumanoidControlIk({
  controlRig,
  posedControls = null,
  role,
  target,
  bendSign = 1,
  bendDirection = null,
} = {}) {
  const keys = HUMANOID_LIMB_CONTROLS[role];
  const current = controlsFor(controlRig, posedControls);
  if (!keys || !current[keys[0]] || !current[keys[1]] || !current[keys[2]]) {
    return { positions: null, residual: Infinity, reached: false, reason: 'limb_unavailable' };
  }
  const anchor = current[keys[0]].clone();
  const rest = controlsFor(controlRig);
  const restAnchor = rest[keys[0]];
  const restBend = rest[keys[1]];
  const restEnd = rest[keys[2]];
  const targetPoint = vector(target);
  const upperLength = restAnchor.distanceTo(restBend);
  const lowerLength = restBend.distanceTo(restEnd);
  if (upperLength <= EPSILON || lowerLength <= EPSILON || !Number.isFinite(targetPoint.lengthSq())) {
    return { positions: null, residual: Infinity, reached: false, reason: 'invalid_limb_lengths' };
  }
  const rawDirection = targetPoint.clone().sub(anchor);
  const rawDistance = rawDirection.length();
  const restDirection = restEnd.clone().sub(restAnchor);
  if (restDirection.lengthSq() <= EPSILON) restDirection.copy(restBend).sub(restAnchor);
  restDirection.normalize();
  const direction = rawDistance > EPSILON ? rawDirection.multiplyScalar(1 / rawDistance) : restDirection.clone();
  const minimum = Math.abs(upperLength - lowerLength);
  const maximum = upperLength + lowerLength;
  const distance = Math.max(minimum, Math.min(maximum, rawDistance));
  const x =
    distance > EPSILON
      ? (distance * distance + upperLength * upperLength - lowerLength * lowerLength) / (2 * distance)
      : 0;
  const height = Math.sqrt(Math.max(0, upperLength * upperLength - x * x));
  let plane = bendDirection ? projectedDirection(bendDirection, direction) : null;
  if (!plane) {
    // Transport a rest-pose pole rather than a collapsed or previously solved
    // elbow. Reaching full extension must not erase the preferred bend plane.
    const restPole =
      projectedDirection(restBend.clone().sub(restAnchor), restDirection) ||
      fallbackPlane(restDirection, controlRig?.frame);
    plane = restPole.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(restDirection, direction));
  }
  if (Number(bendSign) < 0) plane.negate();
  const desiredBend = anchor.clone().addScaledVector(direction, x).addScaledVector(plane, height);
  const solvedEnd = anchor.clone().addScaledVector(direction, distance);
  const solved = { ...current, [keys[1]]: desiredBend.toArray(), [keys[2]]: solvedEnd.toArray() };
  const residual = solvedEnd.distanceTo(targetPoint);
  return {
    positions: solved,
    keys: [...keys],
    anchor: anchor.toArray(),
    bend: desiredBend.toArray(),
    end: solvedEnd.toArray(),
    upperLength,
    lowerLength,
    residual,
    reached: residual <= Math.max(1e-5, maximum * 0.001),
    iterations: 1,
  };
}
