// Mesh/group rows, panel gestures and view synchronization share one row registry.
import { bridgeReady, reportPersistenceFailure } from '../app/bridge.js';
import { textureFile } from '../textures/texture-key.js';
import {
  activeMeshes,
  applyMeshVisibility,
  isMeshIsolationActive,
  meshUnderlyingVisible,
  setMeshIsolation,
} from '../mesh/mesh-state.js';
import { bindMeshView, getMeshView } from '../mesh/mesh-view-bindings.js';
import {
  addMeshesToSelection,
  clearSelection,
  getFaceSelection,
  getMeshEditSource,
  getSelectedMeshes,
  isMeshSelected,
  selectMesh,
  selectMeshes,
  setMeshesVisibility,
  toggleMeshSelection,
  toggleMeshVisibility,
} from '../scene/selection.js';
import { registerInspectorMesh } from './inspector-panel.js';
import { closeMeshContextMenu, showMeshContextMenu } from './mesh-panel-context-menu.js';
import { createIcon } from '../ui/ui-icons.js';
import { isRecording } from '../editing/record-session.js';
import { openToggleModal } from '../editing/toggle-modal.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { requestRender } from '../scene/render-scheduler.js';
import { frameView, invalidateCharacterShadowVisibility } from '../scene/scene.js';
import { setRightDockTab } from './right-dock.js';
import { alertDialog } from '../ui/dialogs.js';
import { canMergeLooseParts, getLooseParts, isLoosePart } from '../mesh/loose-parts.js';

export const groupsUI = [];

let panelSelectionListenersInstalled = false;

let panelSelectionDrag = null;

let panelSelectionAnchor = null;

let suppressPanelClick = false;

export const meshPanelContexts = new WeakMap();

const meshByRow = new WeakMap();

const PANEL_SELECTION_THRESHOLD = 5;

function canIsolateSelectedMeshes() {
  return (
    !isRecording() &&
    !activeMeshes.some((mesh) => componentForMesh(mesh)?.meshEditApplying) &&
    getSelectedMeshes().length > 0
  );
}

function isolateSelectedMeshes() {
  setMeshIsolation(getSelectedMeshes());
}

window.addEventListener('mod-viewer-isolation-changed', () => {
  const button = document.getElementById('exit-isolation-btn');
  if (button) button.hidden = !isMeshIsolationActive();
});

export function showTextureLoadError(key) {
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

export function meshRowWrap(mesh) {
  return getMeshView(mesh)?.row?.closest('.draw-item-wrap') || null;
}

export function meshRowLabel(mesh, context) {
  const label = getMeshView(mesh)?.row?.querySelector('.mesh-name')?.textContent.trim();
  if (label) return label;
  if (mesh.userData.displayName) return mesh.userData.displayName;
  if (context?.entry?.drawindexed) return context.entry.drawindexed.join(', ');
  return mesh.name || 'Mesh';
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
  event.currentTarget.focus({ preventScroll: true });
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

export function installPanelSelectionDrag(list) {
  if (panelSelectionListenersInstalled) return;
  list.tabIndex = 0;
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

function openMeshContextMenu(event, mesh) {
  if (isRecording()) {
    event.preventDefault();
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  if (!isMeshSelected(mesh)) selectMesh(mesh);
  const actions = [
    {
      key: 'mesh.createToggle',
      className: 'mesh-create-toggle-action',
      hidden: !createToggleSelection(mesh),
      run() {
        const quickCreate = createToggleSelection(mesh);
        const context = meshPanelContexts.get(mesh);
        if (quickCreate)
          return openToggleModal({
            mode: 'add',
            modPath: context.modPath,
            quickCreate,
            onSaved: context.onToggleChange,
          });
      },
    },
    { key: 'mesh.rename', className: 'mesh-rename-action', run: () => renameMesh(mesh) },
    {
      key: 'mesh.isolateSelected',
      className: 'mesh-isolate-action',
      disabled: !canIsolateSelectedMeshes(),
      run: isolateSelectedMeshes,
    },
    ...componentForMesh(getMeshEditSource(mesh))
      .getMeshEditTools(mesh)
      .actions.filter((action) => !['connected', 'invert'].includes(action.id))
      .map((action) => ({
        ...action,
        hidden: action.id === 'separateSelection' && canMergeLooseParts(getSelectedMeshes()),
      })),
  ];
  showMeshContextMenu(event, actions);
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
  showMeshContextMenu(
    event,
    descriptor.getMeshEditTools().actions.filter((action) => ['apply', 'cancel'].includes(action.id)),
  );
}

export function syncMeshPanel(changedMeshes = null) {
  const changed = changedMeshes && new Set(changedMeshes);
  for (const group of groupsUI) {
    if (
      changed &&
      !group.itemObjs.some((mesh) => changed.has(mesh) || getLooseParts(mesh).some((part) => changed.has(part)))
    )
      continue;
    group.itemObjs.forEach((mesh, index) => {
      group.itemCbs[index].checked = meshUnderlyingVisible(mesh);
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

export function sourceMeshes(header) {
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
  const any = targets.some(meshUnderlyingVisible);
  const all = targets.length > 0 && targets.every(meshUnderlyingVisible);
  button.classList.toggle('state-hidden', !any);
  button.classList.toggle(
    'state-manual',
    targets.some((mesh) => mesh.userData.manuallyToggled),
  );
  button.setAttribute('aria-pressed', any && !all ? 'mixed' : String(all));
  button.title = t(all ? 'mesh.hideGroup' : 'mesh.showGroup', { name: button.dataset.visibilityName });
  button.setAttribute('aria-label', button.title);
}

export function buildGroupVisibilityButton(name, getMeshes, className) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `mesh-state-btn ${className}`;
  button.dataset.visibilityName = name;
  button.appendChild(createIcon('visibility'));
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    const targets = visibilityTargets(getMeshes());
    const visible = !targets.every(meshUnderlyingVisible);
    setMeshesVisibility(targets, visible);
  });
  button.addEventListener('dblclick', (event) => event.stopPropagation());
  syncGroupVisibilityButton(button, getMeshes());
  return button;
}

window.addEventListener('mod-viewer-mesh-state-changed', (event) => {
  if (event.detail?.meshes?.length) syncMeshPanel(event.detail.meshes);
});

function buildHeaderEditActions(mesh, descriptor, ids) {
  const controls = document.createElement('span');
  controls.className = 'mesh-header-edit-actions';
  controls.hidden = true;
  const buttons = new Map();
  const sync = () => {
    controls.hidden = mesh ? getFaceSelection()?.target !== mesh : descriptor?.meshEditState !== 'edited';
    if (controls.hidden) return;
    const actions = descriptor.getMeshEditTools(mesh).actions.filter((action) => ids.includes(action.id));
    for (const action of actions) {
      let button = buttons.get(action.id);
      if (!button) {
        button = document.createElement('button');
        button.type = 'button';
        button.className = 'icon-btn mesh-header-edit-btn';
        button.dataset.meshHeaderAction = action.id;
        button.appendChild(createIcon(action.id.startsWith('apply') ? 'check' : 'close'));
        button.addEventListener('click', (event) => {
          event.stopPropagation();
          void descriptor.getMeshEditTools(mesh).run(action.id);
        });
        button.addEventListener('dblclick', (event) => event.stopPropagation());
        buttons.set(action.id, button);
        controls.appendChild(button);
      }
      const label = t(action.key);
      button.setAttribute('aria-label', label);
      button.title = action.reason ? `${label}: ${t(action.reason)}` : label;
      button.disabled = action.disabled;
    }
  };
  sync();
  return { controls, sync };
}

export function buildGroupHeader(groupName, itemsWrap, componentDescriptor) {
  const onComponentSelected = () =>
    window.dispatchEvent(
      new CustomEvent('mod-viewer-component-selected', { detail: { component: componentDescriptor } }),
    );
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
  const editActions = buildHeaderEditActions(null, componentDescriptor, ['apply', 'cancel']);
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
      componentDescriptor.getMeshEditHistory().restore(direction);
    });
    button.addEventListener('dblclick', (event) => event.stopPropagation());
    historyControls.appendChild(button);
    return button;
  });
  const syncEditState = () => {
    editActions.sync();
    const face = getFaceSelection();
    if (face && componentForMesh(face.source) === componentDescriptor) getMeshView(face.target)?.syncEditActions?.();
    const state = componentDescriptor?.meshEditState || 'clean';
    editBadge.hidden = state !== 'edited';
    editBadge.dataset.state = state;
    editBadge.title = t('toolbar.edited');
    editBadge.textContent = t('toolbar.edited');
    const history = componentDescriptor.getMeshEditHistory();
    historyControls.hidden = !history.visible;
    historyButtons.forEach((button) => {
      const direction = button.dataset.meshEditAction;
      button.disabled = !history[direction];
      button.setAttribute('aria-label', t(`mesh.${direction}`));
      button.title = `${t(`mesh.${direction}`)} (Ctrl+${direction === 'undo' ? 'Z' : 'Y'})`;
    });
  };
  hdr.append(editBadge, editActions.controls, historyControls);
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
export function buildDrawRow(
  name,
  groupName,
  entry,
  mesh,
  itemCbs,
  { labelOverride = null, includeInGroup = true } = {},
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
    toggleMeshVisibility(mesh);
  });
  cb.addEventListener('dblclick', (event) => event.stopPropagation());
  if (!loosePart && includeInGroup) itemCbs.push(cb);

  const label = entry.drawindexed ? entry.drawindexed.join(', ') : '#' + name.slice(groupName.length + 1);
  const labelSpan = document.createElement('span');
  labelSpan.className = 'mesh-name';
  const displayLabel = labelOverride || mesh.userData.loosePartLabel || mesh.userData.displayName || label;
  labelSpan.textContent = displayLabel;
  row.append(cb, labelSpan);
  const editActions = buildHeaderEditActions(mesh, componentForMesh(getMeshEditSource(mesh)), [
    'applySelection',
    'cancelSelection',
  ]);
  row.appendChild(editActions.controls);
  const updateStateIndicator = (m) => {
    const visible = meshUnderlyingVisible(m);
    cb.checked = visible;
    cb.classList.toggle('state-hidden', !visible);
    cb.classList.toggle('state-manual', !!m.userData.manuallyToggled);
    cb.setAttribute('aria-pressed', String(visible));
    if (m.userData.manuallyToggled) {
      cb.title = t(visible ? 'mesh.visibleManual' : 'mesh.hiddenManual');
    } else {
      cb.title = t(visible ? 'mesh.visibleAutomatic' : 'mesh.hiddenAutomatic');
    }
  };
  updateStateIndicator(mesh);
  bindMeshView(mesh, {
    row,
    stateButton: cb,
    syncStateIndicator: () => updateStateIndicator(mesh),
    syncEditActions: editActions.sync,
  });
  meshByRow.set(row, mesh);
  row.addEventListener('click', (event) => selectPanelRow(event, mesh));
  row.addEventListener('dblclick', (event) => {
    if (isPanelSelectionInteractiveTarget(event.target) || getFaceSelection()) return;
    selectMesh(mesh);
    frameView([mesh]);
    setRightDockTab('inspector', { userInitiated: true });
  });
  if (!isRecording()) {
    row.addEventListener('contextmenu', (event) => openMeshContextMenu(event, mesh));
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
      },
    );
    registerInspectorMesh(part, context.inspectorRecord);
    return wrap;
  });
}

export function connectedPartWraps(source) {
  return getLooseParts(source)
    .map((part) => getMeshView(part)?.row?.closest('.draw-item-wrap'))
    .filter(Boolean);
}

function restoreSourceRow(source, oldPartWraps = connectedPartWraps(source)) {
  const context = meshPanelContexts.get(source);
  const group = groupsUI.find((candidate) => candidate.itemObjs.includes(source));
  const sourceIndex = group?.itemObjs.indexOf(source) ?? -1;
  if (!context || !group || sourceIndex < 0 || !oldPartWraps.length) return false;
  const { wrap, cb } = buildDrawRow(context.name, context.groupName, context.entry, source, group.itemCbs, {
    includeInGroup: false,
  });
  group.itemCbs[sourceIndex] = cb;
  oldPartWraps[0].replaceWith(wrap);
  oldPartWraps.slice(1).forEach((partWrap) => partWrap.remove());
  return true;
}

export function showRecordingSourceRows() {
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

export function restoreLoosePartRows() {
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
      refreshMeshEditRows(source, []);
    });
  }
  requestRender();
}

export function componentForMesh(mesh) {
  return mesh?.userData?.componentDescriptor || null;
}

export function refreshMeshEditRows(source, oldPartWraps = connectedPartWraps(source)) {
  if (!getLooseParts(source).length) return restoreSourceRow(source, oldPartWraps);
  const context = meshPanelContexts.get(source);
  const anchor = oldPartWraps[0] || meshRowWrap(source);
  if (!context || !anchor?.isConnected) return false;
  anchor.replaceWith(...buildPartRows(source, context));
  oldPartWraps.slice(1).forEach((row) => row.remove());
  return true;
}

export function clearMeshPanelRows(list) {
  list.innerHTML = '';
  groupsUI.length = 0;
  panelSelectionAnchor = null;
}

window.addEventListener(LANGUAGE_CHANGED, () => {
  groupsUI.forEach((group) => {
    group.syncLabels?.();
    group.itemObjs.forEach((mesh) => getMeshView(mesh)?.syncStateIndicator?.());
    group.componentDescriptor.syncEditState?.();
  });
  syncMeshPanel();
});
