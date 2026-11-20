// Model-scaled, on-demand directional shadows and their transparent receiver.

import * as THREE from 'three/webgpu';
import { float, reference } from 'three/tsl';
import { computeModelBounds, computeRestBounds } from './model-bounds.js';
import { stableShadowFilter } from './stable-shadow-filter.js';

const FIT_MARGIN = 0.12;
const MAX_GROUND_REACH = 2.5;
const NORMAL_BIAS_SCALE = 0.0015;
const MIN_SIZE = 0.001;
const SHADOW_OPACITY = 0.32;
const SHADOW_REFERENCE_INTENSITY = 1.0;
const FRUSTUM_STEPS = 32;

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

// Grow a light-space range in model-scaled steps and snap its centre to the
// shadow-map texel grid, so small per-frame bound changes from posing, physics
// or animation keep the same texel layout instead of shimmering edges.
function stableRange(min, max, margin, step, resolution) {
  const extent = Math.max(Math.ceil((max - min + margin * 2) / step) * step, step);
  const texel = extent / resolution;
  const center = Math.round((min + max) / 2 / texel) * texel;
  return [center - extent / 2, center + extent / 2];
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
  const step = Math.max(modelSize / FRUSTUM_STEPS, MIN_SIZE);
  [camera.left, camera.right] = stableRange(bounds.min.x, bounds.max.x, marginX, step, light.shadow.mapSize.x);
  [camera.bottom, camera.top] = stableRange(bounds.min.y, bounds.max.y, marginY, step, light.shadow.mapSize.y);
  camera.near = -bounds.max.z - marginDepth;
  camera.far = Math.max(camera.near + MIN_SIZE, -bounds.min.z + marginDepth);
  camera.updateProjectionMatrix();
  light.shadow.bias = -0.00002;
  light.shadow.normalBias = modelSize * NORMAL_BIAS_SCALE;
  return true;
}

export function createCharacterShadowController({ renderer, scene, light, grid = null, onGroundChange = null }) {
  // The floor can retain a shadow at grazing angles without changing model lighting.
  const floorLight = new THREE.DirectionalLight();
  floorLight.shadow.autoUpdate = false;
  floorLight.shadow.mapSize.set(2048, 2048);
  floorLight.shadow.filterNode = stableShadowFilter;
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
  renderer.shadowMap.type = THREE.PCFShadowMap;
  light.castShadow = true;
  light.shadow.autoUpdate = false;
  light.shadow.mapSize.set(2048, 2048);
  light.shadow.filterNode = stableShadowFilter;

  let meshes = [];
  let modelBoundsDirty = true;
  let casterBoundsDirty = true;
  let shadowFitDirty = true;
  let shadowMapDirty = true;
  let modelBounds = new THREE.Box3();
  let casterBounds = new THREE.Box3();
  // The ground is measured once per load (or explicit re-seat) and then held:
  // posing, physics, animation frames, visibility and translation never move
  // it. Selecting or stopping a rig clip re-seats it once (setGroundAnchor).
  let groundFrame = null;
  let groundFrameDirty = true;
  let groundLockCount = 0;
  // Optional world floor height from an applied rig clip; see setGroundAnchor.
  let groundAnchor = null;
  let groundAnchored = false;
  let lastLightPosition = null;
  let lastLightTarget = null;
  let groundAvailable = false;
  let fitCount = 0;
  let shadowUpdateCount = 0;

  function invalidateGeometry() {
    modelBoundsDirty = true;
    casterBoundsDirty = true;
    shadowFitDirty = true;
    shadowMapDirty = true;
  }

  function invalidateVisibility() {
    casterBoundsDirty = true;
    shadowFitDirty = true;
    shadowMapDirty = true;
  }

  function invalidateMap() {
    shadowMapDirty = true;
  }

  /** Re-measure the ground on the next update, e.g. after an orientation change. */
  function reanchorGround() {
    groundFrameDirty = true;
    invalidateGeometry();
  }

  /**
   * Seat the floor on an animation's own ground instead of the rest-pose base.
   * `anchor` returns a world floor height (or null to keep the rest base) and
   * is read whenever the ground is locked, so quarter turns and Reset View
   * re-evaluate it. Pass null when the clip stops.
   */
  function setGroundAnchor(anchor) {
    if (anchor === groundAnchor) return;
    groundAnchor = anchor || null;
    groundFrameDirty = true;
    shadowFitDirty = true;
    shadowMapDirty = true;
  }

  function setMeshes(nextMeshes = []) {
    meshes = [...new Set(nextMeshes.filter(Boolean))];
    reanchorGround();
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
    if (meshes.length === before) return;
    if (!meshes.length) groundFrameDirty = true;
    invalidateGeometry();
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

  function refreshBounds() {
    if (modelBoundsDirty) {
      modelBounds = computeModelBounds(meshes);
      modelBoundsDirty = false;
    }
    // Key-light drags refit every frame; casters only change with geometry
    // or visibility invalidation.
    if (casterBoundsDirty) {
      casterBounds = computeModelBounds(meshes, { visibleOnly: true });
      casterBoundsDirty = false;
    }
  }

  // Lock the floor to the model's rest pose: the visible base when anything is
  // visible, otherwise the whole model. Rest bounds keep a re-anchor during
  // playback from reading padded animation bounds. An applied clip's ground
  // anchor replaces only the height; center and size stay rest-pose derived.
  // Grid, shadow receiver and environment floor share this frame, including
  // while the key light is off.
  function lockGround() {
    if (!groundFrameDirty) return groundFrame;
    groundFrameDirty = false;
    const whole = computeRestBounds(meshes);
    const visible = computeRestBounds(meshes, { visibleOnly: true });
    const base = finiteBox(visible) ? visible : whole;
    const anchored = finiteBox(base) ? groundAnchor?.() : null;
    groundAnchored = Number.isFinite(anchored);
    groundFrame = finiteBox(base)
      ? Object.freeze({
          y: groundAnchored ? anchored : base.min.y,
          center: Object.freeze([(base.min.x + base.max.x) / 2, (base.min.z + base.max.z) / 2]),
          size: Math.max(whole.getSize(new THREE.Vector3()).length(), MIN_SIZE),
        })
      : null;
    groundLockCount += 1;
    if (grid) grid.position.y = groundFrame ? groundFrame.y : 0;
    onGroundChange?.(groundFrame);
    return groundFrame;
  }

  function fitShadow() {
    lockGround();
    refreshBounds();
    if (!groundFrame || !finiteBox(casterBounds)) {
      groundAvailable = false;
      ground.visible = false;
      shadowFitDirty = false;
      fitCount += 1;
      return false;
    }

    const modelSize = groundFrame.size;
    const floorY = groundFrame.y;
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
    groundAvailable = light.position.y > floorY;
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
      // Keep the shadow fit pending for when the light returns.
      lockGround();
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
    casterBoundsDirty = false;
    shadowFitDirty = false;
    shadowMapDirty = false;
    groundFrame = null;
    groundFrameDirty = true;
    groundAnchor = null;
    groundAnchored = false;
    lastLightPosition = null;
    lastLightTarget = null;
    groundAvailable = false;
    ground.visible = false;
    if (grid) grid.position.y = 0;
    onGroundChange?.(null);
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
      groundHeight: ground.position.y,
      groundLockCount,
      groundAnchored,
      shadowFrustum: ['left', 'right', 'bottom', 'top'].map((side) => light.shadow.camera[side]),
      normalBias: light.shadow.normalBias,
    };
  }

  return {
    setMeshes,
    adoptMeshes,
    forgetMeshes,
    reanchorGround,
    setGroundAnchor,
    invalidateGeometry,
    invalidateVisibility,
    invalidateMap,
    update,
    reset,
    getDebugState,
  };
}
