// Experimental, read-only anatomical fitting. Surface evidence and the existing
// proportions share one fit; this module never reads skinning or pose state.
import * as THREE from 'three';
import {
  buildHumanoidControlRig,
  HUMANOID_CONTROL_KEYS,
  humanoidControlPositionToSemantic,
  rebuildHumanoidControlPaths,
} from './humanoid-control-rig.js';
import { characterAxesFromOrientation } from './humanoid-orientation.js';
import { semanticAxesFrame } from './humanoid-proportional-template.js';

const MAX_TRIANGLES = 90000;
const MAX_SAMPLES = 16000;
const clock = () => performance.now();
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const distance = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]));
const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
const dot = (a, b) => a.reduce((sum, v, i) => sum + v * b[i], 0);
const quantile = (values, t) => {
  if (!values.length) return 0;
  const sorted = values.slice().sort((a, b) => a - b);
  return sorted[Math.floor((sorted.length - 1) * t)];
};
const median = (values) => quantile(values, 0.5);
const yieldFrame = () => new Promise((resolve) => setTimeout(resolve, 0));

export function isHumanoidMeshDisplayed(mesh) {
  for (let item = mesh; item; item = item.parent) if (item.visible === false) return false;
  return true;
}

/** Deterministic area sampling of only displayed triangles in model space. */
export async function sampleHumanoidRestSurface({ meshes = [], axes, options = {}, isCurrent = () => true } = {}) {
  const maxTriangles = clamp(Math.floor(options.maxTriangles || MAX_TRIANGLES), 1, MAX_TRIANGLES);
  const maxSamples = clamp(Math.floor(options.maxSamples || MAX_SAMPLES), 1, MAX_SAMPLES);
  const active = [...meshes].filter(isHumanoidMeshDisplayed);
  active[0]?.updateWorldMatrix?.(true, false);
  const inverseModel = active[0]?.matrixWorld?.clone().invert() || new THREE.Matrix4();
  const sources = [];
  const semanticFrame = semanticAxesFrame(axes);
  const semanticKey = (v) =>
    [semanticFrame.right, semanticFrame.up, semanticFrame.forward].map((axis) => Math.round(dot(v, axis) * 1e5));
  const identities = new Map();
  const surfaceSignatures = new Map();
  let displayedTriangleCount = 0;
  for (const mesh of active) {
    const geometry = mesh.geometry;
    const rest = mesh.userData?.humanoidRestPositions || mesh.userData?.basePositions;
    // A live position attribute may be animated or posed. Never sample it.
    if (!rest || rest.length < 9 || !geometry) continue;
    mesh.updateWorldMatrix?.(true, false);
    const matrix =
      mesh.userData?.humanoidRestMatrix?.clone() ||
      new THREE.Matrix4().multiplyMatrices(inverseModel, mesh.matrixWorld || new THREE.Matrix4());
    const indices = geometry.index?.array;
    const count = indices?.length ?? Math.floor(rest.length / 3);
    const start = Math.max(0, Math.floor(geometry.drawRange?.start || 0));
    const end = Math.min(count, start + (geometry.drawRange?.count ?? Infinity));
    const triangles = Math.floor((end - start) / 3);
    if (triangles <= 0) continue;
    displayedTriangleCount += triangles;
    const signature = `${start}:${end}:${matrix.elements.join(',')}`;
    let entries = identities.get(rest);
    if (!entries) identities.set(rest, (entries = []));
    if (entries.some((entry) => entry.indices === indices && entry.signature === signature)) continue;
    entries.push({ indices, signature });
    const source = { rest, indices, start, triangles, matrix };
    const fingerprint = (array, first, size) => {
      let hash = 2166136261;
      for (let i = 0; i < Math.min(size, 256); i += 1) {
        const value = array[first + Math.floor((i * size) / Math.min(size, 256))];
        hash = Math.imul(hash ^ Math.round(value * 1e5), 16777619);
      }
      return hash;
    };
    const contentKey = `${signature}:${rest.length}:${fingerprint(rest, 0, rest.length)}:${indices ? fingerprint(indices, start, triangles * 3) : 'unindexed'}`;
    let matches = surfaceSignatures.get(contentKey);
    if (!matches) surfaceSignatures.set(contentKey, (matches = []));
    let duplicate = false;
    for (const match of matches) {
      // Fingerprints only shortlist candidates; exact comparison prevents hash
      // collisions from discarding distinct visible geometry.
      let equal = true;
      for (let i = 0; i < rest.length && equal; i += 1) {
        equal = rest[i] === match.rest[i];
        if (i % 16384 === 0) {
          await yieldFrame();
          if (!isCurrent()) return null;
        }
      }
      for (let i = start; indices && i < end && equal; i += 1) equal = indices[i] === match.indices?.[i];
      if (equal) {
        duplicate = true;
        break;
      }
    }
    if (duplicate) continue;
    matches.push(source);
    sources.push(source);
  }
  const total = sources.reduce((sum, source) => sum + source.triangles, 0);
  const triangles = new Map();
  const point = new THREE.Vector3();
  const cross = new THREE.Vector3();
  const edge = new THREE.Vector3();
  let visited = 0;
  let offset = 0;
  // Stratify the bounded scan globally; small meshes are not allowed to spend
  // the entire budget before a dense body mesh is reached.
  const scanCount = Math.min(total, maxTriangles);
  let scanIndex = 0;
  for (const source of sources) {
    while (scanIndex < scanCount) {
      const ordinal = Math.floor(((scanIndex + 0.5) * total) / scanCount);
      if (ordinal >= offset + source.triangles) break;
      scanIndex += 1;
      const index = source.start + (ordinal - offset) * 3;
      const vertices = [0, 1, 2].map((corner) => {
        const vertex = source.indices ? source.indices[index + corner] : index + corner;
        return point
          .fromArray(source.rest, vertex * 3)
          .applyMatrix4(source.matrix)
          .toArray();
      });
      visited += 1;
      if (vertices.flat().every(Number.isFinite)) {
        // Canonical corners remove duplicate draws, UV seams, and winding bias.
        vertices.sort((a, b) => {
          const first = semanticKey(a),
            second = semanticKey(b);
          return first[0] - second[0] || first[1] - second[1] || first[2] - second[2];
        });
        const key = vertices.map((v) => semanticKey(v).join(',')).join('/');
        const semanticVertices = vertices.map((v) => semanticKey(v).map((n) => n / 1e5));
        cross.fromArray(semanticVertices[1]).sub(point.fromArray(semanticVertices[0]));
        edge.fromArray(semanticVertices[2]).sub(point.fromArray(semanticVertices[0]));
        const area = cross.cross(edge).length() * 0.5;
        if (area > 1e-12 && !triangles.has(key)) triangles.set(key, { vertices: semanticVertices, area });
      }
      if (visited % 2048 === 0) {
        await yieldFrame();
        if (!isCurrent()) return null;
      }
    }
    offset += source.triangles;
  }
  const surface = [...triangles.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, value]) => value);
  const area = surface.reduce((sum, item) => sum + item.area, 0);
  const points = [];
  let triangleIndex = 0;
  let cumulativeArea = surface[0]?.area || 0;
  for (let i = 0; i < maxSamples && area > 0; i += 1) {
    const target = ((i + 0.5) * area) / maxSamples;
    while (triangleIndex < surface.length - 1 && cumulativeArea < target) {
      triangleIndex += 1;
      cumulativeArea += surface[triangleIndex].area;
    }
    const corners = surface[triangleIndex].vertices;
    const u = Math.sqrt(((i + 1) * 0.754877666) % 1);
    const v = ((i + 1) * 0.569840296) % 1;
    const semantic = corners[0].map(
      (n, axis) => n * (1 - u) + corners[1][axis] * u * (1 - v) + corners[2][axis] * u * v,
    );
    points.push(
      [0, 1, 2].map(
        (axis) =>
          semanticFrame.right[axis] * semantic[0] +
          semanticFrame.up[axis] * semantic[1] +
          semanticFrame.forward[axis] * semantic[2],
      ),
    );
  }
  return {
    points,
    diagnostics: {
      visibleMeshCount: active.length,
      sourceCount: sources.length,
      inputTriangleCount: total,
      displayedTriangleCount,
      visitedTriangleCount: visited,
      uniqueTriangleCount: surface.length,
      sampledPointCount: points.length,
      scanCapped: total > maxTriangles,
    },
  };
}

function section(points, prior, radius, axis = 1) {
  const local = points.filter(
    (p) =>
      Math.abs(p[axis] - prior[axis]) <= 0.012 && p.every((v, i) => i === axis || Math.abs(v - prior[i]) <= radius[i]),
  );
  if (local.length < 10) return null;
  const low = [0, 1, 2].map((i) =>
    quantile(
      local.map((p) => p[i]),
      0.12,
    ),
  );
  const high = [0, 1, 2].map((i) =>
    quantile(
      local.map((p) => p[i]),
      0.88,
    ),
  );
  const center = low.map((v, i) => (v + high[i]) * 0.5);
  center[axis] = prior[axis];
  return { center, width: high.map((v, i) => v - low[i]), support: local.length };
}

function normalizeSurface(points, axes) {
  const projected = points.map((p) => [dot(p, axes.right), dot(p, axes.up), dot(p, axes.forward)]);
  const bottom = quantile(
    projected.map((p) => p[1]),
    0.005,
  );
  const roughHeight =
    quantile(
      projected.map((p) => p[1]),
      0.995,
    ) - bottom;
  if (roughHeight <= 1e-8) return null;
  const lower = projected.filter((p) => p[1] < bottom + roughHeight * 0.25);
  const lateral = lower.map((p) => p[0]);
  const center = [(quantile(lateral, 0.1) + quantile(lateral, 0.9)) * 0.5, bottom, median(lower.map((p) => p[2]))];
  const core = projected.filter(
    (p) => Math.abs(p[0] - center[0]) < roughHeight * 0.12 && Math.abs(p[2] - center[2]) < roughHeight * 0.13,
  );
  if (core.length < 30) return null;
  // Accessories outside the central body corridor cannot set character height.
  const height =
    quantile(
      core.map((p) => p[1]),
      0.995,
    ) - bottom;
  const cells = new Map();
  for (const p of projected) {
    const normalized = p.map((v, i) => (v - center[i]) / height);
    const key = normalized.map((v) => Math.round(v / 0.003)).join(',');
    if (!cells.has(key)) cells.set(key, normalized);
  }
  return { points: [...cells.values()], center, height };
}

// Local cross-section candidates, not a component or voxel skeleton. Continuity,
// depth, length, and a broad downward A-pose constraint select anatomical paths.
function armPath(points, shoulder, side) {
  const path = [{ center: shoulder, width: [0, 0.04, 0.04], support: 0 }];
  let slope = -0.65;
  for (let x = Math.abs(shoulder[0]) + 0.015; x <= 0.44; x += 0.015) {
    const previous = path.at(-1).center;
    const predicted = [side * x, previous[1] + slope * 0.015, previous[2]];
    const local = points.filter(
      (p) =>
        Math.abs(side * p[0] - x) <= 0.009 &&
        p[1] > 0.38 &&
        p[1] < shoulder[1] + 0.025 &&
        Math.abs(p[2] - shoulder[2]) < 0.09,
    );
    const candidates = new Map();
    for (const p of local) {
      const key = `${Math.round(p[1] / 0.022)},${Math.round(p[2] / 0.022)}`;
      if (!candidates.has(key)) {
        const slice = local.filter((q) => Math.abs(q[1] - p[1]) < 0.027 && Math.abs(q[2] - p[2]) < 0.027);
        if (slice.length < 5) continue;
        const center = [side * x, median(slice.map((q) => q[1])), median(slice.map((q) => q[2]))];
        const width = [0, 1, 2].map(
          (i) =>
            quantile(
              slice.map((q) => q[i]),
              0.9,
            ) -
            quantile(
              slice.map((q) => q[i]),
              0.1,
            ),
        );
        candidates.set(key, { center, width, support: slice.length });
      }
    }
    const best = [...candidates.values()]
      .filter((c) => distance(c.center, previous) < 0.055)
      .sort((a, b) => {
        const score = (c) =>
          distance(c.center, predicted) +
          Math.abs(c.center[2] - shoulder[2]) * 0.45 -
          Math.min(c.support, 25) * 0.00015;
        return score(a) - score(b);
      })[0];
    if (!best) break;
    path.push(best);
    slope = clamp(slope * 0.4 + ((best.center[1] - previous[1]) / 0.015) * 0.6, -2, 0.1);
    if (pathLength(path) > 0.38) break;
  }
  return path;
}

function pathLength(path) {
  return path.slice(1).reduce((sum, item, i) => sum + distance(item.center, path[i].center), 0);
}

function fitArm(points, priorShoulder, side) {
  const candidates = [];
  for (let y = priorShoulder[1] - 0.06; y <= priorShoulder[1] - 0.008; y += 0.012) {
    const slice = section(points, [0, y, priorShoulder[2]], [0.16, 0, 0.08]);
    if (!slice) continue;
    const shoulder = [side * clamp(slice.width[0] * 0.42, 0.08, 0.12), y, slice.center[2]];
    const path = armPath(points, shoulder, side);
    const length = pathLength(path);
    const curvature = path
      .slice(1, -1)
      .reduce((sum, item, i) => sum + distance(item.center, mix(path[i].center, path[i + 2].center, 0.5)), 0);
    const score =
      Math.abs(length - 0.315) +
      Math.abs(y - priorShoulder[1]) * 0.4 +
      curvature * 2 -
      Math.abs(path.at(-1).center[0]) * 0.3;
    candidates.push({ path, length, score });
  }
  const best = candidates.sort((a, b) => a.score - b.score)[0];
  if (!best || best.length < 0.19 || best.path.length < 8) return null;
  const path = best.path;
  const cumulative = [0];
  path.slice(1).forEach((item, i) => cumulative.push(cumulative.at(-1) + distance(item.center, path[i].center)));
  const elbowCandidates = path
    .map((item, i) => ({ ...item, i, fraction: cumulative[i] / best.length }))
    .filter((item) => item.fraction >= 0.38 && item.fraction <= 0.62);
  const elbow = elbowCandidates.sort((a, b) => {
    const score = (item) => {
      const before = path[item.i - 1].center,
        after = path[item.i + 1].center;
      const bend = distance(item.center, mix(before, after, 0.5));
      return Math.abs(item.fraction - 0.5) * 0.02 + item.width[1] * 0.2 - bend;
    };
    return score(a) - score(b);
  })[0];
  // The distal forearm establishes a direction in three dimensions. A hand
  // expansion can refine its endpoint, but a narrow sleeve cannot select it.
  const forearm = path.filter((_, i) => cumulative[i] / best.length >= 0.6 && cumulative[i] / best.length <= 0.8);
  const shoulder = { ...path[1], center: mix(path[0].center, path[1].center, 0.3) };
  if (forearm.length < 2) return { Shoulder: shoulder, Elbow: elbow };
  const direction = forearm.at(-1).center.map((v, i) => v - forearm[0].center[i]);
  const lengthSquared = dot(direction, direction);
  if (lengthSquared < 1e-12) return { Shoulder: shoulder, Elbow: elbow };
  const wrist = path
    .map((item, i) => ({ ...item, i, fraction: cumulative[i] / best.length }))
    .filter((item) => item.fraction >= 0.8 && item.fraction <= 0.94)
    .sort((a, b) => {
      const score = (item) => {
        const delta = item.center.map((v, i) => v - forearm[0].center[i]);
        const projected = forearm[0].center.map((v, i) => v + (direction[i] * dot(delta, direction)) / lengthSquared);
        const later = path.slice(item.i + 1, item.i + 4);
        const expansion = median(later.map((p) => p.width[1] + p.width[2])) - item.width[1] - item.width[2];
        return (
          distance(item.center, projected) * 0.8 +
          Math.abs(item.fraction - 0.87) * 0.05 -
          clamp(expansion, 0, 0.015) * 0.2
        );
      };
      return score(a) - score(b);
    })[0];
  return { Shoulder: shoulder, Elbow: elbow, Hand: wrist };
}

function fitLeg(points, hip, knee, foot, side) {
  const region = points.filter((p) => side * p[0] > 0.012);
  const path = [];
  for (let y = foot[1] + 0.045; y <= hip[1] + 0.035; y += 0.022) {
    const expected = mix(foot, hip, clamp((y - foot[1]) / (hip[1] - foot[1]), 0, 1));
    expected[1] = y;
    const slice = section(region, expected, [0.06, 0, 0.075]);
    const previous = path.at(-1);
    // Trace a bounded, continuous leg from the foot. Broad cloth, disconnected
    // panels, and clipped sections cannot establish an internal limb center.
    if (
      !slice ||
      slice.width[0] > 0.085 ||
      slice.width[2] > 0.12 ||
      (previous &&
        (distance(slice.center, previous.center) > 0.035 ||
          slice.width[0] > previous.width[0] * 1.6 ||
          slice.width[2] > previous.width[2] * 1.6))
    )
      break;
    path.push(slice);
  }
  const candidate = (prior, clearance) => {
    if ((path.at(-1)?.center[1] ?? 0) < prior[1] + clearance) return null;
    const near = path.filter((item) => Math.abs(item.center[1] - prior[1]) < 0.025);
    if (near.length < 2) return null;
    const center = [0, 1, 2].map((i) => median(near.map((item) => item.center[i])));
    // The segment relationship sets joint height; taper never identifies it.
    center[1] = prior[1];
    const displacement = distance(center, prior);
    return {
      center: mix(prior, center, Math.min(1, 0.025 / (displacement || 1))),
      support: near.reduce((sum, item) => sum + item.support, 0),
      strength: 0.3,
    };
  };
  return { Hip: candidate(hip, 0.025), Knee: candidate(knee, 0.045) };
}

function fitNeck(points, priors, targets, evidence) {
  const shoulders = ['leftShoulder', 'rightShoulder'].map((key) =>
    mix(priors[key], targets[key] || priors[key], evidence[key]?.strength || 0),
  );
  const shoulderHeight = (shoulders[0][1] + shoulders[1][1]) * 0.5;
  const head = targets.head || priors.head;
  const torso = targets.chest || priors.chest;
  const height = clamp(
    shoulderHeight + (head[1] - shoulderHeight) * 0.25,
    priors.neck[1] - 0.02,
    priors.neck[1] + 0.02,
  );
  const slices = [];
  for (let y = shoulderHeight + 0.012; y <= height + 0.012; y += 0.008) {
    const centerline = mix(torso, head, clamp((y - torso[1]) / (head[1] - torso[1]), 0, 1));
    centerline[1] = y;
    const slice = section(points, centerline, [0.07, 0, 0.065]);
    if (
      slice &&
      slice.width[0] > 0.025 &&
      slice.width[0] < 0.12 &&
      distance(slice.center, centerline) < 0.025 &&
      (!slices.length || distance(slice.center, slices.at(-1).center) < 0.02)
    )
      slices.push(slice);
  }
  if (slices.length < 3) return null;
  return {
    center: [median(slices.map((s) => s.center[0])), height, median(slices.map((s) => s.center[2]))],
    support: slices.reduce((sum, s) => sum + s.support, 0),
    strength: 0.55,
  };
}

/** Fit the existing 16-control topology without changing any active Rig. */
export async function fitHumanoidGeometryRig({ meshes = [], axes, orientationState, options = {}, isCurrent } = {}) {
  const started = clock();
  const resolvedAxes = axes || characterAxesFromOrientation(orientationState);
  if (!resolvedAxes || orientationState?.orientationInitialized === false) {
    return { available: false, diagnostics: { failureReasons: ['orientation_not_ready'] } };
  }
  const frame = semanticAxesFrame(resolvedAxes);
  const sampled = await sampleHumanoidRestSurface({ meshes, axes: frame, options, isCurrent });
  if (!sampled) return null;
  const surface = normalizeSurface(sampled.points, frame);
  if (!surface)
    return {
      available: false,
      diagnostics: { ...sampled.diagnostics, failureReasons: ['insufficient_body_geometry'] },
    };
  const { points, center, height } = surface;
  const toModel = (p) =>
    [0, 1, 2].map(
      (i) =>
        frame.right[i] * (center[0] + p[0] * height) +
        frame.up[i] * (center[1] + p[1] * height) +
        frame.forward[i] * (center[2] + p[2] * height),
    );
  const bodyPoints = points.filter((p) => p[1] >= 0 && p[1] <= 1);
  const proportionalRig = buildHumanoidControlRig({
    meshes: [
      {
        userData: {
          humanoidRestPositions: new Float32Array(bodyPoints.flatMap(toModel)),
        },
      },
    ],
    axes: frame,
    orientationState,
  });
  if (!proportionalRig.available) return { ...proportionalRig, proportionalRig };
  const rig = structuredClone(proportionalRig);
  const priors = Object.fromEntries(
    HUMANOID_CONTROL_KEYS.map((key) => [
      key,
      rig.controls[key].position.map(
        (_, i) => (dot(rig.controls[key].position, [frame.right, frame.up, frame.forward][i]) - center[i]) / height,
      ),
    ]),
  );
  const evidence = {};
  const targets = {};
  for (const key of ['pelvis', 'chest', 'head']) {
    const p = priors[key];
    const slice = section(points, p, [key === 'head' ? 0.09 : 0.13, 0, 0.09]);
    if (slice) {
      targets[key] = slice.center;
      evidence[key] = { ...slice, strength: key === 'pelvis' && slice.width[0] > 0.2 ? 0.2 : 0.55 };
    }
  }
  for (const key of ['chest', 'pelvis']) {
    const prior = priors[key];
    const slices = [];
    for (let y = prior[1] - 0.025; y <= prior[1] + 0.025; y += 0.008) {
      const slice = section(points, [prior[0], y, prior[2]], [0.13, 0, 0.09]);
      if (slice && slice.width[0] > 0.06 && slice.width[0] < 0.2) slices.push(slice);
    }
    const best = slices.sort((a, b) => {
      const score = (slice) => slice.width[0] - Math.abs(slice.center[1] - prior[1]) * 0.6;
      return score(b) - score(a);
    })[0];
    if (best) {
      targets[key] = best.center;
      targets[key][1] = prior[1];
      evidence[key] = { ...best, strength: 0.55 };
    }
  }
  for (const [prefix, side] of [
    ['left', -1],
    ['right', 1],
  ]) {
    const shoulder = [...priors[`${prefix}Shoulder`]];
    shoulder[2] = targets.chest?.[2] ?? shoulder[2];
    const arm = fitArm(points, shoulder, side);
    for (const suffix of ['Shoulder', 'Elbow', 'Hand']) {
      const key = `${prefix}${suffix}`;
      if (arm?.[suffix]) {
        targets[key] = arm[suffix].center;
        evidence[key] = {
          ...arm[suffix],
          strength: (suffix === 'Elbow' ? 0.6 : 0.9) * Math.min(1, arm[suffix].support / 16),
        };
      }
    }
    const footKey = `${prefix}Foot`;
    const foot = section(
      points.filter((p) => side * p[0] > 0.012),
      priors[footKey],
      [0.06, 0, 0.09],
    );
    if (foot && foot.width[0] < 0.09 && foot.width[2] < 0.14 && side * foot.center[0] > 0.015) {
      targets[footKey] = foot.center;
      evidence[footKey] = { ...foot, strength: 0.75 };
    }
    const leg = fitLeg(
      points,
      priors[`${prefix}Hip`],
      priors[`${prefix}Knee`],
      targets[footKey] || priors[footKey],
      side,
    );
    for (const suffix of ['Hip', 'Knee']) {
      if (!leg[suffix]) continue;
      targets[prefix + suffix] = leg[suffix].center;
      evidence[prefix + suffix] = leg[suffix];
    }
  }
  const neck = fitNeck(points, priors, targets, evidence);
  if (neck) {
    targets.neck = neck.center;
    evidence.neck = neck;
  }
  // Symmetry is a soft constraint. A well supported side may guide an obscured
  // side, while reliable differences retain their geometric contribution.
  for (const suffix of ['Shoulder', 'Elbow', 'Hand', 'Hip', 'Knee', 'Foot']) {
    const originalTargets = { ...targets };
    for (const [prefix, opposite] of [
      ['left', 'right'],
      ['right', 'left'],
    ]) {
      const key = prefix + suffix,
        other = opposite + suffix;
      const local = evidence[key]?.strength || 0;
      const remote = evidence[other]?.strength || 0;
      const own = originalTargets[key] || priors[key];
      const mirrored = originalTargets[other]
        ? [-originalTargets[other][0], originalTargets[other][1], originalTargets[other][2]]
        : priors[key];
      const mirrorSupport = points.filter((p) => distance(p, mirrored) < 0.04).length;
      const symmetry = remote > local ? 0.3 * remote : mirrorSupport >= 10 ? 0.45 : 0.06 * remote;
      if (local || remote) targets[key] = mix(own, mirrored, symmetry);
    }
  }
  const joints = {};
  for (const key of HUMANOID_CONTROL_KEYS) {
    const local = evidence[key];
    const strength = local?.strength || 0;
    const target = targets[key] || priors[key];
    const fitted = mix(priors[key], target, Math.max(strength, targets[key] ? 0.18 : 0));
    const position = toModel(fitted);
    const confidence = strength >= 0.7 ? 'high' : strength >= 0.3 ? 'medium' : 'low';
    rig.controls[key] = {
      ...rig.controls[key],
      position,
      semantic: humanoidControlPositionToSemantic(position, rig),
      confidence,
      source: 'geometry_constraint_fit',
      support: local?.support || 0,
      fitted: strength > 0,
    };
    joints[key] = {
      position,
      geometryDetected: strength > 0,
      confidence,
      evidence: strength,
      support: local?.support || 0,
      deviationHeight: distance(position, proportionalRig.controls[key].position) / height,
    };
  }
  rig.mode = rig.source = 'geometry_constraint_fit';
  rig.confidence = 'medium';
  rig.confidenceByRegion = Object.fromEntries(
    Object.entries({
      torso: ['pelvis', 'chest'],
      head: ['neck', 'head'],
      left_arm: ['leftShoulder', 'leftElbow', 'leftHand'],
      right_arm: ['rightShoulder', 'rightElbow', 'rightHand'],
      left_leg: ['leftHip', 'leftKnee', 'leftFoot'],
      right_leg: ['rightHip', 'rightKnee', 'rightFoot'],
    }).map(([key, controls]) => [
      key,
      controls.every((control) => joints[control].confidence === 'high')
        ? 'high'
        : controls.some((control) => joints[control].confidence === 'low')
          ? 'low'
          : 'medium',
    ]),
  );
  rebuildHumanoidControlPaths(rig);
  rig.diagnostics = {
    ...sampled.diagnostics,
    areaSampleCount: sampled.points.length,
    sampledPointCount: points.length,
    characterHeight: height,
    fitRuntimeMs: clock() - started,
    joints,
    regions: rig.confidenceByRegion,
    failureReasons: [],
  };
  // Preserve the proportional result for direct comparison on identical input.
  rig.proportionalRig = proportionalRig;
  return rig;
}
