// Pure helpers for the authored-weight selection experiment.

function selectedSet(value) {
  const values = value instanceof Set ? [...value] : value || [];
  return new Set(values.map(value => Number(value)).filter(value =>
    Number.isInteger(value) && value >= 0));
}

/** Return the aggregate authored weight contributed by selected bone IDs. */
export function buildSelectedWeightMask(
    indices, weights, influenceCount, selectedBoneIds) {
  const count = Number.isInteger(influenceCount) && influenceCount > 0
    && indices && weights
    ? Math.floor(Math.min(indices.length, weights.length) / influenceCount) : 0;
  const selected = selectedSet(selectedBoneIds);
  const result = new Float32Array(count);
  for (let vertex = 0; vertex < count; vertex += 1) {
    const start = vertex * influenceCount;
    let total = 0;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const weight = Number(weights[start + influence]);
      if (weight > 0 && Number.isFinite(weight)
          && selected.has(Number(indices[start + influence]))) {
        total += weight;
      }
    }
    result[vertex] = Math.max(0, Math.min(1, total));
  }
  return result;
}

export function normalizeSelectedBoneIds(ids) {
  return [...selectedSet(ids)].sort((left, right) => left - right);
}

function sortedInfluences(accumulator, divisor = 1) {
  return [...accumulator.entries()]
    .map(([boneId, weight]) => ({boneId, weight: weight / divisor}))
    .filter(entry => Number.isFinite(entry.weight) && entry.weight > 0)
    .sort((left, right) => right.weight - left.weight
      || left.boneId - right.boneId);
}

/** Interpolate authored weights at one point inside an indexed triangle. */
export function interpolateTriangleBoneWeights(
    skinIndices, skinWeights, influenceCount, vertexIndices, barycentric) {
  if (!skinIndices || !skinWeights || !Number.isInteger(influenceCount)
      || influenceCount <= 0 || !vertexIndices
      || vertexIndices.length < 3 || !barycentric
      || barycentric.length < 3) return [];
  const accumulator = new Map();
  for (let corner = 0; corner < 3; corner += 1) {
    const vertex = Number(vertexIndices[corner]);
    const factor = Number(barycentric[corner]);
    if (!Number.isInteger(vertex) || vertex < 0
        || !Number.isFinite(factor) || factor < 0) continue;
    const start = vertex * influenceCount;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const boneId = Number(skinIndices[start + influence]);
      const weight = Number(skinWeights[start + influence]);
      if (!Number.isInteger(boneId) || boneId < 0
          || !Number.isFinite(weight) || weight <= 0) continue;
      accumulator.set(boneId, (accumulator.get(boneId) || 0)
        + factor * weight);
    }
  }
  return sortedInfluences(accumulator);
}

/** Return barycentric coordinates for a point relative to a triangle. */
export function barycentricCoordinates(point, first, second, third) {
  if (!point || !first || !second || !third
      || point.length < 3 || first.length < 3 || second.length < 3
      || third.length < 3) return null;
  const ax = Number(first[0]);
  const ay = Number(first[1]);
  const az = Number(first[2]);
  const v0x = Number(second[0]) - ax;
  const v0y = Number(second[1]) - ay;
  const v0z = Number(second[2]) - az;
  const v1x = Number(third[0]) - ax;
  const v1y = Number(third[1]) - ay;
  const v1z = Number(third[2]) - az;
  const v2x = Number(point[0]) - ax;
  const v2y = Number(point[1]) - ay;
  const v2z = Number(point[2]) - az;
  const d00 = v0x * v0x + v0y * v0y + v0z * v0z;
  const d01 = v0x * v1x + v0y * v1y + v0z * v1z;
  const d11 = v1x * v1x + v1y * v1y + v1z * v1z;
  const d20 = v2x * v0x + v2y * v0y + v2z * v0z;
  const d21 = v2x * v1x + v2y * v1y + v2z * v1z;
  const denominator = d00 * d11 - d01 * d01;
  if (!Number.isFinite(denominator) || Math.abs(denominator) < 1e-12) {
    return null;
  }
  const secondWeight = (d11 * d20 - d01 * d21) / denominator;
  const thirdWeight = (d00 * d21 - d01 * d20) / denominator;
  const firstWeight = 1 - secondWeight - thirdWeight;
  return [firstWeight, secondWeight, thirdWeight]
    .every(value => Number.isFinite(value))
    ? [firstWeight, secondWeight, thirdWeight] : null;
}

/** Sample distance-weighted authored influences around a point. */
export function sampleNearbyBoneWeights(
    positions, skinIndices, skinWeights, influenceCount, point, radius) {
  if (!positions || !skinIndices || !skinWeights
      || !Number.isInteger(influenceCount) || influenceCount <= 0
      || !point || point.length < 3
      || !Number.isFinite(Number(radius)) || Number(radius) <= 0) return [];
  const radiusValue = Number(radius);
  const radiusSquared = radiusValue * radiusValue;
  const vertexCount = Math.min(
    Math.floor(positions.length / 3),
    Math.floor(Math.min(skinIndices.length, skinWeights.length)
      / influenceCount));
  const accumulator = new Map();
  let sampleMass = 0;
  const px = Number(point[0]);
  const py = Number(point[1]);
  const pz = Number(point[2]);
  if (![px, py, pz].every(Number.isFinite)) return [];
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const offset = vertex * 3;
    const x = Number(positions[offset]);
    const y = Number(positions[offset + 1]);
    const z = Number(positions[offset + 2]);
    if (![x, y, z].every(Number.isFinite)) continue;
    const dx = x - px;
    const dy = y - py;
    const dz = z - pz;
    const distanceSquared = dx * dx + dy * dy + dz * dz;
    if (!Number.isFinite(distanceSquared) || distanceSquared > radiusSquared) {
      continue;
    }
    const distance = Math.sqrt(distanceSquared);
    const t = Math.max(0, Math.min(1, 1 - distance / radiusValue));
    const spatialWeight = t * t * (3 - 2 * t);
    if (spatialWeight <= 0) continue;
    const start = vertex * influenceCount;
    const vertexInfluences = [];
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const boneId = Number(skinIndices[start + influence]);
      const weight = Number(skinWeights[start + influence]);
      if (!Number.isInteger(boneId) || boneId < 0
          || !Number.isFinite(weight) || weight <= 0) continue;
      vertexInfluences.push([boneId, weight]);
    }
    if (!vertexInfluences.length) continue;
    sampleMass += spatialWeight;
    for (const [boneId, weight] of vertexInfluences) {
      accumulator.set(boneId, (accumulator.get(boneId) || 0)
        + spatialWeight * weight);
    }
  }
  return sampleMass > 0 ? sortedInfluences(accumulator, sampleMass) : [];
}

function normalizedSourceFile(value) {
  if (typeof value !== 'string') return '';
  const normalized = value.trim().replaceAll('\\', '/')
    .replace(/\/+/g, '/').replace(/(^|\/)\.\//g, '$1');
  return normalized && normalized !== '.' ? normalized : '';
}

function sourceDescriptor(value, fallbackKey = '') {
  const source = value?.source && typeof value.source === 'object'
    ? value.source : value;
  const fileValue = value?.sourceFile ?? value?.source_file
    ?? (typeof value?.source === 'string' ? value.source : source?.file);
  let file = normalizedSourceFile(fileValue);
  const rawOffset = value?.boneIdOffset ?? value?.bone_id_offset
    ?? source?.bone_id_offset;
  let offset = Number(rawOffset ?? 0);
  if (!file && fallbackKey) {
    const separator = fallbackKey.lastIndexOf('|offset=');
    if (separator > 0) {
      file = normalizedSourceFile(fallbackKey.slice(0, separator));
      offset = Number(fallbackKey.slice(separator + 8));
    }
  }
  if (!file || !Number.isInteger(offset) || offset < 0) return null;
  const key = String(value?.sourceKey ?? source?.key
    ?? fallbackKey ?? `${file.toLowerCase()}|offset=${offset}`);
  if (!key) return null;
  return {sourceKey: key, sourceFile: file, boneIdOffset: offset};
}

function selectionEntries(entries) {
  if (entries instanceof Map) {
    return [...entries.entries()].map(([sourceKey, value]) => ({
      ...(value && typeof value === 'object' && !(value instanceof Set)
        ? value : {boneIds: value}),
      sourceKey,
    }));
  }
  return Array.isArray(entries) ? entries : [];
}

/** Normalize and merge source-scoped selections for UI and persistence. */
export function normalizeBoneSelection(entries) {
  const merged = new Map();
  for (const raw of selectionEntries(entries)) {
    if (!raw || typeof raw !== 'object') continue;
    const descriptor = typeof raw.source === 'string'
      ? (() => {
        const sourceFile = normalizedSourceFile(raw.source);
        const offset = Number(raw.bone_id_offset ?? raw.boneIdOffset ?? 0);
        const sourceKey = String(raw.sourceKey
          ?? `${sourceFile.toLowerCase()}|offset=${offset}`);
        return sourceFile && Number.isInteger(offset) && offset >= 0
          && sourceKey ? {
            sourceKey, sourceFile, boneIdOffset: offset,
          } : null;
      })()
      : sourceDescriptor(raw, raw.sourceKey);
    if (!descriptor) continue;
    const ids = raw.boneIds ?? raw.bone_ids ?? [];
    const validIds = normalizeSelectedBoneIds(ids);
    if (!validIds.length) continue;
    const current = merged.get(descriptor.sourceKey) || {
      ...descriptor, boneIds: new Set(),
    };
    current.boneIds = new Set([
      ...current.boneIds, ...validIds,
    ]);
    merged.set(descriptor.sourceKey, current);
  }
  return [...merged.values()]
    .map(entry => ({...entry, boneIds: normalizeSelectedBoneIds(entry.boneIds)}))
    .sort((left, right) => left.sourceKey.localeCompare(right.sourceKey));
}

export function selectionForSource(selection, sourceKey) {
  const entry = normalizeBoneSelection(selection)
    .find(item => item.sourceKey === sourceKey);
  return new Set(entry?.boneIds || []);
}

export function serializeBoneSelection(selection) {
  return normalizeBoneSelection(selection).map(entry => ({
    source: entry.sourceFile,
    bone_id_offset: entry.boneIdOffset,
    bone_ids: [...entry.boneIds],
  }));
}

export function selectedBoneCount(selection) {
  return normalizeBoneSelection(selection).reduce(
    (total, entry) => total + entry.boneIds.length, 0);
}

export function sameBoneSelection(left, right) {
  return JSON.stringify(serializeBoneSelection(left))
    === JSON.stringify(serializeBoneSelection(right));
}
