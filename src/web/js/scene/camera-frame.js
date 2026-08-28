// Camera/model framing, clipping, orientation and unobstructed viewport math.

import * as THREE from 'three';
import { computeModelBounds } from './model-bounds.js';

const INITIAL_CAMERA_DIRECTION = new THREE.Vector3(0, 0, 1);
const INITIAL_CAMERA_UP = new THREE.Vector3(0, 1, 0);

export function createCameraFrame({
  camera, renderer, controls, grid, cancelViewSnap, onModelFit,
}) {
  let homeView = null;
  let clipNear = camera.near;
  let clipFar = camera.far;
  let orientationInitialized = false;
  const uprightRotation = new THREE.Quaternion();
  const baseFacingRotation = new THREE.Quaternion();
  const modelRotation = new THREE.Quaternion();
  let modelPivot = null;
  const modelTranslation = new THREE.Vector3();

  /** Aim projection at the unobstructed region while retaining the real model
   * center as Arcball's physical orbit target. */
  function usableViewport() {
    const canvas = renderer.domElement.getBoundingClientRect();
    const fullWidth = Math.max(canvas.width, 1);
    const fullHeight = Math.max(canvas.height, 1);
    let left = 0;
    let right = fullWidth;
    const gap = 14;

    const leftDock = document.getElementById('left-dock');
    const leftRect = leftDock?.getBoundingClientRect();
    if (leftDock && leftRect.width > 1) {
      left = Math.max(left, leftRect.right - canvas.left + gap);
    }
    const rightDock = document.getElementById('right-dock');
    const rightRect = rightDock?.getBoundingClientRect();
    if (rightDock && rightRect.width > 1) {
      right = Math.min(right, rightRect.left - canvas.left - gap);
    }
    if (right - left < fullWidth * 0.25) {
      left = 0;
      right = fullWidth;
    }
    return {
      fullWidth,
      fullHeight,
      width: right - left,
      centerShiftX: (left + right - fullWidth) * 0.5,
    };
  }

  function updateViewport() {
    const viewport = usableViewport();
    camera.clearViewOffset();
    if (Math.abs(viewport.centerShiftX) > 0.5) {
      camera.setViewOffset(
        viewport.fullWidth, viewport.fullHeight,
        -viewport.centerShiftX, 0,
        viewport.fullWidth, viewport.fullHeight);
    }
    camera.updateProjectionMatrix();
    return viewport;
  }

  function resize(width, height) {
    camera.aspect = width / height;
    renderer.setSize(width, height);
    updateViewport();
  }

  function updateClipping() {
    // Arcball can restore startup planes during scaling; reapply model-scaled
    // clipping after its update.
    const viewDistance = camera.position.distanceTo(controls.target);
    const requiredFar = Math.max(clipFar, viewDistance * 4, 100);
    if (camera.near !== clipNear || camera.far !== requiredFar) {
      camera.near = clipNear;
      camera.far = requiredFar;
      camera.updateProjectionMatrix();
    }
  }

  function frameView(meshes = [], direction = null, targetYOffset = 0) {
    const box = computeModelBounds(meshes);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    center.y += targetYOffset;
    const radius = Math.max(size.length() * 0.5, 0.001);
    const viewport = updateViewport();
    const narrowScale = Math.max(1, viewport.fullHeight / viewport.width);
    const distance = radius / Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5))
      * 1.15 * narrowScale;
    const offset = direction
      ? direction.clone().normalize()
      : camera.position.clone().sub(controls.target).normalize();
    if (offset.lengthSq() < 0.01) offset.set(0.3, 0.5, 1).normalize();
    controls.target.copy(center);
    camera.position.copy(center).addScaledVector(offset, distance);
    camera.near = radius * 0.001;
    camera.far = Math.max(radius * 100, 100);
    clipNear = camera.near;
    clipFar = camera.far;
    camera.updateProjectionMatrix();
    controls.update();
  }

  function currentModelPivot() {
    return modelPivot
      ? modelPivot.clone().add(modelTranslation)
      : null;
  }

  function rotateMeshesAroundCenter(meshes, rotation, centerOverride = null) {
    if (!meshes.length) return [];
    // The post-upright pivot is stable; recomputing an AABB center after each
    // turn makes asymmetric models drift.
    let center = centerOverride?.clone() || currentModelPivot();
    if (!center) {
      const box = computeModelBounds(meshes);
      if (box.isEmpty()) return [];
      center = box.getCenter(new THREE.Vector3());
    }
    meshes.forEach(mesh => {
      mesh.position.sub(center).applyQuaternion(rotation).add(center);
      mesh.quaternion.premultiply(rotation);
    });
    return meshes;
  }

  function rotateModelQuarterTurn(meshes = []) {
    if (!meshes.length) return [];
    const rotation = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(0, 1, 0), Math.PI / 2);
    const changed = rotateMeshesAroundCenter(meshes, rotation,
      currentModelPivot());
    if (!changed.length) return [];
    modelRotation.premultiply(rotation);
    return changed;
  }

  function rotateModelHorizontalQuarterTurn(meshes = []) {
    if (!meshes.length) return [];
    const rotation = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(1, 0, 0), Math.PI / 2);
    const changed = rotateMeshesAroundCenter(meshes, rotation,
      currentModelPivot());
    if (!changed.length) return [];
    modelRotation.premultiply(rotation);
    return changed;
  }

  function applyCurrentModelOrientation(meshes = [], { includeUserRotation = true } = {}) {
    if (!orientationInitialized || !modelPivot || !meshes.length) return;
    meshes.forEach(mesh => mesh.quaternion.copy(uprightRotation));
    rotateMeshesAroundCenter(meshes, baseFacingRotation, modelPivot);
    if (includeUserRotation) rotateMeshesAroundCenter(
      meshes, modelRotation, modelPivot);
  }

  function adoptModelMeshes(meshes = []) {
    if (!orientationInitialized || !homeView || !meshes.length) return [];
    const known = new Set(homeView.meshes.map(item => item.mesh));
    const added = meshes.filter(mesh => mesh && !known.has(mesh));
    if (!added.length) return [];
    applyCurrentModelOrientation(added, {includeUserRotation: false});
    const homeTransforms = added.map(mesh => ({
      mesh,
      quaternion: mesh.quaternion.clone(),
      position: mesh.position.clone(),
    }));
    rotateMeshesAroundCenter(added, modelRotation, modelPivot);
    added.forEach(mesh => mesh.position.add(modelTranslation));
    homeView.meshes.push(...homeTransforms);
    return added;
  }

  function forgetModelMeshes(meshes = []) {
    if (!homeView || !meshes.length) return;
    const removed = new Set(meshes);
    homeView.meshes = homeView.meshes.filter(item => !removed.has(item.mesh));
  }

  function resetModelOrientation({ preserveRotation = false } = {}) {
    orientationInitialized = false;
    uprightRotation.identity();
    baseFacingRotation.identity();
    if (!preserveRotation) {
      modelRotation.identity();
      modelTranslation.set(0, 0, 0);
    }
    modelPivot = null;
    homeView = null;
  }

  function resetView() {
    if (!homeView) {
      return {
        meshes: [],
        translationDeltaWorld: new THREE.Vector3(),
      };
    }
    cancelViewSnap?.();
    const previousTranslation = modelTranslation.clone();
    homeView.meshes.forEach(({ mesh, quaternion, position }) => {
      mesh.quaternion.copy(quaternion);
      mesh.position.copy(position);
    });
    const restoredMeshes = homeView.meshes.map(({ mesh }) => mesh);
    modelRotation.identity();
    modelTranslation.set(0, 0, 0);
    const box = computeModelBounds(homeView.meshes.map(({ mesh }) => mesh));
    if (box.isEmpty()) {
      return {
        meshes: restoredMeshes,
        translationDeltaWorld: previousTranslation.multiplyScalar(-1),
      };
    }
    const size = box.getSize(new THREE.Vector3());
    camera.up.copy(INITIAL_CAMERA_UP);
    frameView(homeView.meshes.map(({ mesh }) => mesh),
      INITIAL_CAMERA_DIRECTION, size.y * 0.08);
    camera.updateMatrix();
    camera.updateMatrixWorld();
    controls.setCamera(camera);
    controls.saveState();
    return {
      meshes: restoredMeshes,
      translationDeltaWorld: previousTranslation.multiplyScalar(-1),
    };
  }

  function translationVector(delta) {
    const values = delta?.isVector3
      ? [delta.x, delta.y, delta.z]
      : Array.isArray(delta)
      ? delta
      : [delta?.x, delta?.y, delta?.z];
    if (values.length < 3) return null;
    const vector = new THREE.Vector3(
      Number(values[0]), Number(values[1]), Number(values[2]));
    return Number.isFinite(vector.x) && Number.isFinite(vector.y)
      && Number.isFinite(vector.z) ? vector : null;
  }

  function translateModel(meshes = [], delta) {
    if (!Array.isArray(meshes) || !meshes.length) return [];
    const vector = translationVector(delta);
    if (!vector || vector.lengthSq() === 0) return [];
    meshes.forEach(mesh => mesh.position.add(vector));
    modelTranslation.add(vector);
    return meshes;
  }

  function fitTo(meshes, {
    preserveCamera = false,
    preserveHomeView = false,
    initialRotationY = 0,
  } = {}) {
    const preservedView = preserveCamera ? {
      position: camera.position.clone(),
      quaternion: camera.quaternion.clone(),
      up: camera.up.clone(),
      target: controls.target.clone(),
      zoom: camera.zoom,
    } : null;
    let homeMeshTransforms = null;
    if (!orientationInitialized && meshes.length) {
      const rawBox = computeModelBounds(meshes);
      const rawSize = rawBox.getSize(new THREE.Vector3());
      uprightRotation.identity();
      if (rawSize.z > rawSize.y * 1.5 && rawSize.z > rawSize.x * 1.15) {
        uprightRotation.setFromAxisAngle(
          new THREE.Vector3(1, 0, 0), -Math.PI / 2);
      }
      meshes.forEach(mesh => mesh.quaternion.copy(uprightRotation));
      const uprightBox = computeModelBounds(meshes);
      baseFacingRotation.identity();
      if (!uprightBox.isEmpty()) {
        modelPivot = uprightBox.getCenter(new THREE.Vector3());
        if (Number.isFinite(initialRotationY) && initialRotationY !== 0) {
          baseFacingRotation.setFromAxisAngle(
            new THREE.Vector3(0, 1, 0), initialRotationY);
          // Capture the base game orientation in homeView.  Manual turns are
          // tracked separately in modelRotation and can still be reset.
          rotateMeshesAroundCenter(meshes, baseFacingRotation, modelPivot);
        }
      }
      homeMeshTransforms = meshes.map(mesh => ({
        mesh,
        quaternion: mesh.quaternion.clone(),
        position: mesh.position.clone(),
      }));
      rotateMeshesAroundCenter(meshes, modelRotation, modelPivot);
      meshes.forEach(mesh => mesh.position.add(modelTranslation));
      orientationInitialized = true;
    }
    const box = computeModelBounds(meshes);
    if (box.isEmpty()) return;

    const boxSize = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    if (!modelPivot) modelPivot = center.clone();
    const size = boxSize.length();
    const dimensions = [boxSize.x, boxSize.y, boxSize.z].sort((a, b) => a - b);
    grid.scale.setScalar(Math.max(1.5, dimensions[1]));
    grid.position.set(center.x, box.min.y, center.z);

    camera.near = size * 0.0005;
    camera.far = size * 50;
    clipNear = camera.near;
    clipFar = camera.far;
    camera.updateProjectionMatrix();

    cancelViewSnap?.();
    camera.up.copy(INITIAL_CAMERA_UP);
    frameView(meshes, INITIAL_CAMERA_DIRECTION, boxSize.y * 0.08);
    onModelFit?.(size);
    if (!preserveHomeView) {
      homeView = {
        position: camera.position.clone(),
        target: controls.target.clone(),
        near: camera.near,
        far: camera.far,
        meshes: homeMeshTransforms || meshes.map(mesh => ({
          mesh,
          quaternion: mesh.quaternion.clone(),
          position: mesh.position.clone(),
        })),
      };
    }
    if (preservedView) {
      camera.position.copy(preservedView.position);
      camera.up.copy(preservedView.up);
      camera.zoom = preservedView.zoom;
      controls.target.copy(preservedView.target);
      camera.updateProjectionMatrix();
    }
    camera.updateMatrix();
    controls.update();
    if (preservedView) {
      // Arcball update synchronizes its target and position but reconstructs
      // camera orientation. Restore its exact pre-reload roll/orientation only
      // after that synchronization, then make it the new saved control state.
      camera.quaternion.copy(preservedView.quaternion);
      camera.updateMatrix();
      camera.updateMatrixWorld();
    }
    controls.saveState();
  }

  return {
    adoptModelMeshes,
    fitTo,
    frameView,
    forgetModelMeshes,
    resetModelOrientation,
    resetView,
    resize,
    translateModel,
    rotateModelHorizontalQuarterTurn,
    rotateModelQuarterTurn,
    updateClipping,
    updateViewport,
  };
}
