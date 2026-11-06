// Settings appearance controls share the viewer preference lifecycle.

import { LOCALES, setLocale } from '../i18n/index.js';

const DEFAULT_PANEL_OPACITY = 58;
const $ = (id) => document.getElementById(id);

export function initAppearanceControls(change) {
  const slider = $('panel-opacity');
  const output = $('panel-opacity-value');
  const select = $('app-language');
  const apply = (name, value) => {
    if (name === 'panelOpacity') {
      const number = Number(value);
      const opacity = Number.isFinite(number) ? Math.min(100, Math.max(0, Math.round(number))) : DEFAULT_PANEL_OPACITY;
      if (slider) slider.value = String(opacity);
      if (output) output.value = output.textContent = `${opacity}%`;
      document.documentElement.style.setProperty('--panel-opacity', String(opacity / 100));
      return opacity;
    }
    if (name === 'language') {
      const language = setLocale(Object.hasOwn(LOCALES, value) ? value : 'en');
      if (select) select.value = language;
      return language;
    }
  };
  apply('panelOpacity', DEFAULT_PANEL_OPACITY);
  apply('language', 'en');
  slider?.addEventListener('input', () => {
    change('panelOpacity', apply('panelOpacity', slider.value), { persist: false });
  });
  slider?.addEventListener('change', () => {
    change('panelOpacity', apply('panelOpacity', slider.value));
  });
  select?.addEventListener('change', () => {
    change('language', apply('language', select.value));
  });
  return apply;
}
