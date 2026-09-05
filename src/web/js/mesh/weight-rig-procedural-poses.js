import {Quaternion, Vector3} from 'three';

const EPSILON = 1e-8;
const DEFAULT_OUTWARD_ANGLE_DEGREES = 10;
const MIN_CONFIDENCE = 0.75;
const MIN_PAIR_MARGIN = 0.1;
const MIN_ARM_Y = 0.45;
const MAX_ARM_Y = 0.9;
const CENTERLINE_RATIO = 0.04;
const MIN_DIRECTION_LATERAL = 0.15;
const MIN_OUTWARD_EXTENT_RATIO = 0.06;
const MAX_CHAIN_DEPTH = 8;

export const BUILTIN_ARMS_UP_ID = 'builtin:arms-up';
export const BUILTIN_ARMS_UP_NAME = 'Arms Up';

function number(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function clamp(value, minimum = 0, maximum = 1) {
  return Math.max(minimum, Math.min(maximum, value));
}

function vector(value) {
  if (value?.isVector3) return value.clone();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? value : [value?.x, value?.y, value?.z];
  if (!values || values.length < 3) return null;
  const result = new Vector3(...values.slice(0, 3).map(Number));
  return result.toArray().every(Number.isFinite) ? result : null;
}

function valueFor(collection, id) {
  if (collection instanceof Map) {
    return collection.get(id) ?? collection.get(String(id));
  }
  return collection?.[id];
}

function vectorFor(collection, id) {
  return vector(valueFor(collection, id));
}

function normalize(value) {
  const result = vector(value);
  if (!result || result.lengthSq() <= EPSILON) return null;
  return result.normalize();
}

function semanticFrame(value = {}) {
  const source = value || {};
  const up = normalize(source.up) || new Vector3(0, 1, 0);
  let right = normalize(source.right) || new Vector3(1, 0, 0);
  right.addScaledVector(up, -right.dot(up));
  if (right.lengthSq() <= EPSILON) {
    right = Math.abs(up.x) < 0.9
      ? new Vector3(1, 0, 0) : new Vector3(0, 0, 1);
    right.addScaledVector(up, -right.dot(up));
  }
  right.normalize();
  let forward = normalize(source.forward) || new Vector3(0, 0, 1);
  forward.addScaledVector(up, -forward.dot(up))
    .addScaledVector(right, -forward.dot(right));
  if (forward.lengthSq() <= EPSILON) {
    forward = new Vector3().crossVectors(right, up);
  }
  forward.normalize();
  return {up, right, forward};
}

function serializedSemanticFrame(frame) {
  return {
    up: frame.up.toArray(),
    right: frame.right.toArray(),
    forward: frame.forward.toArray(),
  };
}

function lateralCoordinate(point, frame) {
  return point.dot(frame.right);
}

function heightCoordinate(point, frame) {
  return point.dot(frame.up);
}

function depthCoordinate(point, frame) {
  return point.dot(frame.forward);
}

function percentile(values, fraction, fallback = 0) {
  const finite = values.filter(Number.isFinite).sort((left, right) => left - right);
  if (!finite.length) return fallback;
  const index = (finite.length - 1) * clamp(fraction);
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return finite[lower];
  return finite[lower] + (finite[upper] - finite[lower]) * (index - lower);
}

function median(values, fallback = 0) {
  return percentile(values, 0.5, fallback);
}

function modelComponents(modelRig) {
  return (modelRig?.defaultComponents?.length
    ? modelRig.defaultComponents : modelRig?.components || [])
    .map(component => ({
      componentId: Number(component.componentId),
      rootId: Number(component.rootId),
      nodeIds: (component.nodeIds || []).map(Number).filter(Number.isInteger),
      parentById: {...(component.parentById || {})},
      childrenById: Object.fromEntries(Object.entries(
        component.childrenById || {}).map(([id, children]) => [
        id, (children || []).map(Number).filter(Number.isInteger),
      ])),
    }));
}

function defaultVectorMap(modelRig, name, fallbackName) {
  return modelRig?.[name] || modelRig?.[fallbackName] || null;
}

function rigData(modelRig) {
  const joints = (modelRig?.joints || []).map(joint => ({
    ...joint,
    jointId: Number(joint.jointId),
  })).filter(joint => Number.isInteger(joint.jointId));
  const byId = new Map(joints.map(joint => [joint.jointId, joint]));
  const components = modelComponents(modelRig);
  const parentById = new Map();
  const depthById = new Map();
  const childrenById = new Map(joints.map(joint => [joint.jointId, []]));
  const adjacency = new Map(joints.map(joint => [joint.jointId, new Set()]));
  const addEdge = (left, right) => {
    if (!byId.has(left) || !byId.has(right) || left === right) return;
    adjacency.get(left).add(right);
    adjacency.get(right).add(left);
  };
  components.forEach(component => {
    Object.entries(component.parentById || {}).forEach(([childValue, parentValue]) => {
      const childId = Number(childValue);
      if (parentValue === null || parentValue === undefined) return;
      const parentId = Number(parentValue);
      if (!Number.isInteger(childId) || !Number.isInteger(parentId)) return;
      parentById.set(childId, parentId);
      const children = childrenById.get(parentId) || [];
      if (!children.includes(childId)) children.push(childId);
      childrenById.set(parentId, children);
      addEdge(childId, parentId);
    });
    Object.entries(component.depthById || {}).forEach(([id, depth]) => {
      const jointId = Number(id);
      const value = Number(depth);
      if (Number.isInteger(jointId) && Number.isFinite(value)) {
        depthById.set(jointId, value);
      }
    });
    Object.entries(component.childrenById || {}).forEach(([parentValue, children]) =>
      (children || []).forEach(childValue => addEdge(Number(parentValue), Number(childValue))));
  });
  (modelRig?.forestEdges || []).forEach(edge => {
    const left = Number(edge.jointA);
    const right = Number(edge.jointB);
    addEdge(left, right);
  });
  const centers = new Map(joints.map(joint => [
    joint.jointId, vector(joint.restCenter) || new Vector3(),
  ]));
  const pivots = defaultVectorMap(
    modelRig, 'defaultRestPivotByJointId', 'restPivotByJointId');
  const directions = defaultVectorMap(
    modelRig, 'defaultRestDirectionByJointId', 'restDirectionByJointId');
  const rawRestDirections = new Map(joints.map(joint => {
    const mapped = valueFor(directions, joint.jointId);
    return [joint.jointId, mapped === undefined ? joint.restDirection : mapped];
  }));
  const restPivots = new Map(joints.map(joint => [
    joint.jointId,
    vectorFor(pivots, joint.jointId)
      || vector(joint.restPivot)
      || centers.get(joint.jointId).clone(),
  ]));
  const restDirections = new Map(joints.map(joint => [
    joint.jointId,
    normalize(vectorFor(directions, joint.jointId) || joint.restDirection),
  ]));
  return {
    joints, byId, components, parentById, childrenById, adjacency,
    centers, restPivots, restDirections, rawRestDirections,
    invalidRestDirectionIds: new Set(),
    depthById,
  };
}

function bodyFrame(data, frame) {
  const centers = [...data.centers.values()];
  const heightValues = centers.map(point => heightCoordinate(point, frame));
  const lateralValues = centers.map(point => lateralCoordinate(point, frame));
  const depthValues = centers.map(point => depthCoordinate(point, frame));
  const heightLow = percentile(heightValues, 0.05, 0);
  const heightHigh = percentile(heightValues, 0.95, heightLow + 1);
  const height = Math.max(EPSILON, heightHigh - heightLow);
  return {
    heightLow, heightHigh, height,
    // Keep the old names in diagnostics for callers that already inspect
    // body-frame output while all calculations use semantic projections.
    yLow: heightLow, yHigh: heightHigh,
    centerLateral: median(lateralValues, 0),
    centerDepth: median(depthValues, 0),
    centerX: median(lateralValues, 0),
    centerZ: median(depthValues, 0),
  };
}

function derivedDirection(data, jointId, fallbackDirection = null) {
  const own = data.restPivots.get(jointId) || data.centers.get(jointId);
  const parentId = data.parentById.get(jointId);
  const candidates = [...(data.adjacency.get(jointId) || [])]
    .filter(id => id !== parentId)
    .map(id => ({
      id,
      vector: (data.centers.get(id) || new Vector3()).clone().sub(own),
    }))
    .filter(item => item.vector.lengthSq() > EPSILON)
    .sort((left, right) => right.vector.lengthSq() - left.vector.lengthSq()
      || left.id - right.id);
  return normalize(candidates[0]?.vector)
    || normalize(own.clone().sub(data.centers.get(parentId) || own))
    || fallbackDirection?.clone()
    || new Vector3(0, 1, 0);
}

function outwardPath(data, jointId, side, frame, semantic) {
  const path = [jointId];
  // Start from every incident edge. The parent may itself be the outward
  // continuation when the inferred forest is oriented backwards; the
  // directed validation later reports that case without silently rerooting.
  let previous = null;
  let current = jointId;
  let lateral = side * (lateralCoordinate(
    data.centers.get(current) || new Vector3(), semantic) - frame.centerLateral);
  let length = 0;
  for (let depth = 0; depth < MAX_CHAIN_DEPTH; depth += 1) {
    const options = [...(data.adjacency.get(current) || [])]
      .filter(id => id !== previous)
      .map(id => {
        const point = data.centers.get(id);
        const currentPoint = data.centers.get(current);
        if (!point || !currentPoint) return null;
        return {
          id,
          point,
          lateral: side * (lateralCoordinate(point, semantic)
            - frame.centerLateral),
          distance: point.distanceTo(currentPoint),
        };
      })
      .filter(Boolean)
      .filter(item => item.lateral >= lateral - frame.height * 0.02)
      .sort((left, right) => right.lateral - left.lateral
        || right.distance - left.distance || left.id - right.id);
    const next = options[0];
    if (!next || next.id === previous) break;
    length += next.distance;
    path.push(next.id);
    previous = current;
    current = next.id;
    lateral = next.lateral;
  }
  const maxLateral = Math.max(...path.map(id =>
    side * (lateralCoordinate(data.centers.get(id) || new Vector3(), semantic)
      - frame.centerLateral)));
  return {
    ids: path,
    length,
    extent: Math.max(0, maxLateral
      - side * (lateralCoordinate(data.centers.get(jointId) || new Vector3(), semantic)
        - frame.centerLateral)),
  };
}

function candidateFor(data, joint, side, frame, semantic) {
  const id = joint.jointId;
  if (data.parentById.get(id) === undefined) return null;
  const anchor = data.restPivots.get(id) || data.centers.get(id);
  const direction = data.restDirections.get(id)
    || derivedDirection(data, id, semantic.up);
  const rawDirection = data.rawRestDirections.get(id);
  if (rawDirection !== undefined && rawDirection !== null
      && !data.restDirections.get(id)) {
    data.invalidRestDirectionIds.add(id);
    return null;
  }
  if (!anchor || !direction) return null;
  const anchorHeight = heightCoordinate(anchor, semantic);
  const anchorLateral = lateralCoordinate(anchor, semantic);
  const normalizedY = (anchorHeight - frame.heightLow) / frame.height;
  const sideOffset = side * (anchorLateral - frame.centerLateral);
  const directionLateral = direction.dot(semantic.right);
  const semanticDirection = [
    direction.dot(semantic.right), direction.dot(semantic.up),
    direction.dot(semantic.forward),
  ];
  if (normalizedY < MIN_ARM_Y || normalizedY > MAX_ARM_Y
      || sideOffset <= frame.height * CENTERLINE_RATIO
      || side * directionLateral < MIN_DIRECTION_LATERAL) return null;
  const path = outwardPath(data, id, side, frame, semantic);
  if (path.extent < frame.height * MIN_OUTWARD_EXTENT_RATIO
      || path.ids.length < 2) return null;
  const outwardNeighbors = [...(data.adjacency.get(id) || [])]
    .filter(childId => {
      const point = data.centers.get(childId);
      return point && side * (lateralCoordinate(point, semantic)
        - frame.centerLateral)
        >= sideOffset - frame.height * 0.02;
    });
  if (!outwardNeighbors.length) return null;
  const directedOutward = path.ids.slice(0, -1).every((parentId, index) =>
    (data.childrenById.get(parentId) || []).includes(path.ids[index + 1]));
  const heightScore = 1 - clamp(Math.abs(normalizedY - 0.67) / 0.25);
  const directionScore = clamp(
    (side * directionLateral - MIN_DIRECTION_LATERAL) / .65)
    * clamp((.65 - Math.max(0, direction.dot(semantic.up))) / .65);
  const extentScore = clamp(path.extent / (frame.height * .3));
  const chainScore = clamp((path.ids.length - 1) / 3);
  const topologyScore = clamp((outwardNeighbors.length <= 2 ? 1 : .5)
    * (path.ids.length >= 3 ? 1 : .7));
  const score = heightScore * .25 + directionScore * .35
    + extentScore * .2 + chainScore * .1 + topologyScore * .1;
  return {
    jointId: id,
    signature: typeof joint.signature === 'string' ? joint.signature : null,
    side,
    anchor: anchor.toArray(),
    direction: direction.toArray(),
    normalizedY,
    height: anchorHeight,
    lateral: anchorLateral,
    depthCoordinate: depthCoordinate(anchor, semantic),
    directionLateral,
    semanticDirection,
    sideOffset,
    pathIds: path.ids,
    orientationCompatible: directedOutward,
    chainLength: path.length,
    outwardExtent: path.extent,
    depth: number(data.depthById.get(id), number(joint.depth)),
    degree: (data.adjacency.get(id) || new Set()).size,
    score,
  };
}

function similarity(left, right, scale) {
  return 1 - clamp(Math.abs(left - right) / Math.max(EPSILON, scale));
}

function pairDirectionScore(left, right) {
  const leftDirection = new Vector3(...left.semanticDirection);
  const rightDirection = new Vector3(...right.semanticDirection);
  leftDirection.x *= -1;
  return clamp((leftDirection.dot(rightDirection) + 1) / 2);
}

function pairScore(left, right, frame) {
  const y = similarity(left.height, right.height, frame.height * .2);
  const lateral = similarity(left.sideOffset, right.sideOffset, frame.height * .4);
  const z = similarity(left.depthCoordinate, right.depthCoordinate,
    frame.height * .3);
  const direction = pairDirectionScore(left, right);
  const chain = similarity(left.chainLength, right.chainLength,
    frame.height * .8);
  const topology = (similarity(left.depth, right.depth, 3)
    + similarity(left.degree, right.degree, 2)) / 2;
  const extent = similarity(left.outwardExtent, right.outwardExtent,
    frame.height * .4);
  const score = symmetryScore({y, lateral, z, direction, chain, topology, extent});
  return {score, features: {y, lateral, z, direction, chain, topology, extent}};
}

function pathsOverlap(left, right) {
  const rightIds = new Set(right.pathIds);
  return left.pathIds.some(id => rightIds.has(id));
}

// A forearm and its upper arm can both satisfy the broad geometric gates.
// Collapse those same-chain observations before measuring pair ambiguity and
// keep the inward-most plausible control as the semantic upper arm.
function collapseSameChainCandidates(candidates) {
  const ordered = [...candidates].sort((left, right) =>
    left.sideOffset - right.sideOffset || right.score - left.score
      || left.jointId - right.jointId);
  const selected = [];
  ordered.forEach(candidate => {
    if (!selected.some(existing => pathsOverlap(existing, candidate))) {
      selected.push(candidate);
    }
  });
  return selected;
}

function symmetryScore(features) {
  return features.lateral * .25 + features.y * .2
    + features.direction * .2 + features.extent * .15
    + features.chain * .1 + features.z * .05 + features.topology * .05;
}

function targetDirectionFor(candidate, semantic, outwardAngleDegrees) {
  const direction = new Vector3(...candidate.direction).normalize();
  const horizontal = direction.clone()
    .addScaledVector(semantic.up, -direction.dot(semantic.up));
  if (horizontal.lengthSq() <= EPSILON) horizontal.copy(semantic.right)
    .multiplyScalar(candidate.side);
  else {
    horizontal.normalize();
    if (horizontal.dot(semantic.right) * candidate.side < 0) {
      horizontal.multiplyScalar(-1);
    }
  }
  const angle = number(outwardAngleDegrees, DEFAULT_OUTWARD_ANGLE_DEGREES)
    * Math.PI / 180;
  return semantic.up.clone().multiplyScalar(Math.cos(angle))
    .add(horizontal.multiplyScalar(Math.sin(angle))).normalize();
}

function armResult(candidate, semantic, outwardAngleDegrees) {
  const direction = new Vector3(...candidate.direction).normalize();
  const targetDirection = targetDirectionFor(
    candidate, semantic, outwardAngleDegrees);
  const rotation = new Quaternion().setFromUnitVectors(
    direction, targetDirection).normalize();
  return {
    jointId: candidate.jointId,
    signature: candidate.signature,
    score: candidate.score,
    restDirection: direction.toArray(),
    targetDirection: targetDirection.toArray(),
    rotation: rotation.toArray(),
    pathIds: [...candidate.pathIds],
  };
}

function unavailable(reason, confidence = 0, diagnostics = {}) {
  return {
    available: false,
    confidence: clamp(confidence),
    reason,
    preset: null,
    diagnostics,
  };
}

/** Infer symmetric upper-arm controls from the model's default rest Rig. */
export function analyzeHumanoidRestPose(modelRig, options = {}) {
  const data = rigData(modelRig);
  const semantic = semanticFrame(options?.semanticFrame);
  if (data.joints.length < 4 || !data.components.length) {
    return unavailable('insufficient_rig', 0, {
      jointCount: data.joints.length,
      semanticFrame: serializedSemanticFrame(semantic),
    });
  }
  const frame = bodyFrame(data, semantic);
  const fail = (reason, confidence, diagnostics = {}) => unavailable(
    reason, confidence, {
      ...diagnostics,
      semanticFrame: serializedSemanticFrame(semantic),
    });
  const geometryCandidates = [-1, 1].map(side => data.joints.map(joint =>
    candidateFor(data, joint, side, frame, semantic)).filter(Boolean));
  const candidates = geometryCandidates.map(collapseSameChainCandidates);
  if (!candidates[0].length || !candidates[1].length) {
    if (data.invalidRestDirectionIds.size) {
      return fail('invalid_rest_direction', 0, {
        invalidJointIds: [...data.invalidRestDirectionIds].sort((left, right) => left - right),
        candidateCounts: {negativeX: candidates[0].length, positiveX: candidates[1].length},
        bodyFrame: {...frame},
      });
    }
    return fail('arm_pair_not_found', 0, {
      candidateCounts: {negativeX: candidates[0].length, positiveX: candidates[1].length},
      geometryCandidateCounts: {
        negativeX: geometryCandidates[0].length,
        positiveX: geometryCandidates[1].length,
      },
      bodyFrame: {...frame},
    });
  }
  const pairs = [];
  candidates[0].forEach(negativeX => candidates[1].forEach(positiveX => {
    const pairing = pairScore(negativeX, positiveX, frame);
    const score = pairing.score * .7
      + (negativeX.score + positiveX.score) * .15;
    pairs.push({negativeX, positiveX, score, features: pairing.features});
  }));
  pairs.sort((left, right) => right.score - left.score
    || left.negativeX.jointId - right.negativeX.jointId
    || left.positiveX.jointId - right.positiveX.jointId);
  const best = pairs[0];
  const runnerUpScore = pairs[1]?.score ?? 0;
  const confidence = clamp(best.score);
  const diagnostics = {
    semanticFrame: serializedSemanticFrame(semantic),
    bodyFrame: {...frame},
    candidateCounts: {negativeX: candidates[0].length, positiveX: candidates[1].length},
    geometryCandidateCounts: {
      negativeX: geometryCandidates[0].length,
      positiveX: geometryCandidates[1].length,
    },
    negativeXCandidates: candidates[0],
    positiveXCandidates: candidates[1],
    runnerUpScore,
    pairFeatures: best.features,
  };
  if (confidence < MIN_CONFIDENCE) {
    return fail('arm_pair_low_confidence', confidence, diagnostics);
  }
  if (confidence - runnerUpScore < MIN_PAIR_MARGIN) {
    return fail('arm_pair_ambiguous', confidence, diagnostics);
  }
  if (!best.negativeX.orientationCompatible
      || !best.positiveX.orientationCompatible) {
    return fail('hierarchy_orientation_incompatible', confidence, {
      ...diagnostics,
      geometryCandidateCounts: {
        negativeX: geometryCandidates[0].length,
        positiveX: geometryCandidates[1].length,
      },
    });
  }
  if (!best.negativeX.signature || !best.positiveX.signature) {
    return fail('invalid_rest_direction', confidence, diagnostics);
  }
  const arms = {
    negativeX: armResult(best.negativeX, semantic, DEFAULT_OUTWARD_ANGLE_DEGREES),
    positiveX: armResult(best.positiveX, semantic, DEFAULT_OUTWARD_ANGLE_DEGREES),
  };
  const preset = {
    id: BUILTIN_ARMS_UP_ID,
    name: BUILTIN_ARMS_UP_NAME,
    roots: [],
    joints: [arms.negativeX, arms.positiveX].map(arm => ({
      joint_signature: arm.signature,
      rotation: [...arm.rotation],
    })).sort((left, right) => left.joint_signature.localeCompare(
      right.joint_signature)),
  };
  return {
    available: true,
    confidence,
    reason: null,
    preset,
    arms,
    semanticFrame: serializedSemanticFrame(semantic),
    diagnostics,
  };
}

/** Generate a schema-compatible transient Arms Up preset. */
export function generateArmsUpPreset(modelRig, options = {}) {
  const result = analyzeHumanoidRestPose(modelRig, options);
  if (!result.available || !options || options.outwardAngleDegrees === undefined) {
    return result;
  }
  const semantic = semanticFrame(options.semanticFrame || result.semanticFrame);
  const outwardAngleDegrees = number(
    options.outwardAngleDegrees, DEFAULT_OUTWARD_ANGLE_DEGREES);
  const arms = {
    negativeX: armResult({
      ...result.diagnostics.negativeXCandidates.find(item =>
        item.jointId === result.arms.negativeX.jointId),
    }, semantic, outwardAngleDegrees),
    positiveX: armResult({
      ...result.diagnostics.positiveXCandidates.find(item =>
        item.jointId === result.arms.positiveX.jointId),
    }, semantic, outwardAngleDegrees),
  };
  return {
    ...result,
    arms,
    preset: {
      ...result.preset,
      joints: [arms.negativeX, arms.positiveX].map(arm => ({
        joint_signature: arm.signature,
        rotation: [...arm.rotation],
      })).sort((left, right) => left.joint_signature.localeCompare(
        right.joint_signature)),
    },
  };
}

/** Return frontend-only built-in descriptors for the current model Rig. */
export function getBuiltInRigPoseDescriptors(modelRig, options = {}) {
  const analysis = analyzeHumanoidRestPose(modelRig, options);
  const diagnostics = analysis.diagnostics || {};
  const summary = {
    semanticFrame: diagnostics.semanticFrame || null,
    bodyFrame: diagnostics.bodyFrame || null,
    candidateCounts: diagnostics.candidateCounts || null,
    geometryCandidateCounts: diagnostics.geometryCandidateCounts || null,
    runnerUpScore: Number.isFinite(diagnostics.runnerUpScore)
      ? diagnostics.runnerUpScore : null,
    pairFeatures: diagnostics.pairFeatures || null,
    selectedJointIds: analysis.arms
      ? [analysis.arms.negativeX.jointId, analysis.arms.positiveX.jointId]
      : [],
  };
  return [{
    id: BUILTIN_ARMS_UP_ID,
    name: BUILTIN_ARMS_UP_NAME,
    kind: 'builtin',
    available: analysis.available,
    confidence: analysis.confidence,
    reason: analysis.reason,
    diagnostics: summary,
  }];
}
