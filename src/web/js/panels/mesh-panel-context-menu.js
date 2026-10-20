import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

let menu = null;

export function closeMeshContextMenu() {
  if (!menu) return;
  menu.hidden = true;
  menu.replaceChildren();
}

window.addEventListener(LANGUAGE_CHANGED, () => {
  for (const button of menu?.children || []) button.textContent = t(button.dataset.i18n);
});

export function showMeshContextMenu(event, actions) {
  if (!menu) {
    menu = document.createElement('div');
    menu.className = 'mesh-context-menu';
    menu.setAttribute('role', 'menu');
    document.body.appendChild(menu);
    document.addEventListener('pointerdown', (event) => {
      if (!menu.contains(event.target)) closeMeshContextMenu();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeMeshContextMenu();
    });
  }
  menu.replaceChildren();
  for (const action of actions) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = action.className || 'mesh-edit-context-action';
    button.setAttribute('role', 'menuitem');
    button.dataset.i18n = action.key;
    button.textContent = t(action.key);
    button.hidden = !!action.hidden;
    button.disabled = !!action.disabled;
    button.title = action.reason ? t(action.reason) : '';
    button.addEventListener('click', () => {
      closeMeshContextMenu();
      void action.run();
    });
    menu.appendChild(button);
  }
  const hasVisibleAction = Array.from(menu.children).some(
    (button) =>
      !button.hidden && getComputedStyle(button).display !== 'none' && getComputedStyle(button).visibility !== 'hidden',
  );
  menu.hidden = !hasVisibleAction;
  if (menu.hidden) return;
  const container = document.getElementById('canvas-container') || document.body;
  const bounds = container.getBoundingClientRect();
  const left = Number(event.clientX);
  const top = Number(event.clientY);
  menu.style.left = `${Math.max(4, left)}px`;
  menu.style.top = `${Math.max(bounds.top + 4, top)}px`;
  requestAnimationFrame(() => {
    if (menu.hidden) return;
    const maxLeft = Math.max(4, window.innerWidth - menu.offsetWidth - 4);
    const maxTop = Math.max(bounds.top + 4, Math.min(window.innerHeight, bounds.bottom) - menu.offsetHeight - 4);
    menu.style.left = `${Math.min(Math.max(4, left), maxLeft)}px`;
    menu.style.top = `${Math.min(Math.max(bounds.top + 4, top), maxTop)}px`;
  });
}
