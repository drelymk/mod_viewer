// Static viewer scenery; model state is supplied only at deliberate placement boundaries.

import * as THREE from 'three/webgpu';
import {
  attribute,
  cameraPosition,
  color,
  Fn,
  mix,
  modelWorldMatrixInverse,
  mx_noise_float,
  output,
  positionGeometry,
  positionLocal,
  smoothstep,
  vec3,
  vec4,
} from 'three/tsl';
import { computeModelBounds } from './model-bounds.js';

function random(seed) {
  const value = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return value - Math.floor(value);
}

function groundHeight(x, z) {
  const edge = THREE.MathUtils.smoothstep(Math.hypot(x, z), 1.8, 6);
  return edge * (0.22 + Math.sin(x * 0.43 + z * 0.21) * 0.28 + Math.cos(z * 0.36 - x * 0.18) * 0.19);
}

function createGrass() {
  const positions = [],
    colors = [];
  const root = new THREE.Color(0x596d3f),
    tip = new THREE.Color(0x89945e);
  for (let blade = 0; blade < 192; blade++) {
    const angle = random(blade * 7 + 1) * Math.PI * 2;
    const radius = Math.sqrt(random(blade * 7 + 2));
    const x = Math.cos(angle) * radius,
      z = Math.sin(angle) * radius;
    const direction = random(blade * 7 + 3) * Math.PI * 2;
    const height = 0.025 + random(blade * 7 + 4) * 0.035;
    const width = 0.004 + random(blade * 7 + 5) * 0.004;
    const bend = (random(blade * 7 + 6) - 0.5) * 0.04;
    for (const [px, py] of [
      [-width, 0],
      [width, 0],
      [bend * 0.5 + width * 0.6, height * 0.55],
      [-width, 0],
      [bend * 0.5 + width * 0.6, height * 0.55],
      [bend * 0.5 - width * 0.6, height * 0.55],
      [bend * 0.5 - width * 0.6, height * 0.55],
      [bend * 0.5 + width * 0.6, height * 0.55],
      [bend, height],
    ]) {
      positions.push(x + px * Math.cos(direction), py - 0.001, z + px * Math.sin(direction));
      const tint = root.clone().lerp(tip, py / height);
      colors.push(tint.r, tint.g, tint.b);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  const material = new THREE.MeshBasicNodeMaterial({ vertexColors: true, side: THREE.DoubleSide });
  const grass = new THREE.InstancedMesh(geometry, material, 48);
  const transform = new THREE.Object3D();
  for (let i = 0; i < grass.count; i++) {
    const angle = i * 2.399963 + random(i + 1) * 0.25;
    const radius = 0.72 + Math.sqrt(random(i * 3 + 2)) * 0.72;
    transform.position.set(Math.cos(angle) * radius, 0, Math.sin(angle) * radius);
    transform.rotation.y = random(i * 3 + 3) * Math.PI * 2;
    const width = 0.24 + random(i * 3 + 4) * 0.08;
    transform.scale.set(width, 0.35 + random(i * 3 + 5) * 0.2, width);
    transform.updateMatrix();
    grass.setMatrixAt(i, transform.matrix);
  }
  grass.computeBoundingSphere();
  return grass;
}

function createTrees(sprite, placements, onLoad) {
  const geometry = new THREE.PlaneGeometry(1, 1);
  geometry.translate(0, 0.5, 0);
  const texture = new THREE.TextureLoader().load(new URL(`../../images/${sprite}`, import.meta.url).href, onLoad);
  texture.colorSpace = THREE.SRGBColorSpace;
  const trees = new THREE.InstancedMesh(
    geometry,
    new THREE.MeshBasicNodeMaterial({
      map: texture,
      alphaTest: 0.5,
      alphaToCoverage: true,
      side: THREE.DoubleSide,
    }),
    placements.length,
  );
  const offsets = [],
    sizes = [];
  const transform = new THREE.Object3D();
  for (let i = 0; i < trees.count; i++) {
    const [x, z, height] = placements[i];
    const width = height * (0.65 + random(i + 1) * 0.35);
    const y = groundHeight(x, z) - 0.012;
    offsets.push(x, y, z);
    sizes.push(width * (i % 2 ? -1 : 1), height);
    transform.position.set(x, y, z);
    transform.scale.set(width, height, width);
    transform.updateMatrix();
    trees.setMatrixAt(i, transform.matrix);
    trees.setColorAt(i, new THREE.Color(0xd7d5bf).multiplyScalar(0.8 + random(i + 2) * 0.15));
  }
  geometry.setAttribute('treeOffset', new THREE.InstancedBufferAttribute(new Float32Array(offsets), 3));
  geometry.setAttribute('treeSize', new THREE.InstancedBufferAttribute(new Float32Array(sizes), 2));
  // Face the camera around the trunk without CPU updates or angle-based alpha clipping.
  trees.material.positionNode = Fn(() => {
    const anchor = attribute('treeOffset', 'vec3');
    const size = attribute('treeSize', 'vec2');
    const view = modelWorldMatrixInverse.mul(vec4(cameraPosition, 1)).xyz.sub(anchor);
    const right = vec3(view.z, 0, view.x.negate()).normalize();
    return anchor.add(right.mul(positionGeometry.x.mul(size.x))).add(vec3(0, positionGeometry.y.mul(size.y), 0));
  })();
  trees.material.outputNode = vec4(
    mix(output.rgb, color(0xc0e6ef), smoothstep(6, 25, positionLocal.xz.length()).mul(0.5)),
    output.a,
  );
  trees.computeBoundingSphere();
  return trees;
}

function createGround() {
  const variation = mx_noise_float(positionGeometry.xz.mul(0.55)).mul(0.5).add(0.5).toVar();
  const detail = mx_noise_float(positionGeometry.xz.mul(95)).mul(0.5).add(0.5).toVar();
  const material = new THREE.MeshStandardNodeMaterial({
    roughness: 1,
    metalness: 0,
    transparent: true,
    forceSinglePass: true,
    depthWrite: true,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    side: THREE.DoubleSide,
  });
  const turf = mix(color(0x596d3f), color(0x8c9160), variation);
  const patches = mix(turf, color(0x88785c), smoothstep(0.56, 0.7, variation).mul(0.6));
  material.colorNode = mix(patches, color(0x4a5838), detail.mul(0.4));
  const radius = positionGeometry.xz.length();
  material.outputNode = vec4(mix(output.rgb, color(0xc0e6ef), smoothstep(3.5, 20, radius).mul(0.95)), output.a);
  material.opacityNode = smoothstep(12, 24, radius.add(variation.mul(2))).oneMinus();
  const geometry = new THREE.BufferGeometry();
  // Concentric rings give the distant edge gentle relief; the clearing stays exactly flat.
  const positions = [0, 0, 0],
    indices = [];
  for (let ring = 1; ring <= 24; ring++) {
    for (let segment = 0; segment < 96; segment++) {
      const angle = (segment / 96) * Math.PI * 2,
        x = Math.cos(angle) * ring,
        z = Math.sin(angle) * ring;
      positions.push(x, groundHeight(x, z), z);
      const current = 1 + (ring - 1) * 96 + segment,
        next = 1 + (ring - 1) * 96 + ((segment + 1) % 96);
      if (ring === 1) indices.push(0, next, current);
      else {
        const previous = current - 96,
          previousNext = next - 96;
        indices.push(previous, previousNext, current, previousNext, next, current);
      }
    }
  }
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  const ground = new THREE.Mesh(geometry, material);
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
  let bounds = new THREE.Box3();

  function applyPlacement() {
    if (disposed) return;
    group.visible = false;
    groundLevel = null;
    if (!enabled) return;
    if (bounds.isEmpty()) return;
    const size = bounds.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.y, size.z);
    if (!(span > 0)) return;
    if (!group.children.length) {
      const onLoad = () => {
        if (!disposed) onVisualChange?.();
      };
      group.add(
        createGround(),
        createGrass(),
        createTrees(
          'nature-tree.png',
          [
            [-3.8, -4.8, 1.9],
            [3.8, -14, 2.4],
            [-8.8, -10, 3.4],
          ],
          onLoad,
        ),
        createTrees(
          'nature-tree-slender.png',
          [
            [5.6, -6.4, 3.2],
            [-5.4, -17, 4.2],
            [11, -15, 4.5],
          ],
          onLoad,
        ),
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

  function updatePlacement() {
    if (disposed) return;
    bounds = computeModelBounds([...meshes], { visibleOnly: true });
    applyPlacement();
  }

  function setEnabled(value) {
    if (enabled === !!value) return;
    enabled = !!value;
    applyPlacement();
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
    bounds.makeEmpty();
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
