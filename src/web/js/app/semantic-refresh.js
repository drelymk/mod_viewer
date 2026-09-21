// Staged-edit semantic refreshes, guarded against older responses winning.

import { viewerState, samePath } from './state.js';
import { refreshAll, setStateRules, updateMeshSemantics } from '../mesh/visibility.js';
import {
  refreshAutomaticTextureBoundaries, refreshMeshAssetDiagnostics,
} from '../panels/mesh-panel.js';
import { buildMenuPanel } from '../panels/menu-panel.js';
import { buildPresentPanel } from '../panels/present-panel.js';
import { t } from '../i18n/index.js';
import { buildTogglePanel } from '../panels/toggle-panel.js';
import { refreshHealthReport, setAssetResolution } from '../panels/health-report.js';
import { alertDialog } from '../ui/dialogs.js';

export function beginSemanticRefresh() {
  return {
    path: viewerState.currentModPath,
    epoch: ++viewerState.semanticRefreshEpoch,
  };
}

export function semanticRefreshIsCurrent(path, epoch) {
  return !!path && epoch === viewerState.semanticRefreshEpoch
    && samePath(viewerState.currentModPath, path);
}

async function finishSemanticRefresh(path, epoch, { refreshPendingState } = {}) {
  if (!semanticRefreshIsCurrent(path, epoch)) return;
  if (refreshPendingState) {
    await refreshPendingState(
      path, () => semanticRefreshIsCurrent(path, epoch));
  }
  if (semanticRefreshIsCurrent(path, epoch)) void refreshHealthReport();
}

function handlersOrDefault(handlers = {}) {
  return {
    onPresentChange: handlers.onPresentChange || null,
    onToggleChange: handlers.onToggleChange || null,
    refreshPendingState: handlers.refreshPendingState || null,
    syncViewportControlPlacement: handlers.syncViewportControlPlacement || (() => {}),
  };
}

function applyControlSemanticResult(result, path, callbacks) {
  const controls = result.controls || {};
  const state = result.state || {};
  viewerState.lastToggles = controls.toggles || {};
  setStateRules(state.rules || [], state.defaults || {}, {
    toggles: controls.toggles || {}, menu: controls.menu || {},
  });
  buildTogglePanel(controls.toggles, {
    modPath: path, onChange: callbacks.onToggleChange,
  });
  buildMenuPanel(controls.menu);
  buildPresentPanel(controls.present, {
    modPath: path, onChange: callbacks.onPresentChange,
  });
}

function applyMeshSemanticResult(result) {
  const update = updateMeshSemantics(result.meshes, {
    materialProfiles: result.material_profiles || {},
  });
  if (!update.success) return update;
  refreshAutomaticTextureBoundaries();
  const assetResolution = result.asset_resolution || null;
  refreshMeshAssetDiagnostics(assetResolution);
  setAssetResolution(assetResolution);
  return update;
}

async function requestSemanticResult(path, epoch, request, errorKey) {
  let result;
  try {
    result = await request(path);
  } catch (error) {
    if (semanticRefreshIsCurrent(path, epoch)) {
      await alertDialog(t(errorKey, {detail: error}));
    }
    return null;
  }
  if (!semanticRefreshIsCurrent(path, epoch)) return null;
  if (result?.error) {
    await alertDialog(t(errorKey, {detail: result.error}));
    return null;
  }
  return result;
}

export async function refreshPresentState(change = {}, handlers = {}) {
  const callbacks = handlersOrDefault(handlers);
  const { path, epoch } = beginSemanticRefresh();
  try {
    if (!path) return false;
    const result = await requestSemanticResult(
      path, epoch, currentPath => window.pywebview.api.get_present_state(currentPath),
      'errors.refreshPresent');
    if (result === null) return false;
    const context = { modPath: path, onChange: callbacks.onPresentChange };
    if (Object.hasOwn(change, 'selectedPosition')) {
      context.selectedPosition = change.selectedPosition;
      context.applySelection = change.applySelection === true;
    }
    buildPresentPanel(result.present, context);
    callbacks.syncViewportControlPlacement();
    return true;
  } finally {
    await finishSemanticRefresh(path, epoch, callbacks);
  }
}

export async function refreshControlSemantics(handlers = {}) {
  const callbacks = handlersOrDefault(handlers);
  const { path, epoch } = beginSemanticRefresh();
  try {
    if (!path) return false;
    const result = await requestSemanticResult(
      path, epoch, currentPath => window.pywebview.api.get_control_state(currentPath),
      'errors.refreshControls');
    if (result === null) return false;
    applyControlSemanticResult(result, path, callbacks);
    callbacks.syncViewportControlPlacement();
    refreshAll();
    return true;
  } finally {
    await finishSemanticRefresh(path, epoch, callbacks);
  }
}

export async function refreshMeshSemantics(handlers = {}) {
  const callbacks = handlersOrDefault(handlers);
  const { path, epoch } = beginSemanticRefresh();
  try {
    if (!path) return false;
    const result = await requestSemanticResult(
      path, epoch, currentPath => window.pywebview.api.get_mesh_semantics(currentPath),
      'errors.refreshSemantics');
    if (result === null) return false;
    const update = applyMeshSemanticResult(result);
    if (!update.success) {
      await alertDialog(t('errors.refreshSemantics', {
        detail: t('semanticRefresh.drawMismatch'),
      }));
      return false;
    }
    refreshAll({
      force: {visibility: true, textures: true},
      additionalMeshes: update.materialChangedMeshes,
    });
    return true;
  } finally {
    await finishSemanticRefresh(path, epoch, callbacks);
  }
}

export async function refreshSemanticState(handlers = {}) {
  const callbacks = handlersOrDefault(handlers);
  const { path, epoch } = beginSemanticRefresh();
  try {
    if (!path) return false;
    const result = await requestSemanticResult(
      path, epoch, currentPath => window.pywebview.api.get_semantic_state(currentPath),
      'errors.refreshSemantics');
    if (result === null) return false;
    const update = applyMeshSemanticResult(result);
    if (!update.success) {
      await alertDialog(t('errors.refreshSemantics', {
        detail: t('semanticRefresh.drawMismatch'),
      }));
      return false;
    }
    applyControlSemanticResult(result, path, callbacks);
    callbacks.syncViewportControlPlacement();
    refreshAll({
      force: {visibility: true, textures: true},
      additionalMeshes: update.materialChangedMeshes,
    });
    return true;
  } finally {
    await finishSemanticRefresh(path, epoch, callbacks);
  }
}
