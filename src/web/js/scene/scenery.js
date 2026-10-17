// Static viewer scenery; model state is supplied only at deliberate placement boundaries.

import * as THREE from 'three/webgpu';
import {
  cameraPosition,
  color,
  context,
  mix,
  modelScale,
  mx_noise_float,
  normalWorld,
  output,
  positionGeometry,
  positionWorld,
  smoothstep,
  vec3,
  vec4,
} from 'three/tsl';
import { computeModelBounds } from './model-bounds.js';
import { ENVIRONMENT_PRESETS } from './environment.js';

function random(seed) {
  const value = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return value - Math.floor(value);
}

function terrainNoise(x, z) {
  const ix = Math.floor(x),
    iz = Math.floor(z);
  const tx = THREE.MathUtils.smoothstep(x - ix, 0, 1),
    tz = THREE.MathUtils.smoothstep(z - iz, 0, 1);
  const sample = (a, b) => random(a * 127.1 + b * 311.7) * 2 - 1;
  return THREE.MathUtils.lerp(
    THREE.MathUtils.lerp(sample(ix, iz), sample(ix + 1, iz), tx),
    THREE.MathUtils.lerp(sample(ix, iz + 1), sample(ix + 1, iz + 1), tx),
    tz,
  );
}

function groundHeight(x, z) {
  return (
    THREE.MathUtils.smoothstep(Math.hypot(x, z), 2.5, 7) *
    (terrainNoise(x * 0.065 + 17, z * 0.065 + 31) * 0.6 + terrainNoise(x * 0.17 + 43, z * 0.17 + 11) * 0.14)
  );
}

function sceneryMaterial(parameters = {}) {
  const material = new THREE.MeshStandardNodeMaterial({ roughness: 1, metalness: 0, ...parameters });
  material.contextNode = context({ getAO: (input) => input });
  const { ambient, hemisphere, atmosphere } = ENVIRONMENT_PRESETS.outdoor;
  const ray = positionWorld.sub(cameraPosition);
  const direction = ray.normalize();
  const haze = smoothstep(10, 44, ray.length().div(modelScale.x));
  const upper = mix(color(ambient.color), color(hemisphere.color), smoothstep(0, 0.5, direction.y));
  const sky = mix(color(atmosphere.groundColor), upper, smoothstep(-0.35, 0.02, direction.y));
  const sun = direction
    .dot(vec3(...atmosphere.sunDirection))
    .max(0)
    .pow(8)
    .mul(0.08);
  material.outputNode = vec4(mix(output.rgb, mix(sky, color(atmosphere.sunColor), sun), haze), output.a);
  return material;
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
  const foliage = sceneryMaterial({ vertexColors: true });
  const bark = sceneryMaterial({ color: 0x66553b });
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
  const variation = mx_noise_float(positionGeometry.xz.mul(0.4)).mul(0.5).add(0.5).toVar();
  const detail = mx_noise_float(positionGeometry.xz.mul(70)).mul(0.5).add(0.5).toVar();
  const material = sceneryMaterial({
    transparent: true,
    forceSinglePass: true,
    depthWrite: true,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    side: THREE.DoubleSide,
  });
  const turf = mix(color(0x606d45), color(0x7c835b), variation);
  const soil = smoothstep(0.002, 0.03, normalWorld.y.oneMinus())
    .mul(0.35)
    .add(smoothstep(0.6, 0.8, variation).mul(0.2));
  material.colorNode = mix(turf, color(0x786e55), soil).mul(detail.mul(0.12).add(0.9));
  material.opacityNode = smoothstep(34, 47, positionGeometry.x.abs().max(positionGeometry.z.abs())).oneMinus();
  const geometry = new THREE.BufferGeometry();
  // Concentrate the heightfield's vertices near the flat inspection clearing.
  const positions = [],
    indices = [];
  const coord = (i) => Math.sign(i / 48 - 1) * Math.abs(i / 48 - 1) ** 1.5 * 48;
  for (let iz = 0; iz <= 96; iz++) {
    for (let ix = 0; ix <= 96; ix++) {
      const x = coord(ix),
        z = coord(iz);
      positions.push(x, groundHeight(x, z), z);
      if (ix < 96 && iz < 96) {
        const a = iz * 97 + ix,
          b = a + 1,
          c = a + 97,
          d = c + 1;
        if ((ix + iz) % 2 === 0) indices.push(a, c, b, b, c, d);
        else indices.push(a, c, d, a, d, b);
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
      group.add(createGround(), ...createTrees());
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
