// Static viewer scenery; model state is supplied only at deliberate placement boundaries.

import * as THREE from 'three/webgpu';
import { color, mix, mx_noise_float, positionGeometry, smoothstep, uv } from 'three/tsl';
import { computeModelBounds } from './model-bounds.js';

function createGround() {
  const variation = mx_noise_float(positionGeometry.xz.mul(0.6)).mul(0.5).add(0.5);
  const patches = smoothstep(0.64, 0.85, mx_noise_float(positionGeometry.xz.mul(0.2)).mul(0.5).add(0.5));
  const material = new THREE.MeshBasicNodeMaterial({
    transparent: true,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    side: THREE.DoubleSide,
  });
  material.colorNode = mix(mix(color(0x526b36), color(0x78864b), variation), color(0x706343), patches);
  material.opacityNode = smoothstep(0.72, 1, uv().sub(0.5).length().mul(2)).oneMinus();
  const ground = new THREE.Mesh(new THREE.CircleGeometry(12, 64), material);
  ground.rotation.x = -Math.PI / 2;
  ground.renderOrder = -2;
  return ground;
}

export function createSceneryController({ scene }) {
  const group = new THREE.Group();
  group.name = 'Nature scenery';
  group.userData.isViewerScenery = true;
  group.visible = false;
  scene.add(group);
  let meshes = new Set();
  let enabled = false;
  let disposed = false;
  let groundLevel = null;

  function updatePlacement() {
    if (disposed) return;
    group.visible = false;
    groundLevel = null;
    if (!enabled) return;
    const bounds = computeModelBounds([...meshes], { visibleOnly: true });
    if (bounds.isEmpty()) return;
    const size = bounds.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.y, size.z);
    if (!(span > 0)) return;
    if (!group.children.length) {
      group.add(createGround());
      group.traverse((object) => {
        if (object.isMesh) object.raycast = () => {};
      });
    }
    const center = bounds.getCenter(new THREE.Vector3());
    groundLevel = bounds.min.y;
    // Keep the existing transparent shadow receiver just above the solid ground.
    group.position.set(center.x, groundLevel - size.length() * 0.0002, center.z);
    group.scale.setScalar(span);
    group.visible = true;
  }

  function setEnabled(value) {
    enabled = !!value;
    updatePlacement();
  }

  function setMeshes(nextMeshes = []) {
    meshes = new Set(nextMeshes.filter(Boolean));
    updatePlacement();
  }

  function adoptMeshes(nextMeshes = []) {
    for (const mesh of nextMeshes) if (mesh) meshes.add(mesh);
    updatePlacement();
  }

  function forgetMeshes(removed = []) {
    for (const mesh of removed) meshes.delete(mesh);
    updatePlacement();
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    const resources = new Set();
    group.traverse((object) => {
      if (object.geometry) resources.add(object.geometry);
      if (object.material) resources.add(object.material);
      if (object.material?.map) resources.add(object.material.map);
      if (object.isInstancedMesh) resources.add(object);
    });
    for (const resource of resources) resource.dispose();
    group.removeFromParent();
    group.clear();
    meshes.clear();
    group.visible = false;
    groundLevel = null;
  }

  return {
    setEnabled,
    setMeshes,
    adoptMeshes,
    forgetMeshes,
    updatePlacement,
    getGroundLevel: () => (group.visible ? groundLevel : null),
    dispose,
  };
}
