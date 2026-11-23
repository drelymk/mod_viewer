// Toggle panel for cycle-type [Key...] sections. One section may drive several
// variables, which share a cycle position.

import { refreshAll, setToggleValue, getToggleValue } from '../mesh/visibility.js';
import { openToggleModal } from '../editing/toggle-modal.js';
import { startRecordSession } from '../editing/record-session.js';
import { alertDialog, confirmDialog } from '../ui/dialogs.js';
import { registerViewSync, syncView } from '../scene/view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from '../ui/panel-utils.js';
import { createIcon } from '../ui/ui-icons.js';
import { showMeshContextMenu } from './mesh-panel-context-menu.js';
import { cyclePositionCount, cycleValueAt } from '../editing/cycle-values.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

/** Variable names carry a "source::" prefix in multi-ini folders. */
function displayName(variable) {
  return variable.split('::').pop();
}

/** Resolve the shared position from all vars, preferring the last match for duplicate tuples. */
function findCyclePosition(vars, positions, preferred = -1) {
  const matches = (position) => vars.every((v) => cycleValueAt(v, position) === getToggleValue(v.var));
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
let labelSyncers = [];

window.addEventListener(LANGUAGE_CHANGED, () => {
  labelSyncers.forEach((sync) => sync());
  syncView('toggle-panel');
});

export function refreshToggleValues() {
  syncView('toggle-panel');
}

document.getElementById('toggle-add-btn').addEventListener('click', () => {
  if (!currentCtx.modPath) return;
  openToggleModal({ mode: 'add', modPath: currentCtx.modPath, onSaved: currentCtx.onChange });
});

/** Disable other controls while one toggle is being recorded. */
function setOthersEnabled(enabled, exceptItem) {
  document.getElementById('toggle-add-btn').disabled = !enabled;
  document.querySelectorAll('#toggle-list button').forEach((btn) => {
    if (exceptItem && exceptItem.contains(btn)) return;
    btn.disabled = !enabled;
  });
}

function summarizeReport(report) {
  const lines = [
    ...(report.always_false_gates || []).map((line) => t('toggle.reportAlwaysFalse', { line })),
    ...(report.always_true_gates || []).map((line) => t('toggle.reportAlwaysTrue', { line })),
    ...(report.unsafe_gates || []).map((line) => t('toggle.reportUnsafe', { line })),
  ];
  return lines.join('\n');
}

async function handleDelete(info, ctx) {
  const ok = await confirmDialog(t('toggle.deleteConfirm', { name: info.name }));
  if (!ok) return;

  const result = await window.pywebview.api.delete_toggle(ctx.modPath, info.ini, info.section);
  if (result.error) {
    await alertDialog(t('toggle.deleteError', { detail: result.error }));
    return;
  }
  const summary = summarizeReport(result.result || {});
  if (summary) await alertDialog(t('toggle.deletedReview', { detail: summary }));
  if (ctx.onChange) await ctx.onChange({ type: 'delete' });
}

function buildToggleItem(info, ctx) {
  const cycleVars = info.cycle_vars?.length ? info.cycle_vars : info.vars;
  for (const v of cycleVars) {
    if (getToggleValue(v.var) === undefined) setToggleValue(v.var, v.default);
  }

  const positions = cyclePositionCount(cycleVars);
  let cyclePosition = findCyclePosition(cycleVars, positions);

  const item = document.createElement('div');
  item.className = 'toggle-item';

  const row = document.createElement('div');
  row.className = 'toggle-row';

  // Step controls lead the row so they sit at a fixed x position regardless
  // of name/value length; both are always present.
  const prevBtn = document.createElement('button');
  prevBtn.type = 'button';
  prevBtn.className = 'toggle-step-btn';
  prevBtn.appendChild(createIcon('chevron-left'));
  prevBtn.title = t('toggle.previous');
  prevBtn.setAttribute('aria-label', t('toggle.previous'));
  prevBtn.addEventListener('click', () => {
    cyclePosition = findCyclePosition(cycleVars, positions, cyclePosition);
    const previous = cyclePosition < 0 ? positions - 1 : (cyclePosition - 1 + positions) % positions;
    for (const v of cycleVars) {
      setToggleValue(v.var, cycleValueAt(v, previous));
    }
    cyclePosition = previous;
    refreshAll();
  });
  row.appendChild(prevBtn);

  const btn = document.createElement('button');
  btn.className = 'toggle-cycle-btn';
  btn.appendChild(createIcon('chevron-right'));
  btn.title = t('toggle.cycle');
  btn.setAttribute('aria-label', t('toggle.cycle'));

  // A plain assignable slot (not addEventListener) so a recording session can
  // swap this button's behaviour to "advance position" and restore it again
  // afterwards without both handlers firing at once.
  btn.onclick = () => {
    cyclePosition = findCyclePosition(cycleVars, positions, cyclePosition);
    const next = (cyclePosition + 1) % positions;
    for (const v of cycleVars) {
      setToggleValue(v.var, cycleValueAt(v, next));
    }
    cyclePosition = next;
    refreshAll();
  };
  row.appendChild(btn);

  const left = document.createElement('span');
  left.className = 'toggle-hdr-left';
  const nameSpan = document.createElement('span');
  nameSpan.className = 'toggle-name';
  nameSpan.textContent = info.name;
  left.appendChild(nameSpan);
  let warnBadge = null;
  if (info.key) {
    const keyBadge = document.createElement('span');
    keyBadge.className = 'toggle-key';
    keyBadge.textContent = info.key;
    left.appendChild(keyBadge);
  }
  if (info.wired === false) {
    warnBadge = document.createElement('span');
    warnBadge.className = 'toggle-unwired-badge';
    warnBadge.appendChild(createIcon('diagnostics'));
    warnBadge.dataset.i18nTitle = 'toggle.unwired';
    warnBadge.title = t('toggle.unwired');
    left.appendChild(warnBadge);
  }
  row.appendChild(left);

  // A key can drive one or several vars; always name them so the value is
  // never ambiguous.
  const describe = () => info.vars.map((v) => `${displayName(v.var)}=${getToggleValue(v.var)}`).join(', ');

  const valSpan = document.createElement('span');
  valSpan.className = 'toggle-value';
  valSpan.textContent = describe();
  valSpan.title = valSpan.textContent;
  row.appendChild(valSpan);

  valueSyncers.push(() => {
    cyclePosition = findCyclePosition(cycleVars, positions, cyclePosition);
    valSpan.textContent = describe();
    valSpan.title = valSpan.textContent;
  });

  const moreBtn = document.createElement('button');
  moreBtn.type = 'button';
  moreBtn.className = 'toggle-icon-btn toggle-more-btn';
  moreBtn.appendChild(createIcon('more'));
  moreBtn.setAttribute('aria-haspopup', 'menu');
  moreBtn.title = t('toggle.actions');
  moreBtn.setAttribute('aria-label', t('toggle.actions'));

  const actions = document.createElement('span');
  actions.className = 'toggle-actions';
  actions.appendChild(moreBtn);
  row.appendChild(actions);

  item.appendChild(row);

  // Hidden until a recording session is active on this item (see
  // record-session.js); Save/Cancel replace the row controls for the
  // duration.
  const recordRow = document.createElement('div');
  recordRow.className = 'toggle-record-row';
  recordRow.style.display = 'none';
  const saveBtn = document.createElement('button');
  saveBtn.className = 'toggle-record-save';
  saveBtn.dataset.i18n = 'common.save';
  saveBtn.textContent = t('common.save');
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'toggle-record-cancel';
  cancelBtn.dataset.i18n = 'common.cancel';
  cancelBtn.textContent = t('common.cancel');
  recordRow.append(saveBtn, cancelBtn);
  item.appendChild(recordRow);

  const startRecording = () =>
    startRecordSession(info, ctx, {
      item,
      row,
      cycleBtn: btn,
      valSpan,
      actionsControl: actions,
      prevBtn,
      recordRow,
      saveBtn,
      cancelBtn,
      describe,
      disableOthers: () => setOthersEnabled(false, item),
      enableOthers: () => setOthersEnabled(true, item),
    });

  moreBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    const rect = moreBtn.getBoundingClientRect();
    showMeshContextMenu({ clientX: rect.left, clientY: rect.bottom + 4 }, [
      {
        key: 'toggle.edit',
        className: 'toggle-context-action',
        run: () => openToggleModal({ mode: 'edit', modPath: ctx.modPath, info, onSaved: ctx.onChange }),
      },
      {
        key: 'toggle.delete',
        className: 'toggle-context-action',
        run: () => handleDelete(info, ctx),
      },
      {
        key: 'toggle.recordAria',
        className: 'toggle-context-action',
        reason: 'toggle.record',
        run: () => startRecording(),
      },
    ]);
  });

  const syncLabels = () => {
    moreBtn.title = t('toggle.actions');
    moreBtn.setAttribute('aria-label', t('toggle.actions'));
    prevBtn.title = t('toggle.previous');
    prevBtn.setAttribute('aria-label', t('toggle.previous'));
    if (!item.classList.contains('recording')) {
      btn.title = t('toggle.cycle');
      btn.setAttribute('aria-label', t('toggle.cycle'));
    }
    if (warnBadge) warnBadge.title = t('toggle.unwired');
    saveBtn.textContent = t('common.save');
    cancelBtn.textContent = t('common.cancel');
  };
  labelSyncers.push(syncLabels);

  return item;
}

/** Render structured toggle data, grouping duplicate keys by source INI. */
export function buildTogglePanel(toggles, ctx = {}) {
  currentCtx = { modPath: ctx.modPath || null, onChange: ctx.onChange || null };

  const list = document.getElementById('toggle-list');
  const panel = document.getElementById('toggle-panel');
  list.innerHTML = '';
  valueSyncers = [];
  labelSyncers = [];
  registerViewSync('toggle-panel', () => {
    valueSyncers.forEach((sync) => sync());
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
    const syncEmptyLabel = () => {
      empty.textContent = t('toggle.noToggles');
    };
    syncEmptyLabel();
    labelSyncers.push(syncEmptyLabel);
    list.appendChild(empty);
    return;
  }

  // Sections with no source (single-ini mods) go in a '' bucket rendered flat,
  // with no header.
  const bySource = groupKeysBySource(toggles, sections);
  const sources = Object.keys(bySource);
  const multiSource = usesSourceSections(bySource);

  for (const src of sources) {
    const container = multiSource && src ? buildSourceSection(src, list) : list;
    for (const section of bySource[src]) {
      container.appendChild(buildToggleItem(toggles[section], currentCtx));
    }
  }
}
