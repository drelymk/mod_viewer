import {Quaternion, Vector3} from 'three';
import {
  analyzeRigSemantics, normalizeSemanticFrame, serializeSemanticFrame,
} from './weight-rig-semantics.js';

const EPSILON = 1e-8;
const DEFAULT_OUTWARD_ANGLE_DEGREES = 10;
const MIN_SEMANTIC_CONFIDENCE = 0.7;
const MIN_PAIR_MARGIN = 0.1;

export const BUILTIN_ARMS_UP_ID = 'builtin:arms-up';
export const BUILTIN_ARMS_UP_NAME = 'Arms Up';

function number(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function clamp(value, minimum = 0, maximum = 1) {
  return Math.max(minimum, Math.min(maximum, value));
}

function vector(value) {
  if (value?.isVector3) return value.clone();
  const values = Array.isArray(value) || ArrayBuffer.isView(value)
    ? value : [value?.x, value?.y, value?.z];
  if (!values || values.length < 3) return null;
  const result = new Vector3(...values.slice(0, 3).map(Number));
  return result.toArray().every(Number.isFinite) ? result : null;
}

function normalize(value) {
  const result = vector(value);
  if (!result || result.lengthSq() <= EPSILON) return null;
  return result.normalize();
}

const semanticFrame = normalizeSemanticFrame;
const serializedSemanticFrame = serializeSemanticFrame;

function lateralCoordinate(point, frame) {
  return point.dot(frame.right);
}

function heightCoordinate(point, frame) {
  return point.dot(frame.up);
}

function modelComponents(modelRig) {
  return (modelRig?.defaultComponents?.length
    ? modelRig.defaultComponents : modelRig?.components || [])
    .map(component => ({
      componentId: Number(component.componentId),
      rootId: Number(component.rootId),
      nodeIds: (component.nodeIds || []).map(Number).filter(Number.isInteger),
      parentById: {...(component.parentById || {})},
      childrenById: Object.fromEntries(Object.entries(
        component.childrenById || {}).map(([id, children]) => [
        id, (children || []).map(Number).filter(Number.isInteger),
      ])),
    }));
}

function rigData(modelRig) {
  const joints = (modelRig?.joints || []).map(joint => ({
    ...joint,
    jointId: Number(joint.jointId),
  })).filter(joint => Number.isInteger(joint.jointId));
  const components = modelComponents(modelRig);
  return {joints, components};
}

function similarity(left, right, scale) {
  return 1 - clamp(Math.abs(left - right) / Math.max(EPSILON, scale));
}

function pairDirectionScore(left, right) {
  const leftDirection = new Vector3(...left.semanticDirection);
  const rightDirection = new Vector3(...right.semanticDirection);
  leftDirection.x *= -1;
  return clamp((leftDirection.dot(rightDirection) + 1) / 2);
}

function pairScore(left, right, frame) {
  const y = similarity(left.height, right.height, frame.height * .2);
  const lateral = similarity(left.sideOffset, right.sideOffset, frame.height * .4);
  const z = similarity(left.depthCoordinate, right.depthCoordinate,
    frame.height * .3);
  const direction = pairDirectionScore(left, right);
  const chain = similarity(left.chainLength, right.chainLength,
    frame.height * .8);
  const topology = (similarity(left.depth, right.depth, 3)
    + similarity(left.degree, right.degree, 2)) / 2;
  const extent = similarity(left.outwardExtent, right.outwardExtent,
    frame.height * .4);
  const score = symmetryScore({y, lateral, z, direction, chain, topology, extent});
  return {score, features: {y, lateral, z, direction, chain, topology, extent}};
}

function symmetryScore(features) {
  return features.lateral * .25 + features.y * .2
    + features.direction * .2 + features.extent * .15
    + features.chain * .1 + features.z * .05 + features.topology * .05;
}

function targetDirectionFor(candidate, semantic, outwardAngleDegrees) {
  const direction = new Vector3(...candidate.direction).normalize();
  const horizontal = direction.clone()
    .addScaledVector(semantic.up, -direction.dot(semantic.up));
  if (horizontal.lengthSq() <= EPSILON) horizontal.copy(semantic.right)
    .multiplyScalar(candidate.side);
  else {
    horizontal.normalize();
    if (horizontal.dot(semantic.right) * candidate.side < 0) {
      horizontal.multiplyScalar(-1);
    }
  }
  const angle = number(outwardAngleDegrees, DEFAULT_OUTWARD_ANGLE_DEGREES)
    * Math.PI / 180;
  return semantic.up.clone().multiplyScalar(Math.cos(angle))
    .add(horizontal.multiplyScalar(Math.sin(angle))).normalize();
}

function armResult(candidate, semantic, outwardAngleDegrees) {
  const direction = new Vector3(...candidate.direction).normalize();
  const targetDirection = targetDirectionFor(
    candidate, semantic, outwardAngleDegrees);
  const rotation = new Quaternion().setFromUnitVectors(
    direction, targetDirection).normalize();
  return {
    jointId: candidate.jointId,
    signature: candidate.signature,
    score: candidate.score,
    restDirection: direction.toArray(),
    targetDirection: targetDirection.toArray(),
    rotation: rotation.toArray(),
    pathIds: [...candidate.pathIds],
    validation: candidate.handCenter ? {
      predictedHand: new Vector3(...candidate.handCenter)
        .sub(new Vector3(...candidate.anchor))
        .applyQuaternion(rotation)
        .add(new Vector3(...candidate.anchor)).toArray(),
    } : null,
  };
}

function candidateFromSemanticArm(arm, side, semantic, frame) {
  const shoulder = arm?.shoulder;
  const hand = arm?.hand;
  const direction = normalize(arm?.restDirection);
  if (!shoulder || !hand || !direction) return null;
  const pathIds = arm.poseConnectivity?.pathIds?.length
    ? [...arm.poseConnectivity.pathIds]
    : [shoulder.jointId, hand.jointId];
  const chainLength = new Vector3(...shoulder.pivot)
    .distanceTo(new Vector3(...hand.center));
  const sideOffset = side * (shoulder.lateral - frame.centerLateral);
  return {
    jointId: shoulder.jointId,
    signature: shoulder.signature,
    side,
    anchor: [...shoulder.pivot],
    direction: direction.toArray(),
    normalizedY: shoulder.height01,
    height: shoulder.height,
    lateral: shoulder.lateral,
    depthCoordinate: shoulder.depth,
    directionLateral: direction.dot(semantic.right),
    semanticDirection: [
      direction.dot(semantic.right), direction.dot(semantic.up),
      direction.dot(semantic.forward),
    ],
    sideOffset,
    pathIds,
    orientationCompatible: arm.poseConnectivity?.connected === true,
    poseConnectivity: arm.poseConnectivity || null,
    chainLength,
    outwardExtent: Math.max(0, side * (hand.lateral - shoulder.lateral)),
    handCenter: [...hand.center],
    depth: shoulder.depthIndex,
    degree: 1,
    score: arm.confidence,
  };
}

function unavailable(reason, confidence = 0, diagnostics = {}) {
  return {
    available: false,
    confidence: clamp(confidence),
    reason,
    preset: null,
    diagnostics,
  };
}

/** Infer symmetric upper-arm controls from the model's default rest Rig. */
export function analyzeHumanoidRestPose(modelRig, options = {}) {
  const data = rigData(modelRig);
  const semantic = semanticFrame(options?.semanticFrame);
  const semanticAnalysis = analyzeRigSemantics(modelRig, {
    semanticFrame: options?.semanticFrame,
  });
  if (data.joints.length < 4 || !data.components.length) {
    return unavailable('insufficient_rig', 0, {
      jointCount: data.joints.length,
      semanticFrame: serializedSemanticFrame(semantic),
      semantic: semanticAnalysis.diagnostics || null,
    });
  }
  const fail = (reason, confidence, diagnostics = {}) => unavailable(
    reason, confidence, {
      ...diagnostics,
      semanticFrame: serializedSemanticFrame(semantic),
    });
  if (!semanticAnalysis.available) {
    return fail('semantic_landmarks_incomplete', semanticAnalysis.confidence, {
      detector: 'semantic-hands',
      semantic: semanticAnalysis.diagnostics || null,
    });
  }
  const semanticArms = [semanticAnalysis.landmarks.negativeArm,
    semanticAnalysis.landmarks.positiveArm];
  const candidates = [-1, 1].map(side => candidateFromSemanticArm(
    side < 0 ? semanticAnalysis.landmarks.negativeArm
      : semanticAnalysis.landmarks.positiveArm, side, semantic,
    semanticAnalysis.bodyFrame));
  if (candidates.some(candidate => !candidate)) {
    return fail('semantic_landmarks_incomplete', semanticAnalysis.confidence, {
      detector: 'semantic-hands',
      semantic: semanticAnalysis.diagnostics || null,
    });
  }
  if (semanticArms.some(arm => !arm?.poseConnectivity?.connected)) {
    return fail('arm_pose_connectivity_insufficient', semanticAnalysis.confidence, {
      detector: 'semantic-hands',
      semantic: semanticAnalysis.diagnostics || null,
      poseConnectivity: {
        negativeArm: semanticArms[0]?.poseConnectivity || null,
        positiveArm: semanticArms[1]?.poseConnectivity || null,
      },
    });
  }
  const selectedFrame = semanticAnalysis.bodyFrame;
  const detector = 'semantic-hands';
  const pairs = [];
  [candidates[0]].forEach(negativeX => [candidates[1]].forEach(positiveX => {
    const pairing = pairScore(negativeX, positiveX, selectedFrame);
    const score = pairing.score * .7
      + (negativeX.score + positiveX.score) * .15;
    pairs.push({negativeX, positiveX, score, features: pairing.features});
  }));
  pairs.sort((left, right) => right.score - left.score
    || left.negativeX.jointId - right.negativeX.jointId
    || left.positiveX.jointId - right.positiveX.jointId);
  const best = pairs[0];
  const runnerUpScore = pairs[1]?.score ?? 0;
  const confidence = clamp(semanticAnalysis.confidence);
  const diagnostics = {
    semanticFrame: serializedSemanticFrame(semantic),
    bodyFrame: {...selectedFrame},
    candidateCounts: {negativeX: 1, positiveX: 1},
    detector,
    semantic: semanticAnalysis.diagnostics || null,
    negativeXCandidates: [candidates[0]],
    positiveXCandidates: [candidates[1]],
    runnerUpScore,
    pairFeatures: best.features,
  };
  const minimumConfidence = MIN_SEMANTIC_CONFIDENCE;
  if (confidence < minimumConfidence) {
    return fail('arm_pair_low_confidence', confidence, diagnostics);
  }
  if (confidence - runnerUpScore < MIN_PAIR_MARGIN) {
    return fail('arm_pair_ambiguous', confidence, diagnostics);
  }
  if (!best.negativeX.orientationCompatible
      || !best.positiveX.orientationCompatible) {
    return fail('hierarchy_orientation_incompatible', confidence, diagnostics);
  }
  if (!best.negativeX.signature || !best.positiveX.signature) {
    return fail('invalid_rest_direction', confidence, diagnostics);
  }
  const arms = {
    negativeX: armResult(best.negativeX, semantic, DEFAULT_OUTWARD_ANGLE_DEGREES),
    positiveX: armResult(best.positiveX, semantic, DEFAULT_OUTWARD_ANGLE_DEGREES),
  };
  const poseValidation = {};
  for (const [name, arm] of Object.entries(arms)) {
      const candidate = name === 'negativeX' ? best.negativeX : best.positiveX;
      const predicted = new Vector3(...arm.validation.predictedHand);
      const restHand = new Vector3(...candidate.handCenter);
      const restHeight = heightCoordinate(restHand, semantic);
      const targetHeight = heightCoordinate(predicted, semantic);
      const targetLateral = lateralCoordinate(predicted, semantic);
      const heightGain = (targetHeight - restHeight) / selectedFrame.height;
      const sidePreserved = candidate.side
        * (targetLateral - selectedFrame.centerLateral) > 0;
      poseValidation[name] = {
        heightGain,
        sidePreserved,
        predictedHand: predicted.toArray(),
      };
      arm.validation = {...arm.validation, heightGain, sidePreserved};
  }
  diagnostics.poseValidation = poseValidation;
  if (Object.values(poseValidation).some(item => item.heightGain < .08
      || !item.sidePreserved)) {
    return fail('semantic_pose_validation_failed', confidence, diagnostics);
  }
  const preset = {
    id: BUILTIN_ARMS_UP_ID,
    name: BUILTIN_ARMS_UP_NAME,
    roots: [],
    joints: [arms.negativeX, arms.positiveX].map(arm => ({
      joint_signature: arm.signature,
      rotation: [...arm.rotation],
    })).sort((left, right) => left.joint_signature.localeCompare(
      right.joint_signature)),
  };
  return {
    available: true,
    confidence,
    reason: null,
    preset,
    arms,
    semanticFrame: serializedSemanticFrame(semantic),
    diagnostics,
  };
}

/** Generate a schema-compatible transient Arms Up preset. */
export function generateArmsUpPreset(modelRig, options = {}) {
  const result = analyzeHumanoidRestPose(modelRig, options);
  if (!result.available || !options || options.outwardAngleDegrees === undefined) {
    return result;
  }
  const semantic = semanticFrame(options.semanticFrame || result.semanticFrame);
  const outwardAngleDegrees = number(
    options.outwardAngleDegrees, DEFAULT_OUTWARD_ANGLE_DEGREES);
  const arms = {
    negativeX: armResult({
      ...result.diagnostics.negativeXCandidates.find(item =>
        item.jointId === result.arms.negativeX.jointId),
    }, semantic, outwardAngleDegrees, result.diagnostics.bodyFrame),
    positiveX: armResult({
      ...result.diagnostics.positiveXCandidates.find(item =>
        item.jointId === result.arms.positiveX.jointId),
    }, semantic, outwardAngleDegrees, result.diagnostics.bodyFrame),
  };
  return {
    ...result,
    arms,
    preset: {
      ...result.preset,
      joints: [arms.negativeX, arms.positiveX].map(arm => ({
        joint_signature: arm.signature,
        rotation: [...arm.rotation],
      })).sort((left, right) => left.joint_signature.localeCompare(
        right.joint_signature)),
    },
  };
}

/** Return frontend-only built-in descriptors for the current model Rig. */
export function getBuiltInRigPoseDescriptors(modelRig, options = {}) {
  const analysis = analyzeHumanoidRestPose(modelRig, options);
  const diagnostics = analysis.diagnostics || {};
  const summary = {
    semanticFrame: diagnostics.semanticFrame || null,
    semantic: diagnostics.semantic || null,
    bodyFrame: diagnostics.bodyFrame || null,
    candidateCounts: diagnostics.candidateCounts || null,
    runnerUpScore: Number.isFinite(diagnostics.runnerUpScore)
      ? diagnostics.runnerUpScore : null,
    pairFeatures: diagnostics.pairFeatures || null,
    poseValidation: diagnostics.poseValidation || null,
    selectedJointIds: analysis.arms
      ? [analysis.arms.negativeX.jointId, analysis.arms.positiveX.jointId]
      : [],
  };
  return [{
    id: BUILTIN_ARMS_UP_ID,
    name: BUILTIN_ARMS_UP_NAME,
    kind: 'builtin',
    available: analysis.available,
    confidence: analysis.confidence,
    reason: analysis.reason,
    diagnostics: summary,
  }];
}
