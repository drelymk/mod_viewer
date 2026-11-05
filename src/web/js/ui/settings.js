// Global loading preferences and their accessible Settings dialog.

import { createPreferencePersistence } from './preference-persistence.js';
import { closeHeaderPopovers } from './header-popovers.js';

const $ = (id) => document.getElementById(id);
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
  persistence = createPreferencePersistence({
    getMethod: 'get_viewer_preferences',
    setMethod: 'set_viewer_preferences',
    apply(name, value) {
      if (name !== 'autoRestoreMissingAssetParts' || typeof value !== 'boolean') return;
      autoRestoreMissingAssetParts = value;
      checkbox.checked = value;
    },
  });
  checkbox.addEventListener('change', () => {
    autoRestoreMissingAssetParts = checkbox.checked;
    persistence.change('autoRestoreMissingAssetParts', checkbox.checked);
  });

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
      if (!backdrop.classList.contains('show') || document.querySelector('.dialog-backdrop.show')) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === 'Tab') {
        const controls = [...backdrop.querySelectorAll('button, input')];
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
