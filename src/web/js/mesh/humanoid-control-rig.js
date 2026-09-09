// Geometry-only humanoid control rig fitting.
//
// This module deliberately knows nothing about ModelJoint, skinning, IK, or
// Physics. It consumes post-shape rest positions and returns a viewer-owned
// semantic scaffold that can be displayed without mutating the authored Rig.

const EPSILON = 1e-8;
const DEFAULT_MAX_POINT_COUNT = 160000;
const DEFAULT_SLAB_WIDTHS = Object.freeze([0.045, 0.065, 0.085]);
const DEFAULT_VOXEL_SIZE = 0.012;
const CONTROL_KEYS = Object.freeze([
  'chest', 'pelvis',
  'leftShoulder', 'leftElbow', 'leftHand',
  'rightShoulder', 'rightElbow', 'rightHand',
  'leftHip', 'leftKnee', 'leftFoot',
  'rightHip', 'rightKnee', 'rightFoot',
]);
const ROLE_CONTROL_KEYS = Object.freeze({
  left_arm: ['leftShoulder', 'leftElbow', 'leftHand'],
  right_arm: ['rightShoulder', 'rightElbow', 'rightHand'],
  left_leg: ['leftHip', 'leftKnee', 'leftFoot'],
  right_leg: ['rightHip', 'rightKnee', 'rightFoot'],
});
const ROLE_NAMES = Object.freeze(['torso', 'left_arm', 'right_arm', 'left_leg', 'right_leg']);

// These are soft search priors. They intentionally describe regions rather
// than authored control positions, so stylized proportions can move the fit.
const TEMPLATE_PRIORS = Object.freeze({
  vertical: Object.freeze({
    headTop: [0.97, 1.00], headBottom: [0.86, 0.92], neck: [0.80, 0.89],
    shoulder: [0.74, 0.84], chest: [0.65, 0.79], pelvis: [0.47, 0.59],
    hip: [0.45, 0.58], knee: [0.22, 0.35], foot: [0.00, 0.08],
  }),
  horizontal: Object.freeze({
    shoulderHalfWidth: [0.08, 0.18], hipHalfWidth: [0.04, 0.11],
    armLength: [0.25, 0.45], legLength: [0.42, 0.62],
  }),
});

function finiteNumber(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function vector3(value, fallback = [0, 0, 0]) {
  if (value?.isVector3) return [finiteNumber(value.x), finiteNumber(value.y), finiteNumber(value.z)];
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    return [0, 1, 2].map(index => finiteNumber(value[index], fallback[index]));
  }
  if (value && typeof value === 'object') {
    return [finiteNumber(value.x, fallback[0]), finiteNumber(value.y, fallback[1]),
      finiteNumber(value.z, fallback[2])];
  }
  return [...fallback];
}

function quaternion4(value) {
  if (value?.isQuaternion) {
    return [finiteNumber(value.x), finiteNumber(value.y),
      finiteNumber(value.z), finiteNumber(value.w, 1)];
  }
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    const result = [0, 1, 2, 3].map(index => Number(value[index]));
    if (result.every(Number.isFinite)) return result;
  }
  if (value && typeof value === 'object') {
    const result = [Number(value.x), Number(value.y), Number(value.z), Number(value.w)];
    if (result.every(Number.isFinite)) return result;
  }
  return [0, 0, 0, 1];
}

function length(value) {
  return Math.hypot(value[0], value[1], value[2]);
}

function normalize(value, fallback) {
  const source = vector3(value, fallback);
  const size = length(source);
  return size > EPSILON ? source.map(component => component / size) : [...fallback];
}

function dot(left, right) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
}

function cross(left, right) {
  return [left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0]];
}

function add(left, right) {
  return [left[0] + right[0], left[1] + right[1], left[2] + right[2]];
}

function scale(value, amount) {
  return value.map(component => component * amount);
}

function distance(left, right) {
  return Math.hypot(left[0] - right[0], left[1] - right[1], left[2] - right[2]);
}

function distance2(left, right) {
  return Math.hypot(left.x - right.x, left.y - right.y);
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function quantile(values, fraction) {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const index = clamp(fraction, 0, 1) * (sorted.length - 1);
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sorted[lower];
  const weight = index - lower;
  return sorted[lower] * (1 - weight) + sorted[upper] * weight;
}

function median(values) {
  return quantile(values, 0.5);
}

function coordinateFrame(axes) {
  const up = normalize(axes?.up, [0, 1, 0]);
  let right = normalize(axes?.right, [1, 0, 0]);
  let forward = normalize(axes?.forward, [0, 0, 1]);
  right = normalize(add(right, scale(up, -dot(right, up))), [1, 0, 0]);
  forward = add(forward, scale(up, -dot(forward, up)));
  forward = add(forward, scale(right, -dot(forward, right)));
  forward = normalize(forward, [0, 0, 1]);
  right = normalize(cross(up, forward), right);
  return {up, right, forward};
}

function transformedPoint(point, matrix) {
  if (!matrix?.elements || matrix.elements.length < 16) return point;
  const e = matrix.elements;
  const x = point[0];
  const y = point[1];
  const z = point[2];
  const w = e[3] * x + e[7] * y + e[11] * z + e[15];
  const divisor = Math.abs(w) > EPSILON ? w : 1;
  return [(e[0] * x + e[4] * y + e[8] * z + e[12]) / divisor,
    (e[1] * x + e[5] * y + e[9] * z + e[13]) / divisor,
    (e[2] * x + e[6] * y + e[10] * z + e[14]) / divisor];
}

function positionsForMesh(mesh) {
  if (mesh?.userData?.assetFill === true) return null;
  const rest = mesh?.userData?.humanoidRestPositions
    || mesh?.userData?.basePositions || mesh?.geometry?.attributes?.position?.array;
  if (!rest || rest.length < 3) return null;
  const matrix = mesh?.userData?.humanoidRestMatrix?.isMatrix4
    ? mesh.userData.humanoidRestMatrix : null;
  const points = [];
  for (let index = 0; index + 2 < rest.length; index += 3) {
    const point = [Number(rest[index]), Number(rest[index + 1]), Number(rest[index + 2])];
    if (point.every(Number.isFinite)) points.push(transformedPoint(point, matrix));
  }
  return points;
}

function semanticPoints(meshes, frame) {
  const result = [];
  (meshes || []).forEach(mesh => {
    const points = positionsForMesh(mesh);
    if (!points) return;
    points.forEach(point => result.push({
      point,
      side: dot(point, frame.right),
      height: dot(point, frame.up),
      depth: dot(point, frame.forward),
    }));
  });
  return result;
}

function downsamplePoints(points, maximum) {
  if (points.length <= maximum) return points;
  const result = [];
  const step = points.length / maximum;
  for (let index = 0; index < maximum; index += 1) result.push(points[Math.floor(index * step)]);
  return result;
}

function minMax(values) {
  let min = Infinity;
  let max = -Infinity;
  values.forEach(value => {
    if (value < min) min = value;
    if (value > max) max = value;
  });
  return {min, max};
}

function boundsFor(points) {
  const sides = points.map(item => item.side);
  const heights = points.map(item => item.height);
  const depths = points.map(item => item.depth);
  const sideBounds = minMax(sides);
  const heightBounds = minMax(heights);
  const depthBounds = minMax(depths);
  const lowHeight = heightBounds.min;
  const highHeight = heightBounds.max;
  return {
    lowHeight,
    highHeight,
    height: Math.max(highHeight - lowHeight, EPSILON),
    sideCenter: median(sides),
    depthCenter: median(depths),
    minSide: sideBounds.min,
    maxSide: sideBounds.max,
    minDepth: depthBounds.min,
    maxDepth: depthBounds.max,
  };
}

function normalizedPoint(item, bounds) {
  return {
    ...item,
    x: (item.side - bounds.sideCenter) / bounds.height,
    y: (item.height - bounds.lowHeight) / bounds.height,
    // All three semantic coordinates are normalized by the same H.
    z: (item.depth - bounds.depthCenter) / bounds.height,
  };
}

function voxelize(points, voxelSize) {
  const buckets = new Map();
  const size = Math.max(finiteNumber(voxelSize, DEFAULT_VOXEL_SIZE), 0.002);
  points.forEach(item => {
    const ix = Math.floor(item.x / size);
    const iy = Math.floor(item.y / size);
    const iz = Math.floor(item.z / size);
    const key = `${ix},${iy},${iz}`;
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = {ix, iy, iz, count: 0, x: 0, y: 0, z: 0};
      buckets.set(key, bucket);
    }
    bucket.count += 1;
    bucket.x += item.x;
    bucket.y += item.y;
    bucket.z += item.z;
  });
  return [...buckets.values()].map(bucket => ({...bucket,
    x: bucket.x / bucket.count, y: bucket.y / bucket.count, z: bucket.z / bucket.count,
  }));
}

function bodyDepthMode(points, depthBinSize) {
  const source = points.filter(item => Math.abs(item.x) < 0.25
    && item.y > 0.16 && item.y < 0.92);
  const values = source.length ? source : points;
  if (!values.length) return {depthN: 0, spreadN: 0};
  // The central body depth is the robust center of the accepted front/back
  // surfaces. A histogram mode can select one surface when the two surfaces
  // have equal support, which makes narrow slabs lose thin arms and legs.
  const depthN = median(values.map(item => item.z));
  const near = values.map(item => item.z);
  return {depthN,
    spreadN: quantile(near.map(value => Math.abs(value - depthN)), 0.9)};
}

function acceptedForSlab(points, bodyDepthN, slabWidth, options = {}) {
  // Include one voxel's center error at the slab boundary. The slab remains
  // normalized, while thin front/back surface pairs do not vanish from the
  // narrow diagnostic pass merely because their centers straddle a cell.
  const padding = Math.max(finiteNumber(options.voxelSize, DEFAULT_VOXEL_SIZE) * 0.55, 0.002);
  return points.filter(item => Math.abs(item.z - bodyDepthN) <= slabWidth + padding);
}

function sliceSamples(points, height, halfWidth = 0.022) {
  let samples = points.filter(item => Math.abs(item.y - height) <= halfWidth);
  if (samples.length < 3) {
    samples = points.filter(item => Math.abs(item.y - height) <= halfWidth * 2.25);
  }
  return samples;
}

function buildHeightCrossSections(points, options = {}) {
  const step = clamp(finiteNumber(options.profileStep, 0.025), 0.012, 0.08);
  const result = [];
  for (let height = 0; height <= 1 + EPSILON; height += step) {
    const samples = sliceSamples(points, height, step * 0.7);
    if (!samples.length) continue;
    const xs = samples.map(item => item.x);
    const zs = samples.map(item => item.z);
    const leftExtent = quantile(xs, 0.08);
    const rightExtent = quantile(xs, 0.92);
    result.push({height: clamp(height, 0, 1), leftExtent, rightExtent,
      width: Math.max(0, rightExtent - leftExtent), center: median(xs),
      depth: median(zs), sampleCount: samples.length});
  }
  return result;
}

function interpolateSamples(samples, value, field, fallback = 0) {
  if (!samples.length) return fallback;
  const ordered = [...samples].sort((left, right) => left.height - right.height);
  if (value <= ordered[0].height) return finiteNumber(ordered[0][field], fallback);
  if (value >= ordered[ordered.length - 1].height) {
    return finiteNumber(ordered[ordered.length - 1][field], fallback);
  }
  for (let index = 1; index < ordered.length; index += 1) {
    const first = ordered[index - 1];
    const second = ordered[index];
    if (value > second.height) continue;
    const factor = (value - first.height) / Math.max(second.height - first.height, EPSILON);
    return first[field] + (second[field] - first[field]) * factor;
  }
  return fallback;
}

function buildCenterline(points, options = {}) {
  const step = clamp(finiteNumber(options.centerlineStep, 0.04), 0.02, 0.1);
  const result = [];
  for (let height = 0.02; height <= 0.98; height += step) {
    const samples = sliceSamples(points.filter(item => Math.abs(item.x) < 0.27), height, step * 0.7);
    if (!samples.length) continue;
    result.push({height, side: median(samples.map(item => item.x)),
      depth: median(samples.map(item => item.z)), sampleCount: samples.length});
  }
  return result;
}

function centerAt(centerline, height) {
  return {
    x: interpolateSamples(centerline, height, 'side', 0),
    y: clamp(height, 0, 1),
    z: interpolateSamples(centerline, height, 'depth', 0),
  };
}

function softRangeCost(value, preferredRange, safetyRange) {
  const preferredLow = preferredRange[0];
  const preferredHigh = preferredRange[1];
  const safetyLow = safetyRange[0];
  const safetyHigh = safetyRange[1];
  if (value >= preferredLow && value <= preferredHigh) return 0;
  if (value < safetyLow || value > safetyHigh) {
    const distanceOutside = value < safetyLow ? safetyLow - value : value - safetyHigh;
    return 100 + distanceOutside / Math.max(safetyHigh - safetyLow, EPSILON);
  }
  const distance = value < preferredLow ? preferredLow - value : value - preferredHigh;
  const safetyDistance = value < preferredLow
    ? preferredLow - safetyLow : safetyHigh - preferredHigh;
  return (distance / Math.max(safetyDistance, EPSILON)) ** 2;
}

function closestProfile(profile, height) {
  if (!profile.length) return null;
  return profile.reduce((best, item) => !best || Math.abs(item.height - height)
    < Math.abs(best.height - height) ? item : best, null);
}

function profileWidthAt(profile, height) {
  return closestProfile(profile, clamp(height, 0, 1))?.width || 0;
}

function centralHalfWidth(points, height, options = {}) {
  const samples = sliceSamples(points.filter(item => Math.abs(item.x) < 0.36), height, 0.035);
  if (!samples.length) return 0.14;
  // A lower robust quantile keeps a broad skirt or sleeve edge from becoming
  // the torso width while retaining the actual central body surface.
  const extentQuantile = clamp(finiteNumber(options.shoulderExtentQuantile, 0.6),
    0.5, 0.85);
  return clamp(quantile(samples.map(item => Math.abs(item.x)), extentQuantile), 0.045,
    finiteNumber(options.maxShoulderHalfWidth, 0.34));
}

function estimateDepth(point, points, fallback = 0, radius = 0.075) {
  const candidates = points.filter(item => Math.hypot(item.x - point.x, item.y - point.y) <= radius);
  if (!candidates.length) return fallback;
  return median(candidates.map(item => item.z));
}

function pointAt(point, points, fallbackDepth = 0) {
  if (!point) return null;
  return {x: finiteNumber(point.x), y: clamp(finiteNumber(point.y), 0, 1),
    z: finiteNumber(point.z, estimateDepth(point, points, fallbackDepth))};
}

function fitHeadNeckShoulders(points, profile, centerline, options = {}) {
  const vertical = TEMPLATE_PRIORS.vertical;
  const candidates = profile.filter(item => item.height >= 0.72 && item.height <= 0.90
    && item.sampleCount >= 2);
  const neck = candidates.reduce((best, item) => {
    const cost = item.width + 0.08 * softRangeCost(item.height,
      vertical.neck, [0.72, 0.96]);
    return !best || cost < best.cost ? {item, cost} : best;
  }, null)?.item;
  const neckHeight = neck?.height ?? 0.84;
  // Keep the canonical shoulder band as a soft prior rather than a hard
  // ceiling. A mesh without an explicitly separated head can place the
  // shoulder attachment above .90, and the width expansion is the stronger
  // evidence in that case.
  const shoulderCandidates = profile.filter(item => item.height >= 0.70
    && item.height <= 0.98);
  const torsoWidths = profile.filter(item => item.height >= 0.30 && item.height <= 0.55)
    .map(item => item.width);
  // Use the lower torso band as a robust body reference. Connected skirts and
  // coats usually widen the profile above this band and must not move the
  // virtual shoulder past the arm attachment.
  const torsoWidth = torsoWidths.length ? quantile(torsoWidths, 0.35) : 0;
  const expandedShoulders = shoulderCandidates.filter(item => item.width
    >= torsoWidth + 0.025);
  const shoulderTarget = (vertical.shoulder[0] + vertical.shoulder[1]) * 0.5;
  let shoulder = (expandedShoulders.length ? expandedShoulders : shoulderCandidates)
    .reduce((best, item) => {
    const below = profileWidthAt(profile, item.height - 0.04);
    const above = profileWidthAt(profile, item.height + 0.04);
    // The attachment is the first strong width transition. Looking both
    // directions avoids selecting a later maximum (or a head-top gap) after
    // the arm has already widened the profile.
    const expansion = Math.max(0, item.width - below, item.width - above);
    const expansionEvidence = clamp((item.width - torsoWidth) / 0.25, 0, 1);
    const cost = 1.2 * Math.abs(item.height - shoulderTarget)
      + 0.18 * softRangeCost(item.height, vertical.shoulder, [0.65, 0.96])
      - 0.08 * expansionEvidence - 0.02 * expansion;
    return !best || cost < best.cost ? {item, cost} : best;
  }, null)?.item;
  if (!shoulder) shoulder = closestProfile(profile, 0.79);
  const shoulderHeight = clamp(shoulder?.height ?? 0.79, 0.55, 0.95);
  const torsoCenter = centerAt(centerline, shoulderHeight - 0.12);
  const shoulderCenter = {x: torsoCenter.x, y: shoulderHeight, z: torsoCenter.z};
  // Sample just below the attachment band so descending arm surfaces do not
  // inflate the torso half-width and move the virtual Shoulder past the arm.
  const shoulderHalfWidth = torsoWidth > 0 ? clamp(torsoWidth * 0.5, 0.045,
    finiteNumber(options.maxShoulderHalfWidth, 0.34))
    : centralHalfWidth(points, shoulderHeight - 0.12, options);
  const chestHeight = clamp(shoulderHeight - Math.max(0.045,
    (shoulderHeight - neckHeight) * 0.2), 0.45, 0.9);
  const chest = centerAt(centerline, chestHeight);
  const headBottomHeight = clamp(neckHeight + 0.025, 0, 1);
  const residual = profile.length ? clamp((profileWidthAt(profile, neckHeight)
    / Math.max(profileWidthAt(profile, shoulderHeight), 0.02)), 0, 1) : 1;
  return {neckHeight, headBottomHeight, shoulderHeight, shoulderHalfWidth, chestHeight,
    center: shoulderCenter, chest, residual};
}

function pathLength(path) {
  let total = 0;
  for (let index = 1; index < path.length; index += 1) {
    total += Math.hypot(path[index].x - path[index - 1].x,
      path[index].y - path[index - 1].y, path[index].z - path[index - 1].z);
  }
  return total;
}

function pointDistance3(left, right) {
  return Math.hypot(left.x - right.x, left.y - right.y, left.z - right.z);
}

function buildSpatialIndex(points, cellSize = 0.06) {
  const size = Math.max(cellSize, 0.01);
  const buckets = new Map();
  points.forEach(item => {
    const key = `${Math.floor(item.x / size)},${Math.floor(item.y / size)},${Math.floor(item.z / size)}`;
    const bucket = buckets.get(key) || [];
    bucket.push(item);
    buckets.set(key, bucket);
  });
  return {buckets, size};
}

function nearbyPoints(index, target, radius) {
  if (!index) return [];
  const size = index.size;
  const minX = Math.floor((target.x - radius) / size);
  const maxX = Math.floor((target.x + radius) / size);
  const minY = Math.floor((target.y - radius) / size);
  const maxY = Math.floor((target.y + radius) / size);
  const minZ = Math.floor((target.z - radius) / size);
  const maxZ = Math.floor((target.z + radius) / size);
  const nearby = [];
  for (let ix = minX; ix <= maxX; ix += 1) {
    for (let iy = minY; iy <= maxY; iy += 1) {
      for (let iz = minZ; iz <= maxZ; iz += 1) {
        const bucket = index.buckets.get(`${ix},${iy},${iz}`) || [];
        bucket.forEach(item => {
          const distance = pointDistance3(item, target);
          if (distance <= radius) nearby.push({item, distance});
        });
      }
    }
  }
  return nearby.sort((left, right) => left.distance - right.distance);
}

function geometryNear(points, spatialIndex, target, radius) {
  if (spatialIndex) return nearbyPoints(spatialIndex, target, radius);
  return points.map(item => ({item, distance: pointDistance3(item, target)}))
    .filter(entry => entry.distance <= radius)
    .sort((left, right) => left.distance - right.distance);
}

function localTubeSample(points, spatialIndex, target, side, radius) {
  const nearby = geometryNear(points, spatialIndex, target, radius);
  const sideNearby = nearby.filter(entry => side * entry.item.x > 0.025);
  const stable = (sideNearby.length ? sideNearby : nearby)
    .slice(0, Math.min(nearby.length, 24)).map(entry => entry.item);
  const center = stable.length ? {x: median(stable.map(item => item.x)),
    y: median(stable.map(item => item.y)), z: median(stable.map(item => item.z))} : null;
  const sectionNearby = geometryNear(points, spatialIndex, target,
    Math.max(radius * 1.8, 0.14)).filter(entry => Math.abs(entry.item.y - target.y) <= 0.035
      && Math.abs(entry.item.z - target.z) <= 0.12
      && Math.abs(entry.item.x - target.x) <= 0.24
      && side * entry.item.x > 0.025);
  const sideValues = (sectionNearby.length >= 3 ? sectionNearby : sideNearby)
    .map(entry => entry.item.x);
  return {
    nearbyCount: nearby.length, sideCount: sideNearby.length, center,
    width: sideValues.length >= 2 ? quantile(sideValues, 0.9)
      - quantile(sideValues, 0.1) : 0,
    sameSide: nearby.length ? sideNearby.length / nearby.length : 0,
    nearestDistance: nearby[0]?.distance ?? 1,
  };
}

function sampleProximalContinuation(points, proximal, distal, side, options = {}) {
  if (!proximal || !distal) {
    return {coverage: 0, straightness: 0, sidePersistence: 0, widthRatio: 0,
      widthBelow: 0, widthAbove: 0, independentScore: 0};
  }
  const radius = finiteNumber(options.proximalCorridorRadius, 0.085);
  const direction = {x: proximal.x - distal.x, y: proximal.y - distal.y,
    z: proximal.z - distal.z};
  const directionLength = Math.max(pointDistance3(proximal, distal), EPSILON);
  direction.x /= directionLength;
  direction.y /= directionLength;
  direction.z /= directionLength;
  const distances = Array.isArray(options.proximalProbeDistances)
    && options.proximalProbeDistances.length
    ? options.proximalProbeDistances : [0.04, 0.08];
  const probes = distances.map(distanceAhead => ({
    x: proximal.x + direction.x * distanceAhead,
    y: proximal.y + direction.y * distanceAhead,
    z: proximal.z + direction.z * distanceAhead,
  }));
  const samples = probes.map(target => localTubeSample(
    points, options.spatialIndex, target, side, radius));
  const below = localTubeSample(points, options.spatialIndex, {
    x: proximal.x - direction.x * 0.04,
    y: proximal.y - direction.y * 0.04,
    z: proximal.z - direction.z * 0.04,
  }, side, radius);
  const supported = samples.map((sample, index) => ({sample, index}))
    .filter(entry => entry.sample.nearbyCount >= 4);
  const coverage = supported.length / Math.max(samples.length, 1);
  const straightness = supported.length ? median(supported.map(({sample, index}) => {
    const residual = sample.center ? pointDistance3(sample.center, probes[index]) : 1;
    return 1 - clamp(residual / Math.max(radius, EPSILON), 0, 1);
  })) : 0;
  const sidePersistence = supported.length ? median(supported.map(({sample}) =>
    sample.sameSide)) : 0;
  const widthAbove = supported.length ? median(supported.map(({sample}) => sample.width)) : 0;
  const widthBelow = below.width;
  const widthRatio = widthAbove / Math.max(widthBelow, 0.035);
  // A narrow, same-side continuation is limb evidence. A sudden width
  // increase at the proximal endpoint is the expected torso/pelvis transition.
  const widthContinuity = clamp(1 - Math.max(widthRatio - 1.1, 0) / 0.8, 0, 1);
  const independentScore = coverage * straightness * sidePersistence * widthContinuity;
  return {coverage, straightness, sidePersistence, widthRatio, widthBelow, widthAbove,
    independentScore};
}

function interpolatePoint(first, second, factor) {
  return {x: first.x + (second.x - first.x) * factor,
    y: first.y + (second.y - first.y) * factor,
    z: first.z + (second.z - first.z) * factor};
}

function sampleGeometryCorridor(points, start, end, radius = 0.085,
  sampleCount = 11, spatialIndex = null) {
  const centers = [];
  const nearestDistances = [];
  const depthResiduals = [];
  const supports = [];
  const continuationSupports = [];
  let occupied = 0;
  let currentGap = 0;
  let longestGap = 0;
  for (let index = 0; index < sampleCount; index += 1) {
    const factor = index / Math.max(sampleCount - 1, 1);
    const target = interpolatePoint(start, end, factor);
    const nearby = spatialIndex ? nearbyPoints(spatialIndex, target, radius)
      : points.map(item => ({item, distance: pointDistance3(item, target)}))
        .filter(entry => entry.distance <= radius)
        .sort((left, right) => left.distance - right.distance);
    supports.push(nearby.length);
    if (!nearby.length) {
      centers.push(target);
      currentGap += 1;
      longestGap = Math.max(longestGap, currentGap);
      continue;
    }
    occupied += 1;
    currentGap = 0;
    const stable = nearby.slice(0, Math.min(nearby.length, 24)).map(entry => entry.item);
    centers.push({x: median(stable.map(item => item.x)),
      y: median(stable.map(item => item.y)), z: median(stable.map(item => item.z))});
    nearestDistances.push(nearby[0].distance);
    depthResiduals.push(Math.abs(nearby[0].item.z - target.z));
  }
  const direction = {x: end.x - start.x, y: end.y - start.y, z: end.z - start.z};
  const directionLength = Math.max(pointDistance3(start, end), EPSILON);
  for (const distanceAhead of [0.08, 0.16]) {
    const target = {x: end.x + direction.x * distanceAhead / directionLength,
      y: end.y + direction.y * distanceAhead / directionLength,
      z: end.z + direction.z * distanceAhead / directionLength};
    const nearby = spatialIndex ? nearbyPoints(spatialIndex, target, radius)
      : points.map(item => ({item, distance: pointDistance3(item, target)}))
        .filter(entry => entry.distance <= radius);
    continuationSupports.push(nearby.length >= 4 ? 1 : 0);
  }
  return {
    centers, occupancyCoverage: occupied / Math.max(sampleCount, 1),
    longestGap: longestGap / Math.max(sampleCount - 1, 1),
    lateralResidual: nearestDistances.length ? median(nearestDistances) : 1,
    depthResidual: depthResiduals.length ? median(depthResiduals) : 1,
    endpointSupport: clamp(Math.log1p(supports[supports.length - 1] || 0)
      / Math.log1p(10), 0, 1),
    continuationCoverage: median(continuationSupports),
    pathLengthN: pathLength(centers),
  };
}

function centerlineFromCorridor(metrics, start, end, reverse = false) {
  const path = (metrics?.centers || []).map(point => ({...point}));
  if (!path.length) return [];
  path[0] = {...start};
  path[path.length - 1] = {...end};
  return reverse ? path.reverse() : path;
}

function pointAtPathFraction(path, fraction) {
  if (!path.length) return null;
  if (path.length === 1) return {...path[0]};
  const target = pathLength(path) * clamp(fraction, 0, 1);
  let travelled = 0;
  for (let index = 1; index < path.length; index += 1) {
    const segment = pointDistance3(path[index], path[index - 1]);
    if (travelled + segment >= target && segment > EPSILON) {
      return interpolatePoint(path[index - 1], path[index],
        (target - travelled) / segment);
    }
    travelled += segment;
  }
  return {...path[path.length - 1]};
}

function fitFoot(points, side) {
  const lower = points.filter(item => item.y <= 0.1 && side * item.x > 0.025);
  if (!lower.length) return null;
  const stableTop = quantile(lower.map(item => item.y), 0.62);
  const stable = lower.filter(item => item.y <= stableTop);
  return {x: median(stable.map(item => item.x)),
    y: median(stable.map(item => item.y)), z: median(stable.map(item => item.z)),
    support: stable.length};
}

function buildHipCandidates(points, side, foot, options = {}) {
  if (!foot) return [];
  const preferred = TEMPLATE_PRIORS.vertical.hip;
  const safety = [0.40, 0.63];
  const step = clamp(finiteNumber(options.hipSearchStep, 0.025), 0.015, 0.06);
  const candidates = [];
  for (let height = safety[0]; height <= safety[1] + EPSILON; height += step) {
    const slice = points.filter(item => Math.abs(item.y - height) <= 0.035
      && side * item.x > 0.025 && side * item.x < 0.42);
    if (!slice.length) continue;
    const hip = {x: median(slice.map(item => item.x)),
      y: median(slice.map(item => item.y)), z: median(slice.map(item => item.z)),
      support: slice.length};
    const metrics = sampleGeometryCorridor(points, hip, foot,
      finiteNumber(options.legCorridorRadius, 0.09), 11, options.spatialIndex);
    metrics.proximalContinuation = sampleProximalContinuation(
      points, hip, foot, side, options);
    const templateCost = softRangeCost(metrics.pathLengthN,
      TEMPLATE_PRIORS.horizontal.legLength, [0.38, 0.67]);
    const heightCost = softRangeCost(hip.y, preferred, safety);
    const score = 3.0 * metrics.occupancyCoverage
      + 0.9 * metrics.endpointSupport
      - 2.2 * metrics.longestGap
      - 1.7 * metrics.lateralResidual
      - 0.9 * metrics.depthResidual
      - 0.9 * templateCost - 0.35 * heightCost
      - 1.6 * metrics.proximalContinuation.independentScore;
    candidates.push({hip, foot, metrics, templateCost, heightCost, score,
      valid: metrics.occupancyCoverage >= 0.55
        && metrics.endpointSupport >= 0.1 && templateCost < 100,
      path: centerlineFromCorridor(metrics, hip, foot, true)});
  }
  return candidates.sort((left, right) => right.score - left.score);
}

function candidateDiagnostic(candidate) {
  if (!candidate) return null;
  return {
    shoulder: candidate.shoulder ? {...candidate.shoulder} : null,
    hand: candidate.hand ? {...candidate.hand} : null,
    hip: candidate.hip ? {...candidate.hip} : null,
    foot: candidate.foot ? {...candidate.foot} : null,
    endpoint: {...(candidate.hand || candidate.hip || {})},
    heightN: candidate.hip?.y ?? candidate.shoulder?.y ?? 0,
    armLengthN: candidate.hand ? candidate.metrics?.pathLengthN || 0 : 0,
    legLengthN: candidate.hip ? candidate.metrics?.pathLengthN || 0 : 0,
    lengthN: candidate.metrics?.pathLengthN || 0,
    corridorCoverage: candidate.metrics?.occupancyCoverage || 0,
    longestGap: candidate.metrics?.longestGap || 0,
    lateralResidual: candidate.metrics?.lateralResidual || 0,
    medianCenterResidual: candidate.metrics?.lateralResidual || 0,
    depthResidual: candidate.metrics?.depthResidual || 0,
    endpointSupport: candidate.metrics?.endpointSupport || 0,
    continuationCoverage: candidate.metrics?.continuationCoverage || 0,
    proximalContinuationCoverage: candidate.metrics?.proximalContinuation?.coverage || 0,
    proximalContinuationStraightness: candidate.metrics?.proximalContinuation?.straightness || 0,
    proximalContinuationSidePersistence: candidate.metrics?.proximalContinuation?.sidePersistence || 0,
    proximalContinuationWidthRatio: candidate.metrics?.proximalContinuation?.widthRatio || 0,
    proximalContinuationScore: candidate.metrics?.proximalContinuation?.independentScore || 0,
    widthBelow: candidate.metrics?.proximalContinuation?.widthBelow || 0,
    widthAbove: candidate.metrics?.proximalContinuation?.widthAbove || 0,
    proximalArmContinuation: candidate.proximalArmContinuation?.independentScore || 0,
    torsoAttachmentScore: candidate.torsoAttachmentScore || 0,
    refinementDistance: candidate.refinementDistance || 0,
    initialShoulder: candidate.initialShoulder ? {...candidate.initialShoulder} : null,
    distalScore: candidate.distalScore || 0,
    outwardScore: candidate.outwardScore || 0,
    templateCost: candidate.templateCost || 0,
    heightCost: candidate.heightCost || 0,
    angle: candidate.angle || 0,
    totalScore: candidate.score || 0,
  };
}

function selectLegPair(leftCandidates, rightCandidates) {
  const left = leftCandidates.filter(candidate => candidate.valid);
  const right = rightCandidates.filter(candidate => candidate.valid);
  const leftPool = left.length ? left : leftCandidates;
  const rightPool = right.length ? right : rightCandidates;
  let best = null;
  leftPool.forEach(leftCandidate => rightPool.forEach(rightCandidate => {
    const width = Math.abs(leftCandidate.hip.x - rightCandidate.hip.x) * 0.5;
    const widthCost = softRangeCost(width,
      TEMPLATE_PRIORS.horizontal.hipHalfWidth, [0.025, 0.18]);
    const baseScore = leftCandidate.score + rightCandidate.score
      - 1.1 * Math.abs(leftCandidate.hip.y - rightCandidate.hip.y)
      - 0.8 * Math.abs(leftCandidate.metrics.pathLengthN
        - rightCandidate.metrics.pathLengthN)
      - 0.75 * widthCost
      - 0.25 * Math.abs(leftCandidate.hip.z - rightCandidate.hip.z);
    let score = baseScore;
    const hipHeight = (leftCandidate.hip.y + rightCandidate.hip.y) * 0.5;
    // Height is only a tie-breaker after the continuation evidence and
    // corridor score have effectively tied. This avoids replacing a clean
    // lower-leg fit with a globally forced high endpoint.
    if (best && Math.abs(baseScore - best.baseScore) <= 0.08
        && hipHeight > best.hipHeight) score += 0.04 * hipHeight;
    if (!best || score > best.score) {
      best = {left: leftCandidate, right: rightCandidate, score, baseScore, hipHeight};
    }
  }));
  return best;
}

function fitPelvisAndHips(points, centerline, options = {}) {
  const leftFoot = fitFoot(points, -1);
  const rightFoot = fitFoot(points, 1);
  const leftCandidates = buildHipCandidates(points, -1, leftFoot, options);
  const rightCandidates = buildHipCandidates(points, 1, rightFoot, options);
  const pair = selectLegPair(leftCandidates, rightCandidates);
  const left = pair?.left || null;
  const right = pair?.right || null;
  const validLeft = !!left?.valid;
  const validRight = !!right?.valid;
  const hipHeight = left && right ? (left.hip.y + right.hip.y) * 0.5
    : left?.hip.y ?? right?.hip.y ?? 0.52;
  const fallbackCenter = centerAt(centerline, hipHeight);
  const leftHip = left?.hip || {x: -0.14, y: hipHeight, z: fallbackCenter.z};
  const rightHip = right?.hip || {x: 0.14, y: hipHeight, z: fallbackCenter.z};
  const pelvis = {x: (leftHip.x + rightHip.x) * 0.5, y: hipHeight,
    z: median([leftHip.z, rightHip.z, fallbackCenter.z])};
  const leftPath = left?.path || [];
  const rightPath = right?.path || [];
  return {
    leftTrack: {samples: leftPath}, rightTrack: {samples: rightPath},
    leftHip: validLeft ? pointAt(leftHip, points) : null,
    rightHip: validRight ? pointAt(rightHip, points) : null,
    pelvis: pointAt(pelvis, points),
    leftKnee: validLeft ? pointAt(pointAtPathFraction(leftPath, 0.5), points) : null,
    rightKnee: validRight ? pointAt(pointAtPathFraction(rightPath, 0.5), points) : null,
    leftFoot: validLeft ? pointAt(left?.foot, points) : null,
    rightFoot: validRight ? pointAt(right?.foot, points) : null,
    pelvisHeight: hipHeight,
    hipHalfWidth: Math.abs(leftHip.x - rightHip.x) * 0.5,
    leftLegLength: left?.metrics.pathLengthN || 0,
    rightLegLength: right?.metrics.pathLengthN || 0,
    validLeft, validRight,
    diagnostics: {
      left: candidateDiagnostic(left), right: candidateDiagnostic(right),
      leftRunnerUp: candidateDiagnostic(leftCandidates.find(item => item !== left)),
      rightRunnerUp: candidateDiagnostic(rightCandidates.find(item => item !== right)),
      preferredHeight: [...TEMPLATE_PRIORS.vertical.hip], safetyHeight: [0.40, 0.63],
      pairScore: pair?.score || 0,
    },
  };
}

function torsoAttachmentScore(points, side, shoulder, options = {}) {
  const radius = finiteNumber(options.shoulderAttachmentRadius, 0.12);
  const nearby = geometryNear(points, options.spatialIndex, shoulder, radius)
    .filter(entry => Math.abs(entry.item.y - shoulder.y) <= 0.08
      && Math.abs(entry.item.z - shoulder.z) <= 0.12);
  const inward = nearby.filter(entry => side * (entry.item.x - shoulder.x) < -0.02);
  const section = geometryNear(points, options.spatialIndex, shoulder, 0.24)
    .filter(entry => Math.abs(entry.item.y - shoulder.y) <= 0.025
      && Math.abs(entry.item.z - shoulder.z) <= 0.08
      && side * entry.item.x > 0.025
      && side * (entry.item.x - shoulder.x) <= 0.24);
  const sectionWidth = section.length >= 2
    ? quantile(section.map(entry => entry.item.x), 0.9)
      - quantile(section.map(entry => entry.item.x), 0.1) : 0.24;
  const inwardScore = clamp(Math.log1p(inward.length) / Math.log1p(12), 0, 1);
  const narrowSectionScore = clamp((0.24 - sectionWidth) / 0.16, 0, 1);
  return clamp(0.35 * inwardScore + 0.65 * narrowSectionScore, 0, 1);
}

function buildArmCandidate(points, side, shoulder, hand, initialShoulder, options = {}) {
  const metrics = sampleGeometryCorridor(points, shoulder, hand,
    finiteNumber(options.armCorridorRadius, 0.085), 11, options.spatialIndex);
  const preferred = TEMPLATE_PRIORS.horizontal.armLength;
  const safety = [0.20, 0.52];
  const templateCost = softRangeCost(metrics.pathLengthN, preferred, safety);
  const distalScore = clamp((metrics.pathLengthN - safety[0])
    / Math.max(preferred[1] - safety[0], EPSILON), 0, 1);
  const outward = side * (hand.x - shoulder.x);
  const outwardScore = clamp((outward - safety[0])
    / Math.max(preferred[1] - safety[0], EPSILON), 0, 1);
  const angle = Math.atan2(shoulder.y - hand.y, Math.max(outward, EPSILON));
  const angleCost = softRangeCost(angle, [0, 1.1], [-0.35, 1.35]);
  const proximalArmContinuation = sampleProximalContinuation(
    points, shoulder, hand, side, options);
  const attachment = torsoAttachmentScore(points, side, shoulder, options);
  const initialDistance = initialShoulder ? pointDistance3(shoulder, initialShoulder) : 0;
  const score = 2.8 * metrics.occupancyCoverage
    + 1.0 * metrics.endpointSupport
    + 0.2 * distalScore
    + 0.9 * outwardScore
    - 1.3 * metrics.continuationCoverage
    - 2.2 * metrics.longestGap
    - 2.0 * metrics.lateralResidual
    - 1.0 * metrics.depthResidual
    - 0.85 * templateCost - 0.25 * angleCost
    - 2.8 * proximalArmContinuation.independentScore
    + 0.65 * attachment
    - 0.25 * Math.min(initialDistance / 0.06, 1);
  metrics.proximalContinuation = proximalArmContinuation;
  return {shoulder, hand, metrics, templateCost, angleCost, angle, distalScore,
    outwardScore, proximalArmContinuation, torsoAttachmentScore: attachment,
    refinementDistance: initialDistance, initialShoulder,
    score, valid: metrics.occupancyCoverage >= 0.55
      && metrics.endpointSupport >= 0.1 && templateCost < 100,
    path: centerlineFromCorridor(metrics, shoulder, hand)};
}

function buildShoulderCandidates(points, side, initialShoulder, hand, options = {}) {
  if (!initialShoulder || !hand) return [];
  const radius = clamp(finiteNumber(options.shoulderRefineRadius, 0.06), 0.03, 0.08);
  const local = points.filter(item => Math.abs(item.y - initialShoulder.y) <= radius
    && side * (item.x - initialShoulder.x) >= -0.04
    && side * (item.x - initialShoulder.x) <= 0.035
    && Math.abs(item.z - initialShoulder.z) <= 0.13
    && side * item.x > 0.025);
  const groups = new Map();
  local.forEach(item => {
    const heightBin = Math.floor((item.y - initialShoulder.y + radius) / 0.025);
    const sideBin = Math.floor((side * (item.x - initialShoulder.x) + 0.04) / 0.035);
    // Merge the depth-facing samples into one local cross-section. A surface
    // split here can turn the same shoulder into separate front/back
    // candidates and leave a torso edge with a better apparent corridor.
    const key = `${heightBin},${sideBin}`;
    const group = groups.get(key) || [];
    group.push(item);
    groups.set(key, group);
  });
  const candidates = [initialShoulder];
  const upperLocal = local.filter(item => item.y >= initialShoulder.y + 0.02);
  if (upperLocal.length) {
    const topHeight = quantile(upperLocal.map(item => item.y), 0.72);
    const top = upperLocal.filter(item => item.y >= topHeight);
    candidates.push({
      x: median(top.map(item => item.x)),
      y: median(top.map(item => item.y)),
      z: initialShoulder.z,
      support: top.length,
    });
  }
  groups.forEach(group => candidates.push({
    x: median(group.map(item => item.x)),
    y: median(group.map(item => item.y)),
    z: initialShoulder.z,
    support: group.length,
  }));
  return candidates.map(shoulder => buildArmCandidate(
    points, side, shoulder, hand, initialShoulder, options))
    .sort((left, right) => right.score - left.score);
}

function buildHandCandidates(points, side, shoulder, options = {}) {
  if (!shoulder) return [];
  const preferred = TEMPLATE_PRIORS.horizontal.armLength;
  const safety = [0.20, 0.52];
  const groups = new Map();
  points.forEach(item => {
    const outward = side * (item.x - shoulder.x);
    const vertical = shoulder.y - item.y;
    const depth = Math.abs(item.z - shoulder.z);
    const radius = Math.hypot(outward, vertical, item.z - shoulder.z);
    const angle = Math.atan2(vertical, Math.max(outward, EPSILON));
    if (radius < safety[0] || radius > safety[1]
        || depth > finiteNumber(options.armDepthLimit, 0.2)
        || angle < -0.35 || angle > 1.35) return;
    const radialBin = Math.floor((radius - safety[0]) / 0.035);
    const angleBin = Math.floor((angle + 0.35) / 0.18);
    const key = `${radialBin},${angleBin}`;
    const group = groups.get(key) || [];
    group.push(item);
    groups.set(key, group);
  });
  const candidates = [];
  groups.forEach(group => {
    const hand = {x: median(group.map(item => item.x)),
      y: median(group.map(item => item.y)), z: median(group.map(item => item.z)),
      support: group.length};
    const angle = Math.atan2(shoulder.y - hand.y,
      Math.max(side * (hand.x - shoulder.x), EPSILON));
    const outward = side * (hand.x - shoulder.x);
    const metrics = sampleGeometryCorridor(points, shoulder, hand,
      finiteNumber(options.armCorridorRadius, 0.085), 11, options.spatialIndex);
    const templateCost = softRangeCost(metrics.pathLengthN, preferred, safety);
    const distalScore = clamp((metrics.pathLengthN - safety[0])
      / Math.max(preferred[1] - safety[0], EPSILON), 0, 1);
    const outwardScore = clamp((outward - safety[0])
      / Math.max(preferred[1] - safety[0], EPSILON), 0, 1);
    const angleCost = softRangeCost(angle, [0, 1.1], [-0.35, 1.35]);
    const score = 2.8 * metrics.occupancyCoverage
      + 1.0 * metrics.endpointSupport
      + 0.2 * distalScore
      + 0.9 * outwardScore
      - 1.3 * metrics.continuationCoverage
      - 2.2 * metrics.longestGap
      - 2.0 * metrics.lateralResidual
      - 1.0 * metrics.depthResidual
      - 0.85 * templateCost - 0.25 * angleCost;
    candidates.push({shoulder, hand, metrics, templateCost, angleCost, angle, distalScore,
      outwardScore,
      score, valid: metrics.occupancyCoverage >= 0.55
        && metrics.endpointSupport >= 0.1 && templateCost < 100,
      path: centerlineFromCorridor(metrics, shoulder, hand)});
  });
  return candidates.sort((left, right) => right.score - left.score);
}

function selectArmPair(leftCandidates, rightCandidates) {
  const left = leftCandidates.filter(candidate => candidate.valid);
  const right = rightCandidates.filter(candidate => candidate.valid);
  const leftPool = left.length ? left : leftCandidates;
  const rightPool = right.length ? right : rightCandidates;
  let best = null;
  leftPool.forEach(leftCandidate => rightPool.forEach(rightCandidate => {
    const score = leftCandidate.score + rightCandidate.score
      - 0.55 * Math.abs(leftCandidate.metrics.pathLengthN
        - rightCandidate.metrics.pathLengthN)
      - 0.35 * Math.abs(leftCandidate.hand.y - rightCandidate.hand.y)
      - 0.2 * Math.abs(leftCandidate.angle - rightCandidate.angle)
      - 0.15 * Math.abs(leftCandidate.hand.z - rightCandidate.hand.z);
    if (!best || score > best.score) {
      best = {left: leftCandidate, right: rightCandidate, score};
    }
  }));
  return best;
}

function fitArmPair(points, upper, options = {}) {
  const center = upper.center || {x: 0, y: upper.shoulderHeight, z: 0};
  const leftShoulder = pointAt({x: center.x - upper.shoulderHalfWidth,
    y: upper.shoulderHeight, z: center.z}, points);
  const rightShoulder = pointAt({x: center.x + upper.shoulderHalfWidth,
    y: upper.shoulderHeight, z: center.z}, points);
  const leftCandidates = buildHandCandidates(points, -1, leftShoulder, options);
  const rightCandidates = buildHandCandidates(points, 1, rightShoulder, options);
  const initialPair = selectArmPair(leftCandidates, rightCandidates);
  const initialLeft = initialPair?.left || null;
  const initialRight = initialPair?.right || null;
  const refinedLeftCandidates = initialLeft
    ? buildShoulderCandidates(points, -1, leftShoulder, initialLeft.hand, options)
    : [];
  const refinedRightCandidates = initialRight
    ? buildShoulderCandidates(points, 1, rightShoulder, initialRight.hand, options)
    : [];
  const pair = selectArmPair(
    refinedLeftCandidates.length ? refinedLeftCandidates : leftCandidates,
    refinedRightCandidates.length ? refinedRightCandidates : rightCandidates);
  const left = pair?.left || initialLeft;
  const right = pair?.right || initialRight;
  const validLeft = !!left?.valid;
  const validRight = !!right?.valid;
  const leftPath = left?.path || [];
  const rightPath = right?.path || [];
  const symmetryResidual = validLeft && validRight
    ? clamp(0.55 * Math.abs(left.metrics.pathLengthN - right.metrics.pathLengthN)
      + 0.3 * Math.abs(left.hand.y - right.hand.y)
      + 0.15 * Math.abs(left.angle - right.angle), 0, 1) : 0.5;
  return {
    leftTrack: {samples: leftPath}, rightTrack: {samples: rightPath},
    leftShoulder: validLeft ? left.shoulder : leftShoulder,
    rightShoulder: validRight ? right.shoulder : rightShoulder,
    leftElbow: validLeft ? pointAtPathFraction(leftPath, 0.5) : null,
    rightElbow: validRight ? pointAtPathFraction(rightPath, 0.5) : null,
    leftHand: validLeft ? left.hand : null, rightHand: validRight ? right.hand : null,
    leftArmLength: left?.metrics.pathLengthN || 0,
    rightArmLength: right?.metrics.pathLengthN || 0,
    symmetryResidual, validLeft, validRight,
    diagnostics: {
      left: candidateDiagnostic(left), right: candidateDiagnostic(right),
      leftRunnerUp: candidateDiagnostic((refinedLeftCandidates.length
        ? refinedLeftCandidates : leftCandidates).find(item => item !== left)),
      rightRunnerUp: candidateDiagnostic((refinedRightCandidates.length
        ? refinedRightCandidates : rightCandidates).find(item => item !== right)),
      preferredLength: [...TEMPLATE_PRIORS.horizontal.armLength], safetyLength: [0.20, 0.52],
      pairScore: pair?.score || 0,
    },
  };
}

function fallbackPoint(key) {
  const values = {
    chest: [0, 0.72], pelvis: [0, 0.53],
    leftShoulder: [-0.15, 0.79], leftElbow: [-0.36, 0.70], leftHand: [-0.58, 0.62],
    rightShoulder: [0.15, 0.79], rightElbow: [0.36, 0.70], rightHand: [0.58, 0.62],
    leftHip: [-0.12, 0.53], leftKnee: [-0.15, 0.29], leftFoot: [-0.16, 0.04],
    rightHip: [0.12, 0.53], rightKnee: [0.15, 0.29], rightFoot: [0.16, 0.04],
  };
  const value = values[key] || [0, 0.5];
  return {x: value[0], y: value[1], z: 0};
}

function controlSemantic(point, points, bodyDepthN, fitted = true) {
  const value = pointAt(point, points, bodyDepthN) || fallbackPoint('pelvis');
  return {...value, fitted};
}

function fitSlab(points, bodyDepthN, slabWidth, options = {}) {
  const accepted = acceptedForSlab(points, bodyDepthN, slabWidth, options);
  const searchOptions = {...options, spatialIndex: buildSpatialIndex(accepted)};
  const profile = buildHeightCrossSections(accepted, options);
  const centerline = buildCenterline(accepted, options);
  const upper = fitHeadNeckShoulders(accepted, profile, centerline, searchOptions);
  const lower = fitPelvisAndHips(accepted, centerline, searchOptions);
  const arms = fitArmPair(accepted, upper, searchOptions);
  const semantic = {};
  const fitted = new Set();
  const set = (key, point, isFitted = true) => {
    if (!point) return;
    semantic[key] = controlSemantic(point, accepted, bodyDepthN, isFitted);
    if (isFitted) fitted.add(key);
  };
  set('chest', upper.chest, centerline.length >= 2);
  set('pelvis', lower.pelvis, lower.validLeft || lower.validRight);
  set('leftShoulder', arms.leftShoulder, profile.length >= 2);
  set('rightShoulder', arms.rightShoulder, profile.length >= 2);
  set('leftElbow', arms.leftElbow, arms.validLeft);
  set('rightElbow', arms.rightElbow, arms.validRight);
  set('leftHand', arms.leftHand, arms.validLeft);
  set('rightHand', arms.rightHand, arms.validRight);
  set('leftHip', lower.leftHip, lower.validLeft);
  set('rightHip', lower.rightHip, lower.validRight);
  set('leftKnee', lower.leftKnee, lower.validLeft);
  set('rightKnee', lower.rightKnee, lower.validRight);
  set('leftFoot', lower.leftFoot, lower.validLeft);
  set('rightFoot', lower.rightFoot, lower.validRight);
  const failures = [];
  if (!lower.validLeft) failures.push('left_leg:path_not_found');
  if (!lower.validRight) failures.push('right_leg:path_not_found');
  if (!arms.validLeft) failures.push('left_arm:path_not_found');
  if (!arms.validRight) failures.push('right_arm:path_not_found');
  const project = path => (path || []).map(point => controlSemantic(point, accepted, bodyDepthN));
  const paths = {
    torso: centerline.map(item => controlSemantic({x: item.side, y: item.height, z: item.depth}, accepted, bodyDepthN)),
    leftArm: project(arms.leftTrack.samples), rightArm: project(arms.rightTrack.samples),
    leftLeg: project(lower.leftTrack.samples), rightLeg: project(lower.rightTrack.samples),
  };
  const roleValid = {
    torso: fitted.has('chest') && fitted.has('pelvis'),
    left_arm: arms.validLeft, right_arm: arms.validRight,
    left_leg: lower.validLeft, right_leg: lower.validRight,
  };
  const template = {
    headBottomHeight: upper.headBottomHeight, neckHeight: upper.neckHeight,
    shoulderHeight: upper.shoulderHeight, shoulderHalfWidth: upper.shoulderHalfWidth,
    chestHeight: upper.chestHeight, pelvisHeight: lower.pelvisHeight,
    hipHalfWidth: lower.hipHalfWidth,
    leftArmAngle: arms.validLeft ? Math.atan2(arms.leftHand.y - arms.leftShoulder.y,
      Math.abs(arms.leftHand.x - arms.leftShoulder.x)) : 0,
    rightArmAngle: arms.validRight ? Math.atan2(arms.rightHand.y - arms.rightShoulder.y,
      Math.abs(arms.rightHand.x - arms.rightShoulder.x)) : 0,
    leftArmLength: arms.leftArmLength, rightArmLength: arms.rightArmLength,
    leftLegSide: lower.leftHip?.x || 0, rightLegSide: lower.rightHip?.x || 0,
    leftLegLength: lower.leftLegLength, rightLegLength: lower.rightLegLength,
  };
  return {controls: semantic, semantic, fitted, roleValid, failures, paths, profile,
    centerline, template, armCompletion: arms.diagnostics,
    legCompletion: lower.diagnostics, acceptedPointCount: accepted.length,
    residuals: {headNeck: upper.residual, shoulder: clamp(upper.shoulderHalfWidth / 0.3, 0, 1),
      pelvis: lower.validLeft && lower.validRight ? 0 : 1,
      arms: arms.validLeft && arms.validRight ? arms.symmetryResidual
        : (arms.validLeft ? 0.5 : 0) + (arms.validRight ? 0.5 : 0),
      legs: (lower.validLeft ? 0 : 0.5) + (lower.validRight ? 0 : 0.5)}};
}

function pointToWorld(point, bounds, frame) {
  if (!point) return [0, 0, 0];
  const side = bounds.sideCenter + point.x * bounds.height;
  const height = bounds.lowHeight + point.y * bounds.height;
  const depth = bounds.depthCenter + point.z * bounds.height;
  return add(add(scale(frame.right, side), scale(frame.up, height)), scale(frame.forward, depth));
}

function spreadFor(values, center) {
  if (!values.length) return 1;
  return Math.max(...values.map(value => distance2(value, center)));
}

function confidenceLabel(score) {
  if (score >= 0.72) return 'high';
  if (score >= 0.45) return 'medium';
  return 'low';
}

function confidenceScore(support, spread, residual) {
  const supportScore = clamp(support, 0, 1);
  const stability = 1 - clamp(spread / 0.12, 0, 1);
  const residualScore = 1 - clamp(residual, 0, 1);
  return clamp(0.48 * supportScore + 0.34 * stability + 0.18 * residualScore, 0, 1);
}

function combineSlabFits(fits, bounds, bodyDepthN, frame) {
  const controls = {};
  const semantic = {};
  const spread = {};
  const support = {};
  const pathRoleFor = role => role === 'torso' ? 'torso'
    : role === 'left_arm' ? 'leftArm' : role === 'right_arm' ? 'rightArm'
      : role === 'left_leg' ? 'leftLeg' : 'rightLeg';
  const roleForKey = key => Object.entries(ROLE_CONTROL_KEYS)
    .find(([, keys]) => keys.includes(key))?.[0] || 'torso';
  const fitCohortForRole = role => {
    const valid = fits.filter(fit => fit.roleValid[role]);
    if (valid.length <= 1) return valid;
    const pathRole = pathRoleFor(role);
    const ranked = valid.map(fit => ({fit, length: pathLength(fit.paths[pathRole] || [])}))
      .sort((left, right) => right.length - left.length
        || right.acceptedPointCount - left.acceptedPointCount);
    const maximum = ranked[0].length;
    // A wider slab can add clothing or hair to a path. Consensus is therefore
    // restricted to fits with comparable coherent path length, rather than
    // allowing a short secondary branch to vote against the arm or leg.
    const threshold = maximum > 0.12 ? maximum * 0.72 : maximum;
    const cohort = ranked.filter(item => item.length >= threshold).map(item => item.fit);
    return cohort.length ? cohort : [ranked[0].fit];
  };
  const validValuesFor = key => fitCohortForRole(roleForKey(key)).map(fit => fit.semantic[key])
    .filter(value => value?.fitted === true);
  CONTROL_KEYS.forEach(key => {
    const values = validValuesFor(key);
    support[key] = values.length;
    if (!values.length) return;
    const result = {x: median(values.map(value => value.x)),
      y: clamp(median(values.map(value => value.y)), 0, 1),
      z: median(values.map(value => value.z))};
    semantic[key] = result;
    spread[key] = spreadFor(values, result);
    controls[key] = {position: pointToWorld(result, bounds, frame), semantic: {
      sideN: result.x, height01: result.y, depthN: result.z - bodyDepthN,
    }, fitted: true, source: 'geometry', spread: spread[key], support: values.length};
  });
  const bestForRole = role => fitCohortForRole(role)[0] || null;
  const paths = {};
  ['torso', 'leftArm', 'rightArm', 'leftLeg', 'rightLeg'].forEach(pathRole => {
    const role = pathRole === 'torso' ? 'torso' : pathRole.replace('Arm', '_arm').replace('Leg', '_leg');
    const selected = bestForRole(role);
    paths[pathRole] = selected ? selected.paths[pathRole] : [];
  });
  return {controls, semantic, spread, support, paths};
}

function regionConfidence(fits, combined, role, controlKeys, residualKey) {
  const validSlabs = fits.filter(fit => fit.roleValid[role]);
  const values = controlKeys.flatMap(key => fits.map(fit => fit.semantic[key])
    .filter(value => value?.fitted === true));
  const spread = values.length && combined.semantic[controlKeys[0]]
    ? Math.max(...controlKeys.map(key => combined.spread[key] || 0)) : 1;
  const residual = validSlabs.length
    ? median(validSlabs.map(fit => fit.residuals[residualKey] ?? 1)) : 1;
  const score = confidenceScore(validSlabs.length / Math.max(fits.length, 1), spread, residual);
  return {score, confidence: confidenceLabel(score), support: validSlabs.length,
    total: fits.length, spread, residual};
}

function diagnosticsOverlay(fit, bounds, frame, bodyDepthN) {
  const line = (first, second) => [pointToWorld(first, bounds, frame), pointToWorld(second, bounds, frame)];
  const center = (height, side = 0) => ({x: side, y: height, z: bodyDepthN});
  const arc = (shoulder, side, radius) => {
    const points = [];
    const start = -0.35;
    const end = 1.35;
    for (let index = 0; index <= 16; index += 1) {
      const angle = start + (end - start) * index / 16;
      points.push(pointToWorld({x: shoulder.x + side * radius * Math.cos(angle),
        y: shoulder.y - radius * Math.sin(angle), z: shoulder.z}, bounds, frame));
    }
    return points;
  };
  const shoulderFor = side => fit.semantic?.[side < 0 ? 'leftShoulder' : 'rightShoulder']
    || center(fit.template.shoulderHeight, side * fit.template.shoulderHalfWidth);
  const searchRegions = {
    arms: [-1, 1].map(side => {
      const shoulder = shoulderFor(side);
      return {side, shoulder: pointToWorld(shoulder, bounds, frame),
        preferred: arc(shoulder, side, TEMPLATE_PRIORS.horizontal.armLength[1]),
        safety: arc(shoulder, side, 0.52)};
    }),
    hips: {
      preferred: line(center(TEMPLATE_PRIORS.vertical.hip[0], -0.3),
        center(TEMPLATE_PRIORS.vertical.hip[0], 0.3)),
      preferredTop: line(center(TEMPLATE_PRIORS.vertical.hip[1], -0.3),
        center(TEMPLATE_PRIORS.vertical.hip[1], 0.3)),
      safety: line(center(0.40, -0.34), center(0.40, 0.34)),
      safetyTop: line(center(0.63, -0.34), center(0.63, 0.34)),
    },
  };
  const profile = fit.profile || [];
  const minHeight = profile.length ? Math.min(...profile.map(item => item.height)) : 0.72;
  const maxHeight = profile.length ? Math.max(...profile.map(item => item.height)) : 0.92;
  return {
    headNeckSearchBand: line(center(minHeight), center(maxHeight)),
    shoulderHeightLine: line(center(fit.template.shoulderHeight, -0.55), center(fit.template.shoulderHeight, 0.55)),
    pelvisHeightLine: line(center(fit.template.pelvisHeight, -0.45), center(fit.template.pelvisHeight, 0.45)),
    hipCenters: [pointToWorld(fit.semantic.leftHip || fallbackPoint('leftHip'), bounds, frame),
      pointToWorld(fit.semantic.rightHip || fallbackPoint('rightHip'), bounds, frame)],
    trackedArmSamples: {left: (fit.paths.leftArm || []).map(item => pointToWorld(item, bounds, frame)),
      right: (fit.paths.rightArm || []).map(item => pointToWorld(item, bounds, frame))},
    trackedLegSamples: {left: (fit.paths.leftLeg || []).map(item => pointToWorld(item, bounds, frame)),
      right: (fit.paths.rightLeg || []).map(item => pointToWorld(item, bounds, frame))},
    searchRegions,
    showSearchRegions: fit.showSearchRegions === true,
    widthProfile: profile.map(item => ({height: item.height, leftExtent: item.leftExtent,
      rightExtent: item.rightExtent, width: item.width, center: item.center})),
  };
}

function consensusTemplate(bestTemplate, combined) {
  const template = {...(bestTemplate || {})};
  const value = key => combined.semantic[key] || null;
  const shoulderLeft = value('leftShoulder');
  const shoulderRight = value('rightShoulder');
  const hipLeft = value('leftHip');
  const hipRight = value('rightHip');
  if (shoulderLeft && shoulderRight) {
    template.shoulderHeight = median([shoulderLeft.y, shoulderRight.y]);
    template.shoulderHalfWidth = Math.max(0.04,
      Math.abs(shoulderRight.x - shoulderLeft.x) * 0.5);
  }
  if (value('chest')) template.chestHeight = value('chest').y;
  if (value('pelvis')) template.pelvisHeight = value('pelvis').y;
  if (hipLeft && hipRight) template.hipHalfWidth = Math.max(0.025,
    Math.abs(hipRight.x - hipLeft.x) * 0.5);
  const armTemplate = (side, shoulderKey, handKey, pathKey) => {
    const shoulder = value(shoulderKey);
    const hand = value(handKey);
    if (shoulder && hand) {
      template[`${side}ArmAngle`] = Math.atan2(hand.y - shoulder.y,
        Math.abs(hand.x - shoulder.x));
    }
    const path = combined.paths[pathKey] || [];
    if (path.length >= 2) template[`${side}ArmLength`] = pathLength(path);
  };
  armTemplate('left', 'leftShoulder', 'leftHand', 'leftArm');
  armTemplate('right', 'rightShoulder', 'rightHand', 'rightArm');
  const legTemplate = (side, hipKey, pathKey, sideKey) => {
    const hip = value(hipKey);
    if (hip) template[sideKey] = hip.x;
    const path = combined.paths[pathKey] || [];
    if (path.length >= 2) template[`${side}LegLength`] = pathLength(path);
  };
  legTemplate('left', 'leftHip', 'leftLeg', 'leftLegSide');
  legTemplate('right', 'rightHip', 'rightLeg', 'rightLegSide');
  return template;
}

function emptyRig(frame, diagnostics = {}, reason = 'no_rest_geometry', available = true) {
  const controls = Object.fromEntries(CONTROL_KEYS.map(key => {
    const point = fallbackPoint(key);
    return [key, {position: [0, 0, 0], semantic: {sideN: point.x,
      height01: point.y, depthN: 0}, confidence: 'low', source: 'fallback', fitted: false,
      support: 0}];
  }));
  return {version: 1, source: 'geometry', available, accepted: false, confidence: 'low',
    confidenceByRegion: {torso: 'low', arms: 'low', legs: 'low', overall: 'low'},
    frame: {...frame, lowHeight: 0, highHeight: 0, height: 0}, controls,
    paths: {torso: [], leftArm: [], rightArm: [], leftLeg: [], rightLeg: []},
    diagnostics: {...diagnostics, failureReasons: [reason], fallbackControls: [...CONTROL_KEYS]},
  };
}

/** Fit a semantic, viewer-owned control rig from registered mesh rest geometry. */
export function buildHumanoidControlRig({meshes = [], axes, orientationState, options = {}} = {}) {
  const started = typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now();
  const frame = coordinateFrame(axes);
  const orientationReady = orientationState
    ? orientationState.orientationInitialized === true : true;
  const baseOrientation = quaternion4(orientationState?.baseOrientation);
  const semanticDiagnostics = {
    semanticAxes: {up: [...frame.up], right: [...frame.right], forward: [...frame.forward]},
    orientationReady,
    baseOrientation,
    modelOrientationRevision: finiteNumber(orientationState?.modelOrientationRevision, 0),
    semanticSpans: {up: 0, right: 0, forward: 0},
  };
  if (orientationState && !orientationReady) {
    return emptyRig(frame, {...semanticDiagnostics, pointCount: 0, sampledPointCount: 0,
      voxelCount: 0, fitRuntimeMs: 0}, 'orientation_not_ready', false);
  }
  const projected = semanticPoints(meshes, frame);
  if (!projected.length) return emptyRig({up: frame.up, right: frame.right, forward: frame.forward},
    {...semanticDiagnostics, pointCount: 0, sampledPointCount: 0, voxelCount: 0,
      slabFits: [], fitRuntimeMs: 0});
  const workingPoints = downsamplePoints(projected,
    Math.max(1000, Math.floor(finiteNumber(options.maxPointCount, DEFAULT_MAX_POINT_COUNT))));
  const bounds = boundsFor(workingPoints);
  const normalized = workingPoints.map(item => normalizedPoint(item, bounds));
  const voxelSize = clamp(finiteNumber(options.voxelSize, DEFAULT_VOXEL_SIZE), 0.003, 0.08);
  const voxels = voxelize(normalized, voxelSize);
  const depthMode = bodyDepthMode(voxels, clamp(finiteNumber(options.depthBinSize, voxelSize), 0.003, 0.08));
  const slabWidths = Array.isArray(options.slabWidths) && options.slabWidths.length
    ? options.slabWidths.map(value => clamp(Number(value), 0.005, 0.3)) : [...DEFAULT_SLAB_WIDTHS];
  const fits = slabWidths.map(width => fitSlab(voxels, depthMode.depthN, width, options));
  const best = [...fits].sort((left, right) => right.fitted.size - left.fitted.size
    || right.acceptedPointCount - left.acceptedPointCount)[0] || null;
  const combined = combineSlabFits(fits, bounds, depthMode.depthN, frame);
  const regionDetails = {
    torso: regionConfidence(fits, combined, 'torso', ['chest', 'pelvis'], 'pelvis'),
    left_arm: regionConfidence(fits, combined, 'left_arm', ROLE_CONTROL_KEYS.left_arm, 'arms'),
    right_arm: regionConfidence(fits, combined, 'right_arm', ROLE_CONTROL_KEYS.right_arm, 'arms'),
    left_leg: regionConfidence(fits, combined, 'left_leg', ROLE_CONTROL_KEYS.left_leg, 'legs'),
    right_leg: regionConfidence(fits, combined, 'right_leg', ROLE_CONTROL_KEYS.right_leg, 'legs'),
  };
  const confidenceByRegion = {
    torso: regionDetails.torso.confidence,
    leftArm: regionDetails.left_arm.confidence, rightArm: regionDetails.right_arm.confidence,
    arms: confidenceLabel((regionDetails.left_arm.score + regionDetails.right_arm.score) * 0.5),
    leftLeg: regionDetails.left_leg.confidence, rightLeg: regionDetails.right_leg.confidence,
    legs: confidenceLabel((regionDetails.left_leg.score + regionDetails.right_leg.score) * 0.5),
    headNeck: regionDetails.torso.confidence,
  };
  const overallScore = [regionDetails.torso.score, regionDetails.left_arm.score,
    regionDetails.right_arm.score, regionDetails.left_leg.score, regionDetails.right_leg.score]
    .reduce((sum, value) => sum + value, 0) / 5;
  const confidence = confidenceLabel(overallScore);
  confidenceByRegion.overall = confidence;
  const controls = {};
  CONTROL_KEYS.forEach(key => {
    const value = combined.controls[key];
    const fallback = fallbackPoint(key);
    const role = Object.entries(ROLE_CONTROL_KEYS).find(([, keys]) => keys.includes(key))?.[0];
    const region = role ? regionDetails[role] : regionDetails.torso;
    controls[key] = value ? {...value, confidence: confidenceLabel(confidenceScore(
      (value.support || 0) / Math.max(fits.length, 1), value.spread || 0, region.residual))}
      : {position: pointToWorld(fallback, bounds, frame), semantic: {sideN: fallback.x,
        height01: fallback.y, depthN: fallback.z - depthMode.depthN}, confidence: 'low',
        source: 'fallback', fitted: false, support: 0};
  });
  const failureReasons = ROLE_NAMES.filter(role => !fits.some(fit => fit.roleValid[role]))
    .flatMap(role => role === 'torso' ? ['torso:fit_not_found'] : [`${role}:path_not_found`]);
  const slabDiagnostics = fits.map((fit, index) => ({
    width: slabWidths[index], acceptedPointCount: fit.acceptedPointCount,
    controlsFound: fit.fitted.size, validRoles: Object.keys(fit.roleValid)
      .filter(role => fit.roleValid[role]), failureReasons: fit.failures, residuals: fit.residuals,
  }));
  const template = consensusTemplate(best?.template, combined);
  const templateDiagnostic = best ? diagnosticsOverlay({...best, template,
    showSearchRegions: options.debugSearchRegions === true}, bounds, frame,
    depthMode.depthN) : {};
  const diagnostics = {
    pointCount: projected.length, sampledPointCount: workingPoints.length, voxelCount: voxels.length,
    voxelSize, bodyDepth: bounds.depthCenter + depthMode.depthN * bounds.height,
    bodyDepthN: depthMode.depthN, bodyDepthSpread: depthMode.spreadN,
    ...semanticDiagnostics,
    semanticSpans: {
      up: bounds.height,
      right: bounds.maxSide - bounds.minSide,
      forward: bounds.maxDepth - bounds.minDepth,
    },
    slabWidths: slabWidths.map(Number), slabFits: slabDiagnostics,
    controlSpreadByRole: combined.spread, consensusSupportByControl: combined.support,
    consensusSupportByRole: Object.fromEntries(ROLE_NAMES.map(role => [role,
      fits.filter(fit => fit.roleValid[role]).length])),
    bilateralSpread: {
      arms: Math.abs((combined.semantic.leftHand?.x || 0) + (combined.semantic.rightHand?.x || 0)),
      legs: Math.abs((combined.semantic.leftFoot?.x || 0) + (combined.semantic.rightFoot?.x || 0)),
    },
    limbCompletion: {
      arms: {
        left: best?.armCompletion?.left || null,
        right: best?.armCompletion?.right || null,
        leftRunnerUp: best?.armCompletion?.leftRunnerUp || null,
        rightRunnerUp: best?.armCompletion?.rightRunnerUp || null,
      },
      legs: {
        left: best?.legCompletion?.left || null,
        right: best?.legCompletion?.right || null,
        leftRunnerUp: best?.legCompletion?.leftRunnerUp || null,
        rightRunnerUp: best?.legCompletion?.rightRunnerUp || null,
      },
    },
    confidenceByRegion, confidenceDetails: regionDetails,
    fallbackControls: CONTROL_KEYS.filter(key => !controls[key].fitted),
    failureReasons, templateParameters: template, templateDiagnostic,
    showSearchRegions: options.debugSearchRegions === true,
    fitRuntimeMs: Math.max(0, (typeof performance !== 'undefined' && performance.now
      ? performance.now() : Date.now()) - started),
  };
  const result = {
    version: 1, source: 'geometry', available: true,
    accepted: projected.length > 0 && Object.keys(combined.semantic).length > 0,
    confidence, confidenceByRegion,
    frame: {up: frame.up, right: frame.right, forward: frame.forward,
      lowHeight: bounds.lowHeight, highHeight: bounds.highHeight, height: bounds.height,
      sideCenter: bounds.sideCenter, depthCenter: bounds.depthCenter,
      bodyDepth: bounds.depthCenter + depthMode.depthN * bounds.height, bodyDepthN: depthMode.depthN},
    template, controls, paths: combined.paths, diagnostics,
  };
  return serializeHumanoidControlRig(result);
}

/** Return a JSON-safe snapshot suitable for events, panels, and overlays. */
export function serializeHumanoidControlRig(rig) {
  if (!rig) return null;
  const result = JSON.parse(JSON.stringify(rig));
  result.controls = Object.fromEntries(CONTROL_KEYS.map(key => {
    const control = result.controls?.[key] || {};
    return [key, {position: vector3(control.position), semantic: {...(control.semantic || {})},
      confidence: control.confidence || result.confidence || 'low',
      source: control.source || 'geometry', fitted: control.fitted !== false,
      spread: finiteNumber(control.spread, 0), support: finiteNumber(control.support, 0)}];
  }));
  return result;
}

export const HUMANOID_CONTROL_KEYS = CONTROL_KEYS;
export const HUMANOID_TEMPLATE_PRIORS = TEMPLATE_PRIORS;
