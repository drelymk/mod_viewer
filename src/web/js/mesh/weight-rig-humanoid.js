/* Geometry-only humanoid limb suggestions for the inferred Model Rig. */

import * as THREE from 'three';
import {characterForwardFromOrientation, detectLimbPath} from './weight-rig-ik.js';

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

function pointFor(rig, id) {
  const value = valueFor(rig?.centerByJointId, id)
    ?? valueFor(rig?.jointPivotByJointId, id)
    ?? rig?.joints?.find(joint => Number(joint?.jointId) === Number(id))?.restCenter;
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

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function weightedMedian(samples, selector) {
  if (!samples.length) return 0;
  const ordered = [...samples].sort((left, right) =>
    selector(left.point) - selector(right.point));
  const total = ordered.reduce((sum, sample) => sum + sample.weight, 0);
  let accumulated = 0;
  for (const sample of ordered) {
    accumulated += sample.weight;
    if (accumulated >= total / 2) return selector(sample.point);
  }
  return selector(ordered.at(-1).point);
}

function quantile(values, fraction) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = (sorted.length - 1) * Math.max(0, Math.min(1, fraction));
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower);
}

function robustFrame(rig, characterForward) {
  const samples = (rig?.joints || []).map(joint => {
    const point = pointFor(rig, joint?.jointId);
    return point ? {point, weight: weightedValue(joint)} : null;
  }).filter(Boolean);
  const points = samples.map(sample => sample.point);
  const up = new THREE.Vector3(0, 1, 0);
  const forward = pointFor({centerByJointId: new Map([[0, characterForward]])}, 0)
    || new THREE.Vector3(0, 0, 1);
  forward.addScaledVector(up, -forward.dot(up));
  if (forward.lengthSq() <= EPSILON) forward.set(0, 0, 1);
  forward.normalize();
  const right = up.clone().cross(forward);
  if (right.lengthSq() <= EPSILON) right.set(1, 0, 0);
  right.normalize();
  const ys = points.map(point => point.y);
  let height = quantile(ys, .95) - quantile(ys, .05);
  if (!Number.isFinite(height) || height <= EPSILON) {
    const box = new THREE.Box3().setFromPoints(points);
    height = box.getSize(new THREE.Vector3()).length();
  }
  height = Math.max(height, EPSILON);
  const medianZ = median(points.map(point => point.z));
  const coreSamples = samples.filter(sample =>
    Math.abs(sample.point.z - medianZ) <= Math.max(height * .2, EPSILON));
  const center = new THREE.Vector3(
    weightedMedian(coreSamples, point => point.x),
    weightedMedian(samples, point => point.y),
    medianZ);
  return {center, up, forward, right, height};
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

function candidateFor(rig, frame, role, anchorJointId, characterForward) {
  const component = componentFor(rig, anchorJointId);
  const parentId = parentFor(component, anchorJointId);
  if (!component || parentId === null) return {rejected: 'anchor_is_component_root'};
  const detected = detectLimbPath({rig, anchorJointId, role, characterForward});
  if (!detected.available) return {rejected: detected.reason || 'path_unavailable', detected};
  const anchor = pointFor(rig, anchorJointId);
  const parent = pointFor(rig, parentId);
  const end = pointFor(rig, detected.endJointId);
  const bend = pointFor(rig, detected.bendJointId);
  if (!anchor || !parent || !end || !bend) return {rejected: 'pivot_invalid', detected};
  const sideSign = role.startsWith('left_') ? -1 : 1;
  const side = anchor.clone().sub(frame.center).dot(frame.right) / frame.height;
  const endSide = end.clone().sub(frame.center).dot(frame.right) / frame.height;
  const y = (anchor.y - frame.center.y) / frame.height;
  const endY = (end.y - frame.center.y) / frame.height;
  const parentSide = Math.abs(parent.clone().sub(frame.center).dot(frame.right) / frame.height);
  const forwardOffset = Math.abs(end.clone().sub(anchor).dot(frame.forward) / frame.height);
  const length = pathLength(rig, detected.pathJointIds) / frame.height;
  const anchorJoint = rig?.joints?.find(joint => Number(joint?.jointId) === Number(anchorJointId));
  const evidenceScore = clamp01(Math.log1p(weightedValue(anchorJoint)) / Math.log(1001));
  const sideAgreement = clamp01((side * sideSign - .04) / .35);
  const endSideAgreement = clamp01((endSide * sideSign - .02) / .4);
  const parentCentrality = clamp01(1 - Math.max(0, parentSide - Math.abs(side) * .7) / .35);
  const lengthScore = role.endsWith('_arm') ? rangeScore(length, .12, .8, .7)
    : rangeScore(length, .2, 1.2, .8);
  const levelScore = role.endsWith('_arm') ? rangeScore(y, .12, .65, .55)
    : rangeScore(y, -.5, .05, .45);
  const verticalScore = role.endsWith('_arm')
    ? rangeScore(y - endY, -.2, .65, .55)
    : rangeScore(y - endY, .25, 1.1, .65);
  const forwardScore = clamp01(1 - forwardOffset / .35);
  const anchorScore = clamp01((Math.abs(side) - .06) / .5);
  const score = .25 * sideAgreement + .12 * endSideAgreement
    + .14 * parentCentrality + .15 * lengthScore + .12 * levelScore
    + .1 * verticalScore + .1 * forwardScore + .04 * anchorScore
    + .03 * evidenceScore;
  const reasons = [];
  if (sideAgreement > .75) reasons.push('anchor_side');
  if (parentCentrality > .75) reasons.push('central_parent');
  if (lengthScore > .75) reasons.push('limb_length');
  if (verticalScore > .75) reasons.push(role.endsWith('_arm') ? 'arm_drop' : 'leg_drop');
  if (forwardScore < .35) reasons.push('forward_offset');
  if (evidenceScore > .65) reasons.push('evidence');
  if (Math.abs(side) < .08) return {rejected: 'anchor_too_central', detected};
  if (length < .08) return {rejected: 'limb_too_short', detected};
  if (forwardOffset > .3) return {rejected: 'forward_offset', detected};
  if (role.endsWith('_leg') && y > .18) {
    return {rejected: 'anchor_too_high_for_leg', detected};
  }
  if (role.endsWith('_arm') && y < -.05) {
    return {rejected: 'anchor_too_low_for_arm', detected};
  }
  return {
    role, available: true, anchorJointId: Number(anchorJointId),
    pathJointIds: [...detected.pathJointIds], bendJointId: detected.bendJointId,
    endJointId: detected.endJointId, score: clamp01(score),
    confidence: score >= .72 ? 'high' : score >= .5 ? 'medium' : 'low',
    pairScore: 0, runnerUpMargin: 0, reasons,
    bendDirection: detected.bendDirection ? [...detected.bendDirection] : null,
    bendDirectionSource: detected.bendDirectionSource,
  };
}

function candidateSort(left, right) {
  if (Math.abs(right.score - left.score) > .000001) return right.score - left.score;
  if (left.pathJointIds.length !== right.pathJointIds.length) {
    return right.pathJointIds.length - left.pathJointIds.length;
  }
  return 0;
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
    const symmetry = Math.abs(leftPoint.y - rightPoint.y) / frame.height
      + Math.abs(leftEnd.y - rightEnd.y) / frame.height
      + Math.abs(Math.abs(leftPoint.clone().sub(frame.center).dot(frame.right))
        - Math.abs(rightPoint.clone().sub(frame.center).dot(frame.right))) / frame.height;
    const pairScore = (first.score + second.score) / 2 - Math.min(.35, symmetry * .18);
    pairs.push({first, second, pairScore, symmetry});
  }
  return pairs.sort((a, b) => b.pairScore - a.pairScore);
}

function choosePair(rig, frame, candidates, roles) {
  const bySide = {left: [], right: []};
  candidates.forEach(candidate => {
    const side = candidate.role.startsWith('left_') ? 'left' : 'right';
    bySide[side].push(candidate);
  });
  const pairs = pairCandidates(rig, frame,
    bySide.left.filter(candidate => candidate.role === roles[0]),
    bySide.right.filter(candidate => candidate.role === roles[1]));
  if (!pairs.length) return {pair: null, margin: 0, pairs: []};
  const best = pairs[0];
  const margin = pairs.length > 1 ? best.pairScore - pairs[1].pairScore : 1;
  return {pair: best, margin, pairs};
}

function makePairSuggestions(choice, roles) {
  if (!choice.pair) return Object.fromEntries(roles.map(role => [role, emptySuggestion(role)]));
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

/** Suggest bilateral arm and leg mappings from ModelJoint geometry. */
export function suggestHumanoidLimbMappings({rig, characterForward, debug = false} = {}) {
  const frame = robustFrame(rig, characterForward);
  const rejected = [];
  const candidates = [];
  const joints = [...(rig?.joints || [])].sort((left, right) => Number(left?.jointId) - Number(right?.jointId));
  for (const joint of joints) {
    const id = numberId(joint?.jointId);
    if (id === null || !pointFor(rig, id)) continue;
    for (const role of ROLES) {
      const result = candidateFor(rig, frame, role, id, characterForward);
      if (result.rejected) {
        if (debug) rejected.push({role, anchorJointId: id, reason: result.rejected});
      } else candidates.push(result);
    }
  }
  const roleCandidates = role => candidates.filter(candidate => candidate.role === role).sort(candidateSort);
  const armChoice = choosePair(rig, frame, [...roleCandidates('left_arm'), ...roleCandidates('right_arm')], ARM_ROLES);
  const legChoice = choosePair(rig, frame, [...roleCandidates('left_leg'), ...roleCandidates('right_leg')], LEG_ROLES);
  let roles = {...makePairSuggestions(armChoice, ARM_ROLES), ...makePairSuggestions(legChoice, LEG_ROLES)};
  const arms = [roles.left_arm, roles.right_arm];
  const legs = [roles.left_leg, roles.right_leg];
  if (arms.every(item => item.available) && legs.every(item => item.available)) {
    const shoulderY = Math.min(...arms.map(item => pointFor(rig, item.anchorJointId)?.y ?? 0));
    const hipY = Math.max(...legs.map(item => pointFor(rig, item.anchorJointId)?.y ?? 0));
    if (shoulderY <= hipY) {
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
  if (debug) result.debug = {
    frame: {center: frame.center.toArray(), right: frame.right.toArray(), forward: frame.forward.toArray(), height: frame.height},
    candidates: candidates.map(candidate => ({...candidate, pathJointIds: [...candidate.pathJointIds]})),
    rejected,
    armPairCount: armChoice.pairs.length,
    legPairCount: legChoice.pairs.length,
  };
  return result;
}

export function characterForwardForHumanoidOrientation(state) {
  return characterForwardFromOrientation(state) || new THREE.Vector3(0, 0, 1);
}
