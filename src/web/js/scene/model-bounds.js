// Shared, defensive world-space bounds for viewer model meshes.

import * as THREE from 'three';

/** Expand `box` by a mesh only when its position data and world bounds are usable. */
export function expandByModelMesh(box, mesh) {
  if (!mesh?.geometry) return box;
  const positions = mesh.geometry.attributes?.position?.array;
  if (!positions || positions.length < 3) return box;
  for (let index = 0; index < positions.length; index += 1) {
    if (!Number.isFinite(positions[index])) return box;
  }
  mesh.updateWorldMatrix(true, false);
  if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
  if (!mesh.geometry.boundingBox) return box;
  const worldBox = mesh.geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld);
  const values = [
    worldBox.min.x, worldBox.min.y, worldBox.min.z,
    worldBox.max.x, worldBox.max.y, worldBox.max.z,
  ];
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
