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
