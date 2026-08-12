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
// The viewer owns model-scaled clipping planes (fitTo/frameView). Arcball's
// automatic near/far adjustment is based on the camera values from startup;
// letting it overwrite our later model-scaled values makes meshes disappear
// while zooming.
controls.adjustNearFar = false;
controls.setGizmosVisible(false);
let trackballGizmoVisible = true;
const viewGizmo = document.getElementById('view-gizmo');
const gizmoAxes = [...viewGizmo.querySelectorAll('.gizmo-axis')];
const _axisVectors = {
  x: new THREE.Vector3(1, 0, 0),
  y: new THREE.Vector3(0, 1, 0),
  z: new THREE.Vector3(0, 0, 1),
};
const _gizmoLocal = new THREE.Vector3();
const _inverseCamera = new THREE.Quaternion();
let viewSnap = null;
let gizmoDrag = null;
let homeView = null;
let clipNear = camera.near;
let clipFar = camera.far;
let uprightApplied = false;
let modelQuarterTurns = 0;
let modelPivot = null;

export function resetModelOrientation() {
  uprightApplied = false;
  modelQuarterTurns = 0;
  modelPivot = null;
}

/** Rotate the current model one quarter-turn around the viewer/world Y axis. */
export function rotateModelQuarterTurn(meshes = []) {
  const yaw = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(0, 1, 0), Math.PI / 2);
  rotateMeshesAroundCenter(meshes, yaw);
  modelQuarterTurns = (modelQuarterTurns + 1) % 4;
}

/** Rotate the current model one quarter-turn around the viewer/world X axis. */
export function rotateModelHorizontalQuarterTurn(meshes = []) {
  const pitch = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(1, 0, 0), Math.PI / 2);
  rotateMeshesAroundCenter(meshes, pitch);
}

function rotateMeshesAroundCenter(meshes, rotation) {
  if (!meshes.length) return;
  // Use the center captured after the initial upright correction. Recomputing
  // an axis-aligned bounding-box center after every turn causes asymmetric
  // models to drift because that center changes with orientation.
  let center = modelPivot && modelPivot.clone();
  if (!center) {
    const box = new THREE.Box3();
    meshes.forEach(m => box.expandByObject(m));
    if (box.isEmpty()) return;
    center = box.getCenter(new THREE.Vector3());
  }
  meshes.forEach(m => {
    m.position.sub(center).applyQuaternion(rotation).add(center);
    m.quaternion.premultiply(rotation);
  });
}

new ResizeObserver(() => {
  camera.aspect = container.clientWidth / container.clientHeight;
  renderer.setSize(container.clientWidth, container.clientHeight);
  updateCameraViewport();
}).observe(container);

(function tick() {
  requestAnimationFrame(tick);
  updateViewSnap();
  controls.update();
  // ArcballControls may restore its startup clip values during a scale
  // operation. Reapply the viewer's model-scaled planes after that update.
  const viewDistance = camera.position.distanceTo(controls.target);
  const requiredFar = Math.max(clipFar, viewDistance * 4, 100);
  if (camera.near !== clipNear || camera.far !== requiredFar) {
    camera.near = clipNear;
    camera.far = requiredFar;
    camera.updateProjectionMatrix();
  }
  updateViewGizmo();
  renderer.render(scene, camera);
})();

export function toggleGrid() {
  grid.visible = !grid.visible;
  document.getElementById('grid-btn').classList.toggle('off', !grid.visible);
}

export function toggleTrackballGizmo() {
  const visible = !trackballGizmoVisible;
  trackballGizmoVisible = visible;
  viewGizmo.classList.toggle('hidden', !visible);
  document.getElementById('trackball-btn').classList.toggle('active', visible);
  document.getElementById('trackball-btn').classList.toggle('off', !visible);
}

/** The canvas runs behind the floating side panels. Aim the projection at the
 * center of the unobstructed region while leaving controls.target on the
 * model itself, so orbiting still uses the correct physical pivot. */
function usableViewport() {
  const canvas = renderer.domElement.getBoundingClientRect();
  const fullWidth = Math.max(canvas.width, 1);
  const fullHeight = Math.max(canvas.height, 1);
  let left = 0;
  let right = fullWidth;
  const gap = 14;

  for (const id of ['sidebar', 'camera-panel']) {
    const element = document.getElementById(id);
    const rect = element?.getBoundingClientRect();
    if (element && getComputedStyle(element).display !== 'none' && rect.width > 1) {
      left = Math.max(left, rect.right - canvas.left + gap);
    }
  }
  const rightDock = document.getElementById('right-dock');
  const dockRect = rightDock?.getBoundingClientRect();
  if (rightDock && getComputedStyle(rightDock).display !== 'none' && dockRect.width > 1) {
    right = Math.min(right, dockRect.left - canvas.left - gap);
  }

  // A pathological narrow window should degrade to ordinary full-canvas
  // framing rather than create a nearly zero-width projection.
  if (right - left < fullWidth * 0.25) {
    left = 0;
    right = fullWidth;
  }
  return {
    fullWidth,
    fullHeight,
    width: right - left,
    centerShiftX: (left + right - fullWidth) * 0.5,
  };
}

function updateCameraViewport() {
  const viewport = usableViewport();
  camera.clearViewOffset();
  if (Math.abs(viewport.centerShiftX) > 0.5) {
    // A negative view offset shifts the world's projected center right.
    camera.setViewOffset(
      viewport.fullWidth, viewport.fullHeight,
      -viewport.centerShiftX, 0,
      viewport.fullWidth, viewport.fullHeight);
  }
  camera.updateProjectionMatrix();
  return viewport;
}

/** Keep the compass aligned with the world while its projection follows the
 * live camera, matching Blender's viewport navigation gizmo. */
function updateViewGizmo() {
  if (!trackballGizmoVisible) return;
  _inverseCamera.copy(camera.quaternion).invert();
  const projected = gizmoAxes.map(axis => {
    const sign = Number(axis.dataset.sign);
    _gizmoLocal.copy(_axisVectors[axis.dataset.axis]).multiplyScalar(sign)
      .applyQuaternion(_inverseCamera);
    return {
      axis,
      x: 52 + _gizmoLocal.x * 34,
      y: 52 - _gizmoLocal.y * 34,
      depth: _gizmoLocal.z,
    };
  });
  // Far endpoints paint first so the near-facing axis stays legible where
  // axes overlap in front/top/side views.
  const svg = viewGizmo.querySelector('svg');
  projected.sort((a, b) => a.depth - b.depth);
  const currentOrder = [...svg.querySelectorAll(':scope > .gizmo-axis')];
  if (projected.some(({ axis }, index) => currentOrder[index] !== axis)) {
    projected.forEach(({ axis }) => svg.appendChild(axis));
    // Re-appending axes changes SVG paint order, so keep the origin cap on top.
    svg.appendChild(viewGizmo.querySelector('.gizmo-origin'));
  }
  projected.forEach(({ axis, x, y, depth }) => {
    const line = axis.querySelector('line');
    line.setAttribute('x1', '52');
    line.setAttribute('y1', '52');
    line.setAttribute('x2', x.toFixed(2));
    line.setAttribute('y2', y.toFixed(2));
    const circle = axis.querySelector('circle');
    circle.setAttribute('cx', x.toFixed(2));
    circle.setAttribute('cy', y.toFixed(2));
    const label = axis.querySelector('text');
    if (label) {
      label.setAttribute('x', x.toFixed(2));
      label.setAttribute('y', y.toFixed(2));
    }
    axis.style.opacity = String(0.48 + (depth + 1) * 0.26);
  });
}

function snapToAxis(axisName, sign) {
  const targetDirection = _axisVectors[axisName].clone().multiplyScalar(sign);
  const startDirection = camera.position.clone().sub(controls.target).normalize();
  const turn = new THREE.Quaternion().setFromUnitVectors(startDirection, targetDirection);
  const endUp = axisName === 'y'
    ? new THREE.Vector3(0, 0, sign > 0 ? -1 : 1)
    : new THREE.Vector3(0, 1, 0);
  viewSnap = {
    started: performance.now(),
    duration: 190,
    distance: camera.position.distanceTo(controls.target),
    startDirection,
    turn,
    startUp: camera.up.clone().normalize(),
    endUp,
  };
}

function updateViewSnap() {
  if (!viewSnap) return;
  const raw = Math.min(1, (performance.now() - viewSnap.started) / viewSnap.duration);
  const t = 1 - Math.pow(1 - raw, 3);
  const rotation = new THREE.Quaternion().slerpQuaternions(
    new THREE.Quaternion(), viewSnap.turn, t);
  const direction = viewSnap.startDirection.clone().applyQuaternion(rotation).normalize();
  camera.position.copy(controls.target).addScaledVector(direction, viewSnap.distance);
  camera.up.lerpVectors(viewSnap.startUp, viewSnap.endUp, t).normalize();
  camera.lookAt(controls.target);
  if (raw === 1) viewSnap = null;
}

gizmoAxes.forEach(axis => {
  axis.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      snapToAxis(axis.dataset.axis, Number(axis.dataset.sign));
    }
  });
});

viewGizmo.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  viewSnap = null;
  gizmoDrag = {
    x: event.clientX,
    y: event.clientY,
    moved: false,
    pointerId: event.pointerId,
    axis: event.target.closest?.('.gizmo-axis') || null,
  };
});

viewGizmo.addEventListener('pointermove', event => {
  if (!gizmoDrag) return;
  const dx = event.clientX - gizmoDrag.x;
  const dy = event.clientY - gizmoDrag.y;
  if (Math.abs(dx) + Math.abs(dy) > 2 && !gizmoDrag.moved) {
    gizmoDrag.moved = true;
    viewGizmo.setPointerCapture(gizmoDrag.pointerId);
    viewGizmo.classList.add('dragging');
  }
  if (!gizmoDrag.moved) return;
  const offset = camera.position.clone().sub(controls.target);
  const spherical = new THREE.Spherical().setFromVector3(offset);
  spherical.theta -= dx * 0.012;
  spherical.phi = THREE.MathUtils.clamp(
    spherical.phi + dy * 0.012, 0.025, Math.PI - 0.025);
  camera.position.copy(controls.target).add(offset.setFromSpherical(spherical));
  camera.up.set(0, 1, 0);
  camera.lookAt(controls.target);
  gizmoDrag.x = event.clientX;
  gizmoDrag.y = event.clientY;
});

function finishGizmoDrag(event, cancelled = false) {
  if (!gizmoDrag) return;
  const finished = gizmoDrag;
  if (!cancelled && !finished.moved && finished.axis) {
    snapToAxis(finished.axis.dataset.axis, Number(finished.axis.dataset.sign));
  }
  if (viewGizmo.hasPointerCapture?.(event.pointerId)) {
    viewGizmo.releasePointerCapture(event.pointerId);
  }
  viewGizmo.classList.remove('dragging');
  gizmoDrag = null;
}
viewGizmo.addEventListener('pointerup', finishGizmoDrag);
viewGizmo.addEventListener('pointercancel', event => finishGizmoDrag(event, true));

viewGizmo.addEventListener('wheel', event => {
  event.preventDefault();
  viewSnap = null;
  const offset = camera.position.clone().sub(controls.target);
  const scale = Math.exp(event.deltaY * 0.0015);
  const distance = THREE.MathUtils.clamp(
    offset.length() * scale, Math.max(camera.near * 4, 0.0001), camera.far * 0.8);
  camera.position.copy(controls.target).addScaledVector(offset.normalize(), distance);
}, { passive: false });

export function frameView(meshes = []) {
  const box = new THREE.Box3();
  meshes.forEach(m => box.expandByObject(m));
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() * 0.5, 0.001);
  const viewport = updateCameraViewport();
  // The vertical FOV normally limits a bounding sphere. If side panels leave
  // a viewport narrower than it is tall, back the camera up proportionally so
  // the model also fits horizontally instead of disappearing under a panel.
  const narrowScale = Math.max(1, viewport.fullHeight / viewport.width);
  const distance = radius / Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5))
    * 1.15 * narrowScale;
  const offset = camera.position.clone().sub(controls.target).normalize();
  if (offset.lengthSq() < 0.01) offset.set(.3, .5, 1).normalize();
  controls.target.copy(center);
  camera.position.copy(center).addScaledVector(offset, distance);
  camera.near = radius * 0.001;
  camera.far = Math.max(radius * 100, 100);
  clipNear = camera.near;
  clipFar = camera.far;
  camera.updateProjectionMatrix();
  controls.update();
}

/** Restore the camera framing captured for the current model. */
export function resetView() {
  if (!homeView) return;
  viewSnap = null;
  // Restore the orientation captured after automatic upright correction.
  if (homeView.meshes) {
    homeView.meshes.forEach(({ mesh, quaternion, position }) => {
      mesh.quaternion.copy(quaternion);
      mesh.position.copy(position);
    });
  }
  // ArcballControls owns internal camera/gizmo matrices in addition to the
  // public camera and target. Copying only those public values lets the next
  // controls.update() reapply the stale orbit state. reset() restores the
  // fitted state captured by saveState() below, keeping both layers aligned.
  controls.reset();
  clipNear = camera.near;
  clipFar = camera.far;
  updateCameraViewport();
  controls.update();
}

/** Frame the camera and size the grid to the given meshes. */
export function fitTo(meshes) {
  if (!uprightApplied && meshes.length) {
    const rawBox = new THREE.Box3();
    meshes.forEach(m => rawBox.expandByObject(m));
    const rawSize = rawBox.getSize(new THREE.Vector3());
    // Some mods are authored Z-up. A clearly dominant Z extent is a reliable
    // signal for a lying-down model; rotate it once so Z becomes viewer Y.
    if (rawSize.z > rawSize.y * 1.5 && rawSize.z > rawSize.x * 1.15) {
      meshes.forEach(m => {
        const pitch = new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(1, 0, 0), -Math.PI / 2);
        m.quaternion.copy(pitch);
      });
    }
    uprightApplied = true;
  }
  const box = new THREE.Box3();
  meshes.forEach(m => box.expandByObject(m));
  if (box.isEmpty()) return;

  const bSize  = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  modelPivot = center.clone();
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
  clipNear = camera.near;
  clipFar = camera.far;
  camera.updateProjectionMatrix();

  frameView(meshes);
  homeView = {
    position: camera.position.clone(),
    target: controls.target.clone(),
    near: camera.near,
    far: camera.far,
    meshes: meshes.map(mesh => ({
      mesh,
      quaternion: mesh.quaternion.clone(),
      position: mesh.position.clone(),
    })),
  };
  // The controller's constructor saved the generic startup camera. Replace
  // that baseline with this model's fitted view so Reset returns here.
  camera.updateMatrix();
  controls.update();
  controls.saveState();
}
