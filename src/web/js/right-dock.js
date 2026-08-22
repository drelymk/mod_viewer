// The right side of the viewer has two intentionally separate destinations:
// selection details and mod controls.  Keeping the tab state here means the
// panel modules do not need to know how they are presented.

const STORAGE_KEY = 'mod-viewer.right-dock-tab';

let activeTab = 'controls';
let ready = false;

function elements() {
  return {
    inspectorTab: document.getElementById('inspector-tab'),
    controlsTab: document.getElementById('controls-tab'),
    inspector: document.getElementById('inspector-panel'),
    controls: document.getElementById('controls-panel'),
    dock: document.getElementById('right-dock'),
  };
}

export function setRightDockTab(tab, { persist = true } = {}) {
  if (tab !== 'controls' && tab !== 'inspector') return;
  activeTab = tab;
  const { inspectorTab, controlsTab, inspector, controls } = elements();
  const inspectorActive = tab === 'inspector';
  inspectorTab?.classList.toggle('active', inspectorActive);
  controlsTab?.classList.toggle('active', !inspectorActive);
  inspectorTab?.setAttribute('aria-selected', String(inspectorActive));
  controlsTab?.setAttribute('aria-selected', String(!inspectorActive));
  if (inspector) inspector.hidden = !inspectorActive;
  if (controls) controls.hidden = inspectorActive;
  if (persist) {
    try { localStorage.setItem(STORAGE_KEY, tab); } catch (_) { /* private mode */ }
  }
}

export function getRightDockTab() {
  return activeTab;
}

export function setRightDockVisible(visible) {
  elements().dock?.classList.toggle('ui-visible', !!visible);
}

export function initRightDock() {
  const { inspectorTab, controlsTab } = elements();
  if (!inspectorTab || !controlsTab) return;
  inspectorTab.addEventListener('click', () => setRightDockTab('inspector'));
  controlsTab.addEventListener('click', () => setRightDockTab('controls'));
  if (!ready) {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === 'controls' || stored === 'inspector') activeTab = stored;
    } catch (_) { /* private mode */ }
    ready = true;
  }
  setRightDockTab(activeTab, { persist: false });
  setRightDockVisible(false);
}
