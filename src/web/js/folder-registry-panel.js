// Shared lazy tree rendering for the Mod Library and Assets registries.

import { createIcon } from './ui-icons.js';

function canonicalPath(path) {
  return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

function setTextError(element, message) {
  if (!element) return;
  element.textContent = message || '';
  element.classList.toggle('show', !!message);
}

export function createFolderRegistryPanel({
  listElement,
  emptyElement,
  errorElement,
  listChildren,
  onRootSelected,
  onChildSelected,
  onEdit,
  onDelete,
  renderLabel,
  classPrefix = 'folder',
}) {
  const childCache = new Map();
  let roots = [];
  let activePath = null;

  const className = suffix => `${classPrefix}-${suffix} folder-${suffix}`;
  const selector = suffix => `.${classPrefix}-${suffix}`;
  const pathAttribute = `data-${classPrefix}-path`;

  function closeMenus(except = null) {
    listElement.querySelectorAll(selector('action-menu')).forEach(menu => {
      if (menu !== except) {
        menu.hidden = true;
        menu.parentElement?.querySelector(`:scope > ${selector('more')}`)
          ?.setAttribute('aria-expanded', 'false');
      }
    });
  }

  document.addEventListener('click', event => {
    if (!event.target.closest(selector('actions'))) closeMenus();
  });

  function setActivePath(path) {
    activePath = canonicalPath(path);
    listElement.querySelectorAll(`[${pathAttribute}]`).forEach(row => {
      const rowPath = canonicalPath(row.getAttribute(pathAttribute));
      row.classList.toggle('active', rowPath === activePath);
      row.classList.toggle('active-descendant', !!activePath && rowPath !== activePath
        && activePath.startsWith(`${rowPath}/`));
    });
  }

  function renderChildren(node, children) {
    const childList = node.querySelector(`:scope > ${selector('children')}`);
    childList.innerHTML = '';
    node.querySelector(`:scope > ${selector('row')} ${selector('expand')}`)
      .classList.toggle('leaf', children.length === 0 && !node.classList.contains('expanded'));
    children.forEach(child => childList.appendChild(createNode(child, false)));
  }

  async function expandNode(node, path, arrow) {
    const childList = node.querySelector(`:scope > ${selector('children')}`);
    if (node.classList.contains('expanded')) {
      node.classList.remove('expanded');
      arrow.classList.remove('expanded');
      childList.hidden = true;
      arrow.classList.toggle('leaf', childCache.get(canonicalPath(path))?.length === 0);
      arrow.setAttribute('aria-expanded', 'false');
      arrow.setAttribute('aria-label', `Expand ${arrow.dataset.folderName}`);
      return;
    }

    node.classList.add('expanded');
    arrow.classList.add('expanded');
    childList.hidden = false;
    arrow.setAttribute('aria-expanded', 'true');
    arrow.setAttribute('aria-label', `Collapse ${arrow.dataset.folderName}`);
    const key = canonicalPath(path);
    if (childCache.has(key)) {
      renderChildren(node, childCache.get(key));
      return;
    }

    childList.innerHTML = '';
    const loading = document.createElement('div');
    loading.className = className('child-error');
    loading.textContent = 'Loading...';
    childList.appendChild(loading);
    try {
      const response = await listChildren(path);
      if (response?.error) throw new Error(response.error);
      const children = Array.isArray(response) ? response : response?.folders || [];
      childCache.set(key, children);
      renderChildren(node, children);
    } catch (caught) {
      childList.innerHTML = '';
      const message = document.createElement('div');
      message.className = className('child-error');
      message.textContent = caught.message || String(caught);
      childList.appendChild(message);
    }
  }

  function appendLabel(select, entry, isRoot) {
    const label = renderLabel?.(entry, isRoot);
    if (label instanceof Node) select.appendChild(label);
    else select.textContent = label ?? entry.name ?? entry.path;
  }

  function createNode(entry, isRoot) {
    const node = document.createElement('div');
    node.className = className('node');
    const row = document.createElement('div');
    row.className = className('row');
    row.setAttribute(pathAttribute, entry.path);
    if (entry.exists === false) row.classList.add('missing');

    const arrow = document.createElement('button');
    arrow.type = 'button';
    arrow.className = className('expand');
    arrow.textContent = '›';
    arrow.dataset.folderName = entry.name || entry.path;
    arrow.setAttribute('aria-label', `Expand ${arrow.dataset.folderName}`);
    arrow.setAttribute('aria-expanded', 'false');
    arrow.addEventListener('click', event => {
      event.stopPropagation();
      void expandNode(node, entry.path, arrow);
    });

    const select = document.createElement('button');
    select.type = 'button';
    select.className = className('select');
    appendLabel(select, entry, isRoot);
    select.title = entry.path;
    select.addEventListener('click', event => {
      event.stopPropagation();
      const callback = isRoot ? onRootSelected : onChildSelected;
      callback?.(entry.path, entry);
    });
    row.append(arrow, select);

    if (isRoot && (onEdit || onDelete)) {
      const actions = document.createElement('span');
      actions.className = className('actions');
      const more = document.createElement('button');
      more.type = 'button';
      more.className = className('more');
      more.appendChild(createIcon('more'));
      more.title = 'More folder actions';
      more.setAttribute('aria-label', `More actions for ${entry.name || entry.path}`);
      more.setAttribute('aria-haspopup', 'menu');
      more.setAttribute('aria-expanded', 'false');
      const menu = document.createElement('span');
      menu.className = className('action-menu');
      menu.setAttribute('role', 'menu');
      menu.hidden = true;
      if (onEdit) {
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.setAttribute('role', 'menuitem');
        edit.textContent = 'Edit';
        edit.addEventListener('click', event => {
          event.stopPropagation();
          menu.hidden = true;
          more.setAttribute('aria-expanded', 'false');
          onEdit(entry);
        });
        menu.appendChild(edit);
      }
      if (onDelete) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.setAttribute('role', 'menuitem');
        remove.textContent = 'Remove';
        remove.addEventListener('click', event => {
          event.stopPropagation();
          menu.hidden = true;
          more.setAttribute('aria-expanded', 'false');
          onDelete(entry);
        });
        menu.appendChild(remove);
      }
      more.addEventListener('click', event => {
        event.stopPropagation();
        closeMenus(menu);
        menu.hidden = !menu.hidden;
        more.setAttribute('aria-expanded', String(!menu.hidden));
      });
      actions.append(more, menu);
      row.appendChild(actions);
    }

    const children = document.createElement('div');
    children.className = className('children');
    children.hidden = true;
    node.append(row, children);
    if (entry.exists === false && isRoot) {
      const missing = document.createElement('div');
      missing.className = className('missing');
      missing.append(createIcon('diagnostics'), document.createTextNode('Folder not found'));
      node.appendChild(missing);
    }
    return node;
  }

  function render(entries) {
    roots = entries || [];
    listElement.innerHTML = '';
    listElement.hidden = roots.length === 0;
    roots.forEach(entry => listElement.appendChild(createNode(entry, true)));
    if (emptyElement) emptyElement.hidden = roots.length !== 0;
    setActivePath(activePath);
  }

  function applyResponse(response) {
    if (response?.error) {
      childCache.clear();
      render([]);
      setTextError(errorElement, response.error);
      return false;
    }
    childCache.clear();
    setTextError(errorElement, '');
    render(response?.folders || []);
    return true;
  }

  return {
    render,
    applyResponse,
    setActivePath,
    clearCache: () => childCache.clear(),
    getRoots: () => roots.slice(),
  };
}
