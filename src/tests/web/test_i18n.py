"""Focused contracts for the first-party frontend localization layer."""

import re
from pathlib import Path


_CATALOG_KEY = re.compile(r"^\s*'([^']+)'\s*:")
_CATALOGS = ("en.js", "zh-CN.js", "ja.js", "ko.js", "es.js", "ru.js")


def test_catalogs_have_matching_keys_and_support_lookup_fallback(module_page):
    result = module_page.evaluate("""async () => {
      const i18n = await import('./js/i18n/index.js');
      const localeNames = ['en', 'zh-CN', 'ja', 'ko', 'es', 'ru'];
      const enKeys = Object.keys(i18n.LOCALES.en).sort();
      const sameKeys = localeNames.every(locale =>
        JSON.stringify(Object.keys(i18n.LOCALES[locale]).sort())
          === JSON.stringify(enKeys));
      i18n.setLocale('zh-CN');
      const element = document.createElement('span');
      element.dataset.i18n = 'toolbar.openMod';
      document.body.appendChild(element);
      i18n.applyTranslations(document.body);
      const chinese = element.textContent;
      return {
        sameKeys,
        chinese,
        interpolation: i18n.t('toolbar.panelOpacityValue', {opacity: 42}),
        unknown: i18n.t('missing.example'),
        fallbackLocale: i18n.setLocale('fr'),
        lang: document.documentElement.lang,
      };
    }""")
    assert result["sameKeys"] is True
    assert result["chinese"] == "打开 MOD"
    assert result["interpolation"] == "面板透明度：42%"
    assert result["unknown"] == "missing.example"
    assert result["fallbackLocale"] == "en"
    assert result["lang"] == "en"


def test_catalog_source_keys_are_unique():
    root = Path(__file__).resolve().parents[3]
    for name in _CATALOGS:
        keys = []
        for line in (root / "src" / "web" / "js" / "i18n" / "locales" / name).read_text(encoding="utf-8").splitlines():
            match = _CATALOG_KEY.match(line)
            if match:
                keys.append(match.group(1))
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        assert not duplicates, f"duplicate keys in {name}: {duplicates}"


def test_catalog_placeholders_match_english(module_page):
    result = module_page.evaluate("""async () => {
      const i18n = await import('./js/i18n/index.js');
      const locales = ['en', 'zh-CN', 'ja', 'ko', 'es', 'ru'];
      const placeholders = value => [...String(value).matchAll(
        /\\{([\\w.-]+)\\}/g)].map(match => match[1]).sort();
      const english = i18n.LOCALES.en;
      const mismatches = [];
      for (const locale of locales.slice(1)) {
        for (const key of Object.keys(english)) {
          const expected = placeholders(english[key]);
          const actual = placeholders(i18n.LOCALES[locale][key]);
          if (JSON.stringify(expected) !== JSON.stringify(actual)) {
            mismatches.push({locale, key, expected, actual});
          }
        }
      }
      return mismatches;
    }""")
    assert result == []


def test_asset_resolution_count_phrases_are_complete_for_each_locale(module_page):
    result = module_page.evaluate("""async () => {
      const i18n = await import('./js/i18n/index.js');
      const expected = {
        en: ['Partial: 2', 'Ambiguous: 2', 'Not found: 2'],
        'zh-CN': ['部分匹配：2', '有歧义：2', '未找到：2'],
        ja: ['部分一致：2', '曖昧：2', '未検出：2'],
        ko: ['부분 일치: 2', '모호함: 2', '찾지 못함: 2'],
        es: ['Parciales: 2', 'Ambiguos: 2', 'No encontrados: 2'],
        ru: ['Частичных совпадений: 2', 'Неоднозначных: 2', 'Не найдено: 2'],
      };
      return Object.fromEntries(Object.entries(expected).map(([locale]) => {
        i18n.setLocale(locale);
        return [locale, [
          i18n.t('health.partialDraws', {count: 2}),
          i18n.t('health.ambiguousDraws', {count: 2}),
          i18n.t('health.notFoundDraws', {count: 2}),
        ]];
      }));
    }""")
    assert result == {
        "en": ["Partial: 2", "Ambiguous: 2", "Not found: 2"],
        "zh-CN": ["部分匹配：2", "有歧义：2", "未找到：2"],
        "ja": ["部分一致：2", "曖昧：2", "未検出：2"],
        "ko": ["부분 일치: 2", "모호함: 2", "찾지 못함: 2"],
        "es": ["Parciales: 2", "Ambiguos: 2", "No encontrados: 2"],
        "ru": ["Частичных совпадений: 2", "Неоднозначных: 2", "Не найдено: 2"],
    }
