// ModelJoint-native inverse kinematics. This module is deliberately pure:
// speculative solver poses never touch the live model Rig.

import * as THREE from 'three';
import {buildForestTransformsFromLocalRotations} from './weight-deformation.js';

const EPSILON = 1e-8;
const MAX_ITERATIONS = 10;
const TOLERANCE_SCALE = 1e-4;

function valueFor(collection, id) {
  if (collection instanceof Map) {
    return collection.get(id) ?? collection.get(String(id));
  }
  return collection?.[id] ?? collection?.[String(id)];
}

function numberId(value) {
  const id = Number(value);
  return Number.isInteger(id) ? id : null;
}

function parentFor(component, id) {
  const parent = valueFor(component?.parentById, id);
  if (parent === null || parent === undefined || parent === '') return null;
  return numberId(parent);
}

function quaternionFrom(value) {
  if (value?.isQuaternion) {
    const quaternion = value.clone();
    return quaternion.normalize();
  }
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 4).map(Number)
    : [value?.x, value?.y, value?.z, value?.w].map(Number);
  if (values.length !== 4 || !values.every(Number.isFinite)) {
    return new THREE.Quaternion();
  }
  const quaternion = new THREE.Quaternion(...values);
  return Number.isFinite(quaternion.lengthSq())
    && quaternion.lengthSq() > EPSILON ? quaternion.normalize()
    : new THREE.Quaternion();
}

function finiteVector(value) {
  if (value?.isVector3) {
    return value.clone().toArray().every(Number.isFinite) ? value.clone() : null;
  }
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 3).map(Number)
    : [value?.x, value?.y, value?.z].map(Number);
  if (values.length !== 3 || !values.every(Number.isFinite)) return null;
  return new THREE.Vector3(...values);
}

function pointFor(id, transforms, centers, pivots) {
  const point = finiteVector(valueFor(pivots, id))
    || finiteVector(valueFor(centers, id));
  if (!point) return null;
  const transform = transforms.get(id);
  if (!transform?.isMatrix4) return point;
  return point.applyMatrix4(transform);
}

function cloneRotations(localRotations) {
  const result = new Map();
  if (localRotations instanceof Map) {
    localRotations.forEach((rotation, id) => {
      const jointId = numberId(id);
      if (jointId !== null) result.set(jointId, quaternionFrom(rotation));
    });
    return result;
  }
  Object.entries(localRotations || {}).forEach(([id, rotation]) => {
    const jointId = numberId(id);
    if (jointId !== null) result.set(jointId, quaternionFrom(rotation));
  });
  return result;
}

function buildEvaluation({forest, centers, pivots, rotations}) {
  const rotationOutput = new Map();
  const transforms = buildForestTransformsFromLocalRotations(
    forest, centers, {
      getQuaternion: id => rotations.get(Number(id)) || new THREE.Quaternion(),
      jointPivotByBoneId: pivots,
      rotationOutput,
      // Keep this cache private to the current solve. The live pose cache is
      // intentionally never used during CCD iterations.
      transformCache: new Map(),
    });
  return {transforms, rotations: rotationOutput};
}

function distanceBetween(left, right) {
  if (!left || !right) return Infinity;
  return left.distanceTo(right);
}

function chainScale(chainJointIds, centers, pivots) {
  let scale = 0;
  for (let index = 1; index < chainJointIds.length; index += 1) {
    const first = finiteVector(valueFor(
      pivots, chainJointIds[index - 1]))
      || finiteVector(valueFor(centers, chainJointIds[index - 1]));
    const second = finiteVector(valueFor(
      pivots, chainJointIds[index]))
      || finiteVector(valueFor(centers, chainJointIds[index]));
    if (first && second) scale += first.distanceTo(second);
  }
  return Math.max(scale, EPSILON);
}

function shortestArc(from, to) {
  const dot = THREE.MathUtils.clamp(from.dot(to), -1, 1);
  if (dot > 1 - EPSILON) return new THREE.Quaternion();
  if (dot < -1 + EPSILON) {
    // Pick a deterministic axis perpendicular to the source vector for the
    // otherwise ambiguous 180-degree correction.
    const axis = Math.abs(from.x) < Math.abs(from.y)
      ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    axis.cross(from).normalize();
    return new THREE.Quaternion().setFromAxisAngle(axis, Math.PI);
  }
  return new THREE.Quaternion().setFromUnitVectors(from, to).normalize();
}

function changedQuaternion(before, after) {
  return Math.abs(Math.abs(before.dot(after)) - 1) > 1e-7;
}

/** Resolve an end-effector chain without ever including the component root. */
export function resolveIkChain({component, endJointId, requestedLength} = {}) {
  const end = numberId(endJointId);
  const requested = Math.max(1, Math.floor(Number(requestedLength) || 1));
  const root = numberId(component?.rootId);
  const jointIds = [];
  if (end !== null && component?.parentById && end !== root) {
    let current = end;
    const visited = new Set();
    while (current !== null && !visited.has(current)
        && jointIds.length < requested) {
      visited.add(current);
      jointIds.unshift(current);
      const parent = parentFor(component, current);
      if (parent === null || parent === root) break;
      current = parent;
    }
  }
  const solverJointIds = jointIds.length > 1
    ? jointIds.slice(0, -1) : [];
  return {
    available: solverJointIds.length > 0,
    endJointId: end,
    jointIds,
    solverJointIds,
    requestedLength: requested,
    effectiveLength: jointIds.length,
    clamped: jointIds.length !== requested,
  };
}

/**
 * Solve one target from the supplied pose. `rotations` contains only changed
 * solver joints; the input map and all forest data remain untouched.
 */
export function solveIkChain({
  forest, centers, jointPivots, localRotations, chainJointIds, target,
} = {}) {
  const chain = (chainJointIds || []).map(numberId)
    .filter(id => id !== null);
  const rootIds = new Set((forest?.components || [])
    .map(component => numberId(component?.rootId))
    .filter(id => id !== null));
  const solverJointIds = chain.length > 1
    ? chain.slice(0, -1).filter(id => !rootIds.has(id)) : [];
  const endJointId = chain.at(-1) ?? null;
  const targetPoint = finiteVector(target);
  const working = cloneRotations(localRotations);
  const original = cloneRotations(localRotations);
  if (!forest || !targetPoint || endJointId === null
      || !solverJointIds.length) {
    return {rotations: new Map(), iterations: 0, residual: Infinity,
      reached: false};
  }

  const tolerance = chainScale(chain, centers, jointPivots) * TOLERANCE_SCALE;
  let evaluation = buildEvaluation({
    forest, centers, pivots: jointPivots, rotations: working,
  });
  let endPoint = pointFor(
    endJointId, evaluation.transforms, centers, jointPivots);
  let residual = distanceBetween(endPoint, targetPoint);
  if (!Number.isFinite(residual)) {
    return {rotations: new Map(), iterations: 0, residual: Infinity,
      reached: false};
  }
  if (residual <= tolerance) {
    return {rotations: new Map(), iterations: 0, residual, reached: true};
  }

  let iterations = 0;
  for (; iterations < MAX_ITERATIONS && residual > tolerance; iterations += 1) {
    // CCD works from the joint nearest the end effector outward.
    for (let index = solverJointIds.length - 1; index >= 0; index -= 1) {
      const jointId = solverJointIds[index];
      const jointPoint = pointFor(
        jointId, evaluation.transforms, centers, jointPivots);
      endPoint = pointFor(
        endJointId, evaluation.transforms, centers, jointPivots);
      if (!jointPoint || !endPoint) continue;
      const currentVector = endPoint.clone().sub(jointPoint);
      const targetVector = targetPoint.clone().sub(jointPoint);
      if (currentVector.lengthSq() <= EPSILON
          || targetVector.lengthSq() <= EPSILON) continue;
      currentVector.normalize();
      targetVector.normalize();
      const delta = shortestArc(currentVector, targetVector);
      if (!delta.isQuaternion || !Number.isFinite(delta.lengthSq())) continue;

      const parentId = parentFor(
        (forest.components || []).find(component =>
          (component.nodeIds || []).map(Number).includes(jointId)), jointId);
      const parentRotation = parentId === null
        ? new THREE.Quaternion()
        : evaluation.rotations.get(parentId)?.clone()
          || new THREE.Quaternion();
      const localDelta = parentRotation.clone().invert()
        .multiply(delta)
        .multiply(parentRotation)
        .normalize();
      const previous = working.get(jointId)?.clone()
        || new THREE.Quaternion();
      const next = localDelta.multiply(previous).normalize();
      if (!Number.isFinite(next.x) || !Number.isFinite(next.y)
          || !Number.isFinite(next.z) || !Number.isFinite(next.w)) continue;
      working.set(jointId, next);
      evaluation = buildEvaluation({
        forest, centers, pivots: jointPivots, rotations: working,
      });
      endPoint = pointFor(
        endJointId, evaluation.transforms, centers, jointPivots);
      residual = distanceBetween(endPoint, targetPoint);
      if (!Number.isFinite(residual)) {
        // Keep the last valid pose and abandon this solve safely.
        working.set(jointId, previous);
        evaluation = buildEvaluation({
          forest, centers, pivots: jointPivots, rotations: working,
        });
        endPoint = pointFor(
          endJointId, evaluation.transforms, centers, jointPivots);
        residual = distanceBetween(endPoint, targetPoint);
        break;
      }
      if (residual <= tolerance) break;
    }
  }

  const rotations = new Map();
  solverJointIds.forEach(jointId => {
    const before = original.get(jointId) || new THREE.Quaternion();
    const after = working.get(jointId) || new THREE.Quaternion();
    if (changedQuaternion(before, after)) rotations.set(jointId, after.clone().normalize());
  });
  return {
    rotations,
    iterations,
    residual: Number.isFinite(residual) ? residual : Infinity,
    reached: Number.isFinite(residual) && residual <= tolerance,
  };
}
