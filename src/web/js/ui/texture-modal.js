// Edit a component's shared texture pool; changes persist to viewer metadata, not INI files.

import { addTexture } from '../mesh/mesh-factory.js';
import { bindModalDismiss } from './modal-shell.js';
import { textureDisplayLabel, textureFile } from '../textures/texture-key.js';
import { createIcon } from './ui-icons.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

const $ = (id) => document.getElementById(id);

let currentPool = null; // the shared array for the open component
let currentTitle = '';
let currentModPath = '';
let currentTexturePicker = null;
let onChange = null; // re-render callback for every open per-mesh list
let currentError = null;

const mapColumns = [
  ['light_map', 'texture.lightMap'],
  ['normal_map', 'texture.normalMap'],
  ['material_map', 'texture.materialMap'],
];

function showError(result) {
  currentError = result;
  render();
}

async function pickInto(opt, field) {
  const result = currentTexturePicker
    ? await currentTexturePicker(field)
    : await window.pywebview.api.pick_texture_file(currentModPath, field);
  if (!result) return;
  if (result.error) return showError(result);
  addTexture(result.tex_key, result.uri);
  if (field === 'normal_map') {
    const transportRole = result.role;
    if (transportRole === 'normal_data') {
      delete opt.normal_map;
      delete opt.normal_map_manual;
      opt.normal_data = result.tex_key;
      opt.normal_data_manual = true;
    } else {
      opt.normal_map = result.tex_key;
      opt.normal_map_manual = true;
      delete opt.normal_data;
      delete opt.normal_data_manual;
    }
  } else {
    opt[field] = result.tex_key;
    opt[`${field}_manual`] = true;
  }
  currentError = null;
  render();
  if (onChange) onChange();
}

function render() {
  $('texm-title').textContent = t('texture.manageTitle', { name: currentTitle });
  const list = $('texm-list');
  list.innerHTML = '';
  if (currentError) {
    const err = document.createElement('div');
    err.className = 'texm-empty';
    err.setAttribute('role', 'alert');
    err.textContent =
      currentError.error_code === 'texture_load_failed'
        ? t('texture.loadFailed', { file: currentError.file || t('texture.unknown') })
        : currentError.error;
    list.appendChild(err);
  }
  const header = document.createElement('div');
  header.className = 'texm-grid texm-header';
  for (const key of ['texture.diffuse', 'texture.lightMap', 'texture.normalMap', 'texture.materialMap', null]) {
    const cell = document.createElement('span');
    cell.textContent = key ? t(key) : '';
    header.appendChild(cell);
  }
  list.appendChild(header);
  if (!currentPool || !currentPool.length) {
    const empty = document.createElement('div');
    empty.className = 'texm-empty';
    empty.textContent = t('texture.noTextures');
    list.appendChild(empty);
  }
  for (const opt of currentPool || []) {
    const row = document.createElement('div');
    row.className = 'texm-row texm-grid';
    const label = document.createElement('span');
    label.className = 'texm-diffuse';
    label.textContent = textureDisplayLabel(opt.tex_key, opt.label);
    label.title = opt.file || opt.tex_key;
    row.appendChild(label);
    for (const [field, titleKey] of mapColumns) {
      const title = t(titleKey);
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'texm-map-cell';
      const displayKey = field === 'normal_map' ? opt.normal_map || opt.normal_data : opt[field];
      const file = textureFile(displayKey);
      cell.title = displayKey ? t('texture.replace', { title, file }) : t('texture.add', { title });
      const name = document.createElement('span');
      name.textContent = file ? textureDisplayLabel(displayKey) : `+ ${title}`;
      cell.appendChild(name);
      cell.addEventListener('click', () => pickInto(opt, field));
      if (displayKey) {
        const clear = document.createElement('span');
        clear.className = 'texm-map-clear';
        clear.appendChild(createIcon('close'));
        clear.title = t('texture.remove', { title });
        clear.addEventListener('click', (evt) => {
          evt.stopPropagation();
          if (field === 'normal_map') {
            const hadNormalData = Object.hasOwn(opt, 'normal_data') || Object.hasOwn(opt, 'normal_data_manual');
            delete opt.normal_map;
            delete opt.normal_data;
            // Keep the clear authoritative against automatic component
            // propagation. The value is gone; this flag is only a
            // viewer-side tombstone until a new normal texture is assigned.
            opt.normal_map_manual = true;
            if (hadNormalData) opt.normal_data_manual = true;
          } else {
            delete opt[field];
            opt[`${field}_manual`] = true;
          }
          render();
          if (onChange) onChange();
        });
        cell.appendChild(clear);
      }
      row.appendChild(cell);
    }
    const del = document.createElement('button');
    del.className = 'toggle-icon-btn';
    del.appendChild(createIcon('delete'));
    del.title = t('texture.removeFromComponent');
    del.setAttribute('aria-label', t('texture.removeFromComponent'));
    del.addEventListener('click', () => {
      const idx = currentPool.indexOf(opt);
      if (idx !== -1) currentPool.splice(idx, 1);
      render();
      if (onChange) onChange();
    });
    row.appendChild(del);
    list.appendChild(row);
  }
}

/** Open a component's shared texture pool. Mutate `pool` in place so every
 * mesh sees changes; `onPoolChange` refreshes open per-mesh lists. */
export function openTextureModal(componentName, pool, modPath, onPoolChange, texturePicker = null) {
  currentPool = pool;
  currentTitle = componentName;
  currentModPath = modPath;
  currentTexturePicker = texturePicker;
  onChange = onPoolChange;
  currentError = null;
  render();
  $('texture-modal-backdrop').classList.add('show');

  $('texm-add').onclick = async () => {
    const result = texturePicker ? await texturePicker(null) : await window.pywebview.api.pick_texture_file(modPath);
    if (!result) return;
    if (result.error) {
      // This modal uses its list area for errors; it has no separate error region.
      return showError(result);
    }
    addTexture(result.tex_key, result.uri);
    const file = result.file || result.tex_key;
    const label = file
      .split('/')
      .pop()
      .replace(/\.[^.]+$/, '');
    currentPool.push({ tex_key: result.tex_key, file, label });
    currentError = null;
    render();
    if (onChange) onChange();
  };
}

function close() {
  $('texture-modal-backdrop').classList.remove('show');
  currentPool = null;
  currentModPath = '';
  currentTexturePicker = null;
  onChange = null;
  currentError = null;
}

bindModalDismiss({
  backdrop: $('texture-modal-backdrop'),
  close,
  buttons: [$('texm-close')],
});

window.addEventListener(LANGUAGE_CHANGED, () => {
  if (currentPool) render();
});
