// Blender-style DOM view gizmo: snap, orbit drag, keyboard and wheel zoom.

import * as THREE from 'three';

export function createViewGizmoController({ camera, controls, element, onChange }) {
  const axes = [...element.querySelectorAll('.gizmo-axis')];
  const axisVectors = {
    x: new THREE.Vector3(1, 0, 0),
    y: new THREE.Vector3(0, 1, 0),
    z: new THREE.Vector3(0, 0, 1),
  };
  const local = new THREE.Vector3();
  const inverseCamera = new THREE.Quaternion();
  let visible = true;
  let snap = null;
  let drag = null;

  function snapToAxis(axisName, sign) {
    const targetDirection = axisVectors[axisName].clone().multiplyScalar(sign);
    const startDirection = camera.position.clone().sub(controls.target).normalize();
    const turn = new THREE.Quaternion().setFromUnitVectors(startDirection, targetDirection);
    const endUp = axisName === 'y'
      ? new THREE.Vector3(0, 0, sign > 0 ? -1 : 1)
      : new THREE.Vector3(0, 1, 0);
    snap = {
      started: performance.now(),
      duration: 190,
      distance: camera.position.distanceTo(controls.target),
      startDirection,
      turn,
      startUp: camera.up.clone().normalize(),
      endUp,
    };
    onChange?.();
  }

  function updateSnap() {
    if (!snap) return false;
    const raw = Math.min(1, (performance.now() - snap.started) / snap.duration);
    const progress = 1 - Math.pow(1 - raw, 3);
    const rotation = new THREE.Quaternion().slerpQuaternions(
      new THREE.Quaternion(), snap.turn, progress);
    const direction = snap.startDirection.clone().applyQuaternion(rotation).normalize();
    camera.position.copy(controls.target).addScaledVector(direction, snap.distance);
    camera.up.lerpVectors(snap.startUp, snap.endUp, progress).normalize();
    camera.lookAt(controls.target);
    if (raw === 1) snap = null;
    return !!snap;
  }

  function updateAxes() {
    if (!visible) return;
    inverseCamera.copy(camera.quaternion).invert();
    const projected = axes.map(axis => {
      const sign = Number(axis.dataset.sign);
      local.copy(axisVectors[axis.dataset.axis]).multiplyScalar(sign)
        .applyQuaternion(inverseCamera);
      return {
        axis,
        x: 52 + local.x * 34,
        y: 52 - local.y * 34,
        depth: local.z,
      };
    });
    // Reorder only when depth order changes; needless SVG reparenting can make
    // a pressed axis lose its click before pointerup.
    const svg = element.querySelector('svg');
    projected.sort((a, b) => a.depth - b.depth);
    const currentOrder = [...svg.querySelectorAll(':scope > .gizmo-axis')];
    if (projected.some(({ axis }, index) => currentOrder[index] !== axis)) {
      projected.forEach(({ axis }) => svg.appendChild(axis));
      svg.appendChild(element.querySelector('.gizmo-origin'));
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

  axes.forEach(axis => {
    axis.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        snapToAxis(axis.dataset.axis, Number(axis.dataset.sign));
      }
    });
  });

  element.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    snap = null;
    drag = {
      x: event.clientX,
      y: event.clientY,
      moved: false,
      pointerId: event.pointerId,
      axis: event.target.closest?.('.gizmo-axis') || null,
    };
  });
  element.addEventListener('pointermove', event => {
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    // Do not capture until the threshold is crossed: tiny movement remains a
    // click-to-snap gesture.
    if (Math.abs(dx) + Math.abs(dy) > 2 && !drag.moved) {
      drag.moved = true;
      element.setPointerCapture(drag.pointerId);
      element.classList.add('dragging');
    }
    if (!drag.moved) return;
    const offset = camera.position.clone().sub(controls.target);
    const spherical = new THREE.Spherical().setFromVector3(offset);
    spherical.theta -= dx * 0.012;
    spherical.phi = THREE.MathUtils.clamp(
      spherical.phi + dy * 0.012, 0.025, Math.PI - 0.025);
    camera.position.copy(controls.target).add(offset.setFromSpherical(spherical));
    camera.up.set(0, 1, 0);
    camera.lookAt(controls.target);
    onChange?.();
    drag.x = event.clientX;
    drag.y = event.clientY;
  });

  function finishDrag(event, cancelled = false) {
    if (!drag) return;
    const finished = drag;
    if (!cancelled && !finished.moved && finished.axis) {
      snapToAxis(finished.axis.dataset.axis, Number(finished.axis.dataset.sign));
    }
    if (element.hasPointerCapture?.(event.pointerId)) {
      element.releasePointerCapture(event.pointerId);
    }
    element.classList.remove('dragging');
    drag = null;
  }
  element.addEventListener('pointerup', finishDrag);
  element.addEventListener('pointercancel', event => finishDrag(event, true));
  element.addEventListener('wheel', event => {
    event.preventDefault();
    snap = null;
    const offset = camera.position.clone().sub(controls.target);
    const scale = Math.exp(event.deltaY * 0.0015);
    const distance = THREE.MathUtils.clamp(
      offset.length() * scale, Math.max(camera.near * 4, 0.0001), camera.far * 0.8);
    camera.position.copy(controls.target).addScaledVector(offset.normalize(), distance);
    onChange?.();
  }, { passive: false });

  function toggle() {
    visible = !visible;
    element.classList.toggle('hidden', !visible);
    const button = document.getElementById('trackball-btn');
    button.classList.toggle('active', visible);
    button.classList.toggle('off', !visible);
    button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', `Toggle navigation gizmo: ${visible ? 'on' : 'off'}`);
  }

  function cancelSnap() {
    snap = null;
  }

  return { cancelSnap, toggle, updateAxes, updateSnap };
}
