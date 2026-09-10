import * as THREE from 'three';

export const HUMANOID_LIMB_CONTROLS = Object.freeze({
  left_arm: Object.freeze(['leftShoulder', 'leftElbow', 'leftHand']),
  right_arm: Object.freeze(['rightShoulder', 'rightElbow', 'rightHand']),
  left_leg: Object.freeze(['leftHip', 'leftKnee', 'leftFoot']),
  right_leg: Object.freeze(['rightHip', 'rightKnee', 'rightFoot']),
});

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
  Object.keys(controlRig?.controls || {}).forEach(key => {
    result[key] = vector(posedControls?.[key] ?? controlRig.controls[key]);
  });
  return result;
}

function projectedDirection(value, direction) {
  const result = vector(value).addScaledVector(direction,
    -vector(value).dot(direction));
  return result.lengthSq() > EPSILON ? result.normalize() : null;
}

function fallbackPlane(direction, frame) {
  const preferred = projectedDirection(frame?.forward, direction)
    || projectedDirection(frame?.right, direction);
  if (preferred) return preferred;
  const fallback = Math.abs(direction.x) < .8
    ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
  return projectedDirection(fallback, direction)
    || new THREE.Vector3(0, 0, 1);
}

/**
 * Solve a guaranteed two-bone control limb.  The returned positions are
 * control transforms, not ModelJoint rotations or topology paths.
 */
export function solveHumanoidControlIk({controlRig, posedControls = null,
    role, target, bendSign = 1, bendDirection = null} = {}) {
  const keys = HUMANOID_LIMB_CONTROLS[role];
  const current = controlsFor(controlRig, posedControls);
  if (!keys || !current[keys[0]] || !current[keys[1]] || !current[keys[2]]) {
    return {positions: null, residual: Infinity, reached: false, reason: 'limb_unavailable'};
  }
  const anchor = current[keys[0]].clone();
  const bend = current[keys[1]].clone();
  const end = current[keys[2]].clone();
  const targetPoint = vector(target);
  const upperLength = anchor.distanceTo(bend);
  const lowerLength = bend.distanceTo(end);
  if (upperLength <= EPSILON || lowerLength <= EPSILON
      || !Number.isFinite(targetPoint.lengthSq())) {
    return {positions: null, residual: Infinity, reached: false,
      reason: 'invalid_limb_lengths'};
  }
  const rawDirection = targetPoint.clone().sub(anchor);
  const rawDistance = rawDirection.length();
  const direction = rawDistance > EPSILON
    ? rawDirection.multiplyScalar(1 / rawDistance)
    : end.clone().sub(anchor).normalize();
  const minimum = Math.abs(upperLength - lowerLength);
  const maximum = upperLength + lowerLength;
  const distance = Math.max(minimum, Math.min(maximum, rawDistance));
  const x = (distance * distance + upperLength * upperLength
    - lowerLength * lowerLength) / Math.max(2 * distance, EPSILON);
  const height = Math.sqrt(Math.max(0, upperLength * upperLength - x * x));
  let plane = bendDirection ? projectedDirection(bendDirection, direction) : null;
  if (!plane) plane = projectedDirection(bend.clone().sub(anchor), direction);
  if (!plane) plane = fallbackPlane(direction, controlRig?.frame);
  if (Number(bendSign) < 0) plane.negate();
  const desiredBend = anchor.clone().addScaledVector(direction, x)
    .addScaledVector(plane, height);
  const solvedEnd = anchor.clone().addScaledVector(direction, distance);
  const solved = {...current,
    [keys[1]]: desiredBend.toArray(),
    [keys[2]]: solvedEnd.toArray(),
  };
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
    reached: residual <= Math.max(1e-5, maximum * .001),
    iterations: 1,
  };
}
