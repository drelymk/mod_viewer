import { Quaternion } from 'three';
import { t } from '../i18n/index.js';
import { isGameRigSkeleton } from './game-rig.js';
import { getLooseParts } from '../mesh/loose-parts.js';

// Playback bindings stay per mesh; they never promote inferred model joints to game bones.
export function buildAnimationMeshBinding(meshes, states) {
  const candidates = [...meshes].filter((mesh) => {
    const state = states.get(mesh);
    const rig = state?.gameRig;
    return (
      state?.loaded &&
      rig?.animations?.length &&
      rig.bones?.length &&
      state.boneIds?.length &&
      state.boneIds.every((bone) => {
        const target = rig.boneMap?.[bone];
        return Number.isInteger(target) && target >= 0 && target < rig.bones.length;
      })
    );
  });
  if (!candidates.length) return null;
  const rig = states.get(candidates[0]).gameRig;
  if (candidates.some((mesh) => states.get(mesh).gameRig.id !== rig.id)) return null;
  if (!isGameRigSkeleton(rig)) return null;
  // The backend shares one skeleton array across its per-mesh palettes.
  // Compare detached equivalents once per array, never once per draw or frame.
  const skeletons = new Map([[rig.bones, true]]);
  let bones = null;
  const sameSkeleton = (candidate) => {
    if (!skeletons.has(candidate)) {
      bones ??= JSON.stringify(rig.bones);
      skeletons.set(candidate, JSON.stringify(candidate) === bones);
    }
    return skeletons.get(candidate);
  };
  const members = new Map();
  for (const mesh of meshes) {
    const state = states.get(mesh);
    if (!state?.loaded || state.gameRig?.id !== rig.id || !sameSkeleton(state.gameRig.bones) || !state.boneIds?.length)
      continue;
    const mapping = new Map(state.boneIds.map((bone) => [bone, state.gameRig.boneMap?.[bone]]));
    if ([...mapping.values()].some((bone) => !Number.isInteger(bone) || bone < 0 || bone >= rig.bones.length)) continue;
    members.set(mesh, mapping);
  }
  return { rig, meshes: members };
}

export function createAnimationMeshSession({
  states,
  skinningRuntime,
  setVisibility,
  invalidateShadow,
  requestRender,
}) {
  const participants = new Map();
  const rotations = new Map();
  function prepare(binding, { deform = false } = {}) {
    if (!binding) return;
    if (deform) {
      for (const [mesh, mapping] of binding.meshes) {
        participants.set(mesh, {
          state: states.get(mesh),
          mapping,
          vertices: null,
          transforms: new Map(),
          rotations: new Map(),
        });
      }
    }
    setVisibility(new Set(binding.meshes.keys()));
  }
  function apply(transforms, { finalize = false } = {}) {
    for (const [bone, matrix] of transforms) {
      if (!rotations.has(bone)) rotations.set(bone, new Quaternion());
      rotations.get(bone).setFromRotationMatrix(matrix).normalize();
    }
    let changed = false;
    for (const [mesh, participant] of participants) {
      if (!finalize && !mesh.visible && !getLooseParts(mesh).some((part) => part.visible)) continue;
      const { state, mapping } = participant;
      if (!participant.vertices) {
        if (!skinningRuntime.ensureRigMeshPrepared(mesh, state))
          throw Object.assign(new Error(t('animation.skinUnavailable')), {
            translationKey: 'animation.skinUnavailable',
          });
        participant.vertices = Uint32Array.from(
          { length: mesh.geometry.attributes.position.count },
          (_, index) => index,
        );
      }
      for (const [bone, target] of mapping) {
        participant.transforms.set(bone, transforms.get(target));
        participant.rotations.set(bone, rotations.get(target));
      }
      state.poseTransforms = participant.transforms;
      state.poseRotations = participant.rotations;
      state.poseActiveVertices = participant.vertices;
      changed =
        skinningRuntime.applyDeformation(mesh, state, { request: false, invalidateShadow: false, gpu: true }) ||
        changed;
      if (finalize) skinningRuntime.finalizeDeformationGeometry(mesh, state);
    }
    if (changed) invalidateShadow({ request: false });
    requestRender();
  }
  function stop() {
    let changed = false;
    for (const [mesh, { state, vertices }] of participants) {
      if (!vertices) continue;
      state.poseTransforms = null;
      state.poseRotations = null;
      state.poseActiveVertices = new Uint32Array();
      changed = skinningRuntime.applyDeformation(mesh, state, { request: false, invalidateShadow: false }) || changed;
      skinningRuntime.finalizeDeformationGeometry(mesh, state);
    }
    participants.clear();
    rotations.clear();
    setVisibility(null);
    if (changed) invalidateShadow({ request: false });
    requestRender();
  }
  return { prepare, apply, stop };
}
