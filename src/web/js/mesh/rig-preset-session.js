// Rig preset session. Persistence and metadata hydration are intentionally
// separate from model-joint pose and deformation transactions.

import {rigPresetSnapshot} from './weight-rig-snapshots.js';
import {
  createRigPreset, normalizeRigPreset, serializeRigPose,
  validateRigPresetName,
} from './weight-rig-presets.js';

let activeSession = null;

function unavailableRigPresetResult(reason, preset = null) {
  return {
    success: false,
    preset,
    appliedJointCount: 0,
    skippedJointCount: 0,
    appliedRootCount: 0,
    skippedRootCount: 0,
    skipped: [{type: 'preset', reason}],
  };
}

function createSession({state, getModelRig, getModelRigState, getKnownMeshes,
    resolveRigPreset, applyResolvedPreset, notifyChanged} = {}) {
  let generation = 0;
  let writeQueue = Promise.resolve();
  let writeToken = 0;

  const snapshot = () => rigPresetSnapshot(state);
  const notify = () => notifyChanged?.();
  const currentModPath = () => [...(getKnownMeshes?.() || [])]
    .find(mesh => mesh.userData?.modPath)?.userData?.modPath || null;

  function setPresetList(presets, error = null) {
    const normalized = [];
    const seenIds = new Set();
    for (const raw of presets || []) {
      const preset = normalizeRigPreset(raw);
      if (!preset || seenIds.has(preset.id)) continue;
      seenIds.add(preset.id);
      normalized.push(preset);
    }
    state.presets = normalized;
    state.error = error || null;
    if (!normalized.some(preset => preset.id === state.selectedPresetId)) {
      state.selectedPresetId = null;
    }
  }

  function queueWrite(operation) {
    const token = ++writeToken;
    state.loading = true;
    state.error = null;
    notify();
    const queued = writeQueue.then(operation, operation);
    writeQueue = queued.catch(() => {});
    return queued.catch(error => ({saved: false,
      error: error instanceof Error ? error.message : String(error)}))
      .finally(() => {
        if (token === writeToken) {
          state.loading = false;
          notify();
        }
      });
  }

  function setMetadata(metadata) {
    generation += 1;
    state.selectedPresetId = null;
    if (!metadata || typeof metadata !== 'object') {
      state.loaded = true;
      state.loading = false;
      state.lastApplyResult = null;
      setPresetList([]);
      notify();
      return snapshot();
    }
    const validVersion = Number(metadata.version) === 1;
    setPresetList(metadata.presets, validVersion
      ? metadata.error || null : 'Pose presets could not be loaded.');
    state.loaded = true;
    state.loading = false;
    state.lastApplyResult = null;
    notify();
    return snapshot();
  }

  function applyById(presetId) {
    const id = presetId ?? state.selectedPresetId;
    const preset = state.presets.find(item => item.id === id);
    if (!preset) {
      const result = unavailableRigPresetResult('invalid_preset');
      state.lastApplyResult = result;
      notify();
      return result;
    }
    state.selectedPresetId = preset.id;
    return applyResolvedPreset(resolveRigPreset(getModelRig(), preset), {
      presetId: preset.id,
    });
  }

  function save(name) {
    const rig = getModelRig();
    const rigState = getModelRigState();
    if (!rig || !rigState.loaded) {
      return Promise.resolve({saved: false,
        error: 'The inferred Rig is not loaded.'});
    }
    const pose = serializeRigPose(rig, {
      explicitRootSignatures: rigState.explicitRootSignatures,
    });
    if (!pose.joints.length && !pose.roots.length) {
      return Promise.resolve({saved: false,
        error: 'There is no manual pose to save.'});
    }
    let preset;
    try {
      preset = createRigPreset({name, modelRig: rig,
        explicitRootSignatures: rigState.explicitRootSignatures});
    } catch (error) {
      return Promise.resolve({saved: false,
        error: error instanceof Error ? error.message : String(error)});
    }
    if (state.presets.some(item =>
        item.name.trim().toLocaleLowerCase() === preset.name.toLocaleLowerCase())) {
      return Promise.resolve({saved: false,
        error: 'A pose with this name already exists.'});
    }
    const api = window.pywebview?.api?.save_rig_pose_preset;
    const path = currentModPath();
    if (typeof api !== 'function' || !path) {
      return Promise.resolve({saved: false, error: 'Pose presets are unavailable.'});
    }
    const requestGeneration = generation;
    return queueWrite(async () => {
      const result = await api(path, preset);
      if (!result?.saved) throw new Error(result?.error || 'The pose was not saved.');
      if (requestGeneration !== generation) return {saved: true, preset, stale: true};
      setPresetList(result.presets || [...state.presets, preset]);
      state.selectedPresetId = preset.id;
      return {saved: true, preset, presets: state.presets};
    });
  }

  function rename(presetId, name) {
    const id = String(presetId || '');
    const preset = state.presets.find(item => item.id === id);
    const checked = validateRigPresetName(name);
    if (!preset) return Promise.resolve({saved: false,
      error: 'Pose preset was not found.'});
    if (!checked.valid) return Promise.resolve({saved: false, error: checked.error});
    if (state.presets.some(item => item.id !== preset.id
        && item.name.toLocaleLowerCase() === checked.value.toLocaleLowerCase())) {
      return Promise.resolve({saved: false,
        error: 'A pose with this name already exists.'});
    }
    const api = window.pywebview?.api?.rename_rig_pose_preset;
    const path = currentModPath();
    if (typeof api !== 'function' || !path) {
      return Promise.resolve({saved: false, error: 'Pose presets are unavailable.'});
    }
    const requestGeneration = generation;
    return queueWrite(async () => {
      const result = await api(path, preset.id, checked.value);
      if (!result?.saved) throw new Error(result?.error || 'The pose was not renamed.');
      if (requestGeneration !== generation) return {saved: true, stale: true};
      setPresetList(result.presets || state.presets);
      state.selectedPresetId = preset.id;
      return {saved: true, presets: state.presets};
    });
  }

  function remove(presetId) {
    const id = String(presetId || '');
    const preset = state.presets.find(item => item.id === id);
    if (!preset) return Promise.resolve({saved: false,
      error: 'Pose preset was not found.'});
    const api = window.pywebview?.api?.delete_rig_pose_preset;
    const path = currentModPath();
    if (typeof api !== 'function' || !path) {
      return Promise.resolve({saved: false, error: 'Pose presets are unavailable.'});
    }
    const requestGeneration = generation;
    return queueWrite(async () => {
      const result = await api(path, preset.id);
      if (!result?.saved) throw new Error(result?.error || 'The pose was not deleted.');
      if (requestGeneration !== generation) return {saved: true, stale: true};
      setPresetList(result.presets || state.presets.filter(item =>
        item.id !== preset.id));
      if (state.selectedPresetId === preset.id) state.selectedPresetId = null;
      return {saved: true, presets: state.presets};
    });
  }

  return {snapshot, setMetadata, applyById, save, rename, remove,
    reset() {
      generation += 1;
      writeToken += 1;
      writeQueue = Promise.resolve();
    }};
}

export function initializeRigPresetSession(options) {
  activeSession = createSession(options);
}

function session() {
  if (!activeSession) throw new Error('Rig preset session is not initialized.');
  return activeSession;
}

export function getRigPresetSnapshot() { return session().snapshot(); }
export function resetRigPresetSession() { activeSession?.reset(); }
export function setRigMetadata(metadata) { return session().setMetadata(metadata); }
export function applyRigPosePresetById(presetId) {
  return session().applyById(presetId);
}
export function saveRigPosePreset(name) { return session().save(name); }
export function renameRigPosePreset(presetId, name) {
  return session().rename(presetId, name);
}
export function deleteRigPosePreset(presetId) { return session().remove(presetId); }
