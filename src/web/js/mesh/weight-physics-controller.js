// Physics controls exposed by the Weight/Rig panel. Integration stays in the
// existing runtime; this module owns the narrow UI/session adapter.

let activeController = null;

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
