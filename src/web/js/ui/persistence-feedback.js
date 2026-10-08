// Report persistence failures without interrupting active editing dialogs.

import { t } from '../i18n/index.js';

export function initPersistenceFeedback() {
  window.addEventListener('viewer-persistence-error', () => {
    if (document.getElementById('persistence-feedback')) return;
    const notice = document.createElement('aside');
    notice.id = 'persistence-feedback';
    notice.setAttribute('role', 'alert');
    const message = document.createElement('span');
    message.dataset.i18n = 'errors.viewerPersistence';
    message.textContent = t('errors.viewerPersistence');
    const close = document.createElement('button');
    close.className = 'ui-button';
    close.dataset.i18n = 'common.close';
    close.textContent = t('common.close');
    close.addEventListener('click', () => notice.remove());
    notice.append(message, close);
    document.body.appendChild(notice);
  });
}
