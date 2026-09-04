// Pure validation, resolution, and swing/twist constraint helpers for the
// inferred Rig. Quaternions use [x, y, z, w] and angles use degrees.
//
// Constraints apply to a ModelJoint's local pose relative to its inferred rest
// frame. Swing is measured from the local +Y axis:
//   swingX = atan2(direction.z, direction.y)
//   swingZ = atan2(-direction.x, direction.y)
// The two swing coordinates are reconstructed by applying X swing first and
// then Z swing. This convention is deliberately explicit so UI and persistence
// layers can share the same signs without depending on Three.js Euler orders.

const EPSILON = 1e-12;
const ANGLE_EPSILON = 1e-8;
const MAX_ANGLE = 180;
const MODE = 'swing_twist';
const IDENTITY = [0, 0, 0, 1];
const SWING_AXIS = [0, 1, 0];
const RAD_TO_DEG = 180 / Math.PI;
const DEG_TO_RAD = Math.PI / 180;

function isObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function signatureValue(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed) || !parsed.length
        || parsed.some(item => typeof item !== 'string' || !item)) {
      return null;
    }
  } catch (_error) {
    return null;
  }
  return value;
}

function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function rangeValue(value, field) {
  if (!Array.isArray(value) || value.length !== 2) {
    return {valid: false, reason: `invalid_${field}`};
  }
  const min = finiteNumber(value[0]);
  const max = finiteNumber(value[1]);
  if (min === null || max === null || min < -MAX_ANGLE
      || max > MAX_ANGLE || min > max || min > 0 || max < 0) {
    return {valid: false, reason: `invalid_${field}`};
  }
  return {valid: true, min, max};
}

function persistedConstraint(raw) {
  if (!isObject(raw)) return null;
  if ('joint_signature' in raw || 'swing_x' in raw
      || 'swing_z' in raw || 'twist' in raw) return raw;
  if (!('jointSignature' in raw)) return raw;
  return {
    joint_signature: raw.jointSignature,
    enabled: raw.enabled,
    mode: raw.mode,
    swing_x: [raw.swingXMin, raw.swingXMax],
    swing_z: [raw.swingZMin, raw.swingZMax],
    twist: [raw.twistMin, raw.twistMax],
  };
}

function checkConstraint(raw) {
  const value = persistedConstraint(raw);
  if (!isObject(value)) return {valid: false, error: 'invalid_constraint'};
  const jointSignature = signatureValue(value.joint_signature);
  if (!jointSignature) return {valid: false, error: 'invalid_signature'};
  if (typeof value.enabled !== 'boolean') {
    return {valid: false, error: 'invalid_enabled'};
  }
  if (value.mode !== MODE) {
    return {valid: false, error: 'unsupported_constraint_mode'};
  }
  const swingX = rangeValue(value.swing_x, 'swing_x');
  if (!swingX.valid) return {valid: false, error: swingX.reason};
  const swingZ = rangeValue(value.swing_z, 'swing_z');
  if (!swingZ.valid) return {valid: false, error: swingZ.reason};
  const twist = rangeValue(value.twist, 'twist');
  if (!twist.valid) return {valid: false, error: twist.reason};
  return {
    valid: true,
    value: {
      jointSignature,
      enabled: value.enabled,
      mode: MODE,
      swingXMin: swingX.min,
      swingXMax: swingX.max,
      swingZMin: swingZ.min,
      swingZMax: swingZ.max,
      twistMin: twist.min,
      twistMax: twist.max,
    },
  };
}

/** Validate and normalize one persisted or runtime constraint record. */
export function validateRigConstraint(raw) {
  const checked = checkConstraint(raw);
  return checked.valid
    ? {valid: true, value: checked.value, error: null}
    : {valid: false, value: null, error: checked.error};
}

/** Return the runtime form of a valid constraint, or null when malformed. */
export function normalizeRigConstraint(raw) {
  const checked = checkConstraint(raw);
  return checked.valid ? checked.value : null;
}

/** Build an exact signature lookup and report ambiguous current joints. */
export function buildConstraintSignatureIndex(modelRig) {
  const resolvedBySignature = new Map();
  const ambiguousSignatures = new Set();
  for (const joint of modelRig?.joints || []) {
    const signature = signatureValue(joint?.signature ?? joint?.jointSignature);
    const jointId = Number(joint?.jointId);
    if (!signature || !Number.isInteger(jointId)) continue;
    if (ambiguousSignatures.has(signature)) continue;
    if (resolvedBySignature.has(signature)) {
      resolvedBySignature.delete(signature);
      ambiguousSignatures.add(signature);
      continue;
    }
    resolvedBySignature.set(signature, jointId);
  }
  return {resolvedBySignature, ambiguousSignatures,
    index: resolvedBySignature};
}

function status(entry, reason, jointId = null) {
  return {
    type: 'constraint',
    jointSignature: entry?.joint_signature ?? entry?.jointSignature ?? null,
    reason,
    status: reason || 'resolved',
    ...(jointId === null ? {} : {jointId}),
  };
}

function constraintEntries(value) {
  if (value instanceof Map) return [...value.entries()];
  if (Array.isArray(value)) return value.map(entry => [null, entry]);
  return [];
}

/** Resolve stored constraints by exact ModelJoint signature. */
export function resolveRigConstraints(modelRig, storedConstraints) {
  const lookup = buildConstraintSignatureIndex(modelRig);
  const resolved = [];
  const statuses = [];
  const seen = new Set();
  for (const [mapKey, entry] of constraintEntries(storedConstraints)) {
    const checked = checkConstraint(entry);
    if (!checked.valid) {
      statuses.push(status(entry, checked.error));
      continue;
    }
    const constraint = checked.value;
    if (seen.has(constraint.jointSignature)) {
      statuses.push(status(entry, 'duplicate_constraint'));
      continue;
    }
    seen.add(constraint.jointSignature);
    if (lookup.ambiguousSignatures.has(constraint.jointSignature)) {
      statuses.push(status(entry, 'ambiguous_joint_signature'));
      continue;
    }
    const jointId = lookup.resolvedBySignature.get(constraint.jointSignature);
    if (!Number.isInteger(jointId)) {
      statuses.push(status(entry, 'joint_not_found'));
      continue;
    }
    resolved.push({...constraint, jointId});
    statuses.push(status(entry, null, jointId));
  }
  if (!Array.isArray(storedConstraints) && !(storedConstraints instanceof Map)) {
    statuses.push(status(null, 'invalid_constraint_collection'));
  }
  const skipped = statuses.filter(item => item.reason);
  const constraintByJointId = new Map(
    resolved.map(constraint => [constraint.jointId, constraint]));
  return {
    success: true,
    constraints: resolved,
    resolved,
    constraintByJointId,
    resolvedBySignature: lookup.resolvedBySignature,
    ambiguousSignatures: lookup.ambiguousSignatures,
    statuses,
    skipped,
    appliedCount: resolved.length,
    skippedCount: skipped.length,
  };
}

function quaternionValues(value) {
  const values = value?.isQuaternion
    ? [value.x, value.y, value.z, value.w]
    : Array.isArray(value) || ArrayBuffer.isView(value)
      ? [...value] : isObject(value)
        ? [value.x, value.y, value.z, value.w] : null;
  if (!values || values.length !== 4
      || values.some(item => typeof item !== 'number'
        || !Number.isFinite(item))) return null;
  const length = Math.hypot(...values);
  if (!Number.isFinite(length) || length <= EPSILON) return null;
  const normalized = values.map(item => item / length);
  const firstNonZero = normalized.find(item => Math.abs(item) > EPSILON);
  if (normalized[3] < -EPSILON
      || Math.abs(normalized[3]) <= EPSILON && firstNonZero < 0) {
    return normalized.map(item => -item);
  }
  return normalized;
}

/** Normalize and canonicalize q/-q. Returns null for an invalid quaternion. */
export function normalizeRigQuaternion(value) {
  return quaternionValues(value);
}

function quaternionMultiply(left, right) {
  const [x1, y1, z1, w1] = left;
  const [x2, y2, z2, w2] = right;
  return [
    w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
    w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
    w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
  ];
}

function quaternionConjugate(value) {
  return [-value[0], -value[1], -value[2], value[3]];
}

function normalizeVector(value) {
  const length = Math.hypot(...value);
  return Number.isFinite(length) && length > EPSILON
    ? value.map(item => item / length) : null;
}

function rotateVector(quaternion, vector) {
  const rotated = quaternionMultiply(
    quaternionMultiply(quaternion, [...vector, 0]),
    quaternionConjugate(quaternion));
  return rotated.slice(0, 3);
}

function canonicalDegrees(value) {
  let result = value;
  while (result > MAX_ANGLE) result -= 2 * MAX_ANGLE;
  while (result < -MAX_ANGLE) result += 2 * MAX_ANGLE;
  if (Math.abs(result) <= ANGLE_EPSILON) return 0;
  return result;
}

function axisAngle(axis, degrees) {
  const halfAngle = degrees * DEG_TO_RAD / 2;
  const sine = Math.sin(halfAngle);
  return [axis[0] * sine, axis[1] * sine, axis[2] * sine,
    Math.cos(halfAngle)];
}

function quaternionFromUnitVectors(from, to) {
  const dot = Math.max(-1, Math.min(1,
    from[0] * to[0] + from[1] * to[1] + from[2] * to[2]));
  if (dot > 1 - EPSILON) return [...IDENTITY];
  if (dot < -1 + EPSILON) return [1, 0, 0, 0];
  const cross = [
    from[1] * to[2] - from[2] * to[1],
    from[2] * to[0] - from[0] * to[2],
    from[0] * to[1] - from[1] * to[0],
  ];
  return quaternionValues([...cross, 1 + dot]) || [...IDENTITY];
}

/**
 * Decompose a local pose into swing direction and signed +Y twist.
 * The returned swingX, swingZ, and twist values are degrees in [-180, 180].
 */
export function decomposeSwingTwist(value) {
  const quaternion = quaternionValues(value);
  if (!quaternion) {
    return {
      success: false,
      quaternion: null,
      swing: null,
      twist: null,
      swingDirection: null,
      swingX: null,
      swingZ: null,
      twistAngle: null,
    };
  }
  const projected = [0, quaternion[1], 0, quaternion[3]];
  const twist = quaternionValues(projected) || [...IDENTITY];
  const swing = quaternionValues(
    quaternionMultiply(quaternion, quaternionConjugate(twist)))
    || [...IDENTITY];
  const direction = normalizeVector(rotateVector(swing, SWING_AXIS))
    || [...SWING_AXIS];
  return {
    success: true,
    quaternion,
    swing,
    twist,
    swingDirection: direction,
    swingX: canonicalDegrees(Math.atan2(direction[2], direction[1])
      * RAD_TO_DEG),
    swingZ: canonicalDegrees(Math.atan2(-direction[0], direction[1])
      * RAD_TO_DEG),
    twistAngle: canonicalDegrees(2 * Math.atan2(twist[1], twist[3])
      * RAD_TO_DEG),
  };
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function directionFromSwing(swingX, swingZ) {
  const x = swingX * DEG_TO_RAD;
  const z = swingZ * DEG_TO_RAD;
  return normalizeVector([
    -Math.sin(z) * Math.cos(x),
    Math.cos(z) * Math.cos(x),
    Math.sin(x),
  ]) || [...SWING_AXIS];
}

function swingForAngles(swingX, swingZ) {
  const direction = directionFromSwing(swingX, swingZ);
  return {
    direction,
    quaternion: quaternionFromUnitVectors(SWING_AXIS, direction),
  };
}

function within(value, min, max) {
  return value >= min - ANGLE_EPSILON && value <= max + ANGLE_EPSILON;
}

function swingCandidate(swingX, swingZ, constraint) {
  // Independently clamped spherical coordinates do not describe a rectangle
  // on every part of the sphere. Back off toward neutral when the reconstructed
  // direction crosses either requested limit; neutral is always valid because
  // validation requires every range to contain zero.
  let best = swingForAngles(0, 0);
  let bestMeasured = decomposeSwingTwist(best.quaternion);
  const full = swingForAngles(swingX, swingZ);
  const fullMeasured = decomposeSwingTwist(full.quaternion);
  if (within(fullMeasured.swingX, constraint.swingXMin,
    constraint.swingXMax)
      && within(fullMeasured.swingZ, constraint.swingZMin,
        constraint.swingZMax)) {
    return {...full, measured: fullMeasured};
  }
  let low = 0;
  let high = 1;
  for (let index = 0; index < 18; index += 1) {
    const factor = (low + high) / 2;
    const candidate = swingForAngles(swingX * factor, swingZ * factor);
    const measured = decomposeSwingTwist(candidate.quaternion);
    if (within(measured.swingX, constraint.swingXMin,
      constraint.swingXMax)
        && within(measured.swingZ, constraint.swingZMin,
          constraint.swingZMax)) {
      best = candidate;
      bestMeasured = measured;
      low = factor;
    } else {
      high = factor;
    }
  }
  return { ...best, measured: bestMeasured };
}

/** Clamp a quaternion and return decomposition diagnostics for the result. */
export function clampSwingTwist(value, rawConstraint) {
  const quaternion = quaternionValues(value);
  if (!quaternion) {
    return {
      success: false,
      quaternion: null,
      clamped: false,
      diagnostic: 'invalid_quaternion',
    };
  }
  const input = decomposeSwingTwist(quaternion);
  const checked = rawConstraint === undefined || rawConstraint === null
    ? {valid: true, value: null}
    : checkConstraint(rawConstraint);
  if (!checked.valid) {
    return {success: true, quaternion, clamped: false,
      diagnostic: checked.error, input, output: input};
  }
  if (!checked.value || !checked.value.enabled) {
    return {success: true, quaternion, clamped: false,
      diagnostic: checked.value ? 'disabled' : 'no_constraint',
      input, output: input};
  }
  const constraint = checked.value;
  const target = {
    swingX: clamp(input.swingX, constraint.swingXMin, constraint.swingXMax),
    swingZ: clamp(input.swingZ, constraint.swingZMin, constraint.swingZMax),
    twist: clamp(input.twistAngle, constraint.twistMin, constraint.twistMax),
  };
  const swing = swingCandidate(target.swingX, target.swingZ, constraint);
  const constrained = quaternionValues(quaternionMultiply(
    swing.quaternion, axisAngle([0, 1, 0], target.twist)))
    || [...IDENTITY];
  const output = decomposeSwingTwist(constrained);
  const orientationDelta = Math.abs(quaternion[0] * constrained[0]
    + quaternion[1] * constrained[1]
    + quaternion[2] * constrained[2]
    + quaternion[3] * constrained[3]);
  return {
    success: true,
    quaternion: constrained,
    clamped: orientationDelta < 1 - ANGLE_EPSILON,
    diagnostic: null,
    input,
    target,
    output,
  };
}

/** Constrain a local ModelJoint pose, returning a normalized [x, y, z, w]. */
export function constrainLocalPoseQuaternion(value, constraint) {
  return clampSwingTwist(value, constraint).quaternion;
}

function persistedFromConstraint(raw, jointSignature = null) {
  const checked = checkConstraint(raw);
  if (!checked.valid) return null;
  const value = checked.value;
  return {
    joint_signature: jointSignature || value.jointSignature,
    enabled: value.enabled,
    mode: value.mode,
    swing_x: [value.swingXMin, value.swingXMax],
    swing_z: [value.swingZMin, value.swingZMax],
    twist: [value.twistMin, value.twistMax],
  };
}

/** Serialize resolved constraints using stable signatures, never runtime IDs. */
export function serializeRigConstraints(modelRig, constraints) {
  const lookup = buildConstraintSignatureIndex(modelRig);
  const entries = constraintEntries(constraints);
  const serialized = [];
  const seen = new Set();
  for (const [mapKey, raw] of entries) {
    const keyJointId = mapKey === null || mapKey === undefined
      ? null : Number(mapKey);
    const keyJoint = Number.isInteger(keyJointId)
      ? (modelRig?.joints || []).find(joint =>
        Number(joint?.jointId) === keyJointId) : null;
    const keySignature = signatureValue(
      keyJoint?.signature ?? keyJoint?.jointSignature);
    const rawSignature = raw?.joint_signature ?? raw?.jointSignature;
    const signature = signatureValue(rawSignature) || keySignature;
    if (!signature || seen.has(signature)
        || lookup.ambiguousSignatures.has(signature)
        || !lookup.resolvedBySignature.has(signature)) continue;
    const record = persistedFromConstraint(raw, signature);
    if (!record) continue;
    seen.add(signature);
    serialized.push(record);
  }
  serialized.sort((left, right) => left.joint_signature.localeCompare(
    right.joint_signature));
  return serialized;
}

export const RIG_CONSTRAINT_MODE = MODE;
export const RIG_CONSTRAINT_MAX_ANGLE = MAX_ANGLE;
