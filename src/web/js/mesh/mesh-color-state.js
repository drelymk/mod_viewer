// Viewer-owned per-mesh diffuse color adjustment state.

import { isAssetTextureKey, splitTextureKey } from '../textures/texture-key.js';
import { setGameMaterialColorAdjustment } from './material-profile.js';
import { requestRender } from '../scene/render-scheduler.js';

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
    blue: clamp(finiteNumber(read('blue'), DEFAULT_COLOR_ADJUSTMENT.blue),
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

export function getMeshColorAdjustment(mesh) {
  return normalizeColorAdjustment(mesh?.userData?.colorAdjustment);
}

function persistenceValue(adjustment) {
  return {
    hue: adjustment.hue,
    saturation: adjustment.saturation,
    brightness: adjustment.brightness,
    contrast: adjustment.contrast,
    red: adjustment.red,
    green: adjustment.green,
    blue: adjustment.blue,
    tint: adjustment.tint,
    tint_strength: adjustment.tintStrength,
  };
}

function persistMeshColorAdjustment(mesh) {
  const path = mesh?.userData?.modPath;
  const key = mesh?.userData?.metadataKey;
  const save = window.pywebview?.api?.save_mesh_color_adjustment;
  if (!path || !key || typeof save !== 'function') return null;
  const adjustment = getMeshColorAdjustment(mesh);
  const request = save(path, key, persistenceValue(adjustment));
  if (request && typeof request.catch === 'function') request.catch(() => {});
  return request;
}

/** Return the active diffuse editability and the reason when it is blocked. */
export function canEditMeshColor(mesh) {
  const key = mesh?.userData?.texKey;
  const parsed = splitTextureKey(key);
  if (!parsed || parsed.role !== 'diffuse') {
    return { editable: false, reason: 'no-diffuse' };
  }
  if (isAssetTextureKey(key)) {
    return { editable: false, reason: 'asset-texture' };
  }
  return { editable: true, reason: null };
}

export function syncMeshColorAdjustment(mesh, { render = true } = {}) {
  if (!mesh?.material) return false;
  const adjustment = getMeshColorAdjustment(mesh);
  const eligibility = canEditMeshColor(mesh);
  const changed = setGameMaterialColorAdjustment(mesh.material, adjustment, {
    enabled: eligibility.editable && !isNeutralColorAdjustment(adjustment),
  });
  if (render && (changed || mesh.visible)) requestRender();
  return changed;
}

export function setMeshColorAdjustment(mesh, adjustment, {
  render = true, persist = false, sync = true,
} = {}) {
  if (!mesh) return DEFAULT_COLOR_ADJUSTMENT;
  const normalized = normalizeColorAdjustment(adjustment);
  mesh.userData.colorAdjustment = normalized;
  if (sync) syncMeshColorAdjustment(mesh, { render });
  if (persist && canEditMeshColor(mesh)) persistMeshColorAdjustment(mesh);
  return normalized;
}

export function resetMeshColorAdjustment(mesh, options = {}) {
  return setMeshColorAdjustment(mesh, DEFAULT_COLOR_ADJUSTMENT, options);
}
