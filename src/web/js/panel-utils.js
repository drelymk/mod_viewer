// Exact shared source grouping/collapse behavior used by the three panels.

import { createIcon } from './ui-icons.js';

let sourceSectionId = 0;

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
  chevron.setAttribute('aria-label', `Collapse ${source}`);
  chevron.appendChild(createIcon('chevron-down'));
  const name = document.createElement('span');
  name.className = 'group-name';
  name.textContent = source;
  header.append(chevron, name);

  const items = document.createElement('div');
  items.className = itemsClass;
  items.id = `source-section-${++sourceSectionId}`;
  chevron.setAttribute('aria-controls', items.id);
  header.addEventListener('click', () => {
    const collapsed = !items.classList.contains('collapsed');
    chevron.classList.toggle('collapsed', collapsed);
    chevron.setAttribute('aria-expanded', String(!collapsed));
    chevron.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${source}`);
    items.classList.toggle('collapsed', collapsed);
  });
  container.append(header, items);
  return items;
}
