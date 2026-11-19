// Procedural blink for clips that carry no facial curves of their own.
//
// The backend names the library's eye-closing shapes; weight 1 is their full
// closure. Optional per-shape scales crossfade from a captured pose (Genshin
// eyes swap their default shape for the closed one). The schedule depends only
// on playback time, so it is deterministic.

const SLOT = 3.2;
const FIRST_BLINK = 0.6;
const SPREAD = 1.8;
const CLOSE = 0.07;
const HOLD = 0.04;
const OPEN = 0.14;
const DURATION = CLOSE + HOLD + OPEN;

const smooth = (value) => value * value * (3 - 2 * value);

// Stable pseudo-random value in [0, 1) for one blink slot.
function slotRandom(slot) {
  let hash = Math.imul(slot + 1, 0x9e3779b1);
  hash ^= hash >>> 15;
  hash = Math.imul(hash, 0x85ebca6b);
  hash ^= hash >>> 13;
  return (hash >>> 0) / 4294967296;
}

/** Eye closure (0 open, 1 closed) after `time` seconds of continuous playback. */
export function idleBlinkWeight(time) {
  if (!(time >= 0)) return 0;
  const slot = Math.floor(time / SLOT);
  const elapsed = time - slot * SLOT - (FIRST_BLINK + slotRandom(slot) * SPREAD);
  if (elapsed < 0 || elapsed >= DURATION) return 0;
  if (elapsed < CLOSE) return smooth(elapsed / CLOSE);
  if (elapsed < CLOSE + HOLD) return 1;
  return 1 - smooth((elapsed - CLOSE - HOLD) / OPEN);
}

/** Prepare a backend `idle_morphs` descriptor once its shared target buffer is loaded. */
export function createIdleBlink(descriptor, buffer) {
  const scales = Array.isArray(descriptor.scales) ? Float32Array.from(descriptor.scales) : null;
  if (scales && (scales.length !== descriptor.names.length || !scales.every(Number.isFinite))) return null;
  return {
    names: descriptor.names,
    components: descriptor.components,
    buffer,
    scales,
    weights: new Float32Array(descriptor.names.length),
  };
}

/** Write the blink weight into every eye-closing shape; paused playback keeps eyes open. */
export function sampleIdleBlink(idle, time, playing) {
  if (!idle) return null;
  const weight = playing ? idleBlinkWeight(time) : 0;
  if (!idle.scales) idle.weights.fill(weight);
  else idle.scales.forEach((scale, index) => (idle.weights[index] = weight * scale));
  return idle;
}
