// Keep large payloads below native message limits without exposing destination paths.
import { base64Buffer } from './base64-buffer.js';

export async function saveGlbBuffer(api, path, buffer, isCurrent = () => true) {
  if (buffer.byteLength <= 2 * 1024 * 1024) {
    const encoded = await base64Buffer(buffer);
    if (!isCurrent()) return { cancelled: true };
    return api.export_model_glb(path, encoded);
  }
  if (!isCurrent()) return { cancelled: true };
  const opened = await api.export_model_glb(path, { action: 'begin', size: buffer.byteLength });
  if (opened.error || opened.cancelled) return opened;
  let token = opened.token;
  try {
    const chunkSize = 1024 * 1024;
    for (let offset = 0; offset < buffer.byteLength; offset += chunkSize) {
      if (!isCurrent()) return { cancelled: true };
      const chunk = await base64Buffer(buffer.slice(offset, offset + chunkSize));
      if (!isCurrent()) return { cancelled: true };
      const result = await api.export_model_glb(path, { action: 'append', token, chunk });
      if (result.error) return result;
    }
    if (!isCurrent()) return { cancelled: true };
    const result = await api.export_model_glb(path, { action: 'finish', token });
    if (!result.error) token = null;
    return result;
  } finally {
    if (token) await api.export_model_glb(path, { action: 'cancel', token });
  }
}
