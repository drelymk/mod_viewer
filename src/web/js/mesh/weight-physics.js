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

const VECTOR_EPSILON = 1e-8;

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

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

function vectorLength(vector) {
  return Math.sqrt(Math.max(0, vectorDot(vector, vector)));
}

function vectorNormalize(vector, fallback = [0, 0, 0]) {
  const length = vectorLength(vector);
  return length > VECTOR_EPSILON ? vectorScale(vector, 1 / length) : [...fallback];
}

function vectorClampMagnitude(vector, maximum) {
  const length = vectorLength(vector);
  if (!Number.isFinite(length) || length <= maximum) return [...vector];
  return vectorScale(vector, maximum / length);
}

function safeVector(value, fallback = [0, 0, 0]) {
  return finiteVector(value) || [...fallback];
}

function valueForBone(collection, boneId) {
  if (collection instanceof Map) {
    return collection.get(Number(boneId)) ?? collection.get(String(boneId));
  }
  return collection?.[boneId];
}

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

export function buildPhysicsTargetRotations(forest, targetRotation = [0, 0, 0]) {
  const targets = new Map();
  const totalRotation = safeVector(targetRotation);
  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    const maxDepth = maxDepthForComponent(component);
    const localRotation = maxDepth > 0
      ? vectorScale(totalRotation, 1 / maxDepth) : [0, 0, 0];
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId !== rootId) targets.set(nodeId, [...localRotation]);
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
      || nodeId !== numericRootId).length;
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

export function applyJointLimitsToRotations(
    rotationByBoneId, jointLimitByBoneId) {
  const result = new Map();
  if (rotationByBoneId instanceof Map) {
    rotationByBoneId.forEach((rotation, boneId) => {
      result.set(boneId, [...safeVector(rotation)]);
    });
  }
  if (!(jointLimitByBoneId instanceof Map)) return result;
  result.forEach((rotation, boneId) => {
    if (!hasLimitForBone(jointLimitByBoneId, boneId)) return;
    result.set(boneId, vectorClampMagnitude(
      rotation, limitForBone(jointLimitByBoneId, boneId)));
  });
  return result;
}

export function initializePhysicsState(forest) {
  const joints = new Map();
  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId !== rootId) {
        joints.set(nodeId, {
          rotationVector: [0, 0, 0],
          angularVelocity: [0, 0, 0],
        });
      }
    });
  });
  return {joints};
}

export function physicsRotationMap(physicsState) {
  const rotations = new Map();
  physicsState?.joints?.forEach((joint, boneId) => {
    rotations.set(Number(boneId), [...safeVector(joint?.rotationVector)]);
  });
  return rotations;
}

/** Return the shortest rotation vector carrying one direction to another. */
export function rotationVectorBetween(fromValue, toValue) {
  const from = finiteVector(fromValue);
  const to = finiteVector(toValue);
  if (!from || !to) return [0, 0, 0];
  const fromLength = vectorLength(from);
  const toLength = vectorLength(to);
  if (fromLength <= VECTOR_EPSILON || toLength <= VECTOR_EPSILON) {
    return [0, 0, 0];
  }
  const fromUnit = vectorScale(from, 1 / fromLength);
  const toUnit = vectorScale(to, 1 / toLength);
  const cross = vectorCross(fromUnit, toUnit);
  const crossLength = vectorLength(cross);
  const dot = clamp(vectorDot(fromUnit, toUnit), -1, 1);
  if (crossLength > VECTOR_EPSILON) {
    return vectorScale(cross, Math.atan2(crossLength, dot) / crossLength);
  }
  if (dot >= 0) return [0, 0, 0];

  // Opposite vectors have infinitely many valid axes. Pick the basis least
  // aligned with the source to make the result deterministic.
  const basis = Math.abs(fromUnit[0]) <= Math.abs(fromUnit[1])
    && Math.abs(fromUnit[0]) <= Math.abs(fromUnit[2]) ? [1, 0, 0]
    : Math.abs(fromUnit[1]) <= Math.abs(fromUnit[2]) ? [0, 1, 0] : [0, 0, 1];
  return vectorScale(vectorNormalize(vectorCross(fromUnit, basis)), Math.PI);
}

/** Apply a root orientation change as a local 3D bend in every component. */
export function applyReferenceFrameAngularDelta(
    physicsState, forest, angularDeltaVector, strength = 1,
    jointLimitByBoneId = null) {
  const delta = finiteVector(angularDeltaVector);
  const response = Number(strength);
  if (!delta || !Number.isFinite(response)) {
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  const lag = vectorScale(delta, -clamp(response, 0, 1));
  if (vectorLength(lag) <= VECTOR_EPSILON) {
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  (forest?.components || []).forEach(component => {
    const rootId = Number(component.rootId);
    const maxDepth = maxDepthForComponent(component);
    if (maxDepth <= 0) return;
    const localLag = vectorScale(lag, 1 / maxDepth);
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId === rootId) return;
      const joint = physicsState?.joints?.get(nodeId);
      if (!joint) return;
      joint.rotationVector = vectorClampMagnitude(
        vectorAdd(safeVector(joint.rotationVector), localLag), MAX_LOCAL_ANGLE);
    });
  });
  applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
  return physicsState;
}

function centerForBone(centerByBoneId, boneId) {
  const candidate = centerByBoneId?.get?.(boneId)
    ?? centerByBoneId?.get?.(String(boneId))
    ?? centerByBoneId?.[boneId];
  return finiteVector(candidate);
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

function diagnosticComponentId(component, fallback) {
  const componentId = Number(component?.componentId);
  return Number.isFinite(componentId) ? componentId : fallback;
}

function diagnosticRootId(component) {
  const rootId = Number(component?.rootId);
  return Number.isFinite(rootId) ? rootId : null;
}

function gravityDiagnostics(componentId, rootId, maxDepth) {
  return {
    componentId,
    rootId,
    maxDepth,
    leverLength: 0,
    effectiveLeverLength: 0,
    totalAngularAccelerationVector: [0, 0, 0],
    localAngularAccelerationVector: [0, 0, 0],
    totalAngularAccelerationMagnitude: 0,
    localAngularAccelerationMagnitude: 0,
    clamped: false,
  };
}

/** Build 3D angular acceleration inputs from rest-space component levers. */
export function buildGravityAngularAccelerations(
    forest, centerByBoneId, gravityLocal,
    {referenceRadius, gravityScale = 1} = {}) {
  const direction = finiteVector(gravityLocal);
  const radius = Number(referenceRadius);
  const scale = Number(gravityScale);
  const accelerations = new Map();
  const diagnostics = {
    componentCount: (forest?.components || []).length,
    activeComponentCount: 0,
    clampedComponentCount: 0,
    maxTotalAccelerationMagnitude: 0,
    maxLocalAccelerationMagnitude: 0,
    referenceRadius: Number.isFinite(radius) && radius > 0 ? radius : 0,
    minLeverRatio: MIN_GRAVITY_LEVER_RATIO,
    components: [],
  };
  const addEmptyDiagnostics = () => {
    (forest?.components || []).forEach((component, index) => {
      diagnostics.components.push(gravityDiagnostics(
        diagnosticComponentId(component, index), diagnosticRootId(component),
        maxDepthForComponent(component)));
    });
  };
  if (!direction || !Number.isFinite(radius) || radius <= 0
      || !Number.isFinite(scale) || scale < 0) {
    addEmptyDiagnostics();
    return {accelerationByBoneId: accelerations, diagnostics};
  }
  const directionLength = vectorLength(direction);
  if (directionLength <= VECTOR_EPSILON) {
    addEmptyDiagnostics();
    return {accelerationByBoneId: accelerations, diagnostics};
  }

  const gravity = vectorScale(
    direction, STANDARD_GRAVITY * radius * scale / directionLength);
  const minimumLever = radius * MIN_GRAVITY_LEVER_RATIO;
  (forest?.components || []).forEach((component, index) => {
    const componentId = diagnosticComponentId(component, index);
    const rootId = Number(component?.rootId);
    const maxDepth = maxDepthForComponent(component);
    const details = gravityDiagnostics(
      componentId, diagnosticRootId(component), maxDepth);
    const lever = representativeComponentLever(component, centerByBoneId);
    if (lever && maxDepth > 0) {
      const leverLength = vectorLength(lever);
      const effectiveLeverLength = Math.max(leverLength, minimumLever);
      const denominator = effectiveLeverLength * effectiveLeverLength;
      const totalAcceleration = vectorScale(
        vectorCross(lever, gravity), 1 / denominator);
      const localAcceleration = vectorScale(totalAcceleration, 1 / maxDepth);
      if (totalAcceleration.every(Number.isFinite)
          && localAcceleration.every(Number.isFinite)) {
        details.leverLength = leverLength;
        details.effectiveLeverLength = effectiveLeverLength;
        details.totalAngularAccelerationVector = totalAcceleration;
        details.localAngularAccelerationVector = localAcceleration;
        details.totalAngularAccelerationMagnitude = vectorLength(totalAcceleration);
        details.localAngularAccelerationMagnitude = vectorLength(localAcceleration);
        details.clamped = leverLength < minimumLever;
        if (details.clamped) diagnostics.clampedComponentCount += 1;
        if (details.totalAngularAccelerationMagnitude > VECTOR_EPSILON) {
          diagnostics.activeComponentCount += 1;
        }
        diagnostics.maxTotalAccelerationMagnitude = Math.max(
          diagnostics.maxTotalAccelerationMagnitude,
          details.totalAngularAccelerationMagnitude);
        diagnostics.maxLocalAccelerationMagnitude = Math.max(
          diagnostics.maxLocalAccelerationMagnitude,
          details.localAngularAccelerationMagnitude);
        nodeIdsForComponent(component).forEach(nodeId => {
          if (nodeId !== rootId) accelerations.set(nodeId, [...localAcceleration]);
        });
      }
    }
    diagnostics.components.push(details);
  });
  return {accelerationByBoneId: accelerations, diagnostics};
}

/** Apply translation lag by rotating each component's lever toward its new
 * rest-space direction. The shortest 3D rotation is used for every joint. */
export function applyReferenceFrameTranslationDelta(
    physicsState, forest, centerByBoneId, translationDeltaLocal,
    strength = 1, diagnostics = null, jointLimitByBoneId = null) {
  const delta = finiteVector(translationDeltaLocal);
  const response = Number(strength);
  if (!delta || !Number.isFinite(response)) {
    if (diagnostics) {
      diagnostics.maxLagRotationVector = [0, 0, 0];
      diagnostics.maxLagRotationMagnitude = 0;
    }
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  const displacement = vectorScale(delta, clamp(response, 0, 1));
  if (vectorLength(displacement) <= VECTOR_EPSILON) {
    if (diagnostics) {
      diagnostics.maxLagRotationVector = [0, 0, 0];
      diagnostics.maxLagRotationMagnitude = 0;
    }
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  let largestLag = [0, 0, 0];
  (forest?.components || []).forEach(component => {
    const lever = representativeComponentLever(component, centerByBoneId);
    const maxDepth = maxDepthForComponent(component);
    if (!lever || maxDepth <= 0) return;
    const localLag = vectorScale(rotationVectorBetween(
      lever, vectorSubtract(lever, displacement)), 1 / maxDepth);
    if (vectorLength(localLag) > vectorLength(largestLag)) largestLag = localLag;
    const rootId = Number(component.rootId);
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId === rootId) return;
      const joint = physicsState?.joints?.get(nodeId);
      if (!joint) return;
      joint.rotationVector = vectorClampMagnitude(
        vectorAdd(safeVector(joint.rotationVector), localLag), MAX_LOCAL_ANGLE);
    });
  });
  if (diagnostics) {
    diagnostics.maxLagRotationVector = [...largestLag];
    diagnostics.maxLagRotationMagnitude = vectorLength(largestLag);
  }
  applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
  return physicsState;
}

function addJointAngularVelocity(joint, delta) {
  if (!joint) return;
  const currentVelocity = safeVector(joint.angularVelocity);
  joint.angularVelocity = vectorClampMagnitude(
    vectorAdd(currentVelocity, safeVector(delta)), MAX_ANGULAR_VELOCITY);
}

/** Apply a root velocity change as a 3D angular-velocity impulse. */
export function applyReferenceFrameLinearVelocityDelta(
    physicsState, forest, centerByBoneId, deltaVelocityLocal,
    strength = 1, diagnostics = null, jointLimitByBoneId = null) {
  const delta = finiteVector(deltaVelocityLocal);
  const response = Number(strength);
  if (!delta || !Number.isFinite(response)) {
    if (diagnostics) {
      diagnostics.maxDeltaAngularVelocityVector = [0, 0, 0];
      diagnostics.maxDeltaAngularVelocityMagnitude = 0;
    }
    applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
    return physicsState;
  }
  const responseScale = clamp(response, 0, 1);
  let largestImpulse = [0, 0, 0];
  (forest?.components || []).forEach(component => {
    const lever = representativeComponentLever(component, centerByBoneId);
    const maxDepth = maxDepthForComponent(component);
    if (!lever || maxDepth <= 0) return;
    const denominator = Math.max(vectorDot(lever, lever), VECTOR_EPSILON);
    const totalImpulse = vectorScale(
      vectorCross(lever, vectorScale(delta, -responseScale)),
      1 / denominator);
    if (!totalImpulse.every(Number.isFinite)) return;
    const localImpulse = vectorScale(totalImpulse, 1 / maxDepth);
    if (vectorLength(localImpulse) > vectorLength(largestImpulse)) {
      largestImpulse = localImpulse;
    }
    const rootId = Number(component.rootId);
    nodeIdsForComponent(component).forEach(nodeId => {
      if (nodeId === rootId) return;
      addJointAngularVelocity(
        physicsState?.joints?.get(nodeId), localImpulse);
    });
  });
  if (diagnostics) {
    diagnostics.maxDeltaAngularVelocityVector = [...largestImpulse];
    diagnostics.maxDeltaAngularVelocityMagnitude = vectorLength(largestImpulse);
  }
  applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
  return physicsState;
}

function projectJointToLimit(joint, limitRadians = MAX_LOCAL_ANGLE) {
  if (!joint) return false;
  const candidateLimit = Number(limitRadians);
  const limit = Number.isFinite(candidateLimit)
    ? clamp(Math.max(0, candidateLimit), 0, MAX_LOCAL_ANGLE)
    : MAX_LOCAL_ANGLE;
  const candidateRotation = finiteVector(joint.rotationVector);
  const candidateVelocity = finiteVector(joint.angularVelocity);
  let rotation = vectorClampMagnitude(
    candidateRotation || [0, 0, 0], limit);
  let velocity = vectorClampMagnitude(
    candidateVelocity || [0, 0, 0], MAX_ANGULAR_VELOCITY);
  if (limit <= VECTOR_EPSILON) {
    rotation = [0, 0, 0];
    velocity = [0, 0, 0];
  } else if (vectorLength(rotation) >= limit - JOINT_LIMIT_CONTACT_EPSILON) {
    const radial = vectorNormalize(rotation);
    const outward = vectorDot(velocity, radial);
    if (outward > 0) velocity = vectorSubtract(velocity, vectorScale(radial, outward));
  }
  const changed = !candidateRotation
    || rotation.some((value, index) => value !== candidateRotation[index])
    || !candidateVelocity
    || velocity.some((value, index) => value !== candidateVelocity[index]);
  joint.rotationVector = rotation;
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
  let maxUsage = 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    if (!limited || !hasLimitForBone(jointLimitByBoneId, boneId)) return;
    const limit = limitForBone(jointLimitByBoneId, boneId);
    const magnitude = vectorLength(safeVector(joint?.rotationVector));
    const atLimit = limit <= VECTOR_EPSILON
      ? magnitude <= JOINT_LIMIT_CONTACT_EPSILON
      : Math.abs(magnitude - limit) <= JOINT_LIMIT_CONTACT_EPSILON;
    const usage = limit <= VECTOR_EPSILON ? 1 : clamp(magnitude / limit, 0, 1);
    maxUsage = Math.max(maxUsage, usage);
    if (atLimit) atLimitCount += 1;
  });
  return {
    maxComponentBend: Number(limitDiagnostics?.maxComponentBend) || 0,
    limitedJointCount: limited ? jointLimitByBoneId.size : 0,
    atLimitCount,
    maxUsage,
    components: limited ? (limitDiagnostics?.components || []) : [],
  };
}

function externalAccelerationForBone(externalAccelerations, boneId) {
  return safeVector(valueForBone(externalAccelerations, boneId));
}

function hasExternalAcceleration(externalAccelerations) {
  if (!(externalAccelerations instanceof Map)) return false;
  for (const value of externalAccelerations.values()) {
    if (vectorLength(safeVector(value)) > VECTOR_EPSILON) return true;
  }
  return false;
}

function applyExternalEquilibriumOffset(
    targets, frequencyHz, externalAngularAccelerationByBoneId) {
  if (!(externalAngularAccelerationByBoneId instanceof Map)) {
    return new Map(targets);
  }
  const frequency = Number(frequencyHz);
  const omega = 2 * Math.PI * (Number.isFinite(frequency)
    ? Math.max(0, frequency) : DEFAULT_PHYSICS_FREQUENCY_HZ);
  const omegaSquared = omega * omega;
  if (omegaSquared <= VECTOR_EPSILON) return new Map(targets);
  const result = new Map();
  targets.forEach((target, boneId) => {
    result.set(boneId, vectorClampMagnitude(vectorAdd(
      safeVector(target), vectorScale(
        externalAccelerationForBone(externalAngularAccelerationByBoneId, boneId),
        1 / omegaSquared)), MAX_LOCAL_ANGLE));
  });
  return result;
}

/** Return spring equilibria after adding per-joint external acceleration. */
export function buildPhysicsEquilibriumRotations(
    forest, targetRotation, frequencyHz,
    externalAngularAccelerationByBoneId = null,
    jointLimitByBoneId = null) {
  const constrainedTargets = applyJointLimitsToRotations(
    buildPhysicsTargetRotations(forest, targetRotation), jointLimitByBoneId);
  return applyJointLimitsToRotations(
    applyExternalEquilibriumOffset(
      constrainedTargets, frequencyHz, externalAngularAccelerationByBoneId),
    jointLimitByBoneId);
}

function jointAcceleration(
    joint, targetRotation, externalAcceleration, frequencyHz, dampingRatio) {
  const frequency = Number.isFinite(Number(frequencyHz))
    ? Math.max(0, Number(frequencyHz)) : DEFAULT_PHYSICS_FREQUENCY_HZ;
  const damping = Number.isFinite(Number(dampingRatio))
    ? Math.max(0, Number(dampingRatio)) : DEFAULT_PHYSICS_DAMPING_RATIO;
  const omega = 2 * Math.PI * frequency;
  return vectorAdd(
    vectorScale(vectorSubtract(targetRotation, safeVector(joint.rotationVector)),
      omega * omega),
    vectorAdd(
      vectorScale(safeVector(joint.angularVelocity), -2 * damping * omega),
      externalAcceleration));
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
  const targets = options.targetRotationByBoneId instanceof Map
    ? options.targetRotationByBoneId
    : buildPhysicsTargetRotations(forest, options.targetRotation);
  const jointLimitByBoneId = options.jointLimitByBoneId;
  const constrainedTargets = applyJointLimitsToRotations(
    targets, jointLimitByBoneId);
  const externalAccelerations = options
    .externalAngularAccelerationByBoneId;
  const equilibriumTargets = applyJointLimitsToRotations(
    applyExternalEquilibriumOffset(
      constrainedTargets, frequency, externalAccelerations),
    jointLimitByBoneId);
  let maxRotationErrorMagnitude = 0;
  let maxAngularVelocityMagnitude = 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    const target = safeVector(valueForBone(constrainedTargets, boneId));
    const limit = hasLimitForBone(jointLimitByBoneId, boneId)
      ? limitForBone(jointLimitByBoneId, boneId) : MAX_LOCAL_ANGLE;
    const acceleration = jointAcceleration(
      joint, target, externalAccelerationForBone(
        externalAccelerations, boneId), frequency, damping);
    const velocity = vectorAdd(
      safeVector(joint.angularVelocity), vectorScale(acceleration, step));
    joint.angularVelocity = vectorClampMagnitude(velocity, MAX_ANGULAR_VELOCITY);
    projectJointToLimit(joint, limit);
    joint.rotationVector = vectorClampMagnitude(vectorAdd(
      safeVector(joint.rotationVector),
      vectorScale(joint.angularVelocity, step)), limit);
    projectJointToLimit(joint, limit);
    const equilibrium = safeVector(valueForBone(equilibriumTargets, boneId), target);
    maxRotationErrorMagnitude = Math.max(maxRotationErrorMagnitude,
      vectorLength(vectorSubtract(equilibrium, joint.rotationVector)));
    maxAngularVelocityMagnitude = Math.max(maxAngularVelocityMagnitude,
      vectorLength(joint.angularVelocity));
  });
  return {maxRotationErrorMagnitude, maxAngularVelocityMagnitude};
}

export function isPhysicsSettled(
    physicsState, forest, targetRotation = [0, 0, 0], options = {}) {
  const candidateRotationTolerance = Number(
    options.rotationTolerance ?? options.angleTolerance ?? DEFAULT_ANGLE_TOLERANCE);
  const rotationTolerance = Number.isFinite(candidateRotationTolerance)
    ? Math.max(0, candidateRotationTolerance) : DEFAULT_ANGLE_TOLERANCE;
  const candidateVelocityTolerance = Number(
    options.velocityTolerance ?? DEFAULT_VELOCITY_TOLERANCE);
  const velocityTolerance = Number.isFinite(candidateVelocityTolerance)
    ? Math.max(0, candidateVelocityTolerance) : DEFAULT_VELOCITY_TOLERANCE;
  const constrainedTargets = applyJointLimitsToRotations(
    options.targetRotationByBoneId instanceof Map
      ? options.targetRotationByBoneId
      : buildPhysicsTargetRotations(forest, targetRotation),
    options.jointLimitByBoneId);
  const targets = applyJointLimitsToRotations(
    applyExternalEquilibriumOffset(
      constrainedTargets, options.frequencyHz,
      options.externalAngularAccelerationByBoneId),
    options.jointLimitByBoneId);
  const frequency = Number(options.frequencyHz ?? DEFAULT_PHYSICS_FREQUENCY_HZ);
  if (hasExternalAcceleration(options.externalAngularAccelerationByBoneId)
      && Number.isFinite(frequency) && frequency <= 0) return false;
  let maxRotationErrorMagnitude = 0;
  let maxAngularVelocityMagnitude = 0;
  physicsState?.joints?.forEach((joint, boneId) => {
    const target = safeVector(valueForBone(targets, boneId));
    maxRotationErrorMagnitude = Math.max(maxRotationErrorMagnitude,
      vectorLength(vectorSubtract(target, safeVector(joint.rotationVector))));
    maxAngularVelocityMagnitude = Math.max(maxAngularVelocityMagnitude,
      vectorLength(safeVector(joint.angularVelocity)));
  });
  return maxRotationErrorMagnitude < rotationTolerance
    && maxAngularVelocityMagnitude < velocityTolerance;
}

export function applyPhysicsKick(
    physicsState, forest, impulseVector, jointLimitByBoneId = null) {
  const impulse = safeVector(impulseVector);
  physicsState?.joints?.forEach((joint, boneId) => {
    const component = (forest?.components || []).find(item =>
      nodeIdsForComponent(item).includes(Number(boneId)));
    const maxDepth = Number(component?.maxDepth) || 0;
    const depth = Number(component?.depthById?.[boneId]);
    const scale = maxDepth > 0 && Number.isFinite(depth)
      ? Math.max(0, depth / maxDepth) : 0;
    addJointAngularVelocity(joint, vectorScale(impulse, scale));
  });
  applyPhysicsJointLimits(physicsState, jointLimitByBoneId);
  return physicsState;
}

export function resetPhysicsState(physicsState) {
  physicsState?.joints?.forEach(joint => {
    joint.rotationVector = [0, 0, 0];
    joint.angularVelocity = [0, 0, 0];
  });
  return physicsState;
}
