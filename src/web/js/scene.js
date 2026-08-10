// Three.js scene, camera, lighting and grid — everything that is about
// *rendering* rather than about the mod being displayed.

import * as THREE from 'three';
import { ArcballControls } from 'three/addons/controls/ArcballControls.js';

const container = document.getElementById('canvas-container');

export const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setSize(container.clientWidth, container.clientHeight);
renderer.shadowMap.enabled = true;
container.appendChild(renderer.domElement);

export const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1117);
scene.add(new THREE.AmbientLight(0xffffff, 0.8));

const dirLight = new THREE.DirectionalLight(0xffffff, 0.5);
dirLight.position.set(5, 10, 7);
scene.add(dirLight);

const grid = new THREE.GridHelper(4, 20, 0x21262d, 0x161b22);
scene.add(grid);

export const camera = new THREE.PerspectiveCamera(
  45, container.clientWidth / container.clientHeight, 0.001, 1000);
camera.position.set(0, 1, 3);

export const controls = new ArcballControls(camera, renderer.domElement, scene);
controls.target.set(0, 0, 0);
controls.enableAnimations = true;
controls.setGizmosVisible(false);
let trackballGizmoVisible = false;

new ResizeObserver(() => {
  camera.aspect = container.clientWidth / container.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(container.clientWidth, container.clientHeight);
}).observe(container);

(function tick() {
  requestAnimationFrame(tick);
  controls.update();
  renderer.render(scene, camera);
})();

export function toggleGrid() {
  grid.visible = !grid.visible;
  document.getElementById('grid-btn').classList.toggle('off', !grid.visible);
}

export function toggleTrackballGizmo() {
  const visible = !trackballGizmoVisible;
  trackballGizmoVisible = visible;
  controls.setGizmosVisible(visible);
  document.getElementById('trackball-btn').classList.toggle('active', visible);
}

export function frameView(meshes = []) {
  const box = new THREE.Box3();
  meshes.forEach(m => box.expandByObject(m));
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() * 0.5, 0.001);
  const distance = radius / Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5)) * 1.15;
  const offset = camera.position.clone().sub(controls.target).normalize();
  if (offset.lengthSq() < 0.01) offset.set(.3, .5, 1).normalize();
  controls.target.copy(center);
  camera.position.copy(center).addScaledVector(offset, distance);
  camera.near = radius * 0.001;
  camera.far = Math.max(radius * 100, 100);
  camera.updateProjectionMatrix();
  controls.update();
}

/** Frame the camera and size the grid to the given meshes. */
export function fitTo(meshes) {
  const box = new THREE.Box3();
  meshes.forEach(m => box.expandByObject(m));
  if (box.isEmpty()) return;

  const bSize  = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const size   = bSize.length();

  // Median dimension filters wing/tail outliers (ZZMI) while still covering
  // large WWMI models.  max(4, median * 2.5) / 4
  const dims = [bSize.x, bSize.y, bSize.z].sort((a, b) => a - b);
  grid.scale.setScalar(Math.max(1, dims[1] * 0.625));
  grid.position.set(center.x, box.min.y, center.z);

  // Clipping planes have to track model scale or big models z-fight and small
  // ones vanish into the near plane.
  camera.near = size * 0.0005;
  camera.far  = size * 50;
  camera.updateProjectionMatrix();

  frameView(meshes);
}
