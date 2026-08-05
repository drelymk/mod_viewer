// The Menu panel (right, under Toggle): the slots of a mod's own in-game
// clickable menu — mods that drive their meshes from an on-screen menu
// instead of [Key...] bindings (see core/ini_menu.py).
//
// Read-only: slots can be cycled to preview what they show, but nothing here
// edits, records or exports.

import { refreshAll, setToggleValue, getToggleValue } from './visibility.js';

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
  setToggleValue(info.var, info.default);

  const item = document.createElement('div');
  item.className = 'menu-item';

  const btn = document.createElement('button');
  btn.className = 'toggle-cycle-btn';
  btn.textContent = '⟳';
  btn.title = `Cycle $${displayName(info.var)} (menu slot ${info.slot})`;

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
    refreshValues();
  });

  item.append(btn, nameSpan, valSpan);
  return { item, sync: () => { valSpan.textContent = getToggleValue(info.var); } };
}

// Cycling one slot can change another slot's variable via a mutual-exclusion
// rule, so every displayed value is re-read after any click.
let syncers = [];
function refreshValues() {
  syncers.forEach((fn) => fn());
}

function buildSourceSection(source, container) {
  const hdr = document.createElement('div');
  hdr.className = 'toggle-src-hdr';

  const chevron = document.createElement('span');
  chevron.className = 'group-toggle';
  chevron.textContent = '▼';

  const nameSpan = document.createElement('span');
  nameSpan.className = 'group-name';
  nameSpan.textContent = source;

  hdr.append(chevron, nameSpan);

  const itemsWrap = document.createElement('div');
  itemsWrap.className = 'toggle-src-items';

  hdr.addEventListener('click', () => {
    chevron.classList.toggle('collapsed');
    itemsWrap.classList.toggle('collapsed');
  });

  container.append(hdr, itemsWrap);
  return itemsWrap;
}

/**
 * Build the panel from the payload's __menu__ model. Hidden entirely when the
 * mod has no clickable menu, which is the common case.
 */
export function buildMenuPanel(menu) {
  const list = document.getElementById('menu-list');
  const panel = document.getElementById('menu-panel');
  list.innerHTML = '';
  syncers = [];

  const keys = Object.keys(menu || {});
  if (!keys.length) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';

  const bySource = {};
  for (const key of keys) {
    const src = menu[key].source || '';
    (bySource[src] = bySource[src] || []).push(key);
  }
  const sources = Object.keys(bySource);
  const multiSource = sources.length > 1 || (sources.length === 1 && sources[0] !== '');

  for (const src of sources) {
    const container = (multiSource && src) ? buildSourceSection(src, list) : list;
    for (const key of bySource[src]) {
      const { item, sync } = buildMenuItem(menu[key]);
      syncers.push(sync);
      container.appendChild(item);
    }
  }

  refreshAll();
}
