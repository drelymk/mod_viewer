// Model-wide authored-weight controls. This panel intentionally has no mesh
// selection dependency: the selected bone set and its heatmap span the model.

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
let loadingPromise = null;

const $ = id => document.getElementById(id);

function addText(parent, className, value) {
  const node = document.createElement('span');
  node.className = className;
  node.textContent = value;
  parent.appendChild(node);
  return node;
}

function addRange(parent, className, label, min, max, step, value, onInput) {
  const field = document.createElement('label');
  field.className = 'weight-field';
  const header = document.createElement('div');
  header.className = 'weight-range-header';
  addText(header, 'inspector-label', label);
  const valueNode = addText(header, 'inspector-value', Number(value).toFixed(2));
  field.appendChild(header);
  const input = document.createElement('input');
  input.type = 'range';
  input.className = className;
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.value = String(value);
  input.addEventListener('input', () => {
    valueNode.textContent = input.value;
    onInput(input.value);
  });
  field.appendChild(input);
  parent.appendChild(field);
  return input;
}

function buildBonePicker(content, state) {
  const section = document.createElement('section');
  section.className = 'weight-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Selected bones';
  section.appendChild(title);

  const picker = document.createElement('div');
  picker.className = 'weight-picker';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'ui-button weight-bone-select';
  button.setAttribute('aria-haspopup', 'listbox');
  button.setAttribute('aria-expanded', 'false');
  const selected = new Set(state.selectedBoneIds);
  button.textContent = selected.size
    ? `${selected.size} bone${selected.size === 1 ? '' : 's'} selected`
    : 'Select bones';
  picker.appendChild(button);

  const popover = document.createElement('div');
  popover.className = 'weight-bone-popover';
  popover.hidden = true;
  popover.setAttribute('role', 'listbox');
  popover.setAttribute('aria-label', 'Bone IDs');
  state.availableBoneIds.forEach(id => {
    const label = document.createElement('label');
    label.className = 'weight-bone-option';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = String(id);
    checkbox.checked = selected.has(id);
    checkbox.addEventListener('change', () => {
      const next = [...popover.querySelectorAll('input:checked')]
        .map(input => Number(input.value));
      setSelectedBoneIds(next);
    });
    label.appendChild(checkbox);
    addText(label, 'weight-bone-id', String(id));
    popover.appendChild(label);
  });
  if (!state.availableBoneIds.length) {
    addText(popover, 'weight-empty', 'No bone IDs available.');
  }
  picker.appendChild(popover);
  button.addEventListener('click', () => {
    popover.hidden = !popover.hidden;
    button.setAttribute('aria-expanded', String(!popover.hidden));
  });
  section.appendChild(picker);

  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'ui-button weight-clear-selection';
  clear.textContent = 'Clear selection';
  clear.disabled = !selected.size;
  clear.addEventListener('click', () => clearSelectedBoneIds());
  section.appendChild(clear);
  content.appendChild(section);
}

function buildPhysicsControls(content, weightState, physicsState) {
  const section = document.createElement('section');
  section.className = 'weight-section weight-physics';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Character physics';
  section.appendChild(title);
  addText(section, 'weight-hint',
    'Only selected bone IDs receive secondary motion. Hold RMB and drag to excite the model.');

  const enableLabel = document.createElement('label');
  enableLabel.className = 'weight-checkbox';
  const enable = document.createElement('input');
  enable.type = 'checkbox';
  enable.className = 'weight-physics-enable';
  enable.checked = !!physicsState.enabled;
  enable.disabled = !weightState.loaded || !weightState.selectedBoneIds.length;
  enable.addEventListener('change', () => {
    if (enable.checked) void enableModelPhysics();
    else disableModelPhysics();
  });
  enableLabel.appendChild(enable);
  addText(enableLabel, 'inspector-label', 'Enable Character Physics');
  section.appendChild(enableLabel);

  addRange(section, 'weight-physics-frequency', 'Frequency (Hz)',
    0.1, 10, 0.1, physicsState.frequencyHz,
    value => setPhysicsFrequency(null, value));
  addRange(section, 'weight-physics-damping', 'Damping',
    0, 2, 0.05, physicsState.dampingRatio,
    value => setPhysicsDamping(null, value));
  addRange(section, 'weight-physics-motion', 'Angular response',
    0, 1, 0.05, physicsState.angularResponse,
    value => setPhysicsMotionStrength(null, value));
  addRange(section, 'weight-physics-linear', 'Translation response',
    0, 1, 0.05, physicsState.translationResponse,
    value => setPhysicsLinearMotionStrength(null, value));
  addRange(section, 'weight-physics-continuous-response', 'Velocity response',
    0, 1, 0.05, physicsState.velocityResponse,
    value => setPhysicsContinuousLinearResponse(null, value));

  const gravity = document.createElement('div');
  gravity.className = 'weight-subsection';
  const gravityLabel = document.createElement('label');
  gravityLabel.className = 'weight-checkbox';
  const gravityEnable = document.createElement('input');
  gravityEnable.type = 'checkbox';
  gravityEnable.className = 'weight-physics-gravity-enable';
  gravityEnable.checked = !!physicsState.gravityEnabled;
  gravityEnable.addEventListener('change', () => {
    setPhysicsGravityEnabled(null, gravityEnable.checked);
  });
  gravityLabel.appendChild(gravityEnable);
  addText(gravityLabel, 'inspector-label', 'Gravity');
  gravity.appendChild(gravityLabel);
  addRange(gravity, 'weight-physics-gravity-scale', 'Gravity scale',
    0, 2, 0.1, physicsState.gravityScale,
    value => setPhysicsGravityScale(null, value));
  section.appendChild(gravity);

  const constraints = document.createElement('div');
  constraints.className = 'weight-subsection';
  const constraintsLabel = document.createElement('label');
  constraintsLabel.className = 'weight-checkbox';
  const constraintsEnable = document.createElement('input');
  constraintsEnable.type = 'checkbox';
  constraintsEnable.className = 'weight-physics-constraints-enable';
  constraintsEnable.checked = !!physicsState.constraintsEnabled;
  constraintsEnable.addEventListener('change', () => {
    setPhysicsConstraintsEnabled(null, constraintsEnable.checked);
  });
  constraintsLabel.appendChild(constraintsEnable);
  addText(constraintsLabel, 'inspector-label', 'Joint limits');
  constraints.appendChild(constraintsLabel);
  addRange(constraints, 'weight-physics-max-bend', 'Max bend',
    0, 90, 1, physicsState.maxBendDegrees,
    value => setPhysicsMaxBendDegrees(null, value));
  section.appendChild(constraints);

  const actions = document.createElement('div');
  actions.className = 'weight-actions';
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button weight-physics-reset';
  reset.textContent = 'Reset motion';
  reset.disabled = !physicsState.enabled;
  reset.addEventListener('click', () => resetModelPhysicsMotion());
  actions.appendChild(reset);
  section.appendChild(actions);
  content.appendChild(section);
}

function render() {
  if (!panel) return;
  const weightState = getModelWeightState();
  const physicsState = getModelPhysicsState();
  panel.replaceChildren();
  const header = document.createElement('div');
  header.className = 'weight-header';
  const heading = document.createElement('h3');
  heading.textContent = 'Weight';
  header.appendChild(heading);
  const status = addText(header, 'weight-status', '');
  if (weightState.loading) status.textContent = 'Loading model weights…';
  else if (weightState.error) status.textContent = weightState.error;
  else if (weightState.noWeights || !weightState.availableBoneIds.length) {
    status.textContent = weightState.loaded
      ? 'No usable skin weights found.' : 'Open this tab to load model weights.';
  } else {
    status.textContent = `${weightState.availableBoneIds.length} bone IDs available`;
  }
  panel.appendChild(header);

  if (!weightState.loading) {
    buildBonePicker(panel, weightState);
    const display = document.createElement('section');
    display.className = 'weight-section';
    const heatmapLabel = document.createElement('label');
    heatmapLabel.className = 'weight-checkbox';
    const heatmap = document.createElement('input');
    heatmap.type = 'checkbox';
    heatmap.className = 'weight-heatmap-enable';
    heatmap.checked = !!weightState.heatmapEnabled;
    heatmap.disabled = !weightState.loaded;
    heatmap.addEventListener('change', () => setModelWeightHeatmap(heatmap.checked));
    heatmapLabel.appendChild(heatmap);
    addText(heatmapLabel, 'inspector-label', 'Show Weight Heatmap');
    display.appendChild(heatmapLabel);
    panel.appendChild(display);
    buildPhysicsControls(panel, weightState, physicsState);
  }
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
  window.addEventListener('mod-viewer-model-weight-changed', render);
  window.addEventListener('mod-viewer-model-physics-changed', render);
  window.addEventListener('mod-viewer-right-dock-tab-changed', event => {
    if (event.detail?.tab !== 'weight' || !event.detail?.open) return;
    void loadOnDemand();
  });
  render();
}
