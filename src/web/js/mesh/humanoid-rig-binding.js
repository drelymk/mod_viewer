import * as THREE from 'three';
import {
  HUMANOID_CONTROL_DRIVER_IDS, HUMANOID_CONTROL_KEYS,
} from './humanoid-control-rig.js';

// The control rig owns the semantic topology. Explicitly mapped ModelJoints
// own their source-connected descendants until another mapped control or a
// resolved path takes ownership; geometric inference resolves missing controls
// before the binding is built.
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
 * also applies binding-specific distance gates. Returning the candidate list
 * makes ambiguity a visible, conservative outcome instead of silently forcing
 * a classification.
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

function controlIncidentSegments(controlKey) {
  return HUMANOID_DRIVER_SEGMENTS.filter(segment =>
    segment.start === controlKey || segment.end === controlKey);
}

const HUMANOID_LIMB_CONTROL_KEYS = Object.freeze({
  left_arm: Object.freeze(['leftShoulder', 'leftElbow', 'leftHand']),
  right_arm: Object.freeze(['rightShoulder', 'rightElbow', 'rightHand']),
  left_leg: Object.freeze(['leftHip', 'leftKnee', 'leftFoot']),
  right_leg: Object.freeze(['rightHip', 'rightKnee', 'rightFoot']),
});

function candidateSummary(candidates) {
  return (candidates || []).slice(0, 4).map(candidate => ({
    jointId: candidate.jointId,
    distanceRatio: candidate.distanceRatio,
    score: candidate.score,
  }));
}

function geometricCandidatesForControl(controlKey, jointIds, reservedJointIds,
    modelRig, drivers, controlRig, height, corridorLimit, maximumDistanceRatio) {
  const controlValue = controlRig?.controls?.[controlKey];
  const controlPointValue = controlValue?.position ?? controlValue;
  if (!controlPointValue) {
    return {allCandidates: [], candidates: [], reason: 'control_position_missing'};
  }
  const controlPointValueVector = vector(controlPointValue);
  const allCandidates = jointIds.map(jointId => {
    if (reservedJointIds.has(jointId)) return null;
    return geometricControlCandidate(controlKey, controlPointValueVector,
      jointId, modelRig, drivers, controlRig, height, corridorLimit);
  }).filter(Boolean);
  const candidates = allCandidates.filter(candidate =>
    candidate.distanceRatio <= maximumDistanceRatio)
    .sort((left, right) => left.score - right.score
      || left.jointId - right.jointId);
  return {
    allCandidates, candidates,
    reason: candidates.length ? null : (jointIds.length
      ? 'outside_geometry_threshold' : 'no_model_joints'),
  };
}

function topologyForLimb(modelRig) {
  return (modelRig?.components || []).some(component =>
    Array.isArray(component?.nodeIds)
      && (Object.keys(component.parentById || {}).length
        || Object.keys(component.childrenById || {}).length));
}

function simpleLimbPath(modelRig, jointIds) {
  const result = [];
  const seen = new Set();
  for (let index = 0; index < jointIds.length - 1; index += 1) {
    const path = shortestModelJointPath(modelRig, jointIds[index],
      jointIds[index + 1]);
    if (!path || path.length < 2) return null;
    const segment = index ? path.slice(1) : path;
    if (segment.some(jointId => seen.has(jointId))) return null;
    segment.forEach(jointId => {
      seen.add(jointId);
      result.push(jointId);
    });
  }
  return result;
}

function terminalProgressPenalty(controlKey, candidate) {
  if (!/(Hand|Foot)$/.test(controlKey)) return 0;
  const progress = Number(candidate?.corridorProjection);
  return Number.isFinite(progress) ? Math.max(0, 1 - progress) * .08 : 0;
}

function topologyLimbResolution(controlKeys, explicitByControl, candidateSets,
    modelRig, ambiguityMargin) {
  const choices = controlKeys.map(controlKey => {
    const explicit = explicitByControl.get(controlKey);
    if (explicit) {
      return [{controlKey, jointId: explicit.jointId, score: 0,
        mapping: explicit.mapping}];
    }
    return (candidateSets.get(controlKey)?.candidates || [])
      .slice(0, 4)
      .map(candidate => ({...candidate, controlKey}));
  });
  if (choices.some(items => !items.length)) return null;

  const combinations = [];
  const visit = (index, selected) => {
    if (index >= choices.length) {
      const jointIds = selected.map(item => item.jointId);
      if (new Set(jointIds).size !== jointIds.length) return;
      const path = simpleLimbPath(modelRig, jointIds);
      if (!path) return;
      const score = selected.reduce((total, item) => total
        + (Number(item.score) || 0)
        + terminalProgressPenalty(item.controlKey, item), 0)
        + (path.length - selected.length) * .0005;
      combinations.push({selected, path, score});
      return;
    }
    choices[index].forEach(item => visit(index + 1, [...selected, item]));
  };
  visit(0, []);
  combinations.sort((left, right) => left.score - right.score
    || left.path.join(',').localeCompare(right.path.join(',')));
  const best = combinations[0];
  if (!best) return null;
  // A coherent forest path is stronger evidence than a small raw point-score
  // gap, but an exact structural/score tie must remain unresolved.
  const topologyAmbiguityMargin = Math.min(ambiguityMargin, .01) * .01;
  if (combinations[1]
      && combinations[1].score - best.score < topologyAmbiguityMargin) {
    return null;
  }
  return new Map(best.selected.map(item => [item.controlKey, item]));
}

function geometricControlCandidate(controlKey, controlPointValue, jointId,
    modelRig, drivers, controlRig, height, corridorLimit) {
  const pivot = pointForJoint(modelRig, jointId);
  if (!pivot) return null;
  const segments = controlIncidentSegments(controlKey);
  const corridorCandidates = segments.map(segment => {
    const driver = drivers.get(segment.id);
    if (!driver) return null;
    const candidate = candidateForPoint(pivot, driver, height);
    if (!semanticCandidateAllowed(
      pivot, driver, candidate, controlRig, height)) return null;
    return {...candidate, driverId: segment.id};
  }).filter(Boolean).sort((left, right) => left.score - right.score
    || segmentOrder(left.driverId) - segmentOrder(right.driverId));
  const corridor = corridorCandidates[0];
  if (!corridor || corridor.distanceRatio > corridorLimit) return null;
  const distance = pivot.distanceTo(controlPointValue);
  const distanceRatio = distance / Math.max(height, EPSILON);
  return {
    controlKey,
    jointId,
    distance,
    distanceRatio,
    corridorDistanceRatio: corridor.distanceRatio,
    corridorDriverId: corridor.driverId,
    corridorProjection: corridor.projection,
    score: distanceRatio + corridor.distanceRatio * .1,
    confidence: confidenceForDistance(distanceRatio),
  };
}

function controlResolutionEntry(controlKey, mapping, modelRig) {
  const jointId = numberId(mapping?.jointId);
  const joint = jointForId(modelRig, jointId);
  return {
    controlKey,
    jointId,
    method: mapping?.method === 'geometric' ? 'geometric' : 'manual',
    confidence: mapping?.method === 'geometric'
      ? mapping.confidence || 'low' : 'high',
    ...(mapping?.distanceRatio !== undefined
      ? {distanceRatio: Number(mapping.distanceRatio)} : {}),
    ...(mapping?.reason ? {reason: mapping.reason} : {}),
    ...(mapping?.jointSignature ? {jointSignature: mapping.jointSignature} : {}),
    sourceMembers: [...(mapping?.sourceMembers || joint?.members || [])]
      .map(member => ({...member})),
  };
}

/**
 * Resolve every Main Rig control independently against the completed
 * ModelRig. Saved mappings are authoritative for their own controls; every
 * other control gets a conservative, runtime-only geometric attempt.
 */
export function resolveHumanoidEffectiveMappings({controlRig, modelRig,
    explicitMappings = null, options = {}} = {}) {
  const result = new Map();
  const explicit = mappedControlEntries(explicitMappings);
  const explicitEntries = explicit.entries.filter(entry =>
    !!jointForId(modelRig, entry.jointId));
  const explicitByControl = new Map(explicitEntries.map(entry =>
    [entry.controlKey, entry]));
  const reservedJointIds = new Set(explicitEntries.map(entry => entry.jointId));
  const height = Math.max(Number(controlRig?.frame?.height) || 0, EPSILON);
  const drivers = buildHumanoidDriverFrames(controlRig);
  const maximumDistanceRatio = Number.isFinite(
    Number(options.maximumDistanceRatio))
    ? Number(options.maximumDistanceRatio) : DEFAULT_DIRECT_DISTANCE_RATIO;
  const corridorLimit = Number.isFinite(Number(options.corridorDistanceRatio))
    ? Number(options.corridorDistanceRatio) : DEFAULT_SECONDARY_DISTANCE_RATIO;
  const ambiguityMargin = Number.isFinite(Number(options.ambiguityMarginRatio))
    ? Number(options.ambiguityMarginRatio) : DEFAULT_AMBIGUITY_MARGIN_RATIO;
  const jointIds = allJointIds(modelRig);

  const setManualMapping = (controlKey, entry) => {
    result.set(controlKey, controlResolutionEntry(controlKey, {
      ...entry.mapping,
      method: 'manual',
      jointId: entry.jointId,
    }, modelRig));
  };
  explicitByControl.forEach((entry, controlKey) => {
    setManualMapping(controlKey, entry);
  });

  const candidateSets = new Map();
  const candidateSetFor = controlKey => {
    if (!candidateSets.has(controlKey)) {
      candidateSets.set(controlKey, geometricCandidatesForControl(
        controlKey, jointIds, reservedJointIds, modelRig, drivers, controlRig,
        height, corridorLimit, maximumDistanceRatio));
    }
    return candidateSets.get(controlKey);
  };
  const setUnresolved = (controlKey, candidateSet, reason = null) => {
    const finalReason = reason || candidateSet?.reason || 'no_model_joints';
    const candidates = candidateSet?.candidates?.length
      ? candidateSet.candidates : candidateSet?.allCandidates;
    result.set(controlKey, {
      controlKey, jointId: null, method: 'unresolved', reason: finalReason,
      candidates: candidateSummary(candidates),
    });
  };
  const setGeometricMapping = (controlKey, candidate, candidates = []) => {
    const joint = jointForId(modelRig, candidate.jointId);
    result.set(controlKey, {
      controlKey,
      jointId: candidate.jointId,
      method: 'geometric',
      confidence: candidate.confidence,
      distanceRatio: candidate.distanceRatio,
      corridorDistanceRatio: candidate.corridorDistanceRatio,
      corridorDriverId: candidate.corridorDriverId,
      sourceMembers: [...(joint?.members || [])].map(member => ({...member})),
      candidates: candidateSummary(candidates),
    });
    reservedJointIds.add(candidate.jointId);
  };
  const resolveIndependently = controlKey => {
    if (explicitByControl.has(controlKey)) return;
    const candidateSet = candidateSetFor(controlKey);
    const candidates = candidateSet.candidates;
    const best = candidates[0];
    if (!best) {
      setUnresolved(controlKey, candidateSet);
      return;
    }
    if (candidates[1] && candidates[1].score - best.score < ambiguityMargin) {
      setUnresolved(controlKey, candidateSet, 'ambiguous_geometry');
      return;
    }
    setGeometricMapping(controlKey, best, candidates);
  };

  // Central controls have no useful chain topology. Preserve their strict,
  // independent point resolution before using the ModelJoint forest to
  // disambiguate limb candidates.
  ['chest', 'pelvis', 'neck', 'head'].forEach(resolveIndependently);
  Object.entries(HUMANOID_LIMB_CONTROL_KEYS).forEach(([, controlKeys]) => {
    const unresolvedKeys = controlKeys.filter(key => !explicitByControl.has(key));
    if (!unresolvedKeys.length) return;
    const sets = new Map(controlKeys.map(key => [key, candidateSetFor(key)]));
    const topologyResolution = topologyForLimb(modelRig)
      ? topologyLimbResolution(controlKeys, explicitByControl, sets, modelRig,
        ambiguityMargin)
      : null;
    if (topologyResolution) {
      topologyResolution.forEach((candidate, controlKey) => {
        if (explicitByControl.has(controlKey)) return;
        setGeometricMapping(controlKey, candidate, sets.get(controlKey)?.candidates);
      });
      return;
    }
    unresolvedKeys.forEach(resolveIndependently);
  });
  return result;
}

function resolvedPathCandidates(modelRig, controlMappings) {
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
    String(edge?.relationshipType || '').toLowerCase() !== 'attachment')
    .map(edge => {
    const left = numberId(edge?.jointA ?? edge?.boneA);
    const right = numberId(edge?.jointB ?? edge?.boneB);
    return left === null || right === null ? null
      : `${Math.min(left, right)}:${Math.max(left, right)}`;
  }).filter(Boolean));
  return children.filter(child => sourcePairs.has(
    `${Math.min(Number(jointId), child)}:${Math.max(Number(jointId), child)}`));
}

function mappedDescendantOwnership(modelRig, jointId, driverId, drivers,
    controlRig, height, secondaryLimit, ambiguityMargin) {
  const point = pointForJoint(modelRig, jointId);
  const driver = drivers.get(driverId);
  if (!point || !driver) return 'unknown';

  // A mapped ancestor must retain unclassified helper/cloth descendants. Only
  // stop when another driver is a clear, unambiguous semantic owner.
  const candidates = sortedCandidates(point, drivers, height, controlRig);
  const best = articulationPreferredCandidate(candidates);
  const ambiguous = isAmbiguous(candidates, ambiguityMargin)
    && !isExactArticulationTie(candidates, best);
  const confidentlyDifferent = !ambiguous
    && best && best.driverId !== driverId
    && best.distanceRatio <= secondaryLimit
    && confidenceForDistance(best.distanceRatio) !== 'low';
  if (confidentlyDifferent) return 'different';

  const ownCandidate = candidateForPoint(point, driver, height);
  if (semanticCandidateAllowed(point, driver, ownCandidate, controlRig, height)
      && ownCandidate.distanceRatio <= secondaryLimit) {
    return 'same';
  }
  return 'unknown';
}

function mappedDescendantJointIds(modelRig, rootId, mappedJointIds,
    jointBindings, driverId, drivers, controlRig, height, secondaryLimit,
    ambiguityMargin) {
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
    const ownership = mappedDescendantOwnership(modelRig, jointId, driverId,
      drivers, controlRig, height, secondaryLimit, ambiguityMargin);
    if (ownership === 'different') continue;
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
 * Build humanoid deltas for the exact source bones owned by the final binding.
 * The result stays source-local so an unassigned member of the same ModelJoint
 * does not inherit another member's humanoid motion.
 */
export function buildHumanoidSourceBoneDriverTransforms({binding,
    controlRig, posedControls = null} = {}) {
  const result = new Map();
  const restDrivers = buildHumanoidDriverFrames(controlRig);
  const posedDrivers = buildHumanoidDriverFrames(controlRig, posedControls);
  const entries = binding?.sourceBoneAssignments instanceof Map
    ? [...binding.sourceBoneAssignments.values()]
    : Object.values(binding?.sourceBoneAssignments || {});
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
export function buildHumanoidRigBinding({controlRig, modelRig,
    effectiveMappings = null, controlMappings = null, options = {}} = {}) {
  const started = typeof performance !== 'undefined' && performance.now
    ? performance.now() : Date.now();
  const height = Math.max(Number(controlRig?.frame?.height) || 0, EPSILON);
  const drivers = buildHumanoidDriverFrames(controlRig);
  const directLimit = Number.isFinite(Number(options.directDistanceRatio))
    ? Number(options.directDistanceRatio) : DEFAULT_DIRECT_DISTANCE_RATIO;
  const secondaryLimit = Number.isFinite(Number(options.secondaryDistanceRatio))
    ? Number(options.secondaryDistanceRatio) : DEFAULT_SECONDARY_DISTANCE_RATIO;
  const ambiguityMargin = Number.isFinite(Number(options.ambiguityMarginRatio))
    ? Number(options.ambiguityMarginRatio) : DEFAULT_AMBIGUITY_MARGIN_RATIO;
  const jointBindings = new Map();
  const candidatesByJointId = new Map();
  let ambiguousBindingCount = 0;
  const resolvedMappings = effectiveMappings instanceof Map
    ? effectiveMappings
    : resolveHumanoidEffectiveMappings({
      controlRig, modelRig, explicitMappings: controlMappings, options,
    });
  const mapped = mappedControlEntries(resolvedMappings);
  const mappedPaths = resolvedPathCandidates(modelRig, resolvedMappings);
  const mappedJointIds = new Set(mapped.entries.map(entry => entry.jointId));
  const sourceBoneAssignments = new Map();
  let mappedDescendantJointCount = 0;

  // Every resolved control owns its ModelJoint before generic geometric
  // corridor scoring is considered. The mapping method remains visible in the
  // binding so inferred controls cannot masquerade as manual snaps.
  mapped.entries.forEach(({controlKey, mapping, jointId}) => {
      const driverId = HUMANOID_CONTROL_DRIVER_IDS[controlKey];
      const driver = drivers.get(driverId);
      const restJointWorld = jointId === null
        ? null : restJointWorldMatrix(modelRig, jointId);
      if (jointId === null || !driver || !restJointWorld) return;
      const localMatrix = driver.matrix.clone().invert().multiply(restJointWorld);
      const sourceMembers = sourceMembersForJoint(modelRig, jointId, mapping);
      const controlMethod = mapping?.method === 'geometric'
        ? 'geometric_control_mapping' : 'manual_control_mapping';
      jointBindings.set(jointId, {
        type: 'driver', driverId, localMatrix, restJointWorld,
        distance: 0, distanceRatio: 0, rawProjection: 0, projection: 0,
        endpointDistanceRatio: 0, score: 0, confidence: 'high',
        bindingMethod: controlMethod,
        controlKey,
        sourceBoneKeys: sourceMembers.map(member => member.sourceBoneKey).sort(),
      });
      sourceMembers.forEach(member => sourceBoneAssignments.set(
        member.sourceBoneKey, sourceAssignmentForMember(member, driverId, {
          limbRole: sourceRoleForControl(controlKey),
          progress: sourceProgress(controlKey),
          segmentIndex: /Elbow|Knee|Hand|Foot/.test(controlKey) ? 1 : 0,
          bindingMethod: controlMethod,
          controlKey,
          controlResolutionMethod: mapping?.method || 'manual',
        })));
  });

  // When both canonical endpoints are resolved, the shortest path in their
  // component's undirected ModelJoint graph owns the segment. Only the
  // interior joints are claimed here; endpoints remain control mappings.
  mappedPaths.candidates.forEach(({segment, path, start, end}) => {
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
         segmentStartMethod: start.mapping?.method || 'manual',
         segmentEndMethod: end.mapping?.method || 'manual',
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
            controlResolutionMethod: 'resolved_joint_path',
          })));
    });
  });

  // A resolved control is an authoritative anchor for its source-connected
  // descendants. Walk through same-driver mapped path joints, but stop at a
  // different resolved control so each control remains a boundary.
  // Attachment edges are excluded by sourceChildIds; accessories retain the
  // existing conservative binding behavior.
  mapped.entries.forEach(({controlKey, jointId}) => {
    const driverId = HUMANOID_CONTROL_DRIVER_IDS[controlKey];
    const driver = drivers.get(driverId);
    if (!driver || jointId === null) return;
    const controlResolutionMethod = resolvedMappings.get(controlKey)?.method
      || 'manual';
    const descendantBindingMethod = controlResolutionMethod === 'geometric'
      ? 'geometric_control_descendant' : 'mapped_control_descendant';
    const descendants = mappedDescendantJointIds(modelRig, jointId,
      mappedJointIds, jointBindings, driverId, drivers, controlRig, height,
      secondaryLimit, ambiguityMargin);
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
        bindingMethod: descendantBindingMethod,
        controlKey,
        mappedRootJointId: jointId,
        sourceBoneKeys: sourceMembers.map(member => member.sourceBoneKey).sort(),
      });
      sourceMembers.forEach(member => sourceBoneAssignments.set(
        member.sourceBoneKey, sourceAssignmentForMember(member, driverId, {
          limbRole: sourceRoleForControl(controlKey),
          progress: sourceProgress(controlKey),
          segmentIndex: /Elbow|Knee|Hand|Foot/.test(controlKey) ? 1 : 0,
          bindingMethod: descendantBindingMethod,
          controlKey,
          controlResolutionMethod,
          mappedRootJointId: jointId,
          pathMember: false,
          branchMember: true,
        })));
      mappedDescendantJointCount += 1;
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
  const controlResolution = Object.fromEntries(
    [...resolvedMappings.entries()].map(([controlKey, resolution]) => [
      controlKey, {
        ...resolution,
        sourceMembers: (resolution.sourceMembers || [])
          .map(member => ({...member})),
        candidates: (resolution.candidates || [])
          .map(candidate => ({...candidate})),
      },
    ]));
  const resolutionEntries = [...resolvedMappings.values()];
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
      resolvedPathCount: mappedPaths.candidates.length,
      resolvedPathJointCount: mappedPaths.candidates.reduce((sum, candidate) =>
        sum + Math.max(0, candidate.path.length - 2), 0),
      mappedDescendantJointCount,
      resolvedPathConflicts: mappedPaths.conflicts,
      controlResolution,
      manualControlCount: resolutionEntries.filter(entry =>
        entry.method === 'manual' && Number.isInteger(numberId(entry.jointId))).length,
      geometricControlCount: resolutionEntries.filter(entry =>
        entry.method === 'geometric' && Number.isInteger(numberId(entry.jointId))).length,
      unresolvedControlCount: resolutionEntries.filter(entry =>
        entry.method === 'unresolved' || !Number.isInteger(numberId(entry.jointId))).length,
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
