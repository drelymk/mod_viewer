"""Focused contracts for the first-party frontend localization layer."""


def test_catalogs_have_matching_keys_and_support_lookup_fallback(module_page):
    result = module_page.evaluate("""async () => {
      const i18n = await import('./js/i18n/index.js');
      const enKeys = Object.keys(i18n.LOCALES.en).sort();
      const zhKeys = Object.keys(i18n.LOCALES['zh-CN']).sort();
      i18n.setLocale('zh-CN');
      const element = document.createElement('span');
      element.dataset.i18n = 'toolbar.openMod';
      document.body.appendChild(element);
      i18n.applyTranslations(document.body);
      const chinese = element.textContent;
      return {
        sameKeys: JSON.stringify(enKeys) === JSON.stringify(zhKeys),
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
