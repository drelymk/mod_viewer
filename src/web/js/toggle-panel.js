// The Toggle panel (right): cycle-type ini [Key...] sections that gate mesh
// visibility.
//
// Keyed by key section rather than by variable, because one section can cycle
// several variables at once and 3DMigoto advances them all on a single
// keypress — so they share a cycle position rather than each stepping
// independently.

import { refreshAll, setToggleValue, getToggleValue } from './visibility.js';
import { openToggleModal } from './toggle-modal.js';
import { startRecordSession } from './record-session.js';
import { alertDialog, confirmDialog } from './dialogs.js';
import { registerViewSync, syncView } from './view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from './panel-utils.js';
import { createIcon } from './ui-icons.js';
import { cyclePositionCount, cycleValueAt } from './cycle-values.js';

/** Variable names carry a "source::" prefix in multi-ini folders. */
function displayName(variable) {
  return variable.split('::').pop();
}

/** Resolve a key section's shared cycle position from all variables it drives.
 * A single variable may repeat a value at several positions (for example
 * Dress=1,0,0), so looking up only the first variable with indexOf() can get
 * permanently stuck on the first duplicate.  Prefer the position we last
 * applied when the complete tuple itself is duplicated. */
function findCyclePosition(vars, positions, preferred = -1) {
  const matches = (position) => vars.every(v =>
    cycleValueAt(v, position) === getToggleValue(v.var));
  if (preferred >= 0 && preferred < positions && matches(preferred)) return preferred;
  // Duplicate complete tuples use the last position, matching the app's
  // toggle identity invariant and Record/PRESENT tie-breaking.
  for (let position = positions - 1; position >= 0; position--) {
    if (matches(position)) return position;
  }
  return -1;
}

// Rebuilt on every buildTogglePanel() call; the static "＋ Add" button in the
// panel header is wired once below and reads the latest ctx at click time.
let currentCtx = { modPath: null, onChange: null };
let valueSyncers = [];

export function refreshToggleValues() {
  syncView('toggle-panel');
}

document.getElementById('toggle-add-btn').addEventListener('click', () => {
  if (!currentCtx.modPath) return;
  openToggleModal({ mode: 'add', modPath: currentCtx.modPath, onSaved: currentCtx.onChange });
});

/** Disable/enable every button in the panel except `exceptItem`'s own, plus
 * the header's Add button — used while a recording session is in progress on
 * one item, so nothing else can reload or edit out from under its snapshots. */
function setOthersEnabled(enabled, exceptItem) {
  document.getElementById('toggle-add-btn').disabled = !enabled;
  document.querySelectorAll('#toggle-list button').forEach((btn) => {
    if (exceptItem && exceptItem.contains(btn)) return;
    btn.disabled = !enabled;
  });
}

function summarizeReport(report) {
  const lines = [
    ...(report.always_false_gates || []).map((l) => `always-false gate at line ${l}`),
    ...(report.always_true_gates || []).map((l) => `always-true gate at line ${l}`),
    ...(report.unsafe_gates || []).map((l) => `unresolved gate at line ${l}`),
  ];
  return lines.join('\n');
}

async function handleDelete(info, ctx) {
  const ok = await confirmDialog(
    `Delete toggle "${info.name}"?\n\nThis only stages the change — nothing is written to the ` +
    `ini file until you click Export.`);
  if (!ok) return;

  const result = await window.pywebview.api.delete_toggle(ctx.modPath, info.ini, info.section);
  if (result.error) {
    await alertDialog('Could not delete toggle:\n\n' + result.error);
    return;
  }
  const summary = summarizeReport(result.result || {});
  if (summary) await alertDialog('Toggle deleted, but review these lines by hand:\n\n' + summary);
  if (ctx.onChange) await ctx.onChange({ type: 'delete' });
}

function buildToggleItem(info, ctx) {
  for (const v of info.vars) {
    if (getToggleValue(v.var) === undefined) setToggleValue(v.var, v.default);
  }

  const positions = cyclePositionCount(info.vars);
  let cyclePosition = findCyclePosition(info.vars, positions);

  const item = document.createElement('div');
  item.className = 'toggle-item';

  const hdr = document.createElement('div');
  hdr.className = 'toggle-hdr';

  const left = document.createElement('span');
  left.className = 'toggle-hdr-left';
  const nameSpan = document.createElement('span');
  nameSpan.className = 'toggle-name';
  nameSpan.textContent = info.name;
  left.appendChild(nameSpan);
  if (info.key) {
    const keyBadge = document.createElement('span');
    keyBadge.className = 'toggle-key';
    keyBadge.textContent = info.key;
    left.appendChild(keyBadge);
  }
  if (info.wired === false) {
    const warnBadge = document.createElement('span');
    warnBadge.className = 'toggle-unwired-badge';
    warnBadge.appendChild(createIcon('diagnostics'));
    warnBadge.title = 'Not wired to any mesh yet — click ⏺ Record below and check/uncheck ' +
      'meshes at each position to assign what this toggle shows. Export is disabled until ' +
      'this toggle is wired (or deleted).';
    left.appendChild(warnBadge);
  }
  hdr.appendChild(left);

  const editBtn = document.createElement('button');
  editBtn.className = 'toggle-icon-btn';
  editBtn.appendChild(createIcon('edit'));
  editBtn.title = 'Edit toggle';
  editBtn.setAttribute('aria-label', 'Edit toggle');
  editBtn.addEventListener('click', () => {
    openToggleModal({ mode: 'edit', modPath: ctx.modPath, info, onSaved: ctx.onChange });
  });

  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'toggle-icon-btn';
  deleteBtn.appendChild(createIcon('delete'));
  deleteBtn.title = 'Delete toggle';
  deleteBtn.setAttribute('aria-label', 'Delete toggle');
  deleteBtn.addEventListener('click', () => handleDelete(info, ctx));

  const recordBtn = document.createElement('button');
  recordBtn.className = 'toggle-icon-btn';
  recordBtn.appendChild(createIcon('record'));
  recordBtn.title = 'Record which meshes show at each position';
  recordBtn.setAttribute('aria-label', 'Record toggle mesh visibility');

  const actions = document.createElement('span');
  actions.className = 'toggle-actions';
  actions.append(editBtn, deleteBtn, recordBtn);
  hdr.appendChild(actions);

  item.appendChild(hdr);

  // A key can drive one or several vars; always name them so the value is
  // never ambiguous.
  const describe = () => info.vars
    .map(v => `${displayName(v.var)}=${getToggleValue(v.var)}`)
    .join(', ');

  const row = document.createElement('div');
  row.className = 'toggle-row';

  const btn = document.createElement('button');
  btn.className = 'toggle-cycle-btn';
  btn.appendChild(createIcon('cycle'));
  btn.title = 'Cycle value';
  btn.setAttribute('aria-label', 'Cycle toggle value');

  const valSpan = document.createElement('span');
  valSpan.className = 'toggle-value';
  valSpan.textContent = describe();

  // A plain assignable slot (not addEventListener) so a recording session can
  // swap this button's behaviour to "advance position" and restore it again
  // afterwards without both handlers firing at once.
  btn.onclick = () => {
    cyclePosition = findCyclePosition(info.vars, positions, cyclePosition);
    const next = (cyclePosition + 1) % positions;
    for (const v of info.vars) {
      setToggleValue(v.var, cycleValueAt(v, next));
    }
    cyclePosition = next;
    refreshAll();
  };
  valueSyncers.push(() => {
    cyclePosition = findCyclePosition(info.vars, positions, cyclePosition);
    valSpan.textContent = describe();
  });

  row.append(btn, valSpan);
  item.appendChild(row);

  // Hidden until a recording session is active on this item (see
  // record-session.js); Save/Cancel replace Edit/Delete/Record for the
  // duration.
  const recordRow = document.createElement('div');
  recordRow.className = 'toggle-record-row';
  recordRow.style.display = 'none';
  const saveBtn = document.createElement('button');
  saveBtn.className = 'toggle-record-save';
  saveBtn.textContent = 'Save';
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'toggle-record-cancel';
  cancelBtn.textContent = 'Cancel';
  recordRow.append(saveBtn, cancelBtn);
  item.appendChild(recordRow);

  recordBtn.addEventListener('click', () => {
    startRecordSession(info, ctx, {
      item, row, cycleBtn: btn, valSpan, recordBtn, editBtn, deleteBtn, recordRow, saveBtn, cancelBtn,
      describe,
      disableOthers: () => setOthersEnabled(false, item),
      enableOthers: () => setOthersEnabled(true, item),
    });
  });

  return item;
}

/**
 * Build the panel from the structured controls.toggles model.
 *
 * Entries carry a `source` (ini tag) when the mod folder has multiple inis —
 * same-named keys are grouped under a collapsible per-ini sub-section instead
 * of lengthening every toggle's display name with a prefix.
 *
 * `ctx` carries what the add/edit/delete actions need: `modPath` (which edit
 * session to update) and `onChange` (called after a successful staged change
 * to refresh the model and controls). The panel is shown whenever a mod
 * is loaded — even with zero toggles — since "Add" must stay reachable.
 */
export function buildTogglePanel(toggles, ctx = {}) {
  currentCtx = { modPath: ctx.modPath || null, onChange: ctx.onChange || null };

  const list = document.getElementById('toggle-list');
  const panel = document.getElementById('toggle-panel');
  list.innerHTML = '';
  valueSyncers = [];
  registerViewSync('toggle-panel', () => {
    valueSyncers.forEach(sync => sync());
  });

  if (!currentCtx.modPath) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';

  const sections = Object.keys(toggles || {});
  if (!sections.length) {
    const empty = document.createElement('div');
    empty.className = 'toggle-empty';
    empty.textContent = 'No toggles yet — click Add to create one.';
    list.appendChild(empty);
    return;
  }

  // Sections with no source (single-ini mods) go in a '' bucket rendered flat,
  // with no header.
  const bySource = groupKeysBySource(toggles, sections);
  const sources = Object.keys(bySource);
  const multiSource = usesSourceSections(bySource);

  for (const src of sources) {
    const container = (multiSource && src) ? buildSourceSection(src, list) : list;
    for (const section of bySource[src]) {
      container.appendChild(buildToggleItem(toggles[section], currentCtx));
    }
  }

  refreshAll();
}
