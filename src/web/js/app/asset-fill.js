// Session-scoped Asset-fill transaction and its toolbar state.

import { viewerState, samePath } from './state.js';
import { adoptModelMeshes, fitTo } from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';
import { addTexture, removeTextures } from '../mesh/mesh-factory.js';
import { buildPayloadMeshes } from '../mesh/mesh-model-builder.js';
import { activeMeshes, removeMesh } from '../mesh/visibility.js';
import { appendMeshPanel, removeAssetFillMeshPanel } from '../panels/mesh-panel.js';
import { alertDialog } from '../ui/dialogs.js';
import { setGeometryBlob } from '../textures/decode.js';
import { createIcon } from '../ui/ui-icons.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

const $ = (id) => document.getElementById(id);

export function updateAssetFillButton() {
  const button = $('asset-fill-btn');
  if (!button) return;
  const { assetFill } = viewerState;
  const available = assetFill.available
    && viewerState.currentSource?.kind === 'mod' && !!viewerState.currentModPath;
  const state = assetFill.loading ? 'loading' : assetFill.loaded ? 'remove' : 'load';
  const label = state === 'remove'
    ? t('mesh.removeMissingParts')
    : state === 'loading'
      ? assetFill.loaded ? t('mesh.removingMissingParts') : t('mesh.loadingMissingParts')
      : t('mesh.loadMissingParts');
  button.disabled = !available || assetFill.loading;
  button.dataset.state = state;
  button.setAttribute('aria-label', label);
  button.setAttribute('aria-pressed', String(assetFill.loaded));
  button.replaceChildren(createIcon(state === 'remove' ? 'close' : 'mesh-add'));
  button.title = assetFill.loaded
    ? t('mesh.removeOriginalParts')
    : t('mesh.addOriginalParts');
}

window.addEventListener(LANGUAGE_CHANGED, updateAssetFillButton);

export function resetAssetFillState() {
  const { assetFill } = viewerState;
  removeTextures(assetFill.textureKeys);
  assetFill.textureKeys = new Set();
  assetFill.fillId = null;
  assetFill.available = false;
  assetFill.loaded = false;
  assetFill.loading = false;
}

function assetFillOperationIsCurrent(operation, path) {
  return operation === viewerState.assetFill.epoch
    && viewerState.currentSource?.kind === 'mod'
    && samePath(viewerState.currentModPath, path);
}

function rollbackAssetFillFrontend(addedMeshes, textureKeys) {
  const targetMeshes = [...new Set(addedMeshes || [])];
  const removedFromPanel = removeAssetFillMeshPanel(targetMeshes);
  const remaining = targetMeshes.filter(mesh => activeMeshes.includes(mesh));
  remaining.forEach(removeMesh);
  const removed = [...new Set([...removedFromPanel, ...remaining])];
  if (removed.length) requestRender();

  const keys = new Set(textureKeys || []);
  removeTextures(keys);
  keys.forEach(key => viewerState.assetFill.textureKeys.delete(key));
  viewerState.assetFill.loaded = false;
  viewerState.assetFill.fillId = null;
}

async function releaseBackendAssetFill(path, fillId = null) {
  try {
    const result = await window.pywebview.api.remove_missing_asset_parts(
      path, fillId);
    if (result?.status === 'error') {
      console.warn('Could not roll back missing Asset parts:', result.error);
    }
  } catch (error) {
    console.warn('Could not roll back missing Asset parts:', error);
  }
}

async function rollbackAssetFill(
    path, operation, fillId, addedMeshes, textureKeys) {
  if (assetFillOperationIsCurrent(operation, path)) {
    rollbackAssetFillFrontend(addedMeshes, textureKeys);
  } else {
    // A stale operation must not touch the scene or global fill state. Texture
    // entries are still owned by this transaction, so release only those.
    const keys = new Set(textureKeys || []);
    removeTextures(keys);
    keys.forEach(key => viewerState.assetFill.textureKeys.delete(key));
  }
  await releaseBackendAssetFill(path, fillId);
}

export async function loadMissingAssetParts() {
  const state = viewerState;
  if (!state.currentModPath || state.currentSource?.kind !== 'mod'
      || state.assetFill.loading) {
    return false;
  }
  const path = state.currentModPath;
  const operation = ++state.assetFill.epoch;
  let backendLoaded = false;
  let rolledBack = false;
  let transactionFillId = null;
  let addedMeshes = [];
  const transactionTextureKeys = new Set();
  const rollback = async () => {
    if (rolledBack) return;
    rolledBack = true;
    if (backendLoaded) {
      await rollbackAssetFill(
        path, operation, transactionFillId,
        addedMeshes, transactionTextureKeys);
    }
  };
  state.assetFill.loading = true;
  updateAssetFillButton();
  try {
    const result = await window.pywebview.api.load_missing_asset_parts(path);
    if (!assetFillOperationIsCurrent(operation, path)) {
      if (result?.status === 'loaded') {
        backendLoaded = true;
        transactionFillId = result.fill_id || null;
      }
      await rollback();
      return false;
    }
    if (result?.status !== 'loaded') {
      const messages = {
        nothing_missing: t('errors.assetFillNothing'),
        asset_ambiguous: t('errors.assetFillAmbiguous'),
        asset_not_found: t('errors.assetFillNotFound'),
      };
      if (result?.error) throw new Error(result.error);
      await alertDialog(messages[result?.status] || t('errors.assetFillDefault'));
      return false;
    }
    backendLoaded = true;
    transactionFillId = result.fill_id || null;
    if (!assetFillOperationIsCurrent(operation, path)) {
      await rollback();
      return false;
    }
    const payload = result.payload || {};
    const geometry = payload.geometry;
    if (geometry) {
      const response = await fetch(geometry.url, { cache: 'no-store' });
      if (!response.ok) throw new Error(`Geometry download failed (${response.status}).`);
      const blob = await response.arrayBuffer();
      if (blob.byteLength !== geometry.length) {
        throw new Error('Geometry download was incomplete.');
      }
      if (!assetFillOperationIsCurrent(operation, path)) {
        await rollback();
        return false;
      }
      setGeometryBlob(blob);
    }
    for (const [key, uri] of Object.entries(payload.textures || {})) {
      if (addTexture(key, uri)) {
        state.assetFill.textureKeys.add(key);
        transactionTextureKeys.add(key);
      }
    }
    if (!assetFillOperationIsCurrent(operation, path)) {
      await rollback();
      return false;
    }
    const before = new Set(activeMeshes);
    try {
      const entries = payload.meshes || {};
      const liveMeshes = buildPayloadMeshes(
        entries, null, {}, payload.metadata?.material_profiles || {}, {
          texturePools: payload.texture_pools || {},
          animations: payload.animations || {},
        });
      appendMeshPanel(
        entries, liveMeshes, null, {
          replace: false,
          texturePools: payload.texture_pools || {},
          readOnlySource: true,
        });
    } finally {
      addedMeshes = activeMeshes.filter(mesh => !before.has(mesh));
    }
    adoptModelMeshes(addedMeshes);
    if (!assetFillOperationIsCurrent(operation, path)) {
      await rollback();
      return false;
    }
    state.assetFill.loaded = true;
    state.assetFill.fillId = transactionFillId;
    fitTo(activeMeshes, {
      preserveCamera: true,
      preserveHomeView: true,
    });
    requestRender();
    return true;
  } catch (error) {
    await rollback();
    if (!assetFillOperationIsCurrent(operation, path)) return false;
    await alertDialog(t('errors.loadMissingAsset', {detail: error.message}));
    return false;
  } finally {
    if (operation === state.assetFill.epoch) {
      state.assetFill.loading = false;
      updateAssetFillButton();
    }
  }
}

export async function removeMissingAssetParts() {
  const state = viewerState;
  if (!state.currentModPath || !state.assetFill.loaded || state.assetFill.loading) {
    return false;
  }
  const path = state.currentModPath;
  const operation = ++state.assetFill.epoch;
  state.assetFill.loading = true;
  updateAssetFillButton();
  try {
    const result = await window.pywebview.api.remove_missing_asset_parts(
      path, state.assetFill.fillId);
    if (!assetFillOperationIsCurrent(operation, path)) return false;
    if (result?.status === 'error') throw new Error(result.error);
    if (result?.stale) return false;
    const fillMeshes = activeMeshes.filter(
      mesh => mesh.userData.assetFill === true).slice();
    removeAssetFillMeshPanel(fillMeshes);
    fillMeshes.forEach(removeMesh);
    removeTextures(state.assetFill.textureKeys);
    state.assetFill.textureKeys = new Set();
    state.assetFill.loaded = false;
    state.assetFill.fillId = null;
    requestRender();
    return true;
  } catch (error) {
    if (!assetFillOperationIsCurrent(operation, path)) return false;
    await alertDialog(t('errors.removeMissingAsset', {detail: error.message}));
    return false;
  } finally {
    if (operation === state.assetFill.epoch) {
      state.assetFill.loading = false;
      updateAssetFillButton();
    }
  }
}

export async function toggleMissingAssetParts() {
  return viewerState.assetFill.loaded
    ? removeMissingAssetParts() : loadMissingAssetParts();
}
