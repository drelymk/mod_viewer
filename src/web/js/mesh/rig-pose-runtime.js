// Owns the public model-pose transaction boundary and its validation. The
// composition root supplies only the low-level rig mutations and deformation
// operation, keeping pose state transitions here.

import * as THREE from 'three';

let activeSession = null;

function strictQuaternion(value) {
  const values = value?.isQuaternion
    ? [value.x, value.y, value.z, value.w]
    : Array.isArray(value) || ArrayBuffer.isView(value)
      ? [...value].slice(0, 4).map(Number)
      : [value?.x, value?.y, value?.z, value?.w].map(Number);
  if (values.length !== 4 || !values.every(Number.isFinite)) return null;
  const quaternion = new THREE.Quaternion(...values);
  if (!Number.isFinite(quaternion.lengthSq())
      || quaternion.lengthSq() <= 1e-12) return null;
  return quaternion.normalize();
}

function rotationEntries(rotationsByJointId) {
  if (rotationsByJointId instanceof Map) return [...rotationsByJointId.entries()];
  return Object.entries(rotationsByJointId || {});
}

function createSession({state, getRig, getJoint, getParent,
    getComponentForJoint, getSourceRig, getSourceComponent,
    getModelJointId, getRepresentativeMember, applyPose,
    setComponentRoot, resetModelPose,
    hasActivePhysics, finalizeAllPoseBounds, wakePhysics,
    notifyChanged, notifyPoseChanged, requestRender, rigPresetState} = {}) {
  function getFrame(jointId) {
    const id = Number(jointId);
    const rig = getRig();
    const joint = getJoint(id);
    if (!rig || !joint || !rig.componentByJointId.has(id)) return null;
    const cache = rig.poseFrameCache.get(id);
    const parentId = getParent(id, rig);
    const parentRotation = parentId === null ? new THREE.Quaternion()
      : rig.poseRotations.get(parentId)?.clone() || new THREE.Quaternion();
    const boneRotation = rig.poseRotations.get(id)?.clone()
      || new THREE.Quaternion();
    const restRotation = rig.restFrameByJointId.get(id)?.clone()
      || new THREE.Quaternion();
    return {
      pivot: (cache?.pivot || new THREE.Vector3(...(
        rig.jointPivotByJointId.get(id) || joint.restPivot || [0, 0, 0])))
        .toArray(),
      center: (cache?.center || new THREE.Vector3(...(
        rig.centerByJointId.get(id) || joint.restCenter || [0, 0, 0])))
        .toArray(),
      parentRotation: parentRotation.normalize().toArray(),
      boneRotation: boneRotation.normalize().toArray(),
      restRotation: restRotation.normalize().toArray(),
      gizmoRotation: boneRotation.clone().multiply(restRotation).normalize()
        .toArray(),
    };
  }

  function setRotations(rotationsByJointId, options = {}) {
    const rig = getRig();
    if (!rig || !state.loaded) return false;
    const updates = [];
    const seen = new Set();
    for (const [rawId, value] of rotationEntries(rotationsByJointId)) {
      const jointId = Number(rawId);
      const joint = getJoint(jointId);
      const component = getComponentForJoint(jointId);
      if (!Number.isInteger(jointId) || !joint || !component
          || seen.has(jointId)) {
        state.pickStatus = 'Could not apply the Rig pose.';
        notifyChanged();
        return false;
      }
      if (Number(component.rootId) === jointId) {
        state.pickStatus = 'Component root / anchor cannot be rotated.';
        notifyChanged();
        return false;
      }
      const quaternion = strictQuaternion(value);
      if (!quaternion) {
        state.pickStatus = 'Could not apply the Rig pose.';
        notifyChanged();
        return false;
      }
      seen.add(jointId);
      updates.push([jointId, quaternion]);
    }
    if (!updates.length) return false;

    updates.forEach(([jointId, quaternion]) => {
      rig.poseRotationByJointId.set(jointId, quaternion);
    });
    const selectedId = options?.selectedJointId === null
      || options?.selectedJointId === undefined
      ? null : Number(options.selectedJointId);
    if (Number.isInteger(selectedId) && getJoint(selectedId)) {
      state.selectedJointId = selectedId;
    }
    const dragging = options?.dragging === true;
    applyPose({dragging});
    state.pickStatus = '';
    if (dragging) {
      const selectedJoint = getJoint(state.selectedJointId);
      const member = getRepresentativeMember(selectedJoint);
      if (member) notifyPoseChanged(
        getSourceRig(member.sourceKey), member.boneId,
        updates.map(([id]) => id));
      else notifyChanged();
    } else {
      notifyChanged();
    }
    return true;
  }

  function setRotation(jointId, quaternion, options = {}) {
    const joint = getJoint(jointId);
    const member = getRepresentativeMember(joint);
    const sourceRig = member && getSourceRig(member.sourceKey);
    const sourceComponent = member && getSourceComponent(
      sourceRig, member.boneId);
    const resolvedJointId = member && getModelJointId(
      member.sourceKey, member.boneId);
    const componentId = Number(getComponentForJoint(resolvedJointId)?.componentId);
    const modelComponent = Number.isInteger(componentId)
      ? getRig()?.components?.[componentId] : null;
    if (!sourceRig || !sourceComponent || !Number.isInteger(resolvedJointId)
        || !modelComponent || !sourceRig.boneIds.includes(Number(member.boneId))) {
      return false;
    }
    if (Number(modelComponent.rootId) === resolvedJointId) {
      state.pickStatus = 'Component root / anchor cannot be rotated.';
      notifyChanged();
      return false;
    }
    return setRotations(new Map([[resolvedJointId, quaternion]]), {
      ...options, selectedJointId: resolvedJointId,
    });
  }

  return {
    getFrame,
    finishPose(jointId) {
      const member = getRepresentativeMember(getJoint(jointId));
      if (!member) return false;
      const rig = getSourceRig(member.sourceKey);
      const id = Number(member.boneId);
      const resolvedJointId = getModelJointId(member.sourceKey, id);
      if (!rig || !Number.isInteger(id) || !rig.boneIds.includes(id)
          || !Number.isInteger(resolvedJointId)) return false;
      if (!hasActivePhysics()) finalizeAllPoseBounds();
      if (hasActivePhysics()) wakePhysics();
      notifyChanged();
      requestRender();
      return true;
    },
    resetJoint(jointId) {
      const member = getRepresentativeMember(getJoint(jointId));
      if (!member) return false;
      const rig = getSourceRig(member.sourceKey);
      const id = Number(member.boneId);
      const resolvedJointId = getModelJointId(member.sourceKey, id);
      if (!rig || !rig.boneIds.includes(id)
          || !Number.isInteger(resolvedJointId)) return false;
      getRig()?.poseRotationByJointId.delete(resolvedJointId);
      applyPose();
      notifyChanged();
      return true;
    },
    resetPose() {
      const changed = resetModelPose({request: false});
      const presetWasSelected = rigPresetState.selectedPresetId !== null
        || rigPresetState.lastApplyResult !== null;
      rigPresetState.selectedPresetId = null;
      rigPresetState.lastApplyResult = null;
      state.pickStatus = '';
      notifyChanged();
      requestRender();
      return changed || presetWasSelected;
    },
    setRoot(jointId) {
      const member = getRepresentativeMember(getJoint(jointId));
      return member ? setComponentRoot(member.sourceKey, member.boneId) : false;
    },
    setRotation,
    setStatus(message = '') {
      state.pickStatus = String(message || '');
      notifyChanged();
      return state.pickStatus;
    },
  };
}

export function initializeRigPoseRuntime(options) {
  activeSession = createSession(options);
  return activeSession;
}

function session() {
  if (!activeSession) throw new Error('Rig pose runtime is not initialized.');
  return activeSession;
}

export function finishRigJointPose(jointId) { return session().finishPose(jointId); }
export function getRigJointPoseFrame(jointId) { return session().getFrame(jointId); }
export function resetRigJoint(jointId) { return session().resetJoint(jointId); }
export function resetRigPose() { return session().resetPose(); }
export function setRigJointRoot(jointId) { return session().setRoot(jointId); }
export function setRigJointRotation(jointId, quaternion, options = {}) {
  return session().setRotation(jointId, quaternion, options);
}
export function setRigPoseControlStatus(message = '') {
  return session().setStatus(message);
}
