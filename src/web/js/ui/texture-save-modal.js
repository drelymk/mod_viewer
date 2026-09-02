// Texture-centric Save to Texture confirmation and commit flow.

import { bindModalDismiss, setModalError } from './modal-shell.js';
import {
  captureTextureSaveState, textureSaveStateMatches,
  textureSaveTargetMatches, textureSaveTargetsPayload,
} from '../mesh/texture-save-state.js';
import {
  flushMeshColorAdjustmentPersistence, resetMeshColorAdjustment,
} from '../mesh/mesh-color-state.js';
import { activeMeshes } from '../mesh/mesh-state.js';
import { reloadTextures } from '../mesh/mesh-factory.js';
import { notifyMeshStateChanged } from '../mesh/mesh-state-events.js';
import { viewerState, samePath } from '../app/state.js';

const $ = id => document.getElementById(id);
const backdrop = $('texture-bake-modal-backdrop');
const body = $('texture-bake-body');
const error = $('texture-bake-error');
const saveButton = $('texture-bake-confirm');

let pendingSave = null;
let saving = false;

function displayNameForTarget(target) {
  return target?.mesh?.userData?.displayName
    || target?.mesh?.userData?.semanticKey
    || target?.semanticKey
    || 'Mesh';
}

function closeTextureSaveModal() {
  // Keep the modal open while the destructive request and post-commit state
  // synchronization are in flight.
  if (saving) return;
  pendingSave = null;
  backdrop?.classList.remove('show');
}

function setSaveAction({visible = false, disabled = true, label = 'Save'} = {}) {
  if (!saveButton) return;
  saveButton.hidden = !visible;
  saveButton.disabled = disabled;
  saveButton.textContent = label;
}

function addDetail(rows, label, value) {
  const term = document.createElement('dt');
  term.textContent = label;
  const description = document.createElement('dd');
  description.textContent = String(value);
  rows.append(term, description);
}

function textureFileForKey(key) {
  const path = key?.split('::').slice(1).join('::') || '';
  return path.replaceAll('\\', '/').split('/').pop() || 'Unknown';
}

function renderSavePrompt(state) {
  setModalError(error, '');
  body.replaceChildren();
  const heading = document.createElement('div');
  heading.className = 'texture-bake-state';
  heading.textContent = 'SAVE TO TEXTURE';
  body.appendChild(heading);

  const rows = document.createElement('dl');
  rows.className = 'texture-bake-details';
  addDetail(rows, 'Texture', textureFileForKey(state?.texKey));
  body.appendChild(rows);

  const label = document.createElement('p');
  label.className = 'texture-bake-summary';
  label.textContent = 'Meshes with Color changes';
  body.appendChild(label);
  const targets = document.createElement('ul');
  targets.className = 'texture-bake-targets';
  (state?.targets || []).forEach(target => {
    const item = document.createElement('li');
    item.textContent = displayNameForTarget(target);
    targets.appendChild(item);
  });
  body.appendChild(targets);

  const note = document.createElement('p');
  note.className = 'texture-bake-summary';
  note.textContent = 'The texture file will be modified and a backup will be created.';
  body.appendChild(note);
  setSaveAction({visible: true, disabled: !(state?.targets?.length), label: 'Save'});
}

function formatSaveError(result) {
  const message = result?.error || 'Texture save failed.';
  const details = result?.details;
  const meshes = Array.isArray(details?.meshes) ? details.meshes : [];
  if (!meshes.length) return message;
  return `${message} Conflicting meshes: ${meshes.join(', ')}.`;
}

function affectedTextureKeys(result) {
  const reported = Array.isArray(result?.affected_tex_keys)
    && result.affected_tex_keys.length
    ? result.affected_tex_keys : [result?.tex_key];
  return [...new Set(reported.filter(key => typeof key === 'string' && key))];
}

function renderSaveError(result) {
  body.replaceChildren();
  setModalError(error, formatSaveError(result));
  setSaveAction();
}

function renderSaveSuccess(result, targetCount) {
  setModalError(error, '');
  body.replaceChildren();
  const heading = document.createElement('div');
  heading.className = 'texture-bake-state';
  heading.textContent = 'TEXTURE SAVED';
  body.appendChild(heading);
  const file = document.createElement('p');
  file.className = 'texture-bake-summary';
  file.textContent = result.texture?.file || 'Texture';
  body.appendChild(file);
  const summary = document.createElement('p');
  summary.className = 'texture-bake-summary';
  const count = Array.isArray(result.saved_meshes)
    ? result.saved_meshes.length : targetCount;
  summary.textContent = `Color changes saved for ${count} mesh${count === 1 ? '' : 'es'}.`;
  body.appendChild(summary);
  const rows = document.createElement('dl');
  rows.className = 'texture-bake-details';
  addDetail(rows, 'Backup', result.backup?.file || 'Created');
  body.appendChild(rows);
  if (result.warning === 'color_state_reset_failed') {
    const warning = document.createElement('p');
    warning.className = 'texture-bake-warning texture-bake-warning-unknown';
    warning.textContent = 'The texture was saved, but its Color metadata could '
      + 'not be cleared. Resolve the metadata write failure before reopening '
      + 'the mod.';
    body.appendChild(warning);
  }
  setSaveAction();
}

async function synchronizeCommittedSave(state, result) {
  const affectedKeys = affectedTextureKeys(result);
  const saved = Array.isArray(result.saved_meshes) ? result.saved_meshes : [];
  const savedKeys = new Set(saved.map(item =>
    `${item?.semantic_key || ''}\u0000${item?.metadata_key || ''}`));
  const meshes = (state.targets || [])
    .filter(target => savedKeys.has(
      `${target.semanticKey || ''}\u0000${target.metadataKey || ''}`)
      && textureSaveTargetMatches(target.mesh, state, target))
    .map(target => target.mesh)
    .filter(mesh => activeMeshes.includes(mesh));
  const sameLoadedMod = viewerState.currentSource?.kind === 'mod'
    && samePath(viewerState.currentModPath, state.modPath);
  if (!sameLoadedMod) return false;

  const retryMetadataClear = result.warning === 'color_state_reset_failed';
  meshes.forEach(mesh => resetMeshColorAdjustment(
    mesh, {persist: retryMetadataClear, render: false}));
  if (retryMetadataClear) {
    try {
      await Promise.all(meshes.map(mesh =>
        flushMeshColorAdjustmentPersistence(mesh)));
      if (meshes.length) delete result.warning;
    } catch (_metadataError) {
      // Keep the warning visible when the per-mesh recovery write also fails.
      result.warning = 'color_state_reset_failed';
    }
  }
  await reloadTextures(affectedKeys, {force: true});
  notifyMeshStateChanged(meshes);
  window.dispatchEvent(new CustomEvent('mod-viewer-texture-saved', {
    detail: {
      texKey: result.tex_key,
      affectedTexKeys: affectedKeys,
      savedMeshes: saved,
    },
  }));
  return true;
}

async function runSave(job) {
  const api = window.pywebview?.api?.save_texture_color;
  if (typeof api !== 'function') {
    renderSaveError({status: 'error', error: 'Texture saving is unavailable.'});
    return null;
  }
  saving = true;
  setSaveAction({visible: true, disabled: true, label: 'Saving…'});
  body.replaceChildren();
  const loading = document.createElement('div');
  loading.className = 'texture-bake-loading';
  loading.textContent = 'Saving color changes to the texture…';
  body.appendChild(loading);

  try {
    await Promise.all((job.state.targets || []).map(target =>
      flushMeshColorAdjustmentPersistence(target.mesh)));
  } catch (_persistenceError) {
    saving = false;
    renderSaveError({
      status: 'error',
      error: 'The pending Color metadata could not be saved. '
        + 'Texture saving was cancelled.',
    });
    return null;
  }
  const currentState = captureTextureSaveState(job.mesh);
  if ((typeof job.isCurrent === 'function' && !job.isCurrent())
      || !textureSaveStateMatches(job.mesh, job.state, currentState)) {
    saving = false;
    pendingSave = {mesh: job.mesh, isCurrent: job.isCurrent, state: currentState};
    renderSavePrompt(currentState);
    return null;
  }
  // The usage snapshot is informational for confirmation; always send the
  // fresh role snapshot captured immediately before the destructive request.
  job.state = currentState;

  let result;
  try {
    result = await api(
      job.state.modPath, job.state.texKey,
      textureSaveTargetsPayload(job.state), job.state.textureUsage);
  } catch (_requestError) {
    result = {status: 'error', error: 'Texture save failed.'};
  }
  if (result?.status !== 'ok') {
    saving = false;
    renderSaveError(result);
    return result;
  }
  try {
    await synchronizeCommittedSave(job.state, result);
  } catch (_refreshError) {
    renderSaveError({
      status: 'error',
      error: 'Texture saved, but the viewer could not refresh it.',
    });
    return result;
  } finally {
    saving = false;
  }
  if (typeof job.isCurrent === 'function' && !job.isCurrent()) {
    closeTextureSaveModal();
    return result;
  }
  renderSaveSuccess(result, job.state.targets.length);
  return result;
}

/** Open the Save to Texture modal without performing a backend preflight. */
export function openTextureSaveModal(mesh, {isCurrent} = {}) {
  if (!backdrop || !body) return null;
  const state = captureTextureSaveState(mesh);
  pendingSave = {mesh, isCurrent, state};
  backdrop.classList.add('show');
  if (state.texKey && state.targets.length) {
    renderSavePrompt(state);
  } else {
    body.replaceChildren();
    setSaveAction();
    setModalError(error, 'No changed, editable meshes use this DDS.');
  }
  return state;
}

saveButton?.addEventListener('click', async () => {
  if (!pendingSave || saving) return;
  const job = pendingSave;
  pendingSave = null;
  await runSave(job);
});

bindModalDismiss({
  backdrop,
  close: closeTextureSaveModal,
  buttons: [$('texture-bake-close'), $('texture-bake-close-x')].filter(Boolean),
});

window.addEventListener('mod-viewer-mesh-selected', () => {
  if (backdrop?.classList.contains('show') && !saving) {
    closeTextureSaveModal();
  }
});

export { closeTextureSaveModal };
