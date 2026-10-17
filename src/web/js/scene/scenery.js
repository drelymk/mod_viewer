// Static viewer scenery; model state is supplied only at deliberate placement boundaries.

import * as THREE from 'three/webgpu';
import { color, context, mix, mx_noise_float, output, positionGeometry, smoothstep, vec4 } from 'three/tsl';
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

function canopyGeometry(shape) {
  const sphere = new THREE.IcosahedronGeometry(1, 3);
  const source = sphere.attributes.position;
  const positions = [],
    normals = [],
    colors = [];
  const lobes = shape
    ? [
        [-0.08, 1, 0.04, 0.8, 0.65],
        [0.08, 1.55, -0.06, 0.64, 0.65],
        [-0.03, 2.05, 0.02, 0.43, 0.65],
      ]
    : [
        [-0.55, 1.35, 0.04, 0.58, 0.5],
        [0.48, 1.45, -0.12, 0.62, 0.58],
        [-0.15, 1.8, -0.35, 0.6, 0.57],
        [0.06, 1.6, 0.38, 0.57, 0.52],
        [0.08, 2.03, 0.03, 0.48, 0.48],
      ];
  const deep = new THREE.Color(0x34472c),
    bright = new THREE.Color(0x71834a);
  for (const [cx, cy, cz, width, height] of lobes) {
    for (let i = 0; i < source.count; i++) {
      const x = source.getX(i),
        y = source.getY(i),
        z = source.getZ(i);
      const h = (y + 1) * 0.5;
      const lump = Math.sin(x * 8.3 + y * 5.7 + cx) * Math.sin(z * 9.9 - y * 4.1 + cz);
      const radius = (1 - h * (shape ? 0.6 : 0.16)) * (1 + lump * 0.28);
      const py = cy + y * height;
      positions.push(cx + x * width * radius, py, cz + z * width * radius);
      const normal = new THREE.Vector3(x, 0.35 + y * 0.65, z).normalize();
      normals.push(normal.x, normal.y, normal.z);
      const tint = deep.clone().lerp(bright, THREE.MathUtils.clamp(py / 2.8 + lump * 0.17, 0, 1));
      colors.push(tint.r, tint.g, tint.b);
    }
  }
  sphere.dispose();
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  return geometry;
}

function createTrees() {
  const placements = [];
  for (let attempt = 0; attempt < 600 && placements.length < 18; attempt++) {
    const x = (random(attempt * 4 + 1) - 0.5) * 32,
      z = -8 - random(attempt * 4 + 2) * 14;
    if (Math.abs(x) < 2.5 + Math.abs(z) * 0.12 || Math.hypot(x, z) > 22.5) continue;
    const density = Math.max(
      ...[
        [-9, -11, 12],
        [8, -15, 18],
        [-11, -19, 16],
      ].map(([cx, cz, spread]) => Math.exp(-((x - cx) ** 2 + (z - cz) ** 2) / spread)),
    );
    if (random(attempt * 4 + 3) > density || placements.some((p) => Math.hypot(x - p.x, z - p.z) < 1.25)) continue;
    placements.push({ x, z, scale: 1 + random(attempt * 4 + 4) * 0.65, yaw: random(attempt + 31) * Math.PI * 2 });
  }
  const foliage = new THREE.MeshStandardNodeMaterial({ vertexColors: true, roughness: 1, metalness: 0 });
  const bark = new THREE.MeshStandardNodeMaterial({ color: 0x66553b, roughness: 1, metalness: 0 });
  for (const material of [foliage, bark]) material.contextNode = context({ getAO: (input) => input });
  const trunk = new THREE.CylinderGeometry(0.045, 0.075, 1, 6);
  trunk.translate(0, 0.5, 0);
  const trunks = new THREE.InstancedMesh(trunk, bark, placements.length);
  const crowns = [0, 1].map(
    (shape) => new THREE.InstancedMesh(canopyGeometry(shape), foliage, Math.ceil(placements.length / 2)),
  );
  const counts = [0, 0],
    transform = new THREE.Object3D();
  for (const [i, p] of placements.entries()) {
    transform.position.set(p.x, groundHeight(p.x, p.z) - 0.02, p.z);
    transform.rotation.set((random(i + 51) - 0.5) * 0.06, p.yaw, (random(i + 71) - 0.5) * 0.06);
    transform.scale.set(p.scale * (0.8 + random(i + 91) * 0.3), p.scale, p.scale * (0.8 + random(i + 101) * 0.3));
    transform.updateMatrix();
    trunks.setMatrixAt(i, transform.matrix);
    const shape = i % 2,
      index = counts[shape]++;
    crowns[shape].setMatrixAt(index, transform.matrix);
    crowns[shape].setColorAt(index, new THREE.Color().setScalar(0.85 + random(i + 111) * 0.25));
  }
  for (const [i, crown] of crowns.entries()) crown.count = counts[i];
  for (const mesh of [trunks, ...crowns]) mesh.computeBoundingSphere();
  return [trunks, ...crowns];
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
      group.add(createGround(), createGrass(), ...createTrees());
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
