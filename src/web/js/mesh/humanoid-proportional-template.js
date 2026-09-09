// Deterministic humanoid proportions used by the control-rig experiment.

const EPSILON = 1e-8;

export const DEFAULT_HUMANOID_PROPORTIONS = Object.freeze({
  footLift: 0.015,
  legLength: 0.50,
  hipToNeckLength: 0.285,
  shoulderHalfWidth: 0.090,
  armLength: 0.33,
  armDropAngleDeg: 50,
  kneeFraction: 0.50,
  elbowFraction: 0.50,
  chestFraction: 0.50,
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

function length(value) {
  return Math.hypot(value[0], value[1], value[2]);
}

function normalize(value, fallback) {
  const source = vector3(value, fallback);
  const size = length(source);
  return size > EPSILON ? source.map(component => component / size) : [...fallback];
}

function dot(left, right) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
}

function cross(left, right) {
  return [left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0]];
}

function add(left, right) {
  return [left[0] + right[0], left[1] + right[1], left[2] + right[2]];
}

function scale(value, amount) {
  return value.map(component => component * amount);
}

function lerp(first, second, fraction) {
  return add(first, scale(second.map((value, index) => value - first[index]), fraction));
}

function midpoint(left, right) {
  return scale(add(left, right), 0.5);
}

export function semanticAxesFrame(semanticAxes) {
  const up = normalize(semanticAxes?.up, [0, 1, 0]);
  let right = normalize(semanticAxes?.right, [1, 0, 0]);
  let forward = normalize(semanticAxes?.forward, [0, 0, 1]);
  right = normalize(add(right, scale(up, -dot(right, up))), [1, 0, 0]);
  forward = add(forward, scale(up, -dot(forward, up)));
  forward = add(forward, scale(right, -dot(forward, right)));
  forward = normalize(forward, [0, 0, 1]);
  right = normalize(cross(up, forward), right);
  return {up, right, forward};
}

function degToRad(degrees) {
  return finiteNumber(degrees) * Math.PI / 180;
}

/** Build a humanoid scaffold from height, feet, and a semantic coordinate frame. */
export function buildProportionalHumanoidRig({
  characterHeight,
  height,
  leftFoot,
  rightFoot,
  semanticAxes,
  up,
  right,
  forward,
  proportions = DEFAULT_HUMANOID_PROPORTIONS,
} = {}) {
  const resolvedHeight = finiteNumber(characterHeight ?? height);
  const resolvedAxes = semanticAxes || {up, right, forward};
  if (!(resolvedHeight > EPSILON) || !leftFoot || !rightFoot) return null;

  const frame = semanticAxesFrame(resolvedAxes);
  const template = {...DEFAULT_HUMANOID_PROPORTIONS, ...(proportions || {})};
  const detectedLeftFoot = vector3(leftFoot);
  const detectedRightFoot = vector3(rightFoot);
  const footLift = resolvedHeight * finiteNumber(template.footLift);
  const leftAnchor = add(detectedLeftFoot, scale(frame.up, footLift));
  const rightAnchor = add(detectedRightFoot, scale(frame.up, footLift));
  const legLength = resolvedHeight * finiteNumber(template.legLength);
  const leftHip = add(leftAnchor, scale(frame.up, legLength));
  const rightHip = add(rightAnchor, scale(frame.up, legLength));
  const leftKnee = lerp(leftHip, leftAnchor, finiteNumber(template.kneeFraction, 0.5));
  const rightKnee = lerp(rightHip, rightAnchor, finiteNumber(template.kneeFraction, 0.5));
  const pelvis = midpoint(leftHip, rightHip);
  const neck = add(pelvis, scale(frame.up,
    resolvedHeight * finiteNumber(template.hipToNeckLength)));
  const chest = lerp(pelvis, neck, finiteNumber(template.chestFraction, 0.5));

  const shoulderOffset = resolvedHeight * finiteNumber(template.shoulderHalfWidth);
  const leftShoulder = add(neck, scale(frame.right, -shoulderOffset));
  const rightShoulder = add(neck, scale(frame.right, shoulderOffset));
  const angle = degToRad(template.armDropAngleDeg);
  const leftDirection = normalize(add(scale(frame.right, -Math.cos(angle)),
    scale(frame.up, -Math.sin(angle))), frame.right.map(value => -value));
  const rightDirection = normalize(add(scale(frame.right, Math.cos(angle)),
    scale(frame.up, -Math.sin(angle))), frame.right);
  const armLength = resolvedHeight * finiteNumber(template.armLength);
  const leftHand = add(leftShoulder, scale(leftDirection, armLength));
  const rightHand = add(rightShoulder, scale(rightDirection, armLength));
  const leftElbow = lerp(leftShoulder, leftHand, finiteNumber(template.elbowFraction, 0.5));
  const rightElbow = lerp(rightShoulder, rightHand, finiteNumber(template.elbowFraction, 0.5));

  return {
    characterHeight: resolvedHeight,
    semanticAxes: frame,
    proportions: template,
    detectedLeftFoot,
    detectedRightFoot,
    chest,
    pelvis,
    neck,
    leftShoulder,
    leftElbow,
    leftHand,
    rightShoulder,
    rightElbow,
    rightHand,
    leftHip,
    leftKnee,
    leftFoot: leftAnchor,
    rightHip,
    rightKnee,
    rightFoot: rightAnchor,
  };
}
