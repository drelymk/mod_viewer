// Three.js scene composition and the WebGPU-native on-demand viewport renderer.

import * as THREE from 'three/webgpu';
import { ArcballControls } from 'three/addons/controls/ArcballControls.js';
import { createCameraFrame } from './camera-frame.js';
import { createCharacterShadowController } from './character-shadow-controller.js';
import { createEnvironmentController } from './environment.js';
import { createKeyLightController } from './key-light-controller.js';
import {
  resetOutlineProjectionReference,
  updateOutlineProjectionScale,
} from './outline-renderer.js';
import { setBCTextureCompression } from './renderer-capabilities.js';
import { requestRender, setRenderCallback } from './render-scheduler.js';
import { createViewportRenderPipeline } from './viewport-render-pipeline.js';
import { createViewGizmoController } from './view-gizmo-controller.js';

const container = document.getElementById('canvas-container');
const openButton = document.getElementById('open-btn');
const rendererError = document.getElementById('renderer-error');
let rendererStopped = false;

export const renderer = new THREE.WebGPURenderer({
  antialias: true,
  samples: 4,
  alpha: false,
});
// r185 installs an automatic WebGL2 fallback callback on WebGPURenderer. This
// application is intentionally WebGPU-only, so disable that private hook
// before init and verify the actual backend after init as a second guard.
renderer._getFallback = null;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NoToneMapping;
renderer.toneMappingExposure = 1;
renderer.setClearColor(0x0d1117, 1);
renderer.setClearAlpha(1);
renderer.setPixelRatio(devicePixelRatio);
renderer.setSize(container.clientWidth, container.clientHeight);
container.appendChild(renderer.domElement);

function showRendererError(message) {
  rendererStopped = true;
  openButton.disabled = true;
  if (!rendererError) return;
  const detail = rendererError.querySelector('.renderer-error-detail');
  if (detail) detail.textContent = message;
  rendererError.classList.add('show');
}

function failRenderer(message) {
  if (rendererStopped) return;
  rendererStopped = true;
  showRendererError(message);
}

function rendererFailureMessage(error) {
  const detail = error?.message ? ` (${error.message})` : '';
  return `WebGPU is required by this version of Mod Viewer. Update your `
    + `graphics driver or use a browser with WebGPU support${detail}`;
}

async function initializeRenderer() {
  if (!globalThis.navigator?.gpu
      || typeof globalThis.navigator.gpu.requestAdapter !== 'function') {
    throw new Error('This browser does not expose navigator.gpu.');
  }
  const adapter = await globalThis.navigator.gpu.requestAdapter({
    powerPreference: 'high-performance',
    featureLevel: 'core',
  });
  if (!adapter) throw new Error('No WebGPU adapter is available.');
  const supportsBC = adapter.features?.has?.(
    'texture-compression-bc') === true;
  const device = await adapter.requestDevice({
    requiredFeatures: supportsBC ? ['texture-compression-bc'] : [],
  });
  if (!device) throw new Error('The WebGPU device could not be created.');
  setBCTextureCompression(
    device.features?.has?.('texture-compression-bc') === true);

  // WebGPUBackend accepts an application-owned device through its public
  // parameters object. Supplying the preflighted device keeps renderer.init()
  // from attempting an implicit backend choice.
  renderer.backend.parameters.device = device;
  await renderer.init();
  if (renderer.backend?.isWebGPUBackend !== true
      || renderer.backend.compatibilityMode !== false
      || renderer.samples !== 4) {
    throw new Error('The renderer initialized with a non-WebGPU backend.');
  }
  return true;
}

export function isRendererAvailable() {
  return !rendererStopped
    && renderer.backend?.isWebGPUBackend === true
    && renderer.backend.compatibilityMode === false
    && renderer.samples === 4;
}

renderer.onDeviceLost = info => {
  failRenderer(`The WebGPU device was lost: ${info?.message || 'unknown reason'}.`);
};
renderer.onError = info => {
  failRenderer(`WebGPU reported an unrecoverable error: ${info?.message || 'unknown error'}.`);
};

export const rendererReady = initializeRenderer()
  .then(() => {
    if (!isRendererAvailable()) {
      throw new Error('The renderer is not using the required WebGPU core backend.');
    }
    requestRender();
    openButton.disabled = !isRendererAvailable();
    return true;
  })
  .catch(error => {
    showRendererError(rendererFailureMessage(error));
    return false;
  });

export const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1117);
const ambientLight = new THREE.AmbientLight(0xffffff, 0.55);
const hemisphereLight = new THREE.HemisphereLight(0xffffff, 0x30343f, 0.35);
scene.add(ambientLight, hemisphereLight);

// The movable key light remains independent from environment-owned lighting.
const keyLight = new THREE.DirectionalLight(0xffffff, 1);
keyLight.position.set(5, 10, 7);
scene.add(keyLight, keyLight.target);

const grid = new THREE.GridHelper(4, 20, 0x21262d, 0x161b22);
scene.add(grid);

export const camera = new THREE.PerspectiveCamera(
  45, container.clientWidth / container.clientHeight, 0.001, 1000);
camera.position.set(0, 1, 3);

export const controls = new ArcballControls(camera, renderer.domElement, scene);
controls.target.set(0, 0, 0);
controls.enableAnimations = true;
// Keep wheel zoom anchored to the point under the cursor for model inspection.
controls.cursorZoom = true;
// Model-scaled clipping belongs to cameraFrame; Arcball must not overwrite it.
controls.adjustNearFar = false;
controls.setGizmosVisible(false);

const environmentController = createEnvironmentController({
  scene,
  ambientLight,
  hemisphereLight,
  lightTarget: keyLight.target,
});
const keyLightController = createKeyLightController({
  scene, camera, renderer, controls, light: keyLight, onChange: requestRender,
});
const characterShadowController = createCharacterShadowController({
  renderer, scene, light: keyLight,
});
const viewportRenderPipeline = createViewportRenderPipeline({
  renderer, scene, camera,
});
const viewGizmoController = createViewGizmoController({
  camera, controls, element: document.getElementById('view-gizmo'),
  onChange: requestRender,
});
const cameraFrame = createCameraFrame({
  camera,
  renderer,
  controls,
  grid,
  cancelViewSnap: viewGizmoController.cancelSnap,
  onModelFit: keyLightController.rebase,
});

new ResizeObserver(() => {
  cameraFrame.resize(container.clientWidth, container.clientHeight);
  requestRender();
}).observe(container);

let renderCount = 0;

function renderFrame() {
  if (rendererStopped || renderer.backend?.isWebGPUBackend !== true) return;
  const snapActive = viewGizmoController.updateSnap();
  keyLightController.update();
  characterShadowController.update();
  cameraFrame.updateViewport();
  cameraFrame.updateClipping();
  updateOutlineProjectionScale(
    camera, controls.target, renderer.domElement.clientHeight);
  viewGizmoController.updateAxes();
  viewportRenderPipeline.render();
  renderCount += 1;
  if (snapActive) requestRender();
}

setRenderCallback(renderFrame);
controls.addEventListener('change', requestRender);

export function setEnvironmentPreset(id) {
  const changed = environmentController.setPreset(id);
  if (changed) requestRender();
  return changed;
}

export function getEnvironmentPreset() {
  return environmentController.getPreset();
}

export function toggleGrid() {
  grid.visible = !grid.visible;
  const button = document.getElementById('grid-btn');
  button.classList.toggle('off', !grid.visible);
  button.setAttribute('aria-pressed', String(grid.visible));
  button.setAttribute('aria-label', `Grid visibility: ${grid.visible ? 'on' : 'off'}`);
  requestRender();
}

export function toggleTrackballGizmo() {
  viewGizmoController.toggle();
}

export function toggleLightHandle() {
  keyLightController.toggleMode();
  requestRender();
}

export function setLightMode(mode) {
  const changed = keyLightController.setMode(mode);
  if (changed) requestRender();
  return changed;
}

export function getLightMode() {
  return keyLightController.getMode();
}

export function frameView(meshes = [], direction = null, targetYOffset = 0) {
  cameraFrame.frameView(meshes, direction, targetYOffset);
  resetOutlineProjectionReference(camera, controls.target);
  requestRender();
}

export function resetView() {
  cameraFrame.resetView();
  resetOutlineProjectionReference(camera, controls.target);
  characterShadowController.invalidateGeometry();
  viewportRenderPipeline.invalidateGeometry();
  requestRender();
}

export function adoptModelMeshes(meshes = []) {
  const adopted = cameraFrame.adoptModelMeshes(meshes);
  characterShadowController.adoptMeshes(meshes);
  viewportRenderPipeline.adoptMeshes(meshes);
  requestRender();
  return adopted;
}

export function fitTo(meshes, options) {
  cameraFrame.fitTo(meshes, options);
  if (!options?.preserveCamera) {
    resetOutlineProjectionReference(camera, controls.target);
  }
  characterShadowController.setMeshes(meshes);
  viewportRenderPipeline.setMeshes(meshes);
  requestRender();
}

export function forgetModelMeshes(meshes = []) {
  cameraFrame.forgetModelMeshes(meshes);
  characterShadowController.forgetMeshes(meshes);
  viewportRenderPipeline.forgetMeshes(meshes);
  requestRender();
}

export function resetModelOrientation(options) {
  cameraFrame.resetModelOrientation(options);
}

export function resetCharacterShadows() {
  characterShadowController.reset();
  viewportRenderPipeline.reset();
}

export function invalidateCharacterShadowGeometry() {
  characterShadowController.invalidateGeometry();
  viewportRenderPipeline.invalidateGeometry();
  requestRender();
}

export function invalidateCharacterShadowVisibility() {
  characterShadowController.invalidateVisibility();
  requestRender();
}

export function getCharacterShadowDebugState() {
  return characterShadowController.getDebugState();
}

export function getViewportRenderPipelineDebugState() {
  return viewportRenderPipeline.getDebugState();
}

export function setAmbientOcclusionStrength(value) {
  const changed = viewportRenderPipeline.setAmbientOcclusionStrength(value);
  if (changed) requestRender();
  return changed;
}

export function getAmbientOcclusionStrength() {
  return viewportRenderPipeline.getAmbientOcclusionStrength();
}

export function setAmbientOcclusionSuppressedByWireframe(value) {
  viewportRenderPipeline.setAmbientOcclusionSuppressedByWireframe(value);
  requestRender();
}

export function rotateModelQuarterTurn(meshes = []) {
  cameraFrame.rotateModelQuarterTurn(meshes);
  characterShadowController.invalidateGeometry();
  viewportRenderPipeline.invalidateGeometry();
  requestRender();
}

export function rotateModelHorizontalQuarterTurn(meshes = []) {
  cameraFrame.rotateModelHorizontalQuarterTurn(meshes);
  characterShadowController.invalidateGeometry();
  viewportRenderPipeline.invalidateGeometry();
  requestRender();
}

export function getRenderCount() {
  return renderCount;
}
