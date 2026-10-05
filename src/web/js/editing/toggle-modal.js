// Add/edit modal for cycle toggles. Add creates one variable; edit changes
// the values of variables already in the selected key section.

import { confirmDialog } from '../ui/dialogs.js';
import { bindModalDismiss, setModalError } from '../ui/modal-shell.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

const $ = (id) => document.getElementById(id);

let currentMode = null; // 'add' | 'edit'
let currentModPath = null;
let currentInfo = null; // the payload entry being edited (add: null)
let onSaved = null; // callback invoked after a successful staged edit
let editVarRows = []; // [{var, original, input}] built for edit mode
let createTargets = null;
let openRevision = 0;

function syncLabels() {
  if (!currentMode) return;
  $('tm-title').textContent =
    currentMode === 'add' ? t('toggle.addTitle') : t('toggle.editTitle', { name: currentInfo?.name });
  editVarRows.forEach((row) => {
    row.label.textContent = t('toggle.valuesLabel', { name: row.var });
  });
  $('tm-save').textContent = t('common.save');
  $('tm-cancel').textContent = t('common.cancel');
}

function setError(message) {
  setModalError($('tm-error'), message);
}

function closeModal() {
  $('toggle-modal-backdrop').classList.remove('show');
  currentMode = null;
  currentInfo = null;
  editVarRows = [];
  createTargets = null;
  openRevision++;
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
    span.textContent = t('toggle.valuesLabel', { name });
    const input = document.createElement('input');
    input.type = 'text';
    input.autocomplete = 'off';
    const joined = values.join(',');
    input.value = joined;
    row.append(span, input);
    wrap.appendChild(row);
    editVarRows.push({ var: name, original: joined, input, label: span });
  }
}

/** Add mode: a picker when there's a real choice. Edit mode: same list, but
 * disabled — an existing toggle's file can't change, only shown for context. */
async function populateIniPicker(modPath, selected, editable, revision) {
  const field = $('tm-ini-field');
  const select = $('tm-ini');
  select.innerHTML = '';
  select.disabled = !editable;

  const inis = await window.pywebview.api.list_toggle_source_inis(modPath);
  if (revision !== openRevision) return;
  for (const opt of inis) {
    const o = document.createElement('option');
    o.value = opt.value;
    o.textContent = opt.label;
    select.appendChild(o);
  }
  if (selected) select.value = selected;
  field.style.display = editable && inis.length <= 1 ? 'none' : '';
}

/** Open for add or edit; edit values come from the authoritative session. */
export async function openToggleModal({ mode, modPath, info, onSaved: cb, quickCreate = null }) {
  const revision = ++openRevision;
  currentMode = mode;
  currentModPath = modPath;
  currentInfo = info || null;
  onSaved = cb;
  createTargets = quickCreate?.targets || null;
  setError('');
  for (const id of ['tm-ini', 'tm-values', 'tm-default']) $(id).disabled = false;
  $('tm-save').disabled = true;

  $('tm-var-single').style.display = mode === 'add' ? '' : 'none';
  $('tm-vars-multi').style.display = mode === 'edit' ? '' : 'none';
  syncLabels();
  $('toggle-modal-backdrop').classList.add('show');

  try {
    if (mode === 'add') {
      $('tm-name').value = quickCreate?.name || '';
      $('tm-key').value = '';
      $('tm-back').value = '';
      $('tm-var').value = quickCreate ? quickCreate.name.replace(/[^\p{L}\p{N}_]+/gu, '_') : '';
      $('tm-values').value = quickCreate ? '0,1' : '';
      $('tm-default').value = quickCreate ? '0' : '';
      $('tm-values').disabled = !!quickCreate;
      $('tm-default').disabled = !!quickCreate;
      await populateIniPicker(modPath, quickCreate?.ini, !quickCreate, revision);
      if (revision !== openRevision) return;
      if (quickCreate) {
        const result = await window.pywebview.api.next_toggle_key(modPath).catch((error) => ({ error: String(error) }));
        if (revision !== openRevision) return;
        $('tm-key').value = result.key || '';
        if (!result.key) setError(result.error || t('toggle.noAutomaticKey'));
      }
    } else {
      await populateIniPicker(modPath, info.ini, false, revision);
      if (revision !== openRevision) return;
      const details = await window.pywebview.api.get_toggle_details(modPath, info.ini, info.section);
      if (revision !== openRevision) return;
      if (details.error) {
        setError(details.error);
      } else {
        $('tm-name').value = details.name;
        $('tm-key').value = details.key;
        $('tm-back').value = details.back;
        buildEditVarRows(details.vars);
      }
    }

    if (revision !== openRevision) return;
    $('tm-save').disabled = false;
    $('tm-name').focus();
  } catch (error) {
    if (revision === openRevision) setError(String(error));
  }
}

function parseValues(text) {
  return text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
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
  if (createTargets) options.record_targets = createTargets;

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

  return window.pywebview.api.edit_toggle(currentModPath, currentInfo.ini, currentInfo.section, changes);
}

async function handleSubmit(evt) {
  evt.preventDefault();
  setError('');
  const saveBtn = $('tm-save');
  saveBtn.disabled = true;
  try {
    let result = currentMode === 'add' ? await submitAdd() : await submitEdit(false);

    // Removing a still-gated value can change mesh visibility, so require
    // explicit confirmation before forcing the edit.
    if (result.error && currentMode === 'edit' && result.error_code === 'orphan_existing_gates') {
      const proceed = await confirmDialog(t('toggle.orphanConfirm', { error: result.error }));
      if (proceed) result = await submitEdit(true);
    }

    if (result.error) {
      setError(result.error);
      return;
    }

    const changeType = currentMode === 'add' ? 'add' : 'edit';
    closeModal();
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

window.addEventListener(LANGUAGE_CHANGED, syncLabels);
