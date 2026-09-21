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

function numeric(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function evaluateExpression(expression, program) {
  if (!expression) return 0;
  switch (expression.kind) {
    case 'literal': return numeric(expression.value);
    case 'dt': return program.dt;
    case 'variable': return numeric(program.variables[expression.variable]);
    case 'binary': {
      const left = evaluateExpression(expression.left, program);
      const right = evaluateExpression(expression.right, program);
      if (expression.op === '+') return left + right;
      if (expression.op === '-') return left - right;
      if (expression.op === '*') return left * right;
      return 0;
    }
    default: return 0;
  }
}

function evaluateCondition(condition, program) {
  if (!condition) return true;
  if (condition.kind !== 'compare') return false;
  const left = evaluateExpression(condition.left, program);
  const right = evaluateExpression(condition.right, program);
  switch (condition.op) {
    case '==': return left === right;
    case '>': return left > right;
    default: return false;
  }
}

function expressionUsesDt(expression) {
  if (!expression || typeof expression !== 'object') return false;
  if (expression.kind === 'dt') return true;
  return Object.values(expression).some(value => Array.isArray(value)
    ? value.some(item => expressionUsesDt(item))
    : expressionUsesDt(value));
}

function initializeGimiProgram(program) {
  const variables = {...(program?.initials || {})};
  const published = {};
  for (const variable of program?.external_variables || []) {
    const external = getControlValue(variable);
    if (external !== undefined) variables[variable] = numeric(external);
    if (variables[variable] === undefined) variables[variable] = 0;
    published[variable] = external === undefined
      ? String(variables[variable]) : String(external);
  }
  return {variables, published, dt: 0};
}

function syncGimiProgramControls(program) {
  for (const variable of program.program.external_variables || []) {
    const external = getControlValue(variable);
    if (external !== undefined
        && String(external) !== program.published[variable]) {
      program.variables[variable] = numeric(external);
      program.published[variable] = String(external);
    }
  }
}

function publishGimiProgramControls(program) {
  for (const variable of program.program.external_variables || []) {
    const value = String(program.variables[variable]);
    if (program.published[variable] === value) continue;
    setControlValue(variable, value);
    program.published[variable] = value;
  }
}

function executeGimiProgram(track, now) {
  const program = track.programState;
  if (!program) return false;
  const previousNow = track.lastNow;
  const dt = previousNow === null
    ? 0 : Math.max(0, (now - previousNow) / 1000);
  track.lastNow = now;
  program.dt = dt;
  syncGimiProgramControls(program);
  for (const output of track.outputs.values()) {
    output.shapePhases = [];
    output.poseTime = 0;
  }
  let changed = false;
  let activeDtCommand = false;
  for (const command of track.program.commands || []) {
    const conditions = command.conditions || [];
    const conditionActive = conditions.every(condition =>
      evaluateCondition(condition, program));
    const commandUsesDt = conditions.some(expressionUsesDt)
      || expressionUsesDt(command.expression)
      || expressionUsesDt(command.phase);
    activeDtCommand = activeDtCommand || (conditionActive && commandUsesDt);
    if (!conditionActive) continue;
    if (command.op === 'set') {
      const value = evaluateExpression(command.expression, program);
      if (!Object.is(program.variables[command.variable], value)) {
        changed = true;
        program.variables[command.variable] = value;
      }
      continue;
    }
    if (command.op !== 'dispatch') continue;
    const output = track.outputs.get(command.track_id);
    if (!output) continue;
    const phase = evaluateExpression(command.phase, program);
    if (command.kind === 'shape') {
      output.shapePhases[command.pass] = phase;
    } else {
      output.poseTime = phase;
    }
  }
  publishGimiProgramControls(program);
  return changed || (previousNow === null && activeDtCommand);
}

function gimiMeshActive(meshState) {
  return dnfSatisfied(meshState?.conditions || []);
}

function applyGimiPose(mesh, meshState, output) {
  const position = mesh.geometry?.attributes?.position;
  const normal = mesh.geometry?.attributes?.normal;
  const basePositions = meshState.overlay
    ? (mesh.userData?.humanoidRestPositions
      || mesh.userData?.basePositions)
    : mesh.userData?.basePositions;
  const baseNormals = meshState.overlay
    ? (mesh.userData?.humanoidRestNormals || meshState.baseNormals)
    : meshState.baseNormals;
  const vertexCount = meshState.vertexCount;
  const positionOnly = meshState.shapePasses.length > 0
    && meshState.shapePasses.every(pass => pass.positionOnly === true);
  if (!position || !normal || !basePositions
      || (!baseNormals && !positionOnly)) return false;

  const positions = position.array;
  const normals = normal.array;
  const shapeWeights = meshState.shapePasses.map((pass, index) => {
    const phase = output.shapePhases[index];
    if (!Number.isFinite(phase)) return 0;
    return 0.5 * (Math.sin(phase * 30) + 1);
  });

  const hasPose = meshState.poseFrames != null;
  const columbinaBasis = output.coordinateVariant === 'columbina_basis';
  const frameValue = hasPose ? Math.max(0, output.poseTime) : 0;
  const frame = hasPose ? Math.min(
    meshState.poseFrameCount - 1, Math.floor(frameValue)) : 0;
  const nextFrame = hasPose
    ? Math.min(meshState.poseFrameCount - 1, frame + 1) : 0;
  const inter = hasPose ? Math.min(1, Math.max(0, frameValue - frame)) : 0;
  const weights = meshState.weights;
  const indices = meshState.indices;
  const pose = meshState.poseFrames;
  const boneCount = meshState.poseBoneCount;

  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const blendOffset = vertex * 4;
    const positionOffset = vertex * 3;
    let px = basePositions[positionOffset];
    let py = basePositions[positionOffset + 1];
    let pz = basePositions[positionOffset + 2];
    let nx = baseNormals ? baseNormals[positionOffset] : normals[positionOffset];
    let ny = baseNormals
      ? baseNormals[positionOffset + 1] : normals[positionOffset + 1];
    let nz = baseNormals
      ? baseNormals[positionOffset + 2] : normals[positionOffset + 2];
    for (let passIndex = 0;
      passIndex < meshState.shapePasses.length; passIndex += 1) {
      const pass = meshState.shapePasses[passIndex];
      const weight = shapeWeights[passIndex];
      const source = vertex * (pass.positionOnly ? 3 : 6);
      px += pass.deltas[source] * weight;
      py += pass.deltas[source + 1] * weight;
      pz += pass.deltas[source + 2] * weight;
      if (pass.positionOnly) continue;
      nx += pass.deltas[source + 3] * weight;
      ny += pass.deltas[source + 4] * weight;
      nz += pass.deltas[source + 5] * weight;
    }

    if (hasPose && columbinaBasis) {
      const oldPy = py;
      const oldNy = ny;
      py = -pz;
      pz = oldPy;
      ny = -nz;
      nz = oldNy;
    }

    if (!hasPose) {
      positions[positionOffset] = px;
      positions[positionOffset + 1] = py;
      positions[positionOffset + 2] = pz;
      if (!positionOnly) {
        normals[positionOffset] = nx;
        normals[positionOffset + 1] = ny;
        normals[positionOffset + 2] = nz;
      }
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
    positions[positionOffset + 1] = columbinaBasis
      ? transformedZ : transformedY;
    positions[positionOffset + 2] = columbinaBasis
      ? -transformedY : transformedZ;
    const transformedNormalX = m00 * nx + m01 * ny + m02 * nz;
    const transformedNormalY = m10 * nx + m11 * ny + m12 * nz;
    const transformedNormalZ = m20 * nx + m21 * ny + m22 * nz;
    const outputNormalX = transformedNormalX;
    const outputNormalY = columbinaBasis
      ? transformedNormalZ : transformedNormalY;
    const outputNormalZ = columbinaBasis
      ? -transformedNormalY : transformedNormalZ;
    const normalLength = Math.hypot(
      outputNormalX, outputNormalY, outputNormalZ);
    normals[positionOffset] = normalLength > 1e-12
      ? outputNormalX / normalLength : 0;
    normals[positionOffset + 1] = normalLength > 1e-12
      ? outputNormalY / normalLength : 0;
    normals[positionOffset + 2] = normalLength > 1e-12
      ? outputNormalZ / normalLength : 0;
  }
  position.needsUpdate = true;
  if (!positionOnly) normal.needsUpdate = true;
  return true;
}

function applyGimiTrack(track) {
  let changed = false;
  for (const mesh of track.meshes) {
    const meshState = track.meshesByMesh.get(mesh);
    if (mesh.visible === false || mesh.userData?.animationSuspended === true) {
      continue;
    }
    if (!gimiMeshActive(meshState)) continue;
    const output = track.outputs.get(meshState.trackId);
    if (!output) continue;
    changed = applyGimiPose(mesh, meshState, output) || changed;
  }
  return changed;
}

function restoreCanonical(mesh) {
  const position = mesh.geometry?.attributes?.position;
  const meshState = mesh.userData?.animationState;
  const base = meshState?.overlay
    ? (mesh.userData?.humanoidRestPositions
      || mesh.userData?.basePositions)
    : mesh.userData?.basePositions;
  let changed = false;
  if (position && base && position.array.length === base.length) {
    changed = position.array.some((value, index) => value !== base[index]);
    position.array.set(base);
    position.needsUpdate = true;
  }
  const normal = mesh.geometry?.attributes?.normal;
  const baseNormals = meshState?.overlay
    ? (mesh.userData?.humanoidRestNormals || mesh.userData?.baseNormals)
    : mesh.userData?.baseNormals;
  if (normal && baseNormals && normal.array.length === baseNormals.length) {
    changed = normal.array.some((value, index) => value !== baseNormals[index])
      || changed;
    normal.array.set(baseNormals);
    normal.needsUpdate = true;
  } else if (normal) {
    mesh.geometry.computeVertexNormals?.();
  }
  return changed;
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
      let active = false;
      for (const mesh of state.meshes) {
        const meshState = state.meshesByMesh.get(mesh);
        if (mesh.visible !== false
            && mesh.userData?.animationSuspended !== true) {
          visible = true;
          if (gimiMeshActive(meshState)) active = true;
          else if (meshState?.overlay) {
            changed = restoreCanonical(mesh) || changed;
          }
        }
      }
      if (!visible || !active) {
        state.lastNow = now;
        continue;
      }
      const advanced = executeGimiProgram(state, now);
      const geometryInterval = 1000 / 30;
      const due = state.lastGeometryTime === null
        || now - state.lastGeometryTime >= geometryInterval;
      if (state.dirty || (advanced && due)) {
        changed = applyGimiTrack(state) || changed;
        state.dirty = false;
        state.lastGeometryTime = now;
      }
      playing = playing || advanced || state.dirty;
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
  const program = geometry.program;
  const programId = geometry.program_id;
  const trackId = geometry.track_id || animationId;
  const coordinateVariant = geometry.coordinate_variant || 'standard';
  if (!program || !programId || !trackId) return false;
  const vertexCount = Number(geometry.vertex_count);
  const poseInfo = geometry.pose || null;
  const poseBoneCount = Number(poseInfo?.bone_count);
  const poseFrameCount = Number(poseInfo?.frame_count);
  const poseBlend = poseInfo?.blend;
  const poseFrames = poseInfo?.frames;
  const hasPose = !!poseInfo;
  let state = tracks.get(programId);
  try {
    if (!state) {
      state = {
        kind: 'gimi_compute',
        meshes: new Set(), meshesByMesh: new Map(),
        program, programState: {
          program, ...initializeGimiProgram(program),
        },
        outputs: new Map(),
        lastNow: null, lastGeometryTime: null, dirty: true,
      };
      tracks.set(programId, state);
    }
    const baseNormals = geometry.base_normals
      ? decodeF32(geometry.base_normals) : null;
    let weights = null;
    let indices = null;
    if (hasPose) {
      weights = decodeF32(poseBlend.weights);
      indices = decodeI32(poseBlend.indices);
    }
    let decodedPoseFrames = null;
    if (hasPose) {
      decodedPoseFrames = decodeF32(poseFrames);
    }
    const shapePasses = (geometry.shape_passes || []).map(pass => ({
      deltas: decodeF32(pass.deltas),
      positionOnly: pass.position_only === true,
    }));
    const meshState = {
      vertexCount, baseNormals, weights, indices, shapePasses,
      poseFrames: decodedPoseFrames,
      poseBoneCount, poseFrameCount, trackId,
      overlay: geometry.overlay === true,
      conditions: geometry.conditions || [],
      animationBounds: geometry.bounds || null,
    };
    if (!state.outputs.has(trackId)) {
      state.outputs.set(trackId, {
        shapePhases: [], poseTime: 0,
        coordinateVariant,
      });
    }
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
      state.dirty = true;
      state.lastGeometryTime = null;
      state.lastNow = null;
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
      state.dirty = true;
      state.lastGeometryTime = null;
      state.lastNow = null;
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
