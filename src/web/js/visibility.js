// Mesh visibility: the interaction between the MESHES checkboxes (manual) and
// the Toggle panel (ini key conditions).
//
// These two controls deliberately do NOT have equal authority — see
// applyMeshVisibility and refreshAll below. Both rules there exist to fix real
// bugs; changing either one will reintroduce them.

import { scene } from './scene.js';
import { setMeshTexture, refreshMeshTexture, setTexturesEnabled } from './mesh-factory.js';

/** Every mesh currently in the scene. */
export const activeMeshes = [];

/** {variable: currentValueString} — the Toggle panel's state. */
let toggleState = {};

/** [{masterCb, itemCbs, itemObjs}] — registered by the meshes panel. */
let groupsUI = [];

let wireframe = false;
let smoothShading = true;
let textures = true;

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

export function addMesh(mesh, conditions, sources, textureVariants) {
  mesh.userData.manualVisible = true;
  mesh.userData.loadedVisible = true;
  mesh.userData.manuallyToggled = false;
  mesh.userData.conditions = conditions || [];
  mesh.userData.sources = sources || [];
  mesh.userData.textureVariants = textureVariants || [];
  mesh.material.wireframe = wireframe;
  mesh.material.flatShading = !smoothShading;
  scene.add(mesh);
  activeMeshes.push(mesh);
  applyTextureVariant(mesh);
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

// True if an OR'd list of AND-groups ([[{var,value,negate}, ...], ...]) is
// satisfied by the current Toggle panel state.
function dnfSatisfied(condGroups) {
  if (!condGroups || condGroups.length === 0) return true;
  return condGroups.some(group => group.every(c => {
    const cur = toggleState[c.var];
    if (cur === undefined) return true;
    return c.negate ? (cur !== c.value) : (cur === c.value);
  }));
}

// True if this mesh's gating conditions (an OR'd list of AND-groups:
// [[{var,value,negate}, ...], ...]) are satisfied by the current Toggle panel
// state. Meshes with no conditions are never gated. Exported for record mode,
// which needs to answer this same question against a hypothetical toggle
// value (a recording position) rather than only the live one.
export function conditionsSatisfied(mesh) {
  return dnfSatisfied(mesh.userData.conditions);
}

// A toggle can reassign a mesh's diffuse texture.
export function applyTextureVariant(mesh) {
  const variants = mesh.userData.textureVariants || [];
  const variant = variants.find(v => dnfSatisfied(v.conditions));
  if (variant) setMeshTexture(mesh, variant.tex_key);
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

export function resetToDefaultState() {
  activeMeshes.forEach(mesh => {
    mesh.userData.manuallyToggled = false;
    mesh.userData.manualVisible = mesh.userData.loadedVisible !== false;
  });
  refreshAll();
}

// Reflect each mesh's actual visibility back onto its MESHES checkbox, so
// cycling a Toggle value visibly checks/unchecks the affected items and
// updates each group's master checkbox.
export function syncCheckboxes() {
  groupsUI.forEach(({ masterCb, itemCbs, itemObjs }) => {
    itemObjs.forEach((mesh, i) => {
      itemCbs[i].checked = mesh.visible;
      updateStateIndicator(mesh);
    });
    const any = itemCbs.some(c => c.checked);
    const all = itemCbs.every(c => c.checked);
    masterCb.checked = all;
    masterCb.indeterminate = any && !all;
  });
}

function updateStateIndicator(mesh) {
  const indicator = mesh.userData.stateIndicator;
  if (!indicator) return;
  indicator.textContent = mesh.userData.manuallyToggled ? (mesh.visible ? '✅' : '🟨') : (mesh.visible ? '✅' : '🟥');
  indicator.title = mesh.userData.manuallyToggled ? 'Manually toggled in the viewer' : (mesh.visible ? 'Visible by default' : 'Hidden by the mod default state');
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
    if (!mesh.userData.defaultCaptured) {
      mesh.userData.loadedVisible = mesh.visible;
      mesh.userData.defaultCaptured = true;
    }
    applyTextureVariant(mesh);
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

export function toggleSmoothShading() {
  smoothShading = !smoothShading;
  document.getElementById('shading-btn').classList.toggle('off', !smoothShading);
  activeMeshes.forEach(m => {
    const mats = Array.isArray(m.material) ? m.material : [m.material];
    mats.forEach(mt => { mt.flatShading = !smoothShading; mt.needsUpdate = true; });
  });
}

export function toggleTextures() {
  textures = !textures;
  document.getElementById('texture-btn').classList.toggle('off', !textures);
  setTexturesEnabled(textures);
  activeMeshes.forEach(refreshMeshTexture);
}
