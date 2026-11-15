// Morphs use the backend's captured-vertex mapping for each mesh.
export function bindAnimationMorphs(state, morphs) {
  const binding = state.gameMorphBinding;
  if (!binding || !morphs) return null;
  const component = morphs.components.find(
    (item) => item.vertex_sha256 === binding.vertex_sha256 && item.index_sha256 === binding.index_sha256,
  );
  if (!component || binding.vertices.length !== state.indices.length / state.influenceCount) return null;
  const source = new Map();
  binding.vertices.forEach((vertex, compact) => {
    if (vertex < 0) return;
    if (!source.has(vertex)) source.set(vertex, []);
    source.get(vertex).push(compact);
  });
  const vertices = Array.from({ length: binding.vertices.length }, () => []);
  const names = new Map(morphs.names.map((name, index) => [name, index]));
  const view = new DataView(morphs.buffer);
  for (const target of component.targets) {
    const id = names.get(target.name);
    if (id === undefined) continue;
    for (let row = 0; row < target.count; row++) {
      const offset = target.offset + row * 28;
      const vertex = view.getUint32(offset, true);
      const compactVertices = source.get(vertex);
      if (!compactVertices) continue;
      const delta = Array.from({ length: 6 }, (_, axis) => view.getFloat32(offset + 4 + axis * 4, true));
      for (const compact of compactVertices) vertices[compact].push(id, ...delta);
    }
  }
  const ranges = new Float32Array(vertices.length * 2);
  const entries = new Float32Array(vertices.reduce((sum, rows) => sum + rows.length, 0));
  if (!entries.length) return null;
  let offset = 0;
  vertices.forEach((rows, vertex) => {
    ranges[vertex * 2] = offset / 7;
    entries.set(rows, offset);
    offset += rows.length;
    ranges[vertex * 2 + 1] = offset / 7;
  });
  return { ranges, entries, weights: morphs.weights };
}

export function morphBaseline(state, morph = state.animationMorph) {
  if (!morph) return { positions: state.baselinePositions, normals: state.baselineNormals };
  const positions = state.baselinePositions.slice();
  const normals = state.baselineNormals?.slice() || null;
  for (let vertex = 0; vertex < positions.length / 3; vertex++) {
    for (let row = morph.ranges[vertex * 2]; row < morph.ranges[vertex * 2 + 1]; row++) {
      const offset = row * 7;
      const weight = morph.weights[morph.entries[offset]];
      for (let axis = 0; axis < 3; axis++) {
        positions[vertex * 3 + axis] += morph.entries[offset + 1 + axis] * weight;
        if (normals) normals[vertex * 3 + axis] += morph.entries[offset + 4 + axis] * weight;
      }
    }
    if (normals && morph.ranges[vertex * 2] !== morph.ranges[vertex * 2 + 1]) {
      const offset = vertex * 3;
      const length = Math.hypot(normals[offset], normals[offset + 1], normals[offset + 2]);
      if (length > 1e-20) for (let axis = 0; axis < 3; axis++) normals[offset + axis] /= length;
    }
  }
  return { positions, normals };
}

export function sampleMorphWeights(clip, values, time) {
  const morphs = clip.morphs;
  if (!morphs) return null;
  const position = Math.max(0, Math.min(1, time / clip.duration)) * (clip.frame_count - 1);
  const first = Math.floor(position),
    next = Math.min(first + 1, clip.frame_count - 1);
  const offset = clip.frame_count * clip.bone_count * 7;
  const count = morphs.names.length;
  for (let index = 0; index < count; index++) {
    const left = values[offset + first * count + index];
    morphs.weights[index] = left + (values[offset + next * count + index] - left) * (position - first);
  }
  return morphs;
}
