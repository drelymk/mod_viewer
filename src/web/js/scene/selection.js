// Mesh <-> MESHES-panel-row selection: clicking a mesh in the 3D view
// highlights it and scrolls its row into view (expanding any collapsed
// group/source section it's hiding inside), and clicking a row does the same
// in reverse. Ctrl adds or toggles meshes, while Ctrl-drag selects visible
// targets whose current screen-space geometry crosses the rectangle.

import { camera, controls, renderer } from './scene.js';
import { activeMeshes } from '../mesh/visibility.js';
import { getMeshView } from '../mesh/mesh-view-bindings.js';
import { setMeshSelectionOutline } from './outline-renderer.js';
import {
  meshesInClientRect, raycastModelAtClientPoint,
} from './model-picking.js';
import { requestRender } from './render-scheduler.js';
import { setLoosePartSelectionCleanup } from '../mesh/loose-parts.js';
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

function setHighlight(mesh, on) {
  setMeshSelectionOutline(mesh, on);
}

function setRowSelected(mesh, on) {
  const row = getMeshView(mesh)?.row;
  if (!row) return;
  row.classList.toggle('selected', on);
  if (on) expandAncestorsAndScrollTo(row);
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
  if (isRigTransformInteractionActive() || isRigJointPickingActive()) return;
  if (boxGesture && event.pointerId === boxGesture.pointerId) {
    const gesture = boxGesture;
    const rect = gesture.dragging ? updateSelectionBox(event) : null;
    restoreBoxGesture(event);
    if (gesture.dragging && rect) {
      addMeshesToSelection(meshesInClientRect({
        meshes: activeMeshes,
        camera,
        canvas: renderer.domElement,
        selectionRect: rect,
      }));
      return;
    }
    const hit = raycastModelAtClientPoint({
      clientX: event.clientX,
      clientY: event.clientY,
      canvas: renderer.domElement,
      camera,
      meshes: activeMeshes,
    });
    toggleMeshSelection(hit?.object || null);
    return;
  }
  if (Math.hypot(event.clientX - downX, event.clientY - downY)
      > DRAG_THRESHOLD_PIXELS) return;
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

function onPointerCancel(event) {
  restoreBoxGesture(event);
}

export function initSelection() {
  const canvas = renderer.domElement;
  canvas.addEventListener('pointerdown', onPointerDown, {capture: true});
  canvas.addEventListener('pointermove', onPointerMove, {capture: true});
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', onPointerCancel);
  canvas.addEventListener('lostpointercapture', onPointerCancel);
}
