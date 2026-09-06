// Unified Weight/Rig controls. The panel owns one stable DOM tree for both
// domains and one lazy-load/picking lifecycle.

import {
  beginModelPicking, cancelModelPicking, clearSelectedBones,
  ensureModelRigLoaded, getModelPhysicsState, getModelRigState,
  getModelWeightState, getRigJointPoseFrame, loadSavedBoneSelection,
  clearRigJointSelection, resetModelPhysics, resetRigJoint, resetRigPose,
  saveModelWeightSelection,
  selectRigJoint, setBoneSelected, setPhysicsConstraintsEnabled,
  setPhysicsContinuousLinearResponse, setPhysicsDamping, setPhysicsFrequency,
  setPhysicsGravityEnabled, setPhysicsGravityScale, setPhysicsLinearMotionStrength,
  setPhysicsMaxBendDegrees, setPhysicsMotionStrength, setModelWeightHeatmap,
  setRigJointRoot, setRigOverlayScope,
  setRigRotationSnapDegrees, setRigVisible, setWeightPickerViewMode,
  applyRigPosePresetById,
  deleteRigPosePreset, renameRigPosePreset,
  saveRigPosePreset,
} from '../mesh/weight-experiment.js';
import {eulerFromRestFrameDelta} from '../mesh/weight-rig-frames.js';
import { confirmDialog, inputConfirmDialog } from '../ui/dialogs.js';

let panel = null;
let ui = null;
let loadingPromise = null;
let latestWeightState = null;
let latestRigState = null;

const $ = id => document.getElementById(id);

function addText(parent, className, value = '') {
  const node = document.createElement('span');
  node.className = className;
  node.textContent = value;
  parent.appendChild(node);
  return node;
}

function addSection(parent, title) {
  const section = document.createElement('section');
  section.className = 'weight-rig-section';
  addText(section, 'weight-rig-section-title', title);
  parent.appendChild(section);
  return section;
}

function addAdvanced(parent, title = 'Advanced Settings') {
  const details = document.createElement('details');
  details.className = 'weight-rig-advanced';
  const summary = document.createElement('summary');
  summary.textContent = title;
  details.appendChild(summary);
  const content = document.createElement('div');
  content.className = 'weight-rig-advanced-content';
  details.appendChild(content);
  parent.appendChild(details);
  return {details, content};
}

function addRigAdvancedGroup(parent, title) {
  const group = document.createElement('div');
  group.className = 'rig-advanced-group';
  addText(group, 'weight-rig-advanced-title', title);
  parent.appendChild(group);
  return group;
}

function selectedLabel(state) {
  const entries = state?.selectedBones || [];
  if (!entries.length) return 'Select bones';
  const count = Number(state.selectedBoneCount)
    || entries.reduce((total, entry) => total + entry.boneIds.length, 0);
  if (entries.length === 1 && count <= 4) return entries[0].boneIds.join(', ');
  return `${count} bones selected`;
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

function buildPrimaryPicker(parent) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'ui-button weight-rig-primary-action weight-pick-model';
  button.textContent = 'Pick from model';
  button.setAttribute('aria-pressed', 'false');
  button.addEventListener('click', () => {
    closePopover();
    if (latestWeightState?.picking || latestRigState?.picking) cancelModelPicking();
    else beginModelPicking();
  });
  parent.appendChild(button);
  ui.pickModel = button;
}

function buildBonePicker(section) {
  const label = addText(section, 'weight-rig-control-label', 'Authored Source');
  label.setAttribute('aria-hidden', 'true');
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
  pickerView.setAttribute('aria-label', 'Bone browser options');
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

  ui.picker = picker;
  ui.boneButton = button;
  ui.popover = popover;
  ui.boneSearch = search;
  ui.allBones = allBones;
  ui.pickedPoint = pickedPoint;
  ui.selectedOnly = selectedOnly;
  ui.boneList = list;
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
  allBones.addEventListener('click', () => setWeightPickerViewMode('all'));
  pickedPoint.addEventListener('click', () => setWeightPickerViewMode('picked'));
}

function buildWeightSection(parent) {
  const section = addSection(parent, 'WEIGHT');
  buildBonePicker(section);

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
  ui.clearSelection = clear;
  ui.saveSelection = save;
  ui.loadSelection = load;

  const heatmapLabel = document.createElement('label');
  heatmapLabel.className = 'weight-checkbox';
  const heatmap = document.createElement('input');
  heatmap.type = 'checkbox';
  heatmap.className = 'weight-heatmap-enable';
  heatmap.addEventListener('change', () => setModelWeightHeatmap(heatmap.checked));
  heatmapLabel.appendChild(heatmap);
  addText(heatmapLabel, 'weight-label', 'Show Weight Heatmap');
  section.appendChild(heatmapLabel);
  ui.heatmap = heatmap;

  const gravityLabel = document.createElement('label');
  gravityLabel.className = 'weight-checkbox';
  const gravity = document.createElement('input');
  gravity.type = 'checkbox';
  gravity.className = 'weight-physics-gravity-enable';
  gravity.addEventListener('change', () => setPhysicsGravityEnabled(gravity.checked));
  gravityLabel.appendChild(gravity);
  addText(gravityLabel, 'weight-label', 'Gravity');
  section.appendChild(gravityLabel);
  ui.physicsGravityEnable = gravity;

  const advanced = addAdvanced(parent);
  const physicsTitle = addText(advanced.content, 'weight-rig-advanced-title', 'Physics tuning');
  physicsTitle.setAttribute('aria-hidden', 'true');
  const initial = getModelPhysicsState();
  const ranges = {};
  ranges.frequency = addRange(advanced.content, 'weight-physics-frequency',
    'Frequency', 0.1, 10, 0.1, initial.frequencyHz, value => setPhysicsFrequency(value));
  ranges.damping = addRange(advanced.content, 'weight-physics-damping', 'Damping',
    0, 2, 0.05, initial.dampingRatio, value => setPhysicsDamping(value));
  ranges.motion = addRange(advanced.content, 'weight-physics-motion',
    'Angular response', 0, 1, 0.05, initial.angularResponse,
    value => setPhysicsMotionStrength(value));
  ranges.linear = addRange(advanced.content, 'weight-physics-linear',
    'Translation response', 0, 1, 0.05, initial.translationResponse,
    value => setPhysicsLinearMotionStrength(value));
  ranges.continuous = addRange(advanced.content, 'weight-physics-continuous-response',
    'Velocity response', 0, 1, 0.05, initial.velocityResponse,
    value => setPhysicsContinuousLinearResponse(value));
  ranges.gravity = addRange(advanced.content, 'weight-physics-gravity-scale',
    'Gravity scale', 0, 2, 0.1, initial.gravityScale,
    value => setPhysicsGravityScale(value));

  const constraintsLabel = document.createElement('label');
  constraintsLabel.className = 'weight-checkbox';
  const constraints = document.createElement('input');
  constraints.type = 'checkbox';
  constraints.className = 'weight-physics-constraints-enable';
  constraints.addEventListener('change', () => setPhysicsConstraintsEnabled(constraints.checked));
  constraintsLabel.appendChild(constraints);
  addText(constraintsLabel, 'weight-label', 'Joint limits');
  advanced.content.appendChild(constraintsLabel);
  ui.physicsConstraintsEnable = constraints;
  ranges.maxBend = addRange(advanced.content, 'weight-physics-max-bend',
    'Max bend', 0, 90, 1, initial.maxBendDegrees,
    value => setPhysicsMaxBendDegrees(value));
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button weight-physics-reset';
  reset.textContent = 'Reset physics';
  reset.addEventListener('click', () => resetModelPhysics());
  advanced.content.appendChild(reset);
  ui.physicsReset = reset;
  ui.ranges = ranges;
}

function selectedJoint(state = latestRigState) {
  const rawId = state?.selectedJointId;
  if (rawId === null || rawId === undefined || rawId === '') return null;
  const id = Number(rawId);
  if (!Number.isInteger(id)) return null;
  return state?.model?.joints?.find(joint =>
    Number(joint.jointId) === id) || null;
}

function componentForJoint(model, jointId) {
  return (model?.components || []).find(component =>
    (component.nodeIds || []).includes(Number(jointId))) || null;
}

function buildRigSection(parent) {
  const section = addSection(parent, 'RIG');
  addText(section, 'weight-rig-control-label', 'Selected Joint');
  const joint = document.createElement('select');
  joint.className = 'rig-bone-select';
  joint.setAttribute('aria-label', 'Selected model joint');
  joint.addEventListener('change', () => {
    if (joint.value !== '') selectRigJoint(Number(joint.value));
  });
  section.appendChild(joint);
  ui.joint = joint;

  const visibleLabel = document.createElement('label');
  visibleLabel.className = 'weight-checkbox rig-show-inferred';
  const visible = document.createElement('input');
  visible.type = 'checkbox';
  visible.addEventListener('change', () => setRigVisible(visible.checked));
  visibleLabel.appendChild(visible);
  addText(visibleLabel, 'weight-label', 'Show inferred rig');
  section.appendChild(visibleLabel);
  ui.visible = visible;

  const jointActions = document.createElement('div');
  jointActions.className = 'rig-actions rig-joint-actions';
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'ui-button rig-clear-joint';
  clear.textContent = 'Clear';
  clear.addEventListener('click', () => clearRigJointSelection());
  const resetJoint = document.createElement('button');
  resetJoint.type = 'button';
  resetJoint.className = 'ui-button rig-reset-joint';
  resetJoint.textContent = 'Reset Joint';
  resetJoint.addEventListener('click', () => {
    const selected = selectedJoint();
    if (selected) resetRigJoint(selected.jointId);
  });
  const resetPose = document.createElement('button');
  resetPose.type = 'button';
  resetPose.className = 'ui-button rig-reset-pose';
  resetPose.textContent = 'Reset Pose';
  resetPose.addEventListener('click', () => resetRigPose());
  jointActions.append(clear, resetJoint, resetPose);
  section.appendChild(jointActions);
  ui.clearJoint = clear;
  ui.resetJoint = resetJoint;
  ui.resetPose = resetPose;

  addText(section, 'weight-rig-control-label', 'Pose presets');
  const preset = document.createElement('select');
  preset.className = 'rig-preset-select';
  preset.setAttribute('aria-label', 'Rig pose preset');
  preset.addEventListener('change', () => {
    const presetId = preset.value || null;
    if (!presetId) return;
    applySelectedPreset(presetId);
  });
  section.appendChild(preset);
  ui.preset = preset;
  const management = document.createElement('div');
  management.className = 'rig-actions rig-preset-actions';
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'ui-button rig-save-preset';
  save.textContent = 'Save';
  save.addEventListener('click', () => savePreset());
  const rename = document.createElement('button');
  rename.type = 'button';
  rename.className = 'ui-button rig-rename-preset';
  rename.textContent = 'Rename';
  rename.addEventListener('click', () => renamePreset());
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'ui-button rig-delete-preset';
  remove.textContent = 'Delete';
  remove.addEventListener('click', () => deletePreset());
  management.append(save, rename, remove);
  section.appendChild(management);
  ui.savePreset = save;
  ui.renamePreset = rename;
  ui.deletePreset = remove;
  ui.presetStatus = addText(section, 'rig-hint');

  const advanced = addAdvanced(parent);
  const display = addRigAdvancedGroup(advanced.content, 'Display');
  const allLabel = document.createElement('label');
  allLabel.className = 'weight-checkbox';
  const all = document.createElement('input');
  all.type = 'checkbox';
  all.className = 'rig-panel-show-all';
  all.addEventListener('change', () => setRigOverlayScope(all.checked ? 'all' : 'selection'));
  allLabel.appendChild(all);
  addText(allLabel, 'weight-label', 'Show all overlay joints');
  display.appendChild(allLabel);
  ui.showAll = all;

  const transform = addRigAdvancedGroup(advanced.content, 'Transform');
  const snapRow = document.createElement('label');
  snapRow.className = 'rig-row';
  addText(snapRow, 'rig-label', 'Rotation snap');
  const snap = document.createElement('select');
  snap.className = 'rig-snap-select';
  [[0, 'Off'], [5, '5°'], [15, '15°'], [30, '30°']].forEach(([value, text]) => {
    const option = document.createElement('option');
    option.value = String(value);
    option.textContent = text;
    snap.appendChild(option);
  });
  snap.addEventListener('change', () => setRigRotationSnapDegrees(Number(snap.value)));
  snapRow.appendChild(snap);
  transform.appendChild(snapRow);
  ui.snap = snap;

  const rotationTitle = addText(transform, 'rig-readout-title', 'Rotation');
  rotationTitle.setAttribute('aria-hidden', 'true');
  ui.rotationValues = [];
  ['X', 'Y', 'Z'].forEach(axis => {
    const row = document.createElement('div');
    row.className = 'rig-row rig-readout-row';
    addText(row, 'rig-label', axis);
    ui.rotationValues.push(addText(row, 'rig-value', '—'));
    transform.appendChild(row);
  });

  const hierarchy = addRigAdvancedGroup(advanced.content, 'Hierarchy');
  ui.root = addAdvancedValue(hierarchy, 'Component root');
  ui.parent = addAdvancedNavValue(hierarchy, 'Parent');
  ui.children = addAdvancedNavValue(hierarchy, 'Children');
  ui.depth = addAdvancedValue(hierarchy, 'Depth');
  const setRoot = document.createElement('button');
  setRoot.type = 'button';
  setRoot.className = 'ui-button rig-set-root';
  setRoot.textContent = 'Set selected as root';
  setRoot.addEventListener('click', () => {
    const selected = selectedJoint();
    if (selected) setRigJointRoot(selected.jointId);
  });
  hierarchy.appendChild(setRoot);
  ui.setRoot = setRoot;

}

function addAdvancedValue(parent, label) {
  const row = document.createElement('div');
  row.className = 'rig-row';
  addText(row, 'rig-label', label);
  const value = addText(row, 'rig-value', '—');
  parent.appendChild(row);
  return value;
}

function addAdvancedNavValue(parent, label) {
  const row = document.createElement('div');
  row.className = 'rig-nav-row';
  addText(row, 'rig-label', label);
  const value = document.createElement('div');
  value.className = 'rig-nav-values';
  row.appendChild(value);
  parent.appendChild(row);
  return value;
}

function buildPanel() {
  panel.replaceChildren();
  ui = {};
  const header = document.createElement('div');
  header.className = 'weight-rig-header';
  const heading = document.createElement('h3');
  heading.textContent = 'Weight/Rig';
  header.appendChild(heading);
  ui.status = addText(header, 'weight-rig-status');
  panel.appendChild(header);
  buildPrimaryPicker(panel);
  buildWeightSection(panel);
  buildRigSection(panel);
}

function formatAffectedVertices(value) {
  const count = Math.max(0, Math.round(Number(value) || 0));
  if (count < 1000) return `${count.toLocaleString('en-US')} verts`;
  return `${(count / 1000).toFixed(1).replace(/\.0$/, '')}k verts`;
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

function syncBoneFilter(weightState = latestWeightState || getModelWeightState()) {
  if (!ui?.groupBySource) return;
  const query = ui.boneSearch.value.trim().toLowerCase();
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
  const allSources = state?.sources || [];
  const picked = state?.pickerViewMode === 'picked' ? state.pickedPoint : null;
  const sources = picked
    ? allSources.filter(source => source.key === picked.sourceKey) : allSources;
  const optionKey = JSON.stringify([state?.pickerViewMode, sources.map(source => [
    source.key, source.file, source.boneIdOffset,
    state?.pickerViewMode === 'picked'
      ? (picked?.influences || []).map(influence => influence.boneId)
      : source.availableBoneIds,
  ])]);
  if (optionKey !== ui.optionKey) {
    const scrollTop = ui.boneList.scrollTop;
    ui.boneList.replaceChildren();
    ui.groupBySource = new Map();
    const basenames = new Map(sources.map(source => [
      source.key, String(source.file).split('/').pop().toLowerCase(),
    ]));
    const basenameCounts = new Map();
    basenames.forEach(name => basenameCounts.set(name, (basenameCounts.get(name) || 0) + 1));
    const fileCounts = new Map();
    sources.forEach(source => fileCounts.set(String(source.file).toLowerCase(),
      (fileCounts.get(String(source.file).toLowerCase()) || 0) + 1));
    sources.forEach(source => {
      const root = document.createElement('section');
      root.className = 'weight-bone-group';
      const heading = addText(root, 'weight-bone-source');
      const basename = String(source.file).split('/').pop();
      let label = basename;
      if (basenameCounts.get(basenames.get(source.key)) > 1) label = source.file;
      if (fileCounts.get(String(source.file).toLowerCase()) > 1) {
        label += ` · offset +${source.boneIdOffset}`;
      }
      heading.textContent = label.toUpperCase();
      heading.title = source.file;
      const optionById = new Map();
      const metaById = new Map();
      const pickedById = new Map((picked?.influences || [])
        .map(influence => [influence.boneId, influence.weight]));
      const ids = state?.pickerViewMode === 'picked'
        ? [...pickedById.keys()] : source.availableBoneIds;
      ids.forEach(id => {
        const row = document.createElement('label');
        row.className = 'weight-bone-option';
        row.dataset.boneId = String(id);
        row.dataset.sourceKey = source.key;
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = String(id);
        checkbox.addEventListener('change', event =>
          setBoneSelected(source.key, id, event.target.checked));
        row.appendChild(checkbox);
        addText(row, 'weight-bone-id', String(id));
        const meta = addText(row, 'weight-bone-meta');
        if (state?.pickerViewMode === 'picked') meta.classList.add('weight-bone-nearby');
        optionById.set(id, row);
        metaById.set(id, meta);
        root.appendChild(row);
      });
      ui.boneList.appendChild(root);
      ui.groupBySource.set(source.key, {root, optionById, metaById});
    });
    ui.boneList.scrollTop = scrollTop;
    ui.optionKey = optionKey;
  }
  const selected = new Map((state?.selectedBones || []).map(entry => [
    entry.sourceKey, new Set(entry.boneIds),
  ]));
  sources.forEach(source => {
    const group = ui.groupBySource.get(source.key);
    if (!group) return;
    const sourceSelected = selected.get(source.key) || new Set();
    const pickedById = new Map((picked?.influences || [])
      .map(influence => [influence.boneId, influence.weight]));
    const ids = state?.pickerViewMode === 'picked'
      ? [...pickedById.keys()] : source.availableBoneIds;
    ids.forEach(id => {
      const row = group.optionById.get(id);
      if (!row) return;
      row.querySelector('input').checked = sourceSelected.has(id);
      const globalMeta = formatBoneMeta(source.boneStats?.[id]);
      group.metaById.get(id).textContent = state?.pickerViewMode === 'picked'
        ? `${formatNearbyInfluence(pickedById.get(id))} · ${globalMeta}` : globalMeta;
    });
  });
  if (!sources.some(source => source.availableBoneIds.length)) {
    if (!ui.empty) ui.empty = addText(ui.boneList, 'weight-empty', 'No bone IDs available.');
    ui.empty.hidden = false;
  } else if (ui.empty) ui.empty.hidden = true;
}

function syncRange(range, value) {
  if (!range) return;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return;
  if (document.activeElement !== range.input) range.input.value = String(numeric);
  range.valueNode.textContent = numeric.toFixed(2);
}

function syncWeightControls(state = latestWeightState || getModelWeightState()) {
  if (!ui) return;
  latestWeightState = state;
  syncBoneOptions(state);
  ui.boneButton.textContent = selectedLabel(state);
  const available = (state.sources || []).some(source => source.availableBoneIds?.length);
  ui.boneButton.disabled = !state.loaded || !available;
  ui.clearSelection.disabled = !state.selectedBoneCount;
  ui.saveSelection.disabled = !state.selectedBoneCount || state.savingSelection;
  ui.loadSelection.disabled = !state.savedBones?.length;
  ui.allBones.classList.toggle('active', state.pickerViewMode !== 'picked');
  ui.allBones.setAttribute('aria-pressed', String(state.pickerViewMode !== 'picked'));
  ui.pickedPoint.classList.toggle('active', state.pickerViewMode === 'picked');
  ui.pickedPoint.setAttribute('aria-pressed', String(state.pickerViewMode === 'picked'));
  ui.pickedPoint.disabled = !state.pickedPoint;
  ui.heatmap.checked = !!state.heatmapEnabled;
  ui.heatmap.disabled = !state.loaded;
  ui.physicsReset.disabled = !state.loaded;
  syncBoneFilter(state);
}

function syncPhysicsControls(state = getModelPhysicsState()) {
  if (!ui) return;
  ui.physicsGravityEnable.checked = !!state.gravityEnabled;
  ui.physicsConstraintsEnable.checked = !!state.constraintsEnabled;
  syncRange(ui.ranges.frequency, state.frequencyHz);
  syncRange(ui.ranges.damping, state.dampingRatio);
  syncRange(ui.ranges.motion, state.angularResponse);
  syncRange(ui.ranges.linear, state.translationResponse);
  syncRange(ui.ranges.continuous, state.velocityResponse);
  syncRange(ui.ranges.gravity, state.gravityScale);
  syncRange(ui.ranges.maxBend, state.maxBendDegrees);
}

function syncRigOptions(state = latestRigState || getModelRigState()) {
  if (!ui?.joint) return;
  latestRigState = state;
  const model = state?.model;
  const joints = model?.joints || [];
  const selected = selectedJoint(state);
  const optionKey = JSON.stringify([
    model?.structureRevision ?? state?.structureRevision ?? null,
    joints.map(joint => joint.jointId),
  ]);
  if (optionKey !== ui.joint.dataset.optionKey) {
    ui.joint.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Select a joint';
    placeholder.disabled = true;
    placeholder.selected = true;
    ui.joint.appendChild(placeholder);
    joints.forEach(item => {
      const option = document.createElement('option');
      option.value = String(item.jointId);
      option.textContent = `Joint ${item.jointId}`;
      ui.joint.appendChild(option);
    });
    ui.joint.dataset.optionKey = optionKey;
  }
  ui.joint.value = selected ? String(selected.jointId) : '';
  ui.joint.disabled = !state?.loaded || !joints.length;
  ui.visible.checked = !!state?.visible;
  ui.visible.disabled = !state?.loaded || !joints.length;
  ui.showAll.checked = state?.overlayScope !== 'selection';
  ui.showAll.disabled = !state?.loaded || !joints.length;
  ui.snap.value = String(state?.rotationSnapDegrees ?? 0);
  ui.snap.disabled = !state?.loaded || !joints.length;
  const hasSelected = !!selected;
  ui.clearJoint.disabled = !hasSelected;
  ui.setRoot.disabled = !hasSelected;
  ui.resetJoint.disabled = !hasSelected;
  ui.resetPose.disabled = !state?.loaded;
  syncHierarchy(state, selected);
  syncRotationReadout(state);
  syncPresetControls(state);
}

function syncHierarchy(state, joint) {
  const model = state?.model;
  const id = joint?.jointId;
  const component = id === undefined ? null : componentForJoint(model, id);
  ui.root.textContent = component ? String(component.rootId) : '—';
  ui.depth.textContent = component ? String(component.depthById?.[id] ?? '—') : '—';
  ui.parent.replaceChildren();
  ui.children.replaceChildren();
  if (!component || id === undefined) {
    addText(ui.parent, 'rig-value', '—');
    addText(ui.children, 'rig-value', '—');
    return;
  }
  const parentId = component.parentById?.[id];
  if (parentId === null || parentId === undefined) addText(ui.parent, 'rig-value', '—');
  else addNavigationButton(ui.parent, Number(parentId));
  const children = component.childrenById?.[id] || [];
  if (!children.length) addText(ui.children, 'rig-value', '—');
  else children.forEach(childId => addNavigationButton(ui.children, Number(childId)));
}

function addNavigationButton(parent, jointId) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'rig-nav-button';
  button.textContent = String(jointId);
  button.addEventListener('click', () => selectRigJoint(jointId));
  parent.appendChild(button);
}

function syncRotationReadout(state, localOverride = null) {
  const joint = selectedJoint(state);
  const id = Number(joint?.jointId);
  const frame = Number.isInteger(id) ? getRigJointPoseFrame(id) : null;
  const local = localOverride?.length === 4 ? localOverride
    : state?.model?.poseRotationByJointId?.[id] || [0, 0, 0, 1];
  if (!frame?.restRotation || !Number.isInteger(id)) {
    ui.rotationValues.forEach(value => { value.textContent = '—'; });
    return;
  }
  const euler = eulerFromRestFrameDelta(local, frame.restRotation, 'XYZ');
  [euler.x, euler.y, euler.z].forEach((value, index) => {
    ui.rotationValues[index].textContent = `${(value * 180 / Math.PI).toFixed(1)}°`;
  });
}

function selectedPreset(state = latestRigState, id = ui?.preset?.value) {
  const presetState = state?.rigPresets || {};
  return (presetState.presets || []).find(item => item.id === id) || null;
}

function syncPresetControls(state) {
  const presetState = state?.rigPresets || {};
  const presets = presetState.presets || [];
  const optionKey = JSON.stringify({
    presets: presets.map(item => [item.id, item.name]),
  });
  if (optionKey !== ui.preset.dataset.optionKey) {
    ui.preset.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Select a pose';
    placeholder.disabled = true;
    ui.preset.appendChild(placeholder);
    const group = document.createElement('optgroup');
    group.label = 'My Poses';
    if (!presets.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'No saved poses';
      option.disabled = true;
      group.appendChild(option);
    } else presets.forEach(item => {
      const option = document.createElement('option');
      option.value = item.id;
      option.textContent = item.name;
      group.appendChild(option);
    });
    ui.preset.appendChild(group);
    ui.preset.dataset.optionKey = optionKey;
  }
  ui.preset.value = presetState.selectedPresetId || '';
  const current = selectedPreset(state, ui.preset.value);
  const hasPreset = !!current;
  ui.preset.disabled = !state?.loaded || !presets.length
    || !!presetState.loading;
  ui.savePreset.disabled = !state?.loaded;
  ui.renamePreset.disabled = !hasPreset;
  ui.deletePreset.disabled = !hasPreset;
  if (presetState.error) ui.presetStatus.textContent = presetState.error;
  else ui.presetStatus.textContent = '';
}

function applySelectedPreset(presetId) {
  const result = applyRigPosePresetById(presetId);
  if (result?.success) {
    ui.presetStatus.textContent = '';
    return;
  }
  ui.presetStatus.textContent = 'Could not apply this pose.';
}

async function savePreset() {
  const name = await inputConfirmDialog('Save pose preset as:', '');
  if (!name) return;
  const result = await saveRigPosePreset(name);
  ui.presetStatus.textContent = result?.saved ? ''
    : result?.error || 'Could not save this pose.';
}

async function renamePreset() {
  const current = latestRigState?.rigPresets?.presets?.find(item => item.id === ui.preset.value);
  if (!current) return;
  const name = await inputConfirmDialog('Rename pose preset:', current.name);
  if (!name) return;
  const result = await renameRigPosePreset(current.id, name);
  ui.presetStatus.textContent = result?.saved ? ''
    : result?.error || 'Could not rename this pose.';
}

async function deletePreset() {
  const current = latestRigState?.rigPresets?.presets?.find(item => item.id === ui.preset.value);
  if (!current || !await confirmDialog(`Delete pose preset "${current.name}"?`)) return;
  const result = await deleteRigPosePreset(current.id);
  ui.presetStatus.textContent = result?.saved ? ''
    : result?.error || 'Could not delete this pose.';
}

function syncPicker() {
  const active = !!(latestWeightState?.picking || latestRigState?.picking);
  ui.pickModel.textContent = active ? 'Cancel picking' : 'Pick from model';
  ui.pickModel.classList.toggle('active', active);
  ui.pickModel.setAttribute('aria-pressed', String(active));
  ui.pickModel.disabled = !active && (
    !latestWeightState?.loaded || !latestRigState?.loaded
    || !(latestWeightState.sources || []).some(source => source.availableBoneIds?.length));
}

function syncStatus() {
  const weight = latestWeightState || getModelWeightState();
  const rig = latestRigState || getModelRigState();
  let status = '';
  if (weight.loading || rig.loading) status = 'Loading weights and rig…';
  else if (weight.error) status = weight.error;
  else if (rig.error) status = rig.error;
  else if (weight.loaded && (!weight.sources?.length || weight.noWeights)) {
    status = 'No skin weights available for this model.';
  } else if (weight.selectionSaveError) {
    status = `Could not save bone selection: ${weight.selectionSaveError}`;
  } else if (!weight.loaded && !rig.loaded) status = '';
  if (weight.pickStatus || rig.pickStatus) status = weight.pickStatus || rig.pickStatus;
  ui.status.textContent = status;
}

function closePopover() {
  if (!ui?.popover || ui.popover.hidden) return;
  ui.popover.hidden = true;
  ui.boneButton.setAttribute('aria-expanded', 'false');
}

function loadOnDemand() {
  if (loadingPromise) return loadingPromise;
  loadingPromise = ensureModelRigLoaded().finally(() => { loadingPromise = null; });
  return loadingPromise;
}

export function initWeightRigPanel() {
  panel = $('weight-rig-panel');
  if (!panel) return;
  buildPanel();
  window.addEventListener('mod-viewer-model-weight-changed', event => {
    latestWeightState = event.detail;
    syncWeightControls(event.detail);
    syncStatus();
    syncPicker();
  });
  window.addEventListener('mod-viewer-model-physics-changed', event => {
    syncPhysicsControls(event.detail);
    syncRigOptions(latestRigState);
  });
  window.addEventListener('mod-viewer-model-rig-changed', event => {
    latestRigState = event.detail;
    if (!event.detail?.loading && !event.detail?.loaded) loadingPromise = null;
    syncRigOptions(event.detail);
    syncStatus();
    syncPicker();
  });
  window.addEventListener('mod-viewer-model-rig-pose-changed', event => {
    if (latestRigState?.selectedJointId !== null
        && Number(event.detail?.jointId) === Number(latestRigState.selectedJointId)) {
      syncRotationReadout(latestRigState, event.detail?.quaternion);
    }
  });
  window.addEventListener('mod-viewer-model-point-picked', () => {
    latestWeightState = getModelWeightState();
    latestRigState = getModelRigState();
    syncWeightControls(latestWeightState);
    syncRigOptions(latestRigState);
    syncStatus();
    if (latestWeightState?.pickedPoint) {
      ui.boneList.scrollTop = 0;
      ui.popover.hidden = false;
      ui.boneButton.setAttribute('aria-expanded', 'true');
    }
  });
  window.addEventListener('mod-viewer-right-dock-tab-changed', event => {
    const active = event.detail?.tab === 'weight-rig' && event.detail?.open;
    if (!active) {
      closePopover();
      if (latestWeightState?.picking || latestRigState?.picking) cancelModelPicking();
    } else void loadOnDemand();
  });
  document.addEventListener('pointerdown', event => {
    if (ui?.popover?.hidden || ui.picker.contains(event.target)) return;
    if (event.target.closest?.('#canvas-container canvas, .draw-item')) return;
    closePopover();
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (latestWeightState?.picking || latestRigState?.picking) {
      cancelModelPicking();
    }
    if (!ui?.popover?.hidden) {
      closePopover();
      ui.boneButton.focus();
    }
  });
  latestWeightState = getModelWeightState();
  latestRigState = getModelRigState();
  syncWeightControls(latestWeightState);
  syncPhysicsControls();
  syncRigOptions(latestRigState);
  syncStatus();
  syncPicker();
}
