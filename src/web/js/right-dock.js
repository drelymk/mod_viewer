// Right-dock navigation keeps the user's preferred tab separate from the
// transient choice to have the dock open at all.

const STORAGE_KEY = 'mod-viewer.right-dock-tab';

let selectedTab = 'controls';
let openTab = null;
let dockEnabled = false;
let userHasChosenDockState = false;
let ready = false;

function validTab(tab) {
  return tab === 'controls' || tab === 'inspector';
}

function elements() {
  return {
    inspectorTab: document.getElementById('inspector-tab'),
    controlsTab: document.getElementById('controls-tab'),
    inspector: document.getElementById('inspector-panel'),
    controls: document.getElementById('controls-panel'),
    dock: document.getElementById('right-dock'),
  };
}

function renderRightDock() {
  const { inspectorTab, controlsTab, inspector, controls, dock } = elements();
  const panelVisible = dockEnabled && validTab(openTab);
  const inspectorActive = panelVisible && openTab === 'inspector';
  const controlsActive = panelVisible && openTab === 'controls';
  inspectorTab?.classList.toggle('active', inspectorActive);
  controlsTab?.classList.toggle('active', controlsActive);
  inspectorTab?.setAttribute('aria-selected', String(inspectorActive));
  controlsTab?.setAttribute('aria-selected', String(controlsActive));
  inspectorTab?.setAttribute('aria-expanded', String(inspectorActive));
  controlsTab?.setAttribute('aria-expanded', String(controlsActive));
  if (inspector) inspector.hidden = !inspectorActive;
  if (controls) controls.hidden = !controlsActive;
  dock?.classList.toggle('ui-visible', dockEnabled);
  document.body?.classList.toggle('right-dock-visible', panelVisible);
}

export function setRightDockTab(tab, { persist = true, userInitiated = false } = {}) {
  if (!validTab(tab)) return false;
  selectedTab = tab;
  openTab = tab;
  if (userInitiated) userHasChosenDockState = true;
  if (persist) {
    try { localStorage.setItem(STORAGE_KEY, tab); } catch (_) { /* private mode */ }
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
    try { localStorage.setItem(STORAGE_KEY, tab); } catch (_) { /* private mode */ }
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

export function setRightDockEnabled(enabled) {
  dockEnabled = !!enabled;
  if (dockEnabled && !userHasChosenDockState && !openTab) openTab = selectedTab;
  renderRightDock();
}

export function initRightDock() {
  const { inspectorTab, controlsTab } = elements();
  if (!inspectorTab || !controlsTab) return;
  inspectorTab.addEventListener('click', () => toggleRightDockTab('inspector'));
  controlsTab.addEventListener('click', () => toggleRightDockTab('controls'));
  if (!ready) {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (validTab(stored)) selectedTab = stored;
    } catch (_) { /* private mode */ }
    ready = true;
  }
  renderRightDock();
}
