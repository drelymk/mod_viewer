// Owns the model-Rig loading and selection lifecycle. The implementation
// receives construction and sampling services from the composition root, but
// the asynchronous state transitions and user intents live here.

import {sourceBoneKey} from './weight-rig-reconcile.js';
import {
  aggregateInfluenceGraphs,
  buildInferredRigForest,
  hasUsableSurfaceTopology,
  inspectSurfaceTopology,
  inspectSurfaceTopologyCooperative,
  jointPivotMap,
} from './weight-rig.js';
import {createWorkBudget} from './cooperative-scheduler.js';

function clockNow() {
  return typeof globalThis.performance?.now === 'function'
    ? globalThis.performance.now() : Date.now();
}

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

function memberEvidenceShapeKey(member = {}) {
  const state = member.state || {};
  return JSON.stringify([
    state.influenceCount ?? null,
    state.baselinePositions?.length ?? null,
    state.indices?.length ?? null,
    state.weights?.length ?? null,
    state.boneIds?.length ?? null,
    member.surfaceIndices?.length ?? null,
  ]);
}

function createSourceSession({states, knownMeshes, modelWeightState,
    sourceSkinningRigs, ensureRigMeshPrepared,
    ensureRigMeshPreparedCooperative = ensureRigMeshPrepared,
    ensureInfluenceGraph, ensureInfluenceGraphCooperative = ensureInfluenceGraph,
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
    const evidenceBuckets = new Map();
    const uniqueMembers = [];
    for (const member of members) {
      // Provenance identifies a draw, but it is not part of Rig evidence
      // equality.  Bucket only by cheap evidence shape, then retain the
      // exact array comparison as the authoritative duplicate check.
      const key = memberEvidenceShapeKey(member);
      const bucket = evidenceBuckets.get(key) || [];
      if (bucket.some(candidate => memberEvidenceEqual(candidate, member))) {
        continue;
      }
      bucket.push(member);
      evidenceBuckets.set(key, bucket);
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

  async function memberArraysEqualCooperative(left, right, budget, isCurrent) {
    if (left === right) return true;
    if (!left || !right || left.length !== right.length) return false;
    for (let index = 0; index < left.length; index += 1) {
      if (!Object.is(left[index], right[index])) return false;
      if ((index & 2047) === 0) {
        if (!isCurrent()) return null;
        await budget.checkpoint();
        if (!isCurrent()) return null;
      }
    }
    return true;
  }

  async function memberEvidenceEqualCooperative(left, right, budget,
      isCurrent) {
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
    for (const [leftValues, rightValues] of [
      [leftState.boneIds, rightState.boneIds],
      [left.surfaceIndices, right.surfaceIndices],
      [leftState.indices, rightState.indices],
      [leftState.weights, rightState.weights],
      [leftState.baselinePositions, rightState.baselinePositions],
    ]) {
      const equal = await memberArraysEqualCooperative(
        leftValues, rightValues, budget, isCurrent);
      if (equal === null) return null;
      if (!equal) return false;
    }
    return isCurrent() ? true : null;
  }

  async function normalizeMembersCooperative(members, budget, isCurrent) {
    const evidenceBuckets = new Map();
    const uniqueMembers = [];
    for (const member of members) {
      if (!isCurrent()) return null;
      const key = memberEvidenceShapeKey(member);
      const bucket = evidenceBuckets.get(key) || [];
      let duplicate = false;
      for (const candidate of bucket) {
        const equal = await memberEvidenceEqualCooperative(
          candidate, member, budget, isCurrent);
        if (equal === null) return null;
        if (equal) {
          duplicate = true;
          break;
        }
      }
      if (!duplicate) {
        bucket.push(member);
        evidenceBuckets.set(key, bucket);
        uniqueMembers.push(member);
      }
      await budget.checkpoint();
    }
    return uniqueMembers;
  }

  async function aggregateInfluenceGraphCooperative(members, {
      budget, isCurrent = () => true,
  }) {
    const timings = {
      sourceKey: String(states.get(members[0])?.skinningSourceKey || ''),
      meshCount: 0,
      vertexCount: 0,
      triangleCount: 0,
      meshPreparationMs: 0,
      topologyMs: 0,
      surfaceGraphMs: 0,
      vertexGraphMs: 0,
    };
    const loadedMembers = [];
    for (const mesh of members) {
      if (!isCurrent()) return null;
      const state = states.get(mesh);
      if (!state?.loaded) continue;
      const preparationStartedAt = clockNow();
      if (!(await ensureRigMeshPreparedCooperative(mesh, state,
        {budget, isCurrent}))) return null;
      timings.meshPreparationMs += clockNow() - preparationStartedAt;
      loadedMembers.push({
        mesh,
        state,
        surfaceIndices: mesh.geometry?.index?.array || null,
      });
      await budget.checkpoint();
    }
    const uniqueMembers = await normalizeMembersCooperative(
      loadedMembers, budget, isCurrent);
    if (!uniqueMembers) return null;
    timings.meshCount = loadedMembers.length;
    timings.vertexCount = uniqueMembers.reduce((sum, member) => sum +
      Math.floor((member.state.baselinePositions?.length || 0) / 3), 0);
    const surfaceEvidenceByMesh = new Map();
    let surfaceEligible = true;
    for (const member of uniqueMembers) {
      const topologyStartedAt = clockNow();
      const measure = await inspectSurfaceTopologyCooperative(
        member.state.baselinePositions, member.surfaceIndices,
        {budget, isCurrent});
      if (!measure) return null;
      timings.topologyMs += clockNow() - topologyStartedAt;
      surfaceEvidenceByMesh.set(member.mesh, measure);
      timings.triangleCount += measure.triangleCount;
      surfaceEligible = surfaceEligible && measure.surfaceEvidenceAvailable;
    }
    const evidenceMode = surfaceEligible ? 'surface' : 'vertex';
    const graphs = [];
    for (const member of uniqueMembers) {
      if (!isCurrent()) return null;
      const surfaceEvidence = surfaceEvidenceByMesh.get(member.mesh);
      const graphStartedAt = clockNow();
      const graph = await ensureInfluenceGraphCooperative(
        member.mesh, member.state, evidenceMode,
        evidenceMode === 'surface' ? surfaceEvidence : surfaceEvidence,
        {budget, isCurrent});
      if (!graph) return null;
      if (evidenceMode === 'surface') {
        timings.surfaceGraphMs += clockNow() - graphStartedAt;
      } else {
        timings.vertexGraphMs += clockNow() - graphStartedAt;
      }
      graphs.push(graph);
      await budget.checkpoint();
    }
    const graph = aggregateInfluenceGraphs(graphs);
    return {
      ...graph,
      memberCount: loadedMembers.length,
      uniqueMemberCount: uniqueMembers.length,
      __cooperativeTimings: timings,
    };
  }

  function assembleSourceSkinningRig(sourceKey, members, influenceGraph,
      inferredForest, {finalize = true} = {}) {
    const descriptor = modelWeightState.sourceDescriptors.get(sourceKey);
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
    if (finalize) {
      rebuildRestFrames(rig);
      rig.defaultInferredForest = cloneForest(rig.inferredForest);
      rig.defaultJointPivotByBoneId = new Map([...rig.jointPivotByBoneId]
        .map(([boneId, pivot]) => [boneId, [...pivot]]));
    }
    return rig;
  }

  function createSourceSkinningRig(sourceKey, members) {
    const influenceGraph = aggregateInfluenceGraph(members);
    const forestStartedAt = clockNow();
    const inferredForest = buildInferredRigForest(influenceGraph);
    if (influenceGraph.__cooperativeTimings) {
      influenceGraph.__cooperativeTimings.inferredForestMs =
        clockNow() - forestStartedAt;
    }
    return assembleSourceSkinningRig(
      sourceKey, members, influenceGraph, inferredForest);
  }

  async function createSourceSkinningRigCooperative(sourceKey, members,
      budget, isCurrent = () => true) {
    const influenceGraph = await aggregateInfluenceGraphCooperative(members, {
      budget, isCurrent,
    });
    if (!influenceGraph || !isCurrent()) return null;
    const forestStartedAt = clockNow();
    const inferredForest = buildInferredRigForest(influenceGraph);
    if (influenceGraph.__cooperativeTimings) {
      influenceGraph.__cooperativeTimings.inferredForestMs =
        clockNow() - forestStartedAt;
    }
    await budget.checkpoint();
    if (!isCurrent()) return null;
    const rig = assembleSourceSkinningRig(
      sourceKey, members, influenceGraph, inferredForest, {finalize: false});
    await budget.checkpoint();
    if (!isCurrent()) return null;
    const restFrameStartedAt = clockNow();
    rebuildRestFrames(rig);
    if (influenceGraph.__cooperativeTimings) {
      influenceGraph.__cooperativeTimings.restFrameMs =
        clockNow() - restFrameStartedAt;
    }
    await budget.checkpoint();
    if (!isCurrent()) return null;
    rig.defaultInferredForest = cloneForest(rig.inferredForest);
    rig.defaultJointPivotByBoneId = new Map([...rig.jointPivotByBoneId]
      .map(([boneId, pivot]) => [boneId, [...pivot]]));
    rig.__cooperativeStats = budget.getStats();
    rig.__cooperativeTimings = influenceGraph.__cooperativeTimings || null;
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

  const inFlight = new Map();

  async function ensureCooperative(sourceKey, members, {
      generation = null, isCurrent = () => true,
  } = {}) {
    const current = () => generation === null
      || generation === undefined || isCurrent();
    if (!current()) return null;
    const existing = sourceSkinningRigs.get(sourceKey);
    if (existing && sameMeshSet(existing.meshes, members)) return existing;
    if (inFlight.has(sourceKey)) {
      const pending = await inFlight.get(sourceKey);
      if (!current()) return null;
      if (pending && sameMeshSet(pending.meshes, members)) return pending;
    }
    const budget = createWorkBudget();
    const promise = (async () => {
      await budget.checkpoint();
      if (!current()) return null;
      const rig = await createSourceSkinningRigCooperative(
        sourceKey, members, budget, current);
      if (!current()) return null;
      sourceSkinningRigs.set(sourceKey, rig);
      return rig;
    })();
    inFlight.set(sourceKey, promise);
    try {
      return await promise;
    } finally {
      if (inFlight.get(sourceKey) === promise) inFlight.delete(sourceKey);
    }
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

  async function buildAllCooperative({generation = null,
      isCurrent = () => true} = {}) {
    const groups = new Map();
    for (const mesh of knownMeshes) {
      const state = states.get(mesh);
      if (!state?.loaded || !state.skinningSourceKey) continue;
      const members = groups.get(state.skinningSourceKey) || [];
      members.push(mesh);
      groups.set(state.skinningSourceKey, members);
    }
    const budget = createWorkBudget();
    for (const [sourceKey, members] of groups) {
      if (generation !== null && !isCurrent()) return null;
      await ensureCooperative(sourceKey, members, {generation, isCurrent});
      await budget.checkpoint();
    }
    if (generation !== null && !isCurrent()) return null;
    for (const sourceKey of [...sourceSkinningRigs.keys()]) {
      if (!groups.has(sourceKey)) sourceSkinningRigs.delete(sourceKey);
    }
    return [...sourceSkinningRigs.values()];
  }

  return {ensure, ensureCooperative, buildAll, buildAllCooperative, resetPose,
    reset() { inFlight.clear(); }};
}

export function initializeRigSourceSession(options) {
  return createSourceSession(options);
}

function createSession({state, modelWeightState, getGeneration,
    ensureModelWeightsLoaded, buildAllSourceSkinningRigs,
    buildAllSourceSkinningRigsCooperatively = buildAllSourceSkinningRigs,
    buildModelSkinningRig,
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
      .then(async () => {
        if (generation !== getGeneration() || loadToken !== token) {
          return getSnapshot();
        }
        if (modelWeightState.error) throw new Error(modelWeightState.error);
        const sourceRigs = await buildAllSourceSkinningRigsCooperatively({
          generation,
          isCurrent: () => generation === getGeneration() && loadToken === token,
        });
        if (!sourceRigs || generation !== getGeneration()
            || loadToken !== token) return getSnapshot();
        const built = await buildModelSkinningRig(sourceRigs, {
          generation,
          isCurrent: () => generation === getGeneration() && loadToken === token,
        });
        if (!built || generation !== getGeneration() || loadToken !== token) {
          return getSnapshot();
        }
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
