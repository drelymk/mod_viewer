// Owns per-mesh skin data and geometry operations. Weight-model composition
// owns model-wide loading, summary and selection lifecycle.

import * as THREE from 'three';
import { applyWeightedNormalDeformationInto, applyWeightedTransformDeformationInto } from './weight-deformation.js';
import { buildSurfaceInfluenceGraphCooperative } from './weight-rig.js';
import { buildSelectedWeightMask } from './weight-selection.js';
import { EMPTY_ACTIVE_VERTICES } from './weight-runtime.js';
import { createWorkBudget } from './cooperative-scheduler.js';
import { resumeAnimatedMesh } from '../mesh/animation-runtime.js';
import { syncLoosePartMaterial } from '../mesh/loose-parts.js';

function typedView(buffer, descriptor, Type, typeName) {
  if (!descriptor || descriptor.type !== typeName) {
    throw new Error('Skin data has an unsupported binary layout.');
  }
  const offset = Number(descriptor.offset);
  const length = Number(descriptor.length);
  if (
    !Number.isInteger(offset) ||
    !Number.isInteger(length) ||
    offset < 0 ||
    length < 0 ||
    offset % Type.BYTES_PER_ELEMENT ||
    length % Type.BYTES_PER_ELEMENT ||
    offset + length > buffer.byteLength
  ) {
    throw new Error('Skin data has an invalid binary range.');
  }
  return new Type(buffer, offset, length / Type.BYTES_PER_ELEMENT);
}

export function createSkinningRuntime({ states, knownMeshes, stateFor, requestRender } = {}) {
  const cooperativeGraphInFlight = new WeakMap();

  function markFinalBoundsDirty(mesh, state) {
    if (state.preDeformationFrustumCulled === null || state.preDeformationFrustumCulled === undefined) {
      state.preDeformationFrustumCulled = mesh.frustumCulled;
    }
    mesh.frustumCulled = false;
    state.finalBoundsDirty = true;
  }

  function restorePoseVertices(mesh, state, vertices) {
    const position = mesh.geometry?.attributes?.position;
    if (!position || !state.baselinePositions) return;
    const normal = mesh.geometry?.attributes?.normal;
    for (let index = 0; index < (vertices?.length || 0); index += 1) {
      const vertex = Number(vertices[index]);
      const offset = vertex * 3;
      if (offset < 0 || offset + 2 >= state.baselinePositions.length) continue;
      position.array[offset] = state.baselinePositions[offset];
      position.array[offset + 1] = state.baselinePositions[offset + 1];
      position.array[offset + 2] = state.baselinePositions[offset + 2];
      if (normal && state.baselineNormals && offset + 2 < state.baselineNormals.length) {
        normal.array[offset] = state.baselineNormals[offset];
        normal.array[offset + 1] = state.baselineNormals[offset + 1];
        normal.array[offset + 2] = state.baselineNormals[offset + 2];
      }
    }
    position.needsUpdate = true;
    if (normal) normal.needsUpdate = true;
  }

  function vertexDifference(previousVertices, currentVertices) {
    if (!previousVertices?.length) return new Uint32Array();
    const current = new Set(currentVertices || []);
    const removed = [];
    for (const vertex of previousVertices) {
      if (!current.has(vertex)) removed.push(vertex);
    }
    return Uint32Array.from(removed);
  }

  function combinedActiveVerticesForState(state) {
    const poseVertices = state.poseActiveVertices || EMPTY_ACTIVE_VERTICES;
    const physicsVertices = state.physicsEnabled
      ? state.physicsActiveVertices || EMPTY_ACTIVE_VERTICES
      : EMPTY_ACTIVE_VERTICES;
    if (
      state.combinedPoseVerticesRef === poseVertices &&
      state.combinedPhysicsVerticesRef === physicsVertices &&
      state.combinedActiveVertices
    ) {
      return state.combinedActiveVertices;
    }
    const values = new Set();
    poseVertices.forEach((vertex) => values.add(Number(vertex)));
    physicsVertices.forEach((vertex) => values.add(Number(vertex)));
    state.combinedPoseVerticesRef = poseVertices;
    state.combinedPhysicsVerticesRef = physicsVertices;
    state.combinedActiveVertices = Uint32Array.from([...values].sort((left, right) => left - right));
    return state.combinedActiveVertices;
  }

  function finalizeDeformationGeometry(mesh, state) {
    if (!state.finalBoundsDirty) return false;
    mesh.geometry.computeBoundingBox();
    mesh.geometry.computeBoundingSphere();
    state.finalBoundsDirty = false;
    if (state.preDeformationFrustumCulled !== null && state.preDeformationFrustumCulled !== undefined) {
      mesh.frustumCulled = state.preDeformationFrustumCulled;
      state.preDeformationFrustumCulled = null;
    }
    return true;
  }

  function restAttributeArray(mesh, attributeName, current) {
    const key = attributeName === 'position' ? 'basePositions' : 'baseNormals';
    const base = mesh.userData?.[key];
    return base && base.length === current.length ? base : current;
  }

  function forEachRigMesh(rig, callback) {
    for (const mesh of rig?.meshes || []) {
      const state = states.get(mesh);
      if (state) callback(mesh, state);
    }
  }

  function applyDeformation(
    mesh,
    state,
    {
      request = true,
      invalidateShadow = true,
      skipHidden = false,
      composedTransforms = null,
      composedRotations = null,
    } = {},
  ) {
    if (!state.loaded || !state.baselinePositions) return false;
    if (skipHidden && !mesh.visible) return false;
    const physicsActive =
      state.deformationMode === 'physics' && state.physicsEnabled && composedTransforms && composedRotations;
    const position = mesh.geometry.attributes.position;
    const finalTransforms = physicsActive ? composedTransforms : state.poseTransforms;
    const finalRotations = physicsActive ? composedRotations : state.poseRotations;
    const previousVertices = state.combinedActiveVertices || EMPTY_ACTIVE_VERTICES;
    const activeVertices = combinedActiveVerticesForState(state);
    const takingOwnership = Boolean(activeVertices.length || state.deformationMode || state.physicsEnabled);
    const wasSuspended = mesh.userData.animationSuspended === true;
    if (takingOwnership && mesh.userData.animationSuspended !== true) {
      // Animation may have written a baked frame since the last deformation
      // pass. Start the deformation owner from the canonical/rest geometry so
      // the first pose does not mix two unrelated vertex spaces.
      position.array.set(state.baselinePositions);
      const normal = mesh.geometry.attributes.normal;
      if (normal && state.baselineNormals && normal.array.length === state.baselineNormals.length) {
        normal.array.set(state.baselineNormals);
        normal.needsUpdate = true;
      }
      position.needsUpdate = true;
    }
    mesh.userData.animationSuspended = takingOwnership;
    const removedVertices = vertexDifference(previousVertices, activeVertices);
    if (removedVertices.length) restorePoseVertices(mesh, state, removedVertices);
    let deformedVertices = 0;
    if (activeVertices.length && finalTransforms) {
      deformedVertices = applyWeightedTransformDeformationInto(
        position.array,
        state.baselinePositions,
        state.indices,
        state.weights,
        state.influenceCount,
        finalTransforms,
        activeVertices,
      );
      const normal = mesh.geometry.attributes.normal;
      if (normal && state.baselineNormals && normal.array.length === state.baselineNormals.length && finalRotations) {
        applyWeightedNormalDeformationInto(
          normal.array,
          state.baselineNormals,
          state.indices,
          state.weights,
          state.influenceCount,
          finalRotations,
          activeVertices,
        );
        normal.needsUpdate = true;
      }
    }
    const changed = removedVertices.length > 0 || deformedVertices > 0;
    state.finalBoundsDirty = state.finalBoundsDirty || changed;
    if (changed) markFinalBoundsDirty(mesh, state);
    position.needsUpdate = changed || activeVertices.length > 0;
    let resumedAnimation = false;
    if (wasSuspended && !takingOwnership) {
      // Rig/Physics may have stopped the animation scheduler while it owned
      // this mesh. Resume from canonical animation geometry and keep the
      // conservative animation bounds after the handoff.
      resumedAnimation = resumeAnimatedMesh(mesh);
      if (resumedAnimation) {
        state.finalBoundsDirty = false;
        if (state.preDeformationFrustumCulled !== null && state.preDeformationFrustumCulled !== undefined) {
          mesh.frustumCulled = state.preDeformationFrustumCulled;
          state.preDeformationFrustumCulled = null;
        }
      }
    }
    if (invalidateShadow && changed && !resumedAnimation) {
      invalidateShadow({ request });
    } else if (request && changed && !resumedAnimation) requestRender();
    return changed;
  }

  function prepareRigMeshBase(mesh, state) {
    const position = mesh.geometry?.attributes?.position;
    if (!position) throw new Error('The selected mesh has no position data.');
    const normal = mesh.geometry?.attributes?.normal;
    if (!state.baselinePositions || state.baselinePositions.length !== position.array.length) {
      state.baselinePositions = new Float32Array(restAttributeArray(mesh, 'position', position.array));
    }
    if (normal && (!state.baselineNormals || state.baselineNormals.length !== normal.array.length)) {
      state.baselineNormals = new Float32Array(restAttributeArray(mesh, 'normal', normal.array));
    } else if (!normal) {
      state.baselineNormals = null;
    }
    if (!state.originalMaterial) state.originalMaterial = mesh.material;
  }

  function normalizeWeightBoneStats(stats) {
    if (!stats || typeof stats !== 'object' || Array.isArray(stats)) {
      throw new Error('Skin data has invalid bone statistics.');
    }
    return Object.fromEntries(
      Object.entries(stats).map(([rawBoneId, rawEntry]) => {
        const boneId = Number(rawBoneId);
        const affectedVertexCount = rawEntry?.affected_vertex_count;
        const totalWeight = rawEntry?.total_weight;
        if (
          !Number.isInteger(boneId) ||
          boneId < 0 ||
          !Number.isInteger(affectedVertexCount) ||
          affectedVertexCount < 0 ||
          !Number.isFinite(totalWeight) ||
          totalWeight < 0
        ) {
          throw new Error('Skin data has invalid bone statistics.');
        }
        return [String(boneId), { affectedVertexCount, totalWeight }];
      }),
    );
  }

  function sourceDescriptorForEntry(entry) {
    const source = entry?.source;
    if (
      source &&
      typeof source === 'object' &&
      typeof source.key === 'string' &&
      source.key &&
      typeof source.file === 'string' &&
      source.file
    ) {
      const offset = Number(source.bone_id_offset);
      if (Number.isInteger(offset) && offset >= 0) {
        return {
          sourceKey: source.key,
          sourceFile: source.file,
          boneIdOffset: offset,
          boneIdsModelWide: source.bone_ids_model_wide === true,
        };
      }
    }
    return null;
  }

  function refreshSelectedWeightMask(mesh, state, selectedBoneIds = []) {
    if (!state?.loaded) return null;
    const selected = new Set(selectedBoneIds || []);
    if (!selected.size) {
      state.selectedWeightMask = null;
      state.physicsActiveVertices = EMPTY_ACTIVE_VERTICES;
      state.combinedPhysicsVerticesRef = null;
      state.combinedActiveVertices = null;
      return null;
    }
    state.selectedWeightMask = buildSelectedWeightMask(state.indices, state.weights, state.influenceCount, selected);
    const activeVertices = [];
    state.selectedWeightMask.forEach((weight, vertex) => {
      if (weight > 0) activeVertices.push(vertex);
    });
    state.physicsActiveVertices = Uint32Array.from(activeVertices);
    state.combinedPhysicsVerticesRef = null;
    state.combinedActiveVertices = null;
    return state.selectedWeightMask;
  }

  function installSkinningEntry(mesh, entry, buffer) {
    const state = stateFor(mesh);
    const source = sourceDescriptorForEntry(entry);
    if (!source) throw new Error('The skin-weight source identity is unavailable for this draw.');
    if (!Array.isArray(entry.bone_ids) || !entry.bone_ids.every((id) => Number.isInteger(id) && id >= 0)) {
      throw new Error('Skin data has no valid bone-ID list.');
    }
    const weightBoneStats = normalizeWeightBoneStats(entry.weight_stats);
    state.skinningSourceKey = source.sourceKey;
    state.skinningSourceFile = source.sourceFile;
    state.skinningBoneOffset = source.boneIdOffset;
    const indices = typedView(buffer, entry.data?.indices, Uint32Array, 'u32');
    const weights = typedView(buffer, entry.data?.weights, Float32Array, 'f32');
    const position = mesh.geometry?.attributes?.position;
    const influenceCount = Number(entry.influence_count);
    if (
      !position ||
      !Number.isInteger(influenceCount) ||
      influenceCount <= 0 ||
      position.count !== Number(entry.vertex_count) ||
      indices.length !== position.count * influenceCount ||
      weights.length !== position.count * influenceCount
    ) {
      throw new Error('Skin data does not match rendered vertices.');
    }
    state.originalMaterial = mesh.material;
    state.baselinePositions = null;
    state.baselineNormals = null;
    state.influenceGraph = null;
    state.indices = indices;
    state.weights = weights;
    state.influenceCount = influenceCount;
    state.boneIds = [...new Set(entry.bone_ids)].sort((left, right) => left - right);
    state.encoding = entry.encoding || null;
    state.diagnostics = entry.diagnostics || null;
    state.weightBoneStats = weightBoneStats;
    state.loaded = true;
    state.error = null;
    return { state, source };
  }

  function ensureRigMeshPrepared(mesh, state) {
    if (!state?.loaded) return false;
    prepareRigMeshBase(mesh, state);
    return true;
  }

  async function ensureInfluenceGraphCooperative(
    mesh,
    state,
    { budget = createWorkBudget(), isCurrent = () => true } = {},
  ) {
    if (!state?.loaded || !isCurrent()) return null;
    if (state.influenceGraph) return state.influenceGraph;
    ensureRigMeshPrepared(mesh, state);
    const baselinePositions = state.baselinePositions;
    const pending = cooperativeGraphInFlight.get(mesh);
    if (pending?.baselinePositions === baselinePositions) {
      const graph = await pending.promise;
      if (!isCurrent()) return null;
      return graph || ensureInfluenceGraphCooperative(mesh, state, { budget, isCurrent });
    }
    if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
    const radius = Number(mesh.geometry.boundingSphere?.radius);
    const promise = buildSurfaceInfluenceGraphCooperative(
      baselinePositions,
      mesh.geometry?.index?.array || null,
      state.indices,
      state.weights,
      state.influenceCount,
      state.boneIds,
      Number.isFinite(radius) && radius > 0 ? radius : null,
      { budget, isCurrent },
    )
      .then((graph) => {
        if (!state.loaded || !isCurrent() || state.baselinePositions !== baselinePositions) return null;
        if (graph) state.influenceGraph = graph;
        return graph;
      })
      .finally(() => {
        if (cooperativeGraphInFlight.get(mesh)?.promise === promise) cooperativeGraphInFlight.delete(mesh);
      });
    cooperativeGraphInFlight.set(mesh, { baselinePositions, promise });
    return promise;
  }

  function updateHeatmap(mesh, state, selectedMask = state.selectedWeightMask) {
    if (!state.heatmapMode) return;
    if (!selectedMask) {
      disableHeatmap(mesh, state);
      return;
    }
    const count = Math.floor(state.indices.length / state.influenceCount);
    const colors = new Float32Array(count * 3);
    for (let vertex = 0; vertex < count; vertex += 1) {
      const value = Math.max(0, Math.min(1, Number(selectedMask[vertex]) || 0));
      const offset = vertex * 3;
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
    syncLoosePartMaterial(mesh);
  }

  function disableHeatmap(mesh, state) {
    state.heatmapMode = null;
    mesh.material = state.originalMaterial;
    mesh.geometry.deleteAttribute('color');
    syncLoosePartMaterial(mesh);
    if (state.debugMaterial) {
      state.debugMaterial.dispose();
      state.debugMaterial = null;
    }
  }

  function updateModelWeightHeatmap(changedSourceKeys = null, heatmapEnabled = false) {
    knownMeshes.forEach((mesh) => {
      const state = states.get(mesh);
      if (!state?.loaded) return;
      if (changedSourceKeys && !changedSourceKeys.has(state.skinningSourceKey)) return;
      const mask = state.selectedWeightMask;
      if (heatmapEnabled && mask?.some((value) => value > 0)) {
        state.heatmapMode = 'bone';
        updateHeatmap(mesh, state, mask);
      } else if (state.heatmapMode) {
        disableHeatmap(mesh, state);
      }
    });
  }

  function getSkinningBaseMaterial(mesh) {
    const state = states.get(mesh);
    return state ? state.originalMaterial || mesh.material : mesh?.material;
  }

  function withSkinningBaseMaterial(mesh, operation) {
    const state = states.get(mesh);
    if (!state) return operation();
    const heatmapActive = state.heatmapMode && state.debugMaterial && mesh.material === state.debugMaterial;
    const displayedMaterial = mesh.material;
    if (heatmapActive) {
      mesh.material = state.originalMaterial || mesh.material;
      syncLoosePartMaterial(mesh);
    }
    try {
      const result = operation();
      state.originalMaterial = mesh.material;
      return result;
    } finally {
      if (heatmapActive) {
        mesh.material = displayedMaterial;
        syncLoosePartMaterial(mesh);
      }
    }
  }

  function rebaseAfterShapeChange(mesh, { positions = null, normals = null } = {}) {
    const state = states.get(mesh);
    const position = mesh?.geometry?.attributes?.position;
    if (!state?.loaded || !position) return false;
    const shapedPositions = positions || new Float32Array(position.array);
    const normal = mesh.geometry.attributes.normal;
    const shapedNormals = normals || (normal ? new Float32Array(normal.array) : null);
    state.poseTransforms = null;
    state.poseRotations = new Map();
    state.poseActiveVertices = null;
    state.combinedActiveVertices = null;
    state.combinedPoseVerticesRef = null;
    state.combinedPhysicsVerticesRef = null;
    position.array.set(shapedPositions);
    position.needsUpdate = true;
    if (normal && shapedNormals && normal.array.length === shapedNormals.length) {
      normal.array.set(shapedNormals);
      normal.needsUpdate = true;
    }
    mesh.geometry.computeBoundingBox();
    mesh.geometry.computeBoundingSphere();
    state.baselinePositions = new Float32Array(position.array);
    state.baselineNormals = normal ? new Float32Array(normal.array) : null;
    ensureRigMeshPrepared(mesh, state);
    state.influenceGraph = null;
    return true;
  }

  function disposeMesh(mesh) {
    const state = states.get(mesh);
    if (!state) return;
    state.disposed = true;
    mesh.userData.animationSuspended = false;
    if (state.debugMaterial) state.debugMaterial.dispose();
    mesh.geometry?.deleteAttribute?.('color');
    mesh.material = state.originalMaterial || mesh.material;
    states.delete(mesh);
  }

  return {
    applyDeformation,
    ensureInfluenceGraphCooperative,
    ensureRigMeshPrepared,
    finalizeDeformationGeometry,
    forEachRigMesh,
    getSkinningState: (mesh) => states.get(mesh) || null,
    getSkinningBaseMaterial,
    withSkinningBaseMaterial,
    refreshSelectedWeightMask,
    installSkinningEntry,
    markFinalBoundsDirty,
    rebaseAfterShapeChange,
    updateModelWeightHeatmap,
    disposeMesh,
  };
}
