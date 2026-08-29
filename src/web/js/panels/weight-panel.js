// Model-wide authored-weight controls. The panel owns one stable DOM tree so
// simulation and input events never interrupt an active picker or slider.

import {
  clearSelectedBoneIds, disableModelPhysics, enableModelPhysics,
  ensureModelWeightsLoaded, getModelPhysicsState, getModelWeightState,
  resetModelPhysicsMotion, setModelWeightHeatmap, setPhysicsConstraintsEnabled,
  setPhysicsContinuousLinearResponse, setPhysicsDamping,
  setPhysicsFrequency, setPhysicsGravityEnabled, setPhysicsGravityScale,
  setPhysicsLinearMotionStrength, setPhysicsMaxBendDegrees,
  setPhysicsMotionStrength, setSelectedBoneIds,
} from '../mesh/weight-experiment.js';

let panel = null;
let ui = null;
let loadingPromise = null;

const $ = id => document.getElementById(id);

function addText(parent, className, value) {
  const node = document.createElement('span');
  node.className = className;
  node.textContent = value;
  parent.appendChild(node);
  return node;
}

function selectedLabel(ids) {
  if (!ids.length) return 'Select bones';
  if (ids.length <= 4) return ids.join(', ');
  return `${ids.length} bones selected`;
}

function formatAffectedVertices(value) {
  const count = Math.max(0, Math.round(Number(value) || 0));
  if (count < 1000) return `${count.toLocaleString('en-US')} verts`;
  const compact = (count / 1000).toFixed(1).replace(/\.0$/, '');
  return `${compact}k verts`;
}

function formatBoneMeta(stats) {
  const average = Math.max(0, Number(stats?.averageInfluence) || 0);
  return `${formatAffectedVertices(stats?.affectedVertexCount)} · ${Math.round(average * 100)}%`;
}

function addRange(parent, className, label, min, max, step, value, onInput) {
  const field = document.createElement('label');
  field.className = 'weight-field';
  const header = document.createElement('div');
  header.className = 'weight-range-header';
  addText(header, 'weight-label', label);
  const valueNode = addText(header, 'weight-value', Number(value).toFixed(2));
  field.appendChild(header);
  const input = document.createElement('input');
  input.type = 'range';
  input.className = className;
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.value = String(value);
  input.addEventListener('input', () => {
    valueNode.textContent = Number(input.value).toFixed(2);
    onInput(input.value);
  });
  field.appendChild(input);
  parent.appendChild(field);
  return {input, valueNode};
}

function buildBonePicker(content) {
  const section = document.createElement('section');
  section.className = 'weight-section';
  const title = document.createElement('div');
  title.className = 'weight-section-title';
  title.textContent = 'Selected bones';
  section.appendChild(title);

  const picker = document.createElement('div');
  picker.className = 'weight-picker';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'ui-button weight-bone-select';
  button.setAttribute('aria-haspopup', 'listbox');
  button.setAttribute('aria-expanded', 'false');
  picker.appendChild(button);

  const popover = document.createElement('div');
  popover.className = 'weight-bone-popover';
  popover.hidden = true;
  popover.setAttribute('role', 'listbox');
  popover.setAttribute('aria-label', 'Bone IDs');

  const search = document.createElement('input');
  search.type = 'search';
  search.className = 'weight-bone-search';
  search.placeholder = 'Find bone ID…';
  search.setAttribute('aria-label', 'Find bone ID');
  popover.appendChild(search);

  const filter = document.createElement('label');
  filter.className = 'weight-checkbox weight-bone-filter';
  const selectedOnly = document.createElement('input');
  selectedOnly.type = 'checkbox';
  selectedOnly.className = 'weight-selected-only';
  filter.appendChild(selectedOnly);
  addText(filter, 'weight-label', 'Selected only');
  popover.appendChild(filter);

  const list = document.createElement('div');
  list.className = 'weight-bone-list';
  popover.appendChild(list);
  picker.appendChild(popover);
  section.appendChild(picker);

  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'ui-button weight-clear-selection';
  clear.textContent = 'Clear selection';
  clear.addEventListener('click', () => clearSelectedBoneIds());
  section.appendChild(clear);
  content.appendChild(section);

  ui.picker = picker;
  ui.boneButton = button;
  ui.popover = popover;
  ui.search = search;
  ui.selectedOnly = selectedOnly;
  ui.boneList = list;
  ui.clearSelection = clear;
  ui.optionById = new Map();
  ui.metaById = new Map();
  ui.optionKey = null;

  button.addEventListener('click', () => {
    const open = popover.hidden;
    popover.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    if (open) search.focus();
  });
  search.addEventListener('input', syncBoneFilter);
  selectedOnly.addEventListener('change', syncBoneFilter);
}

function buildPhysicsControls(content) {
  const section = document.createElement('section');
  section.className = 'weight-section weight-physics';
  const title = document.createElement('div');
  title.className = 'weight-section-title';
  title.textContent = 'Character physics';
  section.appendChild(title);
  addText(section, 'weight-hint',
    'Only selected bone IDs receive secondary motion. Hold RMB and drag to excite the model.');

  const enableLabel = document.createElement('label');
  enableLabel.className = 'weight-checkbox';
  const enable = document.createElement('input');
  enable.type = 'checkbox';
  enable.className = 'weight-physics-enable';
  enable.addEventListener('change', () => {
    if (enable.checked) void enableModelPhysics();
    else disableModelPhysics();
  });
  enableLabel.appendChild(enable);
  addText(enableLabel, 'weight-label', 'Enable Character Physics');
  section.appendChild(enableLabel);

  const ranges = {};
  ranges.frequency = addRange(section, 'weight-physics-frequency',
    'Frequency (Hz)', 0.1, 10, 0.1, 2,
    value => setPhysicsFrequency(null, value));
  ranges.damping = addRange(section, 'weight-physics-damping', 'Damping',
    0, 2, 0.05, 0.35, value => setPhysicsDamping(null, value));
  ranges.motion = addRange(section, 'weight-physics-motion',
    'Angular response', 0, 1, 0.05, 0.35,
    value => setPhysicsMotionStrength(null, value));
  ranges.linear = addRange(section, 'weight-physics-linear',
    'Translation response', 0, 1, 0.05, 0.35,
    value => setPhysicsLinearMotionStrength(null, value));
  ranges.continuous = addRange(section, 'weight-physics-continuous-response',
    'Velocity response', 0, 1, 0.05, 0.35,
    value => setPhysicsContinuousLinearResponse(null, value));

  const gravity = document.createElement('div');
  gravity.className = 'weight-subsection';
  const gravityLabel = document.createElement('label');
  gravityLabel.className = 'weight-checkbox';
  const gravityEnable = document.createElement('input');
  gravityEnable.type = 'checkbox';
  gravityEnable.className = 'weight-physics-gravity-enable';
  gravityEnable.addEventListener('change', () => {
    setPhysicsGravityEnabled(null, gravityEnable.checked);
  });
  gravityLabel.appendChild(gravityEnable);
  addText(gravityLabel, 'weight-label', 'Gravity');
  gravity.appendChild(gravityLabel);
  ranges.gravity = addRange(gravity, 'weight-physics-gravity-scale',
    'Gravity scale', 0, 2, 0.1, 1,
    value => setPhysicsGravityScale(null, value));
  section.appendChild(gravity);

  const constraints = document.createElement('div');
  constraints.className = 'weight-subsection';
  const constraintsLabel = document.createElement('label');
  constraintsLabel.className = 'weight-checkbox';
  const constraintsEnable = document.createElement('input');
  constraintsEnable.type = 'checkbox';
  constraintsEnable.className = 'weight-physics-constraints-enable';
  constraintsEnable.addEventListener('change', () => {
    setPhysicsConstraintsEnabled(null, constraintsEnable.checked);
  });
  constraintsLabel.appendChild(constraintsEnable);
  addText(constraintsLabel, 'weight-label', 'Joint limits');
  constraints.appendChild(constraintsLabel);
  ranges.maxBend = addRange(constraints, 'weight-physics-max-bend',
    'Max bend', 0, 90, 1, 45,
    value => setPhysicsMaxBendDegrees(null, value));
  section.appendChild(constraints);

  const actions = document.createElement('div');
  actions.className = 'weight-actions';
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button weight-physics-reset';
  reset.textContent = 'Reset motion';
  reset.addEventListener('click', () => resetModelPhysicsMotion());
  actions.appendChild(reset);
  section.appendChild(actions);
  content.appendChild(section);

  ui.physicsEnable = enable;
  ui.physicsGravityEnable = gravityEnable;
  ui.physicsConstraintsEnable = constraintsEnable;
  ui.physicsReset = reset;
  ui.ranges = ranges;
}

function buildPanel() {
  panel.replaceChildren();
  ui = {};
  const header = document.createElement('div');
  header.className = 'weight-header';
  const heading = document.createElement('h3');
  heading.textContent = 'Weight';
  header.appendChild(heading);
  ui.status = addText(header, 'weight-status', '');
  panel.appendChild(header);

  const display = document.createElement('section');
  display.className = 'weight-section';
  const heatmapLabel = document.createElement('label');
  heatmapLabel.className = 'weight-checkbox';
  const heatmap = document.createElement('input');
  heatmap.type = 'checkbox';
  heatmap.className = 'weight-heatmap-enable';
  heatmap.addEventListener('change', () => setModelWeightHeatmap(heatmap.checked));
  heatmapLabel.appendChild(heatmap);
  addText(heatmapLabel, 'weight-label', 'Show Weight Heatmap');
  display.appendChild(heatmapLabel);
  panel.appendChild(display);
  ui.heatmap = heatmap;

  buildBonePicker(panel);
  buildPhysicsControls(panel);
}

function syncBoneFilter() {
  if (!ui?.optionById) return;
  const query = ui.search.value.trim().toLowerCase();
  const selected = new Set(getModelWeightState().selectedBoneIds);
  ui.optionById.forEach((option, id) => {
    option.hidden = (!!query && !String(id).includes(query))
      || (ui.selectedOnly.checked && !selected.has(id));
  });
}

function syncBoneOptions(state) {
  const available = new Set(state.availableBoneIds);
  const selected = new Set(state.selectedBoneIds);
  const optionKey = state.availableBoneIds.join(',');
  if (optionKey !== ui.optionKey) {
    const scrollTop = ui.boneList.scrollTop;
    ui.optionById.forEach((option, id) => {
      if (!available.has(id)) {
        option.remove();
        ui.optionById.delete(id);
        ui.metaById.delete(id);
      }
    });
    state.availableBoneIds.forEach(id => {
      if (ui.optionById.has(id)) return;
      const label = document.createElement('label');
      label.className = 'weight-bone-option';
      label.dataset.boneId = String(id);
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = String(id);
      checkbox.addEventListener('change', event => {
        const next = new Set(getModelWeightState().selectedBoneIds);
        if (event.target.checked) next.add(id);
        else next.delete(id);
        setSelectedBoneIds([...next]);
      });
      label.appendChild(checkbox);
      addText(label, 'weight-bone-id', String(id));
      const meta = addText(label, 'weight-bone-meta', '');
      ui.optionById.set(id, label);
      ui.metaById.set(id, meta);
    });
    state.availableBoneIds.forEach(id =>
      ui.boneList.appendChild(ui.optionById.get(id)));
    ui.boneList.scrollTop = scrollTop;
    ui.optionKey = optionKey;
  }
  state.availableBoneIds.forEach(id => {
    ui.optionById.get(id).querySelector('input').checked = selected.has(id);
    ui.metaById.get(id).textContent = formatBoneMeta(state.boneStats?.[id]);
  });
  if (!state.availableBoneIds.length) {
    if (!ui.empty) {
      ui.empty = addText(ui.boneList, 'weight-empty', 'No bone IDs available.');
    }
    ui.empty.hidden = false;
  } else if (ui.empty) {
    ui.empty.hidden = true;
  }
}

function syncRange(range, value) {
  if (!range) return;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return;
  if (document.activeElement !== range.input) range.input.value = String(numeric);
  range.valueNode.textContent = numeric.toFixed(2);
}

function syncPanel() {
  if (!ui) return;
  const weightState = getModelWeightState();
  const physicsState = getModelPhysicsState();
  if (weightState.loading) ui.status.textContent = 'Loading model weights…';
  else if (weightState.error) ui.status.textContent = weightState.error;
  else if (weightState.noWeights || !weightState.availableBoneIds.length) {
    ui.status.textContent = weightState.loaded
      ? 'No usable skin weights found.' : 'Open this tab to load model weights.';
  } else {
    ui.status.textContent = `${weightState.availableBoneIds.length} bone IDs available`;
  }

  syncBoneOptions(weightState);
  ui.boneButton.textContent = selectedLabel(weightState.selectedBoneIds);
  ui.boneButton.disabled = !weightState.loaded
    && !weightState.availableBoneIds.length;
  ui.clearSelection.disabled = !weightState.selectedBoneIds.length;
  ui.heatmap.checked = !!weightState.heatmapEnabled;
  ui.heatmap.disabled = !weightState.loaded;
  ui.physicsEnable.checked = !!physicsState.enabled;
  ui.physicsEnable.disabled = !weightState.loaded
    || !weightState.selectedBoneIds.length;
  ui.physicsGravityEnable.checked = !!physicsState.gravityEnabled;
  ui.physicsConstraintsEnable.checked = !!physicsState.constraintsEnabled;
  ui.physicsReset.disabled = !physicsState.enabled;
  syncRange(ui.ranges.frequency, physicsState.frequencyHz);
  syncRange(ui.ranges.damping, physicsState.dampingRatio);
  syncRange(ui.ranges.motion, physicsState.angularResponse);
  syncRange(ui.ranges.linear, physicsState.translationResponse);
  syncRange(ui.ranges.continuous, physicsState.velocityResponse);
  syncRange(ui.ranges.gravity, physicsState.gravityScale);
  syncRange(ui.ranges.maxBend, physicsState.maxBendDegrees);
  syncBoneFilter();
}

function closePopover() {
  if (!ui?.popover || ui.popover.hidden) return;
  ui.popover.hidden = true;
  ui.boneButton.setAttribute('aria-expanded', 'false');
}

function loadOnDemand() {
  if (loadingPromise) return loadingPromise;
  loadingPromise = ensureModelWeightsLoaded().finally(() => {
    loadingPromise = null;
  });
  return loadingPromise;
}

export function initWeightPanel() {
  panel = $('weight-panel');
  if (!panel) return;
  buildPanel();
  window.addEventListener('mod-viewer-model-weight-changed', syncPanel);
  window.addEventListener('mod-viewer-model-physics-changed', syncPanel);
  window.addEventListener('mod-viewer-right-dock-tab-changed', event => {
    const inWeight = event.detail?.tab === 'weight' && event.detail?.open;
    if (!inWeight) closePopover();
    if (inWeight) void loadOnDemand();
  });
  document.addEventListener('pointerdown', event => {
    if (ui?.popover?.hidden || ui.picker.contains(event.target)) return;
    closePopover();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !ui?.popover?.hidden) {
      closePopover();
      ui.boneButton.focus();
    }
  });
  syncPanel();
}
