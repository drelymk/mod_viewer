// Public Weight/Rig composition surface.
//
// Keep this facade deliberately boring: UI consumers import product actions
// from named session boundaries, while the core coordinates the shared
// source, pose and physics state privately.

export {
  clearSelectedBones,
  getModelWeightState,
  loadSavedBoneSelection,
  saveModelWeightSelection,
  setBoneSelected,
  setModelWeightHeatmap,
  setSelectedBones,
} from './weight-model-session.js';
export {
  beginWeightModelPicking,
  cancelWeightModelPicking,
  modelJointFromSkinningSample,
  sampleModelSkinningAtIntersection,
  setWeightPickerViewMode,
} from './weight-rig-core.js';
export * from './rig-model-session.js';
export * from './rig-pose-runtime.js';
export * from './humanoid-pose-runtime.js';
export {
  applyRigPosePresetById,
  deleteRigPosePreset,
  renameRigPosePreset,
  saveRigPosePreset,
  setRigMetadata,
} from './rig-preset-session.js';
export {
  getModelPhysicsState,
  resetModelPhysics,
  setPhysicsConstraintsEnabled,
  setPhysicsContinuousLinearResponse,
  setPhysicsDamping,
  setPhysicsFrequency,
  setPhysicsGravityEnabled,
  setPhysicsGravityScale,
  setPhysicsLinearMotionStrength,
  setPhysicsMaxBendDegrees,
  setPhysicsMotionStrength,
} from './weight-physics-controller.js';

// Skinning state is exposed here for the shared viewport integration while
// mesh registration and disposal remain owned by skinning-runtime.js.
export {getSkinningState} from './skinning-runtime.js';
