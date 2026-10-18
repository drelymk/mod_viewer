import { bridgeReady, reportPersistenceFailure } from '../app/bridge.js';
// MESHES lists draw calls grouped by source INI and component.

import { hasTexture } from '../mesh/mesh-factory.js';
import { textureFile } from '../textures/texture-key.js';
import {
  activeMeshes,
  applyMeshVisibility,
  setManualTexOverride,
  toggleManualMeshVisibility,
} from '../mesh/mesh-state.js';
import {
  clearTextureRunGroups,
  recomputeTextureRuns,
  registerTextureRunGroup,
  unregisterTextureRunGroup,
  saveTextureState,
} from '../mesh/mesh-texture-state.js';
import { bindMeshView, getMeshView } from '../mesh/mesh-view-bindings.js';
import { registerViewSync } from '../scene/view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from '../ui/panel-utils.js';
import {
  addMeshesToSelection,
  applyFaceSelection,
  beginFaceSelection,
  canEditMesh,
  cancelFaceSelection,
  clearSelection,
  getActiveMeshEditSource,
  getFaceSelection,
  getMeshEditSource,
  getSelectedMeshes,
  getPrimarySelectedMesh,
  invertFaceSelection,
  selectConnectedFaces,
  setSelectedFaceTriangles,
  setFaceSelectionThrough,
  isMeshSelected,
  selectMesh,
  selectMeshes,
  toggleMeshSelection,
} from '../scene/selection.js';
import { openTextureModal } from '../ui/texture-modal.js';
import { registerInspectorMesh } from './inspector-panel.js';
import { createIcon } from '../ui/ui-icons.js';
import { notifyMeshStateChanged } from '../mesh/mesh-state-events.js';
import { summarizeAssetBindings } from './asset-diagnostics.js';
import { isRecording, noteRecordMeshEdit } from '../editing/record-session.js';
import { openToggleModal } from '../editing/toggle-modal.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { requestRender } from '../scene/render-scheduler.js';
import { frameView, invalidateCharacterShadowVisibility } from '../scene/scene.js';
import { setRightDockTab } from './right-dock.js';
import { isRigTransformInteractionActive, isRigJointPickingActive } from '../scene/rig-overlay-state.js';
import { alertDialog, confirmDialog, rangeInputDialog } from '../ui/dialogs.js';
import {
  MAX_LOOSE_PART_TOLERANCE,
  canMergeLooseParts,
  getLoosePartSource,
  clearLooseParts,
  getLooseParts,
  isLoosePart,
  mergeLooseParts,
  separateLooseParts,
  snapshotLooseParts,
  restoreLooseParts,
} from '../mesh/loose-parts.js';

let groupsUI = [];
let meshSectionId = 0;
let meshContextMenu = null;
let meshContextActions = null;
let meshContextTarget = null;
let meshContextListenersInstalled = false;
let panelSelectionListenersInstalled = false;
let panelSelectionDrag = null;
let panelSelectionAnchor = null;
let suppressPanelClick = false;
const meshPanelContexts = new WeakMap();
const meshByRow = new WeakMap();
const PANEL_SELECTION_THRESHOLD = 5;
let previewHistory = { source: null, undo: [], redo: [] };
let restoringPreview = false;
let isolationSnapshot = null;

function notifyEditToolsChanged() {
  groupsUI.forEach((group) => group.componentDescriptor.syncEditState?.());
  window.dispatchEvent(new CustomEvent('mod-viewer-mesh-edit-tools-changed'));
}

function captureMeshPreview(source) {
  const parts = getLooseParts(source);
  const face = getFaceSelection();
  const indexOf = (mesh) => (mesh === source ? -1 : parts.indexOf(mesh));
  return {
    parts: snapshotLooseParts(source).map((state, index) => {
      const saved = isolationSnapshot?.get(parts[index]);
      return saved ? { ...state, ...saved } : state;
    }),
    selected: getSelectedMeshes()
      .filter((mesh) => mesh === source || parts.includes(mesh))
      .map(indexOf),
    primary: indexOf(getPrimarySelectedMesh()),
    face:
      face?.source === source
        ? { target: indexOf(face.target), triangles: [...face.triangles], through: face.selectThrough }
        : null,
  };
}

function rememberMeshPreview(source, snapshot = captureMeshPreview(source)) {
  if (restoringPreview || isRecording()) return;
  if (previewHistory.source !== source) previewHistory = { source, undo: [], redo: [] };
  previewHistory.undo.push(snapshot);
  if (previewHistory.undo.length > 30) previewHistory.undo.shift();
  previewHistory.redo = [];
  notifyEditToolsChanged();
}

function restoreMeshIsolation() {
  if (!isolationSnapshot) return;
  const snapshot = isolationSnapshot;
  isolationSnapshot = null;
  const live = new Set(activeMeshes.flatMap((source) => [source, ...getLooseParts(source)]));
  const changed = [];
  for (const [mesh, state] of snapshot) {
    if (!live.has(mesh)) continue;
    mesh.userData.manualVisible = state.visible;
    applyMeshVisibility(mesh, { notify: false, render: false });
    mesh.userData.manualVisible = state.manualVisible;
    mesh.userData.manuallyToggled = state.manuallyToggled;
    changed.push(mesh);
  }
  notifyMeshStateChanged(changed);
  notifyEditToolsChanged();
  requestRender();
}

function canIsolateSelectedMeshes() {
  return (
    !isRecording() &&
    !activeMeshes.some((mesh) => componentForMesh(mesh)?.meshEditApplying) &&
    (!!isolationSnapshot || getSelectedMeshes().length > 0)
  );
}

function isolateSelectedMeshes() {
  if (isolationSnapshot) return restoreMeshIsolation();
  const selected = new Set(getSelectedMeshes());
  if (!selected.size) return;
  isolationSnapshot = new Map();
  const changed = [];
  for (const source of activeMeshes) {
    const parts = getLooseParts(source);
    for (const mesh of [source, ...parts]) {
      isolationSnapshot.set(mesh, {
        visible: mesh.visible,
        manualVisible: mesh.userData.manualVisible,
        manuallyToggled: mesh.userData.manuallyToggled,
      });
      mesh.userData.manualVisible =
        selected.has(source) || selected.has(mesh) || (mesh === source && parts.some((part) => selected.has(part)));
      applyMeshVisibility(mesh, { notify: false, render: false });
      changed.push(mesh);
    }
  }
  notifyMeshStateChanged(changed);
  notifyEditToolsChanged();
  requestRender();
}

function canRestoreMeshPreview(direction, descriptor = componentForMesh(previewHistory.source)) {
  return (
    !!descriptor &&
    componentForMesh(previewHistory.source) === descriptor &&
    groupsUI.some(
      (group) => group.componentDescriptor === descriptor && group.itemObjs.includes(previewHistory.source),
    ) &&
    (!getActiveMeshEditSource() || getActiveMeshEditSource() === previewHistory.source) &&
    previewHistory[direction].length > 0 &&
    descriptor.meshEditWritable &&
    !descriptor.meshEditApplying &&
    descriptor.meshEditState !== 'applied' &&
    !isRecording() &&
    !document.body.classList.contains('feature-edit-mesh-off')
  );
}

function restoreMeshPreview(direction) {
  const source = previewHistory.source;
  const descriptor = componentForMesh(source);
  const stack = previewHistory[direction];
  if (!canRestoreMeshPreview(direction)) return false;
  closeMeshContextMenu();
  restoreMeshIsolation();
  const snapshot = stack.pop();
  previewHistory[direction === 'undo' ? 'redo' : 'undo'].push(captureMeshPreview(source));
  restoringPreview = true;
  try {
    const oldRows = connectedPartWraps(source);
    const sourceRow = meshRowWrap(source);
    cancelFaceSelection();
    clearSelection();
    restoreLooseParts(source, snapshot.parts);
    const parts = getLooseParts(source);
    if (parts.length) {
      const anchor = oldRows[0] || sourceRow;
      anchor?.replaceWith(...buildPartRows(source, meshPanelContexts.get(source)));
      oldRows.slice(1).forEach((row) => row.remove());
    } else if (oldRows.length) restoreSourceRow(source, oldRows);
    const meshAt = (index) => (index === -1 ? source : parts[index]);
    setComponentMeshEditState(descriptor, parts.length ? 'edited' : 'clean');
    if (snapshot.face) {
      beginFaceSelection(meshAt(snapshot.face.target));
      setSelectedFaceTriangles(snapshot.face.triangles);
      setFaceSelectionThrough(snapshot.face.through);
    }
    selectMeshes(snapshot.selected.map(meshAt), meshAt(snapshot.primary));
    invalidateCharacterShadowVisibility({ request: false });
    notifyEditToolsChanged();
    requestRender();
    return true;
  } finally {
    restoringPreview = false;
  }
}

window.addEventListener('mod-viewer-mesh-edit-before-change', ({ detail }) => rememberMeshPreview(detail.source));
window.addEventListener('mod-viewer-face-selection-changed', notifyEditToolsChanged);
document.addEventListener('keydown', (event) => {
  const direction = { z: 'undo', y: 'redo' }[event.key.toLowerCase()];
  if (
    !direction ||
    !event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    event.metaKey ||
    event.repeat ||
    event.isComposing ||
    event.defaultPrevented ||
    isRigTransformInteractionActive() ||
    isRigJointPickingActive() ||
    document.querySelector('.modal-backdrop.show, .dialog-backdrop.show')
  )
    return;
  const target = event.target;
  if (
    target instanceof Element &&
    (target.isContentEditable || target.closest('input, textarea, select, .ace_editor, [role="textbox"]'))
  )
    return;
  if (!canRestoreMeshPreview(direction)) return;
  event.preventDefault();
  restoreMeshPreview(direction);
});

function showTextureLoadError(key) {
  alertDialog(t('texture.loadFailed', { file: textureFile(key) || t('texture.unknown') }));
}

window.addEventListener('mod-viewer-texture-load-error', ({ detail }) => {
  const key = detail?.key;
  if (!key) return;
  if (
    groupsUI.some((group) =>
      group.itemObjs.some((mesh) => mesh.userData.manualTexOverride === key && mesh.userData.texKey === key),
    )
  )
    showTextureLoadError(key);
});

function meshRowWrap(mesh) {
  return getMeshView(mesh)?.row?.closest('.draw-item-wrap') || null;
}

function meshRowLabel(mesh, context) {
  const label = getMeshView(mesh)?.row?.querySelector('.mesh-name')?.textContent.trim();
  if (label) return label;
  if (mesh.userData.displayName) return mesh.userData.displayName;
  if (context?.entry?.drawindexed) return context.entry.drawindexed.join(', ');
  return mesh.name || 'Mesh';
}

function saveComponentMaterialKind(modPath, source, component, kind) {
  if (!modPath) {
    return Promise.resolve({ saved: false });
  }
  return bridgeReady().then((api) => api.save_component_material_kind(modPath, source, component, kind));
}

function closeMeshContextMenu() {
  if (!meshContextMenu) return;
  meshContextMenu.hidden = true;
  meshContextTarget = null;
}

function createToggleSelection(anchor) {
  if (isRecording() || getFaceSelection()) return null;
  const context = meshPanelContexts.get(anchor);
  if (!context?.modPath) return null;
  const selected = getSelectedMeshes();
  if (!selected.length || !selected.includes(anchor)) return null;
  let ini = null;
  const targets = new Map();
  for (const mesh of selected) {
    const item = meshPanelContexts.get(mesh);
    if (
      !item ||
      item.modPath !== context.modPath ||
      isLoosePart(mesh) ||
      mesh.userData.assetFill ||
      mesh.userData.componentDescriptor?.meshEditApplying ||
      mesh.userData.componentDescriptor?.meshEditState === 'edited' ||
      getLooseParts(mesh).length
    )
      return null;
    const sources = mesh.userData.sources || [];
    const drawindexed = mesh.userData.assetEntry?.drawindexed;
    if (
      !sources.length ||
      !Array.isArray(drawindexed) ||
      drawindexed.length !== 3 ||
      !drawindexed.every(Number.isInteger)
    )
      return null;
    for (const source of sources) {
      if (
        !source.ini ||
        !source.section ||
        !source.occurrence ||
        !Number.isInteger(source.line) ||
        source.line < 1 ||
        !Number.isInteger(source.occurrence.ordinal) ||
        source.occurrence.ordinal < 0 ||
        source.occurrence.section !== source.section
      )
        return null;
      // A merged mesh must have every authored contribution in the edited INI.
      if (ini !== null && source.ini !== ini) return null;
      if (source.occurrence.path?.length) return null;
      ini = source.ini;
      const ref = {
        ini,
        line: source.line,
        section: source.section,
        drawindexed,
        occurrence: source.occurrence,
      };
      targets.set(JSON.stringify(ref), ref);
    }
  }
  return { ini, name: anchor.userData.displayName || context.name, targets: [...targets.values()] };
}

function positionMeshContextMenu(menu, event) {
  const container = document.getElementById('canvas-container') || document.body;
  const bounds = container.getBoundingClientRect();
  const left = Number(event.clientX);
  const top = Number(event.clientY);
  menu.style.left = `${Math.max(4, left)}px`;
  menu.style.top = `${Math.max(bounds.top + 4, top)}px`;
  requestAnimationFrame(() => {
    if (menu.hidden) return;
    const maxLeft = Math.max(4, window.innerWidth - menu.offsetWidth - 4);
    const maxTop = Math.max(bounds.top + 4, Math.min(window.innerHeight, bounds.bottom) - menu.offsetHeight - 4);
    menu.style.left = `${Math.min(Math.max(4, left), maxLeft)}px`;
    menu.style.top = `${Math.min(Math.max(bounds.top + 4, top), maxTop)}px`;
  });
}

function panelRowsInRange(list, startY, currentY) {
  const top = Math.min(startY, currentY);
  const bottom = Math.max(startY, currentY);
  const meshes = [];
  list.querySelectorAll('.draw-item').forEach((row) => {
    if (!row.isConnected) return;
    const rect = row.getBoundingClientRect();
    if (rect.height <= 0 || rect.bottom < top || rect.top > bottom) return;
    const mesh = meshByRow.get(row);
    if (mesh) meshes.push(mesh);
  });
  return meshes;
}

function isPanelSelectionInteractiveTarget(target) {
  return target instanceof Element && !!target.closest('button, input, select, textarea, a, [contenteditable="true"]');
}

function onPanelPointerDown(event) {
  if (event.button !== 0 || event.defaultPrevented || isPanelSelectionInteractiveTarget(event.target)) return;
  if (event.shiftKey) {
    event.preventDefault();
    return;
  }
  if (!event.ctrlKey) return;
  const row = event.target.closest?.('.draw-item');
  if (!row || !row.isConnected) return;
  panelSelectionDrag = {
    pointerId: event.pointerId,
    startY: event.clientY,
    list: event.currentTarget,
    dragging: false,
  };
}

function onPanelPointerMove(event) {
  const gesture = panelSelectionDrag;
  if (!gesture || event.pointerId !== gesture.pointerId) return;
  if (!gesture.dragging && Math.abs(event.clientY - gesture.startY) <= PANEL_SELECTION_THRESHOLD) {
    return;
  }
  gesture.dragging = true;
  event.preventDefault();
  event.stopPropagation();
  addMeshesToSelection(panelRowsInRange(gesture.list, gesture.startY, event.clientY));
}

function finishPanelPointerGesture(event) {
  const gesture = panelSelectionDrag;
  if (!gesture || event.pointerId !== gesture.pointerId) return;
  panelSelectionDrag = null;
  if (!gesture.dragging) return;
  event.preventDefault();
  event.stopPropagation();
  suppressPanelClick = true;
  window.setTimeout(() => {
    suppressPanelClick = false;
  }, 0);
}

function onPanelClickCapture(event) {
  if (!suppressPanelClick) return;
  suppressPanelClick = false;
  event.preventDefault();
  event.stopImmediatePropagation();
}

function installPanelSelectionDrag(list) {
  if (panelSelectionListenersInstalled) return;
  list.addEventListener('pointerdown', onPanelPointerDown);
  list.addEventListener('click', onPanelClickCapture, true);
  document.addEventListener('pointermove', onPanelPointerMove, true);
  document.addEventListener('pointerup', finishPanelPointerGesture, true);
  document.addEventListener('pointercancel', finishPanelPointerGesture, true);
  panelSelectionListenersInstalled = true;
}

function selectPanelRow(event, mesh) {
  if (isPanelSelectionInteractiveTarget(event.target)) return;
  if (event.shiftKey) {
    event.preventDefault();
    const rows = [...event.currentTarget.closest('#mesh-list').querySelectorAll('.draw-item')].filter(
      (row) => row.getBoundingClientRect().height > 0,
    );
    const anchor = isMeshSelected(panelSelectionAnchor) ? panelSelectionAnchor : getSelectedMeshes().at(-1);
    const start = rows.findIndex((row) => meshByRow.get(row) === anchor);
    const end = rows.indexOf(event.currentTarget);
    if (start !== -1 && end !== -1) {
      const range = rows.slice(Math.min(start, end), Math.max(start, end) + 1).map((row) => meshByRow.get(row));
      panelSelectionAnchor = anchor;
      selectMeshes(event.ctrlKey ? [...getSelectedMeshes(), ...range] : range, mesh);
      return;
    }
  }
  panelSelectionAnchor = mesh;
  if (event.ctrlKey) toggleMeshSelection(mesh);
  else selectMesh(mesh);
}

function ensureMeshContextMenu() {
  if (meshContextMenu) return meshContextMenu;
  meshContextMenu = document.createElement('div');
  meshContextMenu.className = 'mesh-context-menu';
  meshContextMenu.setAttribute('role', 'menu');
  meshContextMenu.hidden = true;
  meshContextActions = {};
  const addAction = (name, key, className = 'mesh-edit-context-action') => {
    const action = document.createElement('button');
    action.type = 'button';
    action.className = className;
    action.setAttribute('role', 'menuitem');
    action.dataset.i18n = key;
    action.textContent = t(key);
    meshContextActions[name] = action;
    meshContextMenu.appendChild(action);
  };
  addAction('createToggle', 'mesh.createToggle', 'mesh-create-toggle-action');
  addAction('rename', 'mesh.rename', 'mesh-rename-action');
  addAction('isolate', 'mesh.isolateSelected', 'mesh-isolate-action');
  addAction('separate', 'mesh.separateLooseParts');
  addAction('separateSelection', 'mesh.separateBySelection');
  addAction('merge', 'mesh.mergeLooseParts');
  addAction('applySelection', 'mesh.applySelection');
  addAction('cancelSelection', 'mesh.cancelSelection');
  addAction('apply', 'mesh.applyMeshChanges');
  addAction('cancel', 'mesh.cancelMeshChanges');
  document.body.appendChild(meshContextMenu);
  meshContextActions.rename.addEventListener('click', () => {
    const mesh = meshContextTarget;
    closeMeshContextMenu();
    if (mesh) renameMesh(mesh);
  });
  meshContextActions.isolate.addEventListener('click', () => {
    closeMeshContextMenu();
    if (canIsolateSelectedMeshes()) isolateSelectedMeshes();
  });
  meshContextActions.createToggle.addEventListener('click', () => {
    const anchor = meshContextTarget;
    const quickCreate = createToggleSelection(anchor);
    const context = meshPanelContexts.get(anchor);
    closeMeshContextMenu();
    if (quickCreate)
      void openToggleModal({
        mode: 'add',
        modPath: context.modPath,
        quickCreate,
        onSaved: context.onToggleChange,
      });
  });
  meshContextActions.separate.addEventListener('click', () => {
    const source = meshContextTarget;
    closeMeshContextMenu();
    if (source && !isLoosePart(source)) void separateMeshRow(source);
  });
  meshContextActions.separateSelection.addEventListener('click', () => {
    const target = meshContextTarget;
    closeMeshContextMenu();
    if (target) beginFaceSelection(target);
  });
  meshContextActions.merge.addEventListener('click', () => {
    closeMeshContextMenu();
    void mergeSelectedLooseParts();
  });
  meshContextActions.applySelection.addEventListener('click', () => {
    closeMeshContextMenu();
    applyCurrentFaceSelection();
  });
  meshContextActions.cancelSelection.addEventListener('click', () => {
    closeMeshContextMenu();
    cancelFaceSelection();
  });
  meshContextActions.apply.addEventListener('click', () => {
    const descriptor = meshContextTarget;
    closeMeshContextMenu();
    void descriptor?.applyMeshChanges?.();
  });
  meshContextActions.cancel.addEventListener('click', () => {
    const descriptor = meshContextTarget;
    closeMeshContextMenu();
    void cancelComponentMeshChanges(descriptor);
  });
  if (!meshContextListenersInstalled) {
    document.addEventListener('pointerdown', (event) => {
      if (!meshContextMenu?.contains(event.target)) closeMeshContextMenu();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeMeshContextMenu();
    });
    meshContextListenersInstalled = true;
  }
  return meshContextMenu;
}

function openMeshContextMenu(event, mesh) {
  if (isRecording()) {
    event.preventDefault();
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  if (!isMeshSelected(mesh)) selectMesh(mesh);
  const menu = ensureMeshContextMenu();
  meshContextTarget = mesh;
  const actions = meshContextActions;
  for (const [id, action] of Object.entries(actions)) {
    action.hidden = !['rename', 'createToggle'].includes(id);
    action.disabled = false;
    action.title = '';
  }
  actions.createToggle.hidden = !createToggleSelection(mesh);
  actions.isolate.hidden = false;
  actions.isolate.disabled = !canIsolateSelectedMeshes();
  actions.isolate.dataset.i18n = isolationSnapshot ? 'mesh.restoreVisibility' : 'mesh.isolateSelected';
  actions.isolate.textContent = t(actions.isolate.dataset.i18n);
  for (const state of meshEditActions(mesh, componentForMesh(getMeshEditSource(mesh)))) {
    if (['apply', 'cancel'].includes(state.id)) continue;
    const action = actions[state.id];
    action.hidden = false;
    action.disabled = state.disabled;
    action.title = state.reason ? t(state.reason) : '';
  }
  if (canMergeLooseParts(getSelectedMeshes())) actions.separateSelection.hidden = true;
  showMeshContextMenuIfActionsVisible(menu, event);
}

window.addEventListener('mod-viewer-mesh-contextmenu', (event) => {
  const detail = event.detail;
  if (!detail?.mesh) return;
  openMeshContextMenu(
    {
      clientX: detail.clientX,
      clientY: detail.clientY,
      preventDefault() {},
      stopPropagation() {},
    },
    detail.mesh,
  );
});

function openComponentContextMenu(event, descriptor) {
  if (isRecording()) {
    event.preventDefault();
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  const menu = ensureMeshContextMenu();
  meshContextTarget = descriptor;
  const actions = meshContextActions;
  for (const action of Object.values(actions)) {
    action.disabled = false;
    action.title = '';
  }
  actions.rename.hidden = true;
  actions.isolate.hidden = true;
  actions.createToggle.hidden = true;
  actions.separate.hidden = true;
  actions.separateSelection.hidden = true;
  actions.merge.hidden = true;
  actions.applySelection.hidden = true;
  actions.cancelSelection.hidden = true;
  const faceSelection = getFaceSelection();
  const faceSelectionOwnsComponent = faceSelection && componentForMesh(faceSelection.source) === descriptor;
  const edited = descriptor.meshEditState === 'edited';
  actions.apply.hidden = !edited;
  actions.cancel.hidden = !edited;
  const disabled = descriptor.meshEditApplying === true || !descriptor.meshEditWritable || faceSelectionOwnsComponent;
  actions.apply.disabled = disabled;
  actions.cancel.disabled = disabled;
  const reason = descriptor.meshEditApplying
    ? t('mesh.reason.applyingChanges')
    : !descriptor.meshEditWritable
      ? t('mesh.reason.cannotWriteChanges')
      : faceSelectionOwnsComponent
        ? t('mesh.reason.finishCurrentEdit')
        : '';
  actions.apply.title = reason;
  actions.cancel.title = reason;
  showMeshContextMenuIfActionsVisible(menu, event);
}

function showMeshContextMenuIfActionsVisible(menu, event) {
  const hasVisibleAction = Array.from(menu.querySelectorAll('button, [role="menuitem"]')).some(
    (action) =>
      !action.hidden && getComputedStyle(action).display !== 'none' && getComputedStyle(action).visibility !== 'hidden',
  );
  if (!hasVisibleAction) {
    menu.hidden = true;
    return;
  }
  menu.hidden = false;
  positionMeshContextMenu(menu, event);
}

function syncMeshPanel(changedMeshes = null) {
  const changed = changedMeshes && new Set(changedMeshes);
  for (const group of groupsUI) {
    if (
      changed &&
      !group.itemObjs.some((mesh) => changed.has(mesh) || getLooseParts(mesh).some((part) => changed.has(part)))
    )
      continue;
    group.itemObjs.forEach((mesh, index) => {
      group.itemCbs[index].checked = mesh.visible;
      const binding = getMeshView(mesh);
      binding?.syncStateIndicator?.();
      binding?.syncTextureSelection?.();
      getLooseParts(mesh).forEach((part) => getMeshView(part)?.syncStateIndicator?.());
    });
    syncGroupVisibilityButton(group.visibilityButton, group.itemObjs);
  }
  for (const header of new Set(groupsUI.map((group) => group.sourceHeader).filter(Boolean))) {
    const button = header.querySelector('.source-visibility-btn');
    if (button) syncGroupVisibilityButton(button, sourceMeshes(header));
  }
}

function sourceMeshes(header) {
  return groupsUI.filter((group) => group.sourceHeader === header).flatMap((group) => group.itemObjs);
}

function visibilityTargets(meshes) {
  return meshes.flatMap((mesh) => {
    const parts = getLooseParts(mesh);
    return !isRecording() && parts.length ? parts : [mesh];
  });
}

function syncGroupVisibilityButton(button, meshes) {
  const targets = visibilityTargets(meshes);
  const any = targets.some((mesh) => mesh.visible);
  const all = targets.length > 0 && targets.every((mesh) => mesh.visible);
  button.classList.toggle('state-hidden', !any);
  button.classList.toggle(
    'state-manual',
    targets.some((mesh) => mesh.userData.manuallyToggled),
  );
  button.setAttribute('aria-pressed', any && !all ? 'mixed' : String(all));
  button.title = t(all ? 'mesh.hideGroup' : 'mesh.showGroup', { name: button.dataset.visibilityName });
  button.setAttribute('aria-label', button.title);
}

function buildGroupVisibilityButton(name, getMeshes, className) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `mesh-state-btn ${className}`;
  button.dataset.visibilityName = name;
  button.appendChild(createIcon('visibility'));
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    const targets = visibilityTargets(getMeshes());
    const visible = !targets.every((mesh) => mesh.visible);
    for (const mesh of targets) {
      if (mesh.visible !== visible) toggleManualMeshVisibility(mesh, { notify: false, render: false });
      if (!isLoosePart(mesh)) noteRecordMeshEdit(mesh);
    }
    notifyMeshStateChanged(targets);
    requestRender();
  });
  button.addEventListener('dblclick', (event) => event.stopPropagation());
  syncGroupVisibilityButton(button, getMeshes());
  return button;
}

window.addEventListener('mod-viewer-mesh-state-changed', (event) => {
  if (event.detail?.meshes?.length) syncMeshPanel(event.detail.meshes);
});

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

function buildGroupHeader(groupName, itemsWrap, onComponentSelected = null, componentDescriptor = null) {
  const hdr = document.createElement('div');
  hdr.className = 'group-hdr';

  const chevron = document.createElement('button');
  chevron.type = 'button';
  chevron.className = 'group-toggle';
  chevron.setAttribute('aria-expanded', 'true');
  const syncLabels = () => {
    const collapsed = itemsWrap.classList.contains('collapsed');
    chevron.setAttribute(
      'aria-label',
      t(collapsed ? 'mesh.expandComponent' : 'mesh.collapseComponent', { name: groupName }),
    );
  };
  syncLabels();
  chevron.setAttribute('aria-controls', itemsWrap.id);
  chevron.appendChild(createIcon('chevron-down'));

  const visibilityButton = buildGroupVisibilityButton(
    groupName,
    () => componentDescriptor?.meshes || [],
    'component-visibility-btn',
  );

  const nameSpan = document.createElement('span');
  nameSpan.className = 'group-name';
  nameSpan.textContent = groupName;
  nameSpan.title = groupName;

  hdr.append(chevron, visibilityButton, nameSpan);
  const editBadge = document.createElement('span');
  editBadge.className = 'mesh-edit-badge';
  editBadge.hidden = true;
  editBadge.textContent = t('toolbar.edited');
  const historyControls = document.createElement('span');
  historyControls.className = 'mesh-history-actions';
  const historyButtons = ['undo', 'redo'].map((direction) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'icon-btn mesh-history-btn';
    button.dataset.meshEditAction = direction;
    button.setAttribute('aria-keyshortcuts', direction === 'undo' ? 'Control+Z' : 'Control+Y');
    button.appendChild(createIcon(direction));
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      if (canRestoreMeshPreview(direction, componentDescriptor)) restoreMeshPreview(direction);
    });
    button.addEventListener('dblclick', (event) => event.stopPropagation());
    historyControls.appendChild(button);
    return button;
  });
  const syncEditState = () => {
    const state = componentDescriptor?.meshEditState || 'clean';
    editBadge.hidden = state !== 'edited';
    editBadge.dataset.state = state;
    editBadge.title = t('toolbar.edited');
    editBadge.textContent = t('toolbar.edited');
    const ownsHistory = componentForMesh(previewHistory.source) === componentDescriptor;
    historyControls.hidden =
      state !== 'edited' && !(ownsHistory && (previewHistory.undo.length || previewHistory.redo.length));
    historyButtons.forEach((button) => {
      const direction = button.dataset.meshEditAction;
      button.disabled = !canRestoreMeshPreview(direction, componentDescriptor);
      button.setAttribute('aria-label', t(`mesh.${direction}`));
      button.title = `${t(`mesh.${direction}`)} (Ctrl+${direction === 'undo' ? 'Z' : 'Y'})`;
    });
  };
  hdr.append(editBadge, historyControls);
  nameSpan.addEventListener('click', (event) => {
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
  chevron.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleItems();
  });
  hdr.addEventListener('click', (e) => {
    if (e.target.closest('button')) return;
    onComponentSelected?.();
  });
  if (componentDescriptor) {
    hdr.addEventListener('contextmenu', (event) => openComponentContextMenu(event, componentDescriptor));
  }

  syncEditState();
  return { hdr, visibilityButton, syncLabels, syncEditState };
}

function renameMesh(mesh) {
  const labelSpan = getMeshView(mesh)?.row?.querySelector('.mesh-name');
  if (!labelSpan || labelSpan.querySelector('input')) return;
  const loosePart = isLoosePart(mesh);
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
    } else {
      mesh.userData.displayName = next;
      mesh.userData.meshNames[mesh.userData.metadataKey] = next;
      if (mesh.userData.modPath) {
        const path = mesh.userData.modPath;
        const names = { ...mesh.userData.meshNames };
        void bridgeReady()
          .then((api) => api.save_mesh_names(path, names))
          .then((result) => {
            if (result?.error) throw new Error(result.error);
          })
          .catch(reportPersistenceFailure);
      }
    }
    window.dispatchEvent(
      new CustomEvent('mod-viewer-inspector-refresh', { detail: { component: componentForMesh(mesh) } }),
    );
  };

  input.addEventListener('click', (event) => event.stopPropagation());
  input.addEventListener('dblclick', (event) => event.stopPropagation());
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
}

/** Display authored drawindexed arguments, or the synthetic number if absent. */
function buildDrawRow(
  name,
  groupName,
  entry,
  mesh,
  itemCbs,
  { labelOverride = null, onContextMenu = null, includeInGroup = true } = {},
) {
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
    if (!loosePart) noteRecordMeshEdit(mesh);
    toggleManualMeshVisibility(mesh);
  });
  cb.addEventListener('dblclick', (event) => event.stopPropagation());
  if (!loosePart && includeInGroup) itemCbs.push(cb);

  const label = entry.drawindexed ? entry.drawindexed.join(', ') : '#' + name.slice(groupName.length + 1);
  const labelSpan = document.createElement('span');
  labelSpan.className = 'mesh-name';
  const displayLabel = labelOverride || mesh.userData.loosePartLabel || mesh.userData.displayName || label;
  labelSpan.textContent = displayLabel;
  row.append(cb, labelSpan);
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
  bindMeshView(mesh, {
    row,
    stateButton: cb,
    syncStateIndicator: () => updateStateIndicator(mesh),
  });
  meshByRow.set(row, mesh);
  row.addEventListener('click', (event) => selectPanelRow(event, mesh));
  row.addEventListener('dblclick', (event) => {
    if (isPanelSelectionInteractiveTarget(event.target) || getFaceSelection()) return;
    selectMesh(mesh);
    frameView([mesh]);
    setRightDockTab('inspector', { userInitiated: true });
  });
  if (onContextMenu) {
    row.addEventListener('contextmenu', (event) => onContextMenu(event, mesh));
  }

  const wrap = document.createElement('div');
  wrap.className = 'draw-item-wrap';
  wrap.append(row);
  return { wrap, cb };
}

function buildPartRows(source, context) {
  return getLooseParts(source).map((part, index) => {
    const { wrap } = buildDrawRow(
      `${context.name}::loose-part-${index + 1}`,
      context.groupName,
      context.entry,
      part,
      [],
      {
        labelOverride: part.userData.loosePartLabel,
        includeInGroup: false,
        onContextMenu: isRecording() ? null : openMeshContextMenu,
      },
    );
    registerInspectorMesh(part, context.inspectorRecord);
    return wrap;
  });
}

function connectedPartWraps(source) {
  return getLooseParts(source)
    .map((part) => getMeshView(part)?.row?.closest('.draw-item-wrap'))
    .filter(Boolean);
}

function showLoosePartRows(source) {
  const context = meshPanelContexts.get(source);
  const sourceRow = getMeshView(source)?.row;
  const sourceWrap = sourceRow?.closest('.draw-item-wrap');
  if (!context || !sourceWrap?.isConnected) return false;
  sourceWrap.replaceWith(...buildPartRows(source, context));
  return true;
}

function restoreSourceRow(source, oldPartWraps = connectedPartWraps(source)) {
  const context = meshPanelContexts.get(source);
  const group = groupsUI.find((candidate) => candidate.itemObjs.includes(source));
  const sourceIndex = group?.itemObjs.indexOf(source) ?? -1;
  if (!context || !group || sourceIndex < 0 || !oldPartWraps.length) return false;
  const { wrap, cb } = buildDrawRow(context.name, context.groupName, context.entry, source, group.itemCbs, {
    includeInGroup: false,
    onContextMenu: isRecording() ? null : openMeshContextMenu,
  });
  group.itemCbs[sourceIndex] = cb;
  oldPartWraps[0].replaceWith(wrap);
  oldPartWraps.slice(1).forEach((partWrap) => partWrap.remove());
  return true;
}

function showRecordingSourceRows() {
  closeMeshContextMenu();
  let selectionCleared = false;
  let partsChanged = false;
  for (const group of groupsUI) {
    group.itemObjs.forEach((source) => {
      const context = meshPanelContexts.get(source);
      const partWraps = connectedPartWraps(source);
      if (!context || !partWraps.length) return;
      for (const part of getLooseParts(source)) {
        if (part.visible) continue;
        part.visible = true;
        partsChanged = true;
      }
      if (!selectionCleared) {
        clearSelection();
        selectionCleared = true;
      }
      restoreSourceRow(source, partWraps);
    });
  }
  if (partsChanged) invalidateCharacterShadowVisibility({ request: false });
  requestRender();
}

function restoreLoosePartRows() {
  let selectionCleared = false;
  for (const group of groupsUI) {
    group.itemObjs.forEach((source) => {
      const parts = getLooseParts(source);
      if (!parts.length) return;
      if (!selectionCleared) {
        clearSelection();
        selectionCleared = true;
      }
      parts.forEach((part) => {
        applyMeshVisibility(part, { notify: false, render: false });
      });
      showLoosePartRows(source);
    });
  }
  requestRender();
}

function componentForMesh(mesh) {
  return mesh?.userData?.componentDescriptor || null;
}

function setComponentMeshEditState(descriptor, state) {
  if (!descriptor || descriptor.meshEditState === state) return;
  descriptor.meshEditState = state;
  descriptor.syncEditState?.();
  window.dispatchEvent(
    new CustomEvent('mod-viewer-mesh-edit-state', {
      detail: { component: descriptor, state },
    }),
  );
}

function markComponentMeshEdited(descriptor) {
  if (descriptor?.meshEditState !== 'applied') {
    setComponentMeshEditState(descriptor, 'edited');
  }
}

export function hasUnappliedMeshChanges() {
  return groupsUI.some((group) => group.componentDescriptor?.meshEditState === 'edited');
}

async function applyComponentMeshChanges(descriptor) {
  if (!descriptor || descriptor.meshEditState !== 'edited' || !descriptor.meshEditWritable || !descriptor.modPath)
    return false;
  const activeSource = getActiveMeshEditSource();
  const context = meshPanelContexts.get(activeSource);
  const faceSelection = getFaceSelection();
  if (
    !activeSource ||
    componentForMesh(activeSource) !== descriptor ||
    !context ||
    !getLooseParts(activeSource).length ||
    (faceSelection && componentForMesh(faceSelection.source) === descriptor)
  ) {
    return false;
  }
  if (!(await confirmDialog(t('mesh.applyMeshChangesConfirm')))) return false;
  if (
    descriptor.meshEditApplying ||
    descriptor.meshEditState !== 'edited' ||
    getActiveMeshEditSource() !== activeSource ||
    !getLooseParts(activeSource).length
  )
    return false;
  const request = {
    component: descriptor.component,
    mesh: {
      key: context.entry.identity?.key,
      sources: context.entry.sources || [],
      parts: getLooseParts(activeSource).map((part) => [...(part.userData.loosePartTriangles || [])]),
    },
  };
  descriptor.meshEditApplying = true;
  restoreMeshIsolation();
  notifyEditToolsChanged();
  try {
    const result = await window.pywebview.api.apply_component_mesh_changes(descriptor.modPath, request);
    if (!result || result.error || result.ok === false) {
      throw new Error(result?.error || t('mesh.applyMeshChangesHint'));
    }
    setComponentMeshEditState(descriptor, 'applied');
    await descriptor.onAllMeshChangesApplied?.();
    const updated = groupsUI.find(
      (group) =>
        group.componentDescriptor.modPath === descriptor.modPath &&
        group.componentDescriptor.source === descriptor.source &&
        group.componentDescriptor.component === descriptor.component,
    );
    if (updated?.itemObjs.length) selectMesh(updated.itemObjs[0]);
    return true;
  } catch (error) {
    await alertDialog(String(error?.message || error));
    return false;
  } finally {
    descriptor.meshEditApplying = false;
    notifyEditToolsChanged();
  }
}

async function cancelComponentMeshChanges(descriptor) {
  if (
    !descriptor ||
    descriptor.meshEditState !== 'edited' ||
    !descriptor.meshEditWritable ||
    descriptor.meshEditApplying
  )
    return false;
  const activeSource = getActiveMeshEditSource();
  const faceSelection = getFaceSelection();
  if (
    !activeSource ||
    componentForMesh(activeSource) !== descriptor ||
    (faceSelection && componentForMesh(faceSelection.source) === descriptor) ||
    !getLooseParts(activeSource).length
  )
    return false;
  if (!(await confirmDialog(t('mesh.cancelMeshChangesConfirm')))) return false;
  if (
    descriptor.meshEditApplying ||
    descriptor.meshEditState !== 'edited' ||
    getActiveMeshEditSource() !== activeSource
  )
    return false;
  restoreMeshIsolation();
  previewHistory = { source: null, undo: [], redo: [] };
  const oldPartWraps = connectedPartWraps(activeSource);
  clearSelection();
  clearLooseParts(activeSource);
  if (!restoreSourceRow(activeSource, oldPartWraps)) return false;
  setComponentMeshEditState(descriptor, 'clean');
  selectMesh(activeSource);
  invalidateCharacterShadowVisibility({ request: false });
  requestRender();
  return true;
}

async function separateMeshRow(source) {
  if (isRecording()) return false;
  const descriptor = componentForMesh(source);
  if (
    descriptor?.meshEditState === 'applied' ||
    !canEditMesh(source) ||
    descriptor?.meshEditWritable === false ||
    descriptor?.meshEditApplying
  ) {
    return false;
  }
  const context = meshPanelContexts.get(source);
  const sourceRow = getMeshView(source)?.row;
  const sourceWrap = sourceRow?.closest('.draw-item-wrap');
  if (!context || !sourceWrap?.isConnected) return false;
  const tolerance = await rangeInputDialog(t('mesh.separateLooseParts'), {
    label: t('mesh.connectionTolerance'),
    rangeText: t('mesh.connectionToleranceRange'),
    value: 0,
    min: 0,
    max: MAX_LOOSE_PART_TOLERANCE,
    step: 0.0001,
    okKey: 'mesh.separate',
  });
  if (tolerance === null || isRecording()) return false;
  if (!canEditMesh(source) || descriptor?.meshEditApplying) return false;
  restoreMeshIsolation();
  const previous = captureMeshPreview(source);
  const parts = separateLooseParts(source, {
    label: meshRowLabel(source, context),
    tolerance,
  });
  if (parts.length <= 1) return false;
  if (getActiveMeshEditSource() && getActiveMeshEditSource() !== source) {
    clearLooseParts(source);
    return false;
  }
  rememberMeshPreview(source, previous);
  sourceWrap.replaceWith(...buildPartRows(source, context));
  markComponentMeshEdited(descriptor);
  selectMesh(parts[0]);
  requestRender();
  return true;
}

function applyCurrentFaceSelection() {
  const state = getFaceSelection();
  if (!state) return false;
  restoreMeshIsolation();
  const context = meshPanelContexts.get(state.source);
  if (!context) return false;
  const oldPartWraps = connectedPartWraps(state.source);
  const sourceWrap = meshRowWrap(state.source);
  const result = applyFaceSelection({ label: meshRowLabel(state.source, context) });
  if (!result) return false;
  if (oldPartWraps.length) {
    oldPartWraps[0].replaceWith(...buildPartRows(state.source, context));
    oldPartWraps.slice(1).forEach((partWrap) => partWrap.remove());
  } else if (sourceWrap?.isConnected) {
    sourceWrap.replaceWith(...buildPartRows(state.source, context));
  }
  markComponentMeshEdited(componentForMesh(state.source));
  selectMesh(result.selected);
  invalidateCharacterShadowVisibility({ request: false });
  requestRender();
  return true;
}

function replaceRowsAfterLoosePartMerge(source, oldPartWraps, result) {
  const context = meshPanelContexts.get(source);
  const group = groupsUI.find((candidate) => candidate.itemObjs.includes(source));
  if (!context || !group || !oldPartWraps.length) return false;
  if (result.full) {
    return restoreSourceRow(source, oldPartWraps);
  } else {
    oldPartWraps[0].replaceWith(...buildPartRows(source, context));
  }
  oldPartWraps.slice(1).forEach((partWrap) => partWrap.remove());
  return true;
}

function mergeSelectedLooseParts() {
  if (isRecording()) return false;
  const selectedMeshes = getSelectedMeshes();
  if (!canMergeLooseParts(selectedMeshes)) return false;
  const source = getLoosePartSource(selectedMeshes[0]);
  if (getActiveMeshEditSource() !== source) return false;
  const descriptor = componentForMesh(source);
  if (descriptor?.meshEditState === 'applied' || descriptor?.meshEditApplying) {
    return false;
  }
  restoreMeshIsolation();
  const previous = captureMeshPreview(source);
  const oldPartWraps = getLooseParts(source).map(meshRowWrap).filter(Boolean);
  clearSelection();
  const result = mergeLooseParts(selectedMeshes);
  if (!result || !replaceRowsAfterLoosePartMerge(source, oldPartWraps, result)) {
    return false;
  }
  rememberMeshPreview(source, previous);
  selectMesh(result.mesh);
  if (result.full) {
    if (descriptor) setComponentMeshEditState(descriptor, 'clean');
  } else {
    markComponentMeshEdited(descriptor);
  }
  invalidateCharacterShadowVisibility({ request: false });
  requestRender();
  return true;
}

function meshEditActions(mesh, descriptor) {
  const source = mesh ? getMeshEditSource(mesh) : getActiveMeshEditSource();
  const active = getActiveMeshEditSource();
  const face = getFaceSelection();
  const reason = descriptor?.meshEditApplying
    ? 'mesh.reason.applyingChanges'
    : !descriptor?.meshEditWritable
      ? 'mesh.reason.cannotWriteChanges'
      : descriptor?.meshEditState === 'applied'
        ? 'mesh.reason.appliedChanges'
        : isRecording() || (active && active !== source)
          ? 'mesh.reason.finishCurrentEdit'
          : null;
  const actions = [];
  const add = (id, key, enabled, why, run) =>
    actions.push({ id, key, disabled: !!reason || !enabled, reason: reason || (!enabled ? why : null), run });
  if (face) {
    const target = mesh === face.target;
    const count = face.triangles.size;
    const total = face.target.userData.loosePartTriangles?.length || Math.floor(face.target.geometry.index.count / 3);
    add(
      'applySelection',
      'mesh.applySelection',
      target && count > 0 && count < total,
      !target ? 'mesh.reason.selectEditingMesh' : count === 0 ? 'mesh.reason.selectFaces' : 'mesh.reason.leaveFaces',
      applyCurrentFaceSelection,
    );
    add('cancelSelection', 'mesh.cancelSelection', target, 'mesh.reason.selectEditingMesh', cancelFaceSelection);
  } else if (mesh) {
    if (!isLoosePart(mesh))
      add('separate', 'mesh.separateLooseParts', !active, 'mesh.reason.finishCurrentEdit', () => separateMeshRow(mesh));
    add(
      'separateSelection',
      'mesh.separateBySelection',
      !active || isLoosePart(mesh),
      'mesh.reason.finishCurrentEdit',
      () => beginFaceSelection(mesh),
    );
    if (active)
      add(
        'merge',
        'mesh.mergeLooseParts',
        canMergeLooseParts(getSelectedMeshes()),
        'mesh.reason.selectParts',
        mergeSelectedLooseParts,
      );
  }
  if (descriptor?.meshEditState === 'edited') {
    add('apply', 'mesh.applyMeshChanges', !face, 'mesh.reason.finishCurrentEdit', () =>
      applyComponentMeshChanges(descriptor),
    );
    add('cancel', 'mesh.cancelMeshChanges', !face, 'mesh.reason.finishCurrentEdit', () =>
      cancelComponentMeshChanges(descriptor),
    );
  }
  return actions;
}

function inspectorEditTools(mesh, descriptor) {
  const face = getFaceSelection();
  const source = mesh
    ? getMeshEditSource(mesh)
    : descriptor.meshes.find((item) => item === getActiveMeshEditSource()) || descriptor.meshes[0];
  const target = mesh || (face?.source === source ? face.target : null);
  const actions = meshEditActions(target, descriptor).filter(
    (action) => !mesh || !['apply', 'cancel'].includes(action.id),
  );
  const add = (id, key, enabled, run) => actions.push({ id, key, disabled: !enabled, run });
  const available = !isRecording() && !descriptor.meshEditApplying;
  if (face?.source === source) {
    add('connected', 'mesh.selectConnected', available && face.triangles.size > 0, selectConnectedFaces);
    add('invert', 'mesh.invertSelection', available, invertFaceSelection);
  }
  return {
    actions,
    parts: getLooseParts(source).length || 1,
    selected: getSelectedMeshes().length,
    faces: face?.source === source ? face.triangles.size : 0,
    stage:
      descriptor.meshEditState === 'clean' &&
      document.getElementById('pending-indicator')?.classList.contains('show') &&
      !hasUnappliedMeshChanges()
        ? 'applied'
        : descriptor.meshEditState,
    face: face?.source === source ? { through: face.selectThrough, disabled: !available } : null,
    run(id) {
      if (document.body.classList.contains('feature-edit-mesh-off')) return;
      const action = inspectorEditTools(mesh, descriptor).actions.find((item) => item.id === id);
      if (action && !action.disabled) return action.run();
    },
    setThrough(value) {
      if (!document.body.classList.contains('feature-edit-mesh-off') && !isRecording()) setFaceSelectionThrough(value);
    },
  };
}

/** Build the panel for live meshes. */
export function buildMeshPanel(meshes, liveMeshes, modPath, options = {}) {
  return appendMeshPanel(meshes, liveMeshes, modPath, { ...options, replace: true });
}

export function appendMeshPanel(meshes, liveMeshes, modPath, options = {}) {
  const list = document.getElementById('mesh-list');
  installPanelSelectionDrag(list);
  const replace = options.replace !== false;
  closeMeshContextMenu();
  if (replace) {
    restoreMeshIsolation();
    previewHistory = { source: null, undo: [], redo: [] };
    list.innerHTML = '';
    groupsUI = [];
    panelSelectionAnchor = null;
    clearTextureRunGroups();
  }
  registerViewSync('mesh-panel', syncMeshPanel);
  const texturePools = options.texturePools || {};
  const readOnlySource = options.readOnlySource === true;
  const meshEditReadOnly = options.meshEditReadOnly === true;
  const canPersistMetadata = options.canPersistMetadata !== false;
  const texturePicker = options.texturePicker || null;

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

      const poolIds = new Set(names.map((name) => meshes[name].texture_pool_id).filter(Boolean));
      if (poolIds.size > 1) {
        throw new Error(`Component ${groupName} has multiple texture pools`);
      }
      const poolId = poolIds.values().next().value;
      const texturePool = poolId ? texturePools[poolId] || [] : [];
      const componentKind = names.map((n) => meshes[n].material_kind_override).find(Boolean) || null;
      const componentIdentity =
        names.map((n) => meshes[n].identity?.component || meshes[n].component).find(Boolean) || null;

      const itemCbs = [],
        itemObjs = [];
      const componentDescriptor = {
        type: 'component',
        component: groupName,
        source: src,
        meshes: itemObjs,
        texturePool,
        modPath,
        meshEditState: 'clean',
        meshEditApplying: false,
      };
      const assetSummary = summarizeAssetBindings(
        names.map((name) => meshes[name]),
        options.assetResolution,
      );
      componentDescriptor.assetSummary = assetSummary;
      componentDescriptor.assetResolution = options.assetResolution || null;
      let materialKind = componentKind;
      let materialKindInFlight = false;
      const canPersist = !readOnlySource && canPersistMetadata && !!modPath;
      const canEdit = !readOnlySource && !!componentIdentity && !!modPath;
      componentDescriptor.meshEditWritable = !meshEditReadOnly && canEdit;
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
        recomputeTextureRuns(itemObjs);
        notifyMeshStateChanged(itemObjs);
        saveTextureState(modPath);
        return true;
      };
      const getTextureOverride = (mesh) => ({
        value: mesh.userData.manualTexOverride,
        automatic: mesh.userData.manualTexOverride === undefined && !mesh.userData.textureHighlightDisabled,
        resolved: mesh.userData.resolvedTexKey || null,
      });
      const onPoolChange = () => {
        recomputeTextureRuns(itemObjs);
        notifyMeshStateChanged(itemObjs);
        window.dispatchEvent(
          new CustomEvent('mod-viewer-inspector-refresh', {
            detail: { component: componentDescriptor, reason: 'pool' },
          }),
        );
        saveTextureState(modPath);
      };
      Object.assign(componentDescriptor, {
        getMeshEditTools: (mesh = null) => inspectorEditTools(mesh, componentDescriptor),
        getMaterialKind: () => itemObjs[0]?.userData.materialKindOverride || materialKind,
        setMaterialKind: canEdit ? setMaterialKind : undefined,
        openTextureManager: () => openTextureModal(groupName, texturePool, modPath, onPoolChange, texturePicker),
        getTextureOverride,
        setTextureOverride,
        applyMeshChanges: () => applyComponentMeshChanges(componentDescriptor),
        onAllMeshChangesApplied: options.onAllMeshChangesApplied,
      });

      const { hdr, visibilityButton, syncLabels, syncEditState } = buildGroupHeader(
        groupName,
        itemsWrap,
        () =>
          window.dispatchEvent(
            new CustomEvent('mod-viewer-component-selected', {
              detail: { component: componentDescriptor },
            }),
          ),
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
        const { wrap } = buildDrawRow(name, groupName, meshes[name], mesh, itemCbs, {
          onContextMenu: isRecording() ? null : openMeshContextMenu,
        });
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

window.addEventListener(LANGUAGE_CHANGED, () => {
  if (meshContextActions) {
    Object.entries(meshContextActions).forEach(([, action]) => {
      action.textContent = t(action.dataset.i18n);
    });
  }
  groupsUI.forEach((group) => {
    group.syncLabels?.();
    group.itemObjs.forEach((mesh) => getMeshView(mesh)?.syncStateIndicator?.());
    group.componentDescriptor.syncEditState?.();
  });
  syncMeshPanel();
});

window.addEventListener('mod-viewer-recording-state', (event) => {
  if (event.detail?.recording) {
    restoreMeshIsolation();
    previewHistory = { source: null, undo: [], redo: [] };
    showRecordingSourceRows();
  } else restoreLoosePartRows();
  notifyEditToolsChanged();
});

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
        const partWrap = getMeshView(part)?.row?.closest('.draw-item-wrap');
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
  groupsUI = groupsUI.filter((group) => !group.assetFill || group.itemObjs.length);
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
