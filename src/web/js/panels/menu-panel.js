// The Menu panel (right, under Toggle): the slots of a mod's own in-game
// clickable menu — mods that drive their meshes from an on-screen menu
// instead of [Key...] bindings (see core/ini/menu.py).
//
// Read-only: slots can be cycled to preview what they show, but nothing here
// edits, records or exports.

import { refreshAll, setToggleValue, getToggleValue } from '../mesh/visibility.js';
import { registerViewSync, syncView } from '../scene/view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from '../ui/panel-utils.js';
import { createIcon } from '../ui/ui-icons.js';
import { dnfSatisfied } from '../editing/control-state.js';

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
  const slotHint = info.slot === undefined || info.slot === null
    ? '' : ` (menu slot ${info.slot})`;
  btn.title = `Cycle $${displayName(info.var)}${slotHint}`;
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

function buildContinuousControl(info) {
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
  input.min = info.domain?.min ?? info.min ?? 0;
  input.max = info.domain?.max ?? info.max ?? 1;
  input.step = info.step ?? 0.01;
  input.value = getToggleValue(info.var) ?? info.default;
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

function nextAssignmentValue(assignment) {
  if (assignment.literal !== null && assignment.literal !== undefined) {
    return String(assignment.literal);
  }
  const target = assignment.target;
  const current = getToggleValue(target);
  if (assignment.exact_copy && assignment.dependencies?.length) {
    return getToggleValue(assignment.dependencies[0]);
  }
  if (assignment.cycle_values?.length) {
    const values = assignment.cycle_values.map(String);
    const index = values.indexOf(String(current));
    return values[(index + 1) % values.length];
  }

  const expression = String(assignment.expression || '').replace(/\s+/g, '');
  const numeric = Number(current);
  if (!Number.isFinite(numeric)) return null;
  let match = expression.match(/^\(\$[^)]+([+-])1\)%(\d+)$/i);
  if (match) {
    const amount = match[1] === '+' ? 1 : -1;
    return String((numeric + amount + Number(match[2])) % Number(match[2]));
  }
  match = expression.match(/^\$[^%]+%(\d+)$/i);
  if (match) return String(numeric % Number(match[1]));
  match = expression.match(/^\$[^)]+([+-])1\)?$/i);
  if (match) return String(numeric + (match[1] === '+' ? 1 : -1));
  match = expression.match(/^1-\$[^)]+$/i);
  if (match) return String(1 - numeric);
  return null;
}

export function applyAction(info) {
  if (!dnfSatisfied(info?.conditions)) return false;
  let changed = false;
  for (const assignment of info?.assignments || []) {
    if (!dnfSatisfied(assignment.conditions)) continue;
    const value = nextAssignmentValue(assignment);
    if (value === null || value === undefined) continue;
    setToggleValue(assignment.target, value);
    changed = true;
  }
  if (changed) refreshAll();
  return changed;
}

function buildActionItem(info) {
  const item = document.createElement('div');
  item.className = 'menu-item menu-action-item';
  const btn = document.createElement('button');
  btn.className = 'menu-action-btn';
  btn.appendChild(createIcon('cycle'));
  btn.title = `Run ${info.trigger || 'action'}`;
  const nameSpan = document.createElement('span');
  nameSpan.className = 'menu-name';
  nameSpan.textContent = info.trigger || 'Action';
  const valueSpan = document.createElement('span');
  valueSpan.className = 'menu-value';
  valueSpan.textContent = `${(info.assignments || []).length} writes`;
  btn.addEventListener('click', () => applyAction(info));
  item.append(btn, nameSpan, valueSpan);
  return { item, sync: () => {} };
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
export function buildMenuPanel(menu, actions = []) {
  const list = document.getElementById('menu-list');
  const panel = document.getElementById('menu-panel');
  list.innerHTML = '';
  syncers = [];
  registerViewSync('menu-panel', () => {
    syncers.forEach(sync => sync());
  });

  const keys = Object.keys(menu || {});
  if (!keys.length && !(actions || []).length) {
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
    if (getToggleValue(info.var) === undefined) {
      setToggleValue(info.var, String(info.default));
    }
  }

  const bySource = groupKeysBySource(menu, keys);
  const sources = Object.keys(bySource);
  const multiSource = usesSourceSections(bySource);

  for (const src of sources) {
    const container = (multiSource && src) ? buildSourceSection(src, list) : list;
    for (const key of bySource[src]) {
      const { item, sync } = menu[key].domain?.kind === 'continuous'
        || menu[key].kind === 'continuous'
        ? buildContinuousControl(menu[key])
        : buildMenuItem(menu[key]);
      syncers.push(sync);
      container.appendChild(item);
    }
  }

  for (const action of actions || []) {
    const { item, sync } = buildActionItem(action);
    syncers.push(sync);
    list.appendChild(item);
  }

}
