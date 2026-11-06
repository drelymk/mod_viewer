// Explicit persistence for texture bindings on the currently open Asset.
import { viewerState } from './state.js';
import { bridgeReady } from './bridge.js';
import { alertDialog } from '../ui/dialogs.js';
import { t } from '../i18n/index.js';

let context = null;
let saving = false;

export function initializeAssetTextureExport(token) {
  context =
    viewerState.currentSource?.kind === 'asset'
      ? { source: viewerState.currentSource, token, pending: {}, saved: {} }
      : null;
}

function pruneSavedAssignments(job) {
  for (const [component, roles] of Object.entries(job.pending)) {
    for (const [role, key] of Object.entries(roles)) {
      if (key === job.saved[component]?.[role]) delete roles[role];
    }
    if (!Object.keys(roles).length) delete job.pending[component];
  }
}

export function recordAssetTextureAssignment(component, role, value, previousValue) {
  if (!context || context.source !== viewerState.currentSource) return;
  const saved = (context.saved[component] ||= {});
  // An initially unavailable map is not proof of a previously authored clear.
  if (!Object.hasOwn(saved, role)) saved[role] = previousValue ?? undefined;
  (context.pending[component] ||= {})[role] = value;
  if (!saving) pruneSavedAssignments(context);
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
      Object.assign(job.saved[component], roles);
    }
  } catch (error) {
    if (context === job && viewerState.currentSource === job.source) {
      await alertDialog(t('errors.export', { detail: error.message || String(error) }));
    }
  } finally {
    saving = false;
    if (context === job) pruneSavedAssignments(job);
    notify();
  }
}
