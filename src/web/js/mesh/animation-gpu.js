// TSL deformation writes the same buffers consumed by every render pass.
import { createGpuDeformation, gpuDeformationSupported } from './deformation-gpu.js';
import { Fn, If, abs, float, instanceIndex, mix, sign, uniform, uint, vec3, vec4 } from 'three/tsl';

export function createAnimationGpu(mesh, state, materialize) {
  const shapeBytes = state.shapePasses.reduce((bytes, pass) => bytes + pass.deltas.byteLength, 0);
  if (
    !gpuDeformationSupported([
      state.vertexCount * 12,
      shapeBytes,
      state.poseFrames?.byteLength || 0,
      state.weights?.byteLength || 0,
      state.indices?.byteLength || 0,
    ])
  )
    return null;
  const gpu = createGpuDeformation(mesh, {
    normal: !state.positionOnly,
    canMirror: () => mesh.userData.animationSuspended !== true,
    materialize,
  });
  const { readBuffer, position, normal } = gpu;
  const basePosition = readBuffer(new Float32Array(state.vertexCount * 3));
  const baseNormal = state.positionOnly ? null : readBuffer(new Float32Array(state.vertexCount * 3));
  const shapeWeights = state.shapePasses.map(() => uniform(0));
  // One packed source avoids a storage binding per shape pass.
  const shapeStride = state.vertexCount * (state.positionOnly ? 3 : 6);
  const deltas = new Float32Array(shapeStride * state.shapePasses.length);
  state.shapePasses.forEach((pass, index) => deltas.set(pass.deltas, index * shapeStride));
  const shapes = deltas.length ? readBuffer(deltas) : null;
  const pose = state.poseFrames ? readBuffer(state.poseFrames) : null;
  const weights = pose ? readBuffer(state.weights) : null;
  const indices = pose ? readBuffer(state.indices, 'int') : null;
  const frame = uniform(0, 'uint');
  const nextFrame = uniform(0, 'uint');
  const interpolation = uniform(0);
  const swap = uniform(false);
  const read3 = (buffer, offset) =>
    vec3(buffer.element(offset), buffer.element(offset.add(1)), buffer.element(offset.add(2)));
  const read4 = (buffer, offset) => vec4(read3(buffer, offset), buffer.element(offset.add(3)));
  const rotate = (q, v) => v.add(q.xyz.cross(q.xyz.cross(v).add(v.mul(q.w))).mul(2));
  const compute = Fn(() => {
    const offset = instanceIndex.mul(3);
    const p = read3(basePosition, offset).toVar();
    const n = (baseNormal ? read3(baseNormal, offset) : vec3(0)).toVar();
    state.shapePasses.forEach((_pass, index) => {
      const source = instanceIndex.mul(state.positionOnly ? 3 : 6).add(index * shapeStride);
      p.addAssign(read3(shapes, source).mul(shapeWeights[index]));
      if (!state.positionOnly) n.addAssign(read3(shapes, source.add(3)).mul(shapeWeights[index]));
    });
    if (pose) {
      If(swap, () => {
        p.assign(vec3(p.x, p.z.negate(), p.y));
        n.assign(vec3(n.x, n.z.negate(), n.y));
      });
      const referenceBone = uint(indices.element(instanceIndex.mul(4)));
      const reference = read4(pose, frame.mul(state.poseBoneCount).add(referenceBone).mul(14).add(6)).toVar();
      const scale = vec3(0).toVar();
      const bias = vec3(0).toVar();
      const real = vec4(0).toVar();
      const dual = vec4(0).toVar();
      for (let influence = 0; influence < 4; influence += 1) {
        const blend = instanceIndex.mul(4).add(influence);
        const weight = weights.element(blend);
        If(abs(weight).greaterThan(1e-8), () => {
          const bone = uint(indices.element(blend));
          const previous = frame.mul(state.poseBoneCount).add(bone).mul(14);
          const next = nextFrame.mul(state.poseBoneCount).add(bone).mul(14);
          scale.addAssign(mix(read3(pose, previous), read3(pose, next), interpolation).mul(weight));
          bias.addAssign(mix(read3(pose, previous.add(3)), read3(pose, next.add(3)), interpolation).mul(weight));
          const previousReal = read4(pose, previous.add(6)).toVar();
          const nextReal = read4(pose, next.add(6)).toVar();
          const previousWeight = weight
            .mul(interpolation.oneMinus())
            .mul(influence === 0 ? float(1) : sign(reference.dot(previousReal)));
          const nextWeight = weight.mul(interpolation).mul(sign(reference.dot(nextReal)));
          real.addAssign(previousReal.mul(previousWeight).add(nextReal.mul(nextWeight)));
          dual.addAssign(
            read4(pose, previous.add(10))
              .mul(previousWeight)
              .add(read4(pose, next.add(10)).mul(nextWeight)),
          );
        });
      }
      const length = real.length().max(1e-6).toVar();
      real.divAssign(length);
      dual.divAssign(length);
      const translation = dual.xyz.mul(real.w).sub(real.xyz.mul(dual.w)).add(real.xyz.cross(dual.xyz)).mul(2);
      p.assign(rotate(real, p.mul(scale).add(bias)).add(translation));
      n.assign(rotate(real, n));
      If(swap, () => {
        p.assign(vec3(p.x, p.z, p.y.negate()));
        n.assign(vec3(n.x, n.z, n.y.negate()));
      });
      const normalLength = n.length().toVar();
      n.assign(normalLength.greaterThan(1e-12).select(n.div(normalLength.max(1e-12)), vec3(0)));
    }
    for (const [axis, component] of ['x', 'y', 'z'].entries()) {
      position.element(offset.add(axis)).assign(p[component]);
      if (normal) normal.element(offset.add(axis)).assign(n[component]);
    }
  })().compute(state.vertexCount);
  let previousPositions = null;
  let previousNormals = null;
  return {
    dispose: gpu.dispose,
    discard: gpu.discard,
    update(output, positions, normals, evaluatedWeights) {
      if (positions !== previousPositions) {
        basePosition.value.array.set(positions);
        basePosition.value.needsUpdate = true;
        previousPositions = positions;
      }
      if (baseNormal && normals && normals !== previousNormals) {
        baseNormal.value.array.set(normals);
        baseNormal.value.needsUpdate = true;
        previousNormals = normals;
      }
      shapeWeights.forEach((weight, index) => {
        weight.value = evaluatedWeights[index];
      });
      const time = Math.max(0, output.poseTime);
      frame.value = Math.min(state.poseFrameCount - 1, Math.floor(time));
      nextFrame.value = Math.min(state.poseFrameCount - 1, frame.value + 1);
      interpolation.value = Math.min(1, Math.max(0, time - frame.value));
      swap.value = output.coordinateTransform === 'swap_yz_negate';
      gpu.dispatch(compute, { ...output });
    },
  };
}
