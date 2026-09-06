// Runtime-owned Physics adapters. Numerical solver algorithms remain in
// weight-physics.js so the session lifecycle does not become another barrel.

import * as THREE from 'three';
import {
  invalidateCharacterShadowGeometry, invalidateCharacterShadowMap,
  setPhysicsInteractionEnabled,
} from '../scene/scene.js';
import {requestRender} from '../scene/render-scheduler.js';
import {
  composeBasePoseWithPhysicsOffsets,
} from './weight-deformation.js';
import {createModelPhysicsSession} from './model-physics-session.js';
import {MODEL_PHYSICS_STEP} from './model-physics-session.js';
import {
  GRAVITY_WORLD_DIRECTION,
  applyReferenceFrameAngularDelta,
  applyReferenceFrameLinearVelocityDelta,
  applyReferenceFrameTranslationDelta,
  applyPhysicsJointLimits, initializePhysicsState,
  buildGravityAngularAccelerations, buildPhysicsConstraintDiagnostics,
  buildPhysicsEquilibriumRotations, buildPhysicsJointLimits,
  buildPhysicsTargetRotations,
  isPhysicsSettled, resetPhysicsState, stepSpringPhysics,
} from './weight-physics.js';
import {
  addWeightPhysicsPerformance, performanceNow,
} from './weight-physics-performance.js';
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

export function createWeightPhysicsRuntime({
  states, sourcePhysicsRigs, getModelSkinningRig,
  applyDeformation, finalizePhysicsGeometry, markFinalBoundsDirty,
}) {
  const modelPhysicsSession = createModelPhysicsSession({
    onInputOwnershipChanged: enabled => setPhysicsInteractionEnabled(enabled),
    onFrame: ({visibleParticipants}) => {
      if (!visibleParticipants?.length) return;
      invalidateCharacterShadowMap({request: false});
      addWeightPhysicsPerformance('dynamicShadowUpdateCount');
      requestRender();
    },
    onStateChanged: detail => {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent(
          'mod-viewer-model-physics-changed', {detail}));
      }
    },
    requestAnimationFrame: callback =>
      typeof window !== 'undefined' ? window.requestAnimationFrame(callback) : null,
    cancelAnimationFrame: frameId =>
      typeof window !== 'undefined' ? window.cancelAnimationFrame(frameId) : null,
  });

  function clearMotionDiagnostics(state) {
    state.lastRootAngularDeltaVector = [0, 0, 0];
    state.lastRootAngularDeltaMagnitude = 0;
    state.motionEventCount = 0;
    state.lastRootTranslationDeltaWorld = [0, 0, 0];
    state.lastRootTranslationDeltaLocal = [0, 0, 0];
    state.lastTranslationLagRotationVector = [0, 0, 0];
    state.lastTranslationLagRotationMagnitude = 0;
    state.translationEventCount = 0;
    state.lastRootLinearVelocityWorld = [0, 0, 0];
    state.lastRootLinearVelocityLocal = [0, 0, 0];
    state.lastRootLinearVelocityDelta = [0, 0, 0];
    state.physicsVirtualLinearVelocityLocal = [0, 0, 0];
  }

  function quaternionFromArray(value) {
    if (!Array.isArray(value) || value.length < 4) return null;
    const quaternion = new THREE.Quaternion(...value.slice(0, 4).map(Number));
    return quaternion.lengthSq() > 1e-12 ? quaternion.normalize() : null;
  }

  function localVector(value, orientation) {
    const quaternion = quaternionFromArray(orientation);
    if (!quaternion) return [...value];
    return new THREE.Vector3(...value)
      .applyQuaternion(quaternion.invert()).toArray();
  }

  function forEachRigMesh(rig, callback) {
    rig.meshes.forEach(mesh => {
      const state = states.get(mesh);
      if (state?.loaded) callback(mesh, state);
    });
  }

  function syncMeshPhysicsState(state, enabled) {
    state.physicsEnabled = !!enabled;
    state.deformationMode = enabled ? 'physics' : null;
    state.physicsParticipantStatus = enabled ? 'participating' : 'not-selected';
    state.physicsParticipantError = null;
  }

  function refreshConstraintState(state, settings = modelPhysicsSession.getSettings()) {
    if (!settings.constraintsEnabled || !state.physicsForest) {
      state.physicsJointLimits = null;
      state.physicsConstraintDiagnostics = null;
      return;
    }
    const result = buildPhysicsJointLimits(
      state.physicsForest,
      THREE.MathUtils.degToRad(settings.maxBendDegrees));
    state.physicsJointLimits = result.limitByBoneId;
    state.physicsConstraintDiagnostics = result.diagnostics;
  }

  function refreshPhysicsBaseCenters(rig) {
    const centers = new Map();
    const modelSkinningRig = getModelSkinningRig();
    const baseTransforms = modelSkinningRig?.sourceTransformAliases
      ?.get(rig.sourceKey);
    for (const [boneId, center] of rig.physicsCenterByBoneId || []) {
      const values = Array.isArray(center) ? center : [
        center?.x, center?.y, center?.z,
      ];
      const point = new THREE.Vector3(
        Number(values[0]) || 0, Number(values[1]) || 0, Number(values[2]) || 0);
      const transform = baseTransforms?.get(Number(boneId));
      if (transform?.isMatrix4) point.applyMatrix4(transform);
      centers.set(Number(boneId), point.toArray());
    }
    rig.physicsBaseCenterByBoneId = centers;
    return centers;
  }

  function refreshPhysicsEquilibrium(state, settings) {
    state.physicsTargetByBoneId = buildPhysicsTargetRotations(
      state.physicsForest, [0, 0, 0]);
    state.physicsEquilibriumByBoneId = buildPhysicsEquilibriumRotations(
      state.physicsForest, [0, 0, 0], settings.frequencyHz,
      settings.gravityEnabled ? state.physicsGravityAccelerations : null,
      settings.constraintsEnabled ? state.physicsJointLimits : null);
  }

  function gravityDirectionLocal(mesh, orientation = null) {
    const quaternion = quaternionFromArray(orientation) || mesh?.quaternion;
    if (!quaternion?.clone || quaternion.lengthSq() === 0) {
      return [...GRAVITY_WORLD_DIRECTION];
    }
    return new THREE.Vector3(...GRAVITY_WORLD_DIRECTION)
      .applyQuaternion(quaternion.clone().normalize().invert())
      .normalize().toArray();
  }

  function refreshGravityState(
      mesh, state, settings = modelPhysicsSession.getSettings(), orientation = null) {
    if (!settings.gravityEnabled) {
      state.physicsGravityAccelerations = null;
      state.physicsGravityDiagnostics = null;
      state.physicsGravityLocal = [...GRAVITY_WORLD_DIRECTION];
      return;
    }
    const localDirection = gravityDirectionLocal(mesh, orientation);
    const referenceRadius = Number(
      state.influenceGraph?.boundingSphereRadius);
    const gravity = buildGravityAngularAccelerations(
      state.physicsForest,
      state.physicsBaseCenterByBoneId || state.physicsCenterByBoneId
        || state.centerByBoneId, localDirection, {
        referenceRadius,
        gravityScale: settings.gravityScale,
      });
    state.physicsGravityLocal = localDirection;
    state.physicsGravityAccelerations = gravity.accelerationByBoneId;
    state.physicsGravityDiagnostics = {
      ...gravity.diagnostics,
      enabled: true,
      scale: settings.gravityScale,
      worldDirection: [...GRAVITY_WORLD_DIRECTION],
      localDirection: [...localDirection],
    };
  }

  function refreshParticipantDerivedState(mesh, state, settings) {
    if (!state.physicsForest) return;
    refreshPhysicsBaseCenters(state);
    refreshConstraintState(state, settings);
    refreshGravityState(mesh, state, settings);
    refreshPhysicsEquilibrium(state, settings);
  }

  function buildComposedSourceTransforms(rig) {
    const started = performanceNow();
    const modelSkinningRig = getModelSkinningRig();
    const baseRevision = modelSkinningRig?.poseRevision ?? -1;
    if (rig.baseInversePoseRevision !== baseRevision) {
      rig.baseTransformInverseCache.clear();
      rig.baseInversePoseRevision = baseRevision;
    }
    const baseTransforms = modelSkinningRig?.sourceTransformAliases
      ?.get(rig.sourceKey) || rig.skinRig?.modelTransformAliasByBoneId || null;
    const baseRotations = modelSkinningRig?.sourceRotationAliases
      ?.get(rig.sourceKey) || rig.skinRig?.modelRotationAliasByBoneId || null;
    rig.composedTransforms = composeBasePoseWithPhysicsOffsets({
      forest: rig.physicsForest,
      nodeCenters: rig.physicsCenterByBoneId || rig.centerByBoneId,
      baseTransformByBoneId: baseTransforms,
      baseRotationByBoneId: baseRotations,
      rotationByBoneId: rig.physicsState?.joints || null,
      getOffsetRotation: boneId => rig.physicsState?.joints.get(boneId)
        ?.rotationVector,
      transformCache: rig.composedTransformCache,
      baseTransformInverseCache: rig.baseTransformInverseCache,
      rotationOutput: rig.composedRotations,
    });
    rig.basePoseRevision = modelSkinningRig?.poseRevision ?? -1;
    rig.composedTransformsDirty = false;
    addWeightPhysicsPerformance('composedTransformBuildCount');
    addWeightPhysicsPerformance(
      'composedTransformMs', performanceNow() - started);
    return rig.composedTransforms;
  }

  function ensureComposedSourceTransforms(rig) {
    const baseRevision = getModelSkinningRig()?.poseRevision ?? -1;
    if (rig.composedTransformsDirty || rig.basePoseRevision !== baseRevision
        || !rig.composedTransforms?.size) {
      buildComposedSourceTransforms(rig);
    }
    return rig.composedTransforms;
  }

  function syncRigParticipantState(rig) {
    forEachRigMesh(rig, (mesh, state) => {
      syncMeshPhysicsState(state, !!rig.physicsState);
    });
  }

  function applySourceDeformation(rig, {visibleOnly = true, meshes = null} = {}) {
    if (!rig.physicsState || !rig.physicsForest) return false;
    const transforms = ensureComposedSourceTransforms(rig);
    let changed = false;
    const target = meshes ? new Set(meshes) : null;
    forEachRigMesh(rig, (mesh, state) => {
      if (target && !target.has(mesh)) return;
      if (visibleOnly && !mesh.visible) return;
      markFinalBoundsDirty(mesh, state);
      changed = applyDeformation(mesh, state, {
        request: false, invalidateShadow: false, skipHidden: false,
        composedTransforms: transforms, composedRotations: rig.composedRotations,
      }) || changed;
      addWeightPhysicsPerformance('participatingPhysicsMeshCount');
    });
    return changed;
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

  function buildSelectedPhysicsForest(
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

  function syncMeshPhysicsState(state, enabled) {
  state.physicsEnabled = !!enabled;
  state.deformationMode = enabled ? 'physics' : null;
  state.physicsParticipantStatus = enabled ? 'participating' : 'not-selected';
  state.physicsParticipantError = null;
}

  function getPhysicsConstraintDiagnostics(meshOrState) {
    const meshState = states.get(meshOrState);
    const state = meshState
      ? sourcePhysicsRigs.get(meshState.skinningSourceKey) : meshOrState;
    const settings = modelPhysicsSession.getSettings();
    const enabled = !!settings.constraintsEnabled
      && state?.physicsJointLimits instanceof Map;
    const dynamic = buildPhysicsConstraintDiagnostics(
      state?.physicsState, enabled ? state.physicsJointLimits : null,
      enabled ? state.physicsConstraintDiagnostics : null);
    return {
      enabled,
      maxComponentBend: Number(settings.maxBendDegrees) || 0,
      limitedJointCount: dynamic.limitedJointCount,
      atLimitCount: dynamic.atLimitCount,
      maxUsage: dynamic.maxUsage,
      components: dynamic.components.map(component => ({
        componentId: component.componentId,
        rootId: component.rootId,
        maxDepth: component.maxDepth,
        jointCount: component.jointCount,
        localLimitDegrees: THREE.MathUtils.radToDeg(
          component.localLimitRadians),
      })),
    };
  }

  function physicsReferenceRadius(mesh, state) {
    const graphRadius = Number(state.influenceGraph?.boundingSphereRadius);
    if (Number.isFinite(graphRadius) && graphRadius > 0) return graphRadius;
    if (!mesh.geometry?.boundingSphere) mesh.geometry?.computeBoundingSphere?.();
    const geometryRadius = Number(mesh.geometry?.boundingSphere?.radius);
    return Number.isFinite(geometryRadius) && geometryRadius > 0
      ? geometryRadius : 1;
  }

  function createSourcePhysicsParticipant(rig) {
    return {
      key: rig.sourceKey,
      getMeshCount: () => rig.meshes.size,
      onSessionAttached(settings) {
        clearMotionDiagnostics(rig);
        rig.physicsState = initializePhysicsState(rig.physicsForest);
        rig.physicsSettled = false;
        refreshParticipantDerivedState(
          [...rig.meshes][0], rig, settings);
        rig.composedTransformsDirty = true;
        syncRigParticipantState(rig);
        applySourceDeformation(rig, {visibleOnly: false});
      },
      onSessionDetached() {
        forEachRigMesh(rig, (mesh, state) => {
          state.physicsEnabled = false;
          state.deformationMode = null;
          state.physicsParticipantStatus = 'not-selected';
          state.physicsParticipantError = null;
          applyDeformation(mesh, state, {
            request: false, invalidateShadow: false, skipHidden: false,
          });
          finalizePhysicsGeometry(mesh, state);
        });
        rig.physicsState = null;
        rig.composedTransforms.clear();
        rig.composedRotations.clear();
        rig.physicsBaseCenterByBoneId = null;
        rig.composedTransformsDirty = true;
        rig.basePoseRevision = -1;
        rig.physicsSettled = true;
        clearMotionDiagnostics(rig);
        rig.lastPhysicsStepMetrics = null;
        invalidateCharacterShadowGeometry({request: false});
        requestRender();
      },
      onSettingsChanged(settings) {
        refreshParticipantDerivedState(
          [...rig.meshes][0], rig, settings);
        if (settings.constraintsEnabled) {
          applyPhysicsJointLimits(rig.physicsState, rig.physicsJointLimits);
        }
        rig.composedTransformsDirty = true;
        rig.physicsSettled = false;
        syncRigParticipantState(rig);
      },
      onModelMotion(motion) {
        if (!rig.physicsState || !rig.physicsForest) return false;
        const representative = [...rig.meshes][0];
        const rotationMagnitude = Math.hypot(...motion.rotationVector);
        rig.lastRootAngularDeltaVector = [...motion.rotationVector];
        rig.lastRootAngularDeltaMagnitude = rotationMagnitude;
        rig.motionEventCount = (rig.motionEventCount || 0) + 1;
        let physicsChanged = false;
        let immediateDeformation = false;
        const settings = motion.settings;
        if (rotationMagnitude >= 1e-10) {
          refreshGravityState(
            representative, rig, settings, motion.modelOrientation);
          refreshPhysicsEquilibrium(rig, settings);
        }
        if (rotationMagnitude >= 1e-10 && settings.angularResponse > 0) {
          applyReferenceFrameAngularDelta(
            rig.physicsState, rig.physicsForest,
            motion.rotationVector, settings.angularResponse,
            settings.constraintsEnabled ? rig.physicsJointLimits : null);
          physicsChanged = true;
          immediateDeformation = true;
        }
        if (motion.deltaLinearVelocityWorld) {
          const deltaVelocityLocal = localVector(
            motion.deltaLinearVelocityWorld, motion.previousModelOrientation);
          rig.lastRootLinearVelocityWorld = [...motion.linearVelocityWorld];
          rig.lastRootLinearVelocityLocal = [...localVector(
            motion.linearVelocityWorld, motion.previousModelOrientation)];
          rig.lastRootLinearVelocityDelta = deltaVelocityLocal;
          const diagnostics = {};
          applyReferenceFrameLinearVelocityDelta(
            rig.physicsState, rig.physicsForest,
            rig.physicsBaseCenterByBoneId || rig.physicsCenterByBoneId
              || rig.centerByBoneId,
            deltaVelocityLocal, settings.velocityResponse, diagnostics,
            settings.constraintsEnabled ? rig.physicsJointLimits : null);
          physicsChanged = diagnostics.maxDeltaAngularVelocityMagnitude >= 1e-10
            || physicsChanged;
        } else if (Math.hypot(...motion.translationDeltaWorld) >= 1e-10) {
          const translationLocal = localVector(
            motion.translationDeltaWorld, motion.previousModelOrientation);
          rig.lastRootTranslationDeltaWorld = [...motion.translationDeltaWorld];
          rig.lastRootTranslationDeltaLocal = [...translationLocal];
          rig.translationEventCount = (rig.translationEventCount || 0) + 1;
          const diagnostics = {};
          applyReferenceFrameTranslationDelta(
            rig.physicsState, rig.physicsForest,
            rig.physicsBaseCenterByBoneId || rig.physicsCenterByBoneId
              || rig.centerByBoneId,
            translationLocal, settings.translationResponse, diagnostics,
            settings.constraintsEnabled ? rig.physicsJointLimits : null);
          rig.lastTranslationLagRotationVector = [
            ...(diagnostics.maxLagRotationVector || [0, 0, 0])];
          rig.lastTranslationLagRotationMagnitude = Number(
            diagnostics.maxLagRotationMagnitude) || 0;
          if (rig.lastTranslationLagRotationMagnitude >= 1e-10
              && settings.translationResponse > 0) {
            physicsChanged = true;
            immediateDeformation = true;
          }
        }

        if (!physicsChanged) return false;
        rig.physicsSettled = false;
        rig.composedTransformsDirty = true;
        syncRigParticipantState(rig);
        if (immediateDeformation) applySourceDeformation(rig);
        return true;
      },
      onVirtualMotion(motion) {
        const representative = [...rig.meshes][0];
        if (!rig.physicsState || !rig.physicsForest || !representative
            || !motion.modelOrientation) return false;
        const currentVelocityLocal = localVector(
          motion.velocityWorld, motion.modelOrientation)
          .map(value => value * physicsReferenceRadius(representative, rig));
        const deltaVelocityLocal = localVector(
          motion.deltaVelocityWorld, motion.modelOrientation)
          .map(value => value * physicsReferenceRadius(representative, rig));
        rig.physicsVirtualLinearVelocityLocal = motion.active === false
          ? [0, 0, 0] : [...currentVelocityLocal];
        const diagnostics = {};
        applyReferenceFrameLinearVelocityDelta(
          rig.physicsState, rig.physicsForest,
          rig.physicsBaseCenterByBoneId || rig.physicsCenterByBoneId
            || rig.centerByBoneId,
          deltaVelocityLocal, motion.settings.velocityResponse, diagnostics,
          motion.settings.constraintsEnabled ? rig.physicsJointLimits : null);
        if (motion.active === false) rig.physicsSettled = false;
        const physicsChanged = diagnostics.maxDeltaAngularVelocityMagnitude >= 1e-10
          || motion.active === false;
        if (physicsChanged) rig.composedTransformsDirty = true;
        syncRigParticipantState(rig);
        return physicsChanged;
      },
      step(dt, settings) {
        if (!rig.physicsState || !rig.physicsForest) return;
        rig.lastPhysicsStepMetrics = stepSpringPhysics(
          rig.physicsState, rig.physicsForest, dt, {
            frequencyHz: settings.frequencyHz,
            dampingRatio: settings.dampingRatio,
            targetRotationByBoneId: rig.physicsTargetByBoneId,
            constrainedTargetRotationByBoneId: rig.physicsTargetByBoneId,
            equilibriumRotationByBoneId: rig.physicsEquilibriumByBoneId,
            externalAngularAccelerationByBoneId: settings.gravityEnabled
              ? rig.physicsGravityAccelerations : null,
            jointLimitByBoneId: settings.constraintsEnabled
              ? rig.physicsJointLimits : null,
            maxDt: MODEL_PHYSICS_STEP,
          });
        rig.composedTransformsDirty = true;
        addWeightPhysicsPerformance('sourcePhysicsStepCount');
      },
      updateSettled(settings) {
        if (!rig.physicsState || !rig.physicsForest) return;
        rig.physicsSettled = isPhysicsSettled(
          rig.physicsState, rig.physicsForest, [0, 0, 0], {
            frequencyHz: settings.frequencyHz,
            targetRotationByBoneId: rig.physicsTargetByBoneId,
            constrainedTargetRotationByBoneId: rig.physicsTargetByBoneId,
            equilibriumRotationByBoneId: rig.physicsEquilibriumByBoneId,
            externalAngularAccelerationByBoneId: settings.gravityEnabled
              ? rig.physicsGravityAccelerations : null,
            jointLimitByBoneId: settings.constraintsEnabled
              ? rig.physicsJointLimits : null,
          });
        syncRigParticipantState(rig);
      },
      onSettled() {
        forEachRigMesh(rig, (mesh, state) => finalizePhysicsGeometry(mesh, state));
        invalidateCharacterShadowGeometry({request: false});
      },
      isSettled: () => rig.physicsSettled,
      isVisible: () => [...rig.meshes].some(mesh => {
        const state = states.get(mesh);
        return mesh.visible && !!state?.physicsActiveVertices?.length;
      }),
      onMeshStateChanged(changedMeshes) {
        const affected = changedMeshes.filter(mesh => rig.meshes.has(mesh)
          && mesh.visible
          && states.get(mesh)?.physicsActiveVertices?.length);
        if (!affected.length) return false;
        return applySourceDeformation(rig, {meshes: affected, visibleOnly: false});
      },
      deform() {
        return applySourceDeformation(rig);
      },
      reset(settings) {
        resetPhysicsState(rig.physicsState);
        refreshParticipantDerivedState(
          [...rig.meshes][0], rig, settings);
        rig.physicsSettled = !settings.gravityEnabled;
        clearMotionDiagnostics(rig);
        rig.lastPhysicsStepMetrics = null;
        rig.composedTransformsDirty = true;
        syncRigParticipantState(rig);
        applySourceDeformation(rig, {visibleOnly: false});
        if (rig.physicsSettled) {
          forEachRigMesh(rig, (mesh, state) => finalizePhysicsGeometry(mesh, state));
          invalidateCharacterShadowGeometry({request: false});
        }
      },
    };
  }

  return {
    modelPhysicsSession,
    buildSelectedPhysicsForest,
    syncMeshPhysicsState,
    gravityDirectionLocal,
    getPhysicsConstraintDiagnostics,
    forEachRigMesh,
    refreshParticipantDerivedState,
    applySourceDeformation,
    syncRigParticipantState,
    createSourcePhysicsParticipant,
  };
}
