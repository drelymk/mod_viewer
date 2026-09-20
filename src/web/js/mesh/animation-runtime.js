// Playback for backend-normalized, frame-baked mesh attributes.

import { decodeF32, decodeI32 } from '../textures/decode.js';
import {
  getControlValue, setControlValue, dnfSatisfied,
} from '../editing/control-state.js';
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

function signedUnit(value) {
  return value < 0 ? -1 : value > 0 ? 1 : 0;
}

function gimiOperand(operand, fallback = 0) {
  if (operand === null || operand === undefined) return fallback;
  if (typeof operand === 'number') {
    return Number.isFinite(operand) ? operand : fallback;
  }
  if (operand.kind === 'literal') {
    const value = Number(operand.value);
    return Number.isFinite(value) ? value : fallback;
  }
  if (operand.kind === 'variable') {
    const value = Number(getControlValue(operand.variable));
    const offset = Number(operand.offset || 0);
    return Number.isFinite(value) ? value + offset : fallback;
  }
  return fallback;
}

function gimiClockRate(clock) {
  const rate = gimiOperand(clock?.rate, 0);
  return Number.isFinite(rate) && rate > 0 ? rate : 0;
}

function gimiClockAdvancing(clock) {
  return !!clock && dnfSatisfied(clock.advance_conditions || [])
    && gimiClockRate(clock) > 0;
}

function gimiClockRange(clock) {
  const target = gimiOperand(clock?.wrap_target, Number.NaN);
  const limit = gimiOperand(clock?.wrap_limit, Number.NaN);
  if (!Number.isFinite(target) || !Number.isFinite(limit)) return null;
  return {
    target, limit, low: Math.min(target, limit), high: Math.max(target, limit),
  };
}

function sameGimiRange(left, right) {
  return left?.target === right?.target && left?.limit === right?.limit;
}

function syncGimiClockRange(track, clock, phaseKey) {
  const range = gimiClockRange(clock);
  const previous = track.resolvedRanges[phaseKey];
  track.resolvedRanges[phaseKey] = range;
  if (previous === undefined || sameGimiRange(previous, range) || !range) {
    return;
  }
  if (track[phaseKey] < range.low || track[phaseKey] > range.high) {
    track[phaseKey] = range.target;
    track.dirty = true;
  }
}

function applyGimiPose(mesh, meshState, track) {
  const position = mesh.geometry?.attributes?.position;
  const normal = mesh.geometry?.attributes?.normal;
  const basePositions = mesh.userData?.basePositions;
  const baseNormals = meshState.baseNormals;
  const vertexCount = meshState.vertexCount;
  if (!position || !normal || !basePositions || !baseNormals
      || position.array.length < vertexCount * 3
      || normal.array.length < vertexCount * 3
      || baseNormals.length < vertexCount * 3) return false;

  const positions = position.array;
  const normals = normal.array;
  const shapeWeights = meshState.shapePasses.map(pass =>
    Number(pass.amplitude) * Math.sin(
      (track.shapePhase + pass.phaseOffset) * Number(pass.angularScale))
    + Number(pass.bias));

  const hasPose = !!(track.poseFrames && track.poseBoneCount
    && meshState.poseActive);
  const frameValue = hasPose ? Math.max(0, track.poseTime) : 0;
  const frame = hasPose ? Math.min(
    track.poseFrameCount - 1, Math.floor(frameValue)) : 0;
  const nextFrame = hasPose
    ? Math.min(track.poseFrameCount - 1, frame + 1) : 0;
  const inter = hasPose ? Math.min(1, Math.max(0, frameValue - frame)) : 0;
  const weights = meshState.weights;
  const indices = meshState.indices;
  const pose = track.poseFrames;
  const boneCount = track.poseBoneCount;

  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const blendOffset = vertex * 4;
    const positionOffset = vertex * 3;
    let px = basePositions[positionOffset];
    let py = basePositions[positionOffset + 1];
    let pz = basePositions[positionOffset + 2];
    let nx = baseNormals[positionOffset];
    let ny = baseNormals[positionOffset + 1];
    let nz = baseNormals[positionOffset + 2];
    for (let passIndex = 0;
      passIndex < meshState.shapePasses.length; passIndex += 1) {
      const pass = meshState.shapePasses[passIndex];
      const source = vertex * 6;
      const weight = shapeWeights[passIndex];
      px += pass.deltas[source] * weight;
      py += pass.deltas[source + 1] * weight;
      pz += pass.deltas[source + 2] * weight;
      nx += pass.deltas[source + 3] * weight;
      ny += pass.deltas[source + 4] * weight;
      nz += pass.deltas[source + 5] * weight;
    }

    if (!hasPose || meshState.poseActive[vertex] < 0.5) {
      positions[positionOffset] = px;
      positions[positionOffset + 1] = py;
      positions[positionOffset + 2] = pz;
      normals[positionOffset] = nx;
      normals[positionOffset + 1] = ny;
      normals[positionOffset + 2] = nz;
      continue;
    }

    let scaleX = 0;
    let scaleY = 0;
    let scaleZ = 0;
    let biasX = 0;
    let biasY = 0;
    let biasZ = 0;
    let qrX = 0;
    let qrY = 0;
    let qrZ = 0;
    let qrW = 0;
    let qdX = 0;
    let qdY = 0;
    let qdZ = 0;
    let qdW = 0;
    const referenceBone = indices[blendOffset];
    const referencePrev = (frame * boneCount + referenceBone) * 14;
    for (let influence = 0; influence < 4; influence += 1) {
      const weight = weights[blendOffset + influence];
      if (Math.abs(weight) <= 1e-8) continue;
      const bone = indices[blendOffset + influence];
      if (bone < 0 || bone >= boneCount) continue;
      const prev = (frame * boneCount + bone) * 14;
      const next = (nextFrame * boneCount + bone) * 14;
      const prevScaleX = pose[prev];
      const prevScaleY = pose[prev + 1];
      const prevScaleZ = pose[prev + 2];
      const prevBiasX = pose[prev + 3];
      const prevBiasY = pose[prev + 4];
      const prevBiasZ = pose[prev + 5];
      const nextScaleX = pose[next];
      const nextScaleY = pose[next + 1];
      const nextScaleZ = pose[next + 2];
      const nextBiasX = pose[next + 3];
      const nextBiasY = pose[next + 4];
      const nextBiasZ = pose[next + 5];
      scaleX += weight * (prevScaleX * (1 - inter) + nextScaleX * inter);
      scaleY += weight * (prevScaleY * (1 - inter) + nextScaleY * inter);
      scaleZ += weight * (prevScaleZ * (1 - inter) + nextScaleZ * inter);
      biasX += weight * (prevBiasX * (1 - inter) + nextBiasX * inter);
      biasY += weight * (prevBiasY * (1 - inter) + nextBiasY * inter);
      biasZ += weight * (prevBiasZ * (1 - inter) + nextBiasZ * inter);

      const prevSign = influence === 0 ? 1 : signedUnit(
        pose[referencePrev + 6] * pose[prev + 6]
        + pose[referencePrev + 7] * pose[prev + 7]
        + pose[referencePrev + 8] * pose[prev + 8]
        + pose[referencePrev + 9] * pose[prev + 9]);
      const nextSign = signedUnit(
        pose[referencePrev + 6] * pose[next + 6]
        + pose[referencePrev + 7] * pose[next + 7]
        + pose[referencePrev + 8] * pose[next + 8]
        + pose[referencePrev + 9] * pose[next + 9]);
      qrX += (pose[prev + 6] * weight * (1 - inter) * prevSign
              + pose[next + 6] * weight * inter * nextSign);
      qrY += (pose[prev + 7] * weight * (1 - inter) * prevSign
              + pose[next + 7] * weight * inter * nextSign);
      qrZ += (pose[prev + 8] * weight * (1 - inter) * prevSign
              + pose[next + 8] * weight * inter * nextSign);
      qrW += (pose[prev + 9] * weight * (1 - inter) * prevSign
              + pose[next + 9] * weight * inter * nextSign);
      qdX += (pose[prev + 10] * weight * (1 - inter) * prevSign
              + pose[next + 10] * weight * inter * nextSign);
      qdY += (pose[prev + 11] * weight * (1 - inter) * prevSign
              + pose[next + 11] * weight * inter * nextSign);
      qdZ += (pose[prev + 12] * weight * (1 - inter) * prevSign
              + pose[next + 12] * weight * inter * nextSign);
      qdW += (pose[prev + 13] * weight * (1 - inter) * prevSign
              + pose[next + 13] * weight * inter * nextSign);
    }

    const qrLength = Math.max(1e-6,
      Math.hypot(qrX, qrY, qrZ, qrW));
    qrX /= qrLength;
    qrY /= qrLength;
    qrZ /= qrLength;
    qrW /= qrLength;
    qdX /= qrLength;
    qdY /= qrLength;
    qdZ /= qrLength;
    qdW /= qrLength;

    const posedX = px * scaleX + biasX;
    const posedY = py * scaleY + biasY;
    const posedZ = pz * scaleZ + biasZ;
    const m00 = 1 - 2 * qrY * qrY - 2 * qrZ * qrZ;
    const m10 = 2 * (qrX * qrY + qrW * qrZ);
    const m20 = 2 * (qrX * qrZ - qrW * qrY);
    const t0 = 2 * (-qdW * qrX + qdX * qrW - qdY * qrZ + qdZ * qrY);
    const m01 = 2 * (qrX * qrY - qrW * qrZ);
    const m11 = 1 - 2 * qrX * qrX - 2 * qrZ * qrZ;
    const m21 = 2 * (qrY * qrZ + qrW * qrX);
    const t1 = 2 * (-qdW * qrY + qdX * qrZ + qdY * qrW - qdZ * qrX);
    const m02 = 2 * (qrX * qrZ + qrW * qrY);
    const m12 = 2 * (qrY * qrZ - qrW * qrX);
    const m22 = 1 - 2 * qrX * qrX - 2 * qrY * qrY;
    const t2 = 2 * (-qdW * qrZ - qdX * qrY + qdY * qrX + qdZ * qrW);
    const transformedX = m00 * posedX + m01 * posedY + m02 * posedZ + t0;
    const transformedY = m10 * posedX + m11 * posedY + m12 * posedZ + t1;
    const transformedZ = m20 * posedX + m21 * posedY + m22 * posedZ + t2;
    positions[positionOffset] = transformedX;
    positions[positionOffset + 1] = transformedY;
    positions[positionOffset + 2] = transformedZ;
    const transformedNormalX = m00 * nx + m01 * ny + m02 * nz;
    const transformedNormalY = m10 * nx + m11 * ny + m12 * nz;
    const transformedNormalZ = m20 * nx + m21 * ny + m22 * nz;
    const normalLength = Math.hypot(
      transformedNormalX, transformedNormalY, transformedNormalZ);
    normals[positionOffset] = normalLength > 1e-12
      ? transformedNormalX / normalLength : 0;
    normals[positionOffset + 1] = normalLength > 1e-12
      ? transformedNormalY / normalLength : 0;
    normals[positionOffset + 2] = normalLength > 1e-12
      ? transformedNormalZ / normalLength : 0;
  }
  position.needsUpdate = true;
  normal.needsUpdate = true;
  return true;
}

function applyGimiTrack(track) {
  let changed = false;
  for (const mesh of track.meshes) {
    if (mesh.visible === false || mesh.userData?.animationSuspended === true) {
      track.meshesByMesh.get(mesh).lastApplied = false;
      continue;
    }
    changed = applyGimiPose(mesh, track.meshesByMesh.get(mesh), track) || changed;
  }
  return changed;
}

function advanceGimiClock(track, clock, phaseKey, dt) {
  if (!gimiClockAdvancing(clock) || dt <= 0) return false;
  const rate = gimiClockRate(clock);
  track[phaseKey] += rate * dt;
  const limit = gimiOperand(clock.wrap_limit, Number.NaN);
  if (Number.isFinite(limit) && track[phaseKey] > limit) {
    track[phaseKey] = gimiOperand(clock.wrap_target, track[phaseKey]);
    track.dirty = true;
  }
  return true;
}

function resetGimiTrack(track) {
  for (const [clock, phaseKey] of [
    [track.shapeClock, 'shapePhase'], [track.poseClock, 'poseTime'],
  ]) {
    for (const [index, rule] of (clock?.reset_rules || []).entries()) {
      const resetKey = `${phaseKey}:${index}`;
      const satisfied = dnfSatisfied(rule.conditions || []);
      if (satisfied && track.resetConditions[resetKey] !== true) {
        track[phaseKey] = gimiOperand(rule.value, track[phaseKey]);
        const clear = rule.clear;
        const conditionVariables = new Set((rule.conditions || [])
          .flatMap(group => group.map(clause =>
            String(clause?.var || '').toLowerCase())));
        if (clear?.variable && clear.value?.kind === 'literal'
            && conditionVariables.size === 1
            && conditionVariables.has(String(clear.variable).toLowerCase())) {
          setControlValue(clear.variable, String(clear.value.value));
        }
        track.dirty = true;
      }
      track.resetConditions[resetKey] = satisfied;
    }
  }
}

function advanceGimiTrack(track, now) {
  if (track.lastNow === null) {
    track.lastNow = now;
    return false;
  }
  const dt = Math.max(0, (now - track.lastNow) / 1000);
  track.lastNow = now;
  const shapeAdvanced = advanceGimiClock(
    track, track.shapeClock, 'shapePhase', dt);
  const poseAdvanced = advanceGimiClock(
    track, track.poseClock, 'poseTime', dt);
  return shapeAdvanced || poseAdvanced;
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
    if (state.kind === 'gimi_compute') {
      let visible = false;
      for (const mesh of state.meshes) {
        if (mesh.visible !== false && mesh.userData?.animationSuspended !== true) {
          visible = true;
          break;
        }
      }
      if (!visible) {
        state.lastNow = now;
        continue;
      }
      const advanced = advanceGimiTrack(state, now);
      const poseRate = gimiClockRate(state.poseClock);
      const geometryInterval = poseRate > 0 ? 1000 / poseRate : 1000 / 60;
      const due = state.lastGeometryTime === null
        || now - state.lastGeometryTime >= geometryInterval;
      if (state.dirty || (advanced && due)) {
        changed = applyGimiTrack(state) || changed;
        state.dirty = false;
        state.lastGeometryTime = now;
      }
      playing = advanced
        || gimiClockAdvancing(state.shapeClock)
        || gimiClockAdvancing(state.poseClock)
        || state.dirty;
      continue;
    }
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

function registerGimiMesh(mesh, animationId, geometry) {
  if (!mesh || geometry?.kind !== 'gimi_compute') return false;
  const vertexCount = positiveInteger(geometry.vertex_count);
  const poseInfo = geometry.pose || null;
  const poseBoneCount = positiveInteger(poseInfo?.bone_count);
  const poseFrameCount = positiveInteger(poseInfo?.frame_count);
  const poseBlend = poseInfo?.blend;
  const poseFrames = poseInfo?.frames;
  const hasPose = !!poseInfo;
  if (!vertexCount || (hasPose && (!poseBoneCount || !poseFrameCount
      || !poseBlend || !poseFrames))) return false;
  let state = tracks.get(animationId);
  try {
    if (!state) {
      let decodedPoseFrames = null;
      if (hasPose) {
        decodedPoseFrames = decodeF32(poseFrames);
        if (decodedPoseFrames.length < poseFrameCount * poseBoneCount * 14) {
          return false;
        }
      }
      state = {
        kind: 'gimi_compute',
        meshes: new Set(), meshesByMesh: new Map(),
        poseFrames: decodedPoseFrames, poseBoneCount, poseFrameCount,
        shapeClock: geometry.shape_clock || null,
        poseClock: geometry.pose_clock || null,
        shapePhase: 0, poseTime: 0,
        resolvedRanges: Object.create(null),
        resetConditions: Object.create(null),
        lastNow: null, lastGeometryTime: null, dirty: true,
      };
      syncGimiClockRange(state, state.shapeClock, 'shapePhase');
      syncGimiClockRange(state, state.poseClock, 'poseTime');
      resetGimiTrack(state);
      tracks.set(animationId, state);
    }
    const baseNormals = decodeF32(geometry.base_normals);
    let weights = null;
    let indices = null;
    let poseActive = null;
    if (hasPose) {
      weights = decodeF32(poseBlend.weights);
      indices = decodeI32(poseBlend.indices);
      poseActive = decodeF32(poseBlend.active);
    }
    if (baseNormals.length < vertexCount * 3
        || (hasPose && (weights.length < vertexCount * 4
        || indices.length < vertexCount * 4
        || poseActive.length < vertexCount))) return false;
    const shapePasses = (geometry.shape_passes || []).map(pass => ({
      deltas: decodeF32(pass.deltas),
      phaseOffset: Number(pass.phase_offset) || 0,
      amplitude: Number(pass.amplitude),
      angularScale: Number(pass.angular_scale),
      bias: Number(pass.bias),
    }));
    if (shapePasses.some(pass =>
      pass.deltas.length < vertexCount * 6
      || !Number.isFinite(pass.amplitude)
      || !Number.isFinite(pass.angularScale)
      || !Number.isFinite(pass.bias))
      || (!hasPose && !shapePasses.length)) return false;
    const meshState = {
      vertexCount, baseNormals, weights, indices, shapePasses,
      poseActive,
      animationBounds: geometry.bounds || null,
    };
    state.meshes.add(mesh);
    state.meshesByMesh.set(mesh, meshState);
    mesh.userData.animationState = meshState;
    installAnimationBounds(mesh, geometry.bounds);
    schedule();
    return true;
  } catch (error) {
    return false;
  }
}

export function registerAnimatedMesh(mesh, animationId, geometry, animationClocks) {
  if (geometry?.kind === 'gimi_compute') {
    return registerGimiMesh(mesh, animationId, geometry);
  }
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
  for (const state of tracks.values()) {
    if (state.kind === 'gimi_compute') {
      syncGimiClockRange(state, state.shapeClock, 'shapePhase');
      syncGimiClockRange(state, state.poseClock, 'poseTime');
      resetGimiTrack(state);
      state.dirty = true;
      state.lastGeometryTime = null;
    }
  }
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
    if (state.kind === 'gimi_compute') {
      syncGimiClockRange(state, state.shapeClock, 'shapePhase');
      syncGimiClockRange(state, state.poseClock, 'poseTime');
      resetGimiTrack(state);
      state.dirty = true;
      state.lastGeometryTime = null;
    }
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
