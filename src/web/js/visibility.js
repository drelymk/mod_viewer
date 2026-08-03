// Mesh visibility: the interaction between the MESHES checkboxes (manual) and
// the Toggle panel (ini key conditions).
//
// These two controls deliberately do NOT have equal authority — see
// applyMeshVisibility and refreshAll below. Both rules there exist to fix real
// bugs; changing either one will reintroduce them.

import { scene } from './scene.js';

/** Every mesh currently in the scene. */
export const activeMeshes = [];

/** {variable: currentValueString} — the Toggle panel's state. */
let toggleState = {};

/** [{masterCb, itemCbs, itemObjs}] — registered by the meshes panel. */
let groupsUI = [];

let wireframe = false;

export function reset() {
  activeMeshes.forEach(m => {
    scene.remove(m);
    m.geometry.dispose();
    (Array.isArray(m.material) ? m.material : [m.material]).forEach(mt => mt.dispose());
  });
  activeMeshes.length = 0;
  groupsUI = [];
  toggleState = {};
}

export function addMesh(mesh, conditions, sources) {
  mesh.userData.manualVisible = true;
  mesh.userData.conditions = conditions || [];
  mesh.userData.sources = sources || [];
  mesh.material.wireframe = wireframe;
  scene.add(mesh);
  activeMeshes.push(mesh);
}

export function registerGroup(group) {
  groupsUI.push(group);
}

export function setToggleValue(variable, value) {
  toggleState[variable] = value;
}

export function getToggleValue(variable) {
  return toggleState[variable];
}

// True if this mesh's gating conditions (an OR'd list of AND-groups:
// [[{var,value,negate}, ...], ...]) are satisfied by the current Toggle panel
// state. Meshes with no conditions are never gated. Exported for record mode,
// which needs to answer this same question against a hypothetical toggle
// value (a recording position) rather than only the live one.
export function conditionsSatisfied(mesh) {
  const condGroups = mesh.userData.conditions || [];
  if (condGroups.length === 0) return true;
  return condGroups.some(group => group.every(c => {
    const cur = toggleState[c.var];
    if (cur === undefined) return true;
    return c.negate ? (cur !== c.value) : (cur === c.value);
  }));
}

// The MESHES checkbox is the sole, direct source of truth for a mesh's
// visibility — clicking it always shows/hides the mesh immediately. Toggle
// conditions only come into play when refreshAll() re-baselines manualVisible
// after a Toggle panel value changes (see below); they must never re-gate a
// manual click, or an already-hidden gated mesh could never be shown again by
// checking its box.
export function applyMeshVisibility(mesh) {
  mesh.visible = mesh.userData.manualVisible !== false;
}

// Reflect each mesh's actual visibility back onto its MESHES checkbox, so
// cycling a Toggle value visibly checks/unchecks the affected items and
// updates each group's master checkbox.
export function syncCheckboxes() {
  groupsUI.forEach(({ masterCb, itemCbs, itemObjs }) => {
    itemObjs.forEach((mesh, i) => { itemCbs[i].checked = mesh.visible; });
    const any = itemCbs.some(c => c.checked);
    const all = itemCbs.every(c => c.checked);
    masterCb.checked = all;
    masterCb.indeterminate = any && !all;
  });
}

// Re-baseline manualVisible to match the current Toggle panel state, then sync
// checkboxes. Only meshes actually GATED by a toggle (non-empty conditions) are
// re-baselined here — a mesh with no conditions isn't affected by any key
// toggle at all, so it must stay checked by default and keep whatever manual
// show/hide state the user gave it, undisturbed by clicking toggles elsewhere
// in the panel.
export function refreshAll() {
  activeMeshes.forEach(mesh => {
    if ((mesh.userData.conditions || []).length > 0) {
      mesh.userData.manualVisible = conditionsSatisfied(mesh);
    }
    applyMeshVisibility(mesh);
  });
  syncCheckboxes();
}

export function toggleWireframe() {
  wireframe = !wireframe;
  document.getElementById('wire-btn').classList.toggle('active', wireframe);
  activeMeshes.forEach(m => {
    const mats = Array.isArray(m.material) ? m.material : [m.material];
    mats.forEach(mt => { mt.wireframe = wireframe; });
  });
}
