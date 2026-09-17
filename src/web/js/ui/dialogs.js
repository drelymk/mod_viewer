// Custom confirm/alert dialogs.

import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

const $ = (id) => document.getElementById(id);

let resolveActive = null;
let activeHasInput = false;

function syncActiveLabels() {
  if (!$('dialog-backdrop').classList.contains('show')) return;
  const button = $('dialog-ok');
  button.textContent = activeHasInput ? t('common.yes') : t('common.ok');
  button.dataset.i18n = activeHasInput ? 'common.yes' : 'dialog.ok';
}

function close(result) {
  $('dialog-backdrop').classList.remove('show');
  $('dialog-input').style.display = 'none';
  const resolve = resolveActive;
  resolveActive = null;
  activeHasInput = false;
  $('dialog-ok').dataset.i18n = 'dialog.ok';
  if (resolve) resolve(result);
}

function open(message, { cancelable, inputValue } = {}) {
  return new Promise((resolve) => {
    resolveActive = resolve;
    $('dialog-message').textContent = message;
    $('dialog-cancel').style.display = cancelable ? '' : 'none';
    const input = $('dialog-input');
    const hasInput = inputValue !== undefined;
    activeHasInput = hasInput;
    input.style.display = hasInput ? 'block' : 'none';
    input.value = hasInput ? inputValue : '';
    $('dialog-backdrop').classList.add('show');
    syncActiveLabels();
    (hasInput ? input : $('dialog-ok')).focus();
    if (hasInput) input.select();
  });
}

/** Drop-in replacement for window.alert() — resolves once dismissed. */
export function alertDialog(message) {
  return open(message, { cancelable: false }).then(() => {});
}

/** Drop-in replacement for window.confirm() — resolves true/false. */
export function confirmDialog(message) {
  return open(message, { cancelable: true });
}

/** Confirm with an editable name; resolves the trimmed name or null. */
export function inputConfirmDialog(message, value) {
  return open(message, { cancelable: true, inputValue: value })
    .then((confirmed) => confirmed ? $('dialog-input').value.trim() : null);
}

$('dialog-ok').addEventListener('click', () => close(true));
$('dialog-cancel').addEventListener('click', () => close(false));
$('dialog-backdrop').addEventListener('click', (evt) => {
  if (evt.target.id === 'dialog-backdrop') close(false);
});
document.addEventListener('keydown', (evt) => {
  if (!$('dialog-backdrop').classList.contains('show')) return;
  if (evt.key === 'Escape') close(false);
});

window.addEventListener(LANGUAGE_CHANGED, syncActiveLabels);
