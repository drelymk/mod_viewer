// The MESHES panel (left): in multi-ini mods, one collapsible section per
// source ini (mirroring the Toggle panel); within each, one collapsible group
// per component, one checkbox per draw call within it.

import { buildMesh } from './mesh-factory.js';
import { activeMeshes, addMesh, applyMeshVisibility, registerGroup } from './visibility.js';
import { selectMesh } from './selection.js';
import { RESERVED_KEYS } from './payload.js';

/** Bucket mesh names by their ini "source" tag (see app/mod_loader.py's
 * _ini_scope). Single-ini mods carry no tag at all — everything lands in the
 * '' bucket, which buildMeshPanel renders flat with no per-ini header,
 * exactly like the Toggle panel. */
function groupBySource(payload) {
  const grouped = {};
  for (const name of Object.keys(payload)) {
    if (RESERVED_KEYS.includes(name)) continue;
    if (payload[name]?.error) continue;
    const src = payload[name].source || '';
    (grouped[src] = grouped[src] || []).push(name);
  }
  return grouped;
}

/** Group mesh names by their clean, never-disambiguated component name
 * (see core/mesh_builder.py's `component` field) — falls back to parsing the
 * dict key itself (stripping a trailing "-N" draw index) for the rare case
 * a payload entry lacks it. Using the explicit field (rather than the key)
 * means a cross-ini name collision's internal "_2" uniqueness suffix (see
 * core/ini_parser.py's build_draw_groups) never leaks into the displayed
 * group header — the per-source section above it already disambiguates. */
function groupByComponent(names, payload) {
  const grouped = {};
  for (const name of names) {
    const explicit = payload[name]?.component;
    let key = explicit;
    if (!key) {
      const m = name.match(/^(.+)-\d+$/);
      key = m ? m[1] : name;
    }
    (grouped[key] = grouped[key] || []).push(name);
  }
  return grouped;
}

function buildSourceSection(source, container) {
  const hdr = document.createElement('div');
  hdr.className = 'mesh-src-hdr';

  const chevron = document.createElement('span');
  chevron.className = 'group-toggle';
  chevron.textContent = '▼';

  const nameSpan = document.createElement('span');
  nameSpan.className = 'group-name';
  nameSpan.textContent = source;

  hdr.append(chevron, nameSpan);

  const itemsWrap = document.createElement('div');
  itemsWrap.className = 'mesh-src-items';

  hdr.addEventListener('click', () => {
    chevron.classList.toggle('collapsed');
    itemsWrap.classList.toggle('collapsed');
  });

  container.append(hdr, itemsWrap);
  return itemsWrap;
}

function buildGroupHeader(groupName, itemsWrap) {
  const hdr = document.createElement('div');
  hdr.className = 'group-hdr';

  const chevron = document.createElement('span');
  chevron.className = 'group-toggle';
  chevron.textContent = '▼';

  const masterCb = document.createElement('input');
  masterCb.type = 'checkbox';
  masterCb.checked = true;

  const nameSpan = document.createElement('span');
  nameSpan.className = 'group-name';
  nameSpan.textContent = groupName;

  hdr.append(chevron, masterCb, nameSpan);

  // Expand/collapse the item list without touching mesh visibility.
  hdr.addEventListener('click', (e) => {
    if (e.target === masterCb) return;
    chevron.classList.toggle('collapsed');
    itemsWrap.classList.toggle('collapsed');
  });

  return { hdr, masterCb };
}

/** "count, start, base" from the ini's own drawindexed line — falls back to
 * the old bare "#N" numbering for the rare draw with no such line at all
 * (whole index buffer read unconditionally; see mesh_builder.build_mesh_payload). */
function buildDrawRow(name, groupName, entry, mesh, itemCbs, masterCb) {
  const row = document.createElement('div');
  row.className = 'draw-item';

  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = true;
  cb.addEventListener('change', () => {
    mesh.userData.manualVisible = cb.checked;
    applyMeshVisibility(mesh);
    const any = itemCbs.some(c => c.checked);
    const all = itemCbs.every(c => c.checked);
    masterCb.indeterminate = any && !all;
    masterCb.checked = all;
  });
  itemCbs.push(cb);

  const label = entry.drawindexed
    ? entry.drawindexed.join(', ')
    : '#' + name.slice(groupName.length + 1);
  row.append(cb, document.createTextNode(label));

  mesh.userData.row = row;
  row.addEventListener('click', (e) => {
    if (e.target === cb) return; // the checkbox only ever toggles visibility
    selectMesh(mesh);
  });

  return row;
}

/** Build the panel and add every mesh in the payload to the scene. */
export function buildMeshPanel(payload) {
  const list = document.getElementById('mesh-list');
  list.innerHTML = '';

  const bySource = groupBySource(payload);
  const sources = Object.keys(bySource);
  const multiSource = sources.length > 1 || (sources.length === 1 && sources[0] !== '');

  for (const src of sources) {
    const container = (multiSource && src) ? buildSourceSection(src, list) : list;

    for (const [groupName, names] of Object.entries(groupByComponent(bySource[src], payload))) {
      const itemsWrap = document.createElement('div');
      itemsWrap.className = 'group-items';

      const { hdr, masterCb } = buildGroupHeader(groupName, itemsWrap);
      container.append(hdr, itemsWrap);

      const itemCbs = [], itemObjs = [];
      for (const name of names) {
        const mesh = buildMesh(name, payload[name]);
        addMesh(mesh, payload[name].conditions, payload[name].sources, payload[name].texture_variants);
        itemObjs.push(mesh);
        itemsWrap.appendChild(buildDrawRow(name, groupName, payload[name], mesh, itemCbs, masterCb));
      }

      masterCb.addEventListener('change', () => {
        masterCb.indeterminate = false;
        const v = masterCb.checked;
        itemCbs.forEach((c, i) => {
          c.checked = v;
          itemObjs[i].userData.manualVisible = v;
          applyMeshVisibility(itemObjs[i]);
        });
      });

      registerGroup({ masterCb, itemCbs, itemObjs });
    }
  }

  document.getElementById('sidebar').style.display = 'block';
  return activeMeshes;
}
