// Encode one bounded transfer chunk without Blob reads or intermediate data URLs.
export function base64Buffer(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunks = [];
  // Complete three-byte groups keep padding out of intermediate chunks.
  const chunkSize = 49152;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    chunks.push(btoa(String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))));
  }
  return chunks.join('');
}
