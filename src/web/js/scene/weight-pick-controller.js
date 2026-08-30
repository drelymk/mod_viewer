// Owns the one-shot LMB gesture used by the Weight panel's model picker.

import { raycastModelAtClientPoint } from './model-picking.js';

const LEFT_BUTTON = 0;
const DRAG_THRESHOLD_PIXELS = 5;

export function createWeightPickController({
  canvas, controls, camera, getMeshes, onPick, requestRender,
} = {}) {
  let enabled = false;
  let pointer = null;

  function restoreControls() {
    controls?.setMouseAction?.('ROTATE', LEFT_BUTTON);
    if (canvas?.style) canvas.style.cursor = '';
  }

  function finish(intersection) {
    const current = pointer;
    pointer = null;
    enabled = false;
    if (current?.id !== undefined && canvas?.hasPointerCapture?.(current.id)) {
      try { canvas.releasePointerCapture(current.id); } catch { /* best effort */ }
    }
    restoreControls();
    onPick?.(intersection || null);
    requestRender?.();
  }

  function begin() {
    if (enabled) return false;
    enabled = true;
    pointer = null;
    controls?.unsetMouseAction?.(LEFT_BUTTON);
    if (canvas?.style) canvas.style.cursor = 'crosshair';
    requestRender?.();
    return true;
  }

  function cancel() {
    if (!enabled) return false;
    finish(null);
    return true;
  }

  function pointerDown(event) {
    if (!enabled || event.button !== LEFT_BUTTON || pointer) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    pointer = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
    };
    try { canvas.setPointerCapture?.(event.pointerId); } catch { /* best effort */ }
  }

  function pointerMove(event) {
    if (!enabled || !pointer || event.pointerId !== pointer.id) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (Math.hypot(
      event.clientX - pointer.startX, event.clientY - pointer.startY)
      > DRAG_THRESHOLD_PIXELS) pointer.moved = true;
  }

  function pointerUp(event) {
    if (!enabled || !pointer || event.button !== LEFT_BUTTON
        || event.pointerId !== pointer.id) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const moved = pointer.moved || Math.hypot(
      event.clientX - pointer.startX, event.clientY - pointer.startY)
      > DRAG_THRESHOLD_PIXELS;
    if (moved) {
      finish(null);
      return;
    }
    const intersection = raycastModelAtClientPoint({
      clientX: event.clientX,
      clientY: event.clientY,
      canvas,
      camera,
      meshes: getMeshes?.() || [],
    });
    finish(intersection);
  }

  function pointerCancel(event) {
    if (!enabled || !pointer || (event.pointerId !== undefined
        && event.pointerId !== pointer.id)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    finish(null);
  }

  function keyDown(event) {
    if (enabled && event.key === 'Escape') {
      event.preventDefault();
      cancel();
    }
  }

  canvas?.addEventListener('pointerdown', pointerDown);
  canvas?.addEventListener('pointermove', pointerMove);
  canvas?.addEventListener('pointerup', pointerUp);
  canvas?.addEventListener('pointercancel', pointerCancel);
  canvas?.addEventListener('lostpointercapture', pointerCancel);
  document.addEventListener('keydown', keyDown);

  return {
    begin,
    cancel,
    isEnabled: () => enabled,
    dispose() {
      if (enabled) finish(null);
      canvas?.removeEventListener('pointerdown', pointerDown);
      canvas?.removeEventListener('pointermove', pointerMove);
      canvas?.removeEventListener('pointerup', pointerUp);
      canvas?.removeEventListener('pointercancel', pointerCancel);
      canvas?.removeEventListener('lostpointercapture', pointerCancel);
      document.removeEventListener('keydown', keyDown);
    },
  };
}
