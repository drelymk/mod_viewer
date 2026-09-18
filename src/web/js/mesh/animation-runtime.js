// Playback for backend-normalized, frame-baked mesh attributes.

import { decodeF32 } from '../textures/decode.js';
import { getControlValue, dnfSatisfied } from '../editing/control-state.js';
import { requestRender } from '../scene/render-scheduler.js';

const clocks = new Map();
let rafId = null;

function positiveInteger(value, fallback = 0) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

export function frameForElapsed(clock, elapsedSeconds) {
  const start = Number(clock?.frame_start);
  const end = Number(clock?.frame_end);
  const fps = Number(clock?.fps ?? 0);
  if (!Number.isFinite(start) || !Number.isFinite(end)
      || end < start || !Number.isFinite(fps) || fps <= 0) return null;
  const count = end - start + 1;
  const tick = Math.floor(Math.max(0, elapsedSeconds) * fps);
  return start + (tick % count + count) % count;
}

function clockActive(clock) {
  return dnfSatisfied(clock?.conditions || []);
}

function clockFps(clock) {
  const configured = clock?.fps_var
    ? getControlValue(clock.fps_var) : undefined;
  const configuredSpeed = clock?.speed_var
    ? getControlValue(clock.speed_var) : undefined;
  const fps = Number(configured ?? clock?.fps);
  const speed = Number(configuredSpeed ?? clock?.speed ?? 1);
  if (!Number.isFinite(fps) || fps <= 0
      || !Number.isFinite(speed) || speed <= 0) return 0;
  return fps * speed;
}

function restoreCanonical(mesh) {
  const position = mesh.geometry?.attributes?.position;
  const base = mesh.userData?.basePositions;
  if (position && base && position.array.length === base.length) {
    position.array.set(base);
    position.needsUpdate = true;
  }
  const normal = mesh.geometry?.attributes?.normal;
  const baseNormals = mesh.userData?.baseNormals;
  if (normal && baseNormals && normal.array.length === baseNormals.length) {
    normal.array.set(baseNormals);
    normal.needsUpdate = true;
  } else if (normal) {
    mesh.geometry.computeVertexNormals();
  }
  mesh.geometry?.computeBoundingBox?.();
  mesh.geometry?.computeBoundingSphere?.();
}

function applyFrame(mesh, state, frame) {
  const index = frame - state.clock.frame_start;
  if (index < 0 || index >= state.frameCount) return false;
  if (state.lastFrame === frame) return false;
  const position = mesh.geometry?.attributes?.position;
  const offset = index * state.positionFrameFloats;
  const positionFrame = state.positions.subarray(
    offset, offset + state.positionFrameFloats);
  if (!position || position.array.length !== positionFrame.length) return false;
  position.array.set(positionFrame);
  position.needsUpdate = true;

  const normal = mesh.geometry?.attributes?.normal;
  if (state.normals && normal) {
    const normalOffset = index * state.normalFrameFloats;
    const normalFrame = state.normals.subarray(
      normalOffset, normalOffset + state.normalFrameFloats);
    if (normal.array.length === normalFrame.length) {
      normal.array.set(normalFrame);
      normal.needsUpdate = true;
    }
  } else if (normal) {
    mesh.geometry.computeVertexNormals();
  }
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  state.lastFrame = frame;
  return true;
}

function schedule() {
  if (rafId !== null || typeof requestAnimationFrame !== 'function') return;
  rafId = requestAnimationFrame(tick);
}

function tick(now) {
  rafId = null;
  let playing = false;
  let changed = false;
  for (const state of clocks.values()) {
    const active = clockActive(state.clock);
    if (!active) {
      if (state.active) {
        state.meshes.forEach(mesh => {
          const meshState = state.meshesByMesh.get(mesh);
          if (mesh.userData?.animationSuspended === true) {
            meshState.lastFrame = null;
            return;
          }
          restoreCanonical(mesh);
          meshState.lastFrame = null;
        });
        state.lastFrame = null;
        state.active = false;
        changed = true;
      }
      continue;
    }
    const fps = clockFps(state.clock);
    if (!fps) continue;
    playing = true;
    if (!state.active) {
      state.startedAt = now;
      state.lastFrame = null;
      state.active = true;
    }
    const clock = {...state.clock, fps};
    const frame = frameForElapsed(clock, (now - state.startedAt) / 1000);
    if (frame === null) continue;
    state.meshes.forEach(mesh => {
      if (mesh.userData?.animationSuspended === true) {
        state.meshesByMesh.get(mesh).lastFrame = null;
        return;
      }
      changed = applyFrame(mesh, state.meshesByMesh.get(mesh), frame) || changed;
    });
  }
  if (changed) requestRender();
  if (playing) schedule();
}

export function registerAnimatedMesh(mesh, animationId, geometry, animationClocks) {
  const clock = animationClocks?.[animationId];
  if (!mesh || !clock || !geometry?.positions) return false;
  let state = clocks.get(animationId);
  if (!state) {
    state = {
      clock: {...clock}, meshes: new Set(), meshesByMesh: new Map(),
      startedAt: 0, lastFrame: null, active: false,
    };
    clocks.set(animationId, state);
  }
  const frameCount = positiveInteger(geometry.frames);
  const positionFrameBytes = positiveInteger(geometry.position_frame_bytes);
  const positions = frameCount && positionFrameBytes
    ? decodeF32(geometry.positions) : null;
  if (!positions) return false;
  const normalFrameBytes = positiveInteger(geometry.normal_frame_bytes);
  const normals = geometry.normals && normalFrameBytes
    ? decodeF32(geometry.normals) : null;
  const meshState = {
    clock: state.clock,
    positions,
    normals,
    frameCount,
    positionFrameFloats: positionFrameBytes / 4,
    normalFrameFloats: normalFrameBytes / 4,
    lastFrame: null,
  };
  state.meshes.add(mesh);
  state.meshesByMesh.set(mesh, meshState);
  mesh.userData.animationState = meshState;
  schedule();
  return true;
}

export function resetAnimationRuntime() {
  if (rafId !== null && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(rafId);
  }
  rafId = null;
  clocks.clear();
}

/** Wake a clock after control/state values change without rebuilding meshes. */
export function wakeAnimationRuntime() {
  schedule();
}

export function animationRuntimeSnapshot() {
  return {
    clocks: clocks.size,
    meshes: [...clocks.values()].reduce(
      (total, state) => total + state.meshes.size, 0),
    rafActive: rafId !== null,
  };
}
