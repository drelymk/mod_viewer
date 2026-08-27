// Entry point: composes frontend application flows and initializes the UI.

import {
  getAmbientOcclusionStrength, getEnvironmentPreset, getRenderCount,
  isRendererAvailable, rendererReady,
  resetView, rotateModelHorizontalQuarterTurn, rotateModelQuarterTurn,
  setAmbientOcclusionStrength, toggleGrid, toggleTrackballGizmo,
} from './scene/scene.js';
import {
  activeMeshes, resetMeshState,
  toggleGlossy, toggleSmoothShading, toggleToonShading, toggleWireframe,
} from './mesh/visibility.js';
import { refreshMeshTexture } from './mesh/mesh-factory.js';
import { initSelection } from './scene/selection.js';
import { initModFolderPanel } from './panels/mod-folder-panel.js';
import { initAssetFolderPanel } from './panels/asset-folder-panel.js';
import { initLeftDock, setLeftDockTab } from './panels/left-dock.js';
import { getMaterialDebugMode, setMaterialDebugMode } from './mesh/material-profile.js';
import { requestRender } from './scene/render-scheduler.js';
import { initInspectorPanel } from './panels/inspector-panel.js';
import { initRightDock } from './panels/right-dock.js';
import { initPanelOpacityControl } from './ui/appearance.js';
import {
  getOutlineState as getMeshOutlineState,
  setOutlineSuppressedByDebug, setOutlinesEnabled,
} from './scene/outline-renderer.js';
import {
  displayMeshPayload as displayMeshPayloadFlow,
  exportChanges as exportChangesFlow,
  openMod as openModFlow,
  refreshPendingState,
  reloadCurrentMod as reloadCurrentModFlow,
  switchAsset as switchAssetFlow,
  switchMod as switchModFlow,
  syncViewportControlPlacement,
} from './app/model-flow.js';
import { toggleMissingAssetParts, updateAssetFillButton } from './app/asset-fill.js';
import {
  refreshControlSemantics as refreshControlSemanticsFlow,
  refreshMeshSemantics as refreshMeshSemanticsFlow,
  refreshPresentState as refreshPresentStateFlow,
} from './app/semantic-refresh.js';
import { viewerState } from './app/state.js';
import {
  initEnvironmentControl, initToolPopovers, initToolbarOverflow,
} from './ui/toolbar.js';
import { initPanelCollapse } from './ui/panel-utils.js';

const $ = (id) => document.getElementById(id);

function semanticHandlers() {
  return {
    onPresentChange: handlePresentChange,
    onToggleChange: handleToggleChange,
    refreshPendingState,
    syncViewportControlPlacement,
  };
}

function modelHandlers() {
  return {
    onMaterialKindChanged: reloadCurrentMod,
    onPresentChange: handlePresentChange,
    onReload: reloadCurrentMod,
    onToggleChange: handleToggleChange,
  };
}

async function handlePresentChange(change = {}) {
  return refreshPresentStateFlow(change, semanticHandlers());
}

async function handleToggleChange(change = {}) {
  if (change.type === 'record') {
    const meshesRefreshed = await refreshMeshSemanticsFlow(semanticHandlers());
    return meshesRefreshed
      ? refreshControlSemanticsFlow(semanticHandlers()) : false;
  }
  if (change.type === 'delete') {
    // Deleting a toggle rewrites every safe branch that references its
    // variable, including resource bindings before drawindexed. The draw
    // label can survive while its geometry identity changes, so semantic
    // patching is not safe here; rebuild from the authoritative session.
    return reloadCurrentMod();
  }
  return refreshControlSemanticsFlow(semanticHandlers());
}

function displayMeshPayload(payload, options = {}) {
  return displayMeshPayloadFlow(payload, {
    ...options,
    ...modelHandlers(),
  });
}

function openMod() {
  return openModFlow(modelHandlers());
}

function switchMod(path) {
  return switchModFlow(path, modelHandlers());
}

function switchAsset(path, entry = {}) {
  return switchAssetFlow(path, entry, modelHandlers());
}

function reloadCurrentMod() {
  return reloadCurrentModFlow(modelHandlers());
}

function exportChanges() {
  return exportChangesFlow();
}

initToolbarOverflow();
initPanelOpacityControl();

rendererReady.then(ready => {
  if (!ready || !isRendererAvailable()) return;

  $('open-btn').addEventListener('click', openMod);
  $('export-btn').addEventListener('click', exportChanges);
  $('asset-fill-btn').addEventListener('click', event => {
    event.stopPropagation();
    void toggleMissingAssetParts();
  });
  $('wire-btn').addEventListener('click', toggleWireframe);
  $('outline-btn').addEventListener('click', () => {
    const enabled = setOutlinesEnabled();
    const button = $('outline-btn');
    button.classList.toggle('active', enabled);
    button.setAttribute('aria-pressed', String(enabled));
    button.setAttribute('aria-label', `Silhouette outlines: ${enabled ? 'on' : 'off'}`);
  });
  $('grid-btn').addEventListener('click', toggleGrid);
  $('shading-btn').addEventListener('click', toggleSmoothShading);
  $('toon-btn').addEventListener('click', toggleToonShading);
  $('glossy-btn').addEventListener('click', toggleGlossy);
  const syncAmbientOcclusionControl = initToolPopovers();
  $('reset-state-btn').addEventListener('click', event => {
    event.stopPropagation();
    resetMeshState();
  });
  $('trackball-btn').addEventListener('click', toggleTrackballGizmo);
  $('camera-reset-view-btn').addEventListener('click', () => resetView(activeMeshes));
  $('camera-flip-btn').addEventListener('click', () => rotateModelQuarterTurn(activeMeshes));
  $('camera-flip-horizontal-btn').addEventListener('click', () => rotateModelHorizontalQuarterTurn(activeMeshes));
  const applyEnvironmentPreset = initEnvironmentControl();
  initLeftDock();
  initRightDock();
  initInspectorPanel();
  initSelection();
  const viewportCameraButtons = $('viewport-camera-buttons');
  const cameraButtons = $('camera-buttons');
  if (viewportCameraButtons && cameraButtons) viewportCameraButtons.append(cameraButtons);
  syncViewportControlPlacement();
  initPanelCollapse($('sidebar'), 'mesh-list');
  updateAssetFillButton();
  initPanelCollapse($('present-panel'), 'present-list');
  initPanelCollapse($('toggle-panel'), 'toggle-list');
  initPanelCollapse($('menu-panel'), 'menu-list');
  const emptyFolderAction = $('empty-add-folder-btn');
  let hasModFolders = false;
  const updateEmptyFolderAction = hasFolders => {
    hasModFolders = !!hasFolders;
    emptyFolderAction.textContent = hasModFolders
      ? 'Open Mod Folder' : 'Add Mod Folder';
    emptyFolderAction.setAttribute('aria-label', emptyFolderAction.textContent);
  };
  updateEmptyFolderAction(false);
  const modFolderPanel = initModFolderPanel({
    switchMod,
    onRegistryChanged: updateEmptyFolderAction,
  });
  const assetFolderPanel = initAssetFolderPanel({ switchAsset });
  $('empty-open-btn').disabled = false;
  emptyFolderAction.disabled = false;
  $('empty-open-btn').addEventListener('click', openMod);
  emptyFolderAction.addEventListener('click', () => {
    setLeftDockTab('mod-library');
    if (!hasModFolders) modFolderPanel.openAddDialog();
  });
  $('mod-folder-empty-add')?.addEventListener('click', modFolderPanel.openAddDialog);
  $('asset-folder-empty-add')?.addEventListener('click', assetFolderPanel.openAddDialog);

  // Exposed for automated smoke tests and for poking at the app from the
  // devtools console; the UI itself always goes through the listeners above.
  const getMaterialState = (index) => {
    const mesh = activeMeshes[index];
    const game = mesh?.material?.userData?.gameMaterial;
    return {
      kind: mesh?.userData.materialKind,
      reliable: mesh?.userData.materialKindReliable,
      reason: mesh?.userData.materialKindReason,
      materialKindOverride: mesh?.userData.materialKindOverride || null,
      component: mesh?.userData.component || null,
      profileId: mesh?.userData.materialProfileId,
      profile: mesh?.userData.materialProfile,
      materialIdDecoder: game?.profile?.material_id_decoder || null,
      directShadowModel: game?.profile?.direct_shadow_model || null,
      directSpecularModel: game?.profile?.direct_specular_model || null,
      hasMaterialId: !!game?.hasMaterialId,
      hasSpecularArea: !!game?.hasSpecularArea,
      hasShadowMask: !!game?.hasShadowMask,
      hasNormalData: !!(game?.hasNormalDataB || game?.hasNormalDataA),
      hasNormalDataB: !!game?.hasNormalDataB,
      hasNormalDataA: !!game?.hasNormalDataA,
      diffuseBound: !!game?.bindings?.diffuse?.enabledNode?.value,
      normalMapBound: !!game?.bindings?.normal_map?.enabledNode?.value,
      shadowMaskBound: !!game?.bindings?.light_map?.enabledNode?.value,
      normalSource: game?.normalSource || 'normal_map',
      normalPacking: game?.normalPacking || 'rgb',
      normalSourceBound: !!game?.bindings?.[game?.normalSource || 'normal_map']
        ?.enabledNode?.value,
      normalDataBound: !!game?.bindings?.normal_data?.enabledNode?.value,
      lightMapBound: !!game?.bindings?.light_map?.enabledNode?.value,
      materialMapBound: !!game?.bindings?.material_map?.enabledNode?.value,
      supportedDebugModes: game?.supportedDebugModes || [],
      debugMode: getMaterialDebugMode(mesh?.material),
    };
  };
  const setMaterialDebugModeForMeshes = mode => {
    const normalized = setMaterialDebugMode(activeMeshes, mode);
    setOutlineSuppressedByDebug(normalized !== 'off');
    // Diagnostics use the same stable packed bindings as normal rendering.
    // WuWa's packed normal is already resident when debug mode changes, so a
    // B/A view only changes the diagnostic uniform and binding selection.
    activeMeshes.forEach(refreshMeshTexture);
    requestRender();
    return normalized;
  };
  window.modViewer = {
    displayMeshPayload, openMod, switchMod, switchAsset, reloadCurrentMod,
    exportChanges,
    refreshPresentState: handlePresentChange,
    refreshControlSemantics: () => refreshControlSemanticsFlow(semanticHandlers()),
    refreshMeshSemantics: () => refreshMeshSemanticsFlow(semanticHandlers()),
    activeMeshes,
    setEnvironmentPreset: applyEnvironmentPreset,
    getEnvironmentPreset,
    getAmbientOcclusionStrength,
    setAmbientOcclusionStrength: value => {
      const changed = setAmbientOcclusionStrength(value);
      syncAmbientOcclusionControl?.();
      return changed;
    },
    getMaterialState,
    getRenderCount,
    setMaterialDebugMode: setMaterialDebugModeForMeshes,
    setOutlineEnabled: value => {
      const enabled = setOutlinesEnabled(value);
      const button = $('outline-btn');
      button.classList.toggle('active', enabled);
      button.setAttribute('aria-pressed', String(enabled));
      button.setAttribute('aria-label', `Silhouette outlines: ${enabled ? 'on' : 'off'}`);
      return enabled;
    },
    getOutlineState: index => getMeshOutlineState(activeMeshes[index]),
    getCurrentSource: () => viewerState.currentSource
      ? { ...viewerState.currentSource } : null,
  };
});
