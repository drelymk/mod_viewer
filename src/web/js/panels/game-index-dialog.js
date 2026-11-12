// Confirm-and-run dialog for building the game metadata index. Used after a game
// directory is chosen in Settings and by the Settings index button.

import { bridgeReady } from '../app/bridge.js';
import { t } from '../i18n/index.js';
import { bindModalDismiss, setModalError } from '../ui/modal-shell.js';

const $ = (name) => document.getElementById('game-index-' + name);

export function initGameIndexDialog() {
  let job = '';
  let busy = false;
  let finish = null;

  const controls = () => {
    $('ok').disabled = busy;
    $('cancel').disabled = false;
  };
  const close = async () => {
    if (busy && job) {
      const api = await bridgeReady();
      await api.cancel_game_index(job).catch(() => {});
      return;
    }
    $('backdrop').classList.remove('show');
    const done = finish;
    finish = null;
    done?.(false);
  };
  bindModalDismiss({ backdrop: $('backdrop'), close, buttons: [$('cancel')] });

  const poll = async (api) => {
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 400));
      const state = await api.get_game_index_job(job);
      if (state.error && !state.status) throw new Error(state.error);
      if (state.status === 'running') {
        $('progress').textContent = t('gameIndex.running') + (state.total ? ` (${state.current}/${state.total})` : '');
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
    let indexed = false;
    try {
      const api = await bridgeReady();
      const started = await api.start_game_index();
      if (started.error) throw new Error(started.error);
      job = started.job_id;
      const state = await poll(api);
      if (state.status === 'complete') indexed = true;
      else if (state.status === 'error') setModalError($('error'), state.error || t('gameIndex.failed'));
      else if (state.status === 'cancelled') {
        $('backdrop').classList.remove('show');
        const done = finish;
        finish = null;
        done?.(false);
      }
    } catch (error) {
      setModalError($('error'), error.message || t('gameIndex.failed'));
    } finally {
      busy = false;
      job = '';
      $('progress').textContent = '';
      controls();
    }
    if (indexed) {
      $('backdrop').classList.remove('show');
      const done = finish;
      finish = null;
      done?.(true);
    }
  });

  // Resolves true when the index was built, false when the dialog was dismissed or failed.
  return {
    open() {
      finish?.(false);
      setModalError($('error'));
      $('progress').textContent = '';
      busy = false;
      controls();
      $('backdrop').classList.add('show');
      $('ok').focus();
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  };
}
