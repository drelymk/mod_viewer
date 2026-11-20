import { Matrix4, Quaternion, Vector3 } from 'three';
import { getLocale, t } from '../i18n/index.js';
import { mergeClipMorphs, sampleMergedMorphWeights, sampleMorphWeights } from './animation-morphs.js';
import { createIdleBlink, sampleIdleBlink } from './idle-blink.js';

// Every clip picker shows the same locale-sorted order after the Rest option;
// authored sequences follow in their own group. Returns the combined order.
export function fillRigClipSelect(select, clips, sequences = []) {
  const locale = getLocale();
  const order = (items) =>
    [...items].sort((left, right) =>
      left.name.localeCompare(right.name, locale, { numeric: true, sensitivity: 'base' }),
    );
  const sorted = order(clips);
  const sortedSequences = order(sequences);
  select.replaceChildren(new Option(t('weightRig.animationRest'), ''));
  sorted.forEach((clip) => select.add(new Option(clip.name, clip.id)));
  if (sortedSequences.length) {
    const group = document.createElement('optgroup');
    group.label = t('weightRig.animationSequences');
    sortedSequences.forEach((sequence) => group.appendChild(new Option(sequence.name, sequence.id)));
    select.appendChild(group);
  }
  return [...sorted, ...sortedSequences];
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
const LOCAL_WIDTH = 10;
const NO_SEQUENCES = Object.freeze([]);
// Decoded clip data a selected sequence may keep resident (a six-step combo is
// about 17 MiB).
const SEQUENCE_CACHE_BYTES = 128 * 1024 * 1024;

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
    const size = rig.bones.length * LOCAL_WIDTH;
    cached = {
      scales,
      inverse: worlds.map((world) => world.invert()),
      worlds: rig.bones.map(() => new Matrix4()),
      // Local-pose scratch: two sampled poses and their blend, kept in double
      // precision so a sampled pose matches direct composition exactly.
      locals: [new Float64Array(size), new Float64Array(size), new Float64Array(size)],
    };
    bindCache.set(rig, cached);
  }
  return cached;
}

// Local translation, rotation and scale per bone (10 numbers) at a clip time.
export function sampleLocalPose(rig, clip, values, time, out) {
  const position = Math.max(0, Math.min(1, time / clip.duration)) * (clip.frame_count - 1);
  const first = Math.floor(position);
  const second = Math.min(first + 1, clip.frame_count - 1);
  const alpha = position - first;
  const count = rig.bones.length;
  const width = clip.layout === 'frame-major-tqs-f32le' ? 10 : 7;
  const { scales } = bindPose(rig);
  for (let index = 0; index < count; index++) {
    const a = (first * count + index) * width;
    const b = (second * count + index) * width;
    const target = index * LOCAL_WIDTH;
    translationA.fromArray(values, a).lerp(translationB.fromArray(values, b), alpha).toArray(out, target);
    rotationA
      .fromArray(values, a + 3)
      .slerp(rotationB.fromArray(values, b + 3), alpha)
      .toArray(out, target + 3);
    const scale =
      width === 10 ? scaleA.fromArray(values, a + 7).lerp(scaleB.fromArray(values, b + 7), alpha) : scales[index];
    scale.toArray(out, target + 7);
  }
  return out;
}

// Crossfade two local poses; rotations take the shortest path.
export function blendLocalPose(from, to, weight, out) {
  for (let target = 0; target < out.length; target += LOCAL_WIDTH) {
    translationA.fromArray(from, target).lerp(translationB.fromArray(to, target), weight).toArray(out, target);
    rotationA
      .fromArray(from, target + 3)
      .slerp(rotationB.fromArray(to, target + 3), weight)
      .toArray(out, target + 3);
    scaleA
      .fromArray(from, target + 7)
      .lerp(scaleB.fromArray(to, target + 7), weight)
      .toArray(out, target + 7);
  }
  return out;
}

// Animated world times inverse rest world, per bone.
export function composePose(rig, locals) {
  const { inverse, worlds } = bindPose(rig);
  const result = new Map();
  for (let index = 0; index < rig.bones.length; index++) {
    const target = index * LOCAL_WIDTH;
    const world = worlds[index].compose(
      translationA.fromArray(locals, target),
      rotationA.fromArray(locals, target + 3),
      scaleA.fromArray(locals, target + 7),
    );
    const parent = rig.bones[index].parent;
    if (parent >= 0) world.premultiply(worlds[parent]);
    result.set(index, new Matrix4().multiplyMatrices(world, inverse[index]));
  }
  return result;
}

export function sampleAnimation(rig, clip, values, time) {
  return composePose(rig, sampleLocalPose(rig, clip, values, time, bindPose(rig).locals[0]));
}

export function createRigAnimation({
  getRig,
  loadClip,
  applyPose,
  suspendPhysics,
  prepare,
  notify,
  sequenceCacheBytes = SEQUENCE_CACHE_BYTES,
}) {
  let generation = 0;
  let selected = null;
  let clip = null;
  let values = null;
  let playing = false;
  let loading = false;
  let error = null;
  let errorKey = null;
  let time = 0;
  // Clips without facial curves borrow the library's eye-closing shapes. The idle
  // clock runs across loops so short clips still blink.
  let idle = null;
  let idleTime = 0;
  let frame = null;
  let last = 0;
  // A sequence downloads its distinct step clips once, within a byte budget, so
  // step changes never wait for a download; combo steps can leave a fraction of a
  // second after they start. Steps beyond the budget load one handoff ahead.
  let sequence = null;
  let cache = null;
  // With every step clip cached, one merged facial source stays bound for the
  // whole sequence so step changes never rebind mesh morphs.
  let faces = null;
  let step = 0;
  let next = null;
  let outgoing = null;
  let fadeElapsed = 0;
  let fadeDuration = 0;
  // A stable empty list keeps pickers from rebuilding on every tick.
  const sequences = () => getRig()?.animation_sequences || NO_SEQUENCES;
  const snapshot = () => ({
    clips: getRig()?.animations || [],
    sequences: sequences(),
    selected,
    playing,
    loading,
    error,
    errorKey,
    time,
    duration: clip?.duration || 0,
    step: sequence ? step : null,
    stepCount: sequence ? sequence.steps.length : 0,
  });
  const changed = () => notify(snapshot());
  const pose = ({ finalize = !playing } = {}) => {
    const rig = getRig();
    if (!clip || !values || rig?.id !== clip.skeleton_id) return;
    let transforms;
    let morphs;
    if (outgoing && fadeDuration > 0) {
      const weight = Math.min(1, fadeElapsed / fadeDuration);
      const [from, to, blended] = bindPose(rig).locals;
      sampleLocalPose(rig, outgoing.clip, outgoing.values, outgoing.time, from);
      sampleLocalPose(rig, clip, values, time, to);
      transforms = composePose(rig, blendLocalPose(from, to, weight, blended));
      if (faces) {
        morphs = sampleMergedMorphWeights(faces, [
          [outgoing.clip, outgoing.values, outgoing.time, 1 - weight],
          [clip, values, time, weight],
        ]);
      } else {
        // Without a merged source each clip keeps its own sparse targets, so
        // facial weights switch halfway.
        morphs =
          weight < 0.5
            ? sampleMorphWeights(outgoing.clip, outgoing.values, outgoing.time)
            : sampleMorphWeights(clip, values, time);
      }
    } else {
      transforms = sampleAnimation(rig, clip, values, time);
      morphs = faces
        ? sampleMergedMorphWeights(faces, [[clip, values, time, 1]])
        : sampleMorphWeights(clip, values, time);
    }
    applyPose(transforms, { finalize, morphs: morphs || sampleIdleBlink(idle, idleTime, playing) });
  };
  function cancel() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  }
  function clearSequence() {
    sequence = null;
    cache = null;
    faces = null;
    step = 0;
    next = null;
    outgoing = null;
    fadeElapsed = 0;
    fadeDuration = 0;
  }
  function fail(failure, current) {
    if (current !== generation) return;
    cancel();
    playing = false;
    clip = null;
    values = null;
    idle = null;
    time = 0;
    clearSequence();
    applyPose(null);
    suspendPhysics(false);
    loading = false;
    error = failure.message;
    errorKey = failure.translationKey || null;
    changed();
  }
  // Load the step after the active one in the background.
  function preload(current) {
    const index = (step + 1) % sequence.steps.length;
    const id = sequence.steps[index].clip;
    if (id === clip.id || cache.has(id)) {
      next = cache.get(id) || { clip, values };
      return;
    }
    const rig = getRig();
    loadEntry(rig, id, current).then(
      (entry) => {
        if (entry && current === generation && sequence) next = entry;
      },
      (failure) => fail(failure, current),
    );
  }
  function beginStep(current) {
    const leaving = sequence.steps[step];
    const index = (step + 1) % sequence.steps.length;
    outgoing = leaving.fade > 0 ? { clip, values, time: leaving.leave } : null;
    fadeElapsed = 0;
    fadeDuration = leaving.fade;
    ({ clip, values } = next);
    next = null;
    step = index;
    time = index === 0 && sequence.cyclic ? sequence.restart : sequence.steps[index].start;
    if (!outgoing) preload(current);
  }
  function advanceSequence(elapsed) {
    const current = generation;
    if (outgoing) {
      outgoing.time = Math.min(outgoing.time + elapsed, outgoing.clip.duration);
      time = Math.min(time + elapsed, clip.duration);
      fadeElapsed += elapsed;
      if (fadeElapsed >= fadeDuration) {
        outgoing = null;
        preload(current);
      }
      return;
    }
    const leave = sequence.steps[step].leave;
    time = Math.min(time + elapsed, leave);
    // Hold the leaving pose until the next step has downloaded.
    if (time >= leave && next) beginStep(current);
  }
  function tick(now) {
    frame = null;
    if (!playing || !clip) return;
    // Allow display-timing jitter so a 60 Hz display steps every frame.
    if (now - last >= 1000 / 60 - 2) {
      const elapsed = (now - last) / 1000;
      if (sequence) advanceSequence(elapsed);
      else time = (time + elapsed) % clip.duration;
      idleTime += elapsed;
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
    idle = null;
    time = 0;
    idleTime = 0;
    error = null;
    errorKey = null;
    clearSequence();
    applyPose(null);
    suspendPhysics(false);
    changed();
  }
  // Download and validate one catalog clip; null when the selection went stale.
  async function loadEntry(rig, id, current) {
    const response = await loadClip(rig.animation_session, id);
    if (current !== generation || getRig() !== rig) return null;
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
    if (current !== generation || getRig() !== rig) return null;
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
      if (current !== generation || getRig() !== rig) return null;
      if (targets.byteLength !== metadata.morphs.data.length) throw animationError('animation.downloadIncomplete');
      metadata.morphs.buffer = targets;
      metadata.morphs.weights = new Float32Array(metadata.morphs.names.length);
    }
    let blink = null;
    if (!metadata.morphs && metadata.idle_morphs) {
      // Shared eye shapes are optional; a failed download keeps the clip playable.
      try {
        const shapes = await fetch(metadata.idle_morphs.data.url, { cache: 'no-store' });
        const targets = shapes.ok ? await shapes.arrayBuffer() : null;
        if (targets?.byteLength === metadata.idle_morphs.data.length) {
          blink = createIdleBlink(metadata.idle_morphs, targets);
        }
      } catch {
        blink = null;
      }
      if (current !== generation || getRig() !== rig) return null;
    }
    return { clip: metadata, values: new Float32Array(buffer), blink };
  }
  // The first two steps are always held. Later distinct clips are kept while the
  // cached total stays within budget; downloads are sequential because each
  // backend load replaces the previous publication.
  async function loadSequence(rig, chosen, current) {
    const stored = new Map();
    const held = new Map();
    let bytes = 0;
    let full = false;
    for (const [number, entryStep] of chosen.steps.entries()) {
      let entry = held.get(entryStep.clip) || stored.get(entryStep.clip);
      if (!entry) {
        if (full && number > 1) continue;
        entry = await loadEntry(rig, entryStep.clip, current);
        if (!entry) return null;
      }
      if (number < 2) held.set(entryStep.clip, entry);
      if (!stored.has(entryStep.clip)) {
        const size = entry.values.byteLength + (entry.clip.morphs?.buffer?.byteLength || 0);
        if (!full && bytes + size <= sequenceCacheBytes) {
          stored.set(entryStep.clip, entry);
          bytes += size;
        } else full = true;
      }
    }
    return { first: held.get(chosen.steps[0].clip), second: held.get(chosen.steps[1].clip), stored };
  }
  async function select(id) {
    const rig = getRig();
    const chosen = id ? sequences().find((entry) => entry.id === id) || null : null;
    if (!id || (!chosen && !rig?.animations?.some((entry) => entry.id === id))) {
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
      let first;
      let second = null;
      let stored = null;
      if (chosen) {
        const loaded = await loadSequence(rig, chosen, current);
        if (!loaded) return;
        ({ first, second, stored } = loaded);
      } else {
        first = await loadEntry(rig, id, current);
        if (!first) return;
      }
      clearSequence();
      sequence = chosen;
      cache = stored;
      faces =
        chosen && chosen.steps.every((entryStep) => stored.has(entryStep.clip))
          ? mergeClipMorphs([...stored.values()].map((entry) => entry.clip))
          : null;
      next = second;
      clip = first.clip;
      values = first.values;
      idle = first.blink;
      time = chosen ? chosen.steps[0].start : 0;
      idleTime = 0;
      loading = false;
      prepare();
      suspendPhysics(true);
      pose({ finalize: false });
      setPlaying(true);
    } catch (failure) {
      fail(failure, current);
    }
  }
  return {
    snapshot,
    select,
    stop,
    setPlaying,
    seek(value) {
      // Sequence steps are loaded one handoff at a time, so they cannot be scrubbed.
      if (!clip || sequence) return;
      time = Math.max(0, Math.min(clip.duration, Number(value) || 0));
      last = performance.now();
      pose();
      changed();
    },
  };
}
