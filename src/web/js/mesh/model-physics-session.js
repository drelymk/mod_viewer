// Character-wide ownership for the opt-in secondary-motion simulation.

import {
  addWeightPhysicsPerformance, performanceNow,
} from './weight-physics-performance.js';

export const MODEL_PHYSICS_STEP = 1 / 120;
export const MODEL_PHYSICS_MAX_FRAME_DELTA = 0.05;
export const MODEL_PHYSICS_MAX_SUBSTEPS = 6;

const ZERO_VECTOR = Object.freeze([0, 0, 0]);
export const DEFAULT_MODEL_PHYSICS_SETTINGS = Object.freeze({
  frequencyHz: 2,
  dampingRatio: 0.35,
  angularResponse: 0.35,
  translationResponse: 0.35,
  velocityResponse: 0.35,
  gravityEnabled: false,
  gravityScale: 1,
  constraintsEnabled: false,
  maxBendDegrees: 45,
});

function vector(value, fallback = ZERO_VECTOR) {
  const values = Array.isArray(value) ? value : [
    value?.x, value?.y, value?.z,
  ];
  if (values.length < 3) return [...fallback];
  const result = values.slice(0, 3).map(Number);
  return result.every(Number.isFinite) ? result : [...fallback];
}

function finiteVector(value) {
  const values = Array.isArray(value) ? value : [
    value?.x, value?.y, value?.z,
  ];
  if (values.length < 3) return null;
  const result = values.slice(0, 3).map(Number);
  return result.every(Number.isFinite) ? result : null;
}

function vectorSubtract(left, right) {
  return [left[0] - right[0], left[1] - right[1], left[2] - right[2]];
}

function vectorLength(value) {
  return Math.hypot(value[0], value[1], value[2]);
}

function quaternion(value) {
  const values = Array.isArray(value) ? value : [
    value?.x, value?.y, value?.z, value?.w,
  ];
  if (values.length < 4) return null;
  const result = values.slice(0, 4).map(Number);
  const length = Math.hypot(...result);
  return result.every(Number.isFinite) && length > 1e-12
    ? result.map(component => component / length) : null;
}

function quaternionMultiply(left, right) {
  return [
    left[3] * right[0] + left[0] * right[3]
      + left[1] * right[2] - left[2] * right[1],
    left[3] * right[1] - left[0] * right[2]
      + left[1] * right[3] + left[2] * right[0],
    left[3] * right[2] + left[0] * right[1]
      - left[1] * right[0] + left[2] * right[3],
    left[3] * right[3] - left[0] * right[0]
      - left[1] * right[1] - left[2] * right[2],
  ];
}

function quaternionInverse(value) {
  return [-value[0], -value[1], -value[2], value[3]];
}

/** Return the shortest rotation vector between normalized orientations. */
export function shortestModelRotationVector(previousValue, currentValue) {
  const previous = quaternion(previousValue);
  const current = quaternion(currentValue);
  if (!previous || !current) return [0, 0, 0];
  let delta = quaternionMultiply(quaternionInverse(previous), current);
  const length = Math.hypot(...delta);
  if (!Number.isFinite(length) || length <= 1e-12) return [0, 0, 0];
  delta = delta.map(component => component / length);
  if (delta[3] < 0) delta = delta.map(component => -component);
  const vectorLength = Math.hypot(delta[0], delta[1], delta[2]);
  if (vectorLength <= 1e-12) return [0, 0, 0];
  const angle = 2 * Math.atan2(vectorLength,
    Math.max(-1, Math.min(1, delta[3])));
  return delta.slice(0, 3).map(component => component * angle / vectorLength);
}

function transform(value) {
  const orientation = quaternion(value?.orientation);
  if (!orientation) return null;
  return {
    orientation,
    translation: vector(value?.translation),
  };
}

function changed(value) {
  return vectorLength(value) > 1e-10;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function normalizedSettings(previous, patch = {}) {
  const next = {...previous};
  if (Object.hasOwn(patch, 'frequencyHz')) {
    const value = Number(patch.frequencyHz);
    if (Number.isFinite(value)) next.frequencyHz = clamp(value, 0.1, 10);
  }
  if (Object.hasOwn(patch, 'dampingRatio')) {
    const value = Number(patch.dampingRatio);
    if (Number.isFinite(value)) next.dampingRatio = clamp(value, 0, 2);
  }
  for (const [key, minimum, maximum] of [
    ['angularResponse', 0, 1],
    ['translationResponse', 0, 1],
    ['velocityResponse', 0, 1],
    ['gravityScale', 0, 2],
    ['maxBendDegrees', 0, 90],
  ]) {
    if (!Object.hasOwn(patch, key)) continue;
    const value = Number(patch[key]);
    if (Number.isFinite(value)) next[key] = clamp(value, minimum, maximum);
  }
  if (Object.hasOwn(patch, 'gravityEnabled')) {
    next.gravityEnabled = !!patch.gravityEnabled;
  }
  if (Object.hasOwn(patch, 'constraintsEnabled')) {
    next.constraintsEnabled = !!patch.constraintsEnabled;
  }
  return next;
}

/** Create the one physics owner for the currently displayed character. */
export function createModelPhysicsSession({
  onInputOwnershipChanged,
  onFrame,
  onStateChanged,
  requestAnimationFrame: requestFrame,
  cancelAnimationFrame: cancelFrame,
} = {}) {
  let enabled = false;
  let generation = 0;
  let settings = {...DEFAULT_MODEL_PHYSICS_SETTINGS};
  let previousModelOrientation = null;
  let previousModelTranslation = [...ZERO_VECTOR];
  let rootLinearVelocityWorld = [...ZERO_VECTOR];
  let virtualLinearVelocityWorld = [...ZERO_VECTOR];
  let virtualActive = false;
  let accumulator = 0;
  let lastTimestamp = null;
  let frameId = null;
  let settled = true;
  let inputOwned = false;
  const participants = new Map();
  const statuses = new Map();

  function participantKey(participantOrKey) {
    if (participantOrKey && typeof participantOrKey === 'object') {
      return participantOrKey.key ?? participantOrKey.mesh;
    }
    return participantOrKey;
  }

  function participantContainsMesh(participant, mesh) {
    if (participant?.meshes instanceof Set) return participant.meshes.has(mesh);
    if (Array.isArray(participant?.meshes)) return participant.meshes.includes(mesh);
    return participant?.mesh === mesh;
  }

  function resolvedParticipantKey(participantOrKey) {
    const key = participantKey(participantOrKey);
    if (participants.has(key)) return key;
    for (const [candidateKey, participant] of participants) {
      if (participantContainsMesh(participant, participantOrKey)) {
        return candidateKey;
      }
    }
    return key;
  }

  function notify() {
    addWeightPhysicsPerformance('physicsUiNotifyCount');
    onStateChanged?.(getState());
  }

  function syncInputOwnership() {
    const next = enabled && participants.size > 0;
    if (next === inputOwned) return;
    inputOwned = next;
    onInputOwnershipChanged?.(next);
  }

  function cancelScheduledFrame() {
    if (frameId === null) return;
    cancelFrame?.(frameId);
    frameId = null;
  }

  function needsSimulation() {
    return enabled && participants.size > 0 && (!settled || virtualActive);
  }

  function schedule() {
    if (frameId !== null || !needsSimulation() || !requestFrame) return;
    frameId = requestFrame(timestamp => {
      frameId = null;
      advance(timestamp);
      if (needsSimulation()) schedule();
    });
  }

  function wake() {
    if (!enabled || participants.size === 0) return false;
    settled = false;
    schedule();
    return true;
  }

  function setReference(modelTransform) {
    const current = transform(modelTransform);
    if (!current) return false;
    previousModelOrientation = [...current.orientation];
    previousModelTranslation = [...current.translation];
    return true;
  }

  function getState() {
    let failedCount = 0;
    let unavailableCount = 0;
    statuses.forEach(status => {
      if (status.kind === 'failed') failedCount += 1;
      if (status.kind === 'unavailable') unavailableCount += 1;
    });
    return {
      enabled,
      generation,
      participantCount: participants.size,
      participatingMeshCount: [...participants.values()].reduce(
        (count, participant) => count + Number(
          participant.getMeshCount?.() ?? (participant.mesh ? 1 : 0)), 0),
      unavailableCount,
      failedCount,
      frequencyHz: settings.frequencyHz,
      dampingRatio: settings.dampingRatio,
      angularResponse: settings.angularResponse,
      translationResponse: settings.translationResponse,
      velocityResponse: settings.velocityResponse,
      gravityEnabled: settings.gravityEnabled,
      gravityScale: settings.gravityScale,
      constraintsEnabled: settings.constraintsEnabled,
      maxBendDegrees: settings.maxBendDegrees,
      previousModelOrientation: previousModelOrientation
        ? [...previousModelOrientation] : null,
      previousModelTranslation: [...previousModelTranslation],
      rootLinearVelocityWorld: [...rootLinearVelocityWorld],
      virtualLinearVelocityWorld: [...virtualLinearVelocityWorld],
      virtualActive,
      accumulator,
      lastTimestamp,
      scheduled: frameId !== null,
      settled,
    };
  }

  function enable(modelTransform) {
    if (!enabled) {
      enabled = true;
      generation += 1;
      accumulator = 0;
      lastTimestamp = null;
      rootLinearVelocityWorld = [...ZERO_VECTOR];
      virtualLinearVelocityWorld = [...ZERO_VECTOR];
      virtualActive = false;
      settled = participants.size === 0;
    }
    setReference(modelTransform);
    syncInputOwnership();
    notify();
    return generation;
  }

  function disable() {
    generation += 1;
    cancelScheduledFrame();
    participants.forEach(participant => participant.onSessionDetached?.());
    participants.clear();
    statuses.clear();
    enabled = false;
    previousModelOrientation = null;
    previousModelTranslation = [...ZERO_VECTOR];
    rootLinearVelocityWorld = [...ZERO_VECTOR];
    virtualLinearVelocityWorld = [...ZERO_VECTOR];
    virtualActive = false;
    accumulator = 0;
    lastTimestamp = null;
    settled = true;
    syncInputOwnership();
    notify();
  }

  function destroy() {
    settings = {...DEFAULT_MODEL_PHYSICS_SETTINGS};
    disable();
  }

  function attach(participant) {
    const key = participantKey(participant);
    if (!enabled || key === undefined || key === null) return false;
    statuses.delete(key);
    participants.set(key, participant);
    participant.onSessionAttached?.(settings);
    settled = false;
    syncInputOwnership();
    notify();
    schedule();
    return true;
  }

  function detach(mesh) {
    const key = resolvedParticipantKey(mesh);
    const participant = participants.get(key);
    if (!participant) {
      statuses.delete(key);
      syncInputOwnership();
      return false;
    }
    participant.onSessionDetached?.();
    participants.delete(key);
    statuses.delete(key);
    if (participants.size === 0) {
      settled = true;
      cancelScheduledFrame();
    }
    syncInputOwnership();
    notify();
    return true;
  }

  function markUnavailable(mesh, reason = 'unavailable') {
    if (!enabled || participants.has(participantKey(mesh))) return;
    statuses.set(mesh, {kind: 'unavailable', reason});
    notify();
  }

  function markFailed(mesh, error) {
    if (!enabled || participants.has(mesh)) return;
    statuses.set(mesh, {
      kind: 'failed',
      error: error instanceof Error ? error.message : String(error || ''),
    });
    notify();
  }

  function clearStatus(mesh) {
    const key = participantKey(mesh);
    let changedStatus = statuses.delete(key);
    if (key !== mesh) changedStatus = statuses.delete(mesh) || changedStatus;
    if (changedStatus) notify();
    return changedStatus;
  }

  function setSettings(patch) {
    const next = normalizedSettings(settings, patch);
    const changedSettings = Object.keys(next).some(key =>
      !Object.is(next[key], settings[key]));
    settings = next;
    if (changedSettings) {
      participants.forEach(participant =>
        participant.onSettingsChanged?.(settings));
      settled = false;
      schedule();
      notify();
    }
    return {...settings};
  }

  function handleModelTransform(detail) {
    const current = transform(detail?.modelTransform);
    if (!current) return false;
    if (!previousModelOrientation) {
      previousModelOrientation = [...current.orientation];
      previousModelTranslation = [...current.translation];
      return false;
    }
    const previousOrientation = [...previousModelOrientation];
    const previousTranslation = [...previousModelTranslation];
    const rotationVector = shortestModelRotationVector(
      previousOrientation, current.orientation);
    const translationDeltaWorld = vectorSubtract(
      current.translation, previousTranslation);
    const velocity = finiteVector(detail?.kinematics?.linearVelocityWorld);
    const hasVelocity = velocity !== null;
    const deltaLinearVelocityWorld = hasVelocity
      ? vectorSubtract(velocity, rootLinearVelocityWorld) : null;
    previousModelOrientation = [...current.orientation];
    previousModelTranslation = [...current.translation];
    if (hasVelocity) rootLinearVelocityWorld = velocity;
    if (!enabled || participants.size === 0) return false;
    let changedParticipant = false;
    participants.forEach(participant => {
      const changedByParticipant = participant.onModelMotion?.({
        rotationVector: [...rotationVector],
        translationDeltaWorld: [...translationDeltaWorld],
        linearVelocityWorld: hasVelocity ? [...velocity] : null,
        deltaLinearVelocityWorld: hasVelocity
          ? [...deltaLinearVelocityWorld] : null,
        previousModelOrientation: previousOrientation,
        modelOrientation: [...current.orientation],
        settings,
      });
      changedParticipant = changedByParticipant || changedParticipant;
    });
    if (changedParticipant || changed(rotationVector)
        || changed(translationDeltaWorld) || hasVelocity) wake();
    return changedParticipant;
  }

  function handleVirtualMotion(detail) {
    if (!enabled) return false;
    const current = vector(detail?.normalizedLinearVelocityWorld);
    const deltaVelocityWorld = vectorSubtract(
      current, virtualLinearVelocityWorld);
    virtualLinearVelocityWorld = [...current];
    virtualActive = detail?.active === true;
    if (participants.size === 0) {
      if (!virtualActive) virtualLinearVelocityWorld = [...ZERO_VECTOR];
      return false;
    }
    let changedParticipant = false;
    participants.forEach(participant => {
      const changedByParticipant = participant.onVirtualMotion?.({
        velocityWorld: [...current],
        deltaVelocityWorld: [...deltaVelocityWorld],
        active: virtualActive,
        modelOrientation: previousModelOrientation
          ? [...previousModelOrientation] : null,
        settings,
      });
      changedParticipant = changedByParticipant || changedParticipant;
    });
    if (!virtualActive) virtualLinearVelocityWorld = [...ZERO_VECTOR];
    if (changedParticipant || changed(deltaVelocityWorld) || !virtualActive) wake();
    return changedParticipant;
  }

  function handleMeshStateChanged(meshes = []) {
    if (!enabled) return false;
    const changedMeshes = Array.isArray(meshes) ? meshes : [meshes];
    const visibleParticipants = [];
    participants.forEach(participant => {
      if (participant.onMeshStateChanged) {
        if (participant.onMeshStateChanged(changedMeshes)) {
          visibleParticipants.push(participant);
        }
        return;
      }
      const mesh = participant.mesh;
      if (!changedMeshes.includes(mesh)
          || participant.isVisible?.() === false) return;
      if (participant.deform?.({
        request: false, invalidateShadow: false, skipHidden: false,
      })) visibleParticipants.push(participant);
    });
    if (!visibleParticipants.length) return false;
    onFrame?.({visibleParticipants, steps: 0});
    return true;
  }

  function reset(modelTransform, {settingsPatch = null} = {}) {
    if (settingsPatch) settings = normalizedSettings(settings, settingsPatch);
    if (!enabled) {
      if (settingsPatch) notify();
      return !!settingsPatch;
    }
    participants.forEach(participant => participant.reset?.(settings));
    accumulator = 0;
    lastTimestamp = null;
    rootLinearVelocityWorld = [...ZERO_VECTOR];
    virtualLinearVelocityWorld = [...ZERO_VECTOR];
    virtualActive = false;
    setReference(modelTransform);
    settled = !virtualActive && [...participants.values()].every(participant =>
      participant.isSettled?.() !== false);
    cancelScheduledFrame();
    notify();
    if (!settled) schedule();
    return true;
  }

  function advance(timestamp) {
    if (!enabled || participants.size === 0) return;
    const currentTimestamp = Number(timestamp);
    if (!Number.isFinite(currentTimestamp)) return;
    if (lastTimestamp === null) {
      lastTimestamp = currentTimestamp;
      return;
    }
    const elapsed = clamp(
      (currentTimestamp - lastTimestamp) / 1000,
      0, MODEL_PHYSICS_MAX_FRAME_DELTA);
    lastTimestamp = currentTimestamp;
    accumulator += elapsed;
    let steps = 0;
    const stepStarted = performanceNow();
    while (accumulator >= MODEL_PHYSICS_STEP
        && steps < MODEL_PHYSICS_MAX_SUBSTEPS) {
      participants.forEach(participant =>
        participant.step?.(MODEL_PHYSICS_STEP, settings));
      accumulator -= MODEL_PHYSICS_STEP;
      steps += 1;
    }
    if (steps) {
      addWeightPhysicsPerformance('physicsStepCount', steps);
      addWeightPhysicsPerformance('physicsStepMs', performanceNow() - stepStarted);
    }
    if (steps === MODEL_PHYSICS_MAX_SUBSTEPS
        && accumulator >= MODEL_PHYSICS_STEP) {
      accumulator = MODEL_PHYSICS_STEP;
    }
    if (!steps) return;

    participants.forEach(participant =>
      participant.updateSettled?.(settings));

    const visibleParticipants = [];
    participants.forEach(participant => {
      if (participant.isVisible?.() === false) return;
      if (participant.deform?.({
        request: false, invalidateShadow: false, skipHidden: true,
      })) visibleParticipants.push(participant);
    });
    if (visibleParticipants.length) {
      onFrame?.({
        visibleParticipants,
        steps,
        timestamp: currentTimestamp,
      });
    }
    settled = !virtualActive && [...participants.values()].every(participant =>
      participant.isSettled?.() !== false);
    if (settled) {
      participants.forEach(participant => participant.onSettled?.());
      lastTimestamp = null;
      cancelScheduledFrame();
    }
    addWeightPhysicsPerformance('physicsFrameCount');
  }

  return {
    enable,
    disable,
    destroy,
    attach,
    detach,
    markUnavailable,
    markFailed,
    clearStatus,
    setSettings,
    getSettings: () => ({...settings}),
    getState,
    getParticipant: mesh => participants.get(resolvedParticipantKey(mesh)) || null,
    handleModelTransform,
    handleVirtualMotion,
    handleMeshStateChanged,
    reset,
    wake,
    advance,
    isScheduled: () => frameId !== null,
  };
}
