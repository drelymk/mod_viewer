// Character-space axes used by the geometry-derived humanoid control rig.

import * as THREE from 'three';

const EPSILON = 1e-8;

function quaternionFrom(value) {
  if (value?.isQuaternion) {
    const result = value.clone();
    return Number.isFinite(result.lengthSq()) && result.lengthSq() > EPSILON
      ? result.normalize() : null;
  }
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 4).map(Number)
    : [value?.x, value?.y, value?.z, value?.w].map(Number);
  if (values.length !== 4 || !values.every(Number.isFinite)) return null;
  const result = new THREE.Quaternion(...values);
  return Number.isFinite(result.lengthSq()) && result.lengthSq() > EPSILON
    ? result.normalize() : null;
}

export function characterAxesFromOrientation({orientation, userRotation,
  baseOrientation, orientationInitialized} = {}) {
  if (orientationInitialized === false) return null;
  let sourceBaseOrientation = baseOrientation
    ? quaternionFrom(baseOrientation) : null;
  if (!sourceBaseOrientation) {
    if (!orientation || !userRotation) return null;
    sourceBaseOrientation = quaternionFrom(userRotation).invert()
      .multiply(quaternionFrom(orientation)).normalize();
  }
  const inverseBase = sourceBaseOrientation.clone().invert();
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(inverseBase).normalize();
  const forward = new THREE.Vector3(0, 0, 1).applyQuaternion(inverseBase);
  forward.addScaledVector(up, -forward.dot(up));
  if (!Number.isFinite(up.lengthSq()) || !Number.isFinite(forward.lengthSq())
      || up.lengthSq() <= EPSILON || forward.lengthSq() <= EPSILON) return null;
  forward.normalize();
  const right = up.clone().cross(forward);
  if (!Number.isFinite(right.lengthSq()) || right.lengthSq() <= EPSILON) return null;
  right.normalize();
  return {up, forward, right};
}
