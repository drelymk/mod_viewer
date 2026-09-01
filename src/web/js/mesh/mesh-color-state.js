// Viewer-owned per-mesh diffuse color adjustment state.

import { isAssetTextureKey, splitTextureKey } from '../textures/texture-key.js';
import { setGameMaterialColorAdjustment } from './material-profile.js';
import {
  DEFAULT_COLOR_ADJUSTMENT, isNeutralColorAdjustment,
  normalizeColorAdjustment,
} from './color-adjustment.js';
import { requestRender } from '../scene/render-scheduler.js';

const persistenceTails = new WeakMap();

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

function persistMeshColorAdjustment(mesh, adjustment = getMeshColorAdjustment(mesh)) {
  const path = mesh?.userData?.modPath;
  const key = mesh?.userData?.metadataKey;
  const save = window.pywebview?.api?.save_mesh_color_adjustment;
  if (!path || !key || typeof save !== 'function') return null;
  const value = persistenceValue(adjustment);
  const previous = persistenceTails.get(mesh) || Promise.resolve();
  const request = previous
    .catch(() => {})
    .then(() => save(path, key, value));
  const settled = request.catch(() => {});
  persistenceTails.set(mesh, settled);
  return request;
}

/** Wait until all queued color writes for this mesh have settled. */
export function flushMeshColorAdjustmentPersistence(mesh) {
  return (persistenceTails.get(mesh) || Promise.resolve()).catch(() => {});
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
  const eligibility = canEditMeshColor(mesh);
  if (persist && eligibility.editable) {
    // Capture the normalized value before another UI event can mutate the
    // mesh while the serialized persistence queue is waiting.
    persistMeshColorAdjustment(mesh, normalized);
  }
  return normalized;
}

export function resetMeshColorAdjustment(mesh, options = {}) {
  return setMeshColorAdjustment(mesh, DEFAULT_COLOR_ADJUSTMENT, options);
}
