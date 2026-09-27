// Shared notification for authoritative viewer mesh-state changes.

export function notifyMeshStateChanged(meshes = []) {
  window.dispatchEvent(new CustomEvent('mod-viewer-mesh-state-changed', {
    detail: { meshes: Array.isArray(meshes) ? meshes : [meshes] },
  }));
}
