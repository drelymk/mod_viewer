// Mesh <-> MESHES-panel-row selection: clicking a mesh in the 3D view
// highlights it and scrolls its row into view (expanding any collapsed
// group/source section it's hiding inside), and clicking a row does the same
// in reverse. Ctrl adds or toggles meshes, while Ctrl-drag selects visible
// targets whose current screen-space geometry crosses the rectangle.

import * as THREE from 'three/webgpu';
import { camera, controls, renderer } from './scene.js';
import { activeMeshes } from '../mesh/visibility.js';
import { getMeshView } from '../mesh/mesh-view-bindings.js';
import { setMeshSelectionOutline } from './outline-renderer.js';
import {
  authoredTriangleOrdinal, meshesInClientRect, raycastModelAtClientPoint,
  trianglesInClientRect,
} from './model-picking.js';
import { requestRender } from './render-scheduler.js';
import {
  getLoosePartSource, getLooseParts, setLoosePartSelectionCleanup,
  separateSelectedTriangles,
} from '../mesh/loose-parts.js';
import {
  isRigTransformInteractionActive, isRigJointPickingActive,
} from './rig-overlay-state.js';

const DRAG_THRESHOLD_PIXELS = 5;

const selected = new Set();
let primary = null;
let downX = 0;
let downY = 0;
let boxGesture = null;
let selectionBox = null;
let activeMeshEditSource = null;
let faceSelection = null;
let selectionInitialized = false;

function setHighlight(mesh, on) {
  setMeshSelectionOutline(mesh, on);
}

function setRowSelected(mesh, on) {
  const row = getMeshView(mesh)?.row;
  if (!row) return;
  row.classList.toggle('selected', on);
}

/** Un-collapse every collapsed group/source section a row is hiding inside,
 * so far as the row's own MESHES panel -- but never force the panel itself
 * back open if the user had it collapsed. */
function expandAncestorsAndScrollTo(row) {
  let el = row.parentElement;
  while (el && !el.id) {
    if (el.classList && el.classList.contains('collapsed')) {
      el.classList.remove('collapsed');
      const toggle = el.previousElementSibling?.querySelector?.('.group-toggle');
      toggle?.classList.remove('collapsed');
      toggle?.setAttribute('aria-expanded', 'true');
    }
    el = el.parentElement;
  }
  row.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

function dispatchSelectionChanged() {
  window.dispatchEvent(new CustomEvent('mod-viewer-mesh-selected', {
    detail: {mesh: primary, meshes: [...selected]},
  }));
  requestRender();
}

function dispatchFaceSelectionChanged() {
  window.dispatchEvent(new CustomEvent('mod-viewer-face-selection-changed', {
    detail: {
      source: activeMeshEditSource,
      target: faceSelection?.target || null,
      triangles: faceSelection ? [...faceSelection.triangles] : [],
      active: !!faceSelection,
    },
  }));
  requestRender();
}

function dispatchMeshEditOwnerChanged() {
  window.dispatchEvent(new CustomEvent('mod-viewer-mesh-edit-owner-changed', {
    detail: {source: activeMeshEditSource},
  }));
  requestRender();
}

function replaceSelection(meshes, nextPrimary) {
  const next = new Set(meshes.filter(Boolean));
  for (const mesh of selected) {
    if (!next.has(mesh)) {
      setHighlight(mesh, false);
      setRowSelected(mesh, false);
    }
  }
  for (const mesh of next) {
    if (!selected.has(mesh)) {
      setHighlight(mesh, true);
      setRowSelected(mesh, true);
    }
  }
  selected.clear();
  next.forEach(mesh => selected.add(mesh));
  primary = next.has(nextPrimary) ? nextPrimary : [...next].pop() || null;
  if (primary) {
    const row = getMeshView(primary)?.row;
    if (row) expandAncestorsAndScrollTo(row);
  }
  dispatchSelectionChanged();
}

export function selectMesh(mesh) {
  const next = mesh ? [mesh] : [];
  if (selected.size === next.length && primary === mesh
      && (!mesh || selected.has(mesh))) return;
  replaceSelection(next, mesh);
}

export function toggleMeshSelection(mesh) {
  if (!mesh) return false;
  const next = new Set(selected);
  if (next.has(mesh)) {
    next.delete(mesh);
    const remaining = [...next];
    replaceSelection(remaining, primary === mesh ? remaining.at(-1) : primary);
    return false;
  }
  next.add(mesh);
  replaceSelection([...next], mesh);
  return true;
}

export function addMeshesToSelection(meshes) {
  const additions = [...new Set(meshes || [])].filter(mesh => !selected.has(mesh));
  if (!additions.length) return false;
  replaceSelection([...selected, ...additions], additions.at(-1));
  return true;
}

export function clearSelection() {
  selectMesh(null);
}

export function getSelectedMeshes() {
  return [...selected];
}

export function isMeshSelected(mesh) {
  return selected.has(mesh);
}

export function getMeshEditSource(mesh) {
  return getLoosePartSource(mesh) || mesh || null;
}

export function getActiveMeshEditSource() {
  return activeMeshEditSource;
}

export function canEditMesh(mesh) {
  const source = getMeshEditSource(mesh);
  return !!source && (!activeMeshEditSource || activeMeshEditSource === source);
}

export function acquireMeshEditSource(mesh) {
  const source = getMeshEditSource(mesh);
  if (!source || (activeMeshEditSource && activeMeshEditSource !== source)) {
    return false;
  }
  if (activeMeshEditSource === source) return true;
  activeMeshEditSource = source;
  dispatchMeshEditOwnerChanged();
  return true;
}

export function releaseMeshEditSource(source = activeMeshEditSource) {
  if (!source || activeMeshEditSource !== source
      || (faceSelection && faceSelection.source === source)) return false;
  activeMeshEditSource = null;
  dispatchMeshEditOwnerChanged();
  return true;
}

export function getFaceSelection() {
  return faceSelection;
}

export function getSelectedFaceTriangles() {
  return faceSelection ? [...faceSelection.triangles] : [];
}

function overlayIndices(source, triangles) {
  const index = source?.geometry?.index;
  if (!index) return null;
  const IndexArray = index.array?.constructor || Uint32Array;
  const indices = new IndexArray(triangles.length * 3);
  triangles.forEach((triangle, indexOffset) => {
    const offset = triangle * 3;
    indices[indexOffset * 3] = index.getX(offset);
    indices[indexOffset * 3 + 1] = index.getX(offset + 1);
    indices[indexOffset * 3 + 2] = index.getX(offset + 2);
  });
  return indices;
}

function createFaceOverlay(state) {
  const topologyOverlay = new THREE.Mesh(state.target.geometry,
    new THREE.MeshBasicNodeMaterial({
      color: 0xffffff, wireframe: true,
      depthTest: true, depthWrite: false, side: THREE.DoubleSide,
    }));
  topologyOverlay.name = `${state.target.name || 'mesh'}-face-topology`;
  topologyOverlay.frustumCulled = false;
  topologyOverlay.renderOrder = 1000;
  topologyOverlay.userData.meshEditTopologyOverlay = true;
  topologyOverlay.raycast = () => {};
  state.target.add(topologyOverlay);
  state.topologyOverlay = topologyOverlay;

  const source = state.source;
  const geometry = new THREE.BufferGeometry();
  for (const [name, attribute] of Object.entries(source.geometry.attributes || {})) {
    if (attribute) geometry.setAttribute(name, attribute);
  }
  for (const [name, attributes] of Object.entries(
    source.geometry.morphAttributes || {})) {
    geometry.morphAttributes[name] = [...attributes];
  }
  geometry.morphTargetsRelative = source.geometry.morphTargetsRelative;
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(), 1));
  const overlay = new THREE.Mesh(geometry, new THREE.MeshBasicNodeMaterial({
    color: 0xffdf5d, transparent: true, opacity: 0.55,
    depthTest: false, depthWrite: false, side: THREE.DoubleSide,
  }));
  overlay.name = `${state.target.name || 'mesh'}-face-selection`;
  overlay.frustumCulled = false;
  overlay.renderOrder = 1001;
  overlay.userData.meshEditFaceOverlay = true;
  overlay.raycast = () => {};
  state.target.add(overlay);
  state.overlay = overlay;
}

function updateFaceOverlay() {
  if (!faceSelection) return;
  if (faceSelection.topologyOverlay) {
    faceSelection.topologyOverlay.visible = faceSelection.target.visible;
  }
  if (!faceSelection.overlay) return;
  const indices = overlayIndices(
    faceSelection.source, [...faceSelection.triangles]);
  if (!indices) return;
  faceSelection.overlay.geometry.setIndex(
    new THREE.BufferAttribute(indices, 1));
  faceSelection.overlay.visible = faceSelection.target.visible;
}

function disposeFaceOverlay(state) {
  const topologyOverlay = state?.topologyOverlay;
  if (topologyOverlay) {
    topologyOverlay.removeFromParent();
    topologyOverlay.material?.dispose?.();
    state.topologyOverlay = null;
  }
  const overlay = state?.overlay;
  if (!overlay) return;
  overlay.removeFromParent();
  overlay.geometry?.dispose?.();
  overlay.material?.dispose?.();
  state.overlay = null;
}

export function beginFaceSelection(target) {
  const source = getMeshEditSource(target);
  const parts = getLooseParts(source);
  if (!source || !target || (parts.length && !parts.includes(target))
      || (faceSelection && (faceSelection.source !== source
        || faceSelection.target !== target))) return false;
  if (faceSelection) return true;
  if (!acquireMeshEditSource(source)) return false;
  faceSelection = {
    source,
    target,
    triangles: new Set(),
    topologyOverlay: null,
    overlay: null,
    startedWithLooseParts: parts.length > 0,
  };
  createFaceOverlay(faceSelection);
  selectMesh(target);
  dispatchFaceSelectionChanged();
  return true;
}

function clearFaceSelectionState() {
  const state = faceSelection;
  if (!state) return null;
  disposeFaceOverlay(state);
  faceSelection = null;
  dispatchFaceSelectionChanged();
  return state;
}

export function cancelFaceSelection() {
  const state = clearFaceSelectionState();
  if (!state) return false;
  if (!state.startedWithLooseParts && !getLooseParts(state.source).length) {
    releaseMeshEditSource(state.source);
  }
  return true;
}

export function applyFaceSelection({label = null} = {}) {
  if (!faceSelection) return null;
  const state = faceSelection;
  const selectedTriangles = [...state.triangles];
  const targetCount = Array.isArray(state.target.userData?.loosePartTriangles)
    ? state.target.userData.loosePartTriangles.length
    : Math.floor(Number(state.source.geometry?.index?.count || 0) / 3);
  if (!selectedTriangles.length || selectedTriangles.length >= targetCount) {
    return null;
  }
  clearFaceSelectionState();
  const result = separateSelectedTriangles(state.target, selectedTriangles, {label});
  if (!result) return null;
  window.dispatchEvent(new CustomEvent('mod-viewer-face-selection-applied', {
    detail: {...result, selectedTriangles},
  }));
  return result;
}

export function resetMeshEditState() {
  if (faceSelection) clearFaceSelectionState();
  activeMeshEditSource = null;
  dispatchMeshEditOwnerChanged();
}

function removeMeshFromSelection(mesh) {
  if (!selected.has(mesh)) return;
  const remaining = [...selected].filter(candidate => candidate !== mesh);
  replaceSelection(remaining, primary === mesh ? remaining.at(-1) : primary);
}

setLoosePartSelectionCleanup(removeMeshFromSelection);

function ensureSelectionBox() {
  if (selectionBox) return selectionBox;
  selectionBox = document.createElement('div');
  selectionBox.className = 'mesh-selection-box';
  selectionBox.hidden = true;
  document.body.appendChild(selectionBox);
  return selectionBox;
}

function canvasRect() {
  const rect = renderer.domElement.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 ? rect : null;
}

function clampClientPoint(value, start, size) {
  return Math.min(start + size, Math.max(start, value));
}

function updateSelectionBox(event) {
  const gesture = boxGesture;
  const rect = gesture?.canvasRect;
  if (!gesture || !rect) return null;
  const currentX = clampClientPoint(event.clientX, rect.left, rect.width);
  const currentY = clampClientPoint(event.clientY, rect.top, rect.height);
  const left = Math.min(gesture.startX, currentX);
  const top = Math.min(gesture.startY, currentY);
  const right = Math.max(gesture.startX, currentX);
  const bottom = Math.max(gesture.startY, currentY);
  const box = ensureSelectionBox();
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
  box.style.width = `${right - left}px`;
  box.style.height = `${bottom - top}px`;
  box.hidden = false;
  return {left, top, right, bottom};
}

function restoreBoxGesture(event) {
  const gesture = boxGesture;
  if (!gesture || event.pointerId !== gesture.pointerId) return null;
  if (renderer.domElement.hasPointerCapture?.(event.pointerId)) {
    try { renderer.domElement.releasePointerCapture(event.pointerId); }
    catch { /* best effort */ }
  }
  controls.enabled = gesture.controlsEnabled;
  if (selectionBox) selectionBox.hidden = true;
  boxGesture = null;
  return gesture;
}

function onPointerDown(event) {
  downX = event.clientX;
  downY = event.clientY;
  if (event.button !== 0 || !event.ctrlKey || event.defaultPrevented
      || isRigTransformInteractionActive() || isRigJointPickingActive()) return;
  const rect = canvasRect();
  if (!rect) return;
  boxGesture = {
    pointerId: event.pointerId,
    startX: clampClientPoint(event.clientX, rect.left, rect.width),
    startY: clampClientPoint(event.clientY, rect.top, rect.height),
    canvasRect: rect,
    controlsEnabled: controls.enabled,
    dragging: false,
  };
  controls.enabled = false;
  event.preventDefault();
  event.stopPropagation();
  try { renderer.domElement.setPointerCapture(event.pointerId); }
  catch { /* best effort */ }
}

function onPointerMove(event) {
  if (!boxGesture || event.pointerId !== boxGesture.pointerId) return;
  if (!boxGesture.dragging
      && Math.hypot(event.clientX - downX, event.clientY - downY)
        <= DRAG_THRESHOLD_PIXELS) return;
  boxGesture.dragging = true;
  updateSelectionBox(event);
  event.preventDefault();
  event.stopPropagation();
}

function onPointerUp(event) {
  if (event.button !== 0) return;
  if (isRigTransformInteractionActive() || isRigJointPickingActive()) return;
  if (boxGesture && event.pointerId === boxGesture.pointerId) {
    const gesture = boxGesture;
    const rect = gesture.dragging ? updateSelectionBox(event) : null;
    restoreBoxGesture(event);
    if (gesture.dragging && rect) {
      if (faceSelection) {
        const triangles = trianglesInClientRect({
          mesh: faceSelection.target, camera,
          canvas: renderer.domElement, selectionRect: rect,
        });
        triangles.forEach(triangle => faceSelection.triangles.add(triangle));
        updateFaceOverlay();
        dispatchFaceSelectionChanged();
      } else {
        addMeshesToSelection(meshesInClientRect({
          meshes: activeMeshes,
          camera,
          canvas: renderer.domElement,
          selectionRect: rect,
        }));
      }
      return;
    }
    const hit = raycastModelAtClientPoint({
      clientX: event.clientX,
      clientY: event.clientY,
      canvas: renderer.domElement,
      camera,
      meshes: faceSelection ? [faceSelection.target] : activeMeshes,
    });
    if (faceSelection) {
      updateFaceSelectionFromClick(event, hit);
      return;
    }
    toggleMeshSelection(hit?.object || null);
    return;
  }
  if (Math.hypot(event.clientX - downX, event.clientY - downY)
      > DRAG_THRESHOLD_PIXELS) return;
  if (faceSelection) {
    if (Math.hypot(event.clientX - downX, event.clientY - downY)
        > DRAG_THRESHOLD_PIXELS) return;
    const hit = raycastModelAtClientPoint({
      clientX: event.clientX,
      clientY: event.clientY,
      canvas: renderer.domElement,
      camera,
      meshes: [faceSelection.target],
    });
    updateFaceSelectionFromClick(event, hit);
    return;
  }
  const hit = raycastModelAtClientPoint({
    clientX: event.clientX,
    clientY: event.clientY,
    canvas: renderer.domElement,
    camera,
    meshes: activeMeshes,
  });
  if (event.ctrlKey) toggleMeshSelection(hit?.object || null);
  else selectMesh(hit?.object || null);
}

function updateFaceSelectionFromClick(event, hit) {
  if (!faceSelection || hit?.object !== faceSelection.target) return;
  const triangle = authoredTriangleOrdinal(hit.object, hit.faceIndex);
  if (triangle === null) return;
  if (event.ctrlKey) {
    if (faceSelection.triangles.has(triangle)) {
      faceSelection.triangles.delete(triangle);
    } else {
      faceSelection.triangles.add(triangle);
    }
  } else {
    faceSelection.triangles.clear();
    faceSelection.triangles.add(triangle);
  }
  updateFaceOverlay();
  dispatchFaceSelectionChanged();
}

function onPointerCancel(event) {
  restoreBoxGesture(event);
}

function onViewportContextMenu(event) {
  if (!faceSelection) return;
  const hit = raycastModelAtClientPoint({
    clientX: event.clientX,
    clientY: event.clientY,
    canvas: renderer.domElement,
    camera,
    meshes: [faceSelection.target],
  });
  if (hit?.object !== faceSelection.target) return;
  event.preventDefault();
  event.stopPropagation();
  window.dispatchEvent(new CustomEvent(
    'mod-viewer-face-selection-contextmenu', {
      detail: {
        mesh: faceSelection.target,
        clientX: event.clientX,
        clientY: event.clientY,
      },
    }));
}

function onKeyDown(event) {
  if (event.key !== 'Escape' || !faceSelection) return;
  event.preventDefault();
  cancelFaceSelection();
}

export function initSelection() {
  if (selectionInitialized) return;
  selectionInitialized = true;
  const canvas = renderer.domElement;
  canvas.addEventListener('pointerdown', onPointerDown, {capture: true});
  canvas.addEventListener('pointermove', onPointerMove, {capture: true});
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', onPointerCancel);
  canvas.addEventListener('lostpointercapture', onPointerCancel);
  canvas.addEventListener('contextmenu', onViewportContextMenu);
  document.addEventListener('keydown', onKeyDown);
}

window.addEventListener('mod-viewer-mod-load-started', resetMeshEditState);
window.addEventListener('mod-viewer-asset-load-started', resetMeshEditState);
