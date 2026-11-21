// View-only MESHES filter: name query plus visibility mode; never changes model state.
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { meshUnderlyingVisible } from '../mesh/mesh-state.js';
import { meshForPanelRow } from './mesh-panel-rows.js';

const $ = (id) => document.getElementById(id);

let query = '';
let mode = 'all';
let initialized = false;
let frame = 0;
let lastCounts = null;

function isMeshPanelFilterActive() {
  return !!query || mode !== 'all';
}

function scheduleMeshPanelFilter() {
  if (frame) return;
  frame = requestAnimationFrame(() => applyFilter());
}

export function resetMeshPanelFilter() {
  query = '';
  mode = 'all';
  const input = $('mesh-search');
  if (input) input.value = '';
  syncModeButtons();
  applyFilter();
}

export function initMeshPanelFilter() {
  if (initialized) return;
  const input = $('mesh-search');
  const list = $('mesh-list');
  if (!input || !list) return;
  initialized = true;

  const onQuery = () => {
    query = input.value.trim().toLowerCase();
    applyFilter();
  };
  input.addEventListener('input', onQuery);
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !input.value) return;
    event.preventDefault();
    event.stopPropagation();
    input.value = '';
    query = '';
    applyFilter();
  });

  document.querySelectorAll('#mesh-visibility-filter [data-mesh-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      mode = button.dataset.meshFilter;
      syncModeButtons();
      applyFilter();
    });
  });

  // Rebuilt, renamed or recounted rows re-filter. Only structure and text are observed:
  // applyFilter changes classes, so attributes stay unobserved and cannot loop.
  new MutationObserver(scheduleMeshPanelFilter).observe(list, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  window.addEventListener('mod-viewer-mesh-state-changed', () => {
    if (mode !== 'all') scheduleMeshPanelFilter();
  });
  window.addEventListener(LANGUAGE_CHANGED, syncSummary);
}

function syncModeButtons() {
  document.querySelectorAll('#mesh-visibility-filter [data-mesh-filter]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.meshFilter === mode));
  });
}

function syncSummary() {
  const element = $('mesh-filter-count');
  if (!element) return;
  const { shown = 0, total = 0 } = lastCounts || {};
  if (total === 0) element.textContent = '';
  else if (isMeshPanelFilterActive()) element.textContent = t('mesh.filterCount', { shown, total });
  else element.textContent = t('mesh.totalCount', { total });
}

function applyFilter() {
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  const list = $('mesh-list');
  if (!list) return;
  const active = isMeshPanelFilterActive();
  list.classList.toggle('filter-active', active);
  list.classList.toggle('searching', !!query);
  list.querySelectorAll('.filtered-out').forEach((node) => node.classList.remove('filtered-out'));
  const total = list.querySelectorAll('.draw-item-wrap').length;
  const shown = active ? walk(list, false) : total;
  lastCounts = { shown, total };
  syncSummary();
  const empty = $('mesh-filter-empty');
  if (empty) empty.hidden = !(active && total > 0 && shown === 0);
}

function textMatches(text) {
  return !!query && text.toLowerCase().includes(query);
}

function rowPasses(row, inherited) {
  if (!row) return false;
  const textOk =
    !query ||
    inherited ||
    !!row.querySelector('.mesh-name-input') ||
    textMatches(row.querySelector('.mesh-name')?.textContent || '');
  if (!textOk) return false;
  if (mode === 'all') return true;
  const mesh = meshForPanelRow(row);
  return !!mesh && meshUnderlyingVisible(mesh) === (mode === 'visible');
}

function walk(container, inherited) {
  let count = 0;
  for (const child of container.children) {
    if (child.classList.contains('draw-item-wrap')) {
      const ok = rowPasses(child.querySelector(':scope > .draw-item'), inherited);
      child.classList.toggle('filtered-out', !ok);
      if (ok) count += 1;
    } else if (child.classList.contains('mesh-src-hdr') || child.classList.contains('group-hdr')) {
      const items = child.nextElementSibling;
      const name = child.querySelector(':scope > .group-name')?.textContent || '';
      const shown = items ? walk(items, inherited || textMatches(name)) : 0;
      const hidden = shown === 0;
      child.classList.toggle('filtered-out', hidden);
      items?.classList.toggle('filtered-out', hidden);
      count += shown;
    }
  }
  return count;
}
