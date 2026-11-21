import { Matrix3 } from 'three';
import { t } from '../i18n/index.js';
import { isGameRigSkeleton } from './game-rig.js';
import { getLooseParts } from '../mesh/loose-parts.js';
import { bindAnimationMorphs } from './animation-morphs.js';

// Used IDs of a loaded mesh resolved to joints of its game skeleton, or null for an
// invalid palette. IDs the proven palette lacks get no transform and stay at rest.
function gameBoneMapping(state) {
  const rig = state?.gameRig;
  if (!state?.loaded || !rig?.bones?.length || !state.boneIds?.length) return null;
  const mapping = new Map();
  for (const bone of state.boneIds) {
    if (!Object.hasOwn(rig.boneMap || {}, bone)) continue;
    const target = rig.boneMap[bone];
    if (!Number.isInteger(target) || target < 0 || target >= rig.bones.length) return null;
    mapping.set(bone, target);
  }
  return mapping;
}

// Playback bindings stay per mesh; they never promote inferred model joints to game bones.
export function buildAnimationMeshBinding(meshes, states) {
  const candidates = [...meshes].filter(
    (mesh) => states.get(mesh)?.gameRig?.animations?.length && gameBoneMapping(states.get(mesh)),
  );
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
    if (state?.gameRig?.id !== rig.id || !sameSkeleton(state.gameRig.bones)) continue;
    const mapping = gameBoneMapping(state);
    if (mapping) members.set(mesh, mapping);
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
  let morphSource = null;
  const morphStates = new Set();
  function setMorphs(binding, morphs) {
    if (morphSource === morphs) return;
    for (const state of morphStates) state.animationMorph = null;
    morphStates.clear();
    morphSource = morphs;
    for (const mesh of binding?.meshes.keys() || []) {
      const state = states.get(mesh);
      state.animationMorph = bindAnimationMorphs(state, morphs);
      if (state.animationMorph) morphStates.add(state);
    }
  }
  function prepare(binding, { deform = false } = {}) {
    if (!binding) return;
    participants.clear();
    for (const [mesh, mapping] of binding.meshes) {
      if (!deform && !mesh.userData?.assetFill) continue;
      participants.set(mesh, {
        state: states.get(mesh),
        mapping,
        vertices: null,
        transforms: new Map(),
        rotations: new Map(),
      });
    }
    setVisibility(new Set(binding.meshes.keys()));
  }
  function apply(transforms, { finalize = false } = {}) {
    for (const [bone, matrix] of transforms) {
      if (!rotations.has(bone)) rotations.set(bone, new Matrix3());
      rotations.get(bone).getNormalMatrix(matrix);
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
    for (const state of morphStates) state.animationMorph = null;
    morphStates.clear();
    morphSource = null;
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
  return { prepare, apply, stop, setMorphs };
}
