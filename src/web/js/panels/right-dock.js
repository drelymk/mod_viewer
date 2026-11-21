// Right-dock navigation keeps the user's preferred tab separate from the
// transient choice to have the dock open at all.
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

const STORAGE_KEY = 'mod-viewer.right-dock-tab';
const VALID_TABS = Object.freeze(['controls', 'inspector', 'weight-rig']);

let selectedTab = 'controls';
let openTab = null;
let dockEnabled = false;
let userHasChosenDockState = false;
let ready = false;
let lastNotifiedTab = null;

function validTab(tab) {
  return VALID_TABS.includes(tab);
}

function elements() {
  return {
    tabs: Object.fromEntries(VALID_TABS.map((tab) => [tab, document.getElementById(`${tab}-tab`)])),
    panes: Object.fromEntries(VALID_TABS.map((tab) => [tab, document.getElementById(`${tab}-panel`)])),
    dock: document.getElementById('right-dock'),
  };
}

function notifyTabChange(activeTab) {
  if (activeTab === lastNotifiedTab) return;
  lastNotifiedTab = activeTab;
  window.dispatchEvent(
    new CustomEvent('mod-viewer-right-dock-tab-changed', {
      detail: { tab: activeTab, open: !!activeTab },
    }),
  );
}

function syncCollapseButton(open) {
  const button = document.getElementById('right-dock-collapse');
  if (!button) return;
  const label = t(open ? 'panels.hideRight' : 'panels.showRight');
  button.setAttribute('aria-expanded', String(open));
  button.setAttribute('aria-controls', `${selectedTab}-panel`);
  button.setAttribute('aria-label', label);
  button.title = label;
  button.querySelector('use')?.setAttribute('href', open ? '#icon-chevron-right' : '#icon-chevron-left');
}

function renderRightDock() {
  const { tabs, panes, dock } = elements();
  const activeTab = dockEnabled && validTab(openTab) ? openTab : null;
  VALID_TABS.forEach((tab) => {
    const active = activeTab === tab;
    tabs[tab]?.classList.toggle('active', active);
    tabs[tab]?.setAttribute('aria-selected', String(active));
    tabs[tab]?.setAttribute('aria-expanded', String(active));
    if (panes[tab]) panes[tab].hidden = !active;
  });
  dock?.classList.toggle('ui-visible', dockEnabled);
  document.body?.classList.toggle('right-dock-mounted', dockEnabled);
  document.body?.classList.toggle('right-dock-visible', !!activeTab);
  syncCollapseButton(!!activeTab);
  notifyTabChange(activeTab);
}

export function setRightDockTab(tab, { persist = true, userInitiated = false } = {}) {
  if (!validTab(tab)) return false;
  selectedTab = tab;
  openTab = tab;
  if (userInitiated) userHasChosenDockState = true;
  if (persist) {
    try {
      localStorage.setItem(STORAGE_KEY, tab);
    } catch {
      /* private mode */
    }
  }
  renderRightDock();
  return true;
}

export function toggleRightDockTab(tab) {
  if (!validTab(tab)) return false;
  userHasChosenDockState = true;
  if (openTab === tab) {
    openTab = null;
  } else {
    selectedTab = tab;
    openTab = tab;
    try {
      localStorage.setItem(STORAGE_KEY, tab);
    } catch {
      /* private mode */
    }
  }
  renderRightDock();
  return true;
}

export function getRightDockTab() {
  return selectedTab;
}

export function isRightDockOpen() {
  return dockEnabled && validTab(openTab);
}

export function toggleRightDock() {
  if (!dockEnabled) return false;
  userHasChosenDockState = true;
  openTab = validTab(openTab) ? null : selectedTab;
  renderRightDock();
  return isRightDockOpen();
}

export function openRightDock() {
  return isRightDockOpen() || toggleRightDock();
}

export function setRightDockEnabled(enabled) {
  dockEnabled = !!enabled;
  if (dockEnabled && !userHasChosenDockState && !openTab) openTab = selectedTab;
  renderRightDock();
}

export function initRightDock() {
  const { tabs } = elements();
  if (!tabs.controls || !tabs.inspector || !tabs['weight-rig']) return;
  VALID_TABS.forEach((tab) => tabs[tab].addEventListener('click', () => toggleRightDockTab(tab)));
  if (!ready) {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      const migrated = stored === 'weight' || stored === 'rig' ? 'weight-rig' : stored;
      if (validTab(migrated)) {
        selectedTab = migrated;
        if (migrated !== stored) localStorage.setItem(STORAGE_KEY, migrated);
      }
    } catch {
      /* private mode */
    }
    document.getElementById('right-dock-collapse')?.addEventListener('click', () => toggleRightDock());
    window.addEventListener(LANGUAGE_CHANGED, renderRightDock);
    ready = true;
  }
  renderRightDock();
}
