// Unified Weight/Rig controls. The panel owns one stable DOM tree for both
// domains while keeping Weight and Rig picking lifecycles separate.

import {weightRigApi} from '../mesh/weight-rig-core.js';
const {
  beginWeightModelPicking, cancelWeightModelPicking, clearSelectedBones,
  ensureModelRigLoaded, ensureModelWeightsLoaded, getModelPhysicsState,
  getModelRigState,
  getModelWeightState, loadSavedBoneSelection,
  clearRigJointSelection, resetModelPhysics, resetRigJoint, resetRigPose,
  saveModelWeightSelection,
  selectRigJoint, setBoneSelected, setPhysicsConstraintsEnabled,
  setPhysicsContinuousLinearResponse, setPhysicsDamping, setPhysicsFrequency,
  setPhysicsGravityEnabled, setPhysicsGravityScale, setPhysicsLinearMotionStrength,
  setPhysicsMaxBendDegrees, setPhysicsMotionStrength, setModelWeightHeatmap,
  beginRigJointPicking, cancelRigJointPicking, setRigIkEnabled,
  setRigJointRoot,
  setRigRotationSnapDegrees, setWeightPickerViewMode,
  beginHumanoidRigEdit, cancelHumanoidRigEdit, saveHumanoidRigEdit,
  resetHumanoidRig,
  applyRigPosePresetById,
  deleteRigPosePreset, renameRigPosePreset,
  saveRigPosePreset,
} = weightRigApi;
import {HUMANOID_CONTROL_KEYS} from '../mesh/humanoid-control-rig.js';
import { confirmDialog, inputConfirmDialog } from '../ui/dialogs.js';
import {LANGUAGE_CHANGED, applyTranslations, getLocale, t} from '../i18n/index.js';

let panel = null;
let ui = null;
let loadingPromise = null;
let latestWeightState = null;
let latestRigState = null;

const $ = id => document.getElementById(id);
const HUMANOID_CONTROL_LABEL_KEYS = Object.freeze({
  chest: 'weightRig.control.chest', pelvis: 'weightRig.control.pelvis',
  neck: 'weightRig.control.neck', head: 'weightRig.control.head',
  leftShoulder: 'weightRig.control.leftShoulder',
  leftElbow: 'weightRig.control.leftElbow',
  leftHand: 'weightRig.control.leftHand',
  rightShoulder: 'weightRig.control.rightShoulder',
  rightElbow: 'weightRig.control.rightElbow',
  rightHand: 'weightRig.control.rightHand',
  leftHip: 'weightRig.control.leftHip', leftKnee: 'weightRig.control.leftKnee',
  leftFoot: 'weightRig.control.leftFoot', rightHip: 'weightRig.control.rightHip',
  rightKnee: 'weightRig.control.rightKnee', rightFoot: 'weightRig.control.rightFoot',
});

function humanoidControlLabel(key) {
  const translationKey = HUMANOID_CONTROL_LABEL_KEYS[key];
  return translationKey ? t(translationKey) : key || '';
}

function statusText(status) {
  if (!status) return '';
  if (typeof status === 'string') return status;
  return status.messageKey ? t(status.messageKey, status.messageParams) : '';
}

function addText(parent, className, value = '', translationKey = null) {
  const node = document.createElement('span');
  node.className = className;
  if (translationKey) node.dataset.i18n = translationKey;
  node.textContent = translationKey ? t(translationKey) : value;
  parent.appendChild(node);
  return node;
}

function addSection(parent, title) {
  const section = document.createElement('section');
  section.className = 'weight-rig-section';
  const key = title === 'WEIGHT' ? 'weightRig.weight' : 'weightRig.rig';
  addText(section, 'weight-rig-section-title', '', key);
  parent.appendChild(section);
  return section;
}

function addAdvanced(parent) {
  const details = document.createElement('details');
  details.className = 'weight-rig-advanced';
  const summary = document.createElement('summary');
  summary.dataset.i18n = 'weightRig.advancedSettings';
  summary.textContent = t('weightRig.advancedSettings');
  details.appendChild(summary);
  const content = document.createElement('div');
  content.className = 'weight-rig-advanced-content';
  details.appendChild(content);
  parent.appendChild(details);
  return {details, content};
}

function selectedLabel(state) {
  const entries = state?.selectedBones || [];
  if (!entries.length) return t('weightRig.selectBones');
  const count = Number(state.selectedBoneCount)
    || entries.reduce((total, entry) => total + entry.boneIds.length, 0);
  if (entries.length === 1 && count <= 4) return entries[0].boneIds.join(', ');
  return t('weightRig.bonesSelected', {count});
}

function addRange(parent, className, label, min, max, step, value, onInput) {
  const field = document.createElement('label');
  field.className = 'weight-field';
  const header = document.createElement('div');
  header.className = 'weight-range-header';
  addText(header, 'weight-label', '', label);
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
  button.dataset.i18n = 'weightRig.pickFromModel';
  button.textContent = t('weightRig.pickFromModel');
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
  const label = addText(section, 'weight-rig-control-label', '', 'weightRig.authoredSource');
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
  popover.dataset.i18nAriaLabel = 'weightRig.boneIds';
  popover.setAttribute('aria-label', t('weightRig.boneIds'));
  const search = document.createElement('input');
  search.type = 'search';
  search.className = 'weight-bone-search';
  search.dataset.i18nPlaceholder = 'weightRig.findBoneId';
  search.placeholder = t('weightRig.findBoneId');
  search.dataset.i18nAriaLabel = 'weightRig.findBoneId';
  search.setAttribute('aria-label', t('weightRig.findBoneId'));
  popover.appendChild(search);
  const pickerView = document.createElement('div');
  pickerView.className = 'weight-picker-view';
  pickerView.setAttribute('role', 'group');
  pickerView.dataset.i18nAriaLabel = 'weightRig.boneBrowserOptions';
  pickerView.setAttribute('aria-label', t('weightRig.boneBrowserOptions'));
  const allBones = document.createElement('button');
  allBones.type = 'button';
  allBones.className = 'weight-picker-view-option';
  allBones.dataset.mode = 'all';
  allBones.dataset.i18n = 'weightRig.allBones';
  allBones.textContent = t('weightRig.allBones');
  const pickedPoint = document.createElement('button');
  pickedPoint.type = 'button';
  pickedPoint.className = 'weight-picker-view-option';
  pickedPoint.dataset.mode = 'picked';
  pickedPoint.dataset.i18n = 'weightRig.atPickedPoint';
  pickedPoint.textContent = t('weightRig.atPickedPoint');
  pickerView.append(allBones, pickedPoint);
  popover.appendChild(pickerView);
  const filter = document.createElement('label');
  filter.className = 'weight-checkbox weight-bone-filter';
  const selectedOnly = document.createElement('input');
  selectedOnly.type = 'checkbox';
  selectedOnly.className = 'weight-selected-only';
  filter.appendChild(selectedOnly);
  addText(filter, 'weight-label', '', 'weightRig.selectedBonesOnly');
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
  save.dataset.i18n = 'common.save';
  save.textContent = t('common.save');
  save.addEventListener('click', () => void saveModelWeightSelection());
  const load = document.createElement('button');
  load.type = 'button';
  load.className = 'ui-button weight-load-selection';
  load.dataset.i18n = 'weightRig.load';
  load.textContent = t('weightRig.load');
  load.addEventListener('click', () => loadSavedBoneSelection());
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'ui-button weight-clear-selection';
  clear.dataset.i18n = 'common.clear';
  clear.textContent = t('common.clear');
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
  addText(heatmapLabel, 'weight-label', '', 'weightRig.showHeatmap');
  section.appendChild(heatmapLabel);
  ui.heatmap = heatmap;

  const gravityLabel = document.createElement('label');
  gravityLabel.className = 'weight-checkbox';
  const gravity = document.createElement('input');
  gravity.type = 'checkbox';
  gravity.className = 'weight-physics-gravity-enable';
  gravity.addEventListener('change', () => setPhysicsGravityEnabled(gravity.checked));
  gravityLabel.appendChild(gravity);
  addText(gravityLabel, 'weight-label', '', 'weightRig.gravity');
  section.appendChild(gravityLabel);
  ui.physicsGravityEnable = gravity;

  const advanced = addAdvanced(parent);
  const physicsTitle = addText(advanced.content, 'weight-rig-advanced-title', '', 'weightRig.physicsTuning');
  physicsTitle.setAttribute('aria-hidden', 'true');
  const initial = getModelPhysicsState();
  const ranges = {};
  ranges.frequency = addRange(advanced.content, 'weight-physics-frequency',
    'weightRig.frequency', 0.1, 10, 0.1, initial.frequencyHz, value => setPhysicsFrequency(value));
  ranges.damping = addRange(advanced.content, 'weight-physics-damping', 'weightRig.damping',
    0, 2, 0.05, initial.dampingRatio, value => setPhysicsDamping(value));
  ranges.motion = addRange(advanced.content, 'weight-physics-motion',
    'weightRig.angularResponse', 0, 1, 0.05, initial.angularResponse,
    value => setPhysicsMotionStrength(value));
  ranges.linear = addRange(advanced.content, 'weight-physics-linear',
    'weightRig.translationResponse', 0, 1, 0.05, initial.translationResponse,
    value => setPhysicsLinearMotionStrength(value));
  ranges.continuous = addRange(advanced.content, 'weight-physics-continuous-response',
    'weightRig.velocityResponse', 0, 1, 0.05, initial.velocityResponse,
    value => setPhysicsContinuousLinearResponse(value));
  ranges.gravity = addRange(advanced.content, 'weight-physics-gravity-scale',
    'weightRig.gravityScale', 0, 2, 0.1, initial.gravityScale,
    value => setPhysicsGravityScale(value));

  const constraintsLabel = document.createElement('label');
  constraintsLabel.className = 'weight-checkbox';
  const constraints = document.createElement('input');
  constraints.type = 'checkbox';
  constraints.className = 'weight-physics-constraints-enable';
  constraints.addEventListener('change', () => setPhysicsConstraintsEnabled(constraints.checked));
  constraintsLabel.appendChild(constraints);
  addText(constraintsLabel, 'weight-label', '', 'weightRig.jointLimits');
  advanced.content.appendChild(constraintsLabel);
  ui.physicsConstraintsEnable = constraints;
  ranges.maxBend = addRange(advanced.content, 'weight-physics-max-bend',
    'weightRig.maxBend', 0, 90, 1, initial.maxBendDegrees,
    value => setPhysicsMaxBendDegrees(value));
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button weight-physics-reset';
  reset.dataset.i18n = 'weightRig.resetPhysics';
  reset.textContent = t('weightRig.resetPhysics');
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

function buildMainRigControls(parent) {
  const mainRig = document.createElement('div');
  mainRig.className = 'rig-main-edit';
  const mainTitle = addText(mainRig, 'weight-rig-control-label', '', 'weightRig.mainRig');
  const normalActions = document.createElement('div');
  normalActions.className = 'rig-actions rig-main-actions';
  const editRig = document.createElement('button');
  editRig.type = 'button';
  editRig.className = 'ui-button weight-rig-primary-action rig-edit-main';
  editRig.dataset.i18n = 'weightRig.editRig';
  editRig.textContent = t('weightRig.editRig');
  editRig.addEventListener('click', () => {
    closePopover();
    if (latestRigState?.humanoidRigEdit?.editing) cancelHumanoidRigEdit();
    else beginHumanoidRigEdit();
  });
  const resetRig = document.createElement('button');
  resetRig.type = 'button';
  resetRig.className = 'ui-button rig-reset-main';
  resetRig.dataset.i18n = 'weightRig.resetRig';
  resetRig.textContent = t('weightRig.resetRig');
  resetRig.addEventListener('click', async () => {
    if (!await confirmDialog(t('weightRig.confirm.resetRig'))) return;
    await resetHumanoidRig();
  });
  normalActions.append(editRig, resetRig);
  mainRig.appendChild(normalActions);
  const editStatus = document.createElement('div');
  editStatus.className = 'rig-edit-status';
  editStatus.setAttribute('aria-live', 'polite');
  const editCount = addText(editStatus, 'rig-edit-count');
  const editControl = addText(editStatus, 'rig-edit-control');
  const editConnection = addText(editStatus, 'rig-edit-connection');
  const editHint = addText(editStatus, 'rig-edit-hint', '', 'weightRig.clickPointToMove');
  const editActions = document.createElement('div');
  editActions.className = 'rig-actions rig-edit-actions';
  const cancelEdit = document.createElement('button');
  cancelEdit.type = 'button';
  cancelEdit.className = 'ui-button rig-cancel-edit';
  cancelEdit.dataset.i18n = 'common.cancel';
  cancelEdit.textContent = t('common.cancel');
  cancelEdit.addEventListener('click', () => cancelHumanoidRigEdit());
  const saveEdit = document.createElement('button');
  saveEdit.type = 'button';
  saveEdit.className = 'ui-button weight-rig-primary-action rig-save-edit';
  saveEdit.dataset.i18n = 'common.save';
  saveEdit.textContent = t('common.save');
  saveEdit.addEventListener('click', () => { void saveHumanoidRigEdit(); });
  editActions.append(cancelEdit, saveEdit);
  editStatus.appendChild(editActions);
  mainRig.appendChild(editStatus);
  parent.appendChild(mainRig);
  ui.editRig = editRig;
  ui.resetRig = resetRig;
  ui.mainTitle = mainTitle;
  ui.editStatus = editStatus;
  ui.editCount = editCount;
  ui.editControl = editControl;
  ui.editConnection = editConnection;
  ui.editHint = editHint;
  ui.cancelEdit = cancelEdit;
  ui.saveEdit = saveEdit;
}

function buildRigSection(parent) {
  const section = addSection(parent, 'RIG');

  addText(section, 'weight-rig-control-label', '', 'weightRig.selectedJoint');
  const joint = document.createElement('select');
  joint.className = 'rig-bone-select';
  joint.dataset.i18nAriaLabel = 'weightRig.selectedModelJoint';
  joint.setAttribute('aria-label', t('weightRig.selectedModelJoint'));
  joint.addEventListener('change', () => {
    if (joint.value !== '') selectRigJoint(Number(joint.value));
  });
  section.appendChild(joint);
  ui.joint = joint;

  const pickJoint = document.createElement('button');
  pickJoint.type = 'button';
  pickJoint.className = 'ui-button weight-rig-primary-action rig-pick-joint';
  pickJoint.dataset.i18n = 'weightRig.pickFromModel';
  pickJoint.textContent = t('weightRig.pickFromModel');
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
  clear.dataset.i18n = 'common.clear';
  clear.textContent = t('common.clear');
  clear.addEventListener('click', () => clearRigJointSelection());
  const resetJoint = document.createElement('button');
  resetJoint.type = 'button';
  resetJoint.className = 'ui-button rig-reset-joint';
  resetJoint.dataset.i18n = 'weightRig.resetJoint';
  resetJoint.textContent = t('weightRig.resetJoint');
  resetJoint.addEventListener('click', () => {
    const selected = selectedJoint();
    if (selected) resetRigJoint(selected.jointId);
  });
  const resetPose = document.createElement('button');
  resetPose.type = 'button';
  resetPose.className = 'ui-button rig-reset-pose';
  resetPose.dataset.i18n = 'weightRig.resetPose';
  resetPose.textContent = t('weightRig.resetPose');
  resetPose.addEventListener('click', () => resetRigPose());
  jointActions.append(clear, resetJoint, resetPose);
  section.appendChild(jointActions);
  ui.clearJoint = clear;
  ui.resetJoint = resetJoint;
  ui.resetPose = resetPose;

  addText(section, 'weight-rig-control-label', '', 'weightRig.posePresets');
  const preset = document.createElement('select');
  preset.className = 'rig-preset-select';
  preset.dataset.i18nAriaLabel = 'weightRig.rigPosePreset';
  preset.setAttribute('aria-label', t('weightRig.rigPosePreset'));
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
  save.dataset.i18n = 'common.save';
  save.textContent = t('common.save');
  save.addEventListener('click', () => savePreset());
  const rename = document.createElement('button');
  rename.type = 'button';
  rename.className = 'ui-button rig-rename-preset';
  rename.dataset.i18n = 'weightRig.rename';
  rename.textContent = t('weightRig.rename');
  rename.addEventListener('click', () => renamePreset());
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'ui-button rig-delete-preset';
  remove.dataset.i18n = 'weightRig.delete';
  remove.textContent = t('weightRig.delete');
  remove.addEventListener('click', () => deletePreset());
  management.append(save, rename, remove);
  section.appendChild(management);
  ui.savePreset = save;
  ui.renamePreset = rename;
  ui.deletePreset = remove;
  ui.presetStatus = addText(section, 'rig-hint');
  ui.presetStatus.setAttribute('aria-live', 'polite');

  const advanced = addAdvanced(parent);
  const ikLabel = document.createElement('label');
  ikLabel.className = 'weight-checkbox';
  const ik = document.createElement('input');
  ik.type = 'checkbox';
  ik.className = 'rig-panel-enable-ik';
  ik.addEventListener('change', () => setRigIkEnabled(ik.checked));
  ikLabel.appendChild(ik);
  addText(ikLabel, 'weight-label', '', 'weightRig.enableIk');
  advanced.content.appendChild(ikLabel);
  ui.ik = ik;
  ui.ikSelection = addText(advanced.content, 'rig-hint');

  buildMainRigControls(advanced.content);

  const snapRow = document.createElement('label');
  snapRow.className = 'rig-row';
  addText(snapRow, 'rig-label', '', 'weightRig.rotationSnap');
  const snap = document.createElement('select');
  snap.className = 'rig-snap-select';
  [[0, 'weightRig.off'], [5, null], [15, null], [30, null]].forEach(([value, key]) => {
    const option = document.createElement('option');
    option.value = String(value);
    option.dataset.i18n = key || '';
    option.textContent = key ? t(key) : `${value}°`;
    snap.appendChild(option);
  });
  snap.addEventListener('change', () => setRigRotationSnapDegrees(Number(snap.value)));
  snapRow.appendChild(snap);
  advanced.content.appendChild(snapRow);
  ui.snap = snap;

  const setRoot = document.createElement('button');
  setRoot.type = 'button';
  setRoot.className = 'ui-button rig-set-root';
  setRoot.dataset.i18n = 'weightRig.setSelectedRoot';
  setRoot.textContent = t('weightRig.setSelectedRoot');
  setRoot.addEventListener('click', () => {
    const selected = selectedJoint();
    if (selected) setRigJointRoot(selected.jointId);
  });
  advanced.content.appendChild(setRoot);
  ui.setRoot = setRoot;
}

function buildPanel() {
  panel.replaceChildren();
  ui = {};
  const header = document.createElement('div');
  header.className = 'weight-rig-header';
  const heading = document.createElement('h3');
  heading.dataset.i18n = 'weightRig.title';
  heading.textContent = t('weightRig.title');
  header.appendChild(heading);
  ui.status = addText(header, 'weight-rig-status');
  panel.appendChild(header);
  buildWeightSection(panel);
  buildRigSection(panel);
}

function formatAffectedVertices(value) {
  const count = Math.max(0, Math.round(Number(value) || 0));
  if (count < 1000) {
    return t('weightRig.verts', {
      count: count.toLocaleString(getLocale()),
    });
  }
  return t('weightRig.vertsK', {
    count: (count / 1000).toFixed(1).replace(/\.0$/, ''),
  });
}

function formatBoneMeta(stats) {
  const average = Math.max(0, Number(stats?.averageInfluence) || 0);
  return `${formatAffectedVertices(stats?.affectedVertexCount)} · ${Math.round(average * 100)}%`;
}

function formatNearbyInfluence(value) {
  const percentage = Math.max(0, Number(value) || 0) * 100;
  return percentage > 0 && percentage < 1
    ? t('weightRig.nearbyLess')
    : t('weightRig.nearby', {count: Math.round(percentage)});
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
        label += ` · ${t('weightRig.offset', {offset: source.boneIdOffset})}`;
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
    if (!ui.empty) ui.empty = addText(ui.boneList, 'weight-empty', '', 'weightRig.noBoneIds');
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
  ui.weightPick.textContent = state.picking
    ? t('weightRig.cancelPicking') : t('weightRig.pickFromModel');
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

function syncHumanoidEditControls(state) {
  const edit = state?.humanoidRigEdit || {};
  const editing = edit.editing === true;
  ui.mainTitle.textContent = editing
    ? t('weightRig.editRigTitle') : t('weightRig.mainRig');
  ui.editStatus.hidden = !editing;
  ui.editRig.hidden = editing;
  ui.resetRig.hidden = editing;
  ui.editRig.disabled = !state?.loaded || editing;
  ui.resetRig.disabled = !edit.hasSavedOverrides || edit.saving;
  ui.cancelEdit.disabled = edit.saving;
  ui.saveEdit.disabled = edit.saving || !state?.loaded;
  const controlCount = Object.keys(edit.controls || {}).length;
  const controlTotal = HUMANOID_CONTROL_KEYS.length;
  ui.editCount.textContent = editing
    ? t('weightRig.controlPointsVisible', {
      count: controlCount, total: controlTotal,
    }) : '';
  const key = edit.selectedControlKey;
  ui.editControl.textContent = key
    ? humanoidControlLabel(key) : t('weightRig.clickPointToMove');
  const mapped = key ? edit.mappedJointIdByControl?.[key] : null;
  ui.editConnection.textContent = key
    ? mapped === undefined || mapped === null
      ? t('weightRig.notConnected')
      : t('weightRig.connectedJoint', {id: mapped}) : '';
  ui.editHint.textContent = edit.error || (key
    ? t('weightRig.movePointRelease') : t('weightRig.clickPointToMove'));
  ui.editHint.hidden = !edit.error && !!key;
  ui.editStatus.classList.toggle('is-saving', !!edit.saving);
}

function syncRigOptions(state = latestRigState || getModelRigState()) {
  if (!ui?.joint) return;
  latestRigState = state;
  const model = state?.model;
  const joints = model?.joints || [];
  const selected = selectedJoint(state);
  syncHumanoidEditControls(state);
  const optionKey = JSON.stringify([
    model?.structureRevision ?? state?.structureRevision ?? null,
    joints.map(joint => joint.jointId),
  ]);
  if (optionKey !== ui.joint.dataset.optionKey) {
    ui.joint.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.dataset.i18n = 'weightRig.selectJoint';
    placeholder.textContent = t('weightRig.selectJoint');
    placeholder.disabled = true;
    placeholder.selected = true;
    ui.joint.appendChild(placeholder);
    joints.forEach(item => {
      const option = document.createElement('option');
      option.value = String(item.jointId);
      option.textContent = t('weightRig.joint', {id: item.jointId});
      ui.joint.appendChild(option);
    });
    ui.joint.dataset.optionKey = optionKey;
  }
  if (ui.joint.options[0]) {
    ui.joint.options[0].textContent = t('weightRig.selectJoint');
  }
  [...ui.joint.options].slice(1).forEach(option => {
    option.textContent = t('weightRig.joint', {id: option.value});
  });
  ui.joint.value = selected ? String(selected.jointId) : '';
  const editing = state?.humanoidRigEdit?.editing === true;
  ui.joint.disabled = editing || !state?.loaded || !joints.length;
  const jointPickActive = state?.jointPickIntent?.type === 'selected-joint';
  ui.rigPickJoint.textContent = jointPickActive
    ? t('weightRig.cancelPicking') : t('weightRig.pickFromModel');
  ui.rigPickJoint.classList.toggle('active', jointPickActive);
  ui.rigPickJoint.setAttribute('aria-pressed', String(jointPickActive));
  ui.rigPickJoint.disabled = editing || !state?.loaded || !joints.length;
  ui.snap.value = String(state?.rotationSnapDegrees ?? 0);
  ui.snap.disabled = editing || !state?.loaded || !joints.length || !!state?.ik?.enabled;
  const ik = state?.ik || {};
  const hasSelected = !!selected;
  const selectedControl = ik.selectedHumanoidControlKey;
  ui.ikSelection.textContent = selectedControl
    ? t('weightRig.selectedPoint', {name: humanoidControlLabel(selectedControl)})
    : t('weightRig.selectPoint');
  ui.ik.checked = !!ik.enabled;
  ui.ik.disabled = editing || !state?.loaded || !ik.available;
  ui.clearJoint.disabled = editing || !hasSelected;
  ui.setRoot.disabled = editing || !hasSelected;
  ui.resetJoint.disabled = editing || !hasSelected;
  ui.resetPose.disabled = editing || !state?.loaded;
  syncPresetControls(state);
}

function selectedPreset(state = latestRigState, id = ui?.preset?.value) {
  const presetState = state?.rigPresets || {};
  return (presetState.presets || []).find(item => item.id === id) || null;
}

const PRESET_SKIP_REASON_LABELS = Object.freeze({
  joint_not_found: 'weightRig.reason.noMatchingJoint',
  root_not_found: 'weightRig.reason.noMatchingRoot',
  ambiguous_joint_signature: 'weightRig.reason.ambiguousMatch',
  duplicate_joint_entry: 'weightRig.reason.duplicateEntry',
  duplicate_root_entry: 'weightRig.reason.duplicateEntry',
  invalid_signature: 'weightRig.reason.invalidJointIdentity',
  invalid_rotation: 'weightRig.reason.invalidRotation',
  invalid_preset: 'weightRig.reason.invalidSavedPose',
});

function pluralizePresetCount(count, key) {
  return t(`${key}.${count === 1 ? 'one' : 'many'}`, {count});
}

function presetSkipLabel(item) {
  const key = PRESET_SKIP_REASON_LABELS[item?.reason]
    || (item?.type === 'root'
      ? 'weightRig.reason.noMatchingRoot'
      : 'weightRig.reason.noMatchingJoint');
  return t(key);
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
      result.appliedJointCount, 'weightRig.count.jointRotation'));
  }
  if (result.appliedRootCount) {
    applied.push(pluralizePresetCount(result.appliedRootCount, 'weightRig.count.root'));
  }
  const skippedParts = [];
  if (result.skippedJointCount) {
    skippedParts.push(pluralizePresetCount(
      result.skippedJointCount, 'weightRig.count.joint'));
  }
  if (result.skippedRootCount) {
    skippedParts.push(pluralizePresetCount(
      result.skippedRootCount, 'weightRig.count.root'));
  }
  const reasonText = [...skippedReasons.entries()]
    .map(([label, count]) => count > 1
      ? t('weightRig.reasonCount', {label, count}) : label)
    .join('; ');
  const reason = reasonText ? `: ${reasonText}` : '.';
  if (!result.success) {
    return t('weightRig.status.couldNotApplyPose', {reason});
  }
  if (!skippedParts.length) {
    return applied.length ? t('weightRig.status.applied', {
      items: applied.join(` ${t('weightRig.and')} `),
    }) : t('weightRig.status.appliedPose');
  }
  return t('weightRig.status.appliedSkipped', {
    applied: applied.length
      ? applied.join(` ${t('weightRig.and')} `) : t('weightRig.nothing'),
    skipped: skippedParts.join(` ${t('weightRig.and')} `),
    reason,
  });
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
    placeholder.dataset.i18n = 'weightRig.selectPose';
    placeholder.textContent = t('weightRig.selectPose');
    placeholder.disabled = true;
    ui.preset.appendChild(placeholder);
    const group = document.createElement('optgroup');
    group.dataset.i18nLabel = 'weightRig.myPoses';
    group.label = t('weightRig.myPoses');
    if (!presets.length) {
      const option = document.createElement('option');
      option.value = '';
      option.dataset.i18n = 'weightRig.noSavedPoses';
      option.textContent = t('weightRig.noSavedPoses');
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
  const placeholder = ui.preset.options[0];
  if (placeholder) placeholder.textContent = t('weightRig.selectPose');
  const group = ui.preset.querySelector('optgroup');
  if (group) {
    group.label = t('weightRig.myPoses');
    const empty = group.querySelector('option[data-i18n="weightRig.noSavedPoses"]');
    if (empty) empty.textContent = t('weightRig.noSavedPoses');
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
  const name = await inputConfirmDialog(t('weightRig.prompt.savePose'), '');
  if (!name) return;
  const result = await saveRigPosePreset(name);
  ui.presetStatus.textContent = result?.saved ? presetFeedback(latestRigState)
    : result?.error || t('weightRig.status.couldNotSavePose');
}

async function renamePreset() {
  const current = latestRigState?.rigPresets?.presets?.find(item => item.id === ui.preset.value);
  if (!current) return;
  const name = await inputConfirmDialog(t('weightRig.prompt.renamePose'), current.name);
  if (!name) return;
  const result = await renameRigPosePreset(current.id, name);
  ui.presetStatus.textContent = result?.saved ? presetFeedback(latestRigState)
    : result?.error || t('weightRig.status.couldNotRenamePose');
}

async function deletePreset() {
  const current = latestRigState?.rigPresets?.presets?.find(item => item.id === ui.preset.value);
  if (!current || !await confirmDialog(
    t('weightRig.confirm.deletePose', {name: current.name}))) return;
  const result = await deleteRigPosePreset(current.id);
  ui.presetStatus.textContent = result?.saved ? presetFeedback(latestRigState)
    : result?.error || t('weightRig.status.couldNotDeletePose');
}

function syncStatus() {
  const weight = latestWeightState || getModelWeightState();
  const rig = latestRigState || getModelRigState();
  let status = '';
  if (weight.error) status = weight.error;
  else if (rig.error) status = rig.error;
  else if (rig.humanoidRigEdit?.error) status = rig.humanoidRigEdit.error;
  else if (weight.selectionSaveError) {
    status = t('weightRig.status.selectionSaveError', {
      detail: weight.selectionSaveError,
    });
  } else if (weight.loading) status = t('weightRig.status.loadingWeights');
  else if (rig.loading) status = t('weightRig.status.loadingRig');
  else if (weight.loaded && (!weight.sources?.length || weight.noWeights)) {
    status = t('weightRig.status.noWeights');
  } else if (!weight.loaded && !rig.loaded) status = '';
  if (weight.pickStatus || rig.pickStatus) {
    status = statusText(weight.pickStatus || rig.pickStatus);
  }
  ui.status.textContent = status;
}

function closePopover() {
  if (!ui?.popover || ui.popover.hidden) return;
  ui.popover.hidden = true;
  ui.boneButton.setAttribute('aria-expanded', 'false');
}

function scheduleRigLoadAfterPaint(generation) {
  const afterPaint = typeof requestAnimationFrame === 'function'
    ? requestAnimationFrame : callback => setTimeout(callback, 0);
  afterPaint(() => setTimeout(() => {
    const weight = getModelWeightState();
    if (weight.generation !== generation || !weight.loaded
        || weight.error || weight.noWeights) return;
    void ensureModelRigLoaded();
  }, 0));
}

function loadOnDemand() {
  if (loadingPromise) return loadingPromise;
  loadingPromise = ensureModelWeightsLoaded()
    .then(weight => {
      if (weight?.loaded && !weight.error && !weight.noWeights) {
        scheduleRigLoadAfterPaint(weight.generation);
      }
      return weight;
    })
    .finally(() => { loadingPromise = null; });
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
  window.addEventListener(LANGUAGE_CHANGED, () => {
    applyTranslations(panel);
    syncWeightControls(latestWeightState || getModelWeightState());
    syncPhysicsControls();
    syncRigOptions(latestRigState || getModelRigState());
    syncStatus();
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
