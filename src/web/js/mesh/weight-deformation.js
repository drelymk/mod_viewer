import * as THREE from 'three';

function vectorFromCenter(center) {
  if (center?.isVector3) return center.clone();
  return new THREE.Vector3(
    Number(center?.[0]) || 0,
    Number(center?.[1]) || 0,
    Number(center?.[2]) || 0,
  );
}

function axisVector(axis) {
  if (axis === 'X') return new THREE.Vector3(1, 0, 0);
  if (axis === 'Y') return new THREE.Vector3(0, 1, 0);
  return new THREE.Vector3(0, 0, 1);
}

function rotationAroundPivot(pivot, rotationAxis, radians) {
  return new THREE.Matrix4()
    .makeTranslation(pivot.x, pivot.y, pivot.z)
    .multiply(new THREE.Matrix4().makeRotationAxis(
      rotationAxis, radians))
    .multiply(new THREE.Matrix4().makeTranslation(
      -pivot.x, -pivot.y, -pivot.z));
}

function centerFromCollection(nodeCenters, boneId) {
  if (nodeCenters instanceof Map) {
    return nodeCenters.get(boneId) ?? nodeCenters.get(String(boneId));
  }
  return nodeCenters?.[boneId];
}

function angleFromCollection(angleByBoneId, boneId) {
  if (angleByBoneId instanceof Map) {
    return Number(angleByBoneId.get(boneId)
      ?? angleByBoneId.get(String(boneId))) || 0;
  }
  return Number(angleByBoneId?.[boneId]) || 0;
}

export function buildForestTransformsFromLocalAngles(
    forest, nodeCenters, options = {}) {
  const transforms = new Map();
  const axis = options.axis || 'Z';
  const angleByBoneId = options.angleByBoneId || new Map();
  const rotationAxis = axisVector(axis);

  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    if (!Number.isFinite(rootId)) return;
    const rootTransform = new THREE.Matrix4();
    transforms.set(rootId, rootTransform);
    const queue = [rootId];
    const visited = new Set([rootId]);
    while (queue.length) {
      const parentId = queue.shift();
      const parentTransform = transforms.get(parentId);
      const parentCenter = vectorFromCenter(
        centerFromCollection(nodeCenters, parentId));
      const pivot = parentCenter.clone().applyMatrix4(parentTransform);
      const children = component.childrenById?.[parentId] || [];
      children.forEach(childValue => {
        const childId = Number(childValue);
        if (!Number.isFinite(childId) || visited.has(childId)) return;
        visited.add(childId);
        // A joint's local angle belongs to the edge from its parent.  Apply
        // it around the already transformed parent pivot before inheriting
        // the parent's accumulated transform.
        const aroundPivot = rotationAroundPivot(
          pivot, rotationAxis, angleFromCollection(angleByBoneId, childId));
        transforms.set(childId,
          aroundPivot.clone().multiply(parentTransform.clone()));
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
    result[offset] = baseline.x * unchanged + x;
    result[offset + 1] = baseline.y * unchanged + y;
    result[offset + 2] = baseline.z * unchanged + z;
  }
  return result;
}
