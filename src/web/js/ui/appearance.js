// Global appearance preferences backed by the app config.

import {
  LANGUAGE_CHANGED, getLocale, setLocale, t,
} from '../i18n/index.js';
import { closeHeaderPopovers } from './header-popovers.js';

const DEFAULT_PANEL_OPACITY = 58;
const DEFAULT_LANGUAGE = 'en';

const $ = id => document.getElementById(id);

function normalizeOpacity(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return DEFAULT_PANEL_OPACITY;
  return Math.min(100, Math.max(0, Math.round(number)));
}

function normalizeLanguage(value) {
  return value === 'zh-CN' ? value : DEFAULT_LANGUAGE;
}

export function initPanelOpacityControl() {
  const button = $('appearance-btn');
  const popover = $('appearance-popover');
  const slider = $('panel-opacity');
  const output = $('panel-opacity-value');
  if (!button || !popover || !slider || !output) return;

  let loadedOpacity = null;
  let pendingOpacity = null;
  let userChanged = false;

  const updateOpacityLabels = opacity => {
    const label = t('toolbar.panelOpacityValue', {opacity});
    button.setAttribute('aria-label', label);
    button.title = label;
    const labelNode = popover.querySelector('label[for="panel-opacity"]');
    if (labelNode) labelNode.textContent = t('toolbar.panelOpacity');
  };
  const apply = value => {
    const opacity = normalizeOpacity(value);
    const factor = opacity / 100;
    slider.value = String(opacity);
    output.value = `${opacity}%`;
    output.textContent = `${opacity}%`;
    document.documentElement.style.setProperty('--panel-opacity', String(factor));
    document.documentElement.style.setProperty('--panel-blur', `${factor * 10}px`);
    document.documentElement.style.setProperty(
      '--panel-shadow-opacity', String(factor * 0.22));
    updateOpacityLabels(opacity);
    return opacity;
  };
  const close = (restoreFocus = false) => {
    popover.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (restoreFocus) button.focus();
  };

  apply(DEFAULT_PANEL_OPACITY);
  button.addEventListener('click', event => {
    event.stopPropagation();
    const opening = popover.hidden;
    if (opening) closeHeaderPopovers('appearance-popover');
    popover.hidden = !popover.hidden;
    button.setAttribute('aria-expanded', String(!popover.hidden));
    if (!popover.hidden) slider.focus();
  });
  const saveOpacity = async opacity => {
    if (loadedOpacity === null) {
      pendingOpacity = opacity;
      return;
    }
    if (opacity === loadedOpacity) return;
    const save = window.pywebview?.api?.set_panel_opacity;
    if (typeof save !== 'function') {
      pendingOpacity = opacity;
      return;
    }
    try {
      const result = await save.call(window.pywebview.api, opacity);
      if (result?.error) {
        console.error(result.error);
        return;
      }
      loadedOpacity = normalizeOpacity(result?.value ?? opacity);
      pendingOpacity = null;
    } catch (error) {
      console.error(error);
    }
  };
  const loadOpacity = async () => {
    const load = window.pywebview?.api?.get_panel_opacity;
    if (typeof load !== 'function') return false;
    try {
      const result = await load.call(window.pywebview.api);
      if (result?.error) {
        console.error(result.error);
        return true;
      }
      loadedOpacity = normalizeOpacity(result?.value);
      if (!userChanged) apply(loadedOpacity);
      else if (pendingOpacity !== null) await saveOpacity(pendingOpacity);
    } catch (error) {
      console.error(error);
    }
    return true;
  };

  slider.addEventListener('input', () => {
    userChanged = true;
    apply(slider.value);
  });
  slider.addEventListener('change', () => {
    userChanged = true;
    const opacity = apply(slider.value);
    void saveOpacity(opacity);
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.appearance-wrap')) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !popover.hidden) close(true);
  });
  window.addEventListener(LANGUAGE_CHANGED, () => {
    updateOpacityLabels(normalizeOpacity(slider.value));
  });

  void loadOpacity().then(loaded => {
    if (!loaded) window.addEventListener('pywebviewready', loadOpacity, {once: true});
  });
}

export function initLanguageControl() {
  const control = $('language-control');
  const button = $('language-btn');
  const popover = $('language-popover');
  const select = $('app-language');
  if (!control || !button || !popover || !select) return;

  let loadedLanguage = null;
  let pendingLanguage = null;
  let userChanged = false;

  const updateLabels = () => {
    const name = select.value === 'zh-CN'
      ? t('toolbar.simplifiedChinese') : t('toolbar.english');
    const label = t('toolbar.languageValue', {name});
    button.setAttribute('aria-label', label);
    button.title = label;
    popover.setAttribute('aria-label', t('toolbar.language'));
  };
  const apply = value => {
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
  const saveLanguage = async language => {
    if (loadedLanguage === null) {
      pendingLanguage = language;
      return;
    }
    if (language === loadedLanguage) return;
    const save = window.pywebview?.api?.set_language;
    if (typeof save !== 'function') {
      pendingLanguage = language;
      return;
    }
    try {
      const result = await save.call(window.pywebview.api, language);
      if (result?.error) {
        console.error(result.error);
        return;
      }
      loadedLanguage = normalizeLanguage(result?.value ?? language);
      pendingLanguage = null;
    } catch (error) {
      console.error(error);
    }
  };
  const loadLanguage = async () => {
    const load = window.pywebview?.api?.get_language;
    if (typeof load !== 'function') return false;
    try {
      const result = await load.call(window.pywebview.api);
      if (result?.error) {
        console.error(result.error);
        return true;
      }
      loadedLanguage = normalizeLanguage(result?.value);
      if (!userChanged) apply(loadedLanguage);
      else if (pendingLanguage !== null) await saveLanguage(pendingLanguage);
    } catch (error) {
      console.error(error);
    }
    return true;
  };

  apply(getLocale());
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', event => {
    event.stopPropagation();
    const opening = popover.hidden;
    if (opening) closeHeaderPopovers('language-popover');
    popover.hidden = !popover.hidden;
    button.setAttribute('aria-expanded', String(!popover.hidden));
    if (!popover.hidden) select.focus();
  });
  select.addEventListener('change', () => {
    userChanged = true;
    const language = apply(select.value);
    void saveLanguage(language);
    close();
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('#language-control')) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !popover.hidden) close(true);
  });
  window.addEventListener(LANGUAGE_CHANGED, updateLabels);
  void loadLanguage().then(loaded => {
    if (!loaded) window.addEventListener('pywebviewready', loadLanguage, {once: true});
  });
}
