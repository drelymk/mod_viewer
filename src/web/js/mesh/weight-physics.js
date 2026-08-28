export const DEFAULT_PHYSICS_FREQUENCY_HZ = 2.0;
export const DEFAULT_PHYSICS_DAMPING_RATIO = 0.35;
export const DEFAULT_ANGLE_TOLERANCE = 0.001;
export const DEFAULT_VELOCITY_TOLERANCE = 0.001;
export const MAX_ANGULAR_VELOCITY = 720 * Math.PI / 180;
export const MAX_LOCAL_ANGLE = 90 * Math.PI / 180;
export const DEFAULT_PHYSICS_MAX_BEND_DEGREES = 45;
export const JOINT_LIMIT_CONTACT_EPSILON = 1e-4;
export const GRAVITY_WORLD_DIRECTION = Object.freeze([0, -1, 0]);
export const STANDARD_GRAVITY = 9.81;
export const MIN_GRAVITY_LEVER_RATIO = 0.15;

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

export function buildPhysicsJointLimits(forest, maxComponentBendRadians) {
  const candidateBend = Number(maxComponentBendRadians);
  const maxComponentBend = Number.isFinite(candidateBend)
    ? Math.max(0, candidateBend) : 0;
  const limitByBoneId = new Map();
  const components = [];
  (forest?.components || []).forEach((component, index) => {
    const componentId = diagnosticComponentId(component, index);
    const rootId = diagnosticRootId(component);
    const numericRootId = Number(component?.rootId);
    const hasRoot = Number.isFinite(numericRootId);
    const nodeIds = nodeIdsForComponent(component);
    const maxDepth = maxDepthForComponent(component);
    const jointCount = nodeIds.filter(nodeId => !hasRoot
      || nodeId !== numericRootId)
      .length;
    const localLimitRadians = maxDepth > 0
      ? clamp(maxComponentBend / maxDepth, 0, MAX_LOCAL_ANGLE) : 0;
    nodeIds.forEach(nodeId => {
      if (!hasRoot || nodeId !== numericRootId) {
        limitByBoneId.set(nodeId, localLimitRadians);
      }
    });
    components.push({
      componentId,
      rootId,
      maxDepth,
      jointCount,
      localLimitRadians,
    });
  });
  return {
    limitByBoneId,
    diagnostics: {
      maxComponentBend,
      jointCount: limitByBoneId.size,
      components,
    },
  };
}

function limitForBone(jointLimitByBoneId, boneId) {
  if (!(jointLimitByBoneId instanceof Map)) return MAX_LOCAL_ANGLE;
  const candidate = jointLimitByBoneId.get(Number(boneId))
    ?? jointLimitByBoneId.get(String(boneId));
  const limit = Number(candidate);
  return Number.isFinite(limit)
    ? clamp(Math.max(0, limit), 0, MAX_LOCAL_ANGLE) : MAX_LOCAL_ANGLE;
}

function hasLimitForBone(jointLimitByBoneId, boneId) {
  return jointLimitByBoneId instanceof Map
    && (jointLimitByBoneId.has(Number(boneId))
      || jointLimitByBoneId.has(String(boneId)));
}

export function applyJointLimitsToAngles(
    angleByBoneId, jointLimitByBoneId) {
  const result = angleByBoneId instanceof Map
    ? new Map(angleByBoneId) : new Map();
  if (!(jointLimitByBoneId instanceof Map)) return result;
  result.forEach((angle, boneId) => {
    if (!hasLimitForBone(jointLimitByBoneId, boneId)) return;
    const value = Number(angle);
    const safeAngle = Number.isFinite(value) ? value : 0;
    const limit = limitForBone(jointLimitByBoneId, boneId);
    result.set(boneId, clamp(safeAngle, -limit, limit));
  });
  return result;
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
    physicsState, forest, angularDeltaRadians, strength = 1,
    jointLimitByBoneId = null) {
  const delta = Number(angularDeltaRadians);
  const response = Number(strength);
  if (!Number.isFinite(delta) || !Number.isFinite(response)) {
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  const lag = -delta * clamp(response, 0, 1);
  if (lag === 0) {
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
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
  applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
  return physicsState;
}

const TRANSLATION_EPSILON = 1e-8;

function finiteVector(value) {
  const values = value?.isVector3
    ? [value.x, value.y, value.z]
    : Array.isArray(value) ? value : [value?.x, value?.y, value?.z];
  if (values.length < 3) return null;
  const vector = values.slice(0, 3).map(Number);
  return vector.every(Number.isFinite) ? vector : null;
}

function vectorAdd(left, right) {
  return [left[0] + right[0], left[1] + right[1], left[2] + right[2]];
}

function vectorSubtract(left, right) {
  return [left[0] - right[0], left[1] - right[1], left[2] - right[2]];
}

function vectorScale(vector, scale) {
  return [vector[0] * scale, vector[1] * scale, vector[2] * scale];
}

function vectorDot(left, right) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
}

function vectorCross(left, right) {
  return [
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0],
  ];
}

function centerForBone(centerByBoneId, boneId) {
  const candidate = centerByBoneId?.get?.(boneId)
    ?? centerByBoneId?.get?.(String(boneId))
    ?? centerByBoneId?.[boneId];
  return finiteVector(candidate);
}

function translationAxisVector(axis) {
  if (axis === 'X') return [1, 0, 0];
  if (axis === 'Y') return [0, 1, 0];
  return [0, 0, 1];
}

function averageVectors(vectors) {
  if (!vectors.length) return null;
  const sum = vectors.reduce((total, vector) => vectorAdd(total, vector),
    [0, 0, 0]);
  return vectorScale(sum, 1 / vectors.length);
}

export function representativeComponentLever(component, centerByBoneId) {
  const rootId = Number(component?.rootId);
  const rootCenter = centerForBone(centerByBoneId, rootId);
  const nodeIds = nodeIdsForComponent(component);
  const maxDepth = maxDepthForComponent(component);
  if (!rootCenter || !nodeIds.length || maxDepth <= 0) return null;

  const deepest = nodeIds
    .filter(nodeId => Number(component?.depthById?.[nodeId]) === maxDepth)
    .map(nodeId => centerForBone(centerByBoneId, nodeId))
    .filter(Boolean);
  let distalCenter = averageVectors(deepest);
  if (!distalCenter) {
    const validCenters = nodeIds.map(nodeId => centerForBone(
      centerByBoneId, nodeId)).filter(Boolean);
    if (!validCenters.length) return null;
    distalCenter = validCenters.reduce((farthest, candidate) => {
      const candidateDistance = vectorDot(
        vectorSubtract(candidate, rootCenter),
        vectorSubtract(candidate, rootCenter));
      const farthestDistance = vectorDot(
        vectorSubtract(farthest, rootCenter),
        vectorSubtract(farthest, rootCenter));
      return candidateDistance > farthestDistance ? candidate : farthest;
    });
  }

  return vectorSubtract(distalCenter, rootCenter);
}

function gravityDiagnostics(componentId, rootId, maxDepth) {
  return {
    componentId,
    rootId,
    maxDepth,
    leverLength: 0,
    projectedLeverLength: 0,
    effectiveLeverLength: 0,
    totalAngularAcceleration: 0,
    localAngularAcceleration: 0,
    clamped: false,
  };
}

function diagnosticComponentId(component, fallback) {
  const componentId = Number(component?.componentId);
  return Number.isFinite(componentId) ? componentId : fallback;
}

function diagnosticRootId(component) {
  const rootId = Number(component?.rootId);
  return Number.isFinite(rootId) ? rootId : null;
}

/** Build one-axis gravity acceleration inputs from rest-space component
 * levers. The returned map contains only non-root joints. */
export function buildGravityAngularAccelerations(
    forest, centerByBoneId, gravityLocal, axis,
    {referenceRadius, gravityScale = 1} = {}) {
  const direction = finiteVector(gravityLocal);
  const radius = Number(referenceRadius);
  const scale = Number(gravityScale);
  const accelerations = new Map();
  const diagnostics = {
    componentCount: (forest?.components || []).length,
    activeComponentCount: 0,
    clampedComponentCount: 0,
    maxAbsTotalAcceleration: 0,
    maxAbsLocalAcceleration: 0,
    referenceRadius: Number.isFinite(radius) && radius > 0 ? radius : 0,
    minLeverRatio: MIN_GRAVITY_LEVER_RATIO,
    components: [],
  };
  if (!direction || !Number.isFinite(radius) || radius <= 0
      || !Number.isFinite(scale) || scale < 0) {
    (forest?.components || []).forEach((component, index) => {
      diagnostics.components.push(gravityDiagnostics(
        diagnosticComponentId(component, index), diagnosticRootId(component),
        maxDepthForComponent(component)));
    });
    return {accelerationByBoneId: accelerations, diagnostics};
  }

  const directionLength = Math.sqrt(vectorDot(direction, direction));
  if (directionLength <= TRANSLATION_EPSILON) {
    (forest?.components || []).forEach((component, index) => {
      diagnostics.components.push(gravityDiagnostics(
        diagnosticComponentId(component, index), diagnosticRootId(component),
        maxDepthForComponent(component)));
    });
    return {accelerationByBoneId: accelerations, diagnostics};
  }

  const unitAxis = translationAxisVector(axis);
  const gravityMagnitude = STANDARD_GRAVITY * radius * scale;
  const gravity = vectorScale(direction, gravityMagnitude / directionLength);
  const minimumLever = radius * MIN_GRAVITY_LEVER_RATIO;
  (forest?.components || []).forEach((component, index) => {
    const componentId = diagnosticComponentId(component, index);
    const rootId = Number(component?.rootId);
    const maxDepth = maxDepthForComponent(component);
    const details = gravityDiagnostics(
      componentId, diagnosticRootId(component), maxDepth);
    const lever = representativeComponentLever(component, centerByBoneId);
    if (lever && maxDepth > 0) {
      const leverLength = Math.sqrt(vectorDot(lever, lever));
      const leverPlane = vectorSubtract(lever,
        vectorScale(unitAxis, vectorDot(lever, unitAxis)));
      const projectedLeverLength = Math.sqrt(
        vectorDot(leverPlane, leverPlane));
      const effectiveLeverLength = Math.max(
        projectedLeverLength, minimumLever);
      const denominator = effectiveLeverLength * effectiveLeverLength;
      const totalAngularAcceleration = vectorDot(
        unitAxis, vectorCross(lever, gravity)) / denominator;
      const localAngularAcceleration = totalAngularAcceleration / maxDepth;
      if (Number.isFinite(totalAngularAcceleration)
          && Number.isFinite(localAngularAcceleration)) {
        details.leverLength = leverLength;
        details.projectedLeverLength = projectedLeverLength;
        details.effectiveLeverLength = effectiveLeverLength;
        details.totalAngularAcceleration = totalAngularAcceleration;
        details.localAngularAcceleration = localAngularAcceleration;
        details.clamped = projectedLeverLength < minimumLever;
        if (details.clamped) diagnostics.clampedComponentCount += 1;
        if (Math.abs(totalAngularAcceleration) > TRANSLATION_EPSILON) {
          diagnostics.activeComponentCount += 1;
        }
        diagnostics.maxAbsTotalAcceleration = Math.max(
          diagnostics.maxAbsTotalAcceleration,
          Math.abs(totalAngularAcceleration));
        diagnostics.maxAbsLocalAcceleration = Math.max(
          diagnostics.maxAbsLocalAcceleration,
          Math.abs(localAngularAcceleration));
        nodeIdsForComponent(component).forEach(nodeId => {
          if (nodeId !== rootId) {
            accelerations.set(nodeId, localAngularAcceleration);
          }
        });
      }
    }
    diagnostics.components.push(details);
  });
  return {accelerationByBoneId: accelerations, diagnostics};
}

function componentTranslationLag(
    component, centerByBoneId, translationDeltaLocal, axis, strength) {
  const lever = representativeComponentLever(component, centerByBoneId);
  if (!lever) return 0;
  const displacement = vectorScale(
    translationDeltaLocal, clamp(Number(strength) || 0, 0, 1));
  const desired = vectorSubtract(lever, displacement);
  const unitAxis = translationAxisVector(axis);
  const leverPlane = vectorSubtract(lever,
    vectorScale(unitAxis, vectorDot(lever, unitAxis)));
  const desiredPlane = vectorSubtract(desired,
    vectorScale(unitAxis, vectorDot(desired, unitAxis)));
  const leverLength = Math.sqrt(vectorDot(leverPlane, leverPlane));
  const desiredLength = Math.sqrt(vectorDot(desiredPlane, desiredPlane));
  if (leverLength <= TRANSLATION_EPSILON
      || desiredLength <= TRANSLATION_EPSILON) return 0;
  return Math.atan2(
    vectorDot(unitAxis, vectorCross(leverPlane, desiredPlane)),
    vectorDot(leverPlane, desiredPlane));
}

/** Apply semantic model translation as a geometric lag in each component's
 * previous local reference frame. This helper is intentionally independent
 * of meshes, rendering, and the DOM. */
export function applyReferenceFrameTranslationDelta(
    physicsState, forest, centerByBoneId, translationDeltaLocal,
    axis, strength = 1, diagnostics = null, jointLimitByBoneId = null) {
  const delta = finiteVector(translationDeltaLocal);
  const response = Number(strength);
  if (!delta || !Number.isFinite(response)) {
    if (diagnostics) diagnostics.maxAbsLag = 0;
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  if (Math.sqrt(vectorDot(delta, delta)) <= TRANSLATION_EPSILON) {
    if (diagnostics) diagnostics.maxAbsLag = 0;
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  let diagnosticLag = 0;
  (forest?.components || []).forEach(component => {
    const totalLag = componentTranslationLag(
      component, centerByBoneId, delta, axis, response);
    if (Math.abs(totalLag) > Math.abs(diagnosticLag)) diagnosticLag = totalLag;
    const maxDepth = maxDepthForComponent(component);
    if (!totalLag || maxDepth <= 0) return;
    const rootId = Number(component.rootId);
    const localLag = totalLag / maxDepth;
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId === rootId) return;
      const joint = physicsState?.joints?.get(nodeId);
      if (!joint) return;
      joint.angle = clamp(
        (Number(joint.angle) || 0) + localLag,
        -MAX_LOCAL_ANGLE, MAX_LOCAL_ANGLE);
    });
  });
  if (diagnostics) diagnostics.maxAbsLag = Math.abs(diagnosticLag);
  applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
  return physicsState;
}

function addJointAngularVelocity(joint, delta) {
  if (!joint) return;
  const currentVelocity = Number(joint.angularVelocity);
  const impulse = Number(delta);
  joint.angularVelocity = clamp(
    (Number.isFinite(currentVelocity) ? currentVelocity : 0)
      + (Number.isFinite(impulse) ? impulse : 0),
    -MAX_ANGULAR_VELOCITY, MAX_ANGULAR_VELOCITY);
  if (joint.angle >= MAX_LOCAL_ANGLE && joint.angularVelocity > 0) {
    joint.angularVelocity = 0;
  }
  if (joint.angle <= -MAX_LOCAL_ANGLE && joint.angularVelocity < 0) {
    joint.angularVelocity = 0;
  }
}

/** Apply a semantic root velocity change as an angular velocity impulse in
 * the selected local bend plane. Unlike displacement lag, this only changes
 * the spring's existing velocity state. */
export function applyReferenceFrameLinearVelocityDelta(
    physicsState, forest, centerByBoneId, deltaVelocityLocal,
    axis, strength = 1, diagnostics = null, jointLimitByBoneId = null) {
  const delta = finiteVector(deltaVelocityLocal);
  const response = Number(strength);
  if (!delta || !Number.isFinite(response)) {
    if (diagnostics) diagnostics.maxAbsDeltaOmega = 0;
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  const unitAxis = translationAxisVector(axis);
  const deltaPlane = vectorSubtract(delta,
    vectorScale(unitAxis, vectorDot(delta, unitAxis)));
  const responseScale = clamp(response, 0, 1);
  let maxAbsDeltaOmega = 0;
  (forest?.components || []).forEach(component => {
    const lever = representativeComponentLever(component, centerByBoneId);
    const maxDepth = maxDepthForComponent(component);
    if (!lever || maxDepth <= 0) return;
    const leverPlane = vectorSubtract(lever,
      vectorScale(unitAxis, vectorDot(lever, unitAxis)));
    const denominator = vectorDot(leverPlane, leverPlane);
    if (denominator <= TRANSLATION_EPSILON) return;
    const totalDeltaOmega = responseScale * vectorDot(
      unitAxis, vectorCross(leverPlane, vectorScale(deltaPlane, -1)))
      / denominator;
    if (!Number.isFinite(totalDeltaOmega)) return;
    maxAbsDeltaOmega = Math.max(maxAbsDeltaOmega, Math.abs(totalDeltaOmega));
    const localDeltaOmega = totalDeltaOmega / maxDepth;
    const rootId = Number(component.rootId);
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId === rootId) return;
      addJointAngularVelocity(
        physicsState?.joints?.get(nodeId), localDeltaOmega);
    });
  });
  if (diagnostics) diagnostics.maxAbsDeltaOmega = maxAbsDeltaOmega;
  applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
  return physicsState;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function projectJointToLimit(joint, limitRadians = MAX_LOCAL_ANGLE) {
  if (!joint) return false;
  const candidateLimit = Number(limitRadians);
  const limit = Number.isFinite(candidateLimit)
    ? clamp(Math.max(0, candidateLimit), 0, MAX_LOCAL_ANGLE)
    : MAX_LOCAL_ANGLE;
  const candidateAngle = Number(joint.angle);
  const candidateVelocity = Number(joint.angularVelocity);
  let angle = Number.isFinite(candidateAngle) ? candidateAngle : 0;
  let velocity = Number.isFinite(candidateVelocity) ? candidateVelocity : 0;
  velocity = clamp(velocity, -MAX_ANGULAR_VELOCITY, MAX_ANGULAR_VELOCITY);
  if (limit === 0) {
    angle = 0;
    velocity = 0;
  } else if (angle > limit) {
    angle = limit;
    if (velocity > 0) velocity = 0;
  } else if (angle < -limit) {
    angle = -limit;
    if (velocity < 0) velocity = 0;
  } else if (angle === limit && velocity > 0) {
    velocity = 0;
  } else if (angle === -limit && velocity < 0) {
    velocity = 0;
  }
  const changed = angle !== candidateAngle || velocity !== candidateVelocity;
  joint.angle = angle;
  joint.angularVelocity = velocity;
  return changed;
}

export function applyPhysicsJointLimits(
    physicsState, jointLimitByBoneId = null) {
  let changed = false;
  physicsState?.joints?.forEach((joint, boneId) => {
    const limit = hasLimitForBone(jointLimitByBoneId, boneId)
      ? limitForBone(jointLimitByBoneId, boneId) : MAX_LOCAL_ANGLE;
    if (projectJointToLimit(joint, limit)) changed = true;
  });
  return physicsState;
}

export function buildPhysicsConstraintDiagnostics(
    physicsState, jointLimitByBoneId, limitDiagnostics = null) {
  const limited = jointLimitByBoneId instanceof Map;
  let atLimitCount = 0;
  let positiveLimitCount = 0;
  let negativeLimitCount = 0;
  let maxUsage = 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    if (!limited || !hasLimitForBone(jointLimitByBoneId, boneId)) return;
    const limit = limitForBone(jointLimitByBoneId, boneId);
    const angle = Number(joint.angle);
    const safeAngle = Number.isFinite(angle) ? angle : 0;
    const atLimit = limit === 0
      ? Math.abs(safeAngle) <= JOINT_LIMIT_CONTACT_EPSILON
      : Math.abs(Math.abs(safeAngle) - limit)
        <= JOINT_LIMIT_CONTACT_EPSILON;
    const usage = limit === 0 ? 1 : clamp(Math.abs(safeAngle) / limit, 0, 1);
    maxUsage = Math.max(maxUsage, usage);
    if (!atLimit) return;
    atLimitCount += 1;
    if (safeAngle > JOINT_LIMIT_CONTACT_EPSILON) positiveLimitCount += 1;
    if (safeAngle < -JOINT_LIMIT_CONTACT_EPSILON) negativeLimitCount += 1;
  });
  return {
    maxComponentBend: Number(limitDiagnostics?.maxComponentBend) || 0,
    limitedJointCount: limited ? jointLimitByBoneId.size : 0,
    atLimitCount,
    positiveLimitCount,
    negativeLimitCount,
    maxUsage,
    components: limited ? (limitDiagnostics?.components || []) : [],
  };
}

function externalAccelerationForBone(externalAccelerations, boneId) {
  if (!(externalAccelerations instanceof Map)) return 0;
  const value = Number(externalAccelerations.get(Number(boneId)));
  return Number.isFinite(value) ? value : 0;
}

function hasExternalAcceleration(externalAccelerations) {
  if (!(externalAccelerations instanceof Map)) return false;
  for (const value of externalAccelerations.values()) {
    if (Number.isFinite(Number(value))
        && Math.abs(Number(value)) > TRANSLATION_EPSILON) return true;
  }
  return false;
}

function applyExternalEquilibriumOffset(
    targets, frequencyHz, externalAngularAccelerationByBoneId) {
  if (!(externalAngularAccelerationByBoneId instanceof Map)) return targets;
  const frequency = Number(frequencyHz);
  const omega = 2 * Math.PI * (Number.isFinite(frequency)
    ? Math.max(0, frequency) : DEFAULT_PHYSICS_FREQUENCY_HZ);
  const omegaSquared = omega * omega;
  if (omegaSquared <= TRANSLATION_EPSILON) return targets;
  const result = new Map(targets);
  result.forEach((target, boneId) => {
    const external = externalAccelerationForBone(
      externalAngularAccelerationByBoneId, boneId);
    result.set(boneId, clamp(
      target + external / omegaSquared, -MAX_LOCAL_ANGLE, MAX_LOCAL_ANGLE));
  });
  return result;
}

/** Return the spring equilibrium after adding per-joint external angular
 * acceleration. The input maps are never mutated. */
export function buildPhysicsEquilibriumAngles(
    forest, targetAngleRadians, frequencyHz,
    externalAngularAccelerationByBoneId = null,
    jointLimitByBoneId = null) {
  const constrainedTargets = applyJointLimitsToAngles(
    buildPhysicsTargetAngles(forest, targetAngleRadians),
    jointLimitByBoneId);
  return applyJointLimitsToAngles(
    applyExternalEquilibriumOffset(
      constrainedTargets, frequencyHz, externalAngularAccelerationByBoneId),
    jointLimitByBoneId);
}

function jointAcceleration(
    joint, targetAngle, externalAcceleration, frequencyHz, dampingRatio) {
  const frequency = Number.isFinite(Number(frequencyHz))
    ? Math.max(0, Number(frequencyHz)) : DEFAULT_PHYSICS_FREQUENCY_HZ;
  const damping = Number.isFinite(Number(dampingRatio))
    ? Math.max(0, Number(dampingRatio)) : DEFAULT_PHYSICS_DAMPING_RATIO;
  const omega = 2 * Math.PI * frequency;
  return omega * omega * (targetAngle - joint.angle)
    - 2 * damping * omega * joint.angularVelocity
    + externalAcceleration;
}

function clampJoint(joint, limitRadians = MAX_LOCAL_ANGLE) {
  projectJointToLimit(joint, limitRadians);
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
  const targets = options.angleByBoneId instanceof Map
    ? options.angleByBoneId
    : buildPhysicsTargetAngles(forest, options.targetAngleRadians);
  const jointLimitByBoneId = options.jointLimitByBoneId;
  const constrainedTargets = applyJointLimitsToAngles(
    targets, jointLimitByBoneId);
  const externalAccelerations = options
    .externalAngularAccelerationByBoneId;
  const equilibriumTargets = applyJointLimitsToAngles(
    applyExternalEquilibriumOffset(
      constrainedTargets, frequency, externalAccelerations),
    jointLimitByBoneId);
  let maxAngleError = 0;
  let maxAngularVelocity = 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    const target = Number(constrainedTargets.get(Number(boneId))) || 0;
    const limit = hasLimitForBone(jointLimitByBoneId, boneId)
      ? limitForBone(jointLimitByBoneId, boneId) : MAX_LOCAL_ANGLE;
    const acceleration = jointAcceleration(
      joint, target, externalAccelerationForBone(
        externalAccelerations, boneId), frequency, damping);
    joint.angularVelocity += acceleration * step;
    clampJoint(joint, limit);
    joint.angle += joint.angularVelocity * step;
    clampJoint(joint, limit);
    const equilibrium = Number(equilibriumTargets.get(Number(boneId)));
    maxAngleError = Math.max(
      maxAngleError, Math.abs((Number.isFinite(equilibrium)
        ? equilibrium : target) - joint.angle));
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
  const constrainedTargets = applyJointLimitsToAngles(
    options.angleByBoneId instanceof Map
      ? options.angleByBoneId
      : buildPhysicsTargetAngles(forest, targetAngleRadians),
    options.jointLimitByBoneId);
  const targets = applyJointLimitsToAngles(
    applyExternalEquilibriumOffset(
      constrainedTargets, options.frequencyHz,
      options.externalAngularAccelerationByBoneId),
    options.jointLimitByBoneId);
  const frequency = Number(options.frequencyHz ?? DEFAULT_PHYSICS_FREQUENCY_HZ);
  if (hasExternalAcceleration(options.externalAngularAccelerationByBoneId)
      && Number.isFinite(frequency) && frequency <= 0) return false;
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
    physicsState, forest, impulseRadiansPerSecond,
    jointLimitByBoneId = null) {
  const impulse = Number(impulseRadiansPerSecond) || 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    const component = (forest?.components || []).find(item =>
      nodeIdsForComponent(item).includes(Number(boneId)));
    const maxDepth = Number(component?.maxDepth) || 0;
    const depth = Number(component?.depthById?.[boneId]);
    const scale = maxDepth > 0 && Number.isFinite(depth)
      ? Math.max(0, depth / maxDepth) : 0;
    addJointAngularVelocity(joint, impulse * scale);
  });
  applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
  return physicsState;
}

export function resetPhysicsState(physicsState) {
  physicsState?.joints?.forEach(joint => {
    joint.angle = 0;
    joint.angularVelocity = 0;
  });
  return physicsState;
}
