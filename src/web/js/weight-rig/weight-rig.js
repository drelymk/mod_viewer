// Generic, source-scoped analysis of authored skinning weights.
//
// This module deliberately contains no physics state.  Character Physics and
// the Rig/Pose mode consume the same inferred topology, while
// retaining their own runtime selection and deformation state.

export {
  inspectSurfaceTopology,
  barycentricThirdMoment,
  integrateLinearSecondMoment,
  integratePositionProduct,
  hasUsableSurfaceTopology,
  buildSurfaceInfluenceGraph,
  inspectSurfaceTopologyCooperative,
  buildSurfaceInfluenceGraphCooperative,
  aggregateInfluenceGraphs,
} from './weight-rig-surface.js';
export {
  CANDIDATE_CONTAINMENT_THRESHOLD,
  CANDIDATE_JACCARD_THRESHOLD,
  candidateRelationshipEdges,
  buildMaximumSpanningTree,
  orientTree,
  chooseSecondaryComponentRoot,
  orientForest,
  buildInferredRigForest,
  jointPivotMap,
} from './weight-rig-forest.js';
