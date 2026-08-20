// Viewer-only material display modes and their toolbar state.

import { refreshMeshTexture, setTextureMode } from './mesh-factory.js';

let wireframe = false;
let smoothShading = true;
const textureModes = ['all', 'diffuse', 'none'];
let textureModeIndex = 0;

export function initializeMeshRenderModes(mesh) {
  mesh.material.wireframe = wireframe;
  mesh.material.flatShading = !smoothShading;
}

export function toggleWireframeMode(meshes) {
  wireframe = !wireframe;
  document.getElementById('wire-btn').classList.toggle('active', wireframe);
  meshes.forEach(mesh => {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach(material => { material.wireframe = wireframe; });
  });
}

export function toggleSmoothShadingMode(meshes) {
  smoothShading = !smoothShading;
  document.getElementById('shading-btn').classList.toggle('off', !smoothShading);
  meshes.forEach(mesh => {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach(material => {
      material.flatShading = !smoothShading;
      material.needsUpdate = true;
    });
  });
}

export function toggleTextureDisplayMode(meshes) {
  textureModeIndex = (textureModeIndex + 1) % textureModes.length;
  const mode = textureModes[textureModeIndex];
  const button = document.getElementById('texture-btn');
  button.classList.toggle('diffuse-only', mode === 'diffuse');
  button.classList.toggle('off', mode === 'none');
  const labels = {
    all: 'Textures: all maps',
    diffuse: 'Textures: diffuse only',
    none: 'Textures: none',
  };
  button.title = labels[mode];
  button.setAttribute('aria-label', labels[mode]);
  setTextureMode(mode);
  meshes.forEach(refreshMeshTexture);
}
