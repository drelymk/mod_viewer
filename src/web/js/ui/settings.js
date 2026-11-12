// Global viewer preferences and their accessible Settings dialog.

import { createPreferencePersistence } from './preference-persistence.js';
import { closeHeaderPopovers } from './header-popovers.js';
import { initAppearanceControls } from './appearance.js';
import { bridgeReady } from '../app/bridge.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { initGameIndexDialog } from '../panels/game-index-dialog.js';

const $ = (id) => document.getElementById(id);

function initGameFolders(root) {
  const error = root.querySelector('.settings-game-error');
  if (!error) return;
  const rows = [...root.querySelectorAll('.settings-game-row')];
  const indexButton = root.querySelector('.settings-game-index');
  const indexStatus = root.querySelector('.settings-game-index-status');
  const indexDialog = initGameIndexDialog();
  let indexState = null;
  let current = {};
  const showIndex = () => {
    const path = current.WWMI || '';
    indexButton.hidden = !path;
    indexStatus.hidden = !path || !indexState;
    if (!indexStatus.hidden) indexStatus.textContent = t('gameIndex.status.' + indexState.state, indexState);
  };
  const refreshIndex = async () => {
    try {
      const state = await (await bridgeReady()).get_game_index_status?.();
      indexState = state && ['ready', 'missing', 'stale'].includes(state.state) ? state : null;
    } catch {
      indexState = null;
    }
    showIndex();
  };
  const offerIndex = async () => {
    await indexDialog.open();
    await refreshIndex();
  };
  const show = (folders) => {
    current = folders || {};
    for (const row of rows) {
      const path = current[row.dataset.gameKind] || '';
      const input = row.querySelector('.settings-game-path');
      input.value = path;
      input.title = path;
      input.placeholder = t('settings.notSet');
      row.querySelector('.settings-game-clear').hidden = !path;
    }
    showIndex();
  };
  const apply = (result) => {
    error.hidden = !result?.error;
    error.textContent = result?.error || '';
    if (result?.value) show(result.value);
  };
  const run = async (row, method) => {
    const buttons = row.querySelectorAll('button');
    const before = current[row.dataset.gameKind];
    buttons.forEach((button) => (button.disabled = true));
    let result;
    try {
      result = await (await bridgeReady())[method](row.dataset.gameKind);
      apply(result);
    } catch (failure) {
      apply({ error: failure.message });
    } finally {
      buttons.forEach((button) => (button.disabled = false));
    }
    if (row.dataset.gameKind !== 'WWMI') return;
    if (method === 'clear_game_directory') indexState = null;
    await refreshIndex();
    const after = result?.value?.WWMI;
    if (method === 'select_game_directory' && after && after !== before) await offerIndex();
  };
  indexButton.addEventListener('click', offerIndex);
  for (const row of rows) {
    row.querySelector('.settings-game-select').addEventListener('click', () => run(row, 'select_game_directory'));
    row.querySelector('.settings-game-clear').addEventListener('click', () => run(row, 'clear_game_directory'));
  }
  window.addEventListener(LANGUAGE_CHANGED, () => show(current));
  bridgeReady()
    .then((api) => api.get_game_folders())
    .then(apply, (failure) => apply({ error: failure.message }))
    .then(refreshIndex);
}
let autoRestoreMissingAssetParts = false;
let persistence = null;

export async function shouldAutoRestoreMissingAssetParts() {
  await persistence?.ready;
  return autoRestoreMissingAssetParts;
}

export function initSettingsDialog() {
  const button = $('settings-btn');
  const backdrop = $('settings-backdrop');
  const checkbox = $('settings-auto-restore');
  const applyAppearance = initAppearanceControls((...args) => persistence.change(...args));
  persistence = createPreferencePersistence({
    getMethod: 'get_viewer_preferences',
    setMethod: 'set_viewer_preferences',
    apply(name, value) {
      applyAppearance(name, value);
      if (name !== 'autoRestoreMissingAssetParts' || typeof value !== 'boolean') return;
      autoRestoreMissingAssetParts = value;
      checkbox.checked = value;
    },
  });
  checkbox.addEventListener('change', () => {
    autoRestoreMissingAssetParts = checkbox.checked;
    persistence.change('autoRestoreMissingAssetParts', checkbox.checked);
  });
  initGameFolders(backdrop);

  const close = () => {
    backdrop.classList.remove('show');
    button.setAttribute('aria-expanded', 'false');
    button.focus();
  };
  button.addEventListener('click', () => {
    closeHeaderPopovers();
    backdrop.classList.add('show');
    button.setAttribute('aria-expanded', 'true');
    checkbox.focus();
  });
  $('settings-close').addEventListener('click', close);
  $('settings-done').addEventListener('click', close);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });
  document.addEventListener(
    'keydown',
    (event) => {
      if (
        !backdrop.classList.contains('show') ||
        document.querySelector('.dialog-backdrop.show, #game-index-backdrop.show')
      )
        return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === 'Tab') {
        const controls = [
          ...backdrop.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)'),
        ];
        const index = controls.indexOf(document.activeElement);
        const next = (index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
        event.preventDefault();
        controls[next].focus();
      }
    },
    true,
  );
  return persistence;
}
