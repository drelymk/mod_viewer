// Read-only preview of in-game menu slots that drive meshes without [Key...] bindings.

import { refreshAll, setToggleValue, getToggleValue } from '../mesh/visibility.js';
import { registerViewSync, syncView } from '../scene/view-sync.js';
import { buildSourceSection, groupKeysBySource, usesSourceSections } from '../ui/panel-utils.js';
import { createIcon } from '../ui/ui-icons.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { compareValues } from '../editing/conditions.js';
import { renderDDSPreview } from '../textures/dds-preview.js';

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
  return compareValues(cur, when.op, when.value);
}

let imageObserver = null;
let imageGeneration = 0;

function loadMenuImage(image) {
  const url = image.dataset.menuSrc;
  delete image.dataset.menuSrc;
  if (!url || !image.isConnected) return;
  if (image.tagName === 'CANVAS') {
    const generation = imageGeneration;
    void renderDDSPreview(image, url, () => image.isConnected && generation === imageGeneration).catch(() => {
      if (image.isConnected && generation === imageGeneration) image.dispatchEvent(new Event('error'));
    });
  } else image.src = url;
}

function createMenuImage(url, name) {
  const nativeDDS = /\.dds(?:[?#]|$)/i.test(url);
  const image = document.createElement(nativeDDS ? 'canvas' : 'img');
  if (nativeDDS) {
    image.setAttribute('role', 'img');
    image.setAttribute('aria-label', name);
  } else {
    image.alt = name;
    image.loading = 'lazy';
    image.decoding = 'async';
  }
  return image;
}

function queueMenuImage(image, url) {
  image.dataset.menuSrc = url;
  if (!imageObserver) {
    queueMicrotask(() => loadMenuImage(image));
    return;
  }
  imageObserver.observe(image);
}

function buildMenuItem(info) {
  const item = document.createElement('div');
  item.className = 'menu-item';

  const btn = document.createElement('button');
  btn.className = 'toggle-cycle-btn';
  btn.appendChild(createIcon('cycle'));
  const syncLabel = () => {
    btn.title = t('menu.cycle', {
      variable: displayName(info.var),
      slot: info.slot,
    });
  };
  syncLabel();
  if (info.image) {
    btn.classList.add('menu-image-btn');
    const img = createMenuImage(info.image, info.name);
    img.addEventListener('error', () => btn.replaceChildren(createIcon('cycle')));
    queueMenuImage(img, info.image);
    btn.replaceChildren(img);
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
  return {
    item,
    sync: () => {
      syncLabel();
      valSpan.textContent = getToggleValue(info.var);
    },
  };
}

function buildShapeSlider(info) {
  const item = document.createElement('div');
  item.className = 'menu-item menu-slider-item';
  const nameSpan = document.createElement('span');
  nameSpan.className = 'menu-name';
  nameSpan.textContent = info.name;
  if (info.image) {
    const img = createMenuImage(info.image, info.name);
    img.className = 'menu-slider-image';
    img.addEventListener('error', () => img.remove());
    queueMenuImage(img, info.image);
    item.appendChild(img);
  }
  const input = document.createElement('input');
  input.type = 'range';
  input.className = 'menu-slider';
  input.min = info.min;
  input.max = info.max;
  input.step = info.step;
  input.value = getToggleValue(info.var) ?? info.default;
  const valSpan = document.createElement('span');
  valSpan.className = 'menu-value';
  valSpan.textContent = Number(input.value).toFixed(2);
  input.addEventListener('input', () => {
    setToggleValue(info.var, input.value);
    refreshAll();
  });
  item.append(nameSpan, input, valSpan);
  return {
    item,
    sync: () => {
      const value = getToggleValue(info.var);
      if (value === undefined) return;
      input.value = value;
      valSpan.textContent = Number(input.value).toFixed(2);
    },
  };
}

// Cycling one slot can change another slot's variable via a mutual-exclusion
// rule, so every displayed value is re-read after any click.
let syncers = [];
export function refreshMenuValues() {
  syncView('menu-panel');
}

window.addEventListener(LANGUAGE_CHANGED, refreshMenuValues);

/**
 * Build the panel from the structured controls.menu model. Hidden entirely when the
 * mod has no clickable menu, which is the common case.
 */
export function buildMenuPanel(menu) {
  const list = document.getElementById('menu-list');
  const panel = document.getElementById('menu-panel');
  imageObserver?.disconnect();
  imageGeneration += 1;
  imageObserver =
    typeof IntersectionObserver === 'function'
      ? new IntersectionObserver(
          (entries, observer) => {
            for (const entry of entries) {
              if (!entry.isIntersecting) continue;
              loadMenuImage(entry.target);
              observer.unobserve(entry.target);
            }
          },
          { rootMargin: '80px' },
        )
      : null;
  list.innerHTML = '';
  syncers = [];
  registerViewSync('menu-panel', () => {
    syncers.forEach((sync) => sync());
  });

  const keys = Object.keys(menu || {});
  if (!keys.length) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';

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
    const container = multiSource && src ? buildSourceSection(src, list) : list;
    for (const key of bySource[src]) {
      const { item, sync } = menu[key].kind === 'shape_slider' ? buildShapeSlider(menu[key]) : buildMenuItem(menu[key]);
      syncers.push(sync);
      container.appendChild(item);
    }
  }
}
