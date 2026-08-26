// Compatibility facade coordinating control state, mesh state and current UI.

import {
  getControlState, getControlValue, replayControlStateRules,
  resetControlState, setControlStateRules, setControlValue,
} from '../editing/control-state.js';
import {
  activeMeshes, refreshMeshes, resetMeshes, resetMeshVisibility,
} from './mesh-state.js';
import {
  toggleGlossyMode, toggleSmoothShadingMode, toggleTextureDisplayMode,
  toggleWireframeMode,
} from '../scene/render-modes.js';
import { clearViewSyncs, syncView, syncViews } from '../scene/view-sync.js';

export {
  activeMeshes, addMesh, applyMeshVisibility, conditionsSatisfied,
  removeAssetFillMeshes, removeMesh, setManualTexOverride,
  updateMeshSemantics,
} from './mesh-state.js';

export const setToggleValue = setControlValue;
export const getToggleValue = getControlValue;
export const getToggleState = getControlState;
export const setStateRules = setControlStateRules;

export function reset(options) {
  resetMeshes(options);
  resetControlState();
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

export function refreshAll() {
  replayControlStateRules();
  refreshMeshes();
  syncViews();
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

export function toggleTextures() {
  toggleTextureDisplayMode(activeMeshes);
}