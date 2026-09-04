// Pure validation, resolution, and swing/twist constraint helpers for the
// inferred Rig. Quaternions use [x, y, z, w] and angles use degrees.
//
// Constraints apply to a ModelJoint's local pose relative to its inferred rest
// frame. Swing is represented as a rotation vector in the local X/Z plane:
// swingX and swingZ are the signed rotation-vector components around the
// inferred local X and Z axes. This representation has an exact quaternion
// round trip and does not depend on a Three.js Euler order.

const EPSILON = 1e-12;
const SINGULAR_EPSILON = 1e-10;
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
  if (normalized[3] < 0
      || normalized[3] === 0 && firstNonZero < 0) {
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

/**
 * Decompose a local pose into swing direction and signed +Y twist.
 * The returned swingX, swingZ, and twist values are rotation-vector degrees
 * in [-180, 180]. A quaternion whose swing/twist split is indeterminate at a
 * half-turn is returned unchanged with success=false.
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
  const twistProjectionLength = Math.hypot(quaternion[1], quaternion[3]);
  if (!Number.isFinite(twistProjectionLength)
      || twistProjectionLength <= SINGULAR_EPSILON) {
    return {
      success: false,
      quaternion,
      swing: null,
      twist: null,
      swingDirection: null,
      swingAxis: null,
      swingAngle: null,
      swingX: null,
      swingZ: null,
      twistAngle: null,
      diagnostic: 'swing_twist_singular',
    };
  }
  const projected = [0, quaternion[1], 0, quaternion[3]];
  const twist = quaternionValues(projected);
  if (!twist) {
    return {
      success: false,
      quaternion,
      swing: null,
      twist: null,
      swingDirection: null,
      swingAxis: null,
      swingAngle: null,
      swingX: null,
      swingZ: null,
      twistAngle: null,
      diagnostic: 'swing_twist_singular',
    };
  }
  const swing = quaternionValues(
    quaternionMultiply(quaternion, quaternionConjugate(twist)));
  if (!swing) {
    return {
      success: false,
      quaternion,
      swing: null,
      twist,
      swingDirection: null,
      swingAxis: null,
      swingAngle: null,
      swingX: null,
      swingZ: null,
      twistAngle: null,
      diagnostic: 'swing_twist_singular',
    };
  }
  const direction = normalizeVector(rotateVector(swing, SWING_AXIS))
    || [...SWING_AXIS];
  const swingAxisLength = Math.hypot(swing[0], swing[2]);
  const swingAngle = 2 * Math.atan2(swingAxisLength,
    Math.max(-1, Math.min(1, swing[3]))) * RAD_TO_DEG;
  const swingAxis = swingAxisLength > EPSILON
    ? [swing[0] / swingAxisLength, 0, swing[2] / swingAxisLength]
    : [1, 0, 0];
  return {
    success: true,
    quaternion,
    swing,
    twist,
    swingDirection: direction,
    swingAxis,
    swingAngle,
    swingX: swingAngle * swingAxis[0],
    swingZ: swingAngle * swingAxis[2],
    twistAngle: canonicalDegrees(2 * Math.atan2(twist[1], twist[3])
      * RAD_TO_DEG),
  };
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function swingForComponents(swingX, swingZ) {
  const angle = Math.hypot(swingX, swingZ);
  if (!Number.isFinite(angle) || angle <= EPSILON) {
    return {quaternion: [...IDENTITY], swingX: 0, swingZ: 0};
  }
  const scale = Math.min(1, MAX_ANGLE / angle);
  const constrainedX = swingX * scale;
  const constrainedZ = swingZ * scale;
  const constrainedAngle = Math.hypot(constrainedX, constrainedZ);
  const axis = [constrainedX / constrainedAngle, 0,
    constrainedZ / constrainedAngle];
  return {
    quaternion: quaternionValues(axisAngle(axis, constrainedAngle))
      || [...IDENTITY],
    swingX: constrainedX,
    swingZ: constrainedZ,
  };
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
  const checked = rawConstraint === undefined || rawConstraint === null
    ? {valid: true, value: null}
    : checkConstraint(rawConstraint);
  if (!checked.valid) {
    return {success: true, quaternion, clamped: false,
      diagnostic: checked.error};
  }
  if (!checked.value || !checked.value.enabled) {
    const input = decomposeSwingTwist(quaternion);
    return {success: true, quaternion, clamped: false,
      diagnostic: checked.value ? 'disabled' : 'no_constraint',
      input, output: input};
  }
  const input = decomposeSwingTwist(quaternion);
  if (!input.success) {
    return {success: true, quaternion, clamped: false,
      diagnostic: input.diagnostic, input, output: input};
  }
  const constraint = checked.value;
  const target = {
    swingX: clamp(input.swingX, constraint.swingXMin, constraint.swingXMax),
    swingZ: clamp(input.swingZ, constraint.swingZMin, constraint.swingZMax),
    twist: clamp(input.twistAngle, constraint.twistMin, constraint.twistMax),
  };
  const swing = swingForComponents(target.swingX, target.swingZ);
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
  const isMap = constraints instanceof Map;
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
    if (isMap && (!keySignature || rawSignature !== undefined
      && rawSignature !== null && rawSignature !== keySignature)) continue;
    const signature = isMap ? keySignature
      : signatureValue(rawSignature);
    if (!signature || seen.has(signature)
        || lookup.ambiguousSignatures.has(signature)
        || !lookup.resolvedBySignature.has(signature)) continue;
    const value = isMap && (rawSignature === undefined || rawSignature === null)
      ? {...raw, jointSignature: signature} : raw;
    const record = persistedFromConstraint(value, signature);
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
