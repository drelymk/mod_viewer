// Three.js scene, camera, lighting and grid — everything that is about
// *rendering* rather than about the mod being displayed.

import * as THREE from 'three';
import { ArcballControls } from 'three/addons/controls/ArcballControls.js';
import { createEnvironmentController } from './environment.js';

const container = document.getElementById('canvas-container');

export const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setSize(container.clientWidth, container.clientHeight);
container.appendChild(renderer.domElement);

export const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1117);
// Ambient light alone cannot reveal normal-map detail because it has no
// direction. Keep a soft neutral base plus a low hemisphere fill so surfaces
// remain readable when the movable key light is in its Gray/off stage.
const ambientLight = new THREE.AmbientLight(0xffffff, 0.55);
const hemisphereLight = new THREE.HemisphereLight(0xffffff, 0x30343f, 0.35);
scene.add(ambientLight);
scene.add(hemisphereLight);

const dirLight = new THREE.DirectionalLight(0xffffff, 0.5);
dirLight.position.set(5, 10, 7);
scene.add(dirLight);
scene.add(dirLight.target);

// Compact screen-facing key-light handle. A sprite reads as a viewport control
// instead of an object in the model, and stays legible from every angle.
const lightIconCanvas = document.createElement('canvas');
lightIconCanvas.width = lightIconCanvas.height = 64;
const lightIconContext = lightIconCanvas.getContext('2d');
const lightGlow = lightIconContext.createRadialGradient(32, 32, 4, 32, 32, 30);
lightGlow.addColorStop(0, 'rgba(255,248,190,1)');
lightGlow.addColorStop(0.35, 'rgba(255,216,102,.95)');
lightGlow.addColorStop(0.7, 'rgba(255,184,70,.4)');
lightGlow.addColorStop(1, 'rgba(255,184,70,0)');
lightIconContext.fillStyle = lightGlow;
lightIconContext.fillRect(0, 0, 64, 64);
const lightHandle = new THREE.Sprite(new THREE.SpriteMaterial({
  map: new THREE.CanvasTexture(lightIconCanvas), transparent: true,
  // Keep the draggable marker in the viewport, but let model depth occlude
  // it so the light cannot appear to shine through the mesh.
  depthTest: true, depthWrite: false,
}));
lightHandle.renderOrder = 1000;
lightHandle.visible = true;
scene.add(lightHandle);

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

// The key light is deliberately not handed to EnvironmentController: its
// intensity is an explicit user interaction (double/current/off) and must not
// be mistaken for environment-owned lighting. Environment presets own the
// ambient, hemisphere and one accent light; the movable key remains an
// independent inspection light layered on top.
const environmentController = createEnvironmentController({
  scene,
  ambientLight,
  hemisphereLight,
});

export function setEnvironmentPreset(id) {
  return environmentController.setPreset(id);
}

export function getEnvironmentPreset() {
  return environmentController.getPreset();
}

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
let lightDrag = null;
let lightPointerInside = false;
const lightModes = ['double', 'current', 'off'];
let lightModeIndex = 0;
const lightRaycaster = new THREE.Raycaster();
const lightPointer = new THREE.Vector2();
const lightDragPlane = new THREE.Plane();
const lightHit = new THREE.Vector3();
const INITIAL_CAMERA_DIRECTION = new THREE.Vector3(0, 0, 1);
const INITIAL_CAMERA_UP = new THREE.Vector3(0, 1, 0);

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
  dirLight.target.position.copy(controls.target);
  lightHandle.position.copy(dirLight.position);
  const handleMultiplier = lightModes[lightModeIndex] === 'double' ? 2 : 1;
  const handleSize = Math.max(
    camera.position.distanceTo(controls.target) * 0.035 * handleMultiplier,
    0.0001);
  lightHandle.scale.set(handleSize, handleSize, 1);
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

export function toggleLightHandle() {
  lightModeIndex = (lightModeIndex + 1) % lightModes.length;
  const mode = lightModes[lightModeIndex];
  const visible = mode !== 'off';
  lightHandle.visible = visible;
  dirLight.intensity = mode === 'double' ? 1 : (mode === 'current' ? 0.5 : 0);
  const button = document.getElementById('light-btn');
  button.classList.toggle('double', mode === 'double');
  button.classList.toggle('current', mode === 'current');
  button.classList.toggle('off', mode === 'off');
  const labels = {
    double: 'Key light: double brightness and handle size',
    current: 'Key light: normal (drag; Shift-drag for depth)',
    off: 'Key light: off',
  };
  button.title = labels[mode];
  button.setAttribute('aria-label', labels[mode]);
  updateLightCursor();
}

function updateLightPointer(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  lightPointer.set(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1);
  lightRaycaster.setFromCamera(lightPointer, camera);
}

function canInteractWithLight(event = null) {
  if (event) {
    const rect = renderer.domElement.getBoundingClientRect();
    lightPointerInside = event.clientX >= rect.left && event.clientX <= rect.right &&
      event.clientY >= rect.top && event.clientY <= rect.bottom;
    updateLightPointer(event);
  }
  if (!lightPointerInside || !lightHandle.visible) return false;

  // The first ray hit matches the depth-tested render: if a model is in front
  // of the sprite, the marker is not visually available for interaction.
  return lightRaycaster.intersectObjects(scene.children, true)[0]?.object === lightHandle;
}

function updateLightCursor(event = null) {
  if (lightDrag) return;
  renderer.domElement.style.cursor = canInteractWithLight(event) ? 'crosshair' : '';
}

renderer.domElement.addEventListener('pointerenter', event => {
  lightPointerInside = true;
  updateLightCursor(event);
});
renderer.domElement.addEventListener('pointerleave', () => {
  lightPointerInside = false;
  if (!lightDrag) renderer.domElement.style.cursor = '';
});

renderer.domElement.addEventListener('pointerdown', event => {
  if (event.button !== 0 || !canInteractWithLight(event)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const cameraDirection = camera.getWorldDirection(new THREE.Vector3());
  lightDragPlane.setFromNormalAndCoplanarPoint(cameraDirection, dirLight.position);
  lightDrag = {
    pointerId: event.pointerId,
    depthMode: event.shiftKey,
    startY: event.clientY,
    startPosition: dirLight.position.clone(),
    cameraDirection,
    depthScale: Math.max(camera.position.distanceTo(controls.target) * 0.004,
      0.0001),
  };
  controls.enabled = false;
  renderer.domElement.setPointerCapture(event.pointerId);
  renderer.domElement.style.cursor = 'grabbing';
}, { capture: true });

renderer.domElement.addEventListener('pointermove', event => {
  if (!lightDrag) {
    lightPointerInside = true;
    updateLightCursor(event);
    return;
  }
  if (event.pointerId !== lightDrag.pointerId) return;
  if (lightDrag.depthMode) {
    dirLight.position.copy(lightDrag.startPosition).addScaledVector(
      lightDrag.cameraDirection,
      (lightDrag.startY - event.clientY) * lightDrag.depthScale);
    return;
  }
  updateLightPointer(event);
  if (lightRaycaster.ray.intersectPlane(lightDragPlane, lightHit)) {
    dirLight.position.copy(lightHit);
  }
});

function finishLightDrag(event) {
  if (!lightDrag || event.pointerId !== lightDrag.pointerId) return;
  renderer.domElement.releasePointerCapture(event.pointerId);
  lightDrag = null;
  controls.enabled = true;
  updateLightCursor(event);
}
renderer.domElement.addEventListener('pointerup', finishLightDrag);
renderer.domElement.addEventListener('pointercancel', finishLightDrag);

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

export function frameView(meshes = [], direction = null, targetYOffset = 0) {
  const box = new THREE.Box3();
  meshes.forEach(m => box.expandByObject(m));
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  center.y += targetYOffset;
  const radius = Math.max(size.length() * 0.5, 0.001);
  const viewport = updateCameraViewport();
  // The vertical FOV normally limits a bounding sphere. If side panels leave
  // a viewport narrower than it is tall, back the camera up proportionally so
  // the model also fits horizontally instead of disappearing under a panel.
  const narrowScale = Math.max(1, viewport.fullHeight / viewport.width);
  const distance = radius / Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5))
    * 1.15 * narrowScale;
  const offset = direction
    ? direction.clone().normalize()
    : camera.position.clone().sub(controls.target).normalize();
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
  // Recompute framing from the restored model bounds. Arcball's saved matrix
  // can become stale after model turns, zoom animation, or viewport changes;
  // using it directly can place some models (notably Beidou) outside view.
  const box = new THREE.Box3();
  homeView.meshes.forEach(({ mesh }) => box.expandByObject(mesh));
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  camera.up.copy(INITIAL_CAMERA_UP);
  frameView(homeView.meshes.map(({ mesh }) => mesh),
            INITIAL_CAMERA_DIRECTION, size.y * 0.08);
  camera.updateMatrix();
  controls.update();
  controls.saveState();
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
  // large WWMI models. GridHelper is 4 units wide before scaling, so this
  // produces a footprint of max(6, median * 4).
  const dims = [bSize.x, bSize.y, bSize.z].sort((a, b) => a - b);
  grid.scale.setScalar(Math.max(1.5, dims[1]));
  grid.position.set(center.x, box.min.y, center.z);

  // Clipping planes have to track model scale or big models z-fight and small
  // ones vanish into the near plane.
  camera.near = size * 0.0005;
  camera.far  = size * 50;
  clipNear = camera.near;
  clipFar = camera.far;
  camera.updateProjectionMatrix();

  // A new model always establishes its home view from the application's
  // startup camera, never from the orbit/zoom left behind by the previous
  // model. Otherwise Reset merely returns to that inherited, already-moved
  // orientation.
  viewSnap = null;
  camera.up.copy(INITIAL_CAMERA_UP);
  frameView(meshes, INITIAL_CAMERA_DIRECTION, bSize.y * 0.08);
  // Directional lights ignore distance, but their draggable helper does not.
  // Rebase it for every model so tiny, huge, or far-from-origin meshes keep
  // the marker inside the newly fitted view (Beidou exposed the fixed-world
  // position bug).
  const lightDistance = Math.max(size * 0.55, 0.001);
  dirLight.position.copy(controls.target).addScaledVector(
    new THREE.Vector3(-0.55, 0.82, 0.35).normalize(), lightDistance);
  lightHandle.position.copy(dirLight.position);
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
