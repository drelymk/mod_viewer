// Source-local rest evidence used for joint identity and source hierarchy.

import { Quaternion, Vector3 } from 'three';

export const EPSILON = 1e-8;

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

export function edgeScore(edge) {
  return number(edge?.treeEdgeScore ?? edge?.score ?? edge?.containment ?? edge?.jaccard, 0);
}

function anchorFor(parentId, center, pivot) {
  return parentId === null ? center : pivot || center;
}

export function collectSourceBoneEvidence(rig) {
  const result = new Map();
  const boneIds = [...(rig?.boneIds || rig?.influenceGraph?.nodes || [])]
    .map((value) => Number(value?.boneId ?? value))
    .filter(Number.isFinite)
    .sort((left, right) => left - right);
  const nodes = new Map((rig?.influenceGraph?.nodes || []).map((node) => [Number(node.boneId), node]));
  boneIds.forEach((boneId) => {
    const component = componentFor(rig, boneId);
    const parentBoneId = parentFor(component, boneId);
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
      jointPivot: pivot?.toArray() || null,
      restAnchor: anchorFor(parentBoneId, center, pivot?.toArray() || null),
      restDirection: direction?.normalize().toArray() || null,
      restFrame: quaternionArray(mapValue(rig?.restFrameByBoneId, boneId)),
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
      _jointPivotVector: {
        value: jointPivot ? Object.freeze(jointPivot) : null,
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
