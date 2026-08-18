// Exact shared source grouping/collapse behavior used by the three panels.

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
  const chevron = document.createElement('span');
  chevron.className = 'group-toggle';
  chevron.textContent = '▼';
  const name = document.createElement('span');
  name.className = 'group-name';
  name.textContent = source;
  header.append(chevron, name);

  const items = document.createElement('div');
  items.className = itemsClass;
  header.addEventListener('click', () => {
    chevron.classList.toggle('collapsed');
    items.classList.toggle('collapsed');
  });
  container.append(header, items);
  return items;
}
