// Mesh preview history and staged-edit commands own the component edit tools.
import { clearMeshIsolation } from '../mesh/mesh-state.js';
import { getMeshView } from '../mesh/mesh-view-bindings.js';
import {
  FACE_SELECTION_MODES,
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
  setFaceSelectionMode,
  selectMesh,
  selectMeshes,
} from '../scene/selection.js';
import { closeMeshContextMenu } from './mesh-panel-context-menu.js';
import { createIcon } from '../ui/ui-icons.js';
import { isRecording } from '../editing/record-session.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { requestRender } from '../scene/render-scheduler.js';
import { invalidateCharacterShadowVisibility } from '../scene/scene.js';
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
import {
  groupsUI,
  componentForMesh,
  connectedPartWraps,
  refreshMeshEditRows,
  meshPanelContexts,
  meshRowLabel,
  meshRowWrap,
  showRecordingSourceRows,
  restoreLoosePartRows,
} from './mesh-panel-rows.js';

let previewHistory = { source: null, undo: [], redo: [] };

let restoringPreview = false;

let faceHeaderTarget = null;

function notifyEditToolsChanged() {
  groupsUI.forEach((group) => group.componentDescriptor.syncEditState?.());
  syncFaceSelectionHeader();
  syncFaceSelectionModes();
  window.dispatchEvent(new CustomEvent('mod-viewer-mesh-edit-tools-changed'));
}

function syncFaceSelectionHeader() {
  const target = getFaceSelection()?.target || null;
  if (faceHeaderTarget !== target) getMeshView(faceHeaderTarget)?.syncEditActions?.();
  faceHeaderTarget = target;
  getMeshView(target)?.syncEditActions?.();
}

function syncFaceSelectionModes() {
  const toolbar = document.getElementById('mesh-selection-modes');
  if (!toolbar) return;
  const face = getFaceSelection();
  const descriptor = componentForMesh(face?.source);
  toolbar.hidden = !face || !descriptor;
  if (toolbar.hidden) return;
  if (!toolbar.childElementCount) {
    for (const mode of FACE_SELECTION_MODES) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'icon-btn';
      button.dataset.faceSelectionMode = mode;
      button.appendChild(createIcon(`selection-${mode}`));
      button.addEventListener('click', () => {
        const current = getFaceSelection();
        const component = componentForMesh(current?.source);
        if (current && component) inspectorEditTools(current.target, component).setMode(mode);
      });
      toolbar.appendChild(button);
    }
  }
  const tools = inspectorEditTools(face.target, descriptor);
  toolbar.setAttribute('aria-label', t('mesh.selectionMode'));
  for (const button of toolbar.children) {
    const mode = button.dataset.faceSelectionMode;
    const label = t(`mesh.selectionMode.${mode}`);
    button.setAttribute('aria-label', label);
    button.title = `${t('mesh.selectionMode')}: ${label}`;
    button.setAttribute('aria-pressed', String(face.mode === mode));
    button.disabled = tools.face.disabled;
  }
}

function captureMeshPreview(source) {
  const parts = getLooseParts(source);
  const face = getFaceSelection();
  const indexOf = (mesh) => (mesh === source ? -1 : parts.indexOf(mesh));
  return {
    parts: snapshotLooseParts(source),
    selected: getSelectedMeshes()
      .filter((mesh) => mesh === source || parts.includes(mesh))
      .map(indexOf),
    primary: indexOf(getPrimarySelectedMesh()),
    face:
      face?.source === source
        ? { target: indexOf(face.target), triangles: [...face.triangles], through: face.selectThrough, mode: face.mode }
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
  const snapshot = stack.pop();
  previewHistory[direction === 'undo' ? 'redo' : 'undo'].push(captureMeshPreview(source));
  restoringPreview = true;
  try {
    const oldRows = connectedPartWraps(source);
    cancelFaceSelection();
    clearSelection();
    restoreLooseParts(source, snapshot.parts);
    const parts = getLooseParts(source);
    refreshMeshEditRows(source, oldRows);
    const meshAt = (index) => (index === -1 ? source : parts[index]);
    setComponentMeshEditState(descriptor, parts.length ? 'edited' : 'clean');
    if (snapshot.face) {
      beginFaceSelection(meshAt(snapshot.face.target));
      setSelectedFaceTriangles(snapshot.face.triangles);
      setFaceSelectionThrough(snapshot.face.through);
      setFaceSelectionMode(snapshot.face.mode);
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
  previewHistory = { source: null, undo: [], redo: [] };
  const oldPartWraps = connectedPartWraps(activeSource);
  clearSelection();
  clearLooseParts(activeSource);
  if (!refreshMeshEditRows(activeSource, oldPartWraps)) return false;
  finishMeshEditRefresh(activeSource, activeSource);
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
  const sourceWrap = meshRowWrap(source);
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
  refreshMeshEditRows(source);
  finishMeshEditRefresh(source, parts[0]);
  return true;
}

function applyCurrentFaceSelection() {
  const state = getFaceSelection();
  if (!state) return false;
  const context = meshPanelContexts.get(state.source);
  if (!context) return false;
  const oldPartWraps = connectedPartWraps(state.source);
  const result = applyFaceSelection({ label: meshRowLabel(state.source, context) });
  if (!result) return false;
  refreshMeshEditRows(state.source, oldPartWraps);
  finishMeshEditRefresh(state.source, result.selected);
  return true;
}

function finishMeshEditRefresh(source, selected) {
  const descriptor = componentForMesh(source);
  if (getLooseParts(source).length) markComponentMeshEdited(descriptor);
  else setComponentMeshEditState(descriptor, 'clean');
  selectMesh(selected);
  invalidateCharacterShadowVisibility({ request: false });
  requestRender();
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
  const previous = captureMeshPreview(source);
  const oldPartWraps = getLooseParts(source).map(meshRowWrap).filter(Boolean);
  clearSelection();
  const result = mergeLooseParts(selectedMeshes);
  if (!result || !refreshMeshEditRows(source, oldPartWraps)) {
    return false;
  }
  rememberMeshPreview(source, previous);
  finishMeshEditRefresh(source, result.mesh);
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
    face: face?.source === source ? { through: face.selectThrough, mode: face.mode, disabled: !available } : null,
    run(id) {
      if (document.body.classList.contains('feature-edit-mesh-off')) return;
      const action = inspectorEditTools(mesh, descriptor).actions.find((item) => item.id === id);
      if (action && !action.disabled) return action.run();
    },
    setThrough(value) {
      if (!document.body.classList.contains('feature-edit-mesh-off') && !isRecording()) setFaceSelectionThrough(value);
    },
    setMode(mode) {
      if (document.body.classList.contains('feature-edit-mesh-off')) return;
      if (inspectorEditTools(mesh, descriptor).face?.disabled === false) setFaceSelectionMode(mode);
    },
  };
}

export function initializeComponentMeshEdit(descriptor, writable, onApplied) {
  Object.assign(descriptor, {
    meshEditState: 'clean',
    meshEditApplying: false,
    meshEditWritable: writable,
    getMeshEditTools: (mesh = null) => inspectorEditTools(mesh, descriptor),
    getMeshEditHistory: () => ({
      visible:
        descriptor.meshEditState === 'edited' ||
        (componentForMesh(previewHistory.source) === descriptor &&
          previewHistory.undo.length + previewHistory.redo.length > 0),
      undo: canRestoreMeshPreview('undo', descriptor),
      redo: canRestoreMeshPreview('redo', descriptor),
      restore(direction) {
        if (canRestoreMeshPreview(direction, descriptor)) return restoreMeshPreview(direction);
      },
    }),
    applyMeshChanges: () => applyComponentMeshChanges(descriptor),
    onAllMeshChangesApplied: onApplied,
  });
}

export function resetMeshPanelEditing() {
  faceHeaderTarget = null;
  previewHistory = { source: null, undo: [], redo: [] };
}

window.addEventListener(LANGUAGE_CHANGED, syncFaceSelectionModes);

window.addEventListener('mod-viewer-recording-state', (event) => {
  if (event.detail?.recording) {
    clearMeshIsolation();
    previewHistory = { source: null, undo: [], redo: [] };
    showRecordingSourceRows();
  } else restoreLoosePartRows();
  notifyEditToolsChanged();
});
