// Building Three.js meshes from payload entries, plus the texture registry.

import * as THREE from 'three';
import { decodeF32, decodeU32 } from './decode.js';
import {
  createGameMaterial, getGameMaterialSources, updateGameMaterialTextures,
  usesPackedNormal,
} from './material-profile.js';
import { getMeshView } from './mesh-view-bindings.js';
import { loadDDSTexture } from './dds-loader.js';
import { requestRender } from './render-scheduler.js';
import { supportsBCTextureCompression } from './renderer-capabilities.js';
import { splitTextureKey } from './texture-key.js';

// Textures arrive as data URIs or same-origin localhost URLs keyed by name;
// loaders are cached so several meshes sharing a texture share one GPU upload.
let registry = {};
const loaders = {};
const readyTextures = new Set();
const failedTextures = new Set();
const nativeDDSFallbacks = new Set();
const textureUsers = new Map();
// all: diffuse + INI material maps; diffuse: diffuse only; none: flat colour.
let textureMode = 'all';

function disposeTexture(texture) {
  texture?.dispose?.();
}

export function setTextures(textures) {
  registry = {};
  for (const [key, uri] of Object.entries(textures || {})) {
    if (splitTextureKey(key)) registry[key] = uri;
  }
  for (const key of Object.keys(loaders)) {
    disposeTexture(loaders[key]);
    delete loaders[key];
  }
  failedTextures.clear();
  readyTextures.clear();
  nativeDDSFallbacks.clear();
  textureUsers.clear();
}

/** Merge one texture into the shared registry without touching the rest --
 * used when the user adds a texture via the per-component picker (see
 * web/js/mesh-panel.js). View-only/session-scoped: never reaches the ini. */
export function addTexture(key, uri) {
  if (!splitTextureKey(key)) return false;
  registry[key] = uri;
  disposeTexture(loaders[key]);
  delete loaders[key];
  readyTextures.delete(key);
  failedTextures.delete(key);
  nativeDDSFallbacks.delete(key);
  textureUsers.delete(key);
  return true;
}

export function hasTexture(key) {
  return !!(splitTextureKey(key) && registry[key]
    && !failedTextures.has(key));
}

function trackTextureUser(mesh, key) {
  let users = textureUsers.get(key);
  if (!users) {
    users = new Set();
    textureUsers.set(key, users);
  }
  users.add(mesh);
}

function primePendingTexture(mesh, role, texture) {
  updateGameMaterialTextures(mesh, {[role]: texture}, {
    pending: {[role]: true},
  });
}

function handleTextureReady(key, texture, uri) {
  // A manual replacement or a newer model may have evicted this request
  // before the browser reported success. Do not revive the stale texture or
  // mark the replacement as ready.
  if (registry[key] !== uri || loaders[key] !== texture) {
    disposeTexture(texture);
    return;
  }
  readyTextures.add(key);
  texture.needsUpdate = true;
  for (const mesh of textureUsers.get(key) || []) refreshMeshTexture(mesh);
}

function handleTextureError(key, texture, uri) {
  // A manual replacement or a newer model may have evicted this request
  // before the browser reported its failure. Do not mark the replacement as
  // unavailable in that case.
  if (registry[key] !== uri || loaders[key] !== texture) {
    disposeTexture(texture);
    return;
  }
  readyTextures.delete(key);
  failedTextures.add(key);
  if (loaders[key] === texture) delete loaders[key];
  disposeTexture(texture);
  for (const mesh of textureUsers.get(key) || []) refreshMeshTexture(mesh);
}

function isDDSUri(uri) {
  return typeof uri === 'string' && /\.dds(?:[?#]|$)/i.test(uri);
}

function pngFallbackUri(uri) {
  return uri.replace(/\.dds(?=([?#]|$))/i, '.png');
}

function handleNativeDDSFailure(key, texture, uri) {
  // A stale native request must not evict a replacement texture.  The next
  // refresh uses the same registry key and the existing PNG publication.
  if (registry[key] !== uri || loaders[key] !== texture) {
    disposeTexture(texture);
    return;
  }
  if (nativeDDSFallbacks.has(key)) return;
  nativeDDSFallbacks.add(key);
  readyTextures.delete(key);
  if (loaders[key] === texture) delete loaders[key];
  disposeTexture(texture);
  for (const mesh of textureUsers.get(key) || []) refreshMeshTexture(mesh);
}

function getTexture(mesh, key) {
  const parsed = splitTextureKey(key);
  if (!parsed || !registry[key]) return null;
  trackTextureUser(mesh, key);
  if (failedTextures.has(key)) return null;
  if (!loaders[key]) {
    const uri = registry[key];
    const nativeDDS = isDDSUri(uri)
      && supportsBCTextureCompression()
      && !nativeDDSFallbacks.has(key);
    const requestUri = nativeDDS ? uri
      : isDDSUri(uri) ? pngFallbackUri(uri) : uri;
    let texture;
    let failedBeforeAssignment = false;
    let readyBeforeAssignment = false;
    const onError = () => {
      if (!texture) {
        failedBeforeAssignment = true;
        return;
      }
      handleTextureError(key, texture, uri);
    };
    const onReady = () => {
      if (!texture) {
        readyBeforeAssignment = true;
        return;
      }
      handleTextureReady(key, texture, uri);
    };
    if (nativeDDS) {
      texture = loadDDSTexture(
        requestUri, onReady,
        () => handleNativeDDSFailure(key, texture, uri));
    } else {
      texture = new THREE.TextureLoader().load(
        requestUri, onReady, undefined, onError);
    }
    texture.colorSpace = parsed.role === 'diffuse'
      ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    loaders[key] = texture;
    if (failedBeforeAssignment) {
      handleTextureError(key, texture, uri);
    }
    if (readyBeforeAssignment) {
      handleTextureReady(key, texture, uri);
    }
  }
  if (!readyTextures.has(key) && loaders[key]) {
    const texture = loaders[key];
    const uri = registry[key];
    queueMicrotask(() => {
      if (registry[key] === uri && loaders[key] === texture
          && !readyTextures.has(key) && !failedTextures.has(key)) {
        primePendingTexture(mesh, parsed.role, texture);
      }
    });
  }
  return readyTextures.has(key) ? loaders[key] : null;
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
  const normalSource = mesh.material?.userData?.gameMaterial?.normalSource
    || 'normal_map';
  const map = showDiffuse ? getTexture(mesh, mesh.userData.texKey) : null;
  const normalMap = showMaterialMaps && normalSource === 'normal_map'
    && mesh.userData.normalMapEnabled !== false
    ? getTexture(mesh, mesh.userData.normalMapKey) : null;
  const normalData = usePackedSource('normal_data')
    ? getTexture(mesh, mesh.userData.normalDataKey) : null;
  const lightMap = usePackedSource('light_map')
    ? getTexture(mesh, mesh.userData.lightMapKey) : null;
  const materialMap = usePackedSource('material_map')
    ? getTexture(mesh, mesh.userData.materialMapKey) : null;
  const changed = updateGameMaterialTextures(mesh, {
    diffuse: map,
    normal_map: normalMap,
    normal_data: normalData, light_map: lightMap, material_map: materialMap,
    normal_map_y_sign: mesh.userData.normalMapYSign ?? -1,
  });
  if (!changed) return;
  getMeshView(mesh)?.onTextureChanged?.();
  if (mesh.visible) requestRender();
}

export function setTextureMode(mode) {
  textureMode = mode;
}

/** Update the complete resolved texture state with one material refresh. */
export function setMeshTextureState(mesh, state) {
  mesh.userData.texKey = state.diffuse || null;
  mesh.userData.normalMapKey = usesPackedNormal(mesh.material)
    ? null : (state.normal_map || null);
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
  // applyTextureVariant). Immutable; setMeshTextureState updates the stable
  // binding state without rebuilding the material.
  mesh.userData.defaultTexKey = data.tex_key || null;
  mesh.userData.normalMapKey = usesPackedNormal(mat)
    ? null : (data.normal_map_key || null);
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
