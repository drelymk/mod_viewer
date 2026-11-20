// Viewer-only controls for the existing animation runtime.

import {
  getAnimationPlaybackState,
  setAnimationFrame,
  setAnimationPaused,
  setAnimationPlaybackListener,
} from '../mesh/animation-runtime.js';
import { LANGUAGE_CHANGED, getLocale, t } from '../i18n/index.js';
import { getLoadedWeightRigFeature } from '../weight-rig/weight-rig-feature.js';
import { fillRigClipSelect } from '../weight-rig/rig-animation.js';

export function initAnimationPlayback() {
  const bar = document.getElementById('animation-playback');
  const button = document.getElementById('animation-play-pause');
  const slider = document.getElementById('animation-frame');
  const counter = document.getElementById('animation-frame-counter');
  const toolbar = document.getElementById('tool-panel');
  const tools = document.getElementById('tool-buttons');
  const clipControls = document.getElementById('animation-clip-controls');
  const clips = document.getElementById('animation-clip');
  const previous = document.getElementById('animation-previous');
  const next = document.getElementById('animation-next');
  let clipList = null;
  let sequenceList = null;
  let clipLocale = null;
  let sortedClips = [];
  const resize = new ResizeObserver(() => {
    bar.style.width = `${toolbar.getBoundingClientRect().width}px`;
  });
  resize.observe(toolbar, { box: 'border-box' });
  const getRig = () => getLoadedWeightRigFeature();
  const rigState = () => getRig()?.getRigAnimationState();
  const rigAvailable = () => !!rigState()?.clips.length;
  const sync = () => {
    const state = getAnimationPlaybackState();
    const rig = rigState();
    const useRig = !!rig?.clips.length;
    const canSeek = useRig || state.canSeek;
    const paused = useRig ? !rig.playing : state.paused;
    const available = useRig || state.available;
    const activating = useRig && getRig().isWeightRigActivating();
    bar.hidden = !available || !canSeek;
    button.hidden = !available;
    button.disabled = useRig && (activating || rig.loading);
    const parent = canSeek ? bar : tools;
    if (button.parentElement !== parent) {
      if (canSeek) clipControls.after(button);
      else parent.prepend(button);
    }
    const label = t(paused ? 'animation.play' : 'animation.pause');
    button.title = label;
    button.setAttribute('aria-label', label);
    button.querySelector('use').setAttribute('href', paused ? '#icon-play' : '#icon-pause');
    clipControls.hidden = !useRig;
    if (useRig) {
      if (clipList !== rig.clips || sequenceList !== rig.sequences || clipLocale !== getLocale()) {
        clipList = rig.clips;
        sequenceList = rig.sequences;
        clipLocale = getLocale();
        sortedClips = fillRigClipSelect(clips, rig.clips, rig.sequences);
      }
      clips.value = rig.selected || '';
      clips.disabled = previous.disabled = next.disabled = !!activating;
      slider.hidden = counter.hidden = false;
      // Sequence playback owns the timeline, so scrubbing stays off while a sequence runs.
      slider.disabled = rig.stepCount ? true : activating || rig.loading || !rig.duration;
      slider.min = '0';
      slider.max = String(rig.duration || 1);
      slider.step = '0.001';
      slider.value = String(rig.time);
      slider.setAttribute('aria-label', t('weightRig.animationTime'));
      const text = `${rig.time.toFixed(2)} / ${rig.duration.toFixed(2)} s`;
      slider.setAttribute('aria-valuetext', text);
      const stepLabel = rig.stepCount
        ? `${t('weightRig.animationSequenceStep', { step: rig.step + 1, count: rig.stepCount })} · `
        : '';
      counter.textContent = rig.errorKey
        ? t(rig.errorKey)
        : rig.error || (rig.loading ? t('weightRig.animationLoading') : `${stepLabel}${text}`);
      return;
    }
    clipList = null;
    sequenceList = null;
    slider.disabled = false;
    slider.step = '1';
    slider.setAttribute('aria-label', t('animation.frame'));
    slider.hidden = counter.hidden = !canSeek;
    if (state.frame === null) return;
    slider.min = String(state.frameStart);
    slider.max = String(state.frameEnd);
    slider.value = String(state.frame);
    const text = t('animation.frameCounter', { frame: state.frame, end: state.frameEnd });
    slider.setAttribute('aria-valuetext', text);
    counter.textContent = text;
  };
  button.addEventListener('click', () => {
    if (!rigAvailable()) return setAnimationPaused(!getAnimationPlaybackState().paused);
    const state = rigState();
    if (!state.selected || !state.duration) return getRig().selectRigAnimation(state.selected || state.clips[0].id);
    getRig().setRigAnimationPlaying(!state.playing);
  });
  slider.addEventListener('input', () => {
    if (rigAvailable()) getRig().seekRigAnimation(slider.value);
    else setAnimationFrame(slider.value);
  });
  clips.addEventListener('change', () => getRig()?.selectRigAnimation(clips.value));
  const advance = (direction) => {
    const state = rigState();
    if (!state?.clips.length) return;
    // Step through the order the picker shows.
    const count = sortedClips.length;
    const index = sortedClips.findIndex((clip) => clip.id === state.selected);
    const target = index < 0 ? (direction > 0 ? 0 : count - 1) : (index + direction + count) % count;
    void getRig().selectRigAnimation(sortedClips[target].id);
  };
  previous.addEventListener('click', () => advance(-1));
  next.addEventListener('click', () => advance(1));
  for (const name of [
    'mod-viewer-model-animation-changed',
    'mod-viewer-model-weight-changed',
    'mod-viewer-model-rig-changed',
    'mod-viewer-weight-rig-activation-changed',
    LANGUAGE_CHANGED,
  ])
    window.addEventListener(name, () => sync());
  setAnimationPlaybackListener(sync);
}
