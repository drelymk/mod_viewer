// Experimental inferred Rig/Pose panel. It exposes canonical model joints
// and source membership without editing Weight/Physics selection state.

import {
  beginRigPicking, cancelRigPicking, ensureModelRigLoaded,
  eulerFromRestFrameDelta, getModelRigState, getRigBonePoseFrame,
  getRigJointPoseFrame,
  resetRigBone, resetRigPose, selectRigBone,
  selectRigJoint,
  setRigComponentRoot, setRigRotationSnapDegrees,
  setRigVisible,
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

function selectedJoint(state = latestState) {
  const id = Number(state?.selectedJointId);
  return state?.model?.joints?.find(joint => joint.jointId === id) || null;
}

function componentFor(source, boneId) {
  if (boneId === null || boneId === undefined
      || !Number.isInteger(Number(boneId))) return null;
  return source?.components?.find(component =>
    component.nodeIds.includes(Number(boneId))) || null;
}

function addNavigationButton(parent, sourceKey, boneId) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'rig-nav-button';
  button.textContent = String(boneId);
  button.dataset.boneId = String(boneId);
  button.addEventListener('click', () => sourceKey === null
    ? selectRigJoint(boneId) : selectRigBone(sourceKey, boneId));
  parent.appendChild(button);
}

function syncReconciliationReadout(state, joint) {
  const model = state?.model;
  const reconciliation = model?.reconciliation;
  if (!model || !reconciliation) {
    ui.reconciliationSummary.textContent = '—';
    ui.connection.textContent = '—';
    ui.confidence.textContent = '—';
    ui.connectionDetail.textContent = '';
    return;
  }
  ui.reconciliationSummary.textContent = [
    `Sources ${reconciliation.sourceCount ?? state.sources?.length ?? 0}`,
    `Source bones ${reconciliation.sourceBoneCount ?? 0}`,
    `Model joints ${reconciliation.modelJointCount ?? model.joints.length}`,
    `Equivalent clusters ${reconciliation.equivalenceClusterCount ?? 0}`,
    `Attachments ${reconciliation.attachmentCount ?? 0}`,
    `Components ${reconciliation.componentCount ?? model.components?.length ?? 0}`,
  ].join(' · ');
  if (!joint) {
    ui.connection.textContent = '—';
    ui.confidence.textContent = '—';
    ui.connectionDetail.textContent = '';
    return;
  }
  const memberKeys = new Set((joint.members || []).map(member =>
    member.sourceBoneKey));
  const equivalences = (reconciliation.acceptedEquivalences || []).filter(item =>
    memberKeys.has(item.left?.sourceBoneKey)
    || memberKeys.has(item.right?.sourceBoneKey));
  const attachment = (model.forestEdges || []).find(edge =>
    edge.relationshipType === 'attachment'
    && (Number(edge.jointA) === Number(joint.jointId)
      || Number(edge.jointB) === Number(joint.jointId)));
  const confidence = equivalences.length
    ? Math.max(...equivalences.map(item => Number(item.score) || 0))
    : attachment ? Number(attachment.attachmentScore ?? attachment.weight) : null;
  ui.connection.textContent = equivalences.length ? 'equivalence'
    : attachment ? 'attachment' : 'unresolved';
  ui.confidence.textContent = Number.isFinite(confidence)
    ? confidence.toFixed(3) : '—';
  if (equivalences.length || attachment) {
    ui.connectionDetail.textContent = '';
    return;
  }
  const bestAttachment = (reconciliation.rejectedCandidates || []).filter(item =>
    item.left?.jointId !== undefined && item.right?.jointId !== undefined
    && (Number(item.left.jointId) === Number(joint.jointId)
      || Number(item.right.jointId) === Number(joint.jointId))
    && String(item.rejectionReason || '').startsWith('attachment_'))
    .sort((left, right) => (Number(right.score) || 0)
      - (Number(left.score) || 0))[0];
  if (!bestAttachment) {
    ui.connectionDetail.textContent = 'No attachment candidate';
    return;
  }
  const otherJointId = Number(bestAttachment.left.jointId) === Number(joint.jointId)
    ? bestAttachment.right.jointId : bestAttachment.left.jointId;
  ui.connectionDetail.textContent = `Best attachment: Joint ${otherJointId}`
    + ` · ${(Number(bestAttachment.score) || 0).toFixed(3)}`
    + ` · ${bestAttachment.rejectionReason}`;
}

function syncRotationReadout(state = latestState, localOverride = null) {
  if (!ui?.rotationValues) return;
  const joint = selectedJoint(state);
  if (joint) {
    const id = Number(joint.jointId);
    const frame = getRigJointPoseFrame(id);
    const local = localOverride?.length === 4
      ? localOverride : state.model?.poseRotationByJointId?.[id]
        || [0, 0, 0, 1];
    if (!frame?.restRotation) {
      ui.rotationValues.forEach(value => { value.textContent = '—'; });
      return;
    }
    const euler = eulerFromRestFrameDelta(local, frame.restRotation, 'XYZ');
    [euler.x, euler.y, euler.z].forEach((value, index) => {
      ui.rotationValues[index].textContent =
        `${(value * 180 / Math.PI).toFixed(1)}°`;
    });
    return;
  }
  const source = selectedSource(state);
  const selectedId = source?.selectedBoneId ?? state?.selectedBoneId;
  const id = Number(selectedId);
  const frame = source && Number.isInteger(id)
    ? getRigBonePoseFrame(source.sourceKey, id) : null;
  const local = localOverride?.length === 4
    ? localOverride : source?.poseRotationByBoneId?.[id]
      || [0, 0, 0, 1];
  if (!frame?.restRotation || !Number.isInteger(id)) {
    ui.rotationValues.forEach(value => { value.textContent = '—'; });
    return;
  }
  const euler = eulerFromRestFrameDelta(local, frame.restRotation, 'XYZ');
  [euler.x, euler.y, euler.z].forEach((value, index) => {
    ui.rotationValues[index].textContent =
      `${(value * 180 / Math.PI).toFixed(1)}°`;
  });
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

  const sourceSection = section('Model');
  ui.modelSummary = addText(sourceSection, 'rig-value', '—');
  const reconciliationSection = section('Reconciliation');
  ui.reconciliationSummary = document.createElement('div');
  ui.reconciliationSummary.className = 'rig-value rig-reconciliation-summary';
  reconciliationSection.appendChild(ui.reconciliationSummary);

  const boneSection = section('Selected Joint');
  ui.bone = document.createElement('select');
  ui.bone.className = 'rig-bone-select';
  ui.bone.setAttribute('aria-label', 'Selected model joint');
  ui.bone.addEventListener('change', () => {
    if (latestState?.model) selectRigJoint(Number(ui.bone.value));
    else {
      const source = selectedSource();
      if (source) selectRigBone(source.sourceKey, Number(ui.bone.value));
    }
  });
  boneSection.appendChild(ui.bone);
  ui.influences = document.createElement('div');
  ui.influences.className = 'rig-influences';
  boneSection.appendChild(ui.influences);
  const parentRow = document.createElement('div');
  parentRow.className = 'rig-nav-row';
  addText(parentRow, 'rig-label', 'Parent');
  ui.parent = document.createElement('div');
  ui.parent.className = 'rig-nav-values';
  parentRow.appendChild(ui.parent);
  boneSection.appendChild(parentRow);
  const childrenRow = document.createElement('div');
  childrenRow.className = 'rig-nav-row';
  addText(childrenRow, 'rig-label', 'Children');
  ui.children = document.createElement('div');
  ui.children.className = 'rig-nav-values';
  childrenRow.appendChild(ui.children);
  boneSection.appendChild(childrenRow);
  const connectionRow = document.createElement('div');
  connectionRow.className = 'rig-row';
  addText(connectionRow, 'rig-label', 'Connection');
  ui.connection = addText(connectionRow, 'rig-value', '—');
  ui.connection.classList.add('rig-connection-value');
  boneSection.appendChild(connectionRow);
  const confidenceRow = document.createElement('div');
  confidenceRow.className = 'rig-row';
  addText(confidenceRow, 'rig-label', 'Confidence');
  ui.confidence = addText(confidenceRow, 'rig-value', '—');
  ui.confidence.classList.add('rig-confidence-value');
  boneSection.appendChild(confidenceRow);
  ui.connectionDetail = addText(boneSection, 'rig-hint');
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
    const joint = selectedJoint();
    const member = joint?.representativeMember || joint?.members?.[0];
    if (member) setRigComponentRoot(member.sourceKey, member.boneId);
    else {
      const source = selectedSource();
      if (source) setRigComponentRoot(source.sourceKey, Number(ui.bone.value));
    }
  });
  componentSection.appendChild(ui.setRoot);

  const poseSection = section('Pose');
  addText(poseSection, 'rig-hint',
    'Rotate the selected non-root inferred model joint with the viewport gizmo. Pose is temporary and is not saved.');
  const snapRow = document.createElement('label');
  snapRow.className = 'rig-row';
  addText(snapRow, 'rig-label', 'Rotation snap');
  ui.snap = document.createElement('select');
  ui.snap.className = 'rig-snap-select';
  ui.snap.setAttribute('aria-label', 'Rig rotation snap');
  [[0, 'Off'], [5, '5°'], [15, '15°'], [30, '30°']].forEach(([value, label]) => {
    const option = document.createElement('option');
    option.value = String(value);
    option.textContent = label;
    ui.snap.appendChild(option);
  });
  ui.snap.addEventListener('change', () =>
    setRigRotationSnapDegrees(Number(ui.snap.value)));
  snapRow.appendChild(ui.snap);
  poseSection.appendChild(snapRow);
  addText(poseSection, 'rig-readout-title',
    'Rotation in inferred Bone frame');
  ui.rotationValues = [];
  ['X', 'Y', 'Z'].forEach(axis => {
    const row = document.createElement('div');
    row.className = 'rig-row rig-readout-row';
    addText(row, 'rig-label', axis);
    ui.rotationValues.push(addText(row, 'rig-value', '—'));
    poseSection.appendChild(row);
  });
  ui.resetBone = document.createElement('button');
  ui.resetBone.type = 'button';
  ui.resetBone.className = 'ui-button rig-reset-bone';
  ui.resetBone.textContent = 'Reset Bone';
  ui.resetBone.addEventListener('click', () => {
    const joint = selectedJoint();
    const member = joint?.representativeMember || joint?.members?.[0];
    if (member) resetRigBone(member.sourceKey, member.boneId);
    else {
      const source = selectedSource();
      if (source) resetRigBone(source.sourceKey, Number(ui.bone.value));
    }
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
  const model = state.model;
  if (model) {
    ui.modelSummary.textContent = `${model.joints.length} model joint${
      model.joints.length === 1 ? '' : 's'} · ${(state.sources || []).length} source${
      (state.sources || []).length === 1 ? '' : 's'}`;
    const jointKey = JSON.stringify(model.joints.map(joint => [
      joint.jointId, joint.members,
    ]));
    if (jointKey !== ui.bone.dataset.optionKey) {
      ui.bone.replaceChildren();
      model.joints.forEach(joint => {
        const option = document.createElement('option');
        option.value = String(joint.jointId);
        option.textContent = String(joint.jointId);
        ui.bone.appendChild(option);
      });
      ui.bone.dataset.optionKey = jointKey;
    }
    const joint = selectedJoint(state);
    const selectedId = joint?.jointId ?? state.selectedJointId;
    if (selectedId !== null && selectedId !== undefined) {
      ui.bone.value = String(selectedId);
    }
    ui.influences.replaceChildren();
    if (joint?.members?.length) {
      joint.members.forEach(member => {
        const row = document.createElement('div');
        row.className = 'rig-influence';
        addText(row, 'rig-influence-id',
          `${member.sourceKey} · bone ${member.boneId}`);
        ui.influences.appendChild(row);
      });
    }
    const component = model.components?.find(item =>
      item.nodeIds.includes(Number(selectedId))) || null;
    syncReconciliationReadout(state, joint);
    ui.root.textContent = component ? String(component.rootId) : '—';
    ui.depth.textContent = component
      ? String(component.depthById?.[selectedId] ?? '—') : '—';
    ui.parent.replaceChildren();
    ui.children.replaceChildren();
    if (component && Number.isInteger(Number(selectedId))) {
      const parentId = component.parentById?.[selectedId];
      if (parentId !== null && parentId !== undefined) {
        addNavigationButton(ui.parent, null, Number(parentId));
      } else addText(ui.parent, 'rig-value', '—');
      const children = component.childrenById?.[selectedId] || [];
      if (children.length) children.forEach(childId =>
        addNavigationButton(ui.children, null, Number(childId)));
      else addText(ui.children, 'rig-value', '—');
    } else {
      addText(ui.parent, 'rig-value', '—');
      addText(ui.children, 'rig-value', '—');
    }
    syncRotationReadout(state);
    return;
  }
  const source = selectedSource(state);
  syncReconciliationReadout(state, null);
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
  ui.parent.replaceChildren();
  ui.children.replaceChildren();
  const id = Number(selectedId);
  if (component && Number.isInteger(id)) {
    const parentId = component.parentById?.[id];
    if (parentId !== null && parentId !== undefined) {
      addNavigationButton(ui.parent, source.sourceKey, Number(parentId));
    } else {
      addText(ui.parent, 'rig-value', '—');
    }
    const children = component.childrenById?.[id] || [];
    if (children.length) {
      children.forEach(childId =>
        addNavigationButton(ui.children, source.sourceKey, Number(childId)));
    } else {
      addText(ui.children, 'rig-value', '—');
    }
  } else {
    addText(ui.parent, 'rig-value', '—');
    addText(ui.children, 'rig-value', '—');
  }
  syncRotationReadout(state);
}

function syncPanel(state = getModelRigState()) {
  if (!ui) return;
  latestState = state;
  if (state.loading) ui.status.textContent = 'Inferring source-local rig…';
  else if (state.error) ui.status.textContent = state.error;
  else if (!state.loaded) ui.status.textContent = 'Open this panel to load authored weights.';
  else if (!state.sources?.length) ui.status.textContent = 'No usable Blend skinning data found.';
  else ui.status.textContent = state.model
    ? `${state.model.joints.length} model joint${state.model.joints.length === 1 ? '' : 's'}`
    : `${state.sources.length} source rig${state.sources.length === 1 ? '' : 's'}`;
  if (state.pickStatus) ui.status.textContent = state.pickStatus;
  syncOptions(state);
  ui.visible.checked = !!state.visible;
  ui.visible.disabled = !state.loaded || !state.sources?.length;
  ui.pick.disabled = !state.loaded || !state.sources?.length;
  ui.pick.classList.toggle('active', !!state.picking);
  ui.pick.textContent = state.picking ? 'Cancel picking' : 'Pick from model';
  ui.snap.value = String(state.rotationSnapDegrees ?? 0);
  ui.snap.disabled = !state.loaded || !state.sources?.length;
  const source = selectedSource(state);
  const physicsActive = modelStateHasPhysics(state);
  const hasSelectedBone = state.model
    ? state.selectedJointId !== null && state.selectedJointId !== undefined
    : state.selectedBoneId !== null && state.selectedBoneId !== undefined;
  ui.setRoot.disabled = !source || physicsActive || !hasSelectedBone;
  ui.resetBone.disabled = !source || physicsActive || !hasSelectedBone;
  ui.resetPose.disabled = !state.loaded || physicsActive;
}

function modelStateHasPhysics(state) {
  return (state.sources || []).some(source => source.physicsActive);
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
  window.addEventListener('mod-viewer-model-rig-pose-changed', event => {
    if (latestState?.model
        && Number(event.detail?.jointId) === Number(latestState?.selectedJointId)) {
      syncRotationReadout(latestState, event.detail?.quaternion);
    } else if (event.detail?.sourceKey === latestState?.activeSourceKey
        && Number(event.detail?.boneId) === Number(latestState?.selectedBoneId)) {
      syncRotationReadout(latestState, event.detail?.quaternion);
    }
  });
  window.addEventListener('mod-viewer-right-dock-tab-changed', event => {
    if (event.detail?.tab === 'rig' && event.detail?.open) void loadOnDemand();
    if (event.detail?.tab !== 'rig' && latestState?.picking) cancelRigPicking();
  });
  syncPanel();
}
