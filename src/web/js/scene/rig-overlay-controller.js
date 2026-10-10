// Visualization for the inferred skinning rig.
// Nothing in this group is a model mesh or a THREE.Bone; it is overlay
// geometry owned entirely by the Rig panel.

import * as THREE from 'three/webgpu';
import { HUMANOID_CONTROL_PICK_RADIUS } from '../weight-rig/humanoid-rig-edit-session.js';
import { HUMANOID_CONTROL_KEYS, HUMANOID_CONTROL_LIMB_ROLES } from '../weight-rig/humanoid-control-rig.js';
import {
  isRigJointPickingActive,
  isRigTransformInteractionActive,
  setRigJointPickingActive,
  setRigTransformInteractionActive,
} from './rig-overlay-state.js';

const PICK_ACQUIRE_RADIUS = 9;
const PICK_RELEASE_RADIUS = 13;
const PICK_SWITCH_MARGIN = 3;
const PICK_CLICK_RADIUS = 15;
const PICK_CLICK_THRESHOLD = 4;
const HUMANOID_MARKER_SIZE_PX = 10;
const HUMANOID_HALO_SIZE_PX = 26;
const HUMANOID_CANDIDATE_SIZE_PX = 30;
const MODEL_JOINT_MARKER_SIZE_PX = 5;
const MODEL_JOINT_CANDIDATE_SIZE_PX = 9;
const SELECTED_JOINT_INDICATOR_SIZE_PX = 16;

function vector(value) {
  if (value?.isVector3) return value.clone();
  return new THREE.Vector3(Number(value?.[0]) || 0, Number(value?.[1]) || 0, Number(value?.[2]) || 0);
}

function sourceFor(snapshot) {
  if (snapshot?.geometryRigPreview?.rig) {
    return {
      key: 'geometry-preview',
      structureRevision: snapshot.geometryRigPreview.revision,
      humanoidControlRig: snapshot.geometryRigPreview.rig,
    };
  }
  if (snapshot?.model) {
    if (snapshot.model.humanoidControlRig) return snapshot.model;
    if (snapshot.humanoidControlRig) {
      return { ...snapshot.model, humanoidControlRig: snapshot.humanoidControlRig };
    }
    return snapshot.model;
  }
  return snapshot?.humanoidControlRig ? { humanoidControlRig: snapshot.humanoidControlRig } : null;
}

function selectedBoneFor(snapshot) {
  const rawId = snapshot?.selectedJointId;
  if (rawId === null || rawId === undefined || rawId === '') return null;
  const id = Number(rawId);
  return Number.isInteger(id) ? id : null;
}

function componentFor(source, boneId) {
  if (boneId === null || boneId === undefined) return null;
  return source?.components?.find((component) => component.nodeIds.includes(boneId)) || null;
}

function pivotFor(source, boneId) {
  const id = Number(boneId);
  const joint = source?.joints?.find((item) => Number(item?.jointId) === id);
  return joint?.restPivot || joint?.restCenter || null;
}

function quaternionFor(source, boneId) {
  return source?.poseRotationByJointId?.[boneId] || source?.poseRotationByJointId?.get?.(boneId);
}

function topologyKey(source) {
  if (!source) return '';
  return `${source.key ?? ''}:${source.structureRevision ?? ''}`;
}

function canvasRect(canvas) {
  const rect = canvas?.getBoundingClientRect?.();
  if (!rect) return null;
  const width = Number(rect.width) || Number(canvas.clientWidth) || 0;
  const height = Number(rect.height) || Number(canvas.clientHeight) || 0;
  return {
    left: Number(rect.left) || 0,
    top: Number(rect.top) || 0,
    right: Number(rect.right) || 0,
    bottom: Number(rect.bottom) || 0,
    width,
    height,
  };
}

export function projectRigPointToClient({ point, camera, canvas, worldMatrix } = {}) {
  const rect = canvasRect(canvas);
  const value = vector(point);
  if (!rect || rect.width <= 0 || rect.height <= 0 || !camera || !Number.isFinite(value.lengthSq())) return null;
  if (worldMatrix?.isMatrix4) value.applyMatrix4(worldMatrix);
  const projected = value.project(camera);
  if (![projected.x, projected.y, projected.z].every(Number.isFinite) || projected.z < -1 || projected.z > 1)
    return null;
  return {
    x: rect.left + (projected.x + 1) * rect.width * 0.5,
    y: rect.top + (1 - projected.y) * rect.height * 0.5,
    depth: projected.z,
  };
}

export function findNearestRigJoint({ candidates = [], pointer, camera, canvas, worldMatrix, hitRadius = 14 } = {}) {
  const x = Number(pointer?.x ?? pointer?.clientX);
  const y = Number(pointer?.y ?? pointer?.clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const found = candidates
    .flatMap((candidate) => {
      const jointId = Number(candidate?.jointId);
      const screen = projectRigPointToClient({
        point: candidate?.pivot,
        camera,
        canvas,
        worldMatrix,
      });
      if (!Number.isInteger(jointId) || !screen) return [];
      const distance = Math.hypot(screen.x - x, screen.y - y);
      return distance <= hitRadius ? [{ ...candidate, jointId, screen, distance }] : [];
    })
    .sort(
      (left, right) =>
        left.distance - right.distance || left.screen.depth - right.screen.depth || left.jointId - right.jointId,
    );
  if (!found.length) return null;
  return { jointId: found[0].jointId, distance: found[0].distance, screen: found[0].screen, candidates: found };
}

function circularMarkerTexture() {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  const context = canvas.getContext('2d');
  if (!context) return null;
  context.fillStyle = '#ffffff';
  context.beginPath();
  context.arc(16, 16, 15, 0, Math.PI * 2);
  context.fill();
  return new THREE.CanvasTexture(canvas);
}

export function findNearestHumanoidControl({
  controls = {},
  pointer,
  camera,
  canvas,
  worldMatrix,
  hitRadius = HUMANOID_CONTROL_PICK_RADIUS,
} = {}) {
  const x = Number(pointer?.x ?? pointer?.clientX);
  const y = Number(pointer?.y ?? pointer?.clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const found = Object.entries(controls)
    .flatMap(([key, control]) => {
      const point = control?.position || control;
      const screen = projectRigPointToClient({
        point,
        camera,
        canvas,
        worldMatrix,
      });
      if (!screen) return [];
      const distance = Math.hypot(screen.x - x, screen.y - y);
      return distance <= hitRadius ? [{ key, point, screen, distance }] : [];
    })
    .sort((left, right) => left.distance - right.distance || left.key.localeCompare(right.key));
  return found[0] || null;
}

function humanoidRigAvailable(source) {
  const rig = source?.humanoidControlRig;
  return !!rig && rig.available !== false && !(rig.source === 'geometry' && rig.accepted === false);
}

function manipulationMode(snapshot, source, jointId = selectedBoneFor(snapshot)) {
  if (snapshot?.geometryRigPreview?.rig || snapshot?.geometryRigPreview?.busy) return null;
  if (snapshot?.humanoidRigEdit?.editing || snapshot?.jointPickIntent || !source) return null;
  if (snapshot?.ik?.enabled) {
    return snapshot.ik.available &&
      Array.isArray(snapshot.ik.controlKeys) &&
      snapshot.ik.controlKeys.length === 3 &&
      humanoidRigAvailable(source)
      ? 'ik'
      : null;
  }
  const component = componentFor(source, jointId);
  return component && component.rootId !== jointId ? 'fk' : null;
}

function humanoidControlPoint(source, key) {
  const value = source?.humanoidControlRig?.controls?.[key];
  return value?.position || value || null;
}

function ikTargetPoint(snapshot, source) {
  const key = snapshot?.ik?.controlKeys?.[2];
  return key ? humanoidControlPoint(source, key) : null;
}

export { isRigJointPickingActive, isRigTransformInteractionActive };

function setGeometry(object, positions, colors = null) {
  const previous = object.geometry;
  const geometry = new THREE.BufferGeometry();
  if (positions.length) {
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.getAttribute('position')?.setUsage?.(THREE.DynamicDrawUsage);
  }
  if (colors?.length) {
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  }
  object.geometry = geometry;
  previous?.dispose?.();
}

function humanoidRoleColor(role) {
  if (role === 'torso') return [0.92, 0.48, 0.2];
  if (role === 'left_arm') return [1, 0.42, 0.24];
  if (role === 'right_arm') return [0.32, 0.72, 1];
  if (role === 'left_leg') return [1, 0.78, 0.18];
  return [0.54, 1, 0.42];
}

function humanoidConfidenceColor(role, confidence, source) {
  if (source === 'fallback') return [0.95, 0.24, 0.82];
  if (confidence === 'low') return [0.98, 0.56, 0.16];
  if (confidence === 'medium') {
    const base = humanoidRoleColor(role);
    return base.map((value) => value * 0.62 + 0.38);
  }
  return humanoidRoleColor(role);
}

const CONTROL_ROLE_BY_KEY = Object.freeze({
  chest: 'torso',
  pelvis: 'torso',
  neck: 'torso',
  head: 'torso',
  ...HUMANOID_CONTROL_LIMB_ROLES,
});
const CONTROL_KEYS_FOR_OVERLAY = Object.freeze(
  HUMANOID_CONTROL_KEYS.map((key) => ({
    key,
    role: CONTROL_ROLE_BY_KEY[key] || 'torso',
  })),
);

export function createRigOverlayController({
  scene,
  camera,
  canvas,
  getMeshes,
  getRigState,
  getHumanoidRigEditSnapshot,
  getRigJointPoseFrame,
  arcballControls,
  setRigJointRotation,
  solveRigIkTarget,
  finishRigJointPose,
  onTransformControlsUnavailable,
  onRigJointPicked,
  onRigSurfacePickRequested,
  onRigJointPickCancelled,
  beginHumanoidControlCarry,
  updateHumanoidControlDraft,
  finishHumanoidControlCarry,
  cancelHumanoidControlCarry,
  onHumanoidControlSelected,
  requestRender,
} = {}) {
  const selectedIdFor = (snapshot) => selectedBoneFor(snapshot);

  const group = new THREE.Group();
  group.name = 'viewer-inferred-rig-overlay';
  group.userData.isViewerRigOverlay = true;
  group.visible = false;
  scene?.add(group);

  const staticGroup = new THREE.Group();
  staticGroup.name = 'viewer-inferred-rig-static-geometry';
  const modelJointMarkerTexture = circularMarkerTexture();
  const selectedMaterial = new THREE.MeshBasicMaterial({
    color: 0xfacc15,
    depthTest: false,
    depthWrite: false,
  });
  const lineMaterial = new THREE.LineBasicMaterial({
    color: 0x526574,
    vertexColors: true,
    depthTest: false,
    depthWrite: false,
    transparent: true,
    opacity: 0.58,
  });
  const lineSegments = new THREE.LineSegments(new THREE.BufferGeometry(), lineMaterial);
  const modelJointMarkerMaterial = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    map: modelJointMarkerTexture,
    vertexColors: true,
    depthTest: false,
    depthWrite: false,
    transparent: true,
    opacity: 0.58,
    alphaTest: 0.1,
  });
  let modelJointMarkerMesh = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1), modelJointMarkerMaterial, 0);
  modelJointMarkerMesh.name = 'viewer-inferred-rig-model-joint-markers';
  modelJointMarkerMesh.renderOrder = 12;
  modelJointMarkerMesh.frustumCulled = false;
  modelJointMarkerMesh.raycast = () => {};
  modelJointMarkerMesh.instanceMatrix.setUsage?.(THREE.DynamicDrawUsage);
  const selectedJointIndicatorMaterial = new THREE.SpriteMaterial({
    color: 0xfacc15,
    map: modelJointMarkerTexture,
    depthTest: false,
    depthWrite: false,
    transparent: true,
    opacity: 0.95,
    alphaTest: 0.1,
  });
  const selectedJointIndicator = new THREE.Sprite(selectedJointIndicatorMaterial);
  selectedJointIndicator.name = 'viewer-inferred-rig-selected-joint-indicator';
  selectedJointIndicator.renderOrder = 13;
  selectedJointIndicator.frustumCulled = false;
  selectedJointIndicator.raycast = () => {};
  selectedJointIndicator.visible = false;
  const hoverMaterial = new THREE.PointsMaterial({
    color: 0xfacc15,
    size: 0.06,
    sizeAttenuation: false,
    depthTest: false,
    depthWrite: false,
  });
  const hoverPoint = new THREE.Points(new THREE.BufferGeometry(), hoverMaterial);
  const hoverPosition = new THREE.Float32BufferAttribute([0, 0, 0], 3);
  hoverPosition.setUsage?.(THREE.DynamicDrawUsage);
  hoverPoint.geometry.setAttribute('position', hoverPosition);
  hoverPoint.visible = false;
  lineSegments.renderOrder = 10;
  lineSegments.frustumCulled = false;
  hoverPoint.frustumCulled = false;
  lineSegments.raycast = () => {};
  hoverPoint.raycast = () => {};
  staticGroup.add(lineSegments, modelJointMarkerMesh, hoverPoint);
  group.add(staticGroup);
  group.add(selectedJointIndicator);

  // Geometry fitting is intentionally a separate semantic group. It draws
  // virtual controls and medial paths; the inferred ModelJoint Rig remains
  // available in the group above without implying anatomical categories.
  const humanoidGroup = new THREE.Group();
  humanoidGroup.name = 'viewer-humanoid-control-rig-overlay';
  humanoidGroup.userData.isViewerHumanoidOverlay = true;
  humanoidGroup.visible = false;
  const humanoidLineMaterial = new THREE.LineBasicMaterial({
    vertexColors: true,
    depthTest: false,
    depthWrite: false,
  });
  const humanoidMarkerTexture = modelJointMarkerTexture;
  const humanoidLines = new THREE.LineSegments(new THREE.BufferGeometry(), humanoidLineMaterial);
  const humanoidPointSprites = [];
  const humanoidHaloSprites = [];
  const humanoidCandidateSprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      color: 0xfacc15,
      map: humanoidMarkerTexture,
      transparent: true,
      opacity: 0.92,
      depthTest: false,
      depthWrite: false,
      alphaTest: 0.1,
    }),
  );
  humanoidCandidateSprite.name = 'viewer-humanoid-candidate-marker';
  humanoidCandidateSprite.renderOrder = 15;
  humanoidCandidateSprite.frustumCulled = false;
  humanoidCandidateSprite.visible = false;
  humanoidLines.renderOrder = 13;
  humanoidLines.frustumCulled = false;
  humanoidLines.raycast = () => {};
  humanoidGroup.add(humanoidLines, humanoidCandidateSprite);
  group.add(humanoidGroup);

  const proxy = new THREE.Object3D();
  proxy.name = 'viewer-inferred-rig-pose-proxy';
  proxy.userData.isViewerRigOverlay = true;
  const proxyRing = new THREE.Mesh(new THREE.TorusGeometry(0.07, 0.004, 8, 32), selectedMaterial);
  proxyRing.name = 'viewer-inferred-rig-pose-handle';
  proxyRing.raycast = () => {};
  proxy.add(proxyRing);
  group.add(proxy);

  const ikTargetProxy = new THREE.Object3D();
  ikTargetProxy.name = 'viewer-inferred-rig-ik-target';
  ikTargetProxy.userData.isViewerRigOverlay = true;
  const ikTargetMarker = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 8), selectedMaterial);
  ikTargetMarker.name = 'viewer-inferred-rig-ik-target-marker';
  ikTargetMarker.raycast = () => {};
  ikTargetProxy.add(ikTargetMarker);
  ikTargetProxy.visible = false;
  group.add(ikTargetProxy);

  let transformControls = null;
  let transformHelper = null;
  let transformControlsReady = null;
  let controlsCreateCount = 0;
  let arcballWasEnabled = null;
  let selectedJointId = null;
  let currentSnapshot = null;
  let currentSource = null;
  let currentTopologyKey = '';
  let currentHumanoidKey = '';
  let jointById = new Map();
  let modelJointMarkerIds = [];
  let lineBonePairs = [];
  let humanoidLinePairs = [];
  let humanoidLandmarks = [];
  let rebuildCount = 0;
  let modelFrameUpdateCount = 0;
  let posedOverlayUpdateCount = 0;
  let poseDrag = null;
  let transformPointerId = null;
  let hoveredJointId = null;
  let hoveredScreen = null;
  let hoveredControlKey = null;
  let humanoidCarryPlane = null;
  let humanoidCarryOffset = new THREE.Vector3();
  let humanoidJointCandidates = null;
  let humanoidCarryControlKey = null;
  let humanoidCarryPointerId = null;
  let humanoidNavigationGesture = false;
  let humanoidLeftRotateBlocked = false;
  let pickPointer = null;
  let pickCandidateCache = [];
  let pickCandidateCount = 0;
  let lastPickClientPoint = null;
  let suppressContextMenu = false;
  let pickLabel = null;
  let disposed = false;

  if (typeof document !== 'undefined' && canvas?.parentElement) {
    pickLabel = document.createElement('div');
    pickLabel.className = 'rig-joint-pick-label';
    pickLabel.hidden = true;
    pickLabel.setAttribute('aria-hidden', 'true');
    canvas.parentElement.appendChild(pickLabel);
  }

  function setArcballDragState(dragging) {
    if (!arcballControls) return;
    if (dragging) {
      if (arcballWasEnabled === null) {
        arcballWasEnabled = arcballControls.enabled;
        arcballControls.enabled = false;
      }
    } else if (arcballWasEnabled !== null) {
      arcballControls.enabled = arcballWasEnabled;
      arcballWasEnabled = null;
    }
  }

  function releasePointerCapture(pointerId) {
    if (pointerId === null || pointerId === undefined) return;
    try {
      if (canvas?.hasPointerCapture?.(pointerId)) canvas.releasePointerCapture(pointerId);
    } catch {
      /* The browser may already have released a cancelled pointer. */
    }
  }

  function clearPickPointer() {
    releasePointerCapture(pickPointer?.id);
    pickPointer = null;
  }

  function resetPoseDrag({ deferInteraction = false } = {}) {
    poseDrag = null;
    setArcballDragState(false);
    setPickCursor('');
    if (deferInteraction) {
      queueMicrotask(() => {
        if (!disposed && !poseDrag) setRigTransformInteractionActive(false);
      });
    } else setRigTransformInteractionActive(false);
  }

  function detachControls() {
    resetPoseDrag();
    releasePointerCapture(transformPointerId);
    transformPointerId = null;
    if (transformControls) transformControls.enabled = false;
    if (transformControls) transformControls.dragging = false;
    transformControls?.detach?.();
  }

  function updateModelFrame() {
    const meshes = getMeshes?.() || [];
    const mesh = [...meshes].find((item) => item?.visible) || meshes[0];
    if (!mesh) return;
    mesh.updateWorldMatrix?.(true, false);
    // Active model meshes are direct scene children. Copying this transform
    // keeps overlay points in model space when the user moves the model;
    // pose quaternions remain in the inferred model frame.
    group.position.copy(mesh.position);
    group.quaternion.copy(mesh.quaternion);
    group.scale.copy(mesh.scale);
    modelFrameUpdateCount += 1;
    transformControls?.update?.();
  }

  function humanoidControlRigFor(source) {
    const edit = currentSnapshot?.humanoidRigEdit;
    if (edit?.editing && edit.controls) {
      return {
        ...(source?.humanoidControlRig || currentSnapshot?.humanoidControlRig || {}),
        available: true,
        controls: edit.controls,
      };
    }
    return source?.humanoidControlRig || null;
  }

  function setArcballHumanoidCarryState(carrying) {
    if (!arcballControls) return;
    if (carrying) {
      if (!humanoidLeftRotateBlocked) {
        arcballControls.unsetMouseAction?.(0);
        humanoidLeftRotateBlocked = true;
      }
    } else if (humanoidLeftRotateBlocked) {
      arcballControls.setMouseAction?.('ROTATE', 0);
      humanoidLeftRotateBlocked = false;
    }
  }

  function humanoidDisplayPoint(source, key) {
    const edit = currentSnapshot?.humanoidRigEdit;
    if (edit?.editing) {
      const displayed = edit.controls?.[key];
      if (displayed) return displayed.position || displayed;
    }
    const rig = humanoidControlRigFor(source);
    const value = rig?.controls?.[key];
    return value?.position || value || rig?.diagnostics?.templatePoints?.[key] || null;
  }

  function humanoidOverlayKey(source) {
    const rig = humanoidControlRigFor(source);
    if (!rig) return '';
    const structureRevision =
      rig.structureRevision ?? rig.diagnostics?.structureRevision ?? source?.structureRevision ?? '';
    const orientationRevision =
      rig.orientationRevision ??
      rig.modelOrientationRevision ??
      rig.diagnostics?.modelOrientationRevision ??
      source?.humanoidOrientationRevision ??
      0;
    return `${source?.sourceKey ?? source?.key ?? ''}:${structureRevision}:${orientationRevision}`;
  }

  function updateHumanoidVisibility() {
    const editing = currentSnapshot?.humanoidRigEdit?.editing === true;
    const rig = humanoidControlRigFor(currentSource);
    const available = editing ? Object.keys(rig?.controls || {}).length > 0 : humanoidRigAvailable(currentSource);
    humanoidGroup.visible =
      (editing || !!currentSnapshot?.geometryRigPreview?.rig || currentSnapshot?.ik?.enabled === true) &&
      available &&
      humanoidLinePairs.length > 0;
  }

  function setHumanoidSpriteColor(sprite, color) {
    sprite.material.color.setRGB(...color);
  }

  function setHumanoidSpritePosition(sprite, point) {
    const value = vector(point);
    sprite.position.set(value.x, value.y, value.z);
  }

  function markerFrame() {
    group.updateMatrixWorld(true);
    camera?.updateMatrixWorld?.();
    const height = Number(canvasRect(canvas)?.height) || 1;
    const worldScale = group.getWorldScale(new THREE.Vector3());
    const localScale = Math.max(
      0.0001,
      (Math.abs(worldScale.x) + Math.abs(worldScale.y) + Math.abs(worldScale.z)) / 3 || 1,
    );
    const rotation = group.getWorldQuaternion(new THREE.Quaternion()).invert();
    rotation.multiply(camera?.getWorldQuaternion?.(new THREE.Quaternion()) || new THREE.Quaternion());
    const zoom = Number(camera?.zoom) || 1;
    const perspectiveFactor = (2 * Math.tan(THREE.MathUtils.degToRad(camera?.fov || 50) / 2)) / (height * zoom);
    return {
      rotation,
      sizeFor(point, pixels) {
        let worldPerPixel = 0.05;
        if (camera?.isPerspectiveCamera) {
          const view = vector(point).applyMatrix4(group.matrixWorld).applyMatrix4(camera.matrixWorldInverse);
          if (view.z < 0) worldPerPixel = -view.z * perspectiveFactor;
        } else if (camera?.isOrthographicCamera) {
          worldPerPixel = (camera.top - camera.bottom) / (height * zoom);
        }
        return Math.max(0.001, (worldPerPixel * pixels) / localScale);
      },
    };
  }

  function updateHumanoidSpriteSizes(sizing = markerFrame()) {
    const resize = (sprite, pixels) => {
      if (!sprite) return;
      const size = sizing.sizeFor(sprite.position, pixels);
      sprite.scale.set(size, size, 1);
    };
    humanoidLandmarks.forEach((_, index) => {
      resize(humanoidHaloSprites[index], HUMANOID_HALO_SIZE_PX);
      resize(humanoidPointSprites[index], HUMANOID_MARKER_SIZE_PX);
    });
    resize(humanoidCandidateSprite, HUMANOID_CANDIDATE_SIZE_PX);
  }

  function clearHumanoidSprites(sprites) {
    sprites.splice(0).forEach((sprite) => {
      sprite.removeFromParent();
      sprite.material.dispose();
    });
  }

  function rebuildModelJointMarkers() {
    const ids = [...jointById.keys()].filter((id) => !!(jointById.get(id).restPivot || jointById.get(id).restCenter));
    if (ids.length !== modelJointMarkerMesh.count) {
      staticGroup.remove(modelJointMarkerMesh);
      modelJointMarkerMesh.geometry.dispose();
      modelJointMarkerMesh = new THREE.InstancedMesh(
        new THREE.PlaneGeometry(1, 1),
        modelJointMarkerMaterial,
        ids.length,
      );
      modelJointMarkerMesh.name = 'viewer-inferred-rig-model-joint-markers';
      modelJointMarkerMesh.renderOrder = 12;
      modelJointMarkerMesh.frustumCulled = false;
      modelJointMarkerMesh.raycast = () => {};
      modelJointMarkerMesh.instanceMatrix.setUsage?.(THREE.DynamicDrawUsage);
      staticGroup.add(modelJointMarkerMesh);
    }
    modelJointMarkerIds = ids;
    modelJointMarkerMesh.visible = ids.length > 0;
  }

  function posedPivot(jointId, pivots = null) {
    if (pivots) return pivots.get(jointId);
    const joint = jointById.get(jointId);
    return getRigJointPoseFrame?.(jointId)?.pivot || joint?.restPivot || joint?.restCenter;
  }

  function updateModelJointMarkers(pivots = null, sizing = markerFrame()) {
    if (!modelJointMarkerIds.length) return;
    const matrix = new THREE.Matrix4(),
      scale = new THREE.Vector3(),
      color = new THREE.Color();
    const rawCandidate = currentSnapshot?.humanoidRigEdit?.candidateJointId;
    const candidateId =
      rawCandidate === null || rawCandidate === undefined || rawCandidate === '' ? null : Number(rawCandidate);
    modelJointMarkerIds.forEach((jointId, index) => {
      const point = posedPivot(jointId, pivots);
      if (!point) return;
      const pixels = jointId === candidateId ? MODEL_JOINT_CANDIDATE_SIZE_PX : MODEL_JOINT_MARKER_SIZE_PX;
      scale.setScalar(sizing.sizeFor(point, pixels));
      matrix.compose(vector(point), sizing.rotation, scale);
      modelJointMarkerMesh.setMatrixAt(index, matrix);
      if (jointId === candidateId) color.setRGB(1, 0.78, 0.08);
      else if (jointId === selectedJointId) color.setRGB(0.48, 0.82, 0.93);
      else color.setRGB(0.29, 0.42, 0.5);
      modelJointMarkerMesh.setColorAt(index, color);
    });
    modelJointMarkerMesh.instanceMatrix.needsUpdate = true;
    if (modelJointMarkerMesh.instanceColor) modelJointMarkerMesh.instanceColor.needsUpdate = true;
  }

  function updateSelectedJointIndicator(source = currentSource, pivots = null, sizing = markerFrame()) {
    const point = source && Number.isInteger(selectedJointId) ? posedPivot(selectedJointId, pivots) : null;
    selectedJointIndicator.visible =
      !!point &&
      !currentSnapshot?.geometryRigPreview?.rig &&
      !humanoidEditActive() &&
      !currentSnapshot?.jointPickIntent;
    if (!selectedJointIndicator.visible) return;
    selectedJointIndicator.position.copy(vector(point));
    const size = sizing.sizeFor(point, SELECTED_JOINT_INDICATOR_SIZE_PX);
    selectedJointIndicator.scale.set(size, size, 1);
  }

  function createHumanoidSprite({ name, color, opacity, renderOrder }) {
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        color: 0xffffff,
        map: humanoidMarkerTexture,
        transparent: true,
        opacity,
        depthTest: false,
        depthWrite: false,
        alphaTest: 0.1,
      }),
    );
    sprite.name = name;
    sprite.renderOrder = renderOrder;
    sprite.frustumCulled = false;
    setHumanoidSpriteColor(sprite, color);
    humanoidGroup.add(sprite);
    return sprite;
  }

  function editControlColor(role, key) {
    const edit = currentSnapshot?.humanoidRigEdit;
    const base = humanoidRoleColor(role);
    if (!edit?.editing) {
      return currentSnapshot?.ik?.selectedHumanoidControlKey === key ? [1, 0.78, 0.08] : base;
    }
    if (edit.carryingControlKey === key) return [1, 0.96, 0.28];
    if (edit.selectedControlKey === key) return [1, 0.78, 0.08];
    if (hoveredControlKey === key) return [1, 0.9, 0.35];
    return base.map((value) => value * 0.72 + 0.28);
  }

  function updateHumanoidCandidateMarker() {
    humanoidCandidateSprite.visible = false;

    const edit = currentSnapshot?.humanoidRigEdit;
    const rawCandidateId = edit?.candidateJointId;
    if (!edit?.editing || rawCandidateId === null || rawCandidateId === undefined || rawCandidateId === '') {
      return;
    }

    const candidateId = Number(rawCandidateId);
    if (!Number.isInteger(candidateId)) return;

    const joint = jointById.get(candidateId);
    const point = getRigJointPoseFrame?.(candidateId)?.pivot || joint?.restPivot || joint?.restCenter;
    if (!point) return;
    humanoidCandidateSprite.visible = true;
    setHumanoidSpritePosition(humanoidCandidateSprite, point);
    setHumanoidSpriteColor(humanoidCandidateSprite, [1, 0.78, 0.08]);
  }

  function rebuildHumanoidOverlay(source = currentSource) {
    humanoidLinePairs = [];
    humanoidLandmarks = [];
    const linePositions = [];
    const lineColors = [];
    clearHumanoidSprites(humanoidPointSprites);
    clearHumanoidSprites(humanoidHaloSprites);
    const rig = humanoidControlRigFor(source);
    const rigAvailable = humanoidEditActive()
      ? Object.keys(rig?.controls || {}).length > 0
      : humanoidRigAvailable(source);
    const controls = rigAvailable ? rig?.controls || {} : {};
    const controlPoint = (key) => humanoidDisplayPoint(source, key);
    const controlMeta = (key) => controls[key] || {};
    const roleConfidence = (role) => rig?.confidenceByRegion?.[role] || rig?.confidence || 'low';
    const links = [
      ['chest', 'pelvis', 'torso'],
      ['chest', 'neck', 'torso'],
      ['neck', 'head', 'torso'],
      ['chest', 'leftShoulder', 'left_arm'],
      ['chest', 'rightShoulder', 'right_arm'],
      ['leftShoulder', 'leftElbow', 'left_arm'],
      ['leftElbow', 'leftHand', 'left_arm'],
      ['rightShoulder', 'rightElbow', 'right_arm'],
      ['rightElbow', 'rightHand', 'right_arm'],
      ['pelvis', 'leftHip', 'left_leg'],
      ['pelvis', 'rightHip', 'right_leg'],
      ['leftHip', 'leftKnee', 'left_leg'],
      ['leftKnee', 'leftFoot', 'left_leg'],
      ['rightHip', 'rightKnee', 'right_leg'],
      ['rightKnee', 'rightFoot', 'right_leg'],
    ];
    links.forEach(([firstKey, secondKey, role]) => {
      const first = controlPoint(firstKey);
      const second = controlPoint(secondKey);
      if (!first || !second) return;
      const firstMeta = controlMeta(firstKey);
      const secondMeta = controlMeta(secondKey);
      const confidence = [firstMeta.confidence, secondMeta.confidence].includes('low')
        ? 'low'
        : [firstMeta.confidence, secondMeta.confidence].includes('medium')
          ? 'medium'
          : roleConfidence(role);
      const color = humanoidConfidenceColor(
        role,
        confidence,
        firstMeta.source === 'fallback' || secondMeta.source === 'fallback' ? 'fallback' : 'semantic',
      );
      linePositions.push(...vector(first).toArray(), ...vector(second).toArray());
      lineColors.push(...color, ...color);
      humanoidLinePairs.push([firstKey, secondKey]);
    });
    CONTROL_KEYS_FOR_OVERLAY.forEach(({ key, role }) => {
      const point = controlPoint(key);
      if (!point) return;
      humanoidLandmarks.push({ key, role });
      humanoidHaloSprites.push(
        createHumanoidSprite({
          name: `viewer-humanoid-halo-${key}`,
          color: editControlColor(role, key),
          opacity: 0.48,
          renderOrder: 13,
        }),
      );
      humanoidPointSprites.push(
        createHumanoidSprite({
          name: `viewer-humanoid-point-${key}`,
          color: editControlColor(role, key),
          opacity: 1,
          renderOrder: 14,
        }),
      );
      setHumanoidSpritePosition(humanoidHaloSprites.at(-1), point);
      setHumanoidSpritePosition(humanoidPointSprites.at(-1), point);
    });
    setGeometry(humanoidLines, linePositions, lineColors);
  }

  function updateHumanoidPosedOverlay(source = currentSource, sizing = markerFrame()) {
    const rig = humanoidControlRigFor(source);
    if (!rig) return;
    const controlPoint = (key) => humanoidDisplayPoint(source, key);
    const points = new Map(CONTROL_KEYS_FOR_OVERLAY.map(({ key }) => [key, controlPoint(key)]));
    humanoidLandmarks.forEach(({ key, role }, index) => {
      const value = points.get(key);
      if (!value) return;
      const point = vector(value);
      const color = editControlColor(role, key);
      const haloSprite = humanoidHaloSprites[index];
      const pointSprite = humanoidPointSprites[index];
      setHumanoidSpritePosition(haloSprite, point);
      setHumanoidSpritePosition(pointSprite, point);
      setHumanoidSpriteColor(haloSprite, color);
      setHumanoidSpriteColor(pointSprite, color);
    });
    const lineAttribute = humanoidLines.geometry.getAttribute('position');
    humanoidLinePairs.forEach(([firstKey, secondKey], index) => {
      const first = points.get(firstKey);
      const second = points.get(secondKey);
      if (!first || !second || !lineAttribute) return;
      const start = vector(first);
      const end = vector(second);
      lineAttribute.setXYZ(index * 2, start.x, start.y, start.z);
      lineAttribute.setXYZ(index * 2 + 1, end.x, end.y, end.z);
    });
    if (lineAttribute) lineAttribute.needsUpdate = true;
    updateHumanoidCandidateMarker();
    updateHumanoidSpriteSizes(sizing);
  }

  function humanoidEditActive() {
    return currentSnapshot?.humanoidRigEdit?.editing === true;
  }

  function humanoidEditControls() {
    return humanoidControlRigFor(currentSource)?.controls || {};
  }

  function nearestHumanoidControl(clientX, clientY) {
    group.updateMatrixWorld?.(true);
    return findNearestHumanoidControl({
      controls: humanoidEditControls(),
      pointer: { x: clientX, y: clientY },
      camera,
      canvas,
      worldMatrix: group.matrixWorld,
      hitRadius: HUMANOID_CONTROL_PICK_RADIUS,
    });
  }

  function mappedJointIdsExcept(controlKey) {
    const mappings = currentSnapshot?.humanoidRigEdit?.mappedJointIdByControl || {};
    return new Set(
      Object.entries(mappings)
        .filter(([key]) => key !== controlKey)
        .map(([, jointId]) => Number(jointId)),
    );
  }

  function invalidateHumanoidJointCandidates() {
    humanoidJointCandidates = null;
  }

  function buildHumanoidJointCandidates() {
    if (!currentSource) {
      humanoidJointCandidates = [];
      return humanoidJointCandidates;
    }
    group.updateMatrixWorld?.(true);
    humanoidJointCandidates = (currentSource.joints || []).flatMap((joint) => {
      const jointId = Number(joint?.jointId);
      if (!Number.isInteger(jointId)) return [];
      const frame = getRigJointPoseFrame?.(jointId);
      const pivot = frame?.pivot || joint.restPivot || joint.restCenter;
      const screen = projectRigPointToClient({
        point: pivot,
        camera,
        canvas,
        worldMatrix: group.matrixWorld,
      });
      return screen && pivot ? [{ jointId, pivot, screen }] : [];
    });
    return humanoidJointCandidates;
  }

  function nearestMagneticJoint(clientX, clientY, controlKey) {
    const excluded = mappedJointIdsExcept(controlKey);
    const mappedJointId = Number(currentSnapshot?.humanoidRigEdit?.mappedJointIdByControl?.[controlKey]);
    let mappedDistance = Infinity;
    let nearest = null;
    for (const candidate of humanoidJointCandidates || buildHumanoidJointCandidates()) {
      if (excluded.has(candidate.jointId)) continue;
      const distance = Math.hypot(candidate.screen.x - clientX, candidate.screen.y - clientY);
      if (candidate.jointId === mappedJointId) mappedDistance = distance;
      if (
        !nearest ||
        distance < nearest.distance ||
        (distance === nearest.distance && candidate.jointId < nearest.jointId)
      ) {
        nearest = { ...candidate, distance };
      }
    }
    return nearest ? { ...nearest, mappedDistance } : null;
  }

  function rayForClientPoint(clientX, clientY) {
    const rect = canvasRect(canvas);
    if (!rect || !camera) return null;
    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      1 - ((clientY - rect.top) / rect.height) * 2,
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(ndc, camera);
    return raycaster.ray;
  }

  function freeHumanoidPointAt(clientX, clientY) {
    const ray = rayForClientPoint(clientX, clientY);
    if (!ray || !humanoidCarryPlane) return null;
    const world = new THREE.Vector3();
    if (!ray.intersectPlane(humanoidCarryPlane, world)) return null;
    world.add(humanoidCarryOffset);
    group.updateMatrixWorld?.(true);
    return world.applyMatrix4(group.matrixWorld.clone().invert()).toArray();
  }

  function syncAfterEditCallback({ refreshState = false } = {}) {
    if (disposed) return;
    const snapshot = refreshState ? getRigState?.() : null;
    if (snapshot) {
      currentSnapshot = snapshot;
      currentSource = sourceFor(snapshot);
    } else {
      const edit = getHumanoidRigEditSnapshot?.();
      if (edit && currentSnapshot) {
        currentSnapshot = { ...currentSnapshot, humanoidRigEdit: edit };
      }
    }
    updateHumanoidPosedOverlay(currentSource);
    updateHumanoidVisibility();
    requestRender?.();
  }

  function setHumanoidCarryPlane(point, clientX, clientY) {
    group.updateMatrixWorld?.(true);
    const worldPoint = vector(point).applyMatrix4(group.matrixWorld);
    const normal = new THREE.Vector3();
    camera?.getWorldDirection?.(normal);
    if (normal.lengthSq() <= 1e-8) normal.set(0, 0, 1);
    humanoidCarryPlane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, worldPoint);
    const ray = rayForClientPoint(clientX, clientY);
    const hit = new THREE.Vector3();
    if (ray?.intersectPlane(humanoidCarryPlane, hit)) {
      humanoidCarryOffset.copy(worldPoint).sub(hit);
    } else humanoidCarryOffset.set(0, 0, 0);
  }

  function rebaseHumanoidCarry(clientX, clientY) {
    if (!humanoidCarryControlKey) return false;
    const point = humanoidDisplayPoint(currentSource, humanoidCarryControlKey);
    if (!point) return false;
    setHumanoidCarryPlane(point, clientX, clientY);
    return true;
  }

  function beginHumanoidCarryAt(clientX, clientY, pointerId = null) {
    if (!humanoidEditActive() || humanoidCarryControlKey) return false;
    const nearest = nearestHumanoidControl(clientX, clientY);
    if (!nearest) return false;
    setHumanoidCarryPlane(nearest.point, clientX, clientY);
    if (!beginHumanoidControlCarry?.(nearest.key)) return false;
    humanoidCarryControlKey = nearest.key;
    humanoidCarryPointerId = pointerId;
    humanoidNavigationGesture = false;
    hoveredControlKey = nearest.key;
    invalidateHumanoidJointCandidates();
    buildHumanoidJointCandidates();
    setArcballHumanoidCarryState(true);
    syncAfterEditCallback();
    return true;
  }

  function updateHumanoidCarryAt(clientX, clientY) {
    if (!humanoidEditActive() || !humanoidCarryControlKey) return false;
    const position = freeHumanoidPointAt(clientX, clientY);
    if (!position) return false;
    group.updateMatrixWorld?.(true);
    const candidate = nearestMagneticJoint(clientX, clientY, humanoidCarryControlKey);
    updateHumanoidControlDraft?.(humanoidCarryControlKey, position, {
      candidateJointId: candidate?.jointId ?? null,
      candidateDistance: candidate?.distance ?? Infinity,
      mappedDistance: candidate?.mappedDistance ?? Infinity,
      notifyState: false,
      request: false,
    });
    syncAfterEditCallback();
    return true;
  }

  function resetHumanoidCarry() {
    releasePointerCapture(humanoidCarryPointerId);
    humanoidCarryControlKey = null;
    humanoidCarryPlane = null;
    humanoidCarryOffset.set(0, 0, 0);
    humanoidCarryPointerId = null;
    humanoidNavigationGesture = false;
    invalidateHumanoidJointCandidates();
    setArcballHumanoidCarryState(false);
    setPickCursor('');
  }

  function endHumanoidCarry(callback) {
    if (!humanoidCarryControlKey) return false;
    // Runtime callbacks can synchronously publish a new edit snapshot.
    resetHumanoidCarry();
    callback?.();
    syncAfterEditCallback();
    return true;
  }

  function finishHumanoidCarry() {
    return endHumanoidCarry(finishHumanoidControlCarry);
  }
  function cancelHumanoidCarry() {
    return endHumanoidCarry(cancelHumanoidControlCarry);
  }

  function rebuildOverlay(source) {
    jointById = new Map(
      (source?.joints || []).map((joint) => [Number(joint.jointId), joint]).filter(([id]) => Number.isInteger(id)),
    );
    lineBonePairs = [];
    const positions = [],
      colors = [];
    for (const edge of source?.forestEdges || []) {
      const parentId = Number(edge.parentId ?? edge.jointA),
        childId = Number(edge.childId ?? edge.jointB);
      const parent = jointById.get(parentId),
        child = jointById.get(childId);
      const first = parent?.restPivot || parent?.restCenter,
        second = child?.restPivot || child?.restCenter;
      if (!first || !second) continue;
      positions.push(...first, ...second);
      const color = edge.relationshipType === 'attachment' ? [1, 0.48, 0.15] : [0.31, 0.43, 0.51];
      colors.push(...color, ...color);
      lineBonePairs.push([parentId, childId]);
    }
    setGeometry(lineSegments, positions, colors);
    rebuildModelJointMarkers();
    rebuildCount += 1;
  }

  function updatePosedOverlay(source = currentSource) {
    if (!source) {
      updateSelectedJointIndicator(source);
      return;
    }
    const pivots = new Map(modelJointMarkerIds.map((id) => [id, posedPivot(id)]));
    const sizing = markerFrame();
    const attribute = lineSegments.geometry.getAttribute('position');
    lineBonePairs.forEach(([parentId, childId], index) => {
      const parent = pivots.get(parentId),
        child = pivots.get(childId);
      if (!parent || !child || !attribute) return;
      attribute.setXYZ(index * 2, ...vector(parent).toArray());
      attribute.setXYZ(index * 2 + 1, ...vector(child).toArray());
    });
    if (attribute) attribute.needsUpdate = true;
    updateModelJointMarkers(pivots, sizing);
    updateSelectedJointIndicator(source, pivots, sizing);
    updateHumanoidPosedOverlay(source, sizing);
    posedOverlayUpdateCount += 1;
  }

  function buildPickCandidates() {
    return (currentSource?.joints || [])
      .map((joint) => {
        const jointId = Number(joint.jointId);
        const frame = getRigJointPoseFrame?.(jointId);
        return {
          jointId,
          pivot: frame?.pivot || joint.restPivot || joint.restCenter,
        };
      })
      .filter((candidate) => Number.isInteger(candidate.jointId) && candidate.pivot);
  }

  function refreshPickCandidates() {
    if (!currentSnapshot?.jointPickIntent || !currentSource) {
      pickCandidateCache = [];
      clearPickHover();
      return;
    }
    pickCandidateCache = buildPickCandidates();
    if (lastPickClientPoint) {
      updatePickHoverAt(lastPickClientPoint.x, lastPickClientPoint.y);
    }
  }

  function updatePickLabel() {
    if (!pickLabel) return;
    if (hoveredJointId === null || !hoveredScreen) {
      pickLabel.hidden = true;
      return;
    }
    const hostRect = canvas?.parentElement?.getBoundingClientRect?.();
    if (!hostRect) {
      pickLabel.hidden = true;
      return;
    }
    pickLabel.textContent = `Joint ${hoveredJointId}`;
    pickLabel.style.left = `${hoveredScreen.x - hostRect.left + 8}px`;
    pickLabel.style.top = `${hoveredScreen.y - hostRect.top - 10}px`;
    pickLabel.hidden = false;
  }

  function clearPickHover() {
    hoveredJointId = null;
    hoveredScreen = null;
    pickCandidateCount = 0;
    hoverPoint.visible = false;
    updatePickLabel();
  }

  function setPickCursor(value = '') {
    if (canvas?.style) canvas.style.cursor = value;
  }

  function showPickHover(candidate, candidateCount) {
    if (!candidate) {
      clearPickHover();
      return;
    }
    hoveredJointId = candidate.jointId;
    hoveredScreen = candidate.screen;
    pickCandidateCount = candidateCount;
    const value = vector(candidate.pivot);
    const attribute = hoverPoint.geometry.getAttribute('position');
    attribute?.setXYZ(0, value.x, value.y, value.z);
    if (attribute) attribute.needsUpdate = true;
    hoverPoint.visible = true;
    updatePickLabel();
  }

  function nearestPickCandidate(clientX, clientY, hitRadius) {
    group.updateMatrixWorld?.(true);
    return findNearestRigJoint({
      candidates: pickCandidateCache,
      pointer: { x: clientX, y: clientY },
      camera,
      canvas,
      worldMatrix: group.matrixWorld,
      hitRadius,
    });
  }

  function updatePickHoverAt(clientX, clientY) {
    if (!currentSnapshot?.jointPickIntent || !currentSource) {
      clearPickHover();
      return null;
    }
    const nearest = nearestPickCandidate(clientX, clientY, PICK_RELEASE_RADIUS);
    let selected = null;
    if (hoveredJointId !== null) {
      const current = nearest?.candidates?.find((candidate) => candidate.jointId === hoveredJointId);
      if (current) {
        const replacement =
          nearest.jointId !== hoveredJointId && nearest.distance <= current.distance - PICK_SWITCH_MARGIN
            ? nearest.candidates[0]
            : current;
        selected = replacement;
      } else if (nearest && nearest.distance <= PICK_ACQUIRE_RADIUS) {
        selected = nearest.candidates[0];
      }
    } else if (nearest && nearest.distance <= PICK_ACQUIRE_RADIUS) {
      selected = nearest.candidates[0];
    }
    showPickHover(selected, nearest?.candidates?.length || 0);
    setPickCursor(selected ? 'pointer' : 'crosshair');
    requestRender?.();
    return selected;
  }

  function updatePickHover(event) {
    lastPickClientPoint = { x: event.clientX, y: event.clientY };
    const selected = updatePickHoverAt(event.clientX, event.clientY);
    if (currentSnapshot?.jointPickIntent && event.altKey) setPickCursor('grab');
    return selected;
  }

  function onPickPointerMove(event) {
    if (poseDrag && transformPointerId !== null && event.pointerId !== transformPointerId) {
      event.stopImmediatePropagation();
      return;
    }
    if (humanoidEditActive()) {
      if (humanoidCarryControlKey) {
        if (humanoidNavigationGesture || (event.buttons & 2) === 2) return;
        updateHumanoidCarryAt(event.clientX, event.clientY);
      } else {
        const nearest = nearestHumanoidControl(event.clientX, event.clientY);
        hoveredControlKey = nearest?.key || null;
        setPickCursor(nearest ? 'pointer' : '');
        updateHumanoidPosedOverlay(currentSource);
        requestRender?.();
      }
      return;
    }
    if (!currentSnapshot?.jointPickIntent && currentSnapshot?.ik?.enabled === true) {
      const nearest = nearestHumanoidControl(event.clientX, event.clientY);
      hoveredControlKey = nearest?.key || null;
      setPickCursor(nearest ? 'pointer' : '');
      updateHumanoidPosedOverlay(currentSource);
      requestRender?.();
      return;
    }
    if (!currentSnapshot?.jointPickIntent) return;
    if (event.altKey) {
      clearPickHover();
      setPickCursor('grab');
      return;
    }
    updatePickHover(event);
  }

  function onPickPointerDown(event) {
    if (poseDrag && transformPointerId !== null && event.pointerId !== transformPointerId) {
      event.stopImmediatePropagation();
      return;
    }
    if (transformControls?.enabled && !poseDrag) transformPointerId = event.pointerId;
    if (humanoidEditActive()) {
      if (event.button === 2) {
        if (humanoidCarryControlKey) humanoidNavigationGesture = true;
        return;
      }
      if (event.button !== 0 || event.altKey) return;
      if (humanoidCarryControlKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        finishHumanoidCarry();
      } else if (beginHumanoidCarryAt(event.clientX, event.clientY, event.pointerId)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        try {
          canvas?.setPointerCapture?.(event.pointerId);
        } catch {
          /* best effort */
        }
      }
      return;
    }
    if (!currentSnapshot?.jointPickIntent && currentSnapshot?.ik?.enabled === true) {
      if (event.button !== 0 || event.altKey) return;
      const nearest = nearestHumanoidControl(event.clientX, event.clientY);
      if (!nearest) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      hoveredControlKey = nearest.key;
      onHumanoidControlSelected?.(nearest.key);
      currentSnapshot = getRigState?.() || currentSnapshot;
      currentSource = sourceFor(currentSnapshot);
      updateHumanoidPosedOverlay(currentSource);
      requestRender?.();
      return;
    }
    if (!currentSnapshot?.jointPickIntent) return;
    if (event.button === 2) {
      event.preventDefault();
      event.stopImmediatePropagation();
      clearPickPointer();
      suppressContextMenu = true;
      onRigJointPickCancelled?.();
      return;
    }
    if (event.button !== 0 || event.altKey || pickPointer) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    pickPointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
    try {
      canvas?.setPointerCapture?.(event.pointerId);
    } catch {
      /* best effort */
    }
  }

  function onPickPointerUp(event) {
    if (poseDrag && transformPointerId !== null && event.pointerId !== transformPointerId) {
      event.stopImmediatePropagation();
      return;
    }
    if (event.pointerId === transformPointerId) transformPointerId = null;
    if (humanoidEditActive()) {
      if (event.button === 2 && humanoidCarryControlKey) {
        humanoidNavigationGesture = false;
        rebaseHumanoidCarry(event.clientX, event.clientY);
        return;
      }
      if (
        event.button === 0 &&
        humanoidCarryControlKey &&
        (humanoidCarryPointerId === null || event.pointerId === humanoidCarryPointerId)
      ) {
        releasePointerCapture(event.pointerId);
      }
      return;
    }
    if (!currentSnapshot?.jointPickIntent || event.button !== 0 || !pickPointer || event.pointerId !== pickPointer.id)
      return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const start = pickPointer;
    clearPickPointer();
    if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) >= PICK_CLICK_THRESHOLD) return;
    const nearest = nearestPickCandidate(event.clientX, event.clientY, PICK_CLICK_RADIUS);
    if (nearest) {
      onRigJointPicked?.(nearest.jointId, currentSnapshot.jointPickIntent);
      return;
    }
    onRigSurfacePickRequested?.(
      {
        clientX: event.clientX,
        clientY: event.clientY,
      },
      currentSnapshot.jointPickIntent,
    );
  }

  function onPickPointerCancel(event) {
    if (event.pointerId === transformPointerId) {
      releasePointerCapture(transformPointerId);
      transformPointerId = null;
      if (transformControls) transformControls.dragging = false;
      return;
    }
    if (humanoidEditActive()) {
      if (humanoidNavigationGesture) {
        humanoidNavigationGesture = false;
        rebaseHumanoidCarry(event?.clientX, event?.clientY);
        return;
      }
      if (humanoidCarryControlKey && (humanoidCarryPointerId === null || event?.pointerId === humanoidCarryPointerId)) {
        cancelHumanoidCarry();
      }
      return;
    }
    if (pickPointer && event?.pointerId !== undefined && event.pointerId !== pickPointer.id) return;
    clearPickPointer();
    if (currentSnapshot?.jointPickIntent) setPickCursor('crosshair');
  }

  function onPickContextMenu(event) {
    if (humanoidEditActive()) {
      return;
    }
    if (suppressContextMenu) {
      suppressContextMenu = false;
      event.preventDefault();
      return;
    }
    if (!currentSnapshot?.jointPickIntent) return;
    event.preventDefault();
    clearPickPointer();
    onRigJointPickCancelled?.();
  }

  function onPickKeyDown(event) {
    if (humanoidEditActive() && event.key === 'Escape') {
      event.preventDefault();
      cancelHumanoidCarry();
      return;
    }
    if (currentSnapshot?.jointPickIntent && event.key === 'Alt') {
      clearPickHover();
      setPickCursor('grab');
      return;
    }
    if (currentSnapshot?.jointPickIntent && event.key === 'Escape') {
      event.preventDefault();
      clearPickPointer();
      onRigJointPickCancelled?.();
    }
  }

  function onPickKeyUp(event) {
    if (currentSnapshot?.jointPickIntent && event.key === 'Alt') {
      if (lastPickClientPoint) {
        updatePickHoverAt(lastPickClientPoint.x, lastPickClientPoint.y);
      } else setPickCursor('crosshair');
    }
  }

  function poseTargetKey(snapshot, source, mode, jointId) {
    return `${topologyKey(source)}:${mode}:${mode === 'ik' ? snapshot.ik.controlKeys.join('|') : jointId}`;
  }

  function updateProxy(source = currentSource, snapshot = currentSnapshot, poseEvent = null) {
    const jointId = poseEvent ? Number(poseEvent.jointId) : selectedIdFor(snapshot);
    selectedJointId = jointId;
    const mode = manipulationMode(snapshot, source, jointId);
    if (!mode) {
      detachControls();
      proxy.visible = ikTargetProxy.visible = false;
      return;
    }
    if (poseDrag) {
      if (poseDrag.targetKey === poseTargetKey(snapshot, source, mode, jointId)) return;
      detachControls();
    }
    proxy.visible = mode === 'fk';
    ikTargetProxy.visible = mode === 'ik';
    const target = mode === 'ik' ? ikTargetProxy : proxy;
    if (mode === 'ik') {
      const point = ikTargetPoint(snapshot, source);
      if (point) target.position.copy(vector(point));
    } else {
      const frame = getRigJointPoseFrame?.(jointId);
      const point = frame?.pivot || pivotFor(source, jointId);
      if (point) target.position.copy(vector(point));
      const rotation =
        frame?.gizmoRotation || frame?.boneRotation || poseEvent?.quaternion || quaternionFor(source, jointId);
      if (rotation) target.quaternion.set(...rotation).normalize();
      else target.quaternion.identity();
    }
    transformControls?.setMode?.(mode === 'ik' ? 'translate' : 'rotate');
    transformControls?.setSpace?.(mode === 'ik' ? 'world' : 'local');
    if (transformControls) transformControls.enabled = true;
    transformControls?.attach?.(target);
    transformControls?.update?.();
  }

  function syncRotationSnap(snapshot = currentSnapshot) {
    const degrees = Number(snapshot?.rotationSnapDegrees) || 0;
    transformControls?.setRotationSnap?.(
      !snapshot?.ik?.enabled && degrees > 0 ? THREE.MathUtils.degToRad(degrees) : null,
    );
  }

  function updatePoseFromEvent(detail) {
    if (disposed || !Number.isInteger(Number(detail?.jointId))) return;
    invalidateHumanoidJointCandidates();
    updatePosedOverlay(currentSource);
    if (currentSnapshot?.jointPickIntent) refreshPickCandidates();
    // The gesture owns its proxy while canonical frames update underneath it.
    if (!poseDrag) updateProxy(currentSource, currentSnapshot, detail);
  }

  function beginPoseDrag() {
    if (disposed || poseDrag) return;
    const jointId = selectedIdFor(currentSnapshot);
    const mode = manipulationMode(currentSnapshot, currentSource, jointId);
    if (!mode) return;
    const frame = mode === 'fk' ? getRigJointPoseFrame?.(jointId) : null;
    poseDrag = {
      mode,
      jointId: mode === 'fk' ? jointId : null,
      targetKey: poseTargetKey(currentSnapshot, currentSource, mode, jointId),
      parentRotation: new THREE.Quaternion(...(frame?.parentRotation || [0, 0, 0, 1])).normalize(),
      restRotation: new THREE.Quaternion(...(frame?.restRotation || [0, 0, 0, 1])).normalize(),
    };
    setPickCursor('grabbing');
    setArcballDragState(true);
    setRigTransformInteractionActive(true);
  }

  function finishPoseDrag() {
    const drag = poseDrag;
    if (disposed || !drag) return;
    resetPoseDrag({ deferInteraction: true });
    if (drag.mode === 'ik') solveRigIkTarget?.(ikTargetProxy.position.toArray(), { dragging: false });
    else finishRigJointPose?.(drag.jointId);
    currentSnapshot = getRigState?.() || currentSnapshot;
    currentSource = sourceFor(currentSnapshot);
    updatePosedOverlay(currentSource);
    updateProxy(currentSource, currentSnapshot);
  }

  async function ensureTransformControls() {
    if (disposed || !manipulationMode(currentSnapshot, currentSource, selectedIdFor(currentSnapshot))) return null;
    if (transformControlsReady) return transformControlsReady;
    transformControlsReady = import('three/addons/controls/TransformControls.js')
      .then((module) => {
        if (disposed || !manipulationMode(currentSnapshot, currentSource, selectedIdFor(currentSnapshot))) {
          transformControlsReady = null;
          return null;
        }
        transformControls = new module.TransformControls(camera, canvas);
        transformHelper = transformControls.getHelper();
        transformHelper.userData.isViewerRigTransformHelper = true;
        scene?.add(transformHelper);
        controlsCreateCount += 1;
        transformControls.addEventListener?.('change', () => {
          if (!disposed) requestRender?.();
        });
        transformControls.addEventListener?.('objectChange', () => {
          if (disposed || !poseDrag) return;
          if (poseDrag.mode === 'ik') {
            solveRigIkTarget?.(ikTargetProxy.position.toArray(), { dragging: true });
            currentSnapshot = getRigState?.() || currentSnapshot;
            currentSource = sourceFor(currentSnapshot);
            updateHumanoidPosedOverlay(currentSource);
          } else {
            const rotation = poseDrag.parentRotation
              .clone()
              .invert()
              .multiply(proxy.quaternion)
              .multiply(poseDrag.restRotation.clone().invert())
              .normalize();
            setRigJointRotation?.(poseDrag.jointId, rotation, { dragging: true });
          }
        });
        transformControls.addEventListener?.('dragging-changed', (event) => {
          if (event.value === true) beginPoseDrag();
          else if (event.value === false) finishPoseDrag();
        });
        syncRotationSnap(currentSnapshot);
        updateProxy(currentSource, currentSnapshot);
        return transformControls;
      })
      .catch(() => {
        transformControlsReady = null;
        if (!disposed) onTransformControlsUnavailable?.();
        return null;
      });
    return transformControlsReady;
  }

  function refresh(snapshot = getRigState?.()) {
    if (disposed) return;
    const wasJointPicking = isRigJointPickingActive();
    currentSnapshot = snapshot || {};
    currentSource = sourceFor(currentSnapshot);
    invalidateHumanoidJointCandidates();
    setRigJointPickingActive(!!currentSnapshot.jointPickIntent);
    const humanoidEditing = humanoidEditActive();
    if (humanoidCarryControlKey) {
      if (!humanoidEditing || topologyKey(currentSource) !== currentTopologyKey) cancelHumanoidCarry();
      else if (!currentSnapshot.humanoidRigEdit.carryingControlKey) resetHumanoidCarry();
    }
    if (!humanoidEditing) hoveredControlKey = null;
    selectedJointId = selectedIdFor(currentSnapshot);
    const nextTopologyKey = topologyKey(currentSource);
    if (nextTopologyKey !== currentTopologyKey) {
      currentTopologyKey = nextTopologyKey;
      rebuildOverlay(currentSource);
    }
    const layout = CONTROL_KEYS_FOR_OVERLAY.filter(({ key }) => !!humanoidDisplayPoint(currentSource, key))
      .map(({ key }) => key)
      .join('|');
    const nextHumanoidKey = `${humanoidOverlayKey(currentSource)}:${layout}`;
    if (nextHumanoidKey !== currentHumanoidKey) {
      currentHumanoidKey = nextHumanoidKey;
      rebuildHumanoidOverlay(currentSource);
    }
    updateModelFrame();
    group.visible = !!currentSource;
    updateHumanoidVisibility();
    staticGroup.visible = (humanoidEditing || !!currentSnapshot.jointPickIntent) && !!currentSource;
    if (!currentSnapshot.jointPickIntent) {
      pickCandidateCache = [];
      clearPickPointer();
      lastPickClientPoint = null;
      clearPickHover();
      if (wasJointPicking) setPickCursor('');
    }
    updatePosedOverlay(currentSource);
    if (currentSnapshot.jointPickIntent && !humanoidEditing) {
      refreshPickCandidates();
      if (hoveredJointId === null) setPickCursor('crosshair');
    }
    updateProxy(currentSource, currentSnapshot);
    syncRotationSnap(currentSnapshot);
    if (!humanoidEditing && manipulationMode(currentSnapshot, currentSource, selectedIdFor(currentSnapshot))) {
      void ensureTransformControls();
    }
    requestRender?.();
  }

  const onRigChanged = (event) => refresh(event.detail || getRigState?.());
  const onPoseChanged = (event) => updatePoseFromEvent(event.detail);
  const onArcballChanged = () => {
    invalidateHumanoidJointCandidates();
    const sizing = markerFrame();
    updateHumanoidSpriteSizes(sizing);
    updateModelJointMarkers(null, sizing);
    updateSelectedJointIndicator(currentSource, null, sizing);
    requestRender?.();
  };
  const onModelTransformChanged = () => {
    invalidateHumanoidJointCandidates();
    updateModelFrame();
    const sizing = markerFrame();
    updateHumanoidSpriteSizes(sizing);
    updateModelJointMarkers(null, sizing);
    updateSelectedJointIndicator(currentSource, null, sizing);
    if (currentSnapshot?.jointPickIntent && lastPickClientPoint) {
      updatePickHoverAt(lastPickClientPoint.x, lastPickClientPoint.y);
    }
    requestRender?.();
  };
  window.addEventListener('mod-viewer-model-rig-changed', onRigChanged);
  window.addEventListener('mod-viewer-model-rig-pose-changed', onPoseChanged);
  window.addEventListener('mod-viewer-model-transform-changed', onModelTransformChanged);
  arcballControls?.addEventListener?.('change', onArcballChanged);
  canvas?.addEventListener('pointermove', onPickPointerMove, true);
  canvas?.addEventListener('pointerdown', onPickPointerDown, true);
  canvas?.addEventListener('pointerup', onPickPointerUp, true);
  canvas?.addEventListener('pointercancel', onPickPointerCancel, true);
  canvas?.addEventListener('contextmenu', onPickContextMenu);
  if (typeof document !== 'undefined') document.addEventListener('keydown', onPickKeyDown);
  if (typeof document !== 'undefined') document.addEventListener('keyup', onPickKeyUp);

  return {
    group,
    refresh,
    ensureTransformControls,
    getDebugState() {
      return {
        rebuildCount,
        modelFrameUpdateCount,
        posedOverlayUpdateCount,
        staticObjectCount: staticGroup.children.length,
        groupVisible: group.visible,
        staticVisible: staticGroup.visible,
        nodeCount: jointById.size,
        modelJointMarkerCount: modelJointMarkerMesh.count,
        modelJointMarkerType: modelJointMarkerMesh.type,
        modelJointMarkerInstanced: modelJointMarkerMesh.isInstancedMesh === true,
        modelJointMarkerSizePx: MODEL_JOINT_MARKER_SIZE_PX,
        modelJointCandidateSizePx: MODEL_JOINT_CANDIDATE_SIZE_PX,
        selectedJointIndicatorVisible: selectedJointIndicator.visible,
        selectedJointIndicatorPosition: selectedJointIndicator.position.toArray(),
        selectedJointIndicatorSizePx: SELECTED_JOINT_INDICATOR_SIZE_PX,
        edgeCount: lineSegments.geometry.getAttribute('position')?.count / 2 || 0,
        humanoidOverlayVisible: humanoidGroup.visible,
        humanoidSegmentCount: humanoidLinePairs.length,
        humanoidLandmarkCount: humanoidLandmarks.length,
        humanoidHaloCount: humanoidHaloSprites.length,
        humanoidPointSpriteCount: humanoidPointSprites.length,
        humanoidHaloSpriteCount: humanoidHaloSprites.length,
        humanoidMarkerTextureReady: !!humanoidMarkerTexture,
        humanoidEditActive: humanoidEditActive(),
        hoveredControlKey,
        carryingControlKey: humanoidCarryControlKey,
        candidateJointId: currentSnapshot?.humanoidRigEdit?.candidateJointId ?? null,
        selectedJointId,
        proxyVisible: proxy.visible,
        ikTargetVisible: ikTargetProxy.visible,
        controlsCreated: !!transformControls,
        controlsCreateCount,
        controlsAttached: transformControls?.object === proxy || transformControls?.object === ikTargetProxy,
        controlsAttachedTo:
          transformControls?.object === ikTargetProxy
            ? 'ik-target'
            : transformControls?.object === proxy
              ? 'fk-proxy'
              : null,
        helperInScene: !!transformHelper && transformHelper.parent === scene,
        arcballEnabled: arcballControls?.enabled,
        arcballWasEnabled,
        poseDragActive: !!poseDrag,
        hoveredJointId,
        pickCandidateCount,
        pickLabelVisible: !!pickLabel && !pickLabel.hidden,
      };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelHumanoidCarry();
      clearPickPointer();
      setPickCursor('');
      setRigJointPickingActive(false);
      window.removeEventListener('mod-viewer-model-rig-changed', onRigChanged);
      window.removeEventListener('mod-viewer-model-rig-pose-changed', onPoseChanged);
      window.removeEventListener('mod-viewer-model-transform-changed', onModelTransformChanged);
      arcballControls?.removeEventListener?.('change', onArcballChanged);
      canvas?.removeEventListener('pointermove', onPickPointerMove, true);
      canvas?.removeEventListener('pointerdown', onPickPointerDown, true);
      canvas?.removeEventListener('pointerup', onPickPointerUp, true);
      canvas?.removeEventListener('pointercancel', onPickPointerCancel, true);
      canvas?.removeEventListener('contextmenu', onPickContextMenu);
      if (typeof document !== 'undefined') {
        document.removeEventListener('keydown', onPickKeyDown);
        document.removeEventListener('keyup', onPickKeyUp);
      }
      pickLabel?.remove?.();
      detachControls();
      if (transformControls) {
        scene?.remove(transformHelper);
        transformControls.dispose?.();
        transformControls = null;
        transformHelper = null;
      }
      lineSegments.geometry.dispose();
      modelJointMarkerMesh.geometry.dispose();
      humanoidLines.geometry.dispose();
      hoverPoint.geometry.dispose();
      proxyRing.geometry.dispose();
      modelJointMarkerMaterial.dispose();
      hoverMaterial.dispose();
      selectedMaterial.dispose();
      lineMaterial.dispose();
      humanoidLineMaterial.dispose();
      humanoidCandidateSprite.material.dispose();
      selectedJointIndicatorMaterial.dispose();
      clearHumanoidSprites(humanoidPointSprites);
      clearHumanoidSprites(humanoidHaloSprites);
      modelJointMarkerTexture?.dispose?.();
      ikTargetMarker.geometry.dispose();
      group.remove(staticGroup, humanoidGroup, proxy, ikTargetProxy);
      scene?.remove(group);
    },
  };
}
