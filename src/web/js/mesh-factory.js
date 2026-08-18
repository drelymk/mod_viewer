// Building Three.js meshes from payload entries, plus the texture registry.

import * as THREE from 'three';
import { decodeF32, decodeU32 } from './decode.js';

// Textures arrive as data URIs or same-origin localhost URLs keyed by name;
// loaders are cached so several meshes sharing a texture share one GPU upload.
let registry = {};
const loaders = {};
// all: diffuse + INI material maps; diffuse: diffuse only; none: flat colour.
let textureMode = 'all';

export function setTextures(textures) {
  registry = textures || {};
  for (const key of Object.keys(loaders)) delete loaders[key];
}

/** Merge one texture into the shared registry without touching the rest --
 * used when the user adds a texture via the per-component picker (see
 * web/js/mesh-panel.js). View-only/session-scoped: never reaches the ini. */
export function addTexture(key, uri) {
  registry[key] = uri;
  for (const cacheKey of Object.keys(loaders)) {
    if (cacheKey.endsWith(`|${key}`)) delete loaders[cacheKey];
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
  return !!(resolved && registry[resolved]);
}

function getTexture(key, role = 'diffuse') {
  const resolved = registryKey(key, role);
  if (!resolved || !registry[resolved]) return null;
  const cacheKey = `${role}|${resolved}`;
  if (!loaders[cacheKey]) {
    const texture = new THREE.TextureLoader().load(registry[resolved]);
    texture.colorSpace = role === 'diffuse'
      ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    texture.needsUpdate = true;
    loaders[cacheKey] = texture;
  }
  return loaders[cacheKey];
}

/** Bind whatever diffuse map the mesh currently wants, honouring the global
 * textures on/off switch. Falls back to the name-guessed flat colour, which is
 * also what an untextured mesh has always shown. */
export function refreshMeshTexture(mesh) {
  const showDiffuse = textureMode !== 'none';
  const showMaterialMaps = textureMode === 'all';
  const map = showDiffuse ? getTexture(mesh.userData.texKey, 'diffuse') : null;
  const normalMap = showMaterialMaps
    ? getTexture(mesh.userData.normalMapKey, 'normal_map') : null;
  const lightMap = showMaterialMaps
    ? getTexture(mesh.userData.lightMapKey, 'light_map') : null;
  const materialMap = showMaterialMaps
    ? getTexture(mesh.userData.materialMapKey, 'material_map') : null;
  if (map === mesh.material.map
      && normalMap === mesh.material.normalMap
      && lightMap === mesh.material.aoMap
      && materialMap === mesh.userData.boundMaterialMap) return;
  mesh.material.map = map;
  mesh.material.normalMap = normalMap;
  // 3DMigoto normal maps use DirectX's Y convention; Three.js expects the
  // opposite green-axis direction.
  mesh.material.normalScale.set(1, normalMap ? -1 : 1);
  // Game LightMaps are packed masks, not RGB irradiance. Treating them as a
  // Three.js lightMap produces their characteristic red cast; scalar AO uses
  // the red mask without introducing a hue.
  mesh.material.aoMap = lightMap;
  mesh.material.aoMapIntensity = 0.5;
  // Keep MaterialMap loaded and variant-aware, but don't guess its
  // game-specific packed channels into standard PBR slots. The previous guess
  // made surfaces metallic and glossy even when the authored material wasn't.
  mesh.userData.boundMaterialMap = materialMap;
  mesh.material.roughnessMap = null;
  mesh.material.metalnessMap = null;
  mesh.material.roughness = 1;
  mesh.material.metalness = 0;
  mesh.material.color.setHex(map ? 0xffffff : mesh.userData.fallbackColor);
  mesh.material.needsUpdate = true;
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
  mesh.userData.lightMapKey = maps.light_map || null;
  mesh.userData.materialMapKey = maps.material_map || null;
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

export function buildMesh(name, data) {
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
  const mat = new THREE.MeshStandardMaterial({
    side: THREE.DoubleSide, roughness: 1.0, metalness: 0.0, color: fallback });

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
  mesh.userData.lightMapKey = data.light_map_key || null;
  mesh.userData.materialMapKey = data.material_map_key || null;
  mesh.userData.defaultNormalMapKey = mesh.userData.normalMapKey;
  mesh.userData.defaultLightMapKey = mesh.userData.lightMapKey;
  mesh.userData.defaultMaterialMapKey = mesh.userData.materialMapKey;
  mesh.userData.fallbackColor = fallback;
  refreshMeshTexture(mesh);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}
