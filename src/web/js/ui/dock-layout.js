// Resizable and collapsible docks, focus mode, bottom clearance and viewer keyboard shortcuts.

import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { isRigJointPickingActive, isRigTransformInteractionActive } from '../scene/rig-overlay-state.js';
import { openLeftDock, toggleLeftDock } from '../panels/left-dock.js';
import { openRightDock, toggleRightDock } from '../panels/right-dock.js';

const DOCK_WIDTHS = Object.freeze({
  left: {
    min: 270,
    max: 690,
    fallback: 338,
    property: '--left-dock-width',
    preference: 'leftDockWidth',
    dock: 'left-dock',
    resizer: 'left-dock-resizer',
  },
  right: {
    min: 260,
    max: 640,
    fallback: 316,
    property: '--right-dock-width',
    preference: 'rightDockWidth',
    dock: 'right-dock',
    resizer: 'right-dock-resizer',
  },
});
const KEY_STEP = 16;
const CLEARANCE_OVERLAYS = ['tool-panel', 'animation-playback'];
const OBSERVED_LAYOUT_IDS = ['tool-panel', 'animation-playback', 'left-dock', 'right-dock', 'left-panel-container'];

let focusMode = false;
let layoutFrame = 0;
let dragging = null;
let widthChangeHandler = () => {};

const $ = (id) => document.getElementById(id);

function specFor(side) {
  return Object.hasOwn(DOCK_WIDTHS, side) ? DOCK_WIDTHS[side] : null;
}

function viewportMax(side) {
  return Math.max(specFor(side).min, Math.floor(window.innerWidth / 2 - (side === 'left' ? 36 : 24)));
}

function effectiveMax(side) {
  return Math.min(specFor(side).max, viewportMax(side));
}

function clampWidth(side, value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return specFor(side).fallback;
  return Math.min(effectiveMax(side), Math.max(specFor(side).min, Math.round(number)));
}

function currentWidth(side) {
  const spec = specFor(side);
  const width = Math.round($(spec.dock)?.getBoundingClientRect().width || 0);
  return width || spec.fallback;
}

function syncResizerAria(side) {
  const resizer = $(specFor(side).resizer);
  if (!resizer) return;
  resizer.setAttribute('aria-valuemin', String(specFor(side).min));
  resizer.setAttribute('aria-valuemax', String(effectiveMax(side)));
  resizer.setAttribute('aria-valuenow', String(currentWidth(side)));
}

function scheduleLayout() {
  if (layoutFrame) return;
  layoutFrame = requestAnimationFrame(() => {
    layoutFrame = 0;
    updateClearance();
  });
}

function updateClearance() {
  const overlays = CLEARANCE_OVERLAYS.map((id) => $(id)?.getBoundingClientRect()).filter(
    (rect) => rect && rect.width > 0 && rect.height > 0,
  );
  const rootStyle = getComputedStyle(document.documentElement);
  const footerHeight = parseFloat(rootStyle.getPropertyValue('--footer-height')) || 0;
  const dockGap = parseFloat(rootStyle.getPropertyValue('--dock-gap')) || 0;
  const baseBottom = window.innerHeight - footerHeight - dockGap + 4;
  for (const side of ['left', 'right']) {
    const dock = $(side === 'left' ? 'left-panel-container' : 'right-dock')?.getBoundingClientRect();
    let clearance = 0;
    if (dock && dock.width > 0) {
      for (const overlay of overlays) {
        if (overlay.left < dock.right && overlay.right > dock.left) {
          clearance = Math.max(clearance, baseBottom - (overlay.top - 8));
        }
      }
    }
    const value = `${Math.ceil(Math.max(0, clearance))}px`;
    const property = `--${side}-dock-clearance`;
    if (document.documentElement.style.getPropertyValue(property) !== value) {
      document.documentElement.style.setProperty(property, value);
    }
  }
}

export function setDockWidth(side, value) {
  const spec = specFor(side);
  if (!spec) return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  const width = Math.round(Math.min(spec.max, Math.max(spec.min, number)));
  document.documentElement.style.setProperty(spec.property, `${width}px`);
  syncResizerAria(side);
  scheduleLayout();
  return width;
}

function bindResizer(side) {
  const spec = specFor(side);
  const resizer = $(spec.resizer);
  if (!resizer) return;

  resizer.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    resizer.setPointerCapture(event.pointerId);
    resizer.classList.add('dragging');
    document.body.classList.add('dock-resizing');
    dragging = { side, width: currentWidth(side) };
  });

  resizer.addEventListener('pointermove', (event) => {
    if (!dragging || dragging.side !== side) return;
    const rect = $(spec.dock).getBoundingClientRect();
    const raw = side === 'left' ? event.clientX - rect.left : rect.right - event.clientX;
    const width = clampWidth(side, raw);
    dragging.width = width;
    setDockWidth(side, width);
    widthChangeHandler(spec.preference, width, { persist: false });
  });

  const endDrag = () => {
    if (!dragging || dragging.side !== side) return;
    const { width } = dragging;
    dragging = null;
    resizer.classList.remove('dragging');
    document.body.classList.remove('dock-resizing');
    widthChangeHandler(spec.preference, width, { persist: true });
  };
  resizer.addEventListener('pointerup', endDrag);
  resizer.addEventListener('pointercancel', endDrag);
  resizer.addEventListener('lostpointercapture', endDrag);

  resizer.addEventListener('dblclick', () => {
    setDockWidth(side, spec.fallback);
    widthChangeHandler(spec.preference, spec.fallback, { persist: true });
  });

  resizer.addEventListener('keydown', (event) => {
    let target;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const grows = (event.key === 'ArrowRight') === (side === 'left');
      target = currentWidth(side) + (grows ? KEY_STEP : -KEY_STEP);
    } else if (event.key === 'Home') {
      target = specFor(side).min;
    } else if (event.key === 'End') {
      target = effectiveMax(side);
    } else {
      return;
    }
    event.preventDefault();
    const width = clampWidth(side, target);
    setDockWidth(side, width);
    widthChangeHandler(spec.preference, width, { persist: true });
  });
}

function syncFocusButton() {
  const button = $('focus-mode-btn');
  if (!button) return;
  const label = t('render.focusMode', { state: t(focusMode ? 'common.on' : 'common.off') });
  button.setAttribute('aria-pressed', String(focusMode));
  button.classList.toggle('active', focusMode);
  button.title = label;
  button.setAttribute('aria-label', label);
}

export function isFocusMode() {
  return focusMode;
}

export function setFocusMode(enabled) {
  focusMode = !!enabled;
  document.body.classList.toggle('focus-mode', focusMode);
  const exit = $('focus-exit-btn');
  if (exit) exit.hidden = !focusMode;
  syncFocusButton();
  scheduleLayout();
}

function shouldIgnoreShortcut(event) {
  if (event.defaultPrevented || event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey)
    return true;
  const target = event.target;
  if (target?.closest?.('input, textarea, select, .ace_editor, [role="textbox"]')) return true;
  if (target?.isContentEditable) return true;
  if (document.querySelector('.modal-backdrop.show, .dialog-backdrop.show')) return true;
  return isRigTransformInteractionActive() || isRigJointPickingActive();
}

function onShortcut(event) {
  if (shouldIgnoreShortcut(event)) return;
  switch (event.code) {
    case 'BracketLeft':
      event.preventDefault();
      if (focusMode) {
        setFocusMode(false);
        openLeftDock();
      } else {
        toggleLeftDock();
      }
      break;
    case 'BracketRight':
      event.preventDefault();
      if (focusMode) {
        setFocusMode(false);
        openRightDock();
      } else {
        toggleRightDock();
      }
      break;
    case 'Backslash':
      event.preventDefault();
      setFocusMode(!focusMode);
      break;
    default:
      break;
  }
}

export function initDockLayout({ onWidthChange } = {}) {
  widthChangeHandler = typeof onWidthChange === 'function' ? onWidthChange : () => {};
  bindResizer('left');
  bindResizer('right');
  $('focus-mode-btn')?.addEventListener('click', () => setFocusMode(!focusMode));
  $('focus-exit-btn')?.addEventListener('click', () => setFocusMode(false));
  window.addEventListener(LANGUAGE_CHANGED, syncFocusButton);
  document.addEventListener('keydown', onShortcut);
  window.addEventListener('resize', () => {
    syncResizerAria('left');
    syncResizerAria('right');
    scheduleLayout();
  });
  window.addEventListener('mod-viewer-right-dock-tab-changed', scheduleLayout);
  const observer = new ResizeObserver(() => scheduleLayout());
  for (const id of OBSERVED_LAYOUT_IDS) {
    const element = $(id);
    if (element) observer.observe(element);
  }
  syncResizerAria('left');
  syncResizerAria('right');
  syncFocusButton();
  scheduleLayout();
}
