import { Matrix4, Quaternion, Vector3 } from 'three';
import { getLocale, t } from '../i18n/index.js';
import { sampleMorphWeights } from './animation-morphs.js';

// Every clip picker shows the same locale-sorted order after the Rest option.
export function fillRigClipSelect(select, clips) {
  const locale = getLocale();
  const sorted = [...clips].sort((left, right) =>
    left.name.localeCompare(right.name, locale, { numeric: true, sensitivity: 'base' }),
  );
  select.replaceChildren(new Option(t('weightRig.animationRest'), ''));
  sorted.forEach((clip) => select.add(new Option(clip.name, clip.id)));
  return sorted;
}

function animationError(key) {
  return Object.assign(new Error(t(key)), { translationKey: key });
}

// The bind pose is constant per rig; compute its inverse world matrices once.
const bindCache = new WeakMap();
const translationA = new Vector3();
const translationB = new Vector3();
const rotationA = new Quaternion();
const rotationB = new Quaternion();
const scaleA = new Vector3();
const scaleB = new Vector3();

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
  const width = clip.layout === 'frame-major-tqs-f32le' ? 10 : 7;
  const { scales, inverse, worlds } = bindPose(rig);
  const result = new Map();
  for (let index = 0; index < count; index++) {
    const a = (first * count + index) * width;
    const b = (second * count + index) * width;
    translationA.fromArray(values, a).lerp(translationB.fromArray(values, b), alpha);
    rotationA.fromArray(values, a + 3).slerp(rotationB.fromArray(values, b + 3), alpha);
    const scale =
      width === 10 ? scaleA.fromArray(values, a + 7).lerp(scaleB.fromArray(values, b + 7), alpha) : scales[index];
    const world = worlds[index].compose(translationA, rotationA, scale);
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
  let errorKey = null;
  let time = 0;
  let frame = null;
  let last = 0;
  const snapshot = () => ({
    clips: getRig()?.animations || [],
    selected,
    playing,
    loading,
    error,
    errorKey,
    time,
    duration: clip?.duration || 0,
  });
  const changed = () => notify(snapshot());
  const pose = ({ finalize = !playing } = {}) => {
    if (clip && values && getRig()?.id === clip.skeleton_id)
      applyPose(sampleAnimation(getRig(), clip, values, time), {
        finalize,
        morphs: sampleMorphWeights(clip, values, time),
      });
  };
  function cancel() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  }
  function tick(now) {
    frame = null;
    if (!playing || !clip) return;
    // Allow display-timing jitter so a 60 Hz display steps every frame.
    if (now - last >= 1000 / 60 - 2) {
      time = (time + (now - last) / 1000) % clip.duration;
      last = now;
      pose();
      changed();
    }
    frame = requestAnimationFrame(tick);
  }
  function setPlaying(value) {
    playing = !!value && !!clip && !loading;
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
    errorKey = null;
    applyPose(null);
    suspendPhysics(false);
    changed();
  }
  async function select(id) {
    const rig = getRig();
    if (!id || !rig?.animations?.some((entry) => entry.id === id)) {
      stop();
      return;
    }
    // Hold the displayed pose until the replacement is ready.
    cancel();
    playing = false;
    error = null;
    errorKey = null;
    const current = ++generation;
    selected = id;
    loading = true;
    changed();
    try {
      const response = await loadClip(rig.animation_session, id);
      if (current !== generation || getRig() !== rig) return;
      if (response?.status !== 'ok')
        throw response?.error ? new Error(response.error) : animationError('animation.unavailable');
      const metadata = response.clip;
      const width =
        metadata.layout === 'frame-major-tqs-f32le' && metadata.version === 2
          ? 10
          : (!metadata.layout || metadata.layout === 'frame-major-tq-f32le') &&
              (metadata.version === undefined || metadata.version === 1)
            ? 7
            : 0;
      if (!width) throw animationError('animation.rigMismatch');
      if (metadata.skeleton_id !== rig.id || metadata.bone_count !== rig.bones.length)
        throw animationError('animation.rigMismatch');
      const download = await fetch(metadata.data.url, { cache: 'no-store' });
      if (!download.ok) throw animationError('animation.downloadFailed');
      const buffer = await download.arrayBuffer();
      if (current !== generation || getRig() !== rig) return;
      if (
        buffer.byteLength !== metadata.data.length ||
        buffer.byteLength !==
          metadata.frame_count * (rig.bones.length * width * 4 + (metadata.morphs?.names.length || 0) * 4)
      )
        throw animationError('animation.downloadIncomplete');
      if (metadata.morphs) {
        const response = await fetch(metadata.morphs.data.url, { cache: 'no-store' });
        if (!response.ok) throw animationError('animation.downloadFailed');
        const targets = await response.arrayBuffer();
        if (current !== generation || getRig() !== rig) return;
        if (targets.byteLength !== metadata.morphs.data.length) throw animationError('animation.downloadIncomplete');
        metadata.morphs.buffer = targets;
        metadata.morphs.weights = new Float32Array(metadata.morphs.names.length);
      }
      clip = metadata;
      values = new Float32Array(buffer);
      time = 0;
      loading = false;
      prepare();
      suspendPhysics(true);
      pose({ finalize: false });
      setPlaying(true);
    } catch (failure) {
      if (current !== generation) return;
      cancel();
      playing = false;
      clip = null;
      values = null;
      time = 0;
      applyPose(null);
      suspendPhysics(false);
      loading = false;
      error = failure.message;
      errorKey = failure.translationKey || null;
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
