// Resizable and collapsible docks, focus mode, bottom clearance and viewer keyboard shortcuts.

import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { isRigJointPickingActive, isRigTransformInteractionActive } from '../scene/rig-overlay-state.js';
import { toggleLeftDock } from '../panels/left-dock.js';
import { toggleRightDock } from '../panels/right-dock.js';

const DOCK_WIDTHS = Object.freeze({
  left: { min: 270, max: 690, fallback: 338, preference: 'leftDockWidth', dock: 'left-dock' },
  right: { min: 260, max: 640, fallback: 316, preference: 'rightDockWidth', dock: 'right-dock' },
});
const KEY_STEP = 16;
const CLEARANCE_OVERLAYS = ['tool-panel', 'animation-playback'];

let focusMode = false;
let layoutFrame = 0;
let widthChangeHandler = () => {};

const $ = (id) => document.getElementById(id);

/** Mirrors the CSS max-width cap so drags and aria values match the rendered width. */
function maxWidth(side) {
  const spec = DOCK_WIDTHS[side];
  return Math.min(spec.max, Math.max(spec.min, Math.floor(window.innerWidth / 2 - (side === 'left' ? 36 : 24))));
}

function clampWidth(side, value) {
  return Math.min(maxWidth(side), Math.max(DOCK_WIDTHS[side].min, Math.round(value)));
}

function currentWidth(side) {
  return Math.round($(DOCK_WIDTHS[side].dock).getBoundingClientRect().width) || DOCK_WIDTHS[side].fallback;
}

function syncResizerAria(side) {
  const resizer = $(`${side}-dock-resizer`);
  resizer.setAttribute('aria-valuemin', String(DOCK_WIDTHS[side].min));
  resizer.setAttribute('aria-valuemax', String(maxWidth(side)));
  resizer.setAttribute('aria-valuenow', String(currentWidth(side)));
}

function scheduleLayout() {
  if (layoutFrame) return;
  layoutFrame = requestAnimationFrame(() => {
    layoutFrame = 0;
    updateClearance();
  });
}

/** Stop each dock above the bottom overlays it horizontally overlaps. */
function updateClearance() {
  const overlays = CLEARANCE_OVERLAYS.map((id) => $(id).getBoundingClientRect()).filter(
    (rect) => rect.width > 0 && rect.height > 0,
  );
  const rootStyle = getComputedStyle(document.documentElement);
  const footerHeight = parseFloat(rootStyle.getPropertyValue('--footer-height')) || 0;
  const dockGap = parseFloat(rootStyle.getPropertyValue('--dock-gap')) || 0;
  const baseBottom = window.innerHeight - footerHeight - dockGap + 4;
  for (const side of ['left', 'right']) {
    const dock = $(side === 'left' ? 'left-panel-container' : 'right-dock').getBoundingClientRect();
    let clearance = 0;
    if (dock.width > 0) {
      for (const overlay of overlays) {
        if (overlay.left < dock.right && overlay.right > dock.left) {
          clearance = Math.max(clearance, baseBottom - (overlay.top - 8));
        }
      }
    }
    document.documentElement.style.setProperty(`--${side}-dock-clearance`, `${Math.ceil(clearance)}px`);
  }
}

/** Apply a saved or dragged width; the CSS max-width still caps it to the window. */
export function setDockWidth(side, value) {
  const spec = DOCK_WIDTHS[side];
  const width = Math.round(Math.min(spec.max, Math.max(spec.min, value)));
  document.documentElement.style.setProperty(`--${side}-dock-width`, `${width}px`);
  syncResizerAria(side);
  scheduleLayout();
  return width;
}

function bindResizer(side) {
  const spec = DOCK_WIDTHS[side];
  const resizer = $(`${side}-dock-resizer`);
  const commit = (width) => {
    setDockWidth(side, width);
    widthChangeHandler(spec.preference, width);
  };
  let dragWidth = null;

  resizer.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    resizer.setPointerCapture(event.pointerId);
    resizer.classList.add('dragging');
    document.body.classList.add('dock-resizing');
    dragWidth = currentWidth(side);
  });
  resizer.addEventListener('pointermove', (event) => {
    if (dragWidth === null) return;
    const rect = $(spec.dock).getBoundingClientRect();
    dragWidth = clampWidth(side, side === 'left' ? event.clientX - rect.left : rect.right - event.clientX);
    setDockWidth(side, dragWidth);
  });
  // Fires for pointerup and pointercancel while captured; persist once per drag.
  resizer.addEventListener('lostpointercapture', () => {
    if (dragWidth === null) return;
    resizer.classList.remove('dragging');
    document.body.classList.remove('dock-resizing');
    commit(dragWidth);
    dragWidth = null;
  });
  resizer.addEventListener('dblclick', () => commit(spec.fallback));
  resizer.addEventListener('keydown', (event) => {
    let target;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const grows = (event.key === 'ArrowRight') === (side === 'left');
      target = currentWidth(side) + (grows ? KEY_STEP : -KEY_STEP);
    } else if (event.key === 'Home') {
      target = spec.min;
    } else if (event.key === 'End') {
      target = maxWidth(side);
    } else {
      return;
    }
    event.preventDefault();
    commit(clampWidth(side, target));
  });
}

function syncFocusButton() {
  const button = $('focus-mode-btn');
  const label = t('render.focusMode', { state: t(focusMode ? 'common.on' : 'common.off') });
  button.setAttribute('aria-pressed', String(focusMode));
  button.classList.toggle('active', focusMode);
  button.title = label;
  button.setAttribute('aria-label', label);
}

function setFocusMode(enabled) {
  focusMode = enabled;
  document.body.classList.toggle('focus-mode', focusMode);
  $('focus-exit-btn').hidden = !focusMode;
  syncFocusButton();
  scheduleLayout();
}

function shouldIgnoreShortcut(event) {
  if (event.defaultPrevented || event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey)
    return true;
  const target = event.target;
  if (target?.closest?.('input, textarea, select, .ace_editor, [role="textbox"]') || target?.isContentEditable)
    return true;
  if (document.querySelector('.modal-backdrop.show, .dialog-backdrop.show')) return true;
  return isRigTransformInteractionActive() || isRigJointPickingActive();
}

const SHORTCUTS = {
  BracketLeft: toggleLeftDock,
  BracketRight: toggleRightDock,
  Backslash: () => setFocusMode(!focusMode),
};

function onShortcut(event) {
  const action = SHORTCUTS[event.code];
  if (!action || shouldIgnoreShortcut(event)) return;
  event.preventDefault();
  // In focus mode a dock shortcut first brings the panels back as they were.
  if (focusMode && event.code !== 'Backslash') setFocusMode(false);
  else action();
}

export function initDockLayout({ onWidthChange }) {
  widthChangeHandler = onWidthChange;
  bindResizer('left');
  bindResizer('right');
  $('focus-mode-btn').addEventListener('click', () => setFocusMode(!focusMode));
  $('focus-exit-btn').addEventListener('click', () => setFocusMode(false));
  window.addEventListener(LANGUAGE_CHANGED, syncFocusButton);
  document.addEventListener('keydown', onShortcut);
  window.addEventListener('resize', () => {
    syncResizerAria('left');
    syncResizerAria('right');
    scheduleLayout();
  });
  // Observing also delivers an initial callback, which computes the first clearance.
  const observer = new ResizeObserver(scheduleLayout);
  for (const id of [...CLEARANCE_OVERLAYS, 'left-dock', 'right-dock']) observer.observe($(id));
  syncResizerAria('left');
  syncResizerAria('right');
  syncFocusButton();
}
