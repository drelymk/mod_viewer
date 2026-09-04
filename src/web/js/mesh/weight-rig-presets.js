// Pure representation and resolution helpers for inferred Rig pose presets.
// Runtime IDs are intentionally kept out of the persisted representation.

const IDENTITY_TOLERANCE = 1e-8;
const MAX_PRESET_NAME_LENGTH = 80;

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

function quaternionValues(value) {
  const values = value?.isQuaternion
    ? [value.x, value.y, value.z, value.w]
    : Array.isArray(value) || ArrayBuffer.isView(value)
      ? [...value] : null;
  if (!values || values.length !== 4
      || values.some(item => typeof item !== 'number'
        || !Number.isFinite(item))) return null;
  const length = Math.hypot(...values);
  if (!Number.isFinite(length) || length <= 1e-12) return null;
  return values.map(item => item / length);
}

function isIdentity(values) {
  return Math.abs(values[0]) < IDENTITY_TOLERANCE
    && Math.abs(values[1]) < IDENTITY_TOLERANCE
    && Math.abs(values[2]) < IDENTITY_TOLERANCE
    && Math.abs(values[3] - 1) < IDENTITY_TOLERANCE;
}

function entriesFrom(value) {
  return value instanceof Set ? [...value]
    : Array.isArray(value) ? [...value] : [];
}

function presetId(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function generatedPresetId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `pose-${Date.now().toString(36)}-${Math.random()
    .toString(36).slice(2, 10)}`;
}

/** Validate and normalize a user-facing preset name. */
export function validateRigPresetName(name) {
  const value = typeof name === 'string' ? name.trim() : '';
  if (!value) return {valid: false, value, error: 'A pose name is required.'};
  if (value.length > MAX_PRESET_NAME_LENGTH) {
    return {
      valid: false,
      value,
      error: `Pose names must be ${MAX_PRESET_NAME_LENGTH} characters or fewer.`,
    };
  }
  return {valid: true, value, error: null};
}

/** Build an exact signature lookup and report ambiguous current joints. */
export function buildJointSignatureIndex(modelRig) {
  const resolvedBySignature = new Map();
  const ambiguousSignatures = new Set();
  for (const joint of modelRig?.joints || []) {
    const signature = signatureValue(joint?.signature);
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

/** Serialize only normalized, non-identity local manual rotations and roots. */
export function serializeRigPose(modelRig, {explicitRootSignatures} = {}) {
  const signatures = buildJointSignatureIndex(modelRig).resolvedBySignature;
  const jointsById = new Map((modelRig?.joints || []).map(joint => [
    Number(joint?.jointId), joint,
  ]));
  const joints = [];
  for (const [jointId, rawRotation] of modelRig?.poseRotationByJointId || []) {
    const joint = jointsById.get(Number(jointId));
    const signature = signatureValue(joint?.signature);
    const rotation = quaternionValues(rawRotation);
    if (!joint || !signature || signatures.get(signature) !== Number(jointId)
        || !rotation || isIdentity(rotation)) continue;
    joints.push({joint_signature: signature, rotation});
  }
  joints.sort((left, right) => left.joint_signature.localeCompare(
    right.joint_signature));
  const roots = entriesFrom(explicitRootSignatures)
    .map(signatureValue)
    .filter(signature => signature && signatures.has(signature))
    .map(joint_signature => ({joint_signature}));
  const seenRoots = new Set();
  const uniqueRoots = roots.filter(root => {
    if (seenRoots.has(root.joint_signature)) return false;
    seenRoots.add(root.joint_signature);
    return true;
  }).sort((left, right) => left.joint_signature.localeCompare(
    right.joint_signature));
  return {roots: uniqueRoots, joints};
}

function normalizedEntry(rawEntry, type) {
  if (!isObject(rawEntry)) {
    return type === 'joint' ? {joint_signature: null, rotation: null}
      : {joint_signature: null};
  }
  if (type === 'joint') {
    return {
      joint_signature: typeof rawEntry.joint_signature === 'string'
        ? rawEntry.joint_signature : null,
      rotation: Array.isArray(rawEntry.rotation)
        || ArrayBuffer.isView(rawEntry.rotation)
        ? [...rawEntry.rotation] : null,
    };
  }
  return {
    joint_signature: typeof rawEntry.joint_signature === 'string'
      ? rawEntry.joint_signature : null,
  };
}

/** Normalize a stored record without adding fields to the M3 schema. */
export function normalizeRigPreset(rawPreset) {
  if (!isObject(rawPreset)) return null;
  const id = presetId(rawPreset.id);
  const name = validateRigPresetName(rawPreset.name);
  if (!id || !name.valid || !Array.isArray(rawPreset.roots)
      || !Array.isArray(rawPreset.joints)) return null;
  return {
    id,
    name: name.value,
    roots: rawPreset.roots.map(entry => normalizedEntry(entry, 'root')),
    joints: rawPreset.joints.map(entry => normalizedEntry(entry, 'joint')),
  };
}

function status(type, entry, reason, jointId = null) {
  return {
    type,
    jointSignature: entry?.joint_signature ?? null,
    reason,
    status: reason ? reason : 'resolved',
    ...(jointId === null ? {} : {jointId}),
  };
}

function resolveEntries(entries, type, lookup) {
  const seen = new Set();
  const resolved = [];
  const statuses = [];
  for (const entry of entries || []) {
    const signature = signatureValue(entry?.joint_signature);
    if (!signature) {
      statuses.push(status(type, entry, 'invalid_signature'));
      continue;
    }
    if (seen.has(signature)) {
      statuses.push(status(type, entry, type === 'joint'
        ? 'duplicate_joint_entry' : 'duplicate_root_entry'));
      continue;
    }
    seen.add(signature);
    if (type === 'joint' && !quaternionValues(entry?.rotation)) {
      statuses.push(status(type, entry, 'invalid_rotation'));
      continue;
    }
    if (lookup.ambiguousSignatures.has(signature)) {
      statuses.push(status(type, entry, 'ambiguous_joint_signature'));
      continue;
    }
    const jointId = lookup.resolvedBySignature.get(signature);
    if (!Number.isInteger(jointId)) {
      statuses.push(status(type, entry, type === 'root'
        ? 'root_not_found' : 'joint_not_found'));
      continue;
    }
    const resolvedEntry = type === 'joint'
      ? {jointId, jointSignature: signature,
        rotation: quaternionValues(entry.rotation)}
      : {jointId, jointSignature: signature};
    resolved.push(resolvedEntry);
    statuses.push(status(type, entry, null, jointId));
  }
  return {resolved, statuses};
}

/** Resolve exact signatures against the current model rig. */
export function resolveRigPreset(modelRig, preset) {
  const normalized = normalizeRigPreset(preset);
  if (!normalized) {
    return {
      success: false,
      preset: null,
      resolvedBySignature: new Map(),
      ambiguousSignatures: new Set(),
      roots: [], joints: [], skipped: [{type: 'preset', reason: 'invalid_preset'}],
      appliedRootCount: 0, skippedRootCount: 0,
      appliedJointCount: 0, skippedJointCount: 0,
    };
  }
  const lookup = buildJointSignatureIndex(modelRig);
  const roots = resolveEntries(normalized.roots, 'root', lookup);
  const joints = resolveEntries(normalized.joints, 'joint', lookup);
  const skipped = [...roots.statuses, ...joints.statuses]
    .filter(item => item.reason);
  return {
    success: true,
    preset: normalized,
    resolvedBySignature: lookup.resolvedBySignature,
    ambiguousSignatures: lookup.ambiguousSignatures,
    roots: roots.resolved,
    joints: joints.resolved,
    rootStatuses: roots.statuses,
    jointStatuses: joints.statuses,
    skipped,
    appliedRootCount: roots.resolved.length,
    skippedRootCount: roots.statuses.length - roots.resolved.length,
    appliedJointCount: joints.resolved.length,
    skippedJointCount: joints.statuses.length - joints.resolved.length,
  };
}

/** Create a complete schema-conforming preset from the current model state. */
export function createRigPreset({
  id = null, name, modelRig, explicitRootSignatures,
} = {}) {
  const checkedName = validateRigPresetName(name);
  if (!checkedName.valid) throw new Error(checkedName.error);
  const normalizedId = presetId(id) || generatedPresetId();
  const pose = serializeRigPose(modelRig, {explicitRootSignatures});
  return normalizeRigPreset({
    id: normalizedId,
    name: checkedName.value,
    roots: pose.roots,
    joints: pose.joints,
  });
}

export const RIG_PRESET_MAX_NAME_LENGTH = MAX_PRESET_NAME_LENGTH;
