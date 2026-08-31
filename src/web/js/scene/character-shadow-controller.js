// Model-scaled, on-demand directional shadows and their transparent receiver.

import * as THREE from 'three/webgpu';
import { computeModelBounds } from './model-bounds.js';
import { addWeightPhysicsPerformance } from '../mesh/weight-physics-performance.js';

const FIT_MARGIN = 0.12;
const MAX_GROUND_REACH = 2.5;
const NORMAL_BIAS_SCALE = 0.0015;
const MIN_SIZE = 0.001;

function finiteBox(box) {
  return !box.isEmpty() && [
    box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z,
  ].every(Number.isFinite);
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

export function createCharacterShadowController({ renderer, scene, light }) {
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.ShadowMaterial({ color: 0x000000, opacity: 0.32, transparent: true, depthWrite: false }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  ground.castShadow = false;
  ground.visible = false;
  ground.userData.isViewerGround = true;
  scene.add(ground);

  renderer.shadowMap.enabled = true;
  if (THREE.PCFShadowMap !== undefined) renderer.shadowMap.type = THREE.PCFShadowMap;

  function activateShadowLight(nextLight) {
    nextLight.castShadow = true;
    nextLight.shadow.autoUpdate = false;
    nextLight.shadow.mapSize.set(2048, 2048);
  }

  function deactivateShadowLight(previousLight) {
    previousLight.castShadow = false;
  }

  let activeLight = light;
  activateShadowLight(activeLight);

  let meshes = [];
  let modelBoundsDirty = true;
  let shadowFitDirty = true;
  let shadowMapDirty = true;
  let modelBounds = new THREE.Box3();
  let casterBounds = new THREE.Box3();
  let lastLightWorldPosition = null;
  let lastTargetWorldPosition = null;
  let groundAvailable = false;
  let fitCount = 0;
  let shadowUpdateCount = 0;
  const lightWorldPosition = new THREE.Vector3();
  const targetWorldPosition = new THREE.Vector3();
  const lightDirection = new THREE.Vector3();

  function readLightWorldState() {
    activeLight.updateWorldMatrix(true, false);
    activeLight.target.updateWorldMatrix(true, false);
    activeLight.getWorldPosition(lightWorldPosition);
    activeLight.target.getWorldPosition(targetWorldPosition);
    lightDirection.subVectors(targetWorldPosition, lightWorldPosition);
    if (lightDirection.lengthSq() < 0.00000001) {
      lightDirection.set(0, -1, 0);
    } else {
      lightDirection.normalize();
    }
  }

  function setLight(nextLight) {
    if (!nextLight || nextLight === activeLight) return false;
    deactivateShadowLight(activeLight);
    activeLight = nextLight;
    activateShadowLight(activeLight);
    lastLightWorldPosition = null;
    lastTargetWorldPosition = null;
    shadowFitDirty = true;
    shadowMapDirty = true;
    return true;
  }

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
    const added = nextMeshes.filter(mesh => mesh && !known.has(mesh));
    if (added.length) {
      meshes.push(...added);
      invalidateGeometry();
    }
    return added;
  }

  function forgetMeshes(nextMeshes = []) {
    const removed = new Set(nextMeshes);
    const before = meshes.length;
    meshes = meshes.filter(mesh => !removed.has(mesh));
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
      addWeightPhysicsPerformance('shadowFitCount');
      return false;
    }

    const modelSize = Math.max(modelBounds.getSize(new THREE.Vector3()).length(), MIN_SIZE);
    const casterCorners = boxCorners(casterBounds);
    const footprint = projectGroundFootprint(
      casterCorners, modelBounds.min.y, modelSize, lightDirection);
    const footprintBox = new THREE.Box3().setFromPoints(footprint);
    const footprintSize = footprintBox.getSize(new THREE.Vector3());
    const groundMargin = Math.max(modelSize * FIT_MARGIN, MIN_SIZE);
    ground.position.set(
      footprintBox.getCenter(new THREE.Vector3()).x,
      modelBounds.min.y - Math.max(modelSize * 0.0001, 0.000001),
      footprintBox.getCenter(new THREE.Vector3()).z,
    );
    ground.scale.set(
      Math.max(footprintSize.x + groundMargin * 2, MIN_SIZE),
      Math.max(footprintSize.z + groundMargin * 2, MIN_SIZE), 1,
    );

    const shadowCamera = activeLight.shadow.camera;
    shadowCamera.position.copy(lightWorldPosition);
    shadowCamera.up.set(0, 1, 0);
    if (Math.abs(lightDirection.dot(shadowCamera.up)) > 0.98) shadowCamera.up.set(0, 0, 1);
    shadowCamera.lookAt(targetWorldPosition);
    shadowCamera.updateMatrixWorld(true);
    const lightSpace = footprint.map(point => point.clone().applyMatrix4(shadowCamera.matrixWorldInverse));
    const lightBox = new THREE.Box3().setFromPoints(lightSpace);
    if (!finiteBox(lightBox)) {
      groundAvailable = false;
      ground.visible = false;
      shadowFitDirty = false;
      return false;
    }
    const size = lightBox.getSize(new THREE.Vector3());
    const marginX = Math.max(size.x * FIT_MARGIN, MIN_SIZE);
    const marginY = Math.max(size.y * FIT_MARGIN, MIN_SIZE);
    const marginDepth = Math.max(size.z * FIT_MARGIN, MIN_SIZE);
    shadowCamera.left = lightBox.min.x - marginX;
    shadowCamera.right = lightBox.max.x + marginX;
    shadowCamera.bottom = lightBox.min.y - marginY;
    shadowCamera.top = lightBox.max.y + marginY;
    shadowCamera.near = Math.max(MIN_SIZE, -lightBox.max.z - marginDepth);
    shadowCamera.far = Math.max(shadowCamera.near + MIN_SIZE, -lightBox.min.z + marginDepth);
    shadowCamera.updateProjectionMatrix();
    activeLight.shadow.bias = -0.00002;
    activeLight.shadow.normalBias = modelSize * NORMAL_BIAS_SCALE;
    groundAvailable = true;
    ground.visible = activeLight.intensity > 0;
    shadowFitDirty = false;
    fitCount += 1;
    addWeightPhysicsPerformance('shadowFitCount');
    return true;
  }

  function update() {
    readLightWorldState();
    const changedLight = !sameVector(
      lastLightWorldPosition, lightWorldPosition)
      || !sameVector(lastTargetWorldPosition, targetWorldPosition);
    if (changedLight) {
      lastLightWorldPosition = lightWorldPosition.clone();
      lastTargetWorldPosition = targetWorldPosition.clone();
      shadowFitDirty = true;
      shadowMapDirty = true;
    }
    if (activeLight.intensity <= 0) {
      ground.visible = false;
      return;
    }
    if (shadowFitDirty) fitShadow();
    else ground.visible = groundAvailable;
    if (shadowMapDirty) {
      activeLight.shadow.needsUpdate = true;
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
    lastLightWorldPosition = null;
    lastTargetWorldPosition = null;
    groundAvailable = false;
    ground.visible = false;
  }

  function getDebugState() {
    readLightWorldState();
    const serialize = box => finiteBox(box) ? {
      min: box.min.toArray(), max: box.max.toArray(),
    } : null;
    return {
      modelBounds: serialize(modelBounds),
      casterBounds: serialize(casterBounds),
      fitCount,
      shadowUpdateCount,
      groundVisible: ground.visible,
      normalBias: activeLight.shadow.normalBias,
      activeLightUuid: activeLight.uuid,
      activeLightIntensity: activeLight.intensity,
      activeLightPosition: lightWorldPosition.toArray(),
      activeLightTarget: targetWorldPosition.toArray(),
      activeLightDirection: lightDirection.toArray(),
      activeLightCastsShadow: activeLight.castShadow,
    };
  }

  return {
    setLight, setMeshes, adoptMeshes, forgetMeshes,
    invalidateGeometry, invalidateVisibility, invalidateMap,
    update, reset, getDebugState,
  };
}
