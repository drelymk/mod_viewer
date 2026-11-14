// Shared scalar storage buffers for GPU deformation and lazy CPU tool access.
import * as THREE from 'three/webgpu';
import { storage } from 'three/tsl';
import { getLooseParts } from './loose-parts.js';

let renderer = null;
let gpuAccess = false;

export function setAnimationComputeRenderer(value) {
  renderer = value;
}

export function withAnimationGpuAccess(callback) {
  const previous = gpuAccess;
  gpuAccess = true;
  try {
    return callback();
  } finally {
    gpuAccess = previous;
  }
}

export function gpuDeformationSupported(sizes) {
  return renderer && sizes.every((bytes) => bytes <= renderer.backend.device.limits.maxStorageBufferBindingSize);
}

export function createGpuDeformation(mesh, { normal = true, canMirror = () => true, materialize }) {
  if (!renderer) return null;
  const computeRenderer = renderer;
  const inputs = [];
  const outputs = new Map();
  let pending = null;
  let mirroring = false;
  let compute = null;
  let disposed = false;
  function mirror() {
    if (gpuAccess || mirroring || !pending) return;
    const snapshot = pending;
    pending = null;
    if (!canMirror()) return;
    mirroring = true;
    try {
      materialize(snapshot);
    } finally {
      mirroring = false;
    }
  }
  const readBuffer = (array, type = 'float') => {
    const attribute = new THREE.StorageBufferAttribute(array, 1);
    inputs.push(attribute);
    return storage(attribute, type, array.length).toReadOnly();
  };
  const writeBuffer = (name) => {
    const original = mesh.geometry.attributes[name];
    // Scalar storage with a three-float vertex stride avoids WGSL vec3 padding.
    const data = new THREE.InterleavedBuffer(original.array, 3);
    let array = data.array;
    Object.defineProperty(data, 'array', {
      configurable: true,
      get() {
        mirror();
        return array;
      },
      set(value) {
        array = value;
      },
    });
    const attribute = new THREE.InterleavedBufferAttribute(data, 3, 0);
    // A draw may upload this buffer before its first compute dispatch.
    attribute.isStorageBufferAttribute = true;
    const node = storage(data, 'float', array.length);
    mesh.geometry.setAttribute(name, attribute);
    for (const part of getLooseParts(mesh)) {
      if (part.geometry.attributes[name] === original) part.geometry.setAttribute(name, attribute);
    }
    outputs.set(name, { original, attribute, version: data.version });
    return node;
  };
  const position = writeBuffer('position');
  const normalBuffer = normal ? writeBuffer('normal') : null;
  function dispose() {
    if (disposed) return;
    disposed = true;
    pending = null;
    compute?.dispose();
    // Pinned Three has no BufferAttribute.dispose(). Its attribute manager
    // releases both backend buffers and the render/compute cache entries.
    for (const attribute of inputs) computeRenderer._attributes.delete(attribute);
    for (const [name, { original, attribute }] of outputs) {
      computeRenderer._attributes.delete(attribute);
      computeRenderer._attributes.delete(attribute.data);
      Object.defineProperty(attribute.data, 'array', { value: original.array, writable: true, configurable: true });
      original.needsUpdate = true;
      mesh.geometry.setAttribute(name, original);
      for (const part of getLooseParts(mesh)) {
        if (part.geometry.attributes[name] === attribute) part.geometry.setAttribute(name, original);
      }
    }
    mesh.geometry.removeEventListener('dispose', dispose);
  }
  mesh.geometry.addEventListener('dispose', dispose);
  return {
    position,
    normal: normalBuffer,
    readBuffer,
    materialize: mirror,
    dispose,
    discard() {
      pending = null;
    },
    dispatch(node, snapshot) {
      compute = node;
      withAnimationGpuAccess(() => computeRenderer.compute(compute));
      pending = snapshot;
      // Rig/shape edits can leave a CPU upload pending in the vertex binding.
      // Mirror once on ownership changes so that upload agrees with this pose.
      for (const output of outputs.values()) {
        const current = output.attribute.data.version;
        if (current !== output.version) mirror();
        output.version = current;
      }
    },
  };
}
