import * as THREE from 'three';
import {HUMANOID_CONTROL_DRIVER_IDS} from './humanoid-control-rig.js';

// The control rig owns the semantic topology. Mapped controls claim their
// exact ModelJoints, unmapped controls get point-radius seeds, and remaining
// ownership comes only from parent-to-child inheritance.
export const HUMANOID_DRIVER_SEGMENTS = Object.freeze([
  {id: 'torso', role: 'torso', start: 'pelvis', end: 'chest'},
  {id: 'neck', role: 'torso', start: 'chest', end: 'neck'},
  {id: 'head', role: 'torso', start: 'neck', end: 'head'},
  {id: 'left_chest_shoulder', role: 'left_arm', start: 'chest',
    end: 'leftShoulder'},
  {id: 'right_chest_shoulder', role: 'right_arm', start: 'chest',
    end: 'rightShoulder'},
  {id: 'left_upper_arm', role: 'left_arm', start: 'leftShoulder',
    end: 'leftElbow'},
  {id: 'left_lower_arm', role: 'left_arm', start: 'leftElbow',
    end: 'leftHand'},
  {id: 'right_upper_arm', role: 'right_arm', start: 'rightShoulder',
    end: 'rightElbow'},
  {id: 'right_lower_arm', role: 'right_arm', start: 'rightElbow',
    end: 'rightHand'},
  {id: 'left_pelvis_hip', role: 'left_leg', start: 'pelvis', end: 'leftHip'},
  {id: 'right_pelvis_hip', role: 'right_leg', start: 'pelvis',
    end: 'rightHip'},
  {id: 'left_upper_leg', role: 'left_leg', start: 'leftHip', end: 'leftKnee'},
  {id: 'left_lower_leg', role: 'left_leg', start: 'leftKnee',
    end: 'leftFoot'},
  {id: 'right_upper_leg', role: 'right_leg', start: 'rightHip',
    end: 'rightKnee'},
  {id: 'right_lower_leg', role: 'right_leg', start: 'rightKnee',
    end: 'rightFoot'},
]);

const EPSILON = 1e-8;
// One height-normalized radius is shared by anchor reservation and additional
// direct seeds. Keep it small: this is an ownership gate, not a limb-width
// classifier.
const DEFAULT_POINT_RADIUS_RATIO = 0.08;
const CONTROL_ORDER = Object.freeze(Object.keys(HUMANOID_CONTROL_DRIVER_IDS));

function numberId(value) {
  const result = Number(value);
  return Number.isInteger(result) ? result : null;
}

function vector(value, fallback = [0, 0, 0]) {
  if (value?.isVector3) return value.clone();
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    return new THREE.Vector3(
      Number.isFinite(Number(value[0])) ? Number(value[0]) : fallback[0],
      Number.isFinite(Number(value[1])) ? Number(value[1]) : fallback[1],
      Number.isFinite(Number(value[2])) ? Number(value[2]) : fallback[2],
    );
  }
  return new THREE.Vector3(
    Number.isFinite(Number(value?.x)) ? Number(value.x) : fallback[0],
    Number.isFinite(Number(value?.y)) ? Number(value.y) : fallback[1],
    Number.isFinite(Number(value?.z)) ? Number(value.z) : fallback[2],
  );
}

function vectorFromCollection(collection, id) {
  const value = collection instanceof Map
    ? collection.get(id) ?? collection.get(String(id)) : collection?.[id];
  return value === undefined ? null : vector(value);
}

function quaternion(value) {
  if (value?.isQuaternion) return value.clone().normalize();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 4).map(Number)
    : [value?.x, value?.y, value?.z, value?.w].map(Number);
  return values.length === 4 && values.every(Number.isFinite)
    ? new THREE.Quaternion(...values).normalize() : new THREE.Quaternion();
}

function controlPoint(controlRig, key, posedControls = null) {
  const value = posedControls?.[key] ?? controlRig?.controls?.[key];
  return vector(value?.position ?? value, [0, 0, 0]);
}

function frameForSegment(start, end, controlRig) {
  const y = end.clone().sub(start);
  const length = y.length();
  if (length <= EPSILON) return null;
  y.multiplyScalar(1 / length);
  const semantic = controlRig?.frame || {};
  const forward = vector(semantic.forward, [0, 0, 1]).normalize();
  const right = vector(semantic.right, [1, 0, 0]).normalize();
  let z = forward.clone().addScaledVector(y, -forward.dot(y));
  if (z.lengthSq() <= EPSILON) z = right.clone().addScaledVector(y,
    -right.dot(y));
  if (z.lengthSq() <= EPSILON) {
    z = Math.abs(y.x) < .8
      ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1);
    z.addScaledVector(y, -z.dot(y));
  }
  z.normalize();
  const x = y.clone().cross(z).normalize();
  z = x.clone().cross(y).normalize();
  const matrix = new THREE.Matrix4().makeBasis(x, y, z);
  matrix.setPosition(start);
  return {matrix, start: start.clone(), end: end.clone(), length};
}

function restJointWorldMatrix(modelRig, jointId) {
  const id = numberId(jointId);
  const indexedJoint = id === null ? null : modelRig?.joints?.[id];
  const joint = indexedJoint && Number(indexedJoint.jointId) === id
    ? indexedJoint : modelRig?.joints?.find?.(item =>
      Number(item?.jointId) === id);
  if (id === null || !joint) return null;
  const pivot = vectorFromCollection(modelRig?.jointPivotByJointId, id)
    || vector(joint.restPivot ?? joint.restCenter);
  const rotation = modelRig?.restFrameByJointId instanceof Map
    ? quaternion(modelRig.restFrameByJointId.get(id))
    : quaternion(joint.restFrame);
  const matrix = new THREE.Matrix4().compose(
    pivot, rotation, new THREE.Vector3(1, 1, 1));
  return matrix;
}

function pointForJoint(modelRig, jointId) {
  const id = numberId(jointId);
  if (id === null) return null;
  const indexedJoint = modelRig?.joints?.[id];
  const joint = indexedJoint && Number(indexedJoint.jointId) === id
    ? indexedJoint : modelRig?.joints?.find?.(item =>
      Number(item?.jointId) === id);
  return vectorFromCollection(modelRig?.jointPivotByJointId, id)
    || vector(joint?.restPivot ?? joint?.restCenter, [0, 0, 0]);
}

function directBindingFor(modelRig, jointId, driverMap, owner) {
  const restJointWorld = restJointWorldMatrix(modelRig, jointId);
  const driverId = HUMANOID_CONTROL_DRIVER_IDS[owner?.controlKey];
  const driver = driverMap.get(driverId);
  if (!restJointWorld || !driver) return null;
  const localMatrix = driver.matrix.clone().invert().multiply(restJointWorld);
  return {
    type: 'driver',
    driverId,
    localMatrix,
    restJointWorld,
    bindingMethod: owner?.bindingMethod || 'inherited_control',
    controlKey: owner?.controlKey || null,
    ownerSource: owner?.source || null,
    anchorJointId: numberId(owner?.anchorJointId),
    inheritedFromJointId: numberId(owner?.inheritedFromJointId),
  };
}

function allJointIds(modelRig) {
  return (modelRig?.joints || []).map(joint => numberId(joint?.jointId))
    .filter(Number.isInteger).sort((left, right) => left - right);
}

function childIdsForComponent(component, jointId) {
  const children = collectionValue(component?.childrenById, numberId(jointId));
  return (children || []).map(numberId).filter(Number.isInteger)
    .sort((left, right) => left - right);
}

function collectionValue(collection, id) {
  return collection instanceof Map
    ? collection.get(id) ?? collection.get(String(id))
    : collection?.[id];
}

function parentJointId(component, jointId) {
  const parent = collectionValue(component?.parentById, numberId(jointId));
  return parent === null || parent === undefined ? null : numberId(parent);
}

function mappedControlEntries(controlMappings) {
  if (!(controlMappings instanceof Map)) return [];
  const entries = [...controlMappings.entries()]
    .filter(([key, mapping]) => CONTROL_ORDER.includes(
      mapping?.controlKey || key))
    .map(([key, mapping]) => ({
      controlKey: mapping?.controlKey || key,
      jointId: numberId(mapping?.jointId),
    })).sort((left, right) => CONTROL_ORDER.indexOf(left.controlKey)
      - CONTROL_ORDER.indexOf(right.controlKey));
  const byJointId = new Map();
  entries.forEach(entry => {
    if (entry.jointId === null) return;
    const owners = byJointId.get(entry.jointId) || [];
    owners.push(entry);
    byJointId.set(entry.jointId, owners);
  });
  const invalidJointIds = new Set();
  byJointId.forEach((owners, jointId) => {
    if (owners.length <= 1) return;
    invalidJointIds.add(jointId);
  });
  return entries.filter(entry => entry.jointId !== null
    && !invalidJointIds.has(entry.jointId));
}

function controlOrder(controlKey) {
  const index = CONTROL_ORDER.indexOf(controlKey);
  return index < 0 ? CONTROL_ORDER.length : index;
}

function pointJointCandidate(modelRig, controlRig, controlKey, jointId,
    height) {
  const point = controlPoint(controlRig, controlKey);
  const joint = pointForJoint(modelRig, jointId);
  if (!joint) return null;
  const distance = point.distanceTo(joint);
  return {
    controlKey,
    jointId,
    distanceRatio: distance / Math.max(height, EPSILON),
  };
}

function candidatesForControl(modelRig, controlRig, controlKey, jointIds,
    height, radius) {
  return jointIds.map(jointId => pointJointCandidate(
    modelRig, controlRig, controlKey, jointId, height))
    .filter(candidate => candidate
      && candidate.distanceRatio <= radius + EPSILON)
    .sort((left, right) => left.distanceRatio - right.distanceRatio
      || left.jointId - right.jointId);
}

function directOwnerFor(controlKey, source, bindingMethod, candidate = null,
    anchorJointId = null) {
  return {
    controlKey,
    source,
    bindingMethod,
    anchorJointId: numberId(anchorJointId ?? candidate?.jointId),
  };
}

function assignAutomaticAnchors({automaticControls, candidatesByControl,
    directOwnerByJointId, controlState}) {
  const nextCandidateByControl = new Map(
    automaticControls.map(controlKey => [controlKey, 0]));
  let pending = [...automaticControls];

  while (pending.length) {
    const proposals = new Map();
    const nextPending = [];
    pending.forEach(controlKey => {
      const candidates = candidatesByControl.get(controlKey) || [];
      let index = nextCandidateByControl.get(controlKey) || 0;
      while (index < candidates.length
          && directOwnerByJointId.has(candidates[index].jointId)) index += 1;
      nextCandidateByControl.set(controlKey, index);
      if (index >= candidates.length) {
        controlState.get(controlKey).source = 'unresolved';
        return;
      }
      const candidate = candidates[index];
      const proposalsForJoint = proposals.get(candidate.jointId) || [];
      proposalsForJoint.push(candidate);
      proposals.set(candidate.jointId, proposalsForJoint);
      nextPending.push(controlKey);
    });
    if (!proposals.size) break;

    proposals.forEach((jointProposals, jointId) => {
      jointProposals.sort((left, right) => left.distanceRatio
        - right.distanceRatio || controlOrder(left.controlKey)
        - controlOrder(right.controlKey));
      const winner = jointProposals[0];
      if (!directOwnerByJointId.has(jointId)) {
        const state = controlState.get(winner.controlKey);
        directOwnerByJointId.set(jointId, directOwnerFor(
          winner.controlKey, 'automatic', 'automatic_control_mapping',
          winner, null));
        state.source = 'automatic';
        state.anchorJointId = jointId;
        state.directSeedCount += 1;
        state.distanceRatio = winner.distanceRatio;
      }
      nextCandidateByControl.set(winner.controlKey,
        (nextCandidateByControl.get(winner.controlKey) || 0) + 1);
    });
    pending = nextPending.filter(controlKey => {
      const candidates = candidatesByControl.get(controlKey) || [];
      return (nextCandidateByControl.get(controlKey) || 0) < candidates.length
        && controlState.get(controlKey).anchorJointId === null;
    });
  }
}

function assignAutomaticExtraSeeds({automaticControls, candidatesByControl,
    directOwnerByJointId, controlState}) {
  const candidatesByJoint = new Map();
  automaticControls.forEach(controlKey => {
    (candidatesByControl.get(controlKey) || []).forEach(candidate => {
      const candidates = candidatesByJoint.get(candidate.jointId) || [];
      candidates.push(candidate);
      candidatesByJoint.set(candidate.jointId, candidates);
    });
  });
  [...candidatesByJoint.keys()].sort((left, right) => left - right)
    .forEach(jointId => {
      if (directOwnerByJointId.has(jointId)) return;
      const winner = (candidatesByJoint.get(jointId) || [])
        .filter(candidate => !directOwnerByJointId.has(candidate.jointId))
        .sort((left, right) => left.distanceRatio - right.distanceRatio
          || controlOrder(left.controlKey) - controlOrder(right.controlKey))[0];
      if (!winner) return;
      const state = controlState.get(winner.controlKey);
      if (!state || state.anchorJointId === null) return;
      directOwnerByJointId.set(jointId, directOwnerFor(
        winner.controlKey, 'automatic', 'automatic_control_mapping',
        winner, state.anchorJointId));
      state.directSeedCount += 1;
    });
}

function inheritedOwners(modelRig, directOwnerByJointId) {
  const ownerByJointId = new Map(directOwnerByJointId);
  const visited = new Set();

  function walk(component, jointId, parentOwner = null, parentId = null) {
    const stack = [{jointId, parentOwner, parentId}];
    while (stack.length) {
      const current = stack.pop();
      const id = numberId(current.jointId);
      if (id === null || visited.has(id)) continue;
      visited.add(id);
      const directOwner = directOwnerByJointId.get(id);
      const owner = directOwner || current.parentOwner;
      if (owner && !directOwner) {
        ownerByJointId.set(id, {
          ...owner,
          bindingMethod: 'inherited_control',
          inheritedFromJointId: numberId(current.parentId),
        });
      }
      const nextParentOwner = owner ? {...owner} : null;
      const children = childIdsForComponent(component, id);
      for (let index = children.length - 1; index >= 0; index -= 1) {
        stack.push({jointId: children[index], parentOwner: nextParentOwner,
          parentId: id});
      }
    }
  }

  (modelRig?.components || []).forEach(component => {
    const nodeIds = (component?.nodeIds || []).map(numberId)
      .filter(Number.isInteger).sort((left, right) => left - right);
    const nodeSet = new Set(nodeIds);
    const roots = nodeIds.filter(jointId => {
      const parent = parentJointId(component, jointId);
      return parent === null || !nodeSet.has(parent);
    });
    const rootId = numberId(component?.rootId);
    if (rootId !== null && nodeSet.has(rootId)) roots.unshift(rootId);
    [...new Set(roots)].sort((left, right) => left - right)
      .forEach(root => walk(component, root));
    nodeIds.forEach(jointId => {
      if (visited.has(jointId)) return;
      const parent = parentJointId(component, jointId);
      walk(component, jointId, parent === null
        ? null : ownerByJointId.get(parent) || null, parent);
    });
  });
  return ownerByJointId;
}

function ownershipDiagnostics(controlState, directOwnerByJointId,
    ownerByJointId, unboundJointIds) {
  const byControl = {};
  const inheritedCountByControl = new Map();
  ownerByJointId.forEach(owner => {
    if (owner.bindingMethod !== 'inherited_control') return;
    inheritedCountByControl.set(owner.controlKey,
      (inheritedCountByControl.get(owner.controlKey) || 0) + 1);
  });
  let automaticControlCount = 0;
  let unresolvedControlCount = 0;
  controlState.forEach((state, controlKey) => {
    const source = state.source || 'unresolved';
    if (source === 'automatic') automaticControlCount += 1;
    if (source === 'unresolved') unresolvedControlCount += 1;
    byControl[controlKey] = {
      controlKey,
      source,
      anchorJointId: state.anchorJointId ?? null,
      rootJointId: state.anchorJointId ?? null,
      directSeedCount: state.directSeedCount || 0,
      distanceRatio: state.distanceRatio ?? null,
      descendantCount: inheritedCountByControl.get(controlKey) || 0,
    };
  });
  return {
    mappedControlCount: [...controlState.values()]
      .filter(state => state.source === 'mapped').length,
    automaticControlCount,
    unresolvedControlCount,
    directSeedCount: directOwnerByJointId.size,
    inheritedJointCount: [...ownerByJointId.values()]
      .filter(owner => owner.bindingMethod === 'inherited_control').length,
    unownedJointCount: unboundJointIds.length,
    bindingsByControl: byControl,
  };
}

/**
 * Build the viewer-owned primary skeleton's driver frames.
 *
 * `posedControls` is optional.  Omitting it returns authored rest frames;
 * passing it is how the runtime evaluates a humanoid pose without changing
 * the fitted control rig or ModelJoint rest data.
 */
export function buildHumanoidDriverFrames(controlRig, posedControls = null) {
  const result = new Map();
  HUMANOID_DRIVER_SEGMENTS.forEach(segment => {
    const start = controlPoint(controlRig, segment.start, posedControls);
    const end = controlPoint(controlRig, segment.end, posedControls);
    const frame = frameForSegment(start, end, controlRig);
    if (frame) result.set(segment.id, {...segment, ...frame});
  });
  return result;
}

/** Build direct ModelJoint ownership and inherited deformation bindings. */
export function buildHumanoidRigBinding({controlRig, modelRig,
    controlMappings = null, options = {}} = {}) {
  const height = Math.max(Number(controlRig?.frame?.height) || 0, EPSILON);
  const drivers = buildHumanoidDriverFrames(controlRig);
  const pointDistanceRatio = Number.isFinite(Number(options.pointDistanceRatio))
    ? Math.max(0, Number(options.pointDistanceRatio))
    : DEFAULT_POINT_RADIUS_RATIO;
  const directOwnerByJointId = new Map();
  const controlState = new Map(CONTROL_ORDER.map(controlKey => [controlKey, {
    source: 'unresolved',
    anchorJointId: null,
    directSeedCount: 0,
    distanceRatio: null,
  }]));
  const mapped = mappedControlEntries(controlMappings);

  // Phase A: explicit mappings claim only their exact ModelJoint.
  mapped.forEach(({controlKey, jointId}) => {
    const driverId = HUMANOID_CONTROL_DRIVER_IDS[controlKey];
    if (!drivers.has(driverId) || !restJointWorldMatrix(modelRig, jointId)) {
      return;
    }
    directOwnerByJointId.set(jointId, directOwnerFor(
      controlKey, 'mapped', 'manual_control_mapping', null, jointId));
    const state = controlState.get(controlKey);
    state.source = 'mapped';
    state.anchorJointId = jointId;
    state.directSeedCount += 1;
  });

  const rejectedControlKeys = controlMappings?.rejectedControlKeys
    instanceof Set ? controlMappings.rejectedControlKeys : new Set();
  const automaticControls = CONTROL_ORDER.filter(controlKey =>
    controlState.get(controlKey).anchorJointId === null
      && !rejectedControlKeys.has(controlKey));

  // Calculate one candidate table after mapped roots are reserved. Both
  // automatic phases reuse it, while later ownership checks exclude anchors
  // and earlier extra seeds from consideration.
  const availableJointIds = allJointIds(modelRig)
    .filter(jointId => !directOwnerByJointId.has(jointId));
  const candidatesByControl = new Map(automaticControls.map(controlKey => [
    controlKey,
    candidatesForControl(modelRig, controlRig, controlKey,
      availableJointIds, height, pointDistanceRatio),
  ]));

  // Phase B: reserve one nearest point-to-point anchor per unmapped control.
  assignAutomaticAnchors({automaticControls, candidatesByControl,
    directOwnerByJointId, controlState});

  // Phase C: reserve all remaining in-radius joints before inheritance.
  assignAutomaticExtraSeeds({automaticControls, candidatesByControl,
    directOwnerByJointId, controlState});

  // Phase D: propagate direct ownership through the actual ModelRig hierarchy.
  // Direct owners are boundaries; no path or geometry is invented here.
  const ownerByJointId = inheritedOwners(modelRig, directOwnerByJointId);

  // Phase E: create the existing driver-relative transforms for every owner.
  const jointBindings = new Map();
  ownerByJointId.forEach((owner, jointId) => {
    const binding = directBindingFor(modelRig, jointId, drivers, owner);
    if (binding) jointBindings.set(jointId, binding);
  });

  const allIds = allJointIds(modelRig);
  const unboundJointIds = allIds.filter(id => !jointBindings.has(id));
  return {
    version: 1,
    jointBindings,
    unboundJointIds,
    diagnostics: ownershipDiagnostics(controlState,
      directOwnerByJointId, ownerByJointId, unboundJointIds),
  };
}

function matrixFrom(value) {
  if (value?.isMatrix4) return value.clone();
  if (Array.isArray(value) && value.length >= 16) {
    return new THREE.Matrix4().fromArray(value);
  }
  return new THREE.Matrix4();
}

/**
 * Evaluate absolute body-driver targets and convert them to skinning deltas.
 * Every result is an absolute target composed with the inverse authored rest
 * frame.  Children therefore receive a driver's motion exactly once even if
 * their ModelJoint parent is also directly bound.
 */
export function buildHumanoidDriverBaseTransforms({binding, controlRig,
    modelRig, posedControls = null} = {}) {
  const result = new Map();
  const driverWorldByJointId = new Map();
  if (!binding || !modelRig) return {driverWorldByJointId, result};
  const drivers = buildHumanoidDriverFrames(controlRig, posedControls);
  const apply = (jointId, driverId, localMatrix) => {
    const driver = drivers.get(driverId);
    const rest = restJointWorldMatrix(modelRig, jointId);
    if (!driver || !rest) return;
    const target = driver.matrix.clone().multiply(matrixFrom(localMatrix));
    driverWorldByJointId.set(Number(jointId), target);
    result.set(Number(jointId), target.multiply(rest.clone().invert()));
  };
  if (binding.jointBindings instanceof Map) {
    binding.jointBindings.forEach((entry, jointId) => {
      apply(jointId, entry.driverId, entry.localMatrix);
    });
  }
  return {driverWorldByJointId, result};
}

export {restJointWorldMatrix};
