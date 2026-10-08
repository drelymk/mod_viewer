// Lazy DDS previews use the existing GPU device and never encode an image file.

import * as THREE from 'three/webgpu';
import { loadDDSTexture } from './dds-loader.js';

const PREVIEW_SIZE = 256;
let previewQueue = Promise.resolve();

async function renderPreview(canvas, url, isCurrent) {
  if (!isCurrent()) return;
  const { renderer, rendererReady } = await import('../scene/scene.js');
  if (!(await rendererReady)) throw new Error('DDS preview renderer unavailable');
  if (!isCurrent()) return;

  let texture;
  let geometry;
  let material;
  let target;
  try {
    await new Promise((resolve, reject) => {
      texture = loadDDSTexture(url, resolve, reject);
    });
    if (!isCurrent()) return;
    texture.colorSpace = THREE.SRGBColorSpace;
    const { width, height } = texture.image;
    if (width > 8192 || height > 8192) throw new Error('DDS preview exceeds the dimension limit');

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 2);
    camera.position.z = 1;
    const largest = Math.max(width, height);
    geometry = new THREE.PlaneGeometry((2 * width) / largest, (2 * height) / largest);
    material = new THREE.MeshBasicMaterial({
      map: texture,
      transparent: true,
      blending: THREE.NoBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    scene.add(new THREE.Mesh(geometry, material));
    target = new THREE.RenderTarget(PREVIEW_SIZE, PREVIEW_SIZE, {
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
    const pixels = await renderer.readRenderTargetPixelsAsync(target, 0, 0, PREVIEW_SIZE, PREVIEW_SIZE);
    if (!isCurrent()) return;
    canvas.width = PREVIEW_SIZE;
    canvas.height = PREVIEW_SIZE;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('DDS preview canvas unavailable');
    context.putImageData(new ImageData(new Uint8ClampedArray(pixels), PREVIEW_SIZE, PREVIEW_SIZE), 0, 0);
    canvas.dataset.previewReady = 'true';
  } finally {
    target?.dispose();
    material?.dispose();
    geometry?.dispose();
    texture?.dispose();
  }
}

export function renderDDSPreview(canvas, url, isCurrent = () => canvas.isConnected) {
  // One preview at a time bounds uploads and gives panel rebuilds a chance to
  // cancel queued work before any source bytes are fetched.
  const pending = previewQueue.then(() => renderPreview(canvas, url, isCurrent));
  previewQueue = pending.catch(() => {});
  return pending;
}
