// Model/source loading lifecycle and the application-facing transition flow.

import { viewerState, samePath } from './state.js';
import { resetAssetFillState, updateAssetFillButton } from './asset-fill.js';
import {
  fitTo, isRendererAvailable, setBloomSuppressedByDebug,
} from '../scene/scene.js';
import { setRightDockEnabled } from '../panels/right-dock.js';
import { clearSelection } from '../scene/selection.js';
import { clearInspector } from '../panels/inspector-panel.js';
import {
  activeMeshes, refreshAll, reset, setStateRules,
} from '../mesh/visibility.js';
import { setTextures } from '../mesh/mesh-factory.js';
import { buildMeshPanel } from '../panels/mesh-panel.js';
import { setMeshesAvailable } from '../panels/left-dock.js';
import { buildTogglePanel } from '../panels/toggle-panel.js';
import { buildMenuPanel } from '../panels/menu-panel.js';
import { buildPresentPanel } from '../panels/present-panel.js';
import { alertDialog, confirmDialog } from '../ui/dialogs.js';
import { setGeometryBlob } from '../textures/decode.js';
import {
  refreshHealthReport, setAssetResolution, setHealthLoader,
  setHealthReport,
} from '../panels/health-report.js';
import { setIniEditorContext } from '../editing/ini-editor.js';
import { setOutlineSuppressedByDebug } from '../scene/outline-renderer.js';

const $ = (id) => document.getElementById(id);

export function syncViewportControlPlacement() {
  setRightDockEnabled(viewerState.rightDockEnabled);
}

function showLoading(on, message) {
  if (message) $('status-text').textContent = message;
  $('loading').classList.toggle('show', on);
}

function hasUnwiredToggle() {
  return Object.values(viewerState.lastToggles)
    .some((info) => info.wired === false);
}

// Reflect the currently open mod's staged-but-not-yet-exported edits onto
// the toolbar: Export only enables once there's something to export, and the
// indicator is the persistent signal that something is unsaved.
export async function refreshPendingState(
    path = viewerState.currentModPath, guard = null) {
  const pending = path ? await window.pywebview.api.has_pending_changes(path) : false;
  if ((guard && !guard()) || (path && !samePath(viewerState.currentModPath, path))) {
    return false;
  }
  $('pending-indicator').classList.toggle('show', pending);
  const blocked = pending && hasUnwiredToggle();
  $('export-btn').disabled = !pending || blocked;
  $('export-btn').title = blocked
    ? 'A newly-added toggle isn\'t wired to any mesh yet — Record (⏺) or delete it before exporting.'
    : '';
}

export function clearScene({ preserveModelOrientation = false } = {}) {
  clearSelection();
  clearInspector();
  viewerState.rightDockEnabled = false;
  reset({ preserveModelOrientation });
  // Debug mode is material-local and does not survive a reload; keep outline
  // suppression in the same lifecycle rather than carrying stale state onto
  // the next mod's normal materials.
  setOutlineSuppressedByDebug(false);
  setBloomSuppressedByDebug(false);
  resetAssetFillState();
  setTextures(null);
  setGeometryBlob(null);
  viewerState.lastToggles = {};
  $('mesh-list').innerHTML = '';
  $('camera-panel').style.display = 'none';
  $('toggle-list').innerHTML = '';
  $('toggle-panel').style.display = 'none';
  $('present-list').innerHTML = '';
  $('present-panel').style.display = 'none';
  $('menu-list').innerHTML = '';
  $('menu-panel').style.display = 'none';
  setMeshesAvailable(false);
  setRightDockEnabled(false);
  syncViewportControlPlacement();
  updateAssetFillButton();
}

function clearPendingState() {
  $('pending-indicator').classList.remove('show');
  $('export-btn').disabled = true;
  $('export-btn').title = '';
}

function setSourceUi(kind) {
  const asset = kind === 'asset';
  document.body.classList.toggle('asset-preview-mode', asset);
  if (asset) {
    setHealthLoader(null);
    setHealthReport(null, null);
    setIniEditorContext(null, null);
  }
  $('pending-indicator').classList.toggle('show', false);
  $('pending-indicator').setAttribute('aria-hidden', String(asset));
  if (asset) $('export-btn').disabled = true;
  updateAssetFillButton();
}

/** Commit the UI to a new folder before asking the backend to load it. */
function beginModLoad(path, message, {
  preserveModelOrientation = false,
  onReload = null,
} = {}) {
  viewerState.assetFill.epoch += 1;
  viewerState.currentModPath = path;
  viewerState.currentSource = { kind: 'mod', path };
  setSourceUi('mod');
  viewerState.semanticRefreshEpoch += 1;
  clearScene({ preserveModelOrientation });
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
  setIniEditorContext(path, onReload || reloadCurrentMod);
  showLoading(true, message);
}

function beginAssetLoad(path, entry, message = 'Loading Asset…', {
  preserveModelOrientation = false,
} = {}) {
  viewerState.assetFill.epoch += 1;
  viewerState.currentModPath = null;
  viewerState.currentSource = {
    kind: 'asset', path, assetType: entry?.asset_type || null,
  };
  viewerState.semanticRefreshEpoch += 1;
  clearScene({ preserveModelOrientation });
  clearPendingState();
  setSourceUi('asset');
  window.dispatchEvent(new CustomEvent('mod-viewer-asset-load-started', {
    detail: { path, entry },
  }));
  $('hint').style.display = 'none';
  $('empty-actions').style.display = 'none';
  $('mod-path').textContent = `Asset Preview  —  ${path}`;
  $('mod-path').title = path;
  setAssetResolution(null);
  showLoading(true, message);
}

export async function displayMeshPayload(payload, {
  preserveCamera = false,
  onToggleChange = null,
  onPresentChange = null,
  onMaterialKindChanged = reloadCurrentMod,
} = {}) {
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
  const assetMode = viewerState.currentSource?.kind === 'asset'
    || payload.metadata?.source_kind === 'asset';
  viewerState.assetFill.available = !assetMode
    && Number(payload.asset_resolution?.configured_roots || 0) > 0;
  if (assetMode && viewerState.currentSource?.kind !== 'asset') {
    viewerState.currentSource = {
      kind: 'asset', path: payload.metadata?.asset?.path || '',
      assetType: payload.metadata?.asset?.type || null,
    };
    setSourceUi('asset');
  }
  const modelPath = assetMode ? null : viewerState.currentModPath;
  viewerState.lastToggles = controls.toggles || {};
  setStateRules(state.rules || [], state.defaults || {}, {
    toggles: controls.toggles || {}, menu: controls.menu || {},
  });
  setTextures(payload.textures);
  buildMeshPanel(
    meshes, modelPath, payload.metadata?.mesh_names || {},
    payload.metadata?.material_profiles || {},
    {
      onMaterialKindChanged: assetMode ? null : onMaterialKindChanged,
      texturePools: payload.texture_pools || {},
      assetResolution: payload.asset_resolution || null,
      readOnlySource: assetMode,
      texturePicker: assetMode
        ? (role => window.pywebview.api.pick_asset_texture_file(
          viewerState.currentSource.path, role)) : null,
    });
  buildTogglePanel(controls.toggles, {
    modPath: viewerState.currentModPath, onChange: onToggleChange,
  });
  buildMenuPanel(controls.menu);
  buildPresentPanel(controls.present, {
    modPath: viewerState.currentModPath, onChange: onPresentChange,
  });
  refreshAll({
    force: { visibility: true, textures: true, shapes: true },
  });
  setMeshesAvailable(true);
  viewerState.rightDockEnabled = true;
  syncViewportControlPlacement();
  fitTo(activeMeshes, {
    preserveCamera,
    // WWMI models use the opposite horizontal facing convention from the
    // viewer's default front view. Keep this as a model base transform so
    // camera controls and viewer-only orientation state remain independent.
    initialRotationY: payload.metadata?.game?.id === 'wuwa' ? Math.PI : 0,
  });

  updateAssetFillButton();
  showLoading(false);
}

async function loadModAt(path, handlers = {}) {
  const preserveViewerPose = samePath(viewerState.displayedModPath, path);
  beginModLoad(path, 'Loading Model…', {
    preserveModelOrientation: preserveViewerPose,
    onReload: handlers.onReload,
  });

  const data = await window.pywebview.api.load_mod(path);
  setHealthReport(data?.health, data?.asset_resolution);
  if (data && data.error) {
    showLoading(false);
    await refreshPendingState();
    await alertDialog('Could not load mod:\n\n' + data.error);
    return false;
  }
  try {
    await displayMeshPayload(data, {
      preserveCamera: preserveViewerPose,
      ...handlers,
    });
  } catch (error) {
    clearScene({ preserveModelOrientation: preserveViewerPose });
    clearPendingState();
    showLoading(false);
    await refreshPendingState();
    await alertDialog('Could not load mod geometry:\n\n' + error.message);
    return false;
  }
  await refreshPendingState();

  // Lead with the folder name; the full path is long and rarely the useful part.
  const folderName = path.replace(/\\/g, '/').split('/').filter(Boolean).at(-1);
  $('mod-path').textContent = folderName;
  $('mod-path').title = path;
  viewerState.displayedModPath = path;
  viewerState.displayedSource = { kind: 'mod', path };
  window.dispatchEvent(new CustomEvent('mod-viewer-mod-loaded', {
    detail: { path },
  }));
  // Diagnostics are independent of geometry rendering. Start them after the
  // mod is visible so the toolbar badge is populated without requiring a
  // click on the Diagnostics button.
  void refreshHealthReport();
  return true;
}

async function loadAssetAt(path, entry = {}, handlers = {}) {
  const preserveViewerPose = viewerState.displayedSource?.kind === 'asset'
    && samePath(viewerState.displayedSource.path, path);
  beginAssetLoad(path, entry, 'Loading Asset…', {
    preserveModelOrientation: preserveViewerPose,
  });
  const data = await window.pywebview.api.load_asset(path);
  if (data && data.error) {
    showLoading(false);
    await alertDialog('Could not load Asset:\n\n' + data.error);
    return false;
  }
  try {
    await displayMeshPayload(data, {
      preserveCamera: preserveViewerPose,
      ...handlers,
    });
  } catch (error) {
    clearScene({ preserveModelOrientation: preserveViewerPose });
    clearPendingState();
    showLoading(false);
    await alertDialog('Could not load Asset geometry:\n\n' + error.message);
    return false;
  }
  const folderName = path.replace(/\\/g, '/').split('/').filter(Boolean).at(-1);
  $('mod-path').textContent = `Asset Preview  —  ${folderName}`;
  $('mod-path').title = path;
  viewerState.displayedModPath = null;
  viewerState.displayedSource = { ...viewerState.currentSource };
  window.dispatchEvent(new CustomEvent('mod-viewer-asset-loaded', {
    detail: { path, entry, source: viewerState.displayedSource },
  }));
  return true;
}

async function performModSwitch(path, handlers = {}) {
  // Switching to a different folder while the current one has staged,
  // not-yet-exported edits would silently strand them in memory, so ask first.
  if (viewerState.currentSource?.kind === 'mod' && viewerState.currentModPath
      && !samePath(viewerState.currentModPath, path)
      && await window.pywebview.api.has_pending_changes(viewerState.currentModPath)) {
    const proceed = await confirmDialog(
      'This mod has unsaved changes that haven\'t been exported.\n\n' +
      'Opening a different mod folder will discard them. Continue?');
    if (!proceed) return false;
    await window.pywebview.api.discard_changes(viewerState.currentModPath);
  }

  return await loadModAt(path, handlers);
}

async function confirmLeaveCurrentModIfDirty() {
  if (viewerState.currentSource?.kind !== 'mod' || !viewerState.currentModPath) return true;
  if (!await window.pywebview.api.has_pending_changes(viewerState.currentModPath)) return true;
  const proceed = await confirmDialog(
    'This mod has unsaved changes that haven\'t been exported.\n\n' +
    'Opening an Asset preview will discard them. Continue?');
  if (!proceed) return false;
  await window.pywebview.api.discard_changes(viewerState.currentModPath);
  return true;
}

export async function switchAsset(path, entry = {}, handlers = {}) {
  if (!path || !entry?.asset) return false;
  return await runModTransition(async () => {
    try {
      if (!await confirmLeaveCurrentModIfDirty()) return false;
      return await loadAssetAt(path, entry, handlers);
    } catch (error) {
      showLoading(false);
      await alertDialog('Unexpected error while loading Asset:\n\n' + error);
      return false;
    }
  });
}

export async function runModTransition(operation) {
  if (viewerState.modTransitionInFlight || !isRendererAvailable()) return false;
  viewerState.modTransitionInFlight = true;
  const button = $('open-btn');
  button.disabled = true;
  try {
    return await operation();
  } finally {
    viewerState.modTransitionInFlight = false;
    button.disabled = !isRendererAvailable();
  }
}

export async function switchMod(path, handlers = {}) {
  if (!path) return false;
  return await runModTransition(async () => {
    try {
      return await performModSwitch(path, handlers);
    } catch (error) {
      showLoading(false);
      await alertDialog('Unexpected error:\n\n' + error);
      return false;
    }
  });
}

export async function openMod(handlers = {}) {
  return await runModTransition(async () => {
    try {
      const path = await window.pywebview.api.select_folder();
      if (!path) return false;
      return await performModSwitch(path, handlers);
    } catch (error) {
      showLoading(false);
      await alertDialog('Unexpected error:\n\n' + error);
      return false;
    }
  });
}

// Re-render the current authoritative edit session after a staged authoring
// change, using the same load path as an ordinary reopen.
export async function reloadCurrentMod(handlers = {}) {
  if (!viewerState.currentModPath) return false;
  return await runModTransition(async () => {
    try {
      return await loadModAt(viewerState.currentModPath, handlers);
    } catch (error) {
      showLoading(false);
      await alertDialog('Unexpected error while reloading:\n\n' + error);
      return false;
    }
  });
}

export async function exportChanges() {
  if (!viewerState.currentModPath) return;
  const button = $('export-btn');
  button.disabled = true;
  try {
    const result = await window.pywebview.api.export_changes(viewerState.currentModPath);
    if (result.error) {
      // Refused outright — e.g. a newly-added toggle is still unwired. The
      // button is normally already disabled for this case, so reaching here
      // means the panel was momentarily stale; nothing was written either way.
      await alertDialog('Export was blocked:\n\n' + result.error);
    } else if (result.failed && result.failed.length) {
      const detail = result.failed.map((failure) =>
        `${failure.ini}: ${failure.error}`).join('\n');
      await alertDialog(
        `${result.saved.length} ini file(s) exported, but ${result.failed.length} failed ` +
        `and are still pending:\n\n${detail}`);
    }
    // Export writes the authoritative staged documents but does not change
    // the current model or control semantics. Refresh only session status;
    // partial failures leave the affected edits pending for retry.
    await refreshPendingState();
    void refreshHealthReport();
  } catch (error) {
    await alertDialog('Unexpected error while exporting:\n\n' + error);
    await refreshPendingState();
  }
}
