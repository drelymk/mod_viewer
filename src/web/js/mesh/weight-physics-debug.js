// Read-only diagnostics for source-owned Physics state. The runtime keeps
// solver data on source rigs; this module defines the detached debug view.

export function physicsDebugSnapshot(rig) {
  if (!rig) return null;
  return {
    sourceKey: rig.sourceKey,
    physicsState: rig.physicsState,
    physicsForest: rig.physicsForest,
    physicsSettled: rig.physicsSettled,
    physicsCenterByBoneId: rig.physicsCenterByBoneId,
    physicsBaseCenterByBoneId: rig.physicsBaseCenterByBoneId,
    physicsJointLimits: rig.physicsJointLimits,
    physicsConstraintDiagnostics: rig.physicsConstraintDiagnostics,
    physicsGravityLocal: [...(rig.physicsGravityLocal || [0, -1, 0])],
    physicsGravityAccelerations: rig.physicsGravityAccelerations,
    physicsGravityDiagnostics: rig.physicsGravityDiagnostics,
    physicsTargetByBoneId: rig.physicsTargetByBoneId,
    physicsEquilibriumByBoneId: rig.physicsEquilibriumByBoneId,
    physicsVirtualLinearVelocityLocal: rig.physicsVirtualLinearVelocityLocal,
    selectionKey: rig.selectionKey || '',
    lastRootAngularDeltaVector: [...(rig.lastRootAngularDeltaVector || [0, 0, 0])],
    lastRootAngularDeltaMagnitude: Number(rig.lastRootAngularDeltaMagnitude) || 0,
    motionEventCount: Number(rig.motionEventCount) || 0,
    lastRootTranslationDeltaWorld: [
      ...(rig.lastRootTranslationDeltaWorld || [0, 0, 0])],
    lastRootTranslationDeltaLocal: [
      ...(rig.lastRootTranslationDeltaLocal || [0, 0, 0])],
    lastTranslationLagRotationVector: [
      ...(rig.lastTranslationLagRotationVector || [0, 0, 0])],
    lastTranslationLagRotationMagnitude:
      Number(rig.lastTranslationLagRotationMagnitude) || 0,
    translationEventCount: Number(rig.translationEventCount) || 0,
    lastRootLinearVelocityWorld: [
      ...(rig.lastRootLinearVelocityWorld || [0, 0, 0])],
    lastRootLinearVelocityLocal: [
      ...(rig.lastRootLinearVelocityLocal || [0, 0, 0])],
    lastRootLinearVelocityDelta: [
      ...(rig.lastRootLinearVelocityDelta || [0, 0, 0])],
    composedTransforms: rig.composedTransforms,
    composedRotations: rig.composedRotations,
  };
}
