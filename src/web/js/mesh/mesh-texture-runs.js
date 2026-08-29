// Ordered texture propagation belongs to mesh state, not panel rendering.

import { setMeshTextureState } from './mesh-factory.js';
import { usesPackedNormal } from './material-profile.js';

const textureRunGroups = new Set();

function normalMapsFor(mesh, option) {
  if (usesPackedNormal(mesh.material)) {
    return {
      normal_map: null,
      normal_data: option?.normal_data || null,
    };
  }
  return {
    normal_map: option?.normal_map || null,
    normal_data: option?.normal_data || null,
  };
}

export function registerTextureRunGroup(groupMeshes) {
  if (groupMeshes) textureRunGroups.add(groupMeshes);
}

export function unregisterTextureRunGroup(groupMeshes) {
  textureRunGroups.delete(groupMeshes);
}

export function clearTextureRunGroups() {
  textureRunGroups.clear();
}

/** Reconcile one ordered component texture run after its texture state changes. */
export function recomputeTextureRuns(groupMeshes, { render = true } = {}) {
  let activeKey = null;
  let activeMaps = null;
  const changedMeshes = new Set();
  for (const mesh of groupMeshes) {
    if (mesh.userData.manualTexOverride !== undefined) {
      activeKey = mesh.userData.manualTexOverride;
      const option = (mesh.userData.texturePool || [])
        .find(candidate => candidate.tex_key === activeKey);
      activeMaps = option ? {
        ...normalMapsFor(mesh, option),
        light_map: option.light_map || null,
        material_map: option.material_map || null,
        emission_map: option.emission_map || null,
      } : null;
    } else if (mesh.userData.automaticTextureBoundary
               && !mesh.userData.textureHighlightDisabled) {
      // Automatic boundaries use the live resolved diffuse and propagate only
      // until the next boundary in this ordered component.
      activeKey = mesh.userData.resolvedTexKey;
      const diffuseKeys = new Set([
        activeKey,
        mesh.userData.defaultTexKey,
        ...(mesh.userData.textureVariants || []).map(variant => variant.tex_key),
      ].filter(Boolean));
      for (const option of (mesh.userData.texturePool || [])) {
        if (!diffuseKeys.has(option.tex_key)) continue;
        const resolvedMaps = {
          ...normalMapsFor(mesh, {
            normal_map: mesh.userData.resolvedNormalMapKey,
            normal_data: mesh.userData.resolvedNormalDataKey,
          }),
          light_map: mesh.userData.resolvedLightMapKey,
          material_map: mesh.userData.resolvedMaterialMapKey,
          emission_map: mesh.userData.resolvedEmissionMapKey,
        };
        for (const [field, key] of Object.entries(resolvedMaps)) {
          if (option[`${field}_manual`]) continue;
          if (key) option[field] = key;
          else delete option[field];
        }
      }
      activeMaps = null;
    }
    const maps = activeMaps || {
      ...normalMapsFor(mesh, {
        normal_map: mesh.userData.resolvedNormalMapKey,
        normal_data: mesh.userData.resolvedNormalDataKey,
      }),
      light_map: mesh.userData.resolvedLightMapKey,
      material_map: mesh.userData.resolvedMaterialMapKey,
      emission_map: mesh.userData.resolvedEmissionMapKey,
    };
    if (setMeshTextureState(mesh, { diffuse: activeKey, ...maps }, { render })) {
      changedMeshes.add(mesh);
    }
  }
  return changedMeshes;
}

/** Reconcile all registered component runs after a texture-dirty refresh. */
export function recomputeAllTextureRuns({ render = true } = {}) {
  const changedMeshes = new Set();
  for (const groupMeshes of textureRunGroups) {
    for (const mesh of recomputeTextureRuns(groupMeshes, { render })) {
      changedMeshes.add(mesh);
    }
  }
  return changedMeshes;
}
