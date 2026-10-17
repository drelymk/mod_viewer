// Static viewer scenery; model state is supplied only at deliberate placement boundaries.

import * as THREE from 'three/webgpu';
import { color, mix, mx_noise_float, positionGeometry, smoothstep, uv } from 'three/tsl';
import { computeModelBounds } from './model-bounds.js';

function random(seed) {
  const value = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return value - Math.floor(value);
}

function createGrass() {
  const positions = [],
    colors = [];
  const root = new THREE.Color(0x354d24),
    tip = new THREE.Color(0x87984d);
  for (const cross of [0, Math.PI / 2]) {
    for (let blade = 0; blade < 3; blade++) {
      const x = (blade - 1) * 0.3,
        height = 0.7 + random(blade) * 0.3;
      for (const [px, py, tint] of [
        [x - 0.14, 0, root],
        [x + 0.14, 0, root],
        [x + 0.2, height, tip],
      ]) {
        positions.push(px * Math.cos(cross), py, px * Math.sin(cross));
        colors.push(tint.r, tint.g, tint.b);
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  const grass = new THREE.InstancedMesh(
    geometry,
    new THREE.MeshBasicNodeMaterial({ vertexColors: true, side: THREE.DoubleSide }),
    72,
  );
  const transform = new THREE.Object3D();
  for (let i = 0; i < grass.count; i++) {
    const angle = random(i * 5 + 1) * Math.PI * 2,
      radius = 1.1 + random(i * 5 + 2) * 4.5;
    transform.position.set(Math.cos(angle) * radius, 0, Math.sin(angle) * radius);
    transform.rotation.y = random(i * 5 + 3) * Math.PI;
    const height = 0.035 + random(i * 5 + 4) * 0.055;
    transform.scale.set(height * 0.8, height, height * 0.8);
    transform.updateMatrix();
    grass.setMatrixAt(i, transform.matrix);
    grass.setColorAt(i, new THREE.Color().setScalar(0.85 + random(i * 5 + 5) * 0.3));
  }
  grass.computeBoundingSphere();
  return grass;
}

function createTrees(onLoad) {
  const geometry = new THREE.BufferGeometry();
  const positions = [],
    uvs = [];
  for (const angle of [0, Math.PI / 2]) {
    for (const [x, y, u, v] of [
      [-0.5, 0, 0, 0],
      [0.5, 0, 1, 0],
      [0.5, 1, 1, 1],
      [-0.5, 0, 0, 0],
      [0.5, 1, 1, 1],
      [-0.5, 1, 0, 1],
    ]) {
      positions.push(x * Math.cos(angle), y, x * Math.sin(angle));
      uvs.push(u, v);
    }
  }
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  const texture = new THREE.TextureLoader().load(new URL('../../images/nature-tree.png', import.meta.url).href, onLoad);
  texture.colorSpace = THREE.SRGBColorSpace;
  const trees = new THREE.InstancedMesh(
    geometry,
    new THREE.MeshBasicNodeMaterial({ map: texture, alphaTest: 0.5, alphaToCoverage: true, side: THREE.DoubleSide }),
    8,
  );
  const transform = new THREE.Object3D();
  for (let i = 0; i < trees.count; i++) {
    const side = i % 2 ? 1 : -1;
    transform.position.set(side * (3.5 + random(i * 3 + 1) * 4), 0, -3.5 - random(i * 3 + 2) * 5);
    transform.rotation.y = random(i * 3 + 3) * Math.PI;
    transform.scale.setScalar(2.2 + random(i * 3 + 4) * 1.8);
    transform.updateMatrix();
    trees.setMatrixAt(i, transform.matrix);
  }
  trees.computeBoundingSphere();
  return trees;
}

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

export function createSceneryController({ scene, onVisualChange = null }) {
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
      group.add(
        createGround(),
        createGrass(),
        createTrees(() => {
          if (!disposed) onVisualChange?.();
        }),
      );
      group.traverse((object) => {
        if (object.isMesh) object.raycast = () => {};
      });
    }
    const center = bounds.getCenter(new THREE.Vector3());
    groundLevel = bounds.min.y;
    // Keep the existing transparent shadow receiver just above the solid ground.
    group.position.set(center.x, groundLevel - Math.max(size.length() * 0.0002, 0.000002), center.z);
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
