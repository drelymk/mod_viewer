// Draft lifecycle for semantic HumanoidControlRig corrections.
//
// This module deliberately knows nothing about DOM or Three.js interaction.
// The viewport supplies model-space cursor positions and resolved snap
// candidates; the session owns draft state, hysteresis, and persistence.

import {
  HUMANOID_CONTROL_KEYS,
  humanoidControlPositionToSemantic,
  rebuildHumanoidControlPaths,
} from './humanoid-control-rig.js';

export const HUMANOID_CONTROL_PICK_RADIUS = 14;
export const JOINT_ATTRACTION_RADIUS_PX = 28;
export const JOINT_SNAP_RADIUS_PX = 12;
export const JOINT_RELEASE_RADIUS_PX = 20;

function clone(value) {
  if (value === undefined || value === null) return value;
  return JSON.parse(JSON.stringify(value));
}

function finitePosition(value) {
  const position = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 3).map(Number)
    : [value?.x, value?.y, value?.z].map(Number);
  return position.length === 3 && position.every(Number.isFinite)
    ? position : null;
}

function normalizeOverrides(value) {
  const raw = value?.humanoid_control_rig || value;
  if (!raw || typeof raw !== 'object' || Number(raw.version) !== 1
      || !raw.controls || typeof raw.controls !== 'object') return null;
  const controls = {};
  HUMANOID_CONTROL_KEYS.forEach(key => {
    const entry = raw.controls[key];
    const semantic = entry?.semantic;
    if (!semantic || typeof semantic !== 'object') return;
    const values = ['sideN', 'height01', 'depthN'].map(name =>
      Number(semantic[name]));
    if (!values.every(Number.isFinite)) return;
    controls[key] = {
      semantic: {
        sideN: values[0], height01: values[1], depthN: values[2],
      },
    };
    if (typeof entry.joint_signature === 'string'
        && entry.joint_signature.length > 0) {
      controls[key].joint_signature = entry.joint_signature;
    }
  });
  return {version: 1, controls};
}

function sameSemantic(left, right, tolerance = 1e-7) {
  return ['sideN', 'height01', 'depthN'].every(key =>
    Number.isFinite(Number(left?.[key]))
      && Number.isFinite(Number(right?.[key]))
      && Math.abs(Number(left[key]) - Number(right[key])) <= tolerance);
}

function mappingJointId(mapping) {
  const jointId = Number(mapping?.jointId);
  return Number.isInteger(jointId) ? jointId : null;
}

function jointFor(modelRig, jointId) {
  return (modelRig?.joints || []).find(item =>
    Number(item?.jointId) === Number(jointId)) || null;
}

function currentModPath(getKnownMeshes) {
  return [...(getKnownMeshes?.() || [])]
    .find(mesh => mesh?.userData?.modPath)?.userData?.modPath || null;
}

function createSession({modelRigState, getModelRig, getAutomaticRig,
    resetCurrentPoseForHumanoidRigEdit, setPhysicsSuspended,
    resolveMappings, rebuildActiveRig, getKnownMeshes, persist, clearPersist,
    cancelWeightPicking, cancelRigPicking, notifyChanged, requestRender} = {}) {
  let savedOverrides = null;
  let generation = 0;
  let writeQueue = Promise.resolve();
  let state = {
    editing: false, saving: false, error: null, dirty: false,
    selectedControlKey: null, carryingControlKey: null,
    draftRig: null, baseRig: null, mappedJointIdByControl: new Map(),
    baseMappedJointIdByControl: new Map(),
    carryBefore: null,
  };

  function notify() { notifyChanged?.(); }

  function editSnapshot() {
    const controls = state.editing && state.draftRig
      ? clone(state.draftRig.controls || {}) : {};
    return {
      editing: state.editing,
      saving: state.saving,
      error: state.error,
      dirty: state.dirty,
      selectedControlKey: state.selectedControlKey,
      carryingControlKey: state.carryingControlKey,
      controls,
      mappedJointIdByControl: Object.fromEntries(
        [...state.mappedJointIdByControl.entries()]
          .map(([key, mapping]) => [key, Number(mapping?.jointId)])),
      candidateJointId: state.candidateJointId ?? null,
      hasSavedOverrides: !!(savedOverrides
        && Object.keys(savedOverrides.controls || {}).length),
    };
  }

  function updateDirty() {
    if (!state.draftRig || !state.baseRig) {
      state.dirty = false;
      return;
    }
    state.dirty = HUMANOID_CONTROL_KEYS.some(key => {
      const draft = state.draftRig.controls?.[key];
      const base = state.baseRig.controls?.[key];
      return JSON.stringify(draft?.position) !== JSON.stringify(base?.position);
    }) || HUMANOID_CONTROL_KEYS.some(key =>
      mappingJointId(state.baseMappedJointIdByControl.get(key))
        !== mappingJointId(state.mappedJointIdByControl.get(key)));
  }

  function setMetadata(metadata) {
    generation += 1;
    savedOverrides = normalizeOverrides(metadata);
    if (state.editing) cancel();
    return editSnapshot();
  }

  function getSavedOverrides() { return clone(savedOverrides); }

  function begin() {
    cancelWeightPicking?.();
    cancelRigPicking?.();
    setPhysicsSuspended?.(true);
    resetCurrentPoseForHumanoidRigEdit?.({request: false});
    const rig = getModelRig?.();
    if (!rig?.humanoidControlRig) {
      setPhysicsSuspended?.(false);
      state.error = 'The inferred Humanoid Rig is not loaded.';
      notify();
      return {started: false, error: state.error};
    }
    const mappings = resolveMappings?.({savedOverrides, modelRig: rig})
      || new Map();
    state = {
      editing: true, saving: false, error: null, dirty: false,
      selectedControlKey: null, carryingControlKey: null,
      draftRig: clone(rig.humanoidControlRig),
      baseRig: clone(rig.humanoidControlRig),
      mappedJointIdByControl: mappings,
      baseMappedJointIdByControl: new Map(mappings),
      carryBefore: null,
    };
    notify();
    requestRender?.();
    return {started: true};
  }

  function beginCarry(controlKey) {
    if (!state.editing || !state.draftRig?.controls?.[controlKey]
        || state.carryingControlKey) return false;
    const control = state.draftRig.controls[controlKey];
    const existing = state.mappedJointIdByControl.get(controlKey);
    state.selectedControlKey = controlKey;
    state.carryingControlKey = controlKey;
    state.carryBefore = {
      position: [...control.position],
      mapping: existing ? {...existing} : null,
    };
    state.candidateJointId = existing?.jointId ?? null;
    notify();
    requestRender?.();
    return true;
  }

  function updateDraft(controlKey, position, {
    candidateJointId = null, candidateDistance = Infinity,
    mappedDistance = Infinity,
    notifyState = true, request = true,
  } = {}) {
    if (!state.editing || state.carryingControlKey !== controlKey) return false;
    const freePosition = finitePosition(position);
    if (!freePosition) {
      state.error = 'The draft control position is invalid.';
      if (notifyState) notify();
      if (request) requestRender?.();
      return false;
    }
    const modelRig = getModelRig?.();
    const current = state.mappedJointIdByControl.get(controlKey);
    const distance = Number(candidateDistance);
    const suppliedMappedDistance = Number(mappedDistance);
    const currentDistance = Number.isFinite(suppliedMappedDistance)
      ? suppliedMappedDistance
      : Number(candidateJointId) === Number(current?.jointId)
        ? distance : Infinity;
    const currentJoint = current ? jointFor(modelRig, current.jointId) : null;
    if (current && (!currentJoint || !Number.isFinite(currentDistance)
        || currentDistance > JOINT_RELEASE_RADIUS_PX)) {
      state.mappedJointIdByControl.delete(controlKey);
    }
    const usedByOther = new Set([...state.mappedJointIdByControl.entries()]
      .filter(([key]) => key !== controlKey)
      .map(([, mapping]) => Number(mapping?.jointId)));
    const candidate = jointFor(modelRig, candidateJointId);
    if (state.mappedJointIdByControl.has(controlKey)) {
      const mappedJoint = jointFor(modelRig,
        state.mappedJointIdByControl.get(controlKey)?.jointId);
      const pivot = mappedJoint?.restPivot || mappedJoint?.restCenter;
      if (pivot) state.draftRig.controls[controlKey].position = finitePosition(pivot);
      state.candidateJointId = candidate && Number.isFinite(distance)
        && distance <= JOINT_ATTRACTION_RADIUS_PX ? Number(candidateJointId)
        : state.mappedJointIdByControl.get(controlKey)?.jointId ?? null;
    } else if (candidate && Number.isFinite(distance)
        && distance <= JOINT_SNAP_RADIUS_PX
        && !usedByOther.has(Number(candidateJointId))) {
      const mapping = {
        controlKey, jointId: Number(candidateJointId),
        jointSignature: candidate.signature || null,
        sourceMembers: [...(candidate.members || candidate.sourceMembers || [])]
          .map(member => ({...member})),
      };
      state.mappedJointIdByControl.set(controlKey, mapping);
      state.draftRig.controls[controlKey].position = finitePosition(
        candidate.restPivot || candidate.restCenter);
      state.candidateJointId = Number(candidateJointId);
    } else {
      state.draftRig.controls[controlKey].position = freePosition;
      state.candidateJointId = candidate && Number.isFinite(distance)
        && distance <= JOINT_ATTRACTION_RADIUS_PX ? Number(candidateJointId) : null;
    }
    const semantic = humanoidControlPositionToSemantic(
      state.draftRig.controls[controlKey].position, state.draftRig);
    if (semantic) state.draftRig.controls[controlKey].semantic = semantic;
    state.draftRig.controls[controlKey].source =
      state.mappedJointIdByControl.has(controlKey)
        ? 'manual_model_joint' : 'manual_override';
    rebuildHumanoidControlPaths(state.draftRig);
    state.error = null;
    updateDirty();
    if (notifyState) notify();
    if (request) requestRender?.();
    return true;
  }

  function finishCarry() {
    if (!state.carryingControlKey) return false;
    state.carryingControlKey = null;
    state.carryBefore = null;
    state.candidateJointId = null;
    updateDirty();
    notify();
    requestRender?.();
    return true;
  }

  function cancelCarry() {
    const key = state.carryingControlKey;
    if (!key || !state.carryBefore) return false;
    state.draftRig.controls[key].position = [...state.carryBefore.position];
    if (state.carryBefore.mapping) {
      state.mappedJointIdByControl.set(key, {...state.carryBefore.mapping});
    } else state.mappedJointIdByControl.delete(key);
    const semantic = humanoidControlPositionToSemantic(
      state.draftRig.controls[key].position, state.draftRig);
    if (semantic) state.draftRig.controls[key].semantic = semantic;
    rebuildHumanoidControlPaths(state.draftRig);
    state.carryingControlKey = null;
    state.carryBefore = null;
    state.candidateJointId = null;
    updateDirty();
    notify();
    requestRender?.();
    return true;
  }

  function serializeDraft() {
    const automatic = getAutomaticRig?.() || state.baseRig;
    const modelRig = getModelRig?.();
    if (!automatic || !modelRig) throw new Error('The inferred Rig is not loaded.');
    const controls = {};
    HUMANOID_CONTROL_KEYS.forEach(key => {
      const draft = state.draftRig?.controls?.[key];
      const position = finitePosition(draft?.position);
      if (!position) throw new Error(`Invalid position for ${key}.`);
      const semantic = humanoidControlPositionToSemantic(position, automatic);
      if (!semantic) throw new Error(`Invalid semantic position for ${key}.`);
      const mapping = state.mappedJointIdByControl.get(key);
      const joint = mapping ? jointFor(modelRig, mapping.jointId) : null;
      const entry = {semantic};
      if (joint?.signature) entry.joint_signature = joint.signature;
      if (!sameSemantic(semantic, automatic.controls?.[key]?.semantic)
          || entry.joint_signature) controls[key] = entry;
    });
    return {version: 1, controls};
  }

  function queueWrite(operation) {
    const queued = writeQueue.then(operation, operation);
    writeQueue = queued.catch(() => {});
    return queued;
  }

  async function save() {
    if (!state.editing || state.saving) return {saved: false,
      error: 'The Humanoid Rig is already being saved.'};
    const dirtyBeforeSave = state.dirty;
    state.saving = true;
    state.error = null;
    state.carryingControlKey = null;
    state.carryBefore = null;
    state.candidateJointId = null;
    updateDirty();
    notify();
    const requestGeneration = generation;
    try {
      const value = serializeDraft();
      const path = currentModPath(getKnownMeshes);
      if (!path) throw new Error('Humanoid Rig persistence is unavailable.');
      const result = await queueWrite(async () => {
        if (Object.keys(value.controls).length) {
          return persist?.(path, value);
        }
        if (savedOverrides) return clearPersist?.(path);
        return {saved: true};
      });
      if (!result?.saved) throw new Error(result?.error
        || 'The Humanoid Rig was not saved.');
      if (requestGeneration !== generation) return {saved: true, stale: true};
      savedOverrides = Object.keys(value.controls).length ? value : null;
      if (dirtyBeforeSave) {
        await rebuildActiveRig?.(savedOverrides);
      }
      state = {
        editing: false, saving: false, error: null, dirty: false,
        selectedControlKey: null, carryingControlKey: null,
        draftRig: null, baseRig: null, mappedJointIdByControl: new Map(),
        baseMappedJointIdByControl: new Map(),
        carryBefore: null,
      };
      setPhysicsSuspended?.(false);
      notify();
      requestRender?.();
      return {saved: true, humanoid_control_rig: value};
    } catch (error) {
      state.saving = false;
      state.error = error instanceof Error ? error.message : String(error);
      notify();
      requestRender?.();
      return {saved: false, error: state.error};
    }
  }

  async function reset() {
    if (!savedOverrides) return {saved: false};
    const path = currentModPath(getKnownMeshes);
    if (!path) return {saved: false, error: 'Humanoid Rig persistence is unavailable.'};
    const requestGeneration = generation;
    state.saving = true;
    state.error = null;
    notify();
    try {
      const result = await queueWrite(() => clearPersist?.(path));
      if (!result?.saved) throw new Error(result?.error
        || 'The Humanoid Rig was not reset.');
      if (requestGeneration !== generation) return {saved: true, stale: true};
      savedOverrides = null;
      await rebuildActiveRig?.(null);
      state.saving = false;
      state.error = null;
      notify();
      requestRender?.();
      return {saved: true};
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      state.saving = false;
      state.error = message;
      notify();
      return {saved: false, error: message};
    }
  }

  function cancel() {
    if (!state.editing) return false;
    state = {
      editing: false, saving: false, error: null, dirty: false,
      selectedControlKey: null, carryingControlKey: null,
      draftRig: null, baseRig: null, mappedJointIdByControl: new Map(),
      baseMappedJointIdByControl: new Map(),
      carryBefore: null,
    };
    setPhysicsSuspended?.(false);
    notify();
    requestRender?.();
    return true;
  }

  function resetSession() {
    generation += 1;
    writeQueue = Promise.resolve();
    savedOverrides = null;
    state = {
      editing: false, saving: false, error: null, dirty: false,
      selectedControlKey: null, carryingControlKey: null,
      draftRig: null, baseRig: null, mappedJointIdByControl: new Map(),
      baseMappedJointIdByControl: new Map(),
      carryBefore: null,
    };
    setPhysicsSuspended?.(false);
  }

  return {
    snapshot: editSnapshot, setMetadata, getSavedOverrides,
    begin, cancel, save, reset, beginCarry, updateDraft, finishCarry,
    cancelCarry, resetSession,
  };
}

let activeSession = null;

export function initializeHumanoidRigEditSession(options) {
  activeSession = createSession(options);
  return activeSession;
}

function session() {
  if (!activeSession) throw new Error('Humanoid Rig edit session is not initialized.');
  return activeSession;
}

export function getHumanoidRigEditSnapshot() { return session().snapshot(); }
export function setHumanoidRigMetadata(metadata) { return session().setMetadata(metadata); }
export function beginHumanoidRigEdit() { return session().begin(); }
export function cancelHumanoidRigEdit() { return session().cancel(); }
export function saveHumanoidRigEdit() { return session().save(); }
export function resetHumanoidRig() { return session().reset(); }
export function beginHumanoidControlCarry(controlKey) {
  return session().beginCarry(controlKey);
}
export function updateHumanoidControlDraft(controlKey, position, options) {
  return session().updateDraft(controlKey, position, options);
}
export function finishHumanoidControlCarry() { return session().finishCarry(); }
export function cancelHumanoidControlCarry() { return session().cancelCarry(); }
export function resetHumanoidRigEditSession() { return activeSession?.resetSession(); }
