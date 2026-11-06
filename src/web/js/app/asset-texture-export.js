// Explicit persistence for texture bindings on the currently open Asset.
import { viewerState } from './state.js';
import { bridgeReady } from './bridge.js';
import { alertDialog } from '../ui/dialogs.js';
import { t } from '../i18n/index.js';

let context = null;
let saving = false;

export function initializeAssetTextureExport(token) {
  context =
    viewerState.currentSource?.kind === 'asset' ? { source: viewerState.currentSource, token, pending: {} } : null;
}

export function recordAssetTextureAssignment(component, role, value) {
  if (!context || context.source !== viewerState.currentSource) return;
  (context.pending[component] ||= {})[role] = value;
  notify();
}

export function assetTextureExportState() {
  const active = viewerState.currentSource?.kind === 'asset' && context?.source === viewerState.currentSource;
  const pending = active && Object.keys(context.pending).length > 0;
  return { pending, saving, available: !!(pending && context.token && !saving && !viewerState.modTransitionInFlight) };
}

function notify() {
  window.dispatchEvent(new CustomEvent('mod-viewer-pending-state-changed'));
}

export async function exportAssetTextures() {
  if (!assetTextureExportState().available) return;
  const job = context;
  const textures = structuredClone(job.pending);
  saving = true;
  notify();
  try {
    const api = await bridgeReady();
    if (context !== job || viewerState.currentSource !== job.source || viewerState.modTransitionInFlight) return;
    const result = await api.export_asset_textures(job.source.path, job.token, textures);
    if (context !== job || viewerState.currentSource !== job.source) return;
    if (!result || result.error) throw new Error(result?.error || 'Asset texture assignments were not saved.');
    for (const [component, roles] of Object.entries(textures)) {
      for (const [role, key] of Object.entries(roles)) {
        if (job.pending[component]?.[role] === key) delete job.pending[component][role];
      }
      if (!Object.keys(job.pending[component] || {}).length) delete job.pending[component];
    }
  } catch (error) {
    if (context === job && viewerState.currentSource === job.source) {
      await alertDialog(t('errors.export', { detail: error.message || String(error) }));
    }
  } finally {
    saving = false;
    notify();
  }
}
