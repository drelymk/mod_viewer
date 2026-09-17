// Texture-centric Save to Texture confirmation and commit flow.

import { bindModalDismiss, setModalError } from './modal-shell.js';
import { createTextureSaveSession } from '../mesh/texture-save-session.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

const $ = id => document.getElementById(id);
const backdrop = $('texture-bake-modal-backdrop');
const body = $('texture-bake-body');
const error = $('texture-bake-error');
const saveButton = $('texture-bake-confirm');

let saveProgressElements = null;
let currentView = null;

const saveStageLabels = {
  preparing: 'texture.saveStage.preparing',
  reading: 'texture.saveStage.reading',
  processing: 'texture.saveStage.processing',
  writing: 'texture.saveStage.writing',
  refreshing: 'texture.saveStage.refreshing',
  complete: 'texture.saveStage.complete',
};

function displayNameForTarget(target) {
  return target?.mesh?.userData?.displayName
    || target?.mesh?.userData?.semanticKey
    || target?.semanticKey
    || t('weightRig.mesh');
}

function closeTextureSaveModal() {
  if (!textureSaveSession.close()) return;
  backdrop?.classList.remove('show');
}

function setSaveAction({visible = false, disabled = true,
                        label = t('common.save')} = {}) {
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
  return path.replaceAll('\\', '/').split('/').pop() || t('texture.unknown');
}

function renderSavePrompt(state) {
  currentView = {kind: 'prompt', state};
  setModalError(error, '');
  saveProgressElements = null;
  body.replaceChildren();
  const heading = document.createElement('div');
  heading.className = 'texture-bake-state';
  heading.textContent = t('texture.savePromptTitle');
  body.appendChild(heading);

  const rows = document.createElement('dl');
  rows.className = 'texture-bake-details';
  addDetail(rows, t('texture.textureLabel'), textureFileForKey(state?.texKey));
  body.appendChild(rows);

  const label = document.createElement('p');
  label.className = 'texture-bake-summary';
  label.textContent = t('texture.meshesColorChanges');
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
  note.textContent = t('texture.backupNotice');
  body.appendChild(note);
  setSaveAction({visible: true, disabled: !(state?.targets?.length)});
}

function formatSaveError(result) {
  const errorKeys = {
    texture_saving_unavailable: 'texture.reason.unavailable',
    pending_color_metadata_failed: 'texture.reason.metadataFailed',
    texture_save_failed: 'texture.reason.saveFailed',
    texture_refresh_failed: 'texture.reason.refreshFailed',
  };
  const message = result?.error
    || (errorKeys[result?.error_code] ? t(errorKeys[result.error_code]) : '')
    || t('texture.saveFailed');
  const details = result?.details;
  const meshes = Array.isArray(details?.meshes) ? details.meshes : [];
  if (!meshes.length) return message;
  return `${message} ${t('texture.conflictingMeshes', {
    meshes: meshes.join(', '),
  })}`;
}

function renderSaveError(result) {
  currentView = {kind: 'error', result};
  saveProgressElements = null;
  body.replaceChildren();
  setModalError(error, formatSaveError(result));
  setSaveAction();
}

function renderSaveSuccess(result, targetCount) {
  currentView = {kind: 'success', result, targetCount};
  setModalError(error, '');
  saveProgressElements = null;
  body.replaceChildren();
  const heading = document.createElement('div');
  heading.className = 'texture-bake-state';
  heading.textContent = t('texture.savedTitle');
  body.appendChild(heading);
  const file = document.createElement('p');
  file.className = 'texture-bake-summary';
  file.textContent = result.texture?.file || t('texture.textureLabel');
  body.appendChild(file);
  const summary = document.createElement('p');
  summary.className = 'texture-bake-summary';
  const count = Array.isArray(result.saved_meshes)
    ? result.saved_meshes.length : targetCount;
  summary.textContent = t(
    count === 1 ? 'texture.colorSavedOne' : 'texture.colorSavedMany',
    {count});
  body.appendChild(summary);
  const rows = document.createElement('dl');
  rows.className = 'texture-bake-details';
  addDetail(rows, t('texture.backupLabel'),
    result.backup?.file || t('texture.created'));
  body.appendChild(rows);
  if (result.warning === 'color_state_reset_failed') {
    const warning = document.createElement('p');
    warning.className = 'texture-bake-warning texture-bake-warning-unknown';
    warning.textContent = t('texture.colorMetadataWarning');
    body.appendChild(warning);
  }
  setSaveAction();
}

function renderSaveProgress(detail = {stage: 'preparing'}) {
  currentView = {...currentView, kind: 'progress', detail};
  if (!saveProgressElements) {
    body.replaceChildren();
    const view = document.createElement('div');
    view.className = 'texture-bake-progress-view';
    const heading = document.createElement('div');
    heading.className = 'texture-bake-state';
    heading.textContent = t('texture.savingTitle');
    const status = document.createElement('p');
    status.className = 'texture-bake-progress-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const progress = document.createElement('progress');
    progress.id = 'texture-bake-progress';
    progress.className = 'texture-bake-progress';
    progress.max = 100;
    progress.setAttribute('aria-label', t('texture.saveProgress'));
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
  const label = t(saveStageLabels[stage] || 'texture.saveStage.saving');
  saveProgressElements.status.textContent = label;
  saveProgressElements.stage = stage;
  const completed = Number(detail?.completed_blocks);
  const total = Number(detail?.total_blocks);
  if (stage === 'processing' && Number.isFinite(completed)
      && Number.isFinite(total) && total > 0) {
    const boundedCompleted = Math.max(0, Math.min(completed, total));
    saveProgressElements.progress.value = boundedCompleted / total * 100;
    saveProgressElements.progress.setAttribute(
      'aria-valuetext', t('texture.blocks', {
        completed: boundedCompleted, total,
      }));
    const mip = Number(detail?.mip);
    const mipCount = Number(detail?.mip_count);
    const mipLabel = mipCount > 1 && Number.isInteger(mip)
      ? `Mip ${mip + 1} of ${mipCount} · ` : '';
    saveProgressElements.progressDetail.textContent = mipLabel
      ? t('texture.mipBlocks', {
        mip: mip + 1, mips: mipCount, completed: boundedCompleted, total,
      })
      : t('texture.blocks', {completed: boundedCompleted, total});
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
    currentView = {kind: 'empty'};
    setSaveAction();
    setModalError(error, t('texture.noChangedMeshes'));
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

window.addEventListener(LANGUAGE_CHANGED, () => {
  if (!backdrop?.classList.contains('show') || !currentView) return;
  if (currentView.kind === 'prompt') renderSavePrompt(currentView.state);
  else if (currentView.kind === 'progress') renderSaveProgress(currentView.detail);
  else if (currentView.kind === 'error') renderSaveError(currentView.result);
  else if (currentView.kind === 'success') {
    renderSaveSuccess(currentView.result, currentView.targetCount);
  }
  saveButton?.setAttribute('aria-label', t('common.save'));
});
