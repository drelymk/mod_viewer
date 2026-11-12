import { Matrix4, Quaternion, Vector3 } from 'three';

// The bind pose is constant per rig; compute its inverse world matrices once.
const bindCache = new WeakMap();
const translationA = new Vector3();
const translationB = new Vector3();
const rotationA = new Quaternion();
const rotationB = new Quaternion();

function bindPose(rig) {
  let cached = bindCache.get(rig);
  if (!cached) {
    const worlds = [];
    const scales = rig.bones.map((bone) => new Vector3(...(bone.scale || [1, 1, 1])));
    rig.bones.forEach((bone, index) => {
      const world = new Matrix4().compose(
        new Vector3(...bone.translation),
        new Quaternion(...bone.rotation),
        scales[index],
      );
      if (bone.parent >= 0) world.premultiply(worlds[bone.parent]);
      worlds.push(world);
    });
    cached = { scales, inverse: worlds.map((world) => world.invert()), worlds: rig.bones.map(() => new Matrix4()) };
    bindCache.set(rig, cached);
  }
  return cached;
}

export function sampleAnimation(rig, clip, values, time) {
  const position = Math.max(0, Math.min(1, time / clip.duration)) * (clip.frame_count - 1);
  const first = Math.floor(position);
  const second = Math.min(first + 1, clip.frame_count - 1);
  const alpha = position - first;
  const count = rig.bones.length;
  const { scales, inverse, worlds } = bindPose(rig);
  const result = new Map();
  for (let index = 0; index < count; index++) {
    const a = (first * count + index) * 7;
    const b = (second * count + index) * 7;
    translationA.fromArray(values, a).lerp(translationB.fromArray(values, b), alpha);
    rotationA.fromArray(values, a + 3).slerp(rotationB.fromArray(values, b + 3), alpha);
    const world = worlds[index].compose(translationA, rotationA, scales[index]);
    const parent = rig.bones[index].parent;
    if (parent >= 0) world.premultiply(worlds[parent]);
    result.set(index, new Matrix4().multiplyMatrices(world, inverse[index]));
  }
  return result;
}

export function createRigAnimation({ getRig, loadClip, applyPose, suspendPhysics, prepare, notify }) {
  let generation = 0;
  let selected = null;
  let clip = null;
  let values = null;
  let playing = false;
  let loading = false;
  let error = null;
  let time = 0;
  let frame = null;
  let last = 0;
  const snapshot = () => ({
    clips: getRig()?.animations || [],
    selected,
    playing,
    loading,
    error,
    time,
    duration: clip?.duration || 0,
  });
  const changed = () => notify(snapshot());
  const pose = () => {
    if (clip && values && getRig()?.id === clip.skeleton_id)
      applyPose(sampleAnimation(getRig(), clip, values, time), { finalize: !playing });
  };
  function cancel() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  }
  function tick(now) {
    frame = null;
    if (!playing || !clip) return;
    // Allow display-timing jitter so a 60 Hz display steps every second frame.
    if (now - last >= 1000 / 30 - 2) {
      time = (time + (now - last) / 1000) % clip.duration;
      last = now;
      pose();
      changed();
    }
    frame = requestAnimationFrame(tick);
  }
  function setPlaying(value) {
    playing = !!value && !!clip;
    cancel();
    if (playing) {
      last = performance.now();
      frame = requestAnimationFrame(tick);
    } else pose();
    changed();
  }
  function stop() {
    generation += 1;
    cancel();
    playing = false;
    loading = false;
    selected = null;
    clip = null;
    values = null;
    time = 0;
    error = null;
    applyPose(null);
    suspendPhysics(false);
    changed();
  }
  async function select(id) {
    stop();
    const rig = getRig();
    if (!id || !rig?.animations?.some((entry) => entry.id === id)) return;
    const current = ++generation;
    selected = id;
    loading = true;
    changed();
    try {
      const response = await loadClip(rig.animation_session, id);
      if (current !== generation || getRig() !== rig) return;
      if (response?.status !== 'ok') throw new Error(response?.error || 'Animation is unavailable.');
      const metadata = response.clip;
      if (metadata.skeleton_id !== rig.id || metadata.bone_count !== rig.bones.length)
        throw new Error('Animation does not match the loaded rig.');
      const download = await fetch(metadata.data.url, { cache: 'no-store' });
      if (!download.ok) throw new Error('Animation download failed.');
      const buffer = await download.arrayBuffer();
      if (current !== generation || getRig() !== rig) return;
      if (
        buffer.byteLength !== metadata.data.length ||
        buffer.byteLength !== metadata.frame_count * rig.bones.length * 28
      )
        throw new Error('Animation download is incomplete.');
      clip = metadata;
      values = new Float32Array(buffer);
      loading = false;
      prepare();
      suspendPhysics(true);
      pose();
      setPlaying(true);
    } catch (failure) {
      if (current !== generation) return;
      loading = false;
      error = failure.message;
      changed();
    }
  }
  return {
    snapshot,
    select,
    stop,
    setPlaying,
    seek(value) {
      if (!clip) return;
      time = Math.max(0, Math.min(clip.duration, Number(value) || 0));
      last = performance.now();
      pose();
      changed();
    },
  };
}
