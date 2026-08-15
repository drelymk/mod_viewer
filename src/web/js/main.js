// Entry point: wires the toolbar and orchestrates loading a mod.

import { fitTo, resetView, rotateModelHorizontalQuarterTurn, rotateModelQuarterTurn, toggleGrid, toggleTrackballGizmo } from './scene.js';
import { setTextures } from './mesh-factory.js';
import { activeMeshes, reset, resetMeshState, setStateRules, toggleWireframe, toggleSmoothShading, toggleTextures } from './visibility.js';
import { initSelection, clearSelection } from './selection.js';
import { buildMeshPanel } from './mesh-panel.js';
import { buildTogglePanel } from './toggle-panel.js';
import { buildMenuPanel } from './menu-panel.js';
import { buildPresentPanel } from './present-panel.js';
import { alertDialog, confirmDialog } from './dialogs.js';
import { setGeometryBlob } from './decode.js';
import { setHealthReport } from './health-report.js';
import { setIniEditorContext } from './ini-editor.js';

const $ = (id) => document.getElementById(id);

// The currently open mod folder, so the Toggle panel's add/edit/delete
// actions know which folder to write into and reloadCurrentMod() knows what
// to reload after a successful write.
let currentModPath = null;

// The last-loaded payload's Toggle panel model (payload.__toggles__), kept
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
  reset();
  lastToggles = {};
  $('mesh-list').innerHTML = '';
  $('camera-panel').style.display = 'none';
  $('toggle-list').innerHTML = '';
  $('toggle-panel').style.display = 'none';
  $('present-list').innerHTML = '';
  $('present-panel').style.display = 'none';
  $('menu-list').innerHTML = '';
  $('menu-panel').style.display = 'none';
}

async function displayMeshPayload(payload) {
  clearScene();
  $('hint').style.display = 'none';

  const geometry = payload.__geometry__;
  if (geometry) {
    const response = await fetch(geometry.url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Geometry download failed (${response.status}).`);
    const blob = await response.arrayBuffer();
    if (blob.byteLength !== geometry.length) throw new Error('Geometry download was incomplete.');
    setGeometryBlob(blob);
  } else {
    setGeometryBlob(null);
  }

  lastToggles = payload.__toggles__ || {};
  setStateRules(payload.__state_rules__ || [], payload.__state_defaults__ || {});
  setTextures(payload.__textures__);
  buildMeshPanel(payload, currentModPath, payload.__mesh_names__ || {});
  buildTogglePanel(payload.__toggles__, { modPath: currentModPath, onChange: reloadCurrentMod });
  buildMenuPanel(payload.__menu__);
  buildPresentPanel(payload.__present__, { modPath: currentModPath, onChange: reloadCurrentMod });
  fitTo(activeMeshes);

  showLoading(false);
}

async function loadModAt(path) {
  currentModPath = path;
  $('ini-view-btn').disabled = true;
  $('mod-path').textContent = path;
  showLoading(true, 'Loading Model…');
  setHealthReport(null);

  const data = await window.pywebview.api.load_mod(path);
  setHealthReport(data?.__health__);
  if (data && data.error) {
    showLoading(false);
    setIniEditorContext(path, reloadCurrentMod);
    await alertDialog('Could not load mod:\n\n' + data.error);
    return;
  }
  try {
    await displayMeshPayload(data);
  } catch (error) {
    showLoading(false);
    setIniEditorContext(path, reloadCurrentMod);
    await alertDialog('Could not load mod geometry:\n\n' + error.message);
    return;
  }
  setIniEditorContext(path, reloadCurrentMod);
  await refreshPendingState();

  // Lead with the folder name; the full path is long and rarely the useful part.
  const folderName = path.replace(/\\/g, '/').split('/').filter(Boolean).at(-1);
  $('mod-path').textContent = `${folderName}  —  ${path}`;
}

async function openMod() {
  const btn = $('open-btn');
  btn.disabled = true;
  try {
    const path = await window.pywebview.api.select_folder();
    if (!path) return;

    // Switching to a different folder while the current one has staged,
    // not-yet-exported edits would silently strand them in memory, so ask
    // first. Reopening the same folder, or one with nothing pending, needs
    // no confirmation.
    if (currentModPath && !samePath(currentModPath, path) &&
        await window.pywebview.api.has_pending_changes(currentModPath)) {
      const proceed = await confirmDialog(
        'This mod has unsaved changes that haven\'t been exported.\n\n' +
        'Opening a different mod folder will discard them. Continue?');
      if (!proceed) return;
      await window.pywebview.api.discard_changes(currentModPath);
    }

    await loadModAt(path);
  } catch (e) {
    showLoading(false);
    await alertDialog('Unexpected error:\n\n' + e);
  } finally {
    btn.disabled = false;
  }
}

// Re-fetches and re-renders the currently open mod folder — used after a
// toggle add/edit/delete writes to its ini file, so the panel and 3D view
// pick up the change via the exact same path a fresh open would take.
export async function reloadCurrentMod() {
  if (!currentModPath) return;
  showLoading(true, 'Reloading mod…');
  try {
    await loadModAt(currentModPath);
  } catch (e) {
    showLoading(false);
    await alertDialog('Unexpected error while reloading:\n\n' + e);
  }
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
    // Refreshes the panel/badges from the now-partly-or-fully-written disk
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

$('open-btn').addEventListener('click', openMod);
$('export-btn').addEventListener('click', exportChanges);
$('wire-btn').addEventListener('click', toggleWireframe);
$('grid-btn').addEventListener('click', toggleGrid);
$('shading-btn').addEventListener('click', toggleSmoothShading);
$('texture-btn').addEventListener('click', toggleTextures);
$('reset-state-btn').addEventListener('click', resetMeshState);
$('trackball-btn').addEventListener('click', toggleTrackballGizmo);
$('camera-reset-view-btn').addEventListener('click', () => resetView(activeMeshes));
$('camera-flip-btn').addEventListener('click', () => rotateModelQuarterTurn(activeMeshes));
$('camera-flip-horizontal-btn').addEventListener('click', () => rotateModelHorizontalQuarterTurn(activeMeshes));
initSelection();

// Collapses a panel's body when its header is clicked.
function initPanelCollapse(panel, contentId) {
  const hdr = panel.querySelector('.panel-hdr');
  const chevron = hdr.querySelector('.group-toggle');
  const content = $(contentId);
  hdr.addEventListener('click', (e) => {
    if (e.target.closest('.icon-btn')) return;
    chevron.classList.toggle('collapsed');
    content.classList.toggle('collapsed');
  });
}
initPanelCollapse($('sidebar'), 'mesh-list');
initPanelCollapse($('camera-panel'), 'camera-buttons');
initPanelCollapse($('tool-panel'), 'tool-buttons');
initPanelCollapse($('present-panel'), 'present-list');
initPanelCollapse($('toggle-panel'), 'toggle-list');
initPanelCollapse($('menu-panel'), 'menu-list');

// Exposed for automated smoke tests and for poking at the app from the
// devtools console; the UI itself always goes through the listeners above.
window.modViewer = { displayMeshPayload, openMod, reloadCurrentMod, exportChanges, activeMeshes };
