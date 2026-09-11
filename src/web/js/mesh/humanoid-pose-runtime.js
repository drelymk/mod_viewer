// Primary humanoid pose session. The control rig is the only IK driver;
// ModelJoint rotations remain a separate manual pose layer.

import {RIG_LIMB_ROLES} from './weight-runtime.js';
import {
  HUMANOID_CONTROL_KEYS, HUMANOID_CONTROL_LIMB_ROLES,
} from './humanoid-control-rig.js';

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
    const wasEnabled = modelRigState.ikEnabled === true;
    const validationRole = enabled && !wasEnabled
      ? 'left_arm' : modelRigState.activeLimbRole;
    const primary = getPrimaryLimb(validationRole);
    if (enabled && !primary.available) {
      modelRigState.ikEnabled = false;
      notifyChanged();
      requestRender();
      return false;
    }
    const next = !!enabled && primary.available;
    if (modelRigState.ikEnabled === next) return next;
    modelRigState.ikEnabled = next;
    if (next && !wasEnabled) {
      modelRigState.activeLimbRole = 'left_arm';
      modelRigState.selectedHumanoidControlKey = 'leftHand';
    }
    notifyChanged();
    requestRender();
    return next;
  }

  function selectControl(controlKey) {
    if (!modelRigState.ikEnabled
        || !HUMANOID_CONTROL_KEYS.includes(controlKey)) return false;
    const role = HUMANOID_CONTROL_LIMB_ROLES[controlKey] || null;
    const changed = modelRigState.selectedHumanoidControlKey !== controlKey
      || (role && modelRigState.activeLimbRole !== role);
    modelRigState.selectedHumanoidControlKey = controlKey;
    if (role) modelRigState.activeLimbRole = role;
    if (changed) {
      notifyChanged();
      requestRender();
    }
    return true;
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

  return {setActiveLimbRole, setIkEnabled, selectControl, solveTarget};
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
export function selectHumanoidControl(controlKey) {
  return session().selectControl(controlKey);
}
export function solveRigIkTarget(target, options = {}) {
  return session().solveTarget(target, options);
}
