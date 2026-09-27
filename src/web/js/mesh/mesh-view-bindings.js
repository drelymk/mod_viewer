// Mesh-to-DOM associations belong to the view, not THREE.Mesh.userData.

const bindings = new WeakMap();

export function bindMeshView(mesh, binding) {
  bindings.set(mesh, binding);
  return binding;
}

export function getMeshView(mesh) {
  return bindings.get(mesh);
}
