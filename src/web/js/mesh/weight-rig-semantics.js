import {Vector3} from 'three';

const EPSILON = 1e-8;
const HAND_RADIUS_RATIO = .18;
const MIN_HAND_SIDE_RATIO = .16;
const MIN_HAND_HEIGHT = .2;
const MAX_HAND_HEIGHT = .88;
const MIN_FINGER_LENGTH_RATIO = .012;
const FINGER_DIRECTION_COSINE = .92;
const MIN_FINGER_RAYS = 3;
const MIN_HAND_CONFIDENCE = .52;
const MIN_ARM_CONFIDENCE = .48;
const semanticCache = new Map();

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

function normalize(value) {
  const result = vector(value);
  if (!result || result.lengthSq() <= EPSILON) return null;
  return result.normalize();
}

/** Normalize the semantic axes used by all spatial landmark calculations. */
export function normalizeSemanticFrame(value = {}) {
  const source = value || {};
  const up = normalize(source.up) || new Vector3(0, 1, 0);
  let right = normalize(source.right) || new Vector3(1, 0, 0);
  right.addScaledVector(up, -right.dot(up));
  if (right.lengthSq() <= EPSILON) {
    right = Math.abs(up.x) < .9
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

export function serializeSemanticFrame(frame) {
  return {
    up: frame.up.toArray(),
    right: frame.right.toArray(),
    forward: frame.forward.toArray(),
  };
}

function percentile(values, fraction, fallback = 0) {
  const finite = values.filter(Number.isFinite).sort((left, right) => left - right);
  if (!finite.length) return fallback;
  const position = (finite.length - 1) * clamp(fraction);
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return finite[lower];
  return finite[lower] + (finite[upper] - finite[lower])
    * (position - lower);
}

function weightedPercentile(values, fraction, fallback = 0) {
  const finite = values.filter(item => Number.isFinite(item.value)
    && Number.isFinite(item.weight) && item.weight > 0)
    .sort((left, right) => left.value - right.value);
  if (!finite.length) return fallback;
  const total = finite.reduce((sum, item) => sum + item.weight, 0);
  const target = clamp(fraction) * total;
  let accumulated = 0;
  for (const item of finite) {
    accumulated += item.weight;
    if (accumulated >= target) return item.value;
  }
  return finite[finite.length - 1].value;
}

function median(values, fallback = 0) {
  return percentile(values, .5, fallback);
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

function componentData(modelRig, joints) {
  const source = modelRig?.defaultComponents?.length
    ? modelRig.defaultComponents : modelRig?.components || [];
  const parentById = new Map();
  const childrenById = new Map(joints.map(joint => [joint.jointId, []]));
  const componentByJointId = new Map();
  const depthById = new Map();
  const adjacency = new Map(joints.map(joint => [joint.jointId, new Set()]));
  const addEdge = (leftValue, rightValue) => {
    const left = Number(leftValue);
    const right = Number(rightValue);
    if (!Number.isInteger(left) || !Number.isInteger(right)
        || left === right || !adjacency.has(left) || !adjacency.has(right)) {
      return;
    }
    adjacency.get(left).add(right);
    adjacency.get(right).add(left);
  };
  source.forEach(component => {
    const componentId = Number(component.componentId);
    (component.nodeIds || []).forEach(jointId => {
      const id = Number(jointId);
      if (Number.isInteger(id)) componentByJointId.set(id, componentId);
    });
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
    Object.entries(component.childrenById || {}).forEach(([parentValue, children]) =>
      (children || []).forEach(childValue => {
        const parentId = Number(parentValue);
        const childId = Number(childValue);
        if (Number.isInteger(parentId) && Number.isInteger(childId)) {
          if (!parentById.has(childId)) parentById.set(childId, parentId);
          const childList = childrenById.get(parentId) || [];
          if (!childList.includes(childId)) childList.push(childId);
          childrenById.set(parentId, childList);
        }
        addEdge(parentValue, childValue);
      }));
    Object.entries(component.depthById || {}).forEach(([id, depth]) => {
      if (Number.isInteger(Number(id)) && Number.isFinite(Number(depth))) {
        depthById.set(Number(id), Number(depth));
      }
    });
  });
  (modelRig?.forestEdges || []).forEach(edge =>
    addEdge(edge.jointA, edge.jointB));
  // A lightweight snapshot may expose topology on each joint without a
  // component forest. Keep that information as a fallback for semantic
  // analysis, without mixing a live hierarchy into an available rest forest.
  const hasForestTopology = parentById.size > 0
    || [...adjacency.values()].some(neighbors => neighbors.size > 0);
  if (!hasForestTopology) joints.forEach(joint => {
    const childId = joint.jointId;
    const parentId = joint.parentId === null || joint.parentId === undefined
      ? null : Number(joint.parentId);
    if (Number.isInteger(parentId) && parentId !== childId) {
      parentById.set(childId, parentId);
      const children = childrenById.get(parentId) || [];
      if (!children.includes(childId)) children.push(childId);
      childrenById.set(parentId, children);
      addEdge(childId, parentId);
    }
    (joint.childrenIds || []).forEach(value => {
      const child = Number(value);
      if (!Number.isInteger(child)) return;
      const children = childrenById.get(childId) || [];
      if (!children.includes(child)) children.push(child);
      childrenById.set(childId, children);
      addEdge(childId, child);
    });
  });
  return {parentById, childrenById, componentByJointId, depthById, adjacency};
}

function supportFor(joint) {
  const evidence = joint?.evidence || {};
  const affected = number(evidence.affectedVertexCount,
    number(joint?.affectedVertexCount));
  const totalWeight = number(evidence.totalWeight,
    number(joint?.totalWeight));
  return Math.max(1, affected, totalWeight);
}

function semanticData(modelRig, semantic) {
  const joints = (modelRig?.joints || []).map(joint => ({
    ...joint,
    jointId: Number(joint.jointId),
  })).filter(joint => Number.isInteger(joint.jointId));
  const topology = componentData(modelRig, joints);
  const pivots = modelRig?.defaultRestPivotByJointId
    || modelRig?.restPivotByJointId;
  const records = joints.map(joint => {
    const center = vector(joint.restCenter) || new Vector3();
    const pivot = vectorFor(pivots, joint.jointId)
      || vector(joint.restPivot) || center.clone();
    const evidence = joint.evidence || {};
    const affectedVertexCount = number(evidence.affectedVertexCount,
      number(joint.affectedVertexCount));
    const totalWeight = number(evidence.totalWeight,
      number(joint.totalWeight));
    return {
      jointId: joint.jointId,
      signature: typeof joint.signature === 'string' ? joint.signature : null,
      center,
      pivot,
      support: supportFor(joint),
      affectedVertexCount,
      totalWeight,
      componentId: topology.componentByJointId.get(joint.jointId) ?? null,
      depthIndex: number(topology.depthById.get(joint.jointId), number(joint.depth)),
      parentId: topology.parentById.get(joint.jointId) ?? null,
      childrenIds: [...(topology.childrenById.get(joint.jointId) || [])],
      neighbors: [...(topology.adjacency.get(joint.jointId) || [])],
      height: center.dot(semantic.up),
      lateral: center.dot(semantic.right),
      depth: center.dot(semantic.forward),
    };
  });
  const supportValues = records.map(record => record.support);
  const supportFloor = percentile(supportValues, .25, 1);
  const core = records.filter(record => record.support >= supportFloor);
  const bodyPoints = core.length >= 4 ? core : records;
  const heights = bodyPoints.map(record => record.height);
  // Percentiles reject isolated accessory extremes, while a central corridor
  // keeps a single supported head or foot from disappearing when a Rig has
  // many finger controls at the lateral extremes.
  const lateralCenterSeed = median(bodyPoints.map(record => record.lateral), 0);
  const lateralDistances = bodyPoints.map(record =>
    Math.abs(record.lateral - lateralCenterSeed));
  const centralLimit = percentile(lateralDistances, .4, Infinity);
  const centralPoints = bodyPoints.filter(record =>
    Math.abs(record.lateral - lateralCenterSeed) <= centralLimit);
  const centralHeights = centralPoints.length
    ? centralPoints.map(record => record.height) : heights;
  const robustBottom = percentile(heights, .02, 0);
  const robustTop = percentile(heights, .98, robustBottom + 1);
  const bottom = Math.min(robustBottom, percentile(centralHeights, .02,
    robustBottom));
  const top = Math.max(robustTop, percentile(centralHeights, .98,
    robustTop));
  const height = Math.max(EPSILON, top - bottom);
  const centerLateral = weightedPercentile(
    bodyPoints.map(record => ({value: record.lateral, weight: record.support})),
    .5, median(records.map(record => record.lateral), 0));
  const centerDepth = weightedPercentile(
    bodyPoints.map(record => ({value: record.depth, weight: record.support})),
    .5, median(records.map(record => record.depth), 0));
  const bodyFrame = {
    bottom, top, height, centerLateral, centerDepth,
    // These aliases make the diagnostics easy to compare with the older
    // arm detector while the semantic names remain authoritative.
    heightLow: bottom, heightHigh: top,
  };
  records.forEach(record => {
    record.height01 = (record.height - bottom) / height;
    record.lateral01 = (record.lateral - centerLateral) / height;
    record.depth01 = (record.depth - centerDepth) / height;
    record.sideOffset = side => side * (record.lateral - centerLateral);
  });
  return {joints, records, bodyFrame, topology, semantic};
}

function recordView(record) {
  if (!record) return null;
  return {
    jointId: record.jointId,
    signature: record.signature,
    center: record.center.toArray(),
    pivot: record.pivot.toArray(),
    height: record.height,
    height01: record.height01,
    lateral: record.lateral,
    lateral01: record.lateral01,
    depth: record.depth,
    depth01: record.depth01,
    affectedVertexCount: record.affectedVertexCount,
    totalWeight: record.totalWeight,
    componentId: record.componentId,
    depthIndex: record.depthIndex,
  };
}

function distanceToSegment(point, start, end) {
  const segment = end.clone().sub(start);
  const lengthSq = segment.lengthSq();
  const t = lengthSq <= EPSILON
    ? 0 : clamp(point.clone().sub(start).dot(segment) / lengthSq);
  return point.distanceTo(start.clone().addScaledVector(segment, t));
}

function directionGroups(palm, nearby, height, side, semantic) {
  const groups = [];
  nearby.forEach(record => {
    const radial = record.center.clone().sub(palm.center);
    const distance = radial.length();
    if (distance < height * MIN_FINGER_LENGTH_RATIO) return;
    // The arm/wrist ray points back toward the torso. Excluding strongly
    // inward rays prevents it from being counted as a sixth finger while
    // still allowing a slightly inward-curved finger.
    if (side * radial.dot(semantic.right) < -height * .04) return;
    const direction = radial.normalize();
    let group = groups.find(item => item.direction.dot(direction)
      >= FINGER_DIRECTION_COSINE);
    if (!group) {
      group = {direction, records: [], length: 0};
      groups.push(group);
    }
    group.records.push({record, distance});
    group.length = Math.max(group.length, distance);
  });
  groups.sort((left, right) => right.length - left.length
    || right.records.length - left.records.length
    || left.records[0].record.jointId - right.records[0].record.jointId);
  return groups.slice(0, 6).map(group => ({
    jointIds: group.records.sort((left, right) => left.distance - right.distance
      || left.record.jointId - right.record.jointId)
      .map(item => item.record.jointId),
    fingertipJointId: group.records[group.records.length - 1].record.jointId,
    length: group.length,
    direction: group.direction.toArray(),
  }));
}

function wristFor(data, palm, side, excluded, height) {
  const candidates = data.records.filter(record => {
    if (excluded.has(record.jointId) || record.jointId === palm.jointId) return false;
    const inward = side * (palm.lateral - record.lateral);
    return inward >= -height * .02
      && inward <= height * .28
      && record.center.distanceTo(palm.center) <= height * .3;
  });
  const parent = candidates.find(record => record.jointId === palm.parentId);
  return parent || candidates.sort((left, right) => {
    const leftDistance = left.center.distanceTo(palm.center);
    const rightDistance = right.center.distanceTo(palm.center);
    return leftDistance - rightDistance || left.jointId - right.jointId;
  })[0] || null;
}

function handCandidate(data, palm, side) {
  const {bodyFrame, records} = data;
  const sideOffset = palm.sideOffset(side);
  if (sideOffset < bodyFrame.height * MIN_HAND_SIDE_RATIO
      || palm.height01 < MIN_HAND_HEIGHT || palm.height01 > MAX_HAND_HEIGHT) {
    return null;
  }
  const radius = bodyFrame.height * HAND_RADIUS_RATIO;
  const nearby = records.filter(record => record.jointId !== palm.jointId
    && record.center.distanceTo(palm.center) <= radius);
  const fingerBranches = directionGroups(
    palm, nearby, bodyFrame.height, side, data.semantic);
  if (fingerBranches.length < MIN_FINGER_RAYS) return null;
  const fingerCount = fingerBranches.length;
  const fingerScore = fingerCount === 5 ? 1
    : fingerCount === 4 ? .84 : fingerCount === 6 ? .9
      : clamp((fingerCount - 2) / 3) * .55;
  const maxRadius = fingerBranches.reduce((maximum, branch) =>
    Math.max(maximum, branch.length), 0);
  const compactnessScore = clamp(1 - Math.max(0,
    maxRadius / bodyFrame.height - .12) / .16);
  const lateralScore = clamp(sideOffset / (bodyFrame.height * .65));
  const heightScore = 1 - clamp(Math.abs(palm.height01 - .45) / .45);
  const excluded = new Set([palm.jointId,
    ...fingerBranches.flatMap(branch => branch.jointIds)]);
  const wrist = wristFor(data, palm, side, excluded, bodyFrame.height);
  const fingertipCandidates = fingerBranches.map(branch => branch.fingertipJointId);
  const handLength = median(fingerBranches.map(branch => branch.length), 0);
  const branchingScore = palm.childrenIds.length
    ? clamp(palm.childrenIds.length / 4) : .5;
  const confidence = clamp(fingerScore * .4 + compactnessScore * .12
    + lateralScore * .08 + heightScore * .06 + (wrist ? .04 : 0)
    + branchingScore * .3);
  return {
    palm: recordView(palm),
    palmRecord: palm,
    wrist: recordView(wrist),
    wristRecord: wrist,
    fingerBranches,
    fingertipCandidates,
    fingerCount,
    clusterRadius: maxRadius,
    handLength,
    side,
    sideOffset,
    confidence,
    evidence: {
      fingerBranchCount: fingerCount,
      fingerScore,
      compactnessScore,
      lateralScore,
      heightScore,
      branchingScore,
      wristFound: !!wrist,
    },
  };
}

function handPair(data, negativeHands, positiveHands) {
  const pairs = [];
  negativeHands.forEach(negative => positiveHands.forEach(positive => {
    const height = 1 - clamp(Math.abs(negative.palm.height
      - positive.palm.height) / (data.bodyFrame.height * .2));
    const lateral = 1 - clamp(Math.abs(negative.sideOffset
      - positive.sideOffset) / (data.bodyFrame.height * .35));
    const depth = 1 - clamp(Math.abs(negative.palm.depth
      - positive.palm.depth) / (data.bodyFrame.height * .2));
    const radius = 1 - clamp(Math.abs(negative.clusterRadius
      - positive.clusterRadius) / (data.bodyFrame.height * .1));
    const fingers = 1 - clamp(Math.abs(negative.fingerCount
      - positive.fingerCount) / 3);
    const length = 1 - clamp(Math.abs(negative.handLength
      - positive.handLength) / (data.bodyFrame.height * .2));
    const plane = (1 - clamp(Math.abs(negative.palm.depth
      - data.bodyFrame.centerDepth) / (data.bodyFrame.height * .18))
      + 1 - clamp(Math.abs(positive.palm.depth
        - data.bodyFrame.centerDepth) / (data.bodyFrame.height * .18))) / 2;
    const branching = (negative.evidence.branchingScore
      + positive.evidence.branchingScore) / 2;
    const symmetry = height * .16 + lateral * .14 + depth * .12
      + radius * .1 + fingers * .1 + length * .1 + plane * .18
      + branching * .1;
    const planeWeight = .45 + .55 * plane;
    const confidence = clamp(symmetry * planeWeight * .7
      + (negative.confidence + positive.confidence) * .15);
    pairs.push({negative, positive, score: symmetry, confidence,
      evidence: {height, lateral, depth, radius, fingers, length, plane,
        branching}});
  }));
  return pairs.sort((left, right) => right.score - left.score
    || left.negative.palm.jointId - right.negative.palm.jointId
    || left.positive.palm.jointId - right.positive.palm.jointId)[0] || null;
}

function shoulderFor(data, hand) {
  const side = hand.side;
  const palm = hand.palmRecord;
  const excluded = new Set([palm.jointId,
    ...hand.fingerBranches.flatMap(branch => branch.jointIds)]);
  if (hand.wristRecord) excluded.add(hand.wristRecord.jointId);
  const torsoTarget = data.semantic.up.clone().multiplyScalar(
    palm.height + data.bodyFrame.height * .24)
    .addScaledVector(data.semantic.right, data.bodyFrame.centerLateral)
    .addScaledVector(data.semantic.forward, data.bodyFrame.centerDepth);
  const candidates = data.records.filter(record => {
    if (excluded.has(record.jointId)) return false;
    const sideOffset = record.sideOffset(side);
    return sideOffset >= data.bodyFrame.height * .08
      && sideOffset <= hand.sideOffset * .9
      && record.height01 >= .38 && record.height01 <= .88;
  }).map(record => {
    const sideOffset = record.sideOffset(side);
    const corridor = 1 - clamp(distanceToSegment(
      record.center, palm.center, torsoTarget) / (data.bodyFrame.height * .2));
    const inward = 1 - clamp(Math.abs(
      sideOffset - hand.sideOffset * .45) / (data.bodyFrame.height * .3));
    const height = 1 - clamp(Math.abs(record.height01 - .66) / .28);
    const support = clamp(Math.log1p(record.support) / 8);
    const score = corridor * .34 + inward * .32 + height * .24 + support * .1;
    return {record, score, sideOffset, corridor, inward, height};
  }).sort((left, right) => right.score - left.score
    || left.sideOffset - right.sideOffset
    || left.record.jointId - right.record.jointId);
  const selected = candidates[0];
  if (!selected || selected.score < MIN_ARM_CONFIDENCE) return null;
  return {
    ...recordView(selected.record),
    record: selected.record,
    confidence: clamp(selected.score),
    evidence: {
      corridorScore: selected.corridor,
      inwardScore: selected.inward,
      heightScore: selected.height,
    },
  };
}

function elbowFor(data, shoulder, hand) {
  if (!shoulder || !hand?.palmRecord) return null;
  const start = shoulder.record.center;
  const end = hand.palmRecord.center;
  const direction = end.clone().sub(start);
  const lengthSq = direction.lengthSq();
  if (lengthSq <= EPSILON) return null;
  const excluded = new Set([shoulder.jointId, hand.palmRecord.jointId,
    ...hand.fingerBranches.flatMap(branch => branch.jointIds)]);
  const candidates = data.records.filter(record => !excluded.has(record.jointId))
    .map(record => {
      const progress = clamp(record.center.clone().sub(start).dot(direction)
        / lengthSq);
      const corridor = 1 - clamp(distanceToSegment(
        record.center, start, end) / (data.bodyFrame.height * .16));
      const middle = 1 - clamp(Math.abs(progress - .5) / .4);
      return {record, progress, score: corridor * .65 + middle * .35};
    }).filter(item => item.progress > .12 && item.progress < .9
      && item.score >= .5)
    .sort((left, right) => right.score - left.score
      || Math.abs(left.progress - .5) - Math.abs(right.progress - .5)
      || left.record.jointId - right.record.jointId);
  const selected = candidates[0];
  return selected ? {
    ...recordView(selected.record),
    confidence: clamp(selected.score),
    progress: selected.progress,
  } : null;
}

function pathBetween(data, startId, endIds) {
  const targets = new Set(endIds.filter(Number.isInteger));
  if (!Number.isInteger(startId) || !targets.size) {
    return {connected: false, pathIds: [], components: [], gaps: ['missing_landmark']};
  }
  const queue = [startId];
  const previous = new Map([[startId, null]]);
  while (queue.length) {
    const current = queue.shift();
    if (targets.has(current)) {
      const pathIds = [];
      let cursor = current;
      while (cursor !== null) {
        pathIds.push(cursor);
        cursor = previous.get(cursor);
      }
      pathIds.reverse();
      return {connected: true, pathIds, components: [], gaps: []};
    }
    for (const neighbor of data.topology.adjacency.get(current) || []) {
      if (previous.has(neighbor)) continue;
      previous.set(neighbor, current);
      queue.push(neighbor);
    }
  }
  const start = data.records.find(record => record.jointId === startId);
  const targetsFound = data.records.filter(record => targets.has(record.jointId));
  return {
    connected: false,
    pathIds: [],
    components: [...new Set([start?.componentId,
      ...targetsFound.map(record => record.componentId)])]
      .filter(value => value !== null && value !== undefined),
    gaps: ['no_rest_graph_path'],
  };
}

function armLandmark(data, hand, shoulder) {
  if (!hand || !shoulder) return null;
  const elbow = elbowFor(data, shoulder, hand);
  const wrist = hand.wrist || null;
  const connectivity = pathBetween(data, shoulder.jointId,
    [hand.palm?.jointId].filter(Number.isInteger));
  const wristConnectivity = wrist
    ? pathBetween(data, shoulder.jointId, [wrist.jointId]) : connectivity;
  connectivity.connected = connectivity.connected && wristConnectivity.connected;
  connectivity.gaps = [...new Set([
    ...connectivity.gaps, ...wristConnectivity.gaps,
  ])];
  connectivity.components = [...new Set([
    ...connectivity.components, ...wristConnectivity.components,
  ])];
  const direction = hand.palmRecord.center.clone().sub(shoulder.record.pivot);
  if (direction.lengthSq() <= EPSILON) return null;
  direction.normalize();
  // Connectivity describes whether the inferred deformation graph can carry
  // the pose; it must not lower spatial landmark confidence or hide a useful
  // semantic result when the graph has a gap.
  const confidence = clamp((hand.confidence + shoulder.confidence) * .5);
  return {
    shoulder: recordView(shoulder.record),
    elbow,
    wrist,
    hand: hand.palm,
    fingers: hand.fingerBranches,
    restDirection: direction.toArray(),
    confidence,
    evidence: {
      handConfidence: hand.confidence,
      shoulderConfidence: shoulder.confidence,
      fingerBranchCount: hand.fingerCount,
      connectivity,
    },
    poseConnectivity: connectivity,
  };
}

function footPair(data) {
  const candidates = data.records.filter(record => record.height01 <= .2
    && Math.abs(record.lateral01) >= .08)
    .sort((left, right) => right.support - left.support
      || left.height - right.height || left.jointId - right.jointId);
  const negative = candidates.find(record => record.lateral01 < 0) || null;
  const positive = candidates.find(record => record.lateral01 > 0) || null;
  if (!negative || !positive) return {negative: null, positive: null};
  const confidence = clamp(.55
    + Math.min(Math.log1p(negative.support), Math.log1p(positive.support)) / 16);
  return {
    negative: {...recordView(negative), confidence,
      evidence: {support: negative.support, paired: true}},
    positive: {...recordView(positive), confidence,
      evidence: {support: positive.support, paired: true}},
  };
}

function coarseBodyLandmarks(data) {
  const head = [...data.records].sort((left, right) => right.height - left.height
    || Math.abs(left.lateral01) - Math.abs(right.lateral01)
    || left.jointId - right.jointId)[0] || null;
  const pelvisCandidates = data.records.filter(record => record.height01 >= .3
    && record.height01 <= .55 && Math.abs(record.lateral01) < .18)
    .sort((left, right) => right.support - left.support
      || Math.abs(left.height01 - .42) - Math.abs(right.height01 - .42)
      || left.jointId - right.jointId);
  return {
    head: head ? {...recordView(head), confidence: clamp(.55
      + Math.log1p(head.support) / 16), evidence: {support: head.support}} : null,
    pelvis: pelvisCandidates[0] ? {...recordView(pelvisCandidates[0]),
      confidence: clamp(.5 + Math.log1p(pelvisCandidates[0].support) / 16),
      evidence: {support: pelvisCandidates[0].support}} : null,
  };
}

function handSummary(hand) {
  if (!hand) return null;
  return {
    jointId: hand.palm?.jointId ?? null,
    wristJointId: hand.wrist?.jointId ?? null,
    palm: hand.palm,
    wrist: hand.wrist,
    fingerBranches: hand.fingerBranches.map(branch => ({...branch,
      jointIds: [...branch.jointIds]})),
    fingerCount: hand.fingerCount,
    fingertipCandidates: [...hand.fingertipCandidates],
    clusterRadius: hand.clusterRadius,
    handLength: hand.handLength,
    sideOffset: hand.sideOffset,
    confidence: hand.confidence,
    evidence: {...hand.evidence},
  };
}

function handCandidateSummary(candidate) {
  if (!candidate) return null;
  return {
    jointId: candidate.palm.jointId,
    confidence: candidate.confidence,
    fingerCount: candidate.fingerCount,
    clusterRadius: candidate.clusterRadius,
    handLength: candidate.handLength,
    evidence: {...candidate.evidence},
  };
}

/**
 * Identify spatial semantic landmarks from the Rig's default/rest geometry.
 * This module deliberately does not mutate the inferred deformation graph.
 */
function analyzeRigSemanticsUncached(modelRig, options = {}) {
  const semantic = normalizeSemanticFrame(options?.semanticFrame);
  const data = semanticData(modelRig, semantic);
  const frame = serializeSemanticFrame(semantic);
  if (data.records.length < 4) {
    return {
      available: false,
      confidence: 0,
      semanticFrame: frame,
      bodyFrame: data.bodyFrame,
      landmarks: {},
      issues: ['insufficient_rig'],
      diagnostics: {semanticFrame: frame, bodyFrame: data.bodyFrame},
    };
  }
  const negativeHands = data.records.map(record =>
    handCandidate(data, record, -1)).filter(Boolean)
    .sort((left, right) => right.confidence - left.confidence
      || left.palm.jointId - right.palm.jointId);
  const positiveHands = data.records.map(record =>
    handCandidate(data, record, 1)).filter(Boolean)
    .sort((left, right) => right.confidence - left.confidence
      || left.palm.jointId - right.palm.jointId);
  const pair = handPair(data, negativeHands, positiveHands);
  // Keep the best side-specific candidates even without a bilateral pair so
  // diagnostics can expose useful partial semantic results.
  const negativeHand = pair?.negative || negativeHands[0] || null;
  const positiveHand = pair?.positive || positiveHands[0] || null;
  const negativeShoulder = negativeHand
    ? shoulderFor(data, negativeHand) : null;
  const positiveShoulder = positiveHand
    ? shoulderFor(data, positiveHand) : null;
  const negativeArm = armLandmark(data, negativeHand, negativeShoulder);
  const positiveArm = armLandmark(data, positiveHand, positiveShoulder);
  const issues = [];
  if (!negativeHand) issues.push('negative_hand_not_found');
  if (!positiveHand) issues.push('positive_hand_not_found');
  if (!pair) issues.push('hand_pair_not_found');
  if (negativeHand && !negativeShoulder) issues.push('negative_shoulder_not_found');
  if (positiveHand && !positiveShoulder) issues.push('positive_shoulder_not_found');
  if (negativeArm && !negativeArm.poseConnectivity.connected) {
    issues.push('negative_arm_pose_disconnected');
  }
  if (positiveArm && !positiveArm.poseConnectivity.connected) {
    issues.push('positive_arm_pose_disconnected');
  }
  const armConfidence = negativeArm && positiveArm
    ? Math.min(negativeArm.confidence, positiveArm.confidence) : 0;
  const confidence = pair && negativeArm && positiveArm
    ? clamp(Math.min(pair.confidence, armConfidence)) : 0;
  const landmarks = {
    negativeHand: handSummary(negativeHand),
    positiveHand: handSummary(positiveHand),
    negativeArm,
    positiveArm,
    negativeFoot: footPair(data).negative,
    positiveFoot: footPair(data).positive,
    ...coarseBodyLandmarks(data),
  };
  const available = !!pair && !!negativeArm && !!positiveArm
    && negativeHand.confidence >= MIN_HAND_CONFIDENCE
    && positiveHand.confidence >= MIN_HAND_CONFIDENCE
    && negativeArm.confidence >= MIN_ARM_CONFIDENCE
    && positiveArm.confidence >= MIN_ARM_CONFIDENCE;
  const diagnostics = {
    semanticFrame: frame,
    bodyFrame: data.bodyFrame,
    handCandidateCounts: {
      negativeLateral: negativeHands.length,
      positiveLateral: positiveHands.length,
    },
    hands: {
      negative: handSummary(negativeHand),
      positive: handSummary(positiveHand),
    },
    handCandidates: {
      negative: negativeHands.slice(0, 8).map(handCandidateSummary),
      positive: positiveHands.slice(0, 8).map(handCandidateSummary),
    },
    armConfidence: {
      negative: negativeArm?.confidence ?? 0,
      positive: positiveArm?.confidence ?? 0,
    },
    poseConnectivity: {
      negativeArm: negativeArm?.poseConnectivity || null,
      positiveArm: positiveArm?.poseConnectivity || null,
    },
    issues: [...issues],
  };
  if (pair) diagnostics.handPair = {
    negativeJointId: pair.negative.palm.jointId,
    positiveJointId: pair.positive.palm.jointId,
    confidence: pair.confidence,
    score: pair.score,
    evidence: {...pair.evidence},
  };
  return {
    available,
    confidence,
    semanticFrame: frame,
    bodyFrame: data.bodyFrame,
    landmarks,
    issues,
    diagnostics,
  };
}

function semanticCacheKey(modelRig, semantic) {
  const revision = Number(modelRig?.structureRevision);
  const identity = modelRig?.key || modelRig?.sourceKey;
  if (!identity || !Number.isFinite(revision)) return null;
  const frame = serializeSemanticFrame(semantic);
  return `${identity}:${revision}:${frame.up.join(',')}`
    + `:${frame.right.join(',')}:${frame.forward.join(',')}`;
}

/** Analyze once per rest-Rig revision and semantic frame. */
export function analyzeRigSemantics(modelRig, options = {}) {
  const semantic = normalizeSemanticFrame(options?.semanticFrame);
  const key = semanticCacheKey(modelRig, semantic);
  if (key && semanticCache.has(key)) return semanticCache.get(key);
  const result = analyzeRigSemanticsUncached(modelRig, options);
  if (key) {
    semanticCache.set(key, result);
    // Keep the cache bounded across model reloads while preserving the most
    // recent revisions used by the current viewer session.
    while (semanticCache.size > 8) {
      semanticCache.delete(semanticCache.keys().next().value);
    }
  }
  return result;
}
