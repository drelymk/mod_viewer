/* Geometry-only humanoid limb suggestions for the inferred Model Rig. */

import * as THREE from 'three';
import {characterAxesFromOrientation, detectLimbPath} from './weight-rig-ik.js';

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

function pointFor(rig, id) {
  const joint = rig?.joints?.find(joint => Number(joint?.jointId) === Number(id));
  const value = valueFor(rig?.jointPivotByJointId, id)
    ?? joint?.restPivot
    ?? valueFor(rig?.centerByJointId, id)
    ?? joint?.restCenter;
  if (value?.isVector3) return value.clone();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 3).map(Number)
    : [value?.x, value?.y, value?.z].map(Number);
  return values.length === 3 && values.every(Number.isFinite)
    ? new THREE.Vector3(...values) : null;
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
    centerSide, centerDepth,
  };
}

/** Build one model-wide semantic frame for every humanoid detector consumer. */
export function buildHumanoidSemanticFrame({rig, axes, characterForward} = {}) {
  const semanticAxes = semanticAxesFor({axes, characterForward});
  return robustFrame(rig, semanticAxes);
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
  const samples = (rig?.joints || []).map(joint => {
    const point = pointFor(rig, joint?.jointId);
    if (!point) return null;
    const height01 = (point.dot(semanticFrame.up) - semanticFrame.lowHeight)
      / Math.max(semanticFrame.height, EPSILON);
    return {
      point, height01: clamp01(height01),
      side: (point.dot(semanticFrame.right) - semanticFrame.centerSide)
        / semanticFrame.height,
      depth: (point.dot(semanticFrame.forward) - semanticFrame.centerDepth)
        / semanticFrame.height,
      weight: 1 + Math.log1p(weightedValue(joint)),
    };
  }).filter(Boolean);
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
      expected: semanticFrame.centerDepth,
      spread: depthSpread,
      tolerance: Math.max(.18, depthSpread * 1.5),
    },
    centerlineAt,
    confidence,
  };
}

function pathLength(rig, path) {
  let total = 0;
  for (let index = 1; index < path.length; index += 1) {
    const previous = pointFor(rig, path[index - 1]);
    const current = pointFor(rig, path[index]);
    if (!previous || !current) return 0;
    total += previous.distanceTo(current);
  }
  return total;
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
    bendJointId: null, endJointId: null, score: 0, confidence: 'low',
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

function ancestryScore(rig, frame, role, anchor, parent, component) {
  const ids = ancestorIds(component, numberId(anchor));
  const anchorSide = Math.abs(anchor.clone().sub(frame.center).dot(frame.right)
    / frame.height);
  const anchorDepth = Math.abs(anchor.clone().sub(frame.center).dot(frame.forward)
    / frame.height);
  const samples = [parent, ...ids.slice(1)].map(point => ({
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

function candidateFor(rig, frame, scaffold, role, anchorJointId, characterForward) {
  const component = componentFor(rig, anchorJointId);
  const parentId = parentFor(component, anchorJointId);
  if (!component || parentId === null) return {rejected: 'anchor_is_component_root'};
  const anchor = pointFor(rig, anchorJointId);
  const parent = pointFor(rig, parentId);
  if (!anchor || !parent) return {rejected: 'pivot_invalid'};
  const sideSign = role.startsWith('left_') ? -1 : 1;
  const side = (anchor.dot(frame.right) - frame.centerSide) / frame.height;
  const height = clamp01((anchor.dot(frame.up) - frame.lowHeight) / frame.height);
  if (side * sideSign < -.12) return {rejected: 'wrong_side'};
  const anchorBand = role.endsWith('_arm') ? scaffold.shoulderBand : scaffold.hipBand;
  const bandMargin = role.endsWith('_arm') ? .08 : .18;
  if (height < anchorBand.minHeight01 - bandMargin
      || height > anchorBand.maxHeight01 + bandMargin) {
    return {rejected: role.endsWith('_arm')
      ? 'anchor_too_low_for_arm' : 'anchor_too_high_for_leg'};
  }
  const detected = detectLimbPath({
    rig, anchorJointId, role, characterForward, characterAxes: frame,
    semanticFrame: frame,
  });
  if (!detected.available) return {rejected: detected.reason || 'path_unavailable', detected};
  const end = pointFor(rig, detected.endJointId);
  const bend = pointFor(rig, detected.bendJointId);
  if (!end || !bend) return {rejected: 'pivot_invalid', detected};
  const endSide = (end.dot(frame.right) - frame.centerSide) / frame.height;
  const endHeight = clamp01((end.dot(frame.up) - frame.lowHeight) / frame.height);
  const forwardOffset = Math.abs(end.clone().sub(anchor).dot(frame.forward) / frame.height);
  const length = pathLength(rig, detected.pathJointIds) / frame.height;
  const ancestry = ancestryScore(rig, frame, role, anchor, parent, component);
  const componentScore = componentBodyScore(rig, frame, component);
  const sideAgreement = clamp01((side * sideSign - .04) / .35);
  const endSideAgreement = clamp01((endSide * sideSign - .02) / .4);
  const parentSide = Math.abs(parent.clone().sub(frame.center).dot(frame.right)
    / frame.height);
  const originDeparture = clamp01((Math.abs(side) - parentSide) / .12);
  const parentCentrality = clamp01(1 - Math.abs(parent.clone().sub(frame.center)
    .dot(frame.right) - frame.centerSide) / frame.height
    / Math.max(Math.abs(side), .08));
  const metrics = detected.pathMetrics || {};
  const pathGeometryScore = clamp01(detected.pathScore);
  const continuationAgreement = clamp01(detected.continuationAgreement);
  const lengthScore = role.endsWith('_arm') ? rangeScore(length, .12, 1.5, .7)
    : rangeScore(length, .2, 1.7, .8);
  const band = role.endsWith('_arm') ? scaffold.shoulderBand : scaffold.hipBand;
  const bandScore = rangeScore(height, band.minHeight01, band.maxHeight01, .24);
  const originHeightScore = clamp01(1 - Math.abs(height - band.expectedHeight01) / .35);
  const centerline = scaffold.centerlineAt(height);
  const centerlineScore = clamp01(1 - Math.abs(side - centerline.side) / .75);
  const anchorPositionScore = role.endsWith('_arm')
    ? clamp01(.38 * sideAgreement + .3 * bandScore
      + .2 * parentCentrality + .12 * centerlineScore)
    : clamp01(.25 * sideAgreement + .23 * bandScore
      + .25 * originHeightScore + .25 * parentCentrality
      + .02 * centerlineScore + .25 * originDeparture);
  const verticalScore = role.endsWith('_arm')
    ? rangeScore(endHeight, .28, .92, .45)
    : rangeScore(endHeight, 0, .24, .28);
  const forwardScore = clamp01(1 - forwardOffset / .45);
  const endpointPositionScore = role.endsWith('_arm')
    ? clamp01(.34 * endSideAgreement + .22 * verticalScore
      + .24 * forwardScore + .2 * (metrics.poseAngleScore || 0))
    : clamp01(.45 * (metrics.endpointBottomScore || 0)
      + .3 * verticalScore + .15 * forwardScore
      + .1 * (metrics.verticalCoverage || 0));
  const pathSupportScore = clamp01(.45 * (metrics.pathSupportScore || 0)
    + .25 * (metrics.anchorSupport || 0)
    + .15 * (metrics.proximalSupport || 0)
    + .15 * (metrics.medianPathSupport || 0));
  const branchDominanceScore = clamp01(metrics.branchDominance || 0);
  const corridorScore = clamp01(metrics.corridorScore || pathGeometryScore);
  const poseAngleScore = clamp01(metrics.poseAngleScore || 0);
  const armProgressPenalty = role.endsWith('_arm')
    ? .42 * clamp01((.22 - Number(metrics.outwardDisplacement || 0)) / .22)
    : 0;
  const score = role.endsWith('_arm')
    ? clamp01(.27 * corridorScore + .09 * poseAngleScore
      + .14 * anchorPositionScore + .1 * endpointPositionScore
      + .18 * ancestry.score + .1 * pathSupportScore
      + .07 * branchDominanceScore + .05 * componentScore
      + .05 * continuationAgreement + .05 * lengthScore - armProgressPenalty)
    : clamp01(.28 * corridorScore + .1 * poseAngleScore
      + .14 * anchorPositionScore + .12 * endpointPositionScore
      + .18 * ancestry.score + .1 * pathSupportScore
      + .07 * branchDominanceScore + .05 * componentScore
      + .04 * continuationAgreement + .04 * lengthScore);
  const reasons = [];
  if (sideAgreement > .75) reasons.push('anchor_side');
  if (parentCentrality > .75) reasons.push('central_parent');
  if (lengthScore > .75) reasons.push('limb_length');
  if (verticalScore > .75) reasons.push(role.endsWith('_arm') ? 'arm_drop' : 'leg_drop');
  if (forwardScore < .35) reasons.push('depth_excessive');
  if (pathSupportScore > .65) reasons.push('evidence');
  if (branchDominanceScore > .65) reasons.push('primary_branch');
  if (componentScore > .65) reasons.push('body_component');
  if (length < .08) return {rejected: 'path_too_short', detected};
  if (role.endsWith('_arm') && (Number(metrics.outwardDisplacement) < .1
      || (Number(metrics.sideFraction) < .18 && Number(metrics.downwardDisplacement) > .35))) {
    return {rejected: 'insufficient_arm_progression', detected};
  }
  if (role.endsWith('_leg') && (-metrics.netHeight < .08
      || (Number(metrics.downwardProgressFraction) < .35 && -metrics.netHeight < .2))) {
    return {rejected: 'insufficient_leg_drop', detected};
  }
  return {
    role, available: true, anchorJointId: Number(anchorJointId),
    pathJointIds: [...detected.pathJointIds], bendJointId: detected.bendJointId,
    endJointId: detected.endJointId, score: clamp01(score),
    confidence: score >= .62 ? 'high' : score >= .42 ? 'medium' : 'low',
    pairScore: 0, runnerUpMargin: 0, reasons,
    bendDirection: detected.bendDirection ? [...detected.bendDirection] : null,
    bendDirectionSource: detected.bendDirectionSource,
    parentJointId: parentId,
    parentCentrality,
    ancestorJointIds: ancestry.ids,
    ancestryScore: ancestry.score,
    componentId: valueFor(rig?.componentByJointId, anchorJointId),
    pathLength: length,
    pathGeometryScore,
    corridorScore,
    poseAngleScore,
    anchorPositionScore,
    endpointPositionScore,
    anchorScore: anchorPositionScore,
    endpointScore: endpointPositionScore,
    pathSupportScore,
    proximalSupport: metrics.proximalSupport,
    branchDominanceScore,
    componentBodyScore: componentScore,
    continuationAgreement,
    pathMetrics: metrics,
    paths: detected.pathCandidates || [],
    pathDiagnostics: detected.pathDiagnostics || null,
    side,
    endSide,
    normalizedHeight: height,
    normalizedEndHeight: endHeight,
    forwardOffset,
    familyId: null,
    alternatives: [],
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
  if (Math.abs(left.parentCentrality - right.parentCentrality) > .000001) {
    return right.parentCentrality - left.parentCentrality;
  }
  if (left.pathJointIds.length !== right.pathJointIds.length) {
    return right.pathJointIds.length - left.pathJointIds.length;
  }
  if (Math.abs(left.score - right.score) > .000001) return right.score - left.score;
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
    // A missing or weak limb on one side must not erase a clearly supported
    // counterpart. Keep both sides unmapped only when they each have
    // candidates but no compatible bilateral pair.
    const canMapPartially = choice.leftFamilies.length === 0
      || choice.rightFamilies.length === 0;
    if (!canMapPartially) {
      return Object.fromEntries(roles.map(role => [role, emptySuggestion(role)]));
    }
    return Object.fromEntries(roles.map(role => {
      const families = role.startsWith('left_')
        ? choice.leftFamilies : choice.rightFamilies;
      const candidate = families[0]?.representative;
      if (!candidate) return [role, emptySuggestion(role)];
      return [role, {
        ...candidate, pairScore: 0, runnerUpMargin: 0,
        available: candidate.confidence !== 'low',
        reasons: candidate.confidence === 'low'
          ? [...candidate.reasons, 'low_confidence'] : candidate.reasons,
      }];
    }));
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
    pathJointIds: [...candidate.pathJointIds],
    ancestorJointIds: [...(candidate.ancestorJointIds || [])],
    componentId: candidate.componentId ?? null,
    familyId: candidate.familyId,
    score: candidate.score,
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
    continuationAgreement: candidate.continuationAgreement,
    pathMetrics: candidate.pathMetrics || null,
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
  const semanticAxes = {up: frame.up, right: frame.right, forward: frame.forward};
  const scaffold = buildHumanoidScaffold({rig, frame});
  const rejected = [];
  const candidates = [];
  const resolverStats = {
    anchorsEvaluated: 0, pathExpansions: 0, maxBeamSize: 0,
    pathCandidatesProduced: 0, maxDepth: 0, beamWidth: 0,
  };
  const joints = [...(rig?.joints || [])].sort((left, right) => Number(left?.jointId) - Number(right?.jointId));
  for (const joint of joints) {
    const id = numberId(joint?.jointId);
    if (id === null || !pointFor(rig, id)) continue;
    for (const role of ROLES) {
      resolverStats.anchorsEvaluated += 1;
      const result = candidateFor(rig, frame, scaffold, role, id, semanticAxes.forward);
      const stats = result.detected?.pathDiagnostics;
      if (stats) {
        resolverStats.pathExpansions += stats.pathExpansions || 0;
        resolverStats.maxBeamSize = Math.max(resolverStats.maxBeamSize,
          stats.maxBeamSize || 0);
        resolverStats.pathCandidatesProduced += stats.pathCandidatesProduced || 0;
        resolverStats.maxDepth = Math.max(resolverStats.maxDepth, stats.maxDepth || 0);
        resolverStats.beamWidth = Math.max(resolverStats.beamWidth, stats.beamWidth || 0);
      }
      if (result.rejected) {
        if (debug) rejected.push({role, anchorJointId: id, reason: result.rejected});
      } else candidates.push(result);
    }
  }
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
      },
      scaffold: {
        centerline: scaffold.centerline,
        shoulderBand: scaffold.shoulderBand,
        hipBand: scaffold.hipBand,
        bodyDepth: scaffold.bodyDepth,
        lowHeight: frame.lowHeight,
        highHeight: frame.highHeight,
      },
      candidates: candidates.map(candidateSnapshot),
      topCandidatesByRole,
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
