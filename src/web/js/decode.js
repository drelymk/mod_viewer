// Base64 -> typed array decoding for the mesh buffers the Python side sends.
// Buffers arrive base64-encoded because the JS bridge carries JSON, which has
// no binary type.

function decodeBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

export function decodeF32(b64) {
  return new Float32Array(decodeBytes(b64).buffer);
}

export function decodeU32(b64) {
  return new Uint32Array(decodeBytes(b64).buffer);
}
