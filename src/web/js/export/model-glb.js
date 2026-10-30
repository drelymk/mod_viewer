// Detached model snapshots: no renderer helpers or live scene objects reach glTF.
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

const ROLE_FIELDS = {
  diffuse: 'texKey',
  normal_map: 'normalMapKey',
  normal_data: 'normalDataKey',
  light_map: 'lightMapKey',
  material_map: 'materialMapKey',
  emission_map: 'emissionMapKey',
};

function copyDrawGeometry(mesh) {
  const original = mesh.geometry;
  const position = original?.attributes.position;
  if (!position) return null;
  const available = original.index?.count ?? position.count;
  const start = original.drawRange.start;
  const count = Math.min(original.drawRange.count, available - start);
  if (count <= 0) return null;
  if (start % 3 || count % 3) throw new Error('Invalid triangle draw range.');
  const vertices = new Map();
  const indices = new Uint32Array(count);
  for (let offset = 0; offset < count; offset += 1) {
    const index = original.index ? original.index.getX(start + offset) : start + offset;
    if (!Number.isInteger(index) || index < 0 || index >= position.count) throw new Error('Invalid vertex index.');
    if (!vertices.has(index)) vertices.set(index, vertices.size);
    indices[offset] = vertices.get(index);
  }
  const geometry = new THREE.BufferGeometry();
  for (const name of ['position', 'normal', 'uv', 'uv1', 'color', 'tangent']) {
    const attribute = original.attributes[name];
    if (!attribute) continue;
    const values = new Float32Array(vertices.size * attribute.itemSize);
    for (const [oldIndex, newIndex] of vertices) {
      for (let channel = 0; channel < attribute.itemSize; channel += 1) {
        const value = attribute.getComponent(oldIndex, channel);
        if (!Number.isFinite(value)) throw new Error('Invalid vertex attribute.');
        values[newIndex * attribute.itemSize + channel] = value;
      }
    }
    geometry.setAttribute(name, new THREE.BufferAttribute(values, attribute.itemSize));
  }
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  return geometry;
}

/** Capture all geometry and binding decisions synchronously before texture I/O. */
export function snapshotModel(meshes, textureSource = () => null) {
  const scene = new THREE.Scene();
  scene.name = 'Mod Viewer';
  scene.userData.modViewer = { version: 1, snapshot: 'visible geometry with current pose and shapes' };
  const materials = new Map();
  const textures = new Set();
  const dispose = () => {
    for (const mesh of scene.children) mesh.geometry.dispose();
    for (const material of materials.keys()) material.dispose();
    for (const texture of textures) texture.dispose();
  };
  try {
    for (const source of meshes) {
      source.updateWorldMatrix(true, true);
      if (!source.visible) continue;
      const data = source.userData;
      const parts = data.looseParts?.length ? data.looseParts : [source];
      for (const part of parts) {
        if (!part.visible) continue;
        const geometry = copyDrawGeometry(part);
        if (!geometry) continue;
        const material = new THREE.MeshStandardMaterial({
          color: data.fallbackColor ?? 0xcccccc,
          side: THREE.DoubleSide,
          metalness: 0,
          roughness: 1,
        });
        const roles = Object.fromEntries(
          Object.entries(ROLE_FIELDS)
            .map(([role, field]) => [role, { key: data[field], url: textureSource(data[field]) }])
            .filter(([, value]) => value.key),
        );
        const metadata = JSON.parse(
          JSON.stringify({
            identity: data.identity,
            component: data.component,
            sources: data.sources,
            materialProfile: data.materialProfile,
            colorAdjustment: data.colorAdjustment,
          }),
        );
        material.name = data.displayName || source.name;
        material.userData.modViewer = metadata;
        materials.set(material, {
          roles,
          hasUv: !!geometry.attributes.uv,
          normalEnabled: data.normalMapEnabled !== false,
          normalYSign: data.normalMapYSign ?? -1,
          profile: metadata.materialProfile,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.name = part === source ? data.displayName || source.name : part.userData.loosePartLabel || part.name;
        mesh.matrixAutoUpdate = false;
        mesh.matrix.copy(part.matrixWorld);
        mesh.userData.modViewer = metadata;
        scene.add(mesh);
      }
    }
    return { scene, materials, textures, dispose };
  } catch (error) {
    dispose();
    throw error;
  }
}

/** Pack original resources alongside standard material approximations. */
export async function encodeModelGlb(snapshot, { loadImage, loadBytes } = {}) {
  const warnings = [];
  const images = new Map();
  const originals = new Map();
  const maps = new Map();
  const imageFor = async (key, url) => {
    if (!images.has(key)) {
      images.set(
        key,
        loadImage(url, key).catch(() => {
          warnings.push(key);
          return null;
        }),
      );
    }
    return images.get(key);
  };
  const textureFor = (key, image, colorSpace) => {
    if (!maps.has(key)) {
      const texture = new THREE.Texture(image);
      texture.name = key;
      texture.colorSpace = colorSpace;
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      snapshot.textures.add(texture);
      maps.set(key, texture);
    }
    return maps.get(key);
  };
  for (const [material, { roles, hasUv, normalEnabled, normalYSign, profile }] of snapshot.materials) {
    for (const [role, { key, url }] of Object.entries(roles)) {
      if (!url) {
        if (!warnings.includes(key)) warnings.push(key);
        continue;
      }
      if (!originals.has(key)) {
        try {
          originals.set(key, await loadBytes(url));
        } catch {
          if (!warnings.includes(key)) warnings.push(key);
        }
      }
      if (!hasUv || !['diffuse', 'normal_map', 'emission_map'].includes(role)) continue;
      if (role === 'normal_map' && !normalEnabled) continue;
      if (role === 'emission_map' && profile?.emission_source !== 'emission_map_rgb') continue;
      const image = await imageFor(key, url);
      if (!image) continue;
      const texture = textureFor(key, image, role === 'diffuse' ? THREE.SRGBColorSpace : THREE.NoColorSpace);
      if (role === 'diffuse') {
        material.map = texture;
        material.color.set(0xffffff);
      } else if (role === 'normal_map') {
        material.normalMap = texture;
        material.normalScale.set(1, normalYSign);
      } else {
        material.emissiveMap = texture;
        material.emissive.setScalar(Math.min(1, Math.max(0, profile.emission_strength ?? 1)));
      }
    }
  }
  const exporter = new GLTFExporter();
  exporter.register((writer) => {
    const buffers = new Map();
    return {
      async writeMaterialAsync(material, definition) {
        const roles = snapshot.materials.get(material)?.roles || {};
        definition.extras.modViewer.textures = {};
        for (const [role, { key }] of Object.entries(roles)) {
          const bytes = originals.get(key);
          if (!bytes) continue;
          if (!buffers.has(key)) buffers.set(key, await writer.processBufferViewImage(new Blob([bytes])));
          definition.extras.modViewer.textures[role] = {
            key,
            bufferView: buffers.get(key),
            byteLength: bytes.byteLength,
          };
        }
      },
    };
  });
  const buffer = await exporter.parseAsync(snapshot.scene, { binary: true });
  return { buffer, meshCount: snapshot.scene.children.length, warnings: [...new Set(warnings)] };
}
