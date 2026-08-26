// Viewer-only material display modes and their toolbar state.

import { refreshMeshTexture, setTextureMode } from '../mesh/mesh-factory.js';
import { setGameMaterialRimEnabled } from '../mesh/material-profile.js';
import { setOutlineSuppressedByWireframe } from './outline-renderer.js';
import { requestRender } from './render-scheduler.js';

let wireframe = false;
let smoothShading = true;
let glossy = false;
const DEFAULT_ROUGHNESS = 1.0;
const GLOSSY_ROUGHNESS = 0.2;
const textureModes = ['all', 'diffuse-normal', 'diffuse', 'none'];
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
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  materials.forEach(material => setGameMaterialRimEnabled(material, !wireframe));
}

export function toggleWireframeMode(meshes) {
  wireframe = !wireframe;
  setOutlineSuppressedByWireframe(wireframe);
  const button = document.getElementById('wire-btn');
  button.classList.toggle('active', wireframe);
  button.setAttribute('aria-pressed', String(wireframe));
  button.setAttribute('aria-label', `Wireframe rendering: ${wireframe ? 'on' : 'off'}`);
  meshes.forEach(mesh => {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach(material => {
      material.wireframe = wireframe;
      setGameMaterialRimEnabled(material, !wireframe);
    });
  });
  requestRender();
}

export function toggleSmoothShadingMode(meshes) {
  smoothShading = !smoothShading;
  const button = document.getElementById('shading-btn');
  button.classList.toggle('off', !smoothShading);
  button.setAttribute('aria-pressed', String(smoothShading));
  button.setAttribute('aria-label', `Smooth shading: ${smoothShading ? 'on' : 'off'}`);
  meshes.forEach(mesh => {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach(material => {
      material.flatShading = !smoothShading;
      material.needsUpdate = true;
    });
  });
  requestRender();
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
  requestRender();
}

export function toggleTextureDisplayMode(meshes) {
  const mode = textureModes[(textureModeIndex + 1) % textureModes.length];
  setTextureDisplayMode(mode, meshes);
}

export function setTextureDisplayMode(mode, meshes) {
  const nextIndex = textureModes.indexOf(mode);
  if (nextIndex < 0) return false;
  textureModeIndex = nextIndex;
  const button = document.getElementById('texture-btn');
  button.classList.toggle('diffuse-normal', mode === 'diffuse-normal');
  button.classList.toggle('diffuse-only', mode === 'diffuse');
  button.classList.toggle('off', mode === 'none');
  const labels = {
    all: 'Textures: all maps',
    'diffuse-normal': 'Textures: diffuse and normal map',
    diffuse: 'Textures: diffuse only',
    none: 'Textures: none',
  };
  button.title = labels[mode];
  button.setAttribute('aria-label', labels[mode]);
  setTextureMode(mode);
  meshes.forEach(refreshMeshTexture);
  requestRender();
  return true;
}
