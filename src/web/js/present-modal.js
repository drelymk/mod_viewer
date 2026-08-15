// Add/edit dialog for the reserved [KeyModViewerPresent] binding.

import { getToggleState } from './visibility.js';

const $ = (id) => document.getElementById(id);

let context = null;

export function presentSnapshots(present) {
  const state = getToggleState();
  return Object.fromEntries((present.target_inis || []).map((target) => [
    target.value,
    Object.fromEntries((target.vars || [])
      .filter((variable) => state[variable] !== undefined)
      .map((variable) => [variable, state[variable]])),
  ]));
}

function setError(message) {
  $('pm-error').textContent = message || '';
  $('pm-error').style.display = message ? 'block' : 'none';
}

function close() {
  $('present-modal-backdrop').classList.remove('show');
  context = null;
}

export function openPresentModal({ mode, modPath, present, item, onSaved }) {
  context = { mode, modPath, present, item: item || null, onSaved };
  const editing = mode === 'edit';
  const completing = mode === 'complete';
  item = context.item;
  $('pm-title').textContent = editing ? 'Edit PRESENT'
    : (completing ? 'Complete PRESENT' : 'Add PRESENT');
  $('pm-key').value = (editing || completing) ? item.key_raw : '';
  $('pm-back').value = (editing || completing) ? item.back : '';
  setError('');
  $('present-modal-backdrop').classList.add('show');
  $('pm-key').focus();
}

async function submit(event) {
  event.preventDefault();
  if (!context) return;
  setError('');
  const button = $('pm-save');
  button.disabled = true;
  try {
    const key = $('pm-key').value.trim();
    const back = $('pm-back').value.trim();
    const result = context.mode === 'add' || context.mode === 'complete'
      ? await window.pywebview.api.add_present(
          context.modPath, key, back, presentSnapshots(context.present))
      : await window.pywebview.api.edit_present(context.modPath, key, back);
    if (result.error) {
      setError(result.error);
      return;
    }
    const saved = context.onSaved;
    close();
    $('present-list').classList.remove('collapsed');
    $('present-panel').querySelector('.group-toggle').classList.remove('collapsed');
    if (saved) await saved();
  } catch (error) {
    setError(String(error));
  } finally {
    button.disabled = false;
  }
}

$('pm-form').addEventListener('submit', submit);
$('pm-cancel').addEventListener('click', close);
$('present-modal-backdrop').addEventListener('click', (event) => {
  if (event.target.id === 'present-modal-backdrop') close();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && $('present-modal-backdrop').classList.contains('show')) close();
});
