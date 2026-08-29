// Active meshes and the mod control state resolved onto their rendering data.

import {
  invalidateCharacterShadowGeometry, invalidateCharacterShadowVisibility,
  forgetModelMeshes as forgetSceneModelMeshes,
  resetCharacterShadows, scene, resetModelOrientation,
} from '../scene/scene.js';
import { dnfSatisfied, getControlValue } from '../editing/control-state.js';
import { disposeGameMaterial } from './material-profile.js';
import {
  setMeshTextureState, updateGeometryNormals,
} from './mesh-factory.js';
import {
  replaceMeshMaterial, updateMeshMaterialMetadata,
} from './mesh-material-state.js';
import { clearTextureRunGroups, recomputeAllTextureRuns } from './mesh-texture-runs.js';
import { attachOutline, detachOutline } from '../scene/outline-renderer.js';
import { initializeMeshRenderModes } from '../scene/render-modes.js';
import { requestRender } from '../scene/render-scheduler.js';
import { notifyMeshStateChanged } from './mesh-state-events.js';
import {
  disposeSkinningExperiment, getSkinningBaseMaterial, getSkinningState,
  destroyModelPhysicsSession, registerSkinningMesh,
  refreshSkinningAfterShapeChange,
  unregisterSkinningMesh,
  withSkinningBaseMaterial,
} from './weight-experiment.js';

export const activeMeshes = [];
const controlDependencies = new WeakMap();

/** Add every variable referenced by an existing DNF condition structure. */
export function variablesFromConditions(conditions, variables = new Set()) {
  for (const group of conditions || []) {
    for (const condition of group || []) {
      if (condition?.var) variables.add(condition.var);
    }
  }
  return variables;
}

function variablesFromVariants(variants, variables) {
  for (const variant of variants || []) {
    variablesFromConditions(variant?.conditions, variables);
  }
}

/** Lazily derive the control categories that can affect one mesh. */
export function dependenciesFor(mesh) {
  let dependencies = controlDependencies.get(mesh);
  if (dependencies) return dependencies;

  const visibility = variablesFromConditions(mesh.userData?.conditions);
  const textures = new Set();
  for (const field of [
    'textureVariants', 'normalMapVariants', 'normalDataVariants',
    'lightMapVariants', 'materialMapVariants', 'emissionMapVariants',
  ]) {
    variablesFromVariants(mesh.userData?.[field], textures);
  }
  const shapes = new Set(
    (mesh.userData?.shapeTargets || [])
      .map(target => target?.var)
      .filter(Boolean));
  dependencies = { visibility, textures, shapes };
  controlDependencies.set(mesh, dependencies);
  return dependencies;
}

export function invalidateControlDependencies(mesh) {
  controlDependencies.delete(mesh);
}

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
  destroyModelPhysicsSession();
  clearTextureRunGroups();
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
  registerSkinningMesh(mesh);
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

const SEMANTIC_SNAPSHOT_FIELDS = [
  'conditions', 'sources', 'source', 'component',
  'identity',
  'textureVariants', 'normalMapVariants', 'normalDataVariants',
  'lightMapVariants', 'materialMapVariants', 'emissionMapVariants',
  'defaultTexKey', 'defaultNormalMapKey', 'defaultNormalDataKey',
  'defaultLightMapKey', 'defaultMaterialMapKey', 'defaultEmissionMapKey',
  'assetEntry', 'materialKind', 'materialKindReliable',
  'materialKindReason', 'materialKindOverride', 'materialProfileId',
  'materialProfile', 'texKey', 'normalMapKey', 'normalDataKey',
  'lightMapKey', 'materialMapKey', 'emissionMapKey',
];

function snapshotMeshSemantics(mesh) {
  const values = {};
  for (const field of SEMANTIC_SNAPSHOT_FIELDS) {
    values[field] = mesh.userData[field];
  }
  return {mesh, values};
}

function restoreMeshSemantics(snapshot) {
  Object.assign(snapshot.mesh.userData, snapshot.values);
}

/** Replace draw visibility, texture and material semantics without reloading. */
export function updateMeshSemantics(semantics, { materialProfiles = {} } = {}) {
  const next = semantics || {};
  const semanticMeshes = activeMeshes.filter(
    mesh => mesh.userData.assetFill !== true);
  const keys = semanticMeshes.map(mesh => mesh.userData.semanticKey);
  if (keys.some(key => !next[key])
      || Object.keys(next).length !== keys.length) {
    return {success: false, materialChangedMeshes: []};
  }
  const identityMismatch = semanticMeshes.some(mesh => {
    const currentKey = mesh.userData.identity?.key;
    const nextKey = next[mesh.userData.semanticKey]?.identity?.key;
    return currentKey && nextKey && currentKey !== nextKey;
  });
  if (identityMismatch) {
    return {success: false, materialChangedMeshes: []};
  }

  const updates = semanticMeshes.map(mesh => {
    const semantic = next[mesh.userData.semanticKey];
    if (!Object.hasOwn(semantic, 'material_profile_id')) {
      return {mesh, semantic, material: null};
    }
    const profileId = semantic.material_profile_id || 'none';
    const profile = materialProfiles?.[profileId] || null;
    if (profileId !== 'none' && !profile) return null;
    return {
      mesh,
      semantic,
      material: {
        profile,
        changed: mesh.userData.materialProfileId !== profileId,
      },
    };
  });
  if (updates.some(update => update === null)) {
    return {success: false, materialChangedMeshes: []};
  }

  const snapshots = semanticMeshes.map(snapshotMeshSemantics);
  const materialChangedMeshes = [];
  const replacements = [];
  try {
    updates.forEach(({mesh, semantic, material}) => {
      mesh.userData.conditions = semantic.conditions || [];
      mesh.userData.sources = semantic.sources || [];
      if (Object.hasOwn(semantic, 'source')) {
        mesh.userData.source = semantic.source;
      }
      if (Object.hasOwn(semantic, 'component')) {
        mesh.userData.component = semantic.component;
      }
      if (Object.hasOwn(semantic, 'identity')) {
        mesh.userData.identity = semantic.identity || null;
      }
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
      const assetEntry = {...(mesh.userData.assetEntry || {})};
      for (const field of [
        'asset_binding', 'texture_resolution', 'asset_slot_evidence',
      ]) {
        if (Object.hasOwn(semantic, field)) assetEntry[field] = semantic[field];
        else delete assetEntry[field];
      }
      mesh.userData.assetEntry = assetEntry;
      if (material?.changed) {
        const replacement = withSkinningBaseMaterial(mesh, () =>
          replaceMeshMaterial(mesh, material.profile, semantic, {
            render: false, disposeOld: false,
          }));
        replacements.push({
          mesh, oldMaterial: replacement.oldMaterial,
          newMaterial: replacement.material,
        });
        materialChangedMeshes.push(mesh);
      } else if (material && updateMeshMaterialMetadata(
          mesh, semantic, material.profile)) {
        materialChangedMeshes.push(mesh);
      }
      invalidateControlDependencies(mesh);
    });
  } catch (error) {
    console.error('Could not apply mesh semantic material update', error);
    for (const {mesh, oldMaterial, newMaterial} of replacements.reverse()) {
      const currentMaterial = getSkinningBaseMaterial(mesh);
      const materialToDispose = currentMaterial === oldMaterial
        ? newMaterial : currentMaterial;
      if (materialToDispose && materialToDispose !== oldMaterial) {
        disposeGameMaterial(materialToDispose);
        materialToDispose.dispose();
      }
      withSkinningBaseMaterial(mesh, () => {
        mesh.material = oldMaterial;
      });
    }
    snapshots.forEach(restoreMeshSemantics);
    return {success: false, materialChangedMeshes: []};
  }
  replacements.forEach(({oldMaterial}) => {
    disposeGameMaterial(oldMaterial);
    oldMaterial.dispose();
  });
  return {success: true, materialChangedMeshes};
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

export function applyTextureVariant(mesh, { render = true } = {}) {
  const previous = [
    mesh.userData.resolvedTexKey,
    mesh.userData.resolvedNormalMapKey,
    mesh.userData.resolvedNormalDataKey,
    mesh.userData.resolvedLightMapKey,
    mesh.userData.resolvedMaterialMapKey,
    mesh.userData.resolvedEmissionMapKey,
    mesh.userData.texKey,
    mesh.userData.normalMapKey,
    mesh.userData.normalDataKey,
    mesh.userData.lightMapKey,
    mesh.userData.materialMapKey,
    mesh.userData.emissionMapKey,
  ];
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
  const materialChanged = setMeshTextureState(mesh, {
    diffuse: mesh.userData.manualTexOverride !== undefined
      ? mesh.userData.manualTexOverride
      : mesh.userData.resolvedTexKey,
    normal_map: mesh.userData.resolvedNormalMapKey,
    normal_data: mesh.userData.resolvedNormalDataKey,
    light_map: mesh.userData.resolvedLightMapKey,
    material_map: mesh.userData.resolvedMaterialMapKey,
    emission_map: mesh.userData.resolvedEmissionMapKey,
  }, { render });
  const next = [
    mesh.userData.resolvedTexKey,
    mesh.userData.resolvedNormalMapKey,
    mesh.userData.resolvedNormalDataKey,
    mesh.userData.resolvedLightMapKey,
    mesh.userData.resolvedMaterialMapKey,
    mesh.userData.resolvedEmissionMapKey,
    mesh.userData.texKey,
    mesh.userData.normalMapKey,
    mesh.userData.normalDataKey,
    mesh.userData.lightMapKey,
    mesh.userData.materialMapKey,
    mesh.userData.emissionMapKey,
  ];
  return materialChanged || next.some((value, index) => !Object.is(value, previous[index]));
}

// The MESHES control is the direct visibility source. Automatic refreshes
// re-baseline visibility and clear any transient manual eye-click marker.
export function applyMeshVisibility(mesh, { notify = true, render = true } = {}) {
  const previous = mesh.visible;
  mesh.visible = mesh.userData.manualVisible !== false;
  const changed = previous !== mesh.visible;
  if (changed) invalidateCharacterShadowVisibility({ request: render });
  if (notify) notifyMeshStateChanged([mesh]);
  if (render) requestRender();
  return changed;
}

function applyShapeTargets(mesh, { render = true } = {}) {
  const targets = mesh.userData.shapeTargets || [];
  if (!targets.length) return false;
  const controlValues = targets.map(target => getControlValue(target.var) ?? 0);
  const previous = mesh.userData.shapeControlValues;
  if (previous?.length === controlValues.length
      && controlValues.every((value, index) => value === previous[index])) return false;
  const skinning = getSkinningState(mesh);
  const restartSkinningLoad = !!(skinning?.loading || skinning?.promise);
  if (skinning?.loading || skinning?.promise) {
    disposeSkinningExperiment(mesh, {preserveRegistration: true});
  }
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
  refreshSkinningAfterShapeChange(mesh);
  if (restartSkinningLoad) registerSkinningMesh(mesh);
  invalidateCharacterShadowGeometry({ request: render });
  return true;
}

function intersects(left, right) {
  for (const value of left) if (right.has(value)) return true;
  return false;
}

/** Apply only mesh categories affected by the final control-state diff. */
export function refreshMeshes(options) {
  // Keep direct low-level callers compatible with the former all-mesh API.
  const legacyRefresh = options === undefined;
  const {
    changedVariables = new Set(),
    force = {},
    additionalMeshes = [],
  } = options || {};
  const changed = changedVariables instanceof Set
    ? changedVariables : new Set(changedVariables || []);
  const effectiveForce = legacyRefresh
    ? { visibility: true, textures: true, shapes: true } : force;
  const visibilityForced = effectiveForce.visibility === true;
  const texturesForced = effectiveForce.textures === true;
  const shapesForced = effectiveForce.shapes === true;
  const normalMeshes = activeMeshes.filter(mesh => mesh.userData.assetFill !== true);
  const textureDirty = texturesForced || normalMeshes.some(mesh =>
    intersects(dependenciesFor(mesh).textures, changed));
  const changedMeshes = new Set();
  for (const mesh of additionalMeshes || []) {
    if (activeMeshes.includes(mesh)) changedMeshes.add(mesh);
  }
  let visibilityChanged = false;
  let texturesChanged = false;
  let shapesChanged = false;

  for (const mesh of activeMeshes) {
    const dependencies = dependenciesFor(mesh);
    const needsVisibility = visibilityForced
      || mesh.userData.manuallyToggled === true
      || intersects(dependencies.visibility, changed);
    if (needsVisibility) {
      mesh.userData.manualVisible = conditionsSatisfied(mesh);
      mesh.userData.manuallyToggled = false;
      visibilityChanged = applyMeshVisibility(mesh, {
        notify: false, render: false,
      }) || visibilityChanged;
      changedMeshes.add(mesh);
      if (!mesh.userData.defaultCaptured) {
        mesh.userData.loadedVisible = mesh.visible;
        mesh.userData.defaultCaptured = true;
      }
    }

    if (textureDirty && mesh.userData.assetFill !== true) {
      const changed = applyTextureVariant(mesh, { render: false });
      texturesChanged = changed || texturesChanged;
      if (changed) changedMeshes.add(mesh);
    }

    if (shapesForced || intersects(dependencies.shapes, changed)) {
      if (applyShapeTargets(mesh, { render: false })) {
        shapesChanged = true;
        changedMeshes.add(mesh);
      }
    }
  }

  if (textureDirty) {
    const runChangedMeshes = recomputeAllTextureRuns({ render: false });
    for (const mesh of runChangedMeshes) changedMeshes.add(mesh);
    texturesChanged = runChangedMeshes.size > 0 || texturesChanged;
  }

  const changedList = [...changedMeshes];
  if (changedList.length) notifyMeshStateChanged(changedList);
  if (visibilityChanged || texturesChanged || shapesChanged
      || (additionalMeshes?.length && changedMeshes.size)) requestRender();
  return {
    visibilityChanged,
    texturesChanged,
    shapesChanged,
    changedMeshes: changedList,
  };
}
