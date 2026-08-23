// Asset Folder registry UI. Character rows are browse-only in this phase.

import { confirmDialog } from './dialogs.js';
import { createFolderRegistryPanel } from './folder-registry-panel.js';

const $ = id => document.getElementById(id);
const ASSET_TYPES = ['ZZMI', 'GIMI', 'WWMI'];

function baseName(path) {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

function setTextError(element, message) {
  element.textContent = message || '';
  element.classList.toggle('show', !!message);
}

function isAssetMatchingEnabled(entry) {
  return entry?.enabled !== false;
}

export function initAssetFolderPanel() {
  const list = $('asset-folder-list');
  const error = $('asset-folder-error');
  const add = $('asset-folder-add');
  const empty = $('asset-folder-empty');
  const backdrop = $('asset-folder-modal-backdrop');
  const title = $('afm-title');
  const form = $('afm-form');
  const typeInput = $('afm-type');
  const pathInput = $('afm-path');
  const browse = $('afm-browse');
  const cancel = $('afm-cancel');
  const save = $('afm-save');
  const modalError = $('afm-error');

  let editorMode = 'add';
  let originalPath = null;
  let selectedPath = null;

  typeInput.replaceChildren(...ASSET_TYPES.map(value => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    return option;
  }));

  const tree = createFolderRegistryPanel({
    listElement: list,
    emptyElement: empty,
    errorElement: error,
    listChildren: path => window.pywebview.api.list_asset_subfolders(path),
    onRootSelected: path => tree.setActivePath(path),
    onChildSelected: path => tree.setActivePath(path),
    onEdit: entry => openEditor('edit', entry),
    onDelete: entry => removeFolder(entry),
    renderRootExtras: entry => {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'asset-folder-switch';
      toggle.setAttribute('role', 'switch');
      const enabled = isAssetMatchingEnabled(entry);
      toggle.textContent = enabled ? 'ON' : 'OFF';
      toggle.setAttribute('aria-checked', String(enabled));
      toggle.setAttribute(
        'aria-label', `Use ${baseName(entry.path)} for asset matching`);
      toggle.title = enabled
        ? 'Include this folder in asset matching'
        : 'Excluded from asset matching';
      toggle.addEventListener('click', async event => {
        event.stopPropagation();
        if (toggle.disabled) return;
        toggle.disabled = true;
        try {
          const response = await window.pywebview.api.set_asset_folder_enabled(
            entry.path, !isAssetMatchingEnabled(entry));
          if (response?.error) {
            setTextError(error, response.error);
            return;
          }
          applyRegistryResponse(response);
        } catch (caught) {
          setTextError(error, caught.message || String(caught));
        } finally {
          toggle.disabled = false;
        }
      });
      return toggle;
    },
    renderLabel: (entry, isRoot) => {
      if (!isRoot) return entry.name;
      const wrapper = document.createElement('span');
      wrapper.className = 'asset-folder-label';
      const badge = document.createElement('span');
      badge.className = 'asset-folder-badge';
      badge.textContent = entry.type;
      wrapper.append(badge, document.createTextNode(baseName(entry.path)));
      if (entry.enabled === false) {
        const status = document.createElement('span');
        status.className = 'asset-folder-status';
        status.textContent = 'Disabled';
        wrapper.appendChild(status);
      }
      return wrapper;
    },
    classPrefix: 'asset-folder',
  });

  function applyRegistryResponse(response) {
    return tree.applyResponse(response);
  }

  function closeEditor() {
    backdrop.classList.remove('show');
    setTextError(modalError, '');
  }

  function openEditor(mode, entry = null) {
    editorMode = mode;
    originalPath = entry?.path || null;
    selectedPath = entry?.path || null;
    title.textContent = mode === 'edit' ? 'Edit Asset Folder' : 'Add Asset Folder';
    save.textContent = mode === 'edit' ? 'Save' : 'Add';
    typeInput.value = entry?.type || 'GIMI';
    pathInput.value = entry?.path || '';
    setTextError(modalError, '');
    backdrop.classList.add('show');
    typeInput.focus();
  }

  function openAddDialog() {
    openEditor('add');
  }

  async function removeFolder(entry) {
    const confirmed = await confirmDialog(
      'Remove this Asset Folder?\n\n' +
      'This only removes it from Mod Viewer.\nFiles on disk will not be deleted.');
    if (!confirmed) return;
    const response = await window.pywebview.api.delete_asset_folder(entry.path);
    applyRegistryResponse(response);
  }

  add.addEventListener('click', openAddDialog);
  cancel.addEventListener('click', closeEditor);
  backdrop.addEventListener('click', event => {
    if (event.target === backdrop) closeEditor();
  });
  browse.addEventListener('click', async () => {
    const picked = await window.pywebview.api.select_asset_folder();
    if (!picked) return;
    selectedPath = picked;
    pathInput.value = picked;
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const path = selectedPath || pathInput.value.trim();
    if (!path) {
      setTextError(modalError, 'Choose a folder with Browse.');
      return;
    }
    save.disabled = true;
    try {
      const response = editorMode === 'edit'
        ? await window.pywebview.api.edit_asset_folder(
          originalPath, typeInput.value, path)
        : await window.pywebview.api.add_asset_folder(typeInput.value, path);
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

  const getAssetFolders = window.pywebview.api.get_asset_folders;
  if (typeof getAssetFolders === 'function') {
    getAssetFolders()
      .then(applyRegistryResponse)
      .catch(caught => setTextError(error, caught.message || String(caught)));
  } else {
    applyRegistryResponse({folders: []});
  }

  return {
    refresh: () => window.pywebview.api.get_asset_folders().then(applyRegistryResponse),
    openAddDialog,
    setActivePath: tree.setActivePath,
  };
}
