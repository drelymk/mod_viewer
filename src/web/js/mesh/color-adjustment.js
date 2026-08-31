// Pure, shared schema helpers for viewer-owned mesh color adjustments.

export const DEFAULT_COLOR_ADJUSTMENT = Object.freeze({
  hue: 0,
  saturation: 1,
  brightness: 1,
  contrast: 1,
  red: 1,
  green: 1,
  blue: 1,
  tint: '#ffffff',
  tintStrength: 0,
});

const COLOR_RANGES = Object.freeze({
  hue: [-180, 180],
  saturation: [0, 2],
  brightness: [0, 2],
  contrast: [0, 2],
  red: [0, 2],
  green: [0, 2],
  blue: [0, 2],
  tintStrength: [0, 1],
});

const TINT_PATTERN = /^#[0-9a-f]{6}$/i;

function finiteNumber(value, fallback) {
  return typeof value === 'number' && Number.isFinite(value)
    ? value : fallback;
}

function clamp(value, [minimum, maximum]) {
  return Math.min(maximum, Math.max(minimum, value));
}

function tintValue(value) {
  return typeof value === 'string' && TINT_PATTERN.test(value)
    ? value.toLowerCase() : DEFAULT_COLOR_ADJUSTMENT.tint;
}

/** Normalize frontend or backend-shaped state to the canonical JS shape. */
export function normalizeColorAdjustment(value) {
  const source = value && typeof value === 'object' ? value : {};
  const read = (name, legacyName = name) =>
    source[name] ?? source[legacyName];
  return {
    hue: clamp(finiteNumber(read('hue'), DEFAULT_COLOR_ADJUSTMENT.hue),
      COLOR_RANGES.hue),
    saturation: clamp(
      finiteNumber(read('saturation'), DEFAULT_COLOR_ADJUSTMENT.saturation),
      COLOR_RANGES.saturation),
    brightness: clamp(
      finiteNumber(read('brightness'), DEFAULT_COLOR_ADJUSTMENT.brightness),
      COLOR_RANGES.brightness),
    contrast: clamp(
      finiteNumber(read('contrast'), DEFAULT_COLOR_ADJUSTMENT.contrast),
      COLOR_RANGES.contrast),
    red: clamp(finiteNumber(read('red'), DEFAULT_COLOR_ADJUSTMENT.red),
      COLOR_RANGES.red),
    green: clamp(
      finiteNumber(read('green'), DEFAULT_COLOR_ADJUSTMENT.green),
      COLOR_RANGES.green),
    blue: clamp(
      finiteNumber(read('blue'), DEFAULT_COLOR_ADJUSTMENT.blue),
      COLOR_RANGES.blue),
    tint: tintValue(read('tint')),
    tintStrength: clamp(
      finiteNumber(read('tintStrength', 'tint_strength'),
        DEFAULT_COLOR_ADJUSTMENT.tintStrength), COLOR_RANGES.tintStrength),
  };
}

export function isNeutralColorAdjustment(value) {
  const adjustment = normalizeColorAdjustment(value);
  return adjustment.hue === 0
    && adjustment.saturation === 1
    && adjustment.brightness === 1
    && adjustment.contrast === 1
    && adjustment.red === 1
    && adjustment.green === 1
    && adjustment.blue === 1
    && adjustment.tint === '#ffffff'
    && adjustment.tintStrength === 0;
}

/** Parse picker sRGB bytes without Three.js color-management conversion. */
export function tintRgbFromHex(value) {
  const tint = tintValue(value);
  return [
    parseInt(tint.slice(1, 3), 16) / 255,
    parseInt(tint.slice(3, 5), 16) / 255,
    parseInt(tint.slice(5, 7), 16) / 255,
  ];
}

/** Format a raw sRGB vector back to the color-picker representation. */
export function tintHexFromRgb(value) {
  if (!value) return DEFAULT_COLOR_ADJUSTMENT.tint;
  const channels = [value.x, value.y, value.z].map(channel =>
    Number.isFinite(channel) ? Math.min(1, Math.max(0, channel)) : null);
  if (channels.some(channel => channel === null)) {
    return DEFAULT_COLOR_ADJUSTMENT.tint;
  }
  return `#${channels.map(channel => Math.round(channel * 255)
    .toString(16).padStart(2, '0')).join('')}`;
}
