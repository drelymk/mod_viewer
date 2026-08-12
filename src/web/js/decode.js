// Base64 -> typed array decoding for the mesh buffers the Python side sends.
// Buffers arrive base64-encoded because the JS bridge carries JSON, which has
// no binary type.

let geometryBlob = null;

export function setGeometryBlob(buffer) {
  geometryBlob = buffer;
}

function decodeBytes(value) {
  if (value && typeof value === 'object') {
    if (!geometryBlob) throw new Error('Geometry blob has not loaded.');
    return new Uint8Array(geometryBlob, value.offset, value.length);
  }
  const b64 = value;
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

export function decodeF32(b64) {
  const bytes = decodeBytes(b64);
  return new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}

export function decodeU32(b64) {
  const bytes = decodeBytes(b64);
  return new Uint32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}
