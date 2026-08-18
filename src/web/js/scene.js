// Three.js scene composition and the permanent viewport render loop.

import * as THREE from 'three';
import { ArcballControls } from 'three/addons/controls/ArcballControls.js';
import { createCameraFrame } from './camera-frame.js';
import { createEnvironmentController } from './environment.js';
import { createKeyLightController } from './key-light-controller.js';
import { createViewGizmoController } from './view-gizmo-controller.js';

const container = document.getElementById('canvas-container');

export const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setSize(container.clientWidth, container.clientHeight);
container.appendChild(renderer.domElement);

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
  scene, camera, renderer, controls, light: keyLight,
});
const viewGizmoController = createViewGizmoController({
  camera, controls, element: document.getElementById('view-gizmo'),
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
}).observe(container);

(function tick() {
  requestAnimationFrame(tick);
  viewGizmoController.updateSnap();
  controls.update();
  keyLightController.update();
  cameraFrame.updateClipping();
  viewGizmoController.updateAxes();
  renderer.render(scene, camera);
})();

export function setEnvironmentPreset(id) {
  return environmentController.setPreset(id);
}

export function getEnvironmentPreset() {
  return environmentController.getPreset();
}

export function toggleGrid() {
  grid.visible = !grid.visible;
  document.getElementById('grid-btn').classList.toggle('off', !grid.visible);
}

export function toggleTrackballGizmo() {
  viewGizmoController.toggle();
}

export function toggleLightHandle() {
  keyLightController.toggleMode();
}

export function frameView(meshes = [], direction = null, targetYOffset = 0) {
  cameraFrame.frameView(meshes, direction, targetYOffset);
}

export function resetView() {
  cameraFrame.resetView();
}

export function fitTo(meshes) {
  cameraFrame.fitTo(meshes);
}

export function resetModelOrientation() {
  cameraFrame.resetModelOrientation();
}

export function rotateModelQuarterTurn(meshes = []) {
  cameraFrame.rotateModelQuarterTurn(meshes);
}

export function rotateModelHorizontalQuarterTurn(meshes = []) {
  cameraFrame.rotateModelHorizontalQuarterTurn(meshes);
}
