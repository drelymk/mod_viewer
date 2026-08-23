// Record mode: assign which meshes are visible at each cycle position of a
// toggle, then stage a rewrite of the INI gates to match.
//
// Repurposes the toggle's own MESHES panel and cycle button rather than a
// modal: cycling the same ⟳ button steps through positions, and the
// checkboxes already on screen are the "visible here" input, pre-populated
// from what's already on disk. Only one session can run at a time -- the
// rest of the Toggle panel and Open Mod are disabled while recording.

import {
  activeMeshes, conditionsSatisfied, getToggleValue, setToggleValue,
  applyMeshVisibility, syncCheckboxes, refreshAll,
} from './visibility.js';
import { notifyMeshStateChanged } from './mesh-state-events.js';
import { alertDialog } from './dialogs.js';
import { cycleValueAt } from './cycle-values.js';

let active = null;    // non-null while a session is in progress
let starting = false; // true from the first click until active is set (or the attempt is abandoned)

export function isRecording() {
  return active !== null;
}

/** Match the API's writable raw names to the source-scoped panel variables. */
function writableVars(vars, rawNames) {
  return vars.filter((v) => rawNames.includes(v.var.split('::').pop()));
}

/** {mesh -> visible} exactly as currently shown — the pre-population for
 * whatever position toggleState currently reflects. A gated mesh is
 * re-evaluated against the (possibly overridden) toggle state; an ungated
 * one keeps its own manual on/off choice untouched, since no position of
 * this toggle has any say over it. */
function snapshotVisibility() {
  const snap = new Map();
  for (const mesh of activeMeshes) {
    const gated = (mesh.userData.conditions || []).length > 0;
    snap.set(mesh, gated ? conditionsSatisfied(mesh) : mesh.userData.manualVisible !== false);
  }
  return snap;
}

function applySnapshot(snap) {
  for (const mesh of activeMeshes) {
    mesh.userData.manualVisible = snap.get(mesh) !== false;
    applyMeshVisibility(mesh, { notify: false });
  }
  notifyMeshStateChanged(activeMeshes);
  syncCheckboxes();
}

function positionLabel() {
  const { current, positions, vars } = active;
  const values = vars.map((v) => `${v.var.split('::').pop()}=${cycleValueAt(v, current)}`).join(', ');
  return `Position ${current + 1} of ${positions} — ${values}`;
}

/**
 * Start recording `info` (a controls.toggles entry). `ctx` is the same
 * {modPath, onChange} the panel already threads through to add/edit/delete.
 * `ui` is the set of DOM handles toggle-panel.js built for this item:
 * {item, row, cycleBtn, valSpan, recordBtn, editBtn, deleteBtn, recordRow,
 * saveBtn, cancelBtn, describe, disableOthers, enableOthers}.
 */
export async function startRecordSession(info, ctx, ui) {
  if (active || starting) return;
  starting = true;

  try {
    const posInfo = await window.pywebview.api.get_record_positions(ctx.modPath, info.ini, info.section);
    if (posInfo.error) {
      await alertDialog('Could not start recording:\n\n' + posInfo.error);
      return;
    }
    const vars = info.cycle_vars || info.vars;
    const writable = writableVars(vars, posInfo.vars || []);
    if (!writable.length || !vars.length || !posInfo.positions) {
      await alertDialog('This toggle has no variable this app can record automatically.');
      return;
    }

    // Undo target for Cancel: every var this section drives, at whatever value
    // it had when recording started (not just the writable ones — a namespaced
    // var in the same section is read-only but still affects visibility).
    const before = vars.map((v) => ({ var: v.var, value: getToggleValue(v.var) }));

    // Pre-populate every position up front from the file's own current
    // combined visibility, never a partial map — an unvisited position must
    // still default to matching what's already on disk.
    const snapshots = [];
    for (let p = 0; p < posInfo.positions; p++) {
      for (const v of vars) setToggleValue(v.var, cycleValueAt(v, p));
      snapshots.push(snapshotVisibility());
    }

    active = {
      info, ctx, ui, vars,
      positions: posInfo.positions, current: 0, snapshots, before,
    };

    for (const v of vars) setToggleValue(v.var, v.values[0]);
    applySnapshot(snapshots[0]);
    enterRecordingUI();
  } finally {
    starting = false;
  }
}

function enterRecordingUI() {
  const { ui } = active;
  ui.recordBtn.style.display = 'none';
  ui.editBtn.style.display = 'none';
  ui.deleteBtn.style.display = 'none';
  ui.row.classList.add('recording');
  ui.valSpan.textContent = positionLabel();

  ui.originalCycleClick = ui.cycleBtn.onclick;
  ui.cycleBtn.onclick = () => advance();
  ui.cycleBtn.title = 'Next position';

  ui.recordRow.style.display = 'flex';
  ui.saveBtn.onclick = () => save();
  ui.cancelBtn.onclick = () => cancel();

  document.getElementById('open-btn').disabled = true;
  ui.disableOthers();
}

function exitRecordingUI() {
  const { ui } = active;
  ui.row.classList.remove('recording');
  ui.recordBtn.style.display = '';
  ui.editBtn.style.display = '';
  ui.deleteBtn.style.display = '';
  ui.recordRow.style.display = 'none';
  ui.cycleBtn.onclick = ui.originalCycleClick;
  ui.cycleBtn.title = 'Cycle value';
  // Cancel just changed toggleState back; Save's caller reloads the whole
  // panel anyway, but refreshing here too keeps this in sync either way.
  ui.valSpan.textContent = ui.describe();

  document.getElementById('open-btn').disabled = false;
  ui.enableOthers();
  active = null;
}

/** Snapshot the checkbox state the user actually left the current position
 * in, before moving off it (Save also calls this, for whatever position it
 * was called while sitting on). */
function captureCurrent() {
  const snap = new Map();
  for (const mesh of activeMeshes) snap.set(mesh, mesh.visible);
  active.snapshots[active.current] = snap;
}

function advance() {
  captureCurrent();
  active.current = (active.current + 1) % active.positions;
  for (const v of active.vars) setToggleValue(v.var, cycleValueAt(v, active.current));
  applySnapshot(active.snapshots[active.current]);
  active.ui.valSpan.textContent = positionLabel();
}

function summarizeSkips(report) {
  const skipped = report.skipped || [];
  if (!skipped.length) return '';
  const shown = skipped.slice(0, 10).map((s) => `line ${s.line ?? '?'}: ${s.reason}`);
  if (skipped.length > shown.length) shown.push(`… and ${skipped.length - shown.length} more`);
  return shown.join('\n');
}

async function save() {
  captureCurrent();
  const { info, ctx, snapshots, ui } = active;

  // Every checked mesh's sources belonging to *this* ini file contribute
  // their line number to that position — a source in some other file is
  // untouched by editing this one, so it's simply not this section's to claim.
  const positionLines = {};
  for (let p = 0; p < snapshots.length; p++) {
    const lines = new Set();
    for (const [mesh, visible] of snapshots[p]) {
      if (!visible) continue;
      for (const src of mesh.userData.sources || []) {
        if (src.ini === info.ini) lines.add(src.line);
      }
    }
    positionLines[p] = [...lines];
  }

  ui.saveBtn.disabled = true;
  try {
    const result = await window.pywebview.api.record_toggle(ctx.modPath, info.ini, info.section, positionLines);
    if (result.error) {
      await alertDialog('Could not save recording:\n\n' + result.error);
      return;
    }
    const summary = summarizeSkips(result.result || {});
    exitRecordingUI();
    if (summary) await alertDialog('Recorded, but review these lines by hand:\n\n' + summary);
    if (ctx.onChange) await ctx.onChange({ type: 'record' });
  } finally {
    ui.saveBtn.disabled = false;
  }
}

function cancel() {
  const { before } = active;
  for (const { var: v, value } of before) setToggleValue(v, value);
  exitRecordingUI();
  refreshAll();
}
