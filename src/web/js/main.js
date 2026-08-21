// Entry point: wires the toolbar and orchestrates loading a mod.

import { fitTo, resetView, rotateModelHorizontalQuarterTurn, rotateModelQuarterTurn,
         toggleGrid, toggleTrackballGizmo,
         getEnvironmentPreset, getLightMode, isRendererAvailable, rendererReady,
         setEnvironmentPreset, setLightMode, getRenderCount } from './scene.js';
import { ENVIRONMENT_PRESETS } from './environment.js';
import { refreshMeshTexture, setTextures } from './mesh-factory.js';
import { activeMeshes, reset, resetMeshState, setStateRules, toggleWireframe, toggleSmoothShading, toggleGlossy } from './visibility.js';
import { initSelection, clearSelection } from './selection.js';
import { buildMeshPanel } from './mesh-panel.js';
import { buildTogglePanel } from './toggle-panel.js';
import { buildMenuPanel } from './menu-panel.js';
import { buildPresentPanel } from './present-panel.js';
import { initModFolderPanel } from './mod-folder-panel.js';
import { alertDialog, confirmDialog } from './dialogs.js';
import { setGeometryBlob } from './decode.js';
import { refreshHealthReport, setHealthLoader, setHealthReport } from './health-report.js';
import { setIniEditorContext } from './ini-editor.js';
import { getMaterialDebugMode, setMaterialDebugMode } from './material-profile.js';
import { requestRender } from './render-scheduler.js';
import { setTextureDisplayMode } from './render-modes.js';
import { clearInspector, initInspectorPanel } from './inspector-panel.js';
import { initRightDock, setRightDockVisible } from './right-dock.js';
import {
  getOutlineState as getMeshOutlineState,
  setOutlineSuppressedByDebug,
  setOutlinesEnabled,
} from './outline-renderer.js';

const $ = (id) => document.getElementById(id);

function initEnvironmentControl() {
  const button = $('environment-btn');
  const icon = $('environment-icon');
  const label = $('environment-label');
  const ids = Object.values(ENVIRONMENT_PRESETS).map(preset => preset.id);
  const labels = Object.fromEntries(
    Object.values(ENVIRONMENT_PRESETS).map(preset => [preset.id, preset.label]));
  const popover = $('environment-popover');
  let currentId = getEnvironmentPreset().id;

  function updateControl(id) {
    const name = labels[id] || id;
    icon.dataset.environment = id;
    button.dataset.environment = id;
    label.textContent = name;
    button.setAttribute('aria-label', `Environment: ${name}. Click to change.`);
    button.title = `Environment: ${name} (click to change)`;
  }

  function applyEnvironmentPreset(id) {
    if (!setEnvironmentPreset(id)) return false;
    currentId = getEnvironmentPreset().id;
    updateControl(currentId);
    return true;
  }

  function closePopover() {
    if (!popover) return;
    popover.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  }

  function openPopover() {
    if (!popover) return;
    popover.replaceChildren();
    Object.values(ENVIRONMENT_PRESETS).forEach(preset => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'ui-popover-option';
      option.setAttribute('role', 'menuitem');
      option.textContent = preset.label;
      option.classList.toggle('selected', preset.id === currentId);
      option.addEventListener('click', () => {
        applyEnvironmentPreset(preset.id);
        closePopover();
      });
      popover.appendChild(option);
    });
    popover.hidden = false;
    button.setAttribute('aria-expanded', 'true');
  }

  updateControl(currentId);
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', () => {
    if (popover?.hidden === false) closePopover();
    else openPopover();
  });
  document.addEventListener('click', event => {
    if (!popover || popover.hidden || event.target.closest('#environment-control, #environment-popover')) return;
    closePopover();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closePopover();
  });

  return applyEnvironmentPreset;
}

function initToolPopovers() {
  const textureButton = $('texture-btn');
  const lightButton = $('light-btn');
  const texturePopover = $('texture-popover');
  const lightPopover = $('light-popover');
  const close = popover => {
    if (!popover) return;
    popover.hidden = true;
  };
  const closeAll = () => {
    close(texturePopover);
    close(lightPopover);
    textureButton?.setAttribute('aria-expanded', 'false');
    lightButton?.setAttribute('aria-expanded', 'false');
  };

  function toggleTexturePopover() {
    if (!texturePopover) return;
    const wasOpen = !texturePopover.hidden;
    closeAll();
    if (wasOpen) return;
    texturePopover.replaceChildren();
    [
      ['all', 'All maps'], ['diffuse', 'Diffuse only'], ['none', 'No textures'],
    ].forEach(([mode, label]) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'ui-popover-option';
      option.setAttribute('role', 'menuitem');
      option.textContent = label;
      option.addEventListener('click', () => {
        setTextureDisplayMode(mode, activeMeshes);
        closeAll();
      });
      texturePopover.appendChild(option);
    });
    texturePopover.hidden = false;
    textureButton?.setAttribute('aria-expanded', 'true');
  }

  function toggleLightPopover() {
    if (!lightPopover) return;
    const wasOpen = !lightPopover.hidden;
    closeAll();
    if (wasOpen) return;
    [['double', 'Bright'], ['current', 'Normal'], ['off', 'Off']].forEach(([mode, label]) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'ui-popover-option';
      option.setAttribute('role', 'menuitemradio');
      option.setAttribute('aria-checked', String(getLightMode() === mode));
      option.textContent = label;
      option.addEventListener('click', () => {
        setLightMode(mode);
        closeAll();
      });
      lightPopover.appendChild(option);
    });
    lightPopover.hidden = false;
    lightButton?.setAttribute('aria-expanded', 'true');
  }

  textureButton?.setAttribute('aria-haspopup', 'menu');
  lightButton?.setAttribute('aria-haspopup', 'menu');
  textureButton?.setAttribute('aria-expanded', 'false');
  lightButton?.setAttribute('aria-expanded', 'false');
  textureButton?.addEventListener('click', toggleTexturePopover);
  lightButton?.addEventListener('click', toggleLightPopover);
  document.addEventListener('click', event => {
    if (event.target.closest('#texture-btn, #texture-popover, #light-btn, #light-popover')) return;
    closeAll();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeAll();
  });
}

function syncViewportControlPlacement() {
  const rightDock = $('right-dock');
  const panelIds = ['inspector-panel', 'present-panel', 'toggle-panel', 'menu-panel'];
  const hasVisiblePanel = rightDockEnabled && panelIds.some(id => {
    const panel = $(id);
    return panel && !panel.hidden && getComputedStyle(panel).display !== 'none';
  });
  setRightDockVisible(hasVisiblePanel);
  document.body.classList.toggle('right-dock-visible', hasVisiblePanel);
}

function initToolbarOverflow() {
  const button = $('toolbar-more');
  const menu = $('toolbar-overflow');
  if (!button || !menu) return;
  const close = () => {
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  };
  button.addEventListener('click', event => {
    event.stopPropagation();
    menu.hidden = !menu.hidden;
    button.setAttribute('aria-expanded', String(!menu.hidden));
  });
  menu.addEventListener('click', event => {
    const item = event.target.closest('[data-toolbar-target]');
    if (!item) return;
    const target = $(item.dataset.toolbarTarget);
    if (target && !target.disabled) target.click();
    close();
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('#toolbar-more, #toolbar-overflow')) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') close();
  });
}

// The currently open mod folder, so the Toggle panel's staged add/edit/delete
// actions know which session to update and reloadCurrentMod() knows what to
// refresh afterward.
let currentModPath = null;
let modTransitionInFlight = false;
let rightDockEnabled = false;

// The last-loaded payload's controls.toggles model, kept
// around purely so refreshPendingState() can check for a still-unwired
// toggle without re-fetching anything — see hasUnwiredToggle().
let lastToggles = {};

function showLoading(on, message) {
  if (message) $('status-text').textContent = message;
  $('loading').classList.toggle('show', on);
}

/** Normalizes a Windows path for a same-folder comparison: slash direction,
 * case and a trailing separator all vary without the folder actually being
 * different. */
function samePath(a, b) {
  const norm = (p) => p.replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '');
  return !!a && !!b && norm(a) === norm(b);
}

/** True if the currently displayed Toggle panel has a toggle that doesn't
 * gate any mesh yet -- only ever a toggle just added via "＋ Add" this
 * session and not yet wired via Record mode; a pre-existing on-disk
 * non-gating key is never loaded into the panel at all. */
function hasUnwiredToggle() {
  return Object.values(lastToggles).some((info) => info.wired === false);
}

// Reflects the currently open mod's staged-but-not-yet-exported edits onto
// the toolbar: Export only enables once there's something to export, and
// the indicator is the persistent signal that something is unsaved.
async function refreshPendingState() {
  const pending = currentModPath ? await window.pywebview.api.has_pending_changes(currentModPath) : false;
  $('pending-indicator').classList.toggle('show', pending);
  const blocked = pending && hasUnwiredToggle();
  $('export-btn').disabled = !pending || blocked;
  $('export-btn').title = blocked
    ? 'A newly-added toggle isn\'t wired to any mesh yet — Record (⏺) or delete it before exporting.'
    : '';
}

function clearScene() {
  clearSelection();
  clearInspector();
  rightDockEnabled = false;
  reset();
  // Debug mode is material-local and does not survive a reload; keep outline
  // suppression in the same lifecycle rather than carrying stale state onto
  // the next mod's normal materials.
  setOutlineSuppressedByDebug(false);
  setTextures(null);
  setGeometryBlob(null);
  lastToggles = {};
  $('sidebar').style.display = 'none';
  $('mesh-list').innerHTML = '';
  $('camera-panel').style.display = 'none';
  $('toggle-list').innerHTML = '';
  $('toggle-panel').style.display = 'none';
  $('present-list').innerHTML = '';
  $('present-panel').style.display = 'none';
  $('menu-list').innerHTML = '';
  $('menu-panel').style.display = 'none';
  setRightDockVisible(false);
  syncViewportControlPlacement();
}

function clearPendingState() {
  $('pending-indicator').classList.remove('show');
  $('export-btn').disabled = true;
  $('export-btn').title = '';
}

/** Commit the UI to a new folder before asking the backend to load it. This
 * makes the folder path, editor/diagnostic context, model, panels and pending
 * state one transition: a failed replacement can never leave the previous
 * mod visible under the new folder's toolbar state. */
function beginModLoad(path, message) {
  currentModPath = path;
  clearScene();
  clearPendingState();
  window.dispatchEvent(new CustomEvent('mod-viewer-mod-load-started', {
    detail: { path },
  }));
  $('hint').style.display = 'none';
  $('empty-actions').style.display = 'none';
  $('mod-path').textContent = path;
  $('mod-path').title = path;
  setHealthReport(null);
  setHealthLoader(() => window.pywebview.api.get_diagnostics(path));
  setIniEditorContext(path, reloadCurrentMod);
  showLoading(true, message);
}

async function displayMeshPayload(payload) {
  const geometry = payload.geometry;
  if (geometry) {
    const response = await fetch(geometry.url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Geometry download failed (${response.status}).`);
    const blob = await response.arrayBuffer();
    if (blob.byteLength !== geometry.length) throw new Error('Geometry download was incomplete.');
    setGeometryBlob(blob);
  } else {
    setGeometryBlob(null);
  }

  const controls = payload.controls || {};
  const state = payload.state || {};
  const meshes = payload.meshes || {};
  lastToggles = controls.toggles || {};
  setStateRules(state.rules || [], state.defaults || {});
  setTextures(payload.textures);
  buildMeshPanel(
    meshes, currentModPath, payload.metadata?.mesh_names || {},
    payload.metadata?.material_profiles || {},
    {
      onMaterialKindChanged: reloadCurrentMod,
      texturePools: payload.texture_pools || {},
    });
  buildTogglePanel(controls.toggles, { modPath: currentModPath, onChange: reloadCurrentMod });
  buildMenuPanel(controls.menu);
  buildPresentPanel(controls.present, { modPath: currentModPath, onChange: reloadCurrentMod });
  rightDockEnabled = true;
  syncViewportControlPlacement();
  fitTo(activeMeshes);

  showLoading(false);
}

async function loadModAt(path) {
  beginModLoad(path, 'Loading Model…');

  const data = await window.pywebview.api.load_mod(path);
  setHealthReport(data?.health);
  if (data && data.error) {
    showLoading(false);
    await refreshPendingState();
    await alertDialog('Could not load mod:\n\n' + data.error);
    return false;
  }
  try {
    await displayMeshPayload(data);
  } catch (error) {
    clearScene();
    clearPendingState();
    showLoading(false);
    await refreshPendingState();
    await alertDialog('Could not load mod geometry:\n\n' + error.message);
    return false;
  }
  await refreshPendingState();

  // Lead with the folder name; the full path is long and rarely the useful part.
  const folderName = path.replace(/\\/g, '/').split('/').filter(Boolean).at(-1);
  $('mod-path').textContent = `${folderName}  —  ${path}`;
  $('mod-path').textContent = folderName;
  $('mod-path').title = path;
  window.dispatchEvent(new CustomEvent('mod-viewer-mod-loaded', {
    detail: { path },
  }));
  // Diagnostics are independent of geometry rendering. Start them after the
  // mod is visible so the toolbar badge is populated without requiring a
  // click on the Diagnostics button.
  void refreshHealthReport();
  return true;
}

async function performModSwitch(path) {
  // Switching to a different folder while the current one has staged,
  // not-yet-exported edits would silently strand them in memory, so ask
  // first. Reopening the same folder, or one with nothing pending, needs
  // no confirmation.
  if (currentModPath && !samePath(currentModPath, path) &&
      await window.pywebview.api.has_pending_changes(currentModPath)) {
    const proceed = await confirmDialog(
      'This mod has unsaved changes that haven\'t been exported.\n\n' +
      'Opening a different mod folder will discard them. Continue?');
    if (!proceed) return false;
    await window.pywebview.api.discard_changes(currentModPath);
  }

  return await loadModAt(path);
}

async function runModTransition(operation) {
  if (modTransitionInFlight || !isRendererAvailable()) return false;
  modTransitionInFlight = true;
  const btn = $('open-btn');
  btn.disabled = true;
  try {
    return await operation();
  } finally {
    modTransitionInFlight = false;
    btn.disabled = !isRendererAvailable();
  }
}

async function switchMod(path) {
  if (!path) return false;
  return await runModTransition(async () => {
    try {
      return await performModSwitch(path);
    } catch (e) {
      showLoading(false);
      await alertDialog('Unexpected error:\n\n' + e);
      return false;
    }
  });
}

async function openMod() {
  return await runModTransition(async () => {
    try {
      const path = await window.pywebview.api.select_folder();
      if (!path) return false;
      return await performModSwitch(path);
    } catch (e) {
      showLoading(false);
      await alertDialog('Unexpected error:\n\n' + e);
      return false;
    }
  });
}

// Re-renders the current authoritative edit session after a staged authoring
// change, using the same load path as an ordinary reopen.
export async function reloadCurrentMod() {
  if (!currentModPath) return false;
  return await runModTransition(async () => {
    try {
      return await loadModAt(currentModPath);
    } catch (e) {
      showLoading(false);
      await alertDialog('Unexpected error while reloading:\n\n' + e);
      return false;
    }
  });
}

async function exportChanges() {
  if (!currentModPath) return;
  const btn = $('export-btn');
  btn.disabled = true;
  try {
    const result = await window.pywebview.api.export_changes(currentModPath);
    if (result.error) {
      // Refused outright — e.g. a newly-added toggle is still unwired (see
      // toggle_api.export_changes). The button is normally already disabled
      // for this case (refreshPendingState/hasUnwiredToggle), so reaching
      // here at all means the panel was momentarily stale; nothing was
      // written either way.
      await alertDialog('Export was blocked:\n\n' + result.error);
    } else if (result.failed && result.failed.length) {
      const detail = result.failed.map((f) => `${f.ini}: ${f.error}`).join('\n');
      await alertDialog(
        `${result.saved.length} ini file(s) exported, but ${result.failed.length} failed ` +
        `and are still pending:\n\n${detail}`);
    }
    // Refreshes the panel/badges from the now-partly-or-fully-exported session
    // state either way, and re-syncs the indicator via its own call to
    // refreshPendingState (a partial failure leaves those inis' edits
    // pending, so it stays lit rather than clearing, and the button
    // re-enables for a retry).
    await reloadCurrentMod();
  } catch (e) {
    await alertDialog('Unexpected error while exporting:\n\n' + e);
    await refreshPendingState();
  }
}

// Collapses a panel's body when its header is clicked.
function initPanelCollapse(panel, contentId) {
  const hdr = panel.querySelector('.panel-hdr');
  const chevron = hdr.querySelector('.group-toggle');
  const content = $(contentId);
  const storageKey = `mod-viewer.panel.${panel.id}.collapsed`;
  const setCollapsed = (collapsed, persist = true) => {
    chevron.classList.toggle('collapsed', collapsed);
    content.classList.toggle('collapsed', collapsed);
    chevron.setAttribute('aria-expanded', String(!collapsed));
    chevron.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${panel.querySelector('h3')?.textContent || 'panel'}`);
    if (persist) {
      try { localStorage.setItem(storageKey, String(collapsed)); } catch (_) { /* private mode */ }
    }
  };
  let initiallyCollapsed = false;
  try { initiallyCollapsed = localStorage.getItem(storageKey) === 'true'; } catch (_) { /* private mode */ }
  chevron.setAttribute('aria-controls', contentId);
  setCollapsed(initiallyCollapsed, false);
  const toggle = (e) => {
    if (e?.target?.closest?.('.icon-btn, .panel-actions, .panel-action-menu')) return;
    e?.stopPropagation?.();
    setCollapsed(!content.classList.contains('collapsed'));
  };
  hdr.addEventListener('click', e => {
    if (e.target.closest('.icon-btn, .group-toggle, .panel-actions, .panel-action-menu')) return;
    setCollapsed(!content.classList.contains('collapsed'));
  });
  chevron.addEventListener('click', toggle);
}

initToolbarOverflow();

rendererReady.then(ready => {
  if (!ready || !isRendererAvailable()) return;

  $('open-btn').addEventListener('click', openMod);
  $('export-btn').addEventListener('click', exportChanges);
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
  $('glossy-btn').addEventListener('click', toggleGlossy);
  initToolPopovers();
  $('reset-state-btn').addEventListener('click', event => {
    event.stopPropagation();
    resetMeshState();
  });
  $('trackball-btn').addEventListener('click', toggleTrackballGizmo);
  $('camera-reset-view-btn').addEventListener('click', () => resetView(activeMeshes));
  $('camera-flip-btn').addEventListener('click', () => rotateModelQuarterTurn(activeMeshes));
  $('camera-flip-horizontal-btn').addEventListener('click', () => rotateModelHorizontalQuarterTurn(activeMeshes));
  const applyEnvironmentPreset = initEnvironmentControl();
  initRightDock();
  initInspectorPanel();
  initSelection();
  const viewportCameraButtons = $('viewport-camera-buttons');
  const cameraButtons = $('camera-buttons');
  if (viewportCameraButtons && cameraButtons) viewportCameraButtons.append(cameraButtons);
  syncViewportControlPlacement();
  initPanelCollapse($('sidebar'), 'mesh-list');
  initPanelCollapse($('tool-panel'), 'tool-buttons');
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
  $('empty-open-btn').disabled = false;
  emptyFolderAction.disabled = false;
  $('empty-open-btn').addEventListener('click', openMod);
  emptyFolderAction.addEventListener('click', () => {
    modFolderPanel.setExpanded(true);
    if (!hasModFolders) modFolderPanel.openAddDialog();
  });
  $('mod-folder-empty-add')?.addEventListener('click', modFolderPanel.openAddDialog);

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
    displayMeshPayload, openMod, switchMod, reloadCurrentMod, exportChanges, activeMeshes,
    setEnvironmentPreset: applyEnvironmentPreset, getEnvironmentPreset,
    getMaterialState, getRenderCount,
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
  };
});
