// Mesh <-> MESHES-panel-row selection: clicking a mesh in the 3D view
// highlights it and scrolls its row into view (expanding any collapsed
// group/source section it's hiding inside), and clicking a row does the same
// in reverse. Clicking empty space in the 3D view deselects.

import * as THREE from 'three';
import { camera, renderer } from './scene.js';
import { activeMeshes } from '../mesh/visibility.js';
import { getMeshView } from '../mesh/mesh-view-bindings.js';
import { requestRender } from './render-scheduler.js';

const HIGHLIGHT_COLOR = 0xffd60a; // selection yellow
const HIGHLIGHT_INTENSITY = 0.22;  // kept low so the mesh's own texture reads through
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

let selected = null; // currently selected mesh, or null

function setHighlight(mesh, on) {
  mesh.material.emissive.setHex(on ? HIGHLIGHT_COLOR : 0x000000);
  mesh.material.emissiveIntensity = on ? HIGHLIGHT_INTENSITY : 1;
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

function toNDC(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
}

// A plain "click" event still fires after an orbit-drag release (mousedown
// and mouseup share the same target regardless of movement between), so a
// pick has to gate on how far the pointer actually moved instead.
let downX = 0, downY = 0;

function onPointerDown(e) {
  downX = e.clientX; downY = e.clientY;
}

function onPointerUp(e) {
  if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) return; // was a drag, not a click

  toNDC(e);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(activeMeshes.filter(m => m.visible), false);
  selectMesh(hits.length ? hits[0].object : null);
}

export function initSelection() {
  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  renderer.domElement.addEventListener('pointerup', onPointerUp);
}