import { Matrix4, Quaternion } from 'three';
import { Fn, If, float, instanceIndex, vec3, vec4 } from 'three/tsl';
import { createGpuDeformation, gpuDeformationSupported } from '../mesh/deformation-gpu.js';
import { applyWeightedNormalDeformationInto, applyWeightedTransformDeformationInto } from './weight-deformation.js';

// Asset clips retain their authored linear blend skinning and all eight influences.
export function createAnimationSkinningGpu(mesh, state) {
  // Reconstructed animation already owns a GPU buffer session for this mesh.
  if (mesh.userData.animationState) return null;
  const bones = state.boneIds;
  const vertexCount = state.baselinePositions.length / 3;
  const stride = 22; // matrix, quaternion, and independent presence flags
  if (
    !gpuDeformationSupported([
      state.baselinePositions.byteLength,
      state.baselineNormals?.byteLength || 0,
      state.indices.byteLength,
      state.weights.byteLength,
      vertexCount * 4,
      (bones.length + 1) * stride * Float32Array.BYTES_PER_ELEMENT,
    ])
  )
    return null;
  const slots = new Map(bones.map((bone, index) => [Number(bone), index + 1]));
  const gpu = createGpuDeformation(mesh, {
    normal: !!state.baselineNormals,
    materialize({ values, vertices }) {
      const transforms = new Map();
      const rotations = new Map();
      for (const [bone, slot] of slots) {
        const offset = slot * stride;
        if (values[offset + 20]) transforms.set(bone, new Matrix4().fromArray(values, offset));
        if (values[offset + 21]) rotations.set(bone, new Quaternion().fromArray(values, offset + 16));
      }
      const position = mesh.geometry.attributes.position.array;
      position.set(state.baselinePositions);
      applyWeightedTransformDeformationInto(
        position,
        state.baselinePositions,
        state.indices,
        state.weights,
        state.influenceCount,
        transforms,
        vertices,
      );
      if (state.baselineNormals) {
        const normal = mesh.geometry.attributes.normal.array;
        normal.set(state.baselineNormals);
        applyWeightedNormalDeformationInto(
          normal,
          state.baselineNormals,
          state.indices,
          state.weights,
          state.influenceCount,
          rotations,
          vertices,
        );
      }
    },
  });
  const { readBuffer } = gpu;
  const positions = readBuffer(state.baselinePositions);
  const normals = state.baselineNormals ? readBuffer(state.baselineNormals) : null;
  const weights = readBuffer(state.weights);
  const indices = readBuffer(
    Uint32Array.from(state.indices, (bone) => slots.get(bone) || 0),
    'uint',
  );
  const palette = readBuffer(new Float32Array((bones.length + 1) * stride));
  const values = new Float64Array(palette.value.array.length);
  const active = readBuffer(new Uint32Array(vertexCount), 'uint');
  const read3 = (buffer, offset) =>
    vec3(buffer.element(offset), buffer.element(offset.add(1)), buffer.element(offset.add(2)));
  const compute = Fn(() => {
    const offset = instanceIndex.mul(3);
    const p = read3(positions, offset).toVar();
    const n = normals ? read3(normals, offset).toVar() : null;
    If(active.element(instanceIndex).greaterThan(0), () => {
      const sumPosition = vec3(0).toVar();
      const sumNormal = vec3(0).toVar();
      const positionWeight = float(0).toVar();
      const normalWeight = float(0).toVar();
      for (let influence = 0; influence < state.influenceCount; influence += 1) {
        const blend = instanceIndex.mul(state.influenceCount).add(influence);
        const weight = weights.element(blend);
        If(weight.greaterThan(0), () => {
          const bone = indices.element(blend).mul(stride);
          If(palette.element(bone.add(20)).greaterThan(0), () => {
            const transformed = read3(palette, bone)
              .mul(p.x)
              .add(read3(palette, bone.add(4)).mul(p.y))
              .add(read3(palette, bone.add(8)).mul(p.z))
              .add(read3(palette, bone.add(12)));
            sumPosition.addAssign(transformed.mul(weight));
            positionWeight.addAssign(weight);
          });
          if (n)
            If(palette.element(bone.add(21)).greaterThan(0), () => {
              const q = vec4(read3(palette, bone.add(16)), palette.element(bone.add(19)));
              const rotated = n.add(q.xyz.cross(q.xyz.cross(n).add(n.mul(q.w))).mul(2));
              sumNormal.addAssign(rotated.mul(weight));
              normalWeight.addAssign(weight);
            });
        });
      }
      p.assign(p.mul(positionWeight.oneMinus().max(0)).add(sumPosition));
      if (n) {
        const transformed = n.mul(normalWeight.oneMinus().max(0)).add(sumNormal).toVar();
        const length = transformed.length().toVar();
        n.assign(length.greaterThan(1e-10).select(transformed.div(length.max(1e-10)), n.div(n.length().max(1e-20))));
      }
    });
    for (const [axis, component] of ['x', 'y', 'z'].entries()) {
      gpu.position.element(offset.add(axis)).assign(p[component]);
      if (n) gpu.normal.element(offset.add(axis)).assign(n[component]);
    }
  })().compute(vertexCount);
  let previousVertices = null;
  return {
    materialize: gpu.materialize,
    dispose: gpu.dispose,
    update(transforms, rotations, vertices) {
      if (vertices !== previousVertices) {
        active.value.array.fill(0);
        for (const vertex of vertices) active.value.array[vertex] = 1;
        active.value.needsUpdate = true;
        previousVertices = vertices;
      }
      // Updates and lazy reads are synchronous; reuse the last applied palette.
      values.fill(0);
      for (const [bone, slot] of slots) {
        const offset = slot * stride;
        const matrix = transforms.get(bone);
        const rotation = rotations?.get(bone);
        if (matrix) {
          matrix.toArray(values, offset);
          values[offset + 20] = 1;
        }
        if (rotation) {
          rotation.toArray(values, offset + 16);
          values[offset + 21] = 1;
        }
      }
      palette.value.array.set(values);
      palette.value.needsUpdate = true;
      gpu.dispatch(compute, { values, vertices });
    },
  };
}
