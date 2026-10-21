// Viewer-only controls for the existing animation runtime.

import {
  getAnimationPlaybackState,
  setAnimationFrame,
  setAnimationPaused,
  setAnimationPlaybackListener,
} from '../mesh/animation-runtime.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

export function initAnimationPlayback() {
  const bar = document.getElementById('animation-playback');
  const button = document.getElementById('animation-play-pause');
  const slider = document.getElementById('animation-frame');
  const counter = document.getElementById('animation-frame-counter');
  const toolbar = document.getElementById('tool-panel');
  const tools = document.getElementById('tool-buttons');
  const resize = new ResizeObserver(() => {
    bar.style.width = `${toolbar.getBoundingClientRect().width}px`;
  });
  resize.observe(toolbar, { box: 'border-box' });
  const sync = (state = getAnimationPlaybackState()) => {
    bar.hidden = !state.available || !state.canSeek;
    button.hidden = !state.available;
    const parent = state.canSeek ? bar : tools;
    if (button.parentElement !== parent) parent.prepend(button);
    const label = t(state.paused ? 'animation.play' : 'animation.pause');
    button.title = label;
    button.setAttribute('aria-label', label);
    button.querySelector('use').setAttribute('href', state.paused ? '#icon-play' : '#icon-pause');
    slider.hidden = counter.hidden = !state.canSeek;
    if (state.frame === null) return;
    slider.min = String(state.frameStart);
    slider.max = String(state.frameEnd);
    slider.value = String(state.frame);
    const text = t('animation.frameCounter', { frame: state.frame, end: state.frameEnd });
    slider.setAttribute('aria-valuetext', text);
    counter.textContent = text;
  };
  button.addEventListener('click', () => setAnimationPaused(!getAnimationPlaybackState().paused));
  slider.addEventListener('input', () => setAnimationFrame(slider.value));
  window.addEventListener(LANGUAGE_CHANGED, () => sync());
  setAnimationPlaybackListener(sync);
}
