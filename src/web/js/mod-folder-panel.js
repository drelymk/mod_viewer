// Persistent Mod Folder registry and lazy directory navigator.

import { confirmDialog } from './dialogs.js';

const $ = id => document.getElementById(id);

function canonicalPath(path) {
  return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

function baseName(path) {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

function setTextError(element, message) {
  element.textContent = message || '';
  element.classList.toggle('show', !!message);
}

export function initModFolderPanel({ switchMod }) {
  const dock = $('mod-folder-dock');
  const panel = $('mod-folder-panel');
  const list = $('mod-folder-list');
  const error = $('mod-folder-error');
  const toggle = $('mod-folder-toggle');
  const close = $('mod-folder-close');
  const add = $('mod-folder-add');
  const backdrop = $('mod-folder-modal-backdrop');
  const title = $('mfm-title');
  const form = $('mfm-form');
  const nameInput = $('mfm-name');
  const pathInput = $('mfm-path');
  const browse = $('mfm-browse');
  const cancel = $('mfm-cancel');
  const save = $('mfm-save');
  const modalError = $('mfm-error');

  const childCache = new Map();
  let roots = [];
  let activePath = null;
  let editorMode = 'add';
  let originalPath = null;
  let selectedPath = null;

  function setExpanded(expanded) {
    dock.classList.toggle('expanded', expanded);
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded
      ? 'Close Mod Folders' : 'Open Mod Folders');
    toggle.textContent = expanded ? '◀' : '▶';
  }

  function setActivePath(path) {
    activePath = canonicalPath(path);
    list.querySelectorAll('[data-mod-folder-path]').forEach(row => {
      row.classList.toggle('active',
        canonicalPath(row.dataset.modFolderPath) === activePath);
    });
  }

  function renderChildren(node, children) {
    const childList = node.querySelector(':scope > .mod-folder-children');
    childList.innerHTML = '';
    node.querySelector(':scope > .mod-folder-row .mod-folder-expand')
      .classList.toggle('leaf', children.length === 0 &&
        !node.classList.contains('expanded'));
    children.forEach(child => childList.appendChild(createNode(child, false)));
  }

  async function expandNode(node, path, arrow) {
    const childList = node.querySelector(':scope > .mod-folder-children');
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
    loading.className = 'mod-folder-child-error';
    loading.textContent = 'Loading...';
    childList.appendChild(loading);
    try {
      const response = await window.pywebview.api.list_subfolders(path);
      if (response?.error) throw new Error(response.error);
      const children = response?.folders || [];
      childCache.set(key, children);
      renderChildren(node, children);
    } catch (caught) {
      childList.innerHTML = '';
      const message = document.createElement('div');
      message.className = 'mod-folder-child-error';
      message.textContent = caught.message || String(caught);
      childList.appendChild(message);
    }
  }

  async function selectFolder(path) {
    const loaded = await switchMod(path);
    if (!loaded) return;
    setActivePath(path);
  }

  function createNode(entry, isRoot) {
    const node = document.createElement('div');
    node.className = 'mod-folder-node';
    const row = document.createElement('div');
    row.className = 'mod-folder-row';
    row.dataset.modFolderPath = entry.path;
    if (entry.exists === false) row.classList.add('missing');

    const arrow = document.createElement('button');
    arrow.type = 'button';
    arrow.className = 'mod-folder-expand';
    arrow.textContent = '›';
    arrow.dataset.folderName = entry.name;
    arrow.setAttribute('aria-label', `Expand ${entry.name}`);
    arrow.setAttribute('aria-expanded', 'false');
    arrow.addEventListener('click', event => {
      event.stopPropagation();
      expandNode(node, entry.path, arrow);
    });

    const select = document.createElement('button');
    select.type = 'button';
    select.className = 'mod-folder-select';
    select.textContent = entry.name;
    select.title = entry.path;
    select.addEventListener('click', event => {
      event.stopPropagation();
      selectFolder(entry.path);
    });
    row.append(arrow, select);

    if (isRoot) {
      const actions = document.createElement('span');
      actions.className = 'mod-folder-actions';
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.textContent = '✎';
      edit.title = 'Edit Mod Folder';
      edit.setAttribute('aria-label', `Edit ${entry.name}`);
      edit.addEventListener('click', event => {
        event.stopPropagation();
        openEditor('edit', entry);
      });
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '🗑';
      remove.title = 'Remove from Mod Folders';
      remove.setAttribute('aria-label', `Remove ${entry.name}`);
      remove.addEventListener('click', event => {
        event.stopPropagation();
        removeFolder(entry);
      });
      actions.append(edit, remove);
      row.appendChild(actions);
    }

    const children = document.createElement('div');
    children.className = 'mod-folder-children';
    children.hidden = true;
    node.append(row, children);
    if (entry.exists === false && isRoot) {
      const missing = document.createElement('div');
      missing.className = 'mod-folder-missing';
      missing.textContent = 'Folder not found';
      node.appendChild(missing);
    }
    if (canonicalPath(entry.path) === activePath) row.classList.add('active');
    return node;
  }

  function renderRegistry(entries) {
    roots = entries || [];
    list.innerHTML = '';
    roots.forEach(entry => list.appendChild(createNode(entry, true)));
    setActivePath(activePath);
  }

  function applyRegistryResponse(response) {
    if (response?.error) {
      renderRegistry([]);
      setTextError(error, response.error);
      return false;
    }
    childCache.clear();
    setTextError(error, '');
    renderRegistry(response?.folders || []);
    return true;
  }

  function closeEditor() {
    backdrop.classList.remove('show');
    setTextError(modalError, '');
  }

  function openEditor(mode, entry = null) {
    editorMode = mode;
    originalPath = entry?.path || null;
    selectedPath = entry?.path || null;
    title.textContent = mode === 'edit' ? 'Edit Mod Folder' : 'Add Mod Folder';
    save.textContent = mode === 'edit' ? 'Save' : 'Add';
    nameInput.value = entry?.name || '';
    pathInput.value = entry?.path || '';
    setTextError(modalError, '');
    backdrop.classList.add('show');
    nameInput.focus();
  }

  async function removeFolder(entry) {
    const confirmed = await confirmDialog(
      `Remove "${entry.name}" from Mod Folders?\n\n` +
      'This only removes it from Mod Viewer.\nFiles on disk will not be deleted.');
    if (!confirmed) return;
    const response = await window.pywebview.api.delete_mod_folder(entry.path);
    applyRegistryResponse(response);
  }

  toggle.addEventListener('click', () => setExpanded(!dock.classList.contains('expanded')));
  close.addEventListener('click', () => setExpanded(false));
  add.addEventListener('click', () => openEditor('add'));
  cancel.addEventListener('click', closeEditor);
  backdrop.addEventListener('click', event => {
    if (event.target === backdrop) closeEditor();
  });
  browse.addEventListener('click', async () => {
    const picked = await window.pywebview.api.select_folder();
    if (!picked) return;
    selectedPath = picked;
    pathInput.value = picked;
    if (editorMode === 'add' && !nameInput.value.trim()) {
      nameInput.value = baseName(picked);
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const name = nameInput.value.trim();
    const path = selectedPath || pathInput.value.trim();
    if (!name) {
      setTextError(modalError, 'Enter a Mod Folder name.');
      return;
    }
    if (!path) {
      setTextError(modalError, 'Choose a folder with Browse.');
      return;
    }
    save.disabled = true;
    try {
      const response = editorMode === 'edit'
        ? await window.pywebview.api.edit_mod_folder(originalPath, name, path)
        : await window.pywebview.api.add_mod_folder(name, path);
      if (response?.error) {
        setTextError(modalError, response.error);
        return;
      }
      applyRegistryResponse(response);
      closeEditor();
    } catch (caught) {
      setTextError(modalError, caught.message || String(caught));
    } finally {
      save.disabled = false;
    }
  });

  window.addEventListener('mod-viewer-mod-loaded', event => {
    setActivePath(event.detail?.path);
  });

  window.pywebview.api.get_mod_folders()
    .then(applyRegistryResponse)
    .catch(caught => setTextError(error, caught.message || String(caught)));

  return {
    refresh: () => window.pywebview.api.get_mod_folders().then(applyRegistryResponse),
    setActivePath,
    setExpanded,
  };
}
