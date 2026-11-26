// Viewport playback dock for the existing animation runtimes: a searchable
// clip/sequence picker, transport controls, and a scrubber that runs over a
// step bar while an authored sequence plays.

import {
  getAnimationPlaybackState,
  setAnimationFrame,
  setAnimationPaused,
  setAnimationPlaybackListener,
} from '../mesh/animation-runtime.js';
import { LANGUAGE_CHANGED, getLocale, t } from '../i18n/index.js';
import { getLoadedWeightRigFeature } from '../weight-rig/weight-rig-feature.js';
import { sortRigAnimations, splitRigClips } from '../weight-rig/rig-animation.js';
import { findDefaultAnimationId } from '../app/default-animation.js';

const SVG = 'http://www.w3.org/2000/svg';
const ICONS = { rest: '#icon-rest-pose', clip: '#icon-clip', sequence: '#icon-sequence', byName: '#icon-by-name' };
const PAGE = 8;
const EMPTY_CATALOG = Object.freeze({ clips: [], sequences: [], byId: new Map() });

// Every whitespace-separated query term must occur in the name.
export function animationMatches(name, query) {
  const text = name.toLocaleLowerCase();
  return query
    .toLocaleLowerCase()
    .split(/\s+/)
    .every((term) => text.includes(term));
}

function icon(kind) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('class', 'ui-icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVG, 'use');
  use.setAttribute('href', ICONS[kind]);
  svg.appendChild(use);
  return svg;
}

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function setProgress(slider) {
  const min = Number(slider.min);
  const span = Number(slider.max) - min;
  const ratio = span > 0 ? (Number(slider.value) - min) / span : 0;
  slider.style.setProperty('--progress', `${(Math.max(0, Math.min(1, ratio)) * 100).toFixed(2)}%`);
}

// Typing in an editable or activatable control keeps Space for that control.
function ownsSpace(target) {
  return (
    target instanceof Element &&
    (target.isContentEditable ||
      !!target.closest(
        'input, textarea, select, button, a[href], summary, .ace_editor, [role="textbox"], [role="option"], [role="button"], [role="tab"], [role="checkbox"], [role="switch"], [contenteditable]',
      ))
  );
}

export function initAnimationPlayback() {
  const $ = (id) => document.getElementById(id);
  const bar = $('animation-playback');
  const toolbar = $('tool-panel');
  const head = $('animation-clip-controls');
  const trigger = $('animation-clip');
  const triggerIcon = trigger.querySelector('.animation-kind-icon use');
  const triggerName = trigger.querySelector('.animation-picker-name');
  const triggerMeta = trigger.querySelector('.animation-picker-meta');
  const previous = $('animation-previous');
  const next = $('animation-next');
  const transport = $('animation-transport');
  const button = $('animation-play-pause');
  const stopButton = $('animation-stop');
  const propsButton = $('animation-props');
  const slider = $('animation-frame');
  const steps = $('animation-steps');
  const timeline = steps.parentElement;
  const counter = $('animation-frame-counter');
  const message = $('animation-message');
  const tools = $('animation-tools');
  const toggle = $('animation-toggle-btn');
  const picker = $('animation-picker');
  const search = $('animation-search');
  const filterRow = picker.querySelector('.animation-filters');
  const filters = [...picker.querySelectorAll('.animation-filter')];
  const list = $('animation-options');
  const empty = $('animation-options-empty');

  let catalog = EMPTY_CATALOG;
  let catalogKey = null;
  let filter = 'all';
  let options = [];
  let active = -1;
  let lastChoice = null;
  let collapsed = false;
  let stepSequence = null;

  const getRig = () => getLoadedWeightRigFeature();
  const rigState = () => getRig()?.getRigAnimationState();
  const rigAvailable = () => !!rigState()?.clips.length;

  const describe = (choice) => {
    if (choice.kind === 'sequence') return t('animation.stepCount', { count: choice.item.steps.length });
    const duration = Number(choice.item.duration);
    return choice.kind === 'clip' && Number.isFinite(duration) ? `${duration.toFixed(2)} s` : '';
  };

  // Ticks rerun sync at 60 Hz; the catalog only changes with the rig or locale.
  function refreshCatalog(rig) {
    const locale = getLocale();
    if (catalogKey?.clips === rig.clips && catalogKey.sequences === rig.sequences && catalogKey.locale === locale)
      return false;
    catalogKey = { clips: rig.clips, sequences: rig.sequences, locale };
    const { own: clips, shared } = splitRigClips(rig.clips);
    const sequences = sortRigAnimations(rig.sequences);
    catalog = {
      clips,
      shared,
      sequences,
      byId: new Map([
        ...clips.map((item) => [item.id, { kind: 'clip', pool: 'clips', id: item.id, item }]),
        ...shared.map((item) => [item.id, { kind: 'clip', pool: 'shared', id: item.id, item }]),
        ...sequences.map((item) => [item.id, { kind: 'sequence', pool: 'sequences', id: item.id, item }]),
      ]),
    };
    if (!catalog[filter]?.length) filter = 'all';
    stepSequence = null;
    return true;
  }

  function placePicker() {
    const rect = bar.getBoundingClientRect();
    const gutter = 8;
    const width = Math.min(Math.max(rect.width, 360), window.innerWidth - 2 * gutter);
    const left = Math.min(Math.max(gutter, rect.left + (rect.width - width) / 2), window.innerWidth - width - gutter);
    picker.style.width = `${width}px`;
    picker.style.left = `${left}px`;
    picker.style.bottom = `${window.innerHeight - rect.top + 6}px`;
    picker.style.maxHeight = `${Math.max(180, Math.min(460, rect.top - 72))}px`;
  }

  function highlight(index, block = 'nearest') {
    active = options.length ? Math.max(0, Math.min(options.length - 1, index)) : -1;
    let current = null;
    for (const node of list.children) {
      const on = node.dataset.index !== undefined && Number(node.dataset.index) === active;
      node.classList.toggle('active', on);
      if (on) current = node;
    }
    if (!current) {
      search.removeAttribute('aria-activedescendant');
      return;
    }
    search.setAttribute('aria-activedescendant', current.id);
    if (block) current.scrollIntoView({ block });
  }

  function optionNode(choice, index, selected) {
    const node = document.createElement('li');
    node.id = `animation-option-${index}`;
    node.className = 'animation-option';
    node.setAttribute('role', 'option');
    node.setAttribute('aria-selected', String(choice.id === selected));
    node.dataset.index = String(index);
    node.dataset.value = choice.id;
    node.dataset.kind = choice.kind;
    const name = document.createElement('span');
    name.className = 'animation-option-name';
    name.textContent = choice.kind === 'rest' ? t('weightRig.animationRest') : choice.item.name;
    name.title = name.textContent;
    const meta = document.createElement('span');
    meta.className = 'animation-option-meta';
    meta.textContent = choice.kind === 'rest' ? '' : describe(choice);
    node.append(icon(choice.kind), name);
    // Sequences paired by clip names rather than game data carry a link mark and a hint.
    if (choice.item?.by_name) {
      const mark = icon('byName');
      mark.classList.add('animation-by-name-mark');
      node.append(mark);
      node.title = t('animation.byNameHint');
    }
    node.append(meta);
    return node;
  }

  // focus: 'selected' reveals the playing entry, 'first' follows a new query.
  function renderOptions(focus = 'selected') {
    const query = search.value.trim();
    const clips = catalog.clips.filter((item) => animationMatches(item.name, query));
    const sequences = catalog.sequences.filter((item) => animationMatches(item.name, query));
    const shared = catalog.shared.filter((item) => animationMatches(item.name, query));
    const counts = {
      all: clips.length + sequences.length + shared.length,
      clips: clips.length,
      sequences: sequences.length,
      shared: shared.length,
    };
    // Kinds this rig has; the filter row appears only when there is more than one.
    const present = ['clips', 'sequences', 'shared'].filter((pool) => catalog[pool].length);
    filterRow.hidden = present.length < 2;
    for (const node of filters) {
      node.hidden = node.dataset.filter !== 'all' && !present.includes(node.dataset.filter);
      node.setAttribute('aria-pressed', String(node.dataset.filter === filter));
      setText(node.querySelector('.animation-filter-count'), String(counts[node.dataset.filter]));
    }
    const groups = [
      ['clips', 'animation.filterClips', 'clip', clips],
      ['sequences', 'weightRig.animationSequences', 'sequence', sequences],
      ['shared', 'animation.filterShared', 'clip', shared],
    ]
      .filter(([pool]) => (filter === 'all' || filter === pool) && catalog[pool].length)
      .map(([, label, kind, items]) => [label, kind, items]);
    const selected = rigState()?.selected || '';
    const labelled = filter === 'all' && present.length > 1;
    const nodes = [];
    options = [];
    const add = (choice) => {
      nodes.push(optionNode(choice, options.length, selected));
      options.push(choice);
    };
    if (filter === 'all' && !query) add({ kind: 'rest', id: '' });
    for (const [label, kind, items] of groups) {
      if (!items.length) continue;
      if (labelled) {
        const group = document.createElement('li');
        group.className = 'animation-option-group';
        group.setAttribute('role', 'presentation');
        const title = document.createElement('span');
        title.textContent = t(label);
        const count = document.createElement('span');
        count.textContent = String(items.length);
        group.append(title, count);
        nodes.push(group);
      }
      items.forEach((item) => add({ kind, id: item.id, item }));
    }
    list.replaceChildren(...nodes);
    empty.hidden = options.length > 0;
    const chosen = options.findIndex((choice) => choice.id === selected);
    if (focus === 'selected') highlight(chosen < 0 ? 0 : chosen, 'center');
    else highlight(0);
  }

  function openPicker() {
    if (!picker.hidden || trigger.disabled || head.hidden) return;
    picker.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    placePicker();
    renderOptions('selected');
    search.focus();
    search.select();
  }

  function closePicker({ focus = false } = {}) {
    if (picker.hidden) return;
    picker.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    search.removeAttribute('aria-activedescendant');
    if (focus) trigger.focus();
  }

  function choose(index) {
    const choice = options[index];
    if (!choice) return;
    // A picked query is done; reopening shows the whole list around the choice.
    search.value = '';
    closePicker({ focus: true });
    void getRig()?.selectRigAnimation(choice.id);
  }

  function renderSteps(sequence, rig) {
    if (stepSequence !== sequence) {
      stepSequence = sequence;
      steps.replaceChildren(
        ...sequence.steps.map((step, index) => {
          const node = document.createElement('span');
          node.className = 'animation-step';
          node.style.flexGrow = String(Math.max(0.05, step.leave - step.start));
          node.title = `${index + 1}. ${catalog.byId.get(step.clip)?.item.name || step.clip}`;
          return node;
        }),
      );
    }
    // A selection that is still loading keeps the previous sequence's step state.
    const live = !rig.loading && rig.stepCount === sequence.steps.length;
    [...steps.children].forEach((node, index) => {
      const step = sequence.steps[index];
      const span = step.leave - step.start;
      const fill =
        !live || index > rig.step
          ? 0
          : index < rig.step || span <= 0
            ? 1
            : Math.max(0, Math.min(1, (rig.time - step.start) / span));
      node.style.setProperty('--fill', fill.toFixed(4));
      node.classList.toggle('active', live && index === rig.step);
    });
  }

  function syncRig(rig) {
    if (refreshCatalog(rig) && !picker.hidden) renderOptions('selected');
    if (rig.selected) lastChoice = rig.selected;
    const choice = catalog.byId.get(rig.selected) || null;
    const kind = choice?.kind || 'rest';
    const name = choice?.item.name || t('weightRig.animationRest');
    trigger.dataset.kind = kind;
    triggerIcon.setAttribute('href', ICONS[kind]);
    setText(triggerName, name);
    setText(triggerMeta, choice ? describe(choice) : '');
    trigger.title = name;
    trigger.setAttribute('aria-label', `${t('animation.select')}: ${name}`);
    stopButton.disabled = !rig.selected && !rig.loading;
    propsButton.hidden = !rig.hasProps;
    propsButton.setAttribute('aria-pressed', String(!!getRig()?.getRigPropsShown()));
    const sequence = choice?.kind === 'sequence' ? choice.item : null;
    // A playing sequence scrubs along its whole timeline over the step bar.
    const live = !!sequence && !rig.loading && rig.stepCount === sequence.steps.length;
    timeline.classList.toggle('sequence', !!sequence);
    slider.hidden = false;
    steps.hidden = !sequence;
    if (sequence) renderSteps(sequence, rig);
    else stepSequence = null;
    // The scrubber covers the step bar, so it names the playing step.
    slider.title = live ? steps.children[rig.step]?.title || '' : '';
    const position = live ? rig.sequenceTime : rig.time;
    const duration = live ? rig.sequenceDuration : rig.duration;
    slider.disabled = rig.loading || !duration;
    slider.min = '0';
    slider.max = String(duration || 1);
    slider.step = '0.001';
    slider.value = String(position);
    setProgress(slider);
    slider.setAttribute('aria-label', t('weightRig.animationTime'));
    const time = `${position.toFixed(2)} / ${duration.toFixed(2)} s`;
    slider.setAttribute('aria-valuetext', time);
    const error = rig.errorKey ? t(rig.errorKey) : rig.error;
    message.hidden = !error;
    setText(message, error || '');
    counter.hidden = !!error;
    setText(
      counter,
      rig.loading
        ? t('weightRig.animationLoading')
        : live
          ? `${t('weightRig.animationSequenceStep', { step: rig.step + 1, count: rig.stepCount })} · ${time}`
          : time,
    );
  }

  function sync() {
    const state = getAnimationPlaybackState();
    const rig = rigState();
    const useRig = !!rig?.clips.length;
    const canSeek = useRig || state.canSeek;
    const paused = useRig ? !rig.playing : state.paused;
    const available = useRig || state.available;
    const docked = available && canSeek;
    bar.hidden = !docked || collapsed;
    if (bar.hidden) closePicker();
    tools.hidden = !available;
    toggle.hidden = !docked;
    toggle.classList.toggle('active', !collapsed);
    toggle.setAttribute('aria-pressed', String(!collapsed));
    const toggleLabel = t(collapsed ? 'animation.showControls' : 'animation.hideControls');
    toggle.title = toggleLabel;
    toggle.setAttribute('aria-label', toggleLabel);
    // A collapsed dock leaves its play button beside the toggle.
    button.hidden = !available;
    button.disabled = useRig && rig.loading;
    if (docked && !collapsed) {
      if (button.parentElement !== transport) transport.prepend(button);
    } else if (button.parentElement !== tools) tools.append(button);
    const label = t(paused ? 'animation.play' : 'animation.pause');
    button.title = `${label} (Space)`;
    button.setAttribute('aria-label', label);
    button.querySelector('use').setAttribute('href', paused ? '#icon-play' : '#icon-pause');
    bar.classList.toggle('playing', !paused);
    head.hidden = !useRig;
    if (useRig) {
      syncRig(rig);
      return;
    }
    if (catalogKey) {
      catalogKey = null;
      catalog = EMPTY_CATALOG;
      lastChoice = null;
      closePicker();
    }
    stepSequence = null;
    steps.hidden = true;
    timeline.classList.remove('sequence');
    slider.title = '';
    message.hidden = true;
    stopButton.disabled = false;
    propsButton.hidden = true;
    slider.disabled = false;
    slider.step = '1';
    slider.setAttribute('aria-label', t('animation.frame'));
    slider.hidden = counter.hidden = !canSeek;
    if (state.frame === null) return;
    slider.min = String(state.frameStart);
    slider.max = String(state.frameEnd);
    slider.value = String(state.frame);
    setProgress(slider);
    const text = t('animation.frameCounter', { frame: state.frame, end: state.frameEnd });
    slider.setAttribute('aria-valuetext', text);
    setText(counter, text);
  }

  function togglePlayback() {
    if (!rigAvailable()) return setAnimationPaused(!getAnimationPlaybackState().paused);
    const state = rigState();
    if (state.selected && state.duration) return getRig().setRigAnimationPlaying(!state.playing);
    // Play after Stop resumes the last choice instead of jumping to the first clip.
    const id =
      state.selected ||
      (catalog.byId.has(lastChoice)
        ? lastChoice
        : findDefaultAnimationId(state) || catalog.clips[0]?.id || catalog.shared[0]?.id || state.clips[0].id);
    void getRig().selectRigAnimation(id);
  }

  // Previous/Next stay within the current kind, in the picker's order.
  function advance(direction) {
    const state = rigState();
    if (!state?.clips.length) return;
    const current = catalog.byId.get(state.selected || lastChoice);
    const pool = catalog[current?.pool || (catalog.clips.length ? 'clips' : 'shared')];
    const index = pool.findIndex((item) => item.id === current?.id);
    const target = index < 0 ? (direction > 0 ? 0 : pool.length - 1) : (index + direction + pool.length) % pool.length;
    void getRig().selectRigAnimation(pool[target].id);
  }

  new ResizeObserver(() => {
    bar.style.width = `${toolbar.getBoundingClientRect().width}px`;
    if (!picker.hidden) placePicker();
  }).observe(toolbar, { box: 'border-box' });
  window.addEventListener('resize', () => {
    if (!picker.hidden) placePicker();
  });

  button.addEventListener('click', togglePlayback);
  stopButton.addEventListener('click', () => {
    if (rigAvailable()) return getRig().stopRigAnimation();
    const state = getAnimationPlaybackState();
    setAnimationPaused(true);
    if (state.frameStart !== null) setAnimationFrame(state.frameStart);
  });
  slider.addEventListener('input', () => {
    setProgress(slider);
    if (rigAvailable()) getRig().seekRigAnimation(slider.value);
    else setAnimationFrame(slider.value);
  });
  // Shows or hides every game prop (on by default); the Weight/Rig panel checkbox mirrors this.
  propsButton.addEventListener('click', () => {
    const feature = getRig();
    feature?.setRigPropsShown(!feature.getRigPropsShown());
    propsButton.setAttribute('aria-pressed', String(!!feature?.getRigPropsShown()));
  });
  previous.addEventListener('click', () => advance(-1));
  next.addEventListener('click', () => advance(1));
  toggle.addEventListener('click', () => {
    collapsed = !collapsed;
    sync();
  });

  trigger.addEventListener('click', () => (picker.hidden ? openPicker() : closePicker()));
  trigger.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    openPicker();
  });
  search.addEventListener('input', () => renderOptions('first'));
  search.addEventListener('keydown', (event) => {
    const moves = { ArrowDown: 1, ArrowUp: -1, PageDown: PAGE, PageUp: -PAGE };
    if (event.key in moves) {
      event.preventDefault();
      highlight(active + moves[event.key]);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      choose(active);
    } else if (event.key === 'Escape') {
      // The first Escape clears a query; the next one closes the picker.
      event.preventDefault();
      event.stopPropagation();
      if (search.value) {
        search.value = '';
        renderOptions('selected');
      } else closePicker({ focus: true });
    }
  });
  // Keep focus in the search field while clicking options and filters.
  picker.addEventListener('mousedown', (event) => {
    if (event.target !== search) event.preventDefault();
  });
  picker.addEventListener('focusout', (event) => {
    if (!picker.contains(event.relatedTarget) && event.relatedTarget !== trigger) closePicker();
  });
  list.addEventListener('mousemove', (event) => {
    const node = event.target.closest('.animation-option');
    if (node && Number(node.dataset.index) !== active) highlight(Number(node.dataset.index), null);
  });
  list.addEventListener('click', (event) => {
    const node = event.target.closest('.animation-option');
    if (node) choose(Number(node.dataset.index));
  });
  for (const node of filters) {
    node.addEventListener('click', () => {
      filter = node.dataset.filter;
      renderOptions('selected');
      search.focus();
    });
  }
  document.addEventListener('keydown', (event) => {
    if (
      event.key !== ' ' ||
      event.repeat ||
      event.defaultPrevented ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      event.isComposing ||
      button.hidden ||
      button.disabled ||
      ownsSpace(event.target) ||
      document.querySelector('.modal-backdrop.show, .dialog-backdrop.show')
    )
      return;
    event.preventDefault();
    togglePlayback();
  });

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
