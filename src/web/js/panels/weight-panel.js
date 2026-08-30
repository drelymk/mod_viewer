// Model-wide authored-weight controls. The panel owns one stable DOM tree so
// simulation and input events never interrupt an active picker or slider.

import {
  beginWeightPicking, clearSelectedBones, ensureModelWeightsLoaded,
  getModelPhysicsState,
  getModelWeightState, loadSavedBoneSelection,
  resetModelPhysics, saveModelWeightSelection, setModelWeightHeatmap,
  setWeightPickerViewMode,
  setPhysicsConstraintsEnabled,
  setPhysicsContinuousLinearResponse, setPhysicsDamping,
  setPhysicsFrequency, setPhysicsGravityEnabled, setPhysicsGravityScale,
  setPhysicsLinearMotionStrength, setPhysicsMaxBendDegrees,
  setPhysicsMotionStrength, setBoneSelected,
} from '../mesh/weight-experiment.js';

let panel = null;
let ui = null;
let loadingPromise = null;
let latestWeightState = null;

const $ = id => document.getElementById(id);

function addText(parent, className, value) {
  const node = document.createElement('span');
  node.className = className;
  node.textContent = value;
  parent.appendChild(node);
  return node;
}

function selectedLabel(state) {
  const entries = state.selectedBones || [];
  if (!entries.length) return 'Select bones';
  const count = Number(state.selectedBoneCount)
    || entries.reduce((total, entry) => total + entry.boneIds.length, 0);
  if (entries.length === 1 && count <= 4) {
    return entries[0].boneIds.join(', ');
  }
  return `${count} bones selected`;
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

function formatNearbyInfluence(value) {
  const percentage = Math.max(0, Number(value) || 0) * 100;
  return percentage > 0 && percentage < 1
    ? '<1% nearby' : `${Math.round(percentage)}% nearby`;
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

  const pickerView = document.createElement('div');
  pickerView.className = 'weight-picker-view';
  pickerView.setAttribute('role', 'group');
  pickerView.setAttribute('aria-label', 'Bone picker view');
  const allBones = document.createElement('button');
  allBones.type = 'button';
  allBones.className = 'weight-picker-view-option';
  allBones.dataset.mode = 'all';
  allBones.textContent = 'All bones';
  const pickedPoint = document.createElement('button');
  pickedPoint.type = 'button';
  pickedPoint.className = 'weight-picker-view-option';
  pickedPoint.dataset.mode = 'picked';
  pickedPoint.textContent = 'At picked point';
  pickerView.append(allBones, pickedPoint);
  popover.appendChild(pickerView);

  const filter = document.createElement('label');
  filter.className = 'weight-checkbox weight-bone-filter';
  const selectedOnly = document.createElement('input');
  selectedOnly.type = 'checkbox';
  selectedOnly.className = 'weight-selected-only';
  filter.appendChild(selectedOnly);
  addText(filter, 'weight-label', 'Selected bones only');
  popover.appendChild(filter);

  const list = document.createElement('div');
  list.className = 'weight-bone-list';
  popover.appendChild(list);
  picker.appendChild(popover);
  section.appendChild(picker);

  const pickModel = document.createElement('button');
  pickModel.type = 'button';
  pickModel.className = 'ui-button weight-pick-model';
  pickModel.textContent = 'Pick from model';
  pickModel.addEventListener('click', () => {
    closePopover();
    beginWeightPicking();
  });
  section.insertBefore(pickModel, picker);

  const actions = document.createElement('div');
  actions.className = 'weight-selection-actions';
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'ui-button weight-save-selection';
  save.textContent = 'Save';
  save.addEventListener('click', () => void saveModelWeightSelection());
  const load = document.createElement('button');
  load.type = 'button';
  load.className = 'ui-button weight-load-selection';
  load.textContent = 'Load';
  load.addEventListener('click', () => loadSavedBoneSelection());
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'ui-button weight-clear-selection';
  clear.textContent = 'Clear';
  clear.addEventListener('click', () => clearSelectedBones());
  actions.append(save, load, clear);
  section.appendChild(actions);
  content.appendChild(section);

  ui.picker = picker;
  ui.boneButton = button;
  ui.popover = popover;
  ui.search = search;
  ui.pickModel = pickModel;
  ui.allBones = allBones;
  ui.pickedPoint = pickedPoint;
  ui.selectedOnly = selectedOnly;
  ui.boneList = list;
  ui.clearSelection = clear;
  ui.saveSelection = save;
  ui.loadSelection = load;
  ui.groupBySource = new Map();
  ui.optionKey = null;

  button.addEventListener('click', () => {
    const open = popover.hidden;
    popover.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    if (open) search.focus();
  });
  search.addEventListener('input', () => syncBoneFilter());
  selectedOnly.addEventListener('change', () => syncBoneFilter());
  allBones.addEventListener('click', () => {
    setWeightPickerViewMode('all');
  });
  pickedPoint.addEventListener('click', () => {
    setWeightPickerViewMode('picked');
  });
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

  const initial = getModelPhysicsState();
  const ranges = {};
  ranges.frequency = addRange(section, 'weight-physics-frequency',
    'Frequency (Hz)', 0.1, 10, 0.1, initial.frequencyHz,
    value => setPhysicsFrequency(null, value));
  ranges.damping = addRange(section, 'weight-physics-damping', 'Damping',
    0, 2, 0.05, initial.dampingRatio, value => setPhysicsDamping(null, value));
  ranges.motion = addRange(section, 'weight-physics-motion',
    'Angular response', 0, 1, 0.05, initial.angularResponse,
    value => setPhysicsMotionStrength(null, value));
  ranges.linear = addRange(section, 'weight-physics-linear',
    'Translation response', 0, 1, 0.05, initial.translationResponse,
    value => setPhysicsLinearMotionStrength(null, value));
  ranges.continuous = addRange(section, 'weight-physics-continuous-response',
    'Velocity response', 0, 1, 0.05, initial.velocityResponse,
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
    'Gravity scale', 0, 2, 0.1, initial.gravityScale,
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
    'Max bend', 0, 90, 1, initial.maxBendDegrees,
    value => setPhysicsMaxBendDegrees(null, value));
  section.appendChild(constraints);

  const actions = document.createElement('div');
  actions.className = 'weight-actions';
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button weight-physics-reset';
  reset.textContent = 'Reset';
  reset.addEventListener('click', () => resetModelPhysics());
  actions.appendChild(reset);
  section.appendChild(actions);
  content.appendChild(section);

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

function syncBoneFilter(weightState = latestWeightState || getModelWeightState()) {
  if (!ui?.groupBySource) return;
  const query = ui.search.value.trim().toLowerCase();
  const selected = new Map((weightState.selectedBones || []).map(entry => [
    entry.sourceKey, new Set(entry.boneIds),
  ]));
  ui.groupBySource.forEach((group, sourceKey) => {
    const sourceSelected = selected.get(sourceKey) || new Set();
    let visible = 0;
    group.optionById.forEach((option, id) => {
      option.hidden = (!!query && !String(id).includes(query))
        || (ui.selectedOnly.checked && !sourceSelected.has(id));
      if (!option.hidden) visible += 1;
    });
    group.root.hidden = visible === 0;
  });
}

function syncBoneOptions(state) {
  const allSources = state.sources || [];
  const picked = state.pickerViewMode === 'picked' ? state.pickedPoint : null;
  const sources = picked
    ? allSources.filter(source => source.key === picked.sourceKey) : allSources;
  const optionKey = JSON.stringify([state.pickerViewMode, sources.map(source => [
    source.key, source.file, source.boneIdOffset,
    state.pickerViewMode === 'picked'
      ? (picked?.influences || []).map(influence => influence.boneId)
      : source.availableBoneIds,
  ])]);
  if (optionKey !== ui.optionKey) {
    const scrollTop = ui.boneList.scrollTop;
    ui.boneList.replaceChildren();
    ui.empty = null;
    ui.groupBySource = new Map();
    const basenames = new Map(sources.map(source => [
      source.key, String(source.file).split('/').pop().toLowerCase(),
    ]));
    const basenameCounts = new Map();
    basenames.forEach(name => basenameCounts.set(
      name, (basenameCounts.get(name) || 0) + 1));
    const fileCounts = new Map();
    sources.forEach(source => fileCounts.set(
      String(source.file).toLowerCase(),
      (fileCounts.get(String(source.file).toLowerCase()) || 0) + 1));
    sources.forEach(source => {
      const root = document.createElement('section');
      root.className = 'weight-bone-group';
      const heading = addText(root, 'weight-bone-source', '');
      const basename = String(source.file).split('/').pop();
      let label = basename;
      if (basenameCounts.get(basenames.get(source.key)) > 1) label = source.file;
      if (fileCounts.get(String(source.file).toLowerCase()) > 1) {
        label += ` · offset +${source.boneIdOffset}`;
      }
      heading.textContent = label.toUpperCase();
      heading.title = source.file;
      root.appendChild(heading);
      const optionById = new Map();
      const metaById = new Map();
      const pickedById = new Map((picked?.influences || [])
        .map(influence => [influence.boneId, influence.weight]));
      const ids = state.pickerViewMode === 'picked'
        ? [...pickedById.keys()] : source.availableBoneIds;
      ids.forEach(id => {
        const label = document.createElement('label');
        label.className = 'weight-bone-option';
        label.dataset.boneId = String(id);
        label.dataset.sourceKey = source.key;
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = String(id);
        checkbox.addEventListener('change', event => {
          setBoneSelected(source.key, id, event.target.checked);
        });
        label.appendChild(checkbox);
        addText(label, 'weight-bone-id', String(id));
        const meta = addText(label, 'weight-bone-meta', '');
        if (state.pickerViewMode === 'picked') {
          meta.classList.add('weight-bone-nearby');
        }
        optionById.set(id, label);
        metaById.set(id, meta);
        root.appendChild(label);
      });
      ui.boneList.appendChild(root);
      ui.groupBySource.set(source.key, {root, optionById, metaById});
    });
    ui.boneList.scrollTop = scrollTop;
    ui.optionKey = optionKey;
  }
  const selected = new Map((state.selectedBones || []).map(entry => [
    entry.sourceKey, new Set(entry.boneIds),
  ]));
  sources.forEach(source => {
    const group = ui.groupBySource.get(source.key);
    if (!group) return;
    const sourceSelected = selected.get(source.key) || new Set();
    const pickedById = new Map((picked?.influences || [])
      .map(influence => [influence.boneId, influence.weight]));
    const ids = state.pickerViewMode === 'picked'
      ? [...pickedById.keys()] : source.availableBoneIds;
    ids.forEach(id => {
      group.optionById.get(id).querySelector('input').checked =
        sourceSelected.has(id);
      const globalMeta = formatBoneMeta(source.boneStats?.[id]);
      group.metaById.get(id).textContent = state.pickerViewMode === 'picked'
        ? `${formatNearbyInfluence(pickedById.get(id))} \u00b7 ${globalMeta}`
        : globalMeta;
    });
  });
  if (!sources.some(source => source.availableBoneIds.length)) {
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

function syncWeightControls(weightState = getModelWeightState()) {
  if (!ui) return;
  latestWeightState = weightState;
  const availableCount = (weightState.sources || []).reduce(
    (total, source) => total + (source.availableBoneIds?.length || 0), 0);
  const sourceCount = (weightState.sources || []).length;
  if (weightState.loading) ui.status.textContent = 'Loading model weights…';
  else if (weightState.error) ui.status.textContent = weightState.error;
  else if (weightState.noWeights || !availableCount) {
    ui.status.textContent = weightState.loaded
      ? 'No usable skin weights found.' : 'Open this tab to load model weights.';
  } else {
    ui.status.textContent = `${availableCount} bones across ${sourceCount} Blend buffers`;
  }
  if (weightState.selectionSaveError) {
    ui.status.textContent = `Could not save bone selection: ${weightState.selectionSaveError}`;
  }

  syncBoneOptions(weightState);
  ui.boneButton.textContent = selectedLabel(weightState);
  ui.boneButton.disabled = !weightState.loaded
    && !availableCount;
  ui.pickModel.disabled = !weightState.loaded || !availableCount;
  ui.pickModel.classList.toggle('active', !!weightState.picking);
  ui.pickModel.setAttribute('aria-pressed', String(!!weightState.picking));
  ui.clearSelection.disabled = !weightState.selectedBoneCount;
  ui.saveSelection.disabled = !weightState.selectedBoneCount
    || weightState.savingSelection;
  ui.loadSelection.disabled = !weightState.savedBones?.length;
  const pickedMode = weightState.pickerViewMode === 'picked';
  ui.allBones.classList.toggle('active', !pickedMode);
  ui.allBones.setAttribute('aria-pressed', String(!pickedMode));
  ui.pickedPoint.classList.toggle('active', pickedMode);
  ui.pickedPoint.setAttribute('aria-pressed', String(pickedMode));
  ui.pickedPoint.disabled = !weightState.pickedPoint;
  ui.heatmap.checked = !!weightState.heatmapEnabled;
  ui.heatmap.disabled = !weightState.loaded;
  ui.physicsReset.disabled = !weightState.loaded;
  if (weightState.pickStatus) ui.status.textContent = weightState.pickStatus;
  syncBoneFilter(weightState);
}

function syncPhysicsControls(physicsState = getModelPhysicsState()) {
  if (!ui) return;
  ui.physicsGravityEnable.checked = !!physicsState.gravityEnabled;
  ui.physicsConstraintsEnable.checked = !!physicsState.constraintsEnabled;
  syncRange(ui.ranges.frequency, physicsState.frequencyHz);
  syncRange(ui.ranges.damping, physicsState.dampingRatio);
  syncRange(ui.ranges.motion, physicsState.angularResponse);
  syncRange(ui.ranges.linear, physicsState.translationResponse);
  syncRange(ui.ranges.continuous, physicsState.velocityResponse);
  syncRange(ui.ranges.gravity, physicsState.gravityScale);
  syncRange(ui.ranges.maxBend, physicsState.maxBendDegrees);
}

function syncPanel() {
  syncWeightControls();
  syncPhysicsControls();
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
  window.addEventListener('mod-viewer-model-weight-changed', event => {
    syncWeightControls(event.detail);
  });
  window.addEventListener('mod-viewer-model-physics-changed', event => {
    syncPhysicsControls(event.detail);
  });
  window.addEventListener('mod-viewer-weight-point-picked', event => {
    syncWeightControls(event.detail);
    ui.boneList.scrollTop = 0;
    ui.popover.hidden = false;
    ui.boneButton.setAttribute('aria-expanded', 'true');
  });
  window.addEventListener('mod-viewer-right-dock-tab-changed', event => {
    const inWeight = event.detail?.tab === 'weight' && event.detail?.open;
    if (!inWeight) {
      closePopover();
    }
    if (inWeight) void loadOnDemand();
  });
  document.addEventListener('pointerdown', event => {
    if (ui?.popover?.hidden || ui.picker.contains(event.target)) return;
    if (event.target.closest?.('#canvas-container canvas, .draw-item')) return;
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
