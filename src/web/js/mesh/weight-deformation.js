import * as THREE from 'three';

function vectorFromCenter(center) {
  if (center?.isVector3) return center.clone();
  return new THREE.Vector3(
    Number(center?.[0]) || 0,
    Number(center?.[1]) || 0,
    Number(center?.[2]) || 0,
  );
}

function rotationAroundPivot(pivot, rotation) {
  return new THREE.Matrix4()
    .makeTranslation(pivot.x, pivot.y, pivot.z)
    .multiply(new THREE.Matrix4().makeRotationFromQuaternion(rotation))
    .multiply(new THREE.Matrix4().makeTranslation(
      -pivot.x, -pivot.y, -pivot.z));
}

function centerFromCollection(nodeCenters, boneId) {
  if (nodeCenters instanceof Map) {
    return nodeCenters.get(boneId) ?? nodeCenters.get(String(boneId));
  }
  return nodeCenters?.[boneId];
}

function rotationFromCollection(rotationByBoneId, boneId) {
  const value = rotationByBoneId instanceof Map
    ? rotationByBoneId.get(boneId) ?? rotationByBoneId.get(String(boneId))
    : rotationByBoneId?.[boneId];
  const values = value?.isVector3
    ? [value.x, value.y, value.z]
    : Array.isArray(value) ? value : [value?.x, value?.y, value?.z];
  const vector = values.slice(0, 3).map(Number);
  return vector.length === 3 && vector.every(Number.isFinite)
    ? new THREE.Vector3(...vector) : new THREE.Vector3();
}

function quaternionFromRotationVector(rotationVector) {
  const angle = rotationVector.length();
  if (!Number.isFinite(angle) || angle < 1e-12) {
    return new THREE.Quaternion();
  }
  return new THREE.Quaternion().setFromAxisAngle(
    rotationVector.clone().multiplyScalar(1 / angle), angle);
}

export function buildForestTransformsFromLocalRotations(
    forest, nodeCenters, options = {}) {
  const transforms = new Map();
  const rotations = new Map();
  const rotationByBoneId = options.rotationByBoneId || new Map();

  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    if (!Number.isFinite(rootId)) return;
    const rootTransform = new THREE.Matrix4();
    transforms.set(rootId, rootTransform);
    rotations.set(rootId, new THREE.Quaternion());
    const queue = [rootId];
    const visited = new Set([rootId]);
    while (queue.length) {
      const parentId = queue.shift();
      const parentTransform = transforms.get(parentId);
      const parentRotation = rotations.get(parentId)
        || new THREE.Quaternion();
      const parentCenter = vectorFromCenter(
        centerFromCollection(nodeCenters, parentId));
      const pivot = parentCenter.clone().applyMatrix4(parentTransform);
      const children = component.childrenById?.[parentId] || [];
      children.forEach(childValue => {
        const childId = Number(childValue);
        if (!Number.isFinite(childId) || visited.has(childId)) return;
        visited.add(childId);
        const localRotation = quaternionFromRotationVector(
          rotationFromCollection(rotationByBoneId, childId));
        // The local rotation vector is expressed in the parent frame. Convert
        // it to a world-space rotation around the already transformed pivot,
        // then inherit the parent's affine transform.
        const worldRotation = parentRotation.clone()
          .multiply(localRotation)
          .multiply(parentRotation.clone().invert());
        const aroundPivot = rotationAroundPivot(
          pivot, worldRotation);
        transforms.set(childId,
          aroundPivot.clone().multiply(parentTransform.clone()));
        rotations.set(childId, parentRotation.clone().multiply(localRotation));
        queue.push(childId);
      });
    }
    // Malformed orientation data receives safe identity transforms rather
    // than making a vertex silently lose its authored influence.
    (component.nodeIds || []).forEach(nodeValue => {
      const nodeId = Number(nodeValue);
      if (Number.isFinite(nodeId) && !transforms.has(nodeId)) {
        transforms.set(nodeId, new THREE.Matrix4());
      }
    });
  });
  return transforms;
}

function transformForBone(transformByBoneId, boneId) {
  if (transformByBoneId instanceof Map) {
    return transformByBoneId.get(Number(boneId));
  }
  return transformByBoneId?.[boneId];
}

export function applyWeightedTransformDeformation(
    baselinePositions, indices, weights, influenceCount, transformByBoneId) {
  const result = new Float32Array(baselinePositions || 0);
  if (!baselinePositions || !indices || !weights || influenceCount <= 0
      || !transformByBoneId) return result;
  const vertexCount = Math.floor(baselinePositions.length / 3);
  const baseline = new THREE.Vector3();
  const transformed = new THREE.Vector3();
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const offset = vertex * 3;
    baseline.set(
      baselinePositions[offset], baselinePositions[offset + 1],
      baselinePositions[offset + 2]);
    const start = vertex * influenceCount;
    let transformedWeight = 0;
    let x = 0, y = 0, z = 0;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const weight = weights[start + influence];
      const transform = transformForBone(
        transformByBoneId, indices[start + influence]);
      if (!transform || !Number.isFinite(weight) || weight <= 0) continue;
      transformedWeight += weight;
      transformed.copy(baseline).applyMatrix4(transform);
      x += transformed.x * weight;
      y += transformed.y * weight;
      z += transformed.z * weight;
    }
    const unchanged = Math.max(0, 1 - transformedWeight);
    const deformedX = baseline.x * unchanged + x;
    const deformedY = baseline.y * unchanged + y;
    const deformedZ = baseline.z * unchanged + z;
    result[offset] = deformedX;
    result[offset + 1] = deformedY;
    result[offset + 2] = deformedZ;
  }
  return result;
}
