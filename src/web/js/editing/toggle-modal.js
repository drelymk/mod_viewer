// Add/Edit toggle modal — the only authoring UI for cycle toggles.
//
// Talks to the write-path API in app/toggle_api.py (exposed as
// window.pywebview.api.*). Only supports the "cycle" toggle type, matching
// the backend: adding a toggle always creates exactly one variable, and
// editing can only change an *existing* var's value list, never add or
// remove one (see toggle_editor.add_toggle / edit_toggle).

import { confirmDialog } from '../ui/dialogs.js';
import { bindModalDismiss, setModalError } from '../ui/modal-shell.js';

const $ = (id) => document.getElementById(id);

let currentMode = null;    // 'add' | 'edit'
let currentModPath = null;
let currentInfo = null;    // the payload entry being edited (add: null)
let onSaved = null;        // callback invoked after a successful staged edit
let editVarRows = [];      // [{var, original, input}] built for edit mode

function setError(message) {
  setModalError($('tm-error'), message);
}

function closeModal() {
  $('toggle-modal-backdrop').classList.remove('show');
  currentMode = null;
  currentInfo = null;
  editVarRows = [];
}

/** One read-only var-name + editable comma-values row per cycled var. */
function buildEditVarRows(vars) {
  const wrap = $('tm-vars-multi');
  wrap.innerHTML = '';
  editVarRows = [];
  for (const [name, values] of Object.entries(vars)) {
    const row = document.createElement('label');
    row.className = 'modal-field';
    const span = document.createElement('span');
    span.textContent = `$${name} values (comma-separated)`;
    const input = document.createElement('input');
    input.type = 'text';
    input.autocomplete = 'off';
    const joined = values.join(',');
    input.value = joined;
    row.append(span, input);
    wrap.appendChild(row);
    editVarRows.push({ var: name, original: joined, input });
  }
}

/** Add mode: a picker when there's a real choice. Edit mode: same list, but
 * disabled — an existing toggle's file can't change, only shown for context. */
async function populateIniPicker(modPath, selected, editable) {
  const field = $('tm-ini-field');
  const select = $('tm-ini');
  select.innerHTML = '';
  select.disabled = !editable;

  const inis = await window.pywebview.api.list_toggle_source_inis(modPath);
  for (const opt of inis) {
    const o = document.createElement('option');
    o.value = opt.value;
    o.textContent = opt.label;
    select.appendChild(o);
  }
  if (selected) select.value = selected;
  field.style.display = (editable && inis.length <= 1) ? 'none' : '';
}

/**
 * Open the modal in 'add' or 'edit' mode.
 *   add:  { mode: 'add', modPath, onSaved }
 *   edit: { mode: 'edit', modPath, info, onSaved }  — info is a controls.toggles
 *         payload entry (needs .ini, .section, .name); the real field values
 *         are read from the authoritative edit session via get_toggle_details.
 */
export async function openToggleModal({ mode, modPath, info, onSaved: cb }) {
  currentMode = mode;
  currentModPath = modPath;
  currentInfo = info || null;
  onSaved = cb;
  setError('');

  $('tm-var-single').style.display = mode === 'add' ? '' : 'none';
  $('tm-vars-multi').style.display = mode === 'edit' ? '' : 'none';
  $('tm-title').textContent = mode === 'add' ? 'Add Toggle' : `Edit ${info.name}`;
  $('toggle-modal-backdrop').classList.add('show');

  if (mode === 'add') {
    $('tm-name').value = '';
    $('tm-key').value = '';
    $('tm-back').value = '';
    $('tm-var').value = '';
    $('tm-values').value = '';
    $('tm-default').value = '';
    await populateIniPicker(modPath, null, true);
  } else {
    await populateIniPicker(modPath, info.ini, false);
    const details = await window.pywebview.api.get_toggle_details(modPath, info.ini, info.section);
    if (details.error) {
      setError(details.error);
    } else {
      $('tm-name').value = details.name;
      $('tm-key').value = details.key;
      $('tm-back').value = details.back;
      buildEditVarRows(details.vars);
    }
  }

  $('tm-name').focus();
}

function parseValues(text) {
  return text.split(',').map((s) => s.trim()).filter(Boolean);
}

function submitAdd() {
  const ini = $('tm-ini').value;
  const name = $('tm-name').value.trim();
  const key = $('tm-key').value.trim();
  const back = $('tm-back').value.trim();
  const varName = $('tm-var').value.trim();
  const values = parseValues($('tm-values').value);
  const def = $('tm-default').value.trim();

  const options = {};
  if (back) options.back_combo = back;
  if (def) options.default = def;

  return window.pywebview.api.add_toggle(currentModPath, ini, name, key, varName, values, options);
}

function submitEdit(allowConflicts) {
  const changes = {
    new_name: $('tm-name').value.trim(),
    key_combo: $('tm-key').value.trim(),
    back_combo: $('tm-back').value.trim(),
  };
  // Only send vars whose text actually changed, so untouched cycle lines
  // are never rewritten (keeps the ini diff minimal).
  const varValues = {};
  for (const row of editVarRows) {
    const text = row.input.value.trim();
    if (text !== row.original) varValues[row.var] = parseValues(text);
  }
  if (Object.keys(varValues).length) changes.var_values = varValues;
  if (allowConflicts) changes.allow_value_conflicts = true;

  return window.pywebview.api.edit_toggle(
    currentModPath, currentInfo.ini, currentInfo.section, changes);
}

async function handleSubmit(evt) {
  evt.preventDefault();
  setError('');
  const saveBtn = $('tm-save');
  saveBtn.disabled = true;
  try {
    let result = currentMode === 'add' ? await submitAdd() : await submitEdit(false);

    // Shrinking a cycle's values can orphan meshes still gated on a removed
    // value — toggle_editor refuses by default; offer to force it, since
    // resolving that mesh's visibility is squarely the user's call.
    if (result.error && currentMode === 'edit' &&
        result.error.startsWith('removing these values would orphan existing gates')) {
      const proceed = await confirmDialog(
        `${result.error}\n\nApply anyway? Meshes only shown for a removed value will no longer be reachable through this toggle.`);
      if (proceed) result = await submitEdit(true);
    }

    if (result.error) {
      setError(result.error);
      return;
    }

    const changeType = currentMode === 'add' ? 'add' : 'edit';
    closeModal();
    // A brand-new toggle now appears in the list right away (see
    // mod_loader.build_toggle_panel's "wired" flag) with a ⚠ badge instead of
    // a one-off alert — its ⏺ Record button already works with zero prior
    // gating, so there's nothing further the user needs telling here.
    if (onSaved) await onSaved({ type: changeType });
  } catch (e) {
    setError(String(e));
  } finally {
    saveBtn.disabled = false;
  }
}

$('tm-form').addEventListener('submit', handleSubmit);
bindModalDismiss({
  backdrop: $('toggle-modal-backdrop'),
  close: closeModal,
  buttons: [$('tm-cancel')],
});