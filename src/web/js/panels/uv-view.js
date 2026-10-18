// Read-only Canvas inspection of the existing geometry and published diffuse.

import { getTexturePreviewSource, getReadyTextureForPreview, isDDSUri } from '../mesh/mesh-factory.js';
import { getLoosePartSource } from '../mesh/loose-parts.js';
import { getDDSPreview } from '../textures/dds-preview.js';
import { createIcon } from '../ui/ui-icons.js';
import { t } from '../i18n/index.js';

function triangleRange(mesh) {
  const geometry = mesh.geometry;
  const uv = geometry.getAttribute('uv');
  const index = geometry.index;
  const start = Math.max(0, geometry.drawRange.start);
  const end = Math.min(index ? index.count : uv?.count || 0, start + geometry.drawRange.count);
  return { uv, index, start, end, membership: mesh.userData.loosePartTriangles };
}

function visitTriangle(range, offset, visit) {
  const { uv, index, membership } = range;
  const id = membership ? membership[offset / 3] : offset / 3;
  if (!Number.isInteger(id) || id < 0) return;
  const a = index ? index.getX(offset) : offset;
  const b = index ? index.getX(offset + 1) : offset + 1;
  const c = index ? index.getX(offset + 2) : offset + 2;
  if (a < 0 || b < 0 || c < 0 || a >= uv.count || b >= uv.count || c >= uv.count) return;
  if (
    !Number.isFinite(uv.getX(a)) ||
    !Number.isFinite(uv.getY(a)) ||
    !Number.isFinite(uv.getX(b)) ||
    !Number.isFinite(uv.getY(b)) ||
    !Number.isFinite(uv.getX(c)) ||
    !Number.isFinite(uv.getY(c))
  )
    return;
  visit(uv, a, b, c, id, offset);
}

/** Visit drawn triangles without recalculating their draw range per triangle. */
export function forEachUVTriangle(mesh, visit) {
  const range = triangleRange(mesh);
  if (!range.uv) return;
  for (let offset = Math.ceil(range.start / 3) * 3; offset + 2 < range.end; offset += 3) {
    visitTriangle(range, offset, visit);
  }
}

function addTriangle(path, uv, a, b, c) {
  path.moveTo(uv.getX(a), 1 - uv.getY(a));
  path.lineTo(uv.getX(b), 1 - uv.getY(b));
  path.lineTo(uv.getX(c), 1 - uv.getY(c));
  path.closePath();
}

function buildDisplay(mesh) {
  const range = triangleRange(mesh);
  const path = new Path2D();
  const offsets = range.membership ? new Map() : null;
  const bounds = { minU: 0, minV: 0, maxU: 1, maxV: 1 };
  let commands = '';
  forEachUVTriangle(mesh, (uv, a, b, c, id, offset) => {
    const uA = uv.getX(a),
      vA = uv.getY(a);
    const uB = uv.getX(b),
      vB = uv.getY(b);
    const uC = uv.getX(c),
      vC = uv.getY(c);
    // Batch native path construction; keep only a bounded temporary command string.
    commands += `M${uA} ${1 - vA}L${uB} ${1 - vB}L${uC} ${1 - vC}Z`;
    if (commands.length >= 65536) {
      path.addPath(new Path2D(commands));
      commands = '';
    }
    offsets?.set(id, offset);
    bounds.minU = Math.min(bounds.minU, uA, uB, uC);
    bounds.maxU = Math.max(bounds.maxU, uA, uB, uC);
    bounds.minV = Math.min(bounds.minV, vA, vB, vC);
    bounds.maxV = Math.max(bounds.maxV, vA, vB, vC);
  });
  if (commands) path.addPath(new Path2D(commands));
  return { ...range, path, offsets, bounds, uvVersion: range.uv.version, indexVersion: range.index?.version };
}

export function createUVView(mesh, { expanded = false, onExpanded = () => {}, getFaceSelection }) {
  let source = getLoosePartSource(mesh) || mesh;
  const section = document.createElement('details');
  section.className = 'inspector-section inspector-uv-section';
  section.open = expanded;
  const summary = document.createElement('summary');
  summary.className = 'inspector-section-title';
  const label = document.createElement('span');
  const expand = document.createElement('button');
  expand.type = 'button';
  expand.className = 'icon-btn inspector-uv-expand';
  expand.disabled = !mesh.geometry.getAttribute('uv');
  expand.appendChild(createIcon('expand'));
  const chevron = createIcon('chevron-down');
  chevron.classList.add('inspector-uv-chevron');
  summary.append(chevron, label, expand);
  const inline = document.createElement('div');
  inline.className = 'inspector-uv-inline';
  const stage = document.createElement('div');
  stage.className = 'inspector-uv-stage';
  stage.setAttribute('role', 'img');
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
  inline.append(stage);
  section.append(summary, inline, status, warning);
  let disposed = false;
  let generation = 0;
  let textureSource;
  let previewSize = 0;
  let image = null;
  let pendingImage = null;
  let display = null;
  let transform = null;
  let overlay = null;
  let overlayBody = null;
  let statusKey = '';

  function syncLabels() {
    label.textContent = t('inspector.uv');
    stage.setAttribute('aria-label', label.textContent);
    expand.title = t('inspector.uvExpand');
    expand.setAttribute('aria-label', expand.title);
    expand.setAttribute('aria-pressed', String(!!overlay));
    const close = overlay?.querySelector('.uv-viewport-close');
    if (close) {
      close.title = t('inspector.uvClose');
      close.setAttribute('aria-label', close.title);
    }
    status.textContent = statusKey ? t(statusKey) : '';
    const b = display?.bounds;
    warning.textContent = b && (b.minU < 0 || b.minV < 0 || b.maxU > 1 || b.maxV > 1) ? t('inspector.uvOutside') : '';
    const zoomStatus = overlay?.querySelector('.uv-viewport-status');
    if (zoomStatus) {
      zoomStatus.textContent = [status.textContent, warning.textContent].filter(Boolean).join(' ');
      zoomStatus.hidden = !zoomStatus.textContent;
    }
  }

  function releaseImage() {
    if (pendingImage) {
      pendingImage.onload = pendingImage.onerror = null;
      pendingImage.src = '';
      pendingImage = null;
    }
    if (image instanceof HTMLImageElement) {
      image.onload = image.onerror = null;
      image.src = '';
    }
    // DDS canvases belong to the bounded shared preview cache.
    image = null;
  }

  function stroke(canvas, path, color, lineWidth) {
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, canvas.width, canvas.height);
    const scaled = new Path2D();
    const { x, y, width, height } = transform;
    scaled.addPath(path, new DOMMatrix([width, 0, 0, height, x, y]));
    context.strokeStyle = color;
    context.lineWidth = lineWidth;
    context.stroke(scaled);
  }

  function syncSelection() {
    if (disposed || !section.open || !transform || !display) return;
    const path = new Path2D();
    const face = getFaceSelection();
    if (face?.source === source) {
      for (const id of face.triangles) {
        const offset = display.offsets ? display.offsets.get(id) : id * 3;
        if (!Number.isInteger(offset) || offset < display.start || offset + 2 >= display.end) continue;
        visitTriangle(display, offset, (uv, a, b, c) => addTriangle(path, uv, a, b, c));
      }
    }
    stroke(highlight, path, '#ffdf5d', 2.5);
  }

  function drawBackground() {
    if (!transform || !section.open) return;
    const { x, y, width, height } = transform;
    const context = textureLayer.getContext('2d');
    context.clearRect(0, 0, textureLayer.width, textureLayer.height);
    if (image) context.drawImage(image, x, y, width, height);
    else {
      context.fillStyle = '#353b45';
      context.fillRect(x, y, width, height);
    }
    context.strokeStyle = '#939ba7';
    context.strokeRect(x, y, width, height);
  }

  function layout() {
    if (disposed || !section.open || !display || (!section.isConnected && !overlay?.isConnected)) return false;
    const host = overlayBody || inline;
    const availableWidth = host.clientWidth || 280;
    const availableHeight = overlayBody ? host.clientHeight : Infinity;
    const textureImage = image || getReadyTextureForPreview(source.userData.texKey)?.image;
    const aspect = textureImage
      ? (textureImage.naturalWidth || textureImage.width) / (textureImage.naturalHeight || textureImage.height)
      : 1;
    const { minU, minV, maxU, maxV } = display.bounds;
    const spanU = maxU - minU,
      spanV = maxV - minV;
    const scale = Math.max(
      Number.MIN_VALUE,
      Math.min((availableWidth - 16) / (spanU * aspect), (availableHeight - 16) / spanV),
    );
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = scale * aspect * dpr,
      height = scale * dpr;
    const outputWidth = Math.ceil(spanU * width + 16 * dpr);
    const outputHeight = Math.ceil(spanV * height + 16 * dpr);
    stage.style.width = `${outputWidth / dpr}px`;
    stage.style.height = `${outputHeight / dpr}px`;
    if (transform?.display === display && transform.width === width && transform.height === height) return false;
    transform = { display, x: 8 * dpr - minU * width, y: 8 * dpr + (maxV - 1) * height, width, height };
    layers.forEach((canvas) => {
      canvas.width = outputWidth;
      canvas.height = outputHeight;
    });
    stroke(outlines, display.path, '#72d8ff', 1);
    drawBackground();
    syncSelection();
    return true;
  }

  async function sync() {
    if (disposed) return;
    syncLabels();
    if (!section.open || (!section.isConnected && !overlay?.isConnected)) return;
    const range = triangleRange(mesh);
    stage.hidden = !range.uv;
    expand.disabled = !range.uv;
    if (!range.uv) {
      statusKey = 'inspector.noUV';
      syncLabels();
      return;
    }
    if (
      !display ||
      display.uv !== range.uv ||
      display.index !== range.index ||
      display.uvVersion !== range.uv.version ||
      display.indexVersion !== range.index?.version ||
      display.start !== range.start ||
      display.end !== range.end ||
      display.membership !== range.membership
    ) {
      display = buildDisplay(mesh);
    }
    const url = getTexturePreviewSource(source.userData.texKey);
    const changed = textureSource !== url;
    if (changed) {
      releaseImage();
      previewSize = 0;
    }
    layout();
    const dds = isDDSUri(url);
    // Match the texture's displayed pixels, with bounded sizes and resize buckets.
    const size = Math.min(overlay ? 2048 : 512, Math.ceil(Math.max(transform.width, transform.height) / 128) * 128);
    if (!changed && (!dds || previewSize >= size)) {
      syncLabels();
      return;
    }
    const token = ++generation;
    textureSource = url;
    previewSize = size;
    textureLayer.dataset.previewReady = String(!!image);
    statusKey = image ? '' : url ? 'inspector.uvLoading' : 'inspector.uvNoTexture';
    if (!layout()) drawBackground();
    syncLabels();
    if (!url) return;
    const isCurrent = () => !disposed && token === generation && (section.isConnected || overlay?.isConnected);
    try {
      let loaded;
      if (dds) {
        loaded = await getDDSPreview(url, isCurrent, {
          size,
          preserveAspect: true,
          texture: getReadyTextureForPreview(source.userData.texKey),
        });
      } else {
        const nativeImage = new Image();
        pendingImage = nativeImage;
        loaded = await new Promise((resolve, reject) => {
          nativeImage.onload = () => resolve(nativeImage);
          nativeImage.onerror = reject;
          nativeImage.src = url;
        });
      }
      if (!isCurrent()) return;
      if (!loaded) {
        textureSource = undefined;
        return;
      }
      image = loaded;
      pendingImage = null;
      statusKey = '';
      if (!layout()) drawBackground();
      textureLayer.dataset.previewReady = 'true';
      syncLabels();
    } catch {
      if (!isCurrent()) return;
      if (!image) releaseImage();
      statusKey = 'inspector.uvPreviewFailed';
      syncLabels();
    }
  }

  function positionOverlay() {
    if (!overlay) return;
    const viewport = overlay.parentElement.getBoundingClientRect();
    const left = document.querySelector('#left-dock.ui-visible')?.getBoundingClientRect();
    const right = document.querySelector('#right-dock.ui-visible')?.getBoundingClientRect();
    overlay.style.left = `${left ? Math.max(0, left.right - viewport.left + 8) : 0}px`;
    overlay.style.right = `${right ? Math.max(0, viewport.right - right.left + 8) : 0}px`;
    layout();
    void sync();
  }

  function closeOverlay() {
    if (!overlay) return;
    observer.unobserve(overlayBody);
    inline.append(stage);
    inline.hidden = false;
    overlay.remove();
    overlay = overlayBody = null;
    window.removeEventListener('keydown', onKeyDown, true);
    window.removeEventListener('resize', positionOverlay);
    window.removeEventListener('mod-viewer-right-dock-tab-changed', positionOverlay);
    layout();
    syncLabels();
    if (!disposed) expand.focus({ preventScroll: true });
  }

  function onKeyDown(event) {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  expand.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (overlay) {
      overlay.querySelector('.uv-viewport-close').focus();
      return;
    }
    section.open = true;
    overlay = document.createElement('section');
    overlay.className = 'uv-viewport';
    overlay.setAttribute('aria-label', t('inspector.uv'));
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'icon-btn uv-viewport-close';
    close.appendChild(createIcon('close'));
    close.addEventListener('click', closeOverlay);
    const zoomStatus = document.createElement('div');
    zoomStatus.className = 'inspector-muted uv-viewport-status';
    zoomStatus.setAttribute('role', 'status');
    overlayBody = document.createElement('div');
    overlayBody.className = 'uv-viewport-body';
    overlayBody.append(stage);
    overlay.append(close, zoomStatus, overlayBody);
    document.getElementById('canvas-container').append(overlay);
    inline.hidden = true;
    observer.observe(overlayBody);
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('resize', positionOverlay);
    window.addEventListener('mod-viewer-right-dock-tab-changed', positionOverlay);
    positionOverlay();
    syncLabels();
    void sync();
    close.focus();
  });
  const observer = new ResizeObserver(() => {
    if (overlay) positionOverlay();
    else void sync();
  });
  observer.observe(inline);
  for (const dock of document.querySelectorAll('#left-dock, #right-dock')) observer.observe(dock);
  summary.addEventListener('click', (event) => {
    if (overlay) event.preventDefault();
  });
  section.addEventListener('toggle', () => {
    if (disposed) return;
    if (overlay && !section.open) section.open = true;
    onExpanded(section.open);
    if (section.open) void sync();
  });
  syncLabels();
  return {
    section,
    sync,
    syncSelection,
    isZoomed: () => !!overlay,
    setMesh(nextMesh) {
      if (mesh === nextMesh) return;
      generation += 1;
      releaseImage();
      mesh = nextMesh;
      source = getLoosePartSource(mesh) || mesh;
      expand.disabled = !mesh.geometry.getAttribute('uv');
      stage.hidden = expand.disabled;
      display = transform = null;
      textureSource = undefined;
      previewSize = 0;
      statusKey = '';
      textureLayer.dataset.previewReady = 'false';
      layers.forEach((canvas) => {
        canvas.width = canvas.height = 1;
      });
    },
    dispose() {
      disposed = true;
      generation += 1;
      closeOverlay();
      observer.disconnect();
      releaseImage();
      display = transform = null;
      layers.forEach((canvas) => {
        canvas.width = canvas.height = 1;
      });
    },
  };
}
