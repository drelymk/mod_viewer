// Experimental inferred Rig/Pose panel.  It exposes source-scoped IDs and
// evidence only; it never edits Weight/Physics selection state.

import {
  beginRigPicking, cancelRigPicking, ensureModelRigLoaded,
  getModelRigState, resetRigBone, resetRigPose, selectRigBone,
  setActiveRigSource, setRigComponentRoot, setRigVisible,
} from '../mesh/weight-experiment.js';

let panel = null;
let ui = null;
let loadingPromise = null;
let latestState = null;

const $ = id => document.getElementById(id);

function addText(parent, className, value = '') {
  const node = document.createElement('span');
  node.className = className;
  node.textContent = value;
  parent.appendChild(node);
  return node;
}

function section(title) {
  const node = document.createElement('section');
  node.className = 'rig-section';
  addText(node, 'rig-section-title', title);
  panel.appendChild(node);
  return node;
}

function selectedSource(state = latestState) {
  return (state?.sources || []).find(source =>
    source.sourceKey === state.activeSourceKey) || state?.sources?.[0] || null;
}

function componentFor(source, boneId) {
  if (boneId === null || boneId === undefined
      || !Number.isInteger(Number(boneId))) return null;
  return source?.components?.find(component =>
    component.nodeIds.includes(Number(boneId))) || null;
}

function sourceLabel(source) {
  const file = String(source?.sourceFile || source?.sourceKey || 'Source');
  return `${file} · offset +${Number(source?.boneIdOffset) || 0}`;
}

function buildPanel() {
  panel.replaceChildren();
  ui = {};
  const header = document.createElement('div');
  header.className = 'rig-header';
  const heading = document.createElement('h3');
  heading.textContent = 'RIG';
  header.appendChild(heading);
  ui.status = addText(header, 'rig-status');
  panel.appendChild(header);

  const display = section('Display');
  const visibleLabel = document.createElement('label');
  visibleLabel.className = 'weight-checkbox';
  ui.visible = document.createElement('input');
  ui.visible.type = 'checkbox';
  ui.visible.addEventListener('change', () => setRigVisible(ui.visible.checked));
  visibleLabel.appendChild(ui.visible);
  addText(visibleLabel, 'weight-label', 'Show inferred rig');
  display.appendChild(visibleLabel);

  const sourceSection = section('Source');
  ui.source = document.createElement('select');
  ui.source.className = 'rig-source-select';
  ui.source.setAttribute('aria-label', 'Rig source');
  ui.source.addEventListener('change', () => setActiveRigSource(ui.source.value));
  sourceSection.appendChild(ui.source);

  const boneSection = section('Selected Bone');
  ui.bone = document.createElement('select');
  ui.bone.className = 'rig-bone-select';
  ui.bone.setAttribute('aria-label', 'Selected rig bone');
  ui.bone.addEventListener('change', () => {
    const source = selectedSource();
    if (source) selectRigBone(source.sourceKey, Number(ui.bone.value));
  });
  boneSection.appendChild(ui.bone);
  ui.influences = document.createElement('div');
  ui.influences.className = 'rig-influences';
  boneSection.appendChild(ui.influences);
  ui.pick = document.createElement('button');
  ui.pick.type = 'button';
  ui.pick.className = 'ui-button rig-pick-model';
  ui.pick.textContent = 'Pick from model';
  ui.pick.addEventListener('click', () => {
    if (latestState?.picking) cancelRigPicking();
    else beginRigPicking();
  });
  boneSection.appendChild(ui.pick);

  const componentSection = section('Component');
  const rootRow = document.createElement('div');
  rootRow.className = 'rig-row';
  addText(rootRow, 'rig-label', 'Root');
  ui.root = addText(rootRow, 'rig-value');
  componentSection.appendChild(rootRow);
  const depthRow = document.createElement('div');
  depthRow.className = 'rig-row';
  addText(depthRow, 'rig-label', 'Depth');
  ui.depth = addText(depthRow, 'rig-value');
  componentSection.appendChild(depthRow);
  ui.setRoot = document.createElement('button');
  ui.setRoot.type = 'button';
  ui.setRoot.className = 'ui-button rig-set-root';
  ui.setRoot.textContent = 'Set selected as root';
  ui.setRoot.addEventListener('click', () => {
    const source = selectedSource();
    if (source) setRigComponentRoot(source.sourceKey, Number(ui.bone.value));
  });
  componentSection.appendChild(ui.setRoot);

  const poseSection = section('Pose');
  addText(poseSection, 'rig-hint',
    'Rotate the selected non-root joint with the viewport gizmo. Pose is source-local and is not saved.');
  ui.resetBone = document.createElement('button');
  ui.resetBone.type = 'button';
  ui.resetBone.className = 'ui-button rig-reset-bone';
  ui.resetBone.textContent = 'Reset Bone';
  ui.resetBone.addEventListener('click', () => {
    const source = selectedSource();
    if (source) resetRigBone(source.sourceKey, Number(ui.bone.value));
  });
  ui.resetPose = document.createElement('button');
  ui.resetPose.type = 'button';
  ui.resetPose.className = 'ui-button rig-reset-pose';
  ui.resetPose.textContent = 'Reset Pose';
  ui.resetPose.addEventListener('click', () => resetRigPose());
  const actions = document.createElement('div');
  actions.className = 'rig-actions';
  actions.append(ui.resetBone, ui.resetPose);
  poseSection.appendChild(actions);
}

function syncOptions(state) {
  const source = selectedSource(state);
  const sourceKey = JSON.stringify((state.sources || []).map(item => [
    item.sourceKey, item.boneIds, item.sourceFile, item.boneIdOffset,
  ]));
  if (sourceKey !== ui.source.dataset.optionKey) {
    ui.source.replaceChildren();
    (state.sources || []).forEach(item => {
      const option = document.createElement('option');
      option.value = item.sourceKey;
      option.textContent = sourceLabel(item);
      ui.source.appendChild(option);
    });
    ui.source.dataset.optionKey = sourceKey;
  }
  if (source) ui.source.value = source.sourceKey;
  const boneKey = JSON.stringify(source?.boneIds || []);
  if (boneKey !== ui.bone.dataset.optionKey) {
    ui.bone.replaceChildren();
    (source?.boneIds || []).forEach(id => {
      const option = document.createElement('option');
      option.value = String(id);
      option.textContent = String(id);
      ui.bone.appendChild(option);
    });
    ui.bone.dataset.optionKey = boneKey;
  }
  const selectedId = source?.selectedBoneId ?? state.selectedBoneId;
  if (selectedId !== null && selectedId !== undefined) {
    ui.bone.value = String(selectedId);
  }
  ui.influences.replaceChildren();
  const picked = state.pickedPoint?.sourceKey === source?.sourceKey
    ? state.pickedPoint : null;
  if (picked) {
    picked.influences.forEach(influence => {
      const label = document.createElement('label');
      label.className = 'rig-influence';
      label.classList.toggle('selected', Number(selectedId) === influence.boneId);
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'rig-picked-influence';
      radio.checked = Number(selectedId) === influence.boneId;
      radio.addEventListener('change', () =>
        selectRigBone(source.sourceKey, influence.boneId));
      label.append(radio);
      addText(label, 'rig-influence-id', String(influence.boneId));
      addText(label, 'rig-influence-weight',
        `${Math.round(Number(influence.weight) * 100)}%`);
      ui.influences.appendChild(label);
    });
  }
  const component = componentFor(source, selectedId);
  ui.root.textContent = component ? String(component.rootId) : '—';
  ui.depth.textContent = component
    ? String(component.depthById?.[selectedId] ?? '—') : '—';
}

function syncPanel(state = getModelRigState()) {
  if (!ui) return;
  latestState = state;
  if (state.loading) ui.status.textContent = 'Inferring source-local rig…';
  else if (state.error) ui.status.textContent = state.error;
  else if (!state.loaded) ui.status.textContent = 'Open this panel to load authored weights.';
  else if (!state.sources?.length) ui.status.textContent = 'No usable Blend skinning data found.';
  else ui.status.textContent = `${state.sources.length} source rig${state.sources.length === 1 ? '' : 's'}`;
  if (state.pickStatus) ui.status.textContent = state.pickStatus;
  syncOptions(state);
  ui.visible.checked = !!state.visible;
  ui.visible.disabled = !state.loaded || !state.sources?.length;
  ui.pick.disabled = !state.loaded || !state.sources?.length;
  ui.pick.classList.toggle('active', !!state.picking);
  ui.pick.textContent = state.picking ? 'Cancel picking' : 'Pick from model';
  const source = selectedSource(state);
  const physicsActive = !!source?.physicsActive;
  const hasSelectedBone = state.selectedBoneId !== null
    && state.selectedBoneId !== undefined;
  ui.setRoot.disabled = !source || physicsActive || !hasSelectedBone;
  ui.resetBone.disabled = !source || physicsActive || !hasSelectedBone;
  ui.resetPose.disabled = !state.loaded || physicsActive;
}

function loadOnDemand() {
  if (loadingPromise) return loadingPromise;
  loadingPromise = ensureModelRigLoaded().finally(() => { loadingPromise = null; });
  return loadingPromise;
}

export function initRigPanel() {
  panel = $('rig-panel');
  if (!panel) return;
  buildPanel();
  window.addEventListener('mod-viewer-model-rig-changed', event =>
    syncPanel(event.detail));
  window.addEventListener('mod-viewer-right-dock-tab-changed', event => {
    if (event.detail?.tab === 'rig' && event.detail?.open) void loadOnDemand();
    if (event.detail?.tab !== 'rig' && latestState?.picking) cancelRigPicking();
  });
  syncPanel();
}
