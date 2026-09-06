// Mesh <-> MESHES-panel-row selection: clicking a mesh in the 3D view
// highlights it and scrolls its row into view (expanding any collapsed
// group/source section it's hiding inside), and clicking a row does the same
// in reverse. Clicking empty space in the 3D view deselects.

import { camera, renderer } from './scene.js';
import { activeMeshes } from '../mesh/visibility.js';
import { getMeshView } from '../mesh/mesh-view-bindings.js';
import { setMeshSelectionOutline } from './outline-renderer.js';
import { raycastModelAtClientPoint } from './model-picking.js';
import { requestRender } from './render-scheduler.js';
import { isRigTransformInteractionActive } from './rig-overlay-controller.js';

let selected = null; // currently selected mesh, or null

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
  row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

export function selectMesh(mesh) {
  if (selected === mesh) return;
  if (selected) {
    setHighlight(selected, false);
    setRowSelected(selected, false);
  }
  selected = mesh;
  if (mesh) {
    setHighlight(mesh, true);
    setRowSelected(mesh, true);
  }
  window.dispatchEvent(new CustomEvent('mod-viewer-mesh-selected', {
    detail: { mesh },
  }));
  requestRender();
}

export function clearSelection() {
  selectMesh(null);
}

// A plain "click" event still fires after an orbit-drag release (mousedown
// and mouseup share the same target regardless of movement between), so a
// pick has to gate on how far the pointer actually moved instead.
let downX = 0, downY = 0;

function onPointerDown(e) {
  downX = e.clientX; downY = e.clientY;
}

function onPointerUp(e) {
  if (isRigTransformInteractionActive()) return;
  if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) return; // was a drag, not a click

  const hit = raycastModelAtClientPoint({
    clientX: e.clientX,
    clientY: e.clientY,
    canvas: renderer.domElement,
    camera,
    meshes: activeMeshes,
  });
  selectMesh(hit?.object || null);
}

export function initSelection() {
  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  renderer.domElement.addEventListener('pointerup', onPointerUp);
}
