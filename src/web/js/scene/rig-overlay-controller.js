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
  return (snapshot?.sources || []).find(source =>
    source.sourceKey === snapshot.activeSourceKey) || null;
}

function selectedBoneFor(snapshot) {
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
  const pivots = source?.jointPivotByBoneId;
  return pivots?.[boneId] || pivots?.get?.(boneId) || null;
}

function quaternionFor(source, boneId) {
  const rotations = source?.poseRotationByBoneId;
  return rotations?.[boneId] || rotations?.get?.(boneId) || null;
}

function topologyKey(source) {
  if (!source) return '';
  return JSON.stringify([
    source.sourceKey,
    source.boneIds || [],
    (source.components || []).map(component => [
      component.componentId, component.rootId, component.nodeIds || [],
    ]),
    source.forestEdges || [],
  ]);
}

function canPose(snapshot, source, boneId = selectedBoneFor(snapshot)) {
  if (!snapshot?.visible || snapshot.picking || !source || boneId === null) {
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
  getRigBonePoseFrame, arcballControls,
  setRigBoneRotation, finishRigPose, onTransformControlsUnavailable,
  requestRender,
} = {}) {
  // getRigDebugState is intentionally not used by the render path. It remains
  // an explicit diagnostic hook for callers that need the raw graph.
  void getRigDebugState;

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
    color: 0x60a5fa, depthTest: false, depthWrite: false,
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
  let rebuildCount = 0;
  let modelFrameUpdateCount = 0;
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
    rigTransformInteractionActive = false;
  }

  function updateModelFrame() {
    const meshes = getMeshes?.() || [];
    const mesh = [...meshes].find(item => item?.visible) || meshes[0];
    if (!mesh) return;
    mesh.updateWorldMatrix?.(true, false);
    // Active model meshes are direct scene children. Copying this transform
    // keeps overlay points in model space when the user moves the model;
    // pose quaternions remain source-local.
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
    const nodeCenter = new Map((source?.nodes || []).map(node => [
      Number(node.boneId), node.weightedCenter,
    ]));
    nodeBoneIds = [...nodeCenter.keys()];
    const nodePositions = [];
    const nodeColors = [];
    nodeBoneIds.forEach(boneId => {
      const center = nodeCenter.get(boneId);
      if (!center) return;
      nodePositions.push(...center);
      nodeColors.push(...centerColor(
        componentFor(source, boneId), boneId, selectedBoneId));
    });

    const linePositions = [];
    const jointPositions = [];
    (source?.forestEdges || []).forEach(edge => {
      const first = nodeCenter.get(Number(edge.boneA));
      const second = nodeCenter.get(Number(edge.boneB));
      if (first && second) linePositions.push(...first, ...second);
      const joint = edge.jointCenter || pivotFor(source, Number(edge.childId));
      if (joint) jointPositions.push(...joint);
    });
    setGeometry(lineSegments, linePositions);
    setGeometry(centerPoints, nodePositions, nodeColors);
    setGeometry(jointPoints, jointPositions);
    rebuildCount += 1;
  }

  function updateProxy(source = currentSource, snapshot = currentSnapshot) {
    const boneId = selectedBoneFor(snapshot);
    selectedBoneId = boneId;
    if (!canPose(snapshot, source, boneId)) {
      detachControls();
      proxy.visible = false;
      return;
    }
    const poseFrame = getRigBonePoseFrame?.(source.sourceKey, boneId);
    const pivot = poseFrame?.pivot || pivotFor(source, boneId);
    if (pivot) proxy.position.copy(vector(pivot));
    const values = poseFrame?.boneRotation || quaternionFor(source, boneId);
    if (values) proxy.quaternion.set(...values).normalize();
    else proxy.quaternion.identity();
    proxy.visible = group.visible;
    if (transformControls) {
      transformControls.attach?.(proxy);
      transformControls.update?.();
    }
  }

  function updatePoseFromEvent(detail) {
    if (disposed || detail?.sourceKey !== activeSourceKey) return;
    const id = Number(detail.boneId);
    if (!Number.isInteger(id)) return;
    selectedBoneId = id;
    const poseFrame = getRigBonePoseFrame?.(detail.sourceKey, id);
    if (poseFrame?.boneRotation?.length === 4) {
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
    if (!canPose(currentSnapshot, currentSource)) return null;
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
          const source = currentSource;
          const boneId = selectedBoneFor(currentSnapshot);
          if (!canPose(currentSnapshot, source, boneId)) return;
          const poseFrame = getRigBonePoseFrame?.(source.sourceKey, boneId);
          const localRotation = proxy.quaternion.clone();
          if (poseFrame?.parentRotation?.length === 4) {
            const parentRotation = new THREE.Quaternion(
              ...poseFrame.parentRotation).normalize();
            localRotation.premultiply(parentRotation.invert()).normalize();
          }
          setRigBoneRotation?.(
            source.sourceKey, boneId, localRotation, {dragging: true});
        });
        transformControls.addEventListener?.('dragging-changed', event => {
          if (event.value !== undefined && canvas?.style) {
            canvas.style.cursor = event.value ? 'grabbing' : '';
          }
          if (event.value) {
            setArcballDragState(true);
            rigTransformInteractionActive = true;
          } else if (event.value === false) {
            setArcballDragState(false);
            queueMicrotask(() => {
              rigTransformInteractionActive = false;
            });
            const source = currentSource;
            const boneId = selectedBoneFor(currentSnapshot);
            if (source && boneId !== null) {
              finishRigPose?.(source.sourceKey, boneId);
            }
          }
        });
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
    currentSource = sourceFor(currentSnapshot);
    activeSourceKey = currentSource?.sourceKey || null;
    selectedBoneId = selectedBoneFor(currentSnapshot);
    const nextTopologyKey = topologyKey(currentSource);
    if (nextTopologyKey !== currentTopologyKey) {
      currentTopologyKey = nextTopologyKey;
      rebuildOverlay(currentSource);
    }
    updateModelFrame();
    group.visible = !!currentSnapshot.visible && !!currentSource;
    staticGroup.visible = group.visible;
    updateCenterColors();
    updateProxy(currentSource, currentSnapshot);
    if (canPose(currentSnapshot, currentSource)) void ensureTransformControls();
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
        staticObjectCount: staticGroup.children.length,
        nodeCount: nodeBoneIds.length,
        jointCount: jointPoints.geometry.getAttribute('position')?.count || 0,
        edgeCount: lineSegments.geometry.getAttribute('position')?.count / 2 || 0,
        selectedBoneId,
        controlsCreated: !!transformControls,
        controlsCreateCount,
        controlsAttached: transformControls?.object === proxy,
        helperInScene: !!transformHelper && transformHelper.parent === scene,
        arcballEnabled: arcballControls?.enabled,
        arcballWasEnabled,
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
