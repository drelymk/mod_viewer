// Staged-edit semantic refreshes, guarded against older responses winning.

import { viewerState, samePath } from './state.js';
import { refreshAll, setStateRules, updateMeshSemantics } from '../mesh/visibility.js';
import { refreshAutomaticTextureBoundaries, refreshMeshAssetDiagnostics } from '../panels/mesh-panel.js';
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
  return !!path && epoch === viewerState.semanticRefreshEpoch && samePath(viewerState.currentModPath, path);
}

async function finishSemanticRefresh(path, epoch, { refreshPendingState } = {}) {
  if (!semanticRefreshIsCurrent(path, epoch)) return;
  if (refreshPendingState) {
    await refreshPendingState(path, () => semanticRefreshIsCurrent(path, epoch));
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

function applyControlSemanticResult(result, path, callbacks, change = {}) {
  const controls = result.controls || {};
  const state = result.state || {};
  viewerState.lastToggles = controls.toggles || {};
  setStateRules(state.rules || [], state.defaults || {}, {
    toggles: controls.toggles || {},
    menu: controls.menu || {},
  });
  buildTogglePanel(controls.toggles, {
    modPath: path,
    onChange: callbacks.onToggleChange,
  });
  buildMenuPanel(controls.menu);
  const presentContext = {
    modPath: path,
    onChange: callbacks.onPresentChange,
  };
  if (Object.hasOwn(change, 'selectedPosition')) {
    presentContext.selectedPosition = change.selectedPosition;
    presentContext.applySelection = change.applySelection === true;
  }
  buildPresentPanel(controls.present, presentContext);
}

async function applyMeshSemanticResult(result, afterUpdate = null) {
  const update = updateMeshSemantics(result.meshes, {
    materialProfiles: result.material_profiles || {},
  });
  if (!update.success) {
    await alertDialog(t('errors.refreshSemantics', { detail: t('semanticRefresh.drawMismatch') }));
    return false;
  }
  refreshAutomaticTextureBoundaries();
  const assetResolution = result.asset_resolution || null;
  refreshMeshAssetDiagnostics(assetResolution);
  setAssetResolution(assetResolution);
  afterUpdate?.();
  refreshAll({
    force: { visibility: true, textures: true },
    additionalMeshes: update.materialChangedMeshes,
  });
  return true;
}

async function requestSemanticResult(path, epoch, request, errorKey) {
  let result;
  try {
    result = await request(path);
  } catch (error) {
    if (semanticRefreshIsCurrent(path, epoch)) {
      await alertDialog(t(errorKey, { detail: error }));
    }
    return null;
  }
  if (!semanticRefreshIsCurrent(path, epoch)) return null;
  if (result?.error) {
    await alertDialog(t(errorKey, { detail: result.error }));
    return null;
  }
  return result;
}

async function refreshSemantics(request, errorKey, handlers, applyResult) {
  const callbacks = handlersOrDefault(handlers);
  const { path, epoch } = beginSemanticRefresh();
  try {
    if (!path) return false;
    const result = await requestSemanticResult(path, epoch, request, errorKey);
    if (result === null) return false;
    return await applyResult(result, path, callbacks);
  } finally {
    await finishSemanticRefresh(path, epoch, callbacks);
  }
}

export function refreshPresentState(change = {}, handlers = {}) {
  return refreshSemantics(
    (currentPath) => window.pywebview.api.get_present_state(currentPath),
    'errors.refreshPresent',
    handlers,
    (result, path, callbacks) => {
      const context = { modPath: path, onChange: callbacks.onPresentChange };
      if (Object.hasOwn(change, 'selectedPosition')) {
        context.selectedPosition = change.selectedPosition;
        context.applySelection = change.applySelection === true;
      }
      buildPresentPanel(result.present, context);
      callbacks.syncViewportControlPlacement();
      return true;
    },
  );
}

export function refreshControlSemantics(handlers = {}) {
  return refreshSemantics(
    (currentPath) => window.pywebview.api.get_control_state(currentPath),
    'errors.refreshControls',
    handlers,
    (result, path, callbacks) => {
      applyControlSemanticResult(result, path, callbacks);
      callbacks.syncViewportControlPlacement();
      refreshAll();
      return true;
    },
  );
}

export function refreshMeshSemantics(handlers = {}) {
  return refreshSemantics(
    (currentPath) => window.pywebview.api.get_mesh_semantics(currentPath),
    'errors.refreshSemantics',
    handlers,
    (result) => applyMeshSemanticResult(result),
  );
}

export function refreshSemanticState(handlers = {}, change = {}) {
  return refreshSemantics(
    (currentPath) => window.pywebview.api.get_semantic_state(currentPath),
    'errors.refreshSemantics',
    handlers,
    (result, path, callbacks) =>
      applyMeshSemanticResult(result, () => {
        applyControlSemanticResult(result, path, callbacks, change);
        callbacks.syncViewportControlPlacement();
      }),
  );
}
