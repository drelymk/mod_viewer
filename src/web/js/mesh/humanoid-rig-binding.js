import * as THREE from 'three';

// The control rig owns this topology.  ModelJoint edges are deliberately not
// consulted when these segments are built or when a limb is posed.
export const HUMANOID_DRIVER_SEGMENTS = Object.freeze([
  {id: 'torso', start: 'pelvis', end: 'chest'},
  {id: 'left_chest_shoulder', start: 'chest', end: 'leftShoulder'},
  {id: 'right_chest_shoulder', start: 'chest', end: 'rightShoulder'},
  {id: 'left_upper_arm', start: 'leftShoulder', end: 'leftElbow'},
  {id: 'left_lower_arm', start: 'leftElbow', end: 'leftHand'},
  {id: 'right_upper_arm', start: 'rightShoulder', end: 'rightElbow'},
  {id: 'right_lower_arm', start: 'rightElbow', end: 'rightHand'},
  {id: 'left_pelvis_hip', start: 'pelvis', end: 'leftHip'},
  {id: 'right_pelvis_hip', start: 'pelvis', end: 'rightHip'},
  {id: 'left_upper_leg', start: 'leftHip', end: 'leftKnee'},
  {id: 'left_lower_leg', start: 'leftKnee', end: 'leftFoot'},
  {id: 'right_upper_leg', start: 'rightHip', end: 'rightKnee'},
  {id: 'right_lower_leg', start: 'rightKnee', end: 'rightFoot'},
]);

const EPSILON = 1e-8;
const DEFAULT_DIRECT_DISTANCE_RATIO = 0.105;
const DEFAULT_SECONDARY_DISTANCE_RATIO = 0.24;
const DEFAULT_AMBIGUITY_MARGIN_RATIO = 0.025;
const TERMINAL_EXTENSION_RATIO = 0.15;

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
  const joint = id === null ? null : (modelRig?.joints?.[id]
    || modelRig?.joints?.find?.(item => Number(item?.jointId) === id));
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

function parentFor(modelRig, jointId) {
  const componentId = modelRig?.componentByJointId?.get?.(Number(jointId));
  const component = Number.isInteger(Number(componentId))
    ? modelRig?.components?.[Number(componentId)] : null;
  const parent = component?.parentById?.[Number(jointId)];
  return parent === null || parent === undefined ? null : numberId(parent);
}

function pointForJoint(modelRig, jointId) {
  const id = numberId(jointId);
  if (id === null) return null;
  const joint = modelRig?.joints?.[id]
    || modelRig?.joints?.find?.(item => Number(item?.jointId) === id);
  return vectorFromCollection(modelRig?.jointPivotByJointId, id)
    || vector(joint?.restPivot ?? joint?.restCenter, [0, 0, 0]);
}

function segmentDistance(point, start, end) {
  const direction = end.clone().sub(start);
  const lengthSq = direction.lengthSq();
  const projection = lengthSq > EPSILON
    ? point.clone().sub(start).dot(direction) / lengthSq : 0;
  const t = Math.max(0, Math.min(1, projection));
  const closest = start.clone().addScaledVector(direction, t);
  return {
    distance: point.distanceTo(closest),
    rawProjection: projection,
    projection: t,
    closest,
  };
}

function segmentOrder(id) {
  const index = HUMANOID_DRIVER_SEGMENTS.findIndex(segment => segment.id === id);
  return index < 0 ? HUMANOID_DRIVER_SEGMENTS.length : index;
}

function confidenceForDistance(distanceRatio) {
  if (distanceRatio <= .035) return 'high';
  if (distanceRatio <= .075) return 'medium';
  return 'low';
}

function candidateForPoint(point, driver, height) {
  const result = segmentDistance(point, driver.start, driver.end);
  return {
    driverId: driver.id,
    distance: result.distance,
    distanceRatio: result.distance / Math.max(height, EPSILON),
    rawProjection: result.rawProjection,
    projection: result.projection,
    endpointDistanceRatio: point.distanceTo(driver.end) / Math.max(height, EPSILON),
    score: result.distance / Math.max(height, EPSILON),
  };
}

function frameVector(controlRig, key, fallback) {
  return vector(controlRig?.frame?.[key], fallback).normalize();
}

function centralDomain(controlRig, height) {
  const right = frameVector(controlRig, 'right', [1, 0, 0]);
  const pelvis = controlPoint(controlRig, 'pelvis');
  const shoulder = controlPoint(controlRig, 'leftShoulder')
    .clone().sub(controlPoint(controlRig, 'rightShoulder')).length() * .5;
  return {
    right, pelvis,
    halfWidth: Math.max(.06 * height, shoulder * .75),
  };
}

function semanticCandidateAllowed(point, driver, candidate, controlRig, height) {
  const id = driver.id;
  if (id === 'torso') {
    const domain = centralDomain(controlRig, height);
    const lateral = Math.abs(point.clone().sub(domain.pelvis).dot(domain.right));
    return lateral <= domain.halfWidth;
  }

  // A limb binding must be on the limb's authored corridor. Clamping a point
  // behind a shoulder/hip to the segment endpoint is not a semantic match.
  if (candidate.rawProjection < -1e-5) return false;
  const terminal = /^(left|right)_lower_(arm|leg)$/.test(id);
  if (candidate.rawProjection > 1 + 1e-5
      && (!terminal || candidate.endpointDistanceRatio > TERMINAL_EXTENSION_RATIO)) {
    return false;
  }

  const side = id.startsWith('left_') ? -1 : id.startsWith('right_') ? 1 : 0;
  if (!side) return false;
  const domain = centralDomain(controlRig, height);
  const lateral = point.clone().sub(domain.pelvis).dot(domain.right);
  // Keep central torso/head geometry on the torso driver. This also prevents
  // a secondary root from making an entire central subtree follow an arm.
  if (Math.abs(lateral) <= domain.halfWidth) return false;
  if (Math.sign(lateral) !== side) return false;
  return true;
}

function sortedCandidates(point, drivers, height, controlRig) {
  const list = Array.isArray(drivers) ? drivers : [...(drivers?.values?.() || [])];
  return list.map(driver => candidateForPoint(point, driver, height))
    .filter(candidate => semanticCandidateAllowed(
      point, list.find(driver => driver.id === candidate.driverId),
      candidate, controlRig, height))
    .sort((left, right) => left.score - right.score
      || segmentOrder(left.driverId) - segmentOrder(right.driverId));
}

function isAmbiguous(candidates, marginRatio) {
  return candidates.length > 1
    && candidates[1].score - candidates[0].score < marginRatio;
}

function articulationPreferredCandidate(candidates) {
  if (!candidates.length) return null;
  const bestScore = candidates[0].score;
  const tied = candidates.filter(candidate =>
    Math.abs(candidate.score - bestScore) <= 1e-7);
  if (tied.length <= 1) return candidates[0];
  // At a shared articulation, the segment beginning at that control owns the
  // joint.  This makes Knee→Foot and Elbow→Hand win at Knee/Elbow without
  // making near-equal candidates elsewhere silently arbitrary.
  const startsAtArticulation = tied.filter(candidate => candidate.projection < .05);
  return startsAtArticulation.length === 1 ? startsAtArticulation[0]
    : candidates[0];
}

function isExactArticulationTie(candidates, best) {
  const tied = candidates.filter(candidate =>
    Math.abs(candidate.score - best.score) <= 1e-7);
  return tied.length === 2 && best.projection < .05
    && tied.some(candidate => candidate !== best
      && candidate.projection > .95);
}

function directBindingFor(modelRig, jointId, candidate, driverMap, metadata = {}) {
  const restJointWorld = restJointWorldMatrix(modelRig, jointId);
  const driver = driverMap.get(candidate.driverId);
  if (!restJointWorld || !driver) return null;
  const localMatrix = driver.matrix.clone().invert().multiply(restJointWorld);
  return {
    type: 'driver',
    driverId: candidate.driverId,
    localMatrix,
    restJointWorld,
    distance: candidate.distance,
    distanceRatio: candidate.distanceRatio,
    rawProjection: candidate.rawProjection,
    projection: candidate.projection,
    endpointDistanceRatio: candidate.endpointDistanceRatio,
    score: candidate.score,
    confidence: confidenceForDistance(candidate.distanceRatio),
    ...metadata,
  };
}

function valueFor(collection, key) {
  if (collection instanceof Map) return collection.get(key) ?? collection.get(String(key));
  return collection?.[key];
}

function allJointIds(modelRig) {
  return (modelRig?.joints || []).map(joint => numberId(joint?.jointId))
    .filter(Number.isInteger).sort((left, right) => left - right);
}

function componentChildren(modelRig, jointId) {
  const componentId = modelRig?.componentByJointId?.get?.(Number(jointId));
  const component = Number.isInteger(Number(componentId))
    ? modelRig?.components?.[Number(componentId)] : null;
  return (component?.childrenById?.[Number(jointId)] || [])
    .map(numberId).filter(Number.isInteger).sort((left, right) => left - right);
}

function unboundSubtree(modelRig, rootId, directIds) {
  const result = [];
  const queue = [rootId];
  while (queue.length) {
    const id = queue.shift();
    if (directIds.has(id)) continue;
    result.push(id);
    componentChildren(modelRig, id).forEach(child => queue.push(child));
  }
  return result;
}

function secondaryCandidates(root, drivers, height, controlRig) {
  return sortedCandidates(root, drivers, height, controlRig);
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

/**
 * Build humanoid deltas for the exact source bones accepted by heat binding.
 * The result stays source-local so an unclassified member of the same
 * ModelJoint does not inherit the classified member's humanoid motion.
 */
export function buildHumanoidSourceBoneDriverTransforms({heatBinding,
    controlRig, posedControls = null} = {}) {
  const result = new Map();
  const restDrivers = buildHumanoidDriverFrames(controlRig);
  const posedDrivers = buildHumanoidDriverFrames(controlRig, posedControls);
  const entries = heatBinding?.sourceBoneAssignments instanceof Map
    ? [...heatBinding.sourceBoneAssignments.values()]
    : Object.values(heatBinding?.sourceBoneAssignments || {});
  entries.forEach(assignment => {
    const sourceKey = assignment?.sourceKey;
    const boneId = numberId(assignment?.boneId);
    const driverId = assignment?.driverId;
    const rest = restDrivers.get(driverId);
    const posed = posedDrivers.get(driverId);
    if (sourceKey === undefined || boneId === null || !rest || !posed) return;
    const matrix = posed.matrix.clone().multiply(rest.matrix.clone().invert());
    const source = result.get(String(sourceKey)) || new Map();
    source.set(boneId, {driverId, matrix});
    result.set(String(sourceKey), source);
  });
  return result;
}

/** Build deterministic direct and secondary ModelJoint bindings. */
export function buildHumanoidRigBinding({controlRig, modelRig, heatBinding,
    options = {}} = {}) {
  const started = typeof performance !== 'undefined' && performance.now
    ? performance.now() : Date.now();
  const height = Math.max(Number(controlRig?.frame?.height) || 0, EPSILON);
  const drivers = buildHumanoidDriverFrames(controlRig);
  const driverList = [...drivers.values()];
  const directLimit = Number.isFinite(Number(options.directDistanceRatio))
    ? Number(options.directDistanceRatio) : DEFAULT_DIRECT_DISTANCE_RATIO;
  const secondaryLimit = Number.isFinite(Number(options.secondaryDistanceRatio))
    ? Number(options.secondaryDistanceRatio) : DEFAULT_SECONDARY_DISTANCE_RATIO;
  const ambiguityMargin = Number.isFinite(Number(options.ambiguityMarginRatio))
    ? Number(options.ambiguityMarginRatio) : DEFAULT_AMBIGUITY_MARGIN_RATIO;
  const jointBindings = new Map();
  const candidatesByJointId = new Map();
  let ambiguousBindingCount = 0;

  // Heat ownership is authoritative for all eight limb drivers. It is
  // deliberately applied before the central-body proximity fallback.
  allJointIds(modelRig).forEach(jointId => {
    const assignment = valueFor(heatBinding?.modelJointAssignments, jointId);
    if (!assignment?.driverId || !drivers.has(assignment.driverId)) return;
    const point = pointForJoint(modelRig, jointId);
    const driver = drivers.get(assignment.driverId);
    const restJointWorld = restJointWorldMatrix(modelRig, jointId);
    if (!point || !driver || !restJointWorld) return;
    const localMatrix = driver.matrix.clone().invert().multiply(restJointWorld);
    jointBindings.set(jointId, {
      type: 'driver',
      driverId: assignment.driverId,
      localMatrix,
      restJointWorld,
      distance: null,
      distanceRatio: null,
      rawProjection: null,
      projection: null,
      endpointDistanceRatio: null,
      score: null,
      confidence: assignment.confidence || 'medium',
      bindingMethod: 'heat_connectivity',
      limbRole: assignment.limbRole || null,
      progress: Number(assignment.progress),
      sourceBoneKeys: [...(assignment.sourceBoneKeys || [])],
      memberCount: Number(assignment.memberCount) || 0,
    });
  });

  allJointIds(modelRig).forEach(jointId => {
    if (jointBindings.has(jointId)) return;
    const point = pointForJoint(modelRig, jointId);
    if (!point) return;
    const candidates = sortedCandidates(point, drivers, height, controlRig);
    candidatesByJointId.set(jointId, candidates);
    const centralCandidates = candidates.filter(candidate =>
      candidate.driverId === 'torso');
    const best = articulationPreferredCandidate(centralCandidates);
    if (!best || best.distanceRatio > directLimit) return;
    const chosenCandidates = centralCandidates[0] === best
      ? centralCandidates : [best, ...centralCandidates.filter(item => item !== best)];
    if (isAmbiguous(chosenCandidates, ambiguityMargin)
        && !isExactArticulationTie(candidates, best)) {
      ambiguousBindingCount += 1;
      return;
    }
    const binding = directBindingFor(modelRig, jointId, best, drivers,
      {bindingMethod: 'central_proximity'});
    if (binding) jointBindings.set(jointId, binding);
  });

  const secondaryAttachments = [];
  const secondaryJointIds = new Set();
  const directIds = new Set(jointBindings.keys());
  const allIds = allJointIds(modelRig);
  const rootCandidates = new Set(allIds.filter(id => {
    if (directIds.has(id)) return false;
    const parent = parentFor(modelRig, id);
    return parent === null || directIds.has(parent);
  }));
  [...rootCandidates].sort((left, right) => left - right).forEach(rootId => {
    if (directIds.has(rootId) || secondaryJointIds.has(rootId)) return;
    const root = pointForJoint(modelRig, rootId);
    if (!root) return;
    const candidates = secondaryCandidates(root, drivers, height, controlRig)
      .filter(candidate => candidate.driverId === 'torso');
    const best = articulationPreferredCandidate(candidates);
    if (!best || best.distanceRatio > secondaryLimit
        || (isAmbiguous(candidates, ambiguityMargin)
          && !isExactArticulationTie(candidates, best))) return;
    const restRootWorld = restJointWorldMatrix(modelRig, rootId);
    const driver = drivers.get(best.driverId);
    if (!restRootWorld || !driver) return;
    const jointIds = unboundSubtree(modelRig, rootId, directIds);
    if (!jointIds.length) return;
    const localMatrix = driver.matrix.clone().invert().multiply(restRootWorld);
    secondaryAttachments.push({
      rootJointId: rootId,
      driverId: best.driverId,
      localMatrix,
      jointIds,
      confidence: confidenceForDistance(best.distanceRatio),
      distance: best.distance,
      distanceRatio: best.distanceRatio,
      rawProjection: best.rawProjection,
      projection: best.projection,
      endpointDistanceRatio: best.endpointDistanceRatio,
      ambiguityMargin: candidates[1] ? candidates[1].score - best.score : 1,
    });
    jointIds.forEach(id => secondaryJointIds.add(id));
  });

  const unboundJointIds = allIds.filter(id =>
    !directIds.has(id) && !secondaryJointIds.has(id));
  const byDriver = {};
  jointBindings.forEach(entry => {
    byDriver[entry.driverId] = (byDriver[entry.driverId] || 0) + 1;
  });
  const runtime = (typeof performance !== 'undefined' && performance.now
    ? performance.now() : Date.now()) - started;
  const binding = {
    version: 1,
    jointBindings,
    secondaryAttachments,
    unboundJointIds,
    diagnostics: {
      totalModelJoints: allJointIds(modelRig).length,
      directBodyBindings: jointBindings.size,
      secondaryAttachmentRoots: secondaryAttachments.length,
      secondaryDescendantJoints: secondaryAttachments.reduce(
        (sum, item) => sum + Math.max(0, item.jointIds.length - 1), 0),
      unboundJoints: unboundJointIds.length,
      bindingsByDriver: byDriver,
      ambiguousBindingCount,
      fitRuntimeMs: Number(controlRig?.diagnostics?.fitRuntimeMs) || 0,
      bindingRuntimeMs: Math.max(0, runtime),
      directDistanceRatio: directLimit,
      secondaryDistanceRatio: secondaryLimit,
      ambiguityMarginRatio: ambiguityMargin,
      candidateCount: candidatesByJointId.size,
      heatBinding: heatBinding?.diagnostics || null,
      heatConflicts: [...(heatBinding?.conflicts || [])],
    },
  };
  return binding;
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
      // Limb heat ownership is applied per source bone by
      // buildHumanoidSourceBoneDriverTransforms. Keeping it out of this
      // ModelJoint layer prevents reconciled accessory members from inheriting
      // the limb driver.
      if (entry.bindingMethod === 'heat_connectivity') return;
      apply(jointId, entry.driverId, entry.localMatrix);
    });
  }
  (binding.secondaryAttachments || []).forEach(attachment => {
    const rootId = numberId(attachment.rootJointId);
    if (rootId === null) return;
    apply(rootId, attachment.driverId, attachment.localMatrix);
    const rootDelta = result.get(rootId);
    if (!rootDelta) return;
    (attachment.jointIds || []).forEach(jointIdValue => {
      const jointId = numberId(jointIdValue);
      if (jointId === null || jointId === rootId) return;
      // A secondary subtree keeps its authored internal hierarchy; its body
      // attachment is the one rigid delta shared by the subtree root.
      result.set(jointId, rootDelta.clone());
    });
  });
  return {driverWorldByJointId, result};
}

export {restJointWorldMatrix};
