// Read-only Canvas inspection of the existing geometry and published diffuse.

import { getTexturePreviewSource, isDDSUri } from '../mesh/mesh-factory.js';
import { getLoosePartSource } from '../mesh/loose-parts.js';
import { authoredTriangleOrdinal } from '../scene/model-picking.js';
import { renderDDSPreview } from '../textures/dds-preview.js';
import { t } from '../i18n/index.js';

const SIZE = 768;

/** Visit drawn triangles without retaining a second copy of the UV/index buffers. */
export function forEachUVTriangle(mesh, visit) {
  const geometry = mesh.geometry;
  const uv = geometry.getAttribute('uv');
  if (!uv) return;
  const index = geometry.index;
  const count = index ? index.count : uv.count;
  const start = Math.max(0, geometry.drawRange.start);
  const end = Math.min(count, start + geometry.drawRange.count);
  for (let offset = Math.ceil(start / 3) * 3; offset + 2 < end; offset += 3) {
    const triangle = authoredTriangleOrdinal(mesh, offset / 3);
    if (triangle === null) continue;
    const a = index ? index.getX(offset) : offset;
    const b = index ? index.getX(offset + 1) : offset + 1;
    const c = index ? index.getX(offset + 2) : offset + 2;
    if (a < 0 || b < 0 || c < 0 || a >= uv.count || b >= uv.count || c >= uv.count) continue;
    if (
      !Number.isFinite(uv.getX(a)) ||
      !Number.isFinite(uv.getY(a)) ||
      !Number.isFinite(uv.getX(b)) ||
      !Number.isFinite(uv.getY(b)) ||
      !Number.isFinite(uv.getX(c)) ||
      !Number.isFinite(uv.getY(c))
    )
      continue;
    visit(uv, a, b, c, triangle);
  }
}

export function createUVView(mesh, { expanded = false, onExpanded, getFaceSelection }) {
  const source = getLoosePartSource(mesh) || mesh;
  const section = document.createElement('details');
  section.className = 'inspector-section inspector-uv-section';
  section.open = expanded;
  const summary = document.createElement('summary');
  summary.className = 'inspector-section-title';
  summary.textContent = t('inspector.uv');
  const stage = document.createElement('div');
  stage.className = 'inspector-uv-stage';
  stage.setAttribute('role', 'img');
  stage.setAttribute('aria-label', t('inspector.uv'));
  const layers = ['texture', 'outlines', 'highlight'].map((name) => {
    const canvas = document.createElement('canvas');
    canvas.className = `inspector-uv-${name}`;
    stage.appendChild(canvas);
    return canvas;
  });
  const [textureLayer, outlines, highlight] = layers;
  const status = document.createElement('div');
  status.className = 'inspector-muted';
  status.setAttribute('role', 'status');
  const warning = document.createElement('div');
  warning.className = 'inspector-muted inspector-uv-warning';
  section.append(summary, stage, status, warning);
  let disposed = false;
  let generation = 0;
  let textureSource;
  let image = null;
  let bounds = null;
  let transform = null;

  function releaseImage() {
    if (!image) return;
    if (image instanceof HTMLImageElement) {
      image.onload = image.onerror = null;
      image.src = '';
    } else {
      image.width = image.height = 1;
    }
    image = null;
  }

  function release() {
    generation += 1;
    textureLayer.dataset.previewReady = 'false';
    textureSource = undefined;
    bounds = transform = null;
    releaseImage();
    layers.forEach((canvas) => {
      canvas.width = canvas.height = 1;
    });
  }

  function drawTriangles(canvas, selected = null) {
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!transform || (selected && !selected.size)) return;
    const { x, y, width, height } = transform;
    context.beginPath();
    forEachUVTriangle(mesh, (uv, a, b, c, triangle) => {
      if (selected && !selected.has(triangle)) return;
      context.moveTo(x + uv.getX(a) * width, y + (1 - uv.getY(a)) * height);
      context.lineTo(x + uv.getX(b) * width, y + (1 - uv.getY(b)) * height);
      context.lineTo(x + uv.getX(c) * width, y + (1 - uv.getY(c)) * height);
      context.closePath();
    });
    context.strokeStyle = selected ? '#ffdf5d' : '#72d8ff';
    context.lineWidth = selected ? 2.5 : 1;
    context.stroke();
  }

  function syncSelection() {
    if (disposed || !section.open || !transform) return;
    const face = getFaceSelection();
    drawTriangles(highlight, face?.source === source ? face.triangles : new Set());
  }

  function draw() {
    const aspect = image ? (image.naturalWidth || image.width) / (image.naturalHeight || image.height) : 1;
    const spanU = bounds.maxU - bounds.minU;
    const spanV = bounds.maxV - bounds.minV;
    const scale = (SIZE - 24) / Math.max(spanU * aspect, spanV);
    const width = scale * aspect;
    const height = scale;
    const outputWidth = Math.max(1, Math.ceil(spanU * width + 24));
    const outputHeight = Math.max(1, Math.ceil(spanV * height + 24));
    transform = { x: 12 - bounds.minU * width, y: 12 + (bounds.maxV - 1) * height, width, height };
    layers.forEach((canvas) => {
      canvas.width = outputWidth;
      canvas.height = outputHeight;
    });
    stage.style.aspectRatio = `${outputWidth} / ${outputHeight}`;
    const { x, y } = transform;
    const context = textureLayer.getContext('2d');
    if (image) context.drawImage(image, x, y, width, height);
    else {
      context.fillStyle = '#353b45';
      context.fillRect(x, y, width, height);
    }
    context.strokeStyle = '#939ba7';
    context.strokeRect(x, y, width, height);
    drawTriangles(outlines);
    syncSelection();
  }

  async function sync() {
    if (disposed || !section.open || !section.isConnected) return;
    const url = getTexturePreviewSource(source.userData.texKey);
    if (textureSource === url && bounds) return;
    release();
    textureSource = url;
    stage.hidden = !mesh.geometry.getAttribute('uv');
    warning.textContent = '';
    if (stage.hidden) {
      status.textContent = t('inspector.noUV');
      return;
    }
    bounds = { minU: 0, minV: 0, maxU: 1, maxV: 1 };
    forEachUVTriangle(mesh, (uv, a, b, c) => {
      for (const vertex of [a, b, c]) {
        bounds.minU = Math.min(bounds.minU, uv.getX(vertex));
        bounds.maxU = Math.max(bounds.maxU, uv.getX(vertex));
        bounds.minV = Math.min(bounds.minV, uv.getY(vertex));
        bounds.maxV = Math.max(bounds.maxV, uv.getY(vertex));
      }
    });
    if (bounds.minU < 0 || bounds.minV < 0 || bounds.maxU > 1 || bounds.maxV > 1) {
      warning.textContent = t('inspector.uvOutside');
    }
    status.textContent = url ? t('inspector.uvLoading') : t('inspector.uvNoTexture');
    draw();
    if (!url) return;
    const token = generation;
    const isCurrent = () => !disposed && section.open && section.isConnected && token === generation;
    try {
      if (isDDSUri(url)) {
        const canvas = document.createElement('canvas');
        image = canvas;
        await renderDDSPreview(canvas, url, isCurrent, { size: SIZE, preserveAspect: true });
      } else {
        const nativeImage = new Image();
        image = nativeImage;
        await new Promise((resolve, reject) => {
          nativeImage.onload = resolve;
          nativeImage.onerror = reject;
          nativeImage.src = url;
        });
      }
      if (!isCurrent()) return;
      status.textContent = '';
      draw();
      textureLayer.dataset.previewReady = 'true';
    } catch {
      if (!isCurrent()) return;
      releaseImage();
      status.textContent = t('inspector.uvPreviewFailed');
    }
  }

  section.addEventListener('toggle', () => {
    if (disposed) return;
    onExpanded(section.open);
    if (section.open) void sync();
    else release();
  });
  return {
    section,
    sync,
    syncSelection,
    dispose() {
      disposed = true;
      release();
    },
  };
}
