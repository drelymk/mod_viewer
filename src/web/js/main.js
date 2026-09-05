// Entry point: composes frontend application flows and initializes the UI.

import {
  camera, controls, renderer, scene,
  getAmbientOcclusionStrength, getBloomEnabled, getEnvironmentPreset, getRenderCount,
  isRendererAvailable, rendererReady,
  resetView, rotateModelHorizontalQuarterTurn, rotateModelQuarterTurn,
  setAmbientOcclusionStrength, setBloomAvailable, setBloomEnabled,
  setBloomSuppressedByDebug,
  toggleGrid, toggleTrackballGizmo,
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
import {
  disableModelPhysics, enableModelPhysics,
  ensureModelRigLoaded, ensureModelWeightsLoaded,
  getModelPhysicsState, getModelRigDebugState, getModelRigState,
  getModelWeightState, getRigBonePoseFrame, getRigJointPoseFrame,
  getWeightPhysicsPerformanceStats, resetWeightPhysicsPerformanceStats,
  finishRigPose, resetModelPhysicsMotion, resetRigBone, resetRigPose,
  selectRigBone, selectRigJoint,
  setActiveRigSource, setRigBoneRotation, setRigJointRotation,
  setRigComponentRoot,
  getRigRotationSnapDegrees, setRigRotationSnapDegrees, setRigVisible,
  setRigOverlayScope,
  setRigPoseControlStatus, applySavedRigPosePreset, deleteRigPosePreset,
  getRigPresetState, renameRigPosePreset, saveRigPosePreset,
  selectRigPosePreset,
  beginRigPicking, cancelRigPicking, setModelWeightHeatmap,
} from './mesh/weight-experiment.js';
import { initInspectorPanel } from './panels/inspector-panel.js';
import { initRightDock } from './panels/right-dock.js';
import { initRigPanel } from './panels/rig-panel.js';
import { initWeightPanel } from './panels/weight-panel.js';
import { createRigOverlayController } from './scene/rig-overlay-controller.js';
import { initPanelOpacityControl } from './ui/appearance.js';
import { alertDialog } from './ui/dialogs.js';
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
import { getLoadBenchmark } from './app/load-benchmark.js';
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
    onMaterialKindChanged: () => refreshMeshSemanticsFlow(semanticHandlers()),
    onPresentChange: handlePresentChange,
    onReload: reloadCurrentMod,
    onToggleChange: handleToggleChange,
  };
}

async function handlePresentChange(change = {}) {
  const presentRefreshed = await refreshPresentStateFlow(change, semanticHandlers());
  if (!presentRefreshed) return false;
  // PRESENT authoring can insert or remove lines in every participating INI,
  // shifting draw provenance just like toggle Add/Edit. Refresh only the
  // geometry-free semantics; the rendered meshes and their geometry survive.
  if (['add-key', 'complete-key', 'edit-key', 'delete-key',
       'new-position', 'update-position', 'delete-position']
      .includes(change.type)) {
    return refreshMeshSemanticsFlow(semanticHandlers());
  }
  return true;
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
  if (change.type === 'add' || change.type === 'edit') {
    const meshesRefreshed =
      await refreshMeshSemanticsFlow(semanticHandlers());
    return meshesRefreshed
      ? refreshControlSemanticsFlow(semanticHandlers()) : false;
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

async function openStartupMod() {
  const consume = window.pywebview?.api?.consume_startup_request;
  if (typeof consume !== 'function') return false;

  let request;
  try {
    request = await consume.call(window.pywebview.api);
  } catch (error) {
    await alertDialog(
      'Could not open startup mod:\n\n' + (error?.message || String(error)));
    return true;
  }
  if (!request) return true;
  if (request.error) {
    await alertDialog(`Could not open startup mod:\n\n${request.error}`);
    return true;
  }

  if (typeof request.disabled_ini === 'boolean') {
    const checkbox = $('open-disabled-mod');
    if (checkbox) checkbox.checked = request.disabled_ini;
  }
  await switchMod(request.path);
  return true;
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

function hasEmissionCapability() {
  return activeMeshes.some(mesh => {
    const game = mesh.material?.userData?.gameMaterial;
    if (game?.profile?.emission_source !== 'emission_map_rgb') return false;
    return !!mesh.userData.emissionMapKey
      || !!mesh.userData.defaultEmissionMapKey
      || !!mesh.userData.resolvedEmissionMapKey
      || (mesh.userData.emissionMapVariants?.length ?? 0) > 0;
  });
}

function syncBloomControl() {
  const button = $('bloom-btn');
  const available = hasEmissionCapability();
  setBloomAvailable(available);
  const enabled = available && getBloomEnabled();
  button.hidden = !available;
  button.disabled = !available;
  button.classList.toggle('off', !enabled);
  button.classList.toggle('active', enabled);
  button.setAttribute('aria-pressed', String(enabled));
  const label = available
    ? `Emission bloom: ${enabled ? 'on' : 'off'}`
    : 'Emission bloom unavailable: no GlowMap detected';
  button.title = label;
  button.setAttribute('aria-label', label);
  return enabled;
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
  $('bloom-btn').addEventListener('click', () => {
    setBloomEnabled(!getBloomEnabled());
    syncBloomControl();
  });
  $('grid-btn').addEventListener('click', toggleGrid);
  $('shading-btn').addEventListener('click', toggleSmoothShading);
  $('toon-btn').addEventListener('click', toggleToonShading);
  $('glossy-btn').addEventListener('click', toggleGlossy);
  const syncAmbientOcclusionControl = initToolPopovers();
  syncBloomControl();
  for (const eventName of [
    'mod-viewer-mod-load-started', 'mod-viewer-mod-loaded',
    'mod-viewer-asset-load-started', 'mod-viewer-asset-loaded',
    'mod-viewer-mesh-state-changed',
  ]) {
    window.addEventListener(eventName, syncBloomControl);
  }
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
  initWeightPanel();
  initRightDock();
  createRigOverlayController({
    scene, camera, canvas: renderer.domElement,
    arcballControls: controls,
    getMeshes: () => activeMeshes,
    getRigState: getModelRigState,
    getRigDebugState: getModelRigDebugState,
    getRigBonePoseFrame,
    getRigJointPoseFrame,
    setRigBoneRotation,
    setRigJointRotation,
    finishRigPose,
    finishRigJointPose: jointId => {
      const joint = getModelRigState().model?.joints?.find(item =>
        item.jointId === Number(jointId));
      const member = joint?.representativeMember || joint?.members?.[0];
      return member ? finishRigPose(member.sourceKey, member.boneId) : false;
    },
    onTransformControlsUnavailable: () => setRigPoseControlStatus(
      'Pose gizmo is unavailable in this build.'),
    requestRender,
  });
  initRigPanel();
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
      emissionMapBound: !!game?.bindings?.emission_map?.enabledNode?.value,
      emissionSource: game?.profile?.emission_source || null,
      supportedDebugModes: game?.supportedDebugModes || [],
      debugMode: getMaterialDebugMode(mesh?.material),
    };
  };
  const setMaterialDebugModeForMeshes = mode => {
    const normalized = setMaterialDebugMode(activeMeshes, mode);
    setOutlineSuppressedByDebug(normalized !== 'off');
    setBloomSuppressedByDebug(normalized !== 'off');
    // Diagnostics use the same stable packed bindings as normal rendering.
    // WuWa's packed normal is already resident when debug mode changes, so a
    // B/A view only changes the diagnostic uniform and binding selection.
    activeMeshes.forEach(refreshMeshTexture);
    requestRender();
    return normalized;
  };
  window.modViewer = {
    displayMeshPayload, openMod, switchMod, switchAsset, reloadCurrentMod,
    getLoadBenchmark,
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
    getBloomEnabled,
    setBloomEnabled: value => {
      const changed = setBloomEnabled(value);
      syncBloomControl();
      return changed;
    },
    getMaterialState,
    getModelPhysicsState,
    enableModelPhysics,
    disableModelPhysics,
    resetModelPhysicsMotion,
    getModelWeightState,
    getWeightPhysicsPerformanceStats,
    resetWeightPhysicsPerformanceStats,
    ensureModelWeightsLoaded,
    setModelWeightHeatmap,
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
  Object.defineProperties(window.modViewer, {
    getModelRigState: {value: getModelRigState},
    getModelRigDebugState: {value: getModelRigDebugState},
    getRigBonePoseFrame: {value: getRigBonePoseFrame},
    getRigJointPoseFrame: {value: getRigJointPoseFrame},
    ensureModelRigLoaded: {value: ensureModelRigLoaded},
    setRigVisible: {value: setRigVisible},
    setRigOverlayScope: {value: setRigOverlayScope},
    getRigRotationSnapDegrees: {value: getRigRotationSnapDegrees},
    setRigRotationSnapDegrees: {value: setRigRotationSnapDegrees},
    setActiveRigSource: {value: setActiveRigSource},
    beginRigPicking: {value: beginRigPicking},
    cancelRigPicking: {value: cancelRigPicking},
    selectRigBone: {value: selectRigBone},
    selectRigJoint: {value: selectRigJoint},
    setRigComponentRoot: {value: setRigComponentRoot},
    setRigBoneRotation: {value: setRigBoneRotation},
    setRigJointRotation: {value: setRigJointRotation},
    finishRigPose: {value: finishRigPose},
    resetRigBone: {value: resetRigBone},
    resetRigPose: {value: resetRigPose},
    getRigPresetState: {value: getRigPresetState},
    applySavedRigPosePreset: {value: applySavedRigPosePreset},
    saveRigPosePreset: {value: saveRigPosePreset},
    renameRigPosePreset: {value: renameRigPosePreset},
    deleteRigPosePreset: {value: deleteRigPosePreset},
    selectRigPosePreset: {value: selectRigPosePreset},
  });

  void openStartupMod().then(apiReady => {
    if (!apiReady) {
      window.addEventListener(
        'pywebviewready', () => void openStartupMod(), { once: true });
    }
  });
});
