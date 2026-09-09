/* Geometry-only humanoid limb suggestions for the inferred Model Rig. */

import * as THREE from 'three';
import {
  characterAxesFromOrientation, resolveLimbBendDirection,
  selectLimbBendJoint, rigPathBetweenJointIds,
} from './weight-rig-ik.js';

const EPSILON = 1e-8;
const ROLES = Object.freeze(['left_arm', 'right_arm', 'left_leg', 'right_leg']);
const ARM_ROLES = Object.freeze(['left_arm', 'right_arm']);
const LEG_ROLES = Object.freeze(['left_leg', 'right_leg']);

function numberId(value) {
  const id = Number(value);
  return Number.isInteger(id) ? id : null;
}

function valueFor(collection, id) {
  if (collection instanceof Map) return collection.get(id) ?? collection.get(String(id));
  return collection?.[id] ?? collection?.[String(id)];
}

function vectorFor(value) {
  if (value?.isVector3) return value.clone();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 3).map(Number)
    : [value?.x, value?.y, value?.z].map(Number);
  return values.length === 3 && values.every(Number.isFinite)
    ? new THREE.Vector3(...values) : null;
}

function semanticAxesFromForward(characterForward) {
  const up = new THREE.Vector3(0, 1, 0);
  const forward = vectorFor(characterForward) || new THREE.Vector3(0, 0, 1);
  forward.addScaledVector(up, -forward.dot(up));
  if (forward.lengthSq() <= EPSILON) forward.set(0, 0, 1);
  forward.normalize();
  const right = up.clone().cross(forward);
  if (right.lengthSq() <= EPSILON) right.set(1, 0, 0);
  right.normalize();
  return {up, forward, right};
}

function semanticAxesFor({axes, characterForward} = {}) {
  const up = vectorFor(axes?.up);
  const forward = vectorFor(axes?.forward);
  if (!up || !forward || up.lengthSq() <= EPSILON || forward.lengthSq() <= EPSILON) {
    return semanticAxesFromForward(characterForward);
  }
  up.normalize();
  forward.addScaledVector(up, -forward.dot(up));
  if (forward.lengthSq() <= EPSILON) return semanticAxesFromForward(characterForward);
  forward.normalize();
  const right = up.clone().cross(forward);
  if (right.lengthSq() <= EPSILON) return semanticAxesFromForward(characterForward);
  right.normalize();
  return {up, forward, right};
}

function jointFor(rig, id) {
  return (rig?.joints || []).find(joint => Number(joint?.jointId) === Number(id)) || null;
}

function vectorValue(value) {
  if (value?.isVector3) return value.clone();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 3).map(Number)
    : [value?.x, value?.y, value?.z].map(Number);
  return values.length === 3 && values.every(Number.isFinite)
    ? new THREE.Vector3(...values) : null;
}

function pivotFor(rig, id) {
  const joint = jointFor(rig, id);
  return vectorValue(valueFor(rig?.jointPivotByJointId, id))
    || vectorValue(joint?.restPivot);
}

function influenceCenterFor(rig, id) {
  const joint = jointFor(rig, id);
  return vectorValue(valueFor(rig?.centerByJointId, id))
    || vectorValue(joint?.restCenter);
}

function pointFor(rig, id) {
  return pivotFor(rig, id) || influenceCenterFor(rig, id);
}

function componentFor(rig, id) {
  const componentId = valueFor(rig?.componentByJointId, id);
  const components = rig?.components || rig?.inferredForest?.components || [];
  return Number.isInteger(Number(componentId)) ? components[Number(componentId)] || null : null;
}

function parentFor(component, id) {
  const value = valueFor(component?.parentById, id);
  return value === null || value === undefined ? null : numberId(value);
}

function weightedValue(joint) {
  const evidence = joint?.evidence || {};
  const count = Number(evidence.affectedVertexCount ?? joint?.affectedVertexCount);
  const weight = Number(evidence.totalWeight ?? joint?.totalWeight);
  if (Number.isFinite(count) && count > 0) return Math.min(count, 100000);
  if (Number.isFinite(weight) && weight > 0) return Math.min(weight * 100, 1000);
  return 1;
}

function weightedMedian(samples, selector) {
  if (!samples.length) return 0;
  const ordered = [...samples].sort((left, right) =>
    selector(left) - selector(right));
  const total = ordered.reduce((sum, sample) => sum + sample.weight, 0);
  let accumulated = 0;
  for (const sample of ordered) {
    accumulated += sample.weight;
    if (accumulated >= total / 2) return selector(sample);
  }
  return selector(ordered.at(-1));
}

function weightedQuantile(samples, selector, fraction) {
  if (!samples.length) return 0;
  const ordered = [...samples].sort((left, right) =>
    selector(left) - selector(right));
  const total = ordered.reduce((sum, sample) => sum + sample.weight, 0);
  const target = total * Math.max(0, Math.min(1, fraction));
  let accumulated = 0;
  for (const sample of ordered) {
    accumulated += sample.weight;
    if (accumulated >= target) return selector(sample);
  }
  return selector(ordered.at(-1));
}

function robustFrame(rig, axes) {
  const {up, forward, right} = axes;
  const samples = (rig?.joints || []).map(joint => {
    const point = pointFor(rig, joint?.jointId);
    const evidence = weightedValue(joint);
    return point ? {
      point,
      weight: 1 + Math.log1p(evidence),
      sideCoordinate: point.dot(right),
      heightCoordinate: point.dot(up),
      depthCoordinate: point.dot(forward),
    } : null;
  }).filter(Boolean);
  const points = samples.map(sample => sample.point);
  if (!points.length) {
    return {
      center: new THREE.Vector3(),
      up,
      forward,
      right,
      lowHeight: -.5,
      highHeight: .5,
      height: 1,
      centerSide: 0,
      centerDepth: 0,
      bodyDepth: 0,
      bodyDepthSpread: 0,
    };
  }
  const lowHeight = weightedQuantile(samples, sample => sample.heightCoordinate, .05);
  const highHeight = weightedQuantile(samples, sample => sample.heightCoordinate, .95);
  let height = highHeight - lowHeight;
  if (!Number.isFinite(height) || height <= EPSILON) {
    const heightCoordinates = samples.map(sample => sample.heightCoordinate);
    height = Math.max(...heightCoordinates) - Math.min(...heightCoordinates);
  }
  if (!Number.isFinite(height) || height <= EPSILON) {
    const box = new THREE.Box3().setFromPoints(points);
    height = box.getSize(new THREE.Vector3()).length();
  }
  height = Math.max(height, EPSILON);
  const medianDepth = weightedMedian(samples, sample => sample.depthCoordinate);
  const depthSpread = weightedQuantile(samples, sample => sample.depthCoordinate, .75)
    - weightedQuantile(samples, sample => sample.depthCoordinate, .25);
  const depthThreshold = Math.max(depthSpread * 1.5, height * .08, EPSILON);
  const coreSamples = samples.filter(sample =>
    Math.abs(sample.depthCoordinate - medianDepth) <= depthThreshold);
  const centerSide = weightedMedian(coreSamples, sample => sample.sideCoordinate);
  const centerHeight = weightedMedian(samples, sample => sample.heightCoordinate);
  const centerDepth = medianDepth;
  const center = right.clone().multiplyScalar(centerSide)
    .addScaledVector(up, centerHeight)
    .addScaledVector(forward, medianDepth);
  return {
    center, up, forward, right, height,
    lowHeight: Number.isFinite(lowHeight) ? lowHeight : centerHeight - height / 2,
    highHeight: Number.isFinite(highHeight) ? highHeight : centerHeight + height / 2,
    centerSide, centerDepth, bodyDepth: centerDepth,
    bodyDepthSpread: Math.max(0, depthSpread),
  };
}

/** Build one model-wide semantic frame for every humanoid detector consumer. */
export function buildHumanoidSemanticFrame({rig, axes, characterForward} = {}) {
  const semanticAxes = semanticAxesFor({axes, characterForward});
  return robustFrame(rig, semanticAxes);
}

/** Collect pivot-first samples used by the geometry fitting stage. */
export function collectHumanoidSamples({rig, frame} = {}) {
  const semanticFrame = frame || buildHumanoidSemanticFrame({rig});
  return (rig?.joints || []).map(joint => {
    const jointId = numberId(joint?.jointId);
    const pivotPosition = jointId === null ? null : pivotFor(rig, jointId);
    const influenceCenter = jointId === null ? null : influenceCenterFor(rig, jointId);
    const point = pivotPosition || influenceCenter;
    if (jointId === null || !point) return null;
    const absoluteHeight = point.dot(semanticFrame.up);
    return {
      jointId,
      pivotPosition: pivotPosition || point.clone(),
      influenceCenter: influenceCenter || null,
      point: point.clone(),
      side: (point.dot(semanticFrame.right) - semanticFrame.centerSide)
        / Math.max(semanticFrame.height, EPSILON),
      height: absoluteHeight,
      height01: clamp01((absoluteHeight - semanticFrame.lowHeight)
        / Math.max(semanticFrame.height, EPSILON)),
      depth: (point.dot(semanticFrame.forward)
        - (semanticFrame.bodyDepth ?? semanticFrame.centerDepth ?? 0))
        / Math.max(semanticFrame.height, EPSILON),
      bodyDepth: point.dot(semanticFrame.forward),
      support: weightedValue(joint),
      totalWeight: Number(joint?.evidence?.totalWeight ?? joint?.totalWeight) || 0,
      affectedVertexCount: Number(joint?.evidence?.affectedVertexCount
        ?? joint?.affectedVertexCount) || 0,
      componentId: valueFor(rig?.componentByJointId, jointId),
      weight: 1 + Math.log1p(weightedValue(joint)),
    };
  }).filter(Boolean).sort((left, right) => left.jointId - right.jointId);
}

function scaffoldPoint(height01, side, depth) {
  return {height01, side, depth};
}

function interpolateCenterline(centerline, height01) {
  const points = [centerline.lower, centerline.mid, centerline.upper];
  const value = clamp01(height01);
  if (value <= points[0].height01) return points[0];
  if (value >= points[2].height01) return points[2];
  const first = value < points[1].height01 ? points[0] : points[1];
  const second = value < points[1].height01 ? points[1] : points[2];
  const fraction = (value - first.height01)
    / Math.max(second.height01 - first.height01, EPSILON);
  return scaffoldPoint(value,
    first.side + (second.side - first.side) * fraction,
    first.depth + (second.depth - first.depth) * fraction);
}

/**
 * Infer broad body regions from the authored pose. This is scoring context,
 * not a replacement hierarchy or a semantic skeleton.
 */
export function buildHumanoidScaffold({rig, frame} = {}) {
  const semanticFrame = frame || buildHumanoidSemanticFrame({rig});
  const samples = collectHumanoidSamples({rig, frame: semanticFrame});
  const centralSamples = samples.filter(sample => Math.abs(sample.depth) <= .28);
  const source = centralSamples.length ? centralSamples : samples;
  const band = (min, max, fallbackHeight) => {
    const values = source.filter(sample => sample.height01 >= min
      && sample.height01 <= max);
    if (!values.length) return scaffoldPoint(fallbackHeight, 0, 0);
    return scaffoldPoint(weightedMedian(values, sample => sample.height01),
      weightedMedian(values, sample => sample.side),
      weightedMedian(values, sample => sample.depth));
  };
  const centerline = {
    lower: band(0, .38, .2),
    mid: band(.28, .72, .5),
    upper: band(.62, 1, .8),
  };
  const virtualChest = band(.56, .9, .68);
  const virtualPelvis = band(.34, .7, .45);
  const lateral = source.filter(sample => Math.abs(sample.side) >= .08);
  const shoulderSamples = lateral.filter(sample => sample.height01 >= .5);
  const hipSamples = source.filter(sample => sample.height01 <= .76
    && Math.abs(sample.side) <= .3);
  const shoulderExpected = shoulderSamples.length
    ? clamp01(weightedQuantile(shoulderSamples, sample => sample.height01, .58))
    : .72;
  const hipSampleExpected = hipSamples.length
    ? clamp01(weightedQuantile(hipSamples, sample => sample.height01, .75))
    : lateral.length
      ? clamp01(weightedQuantile(lateral.filter(sample => sample.height01 <= .76),
        sample => sample.height01, .62))
      : .35;
  // The lower central band is a safer pelvis cue than a lateral endpoint
  // quantile: legs and accessory strands contribute many low points.
  const hipExpected = Math.max(hipSampleExpected,
    clamp01(centerline.mid.height01 * .9));
  const makeBand = (expected, width, min, max) => ({
    minHeight01: clamp01(Math.max(min, expected - width)),
    maxHeight01: clamp01(Math.min(max, expected + width)),
    expectedHeight01: clamp01(expected),
  });
  const shoulderBand = makeBand(shoulderExpected, .2, .38, .98);
  const hipBand = makeBand(hipExpected, .22, .08, .72);
  const depthValues = source.map(sample => sample.depth);
  const depthSpread = depthValues.length
    ? weightedQuantile(source, sample => sample.depth, .75)
      - weightedQuantile(source, sample => sample.depth, .25) : 0;
  const centerlineAt = height01 => interpolateCenterline(centerline, height01);
  const confidence = clamp01(.3 * (source.length >= 4 ? 1 : source.length / 4)
    + .35 * (lateral.length >= 4 ? 1 : lateral.length / 4)
    + .35 * (semanticFrame.height > EPSILON ? 1 : 0));
  return {
    centerline,
    virtualChest,
    virtualPelvis,
    shoulderBand,
    hipBand,
    armRegion: {
      minHeight01: shoulderBand.minHeight01,
      maxHeight01: 1,
      expectedHeight01: shoulderBand.expectedHeight01,
    },
    legRegion: {
      minHeight01: 0,
      maxHeight01: hipBand.maxHeight01,
      expectedHeight01: hipBand.expectedHeight01,
    },
    bodyDepth: {
      expected: semanticFrame.bodyDepth ?? semanticFrame.centerDepth,
      spread: depthSpread,
      tolerance: Math.max(.18, depthSpread * 1.5),
    },
    centerlineAt,
    confidence,
  };
}

function clamp01(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}

function rangeScore(value, low, high, softness = .15) {
  if (value >= low && value <= high) return 1;
  const distance = value < low ? low - value : value - high;
  return clamp01(1 - distance / Math.max(softness, EPSILON));
}

function emptySuggestion(role, reason = 'no_bilateral_pair') {
  return {
    role, available: false, anchorJointId: null, pathJointIds: [],
    bendJointId: null, endJointId: null, hipJointId: null, kneeJointId: null,
    footJointId: null, score: 0, totalScore: 0, confidence: 'low',
    hipScore: 0, kneeScore: 0, footScore: 0, pathScore: 0,
    topologyScore: 0,
    pairScore: 0, runnerUpMargin: 0, reasons: [reason],
  };
}

function ancestorIds(component, id) {
  const result = [];
  const seen = new Set();
  let current = parentFor(component, id);
  while (current !== null && !seen.has(current)) {
    result.push(current);
    seen.add(current);
    current = parentFor(component, current);
  }
  return result;
}

function componentBodyScore(rig, frame, component) {
  const samples = (component?.nodeIds || []).map(id => pointFor(rig, id))
    .filter(Boolean).map(point => ({
      side: (point.dot(frame.right) - frame.centerSide) / frame.height,
      height01: clamp01((point.dot(frame.up) - frame.lowHeight) / frame.height),
      depth: (point.dot(frame.forward) - frame.centerDepth) / frame.height,
      weight: 1,
    }));
  if (!samples.length) return 0;
  const central = samples.filter(sample => Math.abs(sample.side) < .48
    && Math.abs(sample.depth) < .35).length / samples.length;
  const span = Math.max(...samples.map(sample => sample.height01))
    - Math.min(...samples.map(sample => sample.height01));
  const depth = 1 - clamp01(Math.abs(weightedMedian(samples,
    sample => sample.depth)) / .75);
  const side = 1 - clamp01(Math.abs(weightedMedian(samples,
    sample => sample.side)) / .65);
  return clamp01(.42 * central + .22 * clamp01(span / .65)
    + .2 * depth + .16 * side);
}

function ancestryScore(rig, frame, role, {anchorJointId, anchorPoint, parentPoint, component}) {
  const ids = ancestorIds(component, numberId(anchorJointId));
  const anchor = anchorPoint;
  const parent = parentPoint;
  const anchorSide = Math.abs(anchor.clone().sub(frame.center).dot(frame.right)
    / frame.height);
  const anchorDepth = Math.abs(anchor.clone().sub(frame.center).dot(frame.forward)
    / frame.height);
  const samples = [parent, ...ids.slice(1).map(id => pointFor(rig, id))]
    .filter(Boolean).map(point => ({
    side: Math.abs(point.clone().sub(frame.center).dot(frame.right) / frame.height),
    depth: Math.abs(point.clone().sub(frame.center).dot(frame.forward) / frame.height),
  }));
  if (!samples.length) return {score: 0, ids};
  const centrality = samples.reduce((sum, sample) => sum
    + .55 * (1 - clamp01(sample.side / Math.max(anchorSide, .12)))
    + .45 * (1 - clamp01(sample.depth / Math.max(anchorDepth, .25))), 0)
    / samples.length;
  const first = samples[0];
  const convergence = .55 * (1 - clamp01(first.side / Math.max(anchorSide, .12)))
    + .45 * (1 - clamp01(first.depth / Math.max(anchorDepth, .25)));
  const direction = first.side <= anchorSide + .08 ? 1 : 0;
  return {score: clamp01(.55 * centrality + .35 * convergence + .1 * direction), ids};
}

function childrenForComponent(component, id) {
  const listed = valueFor(component?.childrenById, id);
  if (Array.isArray(listed)) return listed.map(numberId).filter(Number.isInteger);
  return (component?.nodeIds || []).map(numberId).filter(childId =>
    Number.isInteger(childId) && parentFor(component, childId) === numberId(id));
}

function sampleIndex(samples) {
  return new Map(samples.map(sample => [sample.jointId, sample]));
}

function absoluteDepthScore(sample, frame, tolerance = .3) {
  if (!sample) return 0;
  const offset = Math.abs((sample.bodyDepth - (frame.bodyDepth ?? frame.centerDepth ?? 0))
    / Math.max(frame.height, EPSILON));
  const normalized = offset / Math.max(tolerance, EPSILON);
  return clamp01(1 - normalized * normalized);
}

function signedSide(sample, role) {
  return (sample?.side || 0) * (role.startsWith('left_') ? -1 : 1);
}

function anchorLandmarkScore(sample, role, frame, scaffold) {
  if (!sample) return 0;
  const arm = role.endsWith('_arm');
  const band = arm ? scaffold.shoulderBand : scaffold.hipBand;
  const virtual = arm ? scaffold.virtualChest : scaffold.virtualPelvis;
  const side = signedSide(sample, role);
  const sideScore = rangeScore(side, .035, arm ? .58 : .42, arm ? .3 : .24);
  const heightScore = rangeScore(sample.height01, band.minHeight01,
    band.maxHeight01, arm ? .22 : .25);
  const depthScore = absoluteDepthScore(sample, frame, arm ? .1 : .16);
  const virtualDistance = Math.hypot(sample.side - virtual.side,
    sample.height01 - virtual.height01, sample.depth - virtual.depth);
  const virtualScore = clamp01(1 - virtualDistance / (arm ? .55 : .45));
  return clamp01(.32 * sideScore + .3 * heightScore + .2 * depthScore
    + .18 * virtualScore);
}

function endpointLandmarkScore(sample, anchor, role, frame, scaffold) {
  if (!sample) return 0;
  const arm = role.endsWith('_arm');
  const side = signedSide(sample, role);
  if (!anchor) {
    return arm
      ? clamp01(.38 * rangeScore(side, .08, .78, .3)
        + .28 * rangeScore(sample.height01, .3, .94, .35)
        + .34 * absoluteDepthScore(sample, frame, .1))
      : clamp01(.5 * rangeScore(sample.height01, 0, .34, .2)
        + .2 * rangeScore(Math.abs(side), .02, .5, .2)
        + .3 * absoluteDepthScore(sample, frame, .28));
  }
  const outward = (sample.side - anchor.side)
    * (role.startsWith('left_') ? -1 : 1);
  const downward = anchor.height01 - sample.height01;
  const depthScore = absoluteDepthScore(sample, frame, arm ? .12 : .18);
  if (arm) {
    return clamp01(.3 * rangeScore(side, .12, .78, .28)
      + .28 * rangeScore(outward, .07, .7, .2)
      + .16 * rangeScore(sample.height01, .3, .92, .35)
      + .12 * rangeScore(downward, -.08, .5, .25)
      + .14 * depthScore);
  }
  const pelvisDistance = Math.hypot(sample.side - scaffold.virtualPelvis.side,
    sample.depth - scaffold.virtualPelvis.depth);
  return clamp01(.46 * clamp01(1 - sample.height01 / .34)
    + .18 * rangeScore(sample.height01, 0, .34, .2)
    + .14 * rangeScore(downward, .12, .85, .3)
    + .1 * rangeScore(Math.abs(side), .02, .5, .2)
    + .07 * depthScore + .05 * clamp01(1 - pelvisDistance / .6));
}

function landmarkSnapshot(sample, geometryScore, frame) {
  return {
    jointId: sample.jointId,
    pivot: sample.pivotPosition?.toArray() || null,
    influenceCenter: sample.influenceCenter?.toArray() || null,
    pivotDistance: sample.pivotPosition && sample.point
      ? sample.pivotPosition.distanceTo(sample.point) : 0,
    centerDistance: sample.influenceCenter && sample.point
      ? sample.influenceCenter.distanceTo(sample.point) : null,
    height01: sample.height01,
    side: sample.side,
    bodyDepth: sample.bodyDepth,
    normalizedDepth: sample.depth,
    componentId: sample.componentId ?? null,
    affectedVertexCount: sample.affectedVertexCount,
    totalWeight: sample.totalWeight,
    support: sample.support,
    geometryScore,
    bodyDepthScore: absoluteDepthScore(sample, frame),
  };
}

function buildLandmarkPools({rig, samples, frame, scaffold, role,
  lockedAnchorJointId = null}) {
  const arm = role.endsWith('_arm');
  const anchors = samples.filter(sample => {
    const rigParent = sample.componentId;
    if (lockedAnchorJointId !== null) {
      return sample.jointId === lockedAnchorJointId && rigParent !== undefined;
    }
    return (lockedAnchorJointId === null || sample.jointId === lockedAnchorJointId)
      && (rigParent !== undefined) && (arm
        ? sample.height01 >= Math.max(scaffold.shoulderBand.minHeight01 - .12,
          scaffold.shoulderBand.expectedHeight01 - .18, .68)
          && sample.height01 <= 1
          && signedSide(sample, role) > .02
        : sample.height01 >= .05
          && sample.height01 <= scaffold.hipBand.maxHeight01 + .12)
      && signedSide(sample, role) > -.04;
  });
  // The component-root check is performed when candidates are built because
  // samples deliberately remain geometry-only and do not carry the Rig.
  const endCandidates = samples.filter(sample => arm
    ? sample.height01 >= 0 && sample.height01 <= 1
      && signedSide(sample, role) > -.02
    : sample.height01 <= .42 && signedSide(sample, role) > -.08);
  const anchorPool = anchors.map(sample => ({sample,
    geometryScore: anchorLandmarkScore(sample, role, frame, scaffold)}))
    .sort((left, right) => right.geometryScore - left.geometryScore
      || left.sample.jointId - right.sample.jointId);
  const selectedAnchors = anchorPool.slice(0, 8);
  const rootChildAnchors = anchorPool.filter(item => {
    const component = componentFor(rig, item.sample.jointId);
    return component && item.sample.jointId !== component.rootId
      && parentFor(component, item.sample.jointId) === component.rootId;
  });
  for (const item of rootChildAnchors) {
    if (selectedAnchors.some(selected => selected.sample.jointId === item.sample.jointId)) continue;
    if (selectedAnchors.length >= 12) selectedAnchors.pop();
    selectedAnchors.push(item);
  }
  for (const item of anchorPool) {
    if (selectedAnchors.length >= 12) break;
    if (!selectedAnchors.some(selected => selected.sample.jointId === item.sample.jointId)) {
      selectedAnchors.push(item);
    }
  }
  selectedAnchors.sort((left, right) => right.geometryScore - left.geometryScore
    || left.sample.jointId - right.sample.jointId);
  const endPool = endCandidates.map(sample => ({sample,
    geometryScore: endpointLandmarkScore(sample, null, role, frame, scaffold)}));
  // Endpoint scores need the selected anchor's lateral coordinate. Keep a
  // broad pool first, then score each pair against its actual anchor below.
  endPool.sort((left, right) => right.geometryScore - left.geometryScore
    || left.sample.jointId - right.sample.jointId);
  // Keep the pool bounded while retaining terminal geometry from each branch.
  // A purely global score can spend the entire budget on the primary body
  // chain and make a secondary branch impossible to compare later.
  const selectedEnds = endPool.slice(0, 8);
  const terminalEnds = endPool.filter(item => {
    const component = componentFor(rig, item.sample.jointId);
    return childrenForComponent(component, item.sample.jointId).length === 0;
  });
  for (const item of terminalEnds) {
    if (selectedEnds.some(selected => selected.sample.jointId === item.sample.jointId)) continue;
    if (selectedEnds.length >= 12) selectedEnds.pop();
    selectedEnds.push(item);
  }
  for (const item of endPool) {
    if (selectedEnds.length >= 12) break;
    if (!selectedEnds.some(selected => selected.sample.jointId === item.sample.jointId)) {
      selectedEnds.push(item);
    }
  }
  selectedEnds.sort((left, right) => right.geometryScore - left.geometryScore
    || left.sample.jointId - right.sample.jointId);
  return {anchorPool: selectedAnchors, endPool: selectedEnds};
}

function pathGeometryMetrics({rig, pathJointIds, role, frame, samplesById: byId}) {
  const points = pathJointIds.map(id => byId.get(id)?.point || pointFor(rig, id));
  if (points.some(point => !point) || points.length < 3) return null;
  const sideSign = role.startsWith('left_') ? -1 : 1;
  const scale = Math.max(frame.height, EPSILON);
  let totalLength = 0;
  let absoluteSide = 0;
  let absoluteHeight = 0;
  let absoluteForward = 0;
  let outwardTravel = 0;
  let downwardTravel = 0;
  let insideCount = 0;
  let lateralError = 0;
  let depthError = 0;
  let supportTotal = 0;
  let continuationMatches = 0;
  let branchDominanceTotal = 0;
  let branchDominanceCount = 0;
  let branchEscapeCount = 0;
  for (let index = 1; index < points.length; index += 1) {
    const delta = points[index].clone().sub(points[index - 1]);
    const sideStep = delta.dot(frame.right);
    const heightStep = delta.dot(frame.up);
    const forwardStep = delta.dot(frame.forward);
    totalLength += delta.length();
    absoluteSide += Math.abs(sideStep);
    absoluteHeight += Math.abs(heightStep);
    absoluteForward += Math.abs(forwardStep);
    outwardTravel += Math.max(0, sideStep * sideSign);
    downwardTravel += Math.max(0, -heightStep);
    const currentId = pathJointIds[index - 1];
    if (numberId(valueFor(rig?.restContinuationChildByJointId
      || rig?.continuationChildByJointId, currentId)) === pathJointIds[index]) {
      continuationMatches += 1;
    }
    const component = componentFor(rig, currentId);
    const children = childrenForComponent(component, currentId);
    if (children.length > 1 && index > 1) {
      const current = byId.get(currentId);
      const next = byId.get(pathJointIds[index]);
      const branchProgress = sample => {
        if (!sample || !current) return -Infinity;
        const outward = (sample.side - current.side) * sideSign;
        const downward = current.height01 - sample.height01;
        return role.endsWith('_arm') ? outward + .35 * downward
          : downward + .2 * Math.abs(sample.side - current.side);
      };
      const selectedProgress = branchProgress(next);
      const bestSiblingProgress = Math.max(...children
        .filter(childId => childId !== pathJointIds[index])
        .map(childId => branchProgress(byId.get(childId))));
      if (bestSiblingProgress > selectedProgress + .04) branchEscapeCount += 1;
    }
    if (children.length > 1) {
      const selectedSupport = Number(byId.get(pathJointIds[index])?.support) || 1;
      const totalSupport = children.reduce((sum, childId) =>
        sum + (Number(byId.get(childId)?.support) || 1), 0);
      branchDominanceTotal += selectedSupport / Math.max(totalSupport, 1);
      branchDominanceCount += 1;
    }
  }
  const anchor = byId.get(pathJointIds[0]);
  const endpoint = byId.get(pathJointIds.at(-1));
  const directDistance = points[0].distanceTo(points.at(-1));
  const endpointSide = endpoint?.side || 0;
  const netSide = (endpoint?.side - (anchor?.side || 0));
  const netHeight = (endpoint?.height01 - (anchor?.height01 || 0));
  const netForward = (endpoint?.depth - (anchor?.depth || 0));
  const expectedProgress = pathJointIds.map(id => byId.get(id)).filter(Boolean);
  expectedProgress.forEach(sample => {
    const progress = (sample.side - (anchor?.side || 0)) * sideSign;
    const expectedDepth = anchor?.depth || 0;
    const depthOffset = Math.abs(sample.depth - expectedDepth);
    // Arms should not climb above the shoulder, while legs are expected to
    // descend. The leg corridor therefore penalizes upward reversal only;
    // measuring downward travel here would reject complete foot-reaching
    // paths in favor of truncated knee paths.
    const heightOffset = role.endsWith('_arm')
      ? Math.max(0, (sample.height01 - (anchor?.height01 || 0)) - .05)
      : Math.max(0, sample.height01 - (anchor?.height01 || 0));
    lateralError += Math.max(0, -progress);
    depthError += depthOffset;
    const inside = role.endsWith('_arm')
      ? progress >= -.08 && depthOffset <= .38 && heightOffset <= .28
      : progress <= .28 && depthOffset <= .3 && heightOffset <= .12;
    if (inside) insideCount += 1;
  });
  const segmentCount = Math.max(1, points.length - 1);
  const insideFraction = insideCount / points.length;
  const depthScore = clamp01(1 - depthError / Math.max(points.length * .42, EPSILON));
  const progressionScore = role.endsWith('_arm')
    ? clamp01(.55 * rangeScore(netSide * sideSign, .16, .85, .3)
      + .25 * rangeScore(-netHeight, -.2, .65, .3)
      + .2 * rangeScore(outwardTravel / scale, .12, .85, .3))
    : clamp01(.55 * rangeScore(-netHeight, .18, 1, .3)
      + .25 * rangeScore(Math.abs(netSide), 0, .35, .2)
      + .2 * rangeScore(downwardTravel / scale, .2, 1, .3));
  const straightness = totalLength > EPSILON
    ? clamp01(directDistance / totalLength) : 0;
  const continuationAgreement = continuationMatches / segmentCount;
  const supportValues = pathJointIds.map(id => Number(byId.get(id)?.support) || 1);
  const supportScale = Math.max(1, ...supportValues);
  const pathSupportScore = supportValues.reduce((sum, value) => sum + value, 0)
    / Math.max(supportValues.length * supportScale, 1);
  const endpointDepth = endpoint?.depth || 0;
  const corridorScore = clamp01(.35 * insideFraction + .25 * progressionScore
    + .25 * depthScore + .15 * straightness);
  const poseAngleScore = clamp01(.5 * progressionScore + .25 * straightness
    + .25 * insideFraction);
  const pathGeometryScore = clamp01(.38 * corridorScore + .25 * poseAngleScore
    + .18 * straightness + .1 * continuationAgreement
    + .09 * (branchDominanceCount
      ? branchDominanceTotal / branchDominanceCount : 1));
  return {
    totalLength: totalLength / scale,
    directDistance: directDistance / scale,
    straightness,
    netSide, netHeight, netForward,
    netSideDisplacement: netSide, netHeightDisplacement: netHeight,
    absoluteForwardTravel: absoluteForward / scale,
    forwardBacktracking: Math.max(0, absoluteForward
      - Math.abs(points.at(-1).clone().sub(points[0]).dot(frame.forward))) / scale,
    outwardProgressFraction: outwardTravel / Math.max(absoluteSide, EPSILON),
    downwardProgressFraction: downwardTravel / Math.max(absoluteHeight, EPSILON),
    sideBacktracking: Math.max(0, absoluteSide - Math.abs(points.at(-1)
      .clone().sub(points[0]).dot(frame.right))) / scale,
    heightBacktracking: Math.max(0, absoluteHeight - Math.abs(points.at(-1)
      .clone().sub(points[0]).dot(frame.up))) / scale,
    endpointSide, endpointHeight: endpoint?.height01 || 0,
    endpointDepth, absoluteBodyDepth: endpoint?.bodyDepth || frame.bodyDepth,
    continuationAgreement, segmentCount,
    sideFraction: clamp01(absoluteSide / scale),
    dropFraction: clamp01(downwardTravel / scale),
    depthFraction: clamp01(absoluteForward / scale),
    sideDownAngle: Math.atan2(downwardTravel, Math.max(outwardTravel, EPSILON)),
    outwardDisplacement: netSide * sideSign,
    downwardDisplacement: -netHeight,
    forwardDisplacement: netForward,
    lateralDeviation: lateralError / points.length,
    depthDeviation: depthError / points.length,
    endpointHeight01: endpoint?.height01 || 0,
    verticalCoverage: clamp01(absoluteHeight / scale),
    endpointBottomScore: clamp01(1 - (endpoint?.height01 || 0) / .34),
    pathSupportScore,
    anchorSupport: (Number(anchor?.support) || 1) / supportScale,
    proximalSupport: (Number(byId.get(pathJointIds[Math.min(1,
      pathJointIds.length - 1)])?.support) || 1) / supportScale,
    medianPathSupport: supportValues.sort((a, b) => a - b)
      .at(Math.floor(supportValues.length / 2)) / supportScale,
    distalSupport: (Number(endpoint?.support) || 1) / supportScale,
    branchDominance: branchDominanceCount
      ? branchDominanceTotal / branchDominanceCount : 1,
    branchEscapeCount,
    insideCorridorFraction: insideFraction,
    corridorScore, poseAngleScore,
    pathGeometryScore,
  };
}

function buildLimbHypotheses({rig, frame, scaffold, role, samples, pools,
  pathCache, lockedAnchorJointId = null}) {
  const byId = sampleIndex(samples);
  const candidates = [];
  const rejected = [];
  for (const anchorEntry of pools.anchorPool) {
    const anchor = anchorEntry.sample;
    const component = componentFor(rig, anchor.jointId);
    const parentId = parentFor(component, anchor.jointId);
    if (!component || parentId === null) {
      rejected.push({role, anchorJointId: anchor.jointId,
        reason: 'anchor_is_component_root'});
      continue;
    }
    for (const endEntry of pools.endPool) {
      const end = endEntry.sample;
      if (end.jointId === anchor.jointId || end.componentId !== anchor.componentId) continue;
      const endScore = endpointLandmarkScore(end, anchor, role, frame, scaffold);
      const signedAnchor = signedSide(anchor, role);
      const signedEnd = signedSide(end, role);
      const directionOkay = role.endsWith('_arm')
        ? signedEnd > signedAnchor + .025
          && end.height01 <= anchor.height01 + .16
        : end.height01 < anchor.height01 - .08
          && Math.abs(signedEnd - signedAnchor) < .5;
      if (!directionOkay) {
        if (lockedAnchorJointId !== null) rejected.push({role,
          anchorJointId: anchor.jointId, endJointId: end.jointId,
          reason: role.endsWith('_arm') ? 'endpoint_not_outward' : 'endpoint_not_below'});
        continue;
      }
      const key = `${Math.min(anchor.jointId, end.jointId)}:${Math.max(anchor.jointId, end.jointId)}`;
      let pathResult = pathCache.get(key);
      if (!pathResult) {
        pathResult = rigPathBetweenJointIds({rig, jointA: anchor.jointId,
          jointB: end.jointId});
        pathCache.set(key, pathResult);
      }
      if (!pathResult.connected || pathResult.jointIds.length < 3) {
        if (lockedAnchorJointId !== null) rejected.push({role,
          anchorJointId: anchor.jointId, endJointId: end.jointId,
          reason: 'manual_topology_mismatch'});
        continue;
      }
      const path = pathResult.jointIds[0] === anchor.jointId
        ? pathResult.jointIds : [...pathResult.jointIds].reverse();
      const pathDirectionOkay = path.slice(1, -1).every(jointId => {
        const sample = byId.get(jointId);
        if (!sample) return false;
        const sideProgress = (sample.side - anchor.side)
          * (role.startsWith('left_') ? -1 : 1);
        return role.endsWith('_arm')
          ? sideProgress >= -.08 && sample.height01 <= anchor.height01 + .16
          : sample.height01 <= anchor.height01 + .08
            && Math.abs(sample.side - anchor.side) <= .32;
      });
      if (!pathDirectionOkay) {
        rejected.push({role, anchorJointId: anchor.jointId,
          endJointId: end.jointId, reason: 'path_leaves_limb_corridor'});
        continue;
      }
      const bendJointId = selectLimbBendJoint(rig, path);
      if (!Number.isInteger(bendJointId) || !path.slice(1, -1).includes(bendJointId)) {
        rejected.push({role, anchorJointId: anchor.jointId, endJointId: end.jointId,
          reason: 'intermediate_not_on_endpoint_path'});
        continue;
      }
      const metrics = pathGeometryMetrics({rig, pathJointIds: path, role, frame,
        samplesById: byId});
      if (!metrics) continue;
      const topologyScore = clamp01(.45 * (pathResult.connected ? 1 : 0)
        + .35 * (pathResult.edgeCount >= 2 ? 1 : 0)
        + .2 * (path.includes(bendJointId) ? 1 : 0));
      const rootChildScore = Number(component.rootId) === parentId ? 1 : 0;
      const anchorPositionScore = clamp01(.85 * anchorEntry.geometryScore
        + .15 * rootChildScore);
      const landmarkGeometryScore = clamp01((anchorPositionScore + endScore) / 2);
      const componentScore = componentBodyScore(rig, frame, component);
      const parent = pointFor(rig, parentId);
      const parentCentrality = parent
        ? clamp01(1 - Math.abs(parent.clone().sub(frame.center).dot(frame.right))
          / Math.max(frame.height, EPSILON) / .45) : 0;
      const ancestry = parent ? ancestryScore(rig, frame, role, {
        anchorJointId: anchor.jointId, anchorPoint: anchor.point,
        parentPoint: parent, component,
      }) : {score: 0, ids: []};
      const bodyRelationScore = clamp01(.3 * componentScore
        + .22 * parentCentrality + .18 * rootChildScore
        + .15 * absoluteDepthScore(anchor, frame, role.endsWith('_arm') ? .1 : .16)
        + .2 * absoluteDepthScore(end, frame, role.endsWith('_arm') ? .12 : .18));
      const support = clamp01(.65 * metrics.pathSupportScore
        + .2 * metrics.anchorSupport + .15 * metrics.distalSupport);
      const pathGeometryScore = clamp01(metrics.pathGeometryScore);
      const predecessorId = path[path.length - 2];
      const predecessorChildren = childrenForComponent(component, predecessorId);
      const branchContinuationPenalty = predecessorChildren.length > 1 ? .12 : 0;
      const branchEscapePenalty = Math.min(.24, metrics.branchEscapeCount * .18);
      const totalScore = clamp01(.48 * landmarkGeometryScore
        + .27 * topologyScore + .12 * pathGeometryScore
        + .07 * bodyRelationScore + .05 * support
        - branchContinuationPenalty - branchEscapePenalty);
      const directionEvidence = resolveLimbBendDirection({
        rig, pathJointIds: path, anchorJointId: anchor.jointId,
        bendJointId, endJointId: end.jointId, role,
        characterForward: frame.forward,
      });
      const reasons = [];
      if (landmarkGeometryScore > .65) reasons.push('landmark_geometry');
      if (topologyScore >= .99) reasons.push('complete_topology');
      if (pathGeometryScore > .65) reasons.push('full_path_geometry');
      if (support > .65) reasons.push('evidence');
      if (bodyRelationScore > .65) reasons.push('primary_body_relation');
      candidates.push({
        role, available: true, anchorJointId: anchor.jointId,
        parentJointId: parentId, bendJointId, endJointId: end.jointId,
        pathJointIds: path, score: totalScore,
        confidence: totalScore >= .62 ? 'high' : totalScore >= .42 ? 'medium' : 'low',
        pairScore: 0, runnerUpMargin: 0, reasons,
        bendDirection: directionEvidence.bendDirection?.toArray() || null,
        bendDirectionSource: directionEvidence.bendDirectionSource,
        bendDirectionStrength: directionEvidence.bendDirectionStrength,
        parentCentrality, ancestorJointIds: ancestry.ids, ancestryScore: ancestry.score,
        componentId: anchor.componentId, pathLength: metrics.totalLength,
        pathGeometryScore, corridorScore: metrics.corridorScore,
        poseAngleScore: metrics.poseAngleScore,
        anchorPositionScore,
        endpointPositionScore: endScore, anchorScore: anchorEntry.geometryScore,
        endpointScore: endScore, pathSupportScore: metrics.pathSupportScore,
        proximalSupport: metrics.proximalSupport,
        branchDominanceScore: metrics.branchDominance,
        componentBodyScore: componentScore, continuationAgreement: metrics.continuationAgreement,
        pathMetrics: metrics,
        paths: [{pathJointIds: [...path], endJointId: end.jointId,
          bendJointId, score: totalScore, pathMetrics: metrics,
          continuationAgreement: metrics.continuationAgreement}],
        pathDiagnostics: {topology: {
          connected: pathResult.connected, edgeCount: pathResult.edgeCount,
          intermediateOnPath: true, onPrimaryPath: true,
          secondaryBranch: branchEscapePenalty > 0,
        }},
        topologyScore, landmarkGeometryScore, supportScore: support,
        bodyRelationScore, secondaryBranch: branchEscapePenalty > 0,
        branchContinuationPenalty, branchEscapePenalty,
        side: anchor.side, endSide: end.side,
        normalizedHeight: anchor.height01, normalizedEndHeight: end.height01,
        forwardOffset: Math.abs(end.depth - anchor.depth),
        familyId: null, alternatives: [],
      });
    }
  }
  candidates.sort(candidateSort);
  const retained = candidates.slice(0, 24);
  // Preserve one hypothesis for each bounded anchor pool entry so a lower
  // scoring branch can still be compared as a family/whole-body alternative.
  for (const anchorEntry of pools.anchorPool) {
    const bestForAnchor = candidates.find(candidate =>
      candidate.anchorJointId === anchorEntry.sample.jointId);
    if (!bestForAnchor || retained.some(candidate =>
      candidate.anchorJointId === bestForAnchor.anchorJointId)) continue;
    if (retained.length >= 24) retained.pop();
    retained.push(bestForAnchor);
  }
  retained.sort(candidateSort);
  return {candidates: retained, rejected,
    anchorPool: pools.anchorPool.map(item => landmarkSnapshot(item.sample,
      item.geometryScore, frame)),
    endPool: pools.endPool.map(item => landmarkSnapshot(item.sample,
      item.geometryScore, frame))};
}

function boundedPool(entries, isRequired, limit = 16) {
  const selected = entries.slice(0, Math.min(10, limit));
  for (const item of entries.filter(isRequired)) {
    if (selected.some(selectedItem => selectedItem.sample.jointId
        === item.sample.jointId)) continue;
    if (selected.length >= limit) selected.pop();
    selected.push(item);
  }
  for (const item of entries) {
    if (selected.length >= limit) break;
    if (!selected.some(selectedItem => selectedItem.sample.jointId
        === item.sample.jointId)) selected.push(item);
  }
  return selected.sort((left, right) => right.geometryScore - left.geometryScore
    || left.sample.jointId - right.sample.jointId);
}

function buildFootCandidatePool({rig, frame, samples, role}) {
  const entries = samples.filter(sample => {
    const component = componentFor(rig, sample.jointId);
    return component && sample.height01 <= .36 && signedSide(sample, role) > -.1;
  }).map(sample => {
    const component = componentFor(rig, sample.jointId);
    const terminal = childrenForComponent(component, sample.jointId).length === 0;
    const bottomScore = rangeScore(sample.height01, 0, .2, .2);
    const sideScore = rangeScore(Math.abs(signedSide(sample, role)), .015, .55, .2);
    const geometryScore = clamp01(.56 * bottomScore + .18 * sideScore
      + .16 * absoluteDepthScore(sample, frame, .28) + .1 * (terminal ? 1 : 0));
    return {sample, geometryScore, terminal};
  }).sort((left, right) => right.geometryScore - left.geometryScore
    || Number(right.terminal) - Number(left.terminal)
    || left.sample.jointId - right.sample.jointId);
  return boundedPool(entries, item => item.terminal);
}

function buildHipCandidatePool({rig, frame, scaffold, samples, role,
  lockedAnchorJointId = null}) {
  const entries = samples.filter(sample => {
    const component = componentFor(rig, sample.jointId);
    if (!component) return false;
    if (lockedAnchorJointId !== null && sample.jointId !== lockedAnchorJointId) return false;
    return sample.height01 >= .25
      && sample.height01 <= scaffold.hipBand.maxHeight01 + .18
      && signedSide(sample, role) > -.1;
  }).map(sample => {
    const component = componentFor(rig, sample.jointId);
    const rootChild = parentFor(component, sample.jointId) === component.rootId;
    return {sample,
      geometryScore: clamp01(.62 * anchorLandmarkScore(sample, role, frame, scaffold)
        + .16 * rangeScore(sample.height01, scaffold.hipBand.minHeight01,
          scaffold.hipBand.maxHeight01, .22)
        + .1 * rangeScore(Math.abs(signedSide(sample, role)), .03, .42, .2)
        + .12 * (rootChild ? 1 : 0)),
    };
  }).sort((left, right) => right.geometryScore - left.geometryScore
    || left.sample.jointId - right.sample.jointId);
  return boundedPool(entries, item => {
    const component = componentFor(rig, item.sample.jointId);
    return component && parentFor(component, item.sample.jointId) === component.rootId;
  });
}

function buildKneeCandidatesForPath({rig, frame, scaffold, role, byId,
  pathJointIds, hip, foot}) {
  const hipToFootHeight = hip.height01 - foot.height01;
  if (hipToFootHeight <= EPSILON) return [];
  const points = pathJointIds.map(id => byId.get(id)?.point || pointFor(rig, id));
  if (points.some(point => !point)) return [];
  const segmentLengths = points.slice(1).map((point, index) =>
    point.distanceTo(points[index]));
  const totalLength = segmentLengths.reduce((sum, length) => sum + length, 0);
  let traveled = 0;
  const candidates = [];
  for (let index = 1; index < pathJointIds.length - 1; index += 1) {
    traveled += segmentLengths[index - 1];
    const jointId = pathJointIds[index];
    const sample = byId.get(jointId);
    if (!sample) continue;
    const arcFraction = traveled / Math.max(totalLength, EPSILON);
    const verticalFraction = (hip.height01 - sample.height01) / hipToFootHeight;
    const verticalOrder = sample.height01 <= hip.height01 + .04
      && sample.height01 >= foot.height01 - .04;
    const centerline = scaffold.centerlineAt(sample.height01);
    const expectedSide = hip.side + (foot.side - hip.side) * arcFraction;
    const centerlineScore = clamp01(1 - Math.hypot(
      sample.side - centerline.side, sample.depth - centerline.depth) / .42);
    const pathSideScore = clamp01(1 - Math.abs(sample.side - expectedSide) / .35);
    const segmentBalance = Math.min(
      traveled, totalLength - traveled) / Math.max(
        Math.max(traveled, totalLength - traveled), EPSILON);
    const supportScale = Math.max(1, hip.support, foot.support, sample.support);
    const supportScore = sample.support / supportScale;
    const kneeScore = clamp01(.2 * rangeScore(arcFraction, .33, .67, .24)
      + .18 * rangeScore(verticalFraction, .25, .75, .24)
      + .18 * (verticalOrder ? 1 : 0)
      + .16 * centerlineScore + .12 * pathSideScore
      + .1 * absoluteDepthScore(sample, frame, .2)
      + .04 * rangeScore(segmentBalance, .25, .8, .25)
      + .02 * supportScore);
    candidates.push({sample, kneeScore, arcFraction, verticalFraction,
      onPrimaryPath: true, secondaryBranch: false});
  }
  return candidates.sort((left, right) => right.kneeScore - left.kneeScore
    || left.sample.jointId - right.sample.jointId);
}

function scoreLegTriple({rig, frame, scaffold, role, component, pathResult,
  path, hipEntry, kneeEntry, footEntry, byId}) {
  const hip = hipEntry.sample;
  const knee = kneeEntry.sample;
  const foot = footEntry.sample;
  const metrics = pathGeometryMetrics({rig, pathJointIds: path, role, frame,
    samplesById: byId});
  if (!metrics) return {candidate: null, rejection: 'path_geometry_unavailable'};
  const parentId = parentFor(component, hip.jointId);
  const parent = pointFor(rig, parentId);
  const rootChildScore = Number(component.rootId) === parentId ? 1 : 0;
  const parentCentrality = parent
    ? clamp01(1 - Math.abs(parent.clone().sub(frame.center).dot(frame.right))
      / Math.max(frame.height, EPSILON) / .45) : 0;
  const ancestry = parent ? ancestryScore(rig, frame, role, {
    anchorJointId: hip.jointId, anchorPoint: hip.point,
    parentPoint: parent, component,
  }) : {score: 0, ids: []};
  const componentScore = componentBodyScore(rig, frame, component);
  const supportScale = Math.max(1, hip.support, knee.support, foot.support);
  const support = clamp01(.55 * metrics.pathSupportScore
    + .25 * hip.support / supportScale + .2 * foot.support / supportScale);
  const endpointTerminal = childrenForComponent(component, foot.jointId).length === 0;
  const kneeOnPrimaryPath = path.slice(1, -1).includes(knee.jointId);
  // A leg candidate must span most of the lower body. This keeps an internal
  // shin or stomach strand from becoming a plausible Hip simply because it
  // has a clean, well-supported path to a low endpoint.
  const verticalReachScore = rangeScore(metrics.verticalCoverage, .5, .95, .18);
  const bottomScore = rangeScore(foot.height01, 0, .22, .18);
  const downwardScore = metrics.downwardProgressFraction;
  const corridorScore = metrics.insideCorridorFraction;
  const qualityScore = clamp01(.38 * verticalReachScore + .25 * bottomScore
    + .2 * downwardScore + .17 * corridorScore);
  const qualityFailure = foot.height01 > .36
    || metrics.verticalCoverage < .5
    || hip.height01 - foot.height01 < .5
    || metrics.downwardProgressFraction < .62
    || metrics.netHeight > -.3
    || metrics.insideCorridorFraction < .5
    || !kneeOnPrimaryPath;
  if (qualityFailure) return {candidate: null, rejection: 'leg_quality_too_low'};
  const topologyScore = clamp01(.2 * (pathResult.connected ? 1 : 0)
    + .16 * (pathResult.edgeCount >= 2 ? 1 : 0)
    + .2 * (kneeOnPrimaryPath ? 1 : 0)
    + .18 * metrics.continuationAgreement
    + .12 * metrics.branchDominance
    + .14 * (endpointTerminal ? 1 : .5));
  const pathScore = clamp01(.55 * metrics.pathGeometryScore
    + .25 * metrics.corridorScore + .2 * verticalReachScore);
  const hipScore = hipEntry.geometryScore;
  const kneeScore = kneeEntry.kneeScore;
  const footScore = footEntry.geometryScore;
  const landmarkGeometryScore = clamp01(.32 * hipScore + .4 * kneeScore
    + .28 * footScore);
  const bodyRelationScore = clamp01(.3 * componentScore
    + .22 * parentCentrality + .18 * rootChildScore
    + .15 * absoluteDepthScore(hip, frame, .16)
    + .15 * absoluteDepthScore(foot, frame, .18));
  const branchContinuationPenalty = path.length > 1
    && childrenForComponent(component, path[path.length - 2]).length > 1 ? .12 : 0;
  const branchEscapePenalty = Math.min(.24, metrics.branchEscapeCount * .18);
  const totalScore = clamp01(.24 * hipScore + .28 * kneeScore
    + .2 * footScore + .16 * pathScore + .08 * topologyScore
    + .04 * bodyRelationScore - branchContinuationPenalty
    - branchEscapePenalty);
  const directionEvidence = resolveLimbBendDirection({
    rig, pathJointIds: path, anchorJointId: hip.jointId,
    bendJointId: knee.jointId, endJointId: foot.jointId, role,
    characterForward: frame.forward,
  });
  const reasons = ['hip_knee_foot_fit'];
  if (footScore > .65) reasons.push('foot_near_bottom');
  if (metrics.verticalCoverage >= .5) reasons.push('long_leg_reach');
  if (topologyScore >= .7) reasons.push('ordered_leg_topology');
  if (support > .65) reasons.push('evidence');
  return {candidate: {
    role, available: true, anchorJointId: hip.jointId,
    parentJointId: parentId, bendJointId: knee.jointId, endJointId: foot.jointId,
    hipJointId: hip.jointId, kneeJointId: knee.jointId, footJointId: foot.jointId,
    pathJointIds: path, score: totalScore, totalScore,
    confidence: totalScore >= .62 ? 'high' : totalScore >= .42 ? 'medium' : 'low',
    pairScore: 0, runnerUpMargin: 0, reasons,
    bendDirection: directionEvidence.bendDirection?.toArray() || null,
    bendDirectionSource: directionEvidence.bendDirectionSource,
    bendDirectionStrength: directionEvidence.bendDirectionStrength,
    parentCentrality, ancestorJointIds: ancestry.ids, ancestryScore: ancestry.score,
    componentId: hip.componentId, pathLength: metrics.totalLength,
    pathGeometryScore: metrics.pathGeometryScore, corridorScore: metrics.corridorScore,
    poseAngleScore: metrics.poseAngleScore, anchorPositionScore: hipScore,
    endpointPositionScore: footScore, anchorScore: hipScore, endpointScore: footScore,
    pathSupportScore: metrics.pathSupportScore, proximalSupport: metrics.proximalSupport,
    branchDominanceScore: metrics.branchDominance, componentBodyScore: componentScore,
    continuationAgreement: metrics.continuationAgreement, pathMetrics: metrics,
    hipScore, kneeScore, footScore, pathScore, topologyScore,
    legQualityScore: qualityScore,
    landmarkGeometryScore, supportScore: support, bodyRelationScore,
    secondaryBranch: branchEscapePenalty > 0,
    branchContinuationPenalty, branchEscapePenalty,
    onPrimaryPath: true,
    side: hip.side, endSide: foot.side, normalizedHeight: hip.height01,
    normalizedEndHeight: foot.height01,
    forwardOffset: Math.abs(foot.depth - hip.depth), familyId: null, alternatives: [],
    paths: [{pathJointIds: [...path], endJointId: foot.jointId,
      bendJointId: knee.jointId, score: totalScore, pathMetrics: metrics,
      continuationAgreement: metrics.continuationAgreement}],
    pathDiagnostics: {topology: {
      connected: pathResult.connected, edgeCount: pathResult.edgeCount,
      intermediateOnPath: kneeOnPrimaryPath, onPrimaryPath: true,
      secondaryBranch: branchEscapePenalty > 0,
    }},
  }, qualityScore};
}

function buildLegTriples({rig, frame, scaffold, role, samples, pathCache,
  lockedAnchorJointId = null}) {
  const hips = buildHipCandidatePool({rig, frame, scaffold, samples, role,
    lockedAnchorJointId});
  const feet = buildFootCandidatePool({rig, frame, samples, role});
  const byId = sampleIndex(samples);
  const candidates = [];
  const rejected = [];
  // Feet define the distal landmark pool first. Hips are then evaluated only
  // through paths that can actually reach one of those foot candidates.
  for (const footEntry of feet) {
    const foot = footEntry.sample;
    for (const hipEntry of hips) {
      const hip = hipEntry.sample;
      const component = componentFor(rig, hip.jointId);
      if (!component || foot.componentId !== hip.componentId
          || hip.jointId === foot.jointId) continue;
      if (parentFor(component, hip.jointId) === null) {
        if (lockedAnchorJointId !== null) rejected.push({role,
          anchorJointId: hip.jointId, endJointId: foot.jointId,
          reason: 'anchor_is_component_root'});
        continue;
      }
      if (foot.height01 >= hip.height01 - .08) {
        if (lockedAnchorJointId !== null) rejected.push({role,
          anchorJointId: hip.jointId, endJointId: foot.jointId,
          reason: 'endpoint_not_below'});
        continue;
      }
      const key = `${Math.min(hip.jointId, foot.jointId)}:${Math.max(hip.jointId, foot.jointId)}`;
      let pathResult = pathCache.get(key);
      if (!pathResult) {
        pathResult = rigPathBetweenJointIds({rig, jointA: hip.jointId,
          jointB: foot.jointId});
        pathCache.set(key, pathResult);
      }
      if (!pathResult.connected || pathResult.jointIds.length < 3) {
        if (lockedAnchorJointId !== null) rejected.push({role,
          anchorJointId: hip.jointId, endJointId: foot.jointId,
          reason: 'manual_topology_mismatch'});
        continue;
      }
      const path = pathResult.jointIds[0] === hip.jointId
        ? pathResult.jointIds : [...pathResult.jointIds].reverse();
      const pathDirectionOkay = path.slice(1, -1).every(jointId => {
        const sample = byId.get(jointId);
        return sample && sample.height01 <= hip.height01 + .08
          && sample.height01 >= foot.height01 - .08
          && Math.abs(sample.side - hip.side) <= .32;
      });
      if (!pathDirectionOkay) {
        rejected.push({role, anchorJointId: hip.jointId,
          endJointId: foot.jointId, reason: 'path_leaves_limb_corridor'});
        continue;
      }
      const knees = buildKneeCandidatesForPath({rig, frame, scaffold, role,
        byId, pathJointIds: path, hip, foot});
      if (!knees.length) {
        rejected.push({role, anchorJointId: hip.jointId, endJointId: foot.jointId,
          reason: 'knee_not_on_endpoint_path'});
        continue;
      }
      for (const kneeEntry of knees) {
        const scored = scoreLegTriple({rig, frame, scaffold, role, component,
          pathResult, path, hipEntry, kneeEntry, footEntry, byId});
        if (scored.candidate) candidates.push(scored.candidate);
        else rejected.push({role, anchorJointId: hip.jointId,
          endJointId: foot.jointId, bendJointId: kneeEntry.sample.jointId,
          reason: scored.rejection});
      }
    }
  }
  candidates.sort(candidateSort);
  const retained = candidates.slice(0, 32);
  for (const hipEntry of hips) {
    const bestForHip = candidates.find(candidate =>
      candidate.anchorJointId === hipEntry.sample.jointId);
    if (!bestForHip || retained.some(candidate =>
      candidate.anchorJointId === bestForHip.anchorJointId)) continue;
    if (retained.length >= 32) retained.pop();
    retained.push(bestForHip);
  }
  retained.sort(candidateSort);
  return {candidates: retained, rejected,
    anchorPool: hips.map(item => landmarkSnapshot(item.sample,
      item.geometryScore, frame)),
    endPool: feet.map(item => landmarkSnapshot(item.sample,
      item.geometryScore, frame)),
    hipPool: hips.map(item => landmarkSnapshot(item.sample,
      item.geometryScore, frame)),
    footPool: feet.map(item => landmarkSnapshot(item.sample,
      item.geometryScore, frame)),
  };
}

/** Select a semantic Knee for a manually chosen leg endpoint. Generic IK
 * midpoint selection remains available for non-humanoid path utilities. */
export function selectHumanoidKneeJoint({rig, role, pathJointIds, axes,
  characterForward, semanticFrame} = {}) {
  if (!role?.endsWith('_leg') || !Array.isArray(pathJointIds)
      || pathJointIds.length < 3) return null;
  const frame = semanticFrame || buildHumanoidSemanticFrame({rig, axes,
    characterForward});
  const scaffold = buildHumanoidScaffold({rig, frame});
  const byId = sampleIndex(collectHumanoidSamples({rig, frame}));
  const hip = byId.get(numberId(pathJointIds[0]));
  const foot = byId.get(numberId(pathJointIds.at(-1)));
  if (!hip || !foot) return null;
  return buildKneeCandidatesForPath({rig, frame, scaffold, role, byId,
    pathJointIds, hip, foot})[0]?.sample.jointId ?? null;
}

function buildRoleCandidates({rig, frame, scaffold, role, samples,
  pathCache, lockedAnchorJointId = null}) {
  if (role.endsWith('_leg')) {
    return buildLegTriples({rig, frame, scaffold, role, samples, pathCache,
      lockedAnchorJointId});
  }
  const pools = buildLandmarkPools({rig, samples, frame, scaffold, role,
    lockedAnchorJointId});
  return buildLimbHypotheses({rig, frame, scaffold, role, samples, pools,
    pathCache, lockedAnchorJointId});
}

/** Resolve a persisted/manual anchor with the same geometry and topology
 * contract as automatic detection. The caller remains authoritative for any
 * later bend or end override. */
export function resolveHumanoidLimbMapping({
  rig, role, anchorJointId, axes, characterForward, semanticFrame,
} = {}) {
  const frame = semanticFrame || buildHumanoidSemanticFrame({rig, axes,
    characterForward});
  const scaffold = buildHumanoidScaffold({rig, frame});
  const samples = collectHumanoidSamples({rig, frame});
  const pathCache = new Map();
  const anchorId = numberId(anchorJointId);
  const result = buildRoleCandidates({rig, frame, scaffold, role, samples,
    pathCache, lockedAnchorJointId: anchorId});
  const candidate = result.candidates.find(item => item.anchorJointId === anchorId);
  if (candidate) return candidate;
  return {
    ...emptySuggestion(role, 'manual_topology_mismatch'),
    anchorJointId: anchorId,
    topologyDiagnostics: result.rejected.filter(item =>
      item.anchorJointId === anchorId),
  };
}

function candidateSort(left, right) {
  if (Math.abs(right.score - left.score) > .000001) return right.score - left.score;
  if (Math.abs((right.pathGeometryScore || 0) - (left.pathGeometryScore || 0)) > .000001) {
    return (right.pathGeometryScore || 0) - (left.pathGeometryScore || 0);
  }
  if (Math.abs((right.continuationAgreement || 0)
      - (left.continuationAgreement || 0)) > .000001) {
    return (right.continuationAgreement || 0) - (left.continuationAgreement || 0);
  }
  if (Math.abs((right.pathLength || 0) - (left.pathLength || 0)) > .000001) {
    return (right.pathLength || 0) - (left.pathLength || 0);
  }
  if (Number(left.endJointId) !== Number(right.endJointId)) {
    return Number(left.endJointId) - Number(right.endJointId);
  }
  if (Number(left.anchorJointId) !== Number(right.anchorJointId)) {
    return Number(left.anchorJointId) - Number(right.anchorJointId);
  }
  for (let index = 0; index < Math.min(left.pathJointIds.length, right.pathJointIds.length); index += 1) {
    if (left.pathJointIds[index] !== right.pathJointIds[index]) {
      return left.pathJointIds[index] - right.pathJointIds[index];
    }
  }
  return left.pathJointIds.length - right.pathJointIds.length;
}

function isDescendantInComponent(component, ancestorId, descendantId) {
  let current = numberId(descendantId);
  const ancestor = numberId(ancestorId);
  const visited = new Set();
  while (current !== null && !visited.has(current)) {
    if (current === ancestor) return true;
    visited.add(current);
    current = parentFor(component, current);
  }
  return false;
}

function candidatePathsAreNested(rig, left, right) {
  const leftPath = new Set(left.pathJointIds);
  const rightPath = new Set(right.pathJointIds);
  const overlap = [...leftPath].filter(id => rightPath.has(id)).length;
  const overlapRatio = overlap / Math.max(1, Math.min(leftPath.size, rightPath.size));
  if (overlapRatio < .6) return false;
  const component = componentFor(rig, left.anchorJointId);
  return left.endJointId === right.endJointId
    || leftPath.has(right.endJointId) || rightPath.has(left.endJointId)
    || isDescendantInComponent(component, left.endJointId, right.endJointId)
    || isDescendantInComponent(component, right.endJointId, left.endJointId);
}

function familyRepresentativeSort(left, right) {
  if (Math.abs(left.score - right.score) > .000001) {
    return right.score - left.score;
  }
  if (Math.abs(left.landmarkGeometryScore - right.landmarkGeometryScore) > .000001) {
    return right.landmarkGeometryScore - left.landmarkGeometryScore;
  }
  if (left.anchorJointId === right.anchorJointId
      && Math.abs(left.pathLength - right.pathLength) > .000001) {
    return right.pathLength - left.pathLength;
  }
  if (Math.abs(left.parentCentrality - right.parentCentrality) > .000001) {
    return right.parentCentrality - left.parentCentrality;
  }
  if (left.pathJointIds.length !== right.pathJointIds.length) {
    return right.pathJointIds.length - left.pathJointIds.length;
  }
  return Number(left.anchorJointId) - Number(right.anchorJointId);
}

function collapseCandidateFamilies(rig, candidates, role) {
  const sideSign = role.startsWith('left_') ? -1 : 1;
  const matching = candidates.filter(candidate => candidate.role === role
    && candidate.side * sideSign > 0);
  const parent = matching.map((_, index) => index);
  const find = index => {
    let root = index;
    while (parent[root] !== root) root = parent[root];
    while (parent[index] !== index) {
      const next = parent[index];
      parent[index] = root;
      index = next;
    }
    return root;
  };
  const union = (left, right) => {
    const leftRoot = find(left);
    const rightRoot = find(right);
    if (leftRoot !== rightRoot) parent[rightRoot] = leftRoot;
  };
  for (let left = 0; left < matching.length; left += 1) {
    for (let right = left + 1; right < matching.length; right += 1) {
      if (candidatePathsAreNested(rig, matching[left], matching[right])) union(left, right);
    }
  }
  const groups = new Map();
  matching.forEach((candidate, index) => {
    const root = find(index);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(candidate);
  });
  const families = [...groups.values()].map(members => {
    const sorted = [...members].sort(familyRepresentativeSort);
    const representative = {...sorted[0], alternatives: sorted.slice(1)};
    return {representative, alternatives: sorted.slice(1)};
  });
  families.sort((left, right) => familyRepresentativeSort(left.representative, right.representative));
  families.forEach((family, index) => {
    family.familyId = `${role}:${family.representative.endJointId}:${index}`;
    family.representative.familyId = family.familyId;
  });
  return families;
}

function pairCandidates(rig, frame, left, right) {
  const pairs = [];
  for (const first of left) for (const second of right) {
    const firstPath = new Set(first.pathJointIds);
    if (second.pathJointIds.some(id => firstPath.has(id))) continue;
    const leftPoint = pointFor(rig, first.anchorJointId);
    const rightPoint = pointFor(rig, second.anchorJointId);
    const leftEnd = pointFor(rig, first.endJointId);
    const rightEnd = pointFor(rig, second.endJointId);
    if (!leftPoint || !rightPoint || !leftEnd || !rightEnd) continue;
    const symmetry = Math.abs(leftPoint.clone().sub(frame.center).dot(frame.up)
      - rightPoint.clone().sub(frame.center).dot(frame.up)) / frame.height
      + Math.abs(leftEnd.clone().sub(frame.center).dot(frame.up)
        - rightEnd.clone().sub(frame.center).dot(frame.up)) / frame.height
      + Math.abs(Math.abs(leftPoint.clone().sub(frame.center).dot(frame.right))
        - Math.abs(rightPoint.clone().sub(frame.center).dot(frame.right))) / frame.height;
    const descriptorSymmetry = .08 * Math.abs((first.pathLength || 0)
        - (second.pathLength || 0))
      + .06 * Math.abs((first.pathMetrics?.netSide || 0) * -1
        - (second.pathMetrics?.netSide || 0))
      + .06 * Math.abs((first.pathMetrics?.netHeight || 0)
        - (second.pathMetrics?.netHeight || 0))
      + .06 * Math.abs((first.pathMetrics?.netForward || 0)
        - (second.pathMetrics?.netForward || 0))
      + .05 * Math.abs((first.pathMetrics?.absoluteForwardTravel || 0)
        - (second.pathMetrics?.absoluteForwardTravel || 0))
      + .05 * Math.abs((first.pathMetrics?.forwardBacktracking || 0)
        - (second.pathMetrics?.forwardBacktracking || 0))
      + .05 * Math.abs(Math.abs(first.pathMetrics?.endpointSide || 0)
        - Math.abs(second.pathMetrics?.endpointSide || 0))
      + .05 * Math.abs((first.pathMetrics?.endpointDepth || 0)
        - (second.pathMetrics?.endpointDepth || 0))
      + .06 * Math.abs((first.pathMetrics?.straightness || 0)
        - (second.pathMetrics?.straightness || 0))
      + .05 * Math.abs((first.pathMetrics?.outwardProgressFraction || 0)
        - (second.pathMetrics?.outwardProgressFraction || 0))
      + .05 * Math.abs((first.pathMetrics?.downwardProgressFraction || 0)
        - (second.pathMetrics?.downwardProgressFraction || 0))
      + .07 * Math.abs((first.corridorScore || 0)
        - (second.corridorScore || 0));
    const pairScore = (first.score + second.score) / 2
      - Math.min(.35, symmetry * .12 + descriptorSymmetry);
    pairs.push({first, second, pairScore, symmetry,
      familyIds: [first.familyId, second.familyId]});
  }
  return pairs.sort((a, b) => {
    if (Math.abs(b.pairScore - a.pairScore) > .000001) return b.pairScore - a.pairScore;
    const firstGeometry = (a.first.pathGeometryScore + a.second.pathGeometryScore) / 2;
    const secondGeometry = (b.first.pathGeometryScore + b.second.pathGeometryScore) / 2;
    if (Math.abs(secondGeometry - firstGeometry) > .000001) return secondGeometry - firstGeometry;
    const firstContinuation = (a.first.continuationAgreement + a.second.continuationAgreement) / 2;
    const secondContinuation = (b.first.continuationAgreement + b.second.continuationAgreement) / 2;
    if (Math.abs(secondContinuation - firstContinuation) > .000001) return secondContinuation - firstContinuation;
    const firstLength = (a.first.pathLength + a.second.pathLength) / 2;
    const secondLength = (b.first.pathLength + b.second.pathLength) / 2;
    if (Math.abs(secondLength - firstLength) > .000001) return secondLength - firstLength;
    if (a.first.endJointId !== b.first.endJointId) return a.first.endJointId - b.first.endJointId;
    if (a.second.endJointId !== b.second.endJointId) return a.second.endJointId - b.second.endJointId;
    return a.first.anchorJointId - b.first.anchorJointId || a.second.anchorJointId - b.second.anchorJointId;
  });
}

function choosePair(rig, frame, candidates, roles) {
  const leftFamilies = collapseCandidateFamilies(rig, candidates, roles[0]);
  const rightFamilies = collapseCandidateFamilies(rig, candidates, roles[1]);
  const pairs = pairCandidates(rig, frame,
    leftFamilies.map(family => family.representative),
    rightFamilies.map(family => family.representative));
  if (!pairs.length) {
    return {pair: null, margin: 0, pairs: [], leftFamilies, rightFamilies};
  }
  const best = pairs[0];
  const margin = pairs.length > 1 ? best.pairScore - pairs[1].pairScore : 1;
  return {pair: best, margin, pairs, leftFamilies, rightFamilies};
}

function makePairSuggestions(choice, roles) {
  if (!choice.pair) {
    // Automatic humanoid mappings are an atomic bilateral contract. A
    // one-sided result is too easy for downstream consumers to interpret as
    // a complete body and is not a safe recovery for a missing counterpart.
    return Object.fromEntries(roles.map(role => [role,
      emptySuggestion(role, 'no_bilateral_pair')]));
  }
  const ambiguous = choice.margin < .065;
  const pair = choice.pair;
  const result = {};
  for (const [role, candidate] of [[roles[0], pair.first], [roles[1], pair.second]]) {
    result[role] = ambiguous ? {
      ...emptySuggestion(role, 'ambiguous_pair'),
      pairScore: pair.pairScore, runnerUpMargin: choice.margin,
    } : {
      ...candidate, pairScore: pair.pairScore, runnerUpMargin: choice.margin,
      available: candidate.confidence !== 'low',
      reasons: candidate.confidence === 'low'
        ? [...candidate.reasons, 'low_confidence'] : candidate.reasons,
    };
  }
  return result;
}

function candidateSnapshot(candidate) {
  return {
    role: candidate.role,
    anchorJointId: candidate.anchorJointId,
    parentJointId: candidate.parentJointId,
    bendJointId: candidate.bendJointId,
    endJointId: candidate.endJointId,
    hipJointId: candidate.hipJointId ?? null,
    kneeJointId: candidate.kneeJointId ?? null,
    footJointId: candidate.footJointId ?? null,
    pathJointIds: [...candidate.pathJointIds],
    ancestorJointIds: [...(candidate.ancestorJointIds || [])],
    componentId: candidate.componentId ?? null,
    familyId: candidate.familyId,
    score: candidate.score,
    totalScore: candidate.totalScore ?? candidate.score,
    hipScore: candidate.hipScore ?? 0,
    kneeScore: candidate.kneeScore ?? 0,
    footScore: candidate.footScore ?? 0,
    pathScore: candidate.pathScore ?? candidate.pathGeometryScore ?? 0,
    legQualityScore: candidate.legQualityScore ?? 0,
    parentCentrality: candidate.parentCentrality,
    pathLength: candidate.pathLength,
    pathGeometryScore: candidate.pathGeometryScore,
    corridorScore: candidate.corridorScore,
    poseAngleScore: candidate.poseAngleScore,
    anchorPositionScore: candidate.anchorPositionScore,
    endpointPositionScore: candidate.endpointPositionScore,
    ancestryScore: candidate.ancestryScore,
    pathSupportScore: candidate.pathSupportScore,
    proximalSupport: candidate.proximalSupport,
    branchDominanceScore: candidate.branchDominanceScore,
    componentBodyScore: candidate.componentBodyScore,
    anchorScore: candidate.anchorScore,
    endpointScore: candidate.endpointScore,
    landmarkGeometryScore: candidate.landmarkGeometryScore,
    topologyScore: candidate.topologyScore,
    supportScore: candidate.supportScore,
    bodyRelationScore: candidate.bodyRelationScore,
    secondaryBranch: candidate.secondaryBranch === true,
    onPrimaryPath: candidate.onPrimaryPath !== false,
    branchContinuationPenalty: candidate.branchContinuationPenalty || 0,
    branchEscapePenalty: candidate.branchEscapePenalty || 0,
    continuationAgreement: candidate.continuationAgreement,
    pathMetrics: candidate.pathMetrics || null,
    pathDiagnostics: candidate.pathDiagnostics || null,
    paths: (candidate.paths || []).map(path => ({
      pathJointIds: [...path.pathJointIds],
      endJointId: path.endJointId,
      bendJointId: path.bendJointId,
      score: path.score,
      ...path.pathMetrics,
      continuationAgreement: path.continuationAgreement,
    })),
    side: candidate.side,
    endSide: candidate.endSide,
    normalizedHeight: candidate.normalizedHeight,
    normalizedEndHeight: candidate.normalizedEndHeight,
    forwardOffset: candidate.forwardOffset,
    reasons: [...candidate.reasons],
  };
}

function familySnapshot(family) {
  return {
    familyId: family.familyId,
    role: family.representative.role,
    side: family.representative.role.startsWith('left_') ? 'left' : 'right',
    representative: candidateSnapshot(family.representative),
    alternatives: family.alternatives.map(candidateSnapshot),
  };
}

function pairSnapshot(choice) {
  const pair = choice.pair;
  return {
    best: pair ? {
      left: candidateSnapshot(pair.first),
      right: candidateSnapshot(pair.second),
      pairScore: pair.pairScore,
      symmetry: pair.symmetry,
      familyIds: [...pair.familyIds],
    } : null,
    runnerUp: choice.pairs[1] ? {
      leftAnchorJointId: choice.pairs[1].first.anchorJointId,
      rightAnchorJointId: choice.pairs[1].second.anchorJointId,
      pairScore: choice.pairs[1].pairScore,
      symmetry: choice.pairs[1].symmetry,
      familyIds: [...choice.pairs[1].familyIds],
    } : null,
    margin: choice.margin,
    pairCount: choice.pairs.length,
  };
}

function wholeBodyCoherence(rig, frame, scaffold, armPair, legPair) {
  const arms = [armPair.first, armPair.second];
  const legs = [legPair.first, legPair.second];
  const point = candidate => pointFor(rig, candidate.anchorJointId);
  const endpoint = candidate => pointFor(rig, candidate.endJointId);
  const height01 = value => value
    ? clamp01((value.dot(frame.up) - frame.lowHeight) / frame.height) : 0;
  const shoulderHeight = arms.reduce((sum, item) => sum + height01(point(item)), 0) / 2;
  const hipHeight = legs.reduce((sum, item) => sum + height01(point(item)), 0) / 2;
  const footHeight = legs.reduce((sum, item) => sum + height01(endpoint(item)), 0) / 2;
  const shoulderMidpoint = point(arms[0])?.clone().add(point(arms[1]))
    .multiplyScalar(.5);
  const hipMidpoint = point(legs[0])?.clone().add(point(legs[1]))
    .multiplyScalar(.5);
  const midpointScore = midpoint => midpoint
    ? clamp01(1 - Math.abs(midpoint.dot(frame.right) - frame.centerSide)
      / frame.height / .45) : 0;
  const ordering = clamp01(.5 * rangeScore(shoulderHeight, hipHeight + .08, 1, .2)
    + .5 * rangeScore(hipHeight, footHeight + .12, 1, .25));
  const centerScore = .5 * midpointScore(shoulderMidpoint)
    + .5 * midpointScore(hipMidpoint);
  const armScore = arms.reduce((sum, item) => sum + (item.corridorScore || 0), 0) / 2;
  const legScore = legs.reduce((sum, item) => sum + (item.corridorScore || 0), 0) / 2;
  const ancestry = [...arms, ...legs].reduce((sum, item) =>
    sum + (item.ancestryScore || 0), 0) / 4;
  const expectedShoulder = scaffold.shoulderBand.expectedHeight01;
  const expectedHip = scaffold.hipBand.expectedHeight01;
  const bandScore = .5 * rangeScore(shoulderHeight, expectedShoulder - .25,
    expectedShoulder + .25, .25)
    + .5 * rangeScore(hipHeight, expectedHip - .28, expectedHip + .28, .28);
  return clamp01(.28 * ordering + .2 * centerScore + .2 * armScore
    + .18 * legScore + .08 * ancestry + .06 * bandScore);
}

function chooseWholeBody(rig, frame, scaffold, armChoice, legChoice) {
  const armPairs = armChoice.pairs.slice(0, 4);
  const legPairs = legChoice.pairs.slice(0, 4);
  const combinations = [];
  for (const armPair of armPairs) for (const legPair of legPairs) {
    const coherence = wholeBodyCoherence(rig, frame, scaffold, armPair, legPair);
    combinations.push({armPair, legPair, coherence,
      wholeBodyScore: (armPair.pairScore + legPair.pairScore) / 2
        + .12 * coherence});
  }
  combinations.sort((left, right) => right.wholeBodyScore - left.wholeBodyScore
    || right.coherence - left.coherence
    || left.armPair.first.anchorJointId - right.armPair.first.anchorJointId
    || left.legPair.first.anchorJointId - right.legPair.first.anchorJointId);
  return {best: combinations[0] || null, combinations};
}

/** Suggest bilateral arm and leg mappings from ModelJoint geometry. */
export function suggestHumanoidLimbMappings({
  rig, characterForward, axes, debug = false,
} = {}) {
  const frame = buildHumanoidSemanticFrame({rig, axes, characterForward});
  const scaffold = buildHumanoidScaffold({rig, frame});
  const samples = collectHumanoidSamples({rig, frame});
  const pathCache = new Map();
  const rejected = [];
  const candidates = [];
  const landmarkCandidates = {};
  const resolverStats = {
    anchorsEvaluated: 0, pathExpansions: 0, maxBeamSize: 0,
    pathCandidatesProduced: 0, maxDepth: 0, beamWidth: 0,
    pathLookups: 0,
  };
  // Fit the lower body first so the whole-body chooser has a stable pelvis
  // reference before arm candidates are ranked.
  for (const role of [...LEG_ROLES, ...ARM_ROLES]) {
    const result = buildRoleCandidates({rig, frame, scaffold, role, samples,
      pathCache});
    candidates.push(...result.candidates);
    landmarkCandidates[role] = {
      anchors: result.anchorPool,
      ends: result.endPool,
      hips: result.hipPool || [],
      feet: result.footPool || [],
    };
    if (debug) rejected.push(...result.rejected);
  }
  resolverStats.anchorsEvaluated = Object.values(landmarkCandidates)
    .reduce((sum, pools) => sum + pools.anchors.length, 0);
  resolverStats.pathLookups = pathCache.size;
  const roleCandidates = role => candidates.filter(candidate => candidate.role === role).sort(candidateSort);
  const armChoice = choosePair(rig, frame, [...roleCandidates('left_arm'), ...roleCandidates('right_arm')], ARM_ROLES);
  const legChoice = choosePair(rig, frame, [...roleCandidates('left_leg'), ...roleCandidates('right_leg')], LEG_ROLES);
  const wholeBody = armChoice.pairs.length && legChoice.pairs.length
    ? chooseWholeBody(rig, frame, scaffold, armChoice, legChoice) : {best: null, combinations: []};
  const selectedArmChoice = wholeBody.best
    ? {...armChoice, pair: wholeBody.best.armPair} : armChoice;
  const selectedLegChoice = wholeBody.best
    ? {...legChoice, pair: wholeBody.best.legPair} : legChoice;
  let roles = {...makePairSuggestions(selectedArmChoice, ARM_ROLES),
    ...makePairSuggestions(selectedLegChoice, LEG_ROLES)};
  const arms = [roles.left_arm, roles.right_arm];
  const legs = [roles.left_leg, roles.right_leg];
  if (arms.every(item => item.available) && legs.every(item => item.available)) {
    const shoulderHeight = Math.min(...arms.map(item => {
      const point = pointFor(rig, item.anchorJointId);
      return point ? (point.dot(frame.up) - frame.lowHeight) / frame.height : 0;
    }));
    const hipHeight = Math.max(...legs.map(item => {
      const point = pointFor(rig, item.anchorJointId);
      return point ? (point.dot(frame.up) - frame.lowHeight) / frame.height : 0;
    }));
    if (shoulderHeight <= hipHeight) {
      roles = {...roles};
      arms.forEach(item => { roles[item.role] = emptySuggestion(item.role, 'humanoid_level_sanity_failed'); });
      legs.forEach(item => { roles[item.role] = emptySuggestion(item.role, 'humanoid_level_sanity_failed'); });
    }
  }
  const result = {
    version: 1,
    roles,
    ...roles,
    suggestions: ROLES.map(role => roles[role]),
    available: Object.values(roles).filter(item => item.available).length,
  };
  if (debug) {
    const rejectionCounts = {};
    rejected.forEach(item => {
      const key = `${item.role}:${item.reason}`;
      rejectionCounts[key] = (rejectionCounts[key] || 0) + 1;
    });
    const topCandidatesByRole = Object.fromEntries(ROLES.map(role => [role,
      roleCandidates(role).slice(0, 10).map(candidateSnapshot)]));
    result.debug = {
      frame: {center: frame.center.toArray(), up: frame.up.toArray(), right: frame.right.toArray(), forward: frame.forward.toArray(), height: frame.height},
      semanticFrame: {
        center: frame.center.toArray(), lowHeight: frame.lowHeight,
        highHeight: frame.highHeight, height: frame.height,
        centerSide: frame.centerSide, centerDepth: frame.centerDepth,
        bodyDepth: frame.bodyDepth, bodyDepthSpread: frame.bodyDepthSpread,
      },
      scaffold: {
        centerline: scaffold.centerline,
        virtualChest: scaffold.virtualChest,
        virtualPelvis: scaffold.virtualPelvis,
        shoulderBand: scaffold.shoulderBand,
        hipBand: scaffold.hipBand,
        bodyDepth: scaffold.bodyDepth,
        lowHeight: frame.lowHeight,
        highHeight: frame.highHeight,
      },
      candidates: candidates.map(candidateSnapshot),
      topCandidatesByRole,
      landmarkCandidates,
      selectedRoles: Object.fromEntries(ROLES.map(role => [role, {
        available: !!roles[role]?.available,
        anchorJointId: roles[role]?.anchorJointId ?? null,
        bendJointId: roles[role]?.bendJointId ?? null,
        endJointId: roles[role]?.endJointId ?? null,
        pathJointIds: [...(roles[role]?.pathJointIds || [])],
      }])),
      rejected,
      rejectionCounts,
      validCandidateCounts: Object.fromEntries(ROLES.map(role =>
        [role, roleCandidates(role).length])),
      families: {
        left_arm: armChoice.leftFamilies.map(familySnapshot),
        right_arm: armChoice.rightFamilies.map(familySnapshot),
        left_leg: legChoice.leftFamilies.map(familySnapshot),
        right_leg: legChoice.rightFamilies.map(familySnapshot),
      },
      pairs: {arms: pairSnapshot(armChoice), legs: pairSnapshot(legChoice)},
      wholeBody: {
        bestScore: wholeBody.best?.wholeBodyScore || 0,
        coherence: wholeBody.best?.coherence || 0,
        combinationCount: wholeBody.combinations.length,
      },
      resolver: resolverStats,
      armPairCount: armChoice.pairs.length,
      legPairCount: legChoice.pairs.length,
    };
  }
  return result;
}

export function characterForwardForHumanoidOrientation(state) {
  return characterAxesFromOrientation(state)?.forward || new THREE.Vector3(0, 0, 1);
}
