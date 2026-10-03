// Model-scaled, on-demand directional shadows and their transparent receiver.

import * as THREE from 'three/webgpu';
import { float, reference } from 'three/tsl';
import { computeModelBounds } from './model-bounds.js';

const FIT_MARGIN = 0.12;
const MAX_GROUND_REACH = 2.5;
const NORMAL_BIAS_SCALE = 0.0015;
const MIN_SIZE = 0.001;
const SHADOW_OPACITY = 0.32;
const SHADOW_REFERENCE_INTENSITY = 1.0;

function finiteBox(box) {
  return !box.isEmpty() && [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z].every(Number.isFinite);
}

function boxCorners(box) {
  const corners = [];
  for (const x of [box.min.x, box.max.x]) {
    for (const y of [box.min.y, box.max.y]) {
      for (const z of [box.min.z, box.max.z]) corners.push(new THREE.Vector3(x, y, z));
    }
  }
  return corners;
}

function sameVector(left, right) {
  return !!left && left.distanceToSquared(right) < 0.0000000001;
}

function shadowOpacityForIntensity(intensity) {
  return SHADOW_OPACITY * THREE.MathUtils.clamp(intensity / SHADOW_REFERENCE_INTENSITY, 0, 1);
}

function fitShadowCamera(light, points, modelSize) {
  const direction = light.target.position.clone().sub(light.position).normalize();
  const camera = light.shadow.camera;
  light.updateWorldMatrix(true, false);
  light.target.updateWorldMatrix(true, false);
  camera.position.copy(light.position);
  camera.up.set(0, 1, 0);
  if (Math.abs(direction.y) > 0.98) camera.up.set(0, 0, 1);
  camera.lookAt(light.target.position);
  camera.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromPoints(
    points.map((point) => point.clone().applyMatrix4(camera.matrixWorldInverse)),
  );
  if (!finiteBox(bounds)) return false;
  const size = bounds.getSize(new THREE.Vector3());
  const marginX = Math.max(size.x * FIT_MARGIN, MIN_SIZE);
  const marginY = Math.max(size.y * FIT_MARGIN, MIN_SIZE);
  const marginDepth = Math.max(size.z * FIT_MARGIN, MIN_SIZE);
  camera.left = bounds.min.x - marginX;
  camera.right = bounds.max.x + marginX;
  camera.bottom = bounds.min.y - marginY;
  camera.top = bounds.max.y + marginY;
  camera.near = -bounds.max.z - marginDepth;
  camera.far = Math.max(camera.near + MIN_SIZE, -bounds.min.z + marginDepth);
  camera.updateProjectionMatrix();
  light.shadow.bias = -0.00002;
  light.shadow.normalBias = modelSize * NORMAL_BIAS_SCALE;
  return true;
}

export function createCharacterShadowController({ renderer, scene, light, grid = null }) {
  // The floor can retain a shadow at grazing angles without changing model lighting.
  const floorLight = new THREE.DirectionalLight();
  floorLight.shadow.autoUpdate = false;
  floorLight.shadow.mapSize.set(2048, 2048);
  const groundMaterial = new THREE.ShadowMaterial({
    color: 0x000000,
    opacity: shadowOpacityForIntensity(light.intensity),
    transparent: true,
    depthWrite: false,
  });
  const floorMaterial = new THREE.MeshBasicNodeMaterial({ color: 0x000000, transparent: true, depthWrite: false });
  floorMaterial.opacityNode = float(new THREE.ShadowNode(floorLight))
    .oneMinus()
    .mul(reference('opacity', 'float', groundMaterial));
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), groundMaterial);
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  ground.castShadow = false;
  ground.visible = false;
  ground.userData.isViewerGround = true;
  scene.add(ground);

  renderer.shadowMap.enabled = true;
  if (THREE.PCFShadowMap !== undefined) renderer.shadowMap.type = THREE.PCFShadowMap;
  light.castShadow = true;
  light.shadow.autoUpdate = false;
  light.shadow.mapSize.set(2048, 2048);

  let meshes = [];
  let modelBoundsDirty = true;
  let shadowFitDirty = true;
  let shadowMapDirty = true;
  let modelBounds = new THREE.Box3();
  let casterBounds = new THREE.Box3();
  let lastLightPosition = null;
  let lastLightTarget = null;
  let groundAvailable = false;
  let fitCount = 0;
  let shadowUpdateCount = 0;

  function invalidateGeometry() {
    modelBoundsDirty = true;
    shadowFitDirty = true;
    shadowMapDirty = true;
  }

  function invalidateVisibility() {
    shadowFitDirty = true;
    shadowMapDirty = true;
  }

  function invalidateMap() {
    shadowMapDirty = true;
  }

  function setMeshes(nextMeshes = []) {
    meshes = [...new Set(nextMeshes.filter(Boolean))];
    invalidateGeometry();
  }

  function adoptMeshes(nextMeshes = []) {
    const known = new Set(meshes);
    const added = nextMeshes.filter((mesh) => mesh && !known.has(mesh));
    if (added.length) {
      meshes.push(...added);
      invalidateGeometry();
    }
    return added;
  }

  function forgetMeshes(nextMeshes = []) {
    const removed = new Set(nextMeshes);
    const before = meshes.length;
    meshes = meshes.filter((mesh) => !removed.has(mesh));
    if (meshes.length !== before) invalidateGeometry();
  }

  function projectGroundFootprint(corners, floorY, size, direction) {
    const result = [...corners];
    const vertical = direction.y;
    if (Math.abs(vertical) < 0.00001) return result;
    const maximumReach = Math.max(size * MAX_GROUND_REACH, MIN_SIZE);
    for (const corner of corners) {
      const distance = (floorY - corner.y) / vertical;
      if (!Number.isFinite(distance) || distance < 0) continue;
      result.push(corner.clone().addScaledVector(direction, Math.min(distance, maximumReach)));
    }
    return result;
  }

  function fitShadow() {
    if (modelBoundsDirty) {
      modelBounds = computeModelBounds(meshes);
      modelBoundsDirty = false;
    }
    casterBounds = computeModelBounds(meshes, { visibleOnly: true });
    if (!finiteBox(modelBounds) || !finiteBox(casterBounds)) {
      groundAvailable = false;
      ground.visible = false;
      shadowFitDirty = false;
      fitCount += 1;
      return false;
    }

    const modelSize = Math.max(modelBounds.getSize(new THREE.Vector3()).length(), MIN_SIZE);
    const floorY = casterBounds.min.y;
    const lightDirection = light.target.position.clone().sub(light.position);
    if (lightDirection.lengthSq() < 0.00000001) lightDirection.set(0, -1, 0);
    lightDirection.normalize();
    const casterCorners = boxCorners(casterBounds);
    const modelFootprint = projectGroundFootprint(casterCorners, floorY, modelSize, lightDirection);
    const minimumDownward = Math.min((casterBounds.max.y - floorY) / (modelSize * MAX_GROUND_REACH), 1);
    const needsFloorProjection = lightDirection.y > -minimumDownward;
    let footprint = modelFootprint;
    ground.material = groundMaterial;
    if (needsFloorProjection) {
      const floorDirection = lightDirection.clone();
      floorDirection.y = 0;
      if (floorDirection.lengthSq() < 0.00000001) floorDirection.set(0, -1, 0);
      else {
        floorDirection.normalize().multiplyScalar(Math.sqrt(1 - minimumDownward ** 2));
        floorDirection.y = -minimumDownward;
      }
      floorLight.position.copy(light.position);
      floorLight.target.position.copy(light.position).addScaledVector(floorDirection, modelSize);
      footprint = projectGroundFootprint(casterCorners, floorY, modelSize, floorDirection);
      ground.material = floorMaterial;
    }
    const footprintBox = new THREE.Box3().setFromPoints(footprint);
    const footprintSize = footprintBox.getSize(new THREE.Vector3());
    const groundMargin = Math.max(modelSize * FIT_MARGIN, MIN_SIZE);
    ground.position.set(
      footprintBox.getCenter(new THREE.Vector3()).x,
      floorY - Math.max(modelSize * 0.0001, 0.000001),
      footprintBox.getCenter(new THREE.Vector3()).z,
    );
    ground.scale.set(
      Math.max(footprintSize.x + groundMargin * 2, MIN_SIZE),
      Math.max(footprintSize.z + groundMargin * 2, MIN_SIZE),
      1,
    );
    if (grid) grid.position.y = floorY;

    // Ordinary angles share the key shadow map; only the floor fallback needs another projection.
    if (
      !fitShadowCamera(light, modelFootprint, modelSize) ||
      (needsFloorProjection && !fitShadowCamera(floorLight, footprint, modelSize))
    ) {
      groundAvailable = false;
      ground.visible = false;
      shadowFitDirty = false;
      return false;
    }
    groundAvailable = light.position.y > casterBounds.min.y;
    ground.visible = groundAvailable && light.intensity > 0;
    shadowFitDirty = false;
    fitCount += 1;
    return true;
  }

  function update() {
    groundMaterial.opacity = shadowOpacityForIntensity(light.intensity);
    const changedLight =
      !sameVector(lastLightPosition, light.position) || !sameVector(lastLightTarget, light.target.position);
    if (changedLight) {
      lastLightPosition = light.position.clone();
      lastLightTarget = light.target.position.clone();
      shadowFitDirty = true;
      shadowMapDirty = true;
    }
    if (light.intensity <= 0) {
      ground.visible = false;
      return;
    }
    if (shadowFitDirty) fitShadow();
    else ground.visible = groundAvailable;
    if (shadowMapDirty) {
      light.shadow.needsUpdate = true;
      floorLight.shadow.needsUpdate = true;
      renderer.shadowMap.needsUpdate = true;
      shadowMapDirty = false;
      shadowUpdateCount += 1;
    }
  }

  function reset() {
    meshes = [];
    modelBounds = new THREE.Box3();
    casterBounds = new THREE.Box3();
    modelBoundsDirty = false;
    shadowFitDirty = false;
    shadowMapDirty = false;
    lastLightPosition = null;
    lastLightTarget = null;
    groundAvailable = false;
    ground.visible = false;
  }

  function getDebugState() {
    const serialize = (box) =>
      finiteBox(box)
        ? {
            min: box.min.toArray(),
            max: box.max.toArray(),
          }
        : null;
    return {
      meshCount: meshes.length,
      modelBounds: serialize(modelBounds),
      casterBounds: serialize(casterBounds),
      fitCount,
      shadowUpdateCount,
      groundVisible: ground.visible,
      normalBias: light.shadow.normalBias,
    };
  }

  return {
    setMeshes,
    adoptMeshes,
    forgetMeshes,
    invalidateGeometry,
    invalidateVisibility,
    invalidateMap,
    update,
    reset,
    getDebugState,
  };
}
