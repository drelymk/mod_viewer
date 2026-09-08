// Generic, source-scoped analysis of authored skinning weights.
//
// This module deliberately contains no physics state.  Character Physics and
// the Rig/Pose mode consume the same inferred topology, while
// retaining their own runtime selection and deformation state.

export const CANDIDATE_CONTAINMENT_THRESHOLD = 0.02;
export const CANDIDATE_JACCARD_THRESHOLD = 0.01;

function triangleArea(points) {
  const [a, b, c] = points;
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  return 0.5 * Math.hypot(
    ab[1] * ac[2] - ab[2] * ac[1],
    ab[2] * ac[0] - ab[0] * ac[2],
    ab[0] * ac[1] - ab[1] * ac[0]);
}

function collectSurfaceTriangles(positions, triangleIndices = null) {
  const positionArray = positions || [];
  const positionCount = Math.floor(positionArray.length / 3);
  const indexed = triangleIndices !== null && triangleIndices !== undefined;
  const indexArray = indexed ? triangleIndices : null;
  const indexCount = indexed
    ? Number(indexArray?.length) || 0
    : Number(positionArray.length) / 3;
  const triangleCount = Math.ceil(indexCount / 3);
  const triangles = [];
  let degenerateTriangleCount = 0;
  let invalidTriangleCount = 0;
  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    const indices = [0, 1, 2].map(offset => {
      const index = triangle * 3 + offset;
      return indexed ? Number(indexArray[index]) : index;
    });
    if (indices.some(index => !Number.isInteger(index)
        || index < 0 || index >= positionCount)) {
      invalidTriangleCount += 1;
      continue;
    }
    const points = indices.map(vertex => {
      const offset = vertex * 3;
      return [
        Number(positionArray[offset]),
        Number(positionArray[offset + 1]),
        Number(positionArray[offset + 2]),
      ];
    });
    if (points.some(point => point.some(value => !Number.isFinite(value)))) {
      invalidTriangleCount += 1;
      continue;
    }
    const area = triangleArea(points);
    if (!Number.isFinite(area)) {
      invalidTriangleCount += 1;
      continue;
    }
    if (area === 0) {
      degenerateTriangleCount += 1;
      continue;
    }
    triangles.push({indices, points, area});
  }
  return {
    positionCount,
    triangleCount,
    triangles,
    validTriangleCount: triangles.length,
    degenerateTriangleCount,
    invalidTriangleCount,
    totalSurfaceArea: triangles.reduce((sum, triangle) =>
      sum + triangle.area, 0),
  };
}

/**
 * Build a lumped barycentric surface measure for each position vertex.
 *
 * `triangleIndices` is optional because non-indexed BufferGeometry uses
 * consecutive position triples as its triangle topology.
 */
export function buildVertexSurfaceMeasure(positions, triangleIndices = null) {
  const topology = collectSurfaceTriangles(positions, triangleIndices);
  const vertexMeasure = new Float64Array(topology.positionCount);
  topology.triangles.forEach(triangle => {
    const contribution = triangle.area / 3;
    triangle.indices.forEach(index => { vertexMeasure[index] += contribution; });
  });
  let measuredVertexCount = 0;
  for (const measure of vertexMeasure) {
    if (measure > 0) measuredVertexCount += 1;
  }
  return {
    vertexMeasure,
    triangleCount: topology.triangleCount,
    validTriangleCount: topology.validTriangleCount,
    degenerateTriangleCount: topology.degenerateTriangleCount,
    invalidTriangleCount: topology.invalidTriangleCount,
    totalSurfaceArea: topology.totalSurfaceArea,
    measuredVertexCount,
    zeroMeasureVertexCount: topology.positionCount - measuredVertexCount,
    surfaceEvidenceAvailable: topology.validTriangleCount > 0
      && topology.totalSurfaceArea > 0,
  };
}

function compactVertexCount(indices, weights, influenceCount) {
  if (!indices || !weights || !Number.isInteger(influenceCount)
      || influenceCount <= 0) return 0;
  return Math.floor(Math.min(indices.length, weights.length) / influenceCount);
}

function vertexEvidenceMeasure(vertexMeasure, vertex) {
  if (vertexMeasure === null || vertexMeasure === undefined) return 1;
  const measure = Number(vertexMeasure[vertex]);
  return Number.isFinite(measure) && measure >= 0 ? measure : 0;
}

function positiveInfluencesForVertex(
    indices, weights, influenceCount, vertexIndex) {
  const result = new Map();
  const start = vertexIndex * influenceCount;
  for (let influence = 0; influence < influenceCount; influence += 1) {
    const boneId = Number(indices[start + influence]);
    const weight = Number(weights[start + influence]);
    if (!Number.isInteger(boneId) || boneId < 0
        || !Number.isFinite(weight) || weight <= 0) continue;
    result.set(boneId, (result.get(boneId) || 0) + weight);
  }
  return result;
}

function barycentricSecondMoment(left, right, area) {
  return left === right ? area / 6 : area / 12;
}

function barycentricThirdMoment(first, second, third, area) {
  const counts = [first, second, third].reduce((map, index) => {
    map.set(index, (map.get(index) || 0) + 1);
    return map;
  }, new Map());
  const multiplicities = [...counts.values()];
  if (multiplicities.length === 1) return area / 10;
  if (multiplicities.length === 2) return area / 30;
  return area / 60;
}

function dot3(left, right) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
}

function integrateLinear(weights, area) {
  return area * (weights[0] + weights[1] + weights[2]) / 3;
}

function integrateLinearPosition(points, weights, area) {
  const result = [0, 0, 0];
  for (let point = 0; point < 3; point += 1) {
    let coefficient = 0;
    for (let weight = 0; weight < 3; weight += 1) {
      coefficient += barycentricSecondMoment(point, weight, area)
        * weights[weight];
    }
    result[0] += points[point][0] * coefficient;
    result[1] += points[point][1] * coefficient;
    result[2] += points[point][2] * coefficient;
  }
  return result;
}

function integrateLinearSecondMoment(points, weights, area) {
  let result = 0;
  for (let left = 0; left < 3; left += 1) {
    for (let right = 0; right < 3; right += 1) {
      for (let weight = 0; weight < 3; weight += 1) {
        result += dot3(points[left], points[right])
          * weights[weight]
          * barycentricThirdMoment(left, right, weight, area);
      }
    }
  }
  return result;
}

function integrateLinearProduct(leftWeights, rightWeights, area) {
  let result = 0;
  for (let left = 0; left < 3; left += 1) {
    for (let right = 0; right < 3; right += 1) {
      result += leftWeights[left] * rightWeights[right]
        * barycentricSecondMoment(left, right, area);
    }
  }
  return result;
}

function integratePositionProduct(points, leftWeights, rightWeights, area) {
  const result = [0, 0, 0];
  for (let point = 0; point < 3; point += 1) {
    for (let left = 0; left < 3; left += 1) {
      for (let right = 0; right < 3; right += 1) {
        const contribution = leftWeights[left] * rightWeights[right]
          * barycentricThirdMoment(point, left, right, area);
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
    position: [0, 1, 2].map(axis =>
      left.position[axis]
      + (right.position[axis] - left.position[axis]) * fraction),
    leftWeight: left.leftWeight
      + (right.leftWeight - left.leftWeight) * fraction,
    rightWeight: left.rightWeight
      + (right.rightWeight - left.rightWeight) * fraction,
    d: 0,
  };
}

function clipLinearWeightPolygon(vertices, keepLeft) {
  if (!vertices.length) return [];
  const result = [];
  const inside = vertex => keepLeft ? vertex.d <= 0 : vertex.d >= 0;
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
    const triangle = [
      origin.position, vertices[index].position,
      vertices[index + 1].position,
    ];
    const area = triangleArea(triangle);
    result += area * (
      origin[side] + vertices[index][side]
      + vertices[index + 1][side]) / 3;
  }
  return result;
}

function integrateMinimumLinearWeight(
    points, leftWeights, rightWeights, area) {
  const vertices = points.map((position, index) => ({
    position,
    leftWeight: leftWeights[index],
    rightWeight: rightWeights[index],
    d: leftWeights[index] - rightWeights[index],
  }));
  const hasPositiveDifference = vertices.some(vertex => vertex.d > 0);
  const hasNegativeDifference = vertices.some(vertex => vertex.d < 0);
  if (!hasPositiveDifference) {
    return integratePolygonLinearWeight(vertices, 'leftWeight');
  }
  if (!hasNegativeDifference) {
    return integratePolygonLinearWeight(vertices, 'rightWeight');
  }
  const leftRegion = clipLinearWeightPolygon(vertices, true);
  const rightRegion = clipLinearWeightPolygon(vertices, false);
  return integratePolygonLinearWeight(leftRegion, 'leftWeight')
    + integratePolygonLinearWeight(rightRegion, 'rightWeight');
}

function surfaceInfluenceWeights(
    indices, weights, influenceCount, vertexIndex, requested) {
  const values = positiveInfluencesForVertex(
    indices, weights, influenceCount, vertexIndex);
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
    baselinePositions, triangleIndices, indices, weights, influenceCount,
    boneIds = null, boundingSphereRadius = null) {
  const topology = collectSurfaceTriangles(
    baselinePositions, triangleIndices);
  const requested = boneIds === null || boneIds === undefined
    ? null : new Set([...boneIds].map(Number));
  const nodeEntries = new Map();
  const relationshipEntries = new Map();
  for (const triangle of topology.triangles) {
    const cornerWeights = triangle.indices.map(vertex => surfaceInfluenceWeights(
      indices, weights, influenceCount, vertex, requested));
    const ids = [...new Set(cornerWeights.flatMap(values => [...values.keys()]))]
      .sort((left, right) => left - right);
    for (const boneId of ids) {
      const boneWeights = cornerWeights.map(values => values.get(boneId) || 0);
      const support = integrateLinear(boneWeights, triangle.area);
      if (support <= 0) continue;
      const firstMoment = integrateLinearPosition(
        triangle.points, boneWeights, triangle.area);
      const secondMoment = integrateLinearSecondMoment(
        triangle.points, boneWeights, triangle.area);
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
      triangle.indices.forEach((vertex, index) => {
        if (cornerWeights[index].has(boneId)) {
          entry.affectedVertices.add(vertex);
        }
      });
      entry.affectedMeasure += triangle.area;
      entry.maxVertexWeight = Math.max(
        entry.maxVertexWeight, ...boneWeights);
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
        const weightsA = cornerWeights.map(values => values.get(boneA) || 0);
        const weightsB = cornerWeights.map(values => values.get(boneB) || 0);
        const productOverlap = integrateLinearProduct(
          weightsA, weightsB, triangle.area);
        if (productOverlap <= 0) continue;
        const key = pairKey(boneA, boneB);
        const jointMoment = integratePositionProduct(
          triangle.points, weightsA, weightsB, triangle.area);
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
        relationship.sharedVertexCount += triangle.indices.filter((vertex, index) =>
          cornerWeights[index].has(boneA)
          && cornerWeights[index].has(boneB)).length;
        relationship.sharedMeasure += triangle.area;
        relationship.minOverlap += integrateMinimumLinearWeight(
          triangle.points, weightsA, weightsB, triangle.area);
        relationship.productOverlap += productOverlap;
        relationship.jointWeightTotal += productOverlap;
        relationship.jointX += jointMoment[0];
        relationship.jointY += jointMoment[1];
        relationship.jointZ += jointMoment[2];
        relationshipEntries.set(key, relationship);
      }
    }
  }
  const nodes = [...nodeEntries.values()].map(entry => {
    const center = entry.totalWeight > 0 ? [
      entry.weightedX / entry.totalWeight,
      entry.weightedY / entry.totalWeight,
      entry.weightedZ / entry.totalWeight,
    ] : [0, 0, 0];
    return {
      boneId: entry.boneId,
      totalWeight: entry.totalWeight,
      affectedVertexCount: entry.affectedVertices.size,
      affectedMeasure: entry.affectedMeasure,
      maxVertexWeight: entry.maxVertexWeight,
      weightedCenter: center,
      weightedRadius: Math.sqrt(Math.max(0,
        entry.secondMoment / entry.totalWeight - dot3(center, center))),
    };
  }).sort((left, right) => left.boneId - right.boneId);
  const nodeById = new Map(nodes.map(node => [node.boneId, node]));
  const radius = Number(boundingSphereRadius);
  const measuredVertices = new Set(
    topology.triangles.flatMap(triangle => triangle.indices));
  const totalNodeWeight = nodes.reduce(
    (sum, node) => sum + node.totalWeight, 0);
  const sourceCenter = nodes.length ? nodes.reduce((sum, node) => [
    sum[0] + node.weightedCenter[0] * node.totalWeight,
    sum[1] + node.weightedCenter[1] * node.totalWeight,
    sum[2] + node.weightedCenter[2] * node.totalWeight,
  ], [0, 0, 0]).map(value =>
    totalNodeWeight > 0 ? value / totalNodeWeight : 0) : [0, 0, 0];
  const sourceRadius = nodes.length ? Math.max(...nodes.map(node =>
    (centerDistance(node.weightedCenter, sourceCenter) || 0)
      + (Number(node.weightedRadius) || 0))) : null;
  const relationships = [...relationshipEntries.values()].map(relationship => {
    const nodeA = nodeById.get(relationship.boneA);
    const nodeB = nodeById.get(relationship.boneB);
    const supportA = Number(nodeA?.totalWeight) || 0;
    const supportB = Number(nodeB?.totalWeight) || 0;
    const denominator = Math.min(supportA, supportB);
    const jaccardDenominator = supportA + supportB - relationship.minOverlap;
    const distance = centerDistance(nodeA?.weightedCenter, nodeB?.weightedCenter);
    const normalizedDistance = distance !== null
      && Number.isFinite(radius) && radius > 0
      ? distance / radius : null;
    const distancePenalty = Number.isFinite(normalizedDistance)
      ? 1 / (1 + Math.max(0, normalizedDistance)) : 1;
    return {
      ...relationship,
      jointCenter: relationship.jointWeightTotal > 0
        ? [relationship.jointX / relationship.jointWeightTotal,
          relationship.jointY / relationship.jointWeightTotal,
          relationship.jointZ / relationship.jointWeightTotal]
        : null,
      containment: denominator > 0 ? relationship.minOverlap / denominator : 0,
      jaccard: jaccardDenominator > 0
        ? relationship.minOverlap / jaccardDenominator : 0,
      centerDistance: distance,
      normalizedDistance,
      treeEdgeScore: (denominator > 0
        ? relationship.minOverlap / denominator : 0) * distancePenalty,
    };
  });
  return {
    nodes,
    relationships,
    boundingSphereRadius: Number.isFinite(radius) && radius > 0
      ? radius : sourceRadius,
    evidenceMode: 'surface',
    triangleCount: topology.triangleCount,
    validTriangleCount: topology.validTriangleCount,
    degenerateTriangleCount: topology.degenerateTriangleCount,
    invalidTriangleCount: topology.invalidTriangleCount,
    totalSurfaceArea: topology.totalSurfaceArea,
    measuredVertexCount: measuredVertices.size,
    zeroMeasureVertexCount: topology.positionCount - measuredVertices.size,
    fallbackReason: null,
  };
}

export function buildInfluenceNodes(
    baselinePositions, indices, weights, influenceCount, boneIds = null,
    options = {}) {
  const vertexCount = compactVertexCount(indices, weights, influenceCount);
  const vertexMeasure = options?.vertexMeasure;
  const hasMeasure = vertexMeasure !== null && vertexMeasure !== undefined;
  const requested = boneIds === null || boneIds === undefined
    ? null : new Set([...boneIds].map(Number));
  const entries = new Map();
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const influences = positiveInfluencesForVertex(
      indices, weights, influenceCount, vertex);
    influences.forEach((weight, boneId) => {
      if (requested && !requested.has(boneId)) return;
      const measure = vertexEvidenceMeasure(vertexMeasure, vertex);
      const entry = entries.get(boneId) || {
        boneId,
        totalWeight: 0,
        affectedVertexCount: 0,
        affectedMeasure: hasMeasure ? 0 : null,
        maxVertexWeight: 0,
        weightedX: 0,
        weightedY: 0,
        weightedZ: 0,
        squaredPositionWeight: 0,
        positionWeight: 0,
      };
      entry.totalWeight += measure * weight;
      entry.affectedVertexCount += 1;
      if (hasMeasure) entry.affectedMeasure += measure;
      entry.maxVertexWeight = Math.max(entry.maxVertexWeight, weight);
      if (baselinePositions
          && baselinePositions.length >= vertex * 3 + 3) {
        const offset = vertex * 3;
        const x = Number(baselinePositions[offset]);
        const y = Number(baselinePositions[offset + 1]);
        const z = Number(baselinePositions[offset + 2]);
        if ([x, y, z].every(Number.isFinite)) {
          entry.weightedX += x * measure * weight;
          entry.weightedY += y * measure * weight;
          entry.weightedZ += z * measure * weight;
          entry.squaredPositionWeight += (
            x * x + y * y + z * z) * measure * weight;
          entry.positionWeight += measure * weight;
        }
      }
      entries.set(boneId, entry);
    });
  }

  const orderedIds = requested ? [...requested] : [...entries.keys()];
  return orderedIds.filter(boneId => entries.has(boneId)).map(boneId => {
    const entry = entries.get(boneId);
    const nodeCenter = entry.positionWeight > 0
      ? [entry.weightedX / entry.positionWeight,
        entry.weightedY / entry.positionWeight,
        entry.weightedZ / entry.positionWeight]
      : [0, 0, 0];
    const weightedRadius = entry.positionWeight > 0
      ? Math.sqrt(Math.max(0,
        entry.squaredPositionWeight / entry.positionWeight
        - (nodeCenter[0] ** 2 + nodeCenter[1] ** 2
          + nodeCenter[2] ** 2)))
      : null;
    return {
      boneId: entry.boneId,
      totalWeight: entry.totalWeight,
      affectedVertexCount: entry.affectedVertexCount,
      affectedMeasure: entry.affectedMeasure,
      maxVertexWeight: entry.maxVertexWeight,
      weightedCenter: nodeCenter,
      weightedRadius,
    };
  });
}

function pairKey(boneA, boneB) {
  return boneA < boneB ? `${boneA}:${boneB}` : `${boneB}:${boneA}`;
}

function pairIds(boneA, boneB) {
  return boneA < boneB ? [boneA, boneB] : [boneB, boneA];
}

function centerDistance(centerA, centerB) {
  if (!centerA || !centerB || centerA.length < 3 || centerB.length < 3) {
    return null;
  }
  return Math.hypot(
    Number(centerA[0]) - Number(centerB[0]),
    Number(centerA[1]) - Number(centerB[1]),
    Number(centerA[2]) - Number(centerB[2]));
}

/** Build overlap evidence and an overlap-derived pivot for every bone pair. */
export function buildInfluenceRelationships(
    baselinePositions, indices, weights, influenceCount, nodes,
    boundingSphereRadius = null, options = {}) {
  const nodeById = new Map((nodes || []).map(node => [
    Number(node.boneId), node]));
  const vertexCount = compactVertexCount(indices, weights, influenceCount);
  const vertexMeasure = options?.vertexMeasure;
  const hasMeasure = vertexMeasure !== null && vertexMeasure !== undefined;
  const relationships = new Map();
  const ids = [];
  const mergedWeights = [];
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    ids.length = 0;
    mergedWeights.length = 0;
    const start = vertex * influenceCount;
    for (let influence = 0; influence < influenceCount; influence += 1) {
      const boneId = Number(indices[start + influence]);
      const weight = Number(weights[start + influence]);
      if (!nodeById.has(boneId) || !Number.isFinite(weight) || weight <= 0) {
        continue;
      }
      const existing = ids.indexOf(boneId);
      if (existing >= 0) mergedWeights[existing] += weight;
      else {
        ids.push(boneId);
        mergedWeights.push(weight);
      }
    }
    for (let left = 0; left < ids.length; left += 1) {
      for (let right = left + 1; right < ids.length; right += 1) {
        const [boneA, boneB] = pairIds(ids[left], ids[right]);
        const key = pairKey(boneA, boneB);
        const weightA = mergedWeights[left];
        const weightB = mergedWeights[right];
        const jointWeight = weightA * weightB;
        const measure = vertexEvidenceMeasure(vertexMeasure, vertex);
        const relationship = relationships.get(key) || {
          boneA,
          boneB,
          sharedVertexCount: 0,
          sharedMeasure: hasMeasure ? 0 : null,
          minOverlap: 0,
          productOverlap: 0,
          jointWeightTotal: 0,
          jointX: 0,
          jointY: 0,
          jointZ: 0,
        };
        relationship.sharedVertexCount += 1;
        if (hasMeasure) relationship.sharedMeasure += measure;
        relationship.minOverlap += measure * Math.min(weightA, weightB);
        relationship.productOverlap += measure * jointWeight;
        if (baselinePositions && baselinePositions.length >= vertex * 3 + 3
            && Number.isFinite(jointWeight) && jointWeight > 0) {
          const offset = vertex * 3;
          const x = Number(baselinePositions[offset]);
          const y = Number(baselinePositions[offset + 1]);
          const z = Number(baselinePositions[offset + 2]);
          if ([x, y, z].every(Number.isFinite)) {
            relationship.jointWeightTotal += measure * jointWeight;
            relationship.jointX += x * measure * jointWeight;
            relationship.jointY += y * measure * jointWeight;
            relationship.jointZ += z * measure * jointWeight;
          }
        }
        relationships.set(key, relationship);
      }
    }
  }

  const radius = Number(boundingSphereRadius);
  return [...relationships.values()].map(relationship => {
    const nodeA = nodeById.get(relationship.boneA);
    const nodeB = nodeById.get(relationship.boneB);
    const supportA = Number(nodeA?.totalWeight) || 0;
    const supportB = Number(nodeB?.totalWeight) || 0;
    const containmentDenominator = Math.min(supportA, supportB);
    const jaccardDenominator = supportA + supportB
      - relationship.minOverlap;
    const distance = centerDistance(
      nodeA?.weightedCenter, nodeB?.weightedCenter);
    const jointCenter = relationship.jointWeightTotal > 0
      ? [relationship.jointX / relationship.jointWeightTotal,
        relationship.jointY / relationship.jointWeightTotal,
        relationship.jointZ / relationship.jointWeightTotal]
      : null;
    return {
      ...relationship,
      jointCenter,
      containment: containmentDenominator > 0
        ? relationship.minOverlap / containmentDenominator : 0,
      jaccard: jaccardDenominator > 0
        ? relationship.minOverlap / jaccardDenominator : 0,
      centerDistance: distance,
      normalizedDistance: distance !== null && radius > 0
        ? distance / radius : null,
    };
  });
}

function relationshipSort(a, b) {
  return (Number(b.containment) || 0) - (Number(a.containment) || 0)
    || (Number(b.jaccard) || 0) - (Number(a.jaccard) || 0)
    || (Number(a.normalizedDistance ?? Infinity)
      - Number(b.normalizedDistance ?? Infinity))
    || String(a.boneA).localeCompare(String(b.boneA))
    || String(a.boneB).localeCompare(String(b.boneB));
}

function treeEdgeScore(edge) {
  return Number(edge.treeEdgeScore ?? edge.score
    ?? edge.containment ?? edge.jaccard ?? 0) || 0;
}

function treeEdgeCompare(a, b) {
  return treeEdgeScore(b) - treeEdgeScore(a) || relationshipSort(a, b);
}

export function candidateRelationshipEdges(graph, options = {}) {
  const minSharedVertexCount = Number(options.minSharedVertexCount ?? 1);
  const containmentThreshold = Number(
    options.containmentThreshold ?? CANDIDATE_CONTAINMENT_THRESHOLD);
  const jaccardThreshold = Number(
    options.jaccardThreshold ?? CANDIDATE_JACCARD_THRESHOLD);
  const surfaceEvidence = graph?.evidenceMode === 'surface';
  return (graph?.relationships || [])
    .filter(relationship => (surfaceEvidence
      ? Number(relationship.productOverlap) > 0
      : relationship.sharedVertexCount >= minSharedVertexCount)
      && (relationship.containment >= containmentThreshold
        || relationship.jaccard >= jaccardThreshold))
    .map(relationship => {
      const normalizedDistance = Number(relationship.normalizedDistance);
      const distancePenalty = Number.isFinite(normalizedDistance)
        ? 1 / (1 + Math.max(0, normalizedDistance)) : 1;
      return {
        ...relationship,
        treeEdgeScore: relationship.treeEdgeScore
          ?? (relationship.containment * distancePenalty),
      };
    })
    .sort(relationshipSort);
}

export function buildMaximumSpanningTree(nodes, edges) {
  const nodeIds = [...new Set((nodes || []).map(node => Number(
    node?.boneId ?? node)).filter(Number.isFinite))];
  const parent = new Map(nodeIds.map(id => [id, id]));
  const rank = new Map(nodeIds.map(id => [id, 0]));
  const find = id => {
    let root = id;
    while (parent.get(root) !== root) root = parent.get(root);
    while (parent.get(id) !== id) {
      const next = parent.get(id);
      parent.set(id, root);
      id = next;
    }
    return root;
  };
  const union = (left, right) => {
    let rootLeft = find(left);
    let rootRight = find(right);
    if (rootLeft === rootRight) return false;
    if (rank.get(rootLeft) < rank.get(rootRight)) {
      [rootLeft, rootRight] = [rootRight, rootLeft];
    }
    parent.set(rootRight, rootLeft);
    if (rank.get(rootLeft) === rank.get(rootRight)) {
      rank.set(rootLeft, rank.get(rootLeft) + 1);
    }
    return true;
  };
  const orderedEdges = [...edges || []].sort(treeEdgeCompare);
  const selected = [];
  for (const edge of orderedEdges) {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!parent.has(boneA) || !parent.has(boneB) || boneA === boneB) continue;
    if (union(boneA, boneB)) selected.push(edge);
  }
  const components = new Map();
  nodeIds.forEach(id => {
    const root = find(id);
    const component = components.get(root) || [];
    component.push(id);
    components.set(root, component);
  });
  return {edges: selected, components: [...components.values()]};
}

export function orientTree(treeEdges, rootId) {
  const adjacency = new Map();
  const add = (from, to) => {
    const neighbors = adjacency.get(from) || [];
    neighbors.push(to);
    adjacency.set(from, neighbors);
  };
  (treeEdges || []).forEach(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!Number.isFinite(boneA) || !Number.isFinite(boneB)) return;
    add(boneA, boneB);
    add(boneB, boneA);
  });
  const root = Number(rootId);
  if (Number.isFinite(root) && !adjacency.has(root)) adjacency.set(root, []);
  const parentById = {};
  const childrenById = {};
  const depthById = {};
  adjacency.forEach((neighbors, boneId) => {
    parentById[boneId] = null;
    childrenById[boneId] = [];
    depthById[boneId] = null;
    neighbors.sort((left, right) => Number(left) - Number(right));
  });
  if (Number.isFinite(root)) {
    const queue = [root];
    depthById[root] = 0;
    while (queue.length) {
      const current = queue.shift();
      const depth = depthById[current];
      (adjacency.get(current) || []).forEach(neighbor => {
        if (depthById[neighbor] !== null) return;
        parentById[neighbor] = current;
        childrenById[current].push(neighbor);
        depthById[neighbor] = depth + 1;
        queue.push(neighbor);
      });
    }
  }
  return {rootId: root, parentById, childrenById, depthById};
}

function connectedNodeComponents(nodes, edges) {
  const nodeIds = normalizedNodeIds(nodes);
  const nodeSet = new Set(nodeIds);
  const adjacency = new Map(nodeIds.map(id => [id, []]));
  (edges || []).forEach(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!nodeSet.has(boneA) || !nodeSet.has(boneB) || boneA === boneB) {
      return;
    }
    adjacency.get(boneA).push(boneB);
    adjacency.get(boneB).push(boneA);
  });
  const seen = new Set();
  const components = [];
  nodeIds.forEach(start => {
    if (seen.has(start)) return;
    const component = [];
    const queue = [start];
    seen.add(start);
    while (queue.length) {
      const current = queue.shift();
      component.push(current);
      for (const neighbor of adjacency.get(current) || []) {
        if (seen.has(neighbor)) continue;
        seen.add(neighbor);
        queue.push(neighbor);
      }
    }
    components.push(component);
  });
  return components;
}

function graphNodeList(graph) {
  return Array.isArray(graph) ? graph : graph?.nodes || [];
}

export function chooseSecondaryComponentRoot(
    component, graph, primaryComponent, primaryRootId) {
  const componentIds = normalizedNodeIds(component?.nodeIds || component);
  if (!componentIds.length) return null;
  const nodes = graphNodeList(graph);
  const nodeById = new Map(nodes.map(node => [Number(node.boneId), node]));
  const primaryIds = normalizedNodeIds(
    primaryComponent?.nodeIds || primaryComponent);
  const primaryNodes = primaryIds.map(id => nodeById.get(id)).filter(Boolean);
  if (!primaryNodes.length && nodeById.has(Number(primaryRootId))) {
    primaryNodes.push(nodeById.get(Number(primaryRootId)));
  }
  if (!primaryNodes.length) return componentIds[0];

  let bestId = componentIds[0];
  let bestDistance = Infinity;
  componentIds.forEach((id, index) => {
    const node = nodeById.get(id);
    let nearest = Infinity;
    primaryNodes.forEach(primary => {
      const distance = centerDistance(
        node?.weightedCenter, primary?.weightedCenter);
      if (distance !== null) nearest = Math.min(nearest, distance);
    });
    if (nearest < bestDistance || (nearest === bestDistance && index === 0)) {
      bestId = id;
      bestDistance = nearest;
    }
  });
  return bestId;
}

function completeOrientation(nodeIds, orientation) {
  nodeIds.forEach(id => {
    if (!Object.prototype.hasOwnProperty.call(orientation.parentById, id)) {
      orientation.parentById[id] = null;
      orientation.childrenById[id] = [];
      orientation.depthById[id] = null;
    }
  });
  return orientation;
}

/** Orient an already selected forest while preserving disconnected components. */
export function orientForest(nodes, treeEdges, primaryRootId, options = {}) {
  const supplied = options.components;
  const rawComponents = supplied?.length
    ? supplied.map(component => component.nodeIds || component)
    : connectedNodeComponents(nodes, treeEdges);
  const components = rawComponents.map(component => normalizedNodeIds(component));
  const requestedRoot = Number(primaryRootId);
  let primaryComponentId = components.findIndex(component =>
    component.includes(requestedRoot));
  if (primaryComponentId < 0 && components.length) primaryComponentId = 0;

  const componentByBoneId = {};
  components.forEach((nodeIds, componentId) => {
    nodeIds.forEach(id => { componentByBoneId[id] = componentId; });
  });
  const rootOverrides = options.secondaryRootByComponent;
  const forestComponents = components.map((nodeIds, componentId) => {
    const primary = componentId === primaryComponentId;
    let rootId = primary && nodeIds.includes(requestedRoot)
      ? requestedRoot : null;
    if (rootId === null && primary) rootId = nodeIds[0] ?? null;
    if (rootId === null) {
      const override = rootOverrides instanceof Map
        ? Number(rootOverrides.get(componentId))
        : Number(rootOverrides?.[componentId]);
      rootId = nodeIds.includes(override) ? override
        : chooseSecondaryComponentRoot(
          nodeIds, nodes, components[primaryComponentId] || [], requestedRoot);
    }
    const nodeSet = new Set(nodeIds);
    const componentEdges = (treeEdges || []).filter(edge =>
      nodeSet.has(Number(edge.boneA)) && nodeSet.has(Number(edge.boneB)));
    const orientation = completeOrientation(
      nodeIds, orientTree(componentEdges, rootId));
    const depths = Object.values(orientation.depthById)
      .filter(depth => depth !== null).map(Number);
    return {
      componentId,
      nodeIds,
      rootId,
      parentById: orientation.parentById,
      childrenById: orientation.childrenById,
      depthById: orientation.depthById,
      edgeCount: componentEdges.length,
      maxDepth: Math.max(0, ...depths),
      primary,
    };
  });
  const primary = forestComponents[primaryComponentId];
  return {
    primaryRootId: primary?.rootId ?? null,
    primaryComponentId: primaryComponentId < 0 ? null : primaryComponentId,
    components: forestComponents,
    componentByBoneId,
  };
}

function componentAdjacency(nodeIds, edges) {
  const nodeSet = new Set(nodeIds);
  const adjacency = new Map(nodeIds.map(id => [id, []]));
  (edges || []).forEach(edge => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!nodeSet.has(boneA) || !nodeSet.has(boneB) || boneA === boneB) return;
    adjacency.get(boneA).push(boneB);
    adjacency.get(boneB).push(boneA);
  });
  adjacency.forEach(neighbors => neighbors.sort((a, b) => a - b));
  return adjacency;
}

function distanceFrom(adjacency, startId) {
  const distance = new Map([[startId, 0]]);
  const queue = [startId];
  while (queue.length) {
    const current = queue.shift();
    for (const neighbor of adjacency.get(current) || []) {
      if (distance.has(neighbor)) continue;
      distance.set(neighbor, distance.get(current) + 1);
      queue.push(neighbor);
    }
  }
  return distance;
}

function nodeEvidenceScore(node, adjacency, edges) {
  const distances = distanceFrom(adjacency, node.boneId);
  const distanceSum = [...distances.values()].reduce((sum, value) => sum + value, 0);
  const centrality = 1 / (1 + distanceSum);
  const edgeStrength = (edges || []).filter(edge =>
    Number(edge.boneA) === node.boneId || Number(edge.boneB) === node.boneId)
    .reduce((sum, edge) => sum + treeEdgeScore(edge), 0);
  // Each term is source-local.  The raw values are normalized by the caller's
  // component maxima so adding a high-ID bone cannot change the result.
  return {centrality, affected: Number(
    node.affectedMeasure ?? node.affectedVertexCount) || 0,
    weight: Number(node.totalWeight) || 0, degree: (adjacency.get(node.boneId) || []).length,
    edgeStrength};
}

function chooseRoot(nodeIds, nodes, edges) {
  const nodeById = new Map((nodes || []).map(node => [Number(node.boneId), node]));
  const adjacency = componentAdjacency(nodeIds, edges);
  const evidence = nodeIds.map(id => ({
    id,
    ...nodeEvidenceScore(nodeById.get(id) || {boneId: id}, adjacency, edges),
  }));
  const max = field => Math.max(...evidence.map(item => Number(item[field]) || 0), 1);
  const score = item => (
    4 * item.centrality / max('centrality')
    + 2 * item.affected / max('affected')
    + 2 * item.weight / max('weight')
    + item.degree / max('degree')
    + item.edgeStrength / max('edgeStrength'));
  evidence.forEach(item => { item.score = score(item); });
  evidence.sort((left, right) => right.score - left.score
    || right.centrality - left.centrality
    || right.affected - left.affected
    || right.weight - left.weight
    || right.degree - left.degree
    || right.edgeStrength - left.edgeStrength
    || String(nodeById.get(left.id)?.weightedCenter || '')
      .localeCompare(String(nodeById.get(right.id)?.weightedCenter || ''))
    || left.id - right.id);
  return evidence[0]?.id ?? null;
}

function normalizedNodeIds(nodes) {
  return [...new Set((nodes || []).map(node => Number(
    node?.boneId ?? node)).filter(Number.isFinite))];
}

/** Build every accepted relationship into a persistent inferred forest. */
export function buildInferredRigForest(graph, options = {}) {
  const nodes = graph?.nodes || [];
  const nodeIds = normalizedNodeIds(nodes);
  const edges = options.edges || candidateRelationshipEdges(graph, options);
  const tree = buildMaximumSpanningTree(nodes, edges);
  const rootOverrides = options.rootOverrides || options.rootByComponent;
  const components = tree.components.map((nodeIdsForComponent, componentId) => {
    const nodeSet = new Set(nodeIdsForComponent);
    const componentEdges = tree.edges.filter(edge =>
      nodeSet.has(Number(edge.boneA)) && nodeSet.has(Number(edge.boneB)));
    let rootId = null;
    if (rootOverrides instanceof Map) rootId = Number(rootOverrides.get(componentId));
    else rootId = Number(rootOverrides?.[componentId]);
    if (!nodeSet.has(rootId)) rootId = chooseRoot(
      nodeIdsForComponent, nodes, componentEdges);
    const orientation = orientTree(componentEdges, rootId);
    const depths = Object.values(orientation.depthById)
      .filter(depth => depth !== null).map(Number);
    return {
      componentId,
      nodeIds: [...nodeIdsForComponent],
      rootId,
      parentById: orientation.parentById,
      childrenById: orientation.childrenById,
      depthById: orientation.depthById,
      edges: componentEdges,
      edgeCount: componentEdges.length,
      maxDepth: Math.max(0, ...depths),
    };
  });
  const componentByBoneId = {};
  components.forEach(component => component.nodeIds.forEach(id => {
    componentByBoneId[id] = component.componentId;
  }));
  return {
    components,
    componentByBoneId,
    edges: tree.edges,
    nodeIds,
  };
}

/** Aggregate per-mesh evidence while preserving weighted joint positions. */
export function aggregateInfluenceGraphs(graphs) {
  const inputGraphs = (graphs || []).filter(Boolean);
  const evidenceModes = new Set(inputGraphs.map(graph =>
    graph.evidenceMode || 'vertex'));
  if (evidenceModes.size > 1) {
    throw new Error('Cannot aggregate incompatible Rig evidence modes.');
  }
  const evidenceMode = [...evidenceModes][0] || 'vertex';
  const diagnosticTotal = field => inputGraphs.reduce((sum, graph) =>
    sum + (Number(graph?.[field]) || 0), 0);
  const fallbackReasons = [...new Set(inputGraphs
    .map(graph => graph?.fallbackReason).filter(Boolean))];
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
        boneId, totalWeight: 0, affectedVertexCount: 0, affectedMeasure: 0,
        maxVertexWeight: 0, weightedX: 0, weightedY: 0, weightedZ: 0,
        secondMoment: 0,
      };
      entry.totalWeight += totalWeight;
      entry.affectedVertexCount += Number(node.affectedVertexCount) || 0;
      if (evidenceMode === 'surface') {
        entry.affectedMeasure += Number(node.affectedMeasure) || 0;
      }
      entry.maxVertexWeight = Math.max(
        entry.maxVertexWeight, Number(node.maxVertexWeight) || 0);
      entry.weightedX += totalWeight * (Number(center[0]) || 0);
      entry.weightedY += totalWeight * (Number(center[1]) || 0);
      entry.weightedZ += totalWeight * (Number(center[2]) || 0);
      entry.secondMoment += totalWeight * (radius * radius
        + (Number(center[0]) || 0) ** 2
        + (Number(center[1]) || 0) ** 2
        + (Number(center[2]) || 0) ** 2);
      nodeTotals.set(boneId, entry);
    }
    for (const relationship of graph?.relationships || []) {
      const boneA = Number(relationship.boneA);
      const boneB = Number(relationship.boneB);
      if (!Number.isFinite(boneA) || !Number.isFinite(boneB)) continue;
      const key = pairKey(boneA, boneB);
      const entry = relationshipTotals.get(key) || {
        boneA: Math.min(boneA, boneB), boneB: Math.max(boneA, boneB),
        sharedVertexCount: 0, sharedMeasure: 0,
        minOverlap: 0, productOverlap: 0,
        jointWeightTotal: 0, jointX: 0, jointY: 0, jointZ: 0,
      };
      entry.sharedVertexCount += Number(relationship.sharedVertexCount) || 0;
      if (evidenceMode === 'surface') {
        entry.sharedMeasure += Number(relationship.sharedMeasure) || 0;
      }
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
  const nodes = [...nodeTotals.values()].map(entry => {
    const center = entry.totalWeight > 0 ? [
      entry.weightedX / entry.totalWeight,
      entry.weightedY / entry.totalWeight,
      entry.weightedZ / entry.totalWeight,
    ] : [0, 0, 0];
    const centerLengthSquared = center.reduce(
      (sum, value) => sum + value * value, 0);
    return {
      boneId: entry.boneId,
      totalWeight: entry.totalWeight,
      affectedVertexCount: entry.affectedVertexCount,
      affectedMeasure: evidenceMode === 'surface'
        ? entry.affectedMeasure : null,
      maxVertexWeight: entry.maxVertexWeight,
      weightedCenter: center,
      weightedRadius: Math.sqrt(Math.max(0,
        entry.secondMoment / entry.totalWeight - centerLengthSquared)),
    };
  }).sort((left, right) => left.boneId - right.boneId);
  const nodeById = new Map(nodes.map(node => [node.boneId, node]));
  const totalWeight = nodes.reduce((sum, node) => sum + node.totalWeight, 0);
  const sourceCenter = nodes.length ? nodes.reduce((sum, node) => [
    sum[0] + node.weightedCenter[0] * node.totalWeight,
    sum[1] + node.weightedCenter[1] * node.totalWeight,
    sum[2] + node.weightedCenter[2] * node.totalWeight,
  ], [0, 0, 0]).map(value => totalWeight > 0 ? value / totalWeight : 0)
    : [0, 0, 0];
  const radius = nodes.length ? Math.max(...nodes.map(node =>
    (centerDistance(node.weightedCenter, sourceCenter) || 0)
      + (Number(node.weightedRadius) || 0))) : null;
  const relationships = [...relationshipTotals.values()].map(relationship => {
    const nodeA = nodeById.get(relationship.boneA);
    const nodeB = nodeById.get(relationship.boneB);
    const supportA = Number(nodeA?.totalWeight) || 0;
    const supportB = Number(nodeB?.totalWeight) || 0;
    const denominator = Math.min(supportA, supportB);
    const jaccardDenominator = supportA + supportB - relationship.minOverlap;
    const distance = centerDistance(nodeA?.weightedCenter, nodeB?.weightedCenter);
    const normalizedDistance = distance !== null && radius > 0
      ? distance / radius : null;
    const distancePenalty = Number.isFinite(normalizedDistance)
      ? 1 / (1 + Math.max(0, normalizedDistance)) : 1;
    return {
      ...relationship,
      sharedMeasure: evidenceMode === 'surface'
        ? relationship.sharedMeasure : null,
      jointCenter: relationship.jointWeightTotal > 0
        ? [relationship.jointX / relationship.jointWeightTotal,
          relationship.jointY / relationship.jointWeightTotal,
          relationship.jointZ / relationship.jointWeightTotal]
        : null,
      containment: denominator > 0 ? relationship.minOverlap / denominator : 0,
      jaccard: jaccardDenominator > 0
        ? relationship.minOverlap / jaccardDenominator : 0,
      centerDistance: distance,
      normalizedDistance,
      treeEdgeScore: (denominator > 0
        ? relationship.minOverlap / denominator : 0) * distancePenalty,
    };
  });
  return {
    nodes,
    relationships,
    boundingSphereRadius: radius,
    evidenceMode,
    triangleCount: diagnosticTotal('triangleCount'),
    validTriangleCount: diagnosticTotal('validTriangleCount'),
    degenerateTriangleCount: diagnosticTotal('degenerateTriangleCount'),
    invalidTriangleCount: diagnosticTotal('invalidTriangleCount'),
    totalSurfaceArea: diagnosticTotal('totalSurfaceArea'),
    measuredVertexCount: diagnosticTotal('measuredVertexCount'),
    zeroMeasureVertexCount: diagnosticTotal('zeroMeasureVertexCount'),
    fallbackReason: fallbackReasons.length ? fallbackReasons.join(';') : null,
  };
}

export function jointPivotMap(forest, relationships) {
  const byPair = new Map((relationships || []).map(edge => [
    pairKey(Number(edge.boneA), Number(edge.boneB)), edge]));
  const pivots = new Map();
  (forest?.components || []).forEach(component => {
    Object.entries(component.parentById || {}).forEach(([childValue, parentValue]) => {
      if (parentValue === null || parentValue === undefined) return;
      const childId = Number(childValue);
      const parentId = Number(parentValue);
      if (!Number.isFinite(childId) || !Number.isFinite(parentId)) return;
      const pivot = byPair.get(pairKey(childId, parentId))?.jointCenter;
      if (pivot?.length >= 3) pivots.set(childId, [...pivot]);
    });
  });
  return pivots;
}
