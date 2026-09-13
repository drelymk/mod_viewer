import * as THREE from 'three';
import {HUMANOID_CONTROL_DRIVER_IDS} from './humanoid-control-rig.js';

// The control rig owns the semantic topology. Explicit mappings, their
// descendant subtrees and mapped paths claim deformation joints; every other
// ModelJoint uses geometry.
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
const DEFAULT_SECONDARY_DISTANCE_RATIO = 0.24;
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

function childJointIds(modelRig, jointId) {
  const id = numberId(jointId);
  if (id === null) return [];
  const component = componentForJoint(modelRig, id);
  const children = component?.childrenById instanceof Map
    ? component.childrenById.get(id) ?? component.childrenById.get(String(id))
    : component?.childrenById?.[id];
  return (children || []).map(numberId).filter(Number.isInteger)
    .sort((left, right) => left - right);
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

/** Build explicit and geometric ModelJoint bindings. */
export function buildHumanoidRigBinding({controlRig, modelRig,
    controlMappings = null, options = {}} = {}) {
  const height = Math.max(Number(controlRig?.frame?.height) || 0, EPSILON);
  const drivers = buildHumanoidDriverFrames(controlRig);
  const geometryLimit = Number.isFinite(Number(options.secondaryDistanceRatio))
    ? Number(options.secondaryDistanceRatio) : DEFAULT_SECONDARY_DISTANCE_RATIO;
  const jointBindings = new Map();
  const mapped = mappedControlEntries(controlMappings);
  const mappedPaths = resolvedPathCandidates(modelRig, controlMappings);
  const mappedJointIds = new Set(mapped.entries.map(entry => entry.jointId));
  let mappedControlCount = 0;
  let mappedDescendantCount = 0;

  // Explicit mappings claim their exact ModelJoint first. They are deformation
  // overrides, not evidence that other controls need to be resolved first.
  mapped.entries.forEach(({controlKey, jointId}) => {
    const driverId = HUMANOID_CONTROL_DRIVER_IDS[controlKey];
    const driver = drivers.get(driverId);
    const binding = driver ? directBindingFor(modelRig, jointId, {
      driverId,
      distance: 0,
      distanceRatio: 0,
      rawProjection: 0,
      projection: 0,
      endpointDistanceRatio: 0,
      score: 0,
    }, drivers, {bindingMethod: 'manual_control_mapping', controlKey}) : null;
    if (!binding) return;
    jointBindings.set(jointId, binding);
    mappedControlCount += 1;
  });

  // When both endpoints are explicitly mapped, only the interior shortest path
  // is claimed by that driver segment. Unmapped endpoints remain geometric.
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
    });
  });

  // A mapped ModelJoint owns all of its descendants. Explicitly mapped joints
  // are boundaries so a more specific mapping can own its own subtree.
  mapped.entries.forEach(({controlKey, jointId}) => {
    const driverId = HUMANOID_CONTROL_DRIVER_IDS[controlKey];
    const driver = drivers.get(driverId);
    if (!driver) return;
    const queue = childJointIds(modelRig, jointId);
    const visited = new Set([jointId]);
    let queueIndex = 0;
    while (queueIndex < queue.length) {
      const descendantId = numberId(queue[queueIndex++]);
      if (descendantId === null || visited.has(descendantId)) continue;
      visited.add(descendantId);
      if (mappedJointIds.has(descendantId)) continue;

      if (!jointBindings.has(descendantId)) {
        const binding = directBindingFor(modelRig, descendantId, {
          driverId,
          distance: 0,
          distanceRatio: 0,
          rawProjection: 0,
          projection: 0,
          endpointDistanceRatio: 0,
          score: 0,
        }, drivers, {
          bindingMethod: 'mapped_control_descendant',
          controlKey,
          mappedRootJointId: jointId,
        });
        if (binding) {
          jointBindings.set(descendantId, binding);
          mappedDescendantCount += 1;
        }
      }
      childJointIds(modelRig, descendantId).forEach(child => {
        if (!visited.has(child)) queue.push(child);
      });
    }
  });

  // Every remaining ModelJoint uses the nearest valid Main Rig driver. The
  // broader secondary limit provides limb coverage without a control-mapping
  // inference layer or ambiguity veto.
  allJointIds(modelRig).forEach(jointId => {
    if (jointBindings.has(jointId)) return;
    const point = pointForJoint(modelRig, jointId);
    if (!point) return;
    const candidates = sortedCandidates(point, drivers, height, controlRig);
    const best = articulationPreferredCandidate(candidates);
    if (!best || best.distanceRatio > geometryLimit) return;
    const binding = directBindingFor(modelRig, jointId, best, drivers,
      {bindingMethod: 'geometric_proximity'});
    if (binding) jointBindings.set(jointId, binding);
  });

  const allIds = allJointIds(modelRig);
  const unboundJointIds = allIds.filter(id => !jointBindings.has(id));
  const byDriver = {};
  jointBindings.forEach(entry => {
    byDriver[entry.driverId] = (byDriver[entry.driverId] || 0) + 1;
  });
  const binding = {
    version: 1,
    jointBindings,
    unboundJointIds,
    diagnostics: {
      mappedControlCount,
      mappedPathCount: mappedPaths.candidates.length,
      mappedDescendantCount,
      geometricBindingCount: [...jointBindings.values()].filter(entry =>
        entry.bindingMethod === 'geometric_proximity').length,
      unboundJointCount: unboundJointIds.length,
      bindingsByDriver: byDriver,
    },
  };
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
  return {driverWorldByJointId, result};
}

export {restJointWorldMatrix};
