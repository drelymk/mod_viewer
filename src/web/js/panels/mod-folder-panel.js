// Mod Library registry and its feature-specific editor.

import { confirmDialog } from '../ui/dialogs.js';
import { createFolderRegistryPanel } from './folder-registry-panel.js';
import { t } from '../i18n/index.js';

const $ = id => document.getElementById(id);

function baseName(path) {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

function setTextError(element, message) {
  element.textContent = message || '';
  element.classList.toggle('show', !!message);
}

export function initModFolderPanel({ switchMod, onRegistryChanged }) {
  const list = $('mod-folder-list');
  const error = $('mod-folder-error');
  const add = $('mod-folder-add');
  const empty = $('mod-folder-empty');
  const backdrop = $('mod-folder-modal-backdrop');
  const title = $('mfm-title');
  const form = $('mfm-form');
  const nameInput = $('mfm-name');
  const pathInput = $('mfm-path');
  const browse = $('mfm-browse');
  const cancel = $('mfm-cancel');
  const save = $('mfm-save');
  const modalError = $('mfm-error');

  let editorMode = 'add';
  let originalPath = null;
  let selectedPath = null;

  const tree = createFolderRegistryPanel({
    listElement: list,
    emptyElement: empty,
    errorElement: error,
    listChildren: path => window.pywebview.api.list_subfolders(path),
    onRootSelected: path => selectFolder(path),
    onChildSelected: path => selectFolder(path),
    onEdit: entry => openEditor('edit', entry),
    onDelete: entry => removeFolder(entry),
    renderLabel: entry => entry.name,
    classPrefix: 'mod-folder',
  });

  function selectFolder(path) {
    return Promise.resolve(switchMod(path)).then(loaded => {
      if (loaded) tree.setActivePath(path);
      return loaded;
    });
  }

  function applyRegistryResponse(response) {
    const applied = tree.applyResponse(response);
    if (applied) onRegistryChanged?.(tree.getRoots().length > 0);
    else onRegistryChanged?.(false);
    return applied;
  }

  function closeEditor() {
    backdrop.classList.remove('show');
    setTextError(modalError, '');
  }

  function openEditor(mode, entry = null) {
    editorMode = mode;
    originalPath = entry?.path || null;
    selectedPath = entry?.path || null;
    title.textContent = mode === 'edit' ? t('folder.editMod') : t('folder.addMod');
    save.textContent = mode === 'edit' ? t('common.save') : t('common.add');
    nameInput.value = entry?.name || '';
    pathInput.value = entry?.path || '';
    setTextError(modalError, '');
    backdrop.classList.add('show');
    nameInput.focus();
  }

  function openAddDialog() {
    openEditor('add');
  }

  async function removeFolder(entry) {
    const confirmed = await confirmDialog(t('folder.removeMod', {name: entry.name}));
    if (!confirmed) return;
    const response = await window.pywebview.api.delete_mod_folder(entry.path);
    applyRegistryResponse(response);
  }

  add.addEventListener('click', openAddDialog);
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
      setTextError(modalError, t('folder.enterModName'));
      return;
    }
    if (!path) {
      setTextError(modalError, t('folder.chooseBrowse'));
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
    tree.setActivePath(event.detail?.path);
  });
  window.addEventListener('mod-viewer-mod-load-started', () => {
    tree.setActivePath(null);
  });

  window.pywebview.api.get_mod_folders()
    .then(applyRegistryResponse)
    .catch(caught => setTextError(error, caught.message || String(caught)));

  return {
    refresh: () => window.pywebview.api.get_mod_folders().then(applyRegistryResponse),
    setActivePath: tree.setActivePath,
    openAddDialog,
  };
}
