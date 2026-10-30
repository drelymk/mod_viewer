// Export a detached visible-model snapshot without entering the INI edit session.
import { viewerState } from './state.js';
import { activeMeshes } from '../mesh/mesh-state.js';
import { getTexturePreviewSource, isDDSUri } from '../mesh/mesh-factory.js';
import { alertDialog } from '../ui/dialogs.js';
import { t } from '../i18n/index.js';

let exporting = false;
const $ = (id) => document.getElementById(id);

export function syncModelExportAvailability() {
  const available =
    !exporting &&
    !viewerState.modTransitionInFlight &&
    viewerState.currentSource?.kind === 'mod' &&
    !viewerState.currentSource.readOnly &&
    activeMeshes.length > 0;
  const choice = $('export-glb-choice');
  const trigger = $('export-menu-btn');
  if (choice) choice.disabled = !available;
  if (trigger) trigger.disabled = exporting || (!available && $('export-btn').disabled);
  const edits = $('export-edits-choice');
  if (edits) edits.disabled = exporting || $('export-btn').disabled;
}

async function bytesAt(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error('Texture download failed.');
  return response.arrayBuffer();
}

function base64Buffer(buffer) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(new Blob([buffer]));
  });
}

export async function exportModelGlb() {
  if (exporting || $('export-glb-choice').disabled) return;
  const path = viewerState.currentModPath;
  const source = viewerState.currentSource;
  const isCurrent = () => viewerState.currentSource === source && !viewerState.modTransitionInFlight;
  exporting = true;
  syncModelExportAvailability();
  const status = $('status-text');
  const previousStatus = status.textContent;
  $('loading').classList.add('show');
  status.textContent = t('model.exportingGlb');
  let snapshot;
  try {
    const { snapshotModel, encodeModelGlb } = await import('../export/model-glb.js');
    if (!isCurrent()) return;
    snapshot = snapshotModel(activeMeshes, getTexturePreviewSource);
    if (!snapshot.scene.children.length) throw new Error(t('model.exportNoMeshes'));
    const result = await encodeModelGlb(snapshot, {
      loadBytes: bytesAt,
      loadImage: async (url) => {
        if (isDDSUri(url)) {
          const { exportDDSCanvas } = await import('../textures/dds-preview.js');
          return exportDDSCanvas(url, isCurrent);
        }
        const image = new Image();
        image.src = url;
        await image.decode();
        return image;
      },
    });
    if (!isCurrent()) return;
    const encoded = await base64Buffer(result.buffer);
    if (!isCurrent()) return;
    const saved = await window.pywebview.api.export_model_glb(path, encoded);
    if (saved.error) throw new Error(saved.error);
    if (!saved.cancelled) {
      await alertDialog(
        t('model.exportGlbComplete', { name: saved.name, count: result.meshCount }) +
          (result.warnings.length ? '\n\n' + t('model.exportGlbWarnings', { detail: result.warnings.join('\n') }) : ''),
      );
    }
  } catch (error) {
    await alertDialog(t('errors.export', { detail: error.message || String(error) }));
  } finally {
    snapshot?.dispose();
    exporting = false;
    if (viewerState.currentSource === source) {
      $('loading').classList.remove('show');
      status.textContent = previousStatus;
    }
    syncModelExportAvailability();
  }
}

export function initExportMenu() {
  const trigger = $('export-menu-btn');
  const menu = $('export-menu');
  const close = () => {
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
  };
  trigger.addEventListener('click', (event) => {
    event.stopPropagation();
    menu.hidden = !menu.hidden;
    trigger.setAttribute('aria-expanded', String(!menu.hidden));
  });
  $('export-glb-choice').addEventListener('click', () => {
    close();
    void exportModelGlb();
  });
  $('export-edits-choice').addEventListener('click', () => {
    close();
    $('export-btn').click();
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#export-control')) close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
  });
  window.addEventListener('mod-viewer-pending-state-changed', syncModelExportAvailability);
  syncModelExportAvailability();
}
