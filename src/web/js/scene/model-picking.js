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
