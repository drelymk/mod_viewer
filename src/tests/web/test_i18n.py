"""Language switching preserves identities and interpolates text."""


def test_locale_switch_translates_ui_and_preserves_identity(module_page):
    result = module_page.evaluate(r"""async () => {
      const i18n = await import('./js/i18n/index.js');
      const button = document.createElement('button');
      button.dataset.i18n = 'common.cancel';
      const identity = document.createElement('span');
      identity.textContent = 'resource-01';
      document.body.append(button, identity);
      const texts = [];
      for (const locale of ['en', 'ja', 'en']) {
        i18n.setLocale(locale);
        texts.push(button.textContent);
      }
      const key = Object.keys(i18n.LOCALES.en).find(key => i18n.LOCALES.en[key].includes('{'));
      const template = i18n.LOCALES.en[key];
      const params = Object.fromEntries([...template.matchAll(/\{([\w.-]+)\}/g)].map(match => [match[1], '<fixture-01>']));
      const interpolated = i18n.t(key, params);
      i18n.setLocale('locale-unknown');
      return {texts, identity: identity.textContent, locale: i18n.getLocale(),
              interpolated, expected: template.replace(/\{([\w.-]+)\}/g, '<fixture-01>')};
    }""")
    assert result['texts'][0] == result['texts'][2]
    assert result['texts'][0] != result['texts'][1]
    assert result['identity'] == 'resource-01'
    assert result['locale'] == 'en'
    assert result['interpolated'] == result['expected']


def test_all_locale_entries_preserve_english_placeholders(module_page):
    mismatches = module_page.evaluate(r"""async () => {
      const {LOCALES} = await import('./js/i18n/index.js');
      const placeholders = text => [...text.matchAll(/\{([\w.-]+)\}/g)]
        .map(match => match[1]).sort().join(',');
      return Object.entries(LOCALES).flatMap(([locale, catalog]) =>
        Object.entries(LOCALES.en).filter(([key, text]) =>
          typeof catalog[key] !== 'string' || placeholders(catalog[key]) !== placeholders(text))
          .map(([key]) => ({locale, key})));
    }""")
    assert mismatches == []
