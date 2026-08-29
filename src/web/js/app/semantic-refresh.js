// Staged-edit semantic refreshes, guarded against older responses winning.

import { viewerState, samePath } from './state.js';
import { refreshAll, setStateRules, updateMeshSemantics } from '../mesh/visibility.js';
import { refreshMeshAssetDiagnostics } from '../panels/mesh-panel.js';
import { buildMenuPanel } from '../panels/menu-panel.js';
import { buildPresentPanel } from '../panels/present-panel.js';
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

export async function refreshPresentState(change = {}, handlers = {}) {
  const callbacks = handlersOrDefault(handlers);
  const { path, epoch } = beginSemanticRefresh();
  try {
    if (!path) return false;
    let result;
    try {
      result = await window.pywebview.api.get_present_state(path);
    } catch (error) {
      if (semanticRefreshIsCurrent(path, epoch)) {
        await alertDialog('Could not refresh PRESENT:\n\n' + error);
      }
      return false;
    }
    if (!semanticRefreshIsCurrent(path, epoch)) return false;
    if (result?.error) {
      await alertDialog('Could not refresh PRESENT:\n\n' + result.error);
      return false;
    }
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
    let result;
    try {
      result = await window.pywebview.api.get_control_state(path);
    } catch (error) {
      if (semanticRefreshIsCurrent(path, epoch)) {
        await alertDialog('Could not refresh controls:\n\n' + error);
      }
      return false;
    }
    if (!semanticRefreshIsCurrent(path, epoch)) return false;
    if (result?.error) {
      await alertDialog('Could not refresh controls:\n\n' + result.error);
      return false;
    }
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
    let result;
    try {
      result = await window.pywebview.api.get_mesh_semantics(path);
    } catch (error) {
      if (semanticRefreshIsCurrent(path, epoch)) {
        await alertDialog('Could not refresh mesh render semantics:\n\n' + error);
      }
      return false;
    }
    if (!semanticRefreshIsCurrent(path, epoch)) return false;
    if (result?.error) {
      await alertDialog('Could not refresh mesh render semantics:\n\n' + result.error);
      return false;
    }
    const update = updateMeshSemantics(result.meshes, {
      materialProfiles: result.material_profiles || {},
    });
    if (!update.success) {
      await alertDialog('Could not refresh mesh render semantics:\n\n' +
        'The staged draw set no longer matches the displayed model.');
      return false;
    }
    const assetResolution = result.asset_resolution || null;
    refreshMeshAssetDiagnostics(assetResolution);
    setAssetResolution(assetResolution);
    refreshAll({
      force: {visibility: true, textures: true},
      additionalMeshes: update.materialChangedMeshes,
    });
    return true;
  } finally {
    await finishSemanticRefresh(path, epoch, callbacks);
  }
}
