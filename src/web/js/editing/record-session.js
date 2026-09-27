// Record captures per-position mesh visibility and stages the matching INI
// gate rewrite. Only one session can run at a time.

import {
  activeMeshes,
  conditionsSatisfied,
  getToggleValue,
  setToggleValue,
  applyMeshVisibility,
  syncCheckboxes,
  refreshAll,
  variablesFromConditions,
} from '../mesh/visibility.js';
import { dnfSatisfied } from './control-state.js';
import { notifyMeshStateChanged } from '../mesh/mesh-state-events.js';
import { alertDialog } from '../ui/dialogs.js';
import { cycleValueAt } from './cycle-values.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

let active = null; // non-null while a session is in progress
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
  return sources.length === 1 ? mesh.userData.conditions || [] : [];
}

function sourceUsesVars(mesh, source, writableNames) {
  const used = variablesFromConditions(sourceConditions(mesh, source));
  return [...used].some((variable) => writableNames.has(variable));
}

function recordSourceConditions(mesh, source, recordVars) {
  return sourceConditions(mesh, source).map((group) => group.filter((condition) => recordVars.has(condition.var)));
}

function sourceVisible(mesh, source, recordVars = null) {
  const conditions = recordVars ? recordSourceConditions(mesh, source, recordVars) : sourceConditions(mesh, source);
  return dnfSatisfied(conditions);
}

/** Snapshot visibility: re-evaluate gated meshes, but preserve manual state for ungated ones. */
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
  const values = previewVars.map((v) => `${v.var.split('::').pop()}=${cycleValueAt(v, current)}`).join(', ');
  return t('record.position', {
    current: current + 1,
    positions,
    values,
  });
}

/** Start a Record session for one Toggle panel item. */
export async function startRecordSession(info, ctx, ui) {
  if (active || starting) return;
  starting = true;

  try {
    const posInfo = await window.pywebview.api.get_record_positions(ctx.modPath, info.ini, info.section);
    if (posInfo.error) {
      await alertDialog(t('record.startError', { detail: posInfo.error }));
      return;
    }
    const previewVars = info.cycle_vars || info.vars;
    const writable = writableVars(previewVars, posInfo.vars || []);
    if (!writable.length || !previewVars.length || !posInfo.positions) {
      await alertDialog(t('record.noAutomaticVariable'));
      return;
    }

    const writableNames = new Set(writable.map((v) => v.var));
    const recordVars = new Set(previewVars.map((v) => v.var));
    const initialSources = new Set();
    const initialSourceMeshes = new Map();
    for (const mesh of activeMeshes) {
      for (const source of mesh.userData.sources || []) {
        if (source.ini !== info.ini || !sourceUsesVars(mesh, source, writableNames)) continue;
        initialSources.add(source);
        initialSourceMeshes.set(source, mesh);
      }
    }

    // Cancel restores every co-driven value, including read-only namespaced vars.
    const before = previewVars.map((v) => ({
      var: v.var,
      value: getToggleValue(v.var),
    }));

    // Initialize every position from file state so untouched positions remain unchanged.
    const snapshots = [];
    const sourceSnapshots = [];
    for (let p = 0; p < posInfo.positions; p++) {
      for (const v of previewVars) setToggleValue(v.var, cycleValueAt(v, p));
      snapshots.push(snapshotVisibility());
      const sourceSnap = new Map();
      for (const [source, mesh] of initialSourceMeshes) {
        // Record only this key's gate; unrelated ancestors describe the current scene.
        sourceSnap.set(source, sourceVisible(mesh, source, recordVars));
      }
      sourceSnapshots.push(sourceSnap);
    }

    active = {
      info,
      ctx,
      ui,
      previewVars,
      writableVars: writable,
      positions: posInfo.positions,
      current: 0,
      snapshots,
      sourceSnapshots,
      before,
      initialSources,
      touchedTargets: new Set(),
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
  ui.cycleBtn.title = t('record.nextPosition');

  ui.recordRow.style.display = 'flex';
  ui.saveBtn.onclick = () => save();
  ui.cancelBtn.onclick = () => cancel();

  document.getElementById('open-btn').disabled = true;
  ui.disableOthers();
  window.dispatchEvent(
    new CustomEvent('mod-viewer-recording-state', {
      detail: { recording: true },
    }),
  );
}

function exitRecordingUI() {
  const { ui } = active;
  ui.row.classList.remove('recording');
  ui.recordBtn.style.display = '';
  ui.editBtn.style.display = '';
  ui.deleteBtn.style.display = '';
  ui.recordRow.style.display = 'none';
  ui.cycleBtn.onclick = ui.originalCycleClick;
  ui.cycleBtn.title = t('record.cycleValue');
  // Cancel just changed toggleState back; Save's caller reloads the whole
  // panel anyway, but refreshing here too keeps this in sync either way.
  ui.valSpan.textContent = ui.describe();

  document.getElementById('open-btn').disabled = false;
  ui.enableOthers();
  active = null;
  window.dispatchEvent(
    new CustomEvent('mod-viewer-recording-state', {
      detail: { recording: false },
    }),
  );
}

/** Capture the current checkbox state before advancing or saving. */
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
  const shown = skipped.slice(0, 10).map((s) =>
    t('record.line', {
      line: s.line ?? '?',
      reason: skipReason(s),
    }),
  );
  if (skipped.length > shown.length)
    shown.push(
      t('record.more', {
        count: skipped.length - shown.length,
      }),
    );
  return shown.join('\n');
}

const SKIP_REASON_KEYS = Object.freeze({
  command_path: 'record.reason.commandPath',
  ambiguous_nesting: 'record.reason.ambiguousNesting',
  unsupported: 'record.reason.unsupported',
  outer_gate: 'record.reason.outerGate',
  multiple_variables: 'record.reason.multipleVariables',
  nested_rewrite: 'record.reason.nestedRewrite',
  same_value: 'record.reason.sameValue',
});

function skipReason(item) {
  const key = SKIP_REASON_KEYS[item?.reason_code];
  return key ? t(key, item?.reason_params || { detail: item.reason }) : item?.reason;
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

  // Scope recorded lines to the selected source INI.
  const positionLines = {};
  for (let p = 0; p < snapshots.length; p++) {
    const lines = new Set();
    for (const [src, mesh] of targets) {
      const visible = active.touchedTargets.has(mesh) ? snapshots[p].get(mesh) : active.sourceSnapshots[p].get(src);
      if (!visible) continue;
      lines.add(src.line);
    }
    positionLines[p] = [...lines].sort((a, b) => a - b);
  }
  const sortedTargetRefs = targetRefs.sort((a, b) => Number(a.line) - Number(b.line));

  ui.saveBtn.disabled = true;
  try {
    const result = await window.pywebview.api.record_toggle(
      ctx.modPath,
      info.ini,
      info.section,
      positionLines,
      sortedTargetRefs,
    );
    if (result.error) {
      await alertDialog(t('record.saveError', { detail: result.error }));
      return;
    }
    const summary = summarizeSkips(result.result || {});
    exitRecordingUI();
    if (summary) await alertDialog(t('record.review', { detail: summary }));
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

window.addEventListener(LANGUAGE_CHANGED, () => {
  if (!active) return;
  active.ui.cycleBtn.title = t('record.nextPosition');
  active.ui.valSpan.textContent = positionLabel();
});
