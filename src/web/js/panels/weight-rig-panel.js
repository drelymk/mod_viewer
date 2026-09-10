// Unified Weight/Rig controls. The panel owns one stable DOM tree for both
// domains while keeping Weight and Rig picking lifecycles separate.

import {
  beginWeightModelPicking, cancelWeightModelPicking, clearSelectedBones,
  ensureModelRigLoaded, getModelPhysicsState, getModelRigState,
  getModelWeightState, loadSavedBoneSelection,
  clearRigJointSelection, resetModelPhysics, resetRigJoint, resetRigPose,
  saveModelWeightSelection,
  selectRigJoint, setBoneSelected, setPhysicsConstraintsEnabled,
  setPhysicsContinuousLinearResponse, setPhysicsDamping, setPhysicsFrequency,
  setPhysicsGravityEnabled, setPhysicsGravityScale, setPhysicsLinearMotionStrength,
  setPhysicsMaxBendDegrees, setPhysicsMotionStrength, setModelWeightHeatmap,
  setRigActiveLimbRole,
  setRigLimbOverride, beginRigJointPicking, cancelRigJointPicking,
  clearRigLimbMapping, flipRigLimbBend, setRigIkEnabled,
  autoDetectHumanoidLimbs,
  setRigJointRoot,
  setRigRotationSnapDegrees, setWeightPickerViewMode,
  applyRigPosePresetById,
  deleteRigPosePreset, renameRigPosePreset,
  saveRigPosePreset,
} from '../mesh/weight-experiment.js';
import { confirmDialog, inputConfirmDialog } from '../ui/dialogs.js';

let panel = null;
let ui = null;
let loadingPromise = null;
let latestWeightState = null;
let latestRigState = null;

const $ = id => document.getElementById(id);
const LIMB_ROLES = Object.freeze([
  ['left_arm', 'Left Arm'], ['right_arm', 'Right Arm'],
  ['left_leg', 'Left Leg'], ['right_leg', 'Right Leg'],
]);
const LIMB_LABELS = Object.freeze({
  left_arm: ['Shoulder', 'Elbow', 'Hand'],
  right_arm: ['Shoulder', 'Elbow', 'Hand'],
  left_leg: ['Hip', 'Knee', 'Foot'],
  right_leg: ['Hip', 'Knee', 'Foot'],
});

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

function buildWeightModelPicker(section) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'ui-button weight-rig-primary-action weight-pick-model';
  button.textContent = 'Pick from model';
  button.setAttribute('aria-pressed', 'false');
  button.addEventListener('click', () => {
    closePopover();
    if (latestWeightState?.picking) cancelWeightModelPicking();
    else beginWeightModelPicking();
  });
  section.appendChild(button);
  ui.weightPick = button;
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
  buildWeightModelPicker(section);

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

  const pickJoint = document.createElement('button');
  pickJoint.type = 'button';
  pickJoint.className = 'ui-button weight-rig-primary-action rig-pick-joint';
  pickJoint.textContent = 'Pick from model';
  pickJoint.setAttribute('aria-pressed', 'false');
  pickJoint.addEventListener('click', () => {
    closePopover();
    const current = latestRigState?.jointPickIntent;
    if (current?.type === 'selected-joint') cancelRigJointPicking();
    else beginRigJointPicking({type: 'selected-joint'});
  });
  section.appendChild(pickJoint);
  ui.rigPickJoint = pickJoint;

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
  ui.presetStatus.setAttribute('aria-live', 'polite');

  const advanced = addAdvanced(parent, 'Rig Advanced Settings');
  const inverseKinematics = addRigAdvancedGroup(
    advanced.content, 'Limb IK');
  const limbRow = document.createElement('label');
  limbRow.className = 'rig-row';
  addText(limbRow, 'rig-label', 'Limb');
  const limb = document.createElement('select');
  limb.className = 'rig-limb-select';
  limb.setAttribute('aria-label', 'Active limb');
  LIMB_ROLES.forEach(([role, label]) => {
    const option = document.createElement('option');
    option.value = role;
    option.textContent = label;
    limb.appendChild(option);
  });
  limb.addEventListener('change', () => setRigActiveLimbRole(limb.value));
  limbRow.appendChild(limb);
  inverseKinematics.appendChild(limbRow);
  ui.limb = limb;

  const autoDetect = document.createElement('button');
  autoDetect.type = 'button';
  autoDetect.className = 'ui-button rig-auto-detect-limbs';
  autoDetect.textContent = 'Refresh humanoid rig diagnostics';
  autoDetect.addEventListener('click', () => {
    autoDetect.disabled = true;
    void Promise.resolve().then(() => autoDetectHumanoidLimbs())
      .catch(error => ({reason: error?.message || 'detection_failed'}))
      .then(result => {
        syncRigOptions(latestRigState || getModelRigState());
      });
  });
  inverseKinematics.appendChild(autoDetect);
  ui.autoDetect = autoDetect;

  ui.humanoidStatus = addText(inverseKinematics, 'rig-humanoid-status');
  ui.humanoidStatus.setAttribute('aria-live', 'polite');

  const mapping = document.createElement('div');
  mapping.className = 'rig-limb-mapping';
  const makeMappingRow = (kind, label, actionText, actionClass) => {
    const row = document.createElement('div');
    row.className = `rig-limb-mapping-row ${kind}`;
    addText(row, 'rig-label', label);
    const value = addText(row, 'rig-limb-value');
    const action = document.createElement('button');
    action.type = 'button';
    action.className = `ui-button rig-limb-pick ${actionClass}`;
    action.textContent = actionText;
    action.addEventListener('click', () => {
      const role = latestRigState?.ik?.activeLimbRole || limb.value;
      const intent = {type: kind, role};
      const current = latestRigState?.jointPickIntent;
      if (current?.type === intent.type && current?.role === intent.role) {
        cancelRigJointPicking();
      } else {
        beginRigJointPicking(intent);
      }
    });
    row.appendChild(action);
    mapping.appendChild(row);
    return {value, action};
  };
  const anchorRow = makeMappingRow(
    'limb-anchor', 'Shoulder', 'Pick', 'rig-limb-anchor-pick');
  const bendRow = makeMappingRow(
    'limb-bend-override', 'Elbow', 'Override', 'rig-limb-bend-pick');
  const endRow = makeMappingRow(
    'limb-end-override', 'Hand', 'Override', 'rig-limb-end-pick');
  inverseKinematics.appendChild(mapping);
  ui.limbMapping = mapping;
  ui.limbAnchor = anchorRow.value;
  ui.limbBend = bendRow.value;
  ui.limbEnd = endRow.value;
  ui.limbAnchorPick = anchorRow.action;
  ui.limbBendPick = bendRow.action;
  ui.limbEndPick = endRow.action;

  addText(inverseKinematics, 'rig-label', 'Detected path');
  ui.pathPreview = addText(inverseKinematics, 'rig-chain-preview', '—');

  const mappingActions = document.createElement('div');
  mappingActions.className = 'rig-actions rig-limb-actions';
  const clearMapping = document.createElement('button');
  clearMapping.type = 'button';
  clearMapping.className = 'ui-button rig-clear-limb';
  clearMapping.textContent = 'Clear';
  clearMapping.addEventListener('click', () => clearRigLimbMapping(limb.value));
  mappingActions.append(clearMapping);
  inverseKinematics.appendChild(mappingActions);
  ui.clearLimb = clearMapping;

  const ikLabel = document.createElement('label');
  ikLabel.className = 'weight-checkbox';
  const ik = document.createElement('input');
  ik.type = 'checkbox';
  ik.className = 'rig-panel-enable-ik';
  ik.addEventListener('change', () => setRigIkEnabled(ik.checked));
  ikLabel.appendChild(ik);
  addText(ikLabel, 'weight-label', 'Enable IK');
  inverseKinematics.appendChild(ikLabel);
  ui.ik = ik;

  const flip = document.createElement('button');
  flip.type = 'button';
  flip.className = 'ui-button rig-flip-bend';
  flip.textContent = 'Flip Bend';
  flip.addEventListener('click', () => flipRigLimbBend(limb.value));
  inverseKinematics.appendChild(flip);
  ui.flipBend = flip;
  ui.ikHint = addText(inverseKinematics, 'rig-hint');

  const manualRotation = addRigAdvancedGroup(
    advanced.content, 'Manual Rotation');
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
  manualRotation.appendChild(snapRow);
  ui.snap = snap;

  const hierarchy = addRigAdvancedGroup(advanced.content, 'Rig Structure');
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
  ui.weightPick.textContent = state.picking ? 'Cancel picking' : 'Pick from model';
  ui.weightPick.classList.toggle('active', !!state.picking);
  ui.weightPick.setAttribute('aria-pressed', String(!!state.picking));
  ui.weightPick.disabled = !state.loaded || !available;
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
  const jointPickActive = state?.jointPickIntent?.type === 'selected-joint';
  ui.rigPickJoint.textContent = jointPickActive ? 'Cancel picking' : 'Pick from model';
  ui.rigPickJoint.classList.toggle('active', jointPickActive);
  ui.rigPickJoint.setAttribute('aria-pressed', String(jointPickActive));
  ui.rigPickJoint.disabled = !state?.loaded || !joints.length;
  ui.snap.value = String(state?.rotationSnapDegrees ?? 0);
  ui.snap.disabled = !state?.loaded || !joints.length || !!state?.ik?.enabled;
  const ik = state?.ik || {};
  const role = ik.activeLimbRole || 'left_arm';
  const labels = LIMB_LABELS[role] || LIMB_LABELS.left_arm;
  const mapping = ik.mappings?.[role] || {};
  const hasMapping = Number.isInteger(mapping.anchorJointId);
  const hasLimb = !!mapping.available;
  const hasSelected = !!selected;
  ui.limbAnchor.previousElementSibling.textContent = labels[0];
  ui.limbBend.previousElementSibling.textContent = labels[1];
  ui.limbEnd.previousElementSibling.textContent = labels[2];
  ui.limb.value = role;
  ui.limb.disabled = !state?.loaded || !joints.length;
  ui.limbAnchor.textContent = mapping.anchorJointId
    ? `Joint ${mapping.anchorJointId} · Manual` : 'Unassigned';
  ui.limbBend.textContent = mapping.bendJointId
    ? `Joint ${mapping.bendJointId} · ${mapping.bendSource === 'override'
      ? 'Override' : 'Auto'}` : '—';
  ui.limbEnd.textContent = mapping.endJointId
    ? `Joint ${mapping.endJointId} · ${mapping.endSource === 'override'
      ? 'Override' : 'Auto'}` : '—';
  const pathIds = mapping.pathJointIds || [];
  ui.pathPreview.textContent = pathIds.length
    ? pathIds.map(id => `Joint ${id}`).join(' → ') : '—';
  const pick = state?.jointPickIntent;
  const pickButton = (button, type, enabled, idleText) => {
    const active = pick?.type === type && pick?.role === role;
    button.textContent = active ? 'Cancel' : idleText;
    button.classList.toggle('active', active);
    button.disabled = !state?.loaded || !enabled;
  };
  pickButton(ui.limbAnchorPick, 'limb-anchor', true, 'Pick');
  pickButton(ui.limbBendPick, 'limb-bend-override', hasLimb, 'Override');
  pickButton(ui.limbEndPick, 'limb-end-override', hasMapping, 'Override');
  ui.clearLimb.disabled = !state?.loaded || !hasMapping;
  ui.ik.checked = !!ik.enabled;
  ui.ik.disabled = !state?.loaded || !hasLimb;
  ui.flipBend.disabled = !state?.loaded || !hasLimb;
  ui.autoDetect.disabled = false;
  ui.humanoidStatus.textContent = ik?.humanoid?.status || '';
  if (!hasMapping) {
    ui.ikHint.textContent = `Pick the ${labels[0]} joint to configure this limb.`;
  }
  else if (!hasLimb) ui.ikHint.textContent = `IK unavailable: ${mapping.reason || 'limb path not detected'}.`;
  else ui.ikHint.textContent = `${mapping.confidence || 'medium'} confidence · drag the ${labels[2]} target.`;
  ui.clearJoint.disabled = !hasSelected;
  ui.setRoot.disabled = !hasSelected;
  ui.resetJoint.disabled = !hasSelected;
  ui.resetJoint.textContent = state?.ik?.enabled && hasLimb
      && selected?.jointId === mapping.endJointId
    ? 'Reset Limb' : 'Reset Joint';
  ui.resetPose.disabled = !state?.loaded;
  syncPresetControls(state);
}

function selectedPreset(state = latestRigState, id = ui?.preset?.value) {
  const presetState = state?.rigPresets || {};
  return (presetState.presets || []).find(item => item.id === id) || null;
}

const PRESET_SKIP_REASON_LABELS = Object.freeze({
  joint_not_found: 'no matching joint',
  root_not_found: 'no matching root',
  ambiguous_joint_signature: 'ambiguous match',
  duplicate_joint_entry: 'duplicate entry',
  duplicate_root_entry: 'duplicate entry',
  invalid_signature: 'invalid joint identity',
  invalid_rotation: 'invalid rotation',
  invalid_preset: 'invalid saved pose',
});

function pluralizePresetCount(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function presetSkipLabel(item) {
  return PRESET_SKIP_REASON_LABELS[item?.reason]
    || (item?.type === 'root' ? 'could not match root' : 'could not match joint');
}

function formatPresetApplication(result) {
  if (!result) return '';
  const skipped = Array.isArray(result.skipped) ? result.skipped : [];
  const skippedReasons = new Map();
  skipped.forEach(item => {
    const label = presetSkipLabel(item);
    skippedReasons.set(label, (skippedReasons.get(label) || 0) + 1);
  });
  const applied = [];
  if (result.appliedJointCount) {
    applied.push(pluralizePresetCount(
      result.appliedJointCount, 'joint rotation'));
  }
  if (result.appliedRootCount) {
    applied.push(pluralizePresetCount(result.appliedRootCount, 'root'));
  }
  const skippedParts = [];
  if (result.skippedJointCount) {
    skippedParts.push(pluralizePresetCount(result.skippedJointCount, 'joint'));
  }
  if (result.skippedRootCount) {
    skippedParts.push(pluralizePresetCount(result.skippedRootCount, 'root'));
  }
  const reasonText = [...skippedReasons.entries()]
    .map(([label, count]) => count > 1 ? `${label} (${count})` : label)
    .join('; ');
  if (!result.success) {
    return `Could not apply this pose${reasonText ? `: ${reasonText}` : '.'}`;
  }
  if (!skippedParts.length) {
    return applied.length ? `Applied ${applied.join(' and ')}.`
      : 'Applied pose.';
  }
  return `Applied ${applied.length ? applied.join(' and ') : 'nothing'}. `
    + `Skipped ${skippedParts.join(' and ')}`
    + `${reasonText ? `: ${reasonText}` : '.'}`;
}

function presetFeedback(state) {
  const presetState = state?.rigPresets || {};
  if (presetState.error) return presetState.error;
  return formatPresetApplication(presetState.lastApplyResult);
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
  ui.presetStatus.textContent = presetFeedback(state);
}

function applySelectedPreset(presetId) {
  return applyRigPosePresetById(presetId);
}

async function savePreset() {
  const name = await inputConfirmDialog('Save pose preset as:', '');
  if (!name) return;
  const result = await saveRigPosePreset(name);
  ui.presetStatus.textContent = result?.saved ? presetFeedback(latestRigState)
    : result?.error || 'Could not save this pose.';
}

async function renamePreset() {
  const current = latestRigState?.rigPresets?.presets?.find(item => item.id === ui.preset.value);
  if (!current) return;
  const name = await inputConfirmDialog('Rename pose preset:', current.name);
  if (!name) return;
  const result = await renameRigPosePreset(current.id, name);
  ui.presetStatus.textContent = result?.saved ? presetFeedback(latestRigState)
    : result?.error || 'Could not rename this pose.';
}

async function deletePreset() {
  const current = latestRigState?.rigPresets?.presets?.find(item => item.id === ui.preset.value);
  if (!current || !await confirmDialog(`Delete pose preset "${current.name}"?`)) return;
  const result = await deleteRigPosePreset(current.id);
  ui.presetStatus.textContent = result?.saved ? presetFeedback(latestRigState)
    : result?.error || 'Could not delete this pose.';
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
  });
  window.addEventListener('mod-viewer-model-rig-pose-changed', event => {
    if (latestRigState) syncRigOptions(latestRigState);
  });
  window.addEventListener('mod-viewer-weight-point-picked', () => {
    latestWeightState = getModelWeightState();
    syncWeightControls(latestWeightState);
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
      if (latestWeightState?.picking) cancelWeightModelPicking();
      if (latestRigState?.jointPickIntent) cancelRigJointPicking();
    } else void loadOnDemand();
  });
  document.addEventListener('pointerdown', event => {
    if (ui?.popover?.hidden || ui.picker.contains(event.target)) return;
    if (event.target.closest?.('#canvas-container canvas, .draw-item')) return;
    closePopover();
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (latestWeightState?.picking) cancelWeightModelPicking();
    if (latestRigState?.jointPickIntent) cancelRigJointPicking();
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
}
