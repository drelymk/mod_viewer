// Coverage preflight and the destructive Safe Bake confirmation.

import { bindModalDismiss, setModalError } from './modal-shell.js';
import {
  analyzeMeshTextureBake, buildTextureUsageSnapshot,
  cancelTextureBakeAnalysis, formatBakeAnalysis,
} from '../mesh/texture-bake-analysis.js';
import { activeMeshes } from '../mesh/mesh-state.js';
import {
  getMeshColorAdjustment, resetMeshColorAdjustment,
} from '../mesh/mesh-color-state.js';
import { isNeutralColorAdjustment } from '../mesh/color-adjustment.js';
import { reloadTextures } from '../mesh/mesh-factory.js';
import { notifyMeshStateChanged } from '../mesh/mesh-state-events.js';
import { viewerState, samePath } from '../app/state.js';

const $ = id => document.getElementById(id);
const backdrop = $('texture-bake-modal-backdrop');
const body = $('texture-bake-body');
const error = $('texture-bake-error');
const bakeButton = $('texture-bake-confirm');

let pendingBake = null;
let baking = false;

function displayNameForSemanticKey(semanticKey) {
  const mesh = activeMeshes.find(item =>
    item.userData?.semanticKey === semanticKey);
  return mesh?.userData?.displayName || semanticKey;
}

function closeTextureBakeModal() {
  // The modal is the guard around the destructive request. Keep it visible
  // until the write settles so the user cannot edit another color state while
  // the backend is committing the captured adjustment.
  if (baking) return;
  cancelTextureBakeAnalysis();
  pendingBake = null;
  backdrop?.classList.remove('show');
}

function setLoading(message = 'Analyzing texture coverage…') {
  if (!body) return;
  body.replaceChildren();
  const node = document.createElement('div');
  node.className = 'texture-bake-loading';
  node.textContent = message;
  body.appendChild(node);
}

function setBakeAction({visible = false, disabled = true, label = 'Bake Color'} = {}) {
  if (!bakeButton) return;
  bakeButton.hidden = !visible;
  bakeButton.disabled = disabled;
  bakeButton.textContent = label;
}

function renderResult(result, displayName = displayNameForSemanticKey) {
  setModalError(error, '');
  body.replaceChildren();
  const formatted = formatBakeAnalysis(result, displayName);
  if (!formatted) return false;
  setBakeAction();
  if (formatted.kind === 'error' || formatted.kind === 'unsupported') {
    setModalError(error, formatted.summary);
    return true;
  }
  const state = document.createElement('div');
  state.className = `texture-bake-state texture-bake-state-${formatted.kind}`;
  state.textContent = formatted.title;
  body.appendChild(state);
  const summary = document.createElement('p');
  summary.className = 'texture-bake-summary';
  summary.textContent = formatted.summary;
  body.appendChild(summary);

  const rows = document.createElement('dl');
  rows.className = 'texture-bake-details';
  formatted.rows.forEach(([label, value]) => {
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    description.textContent = value;
    rows.append(term, description);
  });
  body.appendChild(rows);
  if (formatted.warning) {
    const warning = document.createElement('p');
    warning.className = `texture-bake-warning texture-bake-warning-${formatted.kind}`;
    warning.textContent = formatted.warning;
    body.appendChild(warning);
  }
  return true;
}

function renderBakeSuccess(result) {
  setModalError(error, '');
  body.replaceChildren();
  const state = document.createElement('div');
  state.className = 'texture-bake-state';
  state.textContent = 'TEXTURE BAKED';
  body.appendChild(state);
  const summary = document.createElement('p');
  summary.className = 'texture-bake-summary';
  summary.textContent = `${result.texture?.file || 'Texture'} was updated. `
    + 'The viewer-only Color adjustment was reset.';
  body.appendChild(summary);
  const rows = document.createElement('dl');
  rows.className = 'texture-bake-details';
  [
    ['Top-level units changed', result.patched?.mip0_units || 0],
    ['Shared units preserved', result.patched?.shared_units_preserved || 0],
    ['Backup', result.backup?.file || 'Created'],
  ].forEach(([label, value]) => {
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    description.textContent = String(value);
    rows.append(term, description);
  });
  body.appendChild(rows);
  setBakeAction();
}

function bakeRequestFor(mesh) {
  const adjustment = getMeshColorAdjustment(mesh);
  return {
    semanticKey: mesh.userData?.semanticKey,
    metadataKey: mesh.userData?.metadataKey,
    texKey: mesh.userData?.texKey || null,
    textureUsage: buildTextureUsageSnapshot(),
    adjustment: {
      hue: adjustment.hue,
      saturation: adjustment.saturation,
      brightness: adjustment.brightness,
      contrast: adjustment.contrast,
      red: adjustment.red,
      green: adjustment.green,
      blue: adjustment.blue,
      tint: adjustment.tint,
      tint_strength: adjustment.tintStrength,
    },
  };
}

function synchronizeCommittedBake(mesh, result) {
  const sameLoadedMod = activeMeshes.includes(mesh)
    && viewerState.currentSource?.kind === 'mod'
    && samePath(mesh.userData?.modPath, viewerState.currentModPath);
  if (!sameLoadedMod) return false;

  resetMeshColorAdjustment(mesh, {persist: false, render: false});
  if (result.warning === 'color_state_reset_failed') {
    // Retry through the normal persistence boundary; texture_bake.py never
    // edits viewer metadata directly.
    resetMeshColorAdjustment(mesh, {persist: true, render: false});
  }
  reloadTextures(result.affected_tex_keys || [result.tex_key]);
  notifyMeshStateChanged([mesh]);
  return true;
}

async function runBake(mesh, isCurrent) {
  const request = bakeRequestFor(mesh);
  const api = window.pywebview?.api?.bake_mesh_texture_color;
  if (typeof api !== 'function') {
    setModalError(error, 'Texture baking is unavailable.');
    setBakeAction();
    return null;
  }
  baking = true;
  setBakeAction({visible: true, disabled: true, label: 'Baking…'});
  setLoading(`Baking color into ${request.texKey || 'the texture'}…`);
  let result;
  try {
    result = await api(
      mesh.userData.modPath, request.semanticKey, request.metadataKey,
      request.texKey, request.textureUsage, request.adjustment);
  } catch (_requestError) {
    result = {status: 'error', error: 'Texture baking failed.'};
  }
  baking = false;
  if (result?.status === 'ok') synchronizeCommittedBake(mesh, result);
  if (typeof isCurrent === 'function' && !isCurrent()) {
    closeTextureBakeModal();
    return result;
  }
  if (result?.status !== 'ok') {
    setModalError(error, result?.error || 'Texture baking failed.');
    // A failed destructive request must go through a fresh preflight. The
    // captured job has been consumed, so an enabled action here would be
    // misleading and inert; the user can close and start analysis again.
    setBakeAction();
    return result;
  }
  renderBakeSuccess(result);
  return result;
}

/** Open the bake preflight modal and wait for analysis to settle. */
export async function openTextureBakeModal(mesh, { isCurrent } = {}) {
  if (!backdrop || !body) return null;
  setModalError(error, '');
  setBakeAction();
  setLoading();
  backdrop.classList.add('show');
  let result;
  try {
    result = await analyzeMeshTextureBake(mesh, {isCurrent});
  } catch (_requestError) {
    result = {
      status: 'error',
      error: 'Texture coverage could not be analyzed safely.',
    };
  }
  if (result === null) {
    closeTextureBakeModal();
    return null;
  }
  renderResult(result);
  if (result.status === 'ok' && (result.safety === 'safe'
      || result.safety === 'shared')) {
    if (!isNeutralColorAdjustment(getMeshColorAdjustment(mesh))) {
      pendingBake = {mesh, isCurrent};
      setBakeAction({
        visible: true, disabled: false,
        label: result.safety === 'shared'
          ? 'Bake Unique Areas Only' : 'Bake Color',
      });
    }
  }
  return result;
}

bakeButton?.addEventListener('click', async () => {
  if (!pendingBake || baking) return;
  const job = pendingBake;
  pendingBake = null;
  await runBake(job.mesh, job.isCurrent);
});

bindModalDismiss({
  backdrop,
  close: closeTextureBakeModal,
  buttons: [$('texture-bake-close'), $('texture-bake-close-x')].filter(Boolean),
});

window.addEventListener('mod-viewer-mesh-selected', () => {
  if (backdrop?.classList.contains('show') && !baking) {
    closeTextureBakeModal();
  }
});

export { closeTextureBakeModal };
