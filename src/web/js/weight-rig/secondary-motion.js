import { Matrix4, Quaternion, Vector3 } from 'three';

// Game rigs drive skirts, pendants and similar chains with runtime spring
// bones, so their clips carry only a constant rest pose for them. This layer
// restores that motion during clip playback for the clip's `secondary_bones`,
// recorded at extraction: bones the game leaves to runtime physics that this
// clip does not key. Each gets a tail particle that springs toward the tail
// its clip pose puts it at under the already simulated parent, damped relative
// to that moving target, pushed out of the leg capsules by a soft contact
// force, and kept at bone length inside per-joint and whole-chain bend cones.
// A held pose never moves on its own. Bones rotate to aim at their particles;
// only those bones' local rotations are rewritten.
export const SECONDARY_MOTION_SETTINGS = Object.freeze({
  frequencyHz: 3,
  // Damping acts on velocity relative to the animated target, so a moving body
  // carries its chains instead of dragging them through the air. Long chains
  // whip (each link amplifies its parent's swing) below critical damping.
  dampingRatio: 1,
  // Share of the anchors' travel the chains feel; the rest carries them along
  // rigidly, so root-motion dashes do not fling them. Turning is always felt.
  movementInfluence: 0.2,
  // Drag (1/s) on the felt velocity: the gentle trail of a moving body.
  airDrag: 1,
  // Multiplies standard gravity in rig units; 0 disables it.
  gravityScale: 1,
  jointMaxBendDegrees: 35,
  chainMaxBendDegrees: 75,
});
const STEP = 1 / 120;
const MAX_FRAME_DELTA = 0.05;
const MAX_SUBSTEPS = 6;
const LOCAL_WIDTH = 10;
const STANDARD_GRAVITY = 9.81;
// Rigs are authored in meters; this height turns the body into a unit scale.
const REFERENCE_HEIGHT = 1.6;
// A lone simulated bone (no simulated parent or child) gets a tip this far out.
const DEFAULT_TIP_LENGTH = 0.1;
// Natural frequency (Hz) of the soft leg contact; well above the chain springs.
const CONTACT_FREQUENCY = 12;
// Substeps a restarted chain spends settling on its first pose (1 s).
const SETTLE_STEPS = 120;
// Chains reseed instead of chasing a body that jumps (clip loops, cuts).
const TELEPORT_SPEED = 40;
// Thigh and calf capsules; radii are fractions of their bind-pose lengths.
const LEGS = [
  ['leftHip', 'leftKnee', 0.17],
  ['leftKnee', 'leftFoot', 0.12],
  ['rightHip', 'rightKnee', 0.17],
  ['rightKnee', 'rightFoot', 0.12],
];
const AXES = [
  [1, 0, 0],
  [-1, 0, 0],
  [0, 1, 0],
  [0, -1, 0],
  [0, 0, 1],
  [0, 0, -1],
];

const structures = new WeakMap();
const masks = new WeakMap();

const translation = new Vector3();
const scale = new Vector3();
const rotation = new Quaternion();
const head = new Vector3();
const tail = new Vector3();
const restHead = new Vector3();
const restTail = new Vector3();
const direction = new Vector3();
const restDirection = new Vector3();
const current = new Vector3();
const offset = new Vector3();
const segment = new Vector3();
const closest = new Vector3();
const legPush = new Vector3();
const residual = new Vector3();
const turned = new Matrix4();
const delta = new Quaternion();
const swing = new Quaternion();
const inverse = new Quaternion();

function controlIndex(rig, name) {
  const index = rig.humanoid_controls?.[name];
  return Number.isInteger(index) && index >= 0 && index < rig.bones.length ? index : -1;
}

// Distance from `point` to the segment `a`-`b`; its closest point goes to `out`.
function segmentDistance(point, a, b, out) {
  segment.subVectors(b, a);
  const span = segment.lengthSq();
  const t = span > 1e-18 ? Math.max(0, Math.min(1, segment.dot(out.subVectors(point, a)) / span)) : 0;
  return out.copy(a).addScaledVector(segment, t).distanceTo(point);
}

// Keep unit vector `value` within `angle` (cosine/sine given) of unit `axis`.
function limitCone(value, axis, cosine, sine) {
  const along = value.dot(axis);
  if (along >= cosine) return value;
  offset.copy(value).addScaledVector(axis, -along);
  if (offset.lengthSq() < 1e-18) {
    if (Math.abs(axis.x) < 0.9) offset.set(1, 0, 0).cross(axis);
    else offset.set(0, 1, 0).cross(axis);
  }
  return value.copy(offset.normalize()).multiplyScalar(sine).addScaledVector(axis, cosine);
}

// Fixed per rig: bind-pose frames, the body's up axis (from the authored
// humanoid head and feet), its unit scale and the leg capsules.
function structureFor(rig) {
  let cached = structures.get(rig);
  if (cached) return cached;
  const bindRotations = [];
  const bindPositions = [];
  const bindWorlds = [];
  rig.bones.forEach((bone) => {
    const local = new Quaternion(...bone.rotation).normalize();
    const matrix = new Matrix4().compose(
      new Vector3(...bone.translation),
      local,
      new Vector3(...(bone.scale || [1, 1, 1])),
    );
    if (bone.parent >= 0) {
      matrix.premultiply(bindWorlds[bone.parent]);
      local.premultiply(bindRotations[bone.parent]);
    }
    bindWorlds.push(matrix);
    bindRotations.push(local);
    bindPositions.push(new Vector3().setFromMatrixPosition(matrix));
  });
  const top = controlIndex(rig, 'head');
  const leftFoot = controlIndex(rig, 'leftFoot');
  const rightFoot = controlIndex(rig, 'rightFoot');
  let gravity = null;
  let unit = 1;
  if (top >= 0 && leftFoot >= 0 && rightFoot >= 0) {
    const up = bindPositions[top]
      .clone()
      .sub(bindPositions[leftFoot].clone().add(bindPositions[rightFoot]).multiplyScalar(0.5));
    const height = up.length();
    if (height > 1e-6 && Number.isFinite(height)) {
      unit = height / REFERENCE_HEIGHT;
      gravity = up.multiplyScalar((-STANDARD_GRAVITY * unit) / height);
    }
  }
  const legs = [];
  for (const [from, to, ratio] of LEGS) {
    const a = controlIndex(rig, from);
    const b = controlIndex(rig, to);
    const length = a >= 0 && b >= 0 ? bindPositions[a].distanceTo(bindPositions[b]) : 0;
    if (length > 1e-6) legs.push({ a, b, radius: length * ratio, bindA: bindPositions[a], bindB: bindPositions[b] });
  }
  // A lone bone hangs along whichever local axis points most along gravity at
  // rest (ZZZ chains run along local -X, the fallback).
  const leafAxes = bindRotations.map((bind) => {
    let best = AXES[1];
    let score = -Infinity;
    for (const axis of gravity ? AXES : []) {
      const value = translation.fromArray(axis).applyQuaternion(bind).dot(gravity);
      if (value > score) {
        score = value;
        best = axis;
      }
    }
    return best;
  });
  cached = { bindWorlds, gravity, unit, legs, leafAxes };
  structures.set(rig, cached);
  return cached;
}

/** The clip's `secondary_bones` as a per-bone mask, or null when it lists none. */
export function secondaryBoneMask(rig, clip) {
  if (masks.has(clip)) return masks.get(clip);
  const mask = new Uint8Array(rig.bones.length);
  let any = false;
  for (const index of Array.isArray(clip.secondary_bones) ? clip.secondary_bones : []) {
    if (Number.isInteger(index) && index >= 0 && index < mask.length) {
      mask[index] = 1;
      any = true;
    }
  }
  const result = any ? mask : null;
  masks.set(clip, result);
  return result;
}

/** Bones simulated in both masks; either mask may be null. */
export function intersectSecondaryMasks(first, second) {
  if (!first || !second) return null;
  const mask = new Uint8Array(first.length);
  let any = false;
  for (let index = 0; index < mask.length; index++) {
    if (first[index] && second[index]) {
      mask[index] = 1;
      any = true;
    }
  }
  return any ? mask : null;
}

export function createSecondaryMotion(settings = SECONDARY_MOTION_SETTINGS) {
  let owner = null;
  let shape = null;
  let positions = null; // tail particle per bone
  let velocities = null;
  let targets = null; // previous substep's spring target, for its velocity
  let started = null;
  let locals = null; // per-frame local matrices
  let animatedWorlds = null;
  let animatedRotations = null;
  let simWorlds = null;
  let simRotations = null;
  let restWorlds = null; // clip pose under unsimulated parents, for the chain cone
  let anchors = null; // nearest unsimulated ancestor of each simulated bone
  let anchorWorlds = null; // interpolated per substep
  let anchorRotations = null;
  let anchorSeen = null;
  let gravityReferences = null; // anchor orientation when its chains (re)started
  let carries = null; // unfelt anchor travel per substep
  let previousPositions = null; // anchor and leg bones at the previous frame
  let previousRotations = null;
  let tails = null; // tail point in the bone's local frame
  let aims = null; // the simulated child a bone aims at, or -1
  let descendants = null; // simulated bones below each bone
  let legStarts = null; // leg capsule ends, interpolated per substep
  let legEnds = null;
  let legRadii = null; // capsule radius per simulated bone and leg
  let haveHistory = false;

  function allocate(rig) {
    const count = rig.bones.length;
    const vectors = () => rig.bones.map(() => new Vector3());
    const quaternions = () => rig.bones.map(() => new Quaternion());
    const matrices = () => rig.bones.map(() => new Matrix4());
    owner = rig;
    shape = structureFor(rig);
    positions = new Float64Array(count * 3);
    velocities = new Float64Array(count * 3);
    targets = new Float64Array(count * 3);
    started = new Uint8Array(count);
    locals = matrices();
    animatedWorlds = matrices();
    animatedRotations = quaternions();
    simWorlds = matrices();
    simRotations = quaternions();
    restWorlds = matrices();
    anchors = new Int32Array(count);
    anchorWorlds = matrices();
    anchorRotations = quaternions();
    anchorSeen = new Uint8Array(count);
    gravityReferences = quaternions();
    carries = vectors();
    previousPositions = vectors();
    previousRotations = quaternions();
    tails = vectors();
    aims = new Int32Array(count);
    descendants = new Int32Array(count);
    legStarts = shape.legs.map(() => new Vector3());
    legEnds = shape.legs.map(() => new Vector3());
    legRadii = new Float64Array(count * LEGS.length);
    haveHistory = false;
  }

  function reset() {
    started?.fill(0);
    anchorSeen?.fill(0);
    haveHistory = false;
  }

  function substeps(elapsed) {
    if (!(elapsed > 0)) return 0;
    const clamped = Math.min(elapsed, MAX_FRAME_DELTA);
    return Math.min(MAX_SUBSTEPS, Math.max(1, Math.ceil(clamped / STEP - 1e-9)));
  }

  /**
   * Replace the local rotations of masked bones in `pose` (10 numbers per
   * bone) with their simulated pose after `elapsed` seconds of playback.
   */
  function apply(rig, pose, mask, elapsed) {
    if (!mask) {
      reset();
      return pose;
    }
    if (owner !== rig) allocate(rig);
    const bones = rig.bones;
    const count = bones.length;
    const { gravity, unit, legs, leafAxes, bindWorlds } = shape;
    // Animated world transforms of the unmodified clip pose.
    for (let index = 0; index < count; index++) {
      const base = index * LOCAL_WIDTH;
      translation.fromArray(pose, base);
      rotation.fromArray(pose, base + 3);
      scale.fromArray(pose, base + 7);
      locals[index].compose(translation, rotation, scale);
      const world = animatedWorlds[index].copy(locals[index]);
      animatedRotations[index].copy(rotation);
      const parent = bones[index].parent;
      if (parent >= 0) {
        world.premultiply(animatedWorlds[parent]);
        animatedRotations[index].premultiply(animatedRotations[parent]);
      }
      if (!mask[index]) started[index] = 0;
    }
    // Chain layout. A bone aims at the child with the most simulated bones
    // below it (the chain's spine, not a dangling pendant or a dress's side
    // bone; ties go to the earlier bone). The others hang from it as their own
    // chains or, at a chain's end, follow it rigidly.
    const roots = new Set();
    for (let index = 0; index < count; index++) {
      if (!mask[index]) continue;
      const parent = bones[index].parent;
      anchors[index] = parent >= 0 && mask[parent] ? anchors[parent] : parent;
      if (anchors[index] >= 0) roots.add(anchors[index]);
    }
    descendants.fill(0);
    for (let index = count - 1; index >= 0; index--) {
      const parent = bones[index].parent;
      if (mask[index] && parent >= 0 && mask[parent]) descendants[parent] += descendants[index] + 1;
    }
    aims.fill(-1);
    for (let index = 0; index < count; index++) {
      const parent = bones[index].parent;
      if (!mask[index] || parent < 0 || !mask[parent]) continue;
      if (aims[parent] < 0 || descendants[index] > descendants[aims[parent]]) aims[parent] = index;
    }
    for (let index = 0; index < count; index++) {
      if (!mask[index]) continue;
      const parent = bones[index].parent;
      if (aims[index] >= 0) tails[index].fromArray(pose, aims[index] * LOCAL_WIDTH);
      else if (parent >= 0 && mask[parent]) tails[index].set(0, 0, 0);
      else tails[index].fromArray(leafAxes[index]).multiplyScalar(DEFAULT_TIP_LENGTH * unit);
      // A garment authored against a leg (a fitted dress) already sits inside
      // its capsule at bind; that depth is its shape, so only push past it.
      tail.copy(tails[index]).applyMatrix4(bindWorlds[index]);
      legs.forEach((leg, number) => {
        legRadii[index * LEGS.length + number] = Math.min(
          leg.radius,
          segmentDistance(tail, leg.bindA, leg.bindB, closest),
        );
      });
    }
    const steps = substeps(elapsed);
    const step = steps ? Math.min(elapsed, MAX_FRAME_DELTA) / steps : 0;
    // A body that jumps further than it could move restarts its chains.
    if (haveHistory && steps) {
      const limit = TELEPORT_SPEED * unit * Math.max(step * steps, STEP);
      for (const anchor of roots) {
        if (head.setFromMatrixPosition(animatedWorlds[anchor]).distanceTo(previousPositions[anchor]) > limit) {
          reset();
          break;
        }
      }
    }
    // Anchors move linearly between frames, so each substep carries the same
    // unfelt share of their travel. Gravity is measured from the orientation
    // an anchor had when its chains (re)started.
    for (const anchor of roots) {
      head.setFromMatrixPosition(animatedWorlds[anchor]);
      carries[anchor]
        .subVectors(head, previousPositions[anchor])
        .multiplyScalar(haveHistory && steps ? (1 - settings.movementInfluence) / steps : 0);
      if (!anchorSeen[anchor]) gravityReferences[anchor].copy(animatedRotations[anchor]);
      anchorSeen[anchor] = 1;
    }
    const omega = 2 * Math.PI * settings.frequencyHz;
    const stiffness = omega * omega;
    const damping = 2 * settings.dampingRatio * omega;
    const drag = settings.airDrag;
    const jointCos = Math.cos((settings.jointMaxBendDegrees * Math.PI) / 180);
    const jointSin = Math.sin((settings.jointMaxBendDegrees * Math.PI) / 180);
    const chainCos = Math.cos((settings.chainMaxBendDegrees * Math.PI) / 180);
    const chainSin = Math.sin((settings.chainMaxBendDegrees * Math.PI) / 180);
    const contact = (2 * Math.PI * CONTACT_FREQUENCY) ** 2;

    // One pass over the chains with anchors and legs interpolated to `alpha`
    // of the way from the previous frame. `dt` 0 only re-aims at particles.
    const pass = (alpha, dt) => {
      const blend = haveHistory ? alpha : 1;
      legs.forEach((leg, number) => {
        legStarts[number].setFromMatrixPosition(animatedWorlds[leg.a]);
        legEnds[number].setFromMatrixPosition(animatedWorlds[leg.b]);
        if (blend < 1) {
          legStarts[number].lerpVectors(previousPositions[leg.a], legStarts[number], blend);
          legEnds[number].lerpVectors(previousPositions[leg.b], legEnds[number], blend);
        }
      });
      for (const anchor of roots) {
        // Turn and move the anchor's own matrix, keeping its (maybe mirrored) scale.
        anchorRotations[anchor].copy(animatedRotations[anchor]);
        anchorWorlds[anchor].copy(animatedWorlds[anchor]);
        if (blend < 1) {
          anchorRotations[anchor].copy(previousRotations[anchor]).slerp(animatedRotations[anchor], blend);
          head.setFromMatrixPosition(animatedWorlds[anchor]).lerpVectors(previousPositions[anchor], head, blend);
          swing.copy(animatedRotations[anchor]).invert().premultiply(anchorRotations[anchor]);
          anchorWorlds[anchor].premultiply(turned.makeRotationFromQuaternion(swing)).setPosition(head);
        }
      }
      for (let index = 0; index < count; index++) {
        if (!mask[index]) continue;
        const parent = bones[index].parent;
        const simulatedParent = parent >= 0 && mask[parent];
        // Clip pose under the simulated parent, and under the unsimulated one.
        const world = simWorlds[index].copy(locals[index]);
        const rest = restWorlds[index].copy(locals[index]);
        const rotationOut = simRotations[index].fromArray(pose, index * LOCAL_WIDTH + 3);
        if (parent >= 0) {
          world.premultiply(simulatedParent ? simWorlds[parent] : anchorWorlds[parent]);
          rest.premultiply(simulatedParent ? restWorlds[parent] : anchorWorlds[parent]);
          rotationOut.premultiply(simulatedParent ? simRotations[parent] : anchorRotations[parent]);
        }
        head.setFromMatrixPosition(world);
        tail.copy(tails[index]).applyMatrix4(world);
        restHead.setFromMatrixPosition(rest);
        restTail.copy(tails[index]).applyMatrix4(rest);
        direction.subVectors(tail, head);
        const length = direction.length();
        restDirection.subVectors(restTail, restHead);
        if (length < 1e-9 || restDirection.lengthSq() < 1e-18) continue;
        direction.divideScalar(length);
        restDirection.normalize();
        const at = index * 3;
        const anchor = anchors[index];
        if (!started[index]) {
          tail.toArray(positions, at);
          tail.toArray(targets, at);
          velocities.fill(0, at, at + 3);
          started[index] = 1;
        } else if (dt > 0 && anchor >= 0) {
          // Move the particle and its target history with the carried travel.
          for (let axis = 0; axis < 3; axis++) {
            const value = carries[anchor].getComponent(axis);
            positions[at + axis] += value;
            targets[at + axis] += value;
          }
        }
        current.fromArray(positions, at);
        offset.copy(current);
        if (dt > 0) {
          residual.set(0, 0, 0);
          // Gravity the clip pose does not already hold: g - (anchor * start^-1) g.
          // (Bind is no reference: an A-pose arm would make sleeves sag at rest.)
          if (gravity && anchor >= 0) {
            swing.copy(gravityReferences[anchor]).invert().premultiply(anchorRotations[anchor]);
            residual.copy(gravity).applyQuaternion(swing).negate().add(gravity).multiplyScalar(settings.gravityScale);
          }
          // Soft leg contact, in proportion to depth: a hard projection would
          // snap a link across a leg whose axis passes the tail.
          legs.forEach((_leg, number) => {
            const radius = legRadii[index * LEGS.length + number];
            const distance = segmentDistance(current, legStarts[number], legEnds[number], closest);
            if (distance < radius && distance > 1e-9) {
              legPush.subVectors(current, closest);
              residual.addScaledVector(legPush, (contact * (radius - distance)) / distance);
            }
          });
          for (let axis = 0; axis < 3; axis++) {
            const goal = tail.getComponent(axis);
            const goalVelocity = (goal - targets[at + axis]) / dt;
            const position = current.getComponent(axis);
            const velocity = velocities[at + axis];
            const force = stiffness * (goal - position) - damping * (velocity - goalVelocity) - drag * velocity;
            offset.setComponent(axis, position + (velocity + (force + residual.getComponent(axis)) * dt) * dt);
          }
          tail.toArray(targets, at);
        }
        // Bone length, then the joint and whole-chain bend cones.
        segment.subVectors(offset, head);
        if (segment.lengthSq() < 1e-18) segment.copy(direction);
        segment.normalize();
        limitCone(segment, direction, jointCos, jointSin);
        limitCone(segment, restDirection, chainCos, chainSin);
        if (dt > 0) {
          offset.copy(head).addScaledVector(segment, length);
          for (let axis = 0; axis < 3; axis++)
            velocities[at + axis] = (offset.getComponent(axis) - current.getComponent(axis)) / dt;
          offset.toArray(positions, at);
        }
        delta.setFromUnitVectors(direction, segment);
        rotationOut.premultiply(delta);
        world.premultiply(turned.makeRotationFromQuaternion(delta)).setPosition(head);
      }
    };
    // Restarted chains settle on this pose first, so gravity and leg contact
    // never play out as motion of their own when playback starts or seeks.
    if (!haveHistory) for (let iteration = 0; iteration < SETTLE_STEPS; iteration++) pass(1, STEP);
    if (steps) for (let iteration = 1; iteration <= steps; iteration++) pass(iteration / steps, step);
    else pass(1, 0);
    // Write simulated rotations back as locals under their (final) parents.
    for (let index = 0; index < count; index++) {
      if (!mask[index]) continue;
      const parent = bones[index].parent;
      rotation.copy(simRotations[index]);
      if (parent >= 0)
        rotation.premultiply(inverse.copy(mask[parent] ? simRotations[parent] : animatedRotations[parent]).invert());
      rotation.normalize().toArray(pose, index * LOCAL_WIDTH + 3);
    }
    // Remember the anchors and legs for next frame's interpolation.
    for (const anchor of roots) {
      previousPositions[anchor].setFromMatrixPosition(animatedWorlds[anchor]);
      previousRotations[anchor].copy(animatedRotations[anchor]);
    }
    for (const leg of legs) {
      previousPositions[leg.a].setFromMatrixPosition(animatedWorlds[leg.a]);
      previousPositions[leg.b].setFromMatrixPosition(animatedWorlds[leg.b]);
    }
    haveHistory = true;
    return pose;
  }

  return { apply, reset };
}
