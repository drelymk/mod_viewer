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
  return {distance: point.distanceTo(closest), projection: t, closest};
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
    projection: result.projection,
    score: result.distance / Math.max(height, EPSILON),
  };
}

function sortedCandidates(point, drivers, height) {
  return drivers.map(driver => candidateForPoint(point, driver, height))
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

function directBindingFor(modelRig, jointId, candidate, driverMap) {
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
    projection: candidate.projection,
    score: candidate.score,
    confidence: confidenceForDistance(candidate.distanceRatio),
  };
}

function serializeMatrix(matrix) {
  return matrix?.isMatrix4 ? matrix.toArray() : new THREE.Matrix4().toArray();
}

function serializeBindingEntry(entry) {
  return {
    type: entry.type,
    driverId: entry.driverId,
    localMatrix: serializeMatrix(entry.localMatrix),
    distance: Number(entry.distance) || 0,
    distanceRatio: Number(entry.distanceRatio) || 0,
    projection: Number(entry.projection) || 0,
    score: Number(entry.score) || 0,
    confidence: entry.confidence || 'low',
  };
}

function serializeAttachment(attachment) {
  return {
    rootJointId: attachment.rootJointId,
    driverId: attachment.driverId,
    localMatrix: serializeMatrix(attachment.localMatrix),
    jointIds: [...attachment.jointIds],
    confidence: attachment.confidence || 'low',
    distance: Number(attachment.distance) || 0,
    distanceRatio: Number(attachment.distanceRatio) || 0,
    ambiguityMargin: Number(attachment.ambiguityMargin) || 0,
  };
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

function secondaryCandidates(root, drivers, height) {
  return sortedCandidates(root, drivers, height);
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

export function serializeHumanoidDriverFrames(controlRig, posedControls = null) {
  return [...buildHumanoidDriverFrames(controlRig, posedControls).values()]
    .map(frame => ({
      id: frame.id, start: frame.start.toArray(), end: frame.end.toArray(),
      length: frame.length, matrix: frame.matrix.toArray(),
    }));
}

/** Build deterministic direct and secondary ModelJoint bindings. */
export function buildHumanoidRigBinding({controlRig, modelRig, options = {}} = {}) {
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

  allJointIds(modelRig).forEach(jointId => {
    const point = pointForJoint(modelRig, jointId);
    if (!point) return;
    const candidates = sortedCandidates(point, driverList, height);
    candidatesByJointId.set(jointId, candidates);
    const best = articulationPreferredCandidate(candidates);
    if (!best || best.distanceRatio > directLimit) return;
    const chosenCandidates = candidates[0] === best
      ? candidates : [best, ...candidates.filter(item => item !== best)];
    if (isAmbiguous(chosenCandidates, ambiguityMargin)
        && !isExactArticulationTie(candidates, best)) {
      ambiguousBindingCount += 1;
      return;
    }
    const binding = directBindingFor(modelRig, jointId, best, drivers);
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
    const candidates = secondaryCandidates(root, driverList, height);
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
    },
  };
  return binding;
}

/** Return a JSON-safe binding diagnostic snapshot. */
export function serializeHumanoidRigBinding(binding) {
  if (!binding) return null;
  return {
    version: Number(binding.version) || 1,
    jointBindings: Object.fromEntries([...(
      binding.jointBindings instanceof Map ? binding.jointBindings.entries() : [])]
      .map(([id, entry]) => [id, serializeBindingEntry(entry)])),
    secondaryAttachments: (binding.secondaryAttachments || [])
      .map(serializeAttachment),
    unboundJointIds: [...(binding.unboundJointIds || [])],
    diagnostics: {...(binding.diagnostics || {})},
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
    binding.jointBindings.forEach((entry, jointId) => apply(
      jointId, entry.driverId, entry.localMatrix));
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
