// Pure helpers for the model-wide authored-weight selection experiment.

function selectedSet(value) {
  const values = value instanceof Set ? [...value] : value || [];
  return new Set(values.map(Number).filter(Number.isFinite));
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
