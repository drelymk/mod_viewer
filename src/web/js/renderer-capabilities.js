// Capabilities negotiated by the WebGPU preflight.  Texture transport uses
// this instead of assuming that every WebGPU adapter exposes BC sampling.
let bcTextureCompression = false;

export function setBCTextureCompression(value) {
  bcTextureCompression = value === true;
}

export function supportsBCTextureCompression() {
  return bcTextureCompression;
}
