// Owns the model-Rig loading and selection lifecycle. The implementation
// receives construction and sampling services from the composition root, but
// the asynchronous state transitions and user intents live here.

import {sourceBoneKey} from './weight-rig-reconcile.js';
import {
  aggregateInfluenceGraphs,
  buildInferredRigForest,
  hasUsableSurfaceTopology,
  inspectSurfaceTopology,
  jointPivotMap,
} from './weight-rig.js';

let activeSession = null;

function memberStructuralEvidenceFields(member = {}) {
  const state = member.state || {};
  const identity = member.mesh?.userData?.identity || {};
  const draw = identity.draw || {};
  const geometry = identity.geometry_state || {};
  const positionCount = member.mesh?.geometry?.attributes?.position?.count;
  const surfaceIndexCount = member.mesh?.geometry?.index?.count
    ?? member.mesh?.geometry?.index?.array?.length;
  return [
    state.skinningSourceKey ?? null,
    state.influenceCount ?? null,
    state.encoding ?? null,
    draw.count ?? null,
    draw.start ?? null,
    draw.base ?? null,
    geometry.ib_file ?? null,
    geometry.index_size ?? null,
    geometry.position_file ?? null,
    geometry.position_stride ?? null,
    geometry.texcoord_file ?? null,
    geometry.texcoord_stride ?? null,
    positionCount ?? null,
    surfaceIndexCount ?? null,
  ];
}

export function memberStructuralEvidenceKey(member = {}) {
  return JSON.stringify(memberStructuralEvidenceFields(member));
}

function memberCompatibilityEvidenceKey(member = {}) {
  // Some authored draw records share identical Rig evidence while their
  // provenance-only draw start differs. Keep that broader key as a candidate
  // only; memberEvidenceEqual remains the final identity decision.
  const fields = memberStructuralEvidenceFields(member);
  fields.splice(4, 1);
  return JSON.stringify(fields);
}

function createSourceSession({states, knownMeshes, modelWeightState,
    sourceSkinningRigs, ensureRigMeshPrepared, ensureInfluenceGraph,
    rebuildRestFrames,
    cloneForest} = {}) {
  function memberArraysEqual(left, right) {
    if (left === right) return true;
    if (!left || !right || left.length !== right.length) return false;
    for (let index = 0; index < left.length; index += 1) {
      if (!Object.is(left[index], right[index])) return false;
    }
    return true;
  }

  function memberEvidenceEqual(left, right) {
    const leftState = left.state;
    const rightState = right.state;
    if (leftState.influenceCount !== rightState.influenceCount) return false;
    const leftLengths = [
      leftState.baselinePositions,
      leftState.indices,
      leftState.weights,
      leftState.boneIds,
      left.surfaceIndices,
    ].map(values => values?.length ?? null);
    const rightLengths = [
      rightState.baselinePositions,
      rightState.indices,
      rightState.weights,
      rightState.boneIds,
      right.surfaceIndices,
    ].map(values => values?.length ?? null);
    if (leftLengths.some((length, index) => length !== rightLengths[index])) {
      return false;
    }
    return memberArraysEqual(leftState.boneIds, rightState.boneIds)
      && memberArraysEqual(left.surfaceIndices, right.surfaceIndices)
      && memberArraysEqual(leftState.indices, rightState.indices)
      && memberArraysEqual(leftState.weights, rightState.weights)
      && memberArraysEqual(leftState.baselinePositions,
        rightState.baselinePositions);
  }

  function normalizeMembers(members) {
    const structuralBuckets = new Map();
    const compatibilityBuckets = new Map();
    const uniqueMembers = [];
    for (const member of members) {
      const structuralKey = memberStructuralEvidenceKey(member);
      const bucket = structuralBuckets.get(structuralKey) || [];
      if (bucket.some(candidate => memberEvidenceEqual(candidate, member))) {
        continue;
      }
      const compatibilityKey = memberCompatibilityEvidenceKey(member);
      const compatibilityBucket = compatibilityBuckets.get(compatibilityKey)
        || [];
      if (compatibilityBucket.some(candidate =>
          memberEvidenceEqual(candidate, member))) continue;
      bucket.push(member);
      structuralBuckets.set(structuralKey, bucket);
      compatibilityBucket.push(member);
      compatibilityBuckets.set(compatibilityKey, compatibilityBucket);
      uniqueMembers.push(member);
    }
    return uniqueMembers;
  }

  function aggregateInfluenceGraph(members) {
    const loadedMembers = members.map(mesh => {
      const state = states.get(mesh);
      if (state?.loaded) ensureRigMeshPrepared?.(mesh, state);
      return state?.loaded ? {
        mesh,
        state,
        surfaceIndices: mesh.geometry?.index?.array || null,
      } : null;
    }).filter(Boolean);
    const uniqueMembers = normalizeMembers(loadedMembers);
    const surfaceEligible = uniqueMembers.map(member =>
      hasUsableSurfaceTopology(
        member.state.baselinePositions, member.surfaceIndices));
    const evidenceMode = surfaceEligible.every(Boolean) ? 'surface' : 'vertex';
    const graph = aggregateInfluenceGraphs(uniqueMembers.map(member => {
      const surfaceEvidence = evidenceMode === 'surface'
        ? {surfaceEvidenceAvailable: true}
        : inspectSurfaceTopology(
          member.state.baselinePositions, member.surfaceIndices);
      return ensureInfluenceGraph(
        member.mesh, member.state, evidenceMode, surfaceEvidence);
    }));
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
  return createSourceSession(options);
}

function createSession({state, modelWeightState, getGeneration,
    ensureModelWeightsLoaded, buildAllSourceSkinningRigs, buildModelSkinningRig,
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
    const promise = ensureModelWeightsLoaded()
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
export function setRigRotationSnapDegrees(value) {
  return session().setRotationSnapDegrees(value);
}
