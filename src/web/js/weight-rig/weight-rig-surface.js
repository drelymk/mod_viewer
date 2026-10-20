// Triangle-integrated skinning evidence and source-local graph accumulation.
import { createWorkBudget } from './cooperative-scheduler.js';

function triangleArea(points) {
  const [a, b, c] = points;
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  return 0.5 * Math.hypot(ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]);
}

function readSurfaceTriangle(positionArray, indexArray, indexed, positionCount, triangle) {
  const indices = [0, 1, 2].map((offset) => {
    const index = triangle * 3 + offset;
    return indexed ? Number(indexArray[index]) : index;
  });
  if (indices.some((index) => !Number.isInteger(index) || index < 0 || index >= positionCount)) {
    return { kind: 'invalid' };
  }
  const points = indices.map((vertex) => {
    const offset = vertex * 3;
    return [Number(positionArray[offset]), Number(positionArray[offset + 1]), Number(positionArray[offset + 2])];
  });
  if (points.some((point) => point.some((value) => !Number.isFinite(value)))) {
    return { kind: 'invalid' };
  }
  const area = triangleArea(points);
  if (!Number.isFinite(area)) return { kind: 'invalid' };
  if (area <= 0) return { kind: 'degenerate', indices, points, area };
  return { kind: 'valid', indices, points, area };
}

/**
 * Inspect triangle topology without constructing surface influence evidence.
 *
 * `triangleIndices` is optional because non-indexed BufferGeometry uses
 * consecutive position triples as its triangle topology.
 */
export function inspectSurfaceTopology(positions, triangleIndices = null) {
  const accumulator = createSurfaceTopologyAccumulator(positions, triangleIndices);
  const { shape } = accumulator;
  for (let triangle = 0; triangle < shape.triangleCount; triangle += 1) {
    accumulator.process(
      readSurfaceTriangle(shape.positionArray, shape.indexArray, shape.indexed, shape.positionCount, triangle),
    );
  }
  return accumulator.finish();
}

function positiveInfluencesForVertex(indices, weights, influenceCount, vertexIndex) {
  const result = new Map();
  const start = vertexIndex * influenceCount;
  for (let influence = 0; influence < influenceCount; influence += 1) {
    const boneId = Number(indices[start + influence]);
    const weight = Number(weights[start + influence]);
    if (!Number.isInteger(boneId) || boneId < 0 || !Number.isFinite(weight) || weight <= 0) continue;
    result.set(boneId, (result.get(boneId) || 0) + weight);
  }
  return result;
}

function barycentricSecondMoment(left, right, area) {
  return left === right ? area / 6 : area / 12;
}

export function barycentricThirdMoment(first, second, third, area) {
  if (first === second && second === third) return area / 10;
  if (first === second || first === third || second === third) {
    return area / 30;
  }
  return area / 60;
}

function dot3(left, right) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
}

function integrateLinear(weights, area) {
  return (area * (weights[0] + weights[1] + weights[2])) / 3;
}

function integrateLinearPosition(points, weights, area) {
  const result = [0, 0, 0];
  for (let point = 0; point < 3; point += 1) {
    let coefficient = 0;
    for (let weight = 0; weight < 3; weight += 1) {
      coefficient += barycentricSecondMoment(point, weight, area) * weights[weight];
    }
    result[0] += points[point][0] * coefficient;
    result[1] += points[point][1] * coefficient;
    result[2] += points[point][2] * coefficient;
  }
  return result;
}

export function integrateLinearSecondMoment(points, weights, area) {
  let result = 0;
  for (let left = 0; left < 3; left += 1) {
    for (let right = 0; right < 3; right += 1) {
      for (let weight = 0; weight < 3; weight += 1) {
        result +=
          dot3(points[left], points[right]) * weights[weight] * barycentricThirdMoment(left, right, weight, area);
      }
    }
  }
  return result;
}

function integrateLinearProduct(leftWeights, rightWeights, area) {
  let result = 0;
  for (let left = 0; left < 3; left += 1) {
    for (let right = 0; right < 3; right += 1) {
      result += leftWeights[left] * rightWeights[right] * barycentricSecondMoment(left, right, area);
    }
  }
  return result;
}

export function integratePositionProduct(points, leftWeights, rightWeights, area) {
  const result = [0, 0, 0];
  for (let point = 0; point < 3; point += 1) {
    for (let left = 0; left < 3; left += 1) {
      for (let right = 0; right < 3; right += 1) {
        const contribution = leftWeights[left] * rightWeights[right] * barycentricThirdMoment(point, left, right, area);
        result[0] += points[point][0] * contribution;
        result[1] += points[point][1] * contribution;
        result[2] += points[point][2] * contribution;
      }
    }
  }
  return result;
}

function interpolateSurfacePoint(left, right) {
  const denominator = left.d - right.d;
  const fraction = denominator === 0 ? 0 : left.d / denominator;
  return {
    position: [0, 1, 2].map((axis) => left.position[axis] + (right.position[axis] - left.position[axis]) * fraction),
    leftWeight: left.leftWeight + (right.leftWeight - left.leftWeight) * fraction,
    rightWeight: left.rightWeight + (right.rightWeight - left.rightWeight) * fraction,
    d: 0,
  };
}

function clipLinearWeightPolygon(vertices, keepLeft) {
  if (!vertices.length) return [];
  const result = [];
  const inside = (vertex) => (keepLeft ? vertex.d <= 0 : vertex.d >= 0);
  for (let index = 0; index < vertices.length; index += 1) {
    const left = vertices[index];
    const right = vertices[(index + 1) % vertices.length];
    const leftInside = inside(left);
    const rightInside = inside(right);
    if (leftInside && rightInside) {
      result.push(right);
    } else if (leftInside && !rightInside) {
      result.push(interpolateSurfacePoint(left, right));
    } else if (!leftInside && rightInside) {
      result.push(interpolateSurfacePoint(left, right), right);
    }
  }
  return result;
}

function integratePolygonLinearWeight(vertices, side) {
  if (vertices.length < 3) return 0;
  const origin = vertices[0];
  let result = 0;
  for (let index = 1; index + 1 < vertices.length; index += 1) {
    const triangle = [origin.position, vertices[index].position, vertices[index + 1].position];
    const area = triangleArea(triangle);
    result += (area * (origin[side] + vertices[index][side] + vertices[index + 1][side])) / 3;
  }
  return result;
}

function integrateMinimumLinearWeight(points, leftWeights, rightWeights) {
  const vertices = points.map((position, index) => ({
    position,
    leftWeight: leftWeights[index],
    rightWeight: rightWeights[index],
    d: leftWeights[index] - rightWeights[index],
  }));
  const hasPositiveDifference = vertices.some((vertex) => vertex.d > 0);
  const hasNegativeDifference = vertices.some((vertex) => vertex.d < 0);
  if (!hasPositiveDifference) {
    return integratePolygonLinearWeight(vertices, 'leftWeight');
  }
  if (!hasNegativeDifference) {
    return integratePolygonLinearWeight(vertices, 'rightWeight');
  }
  const leftRegion = clipLinearWeightPolygon(vertices, true);
  const rightRegion = clipLinearWeightPolygon(vertices, false);
  return (
    integratePolygonLinearWeight(leftRegion, 'leftWeight') + integratePolygonLinearWeight(rightRegion, 'rightWeight')
  );
}

/** Return whether topology contains at least one usable positive-area triangle. */
export function hasUsableSurfaceTopology(positions, triangleIndices = null) {
  const positionArray = positions || [];
  const positionCount = Math.floor(positionArray.length / 3);
  const indexed = triangleIndices !== null && triangleIndices !== undefined;
  const indexArray = indexed ? triangleIndices : null;
  const indexCount = indexed ? Number(indexArray?.length) || 0 : Number(positionArray.length) / 3;
  const triangleCount = Math.ceil(indexCount / 3);
  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    const sample = readSurfaceTriangle(positionArray, indexArray, indexed, positionCount, triangle);
    if (sample.kind === 'valid' && sample.area > 0) return true;
  }
  return false;
}

function surfaceInfluenceWeights(indices, weights, influenceCount, vertexIndex, requested) {
  const values = positiveInfluencesForVertex(indices, weights, influenceCount, vertexIndex);
  if (!requested) return values;
  return new Map([...values].filter(([boneId]) => requested.has(boneId)));
}

/**
 * Integrate source-local skinning evidence over valid triangles.
 *
 * Skin weights are linear over each triangle, matching the interpolation used
 * by the renderer. The resulting support, moments, pair products and pivots
 * are therefore independent of equivalent tessellation.
 */
export function buildSurfaceInfluenceGraph(
  baselinePositions,
  triangleIndices,
  indices,
  weights,
  influenceCount,
  boneIds = null,
  boundingSphereRadius = null,
) {
  const accumulator = createSurfaceGraphAccumulator(
    baselinePositions,
    triangleIndices,
    indices,
    weights,
    influenceCount,
    boneIds,
    boundingSphereRadius,
  );
  const { shape } = accumulator;
  for (let triangle = 0; triangle < shape.triangleCount; triangle += 1) {
    accumulator.process(
      readSurfaceTriangle(shape.positionArray, shape.indexArray, shape.indexed, shape.positionCount, triangle),
    );
  }
  return accumulator.finish();
}

function surfaceTriangleCount(positions, triangleIndices = null) {
  const positionArray = positions || [];
  const indexed = triangleIndices !== null && triangleIndices !== undefined;
  const indexCount = indexed ? Number(triangleIndices?.length) || 0 : Number(positionArray.length) / 3;
  return {
    positionArray,
    positionCount: Math.floor(positionArray.length / 3),
    indexed,
    indexArray: indexed ? triangleIndices : null,
    triangleCount: Math.ceil(indexCount / 3),
  };
}

function createSurfaceTopologyAccumulator(positions, triangleIndices = null) {
  const shape = surfaceTriangleCount(positions, triangleIndices);
  const topology = {
    triangleCount: shape.triangleCount,
    validTriangleCount: 0,
    degenerateTriangleCount: 0,
    invalidTriangleCount: 0,
    totalSurfaceArea: 0,
    measuredVertices: new Set(),
  };

  function process(sample) {
    if (sample.kind === 'invalid') {
      topology.invalidTriangleCount += 1;
    } else if (sample.kind === 'degenerate') {
      topology.degenerateTriangleCount += 1;
    } else {
      topology.validTriangleCount += 1;
      topology.totalSurfaceArea += sample.area;
      sample.indices.forEach((index) => topology.measuredVertices.add(index));
    }
  }

  function finish() {
    return {
      triangleCount: topology.triangleCount,
      validTriangleCount: topology.validTriangleCount,
      degenerateTriangleCount: topology.degenerateTriangleCount,
      invalidTriangleCount: topology.invalidTriangleCount,
      totalSurfaceArea: topology.totalSurfaceArea,
      measuredVertexCount: topology.measuredVertices.size,
      zeroMeasureVertexCount: shape.positionCount - topology.measuredVertices.size,
      surfaceEvidenceAvailable: topology.validTriangleCount > 0 && topology.totalSurfaceArea > 0,
    };
  }

  return { shape, process, finish };
}

function createSurfaceGraphAccumulator(
  baselinePositions,
  triangleIndices,
  indices,
  weights,
  influenceCount,
  boneIds = null,
  boundingSphereRadius = null,
) {
  const topologyAccumulator = createSurfaceTopologyAccumulator(baselinePositions, triangleIndices);
  const { shape } = topologyAccumulator;
  const requested = boneIds === null || boneIds === undefined ? null : new Set([...boneIds].map(Number));
  const nodeEntries = new Map();
  const relationshipEntries = new Map();

  function process(sample) {
    topologyAccumulator.process(sample);
    if (sample.kind !== 'valid') return;
    const cornerWeights = sample.indices.map((vertex) =>
      surfaceInfluenceWeights(indices, weights, influenceCount, vertex, requested),
    );
    const ids = [...new Set(cornerWeights.flatMap((values) => [...values.keys()]))].sort((left, right) => left - right);
    for (const boneId of ids) {
      const boneWeights = cornerWeights.map((values) => values.get(boneId) || 0);
      const support = integrateLinear(boneWeights, sample.area);
      if (support <= 0) continue;
      const firstMoment = integrateLinearPosition(sample.points, boneWeights, sample.area);
      const secondMoment = integrateLinearSecondMoment(sample.points, boneWeights, sample.area);
      const entry = nodeEntries.get(boneId) || {
        boneId,
        totalWeight: 0,
        affectedVertexCount: 0,
        affectedVertices: new Set(),
        affectedMeasure: 0,
        maxVertexWeight: 0,
        weightedX: 0,
        weightedY: 0,
        weightedZ: 0,
        secondMoment: 0,
      };
      entry.totalWeight += support;
      sample.indices.forEach((vertex, index) => {
        if (cornerWeights[index].has(boneId)) entry.affectedVertices.add(vertex);
      });
      entry.affectedMeasure += sample.area;
      entry.maxVertexWeight = Math.max(entry.maxVertexWeight, ...boneWeights);
      entry.weightedX += firstMoment[0];
      entry.weightedY += firstMoment[1];
      entry.weightedZ += firstMoment[2];
      entry.secondMoment += secondMoment;
      nodeEntries.set(boneId, entry);
    }
    for (let left = 0; left < ids.length; left += 1) {
      for (let right = left + 1; right < ids.length; right += 1) {
        const boneA = ids[left];
        const boneB = ids[right];
        const weightsA = cornerWeights.map((values) => values.get(boneA) || 0);
        const weightsB = cornerWeights.map((values) => values.get(boneB) || 0);
        const productOverlap = integrateLinearProduct(weightsA, weightsB, sample.area);
        if (productOverlap <= 0) continue;
        const key = pairKey(boneA, boneB);
        const jointMoment = integratePositionProduct(sample.points, weightsA, weightsB, sample.area);
        const relationship = relationshipEntries.get(key) || {
          boneA,
          boneB,
          sharedVertexCount: 0,
          sharedMeasure: 0,
          minOverlap: 0,
          productOverlap: 0,
          jointWeightTotal: 0,
          jointX: 0,
          jointY: 0,
          jointZ: 0,
        };
        relationship.sharedVertexCount += sample.indices.filter(
          (vertex, index) => cornerWeights[index].has(boneA) && cornerWeights[index].has(boneB),
        ).length;
        relationship.sharedMeasure += sample.area;
        relationship.minOverlap += integrateMinimumLinearWeight(sample.points, weightsA, weightsB);
        relationship.productOverlap += productOverlap;
        relationship.jointWeightTotal += productOverlap;
        relationship.jointX += jointMoment[0];
        relationship.jointY += jointMoment[1];
        relationship.jointZ += jointMoment[2];
        relationshipEntries.set(key, relationship);
      }
    }
  }

  function finish() {
    const topology = topologyAccumulator.finish();
    const nodes = [...nodeEntries.values()]
      .map((entry) => {
        const center =
          entry.totalWeight > 0
            ? [
                entry.weightedX / entry.totalWeight,
                entry.weightedY / entry.totalWeight,
                entry.weightedZ / entry.totalWeight,
              ]
            : [0, 0, 0];
        return {
          boneId: entry.boneId,
          totalWeight: entry.totalWeight,
          affectedVertexCount: entry.affectedVertices.size,
          affectedMeasure: entry.affectedMeasure,
          maxVertexWeight: entry.maxVertexWeight,
          weightedCenter: center,
          weightedRadius: Math.sqrt(Math.max(0, entry.secondMoment / entry.totalWeight - dot3(center, center))),
        };
      })
      .sort((left, right) => left.boneId - right.boneId);
    const nodeById = new Map(nodes.map((node) => [node.boneId, node]));
    const radius = Number(boundingSphereRadius);
    const totalNodeWeight = nodes.reduce((sum, node) => sum + node.totalWeight, 0);
    const sourceCenter = nodes.length
      ? nodes
          .reduce(
            (sum, node) => [
              sum[0] + node.weightedCenter[0] * node.totalWeight,
              sum[1] + node.weightedCenter[1] * node.totalWeight,
              sum[2] + node.weightedCenter[2] * node.totalWeight,
            ],
            [0, 0, 0],
          )
          .map((value) => (totalNodeWeight > 0 ? value / totalNodeWeight : 0))
      : [0, 0, 0];
    const sourceRadius = nodes.length
      ? Math.max(
          ...nodes.map(
            (node) => (centerDistance(node.weightedCenter, sourceCenter) || 0) + (Number(node.weightedRadius) || 0),
          ),
        )
      : null;
    const relationships = [...relationshipEntries.values()].map((relationship) => {
      const nodeA = nodeById.get(relationship.boneA);
      const nodeB = nodeById.get(relationship.boneB);
      const supportA = Number(nodeA?.totalWeight) || 0;
      const supportB = Number(nodeB?.totalWeight) || 0;
      const denominator = Math.min(supportA, supportB);
      const jaccardDenominator = supportA + supportB - relationship.minOverlap;
      const distance = centerDistance(nodeA?.weightedCenter, nodeB?.weightedCenter);
      const normalizedDistance = distance !== null && Number.isFinite(radius) && radius > 0 ? distance / radius : null;
      const distancePenalty = Number.isFinite(normalizedDistance) ? 1 / (1 + Math.max(0, normalizedDistance)) : 1;
      return {
        ...relationship,
        jointCenter:
          relationship.jointWeightTotal > 0
            ? [
                relationship.jointX / relationship.jointWeightTotal,
                relationship.jointY / relationship.jointWeightTotal,
                relationship.jointZ / relationship.jointWeightTotal,
              ]
            : null,
        containment: denominator > 0 ? relationship.minOverlap / denominator : 0,
        jaccard: jaccardDenominator > 0 ? relationship.minOverlap / jaccardDenominator : 0,
        centerDistance: distance,
        normalizedDistance,
        treeEdgeScore: (denominator > 0 ? relationship.minOverlap / denominator : 0) * distancePenalty,
      };
    });
    return {
      nodes,
      relationships,
      boundingSphereRadius: Number.isFinite(radius) && radius > 0 ? radius : sourceRadius,
      evidenceMode: 'surface',
      triangleCount: topology.triangleCount,
      validTriangleCount: topology.validTriangleCount,
      degenerateTriangleCount: topology.degenerateTriangleCount,
      invalidTriangleCount: topology.invalidTriangleCount,
      totalSurfaceArea: topology.totalSurfaceArea,
      measuredVertexCount: topology.measuredVertexCount,
      zeroMeasureVertexCount: topology.zeroMeasureVertexCount,
    };
  }

  return { shape, process, finish };
}

export async function inspectSurfaceTopologyCooperative(
  positions,
  triangleIndices = null,
  { budget = createWorkBudget(), isCurrent = () => true, triangleBatch = 32 } = {},
) {
  const accumulator = createSurfaceTopologyAccumulator(positions, triangleIndices);
  const { shape } = accumulator;
  for (let triangle = 0; triangle < shape.triangleCount; triangle += 1) {
    if (triangle % triangleBatch === 0) {
      if (!isCurrent()) return null;
      await budget.checkpoint();
      if (!isCurrent()) return null;
    }
    accumulator.process(
      readSurfaceTriangle(shape.positionArray, shape.indexArray, shape.indexed, shape.positionCount, triangle),
    );
  }
  if (!isCurrent()) return null;
  await budget.checkpoint();
  return accumulator.finish();
}

export async function buildSurfaceInfluenceGraphCooperative(
  baselinePositions,
  triangleIndices,
  indices,
  weights,
  influenceCount,
  boneIds = null,
  boundingSphereRadius = null,
  { budget = createWorkBudget(), isCurrent = () => true, triangleBatch = 32 } = {},
) {
  const accumulator = createSurfaceGraphAccumulator(
    baselinePositions,
    triangleIndices,
    indices,
    weights,
    influenceCount,
    boneIds,
    boundingSphereRadius,
  );
  const { shape } = accumulator;
  for (let triangle = 0; triangle < shape.triangleCount; triangle += 1) {
    if (triangle % triangleBatch === 0) {
      if (!isCurrent()) return null;
      await budget.checkpoint();
      if (!isCurrent()) return null;
    }
    accumulator.process(
      readSurfaceTriangle(shape.positionArray, shape.indexArray, shape.indexed, shape.positionCount, triangle),
    );
  }
  if (!isCurrent()) return null;
  await budget.checkpoint();
  return accumulator.finish();
}

export function pairKey(boneA, boneB) {
  return boneA < boneB ? `${boneA}:${boneB}` : `${boneB}:${boneA}`;
}

export function centerDistance(centerA, centerB) {
  if (!centerA || !centerB || centerA.length < 3 || centerB.length < 3) {
    return null;
  }
  return Math.hypot(
    Number(centerA[0]) - Number(centerB[0]),
    Number(centerA[1]) - Number(centerB[1]),
    Number(centerA[2]) - Number(centerB[2]),
  );
}

export function relationshipSort(a, b) {
  return (
    (Number(b.containment) || 0) - (Number(a.containment) || 0) ||
    (Number(b.jaccard) || 0) - (Number(a.jaccard) || 0) ||
    Number(a.normalizedDistance ?? Infinity) - Number(b.normalizedDistance ?? Infinity) ||
    String(a.boneA).localeCompare(String(b.boneA)) ||
    String(a.boneB).localeCompare(String(b.boneB))
  );
}

/** Aggregate per-mesh evidence while preserving weighted joint positions. */
export function aggregateInfluenceGraphs(graphs) {
  const inputGraphs = (graphs || []).filter(Boolean);
  if (inputGraphs.some((graph) => graph.evidenceMode !== 'surface')) {
    throw new Error('Rig evidence must come from usable triangle geometry.');
  }
  const diagnosticTotal = (field) => inputGraphs.reduce((sum, graph) => sum + (Number(graph?.[field]) || 0), 0);
  const nodeTotals = new Map();
  const relationshipTotals = new Map();
  for (const graph of inputGraphs) {
    for (const node of graph?.nodes || []) {
      const boneId = Number(node.boneId);
      const totalWeight = Number(node.totalWeight) || 0;
      if (!Number.isFinite(boneId) || totalWeight <= 0) continue;
      const center = node.weightedCenter || [0, 0, 0];
      const radius = Math.max(0, Number(node.weightedRadius) || 0);
      const entry = nodeTotals.get(boneId) || {
        boneId,
        totalWeight: 0,
        affectedVertexCount: 0,
        affectedMeasure: 0,
        maxVertexWeight: 0,
        weightedX: 0,
        weightedY: 0,
        weightedZ: 0,
        secondMoment: 0,
      };
      entry.totalWeight += totalWeight;
      entry.affectedVertexCount += Number(node.affectedVertexCount) || 0;
      entry.affectedMeasure += Number(node.affectedMeasure) || 0;
      entry.maxVertexWeight = Math.max(entry.maxVertexWeight, Number(node.maxVertexWeight) || 0);
      entry.weightedX += totalWeight * (Number(center[0]) || 0);
      entry.weightedY += totalWeight * (Number(center[1]) || 0);
      entry.weightedZ += totalWeight * (Number(center[2]) || 0);
      entry.secondMoment +=
        totalWeight *
        (radius * radius +
          (Number(center[0]) || 0) ** 2 +
          (Number(center[1]) || 0) ** 2 +
          (Number(center[2]) || 0) ** 2);
      nodeTotals.set(boneId, entry);
    }
    for (const relationship of graph?.relationships || []) {
      const boneA = Number(relationship.boneA);
      const boneB = Number(relationship.boneB);
      if (!Number.isFinite(boneA) || !Number.isFinite(boneB)) continue;
      const key = pairKey(boneA, boneB);
      const entry = relationshipTotals.get(key) || {
        boneA: Math.min(boneA, boneB),
        boneB: Math.max(boneA, boneB),
        sharedVertexCount: 0,
        sharedMeasure: 0,
        minOverlap: 0,
        productOverlap: 0,
        jointWeightTotal: 0,
        jointX: 0,
        jointY: 0,
        jointZ: 0,
      };
      entry.sharedVertexCount += Number(relationship.sharedVertexCount) || 0;
      entry.sharedMeasure += Number(relationship.sharedMeasure) || 0;
      entry.minOverlap += Number(relationship.minOverlap) || 0;
      entry.productOverlap += Number(relationship.productOverlap) || 0;
      const jointWeightTotal = Number(relationship.jointWeightTotal) || 0;
      const jointCenter = relationship.jointCenter;
      if (jointWeightTotal > 0 && jointCenter?.length >= 3) {
        entry.jointWeightTotal += jointWeightTotal;
        entry.jointX += Number(jointCenter[0]) * jointWeightTotal;
        entry.jointY += Number(jointCenter[1]) * jointWeightTotal;
        entry.jointZ += Number(jointCenter[2]) * jointWeightTotal;
      }
      relationshipTotals.set(key, entry);
    }
  }
  const nodes = [...nodeTotals.values()]
    .map((entry) => {
      const center =
        entry.totalWeight > 0
          ? [
              entry.weightedX / entry.totalWeight,
              entry.weightedY / entry.totalWeight,
              entry.weightedZ / entry.totalWeight,
            ]
          : [0, 0, 0];
      const centerLengthSquared = center.reduce((sum, value) => sum + value * value, 0);
      return {
        boneId: entry.boneId,
        totalWeight: entry.totalWeight,
        affectedVertexCount: entry.affectedVertexCount,
        affectedMeasure: entry.affectedMeasure,
        maxVertexWeight: entry.maxVertexWeight,
        weightedCenter: center,
        weightedRadius: Math.sqrt(Math.max(0, entry.secondMoment / entry.totalWeight - centerLengthSquared)),
      };
    })
    .sort((left, right) => left.boneId - right.boneId);
  const nodeById = new Map(nodes.map((node) => [node.boneId, node]));
  const totalWeight = nodes.reduce((sum, node) => sum + node.totalWeight, 0);
  const sourceCenter = nodes.length
    ? nodes
        .reduce(
          (sum, node) => [
            sum[0] + node.weightedCenter[0] * node.totalWeight,
            sum[1] + node.weightedCenter[1] * node.totalWeight,
            sum[2] + node.weightedCenter[2] * node.totalWeight,
          ],
          [0, 0, 0],
        )
        .map((value) => (totalWeight > 0 ? value / totalWeight : 0))
    : [0, 0, 0];
  const radius = nodes.length
    ? Math.max(
        ...nodes.map(
          (node) => (centerDistance(node.weightedCenter, sourceCenter) || 0) + (Number(node.weightedRadius) || 0),
        ),
      )
    : null;
  const relationships = [...relationshipTotals.values()].map((relationship) => {
    const nodeA = nodeById.get(relationship.boneA);
    const nodeB = nodeById.get(relationship.boneB);
    const supportA = Number(nodeA?.totalWeight) || 0;
    const supportB = Number(nodeB?.totalWeight) || 0;
    const denominator = Math.min(supportA, supportB);
    const jaccardDenominator = supportA + supportB - relationship.minOverlap;
    const distance = centerDistance(nodeA?.weightedCenter, nodeB?.weightedCenter);
    const normalizedDistance = distance !== null && radius > 0 ? distance / radius : null;
    const distancePenalty = Number.isFinite(normalizedDistance) ? 1 / (1 + Math.max(0, normalizedDistance)) : 1;
    return {
      ...relationship,
      sharedMeasure: relationship.sharedMeasure,
      jointCenter:
        relationship.jointWeightTotal > 0
          ? [
              relationship.jointX / relationship.jointWeightTotal,
              relationship.jointY / relationship.jointWeightTotal,
              relationship.jointZ / relationship.jointWeightTotal,
            ]
          : null,
      containment: denominator > 0 ? relationship.minOverlap / denominator : 0,
      jaccard: jaccardDenominator > 0 ? relationship.minOverlap / jaccardDenominator : 0,
      centerDistance: distance,
      normalizedDistance,
      treeEdgeScore: (denominator > 0 ? relationship.minOverlap / denominator : 0) * distancePenalty,
    };
  });
  return {
    nodes,
    relationships,
    boundingSphereRadius: radius,
    evidenceMode: 'surface',
    triangleCount: diagnosticTotal('triangleCount'),
    validTriangleCount: diagnosticTotal('validTriangleCount'),
    degenerateTriangleCount: diagnosticTotal('degenerateTriangleCount'),
    invalidTriangleCount: diagnosticTotal('invalidTriangleCount'),
    totalSurfaceArea: diagnosticTotal('totalSurfaceArea'),
    measuredVertexCount: diagnosticTotal('measuredVertexCount'),
    zeroMeasureVertexCount: diagnosticTotal('zeroMeasureVertexCount'),
  };
}
