// Navigation state for the Meshes, Mod Library and Assets panels.
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';

export const LEFT_TABS = new Set(['meshes', 'mod-library', 'assets']);

let activeTab = null;
let lastTab = null;
let meshesAvailable = false;
let userHasChosenDockState = false;
let ready = false;

function elements() {
  const tabs = [...document.querySelectorAll('[data-left-tab]')];
  const panels = Object.fromEntries(
    tabs.map((tab) => [tab.dataset.leftTab, document.getElementById(tab.getAttribute('aria-controls'))]),
  );
  return { dock: document.getElementById('left-dock'), tabs, panels };
}

function tabCanOpen(tab) {
  return tab !== 'meshes' || meshesAvailable;
}

function syncCollapseButton(visible) {
  const button = document.getElementById('left-dock-collapse');
  if (!button) return;
  const label = t(visible ? 'panels.hideLeft' : 'panels.showLeft');
  button.setAttribute('aria-expanded', String(visible));
  button.setAttribute('aria-label', label);
  button.title = label;
  button.querySelector('use')?.setAttribute('href', visible ? '#icon-chevron-left' : '#icon-chevron-right');
}

function render() {
  const { dock, tabs, panels } = elements();
  const visible = !!activeTab && tabCanOpen(activeTab);
  if (visible) lastTab = activeTab;
  syncCollapseButton(visible);
  tabs.forEach((tab) => {
    const name = tab.dataset.leftTab;
    const active = visible && name === activeTab;
    const disabled = name === 'meshes' && !meshesAvailable;
    tab.disabled = disabled;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
    tab.setAttribute('aria-expanded', String(active));
    tab.setAttribute('aria-disabled', String(disabled));
  });
  Object.entries(panels).forEach(([name, panel]) => {
    if (!panel) return;
    const shown = visible && name === activeTab;
    panel.hidden = !shown;
    panel.inert = !shown;
    panel.setAttribute('aria-hidden', String(!shown));
  });
  dock?.classList.toggle('ui-visible', visible);
}

export function setLeftDockTab(tab, { userInitiated = true } = {}) {
  if (!LEFT_TABS.has(tab) || !tabCanOpen(tab)) return false;
  activeTab = tab;
  if (userInitiated) userHasChosenDockState = true;
  render();
  return true;
}

export function toggleLeftDockTab(tab) {
  if (!LEFT_TABS.has(tab) || !tabCanOpen(tab)) return false;
  userHasChosenDockState = true;
  activeTab = activeTab === tab ? null : tab;
  render();
  return true;
}

export function getLeftDockTab() {
  return activeTab;
}

export function isLeftDockOpen() {
  return !!activeTab && tabCanOpen(activeTab);
}

export function toggleLeftDock() {
  userHasChosenDockState = true;
  if (isLeftDockOpen()) activeTab = null;
  else activeTab = lastTab && tabCanOpen(lastTab) ? lastTab : meshesAvailable ? 'meshes' : 'mod-library';
  render();
  return isLeftDockOpen();
}

export function openLeftDock() {
  if (!isLeftDockOpen()) toggleLeftDock();
  return isLeftDockOpen();
}

export function setMeshesAvailable(value) {
  meshesAvailable = !!value;
  render();
  if (meshesAvailable && !userHasChosenDockState && !activeTab) {
    activeTab = 'meshes';
    render();
  }
}

export function initLeftDock() {
  if (ready) {
    render();
    return;
  }
  const { tabs } = elements();
  tabs.forEach((tab) =>
    tab.addEventListener('click', () => {
      toggleLeftDockTab(tab.dataset.leftTab);
    }),
  );
  document.getElementById('left-dock-collapse')?.addEventListener('click', () => {
    toggleLeftDock();
  });
  window.addEventListener(LANGUAGE_CHANGED, render);
  ready = true;
  render();
}
