// Exact shared source grouping/collapse behavior used by the three panels.

import { createIcon } from './ui-icons.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

let sourceSectionId = 0;
const sourceSections = new Set();

function syncSourceSection(section) {
  const collapsed = section.items.classList.contains('collapsed');
  section.chevron.setAttribute('aria-label', t(
    collapsed ? 'panel.expandSource' : 'panel.collapseSource', {
      source: section.source,
    }));
}

function syncSourceSections() {
  for (const section of sourceSections) {
    if (!section.header.isConnected) {
      sourceSections.delete(section);
      continue;
    }
    syncSourceSection(section);
  }
}

window.addEventListener(LANGUAGE_CHANGED, syncSourceSections);

export function groupKeysBySource(records, keys = Object.keys(records || {})) {
  const grouped = {};
  for (const key of keys) {
    const source = records[key]?.source || '';
    (grouped[source] = grouped[source] || []).push(key);
  }
  return grouped;
}

export function usesSourceSections(grouped) {
  const sources = Object.keys(grouped);
  return sources.length > 1 || (sources.length === 1 && sources[0] !== '');
}

export function buildSourceSection(source, container, {
  headerClass = 'toggle-src-hdr',
  itemsClass = 'toggle-src-items',
} = {}) {
  const header = document.createElement('div');
  header.className = headerClass;
  const chevron = document.createElement('button');
  chevron.type = 'button';
  chevron.className = 'group-toggle';
  chevron.setAttribute('aria-expanded', 'true');
  chevron.appendChild(createIcon('chevron-down'));
  const name = document.createElement('span');
  name.className = 'group-name';
  name.textContent = source;
  header.append(chevron, name);

  const items = document.createElement('div');
  items.className = itemsClass;
  items.id = `source-section-${++sourceSectionId}`;
  const section = {header, items, chevron, source};
  syncSourceSection(section);
  chevron.setAttribute('aria-controls', items.id);
  header.addEventListener('click', () => {
    const collapsed = !items.classList.contains('collapsed');
    chevron.classList.toggle('collapsed', collapsed);
    chevron.setAttribute('aria-expanded', String(!collapsed));
    items.classList.toggle('collapsed', collapsed);
    syncSourceSection(section);
  });
  sourceSections.add(section);
  container.append(header, items);
  return items;
}

// Collapse a panel body while preserving the shared local preference.
export function initPanelCollapse(panel, contentId) {
  const hdr = panel.querySelector('.panel-hdr');
  const chevron = hdr.querySelector('.group-toggle');
  const content = document.getElementById(contentId);
  const storageKey = `mod-viewer.panel.${panel.id}.collapsed`;
  const setCollapsed = (collapsed, persist = true) => {
    chevron.classList.toggle('collapsed', collapsed);
    content.classList.toggle('collapsed', collapsed);
    chevron.setAttribute('aria-expanded', String(!collapsed));
    const name = panel.querySelector('h3')?.textContent || t('panel.default');
    chevron.setAttribute('aria-label', t(
      collapsed ? 'panel.expandPanel' : 'panel.collapsePanel', {name}));
    if (persist) {
      try { localStorage.setItem(storageKey, String(collapsed)); } catch (_) { /* private mode */ }
    }
  };
  let initiallyCollapsed = false;
  try { initiallyCollapsed = localStorage.getItem(storageKey) === 'true'; } catch (_) { /* private mode */ }
  chevron.setAttribute('aria-controls', contentId);
  setCollapsed(initiallyCollapsed, false);
  const toggle = (event) => {
    if (event?.target?.closest?.('.icon-btn, .panel-hdr-actions, .panel-actions, .panel-action-menu')) return;
    event?.stopPropagation?.();
    setCollapsed(!content.classList.contains('collapsed'));
  };
  hdr.addEventListener('click', event => {
    if (event.target.closest('.icon-btn, .group-toggle, .panel-hdr-actions, .panel-actions, .panel-action-menu')) return;
    setCollapsed(!content.classList.contains('collapsed'));
  });
  chevron.addEventListener('click', toggle);
  window.addEventListener(LANGUAGE_CHANGED, () =>
    setCollapsed(content.classList.contains('collapsed'), false));
}
