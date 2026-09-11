// Public Weight/Rig composition surface.
//
// Keep this facade deliberately boring: UI consumers import product actions
// from named session boundaries, while the core coordinates the shared
// source, pose and physics state privately.

export {
  clearSelectedBones,
  beginWeightModelPicking,
  cancelWeightModelPicking,
  getModelWeightState,
  loadSavedBoneSelection,
  saveModelWeightSelection,
  setBoneSelected,
  setModelWeightHeatmap,
  setWeightPickerViewMode,
} from './weight-model-session.js';
export {
  beginRigJointPicking,
  cancelRigJointPicking,
  clearRigJointSelection,
  ensureModelRigLoaded,
  getModelRigState,
  handleRigJointPicked,
  pickRigJointFromModelSurface,
  selectRigJoint,
  setRigRotationSnapDegrees,
} from './rig-model-session.js';
export {
  finishRigJointPose,
  getRigJointPoseFrame,
  resetRigJoint,
  resetRigPose,
  setRigJointRoot,
  setRigJointRotation,
  setRigPoseControlStatus,
} from './rig-pose-runtime.js';
export {
  setRigActiveLimbRole,
  setRigIkEnabled,
  solveRigIkTarget,
} from './humanoid-pose-runtime.js';
export {
  beginHumanoidRigEdit,
  cancelHumanoidRigEdit,
  saveHumanoidRigEdit,
  resetHumanoidRig,
  getHumanoidRigEditSnapshot,
  setHumanoidRigMetadata,
  beginHumanoidControlCarry,
  updateHumanoidControlDraft,
  finishHumanoidControlCarry,
  cancelHumanoidControlCarry,
} from './humanoid-rig-edit-session.js';
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
