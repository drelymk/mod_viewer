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
  applyMeshVisibility, syncCheckboxes, refreshAll, variablesFromConditions,
} from '../mesh/visibility.js';
import { dnfSatisfied } from './control-state.js';
import { notifyMeshStateChanged } from '../mesh/mesh-state-events.js';
import { alertDialog } from '../ui/dialogs.js';
import { cycleValueAt } from './cycle-values.js';

let active = null;    // non-null while a session is in progress
let starting = false; // true from the first click until active is set (or the attempt is abandoned)

export function isRecording() {
  return active !== null;
}

/** Mark one mesh as an explicit Record target after a user visibility edit. */
export function noteRecordMeshEdit(mesh) {
  if (active && mesh) active.touchedTargets.add(mesh);
}

/** Match the API's writable raw names to the source-scoped panel variables. */
function writableVars(vars, rawNames) {
  return vars.filter((v) => rawNames.includes(v.var.split('::').pop()));
}

function sourceConditions(mesh, source) {
  if (Object.prototype.hasOwnProperty.call(source, 'conditions')) {
    return source.conditions || [];
  }
  // Low-level/legacy payloads may have only one source and put its conditions
  // on the mesh entry. Never use a merged mesh condition for multiple sources.
  const sources = mesh.userData.sources || [];
  return sources.length === 1 ? (mesh.userData.conditions || []) : [];
}

function sourceUsesVars(mesh, source, writableNames) {
  const used = variablesFromConditions(sourceConditions(mesh, source));
  return [...used].some(variable => writableNames.has(variable));
}

function recordSourceConditions(mesh, source, recordVars) {
  return sourceConditions(mesh, source).map(group =>
    group.filter(condition => recordVars.has(condition.var)));
}

function sourceVisible(mesh, source, recordVars = null) {
  const conditions = recordVars
    ? recordSourceConditions(mesh, source, recordVars)
    : sourceConditions(mesh, source);
  return dnfSatisfied(conditions);
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
  const { current, positions, previewVars } = active;
  const values = previewVars
    .map((v) => `${v.var.split('::').pop()}=${cycleValueAt(v, current)}`)
    .join(', ');
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
    const previewVars = info.cycle_vars || info.vars;
    const writable = writableVars(previewVars, posInfo.vars || []);
    if (!writable.length || !previewVars.length || !posInfo.positions) {
      await alertDialog('This toggle has no variable this app can record automatically.');
      return;
    }

    const writableNames = new Set(writable.map(v => v.var));
    const recordVars = new Set(previewVars.map(v => v.var));
    const initialSources = new Set();
    const initialSourceMeshes = new Map();
    for (const mesh of activeMeshes) {
      for (const source of mesh.userData.sources || []) {
        if (source.ini !== info.ini
            || !sourceUsesVars(mesh, source, writableNames)) continue;
        initialSources.add(source);
        initialSourceMeshes.set(source, mesh);
      }
    }

    // Undo target for Cancel: every var this section drives, at whatever value
    // it had when recording started (not just the writable ones — a namespaced
    // var in the same section is read-only but still affects visibility).
    const before = previewVars.map((v) => ({
      var: v.var, value: getToggleValue(v.var),
    }));

    // Pre-populate every position up front from the file's own current
    // combined visibility, never a partial map — an unvisited position must
    // still default to matching what's already on disk.
    const snapshots = [];
    const sourceSnapshots = [];
    for (let p = 0; p < posInfo.positions; p++) {
      for (const v of previewVars) setToggleValue(v.var, cycleValueAt(v, p));
      snapshots.push(snapshotVisibility());
      const sourceSnap = new Map();
      for (const [source, mesh] of initialSourceMeshes) {
        // An untouched source should contribute only the selected key's own
        // visibility gate. Unrelated outer conditions describe the current
        // scene, not what this Record session is authoring.
        sourceSnap.set(source, sourceVisible(mesh, source, recordVars));
      }
      sourceSnapshots.push(sourceSnap);
    }

    active = {
      info, ctx, ui, previewVars, writableVars: writable,
      positions: posInfo.positions, current: 0, snapshots, sourceSnapshots,
      before, initialSources, touchedTargets: new Set(),
    };

    for (const v of previewVars) setToggleValue(v.var, v.values[0]);
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
  for (const v of active.previewVars) {
    setToggleValue(v.var, cycleValueAt(v, active.current));
  }
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

function recordTargetRef(mesh, src) {
  return {
    ini: src.ini,
    line: src.line,
    section: src.section,
    drawindexed: mesh.userData.assetEntry?.drawindexed,
    occurrence: src.occurrence,
  };
}

async function save() {
  captureCurrent();
  const { info, ctx, snapshots, ui } = active;

  const targets = new Map();
  for (const mesh of activeMeshes) {
    for (const src of mesh.userData.sources || []) {
      if (src.ini !== info.ini) continue;
      if (active.initialSources.has(src) || active.touchedTargets.has(mesh)) {
        targets.set(src, mesh);
      }
    }
  }
  const targetRefs = [...targets].map(([src, mesh]) => recordTargetRef(mesh, src));

  // Only visible target meshes contribute their source lines at a position.
  // A source in some other file is untouched by editing this one, so it is
  // never part of this section's target scope.
  const positionLines = {};
  for (let p = 0; p < snapshots.length; p++) {
    const lines = new Set();
    for (const [src, mesh] of targets) {
      const visible = active.touchedTargets.has(mesh)
        ? snapshots[p].get(mesh)
        : active.sourceSnapshots[p].get(src);
      if (!visible) continue;
      lines.add(src.line);
    }
    positionLines[p] = [...lines].sort((a, b) => a - b);
  }
  const sortedTargetRefs = targetRefs.sort(
    (a, b) => Number(a.line) - Number(b.line));

  ui.saveBtn.disabled = true;
  try {
    const result = await window.pywebview.api.record_toggle(
      ctx.modPath, info.ini, info.section, positionLines, sortedTargetRefs);
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
