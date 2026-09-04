import {
  Euler, Matrix4, Quaternion, Vector3,
} from 'three';

const EPSILON = 1e-8;
const CANONICAL_AXES = Object.freeze([
  new Vector3(1, 0, 0),
  new Vector3(0, 0, 1),
  new Vector3(0, 1, 0),
]);

function collectionValue(collection, boneId) {
  if (collection instanceof Map) {
    return collection.get(boneId) ?? collection.get(String(boneId));
  }
  return collection?.[boneId];
}

function vectorFrom(value) {
  if (value?.isVector3) return value.clone();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? value : [value?.x, value?.y, value?.z];
  if (!values || values.length < 3) return null;
  const vector = new Vector3(...values.slice(0, 3).map(Number));
  return vector.toArray().every(Number.isFinite) ? vector : null;
}

function vectorFor(collection, boneId) {
  return vectorFrom(collectionValue(collection, boneId));
}

function parentIdFor(component, boneId) {
  const value = component?.parentById?.[boneId];
  if (value === null || value === undefined) return null;
  const id = Number(value);
  return Number.isFinite(id) ? id : null;
}

function childrenFor(component, boneId) {
  return (component?.childrenById?.[boneId] || [])
    .map(Number).filter(Number.isFinite);
}

function edgeFor(component, parentId, childId) {
  return (component?.edges || []).find(edge => {
    const left = Number(edge.boneA);
    const right = Number(edge.boneB);
    return (left === parentId && right === childId)
      || (left === childId && right === parentId);
  }) || null;
}

function edgeStrength(edge) {
  return Number(edge?.treeEdgeScore ?? edge?.score
    ?? edge?.containment ?? edge?.jaccard ?? 0) || 0;
}

function restPointFor(boneId, centerByBoneId, jointPivotByBoneId) {
  const pivot = vectorFor(jointPivotByBoneId, boneId);
  if (pivot) return {vector: pivot, source: 'joint-pivot'};
  const center = vectorFor(centerByBoneId, boneId);
  return center ? {vector: center, source: 'weighted-center'} : null;
}

function directionCandidate(component, parentId, childId, centers, pivots,
    pivotB) {
  const child = restPointFor(childId, centers, pivots);
  if (!child) return null;
  const vector = child.vector.clone().sub(pivotB);
  const distance = vector.length();
  if (!Number.isFinite(distance) || distance <= EPSILON) return null;
  return {
    childId,
    vector: vector.normalize(),
    distance,
    pivotSource: child.source,
    edgeStrength: edgeStrength(edgeFor(component, parentId, childId)),
  };
}

function betterCandidate(left, right, incoming) {
  if (!left) return right;
  if (!right) return left;
  if (incoming) {
    const leftAlignment = incoming.dot(left.vector);
    const rightAlignment = incoming.dot(right.vector);
    if (Math.abs(leftAlignment - rightAlignment) > EPSILON) {
      return leftAlignment > rightAlignment ? left : right;
    }
  }
  if (Math.abs(left.edgeStrength - right.edgeStrength) > EPSILON) {
    return left.edgeStrength > right.edgeStrength ? left : right;
  }
  if (Math.abs(left.distance - right.distance) > EPSILON) {
    return left.distance > right.distance ? left : right;
  }
  return left.childId < right.childId ? left : right;
}

function canonicalDirection() {
  return new Vector3(0, 1, 0);
}

function directionForBone(component, boneId, centers, pivots) {
  const parentId = parentIdFor(component, boneId);
  const ownPoint = restPointFor(boneId, centers, pivots);
  const parentPoint = parentId === null
    ? vectorFor(centers, boneId) : restPointFor(parentId, centers, pivots)?.vector;
  const pivotB = vectorFor(pivots, boneId) || ownPoint?.vector
    || vectorFor(centers, boneId) || new Vector3();
  const incoming = parentPoint ? pivotB.clone().sub(parentPoint) : null;
  if (incoming && incoming.length() <= EPSILON) incoming.set(0, 0, 0);
  else incoming?.normalize();

  let best = null;
  childrenFor(component, boneId).forEach(childId => {
    best = betterCandidate(best,
      directionCandidate(component, boneId, childId, centers, pivots, pivotB),
      parentId !== null ? incoming : null);
  });
  if (best) {
    return {
      direction: best.vector,
      continuationChildId: best.childId,
      directionSource: best.pivotSource === 'joint-pivot'
        ? 'child-joint-pivot' : 'child-weighted-center',
    };
  }

  const weightedCenter = vectorFor(centers, boneId);
  if (weightedCenter) {
    const fromCenter = weightedCenter.sub(pivotB);
    if (fromCenter.length() > EPSILON) {
      return {
        direction: fromCenter.normalize(),
        continuationChildId: null,
        directionSource: 'weighted-center',
      };
    }
  }
  if (incoming?.length() > EPSILON) {
    return {
      direction: incoming,
      continuationChildId: null,
      directionSource: 'incoming-chain',
    };
  }
  return {
    direction: canonicalDirection(),
    continuationChildId: null,
    directionSource: 'canonical-y',
  };
}

function canonicalReferenceAxis(y) {
  let best = CANONICAL_AXES[0];
  let bestAlignment = Math.abs(best.dot(y));
  CANONICAL_AXES.slice(1).forEach(axis => {
    const alignment = Math.abs(axis.dot(y));
    if (alignment < bestAlignment - EPSILON) {
      best = axis;
      bestAlignment = alignment;
    }
  });
  return best.clone();
}

function basisFor(direction, parentBasis = null) {
  const y = direction.clone();
  if (y.length() <= EPSILON) y.copy(canonicalDirection());
  else y.normalize();
  let x = parentBasis
    ? parentBasis.x.clone().addScaledVector(y, -parentBasis.x.dot(y))
    : canonicalReferenceAxis(y).addScaledVector(y, -canonicalReferenceAxis(y).dot(y));
  if (x.length() <= EPSILON && parentBasis) {
    x = parentBasis.z.clone().addScaledVector(y, -parentBasis.z.dot(y));
  }
  if (x.length() <= EPSILON) {
    const reference = canonicalReferenceAxis(y);
    x = reference.addScaledVector(y, -reference.dot(y));
  }
  if (x.length() <= EPSILON) x.set(1, 0, 0);
  x.normalize();
  if (parentBasis && x.dot(parentBasis.x) < 0) x.negate();
  const z = new Vector3().crossVectors(x, y).normalize();
  x.crossVectors(y, z).normalize();
  return {x, y, z};
}

function quaternionForBasis(basis) {
  return new Quaternion().setFromRotationMatrix(
    new Matrix4().makeBasis(basis.x, basis.y, basis.z)).normalize();
}

function addComponentFrames(component, centers, pivots, result) {
  const nodeIds = (component?.nodeIds || []).map(Number)
    .filter(Number.isFinite);
  if (!nodeIds.length) return;
  const rootId = nodeIds.includes(Number(component.rootId))
    ? Number(component.rootId) : nodeIds[0];
  nodeIds.forEach(boneId => {
    const direction = directionForBone(component, boneId, centers, pivots);
    result.directionByBoneId.set(boneId, direction.direction.clone().normalize());
    result.continuationChildByBoneId.set(
      boneId, direction.continuationChildId ?? null);
    result.evidenceByBoneId.set(boneId, {
      directionSource: direction.directionSource,
      continuationChildId: direction.continuationChildId ?? null,
    });
  });

  const queue = [{boneId: rootId, parentBasis: null}];
  const visited = new Set();
  while (queue.length) {
    const {boneId, parentBasis} = queue.shift();
    if (visited.has(boneId)) continue;
    visited.add(boneId);
    const basis = basisFor(
      result.directionByBoneId.get(boneId) || canonicalDirection(),
      parentBasis);
    result.frameByBoneId.set(boneId, quaternionForBasis(basis));
    childrenFor(component, boneId).forEach(childId => {
      if (!visited.has(childId)) queue.push({boneId: childId, parentBasis: basis});
    });
  }
  nodeIds.forEach(boneId => {
    if (result.frameByBoneId.has(boneId)) return;
    const basis = basisFor(
      result.directionByBoneId.get(boneId) || canonicalDirection());
    result.frameByBoneId.set(boneId, quaternionForBasis(basis));
  });
}

export function buildInferredRigRestFrames(
    forest, centerByBoneId, jointPivotByBoneId) {
  const result = {
    frameByBoneId: new Map(),
    directionByBoneId: new Map(),
    continuationChildByBoneId: new Map(),
    evidenceByBoneId: new Map(),
  };
  (forest?.components || []).forEach(component =>
    addComponentFrames(component, centerByBoneId, jointPivotByBoneId, result));
  return result;
}

function quaternionFrom(value) {
  if (value?.isQuaternion) return value.clone().normalize();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 4).map(Number)
    : [value?.x, value?.y, value?.z, value?.w].map(Number);
  return values.length === 4 && values.every(Number.isFinite)
    ? new Quaternion(...values).normalize() : new Quaternion();
}

export function poseToRestFrameDelta(localPose, restFrame) {
  const rest = quaternionFrom(restFrame);
  return rest.clone().invert().multiply(quaternionFrom(localPose))
    .multiply(rest).normalize();
}

export function restFrameDeltaToPose(frameDelta, restFrame) {
  const rest = quaternionFrom(restFrame);
  return rest.clone().multiply(quaternionFrom(frameDelta))
    .multiply(rest.invert()).normalize();
}

export function eulerFromRestFrameDelta(localPose, restFrame, order = 'XYZ') {
  const delta = poseToRestFrameDelta(localPose, restFrame);
  return new Euler().setFromQuaternion(delta, order);
}
