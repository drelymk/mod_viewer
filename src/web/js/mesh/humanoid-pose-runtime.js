// Primary humanoid pose session. The control rig is the only IK driver;
// ModelJoint rotations remain a separate manual pose layer.

import {RIG_LIMB_ROLES} from './weight-runtime.js';

let activeSession = null;

function createSession({modelRigState, getModelRig, getPrimaryLimb,
    solveControlIk, mergeLimbPose, applyPose, notifyChanged, requestRender} = {}) {
  function setActiveLimbRole(role) {
    const next = RIG_LIMB_ROLES.includes(role) ? role : null;
    if (!next || modelRigState.activeLimbRole === next) return next || false;
    modelRigState.activeLimbRole = next;
    notifyChanged();
    requestRender();
    return next;
  }

  function setIkEnabled(enabled) {
    const primary = getPrimaryLimb();
    if (enabled && !primary.available) {
      modelRigState.ikEnabled = false;
      notifyChanged();
      requestRender();
      return false;
    }
    const next = !!enabled && primary.available;
    if (modelRigState.ikEnabled === next) return next;
    modelRigState.ikEnabled = next;
    notifyChanged();
    requestRender();
    return next;
  }

  function solveTarget(target, options = {}) {
    const rig = getModelRig();
    const primary = getPrimaryLimb();
    if (!rig?.humanoidControlRig?.accepted || !modelRigState.ikEnabled
        || !primary.available) return false;
    const previousPose = modelRigState.humanoidPose || {};
    const solved = solveControlIk({
      controlRig: rig.humanoidControlRig,
      posedControls: previousPose,
      role: primary.role,
      target,
      bendSign: primary.bendSign,
    });
    if (!solved.positions) return solved;
    modelRigState.humanoidPose = mergeLimbPose(
      previousPose, solved.positions, primary.keys);
    const applied = applyPose({dragging: options?.dragging === true});
    if (!options?.dragging) notifyChanged();
    return {...solved, applied, controlRig: 'humanoid'};
  }

  return {setActiveLimbRole, setIkEnabled, solveTarget};
}

export function initializeHumanoidPoseRuntime(options) {
  activeSession = createSession(options);
}

function session() {
  if (!activeSession) throw new Error('Humanoid pose session is not initialized.');
  return activeSession;
}

export function setRigActiveLimbRole(role) {
  return session().setActiveLimbRole(role);
}
export function setRigIkEnabled(enabled) { return session().setIkEnabled(enabled); }
export function solveRigIkTarget(target, options = {}) {
  return session().solveTarget(target, options);
}
