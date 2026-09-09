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

function softPriorCost(value, region, tolerance = 0.08) {
  const low = region[0];
  const high = region[1];
  if (value < low) return (low - value) / tolerance;
  if (value > high) return (value - high) / tolerance;
  return 0;
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
  return clamp(quantile(samples.map(item => Math.abs(item.x)), 0.78), 0.045,
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
    const cost = item.width + 0.08 * softPriorCost(item.height, vertical.neck, 0.08);
    return !best || cost < best.cost ? {item, cost} : best;
  }, null)?.item;
  const neckHeight = neck?.height ?? 0.84;
  // Keep the canonical shoulder band as a soft prior rather than a hard
  // ceiling. A mesh without an explicitly separated head can place the
  // shoulder attachment above .90, and the width expansion is the stronger
  // evidence in that case.
  const shoulderCandidates = profile.filter(item => item.height >= 0.70
    && item.height <= 0.98);
  let shoulder = shoulderCandidates.reduce((best, item) => {
    const below = profileWidthAt(profile, item.height - 0.04);
    const above = profileWidthAt(profile, item.height + 0.04);
    // The attachment is the first strong width transition. Looking both
    // directions avoids selecting a later maximum (or a head-top gap) after
    // the arm has already widened the profile.
    const expansion = Math.max(0, item.width - below, item.width - above);
    const cost = -expansion + 0.12 * softPriorCost(item.height, vertical.shoulder, 0.1)
      + 0.02 * Math.abs(item.height - neckHeight);
    return !best || cost < best.cost ? {item, cost} : best;
  }, null)?.item;
  if (!shoulder) shoulder = closestProfile(profile, 0.79);
  const shoulderHeight = clamp(shoulder?.height ?? 0.79, 0.55, 0.95);
  const shoulderCenter = centerAt(centerline, shoulderHeight);
  const shoulderHalfWidth = centralHalfWidth(points, shoulderHeight - 0.07, options);
  const chestHeight = clamp(shoulderHeight - Math.max(0.045,
    (shoulderHeight - neckHeight) * 0.2), 0.45, 0.9);
  const chest = centerAt(centerline, chestHeight);
  const headBottomHeight = clamp(neckHeight + 0.025, 0, 1);
  const residual = profile.length ? clamp((profileWidthAt(profile, neckHeight)
    / Math.max(profileWidthAt(profile, shoulderHeight), 0.02)), 0, 1) : 1;
  return {neckHeight, headBottomHeight, shoulderHeight, shoulderHalfWidth, chestHeight,
    center: shoulderCenter, chest, residual};
}

function lowBodySeed(points, side, options = {}) {
  const low = points.filter(item => item.y <= clamp(finiteNumber(options.legSeedTop, 0.12), 0.06, 0.2)
    && side * item.x > 0.025);
  if (!low.length) return null;
  return {x: median(low.map(item => item.x)), y: quantile(low.map(item => item.y), 0.25),
    z: median(low.map(item => item.z))};
}

function localTrackPoint(points, height, side, previousX, radius, step) {
  const samples = sliceSamples(points, height, Math.max(step * 0.8, 0.018))
    .filter(item => side * item.x > 0.025 && Math.abs(item.x - previousX) <= radius);
  if (!samples.length) return null;
  const xs = samples.map(item => item.x);
  return {x: median(xs), y: median(samples.map(item => item.y)),
    z: median(samples.map(item => item.z)), support: samples.length,
    width: quantile(xs, 0.9) - quantile(xs, 0.1)};
}

function hasCentralBridge(points, height, leftX, rightX, step) {
  if (height < 0.22) return false;
  const samples = sliceSamples(points, height, Math.max(step * 0.9, 0.02));
  if (!samples.length) return false;
  const low = Math.min(leftX, rightX);
  const high = Math.max(leftX, rightX);
  return samples.filter(item => item.x > low + 0.025 && item.x < high - 0.025).length >= 2;
}

function trackLegFromBottom(points, side, seed, options = {}) {
  if (!seed) return {samples: []};
  const step = clamp(finiteNumber(options.trackStep, 0.025), 0.015, 0.06);
  const samples = [seed];
  let previous = seed;
  let misses = 0;
  const lastHeight = clamp(finiteNumber(options.legTrackTop, 0.76), 0.5, 0.82);
  for (let height = Math.max(seed.y + step, 0.02); height <= lastHeight; height += step) {
    const radius = clamp(0.085 + (height - seed.y) * 0.12, 0.085, 0.18);
    const candidate = localTrackPoint(points, height, side, previous.x, radius, step);
    if (!candidate) {
      misses += 1;
      if (samples.length >= 3 && misses >= 2) break;
      continue;
    }
    misses = 0;
    previous = candidate;
    samples.push(candidate);
  }
  return {samples};
}

function pathLength(path) {
  let total = 0;
  for (let index = 1; index < path.length; index += 1) total += distance2(path[index], path[index - 1]);
  return total;
}

function fitPelvisAndHips(points, centerline, options = {}) {
  const leftSeed = lowBodySeed(points, -1, options);
  const rightSeed = lowBodySeed(points, 1, options);
  const leftTrack = trackLegFromBottom(points, -1, leftSeed, options);
  const rightTrack = trackLegFromBottom(points, 1, rightSeed, options);
  // Remove the part of a track that has entered a central pelvis column. Both
  // sides are inspected together so a skirt or a torso cannot become a leg.
  const commonLength = Math.min(leftTrack.samples.length, rightTrack.samples.length);
  let stop = commonLength;
  const step = clamp(finiteNumber(options.trackStep, 0.025), 0.015, 0.06);
  for (let index = 2; index < commonLength; index += 1) {
    const left = leftTrack.samples[index];
    const right = rightTrack.samples[index];
    if (hasCentralBridge(points, (left.y + right.y) * 0.5, left.x, right.x, step)) {
      stop = index;
      break;
    }
  }
  // Never pad a missing track with sparse array entries. The small-model and
  // empty-payload paths rely on an actually empty track becoming fallback
  // controls, rather than reaching distance/knee calculations as undefined
  // points.
  const retainedCount = Math.min(commonLength, stop);
  leftTrack.samples.length = retainedCount;
  rightTrack.samples.length = retainedCount;
  const validLeft = leftTrack.samples.length >= 2;
  const validRight = rightTrack.samples.length >= 2;
  const topLeft = validLeft ? leftTrack.samples[leftTrack.samples.length - 1] : null;
  const topRight = validRight ? rightTrack.samples[rightTrack.samples.length - 1] : null;
  const bottomLeft = validLeft ? leftTrack.samples[0] : null;
  const bottomRight = validRight ? rightTrack.samples[0] : null;
  const hipHeight = topLeft && topRight ? (topLeft.y + topRight.y) * 0.5
    : topLeft?.y ?? topRight?.y ?? 0.52;
  const fallbackCenter = centerAt(centerline, hipHeight);
  const leftHip = topLeft || {x: -0.14, y: hipHeight, z: fallbackCenter.z};
  const rightHip = topRight || {x: 0.14, y: hipHeight, z: fallbackCenter.z};
  const pelvis = {x: (leftHip.x + rightHip.x) * 0.5, y: hipHeight,
    z: median([leftHip.z, rightHip.z, fallbackCenter.z])};
  const knee = track => {
    if (!track.samples.length) return null;
    const path = [...track.samples].reverse();
    const total = pathLength(path);
    const target = total * 0.5;
    let travelled = 0;
    for (let index = 1; index < path.length; index += 1) {
      const segment = distance2(path[index], path[index - 1]);
      if (travelled + segment >= target && segment > EPSILON) {
        const factor = (target - travelled) / segment;
        return {x: path[index - 1].x + (path[index].x - path[index - 1].x) * factor,
          y: path[index - 1].y + (path[index].y - path[index - 1].y) * factor,
          z: path[index - 1].z + (path[index].z - path[index - 1].z) * factor};
      }
      travelled += segment;
    }
    return path[Math.floor(path.length * 0.5)] || path[0];
  };
  return {leftTrack, rightTrack, leftHip: pointAt(leftHip, points),
    rightHip: pointAt(rightHip, points), pelvis: pointAt(pelvis, points),
    leftKnee: pointAt(knee(leftTrack), points), rightKnee: pointAt(knee(rightTrack), points),
    leftFoot: pointAt(bottomLeft, points), rightFoot: pointAt(bottomRight, points),
    pelvisHeight: hipHeight,
    hipHalfWidth: (Math.abs(leftHip.x) + Math.abs(rightHip.x)) * 0.5,
    leftLegLength: validLeft ? pathLength(leftTrack.samples) : 0,
    rightLegLength: validRight ? pathLength(rightTrack.samples) : 0,
    validLeft, validRight};
}

function armCandidatesAt(points, side, outward, previousY, step, shoulderHeight,
  allowSteep = false) {
  const corridor = points.filter(item => {
    const progress = side * item.x;
    return progress >= outward - 0.025 && progress <= outward + step * 1.7
      && progress > 0.025
      && item.y <= shoulderHeight + 0.065 && item.y >= shoulderHeight - 0.48;
  });
  const preferred = corridor.filter(item => item.y >= shoulderHeight - 0.105
    && Math.abs(item.y - previousY) <= 0.13);
  const samples = preferred.length ? preferred : allowSteep
    ? corridor.filter(item => Math.abs(item.y - previousY) <= 0.48) : [];
  if (!samples.length) return null;
  // A median of the local corridor resists fingers, sleeve edges, and sparse
  // surface samples while still following the actual center of the arm.
  return {x: median(samples.map(item => item.x)), y: median(samples.map(item => item.y)),
    z: median(samples.map(item => item.z)), support: samples.length,
    steep: preferred.length === 0};
}

function distalArmCenter(points, side, endpoint, shoulderHeight) {
  const progress = side * endpoint.x;
  const terminal = points.filter(item => side * item.x >= progress - 0.11
    && side * item.x <= progress + 0.015
    && item.y >= shoulderHeight - 0.18 && item.y <= shoulderHeight + 0.08);
  if (terminal.length < 2) return endpoint;
  // The terminal cross-section contains both sleeve/hand surfaces. Its
  // median is a center estimate, unlike the last outward surface sample.
  const xs = terminal.map(item => item.x);
  const low = quantile(xs, 0.05);
  const high = quantile(xs, 0.95);
  return {x: (low + high) * 0.5,
    y: median(terminal.map(item => item.y)), z: median(terminal.map(item => item.z)),
    support: terminal.length};
}

function trackArmFromShoulder(points, side, shoulder, options = {}) {
  if (!shoulder) return {samples: [], length: 0};
  const step = clamp(finiteNumber(options.armTrackStep, 0.035), 0.02, 0.08);
  const allowSteep = options.allowSteep === true;
  const samples = [shoulder];
  let previous = shoulder;
  let misses = 0;
  let outward = side * shoulder.x + 0.015;
  const shoulderProgress = side * shoulder.x;
  const maximum = points.reduce((result, item) => Math.max(result, side * item.x),
    side * shoulder.x);
  while (outward <= maximum + step && outward <= 1.2) {
    const candidate = armCandidatesAt(points, side, outward, previous.y, step,
      shoulder.y, allowSteep);
    const progress = candidate ? side * candidate.x : -Infinity;
    // The first arm cross-section can overlap the torso/shoulder surface. It
    // is valid evidence even when its median is level with the shoulder; the
    // next outward bins decide whether the corridor truly continues.
    // A steep candidate is held back until the search has cleared the
    // shoulder attachment. This prevents a nearby downward skirt/sleeve
    // branch from winning before the actual outward arm corridor is reached.
    if (!candidate || candidate.steep && outward - shoulderProgress < 0.16
      || progress < side * previous.x + 0.012
      || progress < shoulderProgress + 0.025) {
      misses += 1;
      outward += step;
      if (samples.length >= 3 && misses >= 3) break;
      continue;
    }
    misses = 0;
    previous = candidate;
    samples.push(candidate);
    outward += step;
  }
  if (samples.length >= 2) {
    samples[samples.length - 1] = distalArmCenter(points, side,
      samples[samples.length - 1], shoulder.y);
  }
  return {samples, length: pathLength(samples)};
}

function halfwayAlong(path) {
  if (path.length < 2) return path[0] || null;
  const target = pathLength(path) * 0.5;
  let travelled = 0;
  for (let index = 1; index < path.length; index += 1) {
    const segment = distance2(path[index], path[index - 1]);
    if (travelled + segment >= target && segment > EPSILON) {
      const factor = (target - travelled) / segment;
      return {x: path[index - 1].x + (path[index].x - path[index - 1].x) * factor,
        y: path[index - 1].y + (path[index].y - path[index - 1].y) * factor,
        z: path[index - 1].z + (path[index].z - path[index - 1].z) * factor};
    }
    travelled += segment;
  }
  return path[path.length - 1];
}

function fitArmPair(points, upper, options = {}) {
  const center = upper.center || {x: 0, y: upper.shoulderHeight, z: 0};
  const leftShoulder = pointAt({x: center.x - upper.shoulderHalfWidth,
    y: upper.shoulderHeight, z: center.z}, points);
  const rightShoulder = pointAt({x: center.x + upper.shoulderHalfWidth,
    y: upper.shoulderHeight, z: center.z}, points);
  const track = (side, shoulder) => {
    const preferred = trackArmFromShoulder(points, side, shoulder, options);
    if (preferred.samples.length >= 3) return preferred;
    return trackArmFromShoulder(points, side, shoulder, {...options, allowSteep: true});
  };
  // First search the shallow A-pose corridor across the complete outward
  // range. Only fall back to the broad vertical corridor when that search
  // cannot form a limb, so a nearby downward branch cannot preempt a distant
  // coherent sleeve/arm.
  const leftTrack = track(-1, leftShoulder);
  const rightTrack = track(1, rightShoulder);
  const validLeft = leftTrack.samples.length >= 3;
  const validRight = rightTrack.samples.length >= 3;
  const symmetryResidual = validLeft && validRight
    ? clamp(0.65 * Math.abs(
      Math.abs(leftTrack.samples[leftTrack.samples.length - 1].x - leftShoulder.x)
      - Math.abs(rightTrack.samples[rightTrack.samples.length - 1].x - rightShoulder.x))
      + 0.35 * Math.abs(leftTrack.samples[leftTrack.samples.length - 1].y
        - rightTrack.samples[rightTrack.samples.length - 1].y), 0, 1)
    : 0.5;
  return {leftTrack, rightTrack, leftShoulder, rightShoulder,
    leftElbow: validLeft ? halfwayAlong(leftTrack.samples) : null,
    rightElbow: validRight ? halfwayAlong(rightTrack.samples) : null,
    leftHand: validLeft ? leftTrack.samples[leftTrack.samples.length - 1] : null,
    rightHand: validRight ? rightTrack.samples[rightTrack.samples.length - 1] : null,
    leftArmLength: leftTrack.length, rightArmLength: rightTrack.length,
    symmetryResidual,
    validLeft, validRight};
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
  const profile = buildHeightCrossSections(accepted, options);
  const centerline = buildCenterline(accepted, options);
  const upper = fitHeadNeckShoulders(accepted, profile, centerline, options);
  const lower = fitPelvisAndHips(accepted, centerline, options);
  const arms = fitArmPair(accepted, upper, options);
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
    centerline, template, acceptedPointCount: accepted.length,
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
  const templateDiagnostic = best ? diagnosticsOverlay({...best, template}, bounds, frame,
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
    confidenceByRegion, confidenceDetails: regionDetails,
    fallbackControls: CONTROL_KEYS.filter(key => !controls[key].fitted),
    failureReasons, templateParameters: template, templateDiagnostic,
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
