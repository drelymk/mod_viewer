// State captured by the texture-centric Save to Texture workflow.

import { viewerState, samePath } from '../app/state.js';
import { activeMeshes } from './mesh-state.js';
import {
  canEditMeshColor, getMeshColorAdjustment,
  flushMeshColorAdjustmentPersistence, persistCurrentMeshColorAdjustment,
  resetMeshColorAdjustment,
} from './mesh-color-session.js';
import { reloadTextures } from './mesh-factory.js';
import { notifyMeshStateChanged } from './mesh-state-events.js';
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
export function captureTextureSaveState(mesh, {includeTextureUsage = true} = {}) {
  return {
    modPath: viewerState.currentModPath,
    texKey: mesh?.userData?.texKey || null,
    targets: getTextureSaveTargets(mesh),
    ...(includeTextureUsage ? {textureUsage: buildTextureUsageSnapshot()} : {}),
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
function textureSaveTargetIdentityMatches(mesh, snapshot, target) {
  if (!snapshot || !target || !activeMeshes.includes(mesh)) return false;
  const data = mesh.userData || {};
  return viewerState.currentSource?.kind === 'mod'
    && samePath(snapshot.modPath, viewerState.currentModPath)
    && samePath(data.modPath, snapshot.modPath)
    && data.semanticKey === target.semanticKey
    && data.metadataKey === target.metadataKey
    && textureIdentity(data.texKey) === textureIdentity(snapshot.texKey);
}

/** Find the current replacement for a captured committed target. */
export function findCurrentTextureSaveTarget(snapshot, target) {
  if (!snapshot || !target) return null;
  return activeMeshes.find(mesh =>
    textureSaveTargetIdentityMatches(mesh, snapshot, target)) || null;
}

/** Check one committed target before clearing its live Color state. */
export function textureSaveTargetMatches(mesh, snapshot, target) {
  if (!textureSaveTargetIdentityMatches(mesh, snapshot, target)) return false;
  return JSON.stringify(publicTargets([targetState(mesh)]))
    === JSON.stringify(publicTargets([target]));
}

function affectedTextureKeys(result) {
  const reported = Array.isArray(result?.affected_tex_keys)
    && result.affected_tex_keys.length
    ? result.affected_tex_keys : [result?.tex_key];
  return [...new Set(reported.filter(key => typeof key === 'string' && key))];
}

async function synchronizeCommittedSave(state, result) {
  const sameLoadedMod = viewerState.currentSource?.kind === 'mod'
    && samePath(viewerState.currentModPath, state.modPath);
  if (!sameLoadedMod) return false;

  const affectedKeys = affectedTextureKeys(result);
  const saved = Array.isArray(result.saved_meshes) ? result.saved_meshes : [];
  const capturedTargets = new Map((state.targets || []).map(target => [
    `${target.semanticKey || ''}\u0000${target.metadataKey || ''}`, target,
  ]));
  const receipt = result.metadata_reset
    && typeof result.metadata_reset === 'object'
    ? result.metadata_reset : null;
  const receiptKeys = name => Array.isArray(receipt?.[name])
    ? receipt[name] : [];
  const fallbackStatus = result.warning === 'color_state_reset_failed'
    ? 'failed' : 'cleared';
  const statusFor = metadataKey => {
    if (!receipt) return fallbackStatus;
    if (receiptKeys('cleared').includes(metadataKey)) return 'cleared';
    if (receiptKeys('preserved').includes(metadataKey)) return 'preserved';
    if (receiptKeys('failed').includes(metadataKey)) return 'failed';
    return fallbackStatus;
  };
  const records = saved.map(item => {
    const semanticKey = item?.semantic_key;
    const metadataKey = item?.metadata_key;
    const target = capturedTargets.get(
      `${semanticKey || ''}\u0000${metadataKey || ''}`);
    return {
      target,
      metadataKey,
      status: target ? statusFor(metadataKey) : 'failed',
    };
  });
  const changedMeshes = new Set();
  let unresolvedFailedTargets = 0;

  for (const record of records) {
    if (record.status === 'preserved') continue;
    if (record.status === 'failed' && !record.target) {
      unresolvedFailedTargets += 1;
      continue;
    }
    const mesh = findCurrentTextureSaveTarget(state, record.target);
    if (!mesh) {
      if (record.status === 'failed') unresolvedFailedTargets += 1;
      continue;
    }
    if (record.status === 'cleared') {
      if (textureSaveTargetMatches(mesh, state, record.target)) {
        resetMeshColorAdjustment(mesh, {persist: false, render: false});
        changedMeshes.add(mesh);
      } else {
        try {
          persistCurrentMeshColorAdjustment(mesh);
          await flushMeshColorAdjustmentPersistence(mesh);
        } catch (_metadataError) {
          unresolvedFailedTargets += 1;
        }
      }
      continue;
    }

    try {
      if (textureSaveTargetMatches(mesh, state, record.target)) {
        resetMeshColorAdjustment(mesh, {persist: true, render: false});
        changedMeshes.add(mesh);
      } else {
        persistCurrentMeshColorAdjustment(mesh);
      }
      await flushMeshColorAdjustmentPersistence(mesh);
    } catch (_metadataError) {
      unresolvedFailedTargets += 1;
    }
  }

  if (unresolvedFailedTargets
      && (!result.warning || result.warning === 'color_state_reset_failed')) {
    result.warning = 'color_state_reset_failed';
  } else if (result.warning === 'color_state_reset_failed') {
    delete result.warning;
  }
  await reloadTextures(affectedKeys, {force: true});
  if (changedMeshes.size) notifyMeshStateChanged([...changedMeshes]);
  window.dispatchEvent(new CustomEvent('mod-viewer-texture-saved', {
    detail: {
      texKey: result.tex_key,
      affectedTexKeys: affectedKeys,
      savedMeshes: saved,
    },
  }));
  return true;
}

/** Create the stateful confirmation and commit workflow for Save to Texture. */
export function createTextureSaveSession({
  onPrompt = () => {},
  onError = () => {},
  onProgress = () => {},
  onRefreshing = () => {},
  onSuccess = () => {},
  onClose = () => {},
} = {}) {
  let pendingSave = null;
  let saving = false;
  let saveRequestSequence = 0;
  let activeSaveRequestId = null;

  function close() {
    if (saving) return false;
    activeSaveRequestId = null;
    pendingSave = null;
    return true;
  }

  function open(mesh, {isCurrent} = {}) {
    activeSaveRequestId = null;
    const state = captureTextureSaveState(mesh, {includeTextureUsage: false});
    pendingSave = {mesh, isCurrent, state};
    return state;
  }

  function handleProgress(event) {
    const detail = event?.detail;
    if (!saving || activeSaveRequestId === null
        || detail?.request_id !== activeSaveRequestId) return;
    onProgress(detail);
  }

  async function runSave(job) {
    const api = window.pywebview?.api?.save_texture_color;
    if (typeof api !== 'function') {
      onError({status: 'error', error: 'Texture saving is unavailable.'});
      return null;
    }
    saving = true;
    const requestId = String(++saveRequestSequence);
    activeSaveRequestId = requestId;
    onProgress({stage: 'preparing'});

    try {
      await Promise.all((job.state.targets || []).map(target =>
        flushMeshColorAdjustmentPersistence(target.mesh)));
    } catch (_persistenceError) {
      saving = false;
      activeSaveRequestId = null;
      onError({
        status: 'error',
        error: 'The pending Color metadata could not be saved. '
          + 'Texture saving was cancelled.',
      });
      return null;
    }
    const currentState = captureTextureSaveState(
      job.mesh, {includeTextureUsage: false});
    if ((typeof job.isCurrent === 'function' && !job.isCurrent())
        || !textureSaveStateMatches(job.mesh, job.state, currentState)) {
      saving = false;
      activeSaveRequestId = null;
      pendingSave = {mesh: job.mesh, isCurrent: job.isCurrent, state: currentState};
      onPrompt(currentState);
      return null;
    }
    job.state = {
      ...currentState,
      textureUsage: buildTextureUsageSnapshot(),
    };

    let result;
    try {
      result = await api(
        job.state.modPath, job.state.texKey,
        textureSaveTargetsPayload(job.state), job.state.textureUsage,
        requestId);
    } catch (_requestError) {
      result = {status: 'error', error: 'Texture save failed.'};
    }
    if (result?.status !== 'ok') {
      saving = false;
      activeSaveRequestId = null;
      onError(result);
      return result;
    }
    onRefreshing();
    try {
      await synchronizeCommittedSave(job.state, result);
    } catch (_refreshError) {
      onError({
        status: 'error',
        error: 'Texture saved, but the viewer could not refresh it.',
      });
      return result;
    } finally {
      saving = false;
      activeSaveRequestId = null;
    }
    if (typeof job.isCurrent === 'function' && !job.isCurrent()) {
      onClose();
      return result;
    }
    onSuccess(result, job.state.targets.length);
    return result;
  }

  async function submit() {
    if (!pendingSave || saving) return null;
    const job = pendingSave;
    pendingSave = null;
    return runSave(job);
  }

  return {
    close,
    handleProgress,
    isSaving: () => saving,
    open,
    submit,
  };
}
