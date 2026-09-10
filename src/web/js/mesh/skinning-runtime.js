// Rendering lifecycle boundary for the optional skinning feature.
//
// Mesh management only needs these operations. Weight selection, rig
// construction and pose execution stay behind the session facade.

export {
  disposeSkinningExperiment,
  getSkinningState,
  getSkinningBaseMaterial,
  registerSkinningMesh,
  refreshSkinningAfterShapeChange,
  unregisterSkinningMesh,
  withSkinningBaseMaterial,
} from './weight-rig-core.js';

export {destroyModelPhysicsSession} from './weight-rig-core.js';
