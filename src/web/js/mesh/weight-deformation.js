import * as THREE from 'three';

function rotatePoint(x, y, z, center, axis, radians) {
  const dx = x - center[0];
  const dy = y - center[1];
  const dz = z - center[2];
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  let rx = dx;
  let ry = dy;
  let rz = dz;
  if (axis === 'X') {
    ry = dy * cos - dz * sin;
    rz = dy * sin + dz * cos;
  } else if (axis === 'Y') {
    rx = dx * cos + dz * sin;
    rz = -dx * sin + dz * cos;
  } else {
    rx = dx * cos - dy * sin;
    ry = dx * sin + dy * cos;
  }
  return [rx + center[0], ry + center[1], rz + center[2]];
}

export function applyWeightedRotation(baselinePositions, indices, weights,
                                       influenceCount, boneId, center,
                                       axis = 'Z', angleDegrees = 0) {
  const result = new Float32Array(baselinePositions || 0);
  if (!baselinePositions || !indices || !weights || influenceCount <= 0
      || !Number.isFinite(angleDegrees)) return result;
  const radians = angleDegrees * Math.PI / 180;
  const vertexCount = Math.floor(baselinePositions.length / 3);
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const offset = vertex * 3;
    const weight = Math.max(0, Math.min(1, weightForBone(
      indices, weights, influenceCount, vertex, boneId)));
    if (!weight || !center) continue;
    const rotated = rotatePoint(
      baselinePositions[offset], baselinePositions[offset + 1],
      baselinePositions[offset + 2], center, axis, radians);
    result[offset] = baselinePositions[offset]
      + weight * (rotated[0] - baselinePositions[offset]);
    result[offset + 1] = baselinePositions[offset + 1]
      + weight * (rotated[1] - baselinePositions[offset + 1]);
    result[offset + 2] = baselinePositions[offset + 2]
      + weight * (rotated[2] - baselinePositions[offset + 2]);
  }
  return result;
}

function weightForBone(indices, weights, influenceCount, vertexIndex, boneId) {
  const start = vertexIndex * influenceCount;
  let total = 0;
  for (let influence = 0; influence < influenceCount; influence += 1) {
    if (indices[start + influence] !== boneId) continue;
    const weight = weights[start + influence];
    if (Number.isFinite(weight) && weight > 0) total += weight;
  }
  return total;
}

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

export function buildChainTransforms(centers, axis = 'Z', totalAngle = 0) {
  const source = Array.isArray(centers) ? centers : [];
  const transforms = source.map(() => new THREE.Matrix4());
  if (source.length < 2 || !Number.isFinite(Number(totalAngle))) {
    return transforms;
  }
  const jointAngle = THREE.MathUtils.degToRad(Number(totalAngle))
    / (source.length - 1);
  const rotationAxis = axisVector(axis);
  for (let index = 1; index < source.length; index += 1) {
    const parent = transforms[index - 1];
    const pivot = vectorFromCenter(source[index - 1])
      .applyMatrix4(parent);
    const aroundPivot = rotationAroundPivot(
      pivot, rotationAxis, jointAngle);
    transforms[index] = aroundPivot.multiply(parent.clone());
  }
  return transforms;
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

function maxDepthForComponent(component) {
  const declared = Number(component?.maxDepth);
  if (Number.isFinite(declared) && declared >= 0) return declared;
  return Math.max(0, ...Object.values(component?.depthById || {})
    .filter(depth => depth !== null && Number.isFinite(Number(depth)))
    .map(Number));
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

export function buildForestTransforms(forest, nodeCenters, options = {}) {
  const totalAngle = Number(options.totalAngle ?? options.angleDegrees ?? 0);
  if (!Number.isFinite(totalAngle)) return new Map();
  const angleByBoneId = new Map();
  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    const maxDepth = maxDepthForComponent(component);
    const edgeAngle = maxDepth > 0
      ? THREE.MathUtils.degToRad(totalAngle) / maxDepth : 0;
    (component.nodeIds || []).forEach(nodeValue => {
      const nodeId = Number(nodeValue);
      if (Number.isFinite(nodeId) && nodeId !== rootId) {
        angleByBoneId.set(nodeId, edgeAngle);
      }
    });
  });
  return buildForestTransformsFromLocalAngles(
    forest, nodeCenters, {axis: options.axis || 'Z', angleByBoneId});
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

export function applyWeightedChainDeformation(
    baselinePositions, indices, weights, influenceCount, chainIds,
    transforms) {
  if (!baselinePositions || !indices || !weights || influenceCount <= 0
      || !Array.isArray(chainIds) || chainIds.length < 2
      || !Array.isArray(transforms) || transforms.length < chainIds.length) {
    return new Float32Array(baselinePositions || 0);
  }
  const transformByBoneId = new Map();
  chainIds.forEach((boneId, index) => {
    if (transforms[index]) {
      transformByBoneId.set(Number(boneId), transforms[index]);
    }
  });
  return applyWeightedTransformDeformation(
    baselinePositions, indices, weights, influenceCount, transformByBoneId);
}
