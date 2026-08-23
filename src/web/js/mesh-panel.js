// The MESHES panel (left): in multi-ini mods, one collapsible section per
// source ini (mirroring the Toggle panel); within each, one collapsible group
// per component, one checkbox per draw call within it.

import { buildMesh, hasTexture } from './mesh-factory.js';
import {
  activeMeshes, addMesh, applyMeshVisibility, conditionsSatisfied,
  setManualTexOverride,
} from './mesh-state.js';
import {
  meshMetadataKey, recomputeTextureRuns, saveTextureState,
} from './mesh-texture-state.js';
import { bindMeshView, getMeshView } from './mesh-view-bindings.js';
import { registerViewSync } from './view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from './panel-utils.js';
import { selectMesh } from './selection.js';
import { openTextureModal } from './texture-modal.js';
import { registerInspectorMesh } from './inspector-panel.js';
import { createIcon } from './ui-icons.js';
import { notifyMeshStateChanged } from './mesh-state-events.js';
import {
  assetSecondaryLabel, assetSummaryLabel, summarizeAssetBindings,
} from './asset-diagnostics.js';

let groupsUI = [];
let meshSectionId = 0;

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

function buildGroupHeader(groupName, itemsWrap, onComponentSelected = null,
                          assetSummary = null) {
  const hdr = document.createElement('div');
  hdr.className = 'group-hdr';

  const chevron = document.createElement('button');
  chevron.type = 'button';
  chevron.className = 'group-toggle';
  chevron.setAttribute('aria-expanded', 'true');
  chevron.setAttribute('aria-label', `Collapse ${groupName}`);
  chevron.setAttribute('aria-controls', itemsWrap.id);
  chevron.appendChild(createIcon('chevron-down'));

  const masterCb = document.createElement('input');
  masterCb.type = 'checkbox';
  masterCb.checked = true;

  const nameSpan = document.createElement('span');
  nameSpan.className = 'group-name';
  nameSpan.textContent = groupName;
  nameSpan.title = groupName;

  hdr.append(chevron, masterCb, nameSpan);
  const summaryLabel = assetSummaryLabel(assetSummary);
  if (summaryLabel) {
    const assetSpan = document.createElement('span');
    assetSpan.className = 'asset-secondary-label asset-component-label';
    assetSpan.textContent = summaryLabel;
    assetSpan.title = summaryLabel;
    hdr.appendChild(assetSpan);
  }
  nameSpan.addEventListener('click', event => {
    event.stopPropagation();
    onComponentSelected?.();
  });

  const toggleItems = () => {
    const collapsed = !itemsWrap.classList.contains('collapsed');
    chevron.classList.toggle('collapsed', collapsed);
    chevron.setAttribute('aria-expanded', String(!collapsed));
    chevron.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${groupName}`);
    itemsWrap.classList.toggle('collapsed', collapsed);
  };
  chevron.addEventListener('click', event => {
    event.stopPropagation();
    toggleItems();
  });
  hdr.addEventListener('click', (e) => {
    if (e.target === masterCb) return;
    onComponentSelected?.();
  });

  return { hdr, masterCb };
}

function updateComponentAssetLabel(header, summary) {
  if (!header) return;
  let assetSpan = header.querySelector('.asset-component-label');
  const label = assetSummaryLabel(summary);
  if (!label) {
    assetSpan?.remove();
    return;
  }
  if (!assetSpan) {
    assetSpan = document.createElement('span');
    assetSpan.className = 'asset-secondary-label asset-component-label';
    header.appendChild(assetSpan);
  }
  assetSpan.textContent = label;
  assetSpan.title = label;
}

function updateDrawAssetLabel(mesh) {
  const row = mesh?.userData?.assetRow;
  if (!row) return;
  let assetSpan = row.querySelector('.asset-draw-label');
  const label = assetSecondaryLabel(
    mesh.userData.assetEntry?.asset_binding);
  if (!label) {
    assetSpan?.remove();
    return;
  }
  if (!assetSpan) {
    assetSpan = document.createElement('span');
    assetSpan.className = 'asset-secondary-label asset-draw-label';
    row.appendChild(assetSpan);
  }
  assetSpan.textContent = label.replace(/^Asset:\s*/, '');
  assetSpan.title = label;
}

/** "count, start, base" from the ini's own drawindexed line — falls back to
 * the old bare "#N" numbering for the rare draw with no such line at all
 * (whole index buffer read unconditionally; see mesh_builder.build_mesh_payload).
 * Returns `{wrap, rebuildTexList}` -- the caller collects `rebuildTexList`
 * alongside every other mesh in the component so the "manage textures"
 * popup can refresh them all after an add/remove (see buildMeshPanel). */
function buildDrawRow(name, groupName, entry, mesh, itemCbs, masterCb) {
  const row = document.createElement('div');
  row.className = 'draw-item';

  const cb = document.createElement('button');
  cb.type = 'button';
  cb.className = 'mesh-state-btn';
  cb.appendChild(createIcon('visibility'));
  cb.checked = true;
  cb.addEventListener('click', (e) => {
    e.stopPropagation();
    const nextVisible = !mesh.visible;
    cb.checked = nextVisible;
    mesh.userData.manualVisible = nextVisible;
    const automaticVisible = conditionsSatisfied(mesh);
    mesh.userData.manuallyToggled = nextVisible !== automaticVisible;
    applyMeshVisibility(mesh);
    updateStateIndicator(mesh);
    const any = itemCbs.some(c => c.checked);
    const all = itemCbs.every(c => c.checked);
    masterCb.indeterminate = any && !all;
    masterCb.checked = all;
  });
  itemCbs.push(cb);

  const label = entry.drawindexed
    ? entry.drawindexed.join(', ')
    : '#' + name.slice(groupName.length + 1);
  const labelSpan = document.createElement('span');
  labelSpan.className = 'mesh-name';
  labelSpan.textContent = mesh.userData.displayName || label;
  row.append(cb, labelSpan);
  const assetLabel = assetSecondaryLabel(entry.asset_binding);
  if (assetLabel) {
    const assetSpan = document.createElement('span');
    assetSpan.className = 'asset-secondary-label asset-draw-label';
    assetSpan.textContent = assetLabel.replace(/^Asset:\s*/, '');
    assetSpan.title = assetLabel;
    row.appendChild(assetSpan);
  }
  mesh.userData.assetRow = row;
  const updateStateIndicator = (m) => {
    cb.checked = m.visible;
    cb.classList.toggle('state-hidden', !m.visible);
    cb.classList.toggle('state-manual', !!m.userData.manuallyToggled);
    cb.setAttribute('aria-pressed', String(m.visible));
    if (m.userData.manuallyToggled) {
      cb.title = m.visible ? 'Visible (manual override)' : 'Hidden (manual override)';
    } else {
      cb.title = m.visible ? 'Visible automatically' : 'Hidden automatically';
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
  });
  row.addEventListener('click', (e) => {
    if (e.target === cb) return;
    selectMesh(mesh);
  });

  const wrap = document.createElement('div');
  wrap.className = 'draw-item-wrap';
  wrap.append(row);
  return { wrap };
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
      itemsWrap.id = `mesh-group-${++meshSectionId}`;

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

      const itemCbs = [], itemObjs = [];
      const highlightedDefaults = new Set();
      const componentDescriptor = {
        type: 'component', component: groupName, source: src,
        meshes: itemObjs, texturePool, modPath,
      };
      const assetSummary = summarizeAssetBindings(
        names.map(name => meshes[name]), options.assetResolution);
      componentDescriptor.assetSummary = assetSummary;
      componentDescriptor.assetResolution = options.assetResolution || null;
      let materialKind = componentKind;
      let materialKindInFlight = false;
      const setMaterialKind = async kind => {
        if (!componentIdentity || materialKindInFlight) return false;
        materialKindInFlight = true;
        try {
          const result = await saveComponentMaterialKind(
            modPath, src, componentIdentity, kind);
          if (result?.error || result?.saved === false) {
            throw new Error(result?.error || 'material kind was not saved');
          }
          materialKind = kind === 'auto' ? null : kind;
          await options.onMaterialKindChanged?.();
          return true;
        } catch (error) {
          console.error(`Could not save material kind for ${groupName}`, error);
          return false;
        } finally {
          materialKindInFlight = false;
        }
      };

      const setTextureOverride = (mesh, value) => {
        if (value !== undefined && value !== null && !hasTexture(value)) {
          console.warn('Texture pool entry missing registry source', value);
          return false;
        }
        mesh.userData.textureHighlightDisabled = false;
        setManualTexOverride(mesh, value, { notify: false });
        recomputeTextureRuns(itemObjs);
        notifyMeshStateChanged(itemObjs);
        saveTextureState(modPath);
        return true;
      };
      const getTextureOverride = mesh => ({
        value: mesh.userData.manualTexOverride,
        automatic: mesh.userData.manualTexOverride === undefined
          && !mesh.userData.textureHighlightDisabled,
        resolved: mesh.userData.resolvedTexKey || null,
      });
      const onPoolChange = () => {
        recomputeTextureRuns(itemObjs);
        notifyMeshStateChanged(itemObjs);
        window.dispatchEvent(new CustomEvent('mod-viewer-inspector-refresh', {
          detail: { component: componentDescriptor, reason: 'pool' },
        }));
        saveTextureState(modPath);
      };
      Object.assign(componentDescriptor, {
        getMaterialKind: () => materialKind,
        setMaterialKind,
        openTextureManager: () => openTextureModal(
          groupName, texturePool, modPath, onPoolChange),
        getTextureOverride,
        setTextureOverride,
      });

      const { hdr, masterCb } = buildGroupHeader(
        groupName, itemsWrap,
        () => window.dispatchEvent(new CustomEvent('mod-viewer-component-selected', {
          detail: { component: componentDescriptor },
        })), assetSummary);
      componentDescriptor.header = hdr;
      container.append(hdr, itemsWrap);

      for (const name of names) {
        const entry = meshes[name];
        const materialProfile = materialProfiles?.[entry.material_profile_id] || null;
        const mesh = buildMesh(name, entry, materialProfile);
        mesh.userData.semanticKey = name;
        mesh.userData.metadataKey = meshMetadataKey(name, meshes[name]);
        mesh.userData.texturePool = texturePool;
        mesh.userData.displayName = meshNames[mesh.userData.metadataKey] || null;
        mesh.userData.meshNames = meshNames;
        mesh.userData.modPath = modPath;
        // Diagnostic-only projection. Operational identity remains the
        // existing semantic key and component grouping.
        mesh.userData.assetEntry = meshes[name];
        mesh.userData.componentDescriptor = componentDescriptor;
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
        const { wrap } = buildDrawRow(
          name, groupName, meshes[name], mesh, itemCbs, masterCb);
        itemsWrap.appendChild(wrap);
        registerInspectorMesh(mesh, {
          component: componentDescriptor,
          entry: meshes[name],
          label: mesh.userData.displayName || name,
        });
      }
      recomputeTextureRuns(itemObjs);

      masterCb.addEventListener('change', () => {
        masterCb.indeterminate = false;
        const v = masterCb.checked;
        itemCbs.forEach((c, i) => {
          c.checked = v;
          itemObjs[i].userData.manualVisible = v;
          itemObjs[i].userData.manuallyToggled = true;
          applyMeshVisibility(itemObjs[i], { notify: false });
          getMeshView(itemObjs[i])?.syncStateIndicator?.();
        });
        notifyMeshStateChanged(itemObjs);
      });

      groupsUI.push({
        masterCb, itemCbs, itemObjs,
        componentDescriptor,
        assetResolution: options.assetResolution || null,
        applyTextureRuns: () => recomputeTextureRuns(itemObjs),
      });
    }
  }

  document.getElementById('camera-panel').style.display = 'none';
  return activeMeshes;
}

export function refreshMeshAssetDiagnostics(assetResolution = undefined) {
  for (const group of groupsUI) {
    if (assetResolution !== undefined) {
      group.assetResolution = assetResolution;
      group.componentDescriptor.assetResolution = assetResolution;
    }
    const summary = summarizeAssetBindings(
      group.itemObjs.map(mesh => mesh.userData.assetEntry),
      group.assetResolution);
    group.componentDescriptor.assetSummary = summary;
    updateComponentAssetLabel(group.componentDescriptor.header, summary);
    group.itemObjs.forEach(updateDrawAssetLabel);
    window.dispatchEvent(new CustomEvent('mod-viewer-inspector-refresh', {
      detail: {
        component: group.componentDescriptor,
        reason: 'asset',
      },
    }));
  }
}
