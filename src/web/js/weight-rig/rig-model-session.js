// Owns the model-Rig loading and selection lifecycle. The implementation
// receives construction and sampling services from the composition root, but
// the asynchronous state transitions and user intents live here.

import { aggregateInfluenceGraphs, buildInferredRigForest, jointPivotMap } from './weight-rig.js';
import { createWorkBudget } from './cooperative-scheduler.js';
import { weightRigStatus } from './weight-rig-status.js';

function clockNow() {
  return typeof globalThis.performance?.now === 'function' ? globalThis.performance.now() : Date.now();
}

function memberEvidenceArrays(member = {}) {
  const state = member.state || {};
  return [state.boneIds, member.surfaceIndices, state.indices, state.weights, state.baselinePositions];
}

function memberEvidenceShapeKey(member = {}) {
  const state = member.state || {};
  return JSON.stringify([
    state.influenceCount ?? null,
    ...memberEvidenceArrays(member).map((values) => values?.length ?? null),
  ]);
}

function preparedMember(mesh, state) {
  return {
    mesh,
    state,
    surfaceIndices: mesh.geometry?.index?.array || null,
  };
}

function finalizeInfluenceGraph(graph, loadedMembers, uniqueMembers) {
  return {
    ...graph,
    memberCount: loadedMembers.length,
    uniqueMemberCount: uniqueMembers.length,
  };
}

export function createRigSourceSession({
  states,
  knownMeshes,
  modelWeightState,
  sourceSkinningRigs,
  ensureRigMeshPrepared,
  ensureInfluenceGraphCooperative,
  rebuildRestFrames,
  cloneForest,
} = {}) {
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

  async function memberEvidenceEqualCooperative(left, right, budget, isCurrent) {
    const leftState = left.state;
    const rightState = right.state;
    if (leftState.influenceCount !== rightState.influenceCount) return false;
    const leftArrays = memberEvidenceArrays(left);
    const rightArrays = memberEvidenceArrays(right);
    const leftLengths = leftArrays.map((values) => values?.length ?? null);
    const rightLengths = rightArrays.map((values) => values?.length ?? null);
    if (leftLengths.some((length, index) => length !== rightLengths[index])) {
      return false;
    }
    for (let index = 0; index < leftArrays.length; index += 1) {
      const equal = await memberArraysEqualCooperative(leftArrays[index], rightArrays[index], budget, isCurrent);
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
        const equal = await memberEvidenceEqualCooperative(candidate, member, budget, isCurrent);
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

  async function aggregateInfluenceGraphCooperative(members, { budget, isCurrent = () => true }) {
    const timings = {
      sourceKey: String(states.get(members[0])?.skinningSourceKey || ''),
      meshCount: 0,
      vertexCount: 0,
      triangleCount: 0,
      meshPreparationMs: 0,
      surfaceGraphMs: 0,
    };
    const loadedMembers = [];
    for (const mesh of members) {
      if (!isCurrent()) return null;
      const state = states.get(mesh);
      if (!state?.loaded) continue;
      const preparationStartedAt = clockNow();
      if (!ensureRigMeshPrepared(mesh, state)) return null;
      timings.meshPreparationMs += clockNow() - preparationStartedAt;
      loadedMembers.push(preparedMember(mesh, state));
      await budget.checkpoint();
    }
    const uniqueMembers = await normalizeMembersCooperative(loadedMembers, budget, isCurrent);
    if (!uniqueMembers) return null;
    timings.meshCount = loadedMembers.length;
    timings.vertexCount = uniqueMembers.reduce(
      (sum, member) => sum + Math.floor((member.state.baselinePositions?.length || 0) / 3),
      0,
    );
    const graphs = [];
    for (const member of uniqueMembers) {
      if (!isCurrent()) return null;
      const graphStartedAt = clockNow();
      const graph = await ensureInfluenceGraphCooperative(member.mesh, member.state, {
        budget,
        isCurrent,
      });
      if (!graph) return null;
      if (!isCurrent()) return null;
      if (!(graph.validTriangleCount > 0 && graph.totalSurfaceArea > 0)) {
        const error = new Error('Rig requires usable triangle geometry for every source member.');
        error.code = 'rig_surface_unavailable';
        throw error;
      }
      timings.surfaceGraphMs += clockNow() - graphStartedAt;
      timings.triangleCount += graph.triangleCount;
      graphs.push(graph);
      await budget.checkpoint();
    }
    const graph = aggregateInfluenceGraphs(graphs);
    return {
      ...finalizeInfluenceGraph(graph, loadedMembers, uniqueMembers),
      __cooperativeTimings: timings,
    };
  }

  function assembleSourceSkinningRig(sourceKey, members, influenceGraph, inferredForest) {
    const descriptor = modelWeightState.sourceDescriptors.get(sourceKey);
    const jointPivotByBoneId = jointPivotMap(inferredForest, influenceGraph.relationships);
    const rig = {
      key: sourceKey,
      sourceKey,
      sourceFile: descriptor?.sourceFile || '',
      boneIdOffset: descriptor?.boneIdOffset ?? 0,
      boneIdsModelWide: descriptor?.boneIdsModelWide === true,
      meshes: new Set(members),
      influenceGraph,
      boneIds: (influenceGraph.nodes || []).map((node) => node.boneId),
      centerByBoneId: new Map((influenceGraph.nodes || []).map((node) => [node.boneId, node.weightedCenter])),
      inferredForest,
      jointPivotByBoneId,
      restFrameByBoneId: new Map(),
      restDirectionByBoneId: new Map(),
      restFrameEvidenceByBoneId: new Map(),
      continuationChildByBoneId: new Map(),
      vertexEvidence: members
        .map((mesh) => {
          const state = states.get(mesh);
          return state?.loaded
            ? {
                meshKey: mesh.userData?.semanticKey || '',
                positions: state.baselinePositions,
                indices: state.indices,
                weights: state.weights,
                influenceCount: state.influenceCount,
              }
            : null;
        })
        .filter(Boolean),
      structureRevision: 0,
      poseRotationByBoneId: new Map(),
      poseTransforms: new Map(),
      poseRotations: new Map(),
      poseTransformCache: new Map(),
      poseFrameCache: new Map(),
      poseRootOverrides: new Map(),
      physicsRig: null,
    };
    return rig;
  }

  async function createSourceSkinningRigCooperative(sourceKey, members, budget, isCurrent = () => true) {
    const influenceGraph = await aggregateInfluenceGraphCooperative(members, {
      budget,
      isCurrent,
    });
    if (!influenceGraph || !isCurrent()) return null;
    const forestStartedAt = clockNow();
    const inferredForest = buildInferredRigForest(influenceGraph);
    if (influenceGraph.__cooperativeTimings) {
      influenceGraph.__cooperativeTimings.inferredForestMs = clockNow() - forestStartedAt;
    }
    await budget.checkpoint();
    if (!isCurrent()) return null;
    const rig = assembleSourceSkinningRig(sourceKey, members, influenceGraph, inferredForest);
    await budget.checkpoint();
    if (!isCurrent()) return null;
    const restFrameStartedAt = clockNow();
    rebuildRestFrames(rig);
    if (influenceGraph.__cooperativeTimings) {
      influenceGraph.__cooperativeTimings.restFrameMs = clockNow() - restFrameStartedAt;
    }
    await budget.checkpoint();
    if (!isCurrent()) return null;
    rig.defaultInferredForest = cloneForest(rig.inferredForest);
    rig.defaultJointPivotByBoneId = new Map([...rig.jointPivotByBoneId].map(([boneId, pivot]) => [boneId, [...pivot]]));
    rig.__cooperativeStats = budget.getStats();
    rig.__cooperativeTimings = influenceGraph.__cooperativeTimings || null;
    return rig;
  }

  function sameMeshSet(left, right) {
    return left?.size === right?.length && right.every((mesh) => left.has(mesh));
  }

  const inFlight = new Map();
  const sourceErrors = new Map();

  async function ensureCooperative(sourceKey, members, { isCurrent = () => true } = {}) {
    const current = isCurrent;
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
      const rig = await createSourceSkinningRigCooperative(sourceKey, members, budget, current);
      if (!rig || !current()) return null;
      sourceErrors.delete(sourceKey);
      sourceSkinningRigs.set(sourceKey, rig);
      return rig;
    })();
    inFlight.set(sourceKey, promise);
    try {
      return await promise;
    } catch (error) {
      if (current() && error.code === 'rig_surface_unavailable') {
        sourceErrors.set(sourceKey, error.message);
        sourceSkinningRigs.delete(sourceKey);
      }
      throw error;
    } finally {
      if (inFlight.get(sourceKey) === promise) inFlight.delete(sourceKey);
    }
  }

  function groupLoadedMeshes() {
    const groups = new Map();
    for (const mesh of knownMeshes) {
      const state = states.get(mesh);
      if (!state?.loaded || !state.skinningSourceKey) continue;
      const members = groups.get(state.skinningSourceKey) || [];
      members.push(mesh);
      groups.set(state.skinningSourceKey, members);
    }
    return groups;
  }

  function retainSourceRigs(groups) {
    for (const sourceKey of [...sourceSkinningRigs.keys()]) {
      if (!groups.has(sourceKey)) sourceSkinningRigs.delete(sourceKey);
    }
    for (const sourceKey of sourceErrors.keys()) {
      if (!groups.has(sourceKey)) sourceErrors.delete(sourceKey);
    }
    return [...sourceSkinningRigs.values()];
  }

  async function buildAllCooperative({ generation = null, isCurrent = () => true } = {}) {
    const groups = groupLoadedMeshes();
    const budget = createWorkBudget();
    for (const [sourceKey, members] of groups) {
      if (!isCurrent()) return null;
      try {
        const rig = await ensureCooperative(sourceKey, members, { generation, isCurrent });
        if (!rig) return null;
      } catch (error) {
        if (error.code !== 'rig_surface_unavailable') throw error;
      }
      await budget.checkpoint();
    }
    if (!isCurrent()) return null;
    const rigs = retainSourceRigs(groups);
    if (!rigs.length && sourceErrors.size) throw new Error([...sourceErrors.values()][0]);
    return rigs;
  }

  return {
    ensureCooperative,
    buildAllCooperative,
    getErrors: () => Object.fromEntries(sourceErrors),
    reset() {
      inFlight.clear();
      sourceErrors.clear();
    },
  };
}

export function createRigModelSession({
  state,
  modelWeightState,
  getGeneration,
  ensureModelWeightsLoaded,
  buildAllSourceSkinningRigsCooperatively,
  buildModelSkinningRig,
  getSnapshot,
  notifyChanged,
  requestRender,
  pickFromSurface,
  getModelJointId,
  rotationSnapValues,
} = {}) {
  let loadToken = null;

  function getState() {
    return getSnapshot();
  }

  function modelJointFromSkinningSample(sampled) {
    if (!sampled?.sourceKey || !Array.isArray(sampled.influences)) return null;
    const jointScores = new Map();
    for (const influence of sampled.influences) {
      const boneId = Number(influence?.boneId);
      const weight = Number(influence?.weight);
      const jointId = getModelJointId(sampled.sourceKey, boneId);
      if (!Number.isInteger(boneId) || !Number.isFinite(weight) || weight <= 0 || !Number.isInteger(jointId)) continue;
      const current = jointScores.get(jointId);
      if (!current) {
        jointScores.set(jointId, { weight, boneId, boneWeight: weight });
        continue;
      }
      current.weight += weight;
      if (weight > current.boneWeight || (weight === current.boneWeight && boneId < current.boneId)) {
        current.boneId = boneId;
        current.boneWeight = weight;
      }
    }
    const best = [...jointScores.entries()].sort(
      (left, right) => right[1].weight - left[1].weight || left[0] - right[0],
    )[0];
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

  function invalidate() {
    const changed = Boolean(
      loadToken ||
      state.promise ||
      state.loading ||
      state.loaded ||
      state.error ||
      state.jointPickIntent ||
      state.pickStatus,
    );
    loadToken = null;
    state.promise = null;
    state.loading = false;
    state.loaded = false;
    state.error = null;
    state.jointPickIntent = null;
    state.pickStatus = '';
    if (changed) {
      notifyChanged();
      requestRender();
    }
    return changed;
  }

  function startLoad() {
    if (state.promise) return state.promise;
    const generation = getGeneration();
    const token = {};
    loadToken = token;
    state.loading = true;
    state.loaded = false;
    state.error = null;
    state.pickStatus = '';
    notifyChanged();

    const isCurrent = () => generation === getGeneration() && loadToken === token;
    const promise = (async () => {
      if (!modelWeightState.loaded) await ensureModelWeightsLoaded();
      if (!isCurrent()) return getSnapshot();
      if (modelWeightState.error) throw new Error(modelWeightState.error);
      const sourceRigs = await buildAllSourceSkinningRigsCooperatively({ generation, isCurrent });
      if (!sourceRigs || !isCurrent()) return getSnapshot();
      const built = await buildModelSkinningRig(sourceRigs, { generation, isCurrent });
      if (!built || !isCurrent()) return getSnapshot();
      state.loaded = true;
      return getSnapshot();
    })()
      .catch((error) => {
        if (!isCurrent()) return getSnapshot();
        state.error = error instanceof Error ? error.message : String(error);
        state.loaded = false;
        return getSnapshot();
      })
      .finally(() => {
        if (!isCurrent()) return;
        state.loading = false;
        state.promise = null;
        loadToken = null;
        notifyChanged();
      });
    state.promise = promise;
    return promise;
  }

  function ensureLoaded() {
    if (state.loaded) return Promise.resolve(getSnapshot());
    return startLoad();
  }

  function isActive() {
    return Boolean(state.loaded || state.loading || state.promise);
  }

  function rebuild() {
    if (!isActive()) return Promise.resolve(getSnapshot());
    invalidate();
    return startLoad();
  }

  function beginJointPicking(intent = {}) {
    const next = String(intent?.type || '') === 'selected-joint' ? { type: 'selected-joint' } : null;
    if (!next || !state.loaded) return false;
    if (state.jointPickIntent && state.jointPickIntent.type === next.type) {
      return cancelJointPicking();
    }
    state.jointPickIntent = next;
    state.pickStatus = weightRigStatus('weightRig.status.pickRigJoint');
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
    if (!rig?.joints?.some((joint) => Number(joint.jointId) === id)) return false;
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
    const next = String(intent?.type || '') === 'selected-joint' ? { type: 'selected-joint' } : null;
    if (!next) return false;
    const jointId = pickFromSurface?.(point, next);
    if (!Number.isInteger(jointId)) {
      state.pickStatus = weightRigStatus('weightRig.status.noRigJointAtPoint');
      notifyChanged();
      requestRender();
      return false;
    }
    return handleJointPicked(jointId, next);
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
    getState,
    invalidate,
    ensureLoaded,
    isActive,
    rebuild,
    beginJointPicking,
    cancelJointPicking,
    handleJointPicked,
    pickJointFromSurface,
    selectJoint,
    modelJointFromSkinningSample,
    clearJointSelection() {
      const hadSelection = state.selectedJointId !== null && state.selectedJointId !== undefined;
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
