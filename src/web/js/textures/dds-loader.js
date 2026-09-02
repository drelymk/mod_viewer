// Minimal, strict DDS transport for the formats the viewer can publish.
// Parsing is deliberately independent from Three's loaders so a malformed or
// unsupported payload can fall back to the existing PNG path without changing
// the material or registry semantics.

import * as THREE from 'three';

const DDS_MAGIC = 0x20534444; // little-endian "DDS "
const HEADER_SIZE = 124;
const PIXEL_FORMAT_SIZE = 32;
const DDPF_RGB = 0x40;
const DDPF_FOURCC = 0x4;
const CAPS2_CUBEMAP = 0x200;
const CAPS2_VOLUME = 0x200000;
const RESOURCE_DIMENSION_TEXTURE2D = 3;
const RESOURCE_MISC_TEXTURECUBE = 0x4;
const MAX_DIMENSION = 65536;

const DXGI_FORMATS = new Map([
  [71, 'bc1_unorm'], [72, 'bc1_srgb'],
  [74, 'bc2_unorm'], [75, 'bc2_srgb'],
  [77, 'bc3_unorm'], [78, 'bc3_srgb'],
  [80, 'bc4_unorm'], [81, 'bc4_snorm'],
  [83, 'bc5_unorm'], [84, 'bc5_snorm'],
  [95, 'bc6h_ufloat'], [96, 'bc6h_float'],
  [98, 'bc7_unorm'], [99, 'bc7_srgb'],
]);

const LEGACY_FORMATS = new Map([
  ['DXT1', 'bc1_unorm'], ['DXT3', 'bc2_unorm'], ['DXT5', 'bc3_unorm'],
  ['ATI1', 'bc4_unorm'], ['BC4U', 'bc4_unorm'], ['BC4S', 'bc4_snorm'],
  ['ATI2', 'bc5_unorm'], ['BC5U', 'bc5_unorm'], ['BC5S', 'bc5_snorm'],
]);

const COMPRESSED_FORMATS = new Set(DXGI_FORMATS.values());

const THREE_FORMATS = {
  bc1_unorm: THREE.RGBA_S3TC_DXT1_Format,
  bc1_srgb: THREE.RGBA_S3TC_DXT1_Format,
  bc2_unorm: THREE.RGBA_S3TC_DXT3_Format,
  bc2_srgb: THREE.RGBA_S3TC_DXT3_Format,
  bc3_unorm: THREE.RGBA_S3TC_DXT5_Format,
  bc3_srgb: THREE.RGBA_S3TC_DXT5_Format,
  bc4_unorm: THREE.RED_RGTC1_Format,
  bc4_snorm: THREE.SIGNED_RED_RGTC1_Format,
  bc5_unorm: THREE.RED_GREEN_RGTC2_Format,
  bc5_snorm: THREE.SIGNED_RED_GREEN_RGTC2_Format,
  bc6h_ufloat: THREE.RGB_BPTC_UNSIGNED_Format,
  bc6h_float: THREE.RGB_BPTC_SIGNED_Format,
  bc7_unorm: THREE.RGBA_BPTC_Format,
  bc7_srgb: THREE.RGBA_BPTC_Format,
};

function fail(message) {
  throw new Error(`Invalid DDS: ${message}`);
}

function fourCC(view, offset) {
  return String.fromCharCode(
    view.getUint8(offset), view.getUint8(offset + 1),
    view.getUint8(offset + 2), view.getUint8(offset + 3));
}

function mipCount(rawCount, width, height) {
  const count = Math.max(1, rawCount);
  if (count > Math.max(width, height).toString(2).length) {
    fail('mip count exceeds the 2D chain');
  }
  return count;
}

function readFormat(view) {
  if (view.getUint32(4, true) !== HEADER_SIZE
      || view.getUint32(76, true) !== PIXEL_FORMAT_SIZE) {
    fail('invalid header size');
  }
  const width = view.getUint32(16, true);
  const height = view.getUint32(12, true);
  if (!width || !height || width > MAX_DIMENSION || height > MAX_DIMENSION) {
    fail('invalid dimensions');
  }
  if (view.getUint32(24, true) > 1
      || (view.getUint32(112, true) & (CAPS2_CUBEMAP | CAPS2_VOLUME))) {
    fail('only 2D non-cube textures are supported');
  }
  const mipCountValue = mipCount(view.getUint32(28, true), width, height);
  const pixelFlags = view.getUint32(80, true);
  const code = fourCC(view, 84);

  if (code === 'DX10') {
    if (!(pixelFlags & DDPF_FOURCC) || view.byteLength < 148) {
      fail('truncated DX10 header');
    }
    const dxgi = view.getUint32(128, true);
    const format = DXGI_FORMATS.get(dxgi);
    if (!format || view.getUint32(132, true) !== RESOURCE_DIMENSION_TEXTURE2D
        || view.getUint32(140, true) !== 1
        || (view.getUint32(136, true) & RESOURCE_MISC_TEXTURECUBE)) {
      fail('unsupported DX10 resource');
    }
    return { width, height, mipCount: mipCountValue, format, dataOffset: 148 };
  }

  const legacyFormat = LEGACY_FORMATS.get(code);
  if (legacyFormat) {
    if (!(pixelFlags & DDPF_FOURCC)) fail('invalid compressed pixel format');
    return { width, height, mipCount: mipCountValue,
      format: legacyFormat, dataOffset: 128 };
  }

  if (!(pixelFlags & DDPF_RGB) || view.getUint32(88, true) !== 32) {
    fail('unsupported pixel format');
  }
  const masks = [92, 96, 100, 104].map(offset => view.getUint32(offset, true));
  const rgba = [0x000000ff, 0x0000ff00, 0x00ff0000, 0xff000000];
  const bgra = [0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000];
  const format = masks.every((value, index) => value === rgba[index])
    ? 'rgba8' : masks.every((value, index) => value === bgra[index])
      ? 'bgra8' : null;
  if (!format) fail('unsupported 32-bit channel layout');
  return { width, height, mipCount: mipCountValue, format, dataOffset: 128 };
}

function levelByteLength(format, width, height) {
  if (!COMPRESSED_FORMATS.has(format)) return width * height * 4;
  const blockBytes = format.startsWith('bc1') || format.startsWith('bc4') ? 8 : 16;
  return Math.ceil(width / 4) * Math.ceil(height / 4) * blockBytes;
}

function decodeUncompressed(bytes, offset, length, width, height, format) {
  const output = bytes.slice(offset, offset + length);
  if (format === 'bgra8') {
    for (let index = 0; index < output.length; index += 4) {
      const blue = output[index];
      output[index] = output[index + 2];
      output[index + 2] = blue;
    }
  }
  return { data: output, width, height };
}

export function parseDDS(input) {
  const bytes = input instanceof Uint8Array
    ? input : input instanceof ArrayBuffer ? new Uint8Array(input) : null;
  if (!bytes || bytes.byteLength < 128) fail('truncated file');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== DDS_MAGIC) fail('invalid magic');
  const header = readFormat(view);
  const mipmaps = [];
  let offset = header.dataOffset;
  let width = header.width;
  let height = header.height;
  for (let level = 0; level < header.mipCount; level += 1) {
    const length = levelByteLength(header.format, width, height);
    if (!Number.isSafeInteger(length) || offset + length > bytes.byteLength) {
      fail('truncated mip payload');
    }
    const mip = COMPRESSED_FORMATS.has(header.format)
      ? { data: bytes.subarray(offset, offset + length), width, height }
      : decodeUncompressed(bytes, offset, length, width, height, header.format);
    mipmaps.push(mip);
    offset += length;
    width = Math.max(1, Math.floor(width / 2));
    height = Math.max(1, Math.floor(height / 2));
  }
  return {
    width: header.width,
    height: header.height,
    mipCount: header.mipCount,
    formatId: header.format,
    compressed: COMPRESSED_FORMATS.has(header.format),
    requiresBC: COMPRESSED_FORMATS.has(header.format),
    format: THREE_FORMATS[header.format] || THREE.RGBAFormat,
    mipmaps,
  };
}

function setDDSOrientation(texture, compressed) {
  texture.flipY = compressed ? false : true;
  if (compressed) {
    texture.repeat.y = -1;
    texture.offset.y = 1;
  } else {
    texture.repeat.set(1, 1);
    texture.offset.set(0, 0);
  }
  texture.updateMatrix();
}

function applyParsedTexture(texture, parsed) {
  texture.mipmaps = parsed.mipmaps;
  texture.image = parsed.compressed
    ? { width: parsed.width, height: parsed.height }
    : parsed.mipmaps[0];
  texture.format = parsed.format;
  texture.generateMipmaps = false;
  // DDS payload rows use the opposite convention from TextureLoader's
  // browser image upload. Compressed uploads cannot use Three's flipY upload
  // path, so express the same vertical inversion in the shared texture
  // transform instead of branching on material role or game.
  setDDSOrientation(texture, parsed.compressed);
  texture.minFilter = parsed.mipCount === 1
    ? THREE.LinearFilter : THREE.LinearMipmapLinearFilter;
  texture.isCompressedTexture = parsed.compressed;
  texture.isDataTexture = !parsed.compressed;
  texture.needsUpdate = true;
}

function fetchDDSIntoTexture(texture, url, onLoad, onError, shouldApply) {
  return fetch(url, {cache: 'no-store'}).then(response => {
    if (!response.ok) throw new Error(`DDS request failed (${response.status})`);
    return response.arrayBuffer();
  }).then(bytes => {
    const parsed = parseDDS(bytes);
    if (!shouldApply || shouldApply()) applyParsedTexture(texture, parsed);
    onLoad?.(texture);
    return texture;
  }).catch(error => {
    onError?.(error);
    throw error;
  });
}

export function loadDDSTexture(url, onLoad, onError) {
  // Return one stable object immediately.  The registry can bind this object
  // before the network request completes and a failed DDS can be evicted once.
  const texture = new THREE.CompressedTexture(
    [], 1, 1, THREE.RGBAFormat, THREE.UnsignedByteType);
  // The placeholder is bound before fetch resolves, so its matrix must carry
  // the eventual compressed-texture orientation when the material graph is
  // first compiled.
  setDDSOrientation(texture, true);
  fetchDDSIntoTexture(texture, url, onLoad, onError).catch(() => {});
  return texture;
}

/** Fetch a fresh DDS into an existing texture object. */
export function reloadDDSTexture(
    texture, url, onLoad, onError, shouldApply) {
  return fetchDDSIntoTexture(
    texture, url, onLoad, onError, shouldApply);
}
