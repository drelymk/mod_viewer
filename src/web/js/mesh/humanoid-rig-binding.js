import * as THREE from 'three';
import {HUMANOID_CONTROL_DRIVER_IDS} from './humanoid-control-rig.js';

// The control rig owns the semantic topology. Explicitly mapped ModelJoints
// own their source-connected descendants until another mapped control or a
// mapped path takes ownership; automatic heat/geometric inference remains the
// fallback for everything else.
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
const DEFAULT_DIRECT_DISTANCE_RATIO = 0.105;
const DEFAULT_SECONDARY_DISTANCE_RATIO = 0.24;
const DEFAULT_AMBIGUITY_MARGIN_RATIO = 0.025;
const TERMINAL_EXTENSION_RATIO = 0.15;
const CENTRAL_DRIVER_IDS = new Set(['torso', 'neck', 'head']);

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
  const indexedJoint = modelRig?.joints?.[id];
  const joint = indexedJoint && Number(indexedJoint.jointId) === id
    ? indexedJoint : modelRig?.joints?.find?.(item =>
      Number(item?.jointId) === id);
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
  if (CENTRAL_DRIVER_IDS.has(id)) {
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

/**
 * Classify a rest-space point against the fitted humanoid driver corridors.
 *
 * The binding path intentionally keeps this classifier private because it
 * also applies binding-specific distance gates.  The optional guided ModelRig
 * builder uses the same spatial semantics, but does not need a ModelJoint or
 * a heat-binding result yet.  Returning the candidate list makes ambiguity a
 * visible, conservative outcome instead of silently forcing a classification.
 */
export function classifyHumanoidPoint(pointValue, controlRig, options = {}) {
  const point = vector(pointValue);
  const height = Math.max(Number(controlRig?.frame?.height) || 0, EPSILON);
  const drivers = buildHumanoidDriverFrames(controlRig);
  const candidates = sortedCandidates(point, drivers, height, controlRig);
  const best = articulationPreferredCandidate(candidates);
  const maximumDistanceRatio = Number.isFinite(
    Number(options.maximumDistanceRatio))
    ? Number(options.maximumDistanceRatio) : DEFAULT_SECONDARY_DISTANCE_RATIO;
  const ambiguityMargin = Number.isFinite(Number(options.ambiguityMarginRatio))
    ? Number(options.ambiguityMarginRatio) : DEFAULT_AMBIGUITY_MARGIN_RATIO;
  if (!best) {
    return {
      classified: false, confident: false, confidence: 'low',
      reason: 'no_semantic_candidate', candidates: [],
    };
  }
  const ambiguous = isAmbiguous(candidates, ambiguityMargin)
    && !isExactArticulationTie(candidates, best);
  const withinDistance = best.distanceRatio <= maximumDistanceRatio;
  const confidence = confidenceForDistance(best.distanceRatio);
  const classified = withinDistance && !ambiguous;
  return {
    ...best,
    role: HUMANOID_DRIVER_SEGMENTS.find(segment =>
      segment.id === best.driverId)?.role || null,
    confidence,
    classified,
    confident: classified && confidence !== 'low',
    reason: !withinDistance ? 'outside_semantic_corridor'
      : ambiguous ? 'ambiguous_semantic_candidate' : null,
    candidates: candidates.slice(0, 4).map(candidate => ({...candidate})),
  };
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

function mappedControlEntries(controlMappings) {
  if (!(controlMappings instanceof Map)) {
    return {entries: [], invalidJointIds: new Set(), conflicts: []};
  }
  const entries = [...controlMappings.entries()].map(([key, mapping]) => ({
    controlKey: mapping?.controlKey || key,
    mapping,
    jointId: numberId(mapping?.jointId),
  }));
  const byJointId = new Map();
  entries.forEach(entry => {
    if (entry.jointId === null) return;
    const owners = byJointId.get(entry.jointId) || [];
    owners.push(entry);
    byJointId.set(entry.jointId, owners);
  });
  const invalidJointIds = new Set();
  const conflicts = [];
  byJointId.forEach((owners, jointId) => {
    if (owners.length <= 1) return;
    invalidJointIds.add(jointId);
    conflicts.push({
      type: 'conflicting_control_mappings',
      jointId,
      controlKeys: owners.map(entry => entry.controlKey).sort(),
    });
  });
  return {
    entries: entries.filter(entry => entry.jointId !== null
      && !invalidJointIds.has(entry.jointId)),
    invalidJointIds,
    conflicts,
  };
}

function mappedPathCandidates(modelRig, controlMappings) {
  const mapped = mappedControlEntries(controlMappings);
  const byControl = new Map(mapped.entries.map(entry =>
    [entry.controlKey, entry]));
  const mappedJointIds = new Set(mapped.entries.map(entry => entry.jointId));
  const candidates = [];
  const conflicts = [...mapped.conflicts];
  HUMANOID_DRIVER_SEGMENTS.forEach(segment => {
    const start = byControl.get(segment.start);
    const end = byControl.get(segment.end);
    if (!start || !end) return;
    const path = shortestModelJointPath(modelRig, start.jointId, end.jointId);
    if (!path || path.length < 2) return;
    const interior = path.slice(1, -1);
    if (interior.some(jointId => mappedJointIds.has(jointId))) {
      conflicts.push({
        type: 'mapped_control_inside_path',
        driverId: segment.id,
        segmentStartControl: segment.start,
        segmentEndControl: segment.end,
        path,
      });
      return;
    }
    candidates.push({segment, path, start, end});
  });

  const ownersByJointId = new Map();
  const rejected = new Set();
  candidates.forEach(candidate => {
    candidate.path.slice(1, -1).forEach(jointId => {
      const owner = ownersByJointId.get(jointId);
      if (!owner) {
        ownersByJointId.set(jointId, candidate);
        return;
      }
      rejected.add(owner);
      rejected.add(candidate);
      conflicts.push({
        type: 'conflicting_mapped_paths',
        jointId,
        driverIds: [owner.segment.id, candidate.segment.id].sort(),
      });
    });
  });
  return {
    candidates: candidates.filter(candidate => !rejected.has(candidate)),
    conflicts,
  };
}

function jointForId(modelRig, jointId) {
  const id = numberId(jointId);
  if (id === null) return null;
  const indexedJoint = modelRig?.joints?.[id];
  return indexedJoint && Number(indexedJoint.jointId) === id
    ? indexedJoint : modelRig?.joints?.find?.(joint =>
      Number(joint?.jointId) === id);
}

function sourceMembersForJoint(modelRig, jointId, mapping = null) {
  const members = [
    ...(jointForId(modelRig, jointId)?.members || []),
    ...(mapping?.sourceMembers || []),
  ];
  const unique = new Map();
  members.forEach(member => {
    const sourceKey = member?.sourceKey;
    const boneId = numberId(member?.boneId);
    if (sourceKey === undefined || boneId === null) return;
    const key = member.sourceBoneKey || `${String(sourceKey)}#bone=${boneId}`;
    if (!unique.has(key)) unique.set(key, member);
  });
  return [...unique.entries()].map(([sourceBoneKey, member]) => ({
    ...member,
    sourceKey: String(member.sourceKey),
    boneId: numberId(member.boneId),
    sourceBoneKey,
  }));
}

const HUMANOID_CONTROL_SOURCE_ROLES = Object.freeze({
  chest: 'torso', pelvis: 'torso', neck: 'torso', head: 'torso',
  leftShoulder: 'left_arm', leftElbow: 'left_arm', leftHand: 'left_arm',
  rightShoulder: 'right_arm', rightElbow: 'right_arm', rightHand: 'right_arm',
  leftHip: 'left_leg', leftKnee: 'left_leg', leftFoot: 'left_leg',
  rightHip: 'right_leg', rightKnee: 'right_leg', rightFoot: 'right_leg',
});

function sourceRoleForControl(controlKey) {
  return HUMANOID_CONTROL_SOURCE_ROLES[controlKey] || null;
}

function sourceProgress(controlKey) {
  if (controlKey === 'chest' || controlKey === 'pelvis') return 0;
  return /Elbow|Knee|Hand|Foot/.test(controlKey || '') ? 1 : 0;
}

function sourceAssignmentForMember(member, driverId, metadata = {}) {
  const totalWeight = Number(member?.totalWeight)
    || Number(member?.affectedMeasure) || Number(member?.affectedVertexCount) || 0;
  return {
    sourceKey: member.sourceKey,
    boneId: member.boneId,
    sourceBoneKey: member.sourceBoneKey,
    driverId,
    totalWeight,
    confidence: 'high',
    pathMember: true,
    branchMember: false,
    ...metadata,
  };
}

function componentChildren(modelRig, jointId) {
  const componentId = modelRig?.componentByJointId?.get?.(Number(jointId));
  const component = Number.isInteger(Number(componentId))
    ? modelRig?.components?.[Number(componentId)] : null;
  return (component?.childrenById?.[Number(jointId)] || [])
    .map(numberId).filter(Number.isInteger).sort((left, right) => left - right);
}

function sourceChildIds(modelRig, jointId) {
  const children = componentChildren(modelRig, jointId);
  const componentId = modelRig?.componentByJointId?.get?.(Number(jointId));
  const component = Number.isInteger(Number(componentId))
    ? modelRig?.components?.[Number(componentId)] : null;
  const edges = component?.edges;
  if (!Array.isArray(edges) || !edges.length) return children;
  const sourcePairs = new Set(edges.filter(edge =>
    edge?.relationshipType !== 'attachment').map(edge => {
    const left = numberId(edge?.jointA ?? edge?.boneA);
    const right = numberId(edge?.jointB ?? edge?.boneB);
    return left === null || right === null ? null
      : `${Math.min(left, right)}:${Math.max(left, right)}`;
  }).filter(Boolean));
  return children.filter(child => sourcePairs.has(
    `${Math.min(Number(jointId), child)}:${Math.max(Number(jointId), child)}`));
}

function mappedDescendantJointIds(modelRig, rootId, mappedJointIds,
    jointBindings, driverId) {
  const descendants = [];
  const visited = new Set([Number(rootId)]);
  const queue = sourceChildIds(modelRig, rootId);
  while (queue.length) {
    const jointId = queue.shift();
    if (visited.has(jointId)) continue;
    visited.add(jointId);
    // An explicitly mapped control is a boundary. Its own mapping will walk
    // its descendants with the correct, more specific driver.
    if (mappedJointIds.has(jointId)) continue;
    const existing = jointBindings.get(jointId);
    if (existing?.driverId && existing.driverId !== driverId) continue;
    if (!existing) descendants.push(jointId);
    sourceChildIds(modelRig, jointId).forEach(child => {
      if (!visited.has(child)) queue.push(child);
    });
  }
  return descendants;
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
 * Build humanoid deltas for the exact source bones accepted by automatic heat
 * or authoritative mapped binding. The result stays source-local so an
 * unclassified member of the same ModelJoint does not inherit the classified
 * member's humanoid motion.
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
    controlMappings = null, options = {}} = {}) {
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
  const mapped = mappedControlEntries(controlMappings);
  const mappedPaths = mappedPathCandidates(modelRig, controlMappings);
  const mappedJointIds = new Set(mapped.entries.map(entry => entry.jointId));
  const sourceBoneAssignments = new Map();
  let mappedDescendantJointCount = 0;
  if (heatBinding?.sourceBoneAssignments instanceof Map) {
    heatBinding.sourceBoneAssignments.forEach((assignment, sourceKey) =>
      sourceBoneAssignments.set(sourceKey, assignment));
  } else {
    Object.entries(heatBinding?.sourceBoneAssignments || {}).forEach(
      ([sourceKey, assignment]) => sourceBoneAssignments.set(sourceKey, assignment));
  }

  // A manually snapped control owns its resolved ModelJoint before either
  // heat connectivity or geometric corridor scoring is considered.
  mapped.entries.forEach(({controlKey, mapping, jointId}) => {
      const driverId = HUMANOID_CONTROL_DRIVER_IDS[controlKey];
      const driver = drivers.get(driverId);
      const restJointWorld = jointId === null
        ? null : restJointWorldMatrix(modelRig, jointId);
      if (jointId === null || !driver || !restJointWorld) return;
      const localMatrix = driver.matrix.clone().invert().multiply(restJointWorld);
      const sourceMembers = sourceMembersForJoint(modelRig, jointId, mapping);
      jointBindings.set(jointId, {
        type: 'driver', driverId, localMatrix, restJointWorld,
        distance: 0, distanceRatio: 0, rawProjection: 0, projection: 0,
        endpointDistanceRatio: 0, score: 0, confidence: 'high',
        bindingMethod: 'manual_control_mapping',
        controlKey,
        sourceBoneKeys: sourceMembers.map(member => member.sourceBoneKey).sort(),
      });
      sourceMembers.forEach(member => sourceBoneAssignments.set(
        member.sourceBoneKey, sourceAssignmentForMember(member, driverId, {
          limbRole: sourceRoleForControl(controlKey),
          progress: sourceProgress(controlKey),
          segmentIndex: /Elbow|Knee|Hand|Foot/.test(controlKey) ? 1 : 0,
          bindingMethod: 'manual_control_mapping',
          controlKey,
        })));
  });

  // When both canonical endpoints are mapped, the shortest path in their
  // component's undirected ModelJoint graph owns the segment. Only the
  // interior joints are claimed here; endpoints remain explicit mappings.
  mappedPaths.candidates.forEach(({segment, path}) => {
    const driver = drivers.get(segment.id);
    if (!driver) return;
    path.slice(1, -1).forEach(jointId => {
      if (jointBindings.has(jointId)) return;
      const binding = directBindingFor(modelRig, jointId, {
        driverId: segment.id,
        distance: 0, distanceRatio: 0, rawProjection: 0, projection: 0,
        endpointDistanceRatio: 0, score: 0,
      }, drivers, {
        bindingMethod: 'mapped_joint_path',
        segmentStartControl: segment.start,
        segmentEndControl: segment.end,
      });
      if (!binding) return;
      jointBindings.set(jointId, binding);
      sourceMembersForJoint(modelRig, jointId).forEach(member =>
        sourceBoneAssignments.set(member.sourceBoneKey,
          sourceAssignmentForMember(member, segment.id, {
            limbRole: segment.role,
            segmentStartControl: segment.start,
            segmentEndControl: segment.end,
            bindingMethod: 'mapped_joint_path',
          })));
    });
  });

  // A mapped control is an authoritative anchor for its source-connected
  // descendants. Walk through same-driver mapped path joints, but stop at a
  // different mapped control so each explicit control remains a boundary.
  // Attachment edges are excluded by sourceChildIds; accessories retain the
  // existing conservative binding behavior.
  mapped.entries.forEach(({controlKey, jointId}) => {
    const driverId = HUMANOID_CONTROL_DRIVER_IDS[controlKey];
    const driver = drivers.get(driverId);
    if (!driver || jointId === null) return;
    const descendants = mappedDescendantJointIds(modelRig, jointId,
      mappedJointIds, jointBindings, driverId);
    descendants.forEach(descendantId => {
      if (jointBindings.has(descendantId)) return;
      const restJointWorld = restJointWorldMatrix(modelRig, descendantId);
      if (!restJointWorld) return;
      const sourceMembers = sourceMembersForJoint(modelRig, descendantId);
      const localMatrix = driver.matrix.clone().invert().multiply(restJointWorld);
      jointBindings.set(descendantId, {
        type: 'driver', driverId, localMatrix, restJointWorld,
        distance: 0, distanceRatio: 0, rawProjection: 0, projection: 0,
        endpointDistanceRatio: 0, score: 0, confidence: 'high',
        bindingMethod: 'mapped_control_descendant',
        controlKey,
        mappedRootJointId: jointId,
        sourceBoneKeys: sourceMembers.map(member => member.sourceBoneKey).sort(),
      });
      sourceMembers.forEach(member => sourceBoneAssignments.set(
        member.sourceBoneKey, sourceAssignmentForMember(member, driverId, {
          limbRole: sourceRoleForControl(controlKey),
          progress: sourceProgress(controlKey),
          segmentIndex: /Elbow|Knee|Hand|Foot/.test(controlKey) ? 1 : 0,
          bindingMethod: 'mapped_control_descendant',
          controlKey,
          mappedRootJointId: jointId,
          pathMember: false,
          branchMember: true,
        })));
      mappedDescendantJointCount += 1;
    });
  });

  // Heat ownership is authoritative for all eight limb drivers. It is
  // automatic inference only; explicit mappings and mapped graph paths have
  // already claimed their endpoints/interiors above.
  allJointIds(modelRig).forEach(jointId => {
    if (jointBindings.has(jointId)) return;
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
      CENTRAL_DRIVER_IDS.has(candidate.driverId));
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
      .filter(candidate => CENTRAL_DRIVER_IDS.has(candidate.driverId));
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
      mappedPathCount: mappedPaths.candidates.length,
      mappedPathJointCount: mappedPaths.candidates.reduce((sum, candidate) =>
        sum + Math.max(0, candidate.path.length - 2), 0),
      mappedDescendantJointCount,
      mappedPathConflicts: mappedPaths.conflicts,
      heatBinding: heatBinding?.diagnostics || null,
      heatConflicts: [...(heatBinding?.conflicts || [])],
    },
  };
  binding.sourceBoneAssignments = sourceBoneAssignments;
  return binding;
}

function componentForJoint(modelRig, jointId) {
  const id = numberId(jointId);
  if (id === null) return null;
  const rawComponentId = modelRig?.componentByJointId instanceof Map
    ? modelRig.componentByJointId.get(id)
      ?? modelRig.componentByJointId.get(String(id))
    : modelRig?.componentByJointId?.[id];
  const componentId = numberId(rawComponentId);
  if (componentId !== null && modelRig?.components?.[componentId]) {
    return modelRig.components[componentId];
  }
  return (modelRig?.components || []).find(component =>
    (component?.nodeIds || []).some(nodeId => numberId(nodeId) === id)) || null;
}

function addGraphEdge(adjacency, leftValue, rightValue) {
  const left = numberId(leftValue);
  const right = numberId(rightValue);
  if (left === null || right === null || left === right) return;
  if (!adjacency.has(left)) adjacency.set(left, new Set());
  if (!adjacency.has(right)) adjacency.set(right, new Set());
  adjacency.get(left).add(right);
  adjacency.get(right).add(left);
}

function modelJointAdjacency(modelRig, component) {
  const adjacency = new Map();
  (component?.nodeIds || []).forEach(id => {
    const jointId = numberId(id);
    if (jointId !== null && !adjacency.has(jointId)) {
      adjacency.set(jointId, new Set());
    }
  });
  Object.entries(component?.parentById || {}).forEach(([child, parent]) => {
    const childId = numberId(child);
    if (childId !== null && !adjacency.has(childId)) {
      adjacency.set(childId, new Set());
    }
    addGraphEdge(adjacency, child, parent);
  });
  Object.entries(component?.childrenById || {}).forEach(([parent, children]) => {
    const parentId = numberId(parent);
    if (parentId !== null && !adjacency.has(parentId)) {
      adjacency.set(parentId, new Set());
    }
    (children || []).forEach(child => addGraphEdge(adjacency, parent, child));
  });
  return adjacency;
}

function shortestModelJointPath(modelRig, startValue, endValue) {
  const start = numberId(startValue);
  const end = numberId(endValue);
  const startComponent = componentForJoint(modelRig, start);
  const endComponent = componentForJoint(modelRig, end);
  if (start === null || end === null || !startComponent
      || startComponent !== endComponent) return null;
  const adjacency = modelJointAdjacency(modelRig, startComponent);
  if (!adjacency.has(start) || !adjacency.has(end)) return null;
  const queue = [start];
  const previous = new Map([[start, null]]);
  while (queue.length) {
    const current = queue.shift();
    if (current === end) break;
    [...(adjacency.get(current) || [])].sort((left, right) => left - right)
      .forEach(next => {
        if (previous.has(next)) return;
        previous.set(next, current);
        queue.push(next);
      });
  }
  if (!previous.has(end)) return null;
  const path = [];
  for (let current = end; current !== null; current = previous.get(current)) {
    path.push(current);
  }
  return path.reverse();
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
