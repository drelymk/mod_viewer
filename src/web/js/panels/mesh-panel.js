// The MESHES panel (left): in multi-ini mods, one collapsible section per
// source ini (mirroring the Toggle panel); within each, one collapsible group
// per component, one checkbox per draw call within it.

import { hasTexture } from '../mesh/mesh-factory.js';
import {
  applyMeshVisibility, conditionsSatisfied,
  setManualTexOverride,
} from '../mesh/mesh-state.js';
import {
  clearTextureRunGroups, recomputeTextureRuns,
  registerTextureRunGroup, unregisterTextureRunGroup, saveTextureState,
} from '../mesh/mesh-texture-state.js';
import { bindMeshView, getMeshView } from '../mesh/mesh-view-bindings.js';
import { registerViewSync } from '../scene/view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from '../ui/panel-utils.js';
import { clearSelection, selectMesh } from '../scene/selection.js';
import { openTextureModal } from '../ui/texture-modal.js';
import { registerInspectorMesh } from './inspector-panel.js';
import { createIcon } from '../ui/ui-icons.js';
import { notifyMeshStateChanged } from '../mesh/mesh-state-events.js';
import {
  assetDetailLabel, assetSummaryLabel, summarizeAssetBindings,
} from './asset-diagnostics.js';
import { noteRecordMeshEdit } from '../editing/record-session.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { requestRender } from '../scene/render-scheduler.js';
import {
  getLooseParts, isLoosePart, separateLooseParts,
} from '../mesh/loose-parts.js';

let groupsUI = [];
let meshSectionId = 0;
let meshContextMenu = null;
let meshContextAction = null;
let meshContextTarget = null;
let meshContextListenersInstalled = false;

function saveComponentMaterialKind(modPath, source, component, kind) {
  if (!modPath || !window.pywebview?.api?.save_component_material_kind) {
    return Promise.resolve({ saved: false });
  }
  return window.pywebview.api.save_component_material_kind(
    modPath, source, component, kind);
}

function closeMeshContextMenu() {
  if (!meshContextMenu) return;
  meshContextMenu.hidden = true;
  meshContextTarget = null;
}

function ensureMeshContextMenu() {
  if (meshContextMenu) return meshContextMenu;
  meshContextMenu = document.createElement('div');
  meshContextMenu.className = 'mesh-context-menu';
  meshContextMenu.setAttribute('role', 'menu');
  meshContextMenu.hidden = true;
  meshContextAction = document.createElement('button');
  meshContextAction.type = 'button';
  meshContextAction.setAttribute('role', 'menuitem');
  meshContextAction.dataset.i18n = 'mesh.separateLooseParts';
  meshContextAction.textContent = t('mesh.separateLooseParts');
  meshContextMenu.appendChild(meshContextAction);
  document.body.appendChild(meshContextMenu);
  meshContextAction.addEventListener('click', () => {
    const source = meshContextTarget;
    closeMeshContextMenu();
    if (source) separateMeshRow(source);
  });
  if (!meshContextListenersInstalled) {
    document.addEventListener('pointerdown', event => {
      if (!meshContextMenu?.contains(event.target)) closeMeshContextMenu();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeMeshContextMenu();
    });
    meshContextListenersInstalled = true;
  }
  return meshContextMenu;
}

function openMeshContextMenu(event, mesh) {
  event.preventDefault();
  event.stopPropagation();
  const menu = ensureMeshContextMenu();
  const container = document.getElementById('canvas-container') || document.body;
  const bounds = container.getBoundingClientRect();
  const left = Number(event.clientX);
  const top = Number(event.clientY);
  menu.hidden = false;
  meshContextTarget = mesh;
  menu.style.left = `${Math.max(4, left)}px`;
  menu.style.top = `${Math.max(bounds.top + 4, top)}px`;
  requestAnimationFrame(() => {
    if (menu.hidden) return;
    const maxLeft = Math.max(4, window.innerWidth - menu.offsetWidth - 4);
    const maxTop = Math.max(
      bounds.top + 4, Math.min(window.innerHeight, bounds.bottom)
        - menu.offsetHeight - 4);
    menu.style.left = `${Math.min(Math.max(4, left), maxLeft)}px`;
    menu.style.top = `${Math.min(Math.max(bounds.top + 4, top), maxTop)}px`;
  });
}

function syncMeshPanel() {
  for (const group of groupsUI) {
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
 * (see core/geometry/mesh_builder.py's `component` field) — falls back to parsing the
 * dict key itself (stripping a trailing "-N" draw index) for the rare case
 * a payload entry lacks it. Using the explicit field (rather than the key)
 * means a cross-ini name collision's internal "_2" uniqueness suffix (see
 * core/ini/parser.py's build_draw_groups) never leaks into the displayed
 * group header — the per-source section above it already disambiguates. */
function groupByComponent(names, meshes) {
  const grouped = {};
  for (const name of names) {
    const explicit = meshes[name]?.identity?.component
      || meshes[name]?.component;
    let key = explicit;
    if (!key) {
      const m = name.match(/^(.+)-\d+$/);
      key = m ? m[1] : name;
    }
    (grouped[key] = grouped[key] || []).push(name);
  }
  return grouped;
}

function automaticTextureBoundaryIdentity(mesh) {
  const defaultKey = mesh.userData.defaultTexKey;
  const variantKeys = mesh.userData.textureVariants || [];
  if (!defaultKey && !variantKeys.length) return null;
  return JSON.stringify({
    defaultKey: defaultKey || null,
    variants: variantKeys.map(variant => ({
      conditions: variant?.conditions || [],
      tex_key: variant?.tex_key || null,
    })),
  });
}

function recomputeAutomaticTextureBoundaries(groupMeshes) {
  let previousBoundaryIdentity = null;
  for (const mesh of groupMeshes) {
    mesh.userData.automaticTextureBoundary = false;
    const boundaryIdentity = automaticTextureBoundaryIdentity(mesh);
    if (boundaryIdentity && boundaryIdentity !== previousBoundaryIdentity) {
      mesh.userData.automaticTextureBoundary = true;
    }
    if (boundaryIdentity) previousBoundaryIdentity = boundaryIdentity;
  }
}

/** Rebuild authored texture-run boundaries after an in-place semantic update. */
export function refreshAutomaticTextureBoundaries() {
  groupsUI.forEach(group => recomputeAutomaticTextureBoundaries(group.itemObjs));
}

function buildGroupHeader(groupName, itemsWrap, onComponentSelected = null,
                          assetSummary = null) {
  const hdr = document.createElement('div');
  hdr.className = 'group-hdr';

  const chevron = document.createElement('button');
  chevron.type = 'button';
  chevron.className = 'group-toggle';
  chevron.setAttribute('aria-expanded', 'true');
  const syncLabels = () => {
    const collapsed = itemsWrap.classList.contains('collapsed');
    chevron.setAttribute('aria-label', t(
      collapsed ? 'mesh.expandComponent' : 'mesh.collapseComponent',
      {name: groupName}));
  };
  syncLabels();
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
    itemsWrap.classList.toggle('collapsed', collapsed);
    syncLabels();
  };
  chevron.addEventListener('click', event => {
    event.stopPropagation();
    toggleItems();
  });
  hdr.addEventListener('click', (e) => {
    if (e.target === masterCb) return;
    onComponentSelected?.();
  });

  return { hdr, masterCb, syncLabels };
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
  const label = assetDetailLabel(
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
  assetSpan.textContent = label;
  assetSpan.title = label;
}

/** "count, start, base" from the ini's own drawindexed line — falls back to
 * the old bare "#N" numbering for the rare draw with no such line at all
 * (whole index buffer read unconditionally; see mesh_builder.build_mesh_payload).
 * Returns `{wrap, rebuildTexList}` -- the caller collects `rebuildTexList`
 * alongside every other mesh in the component so the "manage textures"
 * popup can refresh them all after an add/remove (see buildMeshPanel). */
function buildDrawRow(name, groupName, entry, mesh, itemCbs, masterCb,
                      {labelOverride = null, onContextMenu = null} = {}) {
  const row = document.createElement('div');
  row.className = 'draw-item';
  const loosePart = isLoosePart(mesh);

  const cb = document.createElement('button');
  cb.type = 'button';
  cb.className = 'mesh-state-btn';
  cb.appendChild(createIcon('visibility'));
  cb.checked = true;
  cb.addEventListener('click', (e) => {
    e.stopPropagation();
    const nextVisible = !mesh.visible;
    cb.checked = nextVisible;
    if (loosePart) {
      mesh.userData.manualVisible = nextVisible;
      mesh.visible = nextVisible;
      updateStateIndicator(mesh);
      requestRender();
      return;
    }
    mesh.userData.manualVisible = nextVisible;
    const automaticVisible = conditionsSatisfied(mesh);
    mesh.userData.manuallyToggled = nextVisible !== automaticVisible;
    noteRecordMeshEdit(mesh);
    applyMeshVisibility(mesh);
    updateStateIndicator(mesh);
    const any = itemCbs.some(c => c.checked);
    const all = itemCbs.every(c => c.checked);
    masterCb.indeterminate = any && !all;
    masterCb.checked = all;
  });
  if (!loosePart) itemCbs.push(cb);

  const label = entry.drawindexed
    ? entry.drawindexed.join(', ')
    : '#' + name.slice(groupName.length + 1);
  const labelSpan = document.createElement('span');
  labelSpan.className = 'mesh-name';
  const displayLabel = labelOverride || mesh.userData.loosePartLabel
    || mesh.userData.displayName || label;
  labelSpan.textContent = displayLabel;
  if (!loosePart) mesh.userData.loosePartBaseLabel = displayLabel;
  row.append(cb, labelSpan);
  const assetLabel = assetDetailLabel(entry.asset_binding);
  if (assetLabel) {
    const assetSpan = document.createElement('span');
    assetSpan.className = 'asset-secondary-label asset-draw-label';
    assetSpan.textContent = assetLabel;
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
      cb.title = t(m.visible ? 'mesh.visibleManual' : 'mesh.hiddenManual');
    } else {
      cb.title = t(m.visible ? 'mesh.visibleAutomatic' : 'mesh.hiddenAutomatic');
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
      if (loosePart) {
        mesh.userData.loosePartLabel = next;
        return;
      }
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
  if (onContextMenu) {
    row.addEventListener('contextmenu', event => onContextMenu(event, mesh));
  }

  const wrap = document.createElement('div');
  wrap.className = 'draw-item-wrap';
  wrap.append(row);
  mesh.userData.meshRowWrap = wrap;
  return { wrap };
}

function separateMeshRow(source) {
  const context = source?.userData?.meshPanelRowContext;
  const sourceWrap = source?.userData?.meshRowWrap;
  if (!context || !sourceWrap?.isConnected) return false;
  const parts = separateLooseParts(source, {
    label: source.userData.loosePartBaseLabel,
  });
  if (parts.length <= 1) return false;

  const partWraps = parts.map((part, index) => {
    const {wrap} = buildDrawRow(
      `${context.name}::loose-part-${index + 1}`,
      context.groupName,
      context.entry,
      part,
      [],
      null,
      {labelOverride: part.userData.loosePartLabel});
    registerInspectorMesh(part, context.inspectorRecord);
    return wrap;
  });
  sourceWrap.replaceWith(...partWraps);
  requestRender();
  return true;
}

/** Build the panel for already-constructed live meshes. `modPath` is threaded
 * through to the per-component texture popup, which needs it to open the
 * native file picker rooted at the mod folder. */
export function buildMeshPanel(meshes, liveMeshes, modPath, options = {}) {
  return appendMeshPanel(meshes, liveMeshes, modPath,
    {...options, replace: true});
}

export function appendMeshPanel(meshes, liveMeshes, modPath, options = {}) {
  const list = document.getElementById('mesh-list');
  const replace = options.replace !== false;
  closeMeshContextMenu();
  if (replace) {
    list.innerHTML = '';
    groupsUI = [];
    clearTextureRunGroups();
  }
  registerViewSync('mesh-panel', syncMeshPanel);
  const texturePools = options.texturePools || {};
  const readOnlySource = options.readOnlySource === true;
  const canPersistMetadata = options.canPersistMetadata !== false;
  const texturePicker = options.texturePicker || null;

  const validNames = Object.keys(meshes).filter(name => !meshes[name]?.error);
  const bySource = groupKeysBySource(meshes, validNames);
  const sources = Object.keys(bySource);
  const multiSource = usesSourceSections(bySource);

  for (const src of sources) {
    const container = (multiSource && src) ? buildSourceSection(src, list, {
      headerClass: 'mesh-src-hdr', itemsClass: 'mesh-src-items',
    }) : list;
    const sourceHeader = container === list
      ? null : container.previousElementSibling;

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
        .map(n => meshes[n].identity?.component || meshes[n].component)
        .find(Boolean) || null;

      const itemCbs = [], itemObjs = [];
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
      const canPersist = !readOnlySource && canPersistMetadata && !!modPath;
      const canEdit = !readOnlySource && !!componentIdentity && !!modPath;
      const setMaterialKind = async kind => {
        if (!canEdit || materialKindInFlight) return false;
        if (!canPersist) {
          // Compressed mods have no writable metadata sidecar. Keep this
          // viewer-only choice in mesh state so the control remains useful,
          // while the read-only source still prevents persistence/export.
          materialKind = kind === 'auto' ? null : kind;
          itemObjs.forEach(mesh => {
            mesh.userData.materialKindOverride = materialKind;
          });
          notifyMeshStateChanged(itemObjs);
          window.dispatchEvent(new CustomEvent('mod-viewer-inspector-refresh', {
            detail: { component: componentDescriptor, reason: 'material-kind' },
          }));
          return true;
        }
        const previousKind = materialKind;
        materialKindInFlight = true;
        try {
          const result = await saveComponentMaterialKind(
            modPath, src, componentIdentity, kind);
          if (result?.error || result?.saved === false) {
            throw new Error(result?.error || 'material kind was not saved');
          }
          materialKind = kind === 'auto' ? null : kind;
          const refreshed = await options.onMaterialKindChanged?.();
          if (refreshed === false) {
            materialKind = previousKind;
            return false;
          }
          return true;
        } catch (error) {
          materialKind = previousKind;
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
        getMaterialKind: () => itemObjs[0]?.userData.materialKindOverride
          || materialKind,
        setMaterialKind: canEdit ? setMaterialKind : undefined,
        openTextureManager: () => openTextureModal(
          groupName, texturePool, modPath, onPoolChange, texturePicker),
        getTextureOverride,
        setTextureOverride,
      });

      const { hdr, masterCb, syncLabels } = buildGroupHeader(
        groupName, itemsWrap,
        () => window.dispatchEvent(new CustomEvent('mod-viewer-component-selected', {
          detail: { component: componentDescriptor },
        })), assetSummary);
      componentDescriptor.header = hdr;
      container.append(hdr, itemsWrap);

      for (const name of names) {
        const entry = meshes[name];
        const mesh = liveMeshes?.get?.(name) || liveMeshes?.[name];
        if (!mesh) throw new Error(`Missing live mesh for ${name}`);
        mesh.userData.componentDescriptor = componentDescriptor;
        itemObjs.push(mesh);
        const { wrap } = buildDrawRow(
          name, groupName, meshes[name], mesh, itemCbs, masterCb,
          {onContextMenu: openMeshContextMenu});
        itemsWrap.appendChild(wrap);
        const inspectorRecord = {
          component: componentDescriptor,
          entry: meshes[name],
          label: mesh.userData.displayName || name,
        };
        registerInspectorMesh(mesh, inspectorRecord);
        mesh.userData.meshPanelRowContext = {
          name, groupName, entry: meshes[name], inspectorRecord,
        };
      }
      recomputeAutomaticTextureBoundaries(itemObjs);
      recomputeTextureRuns(itemObjs);
      registerTextureRunGroup(itemObjs);

      masterCb.addEventListener('change', () => {
        masterCb.indeterminate = false;
        const v = masterCb.checked;
        itemCbs.forEach((c, i) => {
          c.checked = v;
          itemObjs[i].userData.manualVisible = v;
          itemObjs[i].userData.manuallyToggled = true;
          noteRecordMeshEdit(itemObjs[i]);
          applyMeshVisibility(itemObjs[i], { notify: false });
          getMeshView(itemObjs[i])?.syncStateIndicator?.();
        });
        notifyMeshStateChanged(itemObjs);
      });

      groupsUI.push({
        masterCb, itemCbs, itemObjs,
        componentDescriptor,
        header: componentDescriptor.header,
        itemsWrap,
        sourceContainer: container === list ? null : container,
        sourceHeader,
        syncLabels,
        assetFill: names.every(name => meshes[name]?.asset_fill === true),
        assetResolution: options.assetResolution || null,
      });
    }
  }

  document.getElementById('camera-panel').style.display = 'none';
}

window.addEventListener(LANGUAGE_CHANGED, () => {
  if (meshContextAction) {
    meshContextAction.textContent = t('mesh.separateLooseParts');
  }
  groupsUI.forEach(group => {
    group.syncLabels?.();
    group.itemObjs.forEach(mesh => getMeshView(mesh)?.syncStateIndicator?.());
    updateComponentAssetLabel(group.header, group.componentDescriptor.assetSummary);
    group.itemObjs.forEach(updateDrawAssetLabel);
  });
});

export function removeAssetFillMeshPanel(targetMeshes = null) {
  closeMeshContextMenu();
  const target = targetMeshes === null ? null : new Set(targetMeshes);
  const groups = groupsUI.filter(group => group.assetFill
    && (!target || group.itemObjs.some(mesh => target.has(mesh))));
  if (!groups.length) return [];
  clearSelection();
  const removed = [];
  for (const group of groups) {
    const members = target
      ? group.itemObjs.filter(mesh => target.has(mesh))
      : [...group.itemObjs];
    members.forEach(mesh => {
      const rowWraps = new Set();
      const sourceWrap = mesh.userData.assetRow?.closest('.draw-item-wrap');
      if (sourceWrap) rowWraps.add(sourceWrap);
      getLooseParts(mesh).forEach(part => {
        const partWrap = getMeshView(part)?.row?.closest('.draw-item-wrap');
        if (partWrap) rowWraps.add(partWrap);
      });
      rowWraps.forEach(rowWrap => rowWrap.remove());
      removed.push(mesh);
      const index = group.itemObjs.indexOf(mesh);
      if (index >= 0) {
        group.itemObjs.splice(index, 1);
        group.itemCbs.splice(index, 1);
      }
    });
    if (!group.itemObjs.length) {
      unregisterTextureRunGroup(group.itemObjs);
      group.header?.remove();
      group.itemsWrap?.remove();
    } else {
      recomputeTextureRuns(group.itemObjs);
    }
  }
  groupsUI = groupsUI.filter(group => !group.assetFill || group.itemObjs.length);
  for (const source of new Set(groups.map(group => group.sourceContainer).filter(Boolean))) {
    if (!source.children.length) {
      source.previousElementSibling?.remove();
      source.remove();
    }
  }
  window.dispatchEvent(new CustomEvent('mod-viewer-inspector-refresh', {
    detail: { component: null, reason: 'asset-fill-removed' },
  }));
  return removed;
}

export function refreshMeshAssetDiagnostics(assetResolution = undefined) {
  for (const group of groupsUI) {
    if (group.assetFill) continue;
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
