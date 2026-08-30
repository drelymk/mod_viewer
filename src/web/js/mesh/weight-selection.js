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
