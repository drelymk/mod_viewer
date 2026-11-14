import { bridgeReady } from '../app/bridge.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { bindModalDismiss, setModalError } from '../ui/modal-shell.js';

export function initRigExtractionDialog({ onSaved }) {
  const $ = (name) => document.getElementById('rig-extract-' + name);
  const backdrop = $('backdrop');
  let entry = null;
  let game = null;
  let job = null;
  let state = null;
  let busy = false;
  let picking = false;
  let revision = 0;
  let support = null;
  let supportError = false;

  function labels() {
    $('availability').textContent = t(
      supportError
        ? 'gameExtract.supportFailed'
        : support === null
          ? 'common.loading'
          : support.extraction
            ? 'gameExtract.available'
            : 'gameExtract.unavailable',
    );
    $('ok').textContent = t(busy ? 'asset.extracting' : 'common.ok');
    $('progress').textContent =
      busy && state?.phase
        ? t('rigExtract.phase.' + state.phase) + (state.total ? ` (${state.current}/${state.total})` : '')
        : '';
  }
  function controls() {
    $('browse').disabled = busy || picking;
    $('ok').disabled = busy || picking || !support?.extraction;
    $('query').disabled = busy;
    $('cancel').disabled = picking || (busy && state?.phase === 'install');
    labels();
  }
  function close() {
    if (busy) return;
    revision++;
    backdrop.classList.remove('show');
  }
  bindModalDismiss({ backdrop, close });
  window.addEventListener(LANGUAGE_CHANGED, labels);
  $('cancel').addEventListener('click', async () => {
    if (!busy) return close();
    if (job && state?.phase !== 'install') {
      try {
        await (await bridgeReady()).cancel_asset_rig_extraction(job);
      } catch (error) {
        setModalError($('error'), error.message);
      }
    }
  });
  $('browse').addEventListener('click', async () => {
    if (busy || picking) return;
    const request = revision;
    picking = true;
    controls();
    try {
      const selected = await (await bridgeReady()).select_game_folder(entry.asset_type);
      if (request !== revision) return;
      if (selected?.error) setModalError($('error'), selected.error);
      else if (selected?.token) {
        game = selected;
        $('game').value = selected.path;
        setModalError($('error'));
      }
    } catch (error) {
      if (request === revision) setModalError($('error'), error.message);
    } finally {
      picking = false;
      controls();
    }
  });
  $('form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy || picking || !support?.extraction) return;
    if (!game || !$('query').value.trim()) {
      setModalError($('error'), t('rigExtract.required'));
      return;
    }
    busy = true;
    state = { phase: 'prepare' };
    controls();
    setModalError($('error'));
    try {
      const api = await bridgeReady();
      const started = await api.start_asset_rig_extraction(entry.path, game.token, $('query').value.trim());
      if (started?.error || !started?.job_id) throw new Error(started?.error || t('rigExtract.failed'));
      job = started.job_id;
      while (true) {
        state = await api.get_asset_rig_extraction(job);
        controls();
        if (state.status !== 'running') break;
        await new Promise((resolve) => setTimeout(resolve, 400));
      }
      if (state.status !== 'complete') throw new Error(state.error || t('rigExtract.failed'));
      onSaved(state, entry.path);
      backdrop.classList.remove('show');
    } catch (error) {
      setModalError($('error'), error.message);
    } finally {
      busy = false;
      job = null;
      controls();
    }
  });
  return {
    open(selected) {
      if (busy || picking) return;
      revision++;
      entry = selected;
      game = null;
      state = null;
      support = null;
      supportError = false;
      $('asset').value = selected.path;
      $('asset').title = selected.path;
      $('query').value = selected.name || selected.path.split(/[\\/]/).pop();
      $('game').value = '';
      setModalError($('error'));
      controls();
      backdrop.classList.add('show');
      $('asset').scrollLeft = $('asset').scrollWidth;
      $('browse').focus();
      const request = revision;
      (async () => {
        try {
          const api = await bridgeReady();
          const capabilities = await api.get_game_extraction_support();
          if (request !== revision) return;
          support = capabilities[selected.asset_type] || { extraction: false };
          controls();
          const saved = await api.get_saved_game_folder?.(selected.asset_type);
          if (request === revision && saved?.token && !game) {
            game = saved;
            $('game').value = saved.path;
          }
        } catch {
          if (request === revision && support === null) {
            supportError = true;
            controls();
          }
        }
      })();
    },
  };
}
