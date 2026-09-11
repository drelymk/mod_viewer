// Visualization for the inferred skinning rig.
// Nothing in this group is a model mesh or a THREE.Bone; it is overlay
// geometry owned entirely by the Rig panel.

import * as THREE from 'three/webgpu';
import {
  HUMANOID_CONTROL_PICK_RADIUS,
  JOINT_ATTRACTION_RADIUS_PX,
} from '../mesh/humanoid-rig-edit-session.js';
import {
  HUMANOID_CONTROL_KEYS, HUMANOID_CONTROL_LIMB_ROLES,
} from '../mesh/humanoid-control-rig.js';

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

function vector(value) {
  if (value?.isVector3) return value.clone();
  return new THREE.Vector3(
    Number(value?.[0]) || 0,
    Number(value?.[1]) || 0,
    Number(value?.[2]) || 0);
}

function sourceFor(snapshot) {
  if (snapshot?.model) {
    if (snapshot.model.humanoidControlRig) return snapshot.model;
    if (snapshot.humanoidControlRig) {
      return {...snapshot.model, humanoidControlRig: snapshot.humanoidControlRig};
    }
    return snapshot.model;
  }
  return snapshot?.humanoidControlRig
    ? {humanoidControlRig: snapshot.humanoidControlRig} : null;
}

function selectedBoneFor(snapshot) {
  const rawId = snapshot?.selectedJointId;
  if (rawId === null || rawId === undefined || rawId === '') return null;
  const id = Number(rawId);
  return Number.isInteger(id) ? id : null;
}

function componentFor(source, boneId) {
  if (boneId === null || boneId === undefined) return null;
  return source?.components?.find(component =>
    component.nodeIds.includes(boneId)) || null;
}

function overlayNodeIds(source) {
  return new Set((source?.joints || []).map(joint => Number(joint.jointId)));
}

function overlayPresentationKey(source) {
  return topologyKey(source);
}

function pivotFor(source, boneId) {
  const joint = source?.joints?.find(item => item.jointId === Number(boneId));
  return joint?.restPivot || joint?.restCenter || null;
}

function quaternionFor(source, boneId) {
  return source?.poseRotationByJointId?.[boneId]
    || source?.poseRotationByJointId?.get?.(boneId);
}

function topologyKey(source) {
  if (!source) return '';
  if (source.structureRevision !== null
      && source.structureRevision !== undefined) {
    return `${source.sourceKey}:${source.structureRevision}`;
  }
  return JSON.stringify([source.key, source.structureRevision]);
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

export function projectRigPointToClient({point, camera, canvas, worldMatrix} = {}) {
  const rect = canvasRect(canvas);
  const value = vector(point);
  if (!rect || rect.width <= 0 || rect.height <= 0
      || !camera || !Number.isFinite(value.lengthSq())) return null;
  if (worldMatrix?.isMatrix4) value.applyMatrix4(worldMatrix);
  const projected = value.project(camera);
  if (![projected.x, projected.y, projected.z].every(Number.isFinite)
      || projected.z < -1 || projected.z > 1) return null;
  return {
    x: rect.left + (projected.x + 1) * rect.width * 0.5,
    y: rect.top + (1 - projected.y) * rect.height * 0.5,
    depth: projected.z,
  };
}

export function findNearestRigJoint({candidates = [], pointer, camera, canvas,
    worldMatrix, hitRadius = 14} = {}) {
  const x = Number(pointer?.x ?? pointer?.clientX);
  const y = Number(pointer?.y ?? pointer?.clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const found = candidates.flatMap(candidate => {
    const jointId = Number(candidate?.jointId);
    const screen = projectRigPointToClient({
      point: candidate?.pivot, camera, canvas, worldMatrix,
    });
    if (!Number.isInteger(jointId) || !screen) return [];
    const distance = Math.hypot(screen.x - x, screen.y - y);
    return distance <= hitRadius ? [{...candidate, jointId, screen, distance}] : [];
  }).sort((left, right) => left.distance - right.distance
    || left.screen.depth - right.screen.depth
    || left.jointId - right.jointId);
  if (!found.length) return null;
  return {jointId: found[0].jointId, distance: found[0].distance,
    screen: found[0].screen, candidates: found};
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

export function findNearestHumanoidControl({controls = {}, pointer, camera,
    canvas, worldMatrix, hitRadius = HUMANOID_CONTROL_PICK_RADIUS} = {}) {
  const x = Number(pointer?.x ?? pointer?.clientX);
  const y = Number(pointer?.y ?? pointer?.clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const found = Object.entries(controls).flatMap(([key, control]) => {
    const point = control?.position || control;
    const screen = projectRigPointToClient({
      point, camera, canvas, worldMatrix,
    });
    if (!screen) return [];
    const distance = Math.hypot(screen.x - x, screen.y - y);
    return distance <= hitRadius ? [{key, point, screen, distance}] : [];
  }).sort((left, right) => left.distance - right.distance
    || left.key.localeCompare(right.key));
  return found[0] || null;
}

function canFkPose(snapshot, source, boneId = selectedBoneFor(snapshot)) {
  if (snapshot?.jointPickIntent || !source
      || boneId === null) {
    return false;
  }
  const component = componentFor(source, boneId);
  return !!component && component.rootId !== boneId;
}

function humanoidRigAvailable(source) {
  const rig = source?.humanoidControlRig;
  return !!rig && rig.available !== false
    && !(rig.source === 'geometry' && rig.accepted === false);
}

function canIkPose(snapshot, source, boneId = selectedBoneFor(snapshot)) {
  if (snapshot?.jointPickIntent || !source) return false;
  const ik = snapshot?.ik;
  if (Array.isArray(ik?.controlKeys) && ik.controlKeys.length === 3) {
    return !!ik?.enabled && !!ik.available
      && humanoidRigAvailable(source);
  }
  if (boneId === null) return false;
  return !!ik?.enabled && !!ik.available
    && Number(ik.endJointId) === Number(boneId);
}

function isPrimaryHumanoidIk(snapshot, source) {
  return Array.isArray(snapshot?.ik?.controlKeys)
    && snapshot.ik.controlKeys.length === 3
    && humanoidRigAvailable(source);
}

function manipulationMode(snapshot, source, boneId = selectedBoneFor(snapshot)) {
  if (canIkPose(snapshot, source, boneId)) return 'ik';
  if (snapshot?.ik?.enabled) return null;
  return canFkPose(snapshot, source, boneId) ? 'fk' : null;
}

function humanoidControlPoint(source, key) {
  const value = source?.humanoidControlRig?.controls?.[key];
  return value?.position || value || null;
}

function ikTargetPoint(snapshot, source) {
  const key = snapshot?.ik?.controlKeys?.[2];
  return key ? humanoidControlPoint(source, key) : null;
}

let rigTransformInteractionActive = false;
let rigJointPickingActive = false;

export function isRigTransformInteractionActive() {
  return rigTransformInteractionActive;
}

export function isRigJointPickingActive() {
  return rigJointPickingActive;
}

function setGeometry(object, positions, colors = null) {
  const previous = object.geometry;
  const geometry = new THREE.BufferGeometry();
  if (positions.length) {
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(
      positions, 3));
    geometry.getAttribute('position')?.setUsage?.(THREE.DynamicDrawUsage);
  }
  if (colors?.length) {
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(
      colors, 3));
  }
  object.geometry = geometry;
  previous?.dispose?.();
}

function centerColor(component, jointId, selectedJointId) {
  if (jointId === selectedJointId) return [1, .78, .08];
  if (component?.rootId === jointId) return [1, .28, .4];
  return [.49, .83, .99];
}

function humanoidRoleColor(role) {
  if (role === 'torso') return [.92, .48, .2];
  if (role === 'left_arm') return [1, .42, .24];
  if (role === 'right_arm') return [.32, .72, 1];
  if (role === 'left_leg') return [1, .78, .18];
  return [.54, 1, .42];
}

function humanoidConfidenceColor(role, confidence, source) {
  if (source === 'fallback') return [.95, .24, .82];
  if (confidence === 'low') return [.98, .56, .16];
  if (confidence === 'medium') {
    const base = humanoidRoleColor(role);
    return base.map(value => value * .62 + .38);
  }
  return humanoidRoleColor(role);
}

const CONTROL_ROLE_BY_KEY = Object.freeze({
  chest: 'torso', pelvis: 'torso', neck: 'torso', head: 'torso',
  ...HUMANOID_CONTROL_LIMB_ROLES,
});
const CONTROL_KEYS_FOR_OVERLAY = Object.freeze(HUMANOID_CONTROL_KEYS.map(key => ({
  key, role: CONTROL_ROLE_BY_KEY[key] || 'torso',
})));

export function createRigOverlayController({
  scene, camera, canvas, getMeshes, getRigState,
  getRigJointPoseFrame, arcballControls, setRigJointRotation,
  solveRigIkTarget, finishRigJointPose, onTransformControlsUnavailable,
  onRigJointPicked, onRigSurfacePickRequested, onRigJointPickCancelled,
  beginHumanoidControlCarry, updateHumanoidControlDraft,
  finishHumanoidControlCarry, cancelHumanoidControlCarry,
  onHumanoidControlSelected,
  requestRender,
} = {}) {
  const selectedIdFor = snapshot => selectedBoneFor(snapshot);

  const group = new THREE.Group();
  group.name = 'viewer-inferred-rig-overlay';
  group.userData.isViewerRigOverlay = true;
  group.visible = false;
  scene?.add(group);

  const staticGroup = new THREE.Group();
  staticGroup.name = 'viewer-inferred-rig-static-geometry';
  const modelJointMarkerTexture = circularMarkerTexture();
  const centerMaterial = new THREE.PointsMaterial({
    size: 0.025, sizeAttenuation: false, vertexColors: true,
    depthTest: false, depthWrite: false, transparent: true, opacity: .45,
  });
  const jointMaterial = new THREE.PointsMaterial({
    color: 0x34d399, size: 0.018, sizeAttenuation: false,
    depthTest: false, depthWrite: false, transparent: true, opacity: .5,
  });
  const selectedMaterial = new THREE.MeshBasicMaterial({
    color: 0xfacc15, depthTest: false, depthWrite: false,
  });
  const lineMaterial = new THREE.LineBasicMaterial({
    color: 0x526574, vertexColors: true,
    depthTest: false, depthWrite: false, transparent: true, opacity: .58,
  });
  const lineSegments = new THREE.LineSegments(
    new THREE.BufferGeometry(), lineMaterial);
  const centerPoints = new THREE.Points(
    new THREE.BufferGeometry(), centerMaterial);
  const jointPoints = new THREE.Points(
    new THREE.BufferGeometry(), jointMaterial);
  const modelJointMarkerMaterial = new THREE.MeshBasicMaterial({
    color: 0xffffff, map: modelJointMarkerTexture, vertexColors: true,
    depthTest: false, depthWrite: false, transparent: true, opacity: .58,
    alphaTest: .1,
  });
  let modelJointMarkerMesh = new THREE.InstancedMesh(
    new THREE.PlaneGeometry(1, 1), modelJointMarkerMaterial, 0);
  modelJointMarkerMesh.name = 'viewer-inferred-rig-model-joint-markers';
  modelJointMarkerMesh.renderOrder = 12;
  modelJointMarkerMesh.frustumCulled = false;
  modelJointMarkerMesh.raycast = () => {};
  modelJointMarkerMesh.instanceMatrix.setUsage?.(THREE.DynamicDrawUsage);
  const hoverMaterial = new THREE.PointsMaterial({
    color: 0xfacc15, size: 0.06, sizeAttenuation: false,
    depthTest: false, depthWrite: false,
  });
  const hoverPoint = new THREE.Points(
    new THREE.BufferGeometry(), hoverMaterial);
  const hoverPosition = new THREE.Float32BufferAttribute([0, 0, 0], 3);
  hoverPosition.setUsage?.(THREE.DynamicDrawUsage);
  hoverPoint.geometry.setAttribute('position', hoverPosition);
  hoverPoint.visible = false;
  lineSegments.renderOrder = 10;
  centerPoints.renderOrder = 11;
  jointPoints.renderOrder = 12;
  lineSegments.frustumCulled = false;
  centerPoints.frustumCulled = false;
  jointPoints.frustumCulled = false;
  hoverPoint.frustumCulled = false;
  lineSegments.raycast = () => {};
  centerPoints.raycast = () => {};
  jointPoints.raycast = () => {};
  hoverPoint.raycast = () => {};
  jointPoints.visible = false;
  // Influence centers are not articulation points. Showing them alongside
  // pivot-to-pivot edges makes the inferred skeleton appear offset from its
  // own joints, especially on dense meshes. ModelJoint markers below are the
  // authoritative visible point layer.
  centerPoints.visible = false;
  staticGroup.add(lineSegments, centerPoints, jointPoints,
    modelJointMarkerMesh, hoverPoint);
  group.add(staticGroup);

  // Geometry fitting is intentionally a separate semantic group. It draws
  // virtual controls and medial paths; the inferred ModelJoint Rig remains
  // available in the group above without implying anatomical categories.
  const humanoidGroup = new THREE.Group();
  humanoidGroup.name = 'viewer-humanoid-control-rig-overlay';
  humanoidGroup.userData.isViewerHumanoidOverlay = true;
  humanoidGroup.visible = false;
  const humanoidLineMaterial = new THREE.LineBasicMaterial({
    vertexColors: true, depthTest: false, depthWrite: false,
  });
  const humanoidMarkerTexture = circularMarkerTexture();
  const humanoidPointMaterial = new THREE.PointsMaterial({
    size: 12, sizeAttenuation: false, vertexColors: true,
    depthTest: false, depthWrite: false, map: humanoidMarkerTexture,
    alphaTest: .1, transparent: true,
  });
  const humanoidHaloMaterial = new THREE.PointsMaterial({
    size: 21, sizeAttenuation: false, vertexColors: true,
    depthTest: false, depthWrite: false, map: humanoidMarkerTexture,
    alphaTest: .1, transparent: true, opacity: .48,
  });
  const humanoidLines = new THREE.LineSegments(
    new THREE.BufferGeometry(), humanoidLineMaterial);
  const humanoidPoints = new THREE.Points(
    new THREE.BufferGeometry(), humanoidPointMaterial);
  const humanoidHalos = new THREE.Points(
    new THREE.BufferGeometry(), humanoidHaloMaterial);
  const humanoidCandidateMaterial = new THREE.PointsMaterial({
      color: 0xfacc15, size: 24, sizeAttenuation: false,
      depthTest: false, depthWrite: false, map: humanoidMarkerTexture,
      alphaTest: .1, transparent: true, opacity: .8,
    });
  const humanoidCandidate = new THREE.Points(
    new THREE.BufferGeometry(), humanoidCandidateMaterial);
  // WebGPU renders THREE.Points as one-pixel point primitives. Keep these
  // buffers for the overlay's lightweight position/update bookkeeping, but
  // use sprites for the visible controls so their circular size is honored.
  humanoidPoints.visible = false;
  humanoidHalos.visible = false;
  humanoidCandidate.visible = false;
  const humanoidPointSprites = [];
  const humanoidHaloSprites = [];
  const humanoidCandidateSprite = new THREE.Sprite(new THREE.SpriteMaterial({
    color: 0xfacc15, map: humanoidMarkerTexture, transparent: true,
    opacity: .92, depthTest: false, depthWrite: false, alphaTest: .1,
  }));
  humanoidCandidateSprite.name = 'viewer-humanoid-candidate-marker';
  humanoidCandidateSprite.renderOrder = 15;
  humanoidCandidateSprite.frustumCulled = false;
  humanoidCandidateSprite.visible = false;
  humanoidLines.renderOrder = 13;
  humanoidPoints.renderOrder = 14;
  humanoidHalos.renderOrder = 13;
  humanoidCandidate.renderOrder = 15;
  humanoidLines.frustumCulled = false;
  humanoidPoints.frustumCulled = false;
  humanoidHalos.frustumCulled = false;
  humanoidCandidate.frustumCulled = false;
  humanoidLines.raycast = () => {};
  humanoidPoints.raycast = () => {};
  humanoidHalos.raycast = () => {};
  humanoidCandidate.raycast = () => {};
  humanoidGroup.add(humanoidLines, humanoidHalos, humanoidPoints,
    humanoidCandidate, humanoidCandidateSprite);
  group.add(humanoidGroup);

  const proxy = new THREE.Object3D();
  proxy.name = 'viewer-inferred-rig-pose-proxy';
  proxy.userData.isViewerRigOverlay = true;
  const proxyRing = new THREE.Mesh(
    new THREE.TorusGeometry(0.07, 0.004, 8, 32), selectedMaterial);
  proxyRing.name = 'viewer-inferred-rig-pose-handle';
  proxyRing.raycast = () => {};
  proxy.add(proxyRing);
  group.add(proxy);

  const ikTargetProxy = new THREE.Object3D();
  ikTargetProxy.name = 'viewer-inferred-rig-ik-target';
  ikTargetProxy.userData.isViewerRigOverlay = true;
  const ikTargetMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.045, 12, 8), selectedMaterial);
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
  let nodeBoneIds = [];
  let nodeByBoneId = new Map();
  let nodeIndexByBoneId = new Map();
  let modelJointMarkerIds = [];
  let lineBonePairs = [];
  let jointChildBoneIds = [];
  let humanoidLinePairs = [];
  let humanoidLandmarks = [];
  let rebuildCount = 0;
  let modelFrameUpdateCount = 0;
  let posedOverlayUpdateCount = 0;
  let poseDragActive = false;
  let dragBoneId = null;
  let dragParentRotation = null;
  let dragRestRotation = null;
  let dragJointId = null;
  let dragMode = null;
  let hoveredJointId = null;
  let hoveredScreen = null;
  let hoveredControlKey = null;
  let humanoidCarryPlane = null;
  let humanoidCarryOffset = new THREE.Vector3();
  let humanoidCarryControlKey = null;
  let humanoidCarryPointerId = null;
  let humanoidNavigationGesture = false;
  let humanoidLeftRotateBlocked = false;
  let humanoidCandidateJointId = null;
  let humanoidCandidateScreen = null;
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

  function detachControls() {
    transformControls?.detach?.();
    setArcballDragState(false);
    poseDragActive = false;
    dragBoneId = null;
    dragParentRotation = null;
    dragRestRotation = null;
    dragJointId = null;
    dragMode = null;
    rigTransformInteractionActive = false;
  }

  function updateModelFrame() {
    const meshes = getMeshes?.() || [];
    const mesh = [...meshes].find(item => item?.visible) || meshes[0];
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

  function updateCenterColors(source = currentSource) {
    const colors = centerPoints.geometry.getAttribute('color');
    if (!colors) return;
    nodeBoneIds.forEach((boneId, index) => {
      const color = centerColor(
        componentFor(source, boneId), boneId, selectedJointId);
      colors.setXYZ(index, ...color);
    });
    colors.needsUpdate = true;
  }

  function humanoidControlRigFor(source) {
    const edit = currentSnapshot?.humanoidRigEdit;
    if (edit?.editing && edit.controls) {
      return {
        ...(source?.humanoidControlRig
          || currentSnapshot?.humanoidControlRig || {}),
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
    return value?.position || value
      || rig?.diagnostics?.templatePoints?.[key]
      || null;
  }

  function humanoidOverlayKey(source) {
    const rig = humanoidControlRigFor(source);
    if (!rig) return '';
    const structureRevision = rig.structureRevision
      ?? rig.diagnostics?.structureRevision
      ?? source?.structureRevision ?? '';
    const orientationRevision = rig.orientationRevision
      ?? rig.modelOrientationRevision
      ?? rig.diagnostics?.modelOrientationRevision
      ?? source?.humanoidOrientationRevision ?? 0;
    return `${source?.sourceKey ?? source?.key ?? ''}:${structureRevision}:${orientationRevision}`;
  }

  function updateHumanoidVisibility() {
    const editing = currentSnapshot?.humanoidRigEdit?.editing === true;
    const rig = humanoidControlRigFor(currentSource);
    const available = editing
      ? Object.keys(rig?.controls || {}).length > 0
      : humanoidRigAvailable(currentSource);
    humanoidGroup.visible = (editing || currentSnapshot?.ik?.enabled === true)
      && available
      && humanoidLinePairs.length > 0;
  }

  function setHumanoidSpriteColor(sprite, color) {
    sprite.material.color.setRGB(...color);
  }

  function setHumanoidSpritePosition(sprite, point) {
    const value = vector(point);
    sprite.position.set(value.x, value.y, value.z);
  }

  function updateHumanoidSpriteSizes() {
    const rect = canvasRect(canvas);
    const height = Number(rect?.height) || 0;
    const cameraHeight = height || 1;
    camera?.updateMatrixWorld?.();
    humanoidGroup.updateMatrixWorld?.(true);
    const worldPerPixelFor = point => {
      if (!camera) return .05;
      const worldPoint = vector(point).applyMatrix4(humanoidGroup.matrixWorld);
      const viewPoint = worldPoint.applyMatrix4(camera.matrixWorldInverse);
      if (camera.isPerspectiveCamera) {
        const depth = -viewPoint.z;
        if (!(depth > 0)) return .05;
        const fov = THREE.MathUtils.degToRad(camera.fov || 50);
        return (2 * depth * Math.tan(fov / 2))
          / (cameraHeight * (Number(camera.zoom) || 1));
      }
      if (camera.isOrthographicCamera) {
        return (camera.top - camera.bottom)
          / (cameraHeight * (Number(camera.zoom) || 1));
      }
      return .05;
    };
    const resize = (sprite, pixels) => {
      if (!sprite) return;
      const size = Math.max(.001, worldPerPixelFor(sprite.position) * pixels);
      sprite.scale.set(size, size, 1);
    };
    humanoidLandmarks.forEach((_, index) => {
      resize(humanoidHaloSprites[index], HUMANOID_HALO_SIZE_PX);
      resize(humanoidPointSprites[index], HUMANOID_MARKER_SIZE_PX);
    });
    resize(humanoidCandidateSprite, HUMANOID_CANDIDATE_SIZE_PX);
  }

  function clearHumanoidSprites(sprites) {
    sprites.splice(0).forEach(sprite => {
      sprite.removeFromParent();
      sprite.material.dispose();
    });
  }

  function rebuildModelJointMarkers(source) {
    const ids = (source?.joints || []).map(joint => Number(joint.jointId))
      .filter(jointId => Number.isInteger(jointId)
        && !!pivotFor(source, jointId));
    if (ids.length !== modelJointMarkerMesh.count) {
      staticGroup.remove(modelJointMarkerMesh);
      modelJointMarkerMesh.geometry.dispose();
      modelJointMarkerMesh = new THREE.InstancedMesh(
        new THREE.PlaneGeometry(1, 1), modelJointMarkerMaterial, ids.length);
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

  function updateModelJointMarkers(source = currentSource) {
    if (!modelJointMarkerMesh || !modelJointMarkerIds.length) return;
    group.updateMatrixWorld?.(true);
    camera?.updateMatrixWorld?.();
    const rect = canvasRect(canvas);
    const canvasHeight = Number(rect?.height) || 1;
    const worldScale = new THREE.Vector3();
    group.getWorldScale?.(worldScale);
    const localScale = Math.max(.0001,
      (Math.abs(worldScale.x) + Math.abs(worldScale.y)
        + Math.abs(worldScale.z)) / 3 || 1);
    const cameraQuaternion = new THREE.Quaternion();
    const parentQuaternion = new THREE.Quaternion();
    const localQuaternion = new THREE.Quaternion();
    camera?.getWorldQuaternion?.(cameraQuaternion);
    group.getWorldQuaternion?.(parentQuaternion);
    localQuaternion.copy(parentQuaternion).invert()
      .multiply(cameraQuaternion);
    const worldPoint = new THREE.Vector3();
    const viewPoint = new THREE.Vector3();
    const matrix = new THREE.Matrix4();
    const scale = new THREE.Vector3();
    const color = new THREE.Color();
    const candidateId = Number(currentSnapshot?.humanoidRigEdit
      ?.candidateJointId);
    modelJointMarkerIds.forEach((jointId, index) => {
      const frame = getRigJointPoseFrame?.(jointId);
      const point = frame?.pivot || pivotFor(source, jointId);
      if (!point) return;
      const localPoint = vector(point);
      worldPoint.copy(localPoint).applyMatrix4(group.matrixWorld);
      viewPoint.copy(worldPoint).applyMatrix4(camera?.matrixWorldInverse
        || new THREE.Matrix4());
      let worldPerPixel = .05;
      if (camera?.isPerspectiveCamera) {
        const depth = -viewPoint.z;
        if (depth > 0) {
          const fov = THREE.MathUtils.degToRad(camera.fov || 50);
          worldPerPixel = (2 * depth * Math.tan(fov / 2))
            / (canvasHeight * (Number(camera.zoom) || 1));
        }
      } else if (camera?.isOrthographicCamera) {
        worldPerPixel = (camera.top - camera.bottom)
          / (canvasHeight * (Number(camera.zoom) || 1));
      }
      const pixels = Number(jointId) === candidateId
        ? MODEL_JOINT_CANDIDATE_SIZE_PX : MODEL_JOINT_MARKER_SIZE_PX;
      const size = Math.max(.001, worldPerPixel * pixels / localScale);
      scale.set(size, size, size);
      matrix.compose(localPoint, localQuaternion, scale);
      modelJointMarkerMesh.setMatrixAt(index, matrix);
      if (Number(jointId) === candidateId) color.setRGB(1, .78, .08);
      else if (Number(jointId) === selectedJointId) color.setRGB(.48, .82, .93);
      else color.setRGB(.29, .42, .50);
      modelJointMarkerMesh.setColorAt(index, color);
    });
    modelJointMarkerMesh.instanceMatrix.needsUpdate = true;
    if (modelJointMarkerMesh.instanceColor) {
      modelJointMarkerMesh.instanceColor.needsUpdate = true;
    }
  }

  function createHumanoidSprite({name, color, opacity, renderOrder}) {
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      color: 0xffffff, map: humanoidMarkerTexture, transparent: true,
      opacity, depthTest: false, depthWrite: false, alphaTest: .1,
    }));
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
      return currentSnapshot?.ik?.selectedHumanoidControlKey === key
        ? [1, .78, .08] : base;
    }
    if (edit.carryingControlKey === key) return [1, .96, .28];
    if (edit.selectedControlKey === key) return [1, .78, .08];
    if (hoveredControlKey === key) return [1, .9, .35];
    return base.map(value => value * .72 + .28);
  }

  function updateHumanoidCandidateMarker() {
    const edit = currentSnapshot?.humanoidRigEdit;
    const joint = edit?.editing && Number.isInteger(Number(
      edit.candidateJointId))
      ? currentSource?.joints?.find(item => Number(item.jointId)
        === Number(edit.candidateJointId)) : null;
    const point = getRigJointPoseFrame?.(Number(edit?.candidateJointId))?.pivot
      || joint?.restPivot || joint?.restCenter;
    humanoidCandidate.visible = false;
    humanoidCandidateSprite.visible = !!point;
    if (!point) return;
    setHumanoidSpritePosition(humanoidCandidateSprite, point);
    setHumanoidSpriteColor(humanoidCandidateSprite, [1, .78, .08]);
    const attribute = humanoidCandidate.geometry.getAttribute('position');
    if (!attribute || attribute.count !== 1) {
      setGeometry(humanoidCandidate, vector(point).toArray());
      return;
    }
    const value = vector(point);
    attribute.setXYZ(0, value.x, value.y, value.z);
    attribute.needsUpdate = true;
    updateHumanoidSpriteSizes();
  }

  function rebuildHumanoidOverlay(source = currentSource) {
    humanoidLinePairs = [];
    humanoidLandmarks = [];
    const linePositions = [];
    const lineColors = [];
    const pointPositions = [];
    const pointColors = [];
    const haloPositions = [];
    const haloColors = [];
    clearHumanoidSprites(humanoidPointSprites);
    clearHumanoidSprites(humanoidHaloSprites);
    const rig = humanoidControlRigFor(source);
    const rigAvailable = humanoidEditActive()
      ? Object.keys(rig?.controls || {}).length > 0
      : humanoidRigAvailable(source);
    const controls = rigAvailable ? (rig?.controls || {}) : {};
    const controlPoint = key => humanoidDisplayPoint(source, key)
      || controls[key]?.position || controls[key]
      || rig?.diagnostics?.templatePoints?.[key] || null;
    const controlMeta = key => controls[key] || {};
    const roleConfidence = role => rig?.confidenceByRegion?.[role]
      || rig?.confidence || 'low';
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
      const confidence = [firstMeta.confidence, secondMeta.confidence]
        .includes('low') ? 'low' : [firstMeta.confidence, secondMeta.confidence]
          .includes('medium') ? 'medium' : roleConfidence(role);
      const color = humanoidConfidenceColor(role, confidence,
          firstMeta.source === 'fallback' || secondMeta.source === 'fallback'
            ? 'fallback' : 'semantic');
      linePositions.push(...vector(first).toArray(), ...vector(second).toArray());
      lineColors.push(...color, ...color);
      humanoidLinePairs.push([firstKey, secondKey]);
    });
    CONTROL_KEYS_FOR_OVERLAY.forEach(({key, role}) => {
      const point = controlPoint(key);
      if (!point) return;
      pointPositions.push(...vector(point).toArray());
      const meta = controlMeta(key);
      pointColors.push(...editControlColor(role, key));
      haloPositions.push(...vector(point).toArray());
      haloColors.push(...editControlColor(role, key));
      humanoidLandmarks.push({key, role});
      humanoidHaloSprites.push(createHumanoidSprite({
        name: `viewer-humanoid-halo-${key}`,
        color: editControlColor(role, key), opacity: .48, renderOrder: 13,
      }));
      humanoidPointSprites.push(createHumanoidSprite({
        name: `viewer-humanoid-point-${key}`,
        color: editControlColor(role, key), opacity: 1, renderOrder: 14,
      }));
      setHumanoidSpritePosition(humanoidHaloSprites.at(-1), point);
      setHumanoidSpritePosition(humanoidPointSprites.at(-1), point);
    });
    setGeometry(humanoidLines, linePositions, lineColors);
    setGeometry(humanoidHalos, haloPositions, haloColors);
    setGeometry(humanoidPoints, pointPositions, pointColors);
    updateHumanoidCandidateMarker();
    updateHumanoidSpriteSizes();
  }

  function updateHumanoidPosedOverlay(source = currentSource) {
    const rig = humanoidControlRigFor(source);
    if (!rig) return;
    const controls = rig.controls || {};
    const controlPoint = key => humanoidDisplayPoint(source, key)
      || controls[key]?.position || controls[key]
      || rig?.diagnostics?.templatePoints?.[key] || null;
    const points = new Map(CONTROL_KEYS_FOR_OVERLAY.map(({key}) => [
      key, controlPoint(key),
    ]));
    const pointAttribute = humanoidPoints.geometry.getAttribute('position');
    const pointColors = humanoidPoints.geometry.getAttribute('color');
    const haloAttribute = humanoidHalos.geometry.getAttribute('position');
    const haloColors = humanoidHalos.geometry.getAttribute('color');
    humanoidLandmarks.forEach(({key}, index) => {
      const value = points.get(key);
      if (!value || !pointAttribute) return;
      const point = vector(value);
      pointAttribute.setXYZ(index, point.x, point.y, point.z);
      haloAttribute?.setXYZ(index, point.x, point.y, point.z);
      if (haloColors) haloColors.setXYZ(index, ...editControlColor(
        CONTROL_KEYS_FOR_OVERLAY.find(item => item.key === key)?.role || 'torso',
        key));
      if (pointColors) pointColors.setXYZ(index, ...editControlColor(
        CONTROL_KEYS_FOR_OVERLAY.find(item => item.key === key)?.role || 'torso',
        key));
      const role = CONTROL_KEYS_FOR_OVERLAY.find(item => item.key === key)?.role
        || 'torso';
      const color = editControlColor(role, key);
      const haloSprite = humanoidHaloSprites[index];
      const pointSprite = humanoidPointSprites[index];
      setHumanoidSpritePosition(haloSprite, point);
      setHumanoidSpritePosition(pointSprite, point);
      setHumanoidSpriteColor(haloSprite, color);
      setHumanoidSpriteColor(pointSprite, color);
    });
    if (pointAttribute) pointAttribute.needsUpdate = true;
    if (haloAttribute) haloAttribute.needsUpdate = true;
    if (haloColors) haloColors.needsUpdate = true;
    if (pointColors) pointColors.needsUpdate = true;
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
    updateHumanoidSpriteSizes();
  }

  function humanoidEditActive() {
    return currentSnapshot?.humanoidRigEdit?.editing === true;
  }

  function humanoidEditControls() {
    const edit = currentSnapshot?.humanoidRigEdit;
    if (edit?.editing && edit.controls) {
      return Object.fromEntries(Object.keys(edit.controls).map(key => [key, {
        ...edit.controls[key],
        position: humanoidDisplayPoint(currentSource, key)
          || edit.controls[key]?.position,
      }]));
    }
    return humanoidControlRigFor(currentSource)?.controls || {};
  }

  function nearestHumanoidControl(clientX, clientY) {
    group.updateMatrixWorld?.(true);
    return findNearestHumanoidControl({
      controls: humanoidEditControls(),
      pointer: {x: clientX, y: clientY}, camera, canvas,
      worldMatrix: group.matrixWorld,
      hitRadius: HUMANOID_CONTROL_PICK_RADIUS,
    });
  }

  function mappedJointIdsExcept(controlKey) {
    const mappings = currentSnapshot?.humanoidRigEdit
      ?.mappedJointIdByControl || {};
    return new Set(Object.entries(mappings)
      .filter(([key]) => key !== controlKey)
      .map(([, jointId]) => Number(jointId)));
  }

  function nearestMagneticJoint(clientX, clientY, controlKey) {
    const excluded = mappedJointIdsExcept(controlKey);
    const mappedJointId = Number(currentSnapshot?.humanoidRigEdit
      ?.mappedJointIdByControl?.[controlKey]);
    let mappedDistance = Infinity;
    const candidates = (currentSource?.joints || []).flatMap(joint => {
      const jointId = Number(joint.jointId);
      if (!Number.isInteger(jointId) || excluded.has(jointId)) return [];
      const pivot = getRigJointPoseFrame?.(jointId)?.pivot
        || joint.restPivot || joint.restCenter;
      const screen = projectRigPointToClient({
        point: pivot, camera, canvas, worldMatrix: group.matrixWorld,
      });
      if (!screen) return [];
      const distance = Math.hypot(screen.x - clientX, screen.y - clientY);
      if (jointId === mappedJointId) mappedDistance = distance;
      return [{jointId, pivot, screen, distance}];
    }).sort((left, right) => left.distance - right.distance
      || left.jointId - right.jointId);
    return candidates[0] ? {...candidates[0], mappedDistance} : null;
  }

  function rayForClientPoint(clientX, clientY) {
    const rect = canvasRect(canvas);
    if (!rect || !camera) return null;
    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      1 - ((clientY - rect.top) / rect.height) * 2);
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

  function syncAfterEditCallback() {
    const snapshot = getRigState?.();
    if (snapshot) {
      currentSnapshot = snapshot;
      currentSource = sourceFor(snapshot);
      updateHumanoidPosedOverlay(currentSource);
      updateHumanoidCandidateMarker();
      updateHumanoidVisibility();
    }
    requestRender?.();
  }

  function setHumanoidCarryPlane(point, clientX, clientY) {
    group.updateMatrixWorld?.(true);
    const worldPoint = vector(point).applyMatrix4(group.matrixWorld);
    const normal = new THREE.Vector3();
    camera?.getWorldDirection?.(normal);
    if (normal.lengthSq() <= 1e-8) normal.set(0, 0, 1);
    humanoidCarryPlane = new THREE.Plane()
      .setFromNormalAndCoplanarPoint(normal, worldPoint);
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
    setArcballHumanoidCarryState(true);
    syncAfterEditCallback();
    return true;
  }

  function updateHumanoidCarryAt(clientX, clientY) {
    if (!humanoidEditActive() || !humanoidCarryControlKey) return false;
    const position = freeHumanoidPointAt(clientX, clientY);
    if (!position) return false;
    group.updateMatrixWorld?.(true);
    const candidate = nearestMagneticJoint(
      clientX, clientY, humanoidCarryControlKey);
    updateHumanoidControlDraft?.(humanoidCarryControlKey, position, {
      candidateJointId: candidate?.jointId ?? null,
      candidateDistance: candidate?.distance ?? Infinity,
      mappedDistance: candidate?.mappedDistance ?? Infinity,
    });
    humanoidCandidateJointId = candidate && candidate.distance
      <= JOINT_ATTRACTION_RADIUS_PX ? candidate.jointId : null;
    humanoidCandidateScreen = candidate?.screen || null;
    syncAfterEditCallback();
    return true;
  }

  function finishHumanoidCarry() {
    if (!humanoidCarryControlKey) return false;
    finishHumanoidControlCarry?.();
    humanoidCarryControlKey = null;
    humanoidCarryPlane = null;
    humanoidCarryOffset.set(0, 0, 0);
    humanoidCarryPointerId = null;
    humanoidNavigationGesture = false;
    humanoidCandidateJointId = null;
    humanoidCandidateScreen = null;
    setArcballHumanoidCarryState(false);
    setPickCursor('');
    syncAfterEditCallback();
    return true;
  }

  function cancelHumanoidCarry() {
    if (!humanoidCarryControlKey) return false;
    cancelHumanoidControlCarry?.();
    humanoidCarryControlKey = null;
    humanoidCarryPlane = null;
    humanoidCarryOffset.set(0, 0, 0);
    humanoidCarryPointerId = null;
    humanoidNavigationGesture = false;
    humanoidCandidateJointId = null;
    humanoidCandidateScreen = null;
    setArcballHumanoidCarryState(false);
    setPickCursor('');
    syncAfterEditCallback();
    return true;
  }

  function rebuildOverlay(source) {
    const visibleIds = overlayNodeIds(source);
    const modelNodes = (source?.joints || []).map(joint => [
      Number(joint.jointId), joint.restCenter,
    ]).filter(([boneId]) => visibleIds.has(boneId));
    nodeByBoneId = new Map(modelNodes.filter(([boneId, center]) => visibleIds.has(boneId)
        && Number.isInteger(boneId) && center));
    nodeBoneIds = [...nodeByBoneId.keys()];
    nodeIndexByBoneId = new Map(nodeBoneIds.map((boneId, index) => [
      boneId, index]));
    lineBonePairs = [];
    jointChildBoneIds = [];
    const nodePositions = [];
    const nodeColors = [];
    nodeBoneIds.forEach(boneId => {
      const center = nodeByBoneId.get(boneId);
      if (!center) return;
      nodePositions.push(...center);
      nodeColors.push(...centerColor(
        componentFor(source, boneId), boneId, selectedJointId));
    });

    const linePositions = [];
    const lineColors = [];
    const jointPositions = [];
    (source?.forestEdges || []).forEach(edge => {
      const parentId = Number(edge.parentId ?? edge.jointA ?? edge.boneA);
      const childId = Number(edge.childId ?? edge.jointB ?? edge.boneB);
      const first = nodeByBoneId.get(parentId);
      const second = nodeByBoneId.get(childId);
      const firstPivot = pivotFor(source, parentId);
      const secondPivot = pivotFor(source, childId);
      if (first && second && firstPivot && secondPivot) {
        linePositions.push(...firstPivot, ...secondPivot);
        const color = edge.relationshipType === 'attachment'
          ? [1, .48, .15] : [.31, .43, .51];
        lineColors.push(...color, ...color);
      }
      if (first && second && firstPivot && secondPivot) {
        lineBonePairs.push([parentId, childId]);
      }
      const joint = edge.jointCenter || pivotFor(source, childId);
      if (first && second && joint) {
        jointChildBoneIds.push(childId);
        jointPositions.push(...joint);
      }
    });
    if (source?.joints?.length) {
      jointChildBoneIds = source.joints
        .map(joint => Number(joint.jointId))
        .filter(jointId => visibleIds.has(jointId));
      jointPositions.length = 0;
      source.joints.forEach(joint => {
        if (!visibleIds.has(Number(joint.jointId))) return;
        const pivot = joint.restPivot || joint.restCenter;
        if (pivot) jointPositions.push(...pivot);
      });
    }
    setGeometry(lineSegments, linePositions, lineColors);
    setGeometry(centerPoints, nodePositions, nodeColors);
    setGeometry(jointPoints, jointPositions);
    rebuildModelJointMarkers(source);
    updatePosedOverlay(source);
    rebuildCount += 1;
  }

  function updatePosedOverlay(source = currentSource) {
    if (!source) return;
    const centers = new Map();
    const pivots = new Map();
    const centerAttribute = centerPoints.geometry.getAttribute('position');
    nodeBoneIds.forEach(boneId => {
      const index = nodeIndexByBoneId.get(boneId);
      if (!Number.isInteger(index)) return;
      const frame = getRigJointPoseFrame?.(boneId);
      const center = frame?.center || nodeByBoneId.get(boneId);
      if (!center || !centerAttribute) return;
      const value = vector(center);
      centerAttribute.setXYZ(index, value.x, value.y, value.z);
      centers.set(boneId, value);
      const pivot = frame?.pivot || pivotFor(source, boneId);
      if (pivot) pivots.set(boneId, vector(pivot));
    });
    if (centerAttribute) centerAttribute.needsUpdate = true;

    const lineAttribute = lineSegments.geometry.getAttribute('position');
    lineBonePairs.forEach(([parentId, childId], index) => {
      const parent = pivots.get(parentId);
      const child = pivots.get(childId);
      if (!parent || !child || !lineAttribute) return;
      lineAttribute.setXYZ(index * 2, parent.x, parent.y, parent.z);
      lineAttribute.setXYZ(index * 2 + 1, child.x, child.y, child.z);
    });
    if (lineAttribute) lineAttribute.needsUpdate = true;

    const jointAttribute = jointPoints.geometry.getAttribute('position');
    jointChildBoneIds.forEach((childId, index) => {
      if (!jointAttribute) return;
      const frame = getRigJointPoseFrame?.(childId);
      const value = frame?.pivot || pivotFor(source, childId);
      if (!value) return;
      const joint = vector(value);
      jointAttribute.setXYZ(index, joint.x, joint.y, joint.z);
    });
    if (jointAttribute) jointAttribute.needsUpdate = true;
    updateModelJointMarkers(source);
    updateHumanoidPosedOverlay(source);
    posedOverlayUpdateCount += 1;
  }

  function buildPickCandidates() {
    return (currentSource?.joints || []).map(joint => {
      const jointId = Number(joint.jointId);
      const frame = getRigJointPoseFrame?.(jointId);
      return {
        jointId,
        pivot: frame?.pivot || joint.restPivot || joint.restCenter,
      };
    }).filter(candidate => Number.isInteger(candidate.jointId)
      && candidate.pivot);
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
      pointer: {x: clientX, y: clientY},
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
    const nearest = nearestPickCandidate(
      clientX, clientY, PICK_RELEASE_RADIUS);
    let selected = null;
    if (hoveredJointId !== null) {
      const current = nearest?.candidates?.find(candidate =>
        candidate.jointId === hoveredJointId);
      if (current) {
        const replacement = nearest.jointId !== hoveredJointId
          && nearest.distance <= current.distance - PICK_SWITCH_MARGIN
          ? nearest.candidates[0] : current;
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
    lastPickClientPoint = {x: event.clientX, y: event.clientY};
    const selected = updatePickHoverAt(event.clientX, event.clientY);
    if (currentSnapshot?.jointPickIntent && event.altKey) setPickCursor('grab');
    return selected;
  }

  function onPickPointerMove(event) {
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
    if (!currentSnapshot?.jointPickIntent
        && currentSnapshot?.ik?.enabled === true) {
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
      } else if (beginHumanoidCarryAt(event.clientX, event.clientY,
          event.pointerId)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        try {
          canvas?.setPointerCapture?.(event.pointerId);
        } catch { /* best effort */ }
      }
      return;
    }
    if (!currentSnapshot?.jointPickIntent
        && currentSnapshot?.ik?.enabled === true) {
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
      suppressContextMenu = true;
      onRigJointPickCancelled?.();
      return;
    }
    if (event.button !== 0 || event.altKey) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    pickPointer = {id: event.pointerId, x: event.clientX, y: event.clientY};
    try { canvas?.setPointerCapture?.(event.pointerId); } catch { /* best effort */ }
  }

  function onPickPointerUp(event) {
    if (humanoidEditActive()) {
      if (event.button === 2 && humanoidCarryControlKey) {
        humanoidNavigationGesture = false;
        rebaseHumanoidCarry(event.clientX, event.clientY);
        return;
      }
      if (event.button === 0 && humanoidCarryControlKey
          && (humanoidCarryPointerId === null
            || event.pointerId === humanoidCarryPointerId)) {
        if (event.pointerId !== undefined
            && canvas?.hasPointerCapture?.(event.pointerId)) {
          try { canvas.releasePointerCapture(event.pointerId); }
          catch { /* best effort */ }
        }
      }
      return;
    }
    if (!currentSnapshot?.jointPickIntent || event.button !== 0 || !pickPointer) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const start = pickPointer;
    pickPointer = null;
    if (start.id !== undefined && canvas?.hasPointerCapture?.(start.id)) {
      try { canvas.releasePointerCapture(start.id); } catch { /* best effort */ }
    }
    if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y)
        >= PICK_CLICK_THRESHOLD) return;
    const nearest = nearestPickCandidate(
      event.clientX, event.clientY, PICK_CLICK_RADIUS);
    if (nearest) {
      onRigJointPicked?.(nearest.jointId, currentSnapshot.jointPickIntent);
      return;
    }
    onRigSurfacePickRequested?.({
      clientX: event.clientX, clientY: event.clientY,
    }, currentSnapshot.jointPickIntent);
  }

  function onPickPointerCancel(event) {
    if (humanoidEditActive()) {
      if (humanoidNavigationGesture) {
        humanoidNavigationGesture = false;
        rebaseHumanoidCarry(event?.clientX, event?.clientY);
        return;
      }
      if (humanoidCarryControlKey
          && (humanoidCarryPointerId === null
            || event?.pointerId === humanoidCarryPointerId)) {
        cancelHumanoidCarry();
      }
      return;
    }
    if (pickPointer && event?.pointerId !== undefined
        && event.pointerId !== pickPointer.id) return;
    pickPointer = null;
    setPickCursor('crosshair');
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

  function updateProxy(source = currentSource, snapshot = currentSnapshot) {
    if (snapshot?.humanoidRigEdit?.editing) {
      transformControls?.detach?.();
      poseDragActive = false;
      dragBoneId = null;
      dragJointId = null;
      dragMode = null;
      rigTransformInteractionActive = false;
      proxy.visible = false;
      ikTargetProxy.visible = false;
      return;
    }
    const boneId = selectedIdFor(snapshot);
    selectedJointId = boneId;
    const mode = manipulationMode(snapshot, source, boneId);
    if (!mode) {
      detachControls();
      proxy.visible = false;
      ikTargetProxy.visible = false;
      return;
    }
    if (poseDragActive && boneId === dragBoneId) {
      proxy.visible = mode === 'fk';
      ikTargetProxy.visible = mode === 'ik';
      return;
    }
    const poseFrame = getRigJointPoseFrame?.(boneId);
    const pivot = mode === 'ik' && isPrimaryHumanoidIk(snapshot, source)
      ? ikTargetPoint(snapshot, source)
      : poseFrame?.pivot || pivotFor(source, boneId)
        || ikTargetPoint(snapshot, source);
    if (mode === 'ik') {
      if (pivot) ikTargetProxy.position.copy(vector(pivot));
      proxy.visible = false;
      ikTargetProxy.visible = true;
      transformControls?.setMode?.('translate');
      transformControls?.setSpace?.('world');
      transformControls?.attach?.(ikTargetProxy);
      transformControls?.update?.();
      return;
    }
    if (pivot) proxy.position.copy(vector(pivot));
    const values = poseFrame?.gizmoRotation || poseFrame?.boneRotation
      || quaternionFor(source, boneId);
    if (values) proxy.quaternion.set(...values).normalize();
    else proxy.quaternion.identity();
    proxy.visible = true;
    ikTargetProxy.visible = false;
    transformControls?.setMode?.('rotate');
    transformControls?.setSpace?.('local');
    if (transformControls) {
      transformControls.attach?.(proxy);
      transformControls.update?.();
    }
  }

  function syncRotationSnap(snapshot = currentSnapshot) {
    const degrees = Number(snapshot?.rotationSnapDegrees) || 0;
    const radians = snapshot?.ik?.enabled
      ? null : degrees > 0 ? THREE.MathUtils.degToRad(degrees) : null;
    transformControls?.setRotationSnap?.(radians);
  }

  function updatePoseFromEvent(detail) {
    if (disposed) return;
    const id = Number(detail?.jointId);
    if (!Number.isInteger(id)) return;
    updatePosedOverlay(currentSource);
    if (currentSnapshot?.jointPickIntent) refreshPickCandidates();
    if (poseDragActive && dragMode === 'ik') return;
    if (poseDragActive && id === dragBoneId) {
      // TransformControls owns the proxy until the gesture ends. The model
      // still updates from every pose event, but its canonical state must not
      // overwrite the control's cached drag transform.
      return;
    }
    selectedJointId = id;
    const poseFrame = getRigJointPoseFrame?.(id);
    const mode = manipulationMode(currentSnapshot, currentSource, id);
    if (mode === 'ik') {
      const pivot = isPrimaryHumanoidIk(currentSnapshot, currentSource)
        ? ikTargetPoint(currentSnapshot, currentSource)
        : poseFrame?.pivot || pivotFor(currentSource, id)
          || ikTargetPoint(currentSnapshot, currentSource);
      if (pivot) ikTargetProxy.position.copy(vector(pivot));
      proxy.visible = false;
      ikTargetProxy.visible = true;
      transformControls?.setMode?.('translate');
      transformControls?.setSpace?.('world');
      transformControls?.attach?.(ikTargetProxy);
      transformControls?.update?.();
    } else if (mode === 'fk') {
      if (poseFrame?.gizmoRotation?.length === 4) {
        proxy.position.copy(vector(poseFrame.pivot));
        proxy.quaternion.set(...poseFrame.gizmoRotation).normalize();
      } else if (poseFrame?.boneRotation?.length === 4) {
        proxy.position.copy(vector(poseFrame.pivot));
        proxy.quaternion.set(...poseFrame.boneRotation).normalize();
      } else if (detail.quaternion?.length === 4) {
        proxy.quaternion.set(...detail.quaternion).normalize();
      }
      proxy.visible = true;
      ikTargetProxy.visible = false;
      transformControls?.setMode?.('rotate');
      transformControls?.setSpace?.('local');
      transformControls?.attach?.(proxy);
      transformControls?.update?.();
    } else {
      detachControls();
      proxy.visible = false;
      ikTargetProxy.visible = false;
    }
  }

  async function ensureTransformControls() {
    if (!manipulationMode(currentSnapshot, currentSource,
      selectedIdFor(currentSnapshot))) return null;
    if (transformControlsReady) return transformControlsReady;
    transformControlsReady = import('three/addons/controls/TransformControls.js')
      .then(module => {
        if (disposed || !module?.TransformControls) return null;
        transformControls = new module.TransformControls(camera, canvas);
        transformHelper = transformControls.getHelper();
        transformHelper.userData.isViewerRigTransformHelper = true;
        scene?.add(transformHelper);
        controlsCreateCount += 1;
        const initialMode = manipulationMode(
          currentSnapshot, currentSource, selectedIdFor(currentSnapshot));
        transformControls.setMode?.(initialMode === 'ik' ? 'translate' : 'rotate');
        transformControls.setSpace?.(initialMode === 'ik' ? 'world' : 'local');
        transformControls.addEventListener?.('mouseUp', () => {
          queueMicrotask(() => {
            rigTransformInteractionActive = false;
          });
        });
        transformControls.addEventListener?.('change', () => {
          requestRender?.();
        });
        transformControls.addEventListener?.('objectChange', () => {
          if (!poseDragActive) return;
          const boneId = dragBoneId;
          if (dragMode === 'ik') {
            solveRigIkTarget?.(ikTargetProxy.position.toArray(), {dragging: true});
            const snapshot = getRigState?.() || currentSnapshot;
            currentSnapshot = snapshot || currentSnapshot;
            currentSource = sourceFor(currentSnapshot);
            updateHumanoidPosedOverlay(currentSource);
            return;
          }
          if (boneId === null) return;
          let localRotation = proxy.quaternion.clone();
          if (dragParentRotation) {
            localRotation = dragParentRotation.clone().invert()
              .multiply(localRotation);
          }
          if (dragRestRotation) {
            localRotation
              .multiply(dragRestRotation.clone().invert())
              .normalize();
          }
          setRigJointRotation?.(boneId, localRotation, {dragging: true});
        });
        transformControls.addEventListener?.('dragging-changed', event => {
          if (event.value !== undefined && canvas?.style) {
            canvas.style.cursor = event.value ? 'grabbing' : '';
          }
          if (event.value) {
            const source = currentSource;
            const boneId = selectedIdFor(currentSnapshot);
            const mode = manipulationMode(currentSnapshot, source, boneId);
            if (!mode) return;
            poseDragActive = true;
            dragMode = mode;
            if (mode === 'ik' && isPrimaryHumanoidIk(
              currentSnapshot, source)) {
              dragBoneId = null;
              dragJointId = null;
            } else {
              dragBoneId = boneId;
              dragJointId = boneId;
            }
            if (mode === 'fk') {
              const modelPoseFrame = getRigJointPoseFrame?.(dragJointId);
              dragParentRotation = modelPoseFrame?.parentRotation?.length === 4
                ? new THREE.Quaternion(
                  ...modelPoseFrame.parentRotation).normalize()
                : new THREE.Quaternion();
              dragRestRotation = modelPoseFrame?.restRotation?.length === 4
                ? new THREE.Quaternion(
                  ...modelPoseFrame.restRotation).normalize()
                : new THREE.Quaternion();
            } else {
              dragParentRotation = null;
              dragRestRotation = null;
            }
            setArcballDragState(true);
            rigTransformInteractionActive = true;
          } else if (event.value === false) {
            setArcballDragState(false);
            const boneId = dragBoneId;
            const endedMode = dragMode;
            poseDragActive = false;
            dragBoneId = null;
            dragParentRotation = null;
            dragRestRotation = null;
            dragMode = null;
            queueMicrotask(() => {
              rigTransformInteractionActive = false;
            });
            let primaryIkDrag = false;
            if (endedMode === 'ik') {
              const snapshot = getRigState?.() || currentSnapshot;
              const source = sourceFor(snapshot);
              primaryIkDrag = isPrimaryHumanoidIk(snapshot, source);
              if (primaryIkDrag) {
                solveRigIkTarget?.(ikTargetProxy.position.toArray(),
                  {dragging: false});
                currentSnapshot = getRigState?.() || snapshot;
                currentSource = sourceFor(currentSnapshot);
                updateHumanoidPosedOverlay(currentSource);
                updateProxy(currentSource, currentSnapshot);
              }
            }
            if (boneId !== null && !primaryIkDrag) {
              if (dragJointId !== null) finishRigJointPose?.(dragJointId);
              const snapshot = getRigState?.();
              currentSnapshot = snapshot || currentSnapshot;
              currentSource = sourceFor(currentSnapshot);
              updatePosedOverlay(currentSource);
              updateProxy(currentSource, currentSnapshot);
            }
          }
        });
        syncRotationSnap(currentSnapshot);
        updateProxy(currentSource, currentSnapshot);
        return transformControls;
      })
      .catch(() => {
        if (!disposed) onTransformControlsUnavailable?.();
        return null;
      });
    return transformControlsReady;
  }

  function refresh(snapshot = getRigState?.()) {
    if (disposed) return;
    const wasJointPicking = rigJointPickingActive;
    const wasHumanoidEditing = currentSnapshot?.humanoidRigEdit?.editing === true;
    currentSnapshot = snapshot || {};
    currentSource = sourceFor(currentSnapshot);
    rigJointPickingActive = !!currentSnapshot.jointPickIntent;
    const humanoidEditing = currentSnapshot?.humanoidRigEdit?.editing === true;
    if (humanoidEditing
        && !currentSnapshot?.humanoidRigEdit?.carryingControlKey
        && humanoidCarryControlKey) {
      humanoidCarryControlKey = null;
      humanoidCarryPlane = null;
      humanoidCarryOffset.set(0, 0, 0);
      humanoidCarryPointerId = null;
      humanoidNavigationGesture = false;
      humanoidCandidateJointId = null;
      humanoidCandidateScreen = null;
      setArcballHumanoidCarryState(false);
      setPickCursor('');
    }
    if (wasHumanoidEditing && !humanoidEditing) {
      if (humanoidCarryControlKey) cancelHumanoidCarry();
      hoveredControlKey = null;
      humanoidCandidateJointId = null;
      humanoidCandidateScreen = null;
      setPickCursor('');
    }
    if (humanoidEditing && !humanoidCarryControlKey) {
      transformControls?.detach?.();
      poseDragActive = false;
      dragBoneId = null;
      dragJointId = null;
      dragMode = null;
      rigTransformInteractionActive = false;
    }
    selectedJointId = selectedIdFor(currentSnapshot);
    const nextTopologyKey = overlayPresentationKey(currentSource);
    if (nextTopologyKey !== currentTopologyKey) {
      currentTopologyKey = nextTopologyKey;
      rebuildOverlay(currentSource);
    }
    const editKey = humanoidEditing ? 'editing' : '';
    const nextHumanoidKey = `${humanoidOverlayKey(currentSource)}:${editKey}`;
    if (nextHumanoidKey !== currentHumanoidKey) {
      currentHumanoidKey = nextHumanoidKey;
      rebuildHumanoidOverlay(currentSource);
    }
    updateModelFrame();
    group.visible = !!currentSource;
    updateHumanoidVisibility();
    staticGroup.visible = (humanoidEditing || !!currentSnapshot.jointPickIntent)
      && !!currentSource;
    if (!currentSnapshot.jointPickIntent) {
      pickCandidateCache = [];
      pickPointer = null;
      lastPickClientPoint = null;
      clearPickHover();
      if (wasJointPicking) setPickCursor('');
    }
    updatePosedOverlay(currentSource);
    if (currentSnapshot.jointPickIntent && !humanoidEditing) {
      refreshPickCandidates();
      if (hoveredJointId === null) setPickCursor('crosshair');
    }
    updateCenterColors();
    updateProxy(currentSource, currentSnapshot);
    syncRotationSnap(currentSnapshot);
    if (!humanoidEditing && manipulationMode(currentSnapshot, currentSource,
      selectedIdFor(currentSnapshot))) {
      void ensureTransformControls();
    }
    requestRender?.();
  }

  const onRigChanged = event => refresh(event.detail || getRigState?.());
  const onPoseChanged = event => updatePoseFromEvent(event.detail);
  const onArcballChanged = () => {
    updateHumanoidSpriteSizes();
    updateModelJointMarkers(currentSource);
    requestRender?.();
  };
  const onModelTransformChanged = () => {
    updateModelFrame();
    updateModelJointMarkers(currentSource);
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
        nodeCount: nodeBoneIds.length,
        jointCount: jointPoints.geometry.getAttribute('position')?.count || 0,
        modelJointMarkerCount: modelJointMarkerMesh.count,
        modelJointMarkerType: modelJointMarkerMesh.type,
        modelJointMarkerInstanced: modelJointMarkerMesh.isInstancedMesh === true,
        modelJointMarkerSizePx: MODEL_JOINT_MARKER_SIZE_PX,
        modelJointCandidateSizePx: MODEL_JOINT_CANDIDATE_SIZE_PX,
        edgeCount: lineSegments.geometry.getAttribute('position')?.count / 2 || 0,
        humanoidOverlayVisible: humanoidGroup.visible,
        humanoidSegmentCount: humanoidLinePairs.length,
        humanoidLandmarkCount: humanoidLandmarks.length,
        humanoidHaloCount: humanoidHalos.geometry.getAttribute('position')?.count || 0,
        humanoidPointSpriteCount: humanoidPointSprites.length,
        humanoidHaloSpriteCount: humanoidHaloSprites.length,
        humanoidMarkerTextureReady: !!humanoidMarkerTexture,
        humanoidEditActive: humanoidEditActive(),
        hoveredControlKey,
        carryingControlKey: humanoidCarryControlKey,
        candidateJointId: currentSnapshot?.humanoidRigEdit?.candidateJointId
          ?? null,
        selectedJointId,
        proxyVisible: proxy.visible,
        ikTargetVisible: ikTargetProxy.visible,
        controlsCreated: !!transformControls,
        controlsCreateCount,
        controlsAttached: transformControls?.object === proxy
          || transformControls?.object === ikTargetProxy,
        controlsAttachedTo: transformControls?.object === ikTargetProxy
          ? 'ik-target' : transformControls?.object === proxy ? 'fk-proxy' : null,
        helperInScene: !!transformHelper && transformHelper.parent === scene,
        arcballEnabled: arcballControls?.enabled,
        arcballWasEnabled,
        poseDragActive,
        hoveredJointId,
        pickCandidateCount,
        pickLabelVisible: !!pickLabel && !pickLabel.hidden,
      };
    },
    dispose() {
      disposed = true;
      rigJointPickingActive = false;
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
      centerPoints.geometry.dispose();
      jointPoints.geometry.dispose();
      modelJointMarkerMesh.geometry.dispose();
      humanoidLines.geometry.dispose();
      humanoidHalos.geometry.dispose();
      humanoidPoints.geometry.dispose();
      humanoidCandidate.geometry.dispose();
      hoverPoint.geometry.dispose();
      proxyRing.geometry.dispose();
      centerMaterial.dispose();
      jointMaterial.dispose();
      modelJointMarkerMaterial.dispose();
      hoverMaterial.dispose();
      selectedMaterial.dispose();
      lineMaterial.dispose();
      humanoidLineMaterial.dispose();
      humanoidHaloMaterial.dispose();
      humanoidPointMaterial.dispose();
      humanoidCandidateMaterial.dispose();
      humanoidCandidateSprite.material.dispose();
      clearHumanoidSprites(humanoidPointSprites);
      clearHumanoidSprites(humanoidHaloSprites);
      humanoidMarkerTexture?.dispose?.();
      modelJointMarkerTexture?.dispose?.();
      ikTargetMarker.geometry.dispose();
      group.remove(staticGroup, humanoidGroup, proxy, ikTargetProxy);
      scene?.remove(group);
    },
  };
}
