// Static viewer scenery; model state is supplied only at deliberate placement boundaries.

import * as THREE from 'three/webgpu';
import {
  cameraPosition,
  color,
  context,
  fwidth,
  mix,
  modelPosition,
  modelScale,
  mx_noise_float,
  normalView,
  normalWorld,
  output,
  positionGeometry,
  positionView,
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
    THREE.MathUtils.smoothstep(Math.hypot(x, z), 2, 8) *
    (terrainNoise(x * 0.085 + 17, z * 0.085 + 31) * 1.5 + terrainNoise(x * 0.24 + 43, z * 0.24 + 11) * 0.2)
  );
}

// Surface-gradient bump, shared by the opaque foliage and ground materials.
function surfaceNormal(height) {
  const dx = positionView.dFdx(),
    dy = positionView.dFdy();
  const r1 = dy.cross(normalView),
    r2 = normalView.cross(dx);
  const det = dx.dot(r1);
  const gradient = height.dFdx().mul(r1).add(height.dFdy().mul(r2)).mul(det.sign());
  return normalView.mul(det.abs()).sub(gradient).normalize();
}

function surfaceNoise(position, frequency) {
  const sample = position.mul(frequency);
  // Remove subpixel detail before it can sparkle while the camera moves.
  const resolved = smoothstep(0.35, 1.2, fwidth(sample).length()).oneMinus();
  return mx_noise_float(sample).mul(resolved);
}

function sceneryMaterial(parameters = {}) {
  const material = new THREE.MeshStandardNodeMaterial({
    roughness: 1,
    metalness: 0,
    ...parameters,
  });
  material.contextNode = context({ getAO: (input) => input });
  const { ambient, hemisphere, atmosphere } = ENVIRONMENT_PRESETS.outdoor;
  const ray = positionWorld.sub(cameraPosition);
  const direction = ray.normalize();
  const height = positionWorld.y.sub(modelPosition.y).div(modelScale.x);
  const lowMist = smoothstep(0.3, 3, height).oneMinus();
  const haze = smoothstep(12, 65, ray.length().div(modelScale.x)).mul(lowMist.mul(0.35).add(0.3));
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
  const sphere = new THREE.IcosahedronGeometry(1, 1);
  const source = sphere.attributes.position;
  const positions = [],
    normals = [],
    colors = [];
  const lobes = [];
  // Offset overlapping branch masses leave a broken silhouette at every yaw.
  for (let i = 0; i < 11; i++) {
    const angle = i * 2.4 + shape;
    const tier = i / 10;
    const spread = shape ? 0.55 * (1 - tier) : 0.5 * Math.sin(tier * Math.PI);
    lobes.push([
      Math.cos(angle) * spread,
      0.9 + tier * 1.55,
      Math.sin(angle) * spread,
      shape ? 0.52 - tier * 0.28 : 0.38 + Math.sin(tier * Math.PI) * 0.18,
      0.32 + random(i + shape * 41) * 0.16,
    ]);
  }
  const deep = new THREE.Color(0x20351a),
    bright = new THREE.Color(0x657d3c);
  for (const [cx, cy, cz, width, height] of lobes) {
    for (let i = 0; i < source.count; i++) {
      const x = source.getX(i),
        y = source.getY(i),
        z = source.getZ(i);
      const h = (y + 1) * 0.5;
      const lump = Math.sin(x * 8.3 + y * 5.7 + cx) * Math.sin(z * 9.9 - y * 4.1 + cz);
      const radius = (1 - h * 0.2) * (1 + lump * 0.35);
      const py = cy + y * height;
      positions.push(cx + x * width * radius, py, cz + z * width * radius);
      const normal = new THREE.Vector3(x, 0.35 + y * 0.65, z).normalize();
      normals.push(normal.x, normal.y, normal.z);
      const tint = deep.clone().lerp(bright, THREE.MathUtils.clamp(py * 0.22 + h * 0.4 + lump * 0.17, 0, 1));
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
  for (let attempt = 0; attempt < 16000 && placements.length < 900; attempt++) {
    const x = (random(attempt * 4 + 1) - 0.5) * 92,
      z = (random(attempt * 4 + 2) - 0.5) * 92;
    const distance = Math.hypot(x, z);
    const clearing = 4.3 + terrainNoise(x * 0.14 + 13, z * 0.14 + 29) * 1.3;
    if (distance < clearing || distance > 44) continue;
    const density = 0.5 + terrainNoise(x * 0.12, z * 0.12) * 0.35;
    if (random(attempt * 4 + 3) > density || placements.some((p) => Math.hypot(x - p.x, z - p.z) < 1.35)) continue;
    placements.push({
      x,
      z,
      scale: 1.25 + random(attempt * 4 + 4) ** 2 * 1.35,
      yaw: random(attempt + 31) * Math.PI * 2,
    });
  }
  const foliage = sceneryMaterial({ vertexColors: true });
  const clumps = surfaceNoise(positionGeometry, 18).toVar();
  const detailFade = smoothstep(10, 35, positionWorld.distance(cameraPosition).div(modelScale.x)).oneMinus();
  foliage.colorNode = clumps.mul(0.55).add(0.85);
  foliage.normalNode = surfaceNormal(clumps.mul(detailFade).mul(modelScale.x).mul(0.006));
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
  const variation = mx_noise_float(positionGeometry.xz.mul(0.65)).mul(0.5).add(0.5).toVar();
  const detail = surfaceNoise(positionGeometry.xz, 55).mul(0.5).add(0.5).toVar();
  const material = sceneryMaterial({
    transparent: true,
    forceSinglePass: true,
    depthWrite: true,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    side: THREE.DoubleSide,
  });
  const turf = mix(color(0x465329), color(0x78804b), variation);
  const soil = smoothstep(0.002, 0.03, normalWorld.y.oneMinus())
    .mul(0.35)
    .add(smoothstep(0.6, 0.8, variation).mul(0.2));
  material.colorNode = mix(turf, color(0x786e55), soil).mul(detail.mul(0.12).add(0.9));
  const detailFade = smoothstep(3, 18, positionWorld.distance(cameraPosition).div(modelScale.x)).oneMinus();
  material.normalNode = surfaceNormal(detail.mul(detailFade).mul(modelScale.x).mul(0.0015));
  material.opacityNode = smoothstep(65, 94, positionGeometry.x.abs().max(positionGeometry.z.abs())).oneMinus();
  const geometry = new THREE.BufferGeometry();
  // Concentrate the heightfield's vertices near the flat inspection clearing.
  const positions = [],
    indices = [];
  const coord = (i) => Math.sign(i / 48 - 1) * Math.abs(i / 48 - 1) ** 2 * 96;
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
