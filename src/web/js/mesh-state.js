// Active meshes and the mod control state resolved onto their rendering data.

import { scene, resetModelOrientation } from './scene.js';
import { dnfSatisfied, getControlValue } from './control-state.js';
import { disposeGameMaterial } from './material-profile.js';
import { setMeshTextureState } from './mesh-factory.js';
import { attachOutline, detachOutline } from './outline-renderer.js';
import { initializeMeshRenderModes } from './render-modes.js';
import { requestRender } from './render-scheduler.js';
import { notifyMeshStateChanged } from './mesh-state-events.js';

export const activeMeshes = [];

export function resetMeshes() {
  activeMeshes.forEach(mesh => {
    detachOutline(mesh);
    scene.remove(mesh);
    mesh.geometry.dispose();
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach(material => {
      disposeGameMaterial(material);
      material.dispose();
    });
  });
  activeMeshes.length = 0;
  resetModelOrientation();
  requestRender();
}

export function addMesh(mesh, conditions, sources, textureVariants, materialVariants = {}) {
  mesh.userData.manualVisible = true;
  mesh.userData.loadedVisible = true;
  mesh.userData.manuallyToggled = false;
  mesh.userData.conditions = conditions || [];
  mesh.userData.sources = sources || [];
  mesh.userData.textureVariants = textureVariants || [];
  mesh.userData.normalMapVariants = materialVariants.normal_map || [];
  mesh.userData.normalDataVariants = materialVariants.normal_data || [];
  mesh.userData.lightMapVariants = materialVariants.light_map || [];
  mesh.userData.materialMapVariants = materialVariants.material_map || [];
  // The texture selected by the INI under the current control state. Ordered
  // component texture runs may still make a following mesh inherit it.
  mesh.userData.resolvedTexKey = mesh.userData.defaultTexKey;
  // undefined = automatic; string = a sticky manual selection.
  mesh.userData.manualTexOverride = undefined;
  initializeMeshRenderModes(mesh);
  attachOutline(mesh);
  scene.add(mesh);
  activeMeshes.push(mesh);
  applyTextureVariant(mesh);
}

export function resetMeshVisibility() {
  activeMeshes.forEach(mesh => {
    mesh.userData.manualVisible = mesh.userData.loadedVisible !== false;
    mesh.userData.manuallyToggled = false;
    applyMeshVisibility(mesh, { notify: false });
  });
  notifyMeshStateChanged(activeMeshes);
  requestRender();
}

/** Pin or clear one mesh's highlighted diffuse. Ordered component propagation
 * remains the texture-state module's responsibility. */
export function setManualTexOverride(mesh, value, { notify = true } = {}) {
  mesh.userData.manualTexOverride = value;
  applyTextureVariant(mesh);
  if (notify) notifyMeshStateChanged([mesh]);
  requestRender();
}

export function conditionsSatisfied(mesh) {
  return dnfSatisfied(mesh.userData.conditions);
}

export function applyTextureVariant(mesh) {
  const resolve = (variants, fallback) => {
    variants = variants || [];
    const variant = variants.findLast
      ? variants.findLast(item => dnfSatisfied(item.conditions))
      : [...variants].reverse().find(item => dnfSatisfied(item.conditions));
    return variant ? variant.tex_key : fallback;
  };
  mesh.userData.resolvedTexKey = resolve(
    mesh.userData.textureVariants, mesh.userData.defaultTexKey);
  mesh.userData.resolvedNormalMapKey = resolve(
    mesh.userData.normalMapVariants, mesh.userData.defaultNormalMapKey);
  mesh.userData.resolvedNormalDataKey = resolve(
    mesh.userData.normalDataVariants, mesh.userData.defaultNormalDataKey);
  mesh.userData.resolvedLightMapKey = resolve(
    mesh.userData.lightMapVariants, mesh.userData.defaultLightMapKey);
  mesh.userData.resolvedMaterialMapKey = resolve(
    mesh.userData.materialMapVariants, mesh.userData.defaultMaterialMapKey);
  setMeshTextureState(mesh, {
    diffuse: mesh.userData.manualTexOverride !== undefined
      ? mesh.userData.manualTexOverride
      : mesh.userData.resolvedTexKey,
    normal_map: mesh.userData.resolvedNormalMapKey,
    normal_data: mesh.userData.resolvedNormalDataKey,
    light_map: mesh.userData.resolvedLightMapKey,
    material_map: mesh.userData.resolvedMaterialMapKey,
  });
}

// The MESHES control is the direct visibility source. Gating conditions only
// re-baseline manualVisible during refreshMeshes(), so a manual click can
// always reveal a currently gated mesh.
export function applyMeshVisibility(mesh, { notify = true } = {}) {
  mesh.visible = mesh.userData.manualVisible !== false;
  if (notify) notifyMeshStateChanged([mesh]);
  requestRender();
}

function applyShapeTargets(mesh) {
  const targets = mesh.userData.shapeTargets || [];
  if (!targets.length) return;
  const controlValues = targets.map(target => getControlValue(target.var) ?? 0);
  const previous = mesh.userData.shapeControlValues;
  if (previous?.length === controlValues.length
      && controlValues.every((value, index) => value === previous[index])) return;
  mesh.userData.shapeControlValues = controlValues;

  const attr = mesh.geometry.attributes.position;
  const base = mesh.userData.basePositions;
  attr.array.set(base);
  const midpointTargets = targets.filter(target => target.mode === 'midpoint_pair');
  for (const target of targets) {
    const weight = Number(getControlValue(target.var) ?? 0);
    if (!Number.isFinite(weight)) continue;
    if (target.mode === 'midpoint_pair') {
      const endpoint = weight <= 0.5 ? target.lowPositions : target.positions;
      // Preserve the shader's endpoint extrapolation. Independently-shaped
      // midpoint targets are averaged, so each uses a 0..2 delta factor.
      const factor = weight <= 0.5 ? 2 - weight * 4 : weight * 4 - 2;
      const divisor = midpointTargets.length || 1;
      for (let index = 0; index < attr.array.length; index++) {
        const shaped = base[index] + (endpoint[index] - base[index]) * factor;
        attr.array[index] += (shaped - base[index]) / divisor;
      }
      continue;
    }
    if (weight === 0) continue;
    for (let index = 0; index < attr.array.length; index++) {
      attr.array[index] += (target.positions[index] - base[index]) * weight;
    }
  }
  attr.needsUpdate = true;
  mesh.geometry.computeVertexNormals();
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
}

export function refreshMeshes() {
  activeMeshes.forEach(mesh => {
    if ((mesh.userData.conditions || []).length > 0) {
      mesh.userData.manualVisible = conditionsSatisfied(mesh);
    }
    applyMeshVisibility(mesh, { notify: false });
    if (!mesh.userData.defaultCaptured) {
      mesh.userData.loadedVisible = mesh.visible;
      mesh.userData.defaultCaptured = true;
    }
    applyTextureVariant(mesh);
    applyShapeTargets(mesh);
  });
  notifyMeshStateChanged(activeMeshes);
  requestRender();
}
