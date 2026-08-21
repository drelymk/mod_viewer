// Shared WebGPU/TSL inverted-hull silhouette outlines.

import * as THREE from 'three/webgpu';
import { normalLocal, positionLocal, uniform } from 'three/tsl';
import { requestRender } from './render-scheduler.js';

const OUTLINE_WIDTH_PIXELS = 1.5;
const outlineWidthWorldNode = uniform(0);
const outlineMaterial = new THREE.MeshBasicNodeMaterial({
  color: 0x111318,
  side: THREE.BackSide,
  depthTest: true,
  depthWrite: false,
});
outlineMaterial.toneMapped = false;
outlineMaterial.positionNode = positionLocal.add(
  normalLocal.normalize().mul(outlineWidthWorldNode));

const attachedOutlines = new Set();
let outlinesEnabled = false;
let suppressedByWireframe = false;
let suppressedByDebug = false;

function outlinesVisible() {
  return outlinesEnabled && !suppressedByWireframe && !suppressedByDebug;
}

function syncOutlineVisibility() {
  const visible = outlinesVisible();
  attachedOutlines.forEach(outline => { outline.visible = visible; });
}

/** Attach one outline child while retaining the base mesh's exact geometry. */
export function attachOutline(mesh) {
  if (!mesh?.isMesh) return null;
  const existing = mesh.userData?.viewerOutline;
  if (existing) {
    syncOutlineVisibility();
    return existing;
  }

  const outline = new THREE.Mesh(mesh.geometry, outlineMaterial);
  outline.name = `${mesh.name || 'mesh'}-viewer-outline`;
  outline.renderOrder = 1;
  outline.userData.isViewerOutline = true;
  outline.raycast = () => {};
  mesh.add(outline);
  mesh.userData.viewerOutline = outline;
  attachedOutlines.add(outline);
  outline.visible = outlinesVisible();
  return outline;
}

/** Remove the child reference without disposing shared geometry or material. */
export function detachOutline(mesh) {
  const outline = mesh?.userData?.viewerOutline;
  if (!outline) return null;
  mesh.remove(outline);
  attachedOutlines.delete(outline);
  delete mesh.userData.viewerOutline;
  return outline;
}

export function setOutlinesEnabled(value) {
  outlinesEnabled = value === undefined ? !outlinesEnabled : !!value;
  syncOutlineVisibility();
  requestRender();
  return outlinesEnabled;
}

export function isOutlineEnabled() {
  return outlinesEnabled;
}

export function setOutlineSuppressedByWireframe(value) {
  suppressedByWireframe = !!value;
  syncOutlineVisibility();
  requestRender();
}

export function setOutlineSuppressedByDebug(value) {
  suppressedByDebug = !!value;
  syncOutlineVisibility();
  requestRender();
}

/** Update the shared world-space extrusion from the current CSS viewport. */
export function updateOutlineCameraScale(camera, target, viewportHeight) {
  const height = Number(viewportHeight);
  const distance = camera?.position?.distanceTo(target);
  if (!camera || !Number.isFinite(height) || height <= 0
      || !Number.isFinite(distance) || distance <= 0) {
    outlineWidthWorldNode.value = 0;
    return 0;
  }
  const effectiveFov = typeof camera.getEffectiveFOV === 'function'
    ? camera.getEffectiveFOV() : camera.fov;
  const fov = THREE.MathUtils.degToRad(Number(effectiveFov));
  const worldHeight = 2 * distance * Math.tan(fov / 2);
  const width = worldHeight / height * OUTLINE_WIDTH_PIXELS;
  outlineWidthWorldNode.value = Number.isFinite(width) ? width : 0;
  return outlineWidthWorldNode.value;
}

export function getOutlineState(mesh) {
  const outline = mesh?.userData?.viewerOutline;
  return {
    attached: !!outline,
    visible: !!outline?.visible,
    globalEnabled: outlinesEnabled,
    widthPixels: OUTLINE_WIDTH_PIXELS,
    suppressedByWireframe,
    suppressedByDebug,
  };
}
