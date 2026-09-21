// Custom confirm/alert dialogs.

import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

const $ = (id) => document.getElementById(id);

let resolveActive = null;
let activeHasInput = false;
let activeRange = null;

function syncActiveLabels() {
  if (!$('dialog-backdrop').classList.contains('show')) return;
  const button = $('dialog-ok');
  button.textContent = activeRange
    ? t(activeRange.okKey || 'common.ok')
    : activeHasInput ? t('common.yes') : t('common.ok');
  button.dataset.i18n = activeRange
    ? activeRange.okKey || 'common.ok'
    : activeHasInput ? 'common.yes' : 'dialog.ok';
}

function close(result) {
  $('dialog-backdrop').classList.remove('show');
  $('dialog-input').style.display = 'none';
  $('dialog-range-field').style.display = 'none';
  const resolve = resolveActive;
  resolveActive = null;
  activeHasInput = false;
  activeRange = null;
  $('dialog-ok').dataset.i18n = 'dialog.ok';
  if (resolve) resolve(result);
}

function open(message, { cancelable, inputValue, range } = {}) {
  return new Promise((resolve) => {
    resolveActive = resolve;
    $('dialog-message').textContent = message;
    $('dialog-cancel').style.display = cancelable ? '' : 'none';
    const input = $('dialog-input');
    const hasInput = inputValue !== undefined;
    activeHasInput = hasInput;
    activeRange = range || null;
    input.style.display = hasInput ? 'block' : 'none';
    input.value = hasInput ? inputValue : '';
    const rangeField = $('dialog-range-field');
    const rangeInput = $('dialog-range-input');
    rangeField.style.display = activeRange ? 'flex' : 'none';
    if (activeRange) {
      $('dialog-range-label').textContent = activeRange.label;
      $('dialog-range-hint').textContent = activeRange.rangeText || '';
      rangeInput.min = String(activeRange.min);
      rangeInput.max = String(activeRange.max);
      rangeInput.step = String(activeRange.step);
      rangeInput.value = String(activeRange.value);
    } else {
      rangeInput.value = '';
    }
    $('dialog-backdrop').classList.add('show');
    syncActiveLabels();
    (activeRange ? rangeInput : hasInput ? input : $('dialog-ok')).focus();
    if (hasInput) input.select();
  });
}

function confirmActive() {
  if (!activeRange) {
    close(true);
    return;
  }
  const input = $('dialog-range-input');
  const raw = input.value.trim();
  if (!raw || !input.checkValidity()) {
    input.focus();
    return;
  }
  close(Number(raw));
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

/** Confirm with a bounded numeric range; resolves the number or null. */
export function rangeInputDialog(message, {
  label, rangeText = '', value = 0, min = 0, max = 1, step = 1,
  okKey = 'common.ok',
} = {}) {
  return open(message, {
    cancelable: true,
    range: {label, rangeText, value, min, max, step, okKey},
  }).then(result => typeof result === 'number' ? result : null);
}

$('dialog-ok').addEventListener('click', confirmActive);
$('dialog-cancel').addEventListener('click', () => close(false));
$('dialog-range-input').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  confirmActive();
});
$('dialog-backdrop').addEventListener('click', (evt) => {
  if (evt.target.id === 'dialog-backdrop') close(false);
});
document.addEventListener('keydown', (evt) => {
  if (!$('dialog-backdrop').classList.contains('show')) return;
  if (evt.key === 'Escape') close(false);
});

window.addEventListener(LANGUAGE_CHANGED, syncActiveLabels);
