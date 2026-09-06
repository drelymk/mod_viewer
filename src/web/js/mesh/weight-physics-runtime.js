// Runtime-owned Physics adapters. Numerical solver algorithms remain in
// weight-physics.js so the session lifecycle does not become another barrel.

import {createModelPhysicsSession} from './model-physics-session.js';
import {
  buildMaximumSpanningTree,
  candidateRelationshipEdges,
  orientTree,
} from './weight-rig.js';
import {
  pruneSelectedRelationshipEdges,
  selectAttachmentRelationship,
} from './weight-rig-runtime.js';
import {normalizeSelectedBoneIds} from './weight-selection.js';

export function createWeightPhysicsRuntime(options) {
  return createModelPhysicsSession(options);
}

function averageSelectedCenter(centerByBoneId, ids) {
  const centers = ids.map(id => centerByBoneId?.get(id))
    .filter(center => Array.isArray(center) && center.length >= 3);
  if (!centers.length) return [0, 0, 0];
  return centers.reduce((sum, center) => [
    sum[0] + Number(center[0] || 0),
    sum[1] + Number(center[1] || 0),
    sum[2] + Number(center[2] || 0),
  ], [0, 0, 0]).map(value => value / centers.length);
}

export function buildSelectedPhysicsForest(
    graph, centerByBoneId, boneIds, selectedBoneIds) {
  const selected = new Set(
    normalizeSelectedBoneIds(selectedBoneIds)
      .filter(id => boneIds.includes(id)));
  if (!selected.size) return null;
  const selectedNodes = (graph.nodes || []).filter(node =>
    selected.has(Number(node.boneId)));
  const candidateEdges = candidateRelationshipEdges(graph);
  const selectedEdges = candidateEdges.filter(relationship =>
    selected.has(Number(relationship.boneA))
    && selected.has(Number(relationship.boneB)));
  const candidateTree = buildMaximumSpanningTree(selectedNodes, selectedEdges);
  const physicsEdges = pruneSelectedRelationshipEdges(
    candidateTree.edges, graph.relationships, selected);
  const selectedTree = buildMaximumSpanningTree(selectedNodes, physicsEdges);
  const centers = new Map(centerByBoneId || []);
  const components = [];
  const componentByBoneId = {};

  selectedTree.components.forEach((componentIds, componentIndex) => {
    const componentSet = new Set(componentIds);
    const boundary = selectAttachmentRelationship((graph.relationships || [])
      .filter(relationship => {
        const boneA = Number(relationship.boneA);
        const boneB = Number(relationship.boneB);
        const leftSelected = componentSet.has(boneA);
        const rightSelected = componentSet.has(boneB);
        if (leftSelected === rightSelected) return false;
        const other = leftSelected ? boneB : boneA;
        return !selected.has(other);
      }));
    let rootId;
    let attachment = 'authored';
    let attachmentEdge;
    if (boundary) {
      rootId = componentSet.has(Number(boundary.boneA))
        ? Number(boundary.boneB) : Number(boundary.boneA);
      attachmentEdge = {
        boneA: rootId,
        boneB: componentSet.has(Number(boundary.boneA))
          ? Number(boundary.boneA) : Number(boundary.boneB),
        containment: boundary.containment,
        jaccard: boundary.jaccard,
        minOverlap: boundary.minOverlap,
        sharedVertexCount: boundary.sharedVertexCount,
        treeEdgeScore: boundary.treeEdgeScore,
        attachment: 'authored',
      };
    } else {
      rootId = -1 - componentIndex;
      attachment = 'synthetic';
      const attachmentBone = [...componentIds].sort((left, right) => {
        const leftCenter = centers.get(left) || [0, 0, 0];
        const rightCenter = centers.get(right) || [0, 0, 0];
        return Math.hypot(...leftCenter) - Math.hypot(...rightCenter);
      })[0];
      centers.set(rootId, centers.get(attachmentBone)
        || averageSelectedCenter(centerByBoneId, componentIds));
      attachmentEdge = {
        boneA: rootId,
        boneB: attachmentBone,
        containment: 0,
        jaccard: 0,
        treeEdgeScore: 0,
        attachment: 'synthetic',
      };
    }
    const edges = selectedTree.edges.filter(edge =>
      componentSet.has(Number(edge.boneA))
      && componentSet.has(Number(edge.boneB)));
    edges.push(attachmentEdge);
    const orientation = orientTree(edges, rootId);
    const depths = Object.values(orientation.depthById)
      .filter(depth => depth !== null).map(Number);
    const component = {
      componentId: componentIndex,
      nodeIds: [rootId, ...componentIds],
      dynamicNodeIds: [...componentIds],
      rootId,
      parentById: orientation.parentById,
      childrenById: orientation.childrenById,
      depthById: orientation.depthById,
      edgeCount: edges.length,
      maxDepth: Math.max(0, ...depths),
      primary: componentIndex === 0,
      attachment,
    };
    components.push(component);
    component.nodeIds.forEach(id => { componentByBoneId[id] = componentIndex; });
  });
  return {
    primaryRootId: components[0]?.rootId ?? null,
    primaryComponentId: components.length ? 0 : null,
    components,
    componentByBoneId,
    selectedBoneIds: [...selected],
    centers,
  };
}

export function syncMeshPhysicsState(state, enabled) {
  state.physicsEnabled = !!enabled;
  state.deformationMode = enabled ? 'physics' : null;
  state.physicsParticipantStatus = enabled ? 'participating' : 'not-selected';
  state.physicsParticipantError = null;
}
