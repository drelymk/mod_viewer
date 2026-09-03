// Temporary visualization for the experimental inferred skinning rig.
// Nothing in this group is a model mesh or a THREE.Bone; it is disposable
// diagnostic geometry owned entirely by the Rig panel.

import * as THREE from 'three/webgpu';

function vector(value) {
  if (value?.isVector3) return value.clone();
  return new THREE.Vector3(
    Number(value?.[0]) || 0,
    Number(value?.[1]) || 0,
    Number(value?.[2]) || 0);
}

function sourceFor(snapshot) {
  return (snapshot?.sources || []).find(source =>
    source.sourceKey === snapshot.activeSourceKey) || null;
}

function componentFor(source, boneId) {
  const id = source?.components?.find(component =>
    component.nodeIds.includes(Number(boneId)));
  return id || null;
}

function safeSetVisible(object, visible) {
  if (object) object.visible = !!visible;
}

// Source checkouts may not have the optional vendored TransformControls asset
// until the next packaged build. Keep the experimental panel usable with a
// small rotation-only proxy; packaged builds prefer the official addon below.
class RotationOnlyProxyControls extends THREE.Object3D {
  constructor(camera, canvas, isPicking) {
    super();
    this.camera = camera;
    this.canvas = canvas;
    this.isPicking = isPicking;
    this.object = null;
    this.mode = 'rotate';
    this.space = 'local';
    this.listeners = new Map();
    this.pointerId = null;
    this.lastX = 0;
    this.onPointerDown = event => {
      if (!this.object || this.isPicking?.() || event.button !== 0
          || this.pointerId !== null) return;
      event.preventDefault();
      this.pointerId = event.pointerId;
      this.lastX = event.clientX;
      this.canvas?.setPointerCapture?.(event.pointerId);
      this.dispatchEvent({type: 'dragging-changed', value: true});
    };
    this.onPointerMove = event => {
      if (event.pointerId !== this.pointerId || !this.object) return;
      event.preventDefault();
      const delta = Number(event.clientX) - this.lastX;
      this.lastX = event.clientX;
      const rotation = new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 1, 0), delta * .01);
      this.object.quaternion.premultiply(rotation).normalize();
      this.dispatchEvent({type: 'change'});
    };
    this.onPointerUp = event => {
      if (event.pointerId !== this.pointerId) return;
      this.pointerId = null;
      this.canvas?.releasePointerCapture?.(event.pointerId);
      this.dispatchEvent({type: 'dragging-changed', value: false});
    };
    canvas?.addEventListener('pointerdown', this.onPointerDown);
    canvas?.addEventListener('pointermove', this.onPointerMove);
    canvas?.addEventListener('pointerup', this.onPointerUp);
    canvas?.addEventListener('pointercancel', this.onPointerUp);
  }

  setMode(mode) { this.mode = mode; return this; }
  setSpace(space) { this.space = space; return this; }
  attach(object) { this.object = object; return this; }
  detach() { this.object = null; return this; }
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  dispatchEvent(event) {
    this.listeners.get(event.type)?.forEach(listener => listener.call(this, event));
  }
  dispose() {
    this.canvas?.removeEventListener('pointerdown', this.onPointerDown);
    this.canvas?.removeEventListener('pointermove', this.onPointerMove);
    this.canvas?.removeEventListener('pointerup', this.onPointerUp);
    this.canvas?.removeEventListener('pointercancel', this.onPointerUp);
    this.listeners.clear();
    this.detach();
  }
}

export function createRigOverlayController({
  scene, camera, canvas, getMeshes, getRigState, getRigDebugState,
  setRigBoneRotation, requestRender,
} = {}) {
  const group = new THREE.Group();
  group.name = 'viewer-inferred-rig-overlay';
  group.userData.isViewerRigOverlay = true;
  group.visible = false;
  scene?.add(group);

  const centerMaterial = new THREE.MeshBasicMaterial({
    color: 0x7dd3fc, depthTest: false, depthWrite: false,
  });
  const jointMaterial = new THREE.MeshBasicMaterial({
    color: 0x34d399, depthTest: false, depthWrite: false,
  });
  const selectedMaterial = new THREE.MeshBasicMaterial({
    color: 0xfacc15, depthTest: false, depthWrite: false,
  });
  const lineMaterial = new THREE.LineBasicMaterial({
    color: 0x60a5fa, depthTest: false, depthWrite: false,
  });
  const rootMaterial = new THREE.MeshBasicMaterial({
    color: 0xfb7185, depthTest: false, depthWrite: false,
  });
  const sphereGeometry = new THREE.SphereGeometry(0.012, 8, 6);
  const proxy = new THREE.Object3D();
  proxy.name = 'viewer-inferred-rig-pose-proxy';
  proxy.userData.isViewerRigOverlay = true;
  // Keep a visible handle while the optional official TransformControls addon
  // is loading (or when a source checkout has not packaged it yet).
  const proxyRing = new THREE.Mesh(
    new THREE.TorusGeometry(0.07, 0.004, 8, 32), selectedMaterial);
  proxyRing.name = 'viewer-inferred-rig-pose-handle';
  proxyRing.raycast = () => {};
  proxy.add(proxyRing);
  group.add(proxy);

  let transformControls = null;
  let transformControlsReady = null;
  let activeSourceKey = null;
  let selectedBoneId = null;
  let disposed = false;

  function clearChildren() {
    while (group.children.some(child => child !== proxy)) {
      const child = group.children.find(item => item !== proxy);
      group.remove(child);
      child.geometry?.dispose?.();
      if (child.material && child.material !== centerMaterial
          && child.material !== jointMaterial
          && child.material !== selectedMaterial
          && child.material !== lineMaterial
          && child.material !== rootMaterial) {
        child.material.dispose?.();
      }
    }
  }

  function removeControls() {
    if (!transformControls) return;
    transformControls.detach?.();
    scene?.remove(transformControls);
    transformControls.dispose?.();
    transformControls = null;
  }

  function syncProxy(snapshot, source) {
    const boneId = Number(snapshot?.selectedBoneId);
    const component = componentFor(source, boneId);
    const root = component?.rootId === boneId;
    if (!source || !Number.isInteger(boneId) || root) {
      removeControls();
      proxy.visible = false;
      return;
    }
    const debug = getRigDebugState?.(source.sourceKey);
    const pivot = debug?.jointPivotByBoneId?.[boneId]
      || debug?.jointPivotByBoneId?.get?.(boneId)
      || [0, 0, 0];
    proxy.position.copy(vector(pivot));
    proxy.visible = true;
    if (debug?.poseRotationByBoneId?.[boneId]) {
      const values = debug.poseRotationByBoneId[boneId];
      proxy.quaternion.set(...values).normalize();
    } else {
      proxy.quaternion.identity();
    }
    if (!transformControls && transformControlsReady) {
      transformControlsReady.then(() => {
        if (!disposed) syncProxy(snapshot, source);
      });
    }
    transformControls?.attach?.(proxy);
  }

  function updateModelFrame() {
    const mesh = [...(getMeshes?.() || [])].find(item => item?.visible)
      || getMeshes?.()?.[0];
    if (!mesh) return;
    mesh.updateWorldMatrix?.(true, false);
    // Active model meshes are direct scene children. Copying this transform
    // keeps overlay points in model space when the user turns, tilts, or
    // translates the model; pose quaternions remain source-local.
    group.position.copy(mesh.position);
    group.quaternion.copy(mesh.quaternion);
    group.scale.copy(mesh.scale);
  }

  function refresh(snapshot = getRigState?.()) {
    if (disposed) return;
    const source = sourceFor(snapshot);
    activeSourceKey = source?.sourceKey || null;
    selectedBoneId = Number.isInteger(Number(snapshot?.selectedBoneId))
      ? Number(snapshot.selectedBoneId) : null;
    updateModelFrame();
    clearChildren();
    safeSetVisible(group, !!snapshot?.visible && !!source);
    if (!source || !snapshot?.visible) {
      removeControls();
      requestRender?.();
      return;
    }
    const debug = getRigDebugState?.(source.sourceKey) || source;
    const nodeCenter = new Map((debug.nodes || source.nodes || [])
      .map(node => [Number(node.boneId), node.weightedCenter]));
    const linePositions = [];
    (debug.relationships || []).forEach(edge => {
      const first = nodeCenter.get(Number(edge.boneA));
      const second = nodeCenter.get(Number(edge.boneB));
      if (!first || !second) return;
      linePositions.push(...first, ...second);
    });
    if (linePositions.length) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(
        linePositions, 3));
      const lines = new THREE.LineSegments(geometry, lineMaterial);
      lines.renderOrder = 10;
      lines.raycast = () => {};
      group.add(lines);
    }
    nodeCenter.forEach((center, boneId) => {
      const component = componentFor(source, boneId);
      const material = boneId === selectedBoneId ? selectedMaterial
        : component?.rootId === boneId ? rootMaterial : centerMaterial;
      const point = new THREE.Mesh(sphereGeometry, material);
      point.position.copy(vector(center));
      point.scale.setScalar(boneId === selectedBoneId ? 1.7 : 1);
      point.renderOrder = 11;
      point.raycast = () => {};
      group.add(point);
    });
    (debug.relationships || []).forEach(edge => {
      if (!edge.jointCenter) return;
      const joint = new THREE.Mesh(sphereGeometry, jointMaterial);
      joint.position.copy(vector(edge.jointCenter));
      joint.scale.setScalar(.8);
      joint.renderOrder = 12;
      joint.raycast = () => {};
      group.add(joint);
    });
    syncProxy(snapshot, source);
    requestRender?.();
  }

  async function ensureTransformControls() {
    if (transformControlsReady) return transformControlsReady;
    transformControlsReady = import('three/addons/controls/TransformControls.js')
      .then(module => {
        if (disposed || !module?.TransformControls) return null;
        transformControls = new module.TransformControls(camera, canvas);
        transformControls.setMode?.('rotate');
        transformControls.setSpace?.('local');
        transformControls.addEventListener?.('change', () => {
          const snapshot = getRigState?.();
          const source = sourceFor(snapshot);
          const boneId = Number(snapshot?.selectedBoneId);
          if (!source || !Number.isInteger(boneId)) return;
          setRigBoneRotation?.(source.sourceKey, boneId, proxy.quaternion.clone());
          requestRender?.();
        });
        transformControls.addEventListener?.('dragging-changed', event => {
          // Arcball remains the camera owner; TransformControls only owns the
          // active rotation gesture while its proxy is being dragged.
          if (event.value !== undefined && canvas?.style) {
            canvas.style.cursor = event.value ? 'grabbing' : '';
          }
        });
        scene?.add(transformControls);
        return transformControls;
      })
      .catch(() => {
        if (disposed) return null;
        transformControls = new RotationOnlyProxyControls(
          camera, canvas, () => getRigState?.()?.picking);
        transformControls.setMode('rotate').setSpace('local');
        transformControls.addEventListener('change', () => {
          const snapshot = getRigState?.();
          const source = sourceFor(snapshot);
          const boneId = Number(snapshot?.selectedBoneId);
          if (!source || !Number.isInteger(boneId)) return;
          setRigBoneRotation?.(source.sourceKey, boneId, proxy.quaternion.clone());
          requestRender?.();
        });
        transformControls.addEventListener('dragging-changed', event => {
          if (event.value !== undefined && canvas?.style) {
            canvas.style.cursor = event.value ? 'grabbing' : '';
          }
        });
        scene?.add(transformControls);
        return transformControls;
      });
    await transformControlsReady;
    return transformControls;
  }

  // TransformControls is loaded only after Rig is opened. This keeps the
  // normal model path independent of the optional experimental UI asset.
  transformControlsReady = null;

  const onRigChanged = event => {
    const snapshot = event.detail || getRigState?.();
    if (snapshot?.visible) void ensureTransformControls().then(() => refresh(snapshot));
    else refresh(snapshot);
  };
  const onModelTransformChanged = () => refresh();
  window.addEventListener('mod-viewer-model-rig-changed', onRigChanged);
  window.addEventListener('mod-viewer-model-transform-changed', onModelTransformChanged);

  return {
    group,
    refresh,
    ensureTransformControls,
    dispose() {
      disposed = true;
      window.removeEventListener('mod-viewer-model-rig-changed', onRigChanged);
      window.removeEventListener('mod-viewer-model-transform-changed', onModelTransformChanged);
      removeControls();
      clearChildren();
      group.remove(proxy);
      proxyRing.geometry.dispose();
      sphereGeometry.dispose();
      centerMaterial.dispose();
      jointMaterial.dispose();
      selectedMaterial.dispose();
      lineMaterial.dispose();
      rootMaterial.dispose();
      scene?.remove(group);
    },
  };
}
