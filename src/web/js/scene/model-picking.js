// Shared viewport raycasting for model selection and one-shot tools.

import * as THREE from 'three';
import {getLooseParts} from '../mesh/loose-parts.js';

export const MAX_BOX_SELECTION_SAMPLES = 64;

function meshPickTargets(mesh) {
  const parts = getLooseParts(mesh);
  if (!parts.length) return [mesh];
  return mesh?.visible ? parts : [];
}

/** Return the same visible render targets used by single-mesh ray picking. */
export function modelPickTargets(meshes = []) {
  return [...(meshes || [])]
    .flatMap(meshPickTargets)
    .filter(mesh => mesh?.visible);
}

/** Return the first visible model intersection at a client-space point. */
export function raycastModelAtClientPoint({
  clientX, clientY, canvas, camera, meshes,
} = {}) {
  const rect = canvas?.getBoundingClientRect?.();
  const width = Number(rect?.width);
  const height = Number(rect?.height);
  if (!rect || !camera || width <= 0 || height <= 0) return null;

  const pointer = new THREE.Vector2(
    ((Number(clientX) - rect.left) / width) * 2 - 1,
    -((Number(clientY) - rect.top) / height) * 2 + 1);
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(pointer, camera);
  const visibleMeshes = modelPickTargets(meshes);
  return raycaster.intersectObjects(visibleMeshes, false)[0] || null;
}

/** Resolve a rendered face back to the source mesh's authored triangle ordinal. */
export function authoredTriangleOrdinal(mesh, faceIndex) {
  const face = Number(faceIndex);
  if (!mesh || !Number.isInteger(face) || face < 0) return null;
  const partTriangles = mesh.userData?.loosePartTriangles;
  if (Array.isArray(partTriangles)) {
    const triangle = Number(partTriangles[face]);
    return Number.isInteger(triangle) && triangle >= 0 ? triangle : null;
  }
  const geometry = mesh.geometry;
  const index = geometry?.index;
  const position = geometry?.getAttribute?.('position');
  const availableEntries = Number(index ? index.count : position?.count || 0);
  const drawStart = Math.max(0, Number(geometry?.drawRange?.start) || 0);
  const drawCount = Number(geometry?.drawRange?.count);
  const entryCount = Math.min(
    Math.max(0, availableEntries - drawStart),
    Number.isFinite(drawCount) ? Math.max(0, drawCount) : availableEntries,
  );
  if (face * 3 + 2 >= entryCount) return null;
  return Math.floor((drawStart + face * 3) / 3);
}

function normalizedSelectionRect(selectionRect) {
  if (!selectionRect) return null;
  const left = Math.min(Number(selectionRect.left), Number(selectionRect.right));
  const right = Math.max(Number(selectionRect.left), Number(selectionRect.right));
  const top = Math.min(Number(selectionRect.top), Number(selectionRect.bottom));
  const bottom = Math.max(Number(selectionRect.top), Number(selectionRect.bottom));
  return [left, top, right, bottom].every(Number.isFinite)
    ? {left, top, right, bottom} : null;
}

/** Test a selectable mesh against a bounded current screen-space projection. */
export function meshIntersectsClientRect({
  mesh, camera, canvas, selectionRect,
} = {}) {
  const targetRect = normalizedSelectionRect(selectionRect);
  const canvasRect = canvas?.getBoundingClientRect?.();
  const width = Number(canvasRect?.width);
  const height = Number(canvasRect?.height);
  const position = mesh?.geometry?.getAttribute?.('position');
  const index = mesh?.geometry?.index;
  const availableEntries = Number(index ? index.count : position?.count || 0);
  const drawStart = Math.max(0, Number(mesh.geometry.drawRange?.start) || 0);
  const drawCount = Number(mesh.geometry.drawRange?.count);
  const entryCount = Math.min(
    Math.max(0, availableEntries - drawStart),
    Number.isFinite(drawCount) ? Math.max(0, drawCount) : availableEntries,
  );
  if (!targetRect || !mesh?.visible || !camera || !position
      || !canvasRect || width <= 0 || height <= 0 || entryCount <= 0) {
    return false;
  }

  mesh.updateWorldMatrix?.(true, false);
  const sampleCount = Math.min(MAX_BOX_SELECTION_SAMPLES, entryCount);
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let projectedCount = 0;
  const point = new THREE.Vector3();
  for (let sample = 0; sample < sampleCount; sample += 1) {
    const entry = drawStart + (sampleCount === 1 ? 0 : Math.round(
      sample * (entryCount - 1) / (sampleCount - 1)));
    const vertex = index ? index.getX(entry) : entry;
    if (!Number.isInteger(vertex) || vertex < 0 || vertex >= position.count) {
      continue;
    }
    point.set(position.getX(vertex), position.getY(vertex), position.getZ(vertex))
      .applyMatrix4(mesh.matrixWorld).project(camera);
    if (![point.x, point.y, point.z].every(Number.isFinite)
        || point.z < -1 || point.z > 1) continue;
    const clientX = canvasRect.left + (point.x + 1) * width / 2;
    const clientY = canvasRect.top + (1 - point.y) * height / 2;
    minX = Math.min(minX, clientX);
    minY = Math.min(minY, clientY);
    maxX = Math.max(maxX, clientX);
    maxY = Math.max(maxY, clientY);
    projectedCount += 1;
  }
  if (!projectedCount) return false;
  return minX <= targetRect.right && maxX >= targetRect.left
    && minY <= targetRect.bottom && maxY >= targetRect.top;
}

/** Return visible model targets whose sampled screen rectangles intersect. */
export function meshesInClientRect({
  meshes, camera, canvas, selectionRect,
} = {}) {
  return modelPickTargets(meshes).filter(mesh => meshIntersectsClientRect({
    mesh, camera, canvas, selectionRect,
  }));
}

function pointInRect(point, rect) {
  return point.x >= rect.left && point.x <= rect.right
    && point.y >= rect.top && point.y <= rect.bottom;
}

function orientation(a, b, c) {
  return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
}

function pointInTriangle(point, triangle) {
  const signs = triangle.map((vertex, index) => orientation(
    vertex, triangle[(index + 1) % 3], point));
  const hasPositive = signs.some(value => value > 1e-7);
  const hasNegative = signs.some(value => value < -1e-7);
  return !(hasPositive && hasNegative);
}

function onSegment(a, b, point) {
  return point.x >= Math.min(a.x, b.x) - 1e-7
    && point.x <= Math.max(a.x, b.x) + 1e-7
    && point.y >= Math.min(a.y, b.y) - 1e-7
    && point.y <= Math.max(a.y, b.y) + 1e-7;
}

function segmentsIntersect(a, b, c, d) {
  const abC = orientation(a, b, c);
  const abD = orientation(a, b, d);
  const cdA = orientation(c, d, a);
  const cdB = orientation(c, d, b);
  const proper = ((abC > 1e-7 && abD < -1e-7)
      || (abC < -1e-7 && abD > 1e-7))
    && ((cdA > 1e-7 && cdB < -1e-7)
      || (cdA < -1e-7 && cdB > 1e-7));
  return proper
    || (Math.abs(abC) <= 1e-7 && onSegment(a, b, c))
    || (Math.abs(abD) <= 1e-7 && onSegment(a, b, d))
    || (Math.abs(cdA) <= 1e-7 && onSegment(c, d, a))
    || (Math.abs(cdB) <= 1e-7 && onSegment(c, d, b));
}

function triangleIntersectsRect(triangle, rect) {
  if (triangle.some(point => pointInRect(point, rect))) return true;
  const corners = [
    {x: rect.left, y: rect.top},
    {x: rect.right, y: rect.top},
    {x: rect.right, y: rect.bottom},
    {x: rect.left, y: rect.bottom},
  ];
  if (corners.some(corner => pointInTriangle(corner, triangle))) return true;
  const triangleEdges = triangle.map((point, index) => [
    point, triangle[(index + 1) % 3],
  ]);
  const rectEdges = corners.map((point, index) => [
    point, corners[(index + 1) % 4],
  ]);
  return triangleEdges.some(([start, end]) => rectEdges.some(
    ([rectStart, rectEnd]) => segmentsIntersect(
      start, end, rectStart, rectEnd)));
}

/** Return authored triangle ordinals whose projected triangles intersect a rect. */
export function trianglesInClientRect({
  mesh, camera, canvas, selectionRect,
} = {}) {
  const targetRect = normalizedSelectionRect(selectionRect);
  const canvasRect = canvas?.getBoundingClientRect?.();
  const width = Number(canvasRect?.width);
  const height = Number(canvasRect?.height);
  const geometry = mesh?.geometry;
  const index = geometry?.index;
  const position = geometry?.getAttribute?.('position');
  const availableEntries = Number(index ? index.count : position?.count || 0);
  const drawStart = Math.max(0, Number(geometry?.drawRange?.start) || 0);
  const drawCount = Number(geometry?.drawRange?.count);
  const entryCount = Math.min(
    Math.max(0, availableEntries - drawStart),
    Number.isFinite(drawCount) ? Math.max(0, drawCount) : availableEntries,
  );
  if (!targetRect || !mesh?.visible || !camera || !position || !canvasRect
      || width <= 0 || height <= 0 || entryCount < 3) return [];

  mesh.updateWorldMatrix?.(true, false);
  const point = new THREE.Vector3();
  const selected = [];
  const partTriangles = mesh.userData?.loosePartTriangles;
  for (let face = 0; face * 3 + 2 < entryCount; face += 1) {
    const entries = [drawStart + face * 3, drawStart + face * 3 + 1,
      drawStart + face * 3 + 2];
    const vertices = entries.map(entry => index ? index.getX(entry) : entry);
    if (vertices.some(vertex => !Number.isInteger(vertex)
        || vertex < 0 || vertex >= position.count)) continue;
    const triangle = vertices.map(vertex => {
      point.set(position.getX(vertex), position.getY(vertex), position.getZ(vertex))
        .applyMatrix4(mesh.matrixWorld).project(camera);
      return {
        x: canvasRect.left + (point.x + 1) * width / 2,
        y: canvasRect.top + (1 - point.y) * height / 2,
        z: point.z,
      };
    });
    if (!triangle.every(vertex => [vertex.x, vertex.y, vertex.z]
        .every(Number.isFinite))
        || triangle.every(vertex => vertex.z < -1)
        || triangle.every(vertex => vertex.z > 1)) continue;
    const authored = Array.isArray(partTriangles)
      ? Number(partTriangles[face])
      : Math.floor((drawStart + face * 3) / 3);
    if (Number.isInteger(authored) && authored >= 0
        && triangleIntersectsRect(triangle, targetRect)) {
      selected.push(authored);
    }
  }
  return selected;
}
