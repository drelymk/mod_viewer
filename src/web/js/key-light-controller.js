// Movable, depth-tested inspection light and its viewport interaction.

import * as THREE from 'three';

function createLightHandle() {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 64;
  const context = canvas.getContext('2d');
  const glow = context.createRadialGradient(32, 32, 4, 32, 32, 30);
  glow.addColorStop(0, 'rgba(255,248,190,1)');
  glow.addColorStop(0.35, 'rgba(255,216,102,.95)');
  glow.addColorStop(0.7, 'rgba(255,184,70,.4)');
  glow.addColorStop(1, 'rgba(255,184,70,0)');
  context.fillStyle = glow;
  context.fillRect(0, 0, 64, 64);
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
  const modes = ['double', 'current', 'off'];
  let modeIndex = 0;
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
      if (object.isMesh && !object.userData.isViewerOutline) {
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

  function toggleMode() {
    modeIndex = (modeIndex + 1) % modes.length;
    const mode = modes[modeIndex];
    handle.visible = mode !== 'off';
    light.intensity = mode === 'double' ? 1 : (mode === 'current' ? 0.5 : 0);
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
    updateCursor();
    onChange?.();
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
    const multiplier = modes[modeIndex] === 'double' ? 2 : 1;
    const size = Math.max(
      camera.position.distanceTo(controls.target) * 0.035 * multiplier,
      0.0001);
    handle.scale.set(size, size, 1);
  }

  return { toggleMode, rebase, update };
}
