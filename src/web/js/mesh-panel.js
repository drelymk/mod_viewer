// The MESHES panel (left): in multi-ini mods, one collapsible section per
// source ini (mirroring the Toggle panel); within each, one collapsible group
// per component, one checkbox per draw call within it.

import { buildMesh, hasTexture } from './mesh-factory.js';
import { isGameMaterialTextureBound } from './material-profile.js';
import {
  activeMeshes, addMesh, applyMeshVisibility, setManualTexOverride,
} from './mesh-state.js';
import {
  meshMetadataKey, recomputeTextureRuns, saveTextureState,
} from './mesh-texture-state.js';
import { bindMeshView, getMeshView } from './mesh-view-bindings.js';
import { registerViewSync } from './view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from './panel-utils.js';
import { selectMesh } from './selection.js';
import { openTextureModal } from './texture-modal.js';

let groupsUI = [];

const MATERIAL_KIND_OPTIONS = Object.freeze([
  ['auto', 'Auto'],
  ['body', 'Body'],
  ['face', 'Face'],
  ['hair', 'Hair'],
  ['eye', 'Eye'],
  ['weapon', 'Weapon'],
  ['special', 'Special'],
]);

function saveComponentMaterialKind(modPath, source, component, kind) {
  if (!modPath || !window.pywebview?.api?.save_component_material_kind) {
    return Promise.resolve({ saved: false });
  }
  return window.pywebview.api.save_component_material_kind(
    modPath, source, component, kind);
}

function syncMeshPanel() {
  for (const group of groupsUI) {
    group.applyTextureRuns?.();
    group.itemObjs.forEach((mesh, index) => {
      group.itemCbs[index].checked = mesh.visible;
      const binding = getMeshView(mesh);
      binding?.syncStateIndicator?.();
      binding?.syncTextureSelection?.();
    });
    const any = group.itemCbs.some(control => control.checked);
    const all = group.itemCbs.every(control => control.checked);
    group.masterCb.checked = all;
    group.masterCb.indeterminate = any && !all;
    group.onTexChanged?.();
  }
}

/** Group mesh names by their clean, never-disambiguated component name
 * (see core/mesh_builder.py's `component` field) — falls back to parsing the
 * dict key itself (stripping a trailing "-N" draw index) for the rare case
 * a payload entry lacks it. Using the explicit field (rather than the key)
 * means a cross-ini name collision's internal "_2" uniqueness suffix (see
 * core/ini_parser.py's build_draw_groups) never leaks into the displayed
 * group header — the per-source section above it already disambiguates. */
function groupByComponent(names, meshes) {
  const grouped = {};
  for (const name of names) {
    const explicit = meshes[name]?.component;
    let key = explicit;
    if (!key) {
      const m = name.match(/^(.+)-\d+$/);
      key = m ? m[1] : name;
    }
    (grouped[key] = grouped[key] || []).push(name);
  }
  return grouped;
}

function buildGroupHeader(groupName, itemsWrap, texturePool, modPath,
                          onPoolChange, componentKind,
                          onMaterialKindChanged, componentIdentity = groupName,
                          source = '') {
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

  const materialLabel = document.createElement('label');
  materialLabel.className = 'material-kind-control component-material-kind-control';
  materialLabel.textContent = 'Material:';
  const materialSelect = document.createElement('select');
  materialSelect.className = 'material-kind-select';
  materialSelect.title = 'Choose a viewer material kind for this component';
  for (const [value, label] of MATERIAL_KIND_OPTIONS) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    materialSelect.appendChild(option);
  }
  materialSelect.value = componentKind || 'auto';
  materialSelect.disabled = !componentIdentity;
  materialSelect.addEventListener('click', event => event.stopPropagation());
  materialSelect.addEventListener('change', async event => {
    event.stopPropagation();
    const previous = componentKind || 'auto';
    const next = materialSelect.value;
    materialSelect.disabled = true;
    try {
      const result = await saveComponentMaterialKind(
        modPath, source, componentIdentity, next);
      if (result?.error || result?.saved === false) {
        throw new Error(result?.error || 'material kind was not saved');
      }
      componentKind = next === 'auto' ? null : next;
      await onMaterialKindChanged?.();
    } catch (error) {
      materialSelect.value = previous;
      console.error('Could not save component material kind', error);
    } finally {
      materialSelect.disabled = false;
    }
  });
  materialLabel.appendChild(materialSelect);
  hdr.appendChild(materialLabel);

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
    if (e.target === masterCb || e.target === texBtn
        || e.target.closest('select')) return;
    chevron.classList.toggle('collapsed');
    itemsWrap.classList.toggle('collapsed');
  });

  return { hdr, masterCb, texBtn };
}

/** Collapsed-by-default child list of every diffuse this mesh's component
 * ever references (the shared `pool` array -- the application payload's
 * `texture_pools` entry, or the fresh empty array a component with none loaded
 * yet falls back to in buildMeshPanel) -- a radio-style single-select that
 * drives mesh.userData.manualTexOverride (see visibility.js).
 *
 * Returns a list element plus separate structural and selection sync calls.
 * `rebuild()` recreates rows from `pool`'s CURRENT contents and must be
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
function buildTextureList(pool, mesh, groupMeshes, onActiveChanged) {
  const wrap = document.createElement('div');
  wrap.className = 'tex-list collapsed';
  let rows = [];

  function selectedKey() {
    const current = mesh.userData.manualTexOverride;
    const autoKey = mesh.userData.automaticTextureBoundary
      ? mesh.userData.resolvedTexKey
      : undefined;
    const isAutomaticBoundary = current === undefined && !!autoKey;
    return current === undefined && !mesh.userData.textureHighlightDisabled
      ? (isAutomaticBoundary ? autoKey : undefined)
      : current;
  }

  function syncSelection() {
    const selected = selectedKey();
    rows.forEach(({ row, value }) => {
      row.classList.toggle('selected', selected === value);
    });
  }

  function rebuild() {
    wrap.replaceChildren();
    rows = [];
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
        if (newVal !== undefined && !hasTexture(newVal)) {
          console.warn('Texture pool entry missing registry source', newVal);
          return;
        }
        if (newVal === undefined && autoHighlighted) {
          // Clicking an automatic boundary disables that boundary. Clearing a
          // manual pin is different: it must restore automatic propagation so
          // INI texture toggles can take control again.
          mesh.userData.textureHighlightDisabled = true;
        } else {
          mesh.userData.textureHighlightDisabled = false;
        }
        setManualTexOverride(mesh, newVal);
        recomputeTextureRuns(groupMeshes);
        syncSelection();
        if (onActiveChanged) onActiveChanged();
        saveTextureState(mesh.userData.modPath);
      });
      wrap.appendChild(row);
      rows.push({ row, value });
    }

    for (const opt of pool) {
      addRow(opt.label, opt.tex_key);
    }
    // A removed active option has no matching row, so all rows remain clear.
    syncSelection();
  }

  rebuild();
  return { wrap, rebuild, syncSelection };
}

/** "count, start, base" from the ini's own drawindexed line — falls back to
 * the old bare "#N" numbering for the rare draw with no such line at all
 * (whole index buffer read unconditionally; see mesh_builder.build_mesh_payload).
 * Returns `{wrap, rebuildTexList}` -- the caller collects `rebuildTexList`
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

  const { wrap: texList, rebuild: rebuildTexList, syncSelection } = buildTextureList(
    pool, mesh, groupMeshes, onActiveChanged);

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
      if (!apply || next === original) return;
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

  bindMeshView(mesh, {
    row,
    stateButton: cb,
    syncStateIndicator: () => updateStateIndicator(mesh),
    syncTextureSelection: syncSelection,
    rebuildTextureList: rebuildTexList,
    onTextureChanged: onActiveChanged,
  });
  row.addEventListener('click', (e) => {
    if (e.target === cb) return;
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
  return { wrap, rebuildTexList };
}

/** Reflects whether any mesh in the component currently has a texture
 * actually applied (post toggle/manual resolution) onto its header button --
 * same on/off visual language as #texture-btn/#wire-btn in app.css. */
function updateTexButtonState(texBtn, itemObjs) {
  if (!texBtn) return;
  texBtn.classList.toggle('active', itemObjs.some(mesh =>
    isGameMaterialTextureBound(mesh.material, 'diffuse')));
}

/** Build the panel and add every mesh in the mesh map to the scene. `modPath`
 * is threaded through to the per-component texture popup, which needs it to
 * open the native file picker rooted at the mod folder. */
export function buildMeshPanel(meshes, modPath, meshNames = {},
                               materialProfiles = {}, options = {}) {
  const list = document.getElementById('mesh-list');
  list.innerHTML = '';
  groupsUI = [];
  registerViewSync('mesh-panel', syncMeshPanel);
  const texturePools = options.texturePools || {};

  const validNames = Object.keys(meshes).filter(name => !meshes[name]?.error);
  const bySource = groupKeysBySource(meshes, validNames);
  const sources = Object.keys(bySource);
  const multiSource = usesSourceSections(bySource);

  for (const src of sources) {
    const container = (multiSource && src) ? buildSourceSection(src, list, {
      headerClass: 'mesh-src-hdr', itemsClass: 'mesh-src-items',
    }) : list;

    for (const [groupName, names] of Object.entries(groupByComponent(bySource[src], meshes))) {
      const itemsWrap = document.createElement('div');
      itemsWrap.className = 'group-items';

      const poolIds = new Set(names.map(name => meshes[name].texture_pool_id)
        .filter(Boolean));
      if (poolIds.size > 1) {
        throw new Error(`Component ${groupName} has multiple texture pools`);
      }
      const poolId = poolIds.values().next().value;
      const texturePool = poolId ? texturePools[poolId] || [] : [];
      const componentKind = names
        .map(n => meshes[n].material_kind_override)
        .find(Boolean) || null;
      const componentIdentity = names
        .map(n => meshes[n].component)
        .find(Boolean) || null;

      const itemCbs = [], itemObjs = [], texListRenderers = [];
      const highlightedDefaults = new Set();
      const onActiveChanged = () => updateTexButtonState(texBtn, itemObjs);
      // Re-renders every mesh's own texture list in this component after the
      // "manage textures" popup adds/removes an entry from the shared pool
      // -- a plain add/remove on `texturePool` doesn't itself touch the
      // already-built DOM rows.
      const onPoolChange = () => {
        texListRenderers.forEach(r => r());
        recomputeTextureRuns(itemObjs);
        onActiveChanged();
        saveTextureState(modPath);
      };

      const { hdr, masterCb, texBtn } = buildGroupHeader(
        groupName, itemsWrap, texturePool, modPath, onPoolChange,
        componentKind, options.onMaterialKindChanged, componentIdentity, src);
      container.append(hdr, itemsWrap);

      for (const name of names) {
        const entry = meshes[name];
        const materialProfile = materialProfiles?.[entry.material_profile_id] || null;
        const mesh = buildMesh(name, entry, materialProfile);
        mesh.userData.metadataKey = meshMetadataKey(name, meshes[name]);
        mesh.userData.texturePool = texturePool;
        mesh.userData.displayName = meshNames[mesh.userData.metadataKey] || null;
        mesh.userData.meshNames = meshNames;
        mesh.userData.modPath = modPath;
        addMesh(mesh, meshes[name].conditions, meshes[name].sources,
          meshes[name].texture_variants, {
            normal_map: meshes[name].normal_map_variants,
            normal_data: meshes[name].normal_data_variants,
            light_map: meshes[name].light_map_variants,
            material_map: meshes[name].material_map_variants,
          });
        // addMesh establishes the automatic defaults; restore persisted
        // viewer choices only after that initialization has completed.
        if (Object.hasOwn(meshes[name], 'saved_texture_override')) {
          mesh.userData.manualTexOverride = meshes[name].saved_texture_override;
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
        const { wrap, rebuildTexList } = buildDrawRow(
          name, groupName, meshes[name], mesh, texturePool, itemCbs, masterCb,
          itemObjs, onActiveChanged);
        texListRenderers.push(rebuildTexList);
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
          getMeshView(itemObjs[i])?.syncStateIndicator?.();
        });
      });

      groupsUI.push({
        masterCb, itemCbs, itemObjs, onTexChanged: onActiveChanged,
        applyTextureRuns: () => recomputeTextureRuns(itemObjs),
      });
    }
  }

  document.getElementById('sidebar').style.display = 'block';
  document.getElementById('camera-panel').style.display = 'block';
  return activeMeshes;
}
