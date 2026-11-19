// Conservative bounds for skinned meshes while their CPU positions are stale.
//
// GPU-skinned animation leaves `geometry.boundingBox` at the rest or last
// finalized pose. Camera clipping and shadow fitting read that box, so a clip
// that moves the model toward the camera would otherwise be cut by the near
// plane. A skinned vertex is a convex combination of `M_bone * rest`, so it
// always lies inside the union of each bone's rest box transformed by that
// bone's skinning matrix.

import * as THREE from 'three';

const RESIDUAL_WEIGHT = 0.999;
const BOUNDS_PADDING = 0.05;

function transformFor(transformByBoneId, boneId) {
  const matrix = transformByBoneId instanceof Map ? transformByBoneId.get(Number(boneId)) : transformByBoneId?.[boneId];
  return matrix?.isMatrix4 ? matrix : null;
}

/** Group rest positions by influencing bone; one pass per baseline. */
export function buildBoneRestBounds(positions, indices, weights, influenceCount) {
  const bones = new Map();
  const residual = new THREE.Box3();
  const rest = new THREE.Box3();
  if (!positions || !indices || !weights || !(influenceCount > 0)) return { positions, bones, residual, rest };
  const point = new THREE.Vector3();
  const vertexCount = Math.floor(positions.length / 3);
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    point.fromArray(positions, vertex * 3);
    if (!Number.isFinite(point.x + point.y + point.z)) continue;
    rest.expandByPoint(point);
    let total = 0;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const slot = vertex * influenceCount + influence;
      const weight = weights[slot];
      if (!(weight > 0)) continue;
      total += weight;
      const bone = Number(indices[slot]);
      let box = bones.get(bone);
      if (!box) bones.set(bone, (box = new THREE.Box3()));
      box.expandByPoint(point);
    }
    // Weight not covered by a bone keeps the vertex at its rest position.
    if (total < RESIDUAL_WEIGHT) residual.expandByPoint(point);
  }
  return { positions, bones, residual, rest };
}

/**
 * Box enclosing every vertex posed by `transformByBoneId`, or null when the
 * result is not finite. Bones without a transform stay at rest, and the whole
 * rest box is included when only part of the mesh is being deformed.
 */
export function computePosedBounds(restBounds, transformByBoneId, { partial = false } = {}, target = new THREE.Box3()) {
  target.makeEmpty();
  const posed = new THREE.Box3();
  for (const [bone, box] of restBounds.bones) {
    const matrix = transformFor(transformByBoneId, bone);
    if (matrix) target.union(posed.copy(box).applyMatrix4(matrix));
    else target.union(box);
  }
  target.union(restBounds.residual);
  if (partial) target.union(restBounds.rest);
  if (target.isEmpty()) return null;
  const size = target.getSize(new THREE.Vector3());
  target.expandByScalar(Math.max(size.length() * BOUNDS_PADDING, 1e-6));
  const values = [target.min.x, target.min.y, target.min.z, target.max.x, target.max.y, target.max.z];
  return values.every(Number.isFinite) ? target : null;
}
