// Mesh visibility: the interaction between the MESHES checkboxes (manual) and
// the Toggle panel (ini key conditions).
//
// These two controls deliberately do NOT have equal authority — see
// applyMeshVisibility and refreshAll below. Both rules there exist to fix real
// bugs; changing either one will reintroduce them.

import { scene, resetModelOrientation } from './scene.js';
import { setMeshTexture, setMeshMaterialMaps, refreshMeshTexture, setTextureMode } from './mesh-factory.js';

/** Every mesh currently in the scene. */
export const activeMeshes = [];

/** {variable: currentValueString} — the Toggle panel's state. */
let toggleState = {};
let stateRules = [];

/** [{masterCb, itemCbs, itemObjs}] — registered by the meshes panel. */
let groupsUI = [];

let wireframe = false;
let smoothShading = true;
const textureModes = ['all', 'diffuse', 'none'];
let textureModeIndex = 0;

export function reset() {
  activeMeshes.forEach(m => {
    scene.remove(m);
    m.geometry.dispose();
    (Array.isArray(m.material) ? m.material : [m.material]).forEach(mt => mt.dispose());
  });
  activeMeshes.length = 0;
  groupsUI = [];
  toggleState = {};
  stateRules = [];
  resetModelOrientation();
}

/** Return meshes to the visibility state established when the mod loaded. */
export function resetMeshState() {
  activeMeshes.forEach(mesh => {
    mesh.userData.manualVisible = mesh.userData.loadedVisible !== false;
    mesh.userData.manuallyToggled = false;
    applyMeshVisibility(mesh);
  });
  syncCheckboxes();
}

export function addMesh(mesh, conditions, sources, textureVariants, materialVariants = {}) {
  mesh.userData.manualVisible = true;
  mesh.userData.loadedVisible = true;
  mesh.userData.manuallyToggled = false;
  mesh.userData.conditions = conditions || [];
  mesh.userData.sources = sources || [];
  mesh.userData.textureVariants = textureVariants || [];
  mesh.userData.normalMapVariants = materialVariants.normal_map || [];
  mesh.userData.lightMapVariants = materialVariants.light_map || [];
  mesh.userData.materialMapVariants = materialVariants.material_map || [];
  // The texture selected by the ini under the current toggle/menu state.
  // Kept separately from texKey because the component's ordered texture-run
  // pass may make this mesh follow a highlighted boundary above it.
  mesh.userData.resolvedTexKey = mesh.userData.defaultTexKey;
  // undefined = unselected (follows leader/toggle resolution), a string =
  // user explicitly picked this tex_key (sticky until de-selected). No
  // "(None)" state -- de-selecting reverts to the ini's own default.
  mesh.userData.manualTexOverride = undefined;
  mesh.material.wireframe = wireframe;
  mesh.material.flatShading = !smoothShading;
  scene.add(mesh);
  activeMeshes.push(mesh);
  applyTextureVariant(mesh);
}

/** Pin or clear one mesh's highlighted texture. Component-local downward
 * propagation is handled by mesh-panel.js's ordered boundary pass. */
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

export function getToggleState() {
  return { ...toggleState };
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
// explicitly de-selected. Falls back to the draw's own resolved default
// (mesh.userData.defaultTexKey) when no variant's condition currently
// matches -- a draw whose toggle only swaps texture under a specific value
// (or that has no texture_variants at all) must still revert cleanly.
export function applyTextureVariant(mesh) {
  const resolve = (variants, fallback) => {
    variants = variants || [];
    const variant = variants.findLast
      ? variants.findLast(v => dnfSatisfied(v.conditions))
      : [...variants].reverse().find(v => dnfSatisfied(v.conditions));
    return variant ? variant.tex_key : fallback;
  };
  const variants = mesh.userData.textureVariants || [];
  // Diffuse assignments execute in source order; later matching writes
  // override earlier ones (including nested, independent condition chains).
  mesh.userData.resolvedTexKey = resolve(variants, mesh.userData.defaultTexKey);
  mesh.userData.resolvedNormalMapKey = resolve(mesh.userData.normalMapVariants,
    mesh.userData.defaultNormalMapKey);
  mesh.userData.resolvedLightMapKey = resolve(mesh.userData.lightMapVariants,
    mesh.userData.defaultLightMapKey);
  mesh.userData.resolvedMaterialMapKey = resolve(mesh.userData.materialMapVariants,
    mesh.userData.defaultMaterialMapKey);
  setMeshTexture(mesh, mesh.userData.manualTexOverride !== undefined
    ? mesh.userData.manualTexOverride
    : mesh.userData.resolvedTexKey);
  setMeshMaterialMaps(mesh, {
    normal_map: mesh.userData.resolvedNormalMapKey,
    light_map: mesh.userData.resolvedLightMapKey,
    material_map: mesh.userData.resolvedMaterialMapKey,
  });
}

export function setStateRules(rules, defaults) {
  stateRules = rules || [];
  for (const [variable, value] of Object.entries(defaults || {})) {
    if (toggleState[variable] === undefined) toggleState[variable] = value;
  }
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

function applyShapeTargets(mesh) {
  const targets = mesh.userData.shapeTargets || [];
  if (!targets.length) return;
  const attr = mesh.geometry.attributes.position;
  const base = mesh.userData.basePositions;
  attr.array.set(base);
  const midpointTargets = targets.filter(target => target.mode === 'midpoint_pair');
  for (const target of targets) {
    const weight = Number(toggleState[target.var] ?? 0);
    if (!Number.isFinite(weight)) continue;
    if (target.mode === 'midpoint_pair') {
      const endpoint = weight <= 0.5 ? target.lowPositions : target.positions;
      // Match the shader's deliberate endpoint extrapolation. Each of the two
      // independently-shaped results is averaged below, so it uses a 0..2
      // delta factor to retain the authored full range after that division.
      // This remains monotonic as long as the high branch moves base->bigger.
      const factor = weight <= 0.5 ? 2 - weight * 4 : weight * 4 - 2;
      const divisor = midpointTargets.length || 1;
      for (let i = 0; i < attr.array.length; i++) {
        const shaped = base[i] + (endpoint[i] - base[i]) * factor;
        attr.array[i] += (shaped - base[i]) / divisor;
      }
      continue;
    }
    if (weight === 0) continue;
    for (let i = 0; i < attr.array.length; i++) {
      attr.array[i] += (target.positions[i] - base[i]) * weight;
    }
  }
  attr.needsUpdate = true;
  mesh.geometry.computeVertexNormals();
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
}

// Reflect each mesh's actual visibility back onto its MESHES checkbox, so
// cycling a Toggle value visibly checks/unchecks the affected items and
// updates each group's master checkbox.
export function syncCheckboxes() {
  groupsUI.forEach(({ masterCb, itemCbs, itemObjs, onTexChanged, applyTextureRuns }) => {
    if (applyTextureRuns) applyTextureRuns();
    itemObjs.forEach((mesh, i) => {
      itemCbs[i].checked = mesh.visible;
      mesh.userData.updateStateIndicator?.(mesh);
      mesh.userData.updateTextureList?.();
    });
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
  // [Present] derives literal draw flags from menu variables every frame in
  // many WWMI mods. Replay those safe rules in source order first.
  for (const rule of stateRules) {
    if (dnfSatisfied(rule.conditions)) toggleState[rule.var] = rule.value;
  }
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
    applyShapeTargets(mesh);
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
  textureModeIndex = (textureModeIndex + 1) % textureModes.length;
  const mode = textureModes[textureModeIndex];
  const button = document.getElementById('texture-btn');
  button.classList.toggle('diffuse-only', mode === 'diffuse');
  button.classList.toggle('off', mode === 'none');
  const labels = {
    all: 'Textures: all maps',
    diffuse: 'Textures: diffuse only',
    none: 'Textures: none',
  };
  button.title = labels[mode];
  button.setAttribute('aria-label', labels[mode]);
  setTextureMode(mode);
  activeMeshes.forEach(refreshMeshTexture);
}
