// Candidate relationships, deterministic spanning forests and root orientation.
import { relationshipSort, centerDistance, pairKey } from './weight-rig-surface.js';

export const CANDIDATE_CONTAINMENT_THRESHOLD = 0.02;

export const CANDIDATE_JACCARD_THRESHOLD = 0.01;

function treeEdgeScore(edge) {
  return Number(edge.treeEdgeScore ?? edge.score ?? edge.containment ?? edge.jaccard ?? 0) || 0;
}

function treeEdgeCompare(a, b) {
  return treeEdgeScore(b) - treeEdgeScore(a) || relationshipSort(a, b);
}

export function candidateRelationshipEdges(graph, options = {}) {
  const containmentThreshold = Number(options.containmentThreshold ?? CANDIDATE_CONTAINMENT_THRESHOLD);
  const jaccardThreshold = Number(options.jaccardThreshold ?? CANDIDATE_JACCARD_THRESHOLD);
  return (graph?.relationships || [])
    .filter(
      (relationship) =>
        Number(relationship.productOverlap) > 0 &&
        (relationship.containment >= containmentThreshold || relationship.jaccard >= jaccardThreshold),
    )
    .map((relationship) => {
      const normalizedDistance = Number(relationship.normalizedDistance);
      const distancePenalty = Number.isFinite(normalizedDistance) ? 1 / (1 + Math.max(0, normalizedDistance)) : 1;
      return {
        ...relationship,
        treeEdgeScore: relationship.treeEdgeScore ?? relationship.containment * distancePenalty,
      };
    })
    .sort(relationshipSort);
}

export function buildMaximumSpanningTree(nodes, edges) {
  const nodeIds = [...new Set((nodes || []).map((node) => Number(node?.boneId ?? node)).filter(Number.isFinite))];
  const parent = new Map(nodeIds.map((id) => [id, id]));
  const rank = new Map(nodeIds.map((id) => [id, 0]));
  const find = (id) => {
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
  const orderedEdges = [...(edges || [])].sort(treeEdgeCompare);
  const selected = [];
  for (const edge of orderedEdges) {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!parent.has(boneA) || !parent.has(boneB) || boneA === boneB) continue;
    if (union(boneA, boneB)) selected.push(edge);
  }
  const components = new Map();
  nodeIds.forEach((id) => {
    const root = find(id);
    const component = components.get(root) || [];
    component.push(id);
    components.set(root, component);
  });
  return { edges: selected, components: [...components.values()] };
}

export function orientTree(treeEdges, rootId) {
  const adjacency = new Map();
  const add = (from, to) => {
    const neighbors = adjacency.get(from) || [];
    neighbors.push(to);
    adjacency.set(from, neighbors);
  };
  (treeEdges || []).forEach((edge) => {
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
      (adjacency.get(current) || []).forEach((neighbor) => {
        if (depthById[neighbor] !== null) return;
        parentById[neighbor] = current;
        childrenById[current].push(neighbor);
        depthById[neighbor] = depth + 1;
        queue.push(neighbor);
      });
    }
  }
  return { rootId: root, parentById, childrenById, depthById };
}

function connectedNodeComponents(nodes, edges) {
  const nodeIds = normalizedNodeIds(nodes);
  const nodeSet = new Set(nodeIds);
  const adjacency = new Map(nodeIds.map((id) => [id, []]));
  (edges || []).forEach((edge) => {
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
  nodeIds.forEach((start) => {
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

export function chooseSecondaryComponentRoot(component, graph, primaryComponent, primaryRootId) {
  const componentIds = normalizedNodeIds(component?.nodeIds || component);
  if (!componentIds.length) return null;
  const nodes = graphNodeList(graph);
  const nodeById = new Map(nodes.map((node) => [Number(node.boneId), node]));
  const primaryIds = normalizedNodeIds(primaryComponent?.nodeIds || primaryComponent);
  const primaryNodes = primaryIds.map((id) => nodeById.get(id)).filter(Boolean);
  if (!primaryNodes.length && nodeById.has(Number(primaryRootId))) {
    primaryNodes.push(nodeById.get(Number(primaryRootId)));
  }
  if (!primaryNodes.length) return componentIds[0];

  let bestId = componentIds[0];
  let bestDistance = Infinity;
  componentIds.forEach((id, index) => {
    const node = nodeById.get(id);
    let nearest = Infinity;
    primaryNodes.forEach((primary) => {
      const distance = centerDistance(node?.weightedCenter, primary?.weightedCenter);
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
  nodeIds.forEach((id) => {
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
    ? supplied.map((component) => component.nodeIds || component)
    : connectedNodeComponents(nodes, treeEdges);
  const components = rawComponents.map((component) => normalizedNodeIds(component));
  const requestedRoot = Number(primaryRootId);
  let primaryComponentId = components.findIndex((component) => component.includes(requestedRoot));
  if (primaryComponentId < 0 && components.length) primaryComponentId = 0;

  const componentByBoneId = {};
  components.forEach((nodeIds, componentId) => {
    nodeIds.forEach((id) => {
      componentByBoneId[id] = componentId;
    });
  });
  const rootOverrides = options.secondaryRootByComponent;
  const forestComponents = components.map((nodeIds, componentId) => {
    const primary = componentId === primaryComponentId;
    let rootId = primary && nodeIds.includes(requestedRoot) ? requestedRoot : null;
    if (rootId === null && primary) rootId = nodeIds[0] ?? null;
    if (rootId === null) {
      const override =
        rootOverrides instanceof Map ? Number(rootOverrides.get(componentId)) : Number(rootOverrides?.[componentId]);
      rootId = nodeIds.includes(override)
        ? override
        : chooseSecondaryComponentRoot(nodeIds, nodes, components[primaryComponentId] || [], requestedRoot);
    }
    const nodeSet = new Set(nodeIds);
    const componentEdges = (treeEdges || []).filter(
      (edge) => nodeSet.has(Number(edge.boneA)) && nodeSet.has(Number(edge.boneB)),
    );
    const orientation = completeOrientation(nodeIds, orientTree(componentEdges, rootId));
    const depths = Object.values(orientation.depthById)
      .filter((depth) => depth !== null)
      .map(Number);
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
  const adjacency = new Map(nodeIds.map((id) => [id, []]));
  (edges || []).forEach((edge) => {
    const boneA = Number(edge.boneA);
    const boneB = Number(edge.boneB);
    if (!nodeSet.has(boneA) || !nodeSet.has(boneB) || boneA === boneB) return;
    adjacency.get(boneA).push(boneB);
    adjacency.get(boneB).push(boneA);
  });
  adjacency.forEach((neighbors) => neighbors.sort((a, b) => a - b));
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
  const edgeStrength = (edges || [])
    .filter((edge) => Number(edge.boneA) === node.boneId || Number(edge.boneB) === node.boneId)
    .reduce((sum, edge) => sum + treeEdgeScore(edge), 0);
  // Each term is source-local.  The raw values are normalized by the caller's
  // component maxima so adding a high-ID bone cannot change the result.
  return {
    centrality,
    affected: Number(node.affectedMeasure) || 0,
    weight: Number(node.totalWeight) || 0,
    degree: (adjacency.get(node.boneId) || []).length,
    edgeStrength,
  };
}

function chooseRoot(nodeIds, nodes, edges) {
  const nodeById = new Map((nodes || []).map((node) => [Number(node.boneId), node]));
  const adjacency = componentAdjacency(nodeIds, edges);
  const evidence = nodeIds.map((id) => ({
    id,
    ...nodeEvidenceScore(nodeById.get(id) || { boneId: id }, adjacency, edges),
  }));
  const max = (field) => Math.max(...evidence.map((item) => Number(item[field]) || 0), 1);
  const score = (item) =>
    (4 * item.centrality) / max('centrality') +
    (2 * item.affected) / max('affected') +
    (2 * item.weight) / max('weight') +
    item.degree / max('degree') +
    item.edgeStrength / max('edgeStrength');
  evidence.forEach((item) => {
    item.score = score(item);
  });
  evidence.sort(
    (left, right) =>
      right.score - left.score ||
      right.centrality - left.centrality ||
      right.affected - left.affected ||
      right.weight - left.weight ||
      right.degree - left.degree ||
      right.edgeStrength - left.edgeStrength ||
      String(nodeById.get(left.id)?.weightedCenter || '').localeCompare(
        String(nodeById.get(right.id)?.weightedCenter || ''),
      ) ||
      left.id - right.id,
  );
  return evidence[0]?.id ?? null;
}

function normalizedNodeIds(nodes) {
  return [...new Set((nodes || []).map((node) => Number(node?.boneId ?? node)).filter(Number.isFinite))];
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
    const componentEdges = tree.edges.filter(
      (edge) => nodeSet.has(Number(edge.boneA)) && nodeSet.has(Number(edge.boneB)),
    );
    let rootId = Number(rootOverrides instanceof Map ? rootOverrides.get(componentId) : rootOverrides?.[componentId]);
    if (!nodeSet.has(rootId)) rootId = chooseRoot(nodeIdsForComponent, nodes, componentEdges);
    const orientation = orientTree(componentEdges, rootId);
    const depths = Object.values(orientation.depthById)
      .filter((depth) => depth !== null)
      .map(Number);
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
  components.forEach((component) =>
    component.nodeIds.forEach((id) => {
      componentByBoneId[id] = component.componentId;
    }),
  );
  return {
    components,
    componentByBoneId,
    edges: tree.edges,
    nodeIds,
  };
}

export function jointPivotMap(forest, relationships) {
  const byPair = new Map((relationships || []).map((edge) => [pairKey(Number(edge.boneA), Number(edge.boneB)), edge]));
  const pivots = new Map();
  (forest?.components || []).forEach((component) => {
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
