// Per-component "manage textures" popup: lists a component's shared texture
// pool (viewer-only -- see core/mesh_builder.py's
// texture_options), lets the user add an existing file from the mod folder
// or remove an option. Changes persist in .mod_viewer.json, never in the ini.

import { addTexture } from './mesh-factory.js';

const $ = (id) => document.getElementById(id);

let currentPool = null;   // the shared array for the open component
let currentTitle = '';
let onChange = null;      // re-render callback for every open per-mesh list

function render() {
  $('texm-title').textContent = `Manage Textures — ${currentTitle}`;
  const list = $('texm-list');
  list.innerHTML = '';
  if (!currentPool || !currentPool.length) {
    const empty = document.createElement('div');
    empty.className = 'texm-empty';
    empty.textContent = 'No textures yet.';
    list.appendChild(empty);
  }
  for (const opt of (currentPool || [])) {
    const row = document.createElement('div');
    row.className = 'texm-row';
    const label = document.createElement('span');
    label.textContent = opt.label;
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
    row.append(label, del);
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
      const err = document.createElement('div');
      err.className = 'texm-empty';
      err.textContent = result.error;
      $('texm-list').prepend(err);
      return;
    }
    addTexture(result.tex_key, result.uri);
    const label = result.tex_key.split('/').pop().replace(/\.[^.]+$/, '');
    currentPool.push({ tex_key: result.tex_key, label });
    render();
    if (onChange) onChange();
  };
}

function close() {
  $('texture-modal-backdrop').classList.remove('show');
  currentPool = null;
  onChange = null;
}

$('texm-close').addEventListener('click', close);
$('texture-modal-backdrop').addEventListener('click', (evt) => {
  if (evt.target.id === 'texture-modal-backdrop') close();
});
document.addEventListener('keydown', (evt) => {
  if (evt.key === 'Escape' && $('texture-modal-backdrop').classList.contains('show')) close();
});
