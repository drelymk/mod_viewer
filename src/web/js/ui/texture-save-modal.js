// Texture-centric Save to Texture confirmation and commit flow.

import { bindModalDismiss, setModalError } from './modal-shell.js';
import { createTextureSaveSession } from '../mesh/texture-save-session.js';

const $ = id => document.getElementById(id);
const backdrop = $('texture-bake-modal-backdrop');
const body = $('texture-bake-body');
const error = $('texture-bake-error');
const saveButton = $('texture-bake-confirm');

let saveProgressElements = null;

const saveStageLabels = {
  preparing: 'Preparing texture…',
  reading: 'Reading source texture…',
  processing: 'Processing texture…',
  writing: 'Writing texture…',
  refreshing: 'Refreshing viewer…',
  complete: 'Texture saved…',
};

function displayNameForTarget(target) {
  return target?.mesh?.userData?.displayName
    || target?.mesh?.userData?.semanticKey
    || target?.semanticKey
    || 'Mesh';
}

function closeTextureSaveModal() {
  if (!textureSaveSession.close()) return;
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
  saveProgressElements = null;
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

function renderSaveError(result) {
  saveProgressElements = null;
  body.replaceChildren();
  setModalError(error, formatSaveError(result));
  setSaveAction();
}

function renderSaveSuccess(result, targetCount) {
  setModalError(error, '');
  saveProgressElements = null;
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

function renderSaveProgress(detail = {stage: 'preparing'}) {
  if (!saveProgressElements) {
    body.replaceChildren();
    const view = document.createElement('div');
    view.className = 'texture-bake-progress-view';
    const heading = document.createElement('div');
    heading.className = 'texture-bake-state';
    heading.textContent = 'SAVING TO TEXTURE';
    const status = document.createElement('p');
    status.className = 'texture-bake-progress-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const progress = document.createElement('progress');
    progress.id = 'texture-bake-progress';
    progress.className = 'texture-bake-progress';
    progress.max = 100;
    progress.setAttribute('aria-label', 'Texture save progress');
    const progressDetail = document.createElement('p');
    progressDetail.className = 'texture-bake-progress-detail';
    view.append(heading, status, progress, progressDetail);
    body.appendChild(view);
    saveProgressElements = {
      status, progress, progressDetail, stage: null,
    };
  }

  const stage = typeof detail?.stage === 'string'
    ? detail.stage : 'preparing';
  const label = saveStageLabels[stage] || 'Saving texture…';
  if (saveProgressElements.stage !== stage) {
    saveProgressElements.status.textContent = label;
    saveProgressElements.stage = stage;
  }
  const completed = Number(detail?.completed_blocks);
  const total = Number(detail?.total_blocks);
  if (stage === 'processing' && Number.isFinite(completed)
      && Number.isFinite(total) && total > 0) {
    const boundedCompleted = Math.max(0, Math.min(completed, total));
    saveProgressElements.progress.value = boundedCompleted / total * 100;
    saveProgressElements.progress.setAttribute(
      'aria-valuetext', `${boundedCompleted} of ${total} blocks`);
    const mip = Number(detail?.mip);
    const mipCount = Number(detail?.mip_count);
    const mipLabel = mipCount > 1 && Number.isInteger(mip)
      ? `Mip ${mip + 1} of ${mipCount} · ` : '';
    saveProgressElements.progressDetail.textContent =
      `${mipLabel}${boundedCompleted} / ${total} blocks`;
  } else {
    saveProgressElements.progress.removeAttribute('value');
    saveProgressElements.progress.removeAttribute('aria-valuetext');
    saveProgressElements.progressDetail.textContent = '';
  }
}

const textureSaveSession = createTextureSaveSession({
  onError: renderSaveError,
  onProgress: renderSaveProgress,
  onPrompt: renderSavePrompt,
  onRefreshing: () => renderSaveProgress({stage: 'refreshing'}),
  onSuccess: renderSaveSuccess,
  onClose: closeTextureSaveModal,
});

/** Open the Save to Texture modal without performing a backend preflight. */
export function openTextureSaveModal(mesh, {isCurrent} = {}) {
  if (!backdrop || !body) return null;
  const state = textureSaveSession.open(mesh, {isCurrent});
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
  if (textureSaveSession.isSaving()) return;
  await textureSaveSession.submit();
});

bindModalDismiss({
  backdrop,
  close: closeTextureSaveModal,
  buttons: [$('texture-bake-close'), $('texture-bake-close-x')].filter(Boolean),
});

window.addEventListener('mod-viewer-mesh-selected', () => {
  if (backdrop?.classList.contains('show')
      && !textureSaveSession.isSaving()) {
    closeTextureSaveModal();
  }
});

window.addEventListener(
  'mod-viewer-texture-save-progress', event =>
    textureSaveSession.handleProgress(event));
