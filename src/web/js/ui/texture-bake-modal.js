// Read-only texture coverage result modal.

import { bindModalDismiss, setModalError } from './modal-shell.js';
import {
  analyzeMeshTextureBake, cancelTextureBakeAnalysis, formatBakeAnalysis,
} from '../mesh/texture-bake-analysis.js';

const $ = id => document.getElementById(id);
const backdrop = $('texture-bake-modal-backdrop');
const body = $('texture-bake-body');
const error = $('texture-bake-error');

function closeTextureBakeModal() {
  cancelTextureBakeAnalysis();
  backdrop?.classList.remove('show');
}

function setLoading(loading) {
  if (!body) return;
  body.replaceChildren();
  if (loading) {
    const message = document.createElement('div');
    message.className = 'texture-bake-loading';
    message.textContent = 'Analyzing texture coverage…';
    body.appendChild(message);
  }
}

function renderResult(result, displayName) {
  setModalError(error, '');
  const formatted = formatBakeAnalysis(result, displayName);
  if (!formatted) return false;
  if (formatted.kind === 'error') {
    setModalError(error, formatted.summary);
    return true;
  }
  body.replaceChildren();
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

/** Open the read-only analysis modal and resolve after the request settles. */
export async function openTextureBakeModal(mesh, { isCurrent } = {}) {
  if (!backdrop || !body) return null;
  setModalError(error, '');
  setLoading(true);
  backdrop.classList.add('show');
  let result;
  try {
    result = await analyzeMeshTextureBake(mesh, { isCurrent });
  } catch (requestError) {
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
  return result;
}

bindModalDismiss({
  backdrop,
  close: closeTextureBakeModal,
  buttons: [$('texture-bake-close'), $('texture-bake-close-x')].filter(Boolean),
});

window.addEventListener('mod-viewer-mesh-selected', () => {
  if (backdrop?.classList.contains('show')) closeTextureBakeModal();
});

export { closeTextureBakeModal };
