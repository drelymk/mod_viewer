// Native-picked dumps feed the compact asset extractor.
import { bridgeReady } from '../app/bridge.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

export function initAssetExtractionDialog({ onSaved }) {
  const $ = (id) => document.getElementById(id);
  const backdrop = $('asset-extract-backdrop');
  const form = $('asset-extract-form');
  const name = $('asset-extract-name');
  const dump = $('asset-extract-dump');
  const browse = $('asset-extract-browse');
  const cancel = $('asset-extract-cancel');
  const ok = $('asset-extract-ok');
  const error = $('asset-extract-error');
  let entry = null;
  let busy = false;
  let picking = false;
  let revision = 0;

  function message(text = '') {
    error.textContent = text;
    error.classList.toggle('show', !!text);
  }

  function labels() {
    $('asset-extract-title').textContent = t(entry?.asset ? 'asset.update' : 'asset.add');
    $('asset-extract-hint').textContent = t(entry?.asset ? 'asset.replaceHint' : 'asset.extractHint');
    ok.textContent = t(busy ? 'asset.extracting' : 'common.ok');
  }

  function setBusy(value) {
    busy = value;
    for (const input of [name, browse, cancel, ok]) input.disabled = value;
    labels();
  }

  function close() {
    if (busy) return;
    revision++;
    backdrop.classList.remove('show');
  }

  cancel.addEventListener('click', close);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
  });
  window.addEventListener(LANGUAGE_CHANGED, labels);
  browse.addEventListener('click', async () => {
    if (busy || picking) return;
    const request = revision;
    picking = true;
    browse.disabled = true;
    ok.disabled = true;
    try {
      const path = await (await bridgeReady()).select_asset_dump_folder();
      if (request === revision && path) dump.value = path;
    } catch (caught) {
      if (request === revision) message(caught.message || String(caught));
    } finally {
      picking = false;
      browse.disabled = busy;
      ok.disabled = busy;
    }
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy || picking) return;
    if (!name.value.trim() || !dump.value) {
      message(t('asset.required'));
      return;
    }
    const original = entry.asset ? entry.path : null;
    const parent = original ? original.replace(/[\\/][^\\/]+[\\/]*$/, '') : entry.path;
    message();
    setBusy(true);
    try {
      const response = await (await bridgeReady()).extract_asset(parent, name.value.trim(), dump.value, original);
      if (response?.error) {
        message(response.error);
        return;
      }
      onSaved(response, original);
      backdrop.classList.remove('show');
    } catch (caught) {
      message(caught.message || String(caught));
    } finally {
      setBusy(false);
    }
  });

  return {
    open(selected) {
      if (busy || picking) return;
      revision++;
      entry = selected;
      name.value = selected.asset ? selected.name : '';
      dump.value = '';
      message();
      setBusy(false);
      backdrop.classList.add('show');
      name.focus();
      name.select();
    },
  };
}
