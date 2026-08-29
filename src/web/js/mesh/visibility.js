// Compatibility facade coordinating control state, mesh state and current UI.

import {
  getControlState, getControlValue, replayControlStateRules,
  changedControlVariables, resetControlState, setControlStateRules,
  setControlValue,
} from '../editing/control-state.js';
import {
  activeMeshes, refreshMeshes, resetMeshes, resetMeshVisibility,
} from './mesh-state.js';
import {
  toggleGlossyMode, toggleSmoothShadingMode, toggleTextureDisplayMode,
  toggleToonShadingMode, toggleWireframeMode,
} from '../scene/render-modes.js';
import { clearViewSyncs, syncView, syncViews } from '../scene/view-sync.js';

export {
  activeMeshes, addMesh, applyMeshVisibility, conditionsSatisfied,
  dependenciesFor, invalidateControlDependencies, variablesFromConditions,
  removeAssetFillMeshes, removeMesh, setManualTexOverride,
  updateMeshSemantics,
} from './mesh-state.js';
export { changedControlVariables } from '../editing/control-state.js';

export const setToggleValue = setControlValue;
export const getToggleValue = getControlValue;
export const getToggleState = getControlState;
export const setStateRules = setControlStateRules;

let lastAppliedControlState = null;

export function reset(options) {
  resetMeshes(options);
  resetControlState();
  lastAppliedControlState = null;
  clearViewSyncs();
}

export function resetMeshState() {
  resetMeshVisibility();
  syncViews();
}

// Kept for Record mode compatibility while mesh-panel owns the actual DOM.
export function syncCheckboxes() {
  syncView('mesh-panel');
}

export function refreshAll({ force = {}, additionalMeshes = [] } = {}) {
  replayControlStateRules();
  const next = getControlState();
  const initialApplication = lastAppliedControlState === null;
  const changedVariables = initialApplication
    ? new Set(Object.keys(next))
    : changedControlVariables(lastAppliedControlState, next);
  const result = refreshMeshes({
    changedVariables,
    additionalMeshes,
    force: initialApplication
      ? { visibility: true, textures: true, shapes: true }
      : force,
  });
  lastAppliedControlState = next;
  syncViews();
  return result;
}

export function toggleWireframe() {
  toggleWireframeMode(activeMeshes);
}

export function toggleSmoothShading() {
  toggleSmoothShadingMode(activeMeshes);
}

export function toggleGlossy() {
  toggleGlossyMode(activeMeshes);
}

export function toggleToonShading() {
  toggleToonShadingMode(activeMeshes);
}

export function toggleTextures() {
  toggleTextureDisplayMode(activeMeshes);
}
