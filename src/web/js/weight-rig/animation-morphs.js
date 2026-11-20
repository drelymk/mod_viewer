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
  const offset = clip.frame_count * clip.bone_count * (clip.layout === 'frame-major-tqs-f32le' ? 10 : 7);
  const count = morphs.names.length;
  for (let index = 0; index < count; index++) {
    const left = values[offset + first * count + index];
    morphs.weights[index] = left + (values[offset + next * count + index] - left) * (position - first);
  }
  return morphs;
}

const TARGET_ROW_BYTES = 28;

function sameBytes(left, right) {
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index++) if (left[index] !== right[index]) return false;
  return true;
}

// One facial source for the clips of a sequence. Rebinding a mesh's morphs
// rebuilds its sparse records and GPU deformation, which stalls playback, so the
// clips' sparse targets merge once by component and name and each clip's
// weights map onto the union. Clips disagreeing on a shared target cannot merge.
export function mergeClipMorphs(clips) {
  const sources = clips.map((clip) => clip.morphs).filter(Boolean);
  if (!sources.length) return null;
  const names = [];
  const nameIndex = new Map();
  const components = new Map();
  const maps = new Map();
  const chunks = [];
  let length = 0;
  for (const morphs of sources) {
    if (maps.has(morphs)) continue;
    const map = new Int32Array(morphs.names.length);
    morphs.names.forEach((name, index) => {
      if (!nameIndex.has(name)) {
        nameIndex.set(name, names.length);
        names.push(name);
      }
      map[index] = nameIndex.get(name);
    });
    maps.set(morphs, map);
    const bytes = new Uint8Array(morphs.buffer);
    for (const component of morphs.components) {
      const key = `${component.vertex_sha256}:${component.index_sha256}`;
      if (!components.has(key)) components.set(key, { component: { ...component, targets: [] }, rows: new Map() });
      const merged = components.get(key);
      for (const target of component.targets) {
        const chunk = bytes.subarray(target.offset, target.offset + target.count * TARGET_ROW_BYTES);
        if (chunk.length !== target.count * TARGET_ROW_BYTES) return null;
        const existing = merged.rows.get(target.name);
        if (existing) {
          if (!sameBytes(existing, chunk)) return null;
          continue;
        }
        merged.rows.set(target.name, chunk);
        merged.component.targets.push({ name: target.name, offset: length, count: target.count });
        chunks.push(chunk);
        length += chunk.length;
      }
    }
  }
  const buffer = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.length;
  }
  return {
    names,
    components: [...components.values()].map((entry) => entry.component),
    buffer: buffer.buffer,
    weights: new Float32Array(names.length),
    maps,
  };
}

// Weighted sum of several clips' facial weights in a merged source. Channels a
// clip does not animate contribute zero, i.e. its captured expression.
export function sampleMergedMorphWeights(merged, layers) {
  merged.weights.fill(0);
  for (const [clip, values, time, factor] of layers) {
    const morphs = factor > 0 ? sampleMorphWeights(clip, values, time) : null;
    const map = morphs && merged.maps.get(morphs);
    if (!map) continue;
    for (let index = 0; index < map.length; index++) merged.weights[map[index]] += morphs.weights[index] * factor;
  }
  return merged;
}
