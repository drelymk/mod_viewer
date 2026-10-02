// Global appearance preferences backed by the app config.

import { LANGUAGE_CHANGED, LOCALES, getLocale, setLocale, t } from '../i18n/index.js';
import { closeHeaderPopovers } from './header-popovers.js';
import { createPreferencePersistence } from './preference-persistence.js';

const DEFAULT_PANEL_OPACITY = 58;
const DEFAULT_LANGUAGE = 'en';

const $ = (id) => document.getElementById(id);

function normalizeOpacity(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return DEFAULT_PANEL_OPACITY;
  return Math.min(100, Math.max(0, Math.round(number)));
}

function normalizeLanguage(value) {
  return Object.hasOwn(LOCALES, value) ? value : DEFAULT_LANGUAGE;
}

export function initPanelOpacityControl() {
  const button = $('appearance-btn');
  const popover = $('appearance-popover');
  const slider = $('panel-opacity');
  const output = $('panel-opacity-value');
  if (!button || !popover || !slider || !output) return;

  const updateOpacityLabels = (opacity) => {
    const label = t('toolbar.panelOpacityValue', { opacity });
    button.setAttribute('aria-label', label);
    button.title = label;
    const labelNode = popover.querySelector('label[for="panel-opacity"]');
    if (labelNode) labelNode.textContent = t('toolbar.panelOpacity');
  };
  const apply = (value) => {
    const opacity = normalizeOpacity(value);
    const factor = opacity / 100;
    slider.value = String(opacity);
    output.value = `${opacity}%`;
    output.textContent = `${opacity}%`;
    document.documentElement.style.setProperty('--panel-opacity', String(factor));
    document.documentElement.style.setProperty('--panel-blur', `${factor * 10}px`);
    document.documentElement.style.setProperty('--panel-shadow-opacity', String(factor * 0.22));
    updateOpacityLabels(opacity);
    return opacity;
  };
  const close = (restoreFocus = false) => {
    popover.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (restoreFocus) button.focus();
  };

  apply(DEFAULT_PANEL_OPACITY);
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    const opening = popover.hidden;
    if (opening) closeHeaderPopovers('appearance-popover');
    popover.hidden = !popover.hidden;
    button.setAttribute('aria-expanded', String(!popover.hidden));
    if (!popover.hidden) slider.focus();
  });
  const persistence = createPreferencePersistence({
    getMethod: 'get_panel_opacity',
    setMethod: 'set_panel_opacity',
    key: 'panelOpacity',
    apply: (_name, value) => apply(value),
  });

  slider.addEventListener('input', () => {
    persistence.change('panelOpacity', apply(slider.value), { persist: false });
  });
  slider.addEventListener('change', () => {
    const opacity = apply(slider.value);
    persistence.change('panelOpacity', opacity);
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.appearance-wrap')) close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !popover.hidden) close(true);
  });
  window.addEventListener(LANGUAGE_CHANGED, () => {
    updateOpacityLabels(normalizeOpacity(slider.value));
  });
}

export function initLanguageControl() {
  const control = $('language-control');
  const button = $('language-btn');
  const popover = $('language-popover');
  const select = $('app-language');
  if (!control || !button || !popover || !select) return;

  const updateLabels = () => {
    const name = select.selectedOptions[0]?.textContent || 'English';
    const label = t('toolbar.languageValue', { name });
    button.setAttribute('aria-label', label);
    button.title = label;
    popover.setAttribute('aria-label', t('toolbar.language'));
  };
  const apply = (value) => {
    const language = setLocale(normalizeLanguage(value));
    select.value = language;
    updateLabels();
    return language;
  };
  const close = (restoreFocus = false) => {
    popover.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (restoreFocus) button.focus();
  };
  apply(getLocale());
  const persistence = createPreferencePersistence({
    getMethod: 'get_language',
    setMethod: 'set_language',
    key: 'language',
    apply: (_name, value) => apply(value),
  });
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    const opening = popover.hidden;
    if (opening) closeHeaderPopovers('language-popover');
    popover.hidden = !popover.hidden;
    button.setAttribute('aria-expanded', String(!popover.hidden));
    if (!popover.hidden) select.focus();
  });
  select.addEventListener('change', () => {
    const language = apply(select.value);
    persistence.change('language', language);
    close();
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#language-control')) close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !popover.hidden) close(true);
  });
  window.addEventListener(LANGUAGE_CHANGED, updateLabels);
}
