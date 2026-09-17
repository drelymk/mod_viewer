import en from './locales/en.js';
import zhCN from './locales/zh-CN.js';

const LOCALES = Object.freeze({en, 'zh-CN': zhCN});
const DEFAULT_LOCALE = 'en';
const LANGUAGE_CHANGED = 'mod-viewer-language-changed';
let activeLocale = DEFAULT_LOCALE;

function normalizeLocale(locale) {
  return Object.hasOwn(LOCALES, locale) ? locale : DEFAULT_LOCALE;
}

function interpolate(value, params) {
  if (!params || typeof value !== 'string') return value;
  return value.replace(/\{([\w.-]+)\}/g, (match, name) =>
    Object.hasOwn(params, name) ? String(params[name]) : match);
}

export function t(key, params) {
  const selected = LOCALES[activeLocale];
  const value = selected[key] ?? LOCALES[DEFAULT_LOCALE][key];
  if (value === undefined) {
    console.warn(`[i18n] Missing translation: ${key}`);
    return key;
  }
  return interpolate(value, params);
}

export function getLocale() {
  return activeLocale;
}

export function applyTranslations(root) {
  const target = root || (typeof document !== 'undefined' ? document : null);
  if (!target) return root;
  const scope = target?.querySelectorAll ? target : document;
  const elements = [];
  if (target?.matches?.('[data-i18n], [data-i18n-title], [data-i18n-aria-label], [data-i18n-placeholder]')) {
    elements.push(target);
  }
  elements.push(...scope.querySelectorAll(
    '[data-i18n], [data-i18n-title], [data-i18n-aria-label], [data-i18n-placeholder]'));
  for (const element of elements) {
    if (element.dataset.i18n) element.textContent = t(element.dataset.i18n);
    if (element.dataset.i18nTitle) element.title = t(element.dataset.i18nTitle);
    if (element.dataset.i18nAriaLabel) {
      element.setAttribute('aria-label', t(element.dataset.i18nAriaLabel));
    }
    if (element.dataset.i18nPlaceholder) {
      element.setAttribute('placeholder', t(element.dataset.i18nPlaceholder));
    }
  }
  return target;
}

export function setLocale(locale) {
  activeLocale = normalizeLocale(locale);
  if (typeof document !== 'undefined') {
    document.documentElement.lang = activeLocale;
    applyTranslations(document);
    window.dispatchEvent(new CustomEvent(LANGUAGE_CHANGED, {
      detail: {locale: activeLocale},
    }));
  }
  return activeLocale;
}

export { LANGUAGE_CHANGED, LOCALES };
