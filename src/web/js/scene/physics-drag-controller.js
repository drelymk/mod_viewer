import * as THREE from 'three/webgpu';

const RIGHT_BUTTON = 2;
const DRAG_THRESHOLD_PIXELS = 4;
const MAX_NORMALIZED_SPEED = 4;

function finiteTime(event, fallback) {
  const time = Number(event?.timeStamp);
  return Number.isFinite(time) && time >= 0 ? time : fallback;
}

function clampSpeed(x, y) {
  const magnitude = Math.hypot(x, y);
  if (magnitude <= MAX_NORMALIZED_SPEED) return [x, y];
  const scale = MAX_NORMALIZED_SPEED / magnitude;
  return [x * scale, y * scale];
}

/** Owns only the RMB gesture used to feed virtual physics motion. */
export function createPhysicsDragController({
  canvas, camera, controls, onMotion, requestRender: requestFrame,
} = {}) {
  let enabled = false;
  let pointer = null;

  function emit(velocity, active) {
    onMotion?.({
      normalizedLinearVelocityWorld: velocity,
      active,
      source: 'rmb-drag',
    });
    requestFrame?.();
  }

  function release(event) {
    if (!pointer || (event?.pointerId !== undefined
        && event.pointerId !== pointer.id)) return;
    const current = pointer;
    pointer = null;
    if (current.moved) emit([0, 0, 0], false);
    if (canvas?.hasPointerCapture?.(current.id)) {
      try {
        canvas.releasePointerCapture(current.id);
      } catch {
        // Synthetic test events and canceled browser pointers may not have a
        // live capture to release.
      }
    }
  }

  function pointerDown(event) {
    if (!enabled || event.button !== RIGHT_BUTTON || pointer) return;
    event.preventDefault();
    const now = finiteTime(event, performance.now());
    pointer = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      x: event.clientX,
      y: event.clientY,
      time: now,
      moved: false,
    };
    try {
      canvas.setPointerCapture?.(event.pointerId);
    } catch {
      // Pointer capture is best effort for non-primary or synthetic pointers.
    }
  }

  function pointerMove(event) {
    if (!enabled || !pointer || event.pointerId !== pointer.id) return;
    event.preventDefault();
    const now = finiteTime(event, performance.now());
    const elapsed = Math.min(.05, Math.max(1000 / 240,
      now - pointer.time) / 1000);
    const dx = event.clientX - pointer.x;
    const dy = event.clientY - pointer.y;
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    pointer.time = now;
    if (!pointer.moved && Math.hypot(
        event.clientX - pointer.startX, event.clientY - pointer.startY)
        < DRAG_THRESHOLD_PIXELS) return;
    pointer.moved = true;
    const rect = canvas.getBoundingClientRect?.();
    const height = Number(rect?.height) || Number(canvas.clientHeight) || 1;
    const [x, y] = clampSpeed(dx / height / elapsed, -dy / height / elapsed);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
    emit(right.multiplyScalar(x).add(up.multiplyScalar(y)).toArray(), true);
  }

  function pointerUp(event) {
    if (!enabled || event.button !== RIGHT_BUTTON) return;
    event.preventDefault();
    release(event);
  }

  function pointerCancel(event) {
    if (!enabled) return;
    event.preventDefault();
    release(event);
  }

  function contextMenu(event) {
    if (!enabled) return;
    event.preventDefault();
  }

  function setEnabled(value) {
    const next = !!value;
    if (next === enabled) return enabled;
    if (!next) release();
    enabled = next;
    if (enabled) {
      controls?.unsetMouseAction?.(RIGHT_BUTTON);
    } else {
      controls?.setMouseAction?.('PAN', RIGHT_BUTTON);
    }
    return enabled;
  }

  canvas?.addEventListener('pointerdown', pointerDown);
  canvas?.addEventListener('pointermove', pointerMove);
  canvas?.addEventListener('pointerup', pointerUp);
  canvas?.addEventListener('pointercancel', pointerCancel);
  canvas?.addEventListener('lostpointercapture', pointerCancel);
  canvas?.addEventListener('contextmenu', contextMenu);

  return {
    setEnabled,
    isEnabled: () => enabled,
    dispose() {
      release();
      setEnabled(false);
      canvas?.removeEventListener('pointerdown', pointerDown);
      canvas?.removeEventListener('pointermove', pointerMove);
      canvas?.removeEventListener('pointerup', pointerUp);
      canvas?.removeEventListener('pointercancel', pointerCancel);
      canvas?.removeEventListener('lostpointercapture', pointerCancel);
      canvas?.removeEventListener('contextmenu', contextMenu);
    },
  };
}
