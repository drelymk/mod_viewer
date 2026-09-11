// Captures the frozen rest-to-display projection used by Humanoid Rig edit
// mode. This module is Three.js-only and has no application or DOM state.

import * as THREE from 'three';
import {
  HUMANOID_CONTROL_DRIVER_IDS, HUMANOID_CONTROL_KEYS,
} from './humanoid-control-rig.js';

const IDENTITY = new THREE.Matrix4();

function numberId(value) {
  const id = Number(value);
  return Number.isInteger(id) ? id : null;
}

function point(value) {
  if (value?.isVector3) return value.clone();
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    return new THREE.Vector3(Number(value[0]) || 0, Number(value[1]) || 0,
      Number(value[2]) || 0);
  }
  return new THREE.Vector3(Number(value?.x) || 0, Number(value?.y) || 0,
    Number(value?.z) || 0);
}

function parentFor(modelRig, jointId) {
  const id = numberId(jointId);
  const componentId = modelRig?.componentByJointId?.get?.(id);
  const component = Number.isInteger(Number(componentId))
    ? modelRig?.components?.[Number(componentId)] : null;
  const parent = component?.parentById?.[id];
  return parent === null || parent === undefined ? null : numberId(parent);
}

function jointPivot(modelRig, jointId) {
  const id = numberId(jointId);
  const joint = modelRig?.joints?.find(item =>
    numberId(item?.jointId) === id);
  return modelRig?.jointPivotByJointId?.get?.(id)
    || joint?.restPivot || joint?.restCenter || null;
}

/**
 * Capture one immutable pose projection per HumanoidControlRig control.
 * Explicit mappings win; otherwise the nearest ModelJoint already owned by
 * the control's existing HumanoidBinding driver is used as a transient
 * articulation anchor. The anchor is never persisted by edit mode.
 */
export function captureHumanoidRigEditPose({modelRig,
    mappedJointIdByControl} = {}) {
  const controlRig = modelRig?.humanoidControlRig;
  if (!controlRig) return {};

  const boundByDriver = new Map();
  modelRig?.humanoidBinding?.jointBindings?.forEach?.((entry, jointIdValue) => {
    const jointId = numberId(jointIdValue);
    const driverId = String(entry?.driverId || '');
    const pivot = jointId === null ? null : jointPivot(modelRig, jointId);
    if (jointId === null || !driverId || !pivot) return;
    const list = boundByDriver.get(driverId) || [];
    list.push({jointId, pivot: point(pivot)});
    boundByDriver.set(driverId, list);
  });
  boundByDriver.forEach(list => list.sort((left, right) =>
    left.jointId - right.jointId));

  const result = {};
  HUMANOID_CONTROL_KEYS.forEach(key => {
    const rest = point(controlRig.controls?.[key]?.position);
    const explicit = numberId(mappedJointIdByControl?.get?.(key)?.jointId);
    let anchorJointId = explicit;
    if (anchorJointId === null) {
      const candidates = boundByDriver.get(
        HUMANOID_CONTROL_DRIVER_IDS[key]) || [];
      anchorJointId = candidates.reduce((best, candidate) => {
        if (!best) return candidate;
        const distance = candidate.pivot.distanceToSquared(rest);
        const bestDistance = best.pivot.distanceToSquared(rest);
        return distance < bestDistance
          || (distance === bestDistance && candidate.jointId < best.jointId)
          ? candidate : best;
      }, null)?.jointId ?? null;
    }

    const parentId = anchorJointId === null
      ? null : parentFor(modelRig, anchorJointId);
    const restToDisplay = parentId === null
      ? IDENTITY : modelRig.poseTransforms?.get?.(parentId) || IDENTITY;
    const displayToRest = restToDisplay.clone().invert();
    const display = rest.clone().applyMatrix4(restToDisplay);
    const posedAnchor = explicit === null || anchorJointId === null ? null
      : modelRig.poseFrameCache?.get?.(anchorJointId)?.pivot;
    if (posedAnchor) display.copy(posedAnchor);
    result[key] = {
      anchorJointId,
      restToDisplay: restToDisplay.elements.slice(),
      displayToRest: displayToRest.elements.slice(),
      displayPosition: display.toArray(),
    };
  });
  return result;
}
