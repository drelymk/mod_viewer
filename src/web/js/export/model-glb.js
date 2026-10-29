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
  for (const name of ['position', 'normal', 'uv']) {
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
            textures: Object.fromEntries(Object.entries(roles).map(([role, { key }]) => [role, key])),
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

// Preserve the pinned exporter's JSON and buffer order if its final Blob read fails.
function packSerializedGlb(writer) {
  const json = new TextEncoder().encode(JSON.stringify(writer.json));
  const jsonLength = Math.ceil(json.byteLength / 4) * 4;
  const binaryLength = writer.buffers.reduce((sum, part) => sum + part.byteLength, 0);
  const paddedBinaryLength = Math.ceil(binaryLength / 4) * 4;
  const bytes = new Uint8Array(28 + jsonLength + paddedBinaryLength);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, bytes.byteLength, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  bytes.fill(0x20, 20, 20 + jsonLength);
  bytes.set(json, 20);
  view.setUint32(20 + jsonLength, paddedBinaryLength, true);
  view.setUint32(24 + jsonLength, 0x004e4942, true);
  let offset = 28 + jsonLength;
  for (const part of writer.buffers) {
    bytes.set(new Uint8Array(part), offset);
    offset += part.byteLength;
  }
  return bytes.buffer;
}

/** Embed only Blender-ready images; role identities remain lightweight metadata. */
export async function encodeModelGlb(snapshot, { loadImage, releaseImage } = {}) {
  const warnings = [];
  const images = new Map();
  const maps = new Map();
  const imageFor = (url) => {
    if (!images.has(url)) {
      images.set(
        url,
        Promise.resolve()
          .then(() => loadImage(url))
          .catch(() => null),
      );
    }
    return images.get(url);
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
      if (!hasUv || !['diffuse', 'normal_map', 'emission_map'].includes(role)) continue;
      if (role === 'normal_map' && !normalEnabled) continue;
      if (role === 'emission_map' && profile?.emission_source !== 'emission_map_rgb') continue;
      const image = url ? await imageFor(url) : null;
      if (!image) {
        warnings.push(key);
        continue;
      }
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
  let writer;
  exporter.register((context) => {
    writer = context;
    return {
      afterParse() {
        // The writer has copied each source image before packing its final GLB.
        if (!releaseImage) return;
        const sources = new Set([...snapshot.textures].map((texture) => texture.image));
        for (const image of sources) releaseImage(image);
      },
    };
  });
  const encoded = await exporter.parseAsync(snapshot.scene, { binary: true });
  const buffer = encoded instanceof ArrayBuffer ? encoded : packSerializedGlb(writer);
  return { buffer, meshCount: snapshot.scene.children.length, warnings: [...new Set(warnings)] };
}
