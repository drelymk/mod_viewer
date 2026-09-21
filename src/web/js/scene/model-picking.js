// Shared viewport raycasting for model selection and one-shot tools.

import * as THREE from 'three';
import {getLooseParts} from '../mesh/loose-parts.js';

function meshPickTargets(mesh) {
  const parts = getLooseParts(mesh);
  if (!parts.length) return [mesh];
  return mesh?.visible ? parts : [];
}

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
  const visibleMeshes = [...(meshes || [])]
    .flatMap(meshPickTargets)
    .filter(mesh => mesh?.visible);
  return raycaster.intersectObjects(visibleMeshes, false)[0] || null;
}
