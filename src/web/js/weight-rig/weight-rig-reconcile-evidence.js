// Source-local bone evidence and cooperative cross-source vertex proximity.

import { Quaternion, Vector3 } from 'three';

export const CROSS_SOURCE_STRONG_VERTEX_COUNT = 8;

export const CROSS_SOURCE_STRONG_WEIGHT_STRENGTH = 0.5;

export const EPSILON = 1e-8;

export function clockNow() {
  return typeof globalThis.performance?.now === 'function' ? globalThis.performance.now() : Date.now();
}

export function number(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

export function vectorFrom(value) {
  if (value?.isVector3) return value.clone();
  const values = Array.isArray(value) || ArrayBuffer.isView(value) ? value : [value?.x, value?.y, value?.z];
  if (!values || values.length < 3) return null;
  const vector = new Vector3(number(values[0], NaN), number(values[1], NaN), number(values[2], NaN));
  return vector.toArray().every(Number.isFinite) ? vector : null;
}

function vectorArray(value, fallback = [0, 0, 0]) {
  return vectorFrom(value)?.toArray() || [...fallback];
}

export function quaternionArray(value) {
  if (value?.isQuaternion) return value.clone().normalize().toArray();
  const values =
    Array.isArray(value) || ArrayBuffer.isView(value)
      ? [...value].slice(0, 4).map(Number)
      : [value?.x, value?.y, value?.z, value?.w].map(Number);
  return values.length === 4 && values.every(Number.isFinite)
    ? new Quaternion(...values).normalize().toArray()
    : [0, 0, 0, 1];
}

function mapValue(collection, boneId) {
  if (collection instanceof Map) {
    return collection.get(boneId) ?? collection.get(String(boneId));
  }
  return collection?.[boneId];
}

export function sourceBoneKey(sourceKey, boneId) {
  return `${String(sourceKey)}#bone=${Number(boneId)}`;
}

function componentFor(rig, boneId) {
  const componentId = mapValue(rig?.inferredForest?.componentByBoneId, boneId);
  const components = rig?.inferredForest?.components || [];
  return Number.isInteger(Number(componentId)) ? components[Number(componentId)] || null : null;
}

function parentFor(component, boneId) {
  const parent = mapValue(component?.parentById, boneId);
  if (parent === null || parent === undefined) return null;
  const id = Number(parent);
  return Number.isFinite(id) ? id : null;
}

function childrenFor(component, boneId) {
  const children = mapValue(component?.childrenById, boneId) || [];
  return children.map(Number).filter(Number.isFinite);
}

export function edgeScore(edge) {
  return number(edge?.treeEdgeScore ?? edge?.score ?? edge?.containment ?? edge?.jaccard, 0);
}

export function directionAvailable(evidence) {
  return !!evidence?.restDirection && evidence.directionSource !== 'canonical-y';
}

function anchorFor(parentId, center, pivot) {
  return parentId === null ? center : pivot || center;
}

function sourceAdjacencyByBoneId(rig) {
  const adjacency = new Map();
  const addEdge = (left, right) => {
    const leftNeighbors = adjacency.get(left) || new Set();
    const rightNeighbors = adjacency.get(right) || new Set();
    leftNeighbors.add(right);
    rightNeighbors.add(left);
    adjacency.set(left, leftNeighbors);
    adjacency.set(right, rightNeighbors);
  };
  (rig?.inferredForest?.components || []).forEach((component) => {
    Object.entries(component.parentById || {}).forEach(([childId, parentId]) => {
      if (parentId !== null && parentId !== undefined) {
        addEdge(Number(childId), Number(parentId));
      }
    });
    Object.entries(component.childrenById || {}).forEach(([parentId, children]) =>
      (children || []).forEach((childId) => addEdge(Number(parentId), Number(childId))),
    );
  });
  return new Map(
    [...adjacency.entries()].map(([boneId, neighbors]) => [
      Number(boneId),
      [...neighbors].filter(Number.isFinite).sort((left, right) => left - right),
    ]),
  );
}

export function collectSourceBoneEvidence(rig) {
  const result = new Map();
  const sourceAdjacency = sourceAdjacencyByBoneId(rig);
  const boneIds = [...(rig?.boneIds || rig?.influenceGraph?.nodes || [])]
    .map((value) => Number(value?.boneId ?? value))
    .filter(Number.isFinite)
    .sort((left, right) => left - right);
  const nodes = new Map((rig?.influenceGraph?.nodes || []).map((node) => [Number(node.boneId), node]));
  boneIds.forEach((boneId) => {
    const component = componentFor(rig, boneId);
    const parentBoneId = parentFor(component, boneId);
    const childBoneIds = childrenFor(component, boneId);
    const neighborBoneIds = sourceAdjacency.get(boneId) || [];
    const node = nodes.get(boneId);
    const center = vectorArray(mapValue(rig?.centerByBoneId, boneId) || node?.weightedCenter);
    const pivot = vectorFrom(mapValue(rig?.jointPivotByBoneId, boneId));
    const direction = vectorFrom(mapValue(rig?.restDirectionByBoneId, boneId));
    const key = sourceBoneKey(rig.sourceKey, boneId);
    result.set(key, {
      sourceKey: String(rig.sourceKey),
      boneId,
      sourceBoneKey: key,
      weightedCenter: center,
      weightedRadius: number(node?.weightedRadius),
      jointPivot: pivot?.toArray() || null,
      restAnchor: anchorFor(parentBoneId, center, pivot?.toArray() || null),
      restDirection: direction?.normalize().toArray() || null,
      directionSource: mapValue(rig?.restFrameEvidenceByBoneId, boneId)?.directionSource || null,
      restFrame: quaternionArray(mapValue(rig?.restFrameByBoneId, boneId)),
      parentBoneId,
      childBoneIds,
      neighborBoneIds,
      degree: childBoneIds.length + (parentBoneId === null ? 0 : 1),
      isRoot: parentBoneId === null,
      totalWeight: number(node?.totalWeight),
      affectedVertexCount: number(node?.affectedVertexCount),
    });
  });
  return result;
}

export function prepareSourceBoneEvidence(evidenceByKey) {
  evidenceByKey.forEach((evidence) => {
    const weightedCenter = vectorFrom(evidence.weightedCenter);
    const restAnchor = vectorFrom(evidence.restAnchor);
    const restDirection = vectorFrom(evidence.restDirection);
    const jointPivot = vectorFrom(evidence.jointPivot);
    Object.defineProperties(evidence, {
      _weightedCenterVector: {
        value: weightedCenter ? Object.freeze(weightedCenter) : null,
        enumerable: false,
      },
      _restAnchorVector: {
        value: restAnchor ? Object.freeze(restAnchor) : null,
        enumerable: false,
      },
      _restDirectionVector: {
        value: restDirection ? Object.freeze(restDirection) : null,
        enumerable: false,
      },
      _jointPivotVector: {
        value: jointPivot ? Object.freeze(jointPivot) : null,
        enumerable: false,
      },
      _neighborBoneKeys: {
        value: Object.freeze(
          (evidence.neighborBoneIds || []).map((boneId) => sourceBoneKey(evidence.sourceKey, boneId)),
        ),
        enumerable: false,
      },
    });
  });
  return evidenceByKey;
}

export function evidenceVector(evidence, property, fallback) {
  return evidence?.[property]?.clone() || vectorFrom(evidence?.[fallback]);
}

export function modelReferenceRadius(evidence) {
  const points = [...evidence.values()]
    .map((item) => evidenceVector(item, '_weightedCenterVector', 'weightedCenter'))
    .filter(Boolean);
  if (!points.length) return 1;
  const center = points.reduce((sum, point) => sum.add(point), new Vector3()).multiplyScalar(1 / points.length);
  return Math.max(EPSILON, ...points.map((point) => point.distanceTo(center)));
}

export function clamp(value, minimum = 0, maximum = 1) {
  return Math.max(minimum, Math.min(maximum, value));
}

export function crossPairKey(leftSourceBoneKey, rightSourceBoneKey) {
  return [leftSourceBoneKey, rightSourceBoneKey].sort().join('|');
}

function vertexEvidenceDescriptors(rig) {
  return (rig?.vertexEvidence || [])
    .map((entry, entryIndex) => {
      const positions = entry.positions || entry.baselinePositions;
      const indices = entry.indices;
      const weights = entry.weights;
      const influenceCount = Number(entry.influenceCount);
      if (!positions || !indices || !weights || !Number.isInteger(influenceCount) || influenceCount <= 0) return null;
      return {
        entry,
        entryIndex,
        positions,
        indices,
        weights,
        influenceCount,
        vertexCount: Math.floor(
          Math.min(positions.length / 3, indices.length / influenceCount, weights.length / influenceCount),
        ),
      };
    })
    .filter(Boolean);
}

function appendVertexSample(samples, descriptor, vertexIndex) {
  const { entry, entryIndex, positions, indices, weights, influenceCount } = descriptor;
  const offset = vertexIndex * 3;
  const point = vectorFrom([positions[offset], positions[offset + 1], positions[offset + 2]]);
  if (!point) return;
  const influenceMap = new Map();
  const start = vertexIndex * influenceCount;
  for (let influenceIndex = 0; influenceIndex < influenceCount; influenceIndex += 1) {
    const boneId = Number(indices[start + influenceIndex]);
    const weight = Number(weights[start + influenceIndex]);
    if (!Number.isInteger(boneId) || boneId < 0 || !Number.isFinite(weight) || weight <= 0) continue;
    influenceMap.set(boneId, (influenceMap.get(boneId) || 0) + weight);
  }
  if (!influenceMap.size) return;
  samples.push({
    sampleKey: `${String(entry.meshKey || entryIndex)}#vertex=${vertexIndex}`,
    point,
    influences: [...influenceMap.entries()].map(([boneId, weight]) => ({
      boneId,
      weight,
    })),
  });
}

function sortVertexSamples(samples) {
  return samples.sort((left, right) => left.sampleKey.localeCompare(right.sampleKey));
}

async function vertexSamplesForRigCooperative(rig, checkpoint) {
  const samples = [];
  for (const descriptor of vertexEvidenceDescriptors(rig)) {
    for (let vertexIndex = 0; vertexIndex < descriptor.vertexCount; vertexIndex += 1) {
      if ((vertexIndex & 255) === 0) await checkpoint();
      appendVertexSample(samples, descriptor, vertexIndex);
    }
  }
  sortVertexSamples(samples);
  await checkpoint();
  return samples;
}

function cellKey(point, cellSize) {
  return [point.x, point.y, point.z].map((value) => Math.floor(value / cellSize)).join(':');
}

function considerNearestSample(sample, candidate, matchDistance, best) {
  const deltaX = sample.point.x - candidate.point.x;
  const deltaY = sample.point.y - candidate.point.y;
  const deltaZ = sample.point.z - candidate.point.z;
  const distanceSquared = deltaX * deltaX + deltaY * deltaY + deltaZ * deltaZ;
  let distance = null;
  if (distanceSquared > matchDistance * matchDistance && (distance = Math.sqrt(distanceSquared)) > matchDistance) {
    return best;
  }
  if (best && distanceSquared > best.distanceSquared) return best;
  distance ??= Math.sqrt(distanceSquared);
  if (distance > matchDistance) return best;
  if (
    !best ||
    distance < best.distance ||
    (distance === best.distance && candidate.sampleKey.localeCompare(best.sample.sampleKey) < 0)
  ) {
    return { sample: candidate, distance, distanceSquared };
  }
  return best;
}

async function nearestSampleCooperative(sample, cells, cellSize, matchDistance, checkpoint) {
  const [x, y, z] = cellKey(sample.point, cellSize).split(':').map(Number);
  let best = null;
  let candidateCount = 0;
  for (let dx = -1; dx <= 1; dx += 1) {
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dz = -1; dz <= 1; dz += 1) {
        const entries = cells.get(`${x + dx}:${y + dy}:${z + dz}`);
        if (!entries) continue;
        for (const candidate of entries) {
          if ((candidateCount++ & 255) === 0) await checkpoint();
          best = considerNearestSample(sample, candidate, matchDistance, best);
        }
      }
    }
  }
  return best;
}

function addSpatialSample(cells, sample, cellSize) {
  const key = cellKey(sample.point, cellSize);
  const entries = cells.get(key) || [];
  entries.push(sample);
  cells.set(key, entries);
}

async function buildSpatialCellsCooperative(samples, cellSize, checkpoint) {
  const cells = new Map();
  for (let index = 0; index < samples.length; index += 1) {
    if ((index & 255) === 0) await checkpoint();
    addSpatialSample(cells, samples[index], cellSize);
  }
  await checkpoint();
  return cells;
}

function addMutualCrossSourceEvidence(evidence, leftRig, rightRig, leftSample, rightSample, distance, matchDistance) {
  const confidence = clamp(1 - distance / matchDistance);
  for (const leftInfluence of leftSample.influences) {
    for (const rightInfluence of rightSample.influences) {
      const leftKey = sourceBoneKey(leftRig.sourceKey, leftInfluence.boneId);
      const rightKey = sourceBoneKey(rightRig.sourceKey, rightInfluence.boneId);
      const key = crossPairKey(leftKey, rightKey);
      const record = evidence.get(key) || {
        leftSourceBoneKey: leftKey,
        rightSourceBoneKey: rightKey,
        matchedVertexCount: 0,
        weightedMatchStrength: 0,
        leftMass: 0,
        rightMass: 0,
        matchedVertexKeys: new Set(),
      };
      const leftMass = leftInfluence.weight * confidence;
      const rightMass = rightInfluence.weight * confidence;
      const vertexPairKey = `${leftSample.sampleKey}|${rightSample.sampleKey}`;
      if (!record.matchedVertexKeys.has(vertexPairKey)) {
        record.matchedVertexKeys.add(vertexPairKey);
        record.matchedVertexCount += 1;
      }
      record.weightedMatchStrength += leftInfluence.weight * rightInfluence.weight * confidence;
      record.leftMass += leftMass;
      record.rightMass += rightMass;
      evidence.set(key, record);
    }
  }
}

async function finishCrossSourceEvidence(evidence, checkpoint) {
  let recordIndex = 0;
  for (const record of evidence.values()) {
    if ((recordIndex++ & 127) === 0) await checkpoint();
    const minimumMass = Math.max(EPSILON, Math.min(record.leftMass, record.rightMass));
    const unionMass = Math.max(EPSILON, record.leftMass + record.rightMass - record.weightedMatchStrength);
    record.crossContainment = clamp(record.weightedMatchStrength / minimumMass);
    record.crossJaccard = clamp(record.weightedMatchStrength / unionMass);
    record.overlapScore = clamp(record.crossContainment * 0.55 + record.crossJaccard * 0.45);
    record.supportReliability = Math.min(
      clamp(record.matchedVertexCount / CROSS_SOURCE_STRONG_VERTEX_COUNT),
      clamp(record.weightedMatchStrength / CROSS_SOURCE_STRONG_WEIGHT_STRENGTH),
    );
    delete record.matchedVertexKeys;
  }
  return evidence;
}

async function crossSourceWeightEvidenceCooperative(
  leftRig,
  rightRig,
  referenceRadius,
  leftSampleList,
  rightSampleList,
  leftCellMap,
  rightCellMap,
  checkpoint,
) {
  if (!leftSampleList.length || !rightSampleList.length) return new Map();
  const matchDistance = crossSourceMatchDistance(referenceRadius);
  const cellSize = matchDistance;
  const nearestLeftByRight = new Map();
  const nearestRightByLeft = new Map();
  for (let index = 0; index < rightSampleList.length; index += 1) {
    if ((index & 127) === 0) await checkpoint();
    const rightSample = rightSampleList[index];
    const best = await nearestSampleCooperative(rightSample, leftCellMap, cellSize, matchDistance, checkpoint);
    if (best)
      nearestLeftByRight.set(rightSample, {
        leftSample: best.sample,
        distance: best.distance,
      });
  }
  for (let index = 0; index < leftSampleList.length; index += 1) {
    if ((index & 127) === 0) await checkpoint();
    const leftSample = leftSampleList[index];
    const best = await nearestSampleCooperative(leftSample, rightCellMap, cellSize, matchDistance, checkpoint);
    if (best)
      nearestRightByLeft.set(leftSample, {
        rightSample: best.sample,
        distance: best.distance,
      });
  }

  const evidence = new Map();
  let pairIndex = 0;
  for (const [rightSample, { leftSample, distance }] of nearestLeftByRight.entries()) {
    if ((pairIndex++ & 127) === 0) await checkpoint();
    const reverse = nearestRightByLeft.get(leftSample);
    if (!reverse || reverse.rightSample !== rightSample) continue;
    addMutualCrossSourceEvidence(evidence, leftRig, rightRig, leftSample, rightSample, distance, matchDistance);
  }
  return finishCrossSourceEvidence(evidence, checkpoint);
}

function orderedSourceRigs(sourceRigs) {
  return [...sourceRigs].sort((left, right) => String(left.sourceKey).localeCompare(String(right.sourceKey)));
}

function crossSourceMatchDistance(referenceRadius) {
  return Math.max(referenceRadius * 0.02, EPSILON);
}

export async function buildCrossSourceWeightEvidenceCooperative(
  sourceRigs,
  referenceRadius,
  checkpoint,
  timings = null,
) {
  const rigs = orderedSourceRigs(sourceRigs);
  if (rigs.length < 2) return new Map();
  const matchDistance = crossSourceMatchDistance(referenceRadius);
  const samplesBySourceKey = new Map();
  const cellsBySourceKey = new Map();
  for (const rig of rigs) {
    const sourceKey = String(rig.sourceKey);
    const sampleStartedAt = clockNow();
    const samples = await vertexSamplesForRigCooperative(rig, checkpoint);
    if (timings) timings.sampleBuildMs = (timings.sampleBuildMs || 0) + clockNow() - sampleStartedAt;
    const spatialStartedAt = clockNow();
    const cells = await buildSpatialCellsCooperative(samples, matchDistance, checkpoint);
    if (timings) timings.spatialIndexMs = (timings.spatialIndexMs || 0) + clockNow() - spatialStartedAt;
    samplesBySourceKey.set(sourceKey, samples);
    cellsBySourceKey.set(sourceKey, cells);
    await checkpoint();
  }
  const evidence = new Map();
  let pairIndex = 0;
  for (let leftIndex = 0; leftIndex < rigs.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < rigs.length; rightIndex += 1) {
      if (pairIndex++ % 2 === 0) await checkpoint();
      const leftRig = rigs[leftIndex];
      const rightRig = rigs[rightIndex];
      const matchStartedAt = clockNow();
      const pairEvidence = await crossSourceWeightEvidenceCooperative(
        leftRig,
        rightRig,
        referenceRadius,
        samplesBySourceKey.get(String(leftRig.sourceKey)),
        samplesBySourceKey.get(String(rightRig.sourceKey)),
        cellsBySourceKey.get(String(leftRig.sourceKey)),
        cellsBySourceKey.get(String(rightRig.sourceKey)),
        checkpoint,
      );
      if (timings) timings.crossSourceMatchMs = (timings.crossSourceMatchMs || 0) + clockNow() - matchStartedAt;
      pairEvidence.forEach((record, key) => evidence.set(key, record));
    }
  }
  return evidence;
}
