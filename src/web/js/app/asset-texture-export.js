// Explicit persistence for texture bindings on the currently open Asset.
import { viewerState } from './state.js';
import { activeMeshes, TEXTURE_ROLE_FIELDS, updateAssetTextureDefaults } from '../mesh/mesh-state.js';
import { bridgeReady } from './bridge.js';
import { alertDialog } from '../ui/dialogs.js';
import { t } from '../i18n/index.js';

let context = null;
let saving = false;

function snapshot() {
  return Object.fromEntries(
    activeMeshes
      .filter((mesh) => !mesh.userData.assetFill)
      .map((mesh) => {
        const data = mesh.userData;
        const option = (data.texturePool || []).find((item) => item.tex_key === data.texKey);
        const ownsRun =
          data.manualTexOverride !== undefined || (data.automaticTextureBoundary && !data.textureHighlightDisabled);
        const values = Object.fromEntries(
          TEXTURE_ROLE_FIELDS.map(({ role, current }) => [role, data[current] || null]),
        );
        const edited = Object.fromEntries(
          TEXTURE_ROLE_FIELDS.filter(({ role }) =>
            role === 'diffuse' ? data.manualTexOverride !== undefined : ownsRun && option?.[`${role}_manual`] === true,
          ).map(({ role }) => [role, true]),
        );
        return [data.semanticKey, { values, edited }];
      }),
  );
}

function changesSince(current, saved) {
  const changes = {};
  for (const [part, state] of Object.entries(current)) {
    for (const [role, key] of Object.entries(state.values)) {
      if (state.edited[role] && (key !== saved[part]?.values[role] || !saved[part]?.edited[role])) {
        (changes[part] ||= {})[role] = key;
      }
    }
  }
  return changes;
}

export function initializeAssetTextureExport(token, sections = {}) {
  context =
    viewerState.currentSource?.kind === 'asset'
      ? { source: viewerState.currentSource, token, saved: snapshot(), sections, removed: new Set() }
      : null;
}

export function assetTextureExportState() {
  const active = viewerState.currentSource?.kind === 'asset' && context?.source === viewerState.currentSource;
  const pending =
    active && (context.removed.size > 0 || Object.keys(changesSince(snapshot(), context.saved)).length > 0);
  return { pending, saving, available: !!(pending && context.token && !saving && !viewerState.modTransitionInFlight) };
}

function notify() {
  window.dispatchEvent(new CustomEvent('mod-viewer-pending-state-changed'));
}

export function stageAssetSectionRemoval(source) {
  if (!context?.token || saving || viewerState.modTransitionInFlight || context.source !== viewerState.currentSource)
    return null;
  const section = context.sections[source];
  if (!section || context.removed.has(source)) return null;
  const parts = new Set(section.parts);
  for (const [name, candidate] of Object.entries(context.sections)) {
    if (candidate.parts.every((part) => parts.has(part))) context.removed.add(name);
  }
  notify();
  return parts;
}

export async function exportAssetTextures() {
  if (!assetTextureExportState().available) return;
  const job = context;
  const sent = snapshot();
  const textures = changesSince(sent, job.saved);
  const removed = [...job.removed];
  saving = true;
  notify();
  try {
    const api = await bridgeReady();
    if (context !== job || viewerState.currentSource !== job.source || viewerState.modTransitionInFlight) return;
    const result = removed.length
      ? await api.export_asset_textures(job.source.path, job.token, textures, removed)
      : await api.export_asset_textures(job.source.path, job.token, textures);
    if (context !== job || viewerState.currentSource !== job.source) return;
    if (!result || result.error) throw new Error(result?.error || 'Asset texture assignments were not saved.');
    for (const [part, roles] of Object.entries(textures)) {
      for (const [role, key] of Object.entries(roles)) {
        job.saved[part].values[role] = key;
        job.saved[part].edited[role] = sent[part].edited[role];
      }
    }
    updateAssetTextureDefaults(textures);
    for (const source of result.removed || []) {
      job.removed.delete(source);
      delete job.sections[source];
    }
    if (result.removed?.length) window.dispatchEvent(new CustomEvent('mod-viewer-asset-sections-exported'));
    if (result.failed?.length)
      throw new Error(result.failed.map((item) => `${item.section}: ${item.error}`).join('\n'));
  } catch (error) {
    if (context === job && viewerState.currentSource === job.source) {
      await alertDialog(t('errors.export', { detail: error.message || String(error) }));
    }
  } finally {
    saving = false;
    notify();
  }
}
