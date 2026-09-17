// Viewer-only material display modes and their toolbar state.

import { refreshMeshTexture, setTextureMode } from '../mesh/mesh-factory.js';
import {
  setGameMaterialRimEnabled, setGameMaterialToonEnabled,
} from '../mesh/material-profile.js';
import { setOutlineSuppressedByWireframe } from './outline-renderer.js';
import { requestRender } from './render-scheduler.js';
import {
  setAmbientOcclusionSuppressedByWireframe,
  setBloomSuppressedByWireframe,
} from './scene.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

let wireframe = false;
let smoothShading = true;
let glossy = false;
let toonShading = false;
const DEFAULT_ROUGHNESS = 1.0;
const GLOSSY_ROUGHNESS = 0.2;
const textureModes = ['all', 'diffuse-normal', 'diffuse', 'none'];
let textureModeIndex = 0;

function stateWord(value) {
  return value ? t('common.on') : t('common.off');
}

function updateRenderModeLabels() {
  const wireButton = document.getElementById('wire-btn');
  if (wireButton) {
    const label = t('render.wireframe', {state: stateWord(wireframe)});
    wireButton.title = label;
    wireButton.setAttribute('aria-label', label);
  }
  const outlineButton = document.getElementById('outline-btn');
  if (outlineButton) {
    const label = t('render.outlines', {state: stateWord(
      outlineButton.getAttribute('aria-pressed') === 'true')});
    outlineButton.title = label;
    outlineButton.setAttribute('aria-label', label);
  }
  const glossyButton = document.getElementById('glossy-btn');
  if (glossyButton) {
    const label = t('render.glossy', {state: stateWord(glossy)});
    glossyButton.title = label;
    glossyButton.setAttribute('aria-label', label);
  }
  const toonButton = document.getElementById('toon-btn');
  if (toonButton) {
    const label = t('render.toon', {state: stateWord(toonShading)});
    toonButton.title = label;
    toonButton.setAttribute('aria-label', label);
  }
  const textureButton = document.getElementById('texture-btn');
  if (textureButton) {
    const mode = textureModes[textureModeIndex];
    const label = t(`render.textureMode.${mode}`);
    textureButton.title = label;
    textureButton.setAttribute('aria-label', label);
  }
}

window.addEventListener(LANGUAGE_CHANGED, updateRenderModeLabels);

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
  materials.forEach(material => {
    setGameMaterialRimEnabled(material, !wireframe);
    setGameMaterialToonEnabled(material, toonShading);
  });
}

export function toggleWireframeMode(meshes) {
  wireframe = !wireframe;
  setOutlineSuppressedByWireframe(wireframe);
  setAmbientOcclusionSuppressedByWireframe(wireframe);
  setBloomSuppressedByWireframe(wireframe);
  const button = document.getElementById('wire-btn');
  button.classList.toggle('active', wireframe);
  button.setAttribute('aria-pressed', String(wireframe));
  const label = t('render.wireframe', {state: stateWord(wireframe)});
  button.title = label;
  button.setAttribute('aria-label', label);
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
  const label = t('render.shading', {state: stateWord(smoothShading)});
  button.title = label;
  button.setAttribute('aria-label', label);
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
  const label = t('render.glossy', {state: stateWord(glossy)});
  button.title = label;
  button.setAttribute('aria-label', label);
  button.setAttribute('aria-pressed', String(glossy));
  meshes.forEach(mesh => {
    setMeshRoughness(mesh, glossy ? GLOSSY_ROUGHNESS : DEFAULT_ROUGHNESS);
  });
  requestRender();
}

export function toggleToonShadingMode(meshes) {
  toonShading = !toonShading;
  const button = document.getElementById('toon-btn');
  button.classList.toggle('active', toonShading);
  button.classList.toggle('off', !toonShading);
  const label = t('render.toon', {state: stateWord(toonShading)});
  button.title = label;
  button.setAttribute('aria-label', label);
  button.setAttribute('aria-pressed', String(toonShading));
  meshes.forEach(mesh => {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach(material => setGameMaterialToonEnabled(material, toonShading));
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
  const label = t(`render.textureMode.${mode}`);
  button.title = label;
  button.setAttribute('aria-label', label);
  setTextureMode(mode);
  meshes.forEach(refreshMeshTexture);
  requestRender();
  return true;
}
