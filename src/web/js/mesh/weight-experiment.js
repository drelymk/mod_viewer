// Explicit, removable skin-weight experiment. Normal mesh loading never
// imports or invokes this module's bridge operation until the Inspector asks.

import * as THREE from 'three';
import { invalidateCharacterShadowGeometry } from '../scene/scene.js';
import { requestRender } from '../scene/render-scheduler.js';

const states = new WeakMap();
let activeHelperMesh = null;

const ERROR_MESSAGES = Object.freeze({
  mesh_not_found: 'The selected mesh could not be found.',
  skinning_not_available: 'No skin-weight stream was found for this draw.',
  ambiguous_skinning_source:
    'More than one possible Blend stream is active for this draw.',
  unsupported_skinning_layout:
    'This Blend format is not supported by the experiment.',
  skinning_buffer_truncated: 'The skin-weight buffer is truncated.',
  geometry_not_available:
    'The rendered draw geometry could not be prepared.',
});

function newState() {
  return {
    loaded: false,
    loading: false,
    promise: null,
    error: null,
    influenceCount: 0,
    boneIds: [],
    indices: null,
    weights: null,
    selectedBone: null,
    axis: 'Z',
    angle: 0,
    chainText: '',
    chainIds: [],
    chainAxis: 'Z',
    chainAngle: 0,
    chainError: null,
    chainHelpersVisible: false,
    chainHelpers: null,
    baselinePositions: null,
    baselineNormals: null,
    originalMaterial: null,
    debugMaterial: null,
    heatmapEnabled: false,
    diagnostics: null,
    encoding: null,
    centers: new Map(),
  };
}

function stateFor(mesh) {
  if (!mesh) return null;
  let state = states.get(mesh);
  if (!state) {
    state = newState();
    states.set(mesh, state);
  }
  return state;
}

export function getSkinningState(mesh) {
  return states.get(mesh) || null;
}

export function weightForBone(indices, weights, influenceCount,
                              vertexIndex, boneId) {
  if (!indices || !weights || influenceCount <= 0) return 0;
  const start = vertexIndex * influenceCount;
  let total = 0;
  for (let influence = 0; influence < influenceCount; influence += 1) {
    if (indices[start + influence] === boneId) {
      const weight = weights[start + influence];
      if (Number.isFinite(weight) && weight > 0) total += weight;
    }
  }
  return total;
}

export function buildBoneIds(indices, weights, influenceCount) {
  const result = new Set();
  if (!indices || !weights || influenceCount <= 0) return [];
  const count = Math.min(indices.length, weights.length);
  for (let offset = 0; offset < count; offset += 1) {
    if (Number.isFinite(weights[offset]) && weights[offset] > 0) {
      result.add(Number(indices[offset]));
    }
  }
  return [...result].sort((a, b) => a - b);
}

export function parseChainIds(text, validBoneIds) {
  if (typeof text !== 'string' || !text.trim()) {
    throw new Error('A chain requires at least 2 unique bone IDs.');
  }
  const valid = new Set([...validBoneIds || []].map(Number));
  const ids = [];
  for (const raw of text.split(',')) {
    const token = raw.trim();
    if (!/^\d+$/.test(token)) {
      throw new Error('Chain IDs must be comma-separated integer IDs.');
    }
    const id = Number(token);
    if (!Number.isSafeInteger(id)) {
      throw new Error('Chain IDs must be comma-separated integer IDs.');
    }
    if (!valid.has(id)) throw new Error(`Unknown Bone ID: ${id}`);
    if (ids.includes(id)) throw new Error(`Duplicate Bone ID: ${id}`);
    ids.push(id);
  }
  if (ids.length < 2) {
    throw new Error('A chain requires at least 2 unique bone IDs.');
  }
  return ids;
}

export function weightedCenter(positions, indices, weights, influenceCount,
                               boneId) {
  const center = [0, 0, 0];
  if (!positions || !indices || !weights || influenceCount <= 0) return center;
  let total = 0;
  const vertexCount = Math.floor(positions.length / 3);
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const weight = weightForBone(
      indices, weights, influenceCount, vertex, boneId);
    if (!weight) continue;
    const offset = vertex * 3;
    center[0] += positions[offset] * weight;
    center[1] += positions[offset + 1] * weight;
    center[2] += positions[offset + 2] * weight;
    total += weight;
  }
  if (total > 0) {
    center[0] /= total;
    center[1] /= total;
    center[2] /= total;
  }
  return center;
}

function rotatePoint(x, y, z, center, axis, radians) {
  const dx = x - center[0];
  const dy = y - center[1];
  const dz = z - center[2];
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  let rx = dx;
  let ry = dy;
  let rz = dz;
  if (axis === 'X') {
    ry = dy * cos - dz * sin;
    rz = dy * sin + dz * cos;
  } else if (axis === 'Y') {
    rx = dx * cos + dz * sin;
    rz = -dx * sin + dz * cos;
  } else {
    rx = dx * cos - dy * sin;
    ry = dx * sin + dy * cos;
  }
  return [rx + center[0], ry + center[1], rz + center[2]];
}

export function applyWeightedRotation(baselinePositions, indices, weights,
                                       influenceCount, boneId, center,
                                       axis = 'Z', angleDegrees = 0) {
  const result = new Float32Array(baselinePositions || 0);
  if (!baselinePositions || !indices || !weights || influenceCount <= 0
      || !Number.isFinite(angleDegrees)) return result;
  const radians = angleDegrees * Math.PI / 180;
  const vertexCount = Math.floor(baselinePositions.length / 3);
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const offset = vertex * 3;
    const weight = Math.max(0, Math.min(1, weightForBone(
      indices, weights, influenceCount, vertex, boneId)));
    if (!weight || !center) continue;
    const rotated = rotatePoint(
      baselinePositions[offset], baselinePositions[offset + 1],
      baselinePositions[offset + 2], center, axis, radians);
    result[offset] = baselinePositions[offset]
      + weight * (rotated[0] - baselinePositions[offset]);
    result[offset + 1] = baselinePositions[offset + 1]
      + weight * (rotated[1] - baselinePositions[offset + 1]);
    result[offset + 2] = baselinePositions[offset + 2]
      + weight * (rotated[2] - baselinePositions[offset + 2]);
  }
  return result;
}

function vectorFromCenter(center) {
  if (center?.isVector3) return center.clone();
  return new THREE.Vector3(
    Number(center?.[0]) || 0,
    Number(center?.[1]) || 0,
    Number(center?.[2]) || 0,
  );
}

function axisVector(axis) {
  if (axis === 'X') return new THREE.Vector3(1, 0, 0);
  if (axis === 'Y') return new THREE.Vector3(0, 1, 0);
  return new THREE.Vector3(0, 0, 1);
}

export function buildChainTransforms(centers, axis = 'Z', totalAngle = 0) {
  const source = Array.isArray(centers) ? centers : [];
  const transforms = source.map(() => new THREE.Matrix4());
  if (source.length < 2 || !Number.isFinite(Number(totalAngle))) {
    return transforms;
  }
  const jointAngle = THREE.MathUtils.degToRad(Number(totalAngle))
    / (source.length - 1);
  const rotationAxis = axisVector(axis);
  for (let index = 1; index < source.length; index += 1) {
    const parent = transforms[index - 1];
    const pivot = vectorFromCenter(source[index - 1])
      .applyMatrix4(parent);
    const aroundPivot = new THREE.Matrix4()
      .makeTranslation(pivot.x, pivot.y, pivot.z)
      .multiply(new THREE.Matrix4().makeRotationAxis(
        rotationAxis, jointAngle))
      .multiply(new THREE.Matrix4().makeTranslation(
        -pivot.x, -pivot.y, -pivot.z));
    transforms[index] = aroundPivot.multiply(parent.clone());
  }
  return transforms;
}

export function applyWeightedChainDeformation(
    baselinePositions, indices, weights, influenceCount, chainIds,
    transforms) {
  const result = new Float32Array(baselinePositions || 0);
  if (!baselinePositions || !indices || !weights || influenceCount <= 0
      || !Array.isArray(chainIds) || chainIds.length < 2
      || !Array.isArray(transforms) || transforms.length < chainIds.length) {
    return result;
  }
  const chainIndex = new Map(chainIds.map((id, index) => [Number(id), index]));
  const vertexCount = Math.floor(baselinePositions.length / 3);
  const baseline = new THREE.Vector3();
  const transformed = new THREE.Vector3();
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const offset = vertex * 3;
    baseline.set(
      baselinePositions[offset], baselinePositions[offset + 1],
      baselinePositions[offset + 2]);
    const start = vertex * influenceCount;
    let chainWeight = 0;
    let x = 0, y = 0, z = 0;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const weight = weights[start + influence];
      const transformIndex = chainIndex.get(indices[start + influence]);
      if (transformIndex === undefined || !Number.isFinite(weight)
          || weight <= 0 || !transforms[transformIndex]) continue;
      chainWeight += weight;
      transformed.copy(baseline).applyMatrix4(transforms[transformIndex]);
      x += transformed.x * weight;
      y += transformed.y * weight;
      z += transformed.z * weight;
    }
    const unchanged = 1 - chainWeight;
    result[offset] = baseline.x * unchanged + x;
    result[offset + 1] = baseline.y * unchanged + y;
    result[offset + 2] = baseline.z * unchanged + z;
  }
  return result;
}

function previewError(result) {
  const code = result?.code;
  return new Error(ERROR_MESSAGES[code] || result?.error
    || 'Could not load skin weights.');
}

function typedView(buffer, descriptor, Type, typeName) {
  if (!descriptor || descriptor.type !== typeName) {
    throw new Error('Skin data has an unsupported binary layout.');
  }
  const offset = Number(descriptor.offset);
  const length = Number(descriptor.length);
  if (!Number.isInteger(offset) || !Number.isInteger(length)
      || offset < 0 || length < 0 || offset % Type.BYTES_PER_ELEMENT
      || length % Type.BYTES_PER_ELEMENT
      || offset + length > buffer.byteLength) {
    throw new Error('Skin data has an invalid binary range.');
  }
  return new Type(buffer, offset, length / Type.BYTES_PER_ELEMENT);
}

function captureBaseline(mesh, state) {
  const position = mesh.geometry?.attributes?.position;
  if (!position) throw new Error('The selected mesh has no position data.');
  const normal = mesh.geometry?.attributes?.normal;
  state.baselinePositions = new Float32Array(position.array);
  state.baselineNormals = normal ? new Float32Array(normal.array) : null;
  state.originalMaterial = mesh.material;
  state.centers.clear();
}

function restoreNormals(mesh, state) {
  if (!state.baselineNormals) {
    mesh.geometry.computeVertexNormals();
    return;
  }
  let normal = mesh.geometry.attributes.normal;
  if (!normal || normal.array.length !== state.baselineNormals.length) {
    normal = new THREE.BufferAttribute(
      new Float32Array(state.baselineNormals.length), 3);
    mesh.geometry.setAttribute('normal', normal);
  }
  normal.array.set(state.baselineNormals);
  normal.needsUpdate = true;
}

function centerFor(mesh, state) {
  return centerForBone(mesh, state, state.selectedBone);
}

function centerForBone(mesh, state, boneId) {
  if (!state.centers.has(boneId)) {
    state.centers.set(boneId, weightedCenter(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, boneId));
  }
  return state.centers.get(boneId);
}

function chainCentersFor(mesh, state) {
  return state.chainIds.map(boneId => centerForBone(mesh, state, boneId));
}

function removeVirtualChainHelpers(mesh, state) {
  const helpers = state?.chainHelpers;
  if (!helpers) {
    if (activeHelperMesh === mesh) activeHelperMesh = null;
    if (state) state.chainHelpersVisible = false;
    return;
  }
  helpers.group.removeFromParent();
  helpers.markerGeometry.dispose();
  helpers.markerMaterial.dispose();
  helpers.lineGeometry.dispose();
  helpers.lineMaterial.dispose();
  state.chainHelpers = null;
  state.chainHelpersVisible = false;
  if (activeHelperMesh === mesh) activeHelperMesh = null;
}

function activateVirtualChain(mesh) {
  if (activeHelperMesh && activeHelperMesh !== mesh) {
    removeVirtualChainHelpers(activeHelperMesh, states.get(activeHelperMesh));
  }
  activeHelperMesh = mesh;
}

function createVirtualChainHelpers(mesh, state) {
  const radius = Math.max(
    (mesh.geometry.boundingSphere?.radius || 1) * 0.012, 0.001);
  const group = new THREE.Group();
  group.name = 'Experimental Virtual Chain';
  const markerGeometry = new THREE.SphereGeometry(1, 8, 6);
  const markerMaterial = new THREE.MeshBasicMaterial({color: 0xffb86c});
  const markers = state.chainIds.map(boneId => {
    const marker = new THREE.Mesh(markerGeometry, markerMaterial);
    marker.scale.setScalar(radius);
    marker.userData.virtualBoneId = boneId;
    group.add(marker);
    return marker;
  });
  const lineGeometry = new THREE.BufferGeometry();
  lineGeometry.setAttribute('position', new THREE.BufferAttribute(
    new Float32Array(state.chainIds.length * 3), 3));
  const lineMaterial = new THREE.LineBasicMaterial({color: 0xff7b72});
  const line = new THREE.Line(lineGeometry, lineMaterial);
  group.add(line);
  mesh.add(group);
  state.chainHelpers = {
    group, markers, line, markerGeometry, markerMaterial,
    lineGeometry, lineMaterial,
  };
  return state.chainHelpers;
}

function updateVirtualChainHelpers(mesh, state) {
  const helpers = state.chainHelpers;
  if (!helpers || state.chainIds.length < 2) return;
  const centers = chainCentersFor(mesh, state);
  const transforms = buildChainTransforms(
    centers, state.chainAxis, state.chainAngle);
  const linePositions = helpers.line.geometry.attributes.position;
  centers.forEach((center, index) => {
    const point = vectorFromCenter(center).applyMatrix4(transforms[index]);
    helpers.markers[index].position.copy(point);
    linePositions.setXYZ(index, point.x, point.y, point.z);
  });
  linePositions.needsUpdate = true;
  helpers.line.geometry.computeBoundingSphere();
}

if (typeof window !== 'undefined') {
  window.addEventListener('mod-viewer-mesh-selected', event => {
    const mesh = event.detail?.mesh || null;
    if (activeHelperMesh && activeHelperMesh !== mesh) {
      removeVirtualChainHelpers(activeHelperMesh, states.get(activeHelperMesh));
    }
    activeHelperMesh = mesh;
  });
}

function applyDeformation(mesh, state) {
  if (!state.loaded || !state.baselinePositions) return;
  const position = mesh.geometry.attributes.position;
  const chainActive = state.chainAngle !== 0
    && state.chainIds.length >= 2;
  const result = chainActive
    ? applyWeightedChainDeformation(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, state.chainIds, buildChainTransforms(
        chainCentersFor(mesh, state), state.chainAxis, state.chainAngle))
    : state.angle === 0
      ? state.baselinePositions
      : applyWeightedRotation(
        state.baselinePositions, state.indices, state.weights,
        state.influenceCount, state.selectedBone, centerFor(mesh, state),
        state.axis, state.angle);
  position.array.set(result);
  position.needsUpdate = true;
  if (!chainActive && state.angle === 0) {
    restoreNormals(mesh, state);
  } else {
    mesh.geometry.computeVertexNormals();
    mesh.geometry.attributes.normal.needsUpdate = true;
  }
  updateVirtualChainHelpers(mesh, state);
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  invalidateCharacterShadowGeometry();
  requestRender();
}

function updateHeatmap(mesh, state) {
  if (!state.heatmapEnabled) return;
  const count = Math.floor(state.indices.length / state.influenceCount);
  const colors = new Float32Array(count * 3);
  for (let vertex = 0; vertex < count; vertex += 1) {
    const value = Math.max(0, Math.min(1, weightForBone(
      state.indices, state.weights, state.influenceCount,
      vertex, state.selectedBone)));
    const offset = vertex * 3;
    // Blue at zero, yellow/red at high influence for quick spatial reading.
    colors[offset] = value;
    colors[offset + 1] = Math.min(1, value * 2);
    colors[offset + 2] = 1 - value;
  }
  mesh.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  mesh.geometry.attributes.color.needsUpdate = true;
  if (!state.debugMaterial) {
    state.debugMaterial = new THREE.MeshBasicMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
    });
  }
  mesh.material = state.debugMaterial;
}

function disableHeatmap(mesh, state) {
  state.heatmapEnabled = false;
  mesh.material = state.originalMaterial;
  mesh.geometry.deleteAttribute('color');
  if (state.debugMaterial) {
    state.debugMaterial.dispose();
    state.debugMaterial = null;
  }
}

export async function loadSkinningWeights(mesh) {
  const state = stateFor(mesh);
  if (!state) throw new Error('No mesh was selected.');
  if (state.loaded) return state;
  if (state.promise) return state.promise;

  state.loading = true;
  state.error = null;
  state.promise = (async () => {
    captureBaseline(mesh, state);
    const api = window.pywebview?.api?.get_skinning_preview;
    if (typeof api !== 'function') {
      throw new Error('Skin-weight preview is unavailable.');
    }
    const preview = await api(
      mesh.userData.modPath, mesh.userData.semanticKey);
    if (!preview || preview.status !== 'ok') throw previewError(preview);
    const response = await fetch(preview.data?.url, {cache: 'no-store'});
    if (!response.ok) {
      throw new Error(`Skin data download failed (${response.status}).`);
    }
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength !== Number(preview.data.length)) {
      throw new Error('Skin data download was incomplete.');
    }
    const indices = typedView(
      buffer, preview.data.indices, Uint32Array, 'u32');
    const weights = typedView(
      buffer, preview.data.weights, Float32Array, 'f32');
    if (states.get(mesh) !== state) {
      throw new Error('The skin-weight experiment was reset.');
    }
    const positionCount = mesh.geometry.attributes.position.count;
    if (positionCount !== Number(preview.vertex_count)) {
      throw new Error(
        `Skin data does not match rendered vertices. Expected ${positionCount.toLocaleString()}, received ${Number(preview.vertex_count).toLocaleString()}.`);
    }
    const influenceCount = Number(preview.influence_count);
    if (!Number.isInteger(influenceCount) || influenceCount <= 0
        || indices.length !== positionCount * influenceCount
        || weights.length !== positionCount * influenceCount) {
      throw new Error('Skin data does not match rendered vertices.');
    }
    state.indices = indices;
    state.weights = weights;
    state.influenceCount = influenceCount;
    state.boneIds = Array.isArray(preview.bone_ids)
      ? [...preview.bone_ids].map(Number).filter(Number.isFinite)
        .sort((a, b) => a - b)
      : buildBoneIds(indices, weights, influenceCount);
    state.selectedBone = state.boneIds[0] ?? 0;
    state.encoding = preview.encoding || null;
    state.diagnostics = preview.diagnostics || null;
    state.loaded = true;
    return state;
  })();
  try {
    return await state.promise;
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
    throw error;
  } finally {
    state.loading = false;
    state.promise = null;
  }
}

export function setSelectedBone(mesh, boneId) {
  const state = stateFor(mesh);
  if (!state?.loaded) return;
  state.selectedBone = Number(boneId);
  if (state.heatmapEnabled) updateHeatmap(mesh, state);
  applyDeformation(mesh, state);
}

export function setSkinningAxis(mesh, axis) {
  const state = stateFor(mesh);
  if (!state?.loaded || !['X', 'Y', 'Z'].includes(axis)) return;
  state.axis = axis;
  applyDeformation(mesh, state);
}

export function setSkinningAngle(mesh, angle) {
  const state = stateFor(mesh);
  if (!state?.loaded) return;
  state.angle = Math.max(-45, Math.min(45, Number(angle) || 0));
  state.chainAngle = 0;
  if (state.chainHelpers) removeVirtualChainHelpers(mesh, state);
  applyDeformation(mesh, state);
}

export function setSkinningChainText(mesh, text) {
  const state = stateFor(mesh);
  if (!state?.loaded) return false;
  state.chainText = String(text ?? '');
  const wasVisible = state.chainHelpersVisible;
  try {
    state.chainIds = parseChainIds(state.chainText, state.boneIds);
    state.chainError = null;
    state.angle = 0;
  } catch (error) {
    state.chainIds = [];
    state.chainAngle = 0;
    state.chainError = error instanceof Error ? error.message : String(error);
    removeVirtualChainHelpers(mesh, state);
    applyDeformation(mesh, state);
    return false;
  }
  if (state.chainHelpers) {
    removeVirtualChainHelpers(mesh, state);
    if (wasVisible) {
      activateVirtualChain(mesh);
      createVirtualChainHelpers(mesh, state);
      state.chainHelpersVisible = true;
      updateVirtualChainHelpers(mesh, state);
    }
  }
  applyDeformation(mesh, state);
  return true;
}

export function setSkinningChainAxis(mesh, axis) {
  const state = stateFor(mesh);
  if (!state?.loaded || !['X', 'Y', 'Z'].includes(axis)) return;
  state.chainAxis = axis;
  state.angle = 0;
  applyDeformation(mesh, state);
}

export function setSkinningChainAngle(mesh, angle) {
  const state = stateFor(mesh);
  if (!state?.loaded || state.chainIds.length < 2) return false;
  state.chainAngle = Math.max(-60, Math.min(60, Number(angle) || 0));
  state.angle = 0;
  applyDeformation(mesh, state);
  return true;
}

export function setVirtualChainVisible(mesh, visible) {
  const state = stateFor(mesh);
  if (!state?.loaded) return false;
  if (visible && state.chainIds.length < 2) return false;
  if (!visible) {
    removeVirtualChainHelpers(mesh, state);
  } else {
    activateVirtualChain(mesh);
    if (!state.chainHelpers) createVirtualChainHelpers(mesh, state);
    updateVirtualChainHelpers(mesh, state);
    state.chainHelpersVisible = true;
  }
  requestRender();
  return state.chainHelpersVisible;
}

export function setSkinningHeatmap(mesh, enabled) {
  const state = stateFor(mesh);
  if (!state?.loaded) return false;
  state.heatmapEnabled = !!enabled;
  if (state.heatmapEnabled) updateHeatmap(mesh, state);
  else disableHeatmap(mesh, state);
  requestRender();
  return state.heatmapEnabled;
}

export function resetSkinningExperiment(mesh) {
  const state = stateFor(mesh);
  if (!state?.loaded) return;
  state.angle = 0;
  state.chainAngle = 0;
  removeVirtualChainHelpers(mesh, state);
  applyDeformation(mesh, state);
  if (state.heatmapEnabled || state.debugMaterial) disableHeatmap(mesh, state);
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  invalidateCharacterShadowGeometry();
  requestRender();
}

export function disposeSkinningExperiment(mesh) {
  const state = states.get(mesh);
  if (!state) return;
  state.disposed = true;
  removeVirtualChainHelpers(mesh, state);
  if (state.debugMaterial) state.debugMaterial.dispose();
  mesh.geometry?.deleteAttribute?.('color');
  mesh.material = state.originalMaterial || mesh.material;
  states.delete(mesh);
}
