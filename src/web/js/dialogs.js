// Custom confirm/alert dialogs.

const $ = (id) => document.getElementById(id);

let resolveActive = null;

function close(result) {
  $('dialog-backdrop').classList.remove('show');
  const resolve = resolveActive;
  resolveActive = null;
  if (resolve) resolve(result);
}

function open(message, { cancelable } = {}) {
  return new Promise((resolve) => {
    resolveActive = resolve;
    $('dialog-message').textContent = message;
    $('dialog-cancel').style.display = cancelable ? '' : 'none';
    $('dialog-backdrop').classList.add('show');
    $('dialog-ok').focus();
  });
}

/** Drop-in replacement for window.alert() — resolves once dismissed. */
export function alertDialog(message) {
  return open(message, { cancelable: false }).then(() => {});
}

/** Drop-in replacement for window.confirm() — resolves true/false. */
export function confirmDialog(message) {
  return open(message, { cancelable: true });
}

$('dialog-ok').addEventListener('click', () => close(true));
$('dialog-cancel').addEventListener('click', () => close(false));
$('dialog-backdrop').addEventListener('click', (evt) => {
  if (evt.target.id === 'dialog-backdrop') close(false);
});
document.addEventListener('keydown', (evt) => {
  if (!$('dialog-backdrop').classList.contains('show')) return;
  if (evt.key === 'Escape') close(false);
});
