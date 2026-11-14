// Confirm-and-run dialog for building the game metadata index. Used after a game
// directory is chosen in Settings and by the Settings index button.

import { bridgeReady } from '../app/bridge.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { bindModalDismiss, setModalError } from '../ui/modal-shell.js';

const $ = (name) => document.getElementById('game-index-' + name);

export function initGameIndexDialog() {
  let job = '';
  let busy = false;
  let resolveDialog = null;
  let kind = null;
  let state = null;
  const labels = () => {
    $('progress').textContent = busy
      ? t('gameIndex.running') + (state?.total ? ` (${state.current}/${state.total})` : '')
      : '';
  };
  window.addEventListener(LANGUAGE_CHANGED, labels);

  const controls = () => {
    $('ok').disabled = busy;
    $('cancel').disabled = busy && !job;
  };
  const finish = (indexed = false) => {
    $('backdrop').classList.remove('show');
    const resolve = resolveDialog;
    resolveDialog = null;
    resolve?.(indexed);
  };
  const close = async () => {
    if (busy) {
      if (job) {
        const api = await bridgeReady();
        await api.cancel_game_index(job).catch(() => {});
      }
      return;
    }
    finish();
  };
  bindModalDismiss({ backdrop: $('backdrop'), close, buttons: [$('cancel')] });

  const poll = async (api) => {
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 400));
      state = await api.get_game_index_job(job);
      if (state.error && !state.status) throw new Error(state.error);
      if (state.status === 'running') {
        labels();
        continue;
      }
      return state;
    }
  };

  $('ok').addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    controls();
    setModalError($('error'));
    $('progress').textContent = t('gameIndex.running');
    try {
      const api = await bridgeReady();
      const started = await api.start_game_index(kind);
      if (started.error) throw new Error(started.error);
      if (!started.job_id) throw new Error(t('gameIndex.failed'));
      job = started.job_id;
      controls();
      const result = await poll(api);
      if (result.status === 'complete') finish(true);
      else if (result.status === 'cancelled') finish();
      else setModalError($('error'), result.error || t('gameIndex.failed'));
    } catch (error) {
      setModalError($('error'), error.message || t('gameIndex.failed'));
    } finally {
      busy = false;
      job = '';
      $('progress').textContent = '';
      controls();
    }
  });

  // Resolves when indexing completes or the dialog is dismissed; failures allow retry.
  return {
    open(gameKind) {
      if (busy) return Promise.resolve(false);
      kind = gameKind;
      state = null;
      resolveDialog?.(false);
      setModalError($('error'));
      $('progress').textContent = '';
      controls();
      $('backdrop').classList.add('show');
      $('ok').focus();
      return new Promise((resolve) => {
        resolveDialog = resolve;
      });
    },
  };
}
