// Small shared behavior for ordinary modals. Ace and confirm dialogs remain
// specialized because their Escape handling has different semantics.

export function bindModalDismiss({ backdrop, close, buttons = [] }) {
  buttons.forEach(button => button.addEventListener('click', close));
  backdrop.addEventListener('click', event => {
    if (event.target === backdrop) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && backdrop.classList.contains('show')) close();
  });
}

export function setModalError(element, message = '') {
  element.textContent = message;
  element.style.display = message ? 'block' : 'none';
}