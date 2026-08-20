// Per-component "manage textures" popup: lists a component's shared texture
// pool (viewer-only -- see core/mesh_builder.py's
// texture_options), lets the user add an existing file from the mod folder
// or remove an option. Changes persist in .mod_viewer.json, never in the ini.

import { addTexture } from './mesh-factory.js';
import { bindModalDismiss } from './modal-shell.js';

const $ = (id) => document.getElementById(id);

let currentPool = null;   // the shared array for the open component
let currentTitle = '';
let currentModPath = '';
let onChange = null;      // re-render callback for every open per-mesh list

const mapColumns = [
  ['light_map', 'LightMap'],
  ['normal_map', 'NormalMap'],
  ['material_map', 'MaterialMap'],
];

function textureFile(key) {
  const value = String(key || '');
  const separator = value.indexOf('::');
  return separator === -1 ? value : value.slice(separator + 2);
}

function showError(message) {
  const err = document.createElement('div');
  err.className = 'texm-empty';
  err.textContent = message;
  $('texm-list').prepend(err);
}

async function pickInto(opt, field) {
  const result = await window.pywebview.api.pick_texture_file(currentModPath, field);
  if (!result) return;
  if (result.error) return showError(result.error);
  addTexture(result.tex_key, result.uri);
  opt[field] = result.tex_key;
  opt[`${field}_manual`] = true;
  if (field === 'normal_map') {
    if (result.normal_data_key) {
      addTexture(result.normal_data_key, result.normal_data_uri);
      opt.normal_data = result.normal_data_key;
      opt.normal_data_manual = true;
    } else {
      delete opt.normal_data;
      delete opt.normal_data_manual;
    }
  }
  render();
  if (onChange) onChange();
}

function render() {
  $('texm-title').textContent = `Manage Textures — ${currentTitle}`;
  const list = $('texm-list');
  list.innerHTML = '';
  const header = document.createElement('div');
  header.className = 'texm-grid texm-header';
  for (const text of ['Diffuse', 'LightMap', 'NormalMap', 'MaterialMap', '']) {
    const cell = document.createElement('span');
    cell.textContent = text;
    header.appendChild(cell);
  }
  list.appendChild(header);
  if (!currentPool || !currentPool.length) {
    const empty = document.createElement('div');
    empty.className = 'texm-empty';
    empty.textContent = 'No textures yet.';
    list.appendChild(empty);
  }
  for (const opt of (currentPool || [])) {
    const row = document.createElement('div');
    row.className = 'texm-row texm-grid';
    const label = document.createElement('span');
    label.className = 'texm-diffuse';
    label.textContent = opt.label;
    label.title = opt.file || opt.tex_key;
    row.appendChild(label);
    for (const [field, title] of mapColumns) {
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'texm-map-cell';
      const file = textureFile(opt[field]);
      cell.title = opt[field] ? `Replace ${title}: ${file}` : `Add ${title}`;
      const name = document.createElement('span');
      name.textContent = file
        ? file.split('/').pop().replace(/\.[^.]+$/, '')
        : `+ ${title}`;
      cell.appendChild(name);
      cell.addEventListener('click', () => pickInto(opt, field));
      if (opt[field]) {
        const clear = document.createElement('span');
        clear.className = 'texm-map-clear';
        clear.textContent = '×';
        clear.title = `Remove ${title}`;
        clear.addEventListener('click', (evt) => {
          evt.stopPropagation();
          delete opt[field];
          opt[`${field}_manual`] = true;
          if (field === 'normal_map') {
            delete opt.normal_data;
            delete opt.normal_data_manual;
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
    del.textContent = '🗑';
    del.title = 'Remove from this component\'s texture list';
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

/** Open the popup for one component. `pool` is the shared texture_options
 * array (same object reference every mesh in the component carries) --
 * mutated in place so add/remove is instantly reflected everywhere.
 * `onPoolChange` re-renders every open per-mesh texture list for this
 * component. */
export function openTextureModal(componentName, pool, modPath, onPoolChange) {
  currentPool = pool;
  currentTitle = componentName;
  currentModPath = modPath;
  onChange = onPoolChange;
  render();
  $('texture-modal-backdrop').classList.add('show');

  $('texm-add').onclick = async () => {
    const result = await window.pywebview.api.pick_texture_file(modPath);
    if (!result) return;
    if (result.error) {
      // Reuses the modal's own list area for feedback -- no separate error
      // box exists here, unlike the toggle modal, since this is a much
      // smaller, lower-stakes surface (view-only, nothing to lose on retry).
      return showError(result.error);
    }
    addTexture(result.tex_key, result.uri);
    const file = result.file || result.tex_key;
    const label = file.split('/').pop().replace(/\.[^.]+$/, '');
    currentPool.push({ tex_key: result.tex_key, file, label });
    render();
    if (onChange) onChange();
  };
}

function close() {
  $('texture-modal-backdrop').classList.remove('show');
  currentPool = null;
  currentModPath = '';
  onChange = null;
}

bindModalDismiss({
  backdrop: $('texture-modal-backdrop'),
  close,
  buttons: [$('texm-close')],
});
