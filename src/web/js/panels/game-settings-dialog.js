// Per-game settings dialog (default animation) opened from each Game directory row in Settings.

import { bridgeReady } from '../app/bridge.js';
import { setDefaultAnimations } from '../app/default-animation.js';
import { t } from '../i18n/index.js';
import { bindModalDismiss, setModalError } from '../ui/modal-shell.js';

const $ = (name) => document.getElementById('game-settings-' + name);

export function initGameSettingsDialog() {
  const backdrop = $('backdrop');
  const input = $('default-animation');
  let kind = null;
  let busy = false;
  const hide = () => backdrop.classList.remove('show');
  const close = () => {
    if (!busy) hide();
  };
  bindModalDismiss({ backdrop, close, buttons: [$('cancel')] });

  const save = async () => {
    if (busy) return;
    busy = true;
    $('save').disabled = true;
    setModalError($('error'));
    try {
      const result = await (await bridgeReady()).set_game_default_animation(kind, input.value);
      if (result.error) {
        setModalError($('error'), result.error);
      } else {
        setDefaultAnimations(result.value);
        hide();
      }
    } catch (error) {
      setModalError($('error'), error.message);
    } finally {
      busy = false;
      $('save').disabled = false;
    }
  };
  $('save').addEventListener('click', save);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      save();
    }
  });

  return {
    open(gameKind, currentName) {
      kind = gameKind;
      $('title').textContent = t('gameSettings.title', { game: kind });
      setModalError($('error'));
      input.value = currentName;
      backdrop.classList.add('show');
      input.focus();
      input.select();
    },
  };
}
