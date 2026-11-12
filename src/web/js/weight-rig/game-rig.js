// Authored skeletons retain the source-scoped weight IDs as transform aliases.
import { Matrix4, Quaternion, Vector3 } from 'three';
import { sourceBoneKey } from './weight-rig-reconcile-evidence.js';
import { orientModelForest } from './weight-rig-reconcile-forest.js';

export function buildGameRigReconciliation(sourceRigs = []) {
  const reference = sourceRigs[0]?.gameRig;
  if (
    !reference?.id ||
    !Array.isArray(reference.bones) ||
    !reference.bones.length ||
    reference.bones.length > 4096 ||
    sourceRigs.some((source) => source.gameRig?.id !== reference.id)
  )
    return null;
  const worlds = [];
  const frames = new Map();
  const pivots = new Map();
  const roots = new Map();
  const joints = [];
  const edges = [];
  const names = new Set();
  for (const [jointId, bone] of reference.bones.entries()) {
    if (
      !bone ||
      typeof bone.name !== 'string' ||
      !bone.name ||
      names.has(bone.name) ||
      !Number.isInteger(bone.parent) ||
      bone.parent < -1 ||
      bone.parent >= jointId ||
      !Array.isArray(bone.translation) ||
      bone.translation.length !== 3 ||
      !bone.translation.every(Number.isFinite) ||
      !Array.isArray(bone.rotation) ||
      bone.rotation.length !== 4 ||
      !bone.rotation.every(Number.isFinite)
    )
      return null;
    names.add(bone.name);
    const rotation = new Quaternion(...bone.rotation);
    if (Math.abs(rotation.length() - 1) > 0.001) return null;
    const scale = bone.scale || [1, 1, 1];
    if (
      !Array.isArray(scale) ||
      scale.length !== 3 ||
      // Same tolerance as the backend validate_rig: ~1e-3 float noise, and uniform negative (mirrored) scales.
      !scale.every(
        (v) =>
          Number.isFinite(v) &&
          Math.abs(v) >= 1e-6 &&
          Math.abs(v) <= 1e6 &&
          Math.abs(v - scale[0]) <= 2e-3 * Math.abs(scale[0]),
      )
    )
      return null;
    const world = new Matrix4().compose(new Vector3(...bone.translation), rotation.normalize(), new Vector3(...scale));
    if (bone.parent >= 0) world.premultiply(worlds[bone.parent]);
    else roots.set(jointId, 1);
    worlds.push(world);
    const position = new Vector3().setFromMatrixPosition(world).toArray();
    const frame = new Quaternion();
    const worldScale = new Vector3();
    world.decompose(new Vector3(), frame, worldScale);
    // A mirrored (uniform negative) world scale makes decompose turn the frame 180° about its X axis.
    if (worldScale.x < 0) frame.multiply(new Quaternion(1, 0, 0, 0));
    frame.normalize();
    pivots.set(jointId, position);
    frames.set(jointId, frame);
    joints.push({
      jointId,
      jointKey: `game:${reference.id}:${jointId}`,
      name: bone.name,
      members: [],
      representativeMember: null,
      restCenter: position,
      restPivot: position,
      restFrame: frame.toArray(),
      evidence: { totalWeight: 0, affectedVertexCount: 0, memberCount: 0 },
    });
    if (bone.parent >= 0)
      edges.push({
        jointA: bone.parent,
        jointB: jointId,
        parentId: bone.parent,
        childId: jointId,
        relationshipType: 'authored',
        score: 1,
        jointCenter: position,
        sourceEdges: [],
      });
  }
  const aliases = new Map();
  for (const source of sourceRigs) {
    for (const boneId of source.boneIds || []) {
      const target = source.gameRig.boneMap?.[boneId];
      if (!Number.isInteger(target) || target < 0 || target >= joints.length) return null;
      const key = sourceBoneKey(source.sourceKey, boneId);
      if (aliases.has(key) && aliases.get(key) !== target) return null;
      aliases.set(key, target);
      const member = { sourceKey: source.sourceKey, boneId, sourceBoneKey: key };
      if (source.legacySourceKey) {
        member.legacySourceBoneKey = sourceBoneKey(
          source.legacySourceKey,
          boneId + Number(source.legacyBoneIdOffset || 0),
        );
      }
      joints[target].members.push(member);
      joints[target].representativeMember ||= member;
    }
  }
  joints.forEach((joint) => {
    joint.members.sort((left, right) => left.sourceBoneKey.localeCompare(right.sourceBoneKey));
    joint.signature = JSON.stringify(
      joint.members.length
        ? joint.members.map((member) => member.sourceBoneKey)
        : [`game:${reference.id}:${joint.jointId}`],
    );
    joint.evidence.memberCount = joint.members.length;
    if (joint.members.some((member) => member.legacySourceBoneKey)) {
      joint.legacySignature = JSON.stringify(
        joint.members
          .map((member) => member.legacySourceBoneKey || member.sourceBoneKey)
          .sort((left, right) => left.localeCompare(right)),
      );
    }
  });
  const forest = orientModelForest(joints, edges, roots);
  const positions = [...pivots.values()];
  const minimum = positions.reduce((a, b) => a.map((v, i) => Math.min(v, b[i])), [...positions[0]]);
  const maximum = positions.reduce((a, b) => a.map((v, i) => Math.max(v, b[i])), [...positions[0]]);
  const radius = Math.max(1e-8, new Vector3(...minimum).distanceTo(new Vector3(...maximum)) / 2);
  return {
    joints,
    edges,
    ...forest,
    sourceBoneToModelJointMap: aliases,
    modelReferenceRadius: radius,
    gameRig: reference,
    authoredRestFrames: frames,
    authoredRestPivots: pivots,
    authoredHumanoidControls: reference.humanoid_controls || null,
    reconciliation: {
      identityMode: 'game-skeleton',
      sourceBoneCount: aliases.size,
      equivalenceClusterCount: joints.filter((joint) => joint.members.length > 1).length,
      skeletonId: reference.id,
      modelReferenceRadius: radius,
    },
  };
}
