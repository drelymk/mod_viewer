// Shared, defensive world-space bounds for viewer model meshes.

import * as THREE from 'three';

/** Expand `box` by a mesh only when its position data and world bounds are usable. */
export function expandByModelMesh(box, mesh) {
  if (!mesh?.geometry) return box;
  const geometry = mesh.geometry;
  // Bounds are validated when first created. Shadow fitting can run on every
  // key-light drag frame, so a cached bounding box must not rescan all vertices.
  // Geometry mutation paths recompute this box before requesting a refit.
  if (!geometry.boundingBox) {
    const positions = geometry.attributes?.position?.array;
    if (!positions || positions.length < 3) return box;
    for (let index = 0; index < positions.length; index += 1) {
      if (!Number.isFinite(positions[index])) return box;
    }
    geometry.computeBoundingBox();
  }
  mesh.updateWorldMatrix(true, false);
  if (!geometry.boundingBox) return box;
  const worldBox = geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld);
  const values = [worldBox.min.x, worldBox.min.y, worldBox.min.z, worldBox.max.x, worldBox.max.y, worldBox.max.z];
  if (values.every(Number.isFinite)) box.union(worldBox);
  return box;
}

/** Return the world bounds of model meshes, optionally excluding hidden meshes. */
export function computeModelBounds(meshes, { visibleOnly = false } = {}) {
  const box = new THREE.Box3();
  for (const mesh of meshes || []) {
    if (visibleOnly && mesh?.visible !== true) continue;
    expandByModelMesh(box, mesh);
  }
  return box;
}

/**
 * Return world bounds of the meshes' rest pose (shape keys included).
 *
 * Animation, posing and physics replace `geometry.boundingBox` with padded,
 * conservative boxes, so anything that must not depend on the current pose,
 * such as the floor, measures the rest positions instead.
 */
export function computeRestBounds(meshes, { visibleOnly = false } = {}) {
  const box = new THREE.Box3();
  const local = new THREE.Box3();
  const point = new THREE.Vector3();
  for (const mesh of meshes || []) {
    if (visibleOnly && mesh?.visible !== true) continue;
    // Loose parts share their source's geometry and bounds, so they share its rest pose too.
    const positions =
      mesh?.userData?.humanoidRestPositions ?? mesh?.userData?.loosePartParent?.userData?.humanoidRestPositions;
    if (!positions?.length) {
      expandByModelMesh(box, mesh);
      continue;
    }
    local.makeEmpty();
    for (let index = 0; index + 2 < positions.length; index += 3) {
      point.fromArray(positions, index);
      if (Number.isFinite(point.x + point.y + point.z)) local.expandByPoint(point);
    }
    if (local.isEmpty()) continue;
    mesh.updateWorldMatrix(true, false);
    box.union(local.applyMatrix4(mesh.matrixWorld));
  }
  return box;
}
