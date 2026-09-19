// Build and register live model meshes from a backend payload. UI panels bind
// their rows to the returned meshes but do not own this model construction.

import {buildMesh} from './mesh-factory.js';
import {addMesh} from './mesh-state.js';
import {normalizeColorAdjustment} from './color-adjustment.js';
import {syncMeshColorAdjustment} from './mesh-color-session.js';
import {registerAnimatedMesh} from './animation-runtime.js';

function legacyMeshMetadataKey(name, entry) {
  const component = entry.component || name.replace(/-\d+$/, '');
  const draw = entry.drawindexed ? entry.drawindexed.join(',') : 'whole';
  return `${component}::${draw}`;
}

/** Build the registered live meshes represented by a payload. */
export function buildPayloadMeshes(entries = {}, modPath = null,
    meshNames = {}, materialProfiles = {}, options = {}) {
  const texturePools = options.texturePools || {};
  const colorAdjustments = options.colorAdjustments || {};
  const animationClocks = options.animations || {};
  const liveMeshes = new Map();
  for (const [name, entry] of Object.entries(entries)) {
    if (entry?.error) continue;
    const materialProfile = materialProfiles?.[entry.material_profile_id] || null;
    const mesh = buildMesh(name, entry, materialProfile);
    const metadataKey = entry.identity?.key
      || legacyMeshMetadataKey(name, entry);
    const texturePool = entry.texture_pool_id
      ? texturePools[entry.texture_pool_id] || [] : [];
    mesh.userData.semanticKey = name;
    mesh.userData.identity = entry.identity || null;
    mesh.userData.metadataKey = metadataKey;
    mesh.userData.colorAdjustment = normalizeColorAdjustment(
      colorAdjustments[metadataKey]);
    mesh.userData.texturePool = texturePool;
    mesh.userData.displayName = meshNames[metadataKey]
      || entry.display_name || null;
    mesh.userData.meshNames = meshNames;
    mesh.userData.modPath = modPath;
    mesh.userData.assetFill = entry.asset_fill === true;
    // Diagnostic-only projection. Operational identity remains the semantic
    // key and component grouping resolved by the panel.
    mesh.userData.assetEntry = entry;
    addMesh(mesh, entry.conditions, entry.sources, entry.texture_variants, {
      normal_map: entry.normal_map_variants,
      normal_data: entry.normal_data_variants,
      light_map: entry.light_map_variants,
      material_map: entry.material_map_variants,
      emission_map: entry.emission_map_variants,
    });
    registerAnimatedMesh(
      mesh, entry.animation_id, entry.animation_geometry, animationClocks);
    syncMeshColorAdjustment(mesh, {render: false});
    // addMesh establishes automatic defaults; restore persisted viewer
    // choices only after that initialization has completed.
    if (Object.hasOwn(entry, 'saved_texture_override')) {
      mesh.userData.manualTexOverride = entry.saved_texture_override;
    }
    liveMeshes.set(name, mesh);
  }
  return liveMeshes;
}
