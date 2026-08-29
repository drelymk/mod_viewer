// Building Three.js meshes from payload entries, plus the texture registry.

import * as THREE from 'three';
import { decodeF32, decodeU32 } from '../textures/decode.js';
import {
  captureGameMaterialViewerState, createGameMaterial, disposeGameMaterial,
  getGameMaterialSources, restoreGameMaterialViewerState,
  updateGameMaterialTextures, usesPackedNormal,
} from './material-profile.js';
import { getMeshView } from './mesh-view-bindings.js';
import { loadDDSTexture } from '../textures/dds-loader.js';
import { requestRender } from '../scene/render-scheduler.js';
import { initializeMeshRenderModes } from '../scene/render-modes.js';
import { supportsBCTextureCompression } from '../scene/renderer-capabilities.js';
import { splitTextureKey } from '../textures/texture-key.js';
import { CHARACTER_AO_LAYER } from '../scene/viewer-layers.js';

// Textures arrive as data URIs or same-origin localhost URLs keyed by name;
// loaders are cached so several meshes sharing a texture share one GPU upload.
let registry = {};
const loaders = {};
const readyTextures = new Set();
const failedTextures = new Set();
const nativeDDSFallbacks = new Set();
const textureUsers = new Map();
// all: every authored map; diffuse-normal: color + the material's actual
// normal source; diffuse: color only; none: flat colour.
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
 * web/js/panels/mesh-panel.js). View-only/session-scoped: never reaches the ini. */
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

export function removeTextures(keys) {
  let removed = 0;
  for (const key of keys || []) {
    if (!Object.hasOwn(registry, key)) continue;
    delete registry[key];
    disposeTexture(loaders[key]);
    delete loaders[key];
    readyTextures.delete(key);
    failedTextures.delete(key);
    nativeDDSFallbacks.delete(key);
    textureUsers.delete(key);
    removed += 1;
  }
  return removed;
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
export function refreshMeshTexture(mesh, { render = true } = {}) {
  const showDiffuse = textureMode !== 'none';
  const showMaterialMaps = textureMode === 'all';
  const showNormal = showMaterialMaps || textureMode === 'diffuse-normal';
  const gameMaterialSources = getGameMaterialSources(mesh.material);
  const usePackedSource = role =>
    showMaterialMaps && gameMaterialSources.has(role);
  const normalSource = mesh.material?.userData?.gameMaterial?.normalSource
    || 'normal_map';
  const map = showDiffuse ? getTexture(mesh, mesh.userData.texKey) : null;
  const normalMap = showNormal && normalSource === 'normal_map'
    && mesh.userData.normalMapEnabled !== false
    ? getTexture(mesh, mesh.userData.normalMapKey) : null;
  const normalData = (usePackedSource('normal_data')
      || (showNormal && normalSource === 'normal_data'
        && gameMaterialSources.has('normal_data')))
    ? getTexture(mesh, mesh.userData.normalDataKey) : null;
  const lightMap = usePackedSource('light_map')
    ? getTexture(mesh, mesh.userData.lightMapKey) : null;
  const materialMap = usePackedSource('material_map')
    ? getTexture(mesh, mesh.userData.materialMapKey) : null;
  const emissionMap = usePackedSource('emission_map')
    ? getTexture(mesh, mesh.userData.emissionMapKey) : null;
  const changed = updateGameMaterialTextures(mesh, {
    diffuse: map,
    normal_map: normalMap,
    normal_data: normalData, light_map: lightMap, material_map: materialMap,
    emission_map: emissionMap,
    normal_map_y_sign: mesh.userData.normalMapYSign ?? -1,
  });
  if (!changed) return false;
  getMeshView(mesh)?.onTextureChanged?.();
  if (render && mesh.visible) requestRender();
  return true;
}

export function setTextureMode(mode) {
  textureMode = mode;
}

/** Keep authored normals at the neutral shape and derive normals only for a
 * shape whose positions have actually changed. */
export function updateGeometryNormals(mesh, deformed) {
  const normal = mesh.geometry.attributes.normal;
  const baseNormals = mesh.userData.baseNormals;
  if (!deformed && baseNormals && normal
      && normal.array.length === baseNormals.length) {
    normal.array.set(baseNormals);
    normal.needsUpdate = true;
    return;
  }
  mesh.geometry.computeVertexNormals();
}

/** Update the complete resolved texture state with one material refresh. */
export function setMeshTextureState(mesh, state, { render = true } = {}) {
  mesh.userData.texKey = state.diffuse || null;
  mesh.userData.normalMapKey = usesPackedNormal(mesh.material)
    ? null : (state.normal_map || null);
  if (Object.hasOwn(state, 'normal_data')) {
    mesh.userData.normalDataKey = state.normal_data || null;
  }
  mesh.userData.lightMapKey = state.light_map || null;
  mesh.userData.materialMapKey = state.material_map || null;
  mesh.userData.emissionMapKey = state.emission_map || null;
  return refreshMeshTexture(mesh, { render });
}

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

  if (data.normal) {
    geo.setAttribute('normal', new THREE.BufferAttribute(
      decodeF32(data.normal), 3));
  } else {
    geo.computeVertexNormals();
  }

  const fallback = fallbackColor(name);
  const mat = createGameMaterial(materialProfile, fallback,
    { hasUv: !!data.uv });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.layers.enable(CHARACTER_AO_LAYER);
  mesh.userData.basePositions = new Float32Array(geo.attributes.position.array);
  mesh.userData.baseNormals = data.normal
    ? new Float32Array(geo.attributes.normal.array) : null;
  mesh.userData.hasAuthoredNormals = !!data.normal;
  mesh.userData.shapeTargets = (data.shape_targets || []).map(target => ({
    var: target.var,
    mode: target.mode,
    positions: new Float32Array(decodeF32(target.pos)),
    lowPositions: target.low_pos ? new Float32Array(decodeF32(target.low_pos)) : null,
  }));
  mesh.userData.texKey = data.tex_key || null;
  // The draw's own resolved default (core/geometry/mesh_builder.py's per-draw
  // tex_key) -- what an unselected mesh falls back to once no toggle-driven
  // texture_variants condition matches (see visibility.js's
  // applyTextureVariant). Immutable; setMeshTextureState updates the stable
  // binding state without rebuilding the material.
  mesh.userData.defaultTexKey = data.tex_key || null;
  const authoredNormalMapKey = data.normal_map_key || null;
  mesh.userData.normalMapKey = usesPackedNormal(mat)
    ? null : authoredNormalMapKey;
  mesh.userData.normalDataKey = data.normal_data_key || null;
  mesh.userData.lightMapKey = data.light_map_key || null;
  mesh.userData.materialMapKey = data.material_map_key || null;
  mesh.userData.emissionMapKey = data.emission_map_key || null;
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
  // Keep the authored normal-map default even while a packed profile hides
  // it. A later profile swap may need to restore that role in place.
  mesh.userData.defaultNormalMapKey = authoredNormalMapKey;
  mesh.userData.defaultNormalDataKey = mesh.userData.normalDataKey;
  mesh.userData.defaultLightMapKey = mesh.userData.lightMapKey;
  mesh.userData.defaultMaterialMapKey = mesh.userData.materialMapKey;
  mesh.userData.defaultEmissionMapKey = mesh.userData.emissionMapKey;
  mesh.userData.fallbackColor = fallback;
  refreshMeshTexture(mesh);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}
