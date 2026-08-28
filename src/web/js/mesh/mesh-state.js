// Active meshes and the mod control state resolved onto their rendering data.

import {
  invalidateCharacterShadowGeometry, invalidateCharacterShadowVisibility,
  forgetModelMeshes as forgetSceneModelMeshes,
  resetCharacterShadows, scene, resetModelOrientation,
} from '../scene/scene.js';
import { dnfSatisfied, getControlValue } from '../editing/control-state.js';
import { disposeGameMaterial } from './material-profile.js';
import { setMeshTextureState, updateGeometryNormals } from './mesh-factory.js';
import { attachOutline, detachOutline } from '../scene/outline-renderer.js';
import { initializeMeshRenderModes } from '../scene/render-modes.js';
import { requestRender } from '../scene/render-scheduler.js';
import { notifyMeshStateChanged } from './mesh-state-events.js';
import {
  disposeSkinningExperiment, getSkinningState,
} from './weight-experiment.js';

export const activeMeshes = [];

export function resetMeshes({ preserveModelOrientation = false } = {}) {
  activeMeshes.forEach(mesh => {
    disposeSkinningExperiment(mesh);
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
  resetModelOrientation({ preserveRotation: preserveModelOrientation });
  resetCharacterShadows();
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
  mesh.userData.emissionMapVariants = materialVariants.emission_map || [];
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

export function removeMesh(mesh) {
  if (!mesh) return false;
  const index = activeMeshes.indexOf(mesh);
  if (index < 0) return false;
  disposeSkinningExperiment(mesh);
  detachOutline(mesh);
  scene.remove(mesh);
  mesh.geometry?.dispose?.();
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  materials.forEach(material => {
    disposeGameMaterial(material);
    material?.dispose?.();
  });
  activeMeshes.splice(index, 1);
  forgetSceneModelMeshes([mesh]);
  return true;
}

export function removeAssetFillMeshes() {
  const removed = activeMeshes
    .filter(mesh => mesh.userData.assetFill === true)
    .slice();
  removed.forEach(removeMesh);
  if (removed.length) requestRender();
  return removed.length;
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

/** Replace draw visibility and texture semantics on the existing meshes. */
export function updateMeshSemantics(semantics) {
  const next = semantics || {};
  const semanticMeshes = activeMeshes.filter(
    mesh => mesh.userData.assetFill !== true);
  const keys = semanticMeshes.map(mesh => mesh.userData.semanticKey);
  if (keys.some(key => !next[key])
      || Object.keys(next).length !== keys.length) return false;
  semanticMeshes.forEach(mesh => {
    const semantic = next[mesh.userData.semanticKey];
    mesh.userData.conditions = semantic.conditions || [];
    mesh.userData.sources = semantic.sources || [];
    const variants = [
      ['textureVariants', 'texture_variants'],
      ['normalMapVariants', 'normal_map_variants'],
      ['normalDataVariants', 'normal_data_variants'],
      ['lightMapVariants', 'light_map_variants'],
      ['materialMapVariants', 'material_map_variants'],
      ['emissionMapVariants', 'emission_map_variants'],
    ];
    for (const [target, source] of variants) {
      mesh.userData[target] = semantic[source] || [];
    }
    const defaults = [
      ['defaultTexKey', 'tex_key'],
      ['defaultNormalMapKey', 'normal_map_key'],
      ['defaultNormalDataKey', 'normal_data_key'],
      ['defaultLightMapKey', 'light_map_key'],
      ['defaultMaterialMapKey', 'material_map_key'],
      ['defaultEmissionMapKey', 'emission_map_key'],
    ];
    for (const [target, source] of defaults) {
      if (Object.hasOwn(semantic, source)) {
        mesh.userData[target] = semantic[source] || null;
      }
    }
    const assetEntry = mesh.userData.assetEntry || {};
    for (const field of [
      'asset_binding', 'texture_resolution', 'asset_slot_evidence',
    ]) {
      if (Object.hasOwn(semantic, field)) assetEntry[field] = semantic[field];
      else delete assetEntry[field];
    }
    mesh.userData.assetEntry = assetEntry;
    applyTextureVariant(mesh);
  });
  return true;
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
  mesh.userData.resolvedEmissionMapKey = resolve(
    mesh.userData.emissionMapVariants, mesh.userData.defaultEmissionMapKey);
  setMeshTextureState(mesh, {
    diffuse: mesh.userData.manualTexOverride !== undefined
      ? mesh.userData.manualTexOverride
      : mesh.userData.resolvedTexKey,
    normal_map: mesh.userData.resolvedNormalMapKey,
    normal_data: mesh.userData.resolvedNormalDataKey,
    light_map: mesh.userData.resolvedLightMapKey,
    material_map: mesh.userData.resolvedMaterialMapKey,
    emission_map: mesh.userData.resolvedEmissionMapKey,
  });
}

// The MESHES control is the direct visibility source. Automatic refreshes
// re-baseline visibility and clear any transient manual eye-click marker.
export function applyMeshVisibility(mesh, { notify = true } = {}) {
  const previous = mesh.visible;
  mesh.visible = mesh.userData.manualVisible !== false;
  if (previous !== mesh.visible) invalidateCharacterShadowVisibility();
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
  if (getSkinningState(mesh)?.loaded) disposeSkinningExperiment(mesh);
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
  const deformed = attr.array.some((value, index) => value !== base[index]);
  attr.needsUpdate = true;
  updateGeometryNormals(mesh, deformed);
  mesh.geometry.computeBoundingBox();
  mesh.geometry.computeBoundingSphere();
  invalidateCharacterShadowGeometry();
}

export function refreshMeshes() {
  activeMeshes.forEach(mesh => {
    mesh.userData.manualVisible = conditionsSatisfied(mesh);
    mesh.userData.manuallyToggled = false;
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
