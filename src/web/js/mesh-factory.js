// Building Three.js meshes from payload entries, plus the texture registry.

import * as THREE from 'three';
import { decodeF32, decodeU32 } from './decode.js';

// Textures arrive as data URIs keyed by name; loaders are cached so several
// meshes sharing a texture share one GPU upload.
let registry = {};
const loaders = {};

export function setTextures(textures) {
  registry = textures || {};
  for (const key of Object.keys(loaders)) delete loaders[key];
}

function getTexture(key) {
  if (!key || !registry[key]) return null;
  if (!loaders[key]) loaders[key] = new THREE.TextureLoader().load(registry[key]);
  return loaders[key];
}

/** Swap a mesh's diffuse map to another registry entry (a texture-swap toggle). */
export function setMeshTexture(mesh, texKey) {
  const map = getTexture(texKey);
  if (map === mesh.material.map) return;
  mesh.material.map = map;
  mesh.material.needsUpdate = true;
}

/** Colour for meshes with no texture, guessed from the component name. */
function fallbackColor(name) {
  const n = name.toLowerCase();
  if (n.includes('wing'))                          return 0xc8a2c8;
  if (n.includes('hair'))                          return 0xa0d8ef;
  if (n.includes('body') || n.includes('top'))     return 0xf5cba7;
  if (n.includes('leg')  || n.includes('bottom'))  return 0xf7dc6f;
  if (n.includes('belt') || n.includes('bag'))     return 0xadd8e6;
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

  const common = { side: THREE.DoubleSide, roughness: 1.0, metalness: 0.0 };
  const mat = data.tex_key
    ? new THREE.MeshStandardMaterial({ ...common, map: getTexture(data.tex_key) })
    : new THREE.MeshStandardMaterial({ ...common, color: fallbackColor(name) });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}
