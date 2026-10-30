// Neutral skin joints carry authored source-scoped weights into Blender groups.
import * as THREE from 'three';

export function attachExportWeights(geometry, vertices, state) {
  const count = state.influenceCount;
  const vertexCount = state.indices.length / count;
  if (![1, 2, 4, 8].includes(count) || !Number.isInteger(vertexCount) || state.weights.length !== state.indices.length)
    throw new Error('Invalid export skin data.');
  const rows = [];
  const boneIds = new Set();
  let unweighted = false;
  for (const vertex of vertices.keys()) {
    if (vertex >= vertexCount) throw new Error('Export skin data does not match geometry.');
    const influences = new Map();
    for (let slot = 0; slot < count; slot += 1) {
      const offset = vertex * count + slot;
      const id = state.indices[offset],
        weight = state.weights[offset];
      if (!Number.isInteger(id) || id < 0 || !Number.isFinite(weight) || weight < 0) {
        throw new Error('Invalid export skin influence.');
      }
      if (weight > 0) influences.set(id, (influences.get(id) || 0) + weight);
    }
    const sum = [...influences.values()].reduce((total, weight) => total + weight, 0);
    if (!sum) unweighted = true;
    for (const id of influences.keys()) boneIds.add(id);
    rows.push({ influences, sum });
  }
  const ids = [...boneIds].sort((a, b) => a - b);
  // JOINTS_n are unsigned shorts; use a compact palette, never truncate u32 IDs.
  if (ids.length + 1 + Number(unweighted) > 65536) throw new Error('Export skin has too many joints.');
  const root = new THREE.Bone();
  root.name = 'WeightRoot';
  const bones = [root];
  const palette = new Map();
  for (const id of ids) {
    const bone = new THREE.Bone();
    bone.name = String(id);
    bone.userData.modViewer = {
      sourceKey: state.skinningSourceKey,
      boneId: id,
    };
    palette.set(id, bones.length);
    bones.push(bone);
    root.add(bone);
  }
  const unweightedIndex = bones.length;
  if (unweighted) {
    const bone = new THREE.Bone();
    bone.name = 'Unweighted';
    bones.push(bone);
    root.add(bone);
  }
  for (let set = 0; set < Math.ceil(count / 4); set += 1) {
    const joints = new Uint16Array(rows.length * 4);
    const weights = new Float32Array(rows.length * 4);
    rows.forEach(({ influences, sum }, vertex) => {
      [...influences].forEach(([id, weight], slot) => {
        if (Math.floor(slot / 4) !== set) return;
        joints[vertex * 4 + (slot % 4)] = palette.get(id);
        weights[vertex * 4 + (slot % 4)] = weight / sum;
      });
      if (!sum && set === 0) {
        joints[vertex * 4] = unweightedIndex;
        weights[vertex * 4] = 1;
      }
    });
    geometry.setAttribute(set ? `JOINTS_${set}` : 'skinIndex', new THREE.BufferAttribute(joints, 4));
    geometry.setAttribute(set ? `WEIGHTS_${set}` : 'skinWeight', new THREE.BufferAttribute(weights, 4));
  }
  geometry.userData.exportWeights = {
    root,
    bones,
    metadata: {
      sourceKey: state.skinningSourceKey,
      sourceFile: state.skinningSourceFile,
      boneIdOffset: state.skinningBoneOffset,
      influenceCount: count,
      normalized: true,
      joints: 'neutral placeholders',
      unweightedVertices: rows.filter((row) => !row.sum).length,
    },
  };
}
