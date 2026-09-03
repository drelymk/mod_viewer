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

function valueFromCollection(collection, boneId) {
  if (collection instanceof Map) {
    return collection.get(boneId) ?? collection.get(String(boneId));
  }
  return collection?.[boneId];
}

function rotationFromCollection(rotationByBoneId, boneId) {
  const value = rotationByBoneId instanceof Map
    ? rotationByBoneId.get(boneId) ?? rotationByBoneId.get(String(boneId))
    : rotationByBoneId?.[boneId];
  const rotation = value?.rotationVector ?? value;
  const values = rotation?.isVector3
    ? [rotation.x, rotation.y, rotation.z]
    : Array.isArray(rotation) ? rotation
      : [rotation?.x, rotation?.y, rotation?.z];
  const vector = values.slice(0, 3).map(Number);
  return vector.length === 3 && vector.every(Number.isFinite)
    ? new THREE.Vector3(...vector) : new THREE.Vector3();
}

function quaternionFromRotationVector(rotationVector) {
  const vector = rotationVector?.isVector3
    ? rotationVector : new THREE.Vector3(
      Number(rotationVector?.[0] ?? rotationVector?.x) || 0,
      Number(rotationVector?.[1] ?? rotationVector?.y) || 0,
      Number(rotationVector?.[2] ?? rotationVector?.z) || 0,
    );
  const angle = vector.length();
  if (!Number.isFinite(angle) || angle < 1e-12) {
    return new THREE.Quaternion();
  }
  return new THREE.Quaternion().setFromAxisAngle(
    vector.clone().multiplyScalar(1 / angle), angle);
}

function quaternionFromCollection(quaternionByBoneId, boneId) {
  const value = valueFromCollection(quaternionByBoneId, boneId);
  const quaternion = value?.quaternion ?? value;
  if (quaternion?.isQuaternion) return quaternion.clone().normalize();
  const values = quaternion?.length >= 4
    ? quaternion.slice(0, 4).map(Number)
    : [quaternion?.x, quaternion?.y, quaternion?.z, quaternion?.w]
      .map(Number);
  return values.length === 4 && values.every(Number.isFinite)
    ? new THREE.Quaternion(...values).normalize() : new THREE.Quaternion();
}

export function buildForestTransformsFromLocalRotations(
    forest, nodeCenters, options = {}) {
  const transforms = new Map();
  const rotations = new Map();
  const transformCache = options.transformCache instanceof Map
    ? options.transformCache : new Map();
  const entryFor = boneId => {
    let entry = transformCache.get(boneId);
    if (!entry) {
      entry = {matrix: new THREE.Matrix4(), rotation: new THREE.Quaternion()};
      transformCache.set(boneId, entry);
    }
    return entry;
  };
  const rotationByBoneId = options.rotationByBoneId || new Map();
  const getRotation = typeof options.getRotation === 'function'
    ? options.getRotation : boneId => rotationFromCollection(
      rotationByBoneId, boneId);
  const getQuaternion = typeof options.getQuaternion === 'function'
    ? options.getQuaternion : options.quaternionByBoneId
      ? boneId => quaternionFromCollection(options.quaternionByBoneId, boneId)
      : null;
  const jointPivotByBoneId = options.jointPivotByBoneId || null;

  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    if (!Number.isFinite(rootId)) return;
    const rootEntry = entryFor(rootId);
    const rootTransform = rootEntry.matrix.identity();
    rootEntry.rotation.identity();
    transforms.set(rootId, rootTransform);
    rotations.set(rootId, rootEntry.rotation);
    const queue = [rootId];
    const visited = new Set([rootId]);
    while (queue.length) {
      const parentId = queue.shift();
      const parentTransform = transforms.get(parentId);
      const parentRotation = rotations.get(parentId)
        || new THREE.Quaternion();
      const parentCenter = vectorFromCenter(
        centerFromCollection(nodeCenters, parentId));
      const children = component.childrenById?.[parentId] || [];
      children.forEach(childValue => {
        const childId = Number(childValue);
        if (!Number.isFinite(childId) || visited.has(childId)) return;
        visited.add(childId);
        const localRotation = getQuaternion
          ? (getQuaternion(childId)?.clone?.() || new THREE.Quaternion())
          : quaternionFromRotationVector(getRotation(childId));
        localRotation.normalize();
        // The local rotation vector is expressed in the parent frame. Convert
        // it to a world-space rotation around the already transformed pivot,
        // then inherit the parent's affine transform.
        const worldRotation = parentRotation.clone()
          .multiply(localRotation)
          .multiply(parentRotation.clone().invert());
        const jointCenter = vectorFromCenter(
          valueFromCollection(jointPivotByBoneId, childId));
        const pivot = jointPivotByBoneId
          && valueFromCollection(jointPivotByBoneId, childId)
          ? jointCenter.applyMatrix4(parentTransform)
          : parentCenter.clone().applyMatrix4(parentTransform);
        const aroundPivot = rotationAroundPivot(
          pivot, worldRotation);
        const childEntry = entryFor(childId);
        childEntry.matrix.copy(aroundPivot).multiply(parentTransform);
        childEntry.rotation.copy(parentRotation).multiply(localRotation);
        transforms.set(childId, childEntry.matrix);
        rotations.set(childId, childEntry.rotation);
        queue.push(childId);
      });
    }
    // Malformed orientation data receives safe identity transforms rather
    // than making a vertex silently lose its authored influence.
    (component.nodeIds || []).forEach(nodeValue => {
      const nodeId = Number(nodeValue);
      if (Number.isFinite(nodeId) && !transforms.has(nodeId)) {
        const entry = entryFor(nodeId);
        entry.matrix.identity();
        entry.rotation.identity();
        transforms.set(nodeId, entry.matrix);
        rotations.set(nodeId, entry.rotation);
      }
    });
  });
  if (options.rotationOutput instanceof Map) {
    options.rotationOutput.clear();
    rotations.forEach((rotation, boneId) => {
      options.rotationOutput.set(boneId, rotation);
    });
  }
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
  applyWeightedTransformDeformationInto(
    result, baselinePositions, indices, weights, influenceCount,
    transformByBoneId);
  return result;
}

export function applyWeightedTransformDeformationInto(
    outputPositions, baselinePositions, indices, weights, influenceCount,
    transformByBoneId, activeVertices = null) {
  if (!baselinePositions || !indices || !weights || influenceCount <= 0
      || !transformByBoneId || !outputPositions) return 0;
  const vertexCount = Math.floor(baselinePositions.length / 3);
  const baseline = new THREE.Vector3();
  const transformed = new THREE.Vector3();
  const vertices = activeVertices || {length: vertexCount};
  for (let activeIndex = 0; activeIndex < vertices.length; activeIndex += 1) {
    const vertex = activeVertices ? Number(vertices[activeIndex]) : activeIndex;
    if (!Number.isInteger(vertex) || vertex < 0 || vertex >= vertexCount) continue;
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
    outputPositions[offset] = deformedX;
    outputPositions[offset + 1] = deformedY;
    outputPositions[offset + 2] = deformedZ;
  }
  return vertices.length;
}

export function applyWeightedNormalDeformationInto(
    outputNormals, baselineNormals, indices, weights, influenceCount,
    rotationByBoneId, activeVertices = null) {
  if (!outputNormals || !baselineNormals || !indices || !weights
      || influenceCount <= 0 || !rotationByBoneId) return 0;
  const vertexCount = Math.floor(baselineNormals.length / 3);
  const baseline = new THREE.Vector3();
  const transformed = new THREE.Vector3();
  const vertices = activeVertices || {length: vertexCount};
  for (let activeIndex = 0; activeIndex < vertices.length; activeIndex += 1) {
    const vertex = activeVertices ? Number(vertices[activeIndex]) : activeIndex;
    if (!Number.isInteger(vertex) || vertex < 0 || vertex >= vertexCount) continue;
    const offset = vertex * 3;
    baseline.set(
      baselineNormals[offset], baselineNormals[offset + 1],
      baselineNormals[offset + 2]);
    const start = vertex * influenceCount;
    let transformedWeight = 0;
    let x = 0, y = 0, z = 0;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const weight = weights[start + influence];
      const rotation = transformForBone(
        rotationByBoneId, indices[start + influence]);
      if (!rotation || !Number.isFinite(weight) || weight <= 0) continue;
      transformedWeight += weight;
      transformed.copy(baseline).applyQuaternion(rotation);
      x += transformed.x * weight;
      y += transformed.y * weight;
      z += transformed.z * weight;
    }
    const unchanged = Math.max(0, 1 - transformedWeight);
    transformed.set(
      baseline.x * unchanged + x,
      baseline.y * unchanged + y,
      baseline.z * unchanged + z,
    );
    if (transformed.lengthSq() > 1e-20) transformed.normalize();
    else transformed.copy(baseline).normalize();
    outputNormals[offset] = transformed.x;
    outputNormals[offset + 1] = transformed.y;
    outputNormals[offset + 2] = transformed.z;
  }
  return vertices.length;
}
