// Component texture propagation, lazy loading and viewer-only persistence.

import { addTexture, hasTexture, setMeshTextureState } from './mesh-factory.js';
import { activeMeshes } from './mesh-state.js';
import { usesPackedNormal } from './material-profile.js';
import { setHealthReport } from './health-report.js';

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

export function recomputeTextureRuns(groupMeshes) {
  let activeKey = null;
  let activeMaps = null;
  for (const mesh of groupMeshes) {
    if (mesh.userData.manualTexOverride !== undefined) {
      activeKey = mesh.userData.manualTexOverride;
      const option = (mesh.userData.texturePool || [])
        .find(candidate => candidate.tex_key === activeKey);
      activeMaps = option ? {
        ...normalMapsFor(mesh, option),
        light_map: option.light_map || null,
        material_map: option.material_map || null,
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
    };
    setMeshTextureState(mesh, { diffuse: activeKey, ...maps });
  }
}

export async function ensureTextureLoaded(mesh, texKey) {
  if (!texKey || hasTexture(texKey)) return true;
  const api = window.pywebview?.api;
  // Browser-only fixtures exercise state without the native image bridge.
  if (!api?.load_texture_file) return true;
  const result = await api.load_texture_file(mesh.userData.modPath, texKey);
  if (!result || result.error) return false;
  addTexture(result.tex_key, result.uri);
  return true;
}

export function meshMetadataKey(name, entry) {
  const component = entry.component || name.replace(/-\d+$/, '');
  const draw = entry.drawindexed ? entry.drawindexed.join(',') : 'whole';
  return `${component}::${draw}`;
}

export function saveTextureState(modPath) {
  if (!modPath || !window.pywebview?.api?.save_mesh_textures) return;
  const state = {};
  for (const mesh of activeMeshes) {
    let texKey;
    let manual = false;
    if (mesh.userData.manualTexOverride !== undefined) {
      texKey = mesh.userData.manualTexOverride;
      manual = true;
    } else if (mesh.userData.automaticTextureBoundary
               && !mesh.userData.textureHighlightDisabled) {
      texKey = mesh.userData.resolvedTexKey;
    }
    if (!texKey) continue;
    const option = (mesh.userData.texturePool || [])
      .find(candidate => candidate.tex_key === texKey);
    // Removing an option also removes its persisted highlight.
    if (!option) continue;
    const savedState = {
      tex_key: texKey,
      label: option.label,
      manual,
      normal_data: option.normal_data || null,
      light_map: option.light_map || null,
      material_map: option.material_map || null,
      normal_data_manual: !!option.normal_data_manual,
      light_map_manual: !!option.light_map_manual,
      material_map_manual: !!option.material_map_manual,
    };
    if (!usesPackedNormal(mesh.material)) {
      savedState.normal_map = option.normal_map || null;
      savedState.normal_map_manual = !!option.normal_map_manual;
    }
    state[mesh.userData.metadataKey] = savedState;
  }
  const request = window.pywebview.api.save_mesh_textures(modPath, state);
  if (request && typeof request.then === 'function') {
    request.then(result => {
      if (!result?.error) setHealthReport(null);
    }, () => {});
  } else {
    setHealthReport(null);
  }
}
