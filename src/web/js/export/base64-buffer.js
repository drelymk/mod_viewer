// Some WebView2 versions cannot read large memory-backed Blobs with FileReader.
async function encodeChunks(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunks = [];
  // Complete three-byte groups keep padding out of intermediate chunks.
  const chunkSize = 49152;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    chunks.push(btoa(String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))));
    if (offset && offset % (chunkSize * 40) === 0) {
      await new Promise((resolve) => {
        const channel = new MessageChannel();
        channel.port1.onmessage = () => {
          channel.port1.close();
          channel.port2.close();
          resolve();
        };
        channel.port2.postMessage(null);
      });
    }
  }
  return chunks.join('');
}

export function base64Buffer(buffer) {
  // Avoid retaining a large Blob plus its data-URL string during export.
  if (buffer.byteLength > 64 * 1024 * 1024) return encodeChunks(buffer);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = () => encodeChunks(buffer).then(resolve, reject);
    reader.readAsDataURL(new Blob([buffer]));
  });
}
