// Owns the model-Rig loading and selection lifecycle. The implementation
// receives construction and sampling services from the composition root, but
// the asynchronous state transitions and user intents live here.

import {sourceBoneKey} from './weight-rig-reconcile.js';
import {
  aggregateInfluenceGraphs,
  buildInferredRigForest,
  inspectSurfaceTopology,
  jointPivotMap,
} from './weight-rig.js';

let activeSession = null;
let activeSourceSession = null;

function createSourceSession({states, knownMeshes, modelWeightState,
    sourceSkinningRigs, ensureInfluenceGraph, rebuildRestFrames,
    cloneForest} = {}) {
  function memberArrayFingerprint(values) {
    if (!values) return 'none';
    const bytes = ArrayBuffer.isView(values)
      ? new Uint8Array(values.buffer, values.byteOffset, values.byteLength)
      : null;
    if (!bytes) return `array:${values.length}:${[...values].join(',')}`;
    let hash = 2166136261;
    for (const byte of bytes) {
      hash ^= byte;
      hash = Math.imul(hash, 16777619);
    }
    return `${values.constructor.name}:${values.length}:${hash >>> 0}`;
  }

  function memberArraysEqual(left, right) {
    if (left === right) return true;
    if (!left || !right || left.length !== right.length) return false;
    for (let index = 0; index < left.length; index += 1) {
      if (!Object.is(left[index], right[index])) return false;
    }
    return true;
  }

  function memberEvidenceEqual(left, right) {
    return left.state.influenceCount === right.state.influenceCount
      && memberArraysEqual(left.state.baselinePositions,
        right.state.baselinePositions)
      && memberArraysEqual(left.state.indices, right.state.indices)
      && memberArraysEqual(left.state.weights, right.state.weights)
      && memberArraysEqual(left.state.boneIds, right.state.boneIds)
      && memberArraysEqual(left.surfaceIndices, right.surfaceIndices);
  }

  function normalizeMembers(members) {
    const buckets = new Map();
    const uniqueMembers = [];
    for (const member of members) {
      const state = member.state;
      const fingerprint = [
        state.influenceCount,
        memberArrayFingerprint(state.baselinePositions),
        memberArrayFingerprint(state.indices),
        memberArrayFingerprint(state.weights),
        memberArrayFingerprint(state.boneIds),
        memberArrayFingerprint(member.surfaceIndices),
      ].join('|');
      const bucket = buckets.get(fingerprint) || [];
      if (bucket.some(candidate => memberEvidenceEqual(candidate, member))) {
        continue;
      }
      bucket.push(member);
      buckets.set(fingerprint, bucket);
      uniqueMembers.push(member);
    }
    return uniqueMembers;
  }

  function aggregateInfluenceGraph(members) {
    const loadedMembers = members.map(mesh => {
      const state = states.get(mesh);
      return state?.loaded ? {
        mesh,
        state,
        surfaceIndices: mesh.geometry?.index?.array || null,
        surfaceEvidence: inspectSurfaceTopology(
          state.baselinePositions, mesh.geometry?.index?.array || null),
      } : null;
    }).filter(Boolean);
    const uniqueMembers = normalizeMembers(loadedMembers);
    const evidenceMode = loadedMembers.every(member =>
      member.surfaceEvidence.surfaceEvidenceAvailable) ? 'surface' : 'vertex';
    const graph = aggregateInfluenceGraphs(uniqueMembers.map(member =>
      ensureInfluenceGraph(member.mesh, member.state, evidenceMode,
        member.surfaceEvidence)));
    return {
      ...graph,
      memberCount: loadedMembers.length,
      uniqueMemberCount: uniqueMembers.length,
    };
  }

  function createSourceSkinningRig(sourceKey, members) {
    const descriptor = modelWeightState.sourceDescriptors.get(sourceKey);
    const influenceGraph = aggregateInfluenceGraph(members);
    const inferredForest = buildInferredRigForest(influenceGraph);
    const jointPivotByBoneId = jointPivotMap(
      inferredForest, influenceGraph.relationships);
    const rig = {
      key: sourceKey,
      sourceKey,
      sourceFile: descriptor?.sourceFile || '',
      boneIdOffset: descriptor?.boneIdOffset ?? 0,
      meshes: new Set(members),
      influenceGraph,
      boneIds: (influenceGraph.nodes || []).map(node => node.boneId),
      centerByBoneId: new Map((influenceGraph.nodes || []).map(node => [
        node.boneId, node.weightedCenter])),
      inferredForest,
      jointPivotByBoneId,
      restFrameByBoneId: new Map(),
      restDirectionByBoneId: new Map(),
      restFrameEvidenceByBoneId: new Map(),
      continuationChildByBoneId: new Map(),
      vertexEvidence: members.map(mesh => {
        const state = states.get(mesh);
        return state?.loaded ? {
          meshKey: mesh.userData?.semanticKey || '',
          positions: state.baselinePositions,
          indices: state.indices,
          weights: state.weights,
          influenceCount: state.influenceCount,
        } : null;
      }).filter(Boolean),
      structureRevision: 0,
      poseRotationByBoneId: new Map(),
      poseTransforms: new Map(),
      poseRotations: new Map(),
      poseTransformCache: new Map(),
      poseFrameCache: new Map(),
      poseRootOverrides: new Map(),
      physicsRig: null,
    };
    rebuildRestFrames(rig);
    rig.defaultInferredForest = cloneForest(rig.inferredForest);
    rig.defaultJointPivotByBoneId = new Map([...rig.jointPivotByBoneId]
      .map(([boneId, pivot]) => [boneId, [...pivot]]));
    return rig;
  }

  function resetPose(rig) {
    rig.poseRotationByBoneId.clear();
    rig.poseTransforms.clear();
    rig.poseRotations.clear();
    rig.poseTransformCache.clear();
    rig.poseFrameCache.clear();
  }

  function sameMeshSet(left, right) {
    return left?.size === right?.length
      && right.every(mesh => left.has(mesh));
  }

  function refresh(rig, members, {resetPose: shouldReset = true} = {}) {
    const refreshed = createSourceSkinningRig(rig.sourceKey, members);
    rig.meshes = refreshed.meshes;
    rig.influenceGraph = refreshed.influenceGraph;
    rig.boneIds = refreshed.boneIds;
    rig.centerByBoneId = refreshed.centerByBoneId;
    rig.inferredForest = refreshed.inferredForest;
    rig.jointPivotByBoneId = refreshed.jointPivotByBoneId;
    rig.defaultInferredForest = cloneForest(refreshed.defaultInferredForest);
    rig.defaultJointPivotByBoneId = new Map(
      [...refreshed.defaultJointPivotByBoneId]
        .map(([boneId, pivot]) => [boneId, [...pivot]]));
    rig.restFrameByBoneId = refreshed.restFrameByBoneId;
    rig.restDirectionByBoneId = refreshed.restDirectionByBoneId;
    rig.restFrameEvidenceByBoneId = refreshed.restFrameEvidenceByBoneId;
    rig.continuationChildByBoneId = refreshed.continuationChildByBoneId;
    rig.vertexEvidence = refreshed.vertexEvidence;
    rig.structureRevision = refreshed.structureRevision;
    rig.poseFrameCache = refreshed.poseFrameCache;
    rig.poseRootOverrides = refreshed.poseRootOverrides;
    if (shouldReset) resetPose(rig);
    return rig;
  }

  function ensure(sourceKey, members) {
    let rig = sourceSkinningRigs.get(sourceKey);
    if (!rig) {
      rig = createSourceSkinningRig(sourceKey, members);
      sourceSkinningRigs.set(sourceKey, rig);
    } else if (!sameMeshSet(rig.meshes, members)) {
      refresh(rig, members);
    }
    return rig;
  }

  function buildAll() {
    const groups = new Map();
    for (const mesh of knownMeshes) {
      const state = states.get(mesh);
      if (!state?.loaded || !state.skinningSourceKey) continue;
      const members = groups.get(state.skinningSourceKey) || [];
      members.push(mesh);
      groups.set(state.skinningSourceKey, members);
    }
    for (const [sourceKey, members] of groups) ensure(sourceKey, members);
    for (const sourceKey of [...sourceSkinningRigs.keys()]) {
      if (!groups.has(sourceKey)) sourceSkinningRigs.delete(sourceKey);
    }
    return [...sourceSkinningRigs.values()];
  }

  return {ensure, buildAll, resetPose};
}

export function initializeRigSourceSession(options) {
  activeSourceSession = createSourceSession(options);
  return activeSourceSession;
}

function sourceSession() {
  if (!activeSourceSession) {
    throw new Error('Rig source session is not initialized.');
  }
  return activeSourceSession;
}

export function ensureRigSourceSkinningRig(sourceKey, members) {
  return sourceSession().ensure(sourceKey, members);
}
export function buildAllRigSourceSkinningRigs() {
  return sourceSession().buildAll();
}
export function resetRigSourceSkinningPose(rig) {
  return sourceSession().resetPose(rig);
}

function createSession({state, modelWeightState, getGeneration,
    loadModelWeights, buildAllSourceSkinningRigs, buildModelSkinningRig,
    getSnapshot, notifyChanged, requestRender, cancelWeightPicking,
    pickFromSurface, getModelJointId, rotationSnapValues} = {}) {
  let loadToken = null;

  function getState() { return getSnapshot(); }

  function modelJointFromSkinningSample(sampled) {
    if (!sampled?.sourceKey || !Array.isArray(sampled.influences)) return null;
    const jointScores = new Map();
    for (const influence of sampled.influences) {
      const boneId = Number(influence?.boneId);
      const weight = Number(influence?.weight);
      const jointId = getModelJointId?.(sampled.sourceKey, boneId)
        ?? getSnapshot()?.model?.sourceBoneToModelJointId?.get(
          sourceBoneKey(sampled.sourceKey, boneId));
      if (!Number.isInteger(boneId) || !Number.isFinite(weight)
          || weight <= 0 || !Number.isInteger(jointId)) continue;
      const current = jointScores.get(jointId);
      if (!current) {
        jointScores.set(jointId, {weight, boneId, boneWeight: weight});
        continue;
      }
      current.weight += weight;
      if (weight > current.boneWeight
          || (weight === current.boneWeight && boneId < current.boneId)) {
        current.boneId = boneId;
        current.boneWeight = weight;
      }
    }
    const best = [...jointScores.entries()].sort((left, right) =>
      right[1].weight - left[1].weight || left[0] - right[0])[0];
    if (!best) return null;
    return {
      point: sampled.point,
      jointId: best[0],
      sourceKey: sampled.sourceKey,
      sourceFile: sampled.sourceFile,
      boneIdOffset: sampled.boneIdOffset,
      boneId: best[1].boneId,
      influences: sampled.influences,
    };
  }

  function invalidateLoad() {
    loadToken = null;
    state.promise = null;
    state.loading = false;
  }

  function ensureLoaded() {
    if (state.loaded) return Promise.resolve(getSnapshot());
    if (state.promise) return state.promise;
    const generation = getGeneration();
    const token = {};
    loadToken = token;
    state.loading = true;
    state.error = null;
    state.pickStatus = '';
    notifyChanged();
    const promise = loadModelWeights()
      .then(() => {
        if (generation !== getGeneration() || loadToken !== token) {
          return getSnapshot();
        }
        if (modelWeightState.error) throw new Error(modelWeightState.error);
        const sourceRigs = buildAllSourceSkinningRigs();
        buildModelSkinningRig(sourceRigs);
        state.loaded = true;
        return getSnapshot();
      })
      .catch(error => {
        if (generation !== getGeneration() || loadToken !== token) {
          return getSnapshot();
        }
        state.error = error instanceof Error ? error.message : String(error);
        state.loaded = false;
        return getSnapshot();
      })
      .finally(() => {
        if (generation !== getGeneration() || loadToken !== token) return;
        state.loading = false;
        state.promise = null;
        loadToken = null;
        notifyChanged();
      });
    state.promise = promise;
    return promise;
  }

  function beginJointPicking(intent = {}) {
    const next = String(intent?.type || '') === 'selected-joint'
      ? {type: 'selected-joint'} : null;
    if (!next || !state.loaded) return false;
    if (state.jointPickIntent && state.jointPickIntent.type === next.type) {
      return cancelJointPicking();
    }
    cancelWeightPicking?.();
    state.jointPickIntent = next;
    state.pickStatus = 'Pick a Rig joint.';
    notifyChanged();
    requestRender();
    return true;
  }

  function cancelJointPicking() {
    if (!state.jointPickIntent && !state.pickStatus) return false;
    state.jointPickIntent = null;
    state.pickStatus = '';
    notifyChanged();
    requestRender();
    return true;
  }

  function selectJoint(jointId) {
    const id = Number(jointId);
    const rig = getSnapshot()?.model;
    if (!rig?.joints?.some(joint => Number(joint.jointId) === id)) return false;
    state.selectedJointId = id;
    state.pickStatus = '';
    notifyChanged();
    requestRender();
    return true;
  }

  function handleJointPicked(jointId, intent = state.jointPickIntent) {
    if (String(intent?.type || '') !== 'selected-joint') return false;
    const changed = selectJoint(jointId);
    if (changed) cancelJointPicking();
    return changed;
  }

  function pickJointFromSurface(point, intent = state.jointPickIntent) {
    const next = String(intent?.type || '') === 'selected-joint'
      ? {type: 'selected-joint'} : null;
    if (!next) return false;
    const jointId = pickFromSurface?.(point, next);
    if (!Number.isInteger(Number(jointId))) {
      state.pickStatus = 'No usable Rig joint was found at this point.';
      notifyChanged();
      requestRender();
      return false;
    }
    return handleJointPicked(Number(jointId), next);
  }

  function setRotationSnapDegrees(value) {
    const degrees = Number(value);
    const next = rotationSnapValues.includes(degrees) ? degrees : 0;
    if (next === state.rotationSnapDegrees) return next;
    state.rotationSnapDegrees = next;
    notifyChanged();
    requestRender();
    return next;
  }

  return {
    getState, invalidateLoad, ensureLoaded,
    beginJointPicking, cancelJointPicking, handleJointPicked,
    pickJointFromSurface, selectJoint,
    modelJointFromSkinningSample,
    clearJointSelection() {
      const hadSelection = state.selectedJointId !== null
        && state.selectedJointId !== undefined;
      const hadStatus = !!state.pickStatus;
      state.selectedJointId = null;
      state.pickStatus = '';
      if (!hadSelection && !hadStatus) return false;
      notifyChanged();
      requestRender();
      return true;
    },
    getRotationSnapDegrees: () => state.rotationSnapDegrees,
    setRotationSnapDegrees,
  };
}

export function initializeRigModelSession(options) {
  activeSession = createSession(options);
  return activeSession;
}

function session() {
  if (!activeSession) throw new Error('Rig model session is not initialized.');
  return activeSession;
}

export function getModelRigState() { return session().getState(); }
export function ensureModelRigLoaded() { return session().ensureLoaded(); }
export function beginRigJointPicking(intent = {}) {
  return session().beginJointPicking(intent);
}
export function cancelRigJointPicking() { return session().cancelJointPicking(); }
export function handleRigJointPicked(jointId, intent) {
  return session().handleJointPicked(jointId, intent);
}
export function pickRigJointFromModelSurface(point, intent) {
  return session().pickJointFromSurface(point, intent);
}
export function modelJointFromSkinningSample(sampled) {
  return session().modelJointFromSkinningSample(sampled);
}
export function selectRigJoint(jointId) { return session().selectJoint(jointId); }
export function clearRigJointSelection() { return session().clearJointSelection(); }
export function getRigRotationSnapDegrees() {
  return session().getRotationSnapDegrees();
}
export function setRigRotationSnapDegrees(value) {
  return session().setRotationSnapDegrees(value);
}
