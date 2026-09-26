// Transient viewer-side loose-part geometry for one semantic source mesh.

import * as THREE from 'three/webgpu';
import {attachOutline, detachOutline} from '../scene/outline-renderer.js';

let selectionCleanup = null;

export const MAX_LOOSE_PART_TOLERANCE = 0.01;

/** Let selection remove a selected part before its source is torn down. */
export function setLoosePartSelectionCleanup(callback) {
  selectionCleanup = typeof callback === 'function' ? callback : null;
}

function positionKey(position, index) {
  return [position.getX(index), position.getY(index), position.getZ(index)]
    .join('\u0000');
}

function unionFind(size) {
  const parents = Array.from({length: size}, (_, index) => index);
  const ranks = new Uint8Array(size);
  const find = value => {
    let root = value;
    while (parents[root] !== root) root = parents[root];
    while (parents[value] !== value) {
      const next = parents[value];
      parents[value] = root;
      value = next;
    }
    return root;
  };
  const union = (left, right) => {
    let leftRoot = find(left);
    let rightRoot = find(right);
    if (leftRoot === rightRoot) return;
    if (ranks[leftRoot] < ranks[rightRoot]) {
      [leftRoot, rightRoot] = [rightRoot, leftRoot];
    }
    parents[rightRoot] = leftRoot;
    if (ranks[leftRoot] === ranks[rightRoot]) ranks[leftRoot] += 1;
  };
  return {find, union};
}

export function normalizeLoosePartTolerance(value) {
  const tolerance = Number(value);
  return Number.isFinite(tolerance) && tolerance >= 0
    && tolerance <= MAX_LOOSE_PART_TOLERANCE ? tolerance : null;
}

/** Find original index-buffer subsets and triangle ordinals for each island. */
function findLoosePartGroups(mesh, {tolerance = 0} = {}) {
  const geometry = mesh?.geometry;
  const index = geometry?.index;
  const position = geometry?.getAttribute?.('position')
    || geometry?.attributes?.position;
  const triangleCount = Math.floor(Number(index?.count || 0) / 3);
  const normalizedTolerance = normalizeLoosePartTolerance(tolerance);
  if (!position || !index || triangleCount < 2
      || normalizedTolerance === null) return [];

  const components = unionFind(triangleCount);
  if (normalizedTolerance === 0) {
    const firstTriangleByPosition = new Map();
    for (let triangle = 0; triangle < triangleCount; triangle += 1) {
      const offset = triangle * 3;
      const vertices = [
        index.getX(offset), index.getX(offset + 1), index.getX(offset + 2),
      ];
      if (vertices.some(vertex => !Number.isInteger(vertex)
          || vertex < 0 || vertex >= position.count)) return [];
      for (const vertex of vertices) {
        const key = positionKey(position, vertex);
        const previous = firstTriangleByPosition.get(key);
        if (previous === undefined) firstTriangleByPosition.set(key, triangle);
        else components.union(triangle, previous);
      }
    }
  } else {
    const cells = new Map();
    const toleranceSquared = normalizedTolerance * normalizedTolerance;
    const cellKey = (x, y, z) => `${x},${y},${z}`;
    for (let triangle = 0; triangle < triangleCount; triangle += 1) {
      const offset = triangle * 3;
      const vertices = [
        index.getX(offset), index.getX(offset + 1), index.getX(offset + 2),
      ];
      if (vertices.some(vertex => !Number.isInteger(vertex)
          || vertex < 0 || vertex >= position.count)) return [];
      for (const vertex of vertices) {
        const x = position.getX(vertex);
        const y = position.getY(vertex);
        const z = position.getZ(vertex);
        if (![x, y, z].every(Number.isFinite)) return [];
        const cellX = Math.floor(x / normalizedTolerance);
        const cellY = Math.floor(y / normalizedTolerance);
        const cellZ = Math.floor(z / normalizedTolerance);
        for (let dx = -1; dx <= 1; dx += 1) {
          for (let dy = -1; dy <= 1; dy += 1) {
            for (let dz = -1; dz <= 1; dz += 1) {
              const bucket = cells.get(cellKey(
                cellX + dx, cellY + dy, cellZ + dz));
              if (!bucket) continue;
              for (const previous of bucket) {
                const offsetX = x - previous.x;
                const offsetY = y - previous.y;
                const offsetZ = z - previous.z;
                if (offsetX * offsetX + offsetY * offsetY
                    + offsetZ * offsetZ <= toleranceSquared) {
                  components.union(triangle, previous.triangle);
                }
              }
            }
          }
        }
        const key = cellKey(cellX, cellY, cellZ);
        const bucket = cells.get(key) || [];
        bucket.push({x, y, z, triangle});
        cells.set(key, bucket);
      }
    }
  }

  const groups = new Map();
  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    const root = components.find(triangle);
    let indices = groups.get(root);
    if (!indices) {
      indices = {indices: [], triangles: []};
      groups.set(root, indices);
    }
    const offset = triangle * 3;
    indices.indices.push(index.getX(offset), index.getX(offset + 1),
      index.getX(offset + 2));
    indices.triangles.push(triangle);
  }
  if (groups.size <= 1) return [];

  const IndexArray = index.array?.constructor || Uint32Array;
  return [...groups.values()].map(group => ({
    indices: new IndexArray(group.indices),
    triangles: group.triangles,
  }));
}

/** Preserve the original viewer API while keeping provenance internally. */
export function findLooseParts(mesh, options = {}) {
  return findLoosePartGroups(mesh, options).map(group => group.indices);
}

function copyGeometryAttributes(sourceGeometry, partGeometry) {
  for (const [name, attribute] of Object.entries(sourceGeometry.attributes || {})) {
    if (attribute) partGeometry.setAttribute(name, attribute);
  }
  for (const [name, attributes] of Object.entries(
    sourceGeometry.morphAttributes || {})) {
    partGeometry.morphAttributes[name] = [...attributes];
  }
  partGeometry.morphTargetsRelative = sourceGeometry.morphTargetsRelative;
}

function loosePartLabelBase(source, label = null) {
  if (label) return label;
  if (source.userData?.loosePartLabelBase) {
    return source.userData.loosePartLabelBase;
  }
  const existing = source.userData?.looseParts?.[0]?.userData?.loosePartLabel;
  if (existing) {
    return existing.replace(/\s+-\s+Part\s+\d+$/, '');
  }
  return source.userData?.displayName || source.name || 'Mesh';
}

function rememberLoosePartLabelBase(source, label = null) {
  const base = loosePartLabelBase(source, label);
  source.userData.loosePartLabelBase = base;
  return base;
}

function partLabel(source, index, label) {
  const base = rememberLoosePartLabelBase(source, label);
  return `${base} - Part ${index + 1}`;
}

function normalizeLooseParts(source) {
  const parts = getLooseParts(source);
  if (!parts.length) return;
  const base = rememberLoosePartLabelBase(source);
  parts.forEach((part, index) => {
    part.userData.loosePartIndex = index;
    part.userData.loosePartLabel = `${base} - Part ${index + 1}`;
  });
}

function indicesForTriangles(sourceGeometry, triangles) {
  const sourceIndex = sourceGeometry?.index;
  if (!sourceIndex || !Array.isArray(triangles)) return null;
  const sourceTriangleCount = Math.floor(sourceIndex.count / 3);
  if (triangles.some(triangle => !Number.isInteger(triangle)
      || triangle < 0 || triangle >= sourceTriangleCount)) return null;
  const IndexArray = sourceIndex.array?.constructor || Uint32Array;
  const indices = new IndexArray(triangles.length * 3);
  triangles.forEach((triangle, index) => {
    const offset = triangle * 3;
    indices[index * 3] = sourceIndex.getX(offset);
    indices[index * 3 + 1] = sourceIndex.getX(offset + 1);
    indices[index * 3 + 2] = sourceIndex.getX(offset + 2);
  });
  return indices;
}

function createLoosePart(source, triangles, {
  index = 0, label = null, template = null, copyTransform = true,
} = {}) {
  const sourceGeometry = source?.geometry;
  const partIndex = indicesForTriangles(sourceGeometry, triangles);
  if (!sourceGeometry || !partIndex) return null;
  if (!sourceGeometry.boundingBox) sourceGeometry.computeBoundingBox();
  if (!sourceGeometry.boundingSphere) sourceGeometry.computeBoundingSphere();
  const geometry = new THREE.BufferGeometry();
  copyGeometryAttributes(sourceGeometry, geometry);
  geometry.setIndex(new THREE.BufferAttribute(partIndex, 1));
  geometry.boundingBox = sourceGeometry.boundingBox;
  geometry.boundingSphere = sourceGeometry.boundingSphere;

  const part = new THREE.Mesh(geometry, source.material);
  part.name = `${source.name || 'mesh'}-loose-part-${index + 1}`;
  part.castShadow = source.castShadow;
  part.receiveShadow = source.receiveShadow;
  // Parts share the source bounds, but rig dragging intentionally marks
  // only the semantic source as uncullable while its bounds are dirty.
  part.frustumCulled = false;
  part.layers.mask = source.layers.mask;
  if (template) {
    if (copyTransform) {
      part.position.copy(template.position);
      part.quaternion.copy(template.quaternion);
      part.scale.copy(template.scale);
    }
    part.visible = template.visible;
    part.userData.manualVisible = template.userData?.manualVisible
      ?? template.visible;
    part.userData.manuallyToggled = !!template.userData?.manuallyToggled;
  } else {
    part.userData.manualVisible = true;
  }
  part.userData.loosePartParent = source;
  part.userData.loosePartIndex = index;
  part.userData.loosePartLabel = label || partLabel(source, index, null);
  part.userData.loosePartTriangles = [...triangles];
  attachOutline(part);
  source.add(part);
  return part;
}

function sourceTriangleOrdinals(source) {
  const count = Math.floor(Number(source?.geometry?.index?.count || 0) / 3);
  return Array.from({length: count}, (_, triangle) => triangle);
}

/** Create viewer children while retaining the source mesh as semantic owner. */
export function separateLooseParts(source, {label = null, tolerance = 0} = {}) {
  if (!source?.geometry || source.userData?.looseParts?.length) {
    return source?.userData?.looseParts || [];
  }
  const groups = findLoosePartGroups(source, {tolerance});
  if (groups.length <= 1) return [];

  const sourceGeometry = source.geometry;
  if (!sourceGeometry.boundingBox) sourceGeometry.computeBoundingBox();
  if (!sourceGeometry.boundingSphere) sourceGeometry.computeBoundingSphere();
  source.userData.loosePartDrawRange = {
    start: sourceGeometry.drawRange.start,
    count: sourceGeometry.drawRange.count,
  };
  source.userData.looseParts = [];
  rememberLoosePartLabelBase(source, label);
  sourceGeometry.setDrawRange(0, 0);

  for (const [partIndex, group] of groups.entries()) {
    const part = createLoosePart(source, group.triangles, {
      index: partIndex,
    });
    if (!part) {
      clearLooseParts(source);
      return [];
    }
    source.userData.looseParts.push(part);
  }
  normalizeLooseParts(source);
  return source.userData.looseParts;
}

export function getLooseParts(mesh) {
  return mesh?.userData?.looseParts || [];
}

export function isLoosePart(mesh) {
  return !!mesh?.userData?.loosePartParent;
}

export function getLoosePartSource(mesh) {
  return mesh?.userData?.loosePartParent || null;
}

/** Split one source or loose part into remainder followed by selected faces. */
export function separateSelectedTriangles(target, selectedTriangles, {
  label = null,
} = {}) {
  const source = getLoosePartSource(target) || target;
  if (!source?.geometry?.index || !target) return null;
  const sourceParts = getLooseParts(source);
  if (target !== source && !sourceParts.includes(target)) return null;
  const targetTriangles = target === source
    ? sourceTriangleOrdinals(source)
    : [...(target.userData?.loosePartTriangles || [])];
  const available = new Set(targetTriangles);
  const selected = [...new Set(selectedTriangles || [])]
    .filter(triangle => Number.isInteger(triangle));
  if (!selected.length || selected.length >= targetTriangles.length
      || selected.some(triangle => !available.has(triangle))) return null;
  const selectedSet = new Set(selected);
  const remainderTriangles = targetTriangles.filter(
    triangle => !selectedSet.has(triangle));
  if (!remainderTriangles.length) return null;

  const sourceWasClean = target === source && sourceParts.length === 0;
  if (!sourceWasClean && target === source) return null;
  if (sourceWasClean) {
    const sourceGeometry = source.geometry;
    if (!sourceGeometry.boundingBox) sourceGeometry.computeBoundingBox();
    if (!sourceGeometry.boundingSphere) sourceGeometry.computeBoundingSphere();
    source.userData.loosePartDrawRange = {
      start: sourceGeometry.drawRange.start,
      count: sourceGeometry.drawRange.count,
    };
    source.userData.looseParts = [];
    rememberLoosePartLabelBase(source, label);
    sourceGeometry.setDrawRange(0, 0);
    const remainder = createLoosePart(source, remainderTriangles, {
      index: 0, template: source,
      copyTransform: false,
    });
    if (remainder) source.userData.looseParts.push(remainder);
    const selectedPart = createLoosePart(source, selected, {
      index: 1, template: source,
      copyTransform: false,
    });
    if (selectedPart) source.userData.looseParts.push(selectedPart);
    if (!remainder || !selectedPart) {
      clearLooseParts(source);
      return null;
    }
    normalizeLooseParts(source);
    return {source, target, remainder, selected: selectedPart, full: false};
  }

  const targetIndex = sourceParts.indexOf(target);
  const childIndex = source.children.indexOf(target);
  const remainder = createLoosePart(source, remainderTriangles, {
    index: targetIndex, template: target,
  });
  const selectedPart = createLoosePart(source, selected, {
    index: targetIndex + 1, template: target,
  });
  if (!remainder || !selectedPart) {
    if (remainder) disposeLoosePart(source, remainder);
    if (selectedPart) disposeLoosePart(source, selectedPart);
    return null;
  }
  disposeLoosePart(source, target);
  const nextParts = sourceParts.slice();
  nextParts.splice(targetIndex, 1, remainder, selectedPart);
  source.userData.looseParts = nextParts;
  const children = source.children;
  [remainder, selectedPart].forEach(part => {
    const index = children.indexOf(part);
    if (index >= 0) children.splice(index, 1);
  });
  children.splice(Math.max(0, childIndex), 0, remainder, selectedPart);
  normalizeLooseParts(source);
  return {source, target, remainder, selected: selectedPart, full: false};
}

export function canMergeLooseParts(meshes) {
  const selected = [...new Set(meshes || [])];
  if (selected.length < 2) return false;
  const source = getLoosePartSource(selected[0]);
  if (!source) return false;
  const parts = getLooseParts(source);
  return selected.every(part => getLoosePartSource(part) === source
    && parts.includes(part));
}

export function syncLoosePartMaterial(source) {
  const color = source.geometry?.getAttribute?.('color') || null;
  for (const part of getLooseParts(source)) {
    part.material = source.material;
    if (color) part.geometry.setAttribute('color', color);
    else part.geometry.deleteAttribute('color');
  }
}

function disposeLoosePart(source, part) {
  selectionCleanup?.(part);
  detachOutline(part);
  source.remove(part);
  part.geometry?.dispose?.();
}

/** Merge selected index subsets without changing the semantic source mesh. */
export function mergeLooseParts(meshes) {
  const requested = [...new Set(meshes || [])];
  if (!canMergeLooseParts(requested)) return null;
  const selected = new Set(requested);
  const source = getLoosePartSource(requested[0]);
  const sourceParts = getLooseParts(source);
  const selectedParts = sourceParts.filter(part => selected.has(part));

  if (selectedParts.length === sourceParts.length) {
    clearLooseParts(source);
    return {source, mesh: source, full: true};
  }

  const anyVisible = selectedParts.some(part => part.visible);
  const anyManual = selectedParts.some(part => part.userData.manuallyToggled);
  const survivor = selectedParts[0];
  const indexes = selectedParts.map(part => part.geometry?.index?.array);
  if (indexes.some(index => !index)) return null;
  if (selectedParts.some(part =>
    !Array.isArray(part.userData?.loosePartTriangles))) return null;
  const triangles = selectedParts.flatMap(part => part.userData.loosePartTriangles);
  const IndexArray = indexes[0].constructor;
  const combined = new IndexArray(indexes.reduce(
    (count, index) => count + index.length, 0));
  let offset = 0;
  indexes.forEach(index => {
    combined.set(index, offset);
    offset += index.length;
  });
  survivor.geometry.setIndex(new THREE.BufferAttribute(combined, 1));
  survivor.userData.loosePartTriangles = triangles;
  survivor.visible = anyVisible;
  survivor.userData.manualVisible = anyVisible;
  survivor.userData.manuallyToggled = anyManual;
  selectedParts.slice(1).forEach(part => disposeLoosePart(source, part));
  source.userData.looseParts = sourceParts.filter(part =>
    part === survivor || !selected.has(part));
  normalizeLooseParts(source);
  return {source, mesh: survivor, full: false};
}

/** Remove transient children and restore the source draw range. */
export function clearLooseParts(source) {
  const parts = getLooseParts(source);
  if (!parts.length) return false;
  for (const part of parts) {
    disposeLoosePart(source, part);
  }
  const drawRange = source.userData.loosePartDrawRange;
  if (drawRange) source.geometry.setDrawRange(drawRange.start, drawRange.count);
  delete source.userData.loosePartDrawRange;
  delete source.userData.loosePartLabelBase;
  source.userData.looseParts = [];
  return true;
}
