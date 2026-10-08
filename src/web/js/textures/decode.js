// Decode packed mesh buffers from the shared binary localhost blob.

let geometryBlob = null;

export function setGeometryBlob(buffer) {
  geometryBlob = buffer;
}

function decodeBytes(value, alignment) {
  if (!geometryBlob) throw new Error('Geometry blob has not loaded.');
  if (
    !value ||
    !Number.isSafeInteger(value.offset) ||
    !Number.isSafeInteger(value.length) ||
    value.offset < 0 ||
    value.length < 0 ||
    value.offset % alignment !== 0 ||
    value.length % alignment !== 0 ||
    value.offset > geometryBlob.byteLength - value.length
  ) {
    throw new Error('Invalid packed geometry reference.');
  }
  return new Uint8Array(geometryBlob, value.offset, value.length);
}

export function decodeF32(reference) {
  const bytes = decodeBytes(reference, 4);
  return new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}

export function decodeU32(reference) {
  const bytes = decodeBytes(reference, 4);
  return new Uint32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}

export function decodeI32(reference) {
  const bytes = decodeBytes(reference, 4);
  return new Int32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}
