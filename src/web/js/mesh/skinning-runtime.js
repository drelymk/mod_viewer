// Owns the model-wide skinning lifecycle. Rig construction and pose
// composition consume this runtime through its callback surface; they do not
// reach into its binary or geometry bookkeeping.

import * as THREE from 'three';
import {
  applyWeightedNormalDeformationInto,
  applyWeightedTransformDeformationInto,
} from './weight-deformation.js';
import {buildSelectedWeightMask} from './weight-selection.js';
import {
  buildInfluenceNodes as buildRigInfluenceNodes,
  buildInfluenceRelationships as buildRigInfluenceRelationships,
  buildSurfaceInfluenceGraph,
  inspectSurfaceTopology,
} from './weight-rig.js';
import {EMPTY_ACTIVE_VERTICES} from './weight-runtime.js';

let activeRuntime = null;

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

function buildBoneIds(indices, weights, influenceCount) {
  const result = new Set();
  if (!indices || !weights || influenceCount <= 0) return [];
  const count = Math.min(indices.length, weights.length);
  for (let offset = 0; offset < count; offset += 1) {
    if (Number.isFinite(weights[offset]) && weights[offset] > 0) {
      result.add(Number(indices[offset]));
    }
  }
  return [...result].sort((left, right) => left - right);
}

export function createSkinningRuntime({
    states, knownMeshes, stateFor, modelWeightState,
    modelPhysicsSession, sourcePhysicsRigs, sourceSkinningRigs,
    modelWeightSnapshot, selectionMapFromEntries, sourceSelectionEntries,
    setSelectedBones, syncPhysicsToSelection, refreshModelWeightSummary,
    refreshSelectedWeightMask, eligibleSkinningMesh,
    getGeneration, getModelRigState, getRigPresetState,
    getModelSkinningRig, setModelSkinningRig,
    invalidateHumanoidDetection, invalidateModelRigLoad, clearPickedPoint,
    resetModelPose,
    syncPhysicsParticipants, buildAllSourceSkinningRigs,
    buildModelSkinningRig, notifyModelWeightChanged, notifyModelRigChanged,
    resetModelState, requestRender, invalidateShadow,
  } = {}) {
  const modelRigState = getModelRigState();
  const rigPresetState = getRigPresetState();

  function markFinalBoundsDirty(mesh, state) {
    if (state.preDeformationFrustumCulled === null
        || state.preDeformationFrustumCulled === undefined) {
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
      if (normal && state.baselineNormals
          && offset + 2 < state.baselineNormals.length) {
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
    if (state.combinedPoseVerticesRef === poseVertices
        && state.combinedPhysicsVerticesRef === physicsVertices
        && state.combinedActiveVertices) {
      return state.combinedActiveVertices;
    }
    const values = new Set();
    poseVertices.forEach(vertex => values.add(Number(vertex)));
    physicsVertices.forEach(vertex => values.add(Number(vertex)));
    state.combinedPoseVerticesRef = poseVertices;
    state.combinedPhysicsVerticesRef = physicsVertices;
    state.combinedActiveVertices = Uint32Array.from([...values].sort(
      (left, right) => left - right));
    return state.combinedActiveVertices;
  }

  function finalizeDeformationGeometry(mesh, state) {
    if (!state.finalBoundsDirty) return false;
    mesh.geometry.computeBoundingBox();
    mesh.geometry.computeBoundingSphere();
    state.finalBoundsDirty = false;
    if (state.preDeformationFrustumCulled !== null
        && state.preDeformationFrustumCulled !== undefined) {
      mesh.frustumCulled = state.preDeformationFrustumCulled;
      state.preDeformationFrustumCulled = null;
    }
    return true;
  }

  function forEachRigMesh(rig, callback) {
    for (const mesh of rig?.meshes || []) {
      const state = states.get(mesh);
      if (state) callback(mesh, state);
    }
  }

  function applyDeformation(mesh, state, {
      request = true, invalidateShadow = true, skipHidden = false,
      composedTransforms = null, composedRotations = null,
    } = {}) {
    if (!state.loaded || !state.baselinePositions) return false;
    if (skipHidden && !mesh.visible) return false;
    const physicsActive = state.deformationMode === 'physics'
      && state.physicsEnabled && composedTransforms && composedRotations;
    const position = mesh.geometry.attributes.position;
    const finalTransforms = physicsActive
      ? composedTransforms : state.poseTransforms;
    const finalRotations = physicsActive
      ? composedRotations : state.poseRotations;
    const previousVertices = state.combinedActiveVertices
      || EMPTY_ACTIVE_VERTICES;
    const activeVertices = combinedActiveVerticesForState(state);
    const removedVertices = vertexDifference(previousVertices, activeVertices);
    if (removedVertices.length) restorePoseVertices(mesh, state, removedVertices);
    let deformedVertices = 0;
    if (activeVertices.length && finalTransforms) {
      deformedVertices = applyWeightedTransformDeformationInto(
        position.array, state.baselinePositions, state.indices, state.weights,
        state.influenceCount, finalTransforms, activeVertices);
      const normal = mesh.geometry.attributes.normal;
      if (normal && state.baselineNormals
          && normal.array.length === state.baselineNormals.length
          && finalRotations) {
        applyWeightedNormalDeformationInto(
          normal.array, state.baselineNormals, state.indices, state.weights,
          state.influenceCount, finalRotations, activeVertices);
        normal.needsUpdate = true;
      }
    }
    const changed = removedVertices.length > 0 || deformedVertices > 0;
    state.finalBoundsDirty = state.finalBoundsDirty || changed;
    if (changed) markFinalBoundsDirty(mesh, state);
    position.needsUpdate = changed || activeVertices.length > 0;
    if (invalidateShadow && changed) invalidateShadow({request});
    else if (request && changed) requestRender();
    return changed;
  }

  function buildInfluenceGraph(mesh, state, requestedEvidenceMode = 'vertex',
      surfaceEvidence = null) {
    ensureRigMeshPrepared(mesh, state);
    if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
    const radius = Number(mesh.geometry.boundingSphere?.radius);
    const rawNodes = state.influenceNodes || buildRigInfluenceNodes(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, state.boneIds);
    state.influenceNodes = rawNodes;
    const measure = surfaceEvidence || (requestedEvidenceMode === 'surface'
      ? inspectSurfaceTopology(
        state.baselinePositions, mesh.geometry?.index?.array || null) : null);
    const evidenceMode = requestedEvidenceMode === 'surface'
      && measure?.surfaceEvidenceAvailable ? 'surface' : 'vertex';
    if (evidenceMode === 'surface') {
      return buildSurfaceInfluenceGraph(
        state.baselinePositions, mesh.geometry?.index?.array || null,
        state.indices, state.weights, state.influenceCount, state.boneIds,
        Number.isFinite(radius) && radius > 0 ? radius : null);
    }
    const relationships = buildRigInfluenceRelationships(
      state.baselinePositions, state.indices, state.weights,
      state.influenceCount, rawNodes,
      Number.isFinite(radius) && radius > 0 ? radius : null, {});
    return {
      nodes: rawNodes, relationships,
      boundingSphereRadius: Number.isFinite(radius) && radius > 0 ? radius : null,
      evidenceMode,
      triangleCount: measure?.triangleCount || 0,
      validTriangleCount: measure?.validTriangleCount || 0,
      degenerateTriangleCount: measure?.degenerateTriangleCount || 0,
      invalidTriangleCount: measure?.invalidTriangleCount || 0,
      totalSurfaceArea: measure?.totalSurfaceArea || 0,
      measuredVertexCount: measure?.measuredVertexCount || 0,
      zeroMeasureVertexCount: measure?.zeroMeasureVertexCount || 0,
      fallbackReason: requestedEvidenceMode === 'surface'
        && evidenceMode === 'vertex' ? 'surface_evidence_unavailable'
        : requestedEvidenceMode === 'vertex' && measure
          && !measure.surfaceEvidenceAvailable
          ? 'surface_evidence_unavailable' : null,
    };
  }

  function ensureInfluenceGraph(mesh, state, evidenceMode = 'vertex',
      surfaceEvidence = null) {
    ensureRigMeshPrepared(mesh, state);
    if (!state.influenceGraph
        || state.influenceGraph.evidenceMode !== evidenceMode) {
      state.influenceGraph = buildInfluenceGraph(
        mesh, state, evidenceMode, surfaceEvidence);
    }
    return state.influenceGraph;
  }

  function ensureRigMeshPrepared(mesh, state) {
    if (!state?.loaded) return false;
    const position = mesh.geometry?.attributes?.position;
    if (!position) throw new Error('The selected mesh has no position data.');
    const normal = mesh.geometry?.attributes?.normal;
    if (!state.baselinePositions
        || state.baselinePositions.length !== position.array.length) {
      state.baselinePositions = new Float32Array(position.array);
    }
    if (normal && (!state.baselineNormals
        || state.baselineNormals.length !== normal.array.length)) {
      state.baselineNormals = new Float32Array(normal.array);
    } else if (!normal) {
      state.baselineNormals = null;
    }
    if (!state.originalMaterial) state.originalMaterial = mesh.material;
    if (!state.influenceNodes) {
      state.influenceNodes = buildRigInfluenceNodes(
        state.baselinePositions, state.indices, state.weights,
        state.influenceCount, state.boneIds);
    }
    if (!state.centerByBoneId) {
      state.centerByBoneId = new Map(state.influenceNodes.map(node => [
        node.boneId, node.weightedCenter]));
    }
    return true;
  }

  function normalizeWeightBoneStats(stats) {
    return Object.fromEntries(Object.entries(stats || {}).flatMap(
      ([rawBoneId, rawEntry]) => {
        const boneId = Number(rawBoneId);
        const affectedVertexCount = Number(
          rawEntry?.affectedVertexCount ?? rawEntry?.affected_vertex_count);
        const totalWeight = Number(
          rawEntry?.totalWeight ?? rawEntry?.total_weight);
        if (!Number.isFinite(boneId) || !Number.isFinite(affectedVertexCount)
            || affectedVertexCount < 0 || !Number.isFinite(totalWeight)) {
          return [];
        }
        return [[String(boneId), {affectedVertexCount, totalWeight}]];
      }));
  }

  function sourceDescriptorForEntry(entry) {
    const source = entry?.source;
    if (source && typeof source === 'object'
        && typeof source.key === 'string' && source.key
        && typeof source.file === 'string' && source.file) {
      const offset = Number(source.bone_id_offset);
      if (Number.isInteger(offset) && offset >= 0) {
        return {
          sourceKey: source.key, sourceFile: source.file,
          boneIdOffset: offset,
        };
      }
    }
    return null;
  }

  function installSkinningEntry(mesh, entry, buffer) {
    const state = stateFor(mesh);
    const source = sourceDescriptorForEntry(entry);
    if (!source) throw new Error(
      'The skin-weight source identity is unavailable for this draw.');
    state.skinningSourceKey = source.sourceKey;
    state.skinningSourceFile = source.sourceFile;
    state.skinningBoneOffset = source.boneIdOffset;
    modelWeightState.sourceDescriptors.set(source.sourceKey, source);
    const indices = typedView(buffer, entry.data?.indices, Uint32Array, 'u32');
    const weights = typedView(buffer, entry.data?.weights, Float32Array, 'f32');
    const position = mesh.geometry?.attributes?.position;
    const influenceCount = Number(entry.influence_count);
    if (!position || !Number.isInteger(influenceCount) || influenceCount <= 0
        || position.count !== Number(entry.vertex_count)
        || indices.length !== position.count * influenceCount
        || weights.length !== position.count * influenceCount) {
      throw new Error('Skin data does not match rendered vertices.');
    }
    state.originalMaterial = mesh.material;
    state.baselinePositions = null;
    state.baselineNormals = null;
    state.influenceNodes = null;
    state.influenceGraph = null;
    state.centerByBoneId = null;
    state.indices = indices;
    state.weights = weights;
    state.influenceCount = influenceCount;
    state.boneIds = Array.isArray(entry.bone_ids)
      ? [...entry.bone_ids].map(Number).filter(Number.isFinite)
        .sort((left, right) => left - right)
      : buildBoneIds(indices, weights, influenceCount);
    state.encoding = entry.encoding || null;
    state.diagnostics = entry.diagnostics || null;
    state.weightBoneStats = normalizeWeightBoneStats(entry.weight_stats);
    state.loaded = true;
    state.error = null;
    refreshSelectedWeightMask(mesh, state);
    return state;
  }

  function setModelWeightLoadError(error) {
    modelWeightState.error = error instanceof Error
      ? error.message : String(error);
    modelWeightState.loaded = false;
    modelWeightState.noWeights = false;
  }

  function loadModelWeights() {
    if (modelWeightState.loaded) return Promise.resolve(modelWeightSnapshot());
    if (modelWeightState.promise) return modelWeightState.promise;
    const generation = getGeneration();
    const deferPhysicsSync = () => {
      const readyGeneration = generation;
      const afterPaint = typeof requestAnimationFrame === 'function'
        ? requestAnimationFrame : callback => setTimeout(callback, 0);
      afterPaint(() => setTimeout(() => {
        if (readyGeneration !== getGeneration() || !modelWeightState.loaded) {
          return;
        }
        syncPhysicsToSelection();
      }, 0));
    };
    const meshes = [...knownMeshes].filter(eligibleSkinningMesh);
    if (!meshes.length) {
      modelWeightState.loaded = true;
      modelWeightState.noWeights = true;
      modelWeightState.savedSelectionApplied = true;
      refreshModelWeightSummary({refreshStats: true});
      deferPhysicsSync();
      notifyModelWeightChanged();
      return Promise.resolve(modelWeightSnapshot());
    }
    const folderPath = meshes[0].userData.modPath;
    modelWeightState.loading = true;
    modelWeightState.error = null;
    modelWeightState.noWeights = false;
    notifyModelWeightChanged();
    modelWeightState.promise = (async () => {
      const api = window.pywebview?.api?.get_model_skinning_preview;
      if (typeof api !== 'function') {
        throw new Error('Model skin-weight preview is unavailable.');
      }
      const preview = await api(folderPath);
      if (generation !== getGeneration()) return modelWeightSnapshot();
      modelWeightState.savedBonesBySource = selectionMapFromEntries(
        preview?.saved_bones);
      const bufferResponse = preview?.data?.url
        ? await fetch(preview.data.url, {cache: 'no-store'}) : null;
      if (bufferResponse && !bufferResponse.ok) {
        throw new Error(`Skin data download failed (${bufferResponse.status}).`);
      }
      const buffer = bufferResponse ? await bufferResponse.arrayBuffer() : null;
      if (buffer && buffer.byteLength !== Number(preview.data.length)) {
        throw new Error('Skin data download was incomplete.');
      }
      for (const mesh of meshes) {
        if (generation !== getGeneration() || !knownMeshes.has(mesh)) break;
        const state = stateFor(mesh);
        const entry = preview?.meshes?.[mesh.userData.semanticKey];
        if (!entry || entry.status !== 'ok') {
          state.error = entry?.error || 'No usable skin weights were returned.';
          state.loaded = false;
          continue;
        }
        try {
          installSkinningEntry(mesh, entry, buffer);
        } catch (error) {
          state.error = error instanceof Error ? error.message : String(error);
          state.loaded = false;
        }
      }
      if (generation !== getGeneration()) return modelWeightSnapshot();
      modelWeightState.loaded = true;
      refreshModelWeightSummary({refreshStats: true});
      if (!modelWeightState.savedSelectionApplied) {
        modelWeightState.savedSelectionApplied = true;
        setSelectedBones(sourceSelectionEntries(
          modelWeightState.savedBonesBySource), {syncPhysics: false});
        deferPhysicsSync();
      } else {
        deferPhysicsSync();
        notifyModelWeightChanged();
      }
      return modelWeightSnapshot();
    })();
    return modelWeightState.promise
      .catch(error => {
        if (generation === getGeneration()) {
          setModelWeightLoadError(error);
          refreshModelWeightSummary({refreshStats: true});
          notifyModelWeightChanged();
        }
        return modelWeightSnapshot();
      })
      .finally(() => {
        if (generation === getGeneration()) {
          modelWeightState.loading = false;
          modelWeightState.promise = null;
          notifyModelWeightChanged();
        }
      });
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
      const value = Math.max(0, Math.min(1,
        Number(selectedMask[vertex]) || 0));
      const offset = vertex * 3;
      colors[offset] = value;
      colors[offset + 1] = Math.min(1, value * 2);
      colors[offset + 2] = 1 - value;
    }
    mesh.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    mesh.geometry.attributes.color.needsUpdate = true;
    if (!state.debugMaterial) {
      state.debugMaterial = new THREE.MeshBasicMaterial({
        vertexColors: true, side: THREE.DoubleSide,
      });
    }
    mesh.material = state.debugMaterial;
  }

  function disableHeatmap(mesh, state) {
    state.heatmapMode = null;
    mesh.material = state.originalMaterial;
    mesh.geometry.deleteAttribute('color');
    if (state.debugMaterial) {
      state.debugMaterial.dispose();
      state.debugMaterial = null;
    }
  }

  function updateModelWeightHeatmap(changedSourceKeys = null) {
    knownMeshes.forEach(mesh => {
      const state = states.get(mesh);
      if (!state?.loaded) return;
      if (changedSourceKeys
          && !changedSourceKeys.has(state.skinningSourceKey)) return;
      const mask = state.selectedWeightMask;
      if (modelWeightState.heatmapEnabled && mask?.some(value => value > 0)) {
        state.heatmapMode = 'bone';
        updateHeatmap(mesh, state, mask);
      } else if (state.heatmapMode) {
        disableHeatmap(mesh, state);
      }
    });
  }

  function getSkinningBaseMaterial(mesh) {
    const state = states.get(mesh);
    return state ? (state.originalMaterial || mesh.material)
      : mesh?.material;
  }

  function withSkinningBaseMaterial(mesh, operation) {
    const state = states.get(mesh);
    if (!state) return operation();
    const heatmapActive = state.heatmapMode && state.debugMaterial
      && mesh.material === state.debugMaterial;
    const displayedMaterial = mesh.material;
    if (heatmapActive) mesh.material = state.originalMaterial || mesh.material;
    try {
      const result = operation();
      state.originalMaterial = mesh.material;
      return result;
    } finally {
      if (heatmapActive) mesh.material = displayedMaterial;
    }
  }

  function registerMesh(mesh) {
    if (!mesh) return;
    const wasKnown = knownMeshes.has(mesh);
    knownMeshes.add(mesh);
    if (!wasKnown) invalidateHumanoidDetection();
    if (!modelPhysicsSession.getState().enabled) return;
    const state = stateFor(mesh);
    if (!eligibleSkinningMesh(mesh)) {
      state.physicsParticipantStatus = 'unavailable';
      modelPhysicsSession.markUnavailable(mesh, 'skinning-unavailable');
      return;
    }
    if (state.loaded) syncPhysicsParticipants();
  }

  function unregisterMesh(mesh) {
    if (modelWeightState.pickedPoint?.meshKey
        && modelWeightState.pickedPoint.meshKey === mesh?.userData?.semanticKey) {
      clearPickedPoint();
    }
    const wasKnown = knownMeshes.delete(mesh);
    if (wasKnown) invalidateHumanoidDetection();
    const sourceKey = states.get(mesh)?.skinningSourceKey;
    if (sourceKey) {
      modelPhysicsSession.detach(sourceKey);
      sourcePhysicsRigs.delete(sourceKey);
      sourceSkinningRigs.delete(sourceKey);
    }
    refreshModelWeightSummary({refreshStats: true});
    if (sourceKey && modelPhysicsSession.getState().enabled) {
      syncPhysicsParticipants(new Set([sourceKey]));
    }
    if (modelRigState.loaded) {
      buildAllSourceSkinningRigs();
      buildModelSkinningRig();
      modelRigState.loaded = true;
    }
    notifyModelRigChanged();
    notifyModelWeightChanged();
  }

  function refreshAfterShapeChange(mesh) {
    const state = states.get(mesh);
    const position = mesh?.geometry?.attributes?.position;
    invalidateHumanoidDetection();
    clearPickedPoint();
    if (!state?.loaded || !position) return false;
    const preservedRootSignatures = new Set(
      modelRigState.explicitRootSignatures);
    const sourceKey = state.skinningSourceKey;
    const shapedPositions = new Float32Array(position.array);
    const normal = mesh.geometry.attributes.normal;
    const shapedNormals = normal ? new Float32Array(normal.array) : null;
    const participant = sourceKey
      ? modelPhysicsSession.getParticipant(sourceKey) : null;
    const wasPhysicsEnabled = !!participant || state.physicsEnabled;
    if (participant) modelPhysicsSession.detach(sourceKey);
    if (sourceKey) sourcePhysicsRigs.delete(sourceKey);
    if (sourceKey) sourceSkinningRigs.delete(sourceKey);
    if (getModelSkinningRig()) resetModelPose({request: false});
    invalidateModelRigLoad();
    modelRigState.promise = null;
    modelRigState.loading = false;
    state.poseTransforms = null;
    state.poseRotations = new Map();
    state.poseActiveVertices = null;
    state.combinedActiveVertices = null;
    state.combinedPoseVerticesRef = null;
    state.combinedPhysicsVerticesRef = null;
    modelRigState.loaded = false;
    setModelSkinningRig(null);
    modelRigState.selectedJointId = null;
    modelRigState.structureRevision = 0;
    modelRigState.jointPickIntent = null;
    modelRigState.ikEnabled = false;
    modelRigState.activeLimbRole = 'left_arm';
    modelRigState.selectedHumanoidControlKey = null;
    modelRigState.explicitRootSignatures = preservedRootSignatures;
    rigPresetState.lastApplyResult = null;
    modelRigState.pickStatus = '';
    notifyModelRigChanged();
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
    state.influenceNodes = null;
    state.centerByBoneId = null;
    ensureRigMeshPrepared(mesh, state);
    state.influenceGraph = null;
    if (wasPhysicsEnabled && modelPhysicsSession.getState().enabled
        && sourceKey) {
      syncPhysicsParticipants(new Set([sourceKey]));
      modelPhysicsSession.wake();
    }
    if (state.heatmapMode) updateModelWeightHeatmap(new Set([sourceKey]));
    return true;
  }

  function disposeMesh(mesh, {preserveRegistration = false} = {}) {
    const state = states.get(mesh);
    if (preserveRegistration) modelPhysicsSession.detach(mesh);
    else unregisterMesh(mesh);
    if (!state) return;
    state.disposed = true;
    if (state.debugMaterial) state.debugMaterial.dispose();
    mesh.geometry?.deleteAttribute?.('color');
    mesh.material = state.originalMaterial || mesh.material;
    states.delete(mesh);
  }

  function destroy() {
    modelPhysicsSession.destroy();
    knownMeshes.clear();
    resetModelState();
  }

  return {
    applyDeformation, buildInfluenceGraph, ensureInfluenceGraph,
    ensureRigMeshPrepared,
    finalizeDeformationGeometry, forEachRigMesh,
    getSkinningState: mesh => states.get(mesh) || null,
    getSkinningBaseMaterial, withSkinningBaseMaterial,
    installSkinningEntry, loadModelWeights, markFinalBoundsDirty,
    registerMesh, unregisterMesh, refreshAfterShapeChange,
    updateModelWeightHeatmap, disposeMesh, destroy,
  };
}

export function initializeSkinningRuntime(options) {
  activeRuntime = createSkinningRuntime(options);
  return activeRuntime;
}

function runtime() {
  if (!activeRuntime) throw new Error('Skinning runtime is not initialized.');
  return activeRuntime;
}

export function getSkinningState(mesh) { return runtime().getSkinningState(mesh); }
export function getSkinningBaseMaterial(mesh) {
  return runtime().getSkinningBaseMaterial(mesh);
}
export function withSkinningBaseMaterial(mesh, operation) {
  return runtime().withSkinningBaseMaterial(mesh, operation);
}
export function registerSkinningMesh(mesh) { return runtime().registerMesh(mesh); }
export function unregisterSkinningMesh(mesh) { return runtime().unregisterMesh(mesh); }
export function refreshSkinningAfterShapeChange(mesh) {
  return runtime().refreshAfterShapeChange(mesh);
}
export function disposeSkinningExperiment(mesh, options = {}) {
  return runtime().disposeMesh(mesh, options);
}
export function destroyModelPhysicsSession() { return runtime().destroy(); }
