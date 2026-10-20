// MESHES component registration, appearance callbacks and panel lifecycle.
import { bridgeReady } from '../app/bridge.js';
import { hasTexture } from '../mesh/mesh-factory.js';
import { clearMeshIsolation, isMeshIsolationActive, setManualTexOverride } from '../mesh/mesh-state.js';
import {
  clearTextureRunGroups,
  recomputeTextureRuns,
  registerTextureRunGroup,
  unregisterTextureRunGroup,
  saveTextureState,
} from '../mesh/mesh-texture-state.js';
import { registerViewSync } from '../scene/view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from '../ui/panel-utils.js';
import { clearSelection } from '../scene/selection.js';
import { openTextureModal } from '../ui/texture-modal.js';
import { registerInspectorMesh } from './inspector-panel.js';
import { closeMeshContextMenu } from './mesh-panel-context-menu.js';
import { notifyMeshStateChanged } from '../mesh/mesh-state-events.js';
import { summarizeAssetBindings } from './asset-diagnostics.js';
import { isRecording } from '../editing/record-session.js';
import { getLooseParts } from '../mesh/loose-parts.js';
import {
  groupsUI,
  showTextureLoadError,
  meshPanelContexts,
  installPanelSelectionDrag,
  syncMeshPanel,
  sourceMeshes,
  buildGroupVisibilityButton,
  buildGroupHeader,
  buildDrawRow,
  showRecordingSourceRows,
  clearMeshPanelRows,
  meshRowWrap,
} from './mesh-panel-rows.js';
import { initializeComponentMeshEdit, resetMeshPanelEditing } from './mesh-panel-edit.js';
export { hasUnappliedMeshChanges } from './mesh-panel-edit.js';

let meshSectionId = 0;

function saveComponentMaterialKind(modPath, source, component, kind) {
  if (!modPath) {
    return Promise.resolve({ saved: false });
  }
  return bridgeReady().then((api) => api.save_component_material_kind(modPath, source, component, kind));
}

/** Prefer the explicit component field so parser collision suffixes stay out of labels. */
function groupByComponent(names, meshes) {
  const grouped = {};
  for (const name of names) {
    const explicit = meshes[name]?.identity?.component || meshes[name]?.component;
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
    variants: variantKeys.map((variant) => ({
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
  groupsUI.forEach((group) => recomputeAutomaticTextureBoundaries(group.itemObjs));
}

function createComponentDescriptor(groupName, names, meshes, src, modPath, options) {
  const texturePicker = options.texturePicker || null;
  const poolIds = new Set(names.map((name) => meshes[name].texture_pool_id).filter(Boolean));
  if (poolIds.size > 1) {
    throw new Error(`Component ${groupName} has multiple texture pools`);
  }
  const poolId = poolIds.values().next().value;
  const texturePool = poolId ? (options.texturePools || {})[poolId] || [] : [];
  const componentKind = names.map((n) => meshes[n].material_kind_override).find(Boolean) || null;
  const componentIdentity =
    names.map((n) => meshes[n].identity?.component || meshes[n].component).find(Boolean) || null;

  const itemObjs = [];
  const componentDescriptor = {
    type: 'component',
    component: groupName,
    source: src,
    meshes: itemObjs,
    texturePool,
    modPath,
  };
  const assetSummary = summarizeAssetBindings(
    names.map((name) => meshes[name]),
    options.assetResolution,
  );
  componentDescriptor.assetSummary = assetSummary;
  componentDescriptor.assetResolution = options.assetResolution || null;
  let materialKind = componentKind;
  let materialKindInFlight = false;
  const canPersist = options.readOnlySource !== true && options.canPersistMetadata !== false && !!modPath;
  const canEdit = options.readOnlySource !== true && !!componentIdentity && !!modPath;
  initializeComponentMeshEdit(
    componentDescriptor,
    options.meshEditReadOnly !== true && canEdit,
    options.onAllMeshChangesApplied,
  );
  const setMaterialKind = async (kind) => {
    if (!canEdit || materialKindInFlight) return false;
    if (!canPersist) {
      // Compressed mods cannot persist metadata; keep this choice in viewer state.
      materialKind = kind === 'auto' ? null : kind;
      itemObjs.forEach((mesh) => {
        mesh.userData.materialKindOverride = materialKind;
      });
      notifyMeshStateChanged(itemObjs);
      window.dispatchEvent(
        new CustomEvent('mod-viewer-inspector-refresh', {
          detail: { component: componentDescriptor, reason: 'material-kind' },
        }),
      );
      return true;
    }
    const previousKind = materialKind;
    materialKindInFlight = true;
    try {
      const result = await saveComponentMaterialKind(modPath, src, componentIdentity, kind);
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
      showTextureLoadError(value);
      return false;
    }
    mesh.userData.textureHighlightDisabled = false;
    setManualTexOverride(mesh, value, { notify: false });
    updateTextures();
    return true;
  };
  const getTextureOverride = (mesh) => ({
    value: mesh.userData.manualTexOverride,
    automatic: mesh.userData.manualTexOverride === undefined && !mesh.userData.textureHighlightDisabled,
    resolved: mesh.userData.resolvedTexKey || null,
  });
  const updateTextures = (reason = null) => {
    recomputeTextureRuns(itemObjs);
    notifyMeshStateChanged(itemObjs);
    if (reason)
      window.dispatchEvent(
        new CustomEvent('mod-viewer-inspector-refresh', {
          detail: { component: componentDescriptor, reason },
        }),
      );
    saveTextureState(modPath);
  };
  Object.assign(componentDescriptor, {
    getMaterialKind: () => itemObjs[0]?.userData.materialKindOverride || materialKind,
    setMaterialKind: canEdit ? setMaterialKind : undefined,
    openTextureManager: () =>
      openTextureModal(groupName, texturePool, modPath, () => updateTextures('pool'), texturePicker),
    getTextureOverride,
    setTextureOverride,
  });

  return componentDescriptor;
}

/** Build the panel for live meshes. */
export function buildMeshPanel(meshes, liveMeshes, modPath, options = {}) {
  return appendMeshPanel(meshes, liveMeshes, modPath, { ...options, replace: true });
}

export function appendMeshPanel(meshes, liveMeshes, modPath, options = {}) {
  const exitIsolation = document.getElementById('exit-isolation-btn');
  exitIsolation.hidden = !isMeshIsolationActive();
  exitIsolation.onclick = clearMeshIsolation;
  const list = document.getElementById('mesh-list');
  installPanelSelectionDrag(list);
  const replace = options.replace !== false;
  closeMeshContextMenu();
  if (replace) {
    resetMeshPanelEditing();
    clearMeshPanelRows(list);
    clearTextureRunGroups();
  }
  registerViewSync('mesh-panel', syncMeshPanel);

  const validNames = Object.keys(meshes).filter((name) => !meshes[name]?.error);
  const bySource = groupKeysBySource(meshes, validNames);
  const sources = Object.keys(bySource);
  const multiSource = usesSourceSections(bySource);

  for (const src of sources) {
    const container =
      multiSource && src
        ? buildSourceSection(src, list, {
            headerClass: 'mesh-src-hdr',
            itemsClass: 'mesh-src-items',
          })
        : list;
    const sourceHeader = container === list ? null : container.previousElementSibling;
    if (sourceHeader) {
      const button = buildGroupVisibilityButton(src, () => sourceMeshes(sourceHeader), 'source-visibility-btn');
      sourceHeader.insertBefore(button, sourceHeader.querySelector('.group-name'));
    }

    for (const [groupName, names] of Object.entries(groupByComponent(bySource[src], meshes))) {
      const itemsWrap = document.createElement('div');
      itemsWrap.className = 'group-items';
      itemsWrap.id = `mesh-group-${++meshSectionId}`;

      const itemCbs = [];
      const componentDescriptor = createComponentDescriptor(groupName, names, meshes, src, modPath, options);
      const itemObjs = componentDescriptor.meshes;
      const { hdr, visibilityButton, syncLabels, syncEditState } = buildGroupHeader(
        groupName,
        itemsWrap,
        componentDescriptor,
      );
      componentDescriptor.header = hdr;
      componentDescriptor.syncEditState = syncEditState;
      container.append(hdr, itemsWrap);

      for (const name of names) {
        const mesh = liveMeshes?.get?.(name) || liveMeshes?.[name];
        if (!mesh) throw new Error(`Missing live mesh for ${name}`);
        mesh.userData.componentDescriptor = componentDescriptor;
        itemObjs.push(mesh);
        const { wrap } = buildDrawRow(name, groupName, meshes[name], mesh, itemCbs);
        itemsWrap.appendChild(wrap);
        const inspectorRecord = {
          component: componentDescriptor,
          entry: meshes[name],
          label: mesh.userData.displayName || name,
        };
        registerInspectorMesh(mesh, inspectorRecord);
        meshPanelContexts.set(mesh, {
          name,
          groupName,
          entry: meshes[name],
          inspectorRecord,
          modPath,
          onToggleChange: options.onToggleChange,
        });
      }
      recomputeAutomaticTextureBoundaries(itemObjs);
      recomputeTextureRuns(itemObjs);
      registerTextureRunGroup(itemObjs);

      groupsUI.push({
        visibilityButton,
        itemCbs,
        itemObjs,
        componentDescriptor,
        header: componentDescriptor.header,
        itemsWrap,
        sourceContainer: container === list ? null : container,
        sourceHeader,
        syncLabels,
        assetFill: names.every((name) => meshes[name]?.asset_fill === true),
        assetResolution: options.assetResolution || null,
      });
    }
  }

  syncMeshPanel();
  if (isRecording()) showRecordingSourceRows();
  document.getElementById('camera-panel').style.display = 'none';
}

export function removeAssetFillMeshPanel(targetMeshes = null) {
  closeMeshContextMenu();
  const target = targetMeshes === null ? null : new Set(targetMeshes);
  const groups = groupsUI.filter(
    (group) => group.assetFill && (!target || group.itemObjs.some((mesh) => target.has(mesh))),
  );
  if (!groups.length) return [];
  clearSelection();
  const removed = [];
  for (const group of groups) {
    const members = target ? group.itemObjs.filter((mesh) => target.has(mesh)) : [...group.itemObjs];
    members.forEach((mesh) => {
      const rowWraps = new Set();
      const sourceWrap = meshRowWrap(mesh);
      if (sourceWrap) rowWraps.add(sourceWrap);
      getLooseParts(mesh).forEach((part) => {
        const partWrap = meshRowWrap(part);
        if (partWrap) rowWraps.add(partWrap);
      });
      rowWraps.forEach((rowWrap) => rowWrap.remove());
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
  groupsUI.splice(0, groupsUI.length, ...groupsUI.filter((group) => !group.assetFill || group.itemObjs.length));
  for (const source of new Set(groups.map((group) => group.sourceContainer).filter(Boolean))) {
    if (!source.children.length) {
      source.previousElementSibling?.remove();
      source.remove();
    }
  }
  window.dispatchEvent(
    new CustomEvent('mod-viewer-inspector-refresh', {
      detail: { component: null, reason: 'asset-fill-removed' },
    }),
  );
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
      group.itemObjs.map((mesh) => mesh.userData.assetEntry),
      group.assetResolution,
    );
    group.componentDescriptor.assetSummary = summary;
    window.dispatchEvent(
      new CustomEvent('mod-viewer-inspector-refresh', {
        detail: {
          component: group.componentDescriptor,
          reason: 'asset',
        },
      }),
    );
  }
}
