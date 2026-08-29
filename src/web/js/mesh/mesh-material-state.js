// Runtime material replacement and metadata state for an existing mesh.

import {
  captureGameMaterialViewerState, createGameMaterial, disposeGameMaterial,
  restoreGameMaterialViewerState,
} from './material-profile.js';
import { setMeshTextureState } from './mesh-factory.js';
import { initializeMeshRenderModes } from '../scene/render-modes.js';
import { requestRender } from '../scene/render-scheduler.js';

const MATERIAL_METADATA_FIELDS = [
  ['materialKind', 'material_kind'],
  ['materialKindReliable', 'material_kind_reliable'],
  ['materialKindReason', 'material_kind_reason'],
  ['materialKindOverride', 'material_kind_override'],
  ['materialProfileId', 'material_profile_id'],
];

export function updateMeshMaterialMetadata(mesh, metadata, profile) {
  let changed = false;
  for (const [target, source] of MATERIAL_METADATA_FIELDS) {
    if (!Object.hasOwn(metadata, source)) continue;
    const value = metadata[source];
    let next;
    if (target === 'materialKindReliable') {
      next = value === true;
    } else if (target === 'materialKindOverride') {
      next = value || null;
    } else if (target === 'materialProfileId') {
      next = value || 'none';
    } else if (target === 'materialKind') {
      next = value || 'unknown';
    } else {
      next = value || '';
    }
    changed = !Object.is(mesh.userData[target], next) || changed;
    mesh.userData[target] = next;
  }
  if (Object.hasOwn(metadata, 'material_profile_id')) {
    mesh.userData.materialProfile = profile;
  }
  return changed;
}

/** Replace one mesh material without replacing its mesh or geometry. */
export function replaceMeshMaterial(
    mesh, profile, metadata = {}, { render = true, disposeOld = true } = {}) {
  const oldMaterial = mesh.material;
  const viewerState = captureGameMaterialViewerState(oldMaterial);
  const previousMetadata = Object.fromEntries(
    MATERIAL_METADATA_FIELDS.map(([target]) => [target, mesh.userData[target]])
      .concat([['materialProfile', mesh.userData.materialProfile]]));
  const textureStateFields = [
    'texKey', 'normalMapKey', 'normalDataKey', 'lightMapKey',
    'materialMapKey', 'emissionMapKey',
  ];
  const previousTextureState = Object.fromEntries(
    textureStateFields.map(field => [field, mesh.userData[field]]));
  const nextMaterial = createGameMaterial(
    profile, mesh.userData.fallbackColor ?? 0xcccccc,
    { hasUv: !!mesh.geometry?.attributes?.uv });
  try {
    mesh.material = nextMaterial;
    initializeMeshRenderModes(mesh);
    restoreGameMaterialViewerState(nextMaterial, viewerState);
    updateMeshMaterialMetadata(mesh, metadata, profile);
    setMeshTextureState(mesh, {
      diffuse: mesh.userData.texKey,
      // The applied normal-map key is intentionally not authoritative when
      // the new profile consumes packed normal data instead.
      normal_map: mesh.userData.resolvedNormalMapKey,
      normal_data: mesh.userData.normalDataKey
        || mesh.userData.resolvedNormalDataKey,
      light_map: mesh.userData.lightMapKey,
      material_map: mesh.userData.materialMapKey,
      emission_map: mesh.userData.emissionMapKey,
    }, { render: false });
  } catch (error) {
    mesh.material = oldMaterial;
    for (const [target, value] of Object.entries(previousMetadata)) {
      mesh.userData[target] = value;
    }
    for (const [field, value] of Object.entries(previousTextureState)) {
      mesh.userData[field] = value;
    }
    disposeGameMaterial(nextMaterial);
    nextMaterial.dispose();
    throw error;
  }
  if (disposeOld) {
    disposeGameMaterial(oldMaterial);
    oldMaterial.dispose();
  }
  if (render) requestRender();
  return disposeOld ? true : {material: nextMaterial, oldMaterial};
}
