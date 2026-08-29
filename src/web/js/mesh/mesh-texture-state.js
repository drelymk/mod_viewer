// Component texture propagation, lazy loading and viewer-only persistence.

import { activeMeshes } from './mesh-state.js';
import { usesPackedNormal } from './material-profile.js';
import { setHealthReport } from '../panels/health-report.js';

export {
  clearTextureRunGroups, recomputeAllTextureRuns, recomputeTextureRuns,
  registerTextureRunGroup, unregisterTextureRunGroup,
} from './mesh-texture-runs.js';

export function legacyMeshMetadataKey(name, entry) {
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
      emission_map: option.emission_map || null,
      normal_data_manual: !!option.normal_data_manual,
      light_map_manual: !!option.light_map_manual,
      material_map_manual: !!option.material_map_manual,
      emission_map_manual: !!option.emission_map_manual,
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
