// Asset Folder registry UI. Category rows browse; indexed Asset rows load.

import { confirmDialog } from '../ui/dialogs.js';
import { createFolderRegistryPanel } from './folder-registry-panel.js';
import { createIcon } from '../ui/ui-icons.js';

const $ = id => document.getElementById(id);
const ASSET_TYPES = ['ZZMI', 'GIMI', 'WWMI'];

function baseName(path) {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

function canonicalPath(path) {
  return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

function setTextError(element, message) {
  element.textContent = message || '';
  element.classList.toggle('show', !!message);
}

function isAssetMatchingEnabled(entry) {
  return entry?.enabled !== false;
}

function indexSummary(entry) {
  const index = entry?.index || {};
  if (index.status === 'ready') {
    const skipped = index.skippedCount ? ` · ${index.skippedCount} skipped` : '';
    return `${index.assetCount || 0} assets · ${index.geometryHashCount || 0} hashes${skipped}`;
  }
  if (index.status === 'invalid') return 'Index invalid';
  return 'Index required';
}

export function initAssetFolderPanel({ switchAsset = null } = {}) {
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
  let editorBusy = false;

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
    onChildSelected: (path, entry) => {
      if (!entry?.asset) {
        tree.setActivePath(path);
        return;
      }
      if (typeof switchAsset !== 'function') return;
      void Promise.resolve(switchAsset(path, entry)).then(loaded => {
        if (loaded) tree.setActivePath(path);
      });
    },
    onEdit: entry => openEditor('edit', entry),
    onDelete: entry => removeFolder(entry),
    rootBusySelectors: ['switch', 'rebuild', 'more', 'edit', 'remove'],
    renderRootExtras: entry => {
      const tools = document.createElement('span');
      tools.className = 'asset-folder-tools';
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
        tree.setRootBusy(entry.path, true);
        try {
          const response = await window.pywebview.api.set_asset_folder_enabled(
            entry.path, !isAssetMatchingEnabled(entry));
          if (response?.error) {
            setTextError(error, response.error);
            return;
          }
          setTextError(error, '');
          const updated = (response?.folders || []).find(candidate =>
            canonicalPath(candidate.path) === canonicalPath(entry.path));
          if (!updated || !tree.updateRoot(updated)) applyRegistryResponse(response);
        } catch (caught) {
          setTextError(error, caught.message || String(caught));
        } finally {
          tree.setRootBusy(entry.path, false);
        }
      });
      tools.appendChild(toggle);

      const rebuild = document.createElement('button');
      rebuild.type = 'button';
      rebuild.className = 'asset-folder-rebuild';
      rebuild.appendChild(createIcon('rebuild'));
      rebuild.title = 'Rebuild asset index';
      rebuild.setAttribute('aria-label', `Rebuild asset index for ${baseName(entry.path)}`);
      rebuild.addEventListener('click', async event => {
        event.stopPropagation();
        if (rebuild.disabled) return;
        tree.setRootBusy(entry.path, true);
        try {
          const response = await window.pywebview.api.rebuild_asset_index(entry.path);
          if (response?.error) {
            const suffix = response.indexPreserved
              ? ' Previous index is still available.' : '';
            setTextError(error, `${response.error}${suffix}`);
            return;
          }
          setTextError(error, '');
          const updated = (response?.folders || []).find(candidate =>
            canonicalPath(candidate.path) === canonicalPath(entry.path));
          if (!updated || !tree.updateRoot(updated)) applyRegistryResponse(response);
        } catch (caught) {
          setTextError(error, caught.message || String(caught));
        } finally {
          tree.setRootBusy(entry.path, false);
        }
      });
      tools.appendChild(rebuild);
      return tools;
    },
    renderRootMeta: entry => {
      const meta = document.createElement('div');
      meta.className = 'asset-folder-index-status';
      meta.textContent = indexSummary(entry);
      return meta;
    },
    renderLabel: (entry, isRoot) => {
      if (!isRoot) return entry.name;
      const wrapper = document.createElement('span');
      wrapper.className = 'asset-folder-label';
      const badge = document.createElement('span');
      badge.className = 'asset-folder-badge';
      badge.textContent = entry.type;
      const name = document.createElement('span');
      name.className = 'asset-folder-name';
      name.textContent = baseName(entry.path);
      wrapper.append(badge, name);
      return wrapper;
    },
    classPrefix: 'asset-folder',
  });

  function applyRegistryResponse(response) {
    return tree.applyResponse(response);
  }

  function closeEditor(force = false) {
    if (editorBusy && !force) return;
    backdrop.classList.remove('show');
    setTextError(modalError, '');
  }

  function openEditor(mode, entry = null) {
    editorBusy = false;
    editorMode = mode;
    originalPath = entry?.path || null;
    selectedPath = entry?.path || null;
    title.textContent = mode === 'edit' ? 'Edit Asset Folder' : 'Add Asset Folder';
    save.textContent = mode === 'edit' ? 'Save' : 'Add';
    typeInput.value = entry?.type || ASSET_TYPES[0];
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
    const originalSaveText = editorMode === 'edit' ? 'Save' : 'Add';
    editorBusy = true;
    typeInput.disabled = true;
    pathInput.disabled = true;
    browse.disabled = true;
    cancel.disabled = true;
    save.disabled = true;
    save.textContent = 'Building index…';
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
      closeEditor(true);
    } catch (caught) {
      setTextError(modalError, caught.message || String(caught));
    } finally {
      editorBusy = false;
      typeInput.disabled = false;
      pathInput.disabled = false;
      browse.disabled = false;
      cancel.disabled = false;
      save.disabled = false;
      save.textContent = originalSaveText;
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