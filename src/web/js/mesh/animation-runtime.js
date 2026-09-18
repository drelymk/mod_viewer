// Playback for backend-normalized, frame-baked mesh attributes.

import { decodeF32 } from '../textures/decode.js';
import { getControlValue, dnfSatisfied } from '../editing/control-state.js';
import {
  invalidateCharacterShadowGeometry,
  invalidateCharacterShadowMap,
} from '../scene/shadow-invalidation.js';
import { requestRender } from '../scene/render-scheduler.js';

const tracks = new Map();
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
    mesh.geometry.computeVertexNormals?.();
  }
}

function installAnimationBounds(mesh, bounds) {
  const geometry = mesh.geometry;
  if (!geometry) return;
  const min = bounds?.min;
  const max = bounds?.max;
  if (!Array.isArray(min) || min.length !== 3
      || !Array.isArray(max) || max.length !== 3
      || !min.every(Number.isFinite) || !max.every(Number.isFinite)) {
    geometry.computeBoundingBox?.();
    geometry.computeBoundingSphere?.();
    return;
  }
  geometry.computeBoundingBox?.();
  const box = geometry.boundingBox;
  if (box?.min?.set && box?.max?.set) {
    box.min.set(min[0], min[1], min[2]);
    box.max.set(max[0], max[1], max[2]);
  }
  geometry.computeBoundingSphere?.();
  const sphere = geometry.boundingSphere;
  if (sphere?.center?.set) {
    const center = [
      (min[0] + max[0]) / 2,
      (min[1] + max[1]) / 2,
      (min[2] + max[2]) / 2,
    ];
    const radius = Math.hypot(
      max[0] - center[0], max[1] - center[1], max[2] - center[2]);
    sphere.center.set(center[0], center[1], center[2]);
    sphere.radius = radius;
  }
}

function applyFrame(mesh, state, frame) {
  const index = frame - state.frameStart;
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
    mesh.geometry.computeVertexNormals?.();
  }
  state.lastFrame = frame;
  return true;
}

function schedule() {
  if (rafId !== null || typeof requestAnimationFrame !== 'function') return;
  rafId = requestAnimationFrame(tick);
}

function selectedClock(state) {
  for (const id of state.clockIds) {
    const clock = state.clocks[id];
    if (clock && clockActive(clock)) return {id, clock};
  }
  return null;
}

function tick(now) {
  rafId = null;
  let playing = false;
  let changed = false;
  for (const state of tracks.values()) {
    const selected = selectedClock(state);
    if (!selected) {
      if (state.active) {
        state.meshes.forEach(mesh => {
          const meshState = state.meshesByMesh.get(mesh);
          if (mesh.userData?.animationSuspended === true) {
            meshState.lastFrame = null;
            return;
          }
          restoreCanonical(mesh);
          meshState.lastFrame = null;
          if (mesh.visible !== false) changed = true;
        });
        state.lastFrame = null;
        state.activeClockId = null;
        state.active = false;
      }
      continue;
    }
    const fps = clockFps(selected.clock);
    if (!fps) continue;
    const visibleMeshes = [...state.meshes].filter(mesh =>
      mesh.visible !== false && mesh.userData?.animationSuspended !== true);
    if (!visibleMeshes.length) continue;
    playing = true;
    if (!state.active || state.activeClockId !== selected.id) {
      state.startedAt = now;
      state.lastFrame = null;
      state.activeClockId = selected.id;
      state.active = true;
    }
    const clock = {...selected.clock, fps};
    const frame = frameForElapsed(clock, (now - state.startedAt) / 1000);
    if (frame === null) continue;
    state.meshes.forEach(mesh => {
      if (mesh.userData?.animationSuspended === true
          || mesh.visible === false) {
        state.meshesByMesh.get(mesh).lastFrame = null;
        return;
      }
      changed = applyFrame(mesh, state.meshesByMesh.get(mesh), frame) || changed;
    });
  }
  if (changed) {
    // Character shadows are demand-driven. One invalidation covers every
    // mesh changed during this animation tick.
    invalidateCharacterShadowMap({request: false});
    requestRender();
  }
  if (playing) schedule();
}

export function registerAnimatedMesh(mesh, animationId, geometry, animationClocks) {
  const clockIds = Array.isArray(geometry?.clock_ids)
    && geometry.clock_ids.length ? geometry.clock_ids : [animationId];
  const clocksForTrack = Object.fromEntries(clockIds
    .map(id => [id, animationClocks?.[id]])
    .filter(([, clock]) => !!clock));
  if (!mesh || !Object.keys(clocksForTrack).length || !geometry?.positions) {
    return false;
  }
  let state = tracks.get(animationId);
  if (!state) {
    state = {
      clocks: clocksForTrack, clockIds: Object.keys(clocksForTrack),
      meshes: new Set(), meshesByMesh: new Map(), startedAt: 0,
      lastFrame: null, activeClockId: null, active: false,
    };
    tracks.set(animationId, state);
  } else {
    Object.assign(state.clocks, clocksForTrack);
    state.clockIds = [...new Set([...state.clockIds, ...clockIds])]
      .filter(id => state.clocks[id]);
  }
  const frameCount = positiveInteger(geometry.frames);
  const positionFrameBytes = positiveInteger(geometry.position_frame_bytes);
  const positions = frameCount && positionFrameBytes
    ? decodeF32(geometry.positions) : null;
  if (!positions) return false;
  const normalFrameBytes = positiveInteger(geometry.normal_frame_bytes);
  const normals = geometry.normals && normalFrameBytes
    ? decodeF32(geometry.normals) : null;
  const firstClock = state.clocks[state.clockIds[0]];
  const meshState = {
    frameStart: Number(geometry.frame_start ?? firstClock?.frame_start ?? 0),
    animationBounds: geometry.bounds || null,
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
  installAnimationBounds(mesh, geometry.bounds);
  schedule();
  return true;
}

export function resetAnimationRuntime() {
  if (rafId !== null && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(rafId);
  }
  rafId = null;
  tracks.clear();
}

/** Wake tracks after control or visibility state changes without rebuilding meshes. */
export function wakeAnimationRuntime() {
  schedule();
}

/** Resume a track after Rig/Physics releases ownership of one mesh. */
export function resumeAnimatedMesh(mesh) {
  for (const state of tracks.values()) {
    if (!state.meshes.has(mesh)) continue;
    const meshState = state.meshesByMesh.get(mesh);
    restoreCanonical(mesh);
    installAnimationBounds(mesh, meshState?.animationBounds);
    if (meshState) meshState.lastFrame = null;
    invalidateCharacterShadowGeometry({request: false});
    requestRender();
    schedule();
    return true;
  }
  return false;
}

export function animationRuntimeSnapshot() {
  return {
    clocks: tracks.size,
    meshes: [...tracks.values()].reduce(
      (total, state) => total + state.meshes.size, 0),
    rafActive: rafId !== null,
  };
}
