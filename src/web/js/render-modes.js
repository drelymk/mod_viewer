// Viewer-only material display modes and their toolbar state.

import { refreshMeshTexture, setTextureMode } from './mesh-factory.js';
import { setOutlineSuppressedByWireframe } from './outline-renderer.js';

let wireframe = false;
let smoothShading = true;
let glossy = false;
const DEFAULT_ROUGHNESS = 1.0;
const GLOSSY_ROUGHNESS = 0.2;
const textureModes = ['all', 'diffuse', 'none'];
let textureModeIndex = 0;

function setMeshRoughness(mesh, roughness) {
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  materials.forEach(material => {
    material.roughness = roughness;
  });
}

export function initializeMeshRenderModes(mesh) {
  mesh.material.wireframe = wireframe;
  mesh.material.flatShading = !smoothShading;
  setMeshRoughness(mesh, glossy ? GLOSSY_ROUGHNESS : DEFAULT_ROUGHNESS);
}

export function toggleWireframeMode(meshes) {
  wireframe = !wireframe;
  setOutlineSuppressedByWireframe(wireframe);
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

export function toggleGlossyMode(meshes) {
  glossy = !glossy;
  const button = document.getElementById('glossy-btn');
  // Glossy materials are off by default, like the other on/off display
  // modes.  This control uses the shared `off` styling; toggling `active`
  // leaves it looking enabled in both states because there is no glossy
  // `.active` rule.
  button.classList.toggle('off', !glossy);
  const label = glossy ? 'Glossy materials: on' : 'Glossy materials: off';
  button.title = label;
  button.setAttribute('aria-label', label);
  button.setAttribute('aria-pressed', String(glossy));
  meshes.forEach(mesh => {
    setMeshRoughness(mesh, glossy ? GLOSSY_ROUGHNESS : DEFAULT_ROUGHNESS);
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
