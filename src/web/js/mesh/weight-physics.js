export const DEFAULT_PHYSICS_FREQUENCY_HZ = 2.0;
export const DEFAULT_PHYSICS_DAMPING_RATIO = 0.35;
export const DEFAULT_ANGLE_TOLERANCE = 0.001;
export const DEFAULT_VELOCITY_TOLERANCE = 0.001;
export const MAX_ANGULAR_VELOCITY = 720 * Math.PI / 180;
export const MAX_LOCAL_ANGLE = 90 * Math.PI / 180;

function nodeIdsForComponent(component) {
  return (component?.nodeIds || []).map(value => Number(value))
    .filter(Number.isFinite);
}

function maxDepthForComponent(component) {
  const declared = Number(component?.maxDepth);
  if (Number.isFinite(declared) && declared >= 0) return declared;
  return Math.max(0, ...Object.values(component?.depthById || {})
    .filter(depth => depth !== null && Number.isFinite(Number(depth)))
    .map(Number));
}

export function buildPhysicsTargetAngles(forest, targetAngleRadians) {
  const targets = new Map();
  const candidateAngle = Number(targetAngleRadians);
  const totalAngle = Number.isFinite(candidateAngle) ? candidateAngle : 0;
  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    const maxDepth = maxDepthForComponent(component);
    const localAngle = maxDepth > 0 ? totalAngle / maxDepth : 0;
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId !== rootId) targets.set(nodeId, localAngle);
    });
  });
  return targets;
}

export function initializePhysicsState(forest) {
  const joints = new Map();
  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId !== rootId) {
        joints.set(nodeId, {angle: 0, angularVelocity: 0});
      }
    });
  });
  return {joints};
}

export function physicsAngleMap(physicsState) {
  const angles = new Map();
  physicsState?.joints?.forEach((joint, boneId) => {
    angles.set(Number(boneId), Number(joint.angle) || 0);
  });
  return angles;
}

/** Apply a root-orientation change as a temporary local bend in every forest
 * component. The solver owns velocity; this helper only changes position. */
export function applyReferenceFrameAngularDelta(
    physicsState, forest, angularDeltaRadians, strength = 1) {
  const delta = Number(angularDeltaRadians);
  const response = Number(strength);
  if (!Number.isFinite(delta) || !Number.isFinite(response)) {
    return physicsState;
  }
  const lag = -delta * clamp(response, 0, 1);
  if (lag === 0) return physicsState;
  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    const maxDepth = maxDepthForComponent(component);
    if (maxDepth <= 0) return;
    const localLag = lag / maxDepth;
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId === rootId) return;
      const joint = physicsState?.joints?.get(nodeId);
      if (!joint) return;
      joint.angle = clamp(
        (Number(joint.angle) || 0) + localLag,
        -MAX_LOCAL_ANGLE, MAX_LOCAL_ANGLE);
    });
  });
  return physicsState;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function clampJoint(joint) {
  joint.angle = clamp(
    Number(joint.angle) || 0, -MAX_LOCAL_ANGLE, MAX_LOCAL_ANGLE);
  joint.angularVelocity = clamp(
    Number(joint.angularVelocity) || 0,
    -MAX_ANGULAR_VELOCITY, MAX_ANGULAR_VELOCITY);
  if (joint.angle === MAX_LOCAL_ANGLE && joint.angularVelocity > 0) {
    joint.angularVelocity = 0;
  }
  if (joint.angle === -MAX_LOCAL_ANGLE && joint.angularVelocity < 0) {
    joint.angularVelocity = 0;
  }
}

export function stepSpringPhysics(
    physicsState, forest, dt, options = {}) {
  const candidateMaxDt = Number(options.maxDt ?? 0.05);
  const maxDt = Number.isFinite(candidateMaxDt) && candidateMaxDt >= 0
    ? candidateMaxDt : 0.05;
  const candidateFrequency = Number(
    options.frequencyHz ?? DEFAULT_PHYSICS_FREQUENCY_HZ);
  const frequency = Number.isFinite(candidateFrequency)
    ? Math.max(0, candidateFrequency) : DEFAULT_PHYSICS_FREQUENCY_HZ;
  const candidateDamping = Number(
    options.dampingRatio ?? DEFAULT_PHYSICS_DAMPING_RATIO);
  const damping = Number.isFinite(candidateDamping)
    ? Math.max(0, candidateDamping) : DEFAULT_PHYSICS_DAMPING_RATIO;
  const step = clamp(Number(dt) || 0, 0, maxDt);
  const omega = 2 * Math.PI * frequency;
  const omegaSquared = omega * omega;
  const dampingTerm = 2 * damping * omega;
  const targets = options.angleByBoneId instanceof Map
    ? options.angleByBoneId
    : buildPhysicsTargetAngles(forest, options.targetAngleRadians);
  let maxAngleError = 0;
  let maxAngularVelocity = 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    const target = Number(targets.get(Number(boneId))) || 0;
    const acceleration = omegaSquared * (target - joint.angle)
      - dampingTerm * joint.angularVelocity;
    joint.angularVelocity += acceleration * step;
    clampJoint(joint);
    joint.angle += joint.angularVelocity * step;
    clampJoint(joint);
    maxAngleError = Math.max(maxAngleError, Math.abs(target - joint.angle));
    maxAngularVelocity = Math.max(
      maxAngularVelocity, Math.abs(joint.angularVelocity));
  });
  return {maxAngleError, maxAngularVelocity};
}

export function isPhysicsSettled(
    physicsState, forest, targetAngleRadians, options = {}) {
  const candidateAngleTolerance = Number(
    options.angleTolerance ?? DEFAULT_ANGLE_TOLERANCE);
  const angleTolerance = Number.isFinite(candidateAngleTolerance)
    ? Math.max(0, candidateAngleTolerance) : DEFAULT_ANGLE_TOLERANCE;
  const candidateVelocityTolerance = Number(
    options.velocityTolerance ?? DEFAULT_VELOCITY_TOLERANCE);
  const velocityTolerance = Number.isFinite(candidateVelocityTolerance)
    ? Math.max(0, candidateVelocityTolerance) : DEFAULT_VELOCITY_TOLERANCE;
  const targets = options.angleByBoneId instanceof Map
    ? options.angleByBoneId
    : buildPhysicsTargetAngles(forest, targetAngleRadians);
  let maxAngleError = 0;
  let maxAngularVelocity = 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    const target = Number(targets.get(Number(boneId))) || 0;
    maxAngleError = Math.max(maxAngleError, Math.abs(target - joint.angle));
    maxAngularVelocity = Math.max(
      maxAngularVelocity, Math.abs(joint.angularVelocity));
  });
  return maxAngleError < angleTolerance
    && maxAngularVelocity < velocityTolerance;
}

export function applyPhysicsKick(
    physicsState, forest, impulseRadiansPerSecond) {
  const impulse = Number(impulseRadiansPerSecond) || 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    const component = (forest?.components || []).find(item =>
      nodeIdsForComponent(item).includes(Number(boneId)));
    const maxDepth = Number(component?.maxDepth) || 0;
    const depth = Number(component?.depthById?.[boneId]);
    const scale = maxDepth > 0 && Number.isFinite(depth)
      ? Math.max(0, depth / maxDepth) : 0;
    joint.angularVelocity = clamp(
      joint.angularVelocity + impulse * scale,
      -MAX_ANGULAR_VELOCITY, MAX_ANGULAR_VELOCITY);
  });
  return physicsState;
}

export function resetPhysicsState(physicsState) {
  physicsState?.joints?.forEach(joint => {
    joint.angle = 0;
    joint.angularVelocity = 0;
  });
  return physicsState;
}
