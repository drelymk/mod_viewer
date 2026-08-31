// Movable, depth-tested inspection light and its viewport interaction.

import * as THREE from 'three';

export const KEY_LIGHT_MAX_INTENSITY = 1.5;
export const DEFAULT_KEY_LIGHT_INTENSITY = 1.0;

function createLightHandle() {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 128;
  const context = canvas.getContext('2d');
  const center = 64;
  const halo = context.createRadialGradient(center, center, 10, center, center, 52);
  halo.addColorStop(0, 'rgba(255,240,170,.25)');
  halo.addColorStop(0.4, 'rgba(255,214,115,.14)');
  halo.addColorStop(0.72, 'rgba(255,198,86,.04)');
  halo.addColorStop(1, 'rgba(255,190,70,0)');
  context.fillStyle = halo;
  context.fillRect(0, 0, 128, 128);

  const innerGlow = context.createRadialGradient(center, center, 3, center, center, 31);
  innerGlow.addColorStop(0, 'rgba(255,253,240,.92)');
  innerGlow.addColorStop(0.32, 'rgba(255,245,190,.72)');
  innerGlow.addColorStop(0.7, 'rgba(255,218,111,.3)');
  innerGlow.addColorStop(1, 'rgba(255,198,86,0)');
  context.fillStyle = innerGlow;
  context.fillRect(0, 0, 128, 128);

  const core = context.createRadialGradient(60, 59, 1, center, center, 16);
  core.addColorStop(0, '#fffdf0');
  core.addColorStop(0.45, 'rgba(255,248,205,.98)');
  core.addColorStop(0.8, 'rgba(255,224,126,.7)');
  core.addColorStop(1, 'rgba(246,201,93,0)');
  context.fillStyle = core;
  context.fillRect(0, 0, 128, 128);
  const handle = new THREE.Sprite(new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(canvas), transparent: true,
    // Model depth must occlude the marker instead of showing through meshes.
    depthTest: true, depthWrite: false,
  }));
  handle.renderOrder = 1000;
  handle.visible = true;
  return handle;
}

export function createKeyLightController({
  scene, camera, renderer, controls, light, onChange,
}) {
  const handle = createLightHandle();
  scene.add(handle);

  let drag = null;
  let pointerInside = false;
  let intensity = Number.isFinite(light.intensity)
    ? Math.min(KEY_LIGHT_MAX_INTENSITY, Math.max(0, light.intensity))
    : DEFAULT_KEY_LIGHT_INTENSITY;
  light.intensity = intensity;
  handle.visible = intensity > 0;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const dragPlane = new THREE.Plane();
  const hit = new THREE.Vector3();

  function updatePointer(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
  }

  function canInteract(event = null) {
    if (event) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointerInside = event.clientX >= rect.left && event.clientX <= rect.right
        && event.clientY >= rect.top && event.clientY <= rect.bottom;
      updatePointer(event);
    }
    if (!pointerInside || !handle.visible) return false;

    // Only raycast model triangles after the cheap sprite candidate test.
    const handleHit = raycaster.intersectObject(handle, false)[0];
    if (!handleHit) return false;
    const visibleMeshes = [];
    scene.traverseVisible(object => {
      if (object.isMesh
          && !object.userData.isViewerOutline
          && !object.userData.isViewerGround) {
        visibleMeshes.push(object);
      }
    });
    const blocker = raycaster.intersectObjects(visibleMeshes, false)[0];
    return !blocker || blocker.distance >= handleHit.distance;
  }

  function updateCursor(event = null) {
    if (drag) return;
    renderer.domElement.style.cursor = canInteract(event) ? 'crosshair' : '';
  }

  renderer.domElement.addEventListener('pointerenter', event => {
    pointerInside = true;
    updateCursor(event);
  });
  renderer.domElement.addEventListener('pointerleave', () => {
    pointerInside = false;
    if (!drag) renderer.domElement.style.cursor = '';
  });
  renderer.domElement.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !canInteract(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const cameraDirection = camera.getWorldDirection(new THREE.Vector3());
    dragPlane.setFromNormalAndCoplanarPoint(cameraDirection, light.position);
    drag = {
      pointerId: event.pointerId,
      depthMode: event.shiftKey,
      startY: event.clientY,
      startPosition: light.position.clone(),
      cameraDirection,
      depthScale: Math.max(camera.position.distanceTo(controls.target) * 0.004, 0.0001),
    };
    controls.enabled = false;
    renderer.domElement.setPointerCapture(event.pointerId);
    renderer.domElement.style.cursor = 'grabbing';
  }, { capture: true });
  renderer.domElement.addEventListener('pointermove', event => {
    if (!drag) {
      pointerInside = true;
      updateCursor(event);
      return;
    }
    if (event.pointerId !== drag.pointerId) return;
    if (drag.depthMode) {
      light.position.copy(drag.startPosition).addScaledVector(
        drag.cameraDirection, (drag.startY - event.clientY) * drag.depthScale);
      onChange?.();
      return;
    }
    updatePointer(event);
    if (raycaster.ray.intersectPlane(dragPlane, hit)) {
      light.position.copy(hit);
      onChange?.();
    }
  });

  function finishDrag(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (renderer.domElement.hasPointerCapture?.(event.pointerId)) {
      renderer.domElement.releasePointerCapture(event.pointerId);
    }
    drag = null;
    controls.enabled = true;
    updateCursor(event);
  }
  renderer.domElement.addEventListener('pointerup', finishDrag);
  renderer.domElement.addEventListener('pointercancel', finishDrag);

  function setIntensity(value) {
    const number = Number(value);
    const next = Number.isNaN(number)
      ? 0 : Math.min(KEY_LIGHT_MAX_INTENSITY, Math.max(0, number));
    if (next === intensity) return false;
    intensity = next;
    light.intensity = intensity;
    handle.visible = intensity > 0;
    updateCursor();
    onChange?.();
    return true;
  }

  function rebase(modelSize) {
    const distance = Math.max(modelSize * 0.55, 0.001);
    light.position.copy(controls.target).addScaledVector(
      new THREE.Vector3(-0.55, 0.82, 0.35).normalize(), distance);
    handle.position.copy(light.position);
    onChange?.();
  }

  function update() {
    light.target.position.copy(controls.target);
    handle.position.copy(light.position);
    const normalized = THREE.MathUtils.clamp(
      intensity / KEY_LIGHT_MAX_INTENSITY, 0, 1);
    const sizeMultiplier = 0.75 + normalized * 1.25;
    const size = Math.max(
      camera.position.distanceTo(controls.target) * 0.035 * sizeMultiplier,
      0.0001);
    handle.scale.set(size, size, 1);
  }

  return { setIntensity, getIntensity: () => intensity, rebase, update };
}
