// One logical PRESENT cycle, authored atomically across every eligible INI.

import { getToggleValue, refreshAll, setToggleValue } from './visibility.js';
import { alertDialog, confirmDialog, inputConfirmDialog } from './dialogs.js';
import { openPresentModal, presentSnapshots } from './present-modal.js';
import { registerViewSync } from './view-sync.js';
import { createIcon } from './ui-icons.js';

const $ = (id) => document.getElementById(id);
const MAX_PRESENTS = 10;
let current = { modPath: null, present: null, onChange: null };
let pendingSelection = null;
let syncCurrentValue = () => {};

async function removeKey() {
  const confirmed = await confirmDialog(
    'Delete the PRESENT key from every participating INI?\n\n' +
    'This only stages the change; the INI is not written until Export.');
  if (!confirmed) return;
  const result = await window.pywebview.api.delete_present(current.modPath);
  if (result.error) return alertDialog('Could not delete PRESENT:\n\n' + result.error);
  if (current.onChange) await current.onChange();
}

function closeKeyMenu() {
  const menu = $('present-key-menu');
  const action = $('present-action-btn');
  if (!menu || !action) return;
  menu.hidden = true;
  action.setAttribute('aria-expanded', 'false');
}

function openKeyMenu() {
  const menu = $('present-key-menu');
  const action = $('present-action-btn');
  if (!menu || !action) return;
  menu.hidden = !menu.hidden;
  action.setAttribute('aria-expanded', String(!menu.hidden));
}

$('present-action-btn').addEventListener('click', event => {
  event.stopPropagation();
  openKeyMenu();
});
$('present-key-add').addEventListener('click', () => {
  closeKeyMenu();
  if (!(current.present?.target_inis || []).length) return;
  openPresentModal({ mode: 'add', modPath: current.modPath,
    present: current.present, onSaved: current.onChange });
});
$('present-key-edit').addEventListener('click', () => {
  closeKeyMenu();
  const item = current.present?.item;
  if (!item) return;
  const incomplete = (item.missing_inis || []).length > 0;
  openPresentModal({ mode: incomplete ? 'complete' : 'edit', modPath: current.modPath,
    present: current.present, item, onSaved: current.onChange });
});
$('present-key-remove').addEventListener('click', () => {
  closeKeyMenu();
  if (current.present?.item) void removeKey();
});
document.addEventListener('click', event => {
  if (!event.target.closest('.present-key-actions')) closeKeyMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeKeyMenu();
});

async function capture(item, position, name, allowDuplicate = false) {
  const result = await window.pywebview.api.capture_present(
    current.modPath, presentSnapshots(current.present), name, position, allowDuplicate);
  if (result.warning) {
    const labels = (result.duplicate_positions || [])
      .map((index) => item.names[index] || `Present ${index + 1}`).join(', ');
    const confirmed = await confirmDialog(
      `These variable values are the same as ${labels || 'another present'}.\n\nSave anyway?`);
    return confirmed ? capture(item, position, name, true) : null;
  }
  if (result.error) {
    await alertDialog(`Could not ${position === null ? 'create' : 'edit'} present:\n\n${result.error}`);
    return null;
  }
  return result;
}

function buildItem(item) {
  let position = 0;
  const synchronized = !item.sync_error && item.count > 0;
  const restoreSelection = pendingSelection &&
    synchronized &&
    pendingSelection.modPath === current.modPath &&
    pendingSelection.position >= 0 && pendingSelection.position < item.count;
  if (restoreSelection) position = pendingSelection.position;
  if (synchronized) {
    for (const variable of item.vars) {
      setToggleValue(variable.var, variable.values[position]);
    }
  }
  if (restoreSelection) {
    pendingSelection = null;
  }

  const wrap = document.createElement('div');
  wrap.className = 'toggle-item';
  const header = document.createElement('div');
  header.className = 'toggle-hdr';
  const fields = document.createElement('div');
  fields.className = 'present-fields';
  for (const [label, value] of [['Key', item.key], ['Back', item.back]]) {
    if (!value) continue;
    const badge = document.createElement('span');
    badge.className = 'toggle-key';
    badge.textContent = `${label}: ${value}`;
    fields.appendChild(badge);
  }
  header.append(fields);

  const row = document.createElement('div');
  row.className = 'toggle-row';
  const cycle = document.createElement('button');
  cycle.className = 'toggle-cycle-btn';
  cycle.appendChild(createIcon('cycle'));
  cycle.title = 'Cycle present';
  cycle.setAttribute('aria-label', 'Cycle PRESENT');
  cycle.disabled = !synchronized;
  const name = document.createElement('span');
  name.className = 'toggle-value';
  const showName = () => {
    name.textContent = synchronized
      ? (item.names[position] || `Present ${position + 1}`)
      : 'Unavailable';
  };
  const sync = () => {
    if (synchronized) {
      const matches = candidate => item.vars.every(variable =>
        variable.values[candidate] === getToggleValue(variable.var));
      if (!matches(position)) {
        const next = Array.from({ length: item.count }, (_, index) => index)
          .find(matches);
        if (next !== undefined) position = next;
      }
    }
    showName();
  };
  showName();
  cycle.onclick = () => {
    position = (position + 1) % item.count;
    for (const variable of item.vars) {
      setToggleValue(variable.var, variable.values[position]);
    }
    refreshAll();
  };
  row.append(cycle, name);

  if (!synchronized) {
    const error = document.createElement('div');
    error.className = 'present-sync-error';
    error.textContent = item.sync_error || 'PRESENT has no usable positions.';
    wrap.append(header, row, error);
    return { wrap, sync };
  }

  const actions = document.createElement('div');
  actions.className = 'present-buttons present-author-actions';
  const add = document.createElement('button');
  add.textContent = 'New';
  add.disabled = item.count >= MAX_PRESENTS || !(item.capture_vars || []).length;
  add.title = item.count >= MAX_PRESENTS
    ? `A PRESENT key is limited to ${MAX_PRESENTS} presents.`
    : (add.disabled ? 'This mod has no key or menu toggle values to capture.' : '');
  add.addEventListener('click', async () => {
    const defaultName = `Present ${item.count + 1}`;
    const chosen = await inputConfirmDialog(
      'Create a new present from the current key and menu toggle states?', defaultName);
    if (chosen === null) return;
    if (!chosen) return alertDialog('A present name is required.');
    if (!await capture(item, null, chosen)) return;
    pendingSelection = { modPath: current.modPath, position: item.count };
    if (current.onChange) await current.onChange();
  });
  const replace = document.createElement('button');
  replace.textContent = 'Update';
  replace.disabled = !(item.capture_vars || []).length;
  replace.title = replace.disabled ? 'This mod has no key or menu toggle states to capture.'
    : 'Replace this present with the current key and menu toggle states.';
  replace.addEventListener('click', async () => {
    const chosen = await inputConfirmDialog(
      `Replace ${item.names[position] || `Present ${position + 1}`} with the current key and menu toggle states?`,
      item.names[position] || `Present ${position + 1}`);
    if (chosen === null) return;
    if (!chosen) return alertDialog('A present name is required.');
    if (!await capture(item, position, chosen)) return;
    pendingSelection = { modPath: current.modPath, position };
    if (current.onChange) await current.onChange();
  });
  const remove = document.createElement('button');
  remove.textContent = 'Delete';
  remove.disabled = item.count <= 1;
  remove.title = remove.disabled ? 'The only present cannot be deleted.' : '';
  remove.addEventListener('click', async () => {
    const label = item.names[position] || `Present ${position + 1}`;
    if (!await confirmDialog(`Delete ${label}?\n\nThis only stages the change until Export.`)) return;
    const result = await window.pywebview.api.delete_present_position(
      current.modPath, position);
    if (result.error) return alertDialog('Could not delete present:\n\n' + result.error);
    pendingSelection = {
      modPath: current.modPath,
      position: Math.min(position, item.count - 2),
    };
    if (current.onChange) await current.onChange();
  });
  actions.append(add, replace, remove);
  wrap.append(header, row, actions);
  return { wrap, sync };
}

export function buildPresentPanel(present, context = {}) {
  current = { modPath: context.modPath || null, present: present || {},
    onChange: context.onChange || null };
  const panel = $('present-panel');
  const list = $('present-list');
  const action = $('present-action-btn');
  list.innerHTML = '';
  syncCurrentValue = () => {};
  registerViewSync('present-panel', () => syncCurrentValue());
  if (!current.modPath) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  const item = current.present.item;
  const incomplete = !!item && (item.missing_inis || []).length > 0;
  const canAdd = !item && (current.present.target_inis || []).length > 0;
  const addKey = $('present-key-add');
  const editKey = $('present-key-edit');
  const removeKeyButton = $('present-key-remove');
  action.replaceChildren(createIcon('more'));
  action.title = 'More PRESENT actions';
  action.setAttribute('aria-label', action.title);
  action.disabled = false;
  addKey.hidden = !!item;
  addKey.disabled = !canAdd;
  editKey.hidden = !item;
  editKey.disabled = !item;
  editKey.textContent = incomplete ? 'Complete PRESENT' : 'Edit key binding';
  removeKeyButton.hidden = !item;
  removeKeyButton.disabled = !item;

  if (!item) {
    const empty = document.createElement('div');
    empty.className = 'toggle-empty';
    empty.textContent = canAdd
      ? 'No presents yet - click Add to create one.'
      : 'No key or menu toggle is available for PRESENT.';
    list.appendChild(empty);
    return;
  }

  const built = buildItem(item);
  syncCurrentValue = built.sync;
  list.appendChild(built.wrap);
  refreshAll();
}
