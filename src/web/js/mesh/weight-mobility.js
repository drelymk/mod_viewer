// Weight-derived mobility envelopes for the opt-in physics experiment.

function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}
function depthFor(component, boneId) {
  const depths = component?.depthById;
  if (depths instanceof Map) return Number(
    depths.get(boneId) ?? depths.get(String(boneId)));
  return Number(depths?.[boneId]);
}

function mobilityForBone(boneMobility, boneId) {
  const value = boneMobility instanceof Map
    ? boneMobility.get(boneId) ?? boneMobility.get(String(boneId))
    : boneMobility?.[boneId];
  return Number.isFinite(Number(value)) ? clamp01(Number(value)) : 0;
}

/** Derive normalized mobility from inferred hierarchy depth per component. */
export function buildBoneMobility(forest) {
  const result = new Map();
  (forest?.components || []).forEach(component => {
    const maxDepth = Number(component.maxDepth);
    const denominator = Number.isFinite(maxDepth) && maxDepth > 0
      ? maxDepth : 0;
    (component.nodeIds || []).forEach(value => {
      const boneId = Number(value);
      if (!Number.isFinite(boneId)) return;
      const depth = depthFor(component, boneId);
      const mobility = denominator > 0 && Number.isFinite(depth)
        ? depth / denominator : 0;
      result.set(boneId, clamp01(mobility));
    });
  });
  return result;
}

/** Blend hierarchy mobility using every valid positive authored influence. */
export function buildVertexMobility(
    indices, weights, influenceCount, boneMobility) {
  const count = Number.isInteger(influenceCount) && influenceCount > 0
    && indices && weights
    ? Math.floor(Math.min(indices.length, weights.length) / influenceCount) : 0;
  const result = new Float32Array(count);
  for (let vertex = 0; vertex < count; vertex += 1) {
    const start = vertex * influenceCount;
    let weightTotal = 0;
    let mobilityTotal = 0;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const weight = Number(weights[start + influence]);
      if (!Number.isFinite(weight) || weight <= 0) continue;
      weightTotal += weight;
      mobilityTotal += weight * mobilityForBone(
        boneMobility, Number(indices[start + influence]));
    }
    result[vertex] = weightTotal > 0
      ? clamp01(mobilityTotal / weightTotal) : 0;
  }
  return result;
}
