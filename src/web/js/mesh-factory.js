// Building Three.js meshes from payload entries, plus the texture registry.

import * as THREE from 'three';
import { decodeF32, decodeU32 } from './decode.js';
import {
  createGameMaterial, getGameMaterialSources, updateGameMaterialTextures,
} from './material-profile.js';
import { getMeshView } from './mesh-view-bindings.js';
import { loadDDSTexture } from './dds-loader.js';
import { supportsBCTextureCompression } from './renderer-capabilities.js';

// Textures arrive as data URIs or same-origin localhost URLs keyed by name;
// loaders are cached so several meshes sharing a texture share one GPU upload.
let registry = {};
const loaders = {};
const failedTextures = new Set();
const nativeDDSFallbacks = new Set();
const textureUsers = new Map();
// all: diffuse + INI material maps; diffuse: diffuse only; none: flat colour.
let textureMode = 'all';

function disposeTexture(texture) {
  texture?.dispose?.();
}

export function setTextures(textures) {
  registry = textures || {};
  for (const key of Object.keys(loaders)) {
    disposeTexture(loaders[key]);
    delete loaders[key];
  }
  failedTextures.clear();
  nativeDDSFallbacks.clear();
  textureUsers.clear();
}

/** Merge one texture into the shared registry without touching the rest --
 * used when the user adds a texture via the per-component picker (see
 * web/js/mesh-panel.js). View-only/session-scoped: never reaches the ini. */
export function addTexture(key, uri) {
  registry[key] = uri;
  for (const cacheKey of Object.keys(loaders)) {
    if (cacheKey.endsWith(`|${key}`)) {
      disposeTexture(loaders[cacheKey]);
      delete loaders[cacheKey];
      failedTextures.delete(cacheKey);
      nativeDDSFallbacks.delete(cacheKey);
      textureUsers.delete(cacheKey);
    }
  }
  for (const cacheKey of [...failedTextures]) {
    if (cacheKey.endsWith(`|${key}`)) {
      failedTextures.delete(cacheKey);
      nativeDDSFallbacks.delete(cacheKey);
      textureUsers.delete(cacheKey);
    }
  }
}

function registryKey(key, role = 'diffuse') {
  if (!key) return null;
  if (registry[key]) return key;
  const prefix = `${role}::`;
  return (!key.includes('::') && registry[prefix + key]) ? prefix + key : key;
}

export function hasTexture(key, role = 'diffuse') {
  const resolved = registryKey(key, role);
  const cacheKey = resolved && registry[resolved]
    ? `${role}|${resolved}` : null;
  return !!(cacheKey && !failedTextures.has(cacheKey));
}

function trackTextureUser(mesh, cacheKey) {
  let users = textureUsers.get(cacheKey);
  if (!users) {
    users = new Set();
    textureUsers.set(cacheKey, users);
  }
  users.add(mesh);
}

function enableTextureTransforms(mesh) {
  // Material-profile bindings pass an explicit UV node to TSL, which disables
  // TextureNode's matrix path by default. Native DDS needs one transport-wide
  // vertical transform for orientation parity; identity matrices keep PNG
  // and data-URI textures unchanged.
  const bindings = mesh.material?.userData?.gameMaterial?.bindings;
  for (const binding of Object.values(bindings || {})) {
    binding.textureNode.setUpdateMatrix?.(true);
  }
}

function handleTextureError(cacheKey, texture, resolved, uri) {
  // A manual replacement or a newer model may have evicted this request
  // before the browser reported its failure. Do not mark the replacement as
  // unavailable in that case.
  if (registry[resolved] !== uri
      || (loaders[cacheKey] && loaders[cacheKey] !== texture)) {
    disposeTexture(texture);
    return;
  }
  failedTextures.add(cacheKey);
  if (loaders[cacheKey] === texture) delete loaders[cacheKey];
  disposeTexture(texture);
  for (const mesh of textureUsers.get(cacheKey) || []) refreshMeshTexture(mesh);
}

function isDDSUri(uri) {
  return typeof uri === 'string' && /\.dds(?:[?#]|$)/i.test(uri);
}

function pngFallbackUri(uri) {
  return uri.replace(/\.dds(?=([?#]|$))/i, '.png');
}

function handleNativeDDSFailure(cacheKey, texture, resolved, uri) {
  // A stale native request must not evict a replacement texture.  The next
  // refresh uses the same registry key and the existing PNG publication.
  if (registry[resolved] !== uri
      || loaders[cacheKey] !== texture) {
    disposeTexture(texture);
    return;
  }
  if (nativeDDSFallbacks.has(cacheKey)) return;
  nativeDDSFallbacks.add(cacheKey);
  if (loaders[cacheKey] === texture) delete loaders[cacheKey];
  disposeTexture(texture);
  for (const mesh of textureUsers.get(cacheKey) || []) refreshMeshTexture(mesh);
}

function getTexture(mesh, key, role = 'diffuse') {
  const resolved = registryKey(key, role);
  if (!resolved || !registry[resolved]) return null;
  const cacheKey = `${role}|${resolved}`;
  trackTextureUser(mesh, cacheKey);
  if (failedTextures.has(cacheKey)) return null;
  if (!loaders[cacheKey]) {
    const uri = registry[resolved];
    const nativeDDS = isDDSUri(uri)
      && supportsBCTextureCompression()
      && !nativeDDSFallbacks.has(cacheKey);
    const requestUri = nativeDDS ? uri
      : isDDSUri(uri) ? pngFallbackUri(uri) : uri;
    let texture;
    let failedBeforeAssignment = false;
    const onError = () => {
      if (!texture) {
        failedBeforeAssignment = true;
        return;
      }
      handleTextureError(cacheKey, texture, resolved, uri);
    };
    if (nativeDDS) {
      texture = loadDDSTexture(
        requestUri, undefined,
        () => handleNativeDDSFailure(cacheKey, texture, resolved, uri));
    } else {
      texture = new THREE.TextureLoader().load(
        requestUri, undefined, undefined, onError);
    }
    texture.colorSpace = role === 'diffuse'
      ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    loaders[cacheKey] = texture;
    if (failedBeforeAssignment) {
      handleTextureError(cacheKey, texture, resolved, uri);
    }
  }
  return loaders[cacheKey];
}

/** Bind whatever diffuse map the mesh currently wants, honouring the global
 * textures on/off switch. Falls back to the name-guessed flat colour, which is
 * also what an untextured mesh has always shown. */
export function refreshMeshTexture(mesh) {
  const showDiffuse = textureMode !== 'none';
  const showMaterialMaps = textureMode === 'all';
  const gameMaterialSources = getGameMaterialSources(mesh.material);
  const usePackedSource = role =>
    showMaterialMaps && gameMaterialSources.has(role);
  const map = showDiffuse ? getTexture(mesh, mesh.userData.texKey, 'diffuse') : null;
  const normalMap = showMaterialMaps && mesh.userData.normalMapEnabled !== false
    ? getTexture(mesh, mesh.userData.normalMapKey, 'normal_map') : null;
  const normalData = usePackedSource('normal_data')
    ? getTexture(mesh, mesh.userData.normalDataKey, 'normal_data') : null;
  const lightMap = usePackedSource('light_map')
    ? getTexture(mesh, mesh.userData.lightMapKey, 'light_map') : null;
  const materialMap = usePackedSource('material_map')
    ? getTexture(mesh, mesh.userData.materialMapKey, 'material_map') : null;
  const aoMap = showMaterialMaps
    ? getTexture(mesh, mesh.userData.aoMapKey, 'occlusion_map') : null;
  const packedChanged = updateGameMaterialTextures(mesh, {
    diffuse: map,
    normal_map: normalMap,
    ao_map: aoMap,
    normal_data: normalData, light_map: lightMap, material_map: materialMap,
    normal_map_y_sign: mesh.userData.normalMapYSign ?? -1,
  });
  enableTextureTransforms(mesh);
  const stockChanged = map !== mesh.material.map
    || normalMap !== mesh.material.normalMap
    || aoMap !== mesh.material.aoMap;
  if (!stockChanged && !packedChanged) return;
  if (stockChanged) {
    mesh.material.map = map;
    mesh.material.normalMap = normalMap;
    mesh.material.normalScale.set(
      1, normalMap ? (mesh.userData.normalMapYSign ?? -1) : 1);
    mesh.material.aoMap = aoMap;
    mesh.material.aoMapIntensity = 1.0;
    mesh.material.lightMap = null;
    mesh.material.roughnessMap = null;
    mesh.material.metalnessMap = null;
    mesh.material.roughness = 1;
    mesh.material.metalness = 0;
    mesh.material.color.setHex(map ? 0xffffff : mesh.userData.fallbackColor);
  }
  getMeshView(mesh)?.onTextureChanged?.();
}

export function setTextureMode(mode) {
  textureMode = mode;
}

/** Swap a mesh's diffuse map to another registry entry (a texture-swap toggle). */
export function setMeshTexture(mesh, texKey) {
  mesh.userData.texKey = texKey;
  refreshMeshTexture(mesh);
}

/** Apply the INI-resolved non-diffuse material textures. Manual texture
 * selection intentionally remains diffuse-only for now. */
export function setMeshMaterialMaps(mesh, maps) {
  mesh.userData.normalMapKey = maps.normal_map || null;
  if (Object.hasOwn(maps, 'normal_data')) {
    mesh.userData.normalDataKey = maps.normal_data || null;
  }
  mesh.userData.lightMapKey = maps.light_map || null;
  mesh.userData.materialMapKey = maps.material_map || null;
  refreshMeshTexture(mesh);
}

/** Update the complete resolved texture state with one material refresh. */
export function setMeshTextureState(mesh, state) {
  mesh.userData.texKey = state.diffuse || null;
  mesh.userData.normalMapKey = state.normal_map || null;
  if (Object.hasOwn(state, 'normal_data')) {
    mesh.userData.normalDataKey = state.normal_data || null;
  }
  mesh.userData.lightMapKey = state.light_map || null;
  mesh.userData.materialMapKey = state.material_map || null;
  refreshMeshTexture(mesh);
}

/** Colour for meshes with no texture, guessed from the component name. */
function fallbackColor(name) {
  const n = name.toLowerCase();
  // if (n.includes('wing'))                          return 0xc8a2c8;
  // if (n.includes('hair'))                          return 0xa0d8ef;
  // if (n.includes('body') || n.includes('top'))     return 0xf5cba7;
  // if (n.includes('leg')  || n.includes('bottom'))  return 0xf7dc6f;
  // if (n.includes('belt') || n.includes('bag'))     return 0xadd8e6;
  return 0xcccccc;
}

export function buildMesh(name, data, materialProfile = null) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(decodeF32(data.pos), 3));
  if (data.uv) {
    const uv = new THREE.BufferAttribute(decodeF32(data.uv), 2);
    geo.setAttribute('uv', uv);
    // Light maps use the secondary UV channel. These mods author all material
    // maps against the same Texcoord buffer, so mirror it without inventing UVs.
    geo.setAttribute('uv1', uv);
    geo.setAttribute('uv2', uv);
  }
  geo.setIndex(new THREE.BufferAttribute(decodeU32(data.idx), 1));

  // We only have vertex positions, no authored normals, so smooth shading
  // needs computeVertexNormals() to average one normal per shared vertex
  geo.computeVertexNormals();

  const fallback = fallbackColor(name);
  const mat = createGameMaterial(materialProfile, fallback,
    { hasUv: !!data.uv });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.userData.basePositions = new Float32Array(geo.attributes.position.array);
  mesh.userData.shapeTargets = (data.shape_targets || []).map(target => ({
    var: target.var,
    mode: target.mode,
    positions: new Float32Array(decodeF32(target.pos)),
    lowPositions: target.low_pos ? new Float32Array(decodeF32(target.low_pos)) : null,
  }));
  mesh.userData.texKey = data.tex_key || null;
  // The draw's own resolved default (core/mesh_builder.py's per-draw
  // tex_key) -- what an unselected mesh falls back to once no toggle-driven
  // texture_variants condition matches (see visibility.js's
  // applyTextureVariant). Immutable; setMeshTexture only ever touches texKey.
  mesh.userData.defaultTexKey = data.tex_key || null;
  mesh.userData.normalMapKey = data.normal_map_key || null;
  mesh.userData.normalDataKey = data.normal_data_key || null;
  mesh.userData.lightMapKey = data.light_map_key || null;
  mesh.userData.materialMapKey = data.material_map_key || null;
  mesh.userData.materialKind = data.material_kind || 'unknown';
  mesh.userData.materialKindReliable = data.material_kind_reliable === true;
  mesh.userData.materialKindReason = data.material_kind_reason || '';
  mesh.userData.materialKindOverride = data.material_kind_override || null;
  mesh.userData.component = data.component || null;
  mesh.userData.materialProfileId = data.material_profile_id || 'none';
  mesh.userData.materialProfile = materialProfile;
  mesh.userData.aoMapKey = data.ao_map_key || null;
  mesh.userData.normalMapEnabled = data.normal_map_enabled !== false;
  mesh.userData.normalMapYSign = Number.isFinite(data.normal_map_y_sign)
    ? data.normal_map_y_sign : -1;
  mesh.userData.defaultNormalMapKey = mesh.userData.normalMapKey;
  mesh.userData.defaultNormalDataKey = mesh.userData.normalDataKey;
  mesh.userData.defaultLightMapKey = mesh.userData.lightMapKey;
  mesh.userData.defaultMaterialMapKey = mesh.userData.materialMapKey;
  mesh.userData.fallbackColor = fallback;
  refreshMeshTexture(mesh);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}
