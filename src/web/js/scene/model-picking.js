// Shared viewport raycasting for model selection and one-shot tools.

import * as THREE from 'three';

/** Return the first visible model intersection at a client-space point. */
export function raycastModelAtClientPoint({
  clientX, clientY, canvas, camera, meshes,
} = {}) {
  const rect = canvas?.getBoundingClientRect?.();
  const width = Number(rect?.width);
  const height = Number(rect?.height);
  if (!rect || !camera || width <= 0 || height <= 0) return null;

  const pointer = new THREE.Vector2(
    ((Number(clientX) - rect.left) / width) * 2 - 1,
    -((Number(clientY) - rect.top) / height) * 2 + 1);
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(pointer, camera);
  const visibleMeshes = [...(meshes || [])].filter(mesh => mesh?.visible);
  return raycaster.intersectObjects(visibleMeshes, false)[0] || null;
}
