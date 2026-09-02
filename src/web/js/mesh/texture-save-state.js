// State captured by the texture-centric Save to Texture workflow.

import { viewerState, samePath } from '../app/state.js';
import { activeMeshes } from './mesh-state.js';
import {
  canEditMeshColor, getMeshColorAdjustment,
} from './mesh-color-state.js';
import { isNeutralColorAdjustment, normalizeColorAdjustment } from './color-adjustment.js';
import { isAssetTextureKey, splitTextureKey } from '../textures/texture-key.js';

function textureIdentity(key) {
  const parsed = splitTextureKey(key);
  if (!parsed || parsed.role !== 'diffuse' || !parsed.path) return null;
  const parts = [];
  for (const part of parsed.path.replaceAll('\\', '/').split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') parts.pop();
    else parts.push(part);
  }
  return `diffuse::${parts.join('/').toLowerCase()}`;
}

function copyAdjustment(adjustment) {
  const normalized = normalizeColorAdjustment(adjustment);
  return {
    hue: normalized.hue,
    saturation: normalized.saturation,
    brightness: normalized.brightness,
    contrast: normalized.contrast,
    red: normalized.red,
    green: normalized.green,
    blue: normalized.blue,
    tint: normalized.tint,
    tint_strength: normalized.tintStrength,
  };
}

function targetState(mesh) {
  const data = mesh?.userData || {};
  return {
    mesh,
    semanticKey: data.semanticKey || null,
    metadataKey: data.metadataKey || null,
    adjustment: copyAdjustment(getMeshColorAdjustment(mesh)),
  };
}

/** Return the complete model-wide role snapshot used by the backend. */
export function buildTextureUsageSnapshot() {
  return activeMeshes
    .filter(mesh => mesh?.userData?.assetFill !== true)
    .map(mesh => {
      const data = mesh.userData || {};
      const textureKeys = {
        diffuse: data.texKey || null,
        normal_map: data.normalMapKey || null,
        normal_data: data.normalDataKey || null,
        light_map: data.lightMapKey || null,
        material_map: data.materialMapKey || null,
        emission_map: data.emissionMapKey || null,
      };
      return {
        semantic_key: data.semanticKey || '',
        texture_keys: textureKeys,
      };
    });
}

/** Check whether a mesh can be used as the texture selected by Save. */
export function canSaveTexture(mesh) {
  const data = mesh?.userData || {};
  const key = data.texKey;
  const parsed = splitTextureKey(key);
  const editable = canEditMeshColor(mesh);
  if (data.assetFill === true || isAssetTextureKey(key)) {
    return { editable: false, reason: 'asset-texture', message: 'Asset textures are read-only.' };
  }
  if (!editable.editable || !parsed || parsed.role !== 'diffuse') {
    return { editable: false, reason: 'no-diffuse', message: 'Select a diffuse texture before saving.' };
  }
  if (!viewerState.currentModPath || !samePath(data.modPath, viewerState.currentModPath)) {
    return { editable: false, reason: 'different-mod', message: 'The selected mesh belongs to a different mod.' };
  }
  if (!parsed.path.toLowerCase().endsWith('.dds')) {
    return { editable: false, reason: 'unsupported-texture-type', message: 'Texture saving currently requires a DDS source.' };
  }
  return { editable: true, reason: null, message: '' };
}

/** Return every changed, color-editable mesh using the selected physical DDS. */
export function getTextureSaveTargets(mesh) {
  const eligibility = canSaveTexture(mesh);
  if (!eligibility.editable) return [];
  const selectedIdentity = textureIdentity(mesh.userData?.texKey);
  if (!selectedIdentity) return [];
  return activeMeshes
    .filter(candidate => {
      const data = candidate?.userData || {};
      if (data.assetFill === true
          || !samePath(data.modPath, viewerState.currentModPath)
          || textureIdentity(data.texKey) !== selectedIdentity
          || !canSaveTexture(candidate).editable
          || isNeutralColorAdjustment(getMeshColorAdjustment(candidate))) {
        return false;
      }
      return Boolean(data.semanticKey && data.metadataKey);
    })
    .map(targetState);
}

/** Capture texture identity, all changed target identities, and role usage. */
export function captureTextureSaveState(mesh) {
  return {
    modPath: viewerState.currentModPath,
    texKey: mesh?.userData?.texKey || null,
    targets: getTextureSaveTargets(mesh),
    textureUsage: buildTextureUsageSnapshot(),
  };
}

function publicTargets(targets) {
  return (targets || []).map(target => ({
    semantic_key: target.semanticKey,
    metadata_key: target.metadataKey,
    adjustment: copyAdjustment(target.adjustment),
  }));
}

/** Convert captured targets to the snake-case bridge request schema. */
export function textureSaveTargetsPayload(state) {
  return publicTargets(state?.targets);
}

function comparableState(state) {
  return {
    modPath: state?.modPath || null,
    texKey: state?.texKey || null,
    targets: publicTargets(state?.targets),
  };
}

/** Check that every identity and adjustment captured by the modal is current. */
export function textureSaveStateMatches(mesh, snapshot, current = null) {
  if (!snapshot || !activeMeshes.includes(mesh)) return false;
  const actual = current || captureTextureSaveState(mesh);
  return samePath(actual.modPath, snapshot.modPath)
    && JSON.stringify(comparableState(actual))
      === JSON.stringify(comparableState(snapshot));
}

/** Check one committed target before clearing its live Color state. */
export function textureSaveTargetMatches(mesh, snapshot, target) {
  if (!snapshot || !target || !activeMeshes.includes(mesh)) return false;
  const data = mesh.userData || {};
  if (!samePath(snapshot.modPath, viewerState.currentModPath)
      || !samePath(data.modPath, snapshot.modPath)
      || data.semanticKey !== target.semanticKey
      || data.metadataKey !== target.metadataKey
      || textureIdentity(data.texKey) !== textureIdentity(snapshot.texKey)) {
    return false;
  }
  return JSON.stringify(publicTargets([targetState(mesh)]))
    === JSON.stringify(publicTargets([target]));
}
