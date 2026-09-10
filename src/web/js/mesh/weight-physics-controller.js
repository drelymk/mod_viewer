// Weight-tab physics session. This module owns both the panel controls and
// the participant lifecycle used by the model weight session.

let activeController = null;

export function createWeightPhysicsCoordinator({modelPhysicsSession,
    modelWeightState, states, knownMeshes, sourcePhysicsRigs,
    selectedBoneCount, eligibleSkinningMesh, createSourcePhysicsRig,
    createSourcePhysicsParticipant, getModelTransformState,
    invalidateCharacterShadowGeometry, notifyModelRigChanged, requestRender,
    defaults} = {}) {
  function disable() {
    if (!modelPhysicsSession.getState().enabled) return false;
    modelPhysicsSession.disable();
    notifyModelRigChanged();
    return true;
  }

  function syncParticipants(changedSourceKeys = null) {
    if (!modelPhysicsSession.getState().enabled) return;
    if (!selectedBoneCount(modelWeightState.selectedBonesBySource)) {
      disable();
      return;
    }
    const groups = new Map();
    for (const mesh of knownMeshes) {
      const state = states.get(mesh);
      if (!eligibleSkinningMesh(mesh)) {
        if (state?.error) {
          state.physicsParticipantStatus = 'failed';
          state.physicsParticipantError = state.error;
          modelPhysicsSession.markFailed(mesh, state.error);
        }
        continue;
      }
      if (!state?.loaded || !state.skinningSourceKey) {
        if (state?.error) {
          state.physicsParticipantStatus = 'failed';
          state.physicsParticipantError = state.error;
          modelPhysicsSession.markFailed(mesh, state.error);
        }
        continue;
      }
      modelPhysicsSession.clearStatus(mesh);
      const members = groups.get(state.skinningSourceKey) || [];
      members.push(mesh);
      groups.set(state.skinningSourceKey, members);
    }
    const affected = changedSourceKeys
      ? new Set(changedSourceKeys)
      : new Set([...groups.keys(), ...sourcePhysicsRigs.keys()]);
    for (const sourceKey of affected) {
      if (modelPhysicsSession.getParticipant(sourceKey)) {
        modelPhysicsSession.detach(sourceKey);
      }
      sourcePhysicsRigs.delete(sourceKey);
    }

    for (const sourceKey of [...sourcePhysicsRigs.keys()]) {
      if (!groups.has(sourceKey)
          || !modelWeightState.selectedBonesBySource.has(sourceKey)) {
        modelPhysicsSession.detach(sourceKey);
        sourcePhysicsRigs.delete(sourceKey);
      }
    }

    let attached = false;
    for (const [sourceKey, members] of groups) {
      const selected = modelWeightState.selectedBonesBySource.get(sourceKey);
      members.forEach(mesh => {
        const state = states.get(mesh);
        if (state) {
          state.physicsParticipantStatus = 'not-selected';
          state.physicsParticipantError = null;
        }
      });
      if (!selected?.size) continue;
      const rig = sourcePhysicsRigs.get(sourceKey)
        || createSourcePhysicsRig(sourceKey, members);
      sourcePhysicsRigs.set(sourceKey, rig);
      if (!rig.physicsForest) continue;
      if (!modelPhysicsSession.getParticipant(sourceKey)) {
        attached = modelPhysicsSession.attach(
          createSourcePhysicsParticipant(rig)) || attached;
      }
    }
    if (attached) {
      modelPhysicsSession.wake();
      invalidateCharacterShadowGeometry({request: false});
      requestRender();
    }
    notifyModelRigChanged();
  }

  function syncToSelection(changedSourceKeys = null) {
    const shouldEnable = modelWeightState.loaded
      && selectedBoneCount(modelWeightState.selectedBonesBySource) > 0;
    const enabled = modelPhysicsSession.getState().enabled;
    if (!shouldEnable) {
      if (enabled) disable();
      return false;
    }
    if (!enabled) {
      modelPhysicsSession.enable(getModelTransformState());
    }
    syncParticipants(changedSourceKeys);
    return true;
  }

  function reset() {
    const physicsDefaults = defaults || {};
    return modelPhysicsSession.reset(getModelTransformState(), {
      settingsPatch: {
        frequencyHz: physicsDefaults.frequencyHz,
        dampingRatio: physicsDefaults.dampingRatio,
        angularResponse: physicsDefaults.angularResponse,
        translationResponse: physicsDefaults.translationResponse,
        velocityResponse: physicsDefaults.velocityResponse,
        gravityEnabled: physicsDefaults.gravityEnabled,
        gravityScale: physicsDefaults.gravityScale,
        constraintsEnabled: physicsDefaults.constraintsEnabled,
        maxBendDegrees: physicsDefaults.maxBendDegrees,
      },
    });
  }

  return {disable, syncParticipants, syncToSelection, reset};
}

function createController({modelPhysicsSession, reset} = {}) {
  const setNumber = (key, value) => {
    const next = Number(value);
    if (!Number.isFinite(next)) return false;
    modelPhysicsSession.setSettings({[key]: next});
    return true;
  };
  return {
    getState: () => modelPhysicsSession.getState(),
    reset,
    setFrequency: value => setNumber('frequencyHz', value),
    setDamping: value => setNumber('dampingRatio', value),
    setMotionStrength: value => setNumber('angularResponse', value),
    setLinearMotionStrength: value => setNumber('translationResponse', value),
    setContinuousLinearResponse: value => setNumber('velocityResponse', value),
    setGravityEnabled(enabled) {
      modelPhysicsSession.setSettings({gravityEnabled: !!enabled});
      return !!enabled;
    },
    setGravityScale: value => setNumber('gravityScale', value),
    setConstraintsEnabled(enabled) {
      modelPhysicsSession.setSettings({constraintsEnabled: !!enabled});
      return !!enabled;
    },
    setMaxBendDegrees: value => setNumber('maxBendDegrees', value),
  };
}

export function initializeWeightPhysicsController(options) {
  activeController = createController(options);
}

function controller() {
  if (!activeController) throw new Error('Weight physics controller is not initialized.');
  return activeController;
}

export function getModelPhysicsState() { return controller().getState(); }
export function resetModelPhysics() { return controller().reset(); }
export function setPhysicsFrequency(value) { return controller().setFrequency(value); }
export function setPhysicsDamping(value) { return controller().setDamping(value); }
export function setPhysicsMotionStrength(value) {
  return controller().setMotionStrength(value);
}
export function setPhysicsLinearMotionStrength(value) {
  return controller().setLinearMotionStrength(value);
}
export function setPhysicsContinuousLinearResponse(value) {
  return controller().setContinuousLinearResponse(value);
}
export function setPhysicsGravityEnabled(value) {
  return controller().setGravityEnabled(value);
}
export function setPhysicsGravityScale(value) {
  return controller().setGravityScale(value);
}
export function setPhysicsConstraintsEnabled(value) {
  return controller().setConstraintsEnabled(value);
}
export function setPhysicsMaxBendDegrees(value) {
  return controller().setMaxBendDegrees(value);
}
