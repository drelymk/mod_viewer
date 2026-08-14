// Building Three.js meshes from payload entries, plus the texture registry.

import * as THREE from 'three';
import { decodeF32, decodeU32 } from './decode.js';

// Textures arrive as data URIs keyed by name; loaders are cached so several
// meshes sharing a texture share one GPU upload.
let registry = {};
const loaders = {};
let texturesEnabled = true;

export function setTextures(textures) {
  registry = textures || {};
  for (const key of Object.keys(loaders)) delete loaders[key];
}

/** Merge one texture into the shared registry without touching the rest --
 * used when the user adds a texture via the per-component picker (see
 * web/js/mesh-panel.js). View-only/session-scoped: never reaches the ini. */
export function addTexture(key, uri) {
  registry[key] = uri;
  delete loaders[key];
}

export function hasTexture(key) {
  return !!(key && registry[key]);
}

function getTexture(key) {
  if (!key || !registry[key]) return null;
  if (!loaders[key]) loaders[key] = new THREE.TextureLoader().load(registry[key]);
  return loaders[key];
}

/** Bind whatever diffuse map the mesh currently wants, honouring the global
 * textures on/off switch. Falls back to the name-guessed flat colour, which is
 * also what an untextured mesh has always shown. */
export function refreshMeshTexture(mesh) {
  const map = texturesEnabled ? getTexture(mesh.userData.texKey) : null;
  if (map === mesh.material.map) return;
  mesh.material.map = map;
  mesh.material.color.setHex(map ? 0xffffff : mesh.userData.fallbackColor);
  mesh.material.needsUpdate = true;
}

export function setTexturesEnabled(on) {
  texturesEnabled = on;
}

/** Swap a mesh's diffuse map to another registry entry (a texture-swap toggle). */
export function setMeshTexture(mesh, texKey) {
  mesh.userData.texKey = texKey;
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
  if (data.uv) geo.setAttribute('uv', new THREE.BufferAttribute(decodeF32(data.uv), 2));
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
  mesh.userData.fallbackColor = fallback;
  refreshMeshTexture(mesh);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}
