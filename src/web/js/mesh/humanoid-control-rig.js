// Geometry-assisted inputs plus a deterministic proportional humanoid rig.
//
// Geometry is used only to establish orientation, model height, and the two
// Foot anchors. Every other control is constructed by the proportional
// template; no mesh point is consulted after those inputs are established.

import {
  DEFAULT_HUMANOID_PROPORTIONS,
  buildProportionalHumanoidRig,
  semanticAxesFrame,
} from './humanoid-proportional-template.js';

const EPSILON = 1e-8;
const DEFAULT_MAX_POINT_COUNT = 160000;
const CONTROL_KEYS = Object.freeze([
  'chest', 'pelvis',
  'leftShoulder', 'leftElbow', 'leftHand',
  'rightShoulder', 'rightElbow', 'rightHand',
  'leftHip', 'leftKnee', 'leftFoot',
  'rightHip', 'rightKnee', 'rightFoot',
]);
const TEMPLATE_PRIORS = Object.freeze({
  ...DEFAULT_HUMANOID_PROPORTIONS,
  vertical: Object.freeze({
    neck: 0.82, chest: 0.685, pelvis: 0.55, knee: 0.2925, foot: 0.035,
  }),
});

function finiteNumber(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function vector3(value, fallback = [0, 0, 0]) {
  if (value?.isVector3) return [finiteNumber(value.x), finiteNumber(value.y),
    finiteNumber(value.z)];
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    return [0, 1, 2].map(index => finiteNumber(value[index], fallback[index]));
  }
  if (value && typeof value === 'object') {
    return [finiteNumber(value.x, fallback[0]), finiteNumber(value.y, fallback[1]),
      finiteNumber(value.z, fallback[2])];
  }
  return [...fallback];
}

function quaternion4(value) {
  if (value?.isQuaternion) return [finiteNumber(value.x), finiteNumber(value.y),
    finiteNumber(value.z), finiteNumber(value.w, 1)];
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    const result = [0, 1, 2, 3].map(index => Number(value[index]));
    if (result.every(Number.isFinite)) return result;
  }
  if (value && typeof value === 'object') {
    const result = [Number(value.x), Number(value.y), Number(value.z), Number(value.w)];
    if (result.every(Number.isFinite)) return result;
  }
  return [0, 0, 0, 1];
}

function length(value) {
  return Math.hypot(value[0], value[1], value[2]);
}

function dot(left, right) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
}

function add(left, right) {
  return [left[0] + right[0], left[1] + right[1], left[2] + right[2]];
}

function scale(value, amount) {
  return value.map(component => component * amount);
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function quantile(values, fraction) {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const index = clamp(fraction, 0, 1) * (sorted.length - 1);
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sorted[lower];
  const weight = index - lower;
  return sorted[lower] * (1 - weight) + sorted[upper] * weight;
}

function median(values) {
  return quantile(values, 0.5);
}

function transformedPoint(point, matrix) {
  if (!matrix?.elements || matrix.elements.length < 16) return point;
  const e = matrix.elements;
  const x = point[0];
  const y = point[1];
  const z = point[2];
  const w = e[3] * x + e[7] * y + e[11] * z + e[15];
  const divisor = Math.abs(w) > EPSILON ? w : 1;
  return [(e[0] * x + e[4] * y + e[8] * z + e[12]) / divisor,
    (e[1] * x + e[5] * y + e[9] * z + e[13]) / divisor,
    (e[2] * x + e[6] * y + e[10] * z + e[14]) / divisor];
}

function positionsForMesh(mesh) {
  if (mesh?.userData?.assetFill === true) return null;
  const rest = mesh?.userData?.humanoidRestPositions
    || mesh?.userData?.basePositions || mesh?.geometry?.attributes?.position?.array;
  if (!rest || rest.length < 3) return null;
  const matrix = mesh?.userData?.humanoidRestMatrix?.isMatrix4
    ? mesh.userData.humanoidRestMatrix : null;
  const points = [];
  for (let index = 0; index + 2 < rest.length; index += 3) {
    const point = [Number(rest[index]), Number(rest[index + 1]), Number(rest[index + 2])];
    if (point.every(Number.isFinite)) points.push(transformedPoint(point, matrix));
  }
  return points;
}

function semanticPoints(meshes, frame) {
  const result = [];
  (meshes || []).forEach(mesh => {
    const points = positionsForMesh(mesh);
    if (!points) return;
    points.forEach(point => result.push({
      point,
      side: dot(point, frame.right),
      height: dot(point, frame.up),
      depth: dot(point, frame.forward),
    }));
  });
  return result;
}

function downsamplePoints(points, maximum) {
  if (points.length <= maximum) return points;
  const result = [];
  const step = points.length / maximum;
  for (let index = 0; index < maximum; index += 1) {
    result.push(points[Math.floor(index * step)]);
  }
  return result;
}

function minMax(values) {
  let min = Infinity;
  let max = -Infinity;
  values.forEach(value => {
    if (value < min) min = value;
    if (value > max) max = value;
  });
  return {min, max};
}

function boundsFor(points) {
  const sideBounds = minMax(points.map(item => item.side));
  const heightBounds = minMax(points.map(item => item.height));
  const depthBounds = minMax(points.map(item => item.depth));
  return {
    lowHeight: heightBounds.min,
    highHeight: heightBounds.max,
    height: Math.max(heightBounds.max - heightBounds.min, 0),
    sideCenter: median(points.map(item => item.side)),
    depthCenter: median(points.map(item => item.depth)),
    minSide: sideBounds.min,
    maxSide: sideBounds.max,
    minDepth: depthBounds.min,
    maxDepth: depthBounds.max,
  };
}

function normalizedPoint(item, bounds) {
  const height = Math.max(bounds.height, EPSILON);
  return {
    x: (item.side - bounds.sideCenter) / height,
    y: (item.height - bounds.lowHeight) / height,
    z: (item.depth - bounds.depthCenter) / height,
  };
}

// This is the only remaining anatomical geometry lookup. It supplies the
// starting Foot anchors; all other controls are produced by the template.
function fitFoot(points, side) {
  const lower = points.filter(item => item.y <= 0.1 && side * item.x > 0.025);
  if (!lower.length) return null;
  const stableTop = quantile(lower.map(item => item.y), 0.62);
  const stable = lower.filter(item => item.y <= stableTop);
  return {
    x: median(stable.map(item => item.x)),
    y: median(stable.map(item => item.y)),
    z: median(stable.map(item => item.z)),
    support: stable.length,
  };
}

function pointToWorld(point, bounds, frame) {
  const side = bounds.sideCenter + point.x * bounds.height;
  const height = bounds.lowHeight + point.y * bounds.height;
  const depth = bounds.depthCenter + point.z * bounds.height;
  return add(add(scale(frame.right, side), scale(frame.up, height)),
    scale(frame.forward, depth));
}

function worldToSemantic(point, bounds, frame) {
  const value = vector3(point);
  return {
    sideN: (dot(value, frame.right) - bounds.sideCenter) / bounds.height,
    height01: (dot(value, frame.up) - bounds.lowHeight) / bounds.height,
    depthN: (dot(value, frame.forward) - bounds.depthCenter) / bounds.height,
  };
}

function fallbackPoint(key) {
  const p = DEFAULT_HUMANOID_PROPORTIONS;
  const foot = 0.02 + p.footLift;
  const hip = foot + p.legLength;
  const neck = hip + p.hipToNeckLength;
  const chest = hip + p.hipToNeckLength * p.chestFraction;
  const shoulder = p.shoulderHalfWidth;
  const angle = p.armDropAngleDeg * Math.PI / 180;
  const armSide = p.armLength * Math.cos(angle);
  const armDrop = p.armLength * Math.sin(angle);
  const elbowSide = shoulder + armSide * p.elbowFraction;
  const elbowHeight = neck - armDrop * p.elbowFraction;
  const values = {
    chest: [0, chest], pelvis: [0, hip],
    leftShoulder: [-shoulder, neck], leftElbow: [-elbowSide, elbowHeight],
    leftHand: [-shoulder - armSide, neck - armDrop], rightShoulder: [shoulder, neck],
    rightElbow: [elbowSide, elbowHeight], rightHand: [shoulder + armSide, neck - armDrop],
    leftHip: [-0.1, hip], leftKnee: [-0.1, foot + p.legLength * 0.5],
    leftFoot: [-0.1, foot], rightHip: [0.1, hip],
    rightKnee: [0.1, foot + p.legLength * 0.5], rightFoot: [0.1, foot],
  };
  const value = values[key] || [0, 0.5];
  return {x: value[0], y: value[1], z: 0};
}

function emptyRig(frame, diagnostics = {}, reason = 'no_rest_geometry', available = false) {
  const controls = Object.fromEntries(CONTROL_KEYS.map(key => {
    const point = fallbackPoint(key);
    return [key, {
      position: [0, 0, 0],
      semantic: {sideN: point.x, height01: point.y, depthN: point.z},
      confidence: 'deterministic', source: 'proportional_template', fitted: false,
      support: 0,
    }];
  }));
  return {
    version: 1,
    source: 'proportional_template',
    mode: 'proportional_template',
    available,
    accepted: false,
    confidence: 'deterministic',
    confidenceByRegion: {},
    frame: {up: [...frame.up], right: [...frame.right], forward: [...frame.forward],
      lowHeight: 0, highHeight: 0, height: 0},
    controls,
    paths: {torso: [], leftArm: [], rightArm: [], leftLeg: [], rightLeg: []},
    diagnostics: {...diagnostics, mode: 'proportional_template', failureReasons: [reason]},
  };
}

function pathFor(template, first, second, third) {
  return [template[first], template[second], template[third]].map(vector3);
}

function buildControl(template, key, bounds, frame, supports) {
  const position = vector3(template[key]);
  return {
    position,
    semantic: worldToSemantic(position, bounds, frame),
    confidence: 'deterministic',
    source: 'proportional_template',
    fitted: true,
    support: finiteNumber(supports[key], 0),
  };
}

function proportionalDiagnostics(template, bounds, frame, supports, base) {
  const p = template.proportions;
  return {
    ...base,
    mode: 'proportional_template',
    semanticAxes: {up: [...frame.up], right: [...frame.right], forward: [...frame.forward]},
    semanticSpans: {
      up: bounds.height,
      right: bounds.maxSide - bounds.minSide,
      forward: bounds.maxDepth - bounds.minDepth,
    },
    footSupport: {
      left: finiteNumber(supports.leftFoot, 0),
      right: finiteNumber(supports.rightFoot, 0),
    },
    detectedFeet: {
      left: vector3(template.detectedLeftFoot || template.leftFoot),
      right: vector3(template.detectedRightFoot || template.rightFoot),
    },
    templatePoints: {neck: vector3(template.neck)},
    proportionalTemplate: {
      characterHeight: template.characterHeight,
      footLiftN: finiteNumber(p.footLift),
      legLengthN: finiteNumber(p.legLength),
      hipToNeckLengthN: finiteNumber(p.hipToNeckLength),
      shoulderHalfWidthN: finiteNumber(p.shoulderHalfWidth),
      armLengthN: finiteNumber(p.armLength),
      armDropAngleDeg: finiteNumber(p.armDropAngleDeg),
      kneeFraction: finiteNumber(p.kneeFraction),
      elbowFraction: finiteNumber(p.elbowFraction),
      chestFraction: finiteNumber(p.chestFraction),
      leftFoot: vector3(template.leftFoot),
      rightFoot: vector3(template.rightFoot),
      neck: vector3(template.neck),
    },
    failureReasons: [],
    fallbackControls: [],
    fitRuntimeMs: finiteNumber(base.fitRuntimeMs),
  };
}

/** Fit a deterministic proportional scaffold anchored by the detected Feet. */
export function buildHumanoidControlRig({meshes = [], axes, orientationState, options = {}} = {}) {
  const started = typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now();
  const frame = semanticAxesFrame(axes);
  const orientationReady = orientationState
    ? orientationState.orientationInitialized === true : true;
  const baseOrientation = quaternion4(orientationState?.baseOrientation);
  const baseDiagnostics = {
    orientationReady,
    baseOrientation,
    modelOrientationRevision: finiteNumber(orientationState?.modelOrientationRevision, 0),
    pointCount: 0,
    sampledPointCount: 0,
    voxelCount: 0,
  };
  if (orientationState && !orientationReady) {
    return emptyRig(frame, {...baseDiagnostics, fitRuntimeMs: 0},
      'orientation_not_ready', false);
  }

  const projected = semanticPoints(meshes, frame);
  baseDiagnostics.pointCount = projected.length;
  if (!projected.length) return emptyRig(frame,
    {...baseDiagnostics, fitRuntimeMs: 0}, 'no_rest_geometry', false);

  const maximum = Math.max(1, Math.floor(finiteNumber(
    options.maxPointCount, DEFAULT_MAX_POINT_COUNT)));
  const workingPoints = downsamplePoints(projected, maximum);
  baseDiagnostics.sampledPointCount = workingPoints.length;
  const bounds = boundsFor(workingPoints);
  if (!(bounds.height > EPSILON)) {
    return emptyRig(frame, {...baseDiagnostics, fitRuntimeMs: 0},
      'invalid_character_height', false);
  }
  const normalized = workingPoints.map(item => normalizedPoint(item, bounds));
  const leftFootN = fitFoot(normalized, -1);
  const rightFootN = fitFoot(normalized, 1);
  if (!leftFootN || !rightFootN) {
    return emptyRig(frame, {...baseDiagnostics, characterHeight: bounds.height,
      fitRuntimeMs: 0}, 'feet_not_found', false);
  }

  // From this point onward the mesh is intentionally out of the pipeline.
  const leftFoot = pointToWorld(leftFootN, bounds, frame);
  const rightFoot = pointToWorld(rightFootN, bounds, frame);
  const template = buildProportionalHumanoidRig({
    characterHeight: bounds.height,
    leftFoot,
    rightFoot,
    semanticAxes: frame,
    proportions: options.proportions || DEFAULT_HUMANOID_PROPORTIONS,
  });
  if (!template) {
    return emptyRig(frame, {...baseDiagnostics, characterHeight: bounds.height,
      fitRuntimeMs: 0}, 'invalid_proportional_template', false);
  }

  const supports = {leftFoot: leftFootN.support, rightFoot: rightFootN.support};
  const controls = Object.fromEntries(CONTROL_KEYS.map(key => [key,
    buildControl(template, key, bounds, frame, supports)]));
  const paths = {
    torso: [controls.chest.position, controls.pelvis.position],
    leftArm: pathFor(template, 'leftShoulder', 'leftElbow', 'leftHand'),
    rightArm: pathFor(template, 'rightShoulder', 'rightElbow', 'rightHand'),
    leftLeg: pathFor(template, 'leftHip', 'leftKnee', 'leftFoot'),
    rightLeg: pathFor(template, 'rightHip', 'rightKnee', 'rightFoot'),
  };
  const runtime = (typeof performance !== 'undefined' && performance.now
    ? performance.now() : Date.now()) - started;
  const diagnostics = proportionalDiagnostics(template, bounds, frame, supports, {
    ...baseDiagnostics,
    characterHeight: bounds.height,
    bodyDepth: bounds.depthCenter,
    bodyDepthN: 0,
    fitRuntimeMs: Math.max(0, runtime),
  });
  return serializeHumanoidControlRig({
    version: 1,
    source: 'proportional_template',
    mode: 'proportional_template',
    available: true,
    accepted: true,
    confidence: 'deterministic',
    confidenceByRegion: {
      torso: 'deterministic', arms: 'deterministic', legs: 'deterministic',
      overall: 'deterministic',
    },
    frame: {
      up: [...frame.up], right: [...frame.right], forward: [...frame.forward],
      lowHeight: bounds.lowHeight, highHeight: bounds.highHeight, height: bounds.height,
      sideCenter: bounds.sideCenter, depthCenter: bounds.depthCenter,
      bodyDepth: bounds.depthCenter, bodyDepthN: 0,
    },
    template: {...template.proportions, characterHeight: template.characterHeight},
    controls,
    paths,
    diagnostics,
  });
}

/** Return a JSON-safe snapshot suitable for events, panels, and overlays. */
export function serializeHumanoidControlRig(rig) {
  if (!rig) return null;
  const result = JSON.parse(JSON.stringify(rig));
  result.controls = Object.fromEntries(CONTROL_KEYS.map(key => {
    const control = result.controls?.[key] || {};
    return [key, {
      position: vector3(control.position),
      semantic: {...(control.semantic || {})},
      confidence: control.confidence || result.confidence || 'deterministic',
      source: control.source || result.source || 'proportional_template',
      fitted: control.fitted !== false,
      support: finiteNumber(control.support, 0),
    }];
  }));
  return result;
}

export const HUMANOID_CONTROL_KEYS = CONTROL_KEYS;
export const HUMANOID_TEMPLATE_PRIORS = TEMPLATE_PRIORS;
export {DEFAULT_HUMANOID_PROPORTIONS, buildProportionalHumanoidRig};
