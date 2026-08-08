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
  mesh.userData.conditions = conditions || [];
  mesh.userData.sources = sources || [];
  mesh.userData.textureVariants = textureVariants || [];
  // Tri-state: undefined = follow automatic/toggle-driven resolution below,
  // null = user explicitly picked "(None)", a string = a manually picked
  // tex_key. Set by the per-mesh texture list (mesh-panel.js) and takes
  // priority over textureVariants until the user clears it back to
  // "(Automatic)" -- see applyTextureVariant.
  mesh.userData.manualTexOverride = undefined;
  mesh.material.wireframe = wireframe;
  mesh.material.flatShading = !smoothShading;
  scene.add(mesh);
  activeMeshes.push(mesh);
  applyTextureVariant(mesh);
}

/** Manually pin (or clear, with `value === undefined`) a mesh's texture,
 * overriding whatever the ini's own toggle-driven resolution would pick.
 * Takes effect immediately and stays sticky across Toggle panel presses
 * until cleared -- see applyTextureVariant/refreshAll. */
export function setManualTexOverride(mesh, value) {
  mesh.userData.manualTexOverride = value;
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

// A toggle can reassign a mesh's diffuse texture -- unless the user has
// manually pinned one via the per-mesh texture list, which wins until
// explicitly cleared back to "(Automatic)" (setManualTexOverride). Falls
// back to the draw's own resolved default (mesh.userData.defaultTexKey)
// when no variant's condition currently matches -- a draw whose toggle only
// swaps texture under a specific value (or that has no texture_variants at
// all) must still revert cleanly instead of keeping whatever texKey a
// previous manual pick left behind.
export function applyTextureVariant(mesh) {
  if (mesh.userData.manualTexOverride !== undefined) {
    setMeshTexture(mesh, mesh.userData.manualTexOverride);
    return;
  }
  const variants = mesh.userData.textureVariants || [];
  const variant = variants.find(v => dnfSatisfied(v.conditions));
  setMeshTexture(mesh, variant ? variant.tex_key : mesh.userData.defaultTexKey);
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
  groupsUI.forEach(({ masterCb, itemCbs, itemObjs, onTexChanged }) => {
    itemObjs.forEach((mesh, i) => { itemCbs[i].checked = mesh.visible; });
    const any = itemCbs.some(c => c.checked);
    const all = itemCbs.every(c => c.checked);
    masterCb.checked = all;
    masterCb.indeterminate = any && !all;
    if (onTexChanged) onTexChanged();
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
