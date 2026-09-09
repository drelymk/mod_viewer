// Geometry-only humanoid control rig fitting.
//
// This module deliberately knows nothing about ModelJoint, skinning, IK, or
// Physics. It consumes rest-shape mesh positions and returns a viewer-owned
// semantic scaffold that can be displayed without mutating the authored Rig.

const EPSILON = 1e-8;
const DEFAULT_MAX_POINT_COUNT = 160000;
const DEFAULT_SLAB_WIDTHS = Object.freeze([0.045, 0.065, 0.085]);
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

function finiteNumber(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function vector3(value, fallback = [0, 0, 0]) {
  if (value?.isVector3) {
    return [finiteNumber(value.x), finiteNumber(value.y), finiteNumber(value.z)];
  }
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    return [0, 1, 2].map(index => finiteNumber(value[index], fallback[index]));
  }
  if (value && typeof value === 'object') {
    return [finiteNumber(value.x, fallback[0]),
      finiteNumber(value.y, fallback[1]), finiteNumber(value.z, fallback[2])];
  }
  return [...fallback];
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
  return [
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0],
  ];
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
  // Gram-Schmidt keeps caller-provided orientation stable even when a model
  // orientation contains a small amount of accumulated numerical drift.
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
  return [
    (e[0] * x + e[4] * y + e[8] * z + e[12]) / divisor,
    (e[1] * x + e[5] * y + e[9] * z + e[13]) / divisor,
    (e[2] * x + e[6] * y + e[10] * z + e[14]) / divisor,
  ];
}

function positionsForMesh(mesh) {
  if (mesh?.userData?.assetFill === true) return null;
  const rest = mesh?.userData?.humanoidRestPositions
    || mesh?.userData?.basePositions
    || mesh?.geometry?.attributes?.position?.array;
  if (!rest || rest.length < 3) return null;
  // Mesh objects are direct scene children and their matrix contains the
  // viewer's model transform. Apply only an explicit asset-local matrix, so
  // the fitted controls remain model-local when the user turns the model.
  const matrix = mesh?.userData?.humanoidRestMatrix?.isMatrix4
    ? mesh.userData.humanoidRestMatrix : null;
  const points = [];
  for (let index = 0; index + 2 < rest.length; index += 3) {
    const point = [Number(rest[index]), Number(rest[index + 1]), Number(rest[index + 2])];
    if (!point.every(Number.isFinite)) continue;
    points.push(transformedPoint(point, matrix));
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
  for (let index = 0; index < maximum; index += 1) {
    result.push(points[Math.floor(index * step)]);
  }
  return result;
}

function boundsFor(points) {
  const sides = points.map(item => item.side);
  const heights = points.map(item => item.height);
  const depths = points.map(item => item.depth);
  const lowHeight = quantile(heights, 0.01);
  const highHeight = quantile(heights, 0.99);
  const height = Math.max(highHeight - lowHeight, EPSILON);
  const sideCenter = median(sides);
  return {
    lowHeight,
    highHeight,
    height,
    sideCenter,
    minSide: quantile(sides, 0.005),
    maxSide: quantile(sides, 0.995),
    minDepth: quantile(depths, 0.01),
    maxDepth: quantile(depths, 0.99),
  };
}

function normalizedPoint(item, bounds) {
  return {
    ...item,
    x: (item.side - bounds.sideCenter) / bounds.height,
    y: (item.height - bounds.lowHeight) / bounds.height,
    z: item.depth,
  };
}

function voxelize(points, bounds, voxelSize) {
  const buckets = new Map();
  const size = Math.max(voxelSize, bounds.height * 0.002);
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
  return [...buckets.values()].map(bucket => ({
    ...bucket,
    x: bucket.x / bucket.count,
    y: bucket.y / bucket.count,
    z: bucket.z / bucket.count,
  }));
}

function bodyDepthMode(points, voxelSize) {
  const bins = new Map();
  const step = Math.max(voxelSize, 1e-4);
  points.filter(item => Math.abs(item.x) < 0.35 && item.y > 0.16 && item.y < 0.9)
    .forEach(item => {
      const bin = Math.floor(item.z / step);
      bins.set(bin, (bins.get(bin) || 0) + 1);
    });
  if (!bins.size) return {depth: median(points.map(item => item.z)), spread: 0};
  const mode = [...bins.entries()].sort((left, right) => right[1] - left[1]
    || Math.abs(left[0]) - Math.abs(right[0]) || left[0] - right[0])[0][0];
  const near = points.filter(item => Math.floor(item.z / step) === mode)
    .map(item => item.z);
  const depth = median(near);
  const spread = quantile(near.map(value => Math.abs(value - depth)), 0.9);
  return {depth, spread};
}

function gridFor(points, bounds, bodyDepth, slabWidth, options) {
  const cellSize = bounds.height * finiteNumber(options?.gridCellHeight, 1 / 128);
  const minX = Math.min(bounds.minSide / bounds.height - bounds.sideCenter / bounds.height,
    ...points.map(item => item.x), -0.9);
  const maxX = Math.max(bounds.maxSide / bounds.height - bounds.sideCenter / bounds.height,
    ...points.map(item => item.x), 0.9);
  const minY = 0;
  const maxY = 1;
  const width = clamp(Math.ceil((maxX - minX) / cellSize) + 5, 32, 480);
  const height = clamp(Math.ceil((maxY - minY) / cellSize) + 5, 48, 180);
  const occupancy = new Uint8Array(width * height);
  const accepted = points.filter(item => Math.abs(item.z - bodyDepth) <= slabWidth);
  const cells = new Map();
  accepted.forEach(item => {
    const ix = clamp(Math.floor((item.x - minX) / cellSize), 0, width - 1);
    const iy = clamp(Math.floor((item.y - minY) / cellSize), 0, height - 1);
    const index = iy * width + ix;
    occupancy[index] = 1;
    const previous = cells.get(index);
    if (!previous) cells.set(index, {x: item.x, y: item.y, z: item.z, count: 1});
    else {
      previous.count += 1;
      previous.x += item.x;
      previous.y += item.y;
      previous.z += item.z;
    }
  });
  cells.forEach(cell => {
    cell.x /= cell.count;
    cell.y /= cell.count;
    cell.z /= cell.count;
  });
  return {width, height, minX, minY, cellSize, occupancy, cells, accepted};
}

function neighbors(index, width, height, includeSelf = false) {
  const x = index % width;
  const y = Math.floor(index / width);
  const result = [];
  for (let dy = -1; dy <= 1; dy += 1) {
    for (let dx = -1; dx <= 1; dx += 1) {
      if (!includeSelf && dx === 0 && dy === 0) continue;
      const nx = x + dx;
      const ny = y + dy;
      if (nx >= 0 && nx < width && ny >= 0 && ny < height) {
        result.push(ny * width + nx);
      }
    }
  }
  return result;
}

function dilate(input, width, height) {
  const output = new Uint8Array(input.length);
  input.forEach((value, index) => {
    if (!value) return;
    output[index] = 1;
    neighbors(index, width, height).forEach(neighbor => { output[neighbor] = 1; });
  });
  return output;
}

function connectedComponents(input, width, height) {
  const visited = new Uint8Array(input.length);
  const components = [];
  input.forEach((value, start) => {
    if (!value || visited[start]) return;
    const queue = [start];
    visited[start] = 1;
    const pixels = [];
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const index = queue[cursor];
      pixels.push(index);
      neighbors(index, width, height).forEach(neighbor => {
        if (input[neighbor] && !visited[neighbor]) {
          visited[neighbor] = 1;
          queue.push(neighbor);
        }
      });
    }
    components.push(pixels);
  });
  return components;
}

function primarySilhouette(grid) {
  const connected = connectedComponents(grid.occupancy, grid.width, grid.height);
  if (!connected.length) return new Uint8Array(grid.occupancy.length);
  const scored = connected.map(pixels => {
    let central = 0;
    let low = grid.height;
    let high = 0;
    pixels.forEach(index => {
      const x = index % grid.width;
      const y = Math.floor(index / grid.width);
      const semanticX = grid.minX + (x + 0.5) * grid.cellSize;
      const semanticY = grid.minY + (y + 0.5) * grid.cellSize;
      if (Math.abs(semanticX) < 0.22 && semanticY > 0.2 && semanticY < 0.9) {
        central += 1;
      }
      low = Math.min(low, y);
      high = Math.max(high, y);
    });
    return {pixels, score: central * 1000 + pixels.length + (high - low) * 2};
  }).sort((left, right) => right.score - left.score)[0];
  const output = new Uint8Array(grid.occupancy.length);
  scored.pixels.forEach(index => { output[index] = 1; });
  return output;
}

function transitions(index, image, width, height) {
  const x = index % width;
  const y = Math.floor(index / width);
  if (x === 0 || y === 0 || x === width - 1 || y === height - 1) return 0;
  // p2..p9 in the clockwise order required by Zhang-Suen.
  const adjacent = [
    (y - 1) * width + x, (y - 1) * width + x + 1,
    y * width + x + 1, (y + 1) * width + x + 1,
    (y + 1) * width + x, (y + 1) * width + x - 1,
    y * width + x - 1, (y - 1) * width + x - 1,
  ];
  let changes = 0;
  for (let i = 0; i < adjacent.length; i += 1) {
    const current = image[adjacent[i]] ? 1 : 0;
    const next = image[adjacent[(i + 1) % adjacent.length]] ? 1 : 0;
    if (!current && next) changes += 1;
  }
  return changes;
}

// Zhang-Suen thinning is deterministic and keeps the skeleton independent of
// mesh tessellation after voxelization.
function thinSkeleton(input, width, height) {
  const image = new Uint8Array(input);
  let changed = true;
  let guard = 0;
  while (changed && guard < 96) {
    changed = false;
    guard += 1;
    for (const phase of [0, 1]) {
      const remove = [];
      image.forEach((value, index) => {
        if (!value) return;
        const x = index % width;
        const y = Math.floor(index / width);
        if (x === 0 || y === 0 || x === width - 1 || y === height - 1) return;
        const north = image[(y - 1) * width + x];
        const east = image[y * width + x + 1];
        const south = image[(y + 1) * width + x];
        const west = image[y * width + x - 1];
        const northEast = image[(y - 1) * width + x + 1];
        const southEast = image[(y + 1) * width + x + 1];
        const southWest = image[(y + 1) * width + x - 1];
        const northWest = image[(y - 1) * width + x - 1];
        const count = north + northEast + east + southEast
          + south + southWest + west + northWest;
        if (count < 2 || count > 6 || transitions(index, image, width, height) !== 1) return;
        const first = north * east * south;
        const second = east * south * west;
        if (phase === 0 ? first !== 0 || second !== 0
          : north * east * west !== 0 || north * south * west !== 0) return;
        remove.push(index);
      });
      if (remove.length) changed = true;
      remove.forEach(index => { image[index] = 0; });
    }
  }
  return image;
}

function skeletonGraph(skeleton, width, height) {
  const pixels = [...skeleton].flatMap((value, index) => value ? [index] : []);
  const nodeSet = new Set(pixels.filter(index =>
    neighbors(index, width, height).filter(neighbor => skeleton[neighbor]).length !== 2));
  if (pixels.length && !nodeSet.size) nodeSet.add(pixels[0]);
  const nodes = [...nodeSet].map(index => ({index, x: index % width, y: Math.floor(index / width)}));
  const edges = [];
  const used = new Set();
  const edgeKey = (left, right) => left < right ? `${left}:${right}` : `${right}:${left}`;
  nodes.forEach(node => {
    neighbors(node.index, width, height).filter(neighbor => skeleton[neighbor])
      .forEach(next => {
        const firstKey = edgeKey(node.index, next);
        if (used.has(firstKey)) return;
        const path = [node.index];
        let previous = node.index;
        let current = next;
        used.add(firstKey);
        let guard = 0;
        while (!nodeSet.has(current) && guard < pixels.length + 1) {
          path.push(current);
          const choices = neighbors(current, width, height)
            .filter(candidate => skeleton[candidate] && candidate !== previous);
          if (!choices.length) break;
          previous = current;
          current = choices[0];
          used.add(edgeKey(previous, current));
          guard += 1;
        }
        path.push(current);
        const first = nodes.findIndex(item => item.index === node.index);
        const second = nodes.findIndex(item => item.index === current);
        if (second >= 0) edges.push({a: first, b: second, pixels: path,
          length: Math.max(0, path.length - 1)});
      });
  });
  return {pixels, nodes, edges};
}

function gridPoint(grid, index) {
  const x = index % grid.width;
  const y = Math.floor(index / grid.width);
  return {
    x: grid.minX + (x + 0.5) * grid.cellSize,
    y: grid.minY + (y + 0.5) * grid.cellSize,
  };
}

function rowValues(image, grid, y, side, threshold = 0) {
  const values = [];
  for (let x = 0; x < grid.width; x += 1) {
    const index = y * grid.width + x;
    if (!image[index]) continue;
    const point = gridPoint(grid, index);
    if (point.x * side >= threshold) values.push(point);
  }
  return values;
}

function pathFromRows(image, grid, role, torsoHalfWidth) {
  const leg = role.endsWith('leg');
  const side = role.startsWith('left') ? -1 : 1;
  const branchThreshold = leg
    ? Math.max(0.055, torsoHalfWidth * 0.72)
    : Math.max(0.11, torsoHalfWidth * 1.05);
  const firstRow = leg ? Math.floor(grid.height * 0.05) : Math.floor(grid.height * 0.52);
  const lastRow = leg ? Math.floor(grid.height * 0.68) : Math.floor(grid.height * 0.94);
  const step = Math.max(1, Math.floor(grid.height / 64));
  const rows = [];
  for (let y = firstRow; y <= lastRow; y += step) {
    const values = rowValues(image, grid, y, side, branchThreshold);
    if (!values.length) continue;
    const selected = leg
      ? values.filter(point => point.y < 0.48)
      : values.filter(point => point.y > 0.48);
    if (!selected.length) continue;
    // The outer medial branch is the strongest silhouette evidence for a
    // distal control. Keeping the median of the outer half resists fingers,
    // shoes, and a few noisy surface voxels.
    selected.sort((left, right) => side * right.x - side * left.x);
    const outer = selected.slice(0, Math.max(1, Math.ceil(selected.length * 0.5)));
    rows.push({x: median(outer.map(point => point.x)), y: median(outer.map(point => point.y))});
  }
  if (rows.length < 2) return [];
  rows.sort((left, right) => leg ? right.y - left.y :
    Math.abs(left.x) - Math.abs(right.x));
  const deduped = [];
  rows.forEach(row => {
    if (!deduped.length || distance([row.x, row.y, 0],
      [deduped[deduped.length - 1].x, deduped[deduped.length - 1].y, 0]) > 0.025) {
      deduped.push(row);
    }
  });
  return deduped;
}

function torsoPath(image, grid) {
  const rows = [];
  const first = Math.floor(grid.height * 0.18);
  const last = Math.floor(grid.height * 0.9);
  const step = Math.max(1, Math.floor(grid.height / 64));
  for (let y = first; y <= last; y += step) {
    const values = rowValues(image, grid, y, 1, -0.24)
      .filter(point => point.x <= 0.24);
    const central = values.filter(point => Math.abs(point.x) < 0.28);
    if (!central.length) continue;
    rows.push({x: median(central.map(point => point.x)),
      y: median(central.map(point => point.y))});
  }
  rows.sort((left, right) => left.y - right.y);
  return rows;
}

function arcLengthPath(path) {
  let result = 0;
  for (let index = 1; index < path.length; index += 1) {
    result += Math.hypot(path[index].x - path[index - 1].x,
      path[index].y - path[index - 1].y);
  }
  return result;
}

function halfwayAlong(path) {
  if (path.length < 2) return path[0] || null;
  const target = arcLengthPath(path) * 0.5;
  let distanceSoFar = 0;
  for (let index = 1; index < path.length; index += 1) {
    const segment = Math.hypot(path[index].x - path[index - 1].x,
      path[index].y - path[index - 1].y);
    if (distanceSoFar + segment >= target && segment > EPSILON) {
      const factor = (target - distanceSoFar) / segment;
      return {
        x: path[index - 1].x + (path[index].x - path[index - 1].x) * factor,
        y: path[index - 1].y + (path[index].y - path[index - 1].y) * factor,
      };
    }
    distanceSoFar += segment;
  }
  return path[path.length - 1];
}

function nearestDepth(point, accepted, fallback) {
  const candidates = accepted.map(item => ({
    item,
    distance: Math.hypot(item.x - point.x, item.y - point.y),
  })).filter(item => item.distance <= 0.085)
    .sort((left, right) => left.distance - right.distance)
    .slice(0, 24);
  if (!candidates.length) return fallback;
  return median(candidates.map(item => item.item.z));
}

function reconstruct(point, grid, accepted, bounds, frame, bodyDepth) {
  const depth = nearestDepth(point, accepted, bodyDepth);
  const side = bounds.sideCenter + point.x * bounds.height;
  const height = bounds.lowHeight + point.y * bounds.height;
  return add(add(scale(frame.right, side), scale(frame.up, height)),
    scale(frame.forward, depth));
}

function fallbackPoint(key) {
  const values = {
    chest: [0, 0.82], pelvis: [0, 0.55],
    leftShoulder: [-0.18, 0.78], leftElbow: [-0.42, 0.71], leftHand: [-0.68, 0.64],
    rightShoulder: [0.18, 0.78], rightElbow: [0.42, 0.71], rightHand: [0.68, 0.64],
    leftHip: [-0.15, 0.55], leftKnee: [-0.17, 0.30], leftFoot: [-0.18, 0.03],
    rightHip: [0.15, 0.55], rightKnee: [0.17, 0.30], rightFoot: [0.18, 0.03],
  };
  const value = values[key] || [0, 0.5];
  return {x: value[0], y: value[1]};
}

function projectPath(path, grid, accepted, bounds, frame, bodyDepth) {
  return path.map(point => reconstruct(point, grid, accepted, bounds, frame, bodyDepth));
}

function fitSlab(points, bounds, frame, bodyDepth, slabWidth, options) {
  const grid = gridFor(points, bounds, bodyDepth, slabWidth, options);
  // A small close bridges gaps left by sparse strip/triangle exports while
  // remaining small relative to the height-normalized silhouette.
  let morphed = grid.occupancy;
  for (let iteration = 0; iteration < 4; iteration += 1) {
    morphed = dilate(morphed, grid.width, grid.height);
  }
  grid.occupancy = primarySilhouette({...grid, occupancy: morphed});
  const skeleton = thinSkeleton(grid.occupancy, grid.width, grid.height);
  const graph = skeletonGraph(skeleton, grid.width, grid.height);
  const torso = torsoPath(grid.occupancy, grid);
  const torsoHalfWidth = torso.length
    ? quantile(torso.map(point => Math.abs(point.x)), 0.8) : 0.15;
  const paths = {
    leftArm: pathFromRows(grid.occupancy, grid, 'left_arm', torsoHalfWidth),
    rightArm: pathFromRows(grid.occupancy, grid, 'right_arm', torsoHalfWidth),
    leftLeg: pathFromRows(grid.occupancy, grid, 'left_leg', torsoHalfWidth),
    rightLeg: pathFromRows(grid.occupancy, grid, 'right_leg', torsoHalfWidth),
  };
  const semantic = {
    chest: torso.length ? torso[Math.floor(torso.length * 0.82)] : fallbackPoint('chest'),
    pelvis: torso.length ? torso[Math.floor(torso.length * 0.42)] : fallbackPoint('pelvis'),
  };
  Object.entries(ROLE_CONTROL_KEYS).forEach(([role, keys]) => {
    const path = paths[role.replace('_', '')[0] === 'l'
      ? role.includes('arm') ? 'leftArm' : 'leftLeg'
      : role.includes('arm') ? 'rightArm' : 'rightLeg'];
    if (path.length >= 2) {
      const side = role.startsWith('left') ? -1 : 1;
      const isArm = role.endsWith('arm');
      const proximal = isArm
        ? path.reduce((best, point) => point.y > best.y ? point : best, path[0])
        : path.reduce((best, point) =>
          Math.abs(point.y - 0.34) < Math.abs(best.y - 0.34) ? point : best,
        path[0]);
      const distal = isArm
        ? path.reduce((best, point) => side * point.x > side * best.x ? point : best,
          path[0])
        : path.reduce((best, point) => point.y < best.y ? point : best, path[0]);
      const controlPath = isArm ? [proximal, distal]
        : [proximal, ...path.filter(point => point.y < proximal.y), distal];
      semantic[keys[0]] = proximal;
      semantic[keys[1]] = halfwayAlong(controlPath);
      semantic[keys[2]] = distal;
    } else keys.forEach(key => { semantic[key] = fallbackPoint(key); });
  });
  CONTROL_KEYS.forEach(key => {
    if (!semantic[key]) semantic[key] = fallbackPoint(key);
  });
  const controls = {};
  CONTROL_KEYS.forEach(key => {
    controls[key] = reconstruct(semantic[key], grid, grid.accepted,
      bounds, frame, bodyDepth);
  });
  const serializedPaths = {
    torso: projectPath(torso, grid, grid.accepted, bounds, frame, bodyDepth),
    leftArm: projectPath(paths.leftArm, grid, grid.accepted, bounds, frame, bodyDepth),
    rightArm: projectPath(paths.rightArm, grid, grid.accepted, bounds, frame, bodyDepth),
    leftLeg: projectPath(paths.leftLeg, grid, grid.accepted, bounds, frame, bodyDepth),
    rightLeg: projectPath(paths.rightLeg, grid, grid.accepted, bounds, frame, bodyDepth),
  };
  const failures = [];
  Object.entries(ROLE_CONTROL_KEYS).forEach(([role, keys]) => {
    if (paths[role.includes('arm')
      ? role.startsWith('left') ? 'leftArm' : 'rightArm'
      : role.startsWith('left') ? 'leftLeg' : 'rightLeg'].length < 2) {
      failures.push(`${role}:path_not_found`);
    }
  });
  const foundKeys = new Set();
  if (torso.length >= 2) {
    foundKeys.add('chest');
    foundKeys.add('pelvis');
  }
  Object.entries(ROLE_CONTROL_KEYS).forEach(([role, keys]) => {
    const path = paths[role.includes('arm')
      ? role.startsWith('left') ? 'leftArm' : 'rightArm'
      : role.startsWith('left') ? 'leftLeg' : 'rightLeg'];
    if (path.length >= 2) keys.forEach(key => foundKeys.add(key));
  });
  return {
    controls,
    semantic,
    paths: serializedPaths,
    foundCount: foundKeys.size,
    failures,
    grid,
    skeleton,
    graph,
    silhouettePixelCount: grid.occupancy.reduce((sum, value) => sum + value, 0),
  };
}

function bilateralSpread(left, right) {
  if (!left || !right) return 1;
  return Math.abs(left[0] + right[0]);
}

function copyPoint(point) {
  return vector3(point);
}

function consensusControls(fits, bounds) {
  const controls = {};
  const spread = {};
  CONTROL_KEYS.forEach(key => {
    const values = fits.map(fit => fit.controls[key]).filter(Boolean);
    if (!values.length) return;
    const result = [0, 1, 2].map(index => median(values.map(value => value[index])));
    controls[key] = result;
    spread[key] = Math.max(...values.map(value => distance(value, result)))
      / Math.max(bounds.height, EPSILON);
  });
  return {controls, spread};
}

function consensusSemantic(fits) {
  const semantic = {};
  CONTROL_KEYS.forEach(key => {
    const values = fits.map(fit => fit.semantic[key]).filter(Boolean);
    if (!values.length) return;
    semantic[key] = {
      x: median(values.map(value => value.x)),
      y: clamp(median(values.map(value => value.y)), 0, 1),
    };
  });
  return semantic;
}

function confidenceFor(fits, spread, failures) {
  const spreadValues = Object.values(spread);
  const maxSpread = spreadValues.length ? Math.max(...spreadValues) : 1;
  const missing = failures.length;
  const bilateral = [
    ['leftShoulder', 'rightShoulder'], ['leftHand', 'rightHand'],
    ['leftFoot', 'rightFoot'], ['leftHip', 'rightHip'],
  ].map(([leftKey, rightKey]) => {
    const left = fits[0]?.semantic?.[leftKey];
    const right = fits[0]?.semantic?.[rightKey];
    return bilateralSpread(left && [left.x, left.y], right && [right.x, right.y]);
  });
  const bilateralError = Math.max(...bilateral, 0);
  if (missing || maxSpread > 0.12 || bilateralError > 0.12) return 'low';
  if (maxSpread > 0.055 || bilateralError > 0.07) return 'medium';
  return 'high';
}

function emptyRig(frame, diagnostics = {}) {
  const controls = Object.fromEntries(CONTROL_KEYS.map(key => [key, {
    position: [0, 0, 0],
    semantic: {sideN: fallbackPoint(key).x, height01: fallbackPoint(key).y, depthN: 0},
    confidence: 'low', source: 'fallback',
  }]));
  return {
    version: 1,
    source: 'geometry',
    accepted: false,
    confidence: 'low',
    frame: {...frame, lowHeight: 0, highHeight: 0, height: 0},
    controls,
    paths: {torso: [], leftArm: [], rightArm: [], leftLeg: [], rightLeg: []},
    diagnostics: {...diagnostics, failureReasons: ['no_rest_geometry']},
  };
}

/**
 * Fit a semantic, viewer-owned control rig from registered mesh rest geometry.
 * Returned positions are model-local and therefore remain fixed while the
 * model is posed or the viewer transform changes.
 */
export function buildHumanoidControlRig({meshes = [], axes, options = {}} = {}) {
  const started = typeof performance !== 'undefined' && performance.now
    ? performance.now() : Date.now();
  const frame = coordinateFrame(axes);
  const projected = semanticPoints(meshes, frame);
  if (!projected.length) return emptyRig({
    up: frame.up, right: frame.right, forward: frame.forward,
  }, {pointCount: 0, voxelCount: 0, slabFits: [], fitRuntimeMs: 0});
  const workingPoints = downsamplePoints(projected,
    Math.max(1000, Math.floor(finiteNumber(
      options.maxPointCount, DEFAULT_MAX_POINT_COUNT))));
  const bounds = boundsFor(workingPoints);
  const normalized = workingPoints.map(item => normalizedPoint(item, bounds));
  const voxelSize = bounds.height * finiteNumber(options.voxelSize, 0.012);
  const voxels = voxelize(normalized, bounds, voxelSize);
  const depthMode = bodyDepthMode(voxels,
    bounds.height * finiteNumber(options.depthBinSize, 0.012));
  const slabWidths = options.slabWidths || DEFAULT_SLAB_WIDTHS;
  const fits = slabWidths.map(width => fitSlab(voxels, bounds, frame,
    depthMode.depth, width * bounds.height, options));
  const best = [...fits].sort((left, right) => right.foundCount - left.foundCount
    || right.silhouettePixelCount - left.silhouettePixelCount)[0];
  const consensus = consensusControls(fits, bounds);
  const semanticConsensus = consensusSemantic(fits);
  const failures = [...new Set(fits.flatMap(fit => fit.failures))];
  const confidence = confidenceFor(fits, consensus.spread, failures);
  const controls = Object.fromEntries(CONTROL_KEYS.map(key => [key, {
    position: copyPoint(consensus.controls[key] || best.controls[key]),
    semantic: {
      sideN: semanticConsensus[key]?.x || best.semantic[key]?.x || 0,
      height01: clamp(semanticConsensus[key]?.y || best.semantic[key]?.y || 0, 0, 1),
      depthN: ((dot(consensus.controls[key] || best.controls[key], frame.forward)
        - depthMode.depth) / Math.max(bounds.height, EPSILON)),
    },
    confidence: consensus.spread[key] > 0.055 ? 'medium' : confidence,
    source: 'geometry',
  }]));
  const diagnostics = {
    pointCount: projected.length,
    sampledPointCount: workingPoints.length,
    voxelCount: voxels.length,
    bodyDepth: depthMode.depth,
    bodyDepthSpread: depthMode.spread,
    slabWidths: slabWidths.map(width => width * bounds.height),
    slabFits: fits.map((fit, index) => ({
      width: slabWidths[index],
      silhouettePixelCount: fit.silhouettePixelCount,
      skeletonPixelCount: fit.graph.pixels.length,
      skeletonNodeCount: fit.graph.nodes.length,
      skeletonEdgeCount: fit.graph.edges.length,
      controlsFound: fit.foundCount,
      failureReasons: fit.failures,
    })),
    silhouettePixelCount: best.silhouettePixelCount,
    skeletonNodeCount: best.graph.nodes.length,
    skeletonEdgeCount: best.graph.edges.length,
    controlSpreadByRole: consensus.spread,
    bilateralSpread: {
      arms: Math.abs(controls.leftHand.position[0] + controls.rightHand.position[0])
        / Math.max(bounds.height, EPSILON),
      legs: Math.abs(controls.leftFoot.position[0] + controls.rightFoot.position[0])
        / Math.max(bounds.height, EPSILON),
    },
    failureReasons: failures,
    fitRuntimeMs: Math.max(0, (typeof performance !== 'undefined' && performance.now
      ? performance.now() : Date.now()) - started),
  };
  const result = {
    version: 1,
    source: 'geometry',
    accepted: confidence !== 'low',
    confidence,
    frame: {
      up: frame.up, right: frame.right, forward: frame.forward,
      lowHeight: bounds.lowHeight, highHeight: bounds.highHeight,
      height: bounds.height, sideCenter: bounds.sideCenter,
      bodyDepth: depthMode.depth,
    },
    controls,
    paths: {
      torso: best.paths.torso,
      leftArm: best.paths.leftArm,
      rightArm: best.paths.rightArm,
      leftLeg: best.paths.leftLeg,
      rightLeg: best.paths.rightLeg,
    },
    diagnostics,
  };
  return serializeHumanoidControlRig(result);
}

/** Return a JSON-safe snapshot suitable for events, panels, and overlays. */
export function serializeHumanoidControlRig(rig) {
  if (!rig) return null;
  const result = JSON.parse(JSON.stringify(rig));
  result.controls = Object.fromEntries(CONTROL_KEYS.map(key => {
    const control = result.controls?.[key] || {};
    return [key, {
      position: vector3(control.position),
      semantic: {...(control.semantic || {})},
      confidence: control.confidence || result.confidence || 'low',
      source: control.source || 'geometry',
    }];
  }));
  return result;
}

export const HUMANOID_CONTROL_KEYS = CONTROL_KEYS;
