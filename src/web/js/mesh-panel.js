// The MESHES panel (left): in multi-ini mods, one collapsible section per
// source ini (mirroring the Toggle panel); within each, one collapsible group
// per component, one checkbox per draw call within it.

import { buildMesh, setMeshTexture } from './mesh-factory.js';
import { activeMeshes, addMesh, applyMeshVisibility, registerGroup,
         setManualTexOverride } from './visibility.js';
import { selectMesh } from './selection.js';
import { RESERVED_KEYS } from './payload.js';
import { openTextureModal } from './texture-modal.js';

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

function buildGroupHeader(groupName, itemsWrap, texturePool, modPath, onPoolChange) {
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

  // Always shown, even for a component with no diffuse loaded at all --
  // that's the only way to attach one via "Add". updateTexButtonState gives
  // it the plain (non-.active) look automatically whenever nothing's
  // actually applied, which already reads as "disabled" without a separate
  // CSS state.
  const texBtn = document.createElement('button');
  texBtn.className = 'group-tex-btn';
  texBtn.textContent = '🖼';
  texBtn.title = 'Manage textures for this component';
  texBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    openTextureModal(groupName, texturePool, modPath, onPoolChange);
  });
  hdr.appendChild(texBtn);

  // Expand/collapse the item list without touching mesh visibility.
  hdr.addEventListener('click', (e) => {
    if (e.target === masterCb || e.target === texBtn) return;
    chevron.classList.toggle('collapsed');
    itemsWrap.classList.toggle('collapsed');
  });

  return { hdr, masterCb, texBtn };
}

/** Collapsed-by-default child list of every diffuse this mesh's component
 * ever references (the shared `pool` array -- core/mesh_builder.py's
 * `texture_options`, or the fresh empty array a component with none loaded
 * yet falls back to in buildMeshPanel) -- a radio-style single-select that
 * drives mesh.userData.manualTexOverride (see visibility.js).
 *
 * Returns `{wrap, render}`: `wrap` is the list element (append once);
 * `render()` rebuilds its rows from `pool`'s CURRENT contents and must be
 * re-invoked after the "manage textures" popup adds/removes an entry (see
 * buildMeshPanel's refreshers array), since `pool` starts empty for a
 * component with nothing loaded yet and is mutated in place afterward --
 * a one-time snapshot at build time would never see what gets added later.
 *
 * Clicking a row selects it (mesh.userData.manualTexOverride = that
 * tex_key, sticky until de-selected); clicking the already-selected row
 * de-selects it (clears back to undefined, mesh reverts to its ini
 * default). `groupMeshes` is the ordered list for this component; texture
 * inheritance is recomputed from top to bottom after every selection. */
function recomputeTextureRuns(groupMeshes) {
  let activeKey = null;
  for (const item of groupMeshes) {
    if (item.userData.manualTexOverride !== undefined) {
      activeKey = item.userData.manualTexOverride;
    } else if (item.userData.automaticTextureBoundary
               && !item.userData.textureHighlightDisabled) {
      // Follow the texture currently resolved by toggle/menu state, not the
      // immutable load-time default. This boundary then applies downward only
      // until the next highlighted boundary in this component.
      activeKey = item.userData.resolvedTexKey;
    }
    setMeshTexture(item, activeKey);
  }
}

function metadataKey(name, entry) {
  const component = entry.component || name.replace(/-\d+$/, '');
  const draw = entry.drawindexed ? entry.drawindexed.join(',') : 'whole';
  return `${component}::${draw}`;
}

function saveTextureState(modPath) {
  if (!modPath || !window.pywebview?.api?.save_mesh_textures) return;
  const state = {};
  for (const mesh of activeMeshes) {
    let texKey;
    let manual = false;
    if (mesh.userData.manualTexOverride !== undefined) {
      texKey = mesh.userData.manualTexOverride;
      manual = true;
    } else if (mesh.userData.automaticTextureBoundary
               && !mesh.userData.textureHighlightDisabled) {
      texKey = mesh.userData.resolvedTexKey;
    }
    if (!texKey) continue;
    const option = (mesh.userData.texturePool || []).find(o => o.tex_key === texKey);
    // Removing an option also removes its persisted highlight, even though
    // the already-rendered mesh may keep that texture until the next refresh.
    if (!option) continue;
    state[mesh.userData.metadataKey] = {
      tex_key: texKey,
      label: option.label,
      manual,
    };
  }
  window.pywebview.api.save_mesh_textures(modPath, state);
}

function buildTextureList(pool, mesh, groupMeshes, onActiveChanged) {
  const wrap = document.createElement('div');
  wrap.className = 'tex-list collapsed';

  function render() {
    wrap.innerHTML = '';
    const rows = [];
    function selectRow(row) {
      rows.forEach(r => r.classList.toggle('selected', r === row));
    }
    function addRow(label, value) {
      const row = document.createElement('div');
      row.className = 'tex-item';
      row.textContent = label;
      row.addEventListener('click', () => {
        const current = mesh.userData.manualTexOverride;
        const autoHighlighted = current === undefined
          && mesh.userData.automaticTextureBoundary
          && mesh.userData.resolvedTexKey === value
          && !mesh.userData.textureHighlightDisabled;
        // Click the already-selected row -> de-select (revert to ini default).
        const newVal = (current === value || autoHighlighted) ? undefined : value;
        if (newVal === undefined && (current === value || autoHighlighted)) {
          mesh.userData.textureHighlightDisabled = true;
          setManualTexOverride(mesh, newVal);
        } else {
          mesh.userData.textureHighlightDisabled = false;
          setManualTexOverride(mesh, newVal);
        }
        recomputeTextureRuns(groupMeshes);
        selectRow(newVal === undefined ? null : row);
        if (onActiveChanged) onActiveChanged();
        saveTextureState(mesh.userData.modPath);
      });
      wrap.appendChild(row);
      rows.push(row);
      return row;
    }

    // With no manual override, highlight only the first mesh using each
    // resolved texture. Toggle/menu changes update that boundary's selected
    // row to its newly resolved texture; followers remain unselected.
    const current = mesh.userData.manualTexOverride;
    const autoKey = mesh.userData.automaticTextureBoundary
      ? mesh.userData.resolvedTexKey
      : undefined;
    const isAutomaticBoundary = current === undefined && !!autoKey;
    const selectedKey = current === undefined && !mesh.userData.textureHighlightDisabled
      ? (isAutomaticBoundary ? autoKey : undefined)
      : current;
    let matched = null;
    for (const opt of pool) {
      const row = addRow(opt.label, opt.tex_key);
      if (selectedKey === opt.tex_key) matched = row;
    }
    // A removed option that was the active manual pick has nothing to
    // highlight -- leave every row unselected rather than falsely claiming
    // it reverted to automatic, since the mesh's actual texKey is untouched.
    if (matched) selectRow(matched);
  }

  render();
  return { wrap, render };
}

/** "count, start, base" from the ini's own drawindexed line — falls back to
 * the old bare "#N" numbering for the rare draw with no such line at all
 * (whole index buffer read unconditionally; see mesh_builder.build_mesh_payload).
 * Returns `{wrap, renderTexList}` -- the caller collects `renderTexList`
 * alongside every other mesh in the component so the "manage textures"
 * popup can refresh them all after an add/remove (see buildMeshPanel). */
function buildDrawRow(name, groupName, entry, mesh, pool, itemCbs, masterCb,
                      groupMeshes, onActiveChanged) {
  const row = document.createElement('div');
  row.className = 'draw-item';

  const cb = document.createElement('button');
  cb.type = 'button';
  cb.className = 'mesh-state-btn';
  cb.checked = true;
  cb.addEventListener('click', (e) => {
    e.stopPropagation();
    cb.checked = !mesh.visible;
    mesh.userData.manualVisible = cb.checked;
    mesh.userData.manuallyToggled = true;
    applyMeshVisibility(mesh);
    updateStateIndicator(mesh);
    const any = itemCbs.some(c => c.checked);
    const all = itemCbs.every(c => c.checked);
    masterCb.indeterminate = any && !all;
    masterCb.checked = all;
  });
  itemCbs.push(cb);

  const { wrap: texList, render: renderTexList } = buildTextureList(
    pool, mesh, groupMeshes, onActiveChanged);
  mesh.userData.updateTextureList = renderTexList;

  const chevron = document.createElement('span');
  chevron.className = 'group-toggle collapsed';
  chevron.textContent = '▼';

  const label = entry.drawindexed
    ? entry.drawindexed.join(', ')
    : '#' + name.slice(groupName.length + 1);
  const labelSpan = document.createElement('span');
  labelSpan.className = 'mesh-name';
  labelSpan.textContent = mesh.userData.displayName || label;
  row.append(cb, chevron, labelSpan);
  const updateStateIndicator = (m) => {
    cb.checked = m.visible;
    cb.textContent = m.userData.manuallyToggled ? (m.visible ? '✅' : '🟨') : (m.visible ? '✅' : '🟥');
    if (m.userData.manuallyToggled) {
      cb.title = 'Manually toggled in the viewer';
    } else if (m.visible === (m.userData.loadedVisible !== false)) {
      cb.title = m.visible ? 'Visible by default' : 'Hidden by the mod default state';
    } else {
      cb.title = m.visible ? 'Visible under the current mod state' : 'Hidden under the current mod state';
    }
  };
  mesh.userData.updateStateIndicator = updateStateIndicator;
  updateStateIndicator(mesh);
  labelSpan.addEventListener('dblclick', (e) => {
    e.stopPropagation();
    if (labelSpan.querySelector('input')) return;

    const original = labelSpan.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'mesh-name-input';
    input.value = original;
    labelSpan.textContent = '';
    labelSpan.append(input);

    let finished = false;
    const finish = (apply) => {
      if (finished) return;
      const next = input.value.trim();
      if (apply && !next) return;
      finished = true;
      labelSpan.textContent = apply ? next : original;
      if (!apply) return;
      mesh.userData.displayName = next;
      mesh.userData.meshNames[mesh.userData.metadataKey] = next;
      if (mesh.userData.modPath) {
        window.pywebview.api.save_mesh_names(mesh.userData.modPath, mesh.userData.meshNames);
      }
    };

    input.addEventListener('click', event => event.stopPropagation());
    input.addEventListener('dblclick', event => event.stopPropagation());
    input.addEventListener('keydown', (event) => {
      event.stopPropagation();
      if (event.key === 'Enter') {
        event.preventDefault();
        finish(true);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        finish(false);
      }
    });
    input.addEventListener('blur', () => finish(false));
    input.focus();
    input.select();
  });

  mesh.userData.row = row;
  row.addEventListener('click', (e) => {
    if (e.target === cb) return; // the state button only ever toggles visibility
    if (e.target === chevron) {
      chevron.classList.toggle('collapsed');
      texList.classList.toggle('collapsed');
      return;
    }
    selectMesh(mesh);
  });

  const wrap = document.createElement('div');
  wrap.className = 'draw-item-wrap';
  wrap.append(row, texList);
  return { wrap, renderTexList };
}

/** Reflects whether any mesh in the component currently has a texture
 * actually applied (post toggle/manual resolution) onto its header button --
 * same on/off visual language as #texture-btn/#wire-btn in app.css. */
function updateTexButtonState(texBtn, itemObjs) {
  if (!texBtn) return;
  texBtn.classList.toggle('active', itemObjs.some(m => !!m.material.map));
}

/** Build the panel and add every mesh in the payload to the scene. `modPath`
 * is threaded through to the per-component texture popup, which needs it to
 * open the native file picker rooted at the mod folder. */
export function buildMeshPanel(payload, modPath, meshNames = {}) {
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

      // Every mesh in a component shares the SAME texture_options array
      // object (see mesh_builder.build_mesh_payload) -- read it once so
      // add/remove via the popup is reflected across every mesh's own list.
      // A component with no resolved diffuses carries no such array -- fall
      // back to a fresh empty array so the "manage textures" popup still has
      // something to push an added texture into; it's captured by this
      // closure, so reopening the popup for the same component within the
      // session sees what was added.
      const texturePool = names.map(n => payload[n].texture_options).find(Boolean) || [];

      const itemCbs = [], itemObjs = [], texListRenderers = [];
      const highlightedDefaults = new Set();
      const onActiveChanged = () => updateTexButtonState(texBtn, itemObjs);
      // Re-renders every mesh's own texture list in this component after the
      // "manage textures" popup adds/removes an entry from the shared pool
      // -- a plain add/remove on `texturePool` doesn't itself touch the
      // already-built DOM rows.
      const onPoolChange = () => {
        texListRenderers.forEach(r => r());
        onActiveChanged();
        saveTextureState(modPath);
      };

      const { hdr, masterCb, texBtn } = buildGroupHeader(
        groupName, itemsWrap, texturePool, modPath, onPoolChange);
      container.append(hdr, itemsWrap);

      for (const name of names) {
        const mesh = buildMesh(name, payload[name]);
        mesh.userData.metadataKey = metadataKey(name, payload[name]);
        mesh.userData.texturePool = texturePool;
        mesh.userData.displayName = meshNames[mesh.userData.metadataKey] || null;
        mesh.userData.meshNames = meshNames;
        mesh.userData.modPath = modPath;
        addMesh(mesh, payload[name].conditions, payload[name].sources, payload[name].texture_variants);
        // addMesh establishes the automatic defaults; restore persisted
        // viewer choices only after that initialization has completed.
        if (Object.hasOwn(payload[name], 'saved_texture_override')) {
          mesh.userData.manualTexOverride = payload[name].saved_texture_override;
        }
        itemObjs.push(mesh);
        // The first mesh for each resolved texture becomes an automatic
        // boundary. The ordered pass below propagates each boundary only
        // downward, stopping at the next one in this component.
        const defaultKey = mesh.userData.defaultTexKey;
        if (defaultKey && !highlightedDefaults.has(defaultKey)) {
          highlightedDefaults.add(defaultKey);
          mesh.userData.automaticTextureBoundary = true;
        }
        const { wrap, renderTexList } = buildDrawRow(
          name, groupName, payload[name], mesh, texturePool, itemCbs, masterCb,
          itemObjs, onActiveChanged);
        texListRenderers.push(renderTexList);
        itemsWrap.appendChild(wrap);
      }
      recomputeTextureRuns(itemObjs);
      updateTexButtonState(texBtn, itemObjs);

      masterCb.addEventListener('change', () => {
        masterCb.indeterminate = false;
        const v = masterCb.checked;
        itemCbs.forEach((c, i) => {
          c.checked = v;
          itemObjs[i].userData.manualVisible = v;
          itemObjs[i].userData.manuallyToggled = true;
          applyMeshVisibility(itemObjs[i]);
          itemObjs[i].userData.updateStateIndicator?.(itemObjs[i]);
        });
      });

      registerGroup({
        masterCb, itemCbs, itemObjs, onTexChanged: onActiveChanged,
        applyTextureRuns: () => recomputeTextureRuns(itemObjs),
      });
    }
  }

  document.getElementById('sidebar').style.display = 'block';
  document.getElementById('camera-panel').style.display = 'block';
  return activeMeshes;
}
