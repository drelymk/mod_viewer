// Visualization for the inferred skinning rig.
// Nothing in this group is a model mesh or a THREE.Bone; it is overlay
// geometry owned entirely by the Rig panel.

import * as THREE from 'three/webgpu';

const PICK_ACQUIRE_RADIUS = 9;
const PICK_RELEASE_RADIUS = 13;
const PICK_SWITCH_MARGIN = 3;
const PICK_CLICK_RADIUS = 15;
const PICK_CLICK_THRESHOLD = 4;

function vector(value) {
  if (value?.isVector3) return value.clone();
  return new THREE.Vector3(
    Number(value?.[0]) || 0,
    Number(value?.[1]) || 0,
    Number(value?.[2]) || 0);
}

function sourceFor(snapshot) {
  return snapshot?.model || null;
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
  return {...rect, width, height};
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

function canFkPose(snapshot, source, boneId = selectedBoneFor(snapshot)) {
  if (snapshot?.jointPickIntent || !source
      || boneId === null) {
    return false;
  }
  const component = componentFor(source, boneId);
  return !!component && component.rootId !== boneId;
}

function canIkPose(snapshot, source, boneId = selectedBoneFor(snapshot)) {
  if (snapshot?.jointPickIntent || !source
      || boneId === null) return false;
  const ik = snapshot?.ik;
  return !!ik?.enabled && !!ik.available
    && Number(ik.endJointId) === Number(boneId);
}

function manipulationMode(snapshot, source, boneId = selectedBoneFor(snapshot)) {
  if (canIkPose(snapshot, source, boneId)) return 'ik';
  if (snapshot?.ik?.enabled) return null;
  return canFkPose(snapshot, source, boneId) ? 'fk' : null;
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
  if (role === 'left_arm') return [1, .42, .24];
  if (role === 'right_arm') return [.32, .72, 1];
  if (role === 'left_leg') return [1, .78, .18];
  return [.54, 1, .42];
}

export function createRigOverlayController({
  scene, camera, canvas, getMeshes, getRigState,
  getRigJointPoseFrame, arcballControls, setRigJointRotation,
  solveRigIkTarget, finishRigJointPose, onTransformControlsUnavailable,
  onRigJointPicked, onRigSurfacePickRequested, onRigJointPickCancelled,
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
  const centerMaterial = new THREE.PointsMaterial({
    size: 0.025, sizeAttenuation: false, vertexColors: true,
    depthTest: false, depthWrite: false,
  });
  const jointMaterial = new THREE.PointsMaterial({
    color: 0x34d399, size: 0.018, sizeAttenuation: false,
    depthTest: false, depthWrite: false,
  });
  const selectedMaterial = new THREE.MeshBasicMaterial({
    color: 0xfacc15, depthTest: false, depthWrite: false,
  });
  const lineMaterial = new THREE.LineBasicMaterial({
    color: 0x60a5fa, vertexColors: true,
    depthTest: false, depthWrite: false,
  });
  const lineSegments = new THREE.LineSegments(
    new THREE.BufferGeometry(), lineMaterial);
  const centerPoints = new THREE.Points(
    new THREE.BufferGeometry(), centerMaterial);
  const jointPoints = new THREE.Points(
    new THREE.BufferGeometry(), jointMaterial);
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
  staticGroup.add(lineSegments, centerPoints, jointPoints, hoverPoint);
  group.add(staticGroup);

  // Semantic detection is intentionally a separate debug group. It only
  // draws the selected S/E/H and H/K/F paths; the full inferred Rig remains
  // available in the group above without implying anatomical categories.
  const humanoidGroup = new THREE.Group();
  humanoidGroup.name = 'viewer-humanoid-backbone-overlay';
  humanoidGroup.userData.isViewerHumanoidOverlay = true;
  humanoidGroup.visible = false;
  const humanoidLineMaterial = new THREE.LineBasicMaterial({
    vertexColors: true, depthTest: false, depthWrite: false,
  });
  const humanoidPointMaterial = new THREE.PointsMaterial({
    size: 0.045, sizeAttenuation: false, vertexColors: true,
    depthTest: false, depthWrite: false,
  });
  const humanoidLines = new THREE.LineSegments(
    new THREE.BufferGeometry(), humanoidLineMaterial);
  const humanoidPoints = new THREE.Points(
    new THREE.BufferGeometry(), humanoidPointMaterial);
  humanoidLines.renderOrder = 13;
  humanoidPoints.renderOrder = 14;
  humanoidLines.frustumCulled = false;
  humanoidPoints.frustumCulled = false;
  humanoidLines.raycast = () => {};
  humanoidPoints.raycast = () => {};
  humanoidGroup.add(humanoidLines, humanoidPoints);
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

  function humanoidDetectionFor(source) {
    return source?.humanoidDetection?.selectedRoles || {};
  }

  function rebuildHumanoidOverlay(source = currentSource) {
    humanoidLinePairs = [];
    humanoidLandmarks = [];
    const linePositions = [];
    const lineColors = [];
    const pointPositions = [];
    const pointColors = [];
    const landmarkKeys = new Set();
    const selectedRoles = humanoidDetectionFor(source);
    Object.entries(selectedRoles).forEach(([role, mapping]) => {
      const path = (mapping?.available ? mapping.pathJointIds : [])
        .map(Number).filter(Number.isInteger);
      const color = humanoidRoleColor(role);
      for (let index = 1; index < path.length; index += 1) {
        const first = pivotFor(source, path[index - 1]);
        const second = pivotFor(source, path[index]);
        if (!first || !second) continue;
        linePositions.push(...first, ...second);
        lineColors.push(...color, ...color);
        humanoidLinePairs.push([path[index - 1], path[index]]);
      }
      [mapping?.anchorJointId, mapping?.bendJointId, mapping?.endJointId]
        .map(Number).filter(Number.isInteger).forEach(jointId => {
          const key = `${role}:${jointId}`;
          if (landmarkKeys.has(key)) return;
          const point = pivotFor(source, jointId);
          if (!point) return;
          landmarkKeys.add(key);
          pointPositions.push(...point);
          pointColors.push(...color);
          humanoidLandmarks.push({jointId, role});
        });
    });
    setGeometry(humanoidLines, linePositions, lineColors);
    setGeometry(humanoidPoints, pointPositions, pointColors);
    humanoidGroup.visible = humanoidLinePairs.length > 0;
  }

  function updateHumanoidPosedOverlay(source = currentSource) {
    if (!source || !humanoidGroup.visible) return;
    const lineAttribute = humanoidLines.geometry.getAttribute('position');
    humanoidLinePairs.forEach(([firstId, secondId], index) => {
      const firstFrame = getRigJointPoseFrame?.(firstId);
      const secondFrame = getRigJointPoseFrame?.(secondId);
      const first = firstFrame?.pivot || pivotFor(source, firstId);
      const second = secondFrame?.pivot || pivotFor(source, secondId);
      if (!lineAttribute || !first || !second) return;
      const firstPoint = vector(first);
      const secondPoint = vector(second);
      lineAttribute.setXYZ(index * 2, firstPoint.x, firstPoint.y, firstPoint.z);
      lineAttribute.setXYZ(index * 2 + 1,
        secondPoint.x, secondPoint.y, secondPoint.z);
    });
    if (lineAttribute) lineAttribute.needsUpdate = true;
    const pointAttribute = humanoidPoints.geometry.getAttribute('position');
    humanoidLandmarks.forEach(({jointId}, index) => {
      const frame = getRigJointPoseFrame?.(jointId);
      const point = frame?.pivot || pivotFor(source, jointId);
      if (!pointAttribute || !point) return;
      const value = vector(point);
      pointAttribute.setXYZ(index, value.x, value.y, value.z);
    });
    if (pointAttribute) pointAttribute.needsUpdate = true;
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
      if (first && second) {
        linePositions.push(...first, ...second);
        const color = edge.relationshipType === 'attachment'
          ? [1, .48, .15] : [.38, .65, 1];
        lineColors.push(...color, ...color);
      }
      if (first && second) lineBonePairs.push([parentId, childId]);
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
    updatePosedOverlay(source);
    rebuildCount += 1;
  }

  function updatePosedOverlay(source = currentSource) {
    if (!source) return;
    const centers = new Map();
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
    });
    if (centerAttribute) centerAttribute.needsUpdate = true;

    const lineAttribute = lineSegments.geometry.getAttribute('position');
    lineBonePairs.forEach(([parentId, childId], index) => {
      const parent = centers.get(parentId);
      const child = centers.get(childId);
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
    if (!currentSnapshot?.jointPickIntent) return;
    if (event.altKey) {
      clearPickHover();
      setPickCursor('grab');
      return;
    }
    updatePickHover(event);
  }

  function onPickPointerDown(event) {
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
    if (pickPointer && event?.pointerId !== undefined
        && event.pointerId !== pickPointer.id) return;
    pickPointer = null;
    setPickCursor('crosshair');
  }

  function onPickContextMenu(event) {
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
    const pivot = poseFrame?.pivot || pivotFor(source, boneId);
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
      if (poseFrame?.pivot) ikTargetProxy.position.copy(vector(poseFrame.pivot));
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
          if (boneId === null) return;
          if (dragMode === 'ik') {
            solveRigIkTarget?.(ikTargetProxy.position.toArray(), {dragging: true});
            return;
          }
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
            dragBoneId = boneId;
            dragJointId = boneId;
            dragMode = mode;
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
            poseDragActive = false;
            dragBoneId = null;
            dragParentRotation = null;
            dragRestRotation = null;
            dragMode = null;
            queueMicrotask(() => {
              rigTransformInteractionActive = false;
            });
            if (boneId !== null) {
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
    currentSnapshot = snapshot || {};
    currentSource = sourceFor(currentSnapshot);
    rigJointPickingActive = !!currentSnapshot.jointPickIntent;
    selectedJointId = selectedIdFor(currentSnapshot);
    const nextTopologyKey = overlayPresentationKey(currentSource);
    if (nextTopologyKey !== currentTopologyKey) {
      currentTopologyKey = nextTopologyKey;
      rebuildOverlay(currentSource);
    }
    const nextHumanoidKey = JSON.stringify(
      humanoidDetectionFor(currentSource));
    if (nextHumanoidKey !== currentHumanoidKey) {
      currentHumanoidKey = nextHumanoidKey;
      rebuildHumanoidOverlay(currentSource);
    }
    updateModelFrame();
    group.visible = !!currentSource;
    humanoidGroup.visible = humanoidGroup.visible && !!currentSource;
    staticGroup.visible = !!currentSnapshot.jointPickIntent && !!currentSource;
    if (!currentSnapshot.jointPickIntent) {
      pickCandidateCache = [];
      pickPointer = null;
      lastPickClientPoint = null;
      clearPickHover();
      if (wasJointPicking) setPickCursor('');
    }
    updatePosedOverlay(currentSource);
    if (currentSnapshot.jointPickIntent) {
      refreshPickCandidates();
      if (hoveredJointId === null) setPickCursor('crosshair');
    }
    updateCenterColors();
    updateProxy(currentSource, currentSnapshot);
    syncRotationSnap(currentSnapshot);
    if (manipulationMode(currentSnapshot, currentSource,
      selectedIdFor(currentSnapshot))) {
      void ensureTransformControls();
    }
    requestRender?.();
  }

  const onRigChanged = event => refresh(event.detail || getRigState?.());
  const onPoseChanged = event => updatePoseFromEvent(event.detail);
  const onModelTransformChanged = () => {
    updateModelFrame();
    if (currentSnapshot?.jointPickIntent && lastPickClientPoint) {
      updatePickHoverAt(lastPickClientPoint.x, lastPickClientPoint.y);
    }
    requestRender?.();
  };
  window.addEventListener('mod-viewer-model-rig-changed', onRigChanged);
  window.addEventListener('mod-viewer-model-rig-pose-changed', onPoseChanged);
  window.addEventListener('mod-viewer-model-transform-changed', onModelTransformChanged);
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
        edgeCount: lineSegments.geometry.getAttribute('position')?.count / 2 || 0,
        humanoidOverlayVisible: humanoidGroup.visible,
        humanoidSegmentCount: humanoidLinePairs.length,
        humanoidLandmarkCount: humanoidLandmarks.length,
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
      humanoidLines.geometry.dispose();
      humanoidPoints.geometry.dispose();
      hoverPoint.geometry.dispose();
      proxyRing.geometry.dispose();
      centerMaterial.dispose();
      jointMaterial.dispose();
      hoverMaterial.dispose();
      selectedMaterial.dispose();
      lineMaterial.dispose();
      humanoidLineMaterial.dispose();
      humanoidPointMaterial.dispose();
      ikTargetMarker.geometry.dispose();
      group.remove(staticGroup, humanoidGroup, proxy, ikTargetProxy);
      scene?.remove(group);
    },
  };
}
