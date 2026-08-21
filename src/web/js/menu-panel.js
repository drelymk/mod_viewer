// The Menu panel (right, under Toggle): the slots of a mod's own in-game
// clickable menu — mods that drive their meshes from an on-screen menu
// instead of [Key...] bindings (see core/ini_menu.py).
//
// Read-only: slots can be cycled to preview what they show, but nothing here
// edits, records or exports.

import { refreshAll, setToggleValue, getToggleValue } from './visibility.js';
import { registerViewSync, syncView } from './view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from './panel-utils.js';
import { createIcon } from './ui-icons.js';

/** Variable names carry a "source::" prefix in multi-ini folders. */
function displayName(variable) {
  return variable.split('::').pop();
}

/** True if one of a slot's mutual-exclusion rules should fire, given the
 * value its variable holds right now. */
function guardHolds(when) {
  if (!when) return true;
  const cur = getToggleValue(when.var);
  if (cur === undefined) return false;
  switch (when.op) {
    case '==': return cur === when.value;
    case '!=': return cur !== when.value;
    case '>':  return Number(cur) >  Number(when.value);
    case '<':  return Number(cur) <  Number(when.value);
    case '>=': return Number(cur) >= Number(when.value);
    case '<=': return Number(cur) <= Number(when.value);
    default:   return false;
  }
}

function buildMenuItem(info) {
  const item = document.createElement('div');
  item.className = 'menu-item';

  const btn = document.createElement('button');
  btn.className = 'toggle-cycle-btn';
  btn.appendChild(createIcon('cycle'));
  btn.title = `Cycle $${displayName(info.var)} (menu slot ${info.slot})`;
  if (info.image_slot) {
    btn.classList.add('menu-image-btn');
    btn.replaceChildren();
  }
  if (info.image) {
    const img = document.createElement('img');
    img.src = info.image;
    img.alt = info.name;
    btn.appendChild(img);
  }

  const nameSpan = document.createElement('span');
  nameSpan.className = 'menu-name';
  nameSpan.textContent = info.name;

  const valSpan = document.createElement('span');
  valSpan.className = 'menu-value';
  valSpan.textContent = getToggleValue(info.var);

  btn.addEventListener('click', () => {
    const idx = info.values.indexOf(getToggleValue(info.var));
    setToggleValue(info.var, info.values[(idx + 1) % info.values.length]);
    // The game applies these right after the click, in source order, so a
    // later rule sees what an earlier one wrote.
    for (const e of info.effects || []) {
      if (guardHolds(e.when)) setToggleValue(e.var, e.value);
    }
    refreshAll();
  });

  item.append(btn, nameSpan, valSpan);
  return { item, sync: () => { valSpan.textContent = getToggleValue(info.var); } };
}

function buildShapeSlider(info) {
  const item = document.createElement('div');
  item.className = 'menu-item menu-slider-item';
  const nameSpan = document.createElement('span');
  nameSpan.className = 'menu-name';
  nameSpan.textContent = info.name;
  if (info.image) {
    const img = document.createElement('img');
    img.className = 'menu-slider-image';
    img.src = info.image;
    img.alt = info.name;
    item.appendChild(img);
  }
  const input = document.createElement('input');
  input.type = 'range';
  input.className = 'menu-slider';
  input.min = info.min;
  input.max = info.max;
  input.step = info.step;
  input.value = info.default;
  const valSpan = document.createElement('span');
  valSpan.className = 'menu-value';
  valSpan.textContent = Number(input.value).toFixed(2);
  input.addEventListener('input', () => {
    setToggleValue(info.var, input.value);
    refreshAll();
  });
  item.append(nameSpan, input, valSpan);
  return { item, sync: () => {
    const value = getToggleValue(info.var);
    if (value === undefined) return;
    input.value = value;
    valSpan.textContent = Number(input.value).toFixed(2);
  } };
}

// Cycling one slot can change another slot's variable via a mutual-exclusion
// rule, so every displayed value is re-read after any click.
let syncers = [];
export function refreshMenuValues() {
  syncView('menu-panel');
}

/**
 * Build the panel from the structured controls.menu model. Hidden entirely when the
 * mod has no clickable menu, which is the common case.
 */
export function buildMenuPanel(menu) {
  const list = document.getElementById('menu-list');
  const panel = document.getElementById('menu-panel');
  list.innerHTML = '';
  syncers = [];
  registerViewSync('menu-panel', () => {
    syncers.forEach(sync => sync());
  });

  const keys = Object.keys(menu || {});
  if (!keys.length) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  // `image_slot` also counts authored-but-empty placeholder textures. Those
  // cells stay blank and clickable, and still belong to the mod's image grid.
  const imaged = keys.filter(key => menu[key].image || menu[key].image_slot).length;
  list.classList.toggle('image-layout', imaged >= 2 && imaged / keys.length >= 0.6);

  // Register every menu variable before the first refresh. Derived [Present]
  // rules often depend on several sibling controls; refreshing while the list
  // is only half-built makes uninitialized clauses fail open and can latch the
  // wrong branch outputs (notably WWMI qipao combinations).
  for (const key of keys) {
    const info = menu[key];
    setToggleValue(info.var, String(info.default));
  }

  const bySource = groupKeysBySource(menu, keys);
  const sources = Object.keys(bySource);
  const multiSource = usesSourceSections(bySource);

  for (const src of sources) {
    const container = (multiSource && src) ? buildSourceSection(src, list) : list;
    for (const key of bySource[src]) {
      const { item, sync } = menu[key].kind === 'shape_slider'
        ? buildShapeSlider(menu[key])
        : buildMenuItem(menu[key]);
      syncers.push(sync);
      container.appendChild(item);
    }
  }

  refreshAll();
}
