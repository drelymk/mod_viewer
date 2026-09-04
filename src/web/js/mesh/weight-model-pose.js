// Source-local model-pose topology and attachment carriers. The merged model
// rig remains the navigation graph; deformation keeps each source forest's
// exact edge set.

import {sourceBoneKey} from './weight-rig-reconcile.js';
import {
  jointPivotMap,
  orientForest as rigOrientForest,
} from './weight-rig.js';

function rigEdgeKey(left, right) {
  const a = Number(left);
  const b = Number(right);
  if (!Number.isFinite(a) || !Number.isFinite(b) || a === b) return '';
  return `${Math.min(a, b)}:${Math.max(a, b)}`;
}

function sourceForestEdges(forest) {
  const byKey = new Map();
  (forest?.components || []).forEach(component => {
    (component.edges || []).forEach(edge => {
      const key = rigEdgeKey(edge.boneA, edge.boneB);
      if (key && !byKey.has(key)) byKey.set(key, {...edge});
    });
  });
  return [...byKey.values()];
}

function sourcePoseTopologyDiagnostics(forest, sourceEdges) {
  const adjacency = new Map();
  const directEdges = new Set();
  (forest?.components || []).forEach(component => {
    (component.nodeIds || []).forEach(nodeId => {
      const id = Number(nodeId);
      if (Number.isFinite(id) && !adjacency.has(id)) adjacency.set(id, []);
    });
    Object.entries(component.parentById || {}).forEach(([childValue, parentValue]) => {
      const key = rigEdgeKey(childValue, parentValue);
      if (!key) return;
      directEdges.add(key);
      const child = Number(childValue);
      const parent = Number(parentValue);
      adjacency.get(child)?.push(parent);
      adjacency.get(parent)?.push(child);
    });
  });
  const pathLength = (start, end) => {
    const distance = new Map([[start, 0]]);
    const queue = [start];
    while (queue.length) {
      const current = queue.shift();
      if (current === end) return distance.get(current);
      for (const neighbor of adjacency.get(current) || []) {
        if (distance.has(neighbor)) continue;
        distance.set(neighbor, distance.get(current) + 1);
        queue.push(neighbor);
      }
    }
    return null;
  };
  let preservedEdgeCount = 0;
  let brokenEdgeCount = 0;
  let maxDetour = 0;
  const uniqueSourceEdges = new Map();
  (sourceEdges || []).forEach(edge => {
    const key = rigEdgeKey(edge.boneA, edge.boneB);
    if (key && !uniqueSourceEdges.has(key)) uniqueSourceEdges.set(key, edge);
  });
  uniqueSourceEdges.forEach(edge => {
    const key = rigEdgeKey(edge.boneA, edge.boneB);
    const distance = pathLength(Number(edge.boneA), Number(edge.boneB));
    if (directEdges.has(key) && distance === 1) preservedEdgeCount += 1;
    else brokenEdgeCount += 1;
    const safeDistance = distance === null
      ? adjacency.size + 1 : distance;
    maxDetour = Math.max(maxDetour, safeDistance);
  });
  return {
    sourceEdgeCount: uniqueSourceEdges.size,
    preservedEdgeCount,
    brokenEdgeCount,
    maxDetour,
  };
}

export function buildSourceModelPoseConfiguration(sourceRig, modelRig) {
  const sourceForest = modelRig?.reconciliation?.sourceForest;
  const sourceComponentByJointId = sourceForest?.componentByJointId
    || new Map();
  const acceptedAttachments = modelRig?.reconciliation?.reconciliation
    ?.acceptedAttachments || [];
  const sourceComponents = sourceRig?.inferredForest?.components || [];
  const sourceEdges = sourceForestEdges(sourceRig?.inferredForest);
  const rootByComponent = new Map();
  const entryJointByComponent = new Map();
  const modelDepth = jointId => {
    const componentId = modelRig?.componentByJointId?.get(Number(jointId));
    const depth = Number.isInteger(Number(componentId))
      ? modelRig?.components?.[Number(componentId)]?.depthById?.[Number(jointId)]
      : null;
    return Number.isFinite(Number(depth)) ? Number(depth) : Infinity;
  };
  sourceComponents.forEach(component => {
    const mapped = (component.nodeIds || []).map(boneId => ({
      boneId: Number(boneId),
      jointId: modelRig?.sourceBoneToModelJointId?.get(
        sourceBoneKey(sourceRig.sourceKey, boneId)),
    })).filter(item => Number.isInteger(item.boneId)
      && Number.isInteger(item.jointId));
    const sourceComponentIds = new Set(mapped.map(item =>
      sourceComponentByJointId.get(item.jointId)).filter(value =>
      Number.isInteger(Number(value))).map(Number));
    const attachment = acceptedAttachments.find(edge =>
      sourceComponentIds.has(Number(edge.accessoryComponentId)));
    let selected = null;
    if (attachment) {
      selected = mapped.find(item => item.jointId === Number(attachment.jointB));
    }
    if (!selected) {
      const candidates = [...mapped].sort((left, right) => {
        const leftDepth = modelDepth(left.jointId);
        const rightDepth = modelDepth(right.jointId);
        if (leftDepth !== rightDepth) return leftDepth < rightDepth ? -1 : 1;
        const leftIsSourceRoot = left.boneId === Number(component.rootId);
        const rightIsSourceRoot = right.boneId === Number(component.rootId);
        if (leftIsSourceRoot !== rightIsSourceRoot) {
          return leftIsSourceRoot ? -1 : 1;
        }
        return left.boneId - right.boneId;
      });
      selected = candidates[0] || null;
    }
    const rootId = selected?.boneId ?? Number(component.rootId);
    if (Number.isInteger(rootId)) {
      rootByComponent.set(Number(component.componentId), rootId);
    }
    if (selected) {
      entryJointByComponent.set(
        Number(component.componentId), Number(selected.jointId));
    }
  });
  const primaryComponent = sourceComponents[0];
  const primaryRoot = rootByComponent.get(Number(primaryComponent?.componentId))
    ?? Number(primaryComponent?.rootId);
  const modelPoseForest = rigOrientForest(
    sourceRig?.influenceGraph?.nodes || [], sourceEdges, primaryRoot, {
      components: sourceComponents,
      secondaryRootByComponent: rootByComponent,
    });
  const modelPoseJointPivotByBoneId = jointPivotMap(
    modelPoseForest, sourceRig?.influenceGraph?.relationships || []);
  const modelPoseTopology = sourcePoseTopologyDiagnostics(
    modelPoseForest, sourceEdges);
  return {
    modelPoseForest,
    modelPoseJointPivotByBoneId,
    modelPoseEntryJointByComponentId: entryJointByComponent,
    modelPoseTopology,
  };
}
