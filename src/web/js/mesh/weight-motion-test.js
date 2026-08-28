import { translateModel } from '../scene/scene.js';

const MAX_FRAME_DELTA = 0.05;
const VELOCITY_EPSILON = 1e-5;
const DEFAULT_SPEED_SCALE = 0.5;
const DEFAULT_ACCELERATION_SCALE = 1.5;

let frameId = null;
const state = {
  meshes: [],
  axis: 'X',
  velocity: 0,
  targetVelocity: 0,
  maxAcceleration: 0,
  speedScale: DEFAULT_SPEED_SCALE,
  accelerationScale: DEFAULT_ACCELERATION_SCALE,
  radius: 0,
  lastTimestamp: null,
  running: false,
  stopRequested: false,
};

function axisVector(axis) {
  if (axis === 'Y') return [0, 1, 0];
  if (axis === 'Z') return [0, 0, 1];
  return [1, 0, 0];
}

function finitePositive(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function scheduleFrame() {
  if (frameId !== null || !state.running
      || typeof window === 'undefined'
      || typeof window.requestAnimationFrame !== 'function') return;
  frameId = window.requestAnimationFrame(timestamp => {
    frameId = null;
    advance(timestamp);
  });
}

function reportVelocity(velocity) {
  const axis = axisVector(state.axis);
  const linearVelocityWorld = axis.map(value => value * velocity);
  return linearVelocityWorld;
}

function moveTowards(current, target, distance) {
  if (current < target) return Math.min(current + distance, target);
  if (current > target) return Math.max(current - distance, target);
  return target;
}

function advance(timestamp) {
  if (!state.running) return;
  if (!state.meshes.length) {
    cancelContinuousMotionTest();
    return;
  }
  const currentTimestamp = Number(timestamp);
  if (!Number.isFinite(currentTimestamp)) {
    cancelContinuousMotionTest();
    return;
  }
  if (state.lastTimestamp === null) {
    state.lastTimestamp = currentTimestamp;
    translateModel(state.meshes, [0, 0, 0], {
      kinematics: {linearVelocityWorld: reportVelocity(state.velocity)},
    });
    scheduleFrame();
    return;
  }
  const dt = Math.max(0, Math.min(
    MAX_FRAME_DELTA, (currentTimestamp - state.lastTimestamp) / 1000));
  state.lastTimestamp = currentTimestamp;
  const oldVelocity = state.velocity;
  const nextVelocity = moveTowards(
    oldVelocity, state.targetVelocity, state.maxAcceleration * dt);
  const distance = (oldVelocity + nextVelocity) * 0.5 * dt;
  const delta = axisVector(state.axis).map(value => value * distance);
  state.velocity = nextVelocity;
  translateModel(state.meshes, delta, {
    kinematics: {linearVelocityWorld: reportVelocity(nextVelocity)},
  });
  if (state.stopRequested && Math.abs(nextVelocity) <= VELOCITY_EPSILON
      && Math.abs(state.targetVelocity) <= VELOCITY_EPSILON) {
    state.velocity = 0;
    state.targetVelocity = 0;
    state.running = false;
    state.stopRequested = false;
    state.lastTimestamp = null;
    state.meshes = [];
    return;
  }
  scheduleFrame();
}

export function getContinuousMotionState() {
  return {
    ...state,
    meshes: state.meshes.slice(),
    frameId,
  };
}

export function setContinuousMotionAxis(axis) {
  if (!['X', 'Y', 'Z'].includes(axis)) return false;
  state.axis = axis;
  return true;
}

export function startContinuousMotion(meshes = [], {
  axis = state.axis,
  speedScale = DEFAULT_SPEED_SCALE,
  accelerationScale = DEFAULT_ACCELERATION_SCALE,
  radius,
  direction = 1,
} = {}) {
  if (!Array.isArray(meshes) || !meshes.length
      || !['X', 'Y', 'Z'].includes(axis)) return false;
  const modelRadius = finitePositive(radius);
  const speed = finitePositive(speedScale);
  const acceleration = finitePositive(accelerationScale);
  const sign = Number(direction) < 0 ? -1 : 1;
  if (!modelRadius || !speed || !acceleration) return false;
  const wasRunning = state.running;
  state.meshes = meshes;
  state.axis = axis;
  state.speedScale = Number(speedScale);
  state.accelerationScale = Number(accelerationScale);
  state.radius = modelRadius;
  state.targetVelocity = sign * speed * modelRadius;
  state.maxAcceleration = acceleration * modelRadius;
  state.stopRequested = false;
  if (!wasRunning) {
    state.velocity = 0;
    state.lastTimestamp = null;
    state.running = true;
  }
  scheduleFrame();
  return true;
}

export function stopContinuousMotion() {
  if (!state.running) return false;
  state.targetVelocity = 0;
  state.stopRequested = true;
  scheduleFrame();
  return true;
}

export function cancelContinuousMotionTest() {
  if (frameId !== null && typeof window !== 'undefined'
      && typeof window.cancelAnimationFrame === 'function') {
    window.cancelAnimationFrame(frameId);
  }
  frameId = null;
  state.meshes = [];
  state.velocity = 0;
  state.targetVelocity = 0;
  state.maxAcceleration = 0;
  state.radius = 0;
  state.lastTimestamp = null;
  state.running = false;
  state.stopRequested = false;
}

export function isContinuousMotionRunning() {
  return state.running;
}
