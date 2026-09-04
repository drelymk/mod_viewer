// Temporary visualization for the experimental inferred skinning rig.
// Nothing in this group is a model mesh or a THREE.Bone; it is disposable
// diagnostic geometry owned entirely by the Rig panel.

import * as THREE from 'three/webgpu';

function vector(value) {
  if (value?.isVector3) return value.clone();
  return new THREE.Vector3(
    Number(value?.[0]) || 0,
    Number(value?.[1]) || 0,
    Number(value?.[2]) || 0);
}

function sourceFor(snapshot) {
  if (snapshot?.model) return snapshot.model;
  return (snapshot?.sources || []).find(source =>
    source.sourceKey === snapshot.activeSourceKey) || null;
}

function selectedBoneFor(snapshot) {
  if (snapshot?.model && snapshot.selectedJointId !== null
      && snapshot.selectedJointId !== undefined) {
    return Number(snapshot.selectedJointId);
  }
  const value = snapshot?.selectedBoneId;
  if (value === null || value === undefined || value === '') return null;
  const id = Number(value);
  return Number.isInteger(id) ? id : null;
}

function componentFor(source, boneId) {
  if (boneId === null || boneId === undefined) return null;
  return source?.components?.find(component =>
    component.nodeIds.includes(boneId)) || null;
}

function pivotFor(source, boneId) {
  const joint = source?.joints?.find(item => item.jointId === Number(boneId));
  if (joint?.restPivot) return joint.restPivot;
  const pivots = source?.jointPivotByBoneId;
  return pivots?.[boneId] || pivots?.get?.(boneId) || null;
}

function quaternionFor(source, boneId) {
  const jointRotation = source?.poseRotationByJointId?.[boneId]
    || source?.poseRotationByJointId?.get?.(boneId);
  if (jointRotation) return jointRotation;
  const rotations = source?.poseRotationByBoneId;
  return rotations?.[boneId] || rotations?.get?.(boneId) || null;
}

function topologyKey(source) {
  if (!source) return '';
  if (source.structureRevision !== null
      && source.structureRevision !== undefined) {
    return `${source.sourceKey}:${source.structureRevision}`;
  }
  return JSON.stringify([
    source.sourceKey,
    source.boneIds || [],
    (source.components || []).map(component => [
      component.componentId, component.rootId, component.nodeIds || [],
    ]),
    source.forestEdges || [],
  ]);
}

function modelRigHasActivePhysics(snapshot, source = null) {
  return !!snapshot?.physicsActive
    || (snapshot?.model && (snapshot.sources || []).some(source =>
      source.physicsActive))
    || !!source?.physicsActive;
}

function canPose(snapshot, source, boneId = selectedBoneFor(snapshot)) {
  if (!snapshot?.visible || snapshot.picking || !source || boneId === null
      || modelRigHasActivePhysics(snapshot, source)) {
    return false;
  }
  const component = componentFor(source, boneId);
  return !!component && component.rootId !== boneId;
}

let rigTransformInteractionActive = false;

export function isRigTransformInteractionActive() {
  return rigTransformInteractionActive;
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

function centerColor(component, boneId, selectedBoneId) {
  if (boneId === selectedBoneId) return [1, .78, .08];
  if (component?.rootId === boneId) return [1, .28, .4];
  return [.49, .83, .99];
}

export function createRigOverlayController({
  scene, camera, canvas, getMeshes, getRigState, getRigDebugState,
  getRigBonePoseFrame, getRigJointPoseFrame, arcballControls,
  setRigBoneRotation, setRigJointRotation, finishRigPose,
  finishRigJointPose, onTransformControlsUnavailable,
  requestRender,
} = {}) {
  // getRigDebugState is intentionally not used by the render path. It remains
  // an explicit diagnostic hook for callers that need the raw graph.
  void getRigDebugState;

  const sourceForController = snapshot => {
    if (snapshot?.model && (typeof getRigJointPoseFrame !== 'function'
        || typeof setRigJointRotation !== 'function')) {
      return (snapshot.sources || []).find(source =>
        source.sourceKey === snapshot.activeSourceKey) || null;
    }
    return sourceFor(snapshot);
  };
  const selectedIdFor = (snapshot, source) => {
    if (source?.joints && snapshot?.selectedJointId !== null
        && snapshot?.selectedJointId !== undefined) {
      return Number(snapshot.selectedJointId);
    }
    const value = snapshot?.selectedBoneId;
    if (value === null || value === undefined || value === '') return null;
    const id = Number(value);
    return Number.isInteger(id) ? id : null;
  };

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
  lineSegments.renderOrder = 10;
  centerPoints.renderOrder = 11;
  jointPoints.renderOrder = 12;
  lineSegments.frustumCulled = false;
  centerPoints.frustumCulled = false;
  jointPoints.frustumCulled = false;
  lineSegments.raycast = () => {};
  centerPoints.raycast = () => {};
  jointPoints.raycast = () => {};
  staticGroup.add(lineSegments, centerPoints, jointPoints);
  group.add(staticGroup);

  const proxy = new THREE.Object3D();
  proxy.name = 'viewer-inferred-rig-pose-proxy';
  proxy.userData.isViewerRigOverlay = true;
  const proxyRing = new THREE.Mesh(
    new THREE.TorusGeometry(0.07, 0.004, 8, 32), selectedMaterial);
  proxyRing.name = 'viewer-inferred-rig-pose-handle';
  proxyRing.raycast = () => {};
  proxy.add(proxyRing);
  group.add(proxy);

  let transformControls = null;
  let transformHelper = null;
  let transformControlsReady = null;
  let controlsCreateCount = 0;
  let arcballWasEnabled = null;
  let activeSourceKey = null;
  let selectedBoneId = null;
  let currentSnapshot = null;
  let currentSource = null;
  let currentTopologyKey = '';
  let nodeBoneIds = [];
  let nodeByBoneId = new Map();
  let nodeIndexByBoneId = new Map();
  let lineBonePairs = [];
  let jointChildBoneIds = [];
  let rebuildCount = 0;
  let modelFrameUpdateCount = 0;
  let posedOverlayUpdateCount = 0;
  let poseDragActive = false;
  let dragSourceKey = null;
  let dragBoneId = null;
  let dragParentRotation = null;
  let dragRestRotation = null;
  let dragJointId = null;
  let disposed = false;

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
    dragSourceKey = null;
    dragBoneId = null;
    dragParentRotation = null;
    dragRestRotation = null;
    dragJointId = null;
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
        componentFor(source, boneId), boneId, selectedBoneId);
      colors.setXYZ(index, ...color);
    });
    colors.needsUpdate = true;
  }

  function rebuildOverlay(source) {
    const modelNodes = (source?.joints || []).map(joint => [
      Number(joint.jointId), joint.restCenter,
    ]);
    nodeByBoneId = new Map((modelNodes.length ? modelNodes
      : (source?.nodes || []).map(node => [
        Number(node.boneId), node.weightedCenter,
      ])).filter(([boneId, center]) => Number.isInteger(boneId) && center));
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
        componentFor(source, boneId), boneId, selectedBoneId));
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
      if (joint) {
        jointChildBoneIds.push(childId);
        jointPositions.push(...joint);
      }
    });
    if (source?.joints?.length) {
      jointChildBoneIds = source.joints.map(joint => Number(joint.jointId));
      jointPositions.length = 0;
      source.joints.forEach(joint => {
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
      const frame = source?.joints
        ? getRigJointPoseFrame?.(boneId)
        : getRigBonePoseFrame?.(source.sourceKey, boneId);
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
      const frame = source?.joints
        ? getRigJointPoseFrame?.(childId)
        : getRigBonePoseFrame?.(source.sourceKey, childId);
      const value = frame?.pivot || pivotFor(source, childId);
      if (!value) return;
      const joint = vector(value);
      jointAttribute.setXYZ(index, joint.x, joint.y, joint.z);
    });
    if (jointAttribute) jointAttribute.needsUpdate = true;
    posedOverlayUpdateCount += 1;
  }

  function updateProxy(source = currentSource, snapshot = currentSnapshot) {
    const boneId = selectedIdFor(snapshot, source);
    selectedBoneId = boneId;
    if (!canPose(snapshot, source, boneId)) {
      detachControls();
      proxy.visible = false;
      return;
    }
    if (poseDragActive && boneId === dragBoneId
        && (source?.joints || source?.sourceKey === dragSourceKey)) {
      proxy.visible = group.visible;
      return;
    }
    const poseFrame = source?.joints
      ? getRigJointPoseFrame?.(boneId)
      : getRigBonePoseFrame?.(source.sourceKey, boneId);
    const pivot = poseFrame?.pivot || pivotFor(source, boneId);
    if (pivot) proxy.position.copy(vector(pivot));
    const values = poseFrame?.gizmoRotation || poseFrame?.boneRotation
      || quaternionFor(source, boneId);
    if (values) proxy.quaternion.set(...values).normalize();
    else proxy.quaternion.identity();
    proxy.visible = group.visible;
    if (transformControls) {
      transformControls.attach?.(proxy);
      transformControls.update?.();
    }
  }

  function syncRotationSnap(snapshot = currentSnapshot) {
    const degrees = Number(snapshot?.rotationSnapDegrees) || 0;
    const radians = degrees > 0 ? THREE.MathUtils.degToRad(degrees) : null;
    transformControls?.setRotationSnap?.(radians);
  }

  function updatePoseFromEvent(detail) {
    const modelMode = !!currentSource?.joints;
    if (disposed || (!modelMode && detail?.sourceKey !== activeSourceKey)) return;
    const id = Number(modelMode ? detail.jointId : detail.boneId);
    if (!Number.isInteger(id)) return;
    updatePosedOverlay(currentSource);
    if (poseDragActive && id === dragBoneId
        && (modelMode || detail?.sourceKey === dragSourceKey)) {
      // TransformControls owns the proxy until the gesture ends. The model
      // still updates from every pose event, but its canonical state must not
      // overwrite the control's cached drag transform.
      return;
    }
    selectedBoneId = id;
    const poseFrame = modelMode
      ? getRigJointPoseFrame?.(id)
      : getRigBonePoseFrame?.(detail.sourceKey, id);
    if (poseFrame?.gizmoRotation?.length === 4) {
      proxy.position.copy(vector(poseFrame.pivot));
      proxy.quaternion.set(...poseFrame.gizmoRotation).normalize();
    } else if (poseFrame?.boneRotation?.length === 4) {
      proxy.position.copy(vector(poseFrame.pivot));
      proxy.quaternion.set(...poseFrame.boneRotation).normalize();
    } else if (detail.quaternion?.length === 4) {
      proxy.quaternion.set(...detail.quaternion).normalize();
    }
    proxy.visible = group.visible && canPose(currentSnapshot, currentSource, id);
    if (!proxy.visible) detachControls();
    else {
      transformControls?.attach?.(proxy);
      transformControls?.update?.();
    }
  }

  async function ensureTransformControls() {
    if (!canPose(currentSnapshot, currentSource,
      selectedIdFor(currentSnapshot, currentSource))) return null;
    if (transformControlsReady) return transformControlsReady;
    transformControlsReady = import('three/addons/controls/TransformControls.js')
      .then(module => {
        if (disposed || !module?.TransformControls) return null;
        transformControls = new module.TransformControls(camera, canvas);
        transformHelper = transformControls.getHelper();
        transformHelper.userData.isViewerRigTransformHelper = true;
        scene?.add(transformHelper);
        controlsCreateCount += 1;
        transformControls.setMode?.('rotate');
        transformControls.setSpace?.('local');
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
          const sourceKey = dragSourceKey;
          const boneId = dragBoneId;
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
          if (currentSource?.joints) {
            setRigJointRotation?.(boneId, localRotation, {dragging: true});
          } else if (sourceKey) {
            setRigBoneRotation?.(
              sourceKey, boneId, localRotation, {dragging: true});
          }
        });
        transformControls.addEventListener?.('dragging-changed', event => {
          if (event.value !== undefined && canvas?.style) {
            canvas.style.cursor = event.value ? 'grabbing' : '';
          }
          if (event.value) {
            const source = currentSource;
            const boneId = selectedIdFor(currentSnapshot, source);
            if (!canPose(currentSnapshot, source, boneId)) return;
            poseDragActive = true;
            dragSourceKey = source?.joints ? null : source.sourceKey;
            dragBoneId = boneId;
            dragJointId = source?.joints ? boneId : null;
            const poseFrame = getRigBonePoseFrame?.(
              dragSourceKey, dragBoneId);
            const modelPoseFrame = source?.joints
              ? getRigJointPoseFrame?.(dragJointId) : poseFrame;
            dragParentRotation = modelPoseFrame?.parentRotation?.length === 4
              ? new THREE.Quaternion(
                ...modelPoseFrame.parentRotation).normalize()
              : new THREE.Quaternion();
            dragRestRotation = modelPoseFrame?.restRotation?.length === 4
              ? new THREE.Quaternion(
                ...modelPoseFrame.restRotation).normalize()
              : new THREE.Quaternion();
            setArcballDragState(true);
            rigTransformInteractionActive = true;
          } else if (event.value === false) {
            setArcballDragState(false);
            const sourceKey = dragSourceKey;
            const boneId = dragBoneId;
            poseDragActive = false;
            dragSourceKey = null;
            dragBoneId = null;
            dragParentRotation = null;
            dragRestRotation = null;
            queueMicrotask(() => {
              rigTransformInteractionActive = false;
            });
            if (boneId !== null) {
              if (dragJointId !== null) finishRigJointPose?.(dragJointId);
              else if (sourceKey) finishRigPose?.(sourceKey, boneId);
              const snapshot = getRigState?.();
              currentSnapshot = snapshot || currentSnapshot;
              currentSource = sourceForController(currentSnapshot);
              activeSourceKey = currentSource?.sourceKey || null;
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
    currentSnapshot = snapshot || {};
    currentSource = sourceForController(currentSnapshot);
    activeSourceKey = currentSource?.sourceKey || null;
    selectedBoneId = selectedIdFor(currentSnapshot, currentSource);
    const nextTopologyKey = topologyKey(currentSource);
    if (nextTopologyKey !== currentTopologyKey) {
      currentTopologyKey = nextTopologyKey;
      rebuildOverlay(currentSource);
    }
    updateModelFrame();
    group.visible = !!currentSnapshot.visible && !!currentSource;
    staticGroup.visible = group.visible;
    updatePosedOverlay(currentSource);
    updateCenterColors();
    updateProxy(currentSource, currentSnapshot);
    syncRotationSnap(currentSnapshot);
    if (canPose(currentSnapshot, currentSource,
      selectedIdFor(currentSnapshot, currentSource))) {
      void ensureTransformControls();
    }
    requestRender?.();
  }

  const onRigChanged = event => refresh(event.detail || getRigState?.());
  const onPoseChanged = event => updatePoseFromEvent(event.detail);
  const onModelTransformChanged = () => {
    updateModelFrame();
    requestRender?.();
  };
  window.addEventListener('mod-viewer-model-rig-changed', onRigChanged);
  window.addEventListener('mod-viewer-model-rig-pose-changed', onPoseChanged);
  window.addEventListener('mod-viewer-model-transform-changed', onModelTransformChanged);

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
        nodeCount: nodeBoneIds.length,
        jointCount: jointPoints.geometry.getAttribute('position')?.count || 0,
        edgeCount: lineSegments.geometry.getAttribute('position')?.count / 2 || 0,
        selectedBoneId,
        proxyVisible: proxy.visible,
        controlsCreated: !!transformControls,
        controlsCreateCount,
        controlsAttached: transformControls?.object === proxy,
        helperInScene: !!transformHelper && transformHelper.parent === scene,
        arcballEnabled: arcballControls?.enabled,
        arcballWasEnabled,
        poseDragActive,
      };
    },
    dispose() {
      disposed = true;
      window.removeEventListener('mod-viewer-model-rig-changed', onRigChanged);
      window.removeEventListener('mod-viewer-model-rig-pose-changed', onPoseChanged);
      window.removeEventListener('mod-viewer-model-transform-changed', onModelTransformChanged);
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
      proxyRing.geometry.dispose();
      centerMaterial.dispose();
      jointMaterial.dispose();
      selectedMaterial.dispose();
      lineMaterial.dispose();
      group.remove(staticGroup, proxy);
      scene?.remove(group);
    },
  };
}
