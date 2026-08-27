// Shared WebGPU/TSL inverted-hull silhouette outlines.

import * as THREE from 'three/webgpu';
import {
  cameraProjectionMatrix,
  normalViewGeometry,
  positionView,
  uniform,
  vec4,
} from 'three/tsl';
import { requestRender } from './render-scheduler.js';

const OUTLINE_WIDTH_PIXELS = 1.5;
const outlineScalePerDepthNode = uniform(0);
const outlineMaterial = new THREE.MeshBasicNodeMaterial({
  color: 0x111318,
  side: THREE.BackSide,
  depthTest: true,
  depthWrite: false,
});
outlineMaterial.toneMapped = false;
const outlineViewDepth = positionView.z.negate().max(0.000001);
const outlineWidthView = outlineViewDepth.mul(outlineScalePerDepthNode);
const displacedOutlinePositionView = positionView.add(
  normalViewGeometry.mul(outlineWidthView));
outlineMaterial.vertexNode = cameraProjectionMatrix.mul(
  vec4(displacedOutlinePositionView, 1));

const attachedOutlines = new Set();
let outlinesEnabled = false;
let suppressedByWireframe = false;
let suppressedByDebug = false;
let outlineViewportHeight = 0;
let outlineEffectiveFov = 0;

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

/** Update the shared view-space extrusion scale from the CSS viewport. */
export function updateOutlineProjectionScale(camera, viewportHeight) {
  const height = Number(viewportHeight);
  const effectiveFov = typeof camera?.getEffectiveFOV === 'function'
    ? camera.getEffectiveFOV() : camera?.fov;
  const fovDegrees = Number(effectiveFov);
  if (!camera || !Number.isFinite(height) || height <= 0
      || !Number.isFinite(fovDegrees)
      || fovDegrees <= 0 || fovDegrees >= 180) {
    outlineScalePerDepthNode.value = 0;
    outlineViewportHeight = 0;
    outlineEffectiveFov = 0;
    return 0;
  }
  const fov = THREE.MathUtils.degToRad(fovDegrees);
  const scalePerDepth = 2 * Math.tan(fov / 2)
    * OUTLINE_WIDTH_PIXELS / height;
  outlineScalePerDepthNode.value = Number.isFinite(scalePerDepth)
    ? scalePerDepth : 0;
  outlineViewportHeight = height;
  outlineEffectiveFov = fovDegrees;
  return outlineScalePerDepthNode.value;
}

export function getOutlineState(mesh) {
  const outline = mesh?.userData?.viewerOutline;
  return {
    attached: !!outline,
    visible: !!outline?.visible,
    globalEnabled: outlinesEnabled,
    widthPixels: OUTLINE_WIDTH_PIXELS,
    scaleMode: 'view-depth',
    scalePerDepth: outlineScalePerDepthNode.value,
    viewportHeight: outlineViewportHeight,
    effectiveFov: outlineEffectiveFov,
    suppressedByWireframe,
    suppressedByDebug,
  };
}
