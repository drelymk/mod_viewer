// ModelJoint-native character limb IK. Detection may retain helper joints in
// a path, but the solver is deliberately limited to an anchor and bend.

import * as THREE from 'three';
import {buildForestTransformsFromLocalRotations} from './weight-deformation.js';

const EPSILON = 1e-8;
const MAX_PATH_DEPTH = 64;
const MAX_SOLVE_PASSES = 2;
const TOLERANCE_SCALE = 1e-4;
export const LIMB_BEND_RATIO_THRESHOLD = 0.025;

export const RIG_LIMB_ROLES = Object.freeze([
  'left_arm', 'right_arm', 'left_leg', 'right_leg',
]);

export const RIG_LIMB_ROLE_INFO = Object.freeze({
  left_arm: Object.freeze({label: 'Left Arm', anchor: 'Shoulder', bend: 'Elbow', end: 'Hand'}),
  right_arm: Object.freeze({label: 'Right Arm', anchor: 'Shoulder', bend: 'Elbow', end: 'Hand'}),
  left_leg: Object.freeze({label: 'Left Leg', anchor: 'Hip', bend: 'Knee', end: 'Foot'}),
  right_leg: Object.freeze({label: 'Right Leg', anchor: 'Hip', bend: 'Knee', end: 'Foot'}),
});

function valueFor(collection, id) {
  if (collection instanceof Map) {
    return collection.get(id) ?? collection.get(String(id));
  }
  return collection?.[id] ?? collection?.[String(id)];
}

function numberId(value) {
  if (value === null || value === undefined || value === '') return null;
  const id = Number(value);
  return Number.isInteger(id) ? id : null;
}

function finiteVector(value) {
  if (value?.isVector3) {
    const result = value.clone();
    return result.toArray().every(Number.isFinite) ? result : null;
  }
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 3).map(Number)
    : [value?.x, value?.y, value?.z].map(Number);
  if (values.length !== 3 || !values.every(Number.isFinite)) return null;
  return new THREE.Vector3(...values);
}

function quaternionFrom(value) {
  if (value?.isQuaternion) {
    const result = value.clone();
    return Number.isFinite(result.lengthSq()) && result.lengthSq() > EPSILON
      ? result.normalize() : new THREE.Quaternion();
  }
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? [...value].slice(0, 4).map(Number)
    : [value?.x, value?.y, value?.z, value?.w].map(Number);
  if (values.length !== 4 || !values.every(Number.isFinite)) {
    return new THREE.Quaternion();
  }
  const result = new THREE.Quaternion(...values);
  return Number.isFinite(result.lengthSq()) && result.lengthSq() > EPSILON
    ? result.normalize() : new THREE.Quaternion();
}

function parentFor(component, id) {
  const parent = valueFor(component?.parentById, id);
  if (parent === null || parent === undefined || parent === '') return null;
  return numberId(parent);
}

function childrenFor(component, id) {
  return (valueFor(component?.childrenById, id) || [])
    .map(numberId).filter(Number.isInteger);
}

function componentFor(rig, id) {
  const componentId = valueFor(rig?.componentByJointId, id)
    ?? valueFor(rig?.inferredForest?.componentByBoneId, id);
  const components = rig?.components || rig?.inferredForest?.components || [];
  return Number.isInteger(Number(componentId))
    ? components[Number(componentId)] || null : null;
}

function jointFor(rig, id) {
  return (rig?.joints || []).find(joint => Number(joint?.jointId) === id)
    || null;
}

function pointFor(rig, id) {
  return finiteVector(valueFor(rig?.jointPivotByJointId, id))
    || finiteVector(jointFor(rig, id)?.restPivot)
    || finiteVector(valueFor(rig?.centerByJointId, id))
    || finiteVector(jointFor(rig, id)?.restCenter);
}

function edgeFor(component, parentId, childId) {
  return (component?.edges || []).find(edge => {
    const left = Number(edge?.jointA ?? edge?.boneA);
    const right = Number(edge?.jointB ?? edge?.boneB);
    return (left === parentId && right === childId)
      || (left === childId && right === parentId);
  }) || null;
}

function edgeSupport(component, parentId, childId) {
  const edge = edgeFor(component, parentId, childId);
  const score = Number(edge?.combinedTreeScore ?? edge?.treeEdgeScore
    ?? edge?.score ?? edge?.attachmentScore ?? edge?.jaccard ?? 0);
  return Number.isFinite(score) ? score : 0;
}

function continuationFor(rig, id) {
  return numberId(valueFor(
    rig?.restContinuationChildByJointId
      || rig?.continuationChildByJointId,
    id));
}

function restFrameFor(rig, id) {
  const value = valueFor(rig?.restFrameByJointId, id)
    || jointFor(rig, id)?.restFrame;
  return quaternionFrom(value);
}

function projectedDirection(vector, axis) {
  const result = vector.clone().addScaledVector(axis, -vector.dot(axis));
  return result.lengthSq() > EPSILON ? result.normalize() : null;
}

export function resolveLimbBendDirection({
  rig, pathJointIds = [], anchorJointId, bendJointId, endJointId, role,
  characterForward,
} = {}) {
  const path = pathJointIds.map(numberId).filter(Number.isInteger);
  const anchorId = numberId(anchorJointId ?? path[0]);
  const endId = numberId(endJointId ?? path.at(-1));
  const bendId = numberId(bendJointId);
  const anchor = pointFor(rig, anchorId);
  const bend = pointFor(rig, bendId);
  const end = pointFor(rig, endId);
  if (!anchor || !bend || !end) return {
    bendDirection: null,
    bendDirectionSource: 'unavailable',
    bendDirectionStrength: 0,
  };
  const axis = end.clone().sub(anchor);
  if (axis.lengthSq() > EPSILON) {
    const limbLength = axis.length();
    axis.normalize();
    const bendOffset = bend.clone().sub(anchor)
      .addScaledVector(axis, -bend.clone().sub(anchor).dot(axis));
    const bendOffsetLength = bendOffset.length();
    const bendRatio = limbLength > EPSILON
      ? bendOffsetLength / limbLength : 0;
    if (bendRatio >= LIMB_BEND_RATIO_THRESHOLD) {
      return {
        bendDirection: bendOffset.normalize(),
        bendDirectionSource: 'rest-offset',
        bendDirectionStrength: bendRatio,
      };
    }

    const semantic = finiteVector(characterForward);
    if (semantic && semantic.lengthSq() > EPSILON) {
      const direction = semantic.normalize();
      if (role?.endsWith('_leg')) direction.negate();
      const projected = projectedDirection(direction, axis);
      if (projected) return {
        bendDirection: projected,
        bendDirectionSource: 'semantic-character-facing',
        bendDirectionStrength: bendRatio,
      };
    }

    const frame = restFrameFor(rig, bendId);
    const candidates = [
      new THREE.Vector3(1, 0, 0).applyQuaternion(frame),
      new THREE.Vector3(0, 0, 1).applyQuaternion(frame),
      new THREE.Vector3(0, 1, 0),
    ];
    const fallback = candidates.map(candidate =>
      projectedDirection(candidate, axis)).find(Boolean);
    if (fallback) return {
      bendDirection: fallback,
      bendDirectionSource: 'rest-frame',
      bendDirectionStrength: bendRatio,
    };

    return {
      bendDirection: null,
      bendDirectionSource: 'ambiguous-straight',
      bendDirectionStrength: bendRatio,
    };
  }

  const frame = restFrameFor(rig, bendId);
  const candidates = [
    new THREE.Vector3(1, 0, 0).applyQuaternion(frame),
    new THREE.Vector3(0, 0, 1).applyQuaternion(frame),
    new THREE.Vector3(0, 1, 0),
  ];
  const fallbackAxis = end.clone().sub(anchor);
  if (fallbackAxis.lengthSq() <= EPSILON) fallbackAxis.set(0, 1, 0);
  fallbackAxis.normalize();
  const direction = candidates.map(candidate =>
    projectedDirection(candidate, fallbackAxis)).find(Boolean)
    || new THREE.Vector3(1, 0, 0);
  return {
    bendDirection: direction,
    bendDirectionSource: 'rest-frame',
    bendDirectionStrength: 0,
  };
}

function confidenceFor(path, reason, branchHub) {
  if (path.length < 3) return 'low';
  if (branchHub && path.length >= 4) return 'high';
  if (path.length >= 4 && !reason) return 'high';
  return 'medium';
}

export function selectLimbBendJoint(rig, pathJointIds = []) {
  if (pathJointIds.length < 3) return null;
  const distances = [0];
  for (let index = 1; index < pathJointIds.length; index += 1) {
    const first = pointFor(rig, pathJointIds[index - 1]);
    const second = pointFor(rig, pathJointIds[index]);
    if (!first || !second) return null;
    distances.push(distances.at(-1) + first.distanceTo(second));
  }
  const total = distances.at(-1);
  if (!Number.isFinite(total) || total <= EPSILON) return null;
  const middle = total / 2;
  let best = null;
  for (let index = 1; index < pathJointIds.length - 1; index += 1) {
    const candidate = {
      jointId: pathJointIds[index],
      distanceFromMiddle: Math.abs(distances[index] - middle),
      edgePenalty: Math.min(distances[index], total - distances[index]),
      support: edgeSupport(componentFor(rig, pathJointIds[index]),
        pathJointIds[index - 1], pathJointIds[index]),
    };
    const closer = !best || candidate.distanceFromMiddle
      < best.distanceFromMiddle - EPSILON;
    const tie = best && Math.abs(candidate.distanceFromMiddle
      - best.distanceFromMiddle) <= EPSILON;
    if (closer || tie && candidate.edgePenalty > best.edgePenalty + EPSILON
        || tie && Math.abs(candidate.edgePenalty - best.edgePenalty) <= EPSILON
          && candidate.support > best.support + EPSILON
        || tie && Math.abs(candidate.edgePenalty - best.edgePenalty) <= EPSILON
          && Math.abs(candidate.support - best.support) <= EPSILON
          && candidate.jointId < best.jointId) best = candidate;
  }
  return best?.jointId ?? null;
}

function detectionResult(role, anchorJointId, pathJointIds, reason,
    branchHub, rig, characterForward, pathCandidate = null) {
  const bendJointId = selectLimbBendJoint(rig, pathJointIds);
  const endJointId = pathJointIds.at(-1) ?? null;
  const component = componentFor(rig, anchorJointId);
  const available = pathJointIds.length >= 3
    && Number.isInteger(bendJointId)
    && Number.isInteger(endJointId)
    && bendJointId !== anchorJointId
    && endJointId !== anchorJointId
    && Number(component?.rootId) !== anchorJointId;
  const finalReason = available ? null : reason || 'short_or_invalid_limb_path';
  const directionEvidence = available
    ? resolveLimbBendDirection({
      rig, pathJointIds, anchorJointId, bendJointId, endJointId, role,
      characterForward,
    })
    : {bendDirection: null, bendDirectionSource: 'unavailable',
      bendDirectionStrength: 0};
  return {
    available,
    role: RIG_LIMB_ROLES.includes(role) ? role : null,
    anchorJointId,
    pathJointIds: [...pathJointIds],
    bendJointId: available ? bendJointId : null,
    endJointId: available ? endJointId : null,
    confidence: available ? confidenceFor(pathJointIds, reason, branchHub) : 'low',
    reason: finalReason,
    bendDirection: directionEvidence.bendDirection?.toArray() || null,
    bendDirectionSource: directionEvidence.bendDirectionSource,
    bendDirectionStrength: directionEvidence.bendDirectionStrength,
    pathScore: Number(pathCandidate?.score) || 0,
    pathMetrics: pathCandidate?.pathMetrics || null,
    continuationAgreement: Number(pathCandidate?.continuationAgreement) || 0,
    pathCandidates: pathCandidate?.pathCandidates || [],
    pathDiagnostics: pathCandidate?.pathDiagnostics || null,
  };
}

function vectorForAxis(value) {
  const vector = finiteVector(value);
  return vector && vector.lengthSq() > EPSILON ? vector.normalize() : null;
}

function semanticAxesForPath({characterAxes, characterForward} = {}) {
  const up = vectorForAxis(characterAxes?.up) || new THREE.Vector3(0, 1, 0);
  let forward = vectorForAxis(characterAxes?.forward)
    || vectorForAxis(characterForward) || new THREE.Vector3(0, 0, 1);
  forward.addScaledVector(up, -forward.dot(up));
  if (forward.lengthSq() <= EPSILON) forward = new THREE.Vector3(0, 0, 1);
  forward.normalize();
  let right = vectorForAxis(characterAxes?.right);
  if (!right || Math.abs(right.dot(up)) > 1e-4
      || Math.abs(right.dot(forward)) > 1e-4) right = up.clone().cross(forward);
  if (right.lengthSq() <= EPSILON) right.set(1, 0, 0);
  right.normalize();
  return {up, forward, right};
}

function medianValue(values) {
  if (!values.length) return 0;
  const ordered = [...values].sort((left, right) => left - right);
  return ordered[Math.floor((ordered.length - 1) / 2)];
}

function clamp01(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}

function rangeScore(value, low, high, softness = .15) {
  if (value >= low && value <= high) return 1;
  const distance = value < low ? low - value : value - high;
  return clamp01(1 - distance / Math.max(softness, EPSILON));
}

function quantileValue(values, fraction) {
  if (!values.length) return 0;
  const ordered = [...values].sort((left, right) => left - right);
  const index = Math.max(0, Math.min(ordered.length - 1,
    Math.round((ordered.length - 1) * clamp01(fraction))));
  return ordered[index];
}

function globalResolverFrame(rig, axes) {
  const ids = new Set();
  (rig?.joints || []).forEach(joint => {
    const id = numberId(joint?.jointId);
    if (id !== null) ids.add(id);
  });
  (rig?.components || rig?.inferredForest?.components || []).forEach(component =>
    (component?.nodeIds || []).forEach(id => {
      const jointId = numberId(id);
      if (jointId !== null) ids.add(jointId);
    }));
  const points = [...ids].sort((left, right) => left - right)
    .map(id => pointFor(rig, id)).filter(Boolean);
  const coordinates = points.map(point => ({
    side: point.dot(axes.right), height: point.dot(axes.up), depth: point.dot(axes.forward),
  }));
  const heights = coordinates.map(item => item.height);
  if (!coordinates.length) {
    return {
      center: new THREE.Vector3(), up: axes.up, forward: axes.forward,
      right: axes.right, lowHeight: -.5, highHeight: .5, height: 1,
      centerSide: 0, centerDepth: 0,
    };
  }
  const lowHeight = quantileValue(heights, .05);
  const highHeight = quantileValue(heights, .95);
  const height = Math.max(highHeight - lowHeight,
    Math.max(...heights) - Math.min(...heights), EPSILON);
  const centerSide = medianValue(coordinates.map(item => item.side));
  const centerHeight = medianValue(coordinates.map(item => item.height));
  const centerDepth = medianValue(coordinates.map(item => item.depth));
  const center = axes.right.clone().multiplyScalar(centerSide)
    .addScaledVector(axes.up, centerHeight)
    .addScaledVector(axes.forward, centerDepth);
  return {
    center, up: axes.up, forward: axes.forward, right: axes.right,
    lowHeight, highHeight, height, centerSide, centerDepth,
  };
}

function resolverFrame(rig, axes, semanticFrame = null) {
  if (semanticFrame?.center && Number.isFinite(Number(semanticFrame.height))) {
    const center = finiteVector(semanticFrame.center);
    const lowHeight = Number(semanticFrame.lowHeight);
    const highHeight = Number(semanticFrame.highHeight);
    const height = Number(semanticFrame.height);
    if (center && Number.isFinite(lowHeight) && Number.isFinite(highHeight)
        && Number.isFinite(height) && height > EPSILON) {
      return {
        center, up: axes.up, forward: axes.forward, right: axes.right,
        lowHeight, highHeight, height,
        centerSide: Number(semanticFrame.centerSide) || 0,
        centerDepth: Number(semanticFrame.centerDepth) || 0,
      };
    }
  }
  return globalResolverFrame(rig, axes);
}

function supportValue(rig, id) {
  const joint = jointFor(rig, id);
  const evidence = joint?.evidence || joint || {};
  const count = Number(evidence.affectedVertexCount);
  const weight = Number(evidence.totalWeight);
  const raw = Math.max(Number.isFinite(count) && count > 0 ? count : 0,
    Number.isFinite(weight) && weight > 0 ? weight * 100 : 0, 1);
  return Math.log1p(raw);
}

function supportScale(rig) {
  const values = (rig?.joints || []).map(joint =>
    supportValue(rig, joint?.jointId));
  return Math.max(...values, 1);
}

function corridorDiagnostics(state, rig, frame, axes, role) {
  const isArm = String(role || '').endsWith('_arm');
  const sideSign = String(role || '').startsWith('left_') ? -1 : 1;
  const anchor = state.points[0] || new THREE.Vector3();
  const distances = [];
  let inside = 0;
  let depthTotal = 0;
  let lateralTotal = 0;
  let downwardTotal = 0;
  for (const point of state.points) {
    const relative = point.clone().sub(anchor);
    const outward = relative.dot(axes.right) * sideSign / frame.height;
    const downward = -relative.dot(axes.up) / frame.height;
    const depth = Math.abs(relative.dot(axes.forward)) / frame.height;
    let distance;
    if (isArm) {
      // A broad wedge around the outward/downward plane accepts T-pose through
      // steep A-pose geometry, while continuously penalizing depth excursions.
      const expectedDown = Math.max(-.08, outward * .38);
      distance = Math.max(0, expectedDown - downward - .2)
        + Math.max(0, -outward - .08) * .8
        + Math.max(0, Math.abs(downward - expectedDown) - .2) * .45
        + Math.max(0, depth - .28) * .9;
    } else {
      const allowedSide = .08 + Math.max(0, downward) * .16;
      distance = Math.max(0, Math.abs(relative.dot(axes.right)) / frame.height
        - allowedSide) * .9
        + Math.max(0, depth - .22) * .9
        + Math.max(0, -downward - .06) * .75;
    }
    distances.push(distance);
    if (distance <= .24) inside += 1;
    depthTotal += depth;
    lateralTotal += Math.abs(relative.dot(axes.right)) / frame.height;
    downwardTotal += Math.max(0, downward);
  }
  const endpoint = state.points.at(-1) || anchor;
  const endpointRelative = endpoint.clone().sub(frame.center);
  const endpointHeight01 = (endpoint.dot(frame.up) - frame.lowHeight) / frame.height;
  const anchorHeight01 = (anchor.dot(frame.up) - frame.lowHeight) / frame.height;
  const netSide = endpoint.clone().sub(anchor).dot(axes.right) / frame.height;
  const netHeight = endpoint.clone().sub(anchor).dot(axes.up) / frame.height;
  const netForward = endpoint.clone().sub(anchor).dot(axes.forward) / frame.height;
  const outwardDisplacement = netSide * sideSign;
  const downwardDisplacement = -netHeight;
  const forwardDisplacement = netForward;
  const planarTravel = Math.max(Math.abs(outwardDisplacement)
    + Math.max(0, downwardDisplacement), EPSILON);
  const dropFraction = clamp01(downwardDisplacement / planarTravel);
  const sideFraction = clamp01(outwardDisplacement / planarTravel);
  const depthFraction = clamp01(Math.abs(forwardDisplacement)
    / Math.max(Math.abs(outwardDisplacement) + downwardDisplacement, EPSILON));
  const sideDownAngle = Math.atan2(Math.max(0, downwardDisplacement),
    Math.max(Math.abs(outwardDisplacement), EPSILON));
  const meanCorridorDistance = distances.reduce((sum, value) => sum + value, 0)
    / Math.max(distances.length, 1);
  const maxCorridorDistance = Math.max(...distances, 0);
  const insideCorridorFraction = inside / Math.max(state.points.length, 1);
  const depthDeviation = depthTotal / Math.max(state.points.length, 1);
  const verticalCoverage = clamp01((anchorHeight01 - endpointHeight01) / .7);
  const endpointBottomScore = isArm
    ? rangeScore(endpointHeight01, .32, .92, .5)
    : rangeScore(endpointHeight01, 0, .22, .3);
  const corridorScore = isArm
    ? clamp01(.48 * insideCorridorFraction
      + .3 * (1 - clamp01(meanCorridorDistance / .65))
      + .22 * (1 - clamp01(depthDeviation / .45)))
    : clamp01(.5 * insideCorridorFraction
      + .26 * (1 - clamp01(meanCorridorDistance / .5))
      + .16 * verticalCoverage + .08 * endpointBottomScore);
  const poseAngleScore = isArm
    ? clamp01(.38 * clamp01(outwardDisplacement / .18)
      + .32 * rangeScore(dropFraction, .03, .72, .35)
      + .18 * (1 - clamp01(depthFraction / .55))
      + .12 * (1 - clamp01(Math.max(0, -outwardDisplacement) / .18)))
    : clamp01(.55 * clamp01(downwardDisplacement / .35)
      + .25 * (1 - clamp01(Math.abs(outwardDisplacement) / .35))
      + .2 * (1 - clamp01(depthDeviation / .45)));
  const supports = state.pathJointIds.map(id => supportValue(rig, id));
  const sortedSupports = [...supports].sort((left, right) => left - right);
  const medianSupport = sortedSupports[Math.floor((sortedSupports.length - 1) / 2)] || 0;
  const proximalCount = Math.max(1, Math.ceil(supports.length / 3));
  const proximalSupport = supports.slice(0, proximalCount)
    .reduce((sum, value) => sum + value, 0) / proximalCount;
  const scale = supportScale(rig);
  const pathSupportScore = clamp01(medianSupport / Math.max(scale, EPSILON));
  return {
    meanCorridorDistance,
    maxCorridorDistance,
    insideCorridorFraction,
    corridorScore,
    poseAngleScore,
    armPoseScore: isArm ? poseAngleScore : 0,
    sideFraction,
    dropFraction,
    depthFraction,
    sideDownAngle,
    outwardDisplacement,
    downwardDisplacement,
    forwardDisplacement,
    lateralDeviation: lateralTotal / Math.max(state.points.length, 1),
    depthDeviation,
    endpointHeight01,
    endpointDepth: endpointRelative.dot(axes.forward) / frame.height,
    verticalCoverage,
    endpointBottomScore,
    pathSupportScore,
    anchorSupport: supports[0] / Math.max(scale, EPSILON),
    proximalSupport: proximalSupport / Math.max(scale, EPSILON),
    medianPathSupport: medianSupport / Math.max(scale, EPSILON),
    distalSupport: (supports.at(-1) || 0) / Math.max(scale, EPSILON),
    branchDominance: state.branchDominanceCount
      ? state.branchDominanceSum / state.branchDominanceCount : 1,
  };
}

function pathMetricsForState(state, rig, frame, axes, role) {
  const path = state.pathJointIds;
  const endpoint = pointFor(rig, path.at(-1));
  const relative = endpoint ? endpoint.clone().sub(frame.center) : new THREE.Vector3();
  const sideSign = String(role || '').startsWith('left_') ? -1 : 1;
  const absoluteSide = state.absoluteSideTravel;
  const absoluteHeight = state.absoluteHeightTravel;
  const absoluteForward = state.absoluteForwardTravel;
  const outwardTravel = state.outwardTravel;
  const downwardTravel = state.downwardTravel;
  const directDistance = state.points.length > 1
    ? state.points[0].distanceTo(state.points.at(-1)) : 0;
  const corridor = corridorDiagnostics(state, rig, frame, axes, role);
  const metrics = {
    totalLength: state.totalLength / frame.height,
    directDistance: directDistance / frame.height,
    straightness: state.totalLength > EPSILON
      ? clamp01(directDistance / state.totalLength) : 0,
    netSide: state.netSide / frame.height,
    netHeight: state.netHeight / frame.height,
    netForward: state.netForward / frame.height,
    netSideDisplacement: state.netSide / frame.height,
    netHeightDisplacement: state.netHeight / frame.height,
    absoluteForwardTravel: absoluteForward / frame.height,
    forwardBacktracking: Math.max(0, absoluteForward - Math.abs(state.netForward)) / frame.height,
    outwardProgressFraction: outwardTravel / Math.max(absoluteSide, EPSILON),
    downwardProgressFraction: downwardTravel / Math.max(absoluteHeight, EPSILON),
    sideBacktracking: Math.max(0, absoluteSide - Math.abs(state.netSide)) / frame.height,
    heightBacktracking: Math.max(0, absoluteHeight - Math.abs(state.netHeight)) / frame.height,
    endpointSide: relative.dot(axes.right) / frame.height,
    endpointHeight: relative.dot(axes.up) / frame.height,
    endpointDepth: relative.dot(axes.forward) / frame.height,
    continuationAgreement: state.segmentCount
      ? state.continuationMatches / state.segmentCount : 0,
    branchCount: state.branchCount,
    segmentCount: state.segmentCount,
    sideSign,
    ...corridor,
  };
  return metrics;
}

function pathScoreForRole(metrics, role, branchHub = false, terminal = false) {
  const isArm = String(role || '').endsWith('_arm');
  const sideSign = metrics.sideSign;
  const outward = clamp01(metrics.netSide * sideSign / .45);
  const endpointOutward = clamp01(metrics.endpointSide * sideSign / .65);
  const downward = clamp01(-metrics.netHeight / .65);
  const endpointLow = clamp01((.25 - metrics.endpointHeight) / .8);
  const length = rangeScore(metrics.totalLength, isArm ? .12 : .2,
    isArm ? 1.5 : 1.7, .7);
  const continuation = clamp01(metrics.continuationAgreement);
  const depthPenalty = clamp01(metrics.absoluteForwardTravel / .45
    + metrics.forwardBacktracking / .3);
  const sidePenalty = clamp01(metrics.sideBacktracking / .65);
  const heightPenalty = clamp01(metrics.heightBacktracking / .8);
  const outwardProgress = clamp01(metrics.outwardProgressFraction);
  const downwardProgress = clamp01(metrics.downwardProgressFraction);
  const downwardReach = clamp01(-metrics.netHeight / .35);
  // Branch topology is only a small tie-breaker after the geometry is already
  // limb-like. It must never rescue a weak or shallow leg path.
  const branchBonus = branchHub && metrics.corridorScore > .62
    ? Math.min(.035, .015 + .02 * (metrics.branchDominance || 0)) : 0;
  if (isArm) {
    return clamp01(.27 * metrics.corridorScore + .2 * metrics.poseAngleScore
      + .1 * outward + .08 * endpointOutward + .1 * length
      + .08 * metrics.straightness + .07 * outwardProgress
      + .05 * downward + .05 * continuation + .06 * metrics.pathSupportScore
      + .04 * (metrics.branchDominance || 0) + branchBonus
      - .2 * depthPenalty - .06 * sidePenalty - .04 * heightPenalty);
  }
  const lateralPenalty = clamp01(Math.abs(metrics.netSide) / .8
    + metrics.sideBacktracking / .45);
  const extended = clamp01(metrics.totalLength / .9);
  return clamp01(.3 * metrics.corridorScore + .2 * metrics.poseAngleScore
    + .13 * downwardReach + .11 * endpointLow + .1 * length
    + .08 * downwardProgress + .07 * metrics.straightness
    + .05 * extended + .05 * continuation + .06 * metrics.pathSupportScore
    + .04 * (metrics.branchDominance || 0) + branchBonus + (terminal ? .025 : 0)
    - .18 * lateralPenalty - .12 * depthPenalty - .05 * heightPenalty);
}

function pathSort(left, right) {
  if (Math.abs(right.score - left.score) > EPSILON) return right.score - left.score;
  const leftMetrics = left.pathMetrics || {};
  const rightMetrics = right.pathMetrics || {};
  if (Math.abs((rightMetrics.straightness || 0) - (leftMetrics.straightness || 0)) > EPSILON) {
    return (rightMetrics.straightness || 0) - (leftMetrics.straightness || 0);
  }
  if (Math.abs((right.continuationAgreement || 0)
      - (left.continuationAgreement || 0)) > EPSILON) {
    return (right.continuationAgreement || 0) - (left.continuationAgreement || 0);
  }
  if (Math.abs((rightMetrics.totalLength || 0) - (leftMetrics.totalLength || 0)) > EPSILON) {
    return (rightMetrics.totalLength || 0) - (leftMetrics.totalLength || 0);
  }
  if (Number(left.endJointId) !== Number(right.endJointId)) {
    return Number(left.endJointId) - Number(right.endJointId);
  }
  const leftPath = left.pathJointIds || [];
  const rightPath = right.pathJointIds || [];
  for (let index = 0; index < Math.min(leftPath.length, rightPath.length); index += 1) {
    if (leftPath[index] !== rightPath[index]) return leftPath[index] - rightPath[index];
  }
  return leftPath.length - rightPath.length;
}

function pathCandidateForState(state, rig, frame, axes, role, reason = null) {
  if (state.pathJointIds.length < 3) return null;
  const metrics = pathMetricsForState(state, rig, frame, axes, role);
  const branchHub = state.branchHub === true && state.pathJointIds.length >= 3;
  const endJointId = state.pathJointIds.at(-1);
  const bendJointId = selectLimbBendJoint(rig, state.pathJointIds);
  if (!Number.isInteger(bendJointId) || !Number.isInteger(endJointId)) return null;
  return {
    pathJointIds: [...state.pathJointIds],
    endJointId,
    bendJointId,
    score: pathScoreForRole(metrics, role, branchHub, reason === 'leaf'),
    pathMetrics: metrics,
    continuationAgreement: metrics.continuationAgreement,
    reason,
    branchHub,
  };
}

function expandPathState(state, childId, rig, component, frame, axes, role) {
  const current = state.pathJointIds.at(-1);
  const previousPoint = state.points.at(-1);
  const nextPoint = pointFor(rig, childId);
  if (!nextPoint) return null;
  const delta = nextPoint.clone().sub(previousPoint);
  const sideStep = delta.dot(axes.right);
  const heightStep = delta.dot(axes.up);
  const forwardStep = delta.dot(axes.forward);
  const continuation = continuationFor(rig, current);
  const children = childrenFor(component, current).filter(child => pointFor(rig, child));
  const siblingSupports = children.map(sibling => supportValue(rig, sibling));
  const siblingTotal = siblingSupports.reduce((sum, value) => sum + value, 0);
  const childSupport = supportValue(rig, childId);
  const branchRatio = children.length > 1
    ? childSupport / Math.max(siblingTotal, EPSILON) : null;
  const next = {
    pathJointIds: [...state.pathJointIds, childId],
    points: [...state.points, nextPoint],
    totalLength: state.totalLength + delta.length(),
    netSide: state.netSide + sideStep,
    netHeight: state.netHeight + heightStep,
    netForward: state.netForward + forwardStep,
    absoluteSideTravel: state.absoluteSideTravel + Math.abs(sideStep),
    absoluteHeightTravel: state.absoluteHeightTravel + Math.abs(heightStep),
    absoluteForwardTravel: state.absoluteForwardTravel + Math.abs(forwardStep),
    outwardTravel: state.outwardTravel + Math.max(0,
      sideStep * (String(role || '').startsWith('left_') ? -1 : 1)),
    downwardTravel: state.downwardTravel + Math.max(0, -heightStep),
    continuationMatches: state.continuationMatches
      + (continuation === childId ? 1 : 0),
    segmentCount: state.segmentCount + 1,
    branchCount: state.branchCount + (children.length > 1 ? 1 : 0),
    branchDominanceSum: state.branchDominanceSum
      + (branchRatio === null ? 1 : branchRatio),
    branchDominanceCount: state.branchDominanceCount + 1,
    branchHub: false,
  };
  const metrics = pathMetricsForState(next, rig, frame, axes, role);
  next.provisionalScore = pathScoreForRole(metrics, role, children.length > 1)
    + Math.min(next.pathJointIds.length, 6) * .01;
  return next;
}

export function resolveLimbPathCandidates({
  rig, anchorJointId, role, characterForward, characterAxes, semanticFrame,
  maxDepth = 32, beamWidth = 12, maxResults = 16,
} = {}) {
  const anchor = numberId(anchorJointId);
  const component = componentFor(rig, anchor);
  if (!component || anchor === null
      || !component.nodeIds?.map(Number).includes(anchor)) {
    return {available: false, candidates: [], reason: 'anchor_not_found'};
  }
  if (!pointFor(rig, anchor)) {
    return {available: false, candidates: [], reason: 'anchor_pivot_invalid'};
  }
  if (Number(component.rootId) === anchor) {
    return {available: false, candidates: [], reason: 'anchor_is_component_root'};
  }
  const axes = semanticAxesForPath({characterAxes, characterForward});
  // The caller normally supplies the model-wide frame. The fallback also
  // measures every Rig component, never the candidate's component alone.
  const frame = resolverFrame(rig, axes, semanticFrame);
  const anchorPoint = pointFor(rig, anchor);
  const initial = {
    pathJointIds: [anchor], points: [anchorPoint], totalLength: 0,
    netSide: 0, netHeight: 0, netForward: 0,
    absoluteSideTravel: 0, absoluteHeightTravel: 0, absoluteForwardTravel: 0,
    outwardTravel: 0, downwardTravel: 0, continuationMatches: 0,
    segmentCount: 0, branchCount: 0, branchDominanceSum: 1,
    branchDominanceCount: 1, provisionalScore: 0,
  };
  let active = [initial];
  const finals = [];
  let pathExpansions = 0;
  let maxBeamSize = active.length;
  const depthLimit = Math.max(1, Math.min(Number(maxDepth) || 32, MAX_PATH_DEPTH));
  const beamLimit = Math.max(1, Math.min(Number(beamWidth) || 12, 64));
  for (let depth = 0; depth < depthLimit && active.length; depth += 1) {
    const expanded = [];
    active.forEach(state => {
      const current = state.pathJointIds.at(-1);
      const children = [...new Set(childrenFor(component, current).map(numberId))]
        .filter(childId => Number.isInteger(childId)
          && !state.pathJointIds.includes(childId) && pointFor(rig, childId))
        .sort((left, right) => left - right);
      if (state.pathJointIds.length >= 3) {
        state.branchHub = children.length > 1;
        const endpoint = pathCandidateForState(state, rig, frame, axes, role,
          children.length ? 'branch_endpoint' : 'leaf');
        const terminalBranch = children.length > 1 && children.every(child =>
          childrenFor(component, child).length === 0);
        // A single-child continuation is not an endpoint. Keeping it out of
        // the final set prevents a short helper segment from beating its
        // complete descendant path; branch hubs and leaves remain options.
        if (endpoint && (children.length === 0 || terminalBranch)) finals.push(endpoint);
      }
      if (children.length > 1 && children.every(child =>
          childrenFor(component, child).length === 0)) return;
      children.forEach(childId => {
        const next = expandPathState(state, childId, rig, component, frame, axes, role);
        if (next) {
          pathExpansions += 1;
          expanded.push(next);
        }
      });
    });
    expanded.sort((left, right) => {
      if (Math.abs(right.provisionalScore - left.provisionalScore) > EPSILON) {
        return right.provisionalScore - left.provisionalScore;
      }
      if (left.pathJointIds.length !== right.pathJointIds.length) {
        return right.pathJointIds.length - left.pathJointIds.length;
      }
      for (let index = 0; index < left.pathJointIds.length; index += 1) {
        if (left.pathJointIds[index] !== right.pathJointIds[index]) {
          return left.pathJointIds[index] - right.pathJointIds[index];
        }
      }
      return 0;
    });
    active = expanded.slice(0, beamLimit);
    maxBeamSize = Math.max(maxBeamSize, active.length);
  }
  active.forEach(state => {
    if (state.pathJointIds.length < 3) return;
    const endpoint = pathCandidateForState(state, rig, frame, axes, role,
      'safety_depth');
    if (endpoint) finals.push(endpoint);
  });
  const uniqueFinals = new Map();
  finals.forEach(candidate => {
    const key = candidate.pathJointIds.join(',');
    const current = uniqueFinals.get(key);
    if (!current || pathSort(candidate, current) < 0) {
      uniqueFinals.set(key, candidate);
    }
  });
  const candidates = [...uniqueFinals.values()]
    .sort(pathSort).slice(0, Math.max(1, Number(maxResults) || 16));
  return {
    available: candidates.length > 0,
    candidates,
    reason: candidates.length ? null : 'no_valid_descendant_path',
    axes,
    frame,
    stats: {
      pathExpansions,
      maxBeamSize,
      pathCandidatesProduced: finals.length,
      maxDepth: depthLimit,
      beamWidth: beamLimit,
    },
  };
}

/** Detect one primary descendant limb path through the shared resolver. */
export function detectLimbPath({
  rig, anchorJointId, role, characterForward, characterAxes, semanticFrame,
} = {}) {
  const resolved = resolveLimbPathCandidates({
    rig, anchorJointId, role, characterForward, characterAxes, semanticFrame,
  });
  const anchor = numberId(anchorJointId);
  if (!resolved.candidates.length) {
    return detectionResult(role, anchor, [], resolved.reason || 'no_valid_descendant_path',
      false, rig, characterForward);
  }
  const best = resolved.candidates[0];
  const pathCandidates = resolved.candidates.map(candidate => ({
    pathJointIds: [...candidate.pathJointIds],
    endJointId: candidate.endJointId,
    bendJointId: candidate.bendJointId,
    score: candidate.score,
    pathMetrics: candidate.pathMetrics,
    continuationAgreement: candidate.continuationAgreement,
    reason: candidate.reason,
    branchHub: candidate.branchHub,
  }));
  return detectionResult(role, anchor, best.pathJointIds, best.reason,
    best.branchHub, rig, characterForward, {
      ...best, pathCandidates, pathDiagnostics: resolved.stats,
  });
}

function cloneRotations(localRotations) {
  const result = new Map();
  if (localRotations instanceof Map) {
    localRotations.forEach((rotation, id) => {
      const jointId = numberId(id);
      if (jointId !== null) result.set(jointId, quaternionFrom(rotation));
    });
  } else Object.entries(localRotations || {}).forEach(([id, rotation]) => {
    const jointId = numberId(id);
    if (jointId !== null) result.set(jointId, quaternionFrom(rotation));
  });
  return result;
}

export function characterAxesFromOrientation({orientation, userRotation} = {}) {
  if (!orientation || !userRotation) return null;
  const baseOrientation = quaternionFrom(userRotation).invert()
    .multiply(quaternionFrom(orientation)).normalize();
  const inverseBase = baseOrientation.clone().invert();
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(inverseBase).normalize();
  const forward = new THREE.Vector3(0, 0, 1).applyQuaternion(inverseBase);
  forward.addScaledVector(up, -forward.dot(up));
  if (!Number.isFinite(up.lengthSq()) || !Number.isFinite(forward.lengthSq())
      || up.lengthSq() <= EPSILON || forward.lengthSq() <= EPSILON) return null;
  forward.normalize();
  const right = up.clone().cross(forward);
  if (!Number.isFinite(right.lengthSq()) || right.lengthSq() <= EPSILON) return null;
  right.normalize();
  return {up, forward, right};
}

export function characterForwardFromOrientation(state = {}) {
  return characterAxesFromOrientation(state)?.forward || null;
}

function buildEvaluation({forest, centers, pivots, rotations}) {
  const rotationOutput = new Map();
  const transforms = buildForestTransformsFromLocalRotations(
    forest, centers, {
      getQuaternion: id => rotations.get(Number(id)) || new THREE.Quaternion(),
      jointPivotByBoneId: pivots,
      rotationOutput,
      transformCache: new Map(),
    });
  return {transforms, rotations: rotationOutput};
}

function pointAt(id, transforms, centers, pivots) {
  const point = finiteVector(valueFor(pivots, id))
    || finiteVector(valueFor(centers, id));
  if (!point) return null;
  const transform = transforms.get(id);
  return transform?.isMatrix4 ? point.applyMatrix4(transform) : point;
}

function parentAcrossForest(forest, id) {
  const component = (forest?.components || []).find(item =>
    item?.nodeIds?.map(Number).includes(id));
  return parentFor(component, id);
}

function shortestArc(from, to) {
  if (from.lengthSq() <= EPSILON || to.lengthSq() <= EPSILON) {
    return new THREE.Quaternion();
  }
  const source = from.clone().normalize();
  const target = to.clone().normalize();
  const dot = THREE.MathUtils.clamp(source.dot(target), -1, 1);
  if (dot > 1 - EPSILON) return new THREE.Quaternion();
  if (dot < -1 + EPSILON) {
    const axis = Math.abs(source.x) < Math.abs(source.y)
      ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    axis.cross(source).normalize();
    return new THREE.Quaternion().setFromAxisAngle(axis, Math.PI);
  }
  return new THREE.Quaternion().setFromUnitVectors(source, target).normalize();
}

function applyWorldDelta({jointId, delta, forest, evaluation, working}) {
  const parentId = parentAcrossForest(forest, jointId);
  const parentRotation = parentId === null
    ? new THREE.Quaternion()
    : evaluation.rotations.get(parentId)?.clone() || new THREE.Quaternion();
  const localDelta = parentRotation.clone().invert()
    .multiply(delta).multiply(parentRotation).normalize();
  const next = localDelta.multiply(
    working.get(jointId)?.clone() || new THREE.Quaternion()).normalize();
  if (![next.x, next.y, next.z, next.w].every(Number.isFinite)) return false;
  working.set(jointId, next);
  return true;
}

function preferredDirectionFromInput(value) {
  const direction = finiteVector(value);
  return direction && direction.lengthSq() > EPSILON ? direction.normalize() : null;
}

function solvePass({forest, centers, pivots, working, anchorJointId,
    bendJointId, endJointId, targetPoint, bendDirection}) {
  let evaluation = buildEvaluation({forest, centers, pivots, rotations: working});
  const anchor = pointAt(anchorJointId, evaluation.transforms, centers, pivots);
  let bend = pointAt(bendJointId, evaluation.transforms, centers, pivots);
  let end = pointAt(endJointId, evaluation.transforms, centers, pivots);
  if (!anchor || !bend || !end) {
    return {evaluation, residual: Infinity, limbScale: 0};
  }

  const upper = bend.clone().sub(anchor);
  const lower = end.clone().sub(bend);
  const l1 = upper.length();
  const l2 = lower.length();
  const limbScale = l1 + l2;
  if (l1 <= EPSILON || l2 <= EPSILON) return {
    evaluation, residual: end.distanceTo(targetPoint), limbScale,
  };
  const targetVector = targetPoint.clone().sub(anchor);
  const distance = targetVector.length();
  if (distance <= EPSILON) return {
    evaluation, residual: end.distanceTo(targetPoint), limbScale,
  };
  const direction = targetVector.multiplyScalar(1 / distance);
  const clampedDistance = THREE.MathUtils.clamp(
    distance, Math.abs(l1 - l2) + EPSILON, l1 + l2 - EPSILON);
  const x = (l1 * l1 - l2 * l2 + clampedDistance * clampedDistance)
    / (2 * clampedDistance);
  const h = Math.sqrt(Math.max(l1 * l1 - x * x, 0));
  let plane = preferredDirectionFromInput(bendDirection);
  plane = plane ? projectedDirection(plane, direction) : null;
  if (!plane) plane = projectedDirection(upper, direction);
  if (!plane) {
    const fallback = Math.abs(direction.x) < 0.8
      ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    plane = projectedDirection(fallback, direction)
      || new THREE.Vector3(0, 0, 1);
  }
  const desiredBend = anchor.clone()
    .addScaledVector(direction, x).addScaledVector(plane, h);

  applyWorldDelta({jointId: anchorJointId,
    delta: shortestArc(upper, desiredBend.clone().sub(anchor)),
    forest, evaluation, working});
  evaluation = buildEvaluation({forest, centers, pivots, rotations: working});
  bend = pointAt(bendJointId, evaluation.transforms, centers, pivots);
  end = pointAt(endJointId, evaluation.transforms, centers, pivots);
  if (!bend || !end) return {evaluation, residual: Infinity, limbScale};
  applyWorldDelta({jointId: bendJointId,
    delta: shortestArc(end.clone().sub(bend), targetPoint.clone().sub(bend)),
    forest, evaluation, working});
  evaluation = buildEvaluation({forest, centers, pivots, rotations: working});
  end = pointAt(endJointId, evaluation.transforms, centers, pivots);
  return {
    evaluation,
    residual: end ? end.distanceTo(targetPoint) : Infinity,
    limbScale,
  };
}

/** Solve a detected limb while changing only its anchor and bend controls. */
export function solveLimbIk({forest, centers, jointPivots, localRotations,
    anchorJointId, bendJointId, endJointId, pathJointIds, target,
    bendDirection, bendSign = 1} = {}) {
  const anchor = numberId(anchorJointId);
  const bend = numberId(bendJointId);
  const end = numberId(endJointId);
  const path = (pathJointIds || []).map(numberId).filter(Number.isInteger);
  const targetPoint = finiteVector(target);
  const component = (forest?.components || []).find(item =>
    item?.nodeIds?.map(Number).includes(anchor));
  if (!forest || !targetPoint || anchor === null || bend === null || end === null
      || !component || Number(component.rootId) === anchor
      || path.length < 3 || path[0] !== anchor || path.at(-1) !== end
      || !path.includes(bend)) {
    return {rotations: new Map(), iterations: 0, residual: Infinity, reached: false};
  }
  const working = cloneRotations(localRotations);
  const original = cloneRotations(localRotations);
  const direction = preferredDirectionFromInput(bendDirection);
  if (direction && Number(bendSign) < 0) direction.negate();
  let result = solvePass({forest, centers, pivots: jointPivots,
    working, anchorJointId: anchor, bendJointId: bend, endJointId: end,
    targetPoint, bendDirection: direction});
  const tolerance = Math.max(Number(result.limbScale) || 0, 1e-6)
    * TOLERANCE_SCALE;
  let iterations = 1;
  while (iterations < MAX_SOLVE_PASSES && result.residual > tolerance) {
    result = solvePass({forest, centers, pivots: jointPivots,
      working, anchorJointId: anchor, bendJointId: bend, endJointId: end,
      targetPoint, bendDirection: direction});
    iterations += 1;
  }
  const rotations = new Map();
  for (const jointId of [anchor, bend]) {
    const before = original.get(jointId) || new THREE.Quaternion();
    const after = working.get(jointId) || new THREE.Quaternion();
    if (Math.abs(Math.abs(before.dot(after)) - 1) > 1e-7) {
      rotations.set(jointId, after.clone().normalize());
    }
  }
  const residual = Number.isFinite(result.residual) ? result.residual : Infinity;
  return {rotations, iterations, residual,
    reached: residual <= tolerance};
}
