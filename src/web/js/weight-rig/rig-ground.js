// Floor height for game-rig animation.
//
// Game extraction prepends a synthetic `mesh-space` root bone holding the
// mesh-to-Avatar basis. Clips keep the Avatar root on the game's ground
// (Avatar-space y = 0), but the bind pose is not a standing pose: its feet can
// reach below that plane, so a floor seated on the rest-pose base leaves
// animated feet hovering above it. Map the Avatar ground plane into world
// space through an animated mesh's world matrix instead.

import { Matrix3, Matrix4, Quaternion, Vector3 } from 'three';

const MESH_SPACE_BONE = 'mesh-space';
// Only a level plane can seat the horizontal floor; tilted models keep the
// rest-pose base.
const MIN_LEVEL = 0.999;

/**
 * Return a world floor height reader for a game rig's Avatar ground, or null
 * when the rig has no mesh-space basis. The reader returns null whenever no
 * animated mesh is in the scene or the plane is not level in world space.
 */
export function rigGroundAnchor(rig, meshes) {
  const root = rig?.bones?.[0];
  if (root?.name !== MESH_SPACE_BONE || !(root.parent < 0)) return null;
  const basis = new Matrix4().compose(
    new Vector3(...root.translation),
    new Quaternion(...root.rotation),
    new Vector3(...(root.scale || [1, 1, 1])),
  );
  if (!Number.isFinite(basis.determinant()) || Math.abs(basis.determinant()) < 1e-12) return null;
  const origin = new Vector3().setFromMatrixPosition(basis);
  // A plane normal maps through the inverse transpose of the basis.
  const normal = new Vector3(0, 1, 0).applyMatrix3(new Matrix3().getNormalMatrix(basis)).normalize();
  const candidates = [...(meshes || [])].filter(Boolean);
  const worldNormal = new Matrix3();
  return () => {
    const mesh = candidates.find((candidate) => candidate.parent);
    if (!mesh) return null;
    mesh.updateWorldMatrix(true, false);
    const up = normal.clone().applyMatrix3(worldNormal.getNormalMatrix(mesh.matrixWorld)).normalize();
    const height = origin.clone().applyMatrix4(mesh.matrixWorld).y;
    return up.y >= MIN_LEVEL && Number.isFinite(height) ? height : null;
  };
}
