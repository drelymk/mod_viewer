// Lazy DDS previews use the existing GPU device and never encode an image file.

import * as THREE from 'three/webgpu';
import { loadDDSTexture } from './dds-loader.js';

const PREVIEW_SIZE = 256;
const MAX_PREVIEWS = 8;
const MAX_PREVIEW_BYTES = 16 * 1024 * 1024;
const previews = new Map();
let previewQueue = Promise.resolve();

async function renderPreview(
  canvas,
  url,
  isCurrent,
  { size = PREVIEW_SIZE, preserveAspect = false, texture: sharedTexture },
) {
  if (!isCurrent()) return;
  const { renderer, rendererReady } = await import('../scene/scene.js');
  if (!(await rendererReady)) throw new Error('DDS preview renderer unavailable');
  if (!isCurrent()) return;

  let texture = sharedTexture;
  let geometry;
  let material;
  let target;
  try {
    if (!texture) {
      await new Promise((resolve, reject) => {
        texture = loadDDSTexture(url, resolve, reject);
      });
    }
    if (!isCurrent()) return;
    texture.colorSpace = THREE.SRGBColorSpace;
    const { width, height } = texture.image;
    if (width > 8192 || height > 8192) throw new Error('DDS preview exceeds the dimension limit');

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 2);
    camera.position.z = 1;
    const largest = Math.max(width, height);
    const outputWidth = preserveAspect ? Math.max(1, Math.round((size * width) / largest)) : size;
    const outputHeight = preserveAspect ? Math.max(1, Math.round((size * height) / largest)) : size;
    geometry = new THREE.PlaneGeometry(
      preserveAspect ? 2 : (2 * width) / largest,
      preserveAspect ? 2 : (2 * height) / largest,
    );
    material = new THREE.MeshBasicMaterial({
      map: texture,
      transparent: true,
      blending: THREE.NoBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    scene.add(new THREE.Mesh(geometry, material));
    target = new THREE.RenderTarget(outputWidth, outputHeight, {
      depthBuffer: false,
      stencilBuffer: false,
      colorSpace: THREE.SRGBColorSpace,
    });

    // Restore viewport state before yielding to the asynchronous GPU readback.
    const state = THREE.RendererUtils.resetRendererState(renderer);
    try {
      renderer.setClearColor(0, 0);
      renderer.setScissorTest(false);
      renderer.setRenderTarget(target);
      renderer.render(scene, camera);
    } finally {
      THREE.RendererUtils.restoreRendererState(renderer, state);
    }
    const pixels = await renderer.readRenderTargetPixelsAsync(target, 0, 0, outputWidth, outputHeight);
    if (!isCurrent()) return;
    canvas.width = outputWidth;
    canvas.height = outputHeight;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('DDS preview canvas unavailable');
    // The pinned WebGPU readback retains 256-byte row alignment (except the last row).
    const rowBytes = outputWidth * 4;
    const stride = Math.ceil(rowBytes / 256) * 256;
    const image = new ImageData(outputWidth, outputHeight);
    for (let y = 0; y < outputHeight; y += 1) {
      image.data.set(pixels.subarray(y * stride, y * stride + rowBytes), y * rowBytes);
    }
    context.putImageData(image, 0, 0);
    canvas.dataset.previewReady = 'true';
  } finally {
    target?.dispose();
    material?.dispose();
    geometry?.dispose();
    if (!sharedTexture) texture?.dispose();
  }
}

/** Clear only previews whose published source has changed, or the entire model lifetime. */
export function clearDDSPreviews(url = null) {
  for (const [key, entry] of previews) {
    if (url !== null && entry.url !== url) continue;
    entry.invalid = true;
    previews.delete(key);
  }
}

/** Bounded, shared Canvas results. Pending consumers share one upload/readback. */
export async function getDDSPreview(url, isCurrent, options = {}) {
  if (!isCurrent()) return null;
  const size = Math.max(1, Math.min(2048, Math.round(options.size || PREVIEW_SIZE)));
  const preserveAspect = options.preserveAspect === true;
  let key = JSON.stringify([url, size, preserveAspect]);
  let entry = previews.get(key);
  if (!entry) {
    for (const [candidateKey, candidate] of previews) {
      if (
        candidate.url === url &&
        candidate.preserveAspect === preserveAspect &&
        candidate.size >= size &&
        (!entry || candidate.size < entry.size)
      ) {
        key = candidateKey;
        entry = candidate;
      }
    }
  }
  if (!entry) {
    entry = { url, size, preserveAspect, consumers: new Set(), invalid: false, complete: false };
    const current = () => !entry.invalid && [...entry.consumers].some((consumer) => consumer());
    const canvas = document.createElement('canvas');
    entry.pending = previewQueue.then(async () => {
      await renderPreview(canvas, url, current, { ...options, size, preserveAspect });
      return canvas.dataset.previewReady === 'true' && !entry.invalid ? canvas : null;
    });
    previewQueue = entry.pending.catch(() => {});
    previews.set(key, entry);
    entry.pending.then(
      (result) => {
        entry.complete = true;
        entry.bytes = result ? result.width * result.height * 4 : 0;
        if (!result && previews.get(key) === entry) previews.delete(key);
        let bytes = [...previews.values()].reduce((sum, cached) => sum + (cached.bytes || 0), 0);
        for (const [oldKey, old] of previews) {
          if (previews.size <= MAX_PREVIEWS && bytes <= MAX_PREVIEW_BYTES) break;
          if (old.complete) {
            previews.delete(oldKey);
            bytes -= old.bytes || 0;
          }
        }
      },
      () => {
        if (previews.get(key) === entry) previews.delete(key);
      },
    );
  } else {
    previews.delete(key);
    previews.set(key, entry);
  }
  entry.consumers.add(isCurrent);
  try {
    const result = await entry.pending;
    return isCurrent() && !entry.invalid ? result : null;
  } finally {
    entry.consumers.delete(isCurrent);
  }
}

export function renderDDSPreview(canvas, url, isCurrent = () => canvas.isConnected, options = {}) {
  return getDDSPreview(url, isCurrent, options).then((preview) => {
    if (!preview || !isCurrent()) return;
    canvas.width = preview.width;
    canvas.height = preview.height;
    canvas.getContext('2d').drawImage(preview, 0, 0);
    canvas.dataset.previewReady = 'true';
  });
}
