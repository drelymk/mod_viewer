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

const REFERENCE_OUTLINE_WIDTH_PIXELS = 0.75;
const MIN_OUTLINE_WIDTH_PIXELS = 0.5;
const MAX_OUTLINE_WIDTH_PIXELS = 1.5;
const outlineScalePerDepthNode = uniform(0);
const outlineViewDepth = positionView.z.negate().max(0.000001);
const outlineWidthView = outlineViewDepth.mul(outlineScalePerDepthNode);
const displacedOutlinePositionView = positionView.add(
  normalViewGeometry.mul(outlineWidthView));
const outlineVertexNode = cameraProjectionMatrix.mul(
  vec4(displacedOutlinePositionView, 1));

function createOutlineMaterial(color) {
  const material = new THREE.MeshBasicNodeMaterial({
    color,
    side: THREE.BackSide,
    depthTest: true,
    depthWrite: false,
  });
  material.toneMapped = false;
  material.vertexNode = outlineVertexNode;
  return material;
}

const outlineMaterial = createOutlineMaterial(0x111318);
const selectionOutlineMaterial = createOutlineMaterial(0xffd60a);

const attachedOutlines = new Set();
let outlinesEnabled = false;
let suppressedByWireframe = false;
let suppressedByDebug = false;
let outlineViewportHeight = 0;
let outlineEffectiveFov = 0;
let outlineEffectiveWidthPixels = REFERENCE_OUTLINE_WIDTH_PIXELS;
let outlineProjectionSpan = 0;
let outlineReferenceProjectionSpan = 0;
let outlineProjectionRatio = 1;

function syncOutlineVisibility() {
  attachedOutlines.forEach(syncOutline);
}

function syncOutline(outline) {
  const selected = outline.userData.selectionSelected === true;
  outline.material = selected ? selectionOutlineMaterial : outlineMaterial;
  const allowed = !suppressedByWireframe && !suppressedByDebug;
  outline.visible = allowed && (selected || outlinesEnabled);
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
  outline.userData.selectionSelected = false;
  outline.raycast = () => {};
  mesh.add(outline);
  mesh.userData.viewerOutline = outline;
  attachedOutlines.add(outline);
  syncOutline(outline);
  return outline;
}

/** Show selection on the existing inverted hull without changing the surface. */
export function setMeshSelectionOutline(mesh, selected) {
  const outline = mesh?.userData?.viewerOutline;
  if (!outline) return false;
  outline.userData.selectionSelected = selected === true;
  syncOutline(outline);
  return true;
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

function effectiveFovRadians(camera) {
  const effectiveFov = typeof camera?.getEffectiveFOV === 'function'
    ? camera.getEffectiveFOV() : camera?.fov;
  const fovDegrees = Number(effectiveFov);
  if (!Number.isFinite(fovDegrees)
      || fovDegrees <= 0 || fovDegrees >= 180) return null;
  return {
    degrees: fovDegrees,
    radians: THREE.MathUtils.degToRad(fovDegrees),
  };
}

function projectionSpan(camera, target, fovRadians) {
  if (!camera?.position || !target) return 0;
  const distance = camera?.position?.distanceTo(target);
  const span = distance * Math.tan(fovRadians / 2);
  return Number.isFinite(span) && span > 0 ? span : 0;
}

/** Capture the fitted camera projection without changing it during Arcball zoom. */
export function resetOutlineProjectionReference(camera, target) {
  const fov = effectiveFovRadians(camera);
  outlineReferenceProjectionSpan = fov
    ? projectionSpan(camera, target, fov.radians) : 0;
  return outlineReferenceProjectionSpan;
}

/** Update the shared view-space extrusion scale from camera projection and CSS viewport. */
export function updateOutlineProjectionScale(camera, target, viewportHeight) {
  const height = Number(viewportHeight);
  const fov = effectiveFovRadians(camera);
  const currentSpan = fov
    ? projectionSpan(camera, target, fov.radians) : 0;
  if (!camera || !Number.isFinite(height) || height <= 0
      || !fov || currentSpan <= 0) {
    outlineScalePerDepthNode.value = 0;
    outlineViewportHeight = 0;
    outlineEffectiveFov = 0;
    outlineEffectiveWidthPixels = 0;
    outlineProjectionSpan = 0;
    outlineProjectionRatio = 0;
    return 0;
  }
  const referenceSpan = outlineReferenceProjectionSpan > 0
    ? outlineReferenceProjectionSpan : currentSpan;
  const ratio = referenceSpan / currentSpan;
  const effectiveWidthPixels = THREE.MathUtils.clamp(
    REFERENCE_OUTLINE_WIDTH_PIXELS * Math.sqrt(ratio),
    MIN_OUTLINE_WIDTH_PIXELS,
    MAX_OUTLINE_WIDTH_PIXELS);
  const scalePerDepth = 2 * Math.tan(fov.radians / 2)
    * effectiveWidthPixels / height;
  outlineScalePerDepthNode.value = Number.isFinite(scalePerDepth)
    ? scalePerDepth : 0;
  outlineViewportHeight = height;
  outlineEffectiveFov = fov.degrees;
  outlineEffectiveWidthPixels = effectiveWidthPixels;
  outlineProjectionSpan = currentSpan;
  outlineProjectionRatio = ratio;
  return outlineScalePerDepthNode.value;
}

export function getOutlineState(mesh) {
  const outline = mesh?.userData?.viewerOutline;
  return {
    attached: !!outline,
    visible: !!outline?.visible,
    selected: outline?.userData?.selectionSelected === true,
    material: outline?.material === selectionOutlineMaterial
      ? 'selection' : 'normal',
    globalEnabled: outlinesEnabled,
    referenceWidthPixels: REFERENCE_OUTLINE_WIDTH_PIXELS,
    minWidthPixels: MIN_OUTLINE_WIDTH_PIXELS,
    maxWidthPixels: MAX_OUTLINE_WIDTH_PIXELS,
    effectiveWidthPixels: outlineEffectiveWidthPixels,
    scaleMode: 'view-depth-adaptive',
    scalePerDepth: outlineScalePerDepthNode.value,
    viewportHeight: outlineViewportHeight,
    effectiveFov: outlineEffectiveFov,
    projectionSpan: outlineProjectionSpan,
    referenceProjectionSpan: outlineReferenceProjectionSpan,
    projectionRatio: outlineProjectionRatio,
    suppressedByWireframe,
    suppressedByDebug,
  };
}
