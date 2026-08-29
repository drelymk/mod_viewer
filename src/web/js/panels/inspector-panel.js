// Selection-aware details panel. Mesh creation remains owned by mesh-panel;
// this module only presents the already-authoritative mesh/component state.

import { isRightDockOpen, setRightDockTab } from './right-dock.js';
import { clearSelection } from '../scene/selection.js';
import {
  GRAVITY_WORLD_DIRECTION,
  getSkinningState, loadSkinningWeights, resetSkinningExperiment,
  setSelectedBone, setSkinningHeatmap,
  setInfluenceVisualizationMode,
  setCandidateTreeRoot,
  setPhysicsFrequency,
  setPhysicsDamping, setPhysicsMotionStrength,
  setPhysicsLinearMotionStrength, setPhysicsContinuousLinearResponse,
  getPhysicsConstraintDiagnostics,
  setPhysicsConstraintsEnabled, setPhysicsMaxBendDegrees,
  setPhysicsGravityEnabled, setPhysicsGravityScale,
  setPhysicsEnabled,
  resetPhysicsMotion,
} from '../mesh/weight-experiment.js';

const meshRecords = new WeakMap();
const skinningUpdates = new WeakMap();
const physicsUpdates = new WeakMap();
let current = null;
let selectionCount = 0;
const $ = id => document.getElementById(id);

const MATERIAL_KIND_OPTIONS = Object.freeze([
  ['auto', 'Auto'],
  ['body', 'Body'],
  ['face', 'Face'],
  ['hair', 'Hair'],
  ['eye', 'Eye'],
  ['weapon', 'Weapon'],
  ['special', 'Special'],
]);

export function registerInspectorMesh(mesh, record) {
  if (mesh) meshRecords.set(mesh, record);
}

function clearContent() {
  const empty = $('inspector-empty');
  const content = $('inspector-content');
  if (empty) empty.hidden = false;
  if (content) {
    content.hidden = true;
    content.replaceChildren();
  }
}

function showContent() {
  $('inspector-empty')?.setAttribute('hidden', '');
  const content = $('inspector-content');
  if (content) content.hidden = false;
  return content;
}

function addText(parent, className, text) {
  const node = document.createElement('span');
  node.className = className;
  node.textContent = text;
  parent.appendChild(node);
  return node;
}

function basename(value) {
  return String(value || '').replaceAll('\\', '/').split('/').pop() || '';
}

function textureOptionLabel(option) {
  return option?.label || basename(option?.file)
    || basename(String(option?.tex_key || '').split('::').slice(1).join('::'))
    || 'Texture';
}

function automaticTextureLabel(resolved, pool) {
  if (!resolved) return 'Automatic';
  const option = pool.find(item => item.tex_key === resolved);
  if (option) return `Automatic · ${textureOptionLabel(option)}`;
  const file = String(resolved).split('::').slice(1).join('::');
  return file ? `Automatic · ${basename(file)}` : 'Automatic';
}

function componentContext(record) {
  const meshes = record.meshes || [];
  const total = meshes.length;
  const visible = meshes.filter(mesh => mesh.visible).length;
  const meshWord = total === 1 ? 'mesh' : 'meshes';
  return `${total} ${meshWord} · ${visible} visible`;
}

function buildHeader(content, title, context, titleHint = '') {
  const header = document.createElement('div');
  header.className = 'inspector-header';
  if (titleHint) header.title = titleHint;
  const heading = document.createElement('h3');
  heading.textContent = title;
  header.appendChild(heading);
  const subtitle = addText(header, 'inspector-context', context || '');
  subtitle.dataset.inspectorContext = 'true';
  content.appendChild(header);
}

function buildMaterialControl(record) {
  const select = document.createElement('select');
  select.className = 'inspector-material-kind-control material-kind-select';
  select.setAttribute('aria-label', 'Material kind');
  MATERIAL_KIND_OPTIONS.forEach(([value, label]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  });
  const getKind = record.getMaterialKind || (() => null);
  const setKind = record.setMaterialKind;
  select.value = getKind() || 'auto';
  select.disabled = typeof setKind !== 'function';
  select.addEventListener('change', async () => {
    if (typeof setKind !== 'function') return;
    const previous = getKind() || 'auto';
    select.disabled = true;
    const saved = await setKind(select.value);
    if (!saved) select.value = previous;
    select.disabled = false;
  });
  return select;
}

function buildMaterialSection(content, record) {
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-material-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Material';
  section.appendChild(title);
  if (record.getMaterialKind || record.setMaterialKind) {
    section.appendChild(buildMaterialControl(record));
  } else {
    addText(section, 'inspector-muted', 'Auto');
  }
  content.appendChild(section);
}

function buildManageTexturesButton(openTextureManager) {
  if (typeof openTextureManager !== 'function') return null;
  const manage = document.createElement('button');
  manage.type = 'button';
  manage.className = 'ui-button inspector-manage-textures';
  manage.textContent = 'Manage textures';
  manage.addEventListener('click', () => openTextureManager());
  return manage;
}

function buildComponentTextureSection(content, record) {
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-textures-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Textures';
  section.appendChild(title);
  const pool = record.texturePool || [];
  addText(section, 'inspector-texture-count', pool.length
    ? `${pool.length} available`
    : 'No textures discovered');
  const manage = buildManageTexturesButton(record.openTextureManager);
  if (manage) section.appendChild(manage);
  content.appendChild(section);
}

function buildTextureControls(content, record, mesh) {
  const component = record.component;
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-texture-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Texture';
  section.appendChild(title);

  const pool = component?.texturePool || [];
  const override = component?.getTextureOverride?.(mesh) || {
    value: undefined, automatic: true, resolved: null,
  };
  if (!pool.length) addText(section, 'inspector-muted', 'No textures discovered');
  const list = document.createElement('div');
  list.className = 'inspector-texture-list';
  const addOption = (label, value, selected, choice, titleText = '') => {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'inspector-texture-option';
    option.textContent = label;
    option.title = titleText || label;
    option.dataset.textureChoice = choice;
    option.dataset.textureValue = value == null ? '' : value;
    option.classList.toggle('selected', selected);
    option.addEventListener('click', () => {
      component?.setTextureOverride?.(mesh, value);
    });
    list.appendChild(option);
  };
  addOption(
    automaticTextureLabel(override.resolved, pool),
    undefined, override.automatic, 'automatic', override.resolved || 'Automatic');
  pool.forEach(option => addOption(
    textureOptionLabel(option), option.tex_key,
    !override.automatic && override.value === option.tex_key,
    'texture', option.file || option.label || option.tex_key));
  addOption('None', null, !override.automatic && override.value === null, 'none');
  section.appendChild(list);
  const manage = buildManageTexturesButton(component?.openTextureManager);
  if (manage) section.appendChild(manage);
  content.appendChild(section);
}

function updateTextureControlState(content, mesh, component) {
  const pool = component?.texturePool || [];
  const override = component?.getTextureOverride?.(mesh);
  if (!override) return;
  const automatic = content.querySelector(
    '.inspector-texture-option[data-texture-choice="automatic"]');
  if (automatic) {
    automatic.textContent = automaticTextureLabel(override.resolved, pool);
    automatic.title = override.resolved || 'Automatic';
  }
  content.querySelectorAll('.inspector-texture-option').forEach(option => {
    const selected = option.dataset.textureChoice === 'automatic'
      ? override.automatic
      : option.dataset.textureChoice === 'none'
        ? !override.automatic && override.value === null
        : !override.automatic && override.value === option.dataset.textureValue;
    option.classList.toggle('selected', selected);
  });
}

function skinningNumber(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(4) : 'n/a';
}

function buildSkinningDiagnostics(parent, state, vertexCount) {
  const diagnostics = state.diagnostics || {};
  const rows = [
    `${Number(diagnostics.vertex_count ?? vertexCount).toLocaleString()} vertices`,
    `weight sums ${skinningNumber(diagnostics.min_weight_sum)}–${skinningNumber(diagnostics.max_weight_sum)}`,
    `${Number(diagnostics.invalid_weight_vertices || 0).toLocaleString()} invalid`,
  ];
  rows.forEach(text => addText(parent, 'inspector-skinning-diagnostic', text));
}
function syncSkinningHeatmapControls(section, mesh) {
  const state = getSkinningState(mesh);
  if (!state || !section) return;
  const heatmap = section.querySelector('.inspector-skinning-heatmap');
  if (!heatmap) return;
  const active = state.heatmapMode === 'bone';
  heatmap.setAttribute('aria-pressed', String(active));
  heatmap.classList.toggle('active', active);
  heatmap.textContent = active
    ? 'Hide Weight Heatmap' : 'Show Weight Heatmap';
}

function selectedInfluenceNode(state) {
  return (state?.influenceNodes || []).find(node =>
    node.boneId === state.selectedBone) || null;
}

function buildSelectedInfluenceStats(parent, mesh, state) {
  const stats = document.createElement('div');
  stats.className = 'inspector-skinning-influence-stats';
  parent.appendChild(stats);

  function addStat(label, value) {
    const row = document.createElement('div');
    row.className = 'inspector-skinning-stat';
    addText(row, 'inspector-label', label);
    addText(row, 'inspector-value', value);
    stats.appendChild(row);
  }

  function update(latest = getSkinningState(mesh)) {
    stats.replaceChildren();
    const node = selectedInfluenceNode(latest);
    if (!node) return;
    addStat('Affected vertices',
      Number(node.affectedVertexCount || 0).toLocaleString());
    addStat('Total weight', skinningNumber(node.totalWeight));
    addStat('Maximum weight', skinningNumber(node.maxVertexWeight));
    addStat('Weighted radius', skinningNumber(node.weightedRadius));
  }

  update(state);
  return {update};
}

function candidateTreeText(tree) {
  if (!tree) return '';
  const lines = ['Root ' + tree.rootId];
  const append = (orientation, boneId, prefix, last, root = false) => {
    if (!root) {
      lines.push(prefix + (last ? '\u2514\u2500 ' : '\u251c\u2500 ') + boneId);
    }
    const children = orientation?.childrenById?.[boneId] || [];
    children.forEach((child, index) => append(
      orientation,
      child,
      root ? '' : prefix + (last ? '   ' : '\u2502  '),
      index === children.length - 1));
  };
  const forest = tree.forest;
  const primary = forest?.components?.[forest.primaryComponentId];
  const primaryOrientation = primary || tree.orientation;
  if (Number.isFinite(Number(tree.rootId))) {
    append(primaryOrientation, Number(tree.rootId), '', true, true);
  }
  if (forest?.components?.length > 1) {
    lines.push('Inferred hierarchy has ' + forest.components.length
      + ' components');
    forest.components.forEach(component => {
      if (component.primary) return;
      const rootLabel = component.rootId == null
        ? 'no root' : 'Root ' + component.rootId;
      lines.push('Component ' + component.componentId + ' · ' + rootLabel
        + ' (automatic)');
      if (component.rootId != null) {
        append(component, Number(component.rootId), '', true, true);
      }
    });
  }
  return lines.join('\n');
}

function hierarchySummary(forest) {
  if (!forest) return 'Hierarchy not inferred. Select Show Hierarchy to build it.';
  const nodes = (forest.components || []).reduce(
    (total, component) => total + (component.nodeIds || []).length, 0);
  const edges = (forest.components || []).reduce(
    (total, component) => total + Number(component.edgeCount || 0), 0);
  const maxDepth = Math.max(0, ...(forest.components || [])
    .map(component => Number(component.maxDepth) || 0));
  return [
    'Inferred influence hierarchy',
    'Components ' + (forest.components || []).length
      + ' · Nodes ' + nodes + ' · Edges ' + edges,
    'Primary root ' + (forest.primaryRootId ?? 'n/a')
      + ' · Max depth ' + maxDepth,
  ].join('\n');
}

function physicsDiagnosticsPayload(state) {
  if (!state?.physicsEnabled && !state?.physicsState) return null;
  const constraints = getPhysicsConstraintDiagnostics(state);
  return {
    enabled: !!state.physicsEnabled,
    frequencyHz: state.physicsFrequencyHz,
    dampingRatio: state.physicsDampingRatio,
    angularResponse: state.physicsMotionStrength,
    linearResponse: state.physicsLinearMotionStrength,
    continuousLinearResponse: state.physicsContinuousLinearResponse,
    gravity: {
      enabled: !!state.physicsGravityEnabled,
      scale: state.physicsGravityScale,
      localDirection: [...(state.physicsGravityLocal
        || GRAVITY_WORLD_DIRECTION)],
      diagnostics: state.physicsGravityDiagnostics || null,
    },
    motion: {
      rootAngularDeltaVector: [...(state.lastRootAngularDeltaVector
        || [0, 0, 0])],
      rootAngularDeltaMagnitude: Number(
        state.lastRootAngularDeltaMagnitude) || 0,
      translationLagRotationVector: [...(state.lastTranslationLagRotationVector
        || [0, 0, 0])],
      translationLagRotationMagnitude: Number(
        state.lastTranslationLagRotationMagnitude) || 0,
      virtualLinearVelocityLocal: [...(state.physicsVirtualLinearVelocityLocal
        || [0, 0, 0])],
    },
    constraints,
    settled: !!state.physicsSettled,
    joints: Object.fromEntries(
      [...(state.physicsState?.joints || new Map()).entries()]
        .map(([boneId, joint]) => [boneId, {
          rotationVector: [...(joint.rotationVector || [0, 0, 0])],
          rotationMagnitude: Math.hypot(
            ...(joint.rotationVector || [0, 0, 0])),
          angularVelocity: [...(joint.angularVelocity || [0, 0, 0])],
          angularVelocityMagnitude: Math.hypot(
            ...(joint.angularVelocity || [0, 0, 0])),
        }])),
  };
}

function hierarchyDiagnosticsPayload(state) {
  const tree = state?.candidateTree;
  const forest = state?.candidateForest;
  if (!tree || !forest) return null;
  return {
    rootId: tree.rootId,
    components: tree.components,
    edges: tree.edges,
    orientation: tree.orientation,
    forest,
  };
}

function skinningDiagnosticsPayload(mesh, state) {
  if (!state?.loaded) return null;
  const selected = selectedInfluenceNode(state);
  return {
    mesh: {
      semanticKey: mesh?.userData?.semanticKey || null,
    },
    skinning: {
      influenceCount: state.influenceCount,
      boneIds: [...state.boneIds],
      selectedBone: state.selectedBone,
      selectedInfluence: selected ? {
        boneId: selected.boneId,
        totalWeight: selected.totalWeight,
        affectedVertexCount: selected.affectedVertexCount,
        maxVertexWeight: selected.maxVertexWeight,
        weightedCenter: selected.weightedCenter,
        weightedRadius: selected.weightedRadius,
      } : null,
      encoding: state.encoding,
      sourceDiagnostics: state.diagnostics,
    },
    hierarchy: hierarchyDiagnosticsPayload(state),
    physics: physicsDiagnosticsPayload(state),
  };
}

async function copySkinningDiagnostics(mesh, state) {
  const payload = skinningDiagnosticsPayload(mesh, state);
  if (!payload) throw new Error('Skin weights are not available.');
  const text = JSON.stringify(payload, null, 2);
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (!document.execCommand('copy')) {
      throw new Error('Clipboard access is unavailable.');
    }
  } finally {
    textarea.remove();
  }
}

function buildSkinningHierarchyControls(parent, mesh, state) {
  const hierarchy = document.createElement('section');
  hierarchy.className = 'inspector-section inspector-skinning-hierarchy';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-subtitle';
  title.textContent = 'Inferred Influence Hierarchy';
  hierarchy.appendChild(title);
  addText(hierarchy, 'inspector-skinning-hint',
    'Derived from shared skin weights and influence-center proximity. '
      + 'It is inferred, not an authored skeleton.');

  const show = document.createElement('button');
  show.type = 'button';
  show.className = 'ui-button inspector-skinning-hierarchy-show';
  show.addEventListener('click', () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    const next = latest.influenceVisualizationMode === 'tree'
      ? null : 'tree';
    setInfluenceVisualizationMode(mesh, next);
    update(latest);
  });
  hierarchy.appendChild(show);

  const rootLabel = document.createElement('label');
  rootLabel.className = 'inspector-skinning-field';
  addText(rootLabel, 'inspector-label', 'Root');
  const rootSelect = document.createElement('select');
  rootSelect.className = 'inspector-skinning-hierarchy-root';
  rootSelect.setAttribute('aria-label', 'Inferred hierarchy root');
  (state.boneIds || []).forEach(id => {
    const option = document.createElement('option');
    option.value = id;
    option.textContent = id;
    rootSelect.appendChild(option);
  });
  rootSelect.addEventListener('change', () => {
    setCandidateTreeRoot(mesh, Number(rootSelect.value));
    update(getSkinningState(mesh));
  });
  rootLabel.appendChild(rootSelect);
  hierarchy.appendChild(rootLabel);

  const summary = document.createElement('pre');
  summary.className = 'inspector-skinning-hierarchy-summary';
  hierarchy.appendChild(summary);
  const output = document.createElement('pre');
  output.className = 'inspector-skinning-hierarchy-output';
  hierarchy.appendChild(output);

  let copying = false;
  const copyButton = document.createElement('button');
  copyButton.type = 'button';
  copyButton.className = 'ui-button inspector-skinning-copy-skinning';
  copyButton.textContent = 'Copy Skinning Diagnostics';
  copyButton.addEventListener('click', async () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    copying = true;
    update(latest);
    try {
      await copySkinningDiagnostics(mesh, latest);
      copyStatus.textContent = 'Skinning diagnostics copied.';
    } catch (error) {
      copyStatus.textContent = error instanceof Error
        ? error.message : String(error);
    } finally {
      copying = false;
      update(getSkinningState(mesh));
    }
  });
  hierarchy.appendChild(copyButton);
  const copyStatus = addText(hierarchy, 'inspector-skinning-copy-status', '');

  function update(latest = getSkinningState(mesh)) {
    if (!latest) return;
    const forest = latest.candidateForest;
    const visible = latest.influenceVisualizationMode === 'tree';
    show.textContent = visible ? 'Hide Hierarchy' : 'Show Hierarchy';
    show.setAttribute('aria-pressed', String(visible));
    show.disabled = !latest.loaded;
    rootSelect.disabled = !forest;
    rootSelect.value = String(latest.candidateRootId
      ?? latest.boneIds?.[0] ?? '');
    summary.textContent = hierarchySummary(forest);
    output.textContent = forest ? candidateTreeText(latest.candidateTree) : '';
    copyButton.disabled = !latest.loaded || copying;
    if (!latest.loaded) copyStatus.textContent = '';
  }

  parent.appendChild(hierarchy);
  update(state);
  return {update};
}

function physicsSummary(state) {
  if (!state?.candidateForest) {
    return 'Infer the hierarchy to enable experimental secondary motion.';
  }
  const status = state.physicsEnabled
    ? (state.physicsSettled ? 'Settled' : 'Active') : 'Disabled';
  const jointCount = state.physicsState?.joints?.size
    ?? (state.candidateForest.components || []).reduce(
      (total, component) => total + Math.max(
        0, (component.nodeIds || []).length - 1), 0);
  const constraints = getPhysicsConstraintDiagnostics(state);
  return [
    'Physics ' + status + ' · ' + jointCount + ' dynamic joints · 3D',
    'Frequency ' + Number(state.physicsFrequencyHz).toFixed(2) + ' Hz · '
      + 'Damping ' + Number(state.physicsDampingRatio).toFixed(2),
    'Angular response ' + Number(state.physicsMotionStrength).toFixed(2)
      + ' · Translation response '
      + Number(state.physicsLinearMotionStrength).toFixed(2),
    'Velocity response '
      + Number(state.physicsContinuousLinearResponse).toFixed(2),
    'Gravity ' + (state.physicsGravityEnabled ? 'On' : 'Off')
      + ' · Scale ' + Number(state.physicsGravityScale).toFixed(1),
    'Joint limits ' + (constraints.enabled ? 'On' : 'Off')
      + ' · At limit ' + constraints.atLimitCount
      + ' / ' + constraints.limitedJointCount,
  ].join('\n');
}


function addPhysicsRange(parent, className, label, min, max, step, value,
    onInput) {
  const field = document.createElement('label');
  field.className = 'inspector-skinning-field';
  const header = document.createElement('div');
  header.className = 'inspector-skinning-rotation-header';
  addText(header, 'inspector-label', label);
  const valueNode = addText(header, 'inspector-value', String(value));
  field.appendChild(header);
  const input = document.createElement('input');
  input.type = 'range';
  input.className = className;
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.value = String(value);
  input.addEventListener('input', () => {
    onInput(input.value);
    valueNode.textContent = input.value;
  });
  field.appendChild(input);
  parent.appendChild(field);
  return {input, valueNode};
}

function buildSkinningPhysicsControls(parent, mesh, state) {
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-skinning-physics';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-subtitle';
  title.textContent = 'Secondary Motion — Experimental';
  section.appendChild(title);
  addText(section, 'inspector-skinning-hint',
    'Hold RMB and drag the viewport to excite secondary motion. '
      + 'LMB continues to control the camera.');

  const enableLabel = document.createElement('label');
  enableLabel.className = 'inspector-skinning-physics-enable-label';
  const enableInput = document.createElement('input');
  enableInput.type = 'checkbox';
  enableInput.className = 'inspector-skinning-physics-enable';
  enableInput.addEventListener('change', () => {
    setPhysicsEnabled(mesh, enableInput.checked);
    update();
  });
  enableLabel.appendChild(enableInput);
  addText(enableLabel, 'inspector-label', 'Enable Physics');
  section.appendChild(enableLabel);

  const frequency = addPhysicsRange(section,
    'inspector-skinning-physics-frequency', 'Frequency (Hz)',
    0.1, 10, 0.1, state.physicsFrequencyHz,
    value => setPhysicsFrequency(mesh, value));
  const damping = addPhysicsRange(section,
    'inspector-skinning-physics-damping', 'Damping',
    0, 2, 0.05, state.physicsDampingRatio,
    value => setPhysicsDamping(mesh, value));
  const angular = addPhysicsRange(section,
    'inspector-skinning-physics-motion', 'Angular response',
    0, 1, 0.05, state.physicsMotionStrength,
    value => setPhysicsMotionStrength(mesh, value));
  const linear = addPhysicsRange(section,
    'inspector-skinning-physics-linear', 'Translation response',
    0, 1, 0.05, state.physicsLinearMotionStrength,
    value => setPhysicsLinearMotionStrength(mesh, value));
  const continuous = addPhysicsRange(section,
    'inspector-skinning-physics-continuous-response',
    'Velocity response', 0, 1, 0.05,
    state.physicsContinuousLinearResponse,
    value => setPhysicsContinuousLinearResponse(mesh, value));

  const gravity = document.createElement('div');
  gravity.className = 'inspector-skinning-physics-gravity';
  const gravityLabel = document.createElement('label');
  gravityLabel.className = 'inspector-skinning-checkbox';
  const gravityEnableInput = document.createElement('input');
  gravityEnableInput.type = 'checkbox';
  gravityEnableInput.className = 'inspector-skinning-physics-gravity-enable';
  gravityEnableInput.addEventListener('change', () => {
    setPhysicsGravityEnabled(mesh, gravityEnableInput.checked);
    update();
  });
  gravityLabel.appendChild(gravityEnableInput);
  addText(gravityLabel, 'inspector-label', 'Gravity');
  gravity.appendChild(gravityLabel);
  const gravityScale = addPhysicsRange(gravity,
    'inspector-skinning-physics-gravity-scale', 'Gravity scale',
    0, 2, 0.1, state.physicsGravityScale,
    value => setPhysicsGravityScale(mesh, value));
  const gravityDirection = addText(gravity,
    'inspector-skinning-physics-gravity-direction', '');
  const gravityDiagnostic = addText(gravity,
    'inspector-skinning-physics-gravity-diagnostic', '');
  section.appendChild(gravity);

  const constraints = document.createElement('div');
  constraints.className = 'inspector-skinning-physics-constraints';
  const constraintsLabel = document.createElement('label');
  constraintsLabel.className = 'inspector-skinning-checkbox';
  const constraintsEnableInput = document.createElement('input');
  constraintsEnableInput.type = 'checkbox';
  constraintsEnableInput.className =
    'inspector-skinning-physics-constraints-enable';
  constraintsEnableInput.addEventListener('change', () => {
    setPhysicsConstraintsEnabled(mesh, constraintsEnableInput.checked);
    update();
  });
  constraintsLabel.appendChild(constraintsEnableInput);
  addText(constraintsLabel, 'inspector-label', 'Joint limits');
  constraints.appendChild(constraintsLabel);
  const maxBend = addPhysicsRange(constraints,
    'inspector-skinning-physics-max-bend', 'Max bend',
    0, 90, 1, state.physicsMaxBendDegrees,
    value => setPhysicsMaxBendDegrees(mesh, value));
  const constraintsDiagnostic = addText(constraints,
    'inspector-skinning-physics-constraints-diagnostic', '');
  section.appendChild(constraints);

  const summary = document.createElement('pre');
  summary.className = 'inspector-skinning-physics-summary';
  section.appendChild(summary);
  const actions = document.createElement('div');
  actions.className = 'inspector-skinning-actions';
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button inspector-skinning-physics-reset';
  reset.textContent = 'Reset Motion';
  reset.addEventListener('click', () => {
    resetPhysicsMotion(mesh);
    update();
  });
  actions.appendChild(reset);
  section.appendChild(actions);

  function update(latest = getSkinningState(mesh)) {
    if (!latest) return;
    const valid = !!latest.candidateForest;
    summary.textContent = physicsSummary(latest);
    enableInput.checked = !!latest.physicsEnabled;
    enableInput.disabled = !latest.loaded;
    frequency.input.disabled = !valid;
    frequency.input.value = latest.physicsFrequencyHz;
    frequency.valueNode.textContent =
      Number(latest.physicsFrequencyHz).toFixed(2);
    damping.input.disabled = !valid;
    damping.input.value = latest.physicsDampingRatio;
    damping.valueNode.textContent =
      Number(latest.physicsDampingRatio).toFixed(2);
    angular.input.disabled = !valid;
    angular.input.value = latest.physicsMotionStrength;
    angular.valueNode.textContent =
      Number(latest.physicsMotionStrength).toFixed(2);
    linear.input.disabled = !valid;
    linear.input.value = latest.physicsLinearMotionStrength;
    linear.valueNode.textContent =
      Number(latest.physicsLinearMotionStrength).toFixed(2);
    continuous.input.disabled = !valid;
    continuous.input.value = latest.physicsContinuousLinearResponse;
    continuous.valueNode.textContent =
      Number(latest.physicsContinuousLinearResponse).toFixed(2);
    gravityEnableInput.disabled = !valid;
    gravityEnableInput.checked = !!latest.physicsGravityEnabled;
    gravityScale.input.disabled = !valid;
    gravityScale.input.value = latest.physicsGravityScale;
    gravityScale.valueNode.textContent =
      Number(latest.physicsGravityScale).toFixed(1);
    const local = latest.physicsGravityLocal || GRAVITY_WORLD_DIRECTION;
    gravityDirection.textContent = 'Down (-Y) → local ['
      + local.map(value => Number(value).toFixed(2)).join(', ') + ']';
    const gravityMax = Number(
      latest.physicsGravityDiagnostics?.maxTotalAccelerationMagnitude) || 0;
    gravityDiagnostic.textContent = 'Max gravity acceleration '
      + (gravityMax * 180 / Math.PI).toFixed(1) + ' deg/s²';
    const diagnostics = getPhysicsConstraintDiagnostics(latest);
    constraintsEnableInput.disabled = !valid;
    constraintsEnableInput.checked = diagnostics.enabled;
    maxBend.input.disabled = !valid;
    maxBend.input.value = diagnostics.maxComponentBend;
    maxBend.valueNode.textContent =
      diagnostics.maxComponentBend.toFixed(0) + '°';
    constraintsDiagnostic.textContent = 'At limit '
      + diagnostics.atLimitCount + ' / ' + diagnostics.limitedJointCount
      + ' joints · Max usage '
      + (diagnostics.maxUsage * 100).toFixed(0) + '%';
    reset.disabled = !latest.physicsEnabled;
  }

  parent.appendChild(section);
  update(state);
  physicsUpdates.set(mesh, update);
  return {update};
}

function buildSkinningAdvancedControls(parent, mesh, state) {
  const advanced = document.createElement('div');
  advanced.className = 'inspector-skinning-advanced';
  const physics = buildSkinningPhysicsControls(advanced, mesh, state);
  const hierarchy = buildSkinningHierarchyControls(advanced, mesh, state);
  parent.appendChild(advanced);
  return {
    update(latest = getSkinningState(mesh)) {
      hierarchy.update(latest);
      physics.update(latest);
    },
  };
}

function renderSkinningControls(section, mesh, state, advancedHost = null) {
  const load = section.querySelector('.inspector-skinning-load');
  const status = section.querySelector('.inspector-skinning-status');
  const controls = section.querySelector('.inspector-skinning-controls');
  if (!load || !status || !controls) return;
  if (!state?.loaded) {
    skinningUpdates.delete(mesh);
    physicsUpdates.delete(mesh);
    if (advancedHost) {
      advancedHost.replaceChildren();
      advancedHost.hidden = true;
    }
    controls.hidden = true;
    load.disabled = !!state?.loading;
    load.textContent = state?.loading ? 'Loading…' : 'Load Weights';
    status.textContent = state?.error || '';
    status.hidden = !status.textContent;
    return;
  }

  load.disabled = true;
  load.textContent = 'Weights loaded';
  status.hidden = true;
  controls.hidden = false;
  skinningUpdates.delete(mesh);
  physicsUpdates.delete(mesh);
  controls.replaceChildren();
  if (advancedHost) {
    advancedHost.replaceChildren();
    advancedHost.hidden = false;
  }

  const summary = document.createElement('div');
  summary.className = 'inspector-skinning-summary';
  summary.textContent = state.influenceCount + ' influences / vertex · '
    + state.boneIds.length + ' bone IDs';
  controls.appendChild(summary);

  const boneLabel = document.createElement('label');
  boneLabel.className = 'inspector-skinning-field';
  addText(boneLabel, 'inspector-label', 'Bone ID');
  const boneSelect = document.createElement('select');
  boneSelect.className = 'inspector-skinning-bone';
  state.boneIds.forEach(id => {
    const option = document.createElement('option');
    option.value = id;
    option.textContent = id;
    option.selected = id === state.selectedBone;
    boneSelect.appendChild(option);
  });
  boneSelect.disabled = !state.boneIds.length;
  boneSelect.addEventListener('change', () => {
    setSelectedBone(mesh, Number(boneSelect.value));
    skinningUpdates.get(mesh)?.();
  });
  boneLabel.appendChild(boneSelect);
  controls.appendChild(boneLabel);

  const influenceStats = buildSelectedInfluenceStats(controls, mesh, state);

  const heatmap = document.createElement('button');
  heatmap.type = 'button';
  heatmap.className = 'ui-button inspector-skinning-heatmap';
  heatmap.addEventListener('click', () => {
    setSkinningHeatmap(
      mesh, getSkinningState(mesh).heatmapMode !== 'bone');
    skinningUpdates.get(mesh)?.();
  });
  controls.appendChild(heatmap);

  const center = document.createElement('button');
  center.type = 'button';
  center.className = 'ui-button inspector-skinning-center';
  center.addEventListener('click', () => {
    const latest = getSkinningState(mesh);
    const visible = latest?.influenceVisualizationMode === 'center';
    setInfluenceVisualizationMode(mesh, visible ? null : 'center');
    skinningUpdates.get(mesh)?.();
  });
  controls.appendChild(center);

  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button inspector-skinning-reset';
  reset.textContent = 'Reset';
  reset.addEventListener('click', () => {
    resetSkinningExperiment(mesh);
    renderSkinningControls(
      section, mesh, getSkinningState(mesh), advancedHost);
  });
  controls.appendChild(reset);

  buildSkinningDiagnostics(
    controls, state, mesh.geometry.attributes.position.count);
  const advanced = buildSkinningAdvancedControls(
    advancedHost || controls, mesh, state);

  function update(latest = getSkinningState(mesh)) {
    if (!latest) return;
    boneSelect.value = String(latest.selectedBone ?? '');
    influenceStats.update(latest);
    syncSkinningHeatmapControls(section, mesh);
    const centerVisible = latest.influenceVisualizationMode === 'center';
    center.textContent = centerVisible
      ? 'Hide Influence Center' : 'Show Influence Center';
    center.setAttribute('aria-pressed', String(centerVisible));
    advanced.update(latest);
  }

  skinningUpdates.set(mesh, update);
  update(state);
}
function buildSkinningSection(content, mesh) {
  if (!mesh?.userData?.modPath || !mesh.userData.semanticKey
      || mesh.userData.assetFill === true) return;
  const group = document.createElement('div');
  group.className = 'inspector-skinning-group';
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-skinning-section';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-title';
  title.textContent = 'Skin Weights';
  section.appendChild(title);
  const load = document.createElement('button');
  load.type = 'button';
  load.className = 'ui-button inspector-skinning-load';
  load.textContent = 'Load Weights';
  section.appendChild(load);
  const status = addText(section, 'inspector-skinning-status', '');
  status.hidden = true;
  const controls = document.createElement('div');
  controls.className = 'inspector-skinning-controls';
  controls.hidden = true;
  section.appendChild(controls);
  const advancedHost = document.createElement('div');
  advancedHost.className = 'inspector-skinning-advanced-host';
  advancedHost.hidden = true;
  group.append(section, advancedHost);
  content.appendChild(group);
  renderSkinningControls(
    section, mesh, getSkinningState(mesh), advancedHost);
  load.addEventListener('click', async () => {
    load.disabled = true;
    status.hidden = true;
    status.textContent = '';
    try {
      await loadSkinningWeights(mesh);
    } catch (error) {
      console.error('Could not load skin weights', error);
    }
    renderSkinningControls(
      section, mesh, getSkinningState(mesh), advancedHost);
  });
}

function buildComponent(record) {
  const content = showContent();
  content.replaceChildren();
  buildHeader(
    content,
    record.component || 'Component',
    componentContext(record),
    record.source || '',
  );
  buildMaterialSection(content, record);
  buildComponentTextureSection(content, record);
}

function buildMesh(mesh, record) {
  const content = showContent();
  content.replaceChildren();
  const name = mesh.userData.displayName || record.label || 'Mesh';
  const component = record.component;
  const componentName = component?.component || component || 'Component';
  buildHeader(content, name, componentName, record.entry?.source?.[0]?.ini || '');
  buildMaterialSection(content, component || {});
  buildTextureControls(content, record, mesh);
  buildSkinningSection(content, mesh);
}

function updateInspectorState() {
  if (!current) return;
  const content = $('inspector-content');
  if (!content) return;
  const material = content.querySelector('.inspector-material-kind-control');
  if (material) {
    const owner = current.type === 'mesh' ? current.record.component : current.record;
    material.value = owner?.getMaterialKind?.() || 'auto';
  }
  if (current.type === 'mesh') {
    updateTextureControlState(
      content, current.mesh, current.record.component);
  } else {
    const context = content.querySelector('[data-inspector-context="true"]');
    if (context) context.textContent = componentContext(current.record);
    const count = content.querySelector('.inspector-texture-count');
    if (count) {
      const total = current.record.texturePool?.length || 0;
      count.textContent = total ? `${total} available` : 'No textures discovered';
    }
  }
}

function selectComponent(record) {
  if (current?.type === 'component') current.record.header?.classList.remove('selected');
  clearSelection();
  if (selectionCount++ === 0 && isRightDockOpen()) {
    setRightDockTab('inspector', { persist: false });
  }
  current = { type: 'component', record };
  record.header?.classList.add('selected');
  buildComponent(record);
  const status = $('selected-mesh-status');
  if (status) status.textContent = record.component || 'Component';
}

function selectMesh(mesh) {
  const record = meshRecords.get(mesh);
  if (!record) return;
  if (selectionCount++ === 0 && isRightDockOpen()) {
    setRightDockTab('inspector', { persist: false });
  }
  if (current?.type === 'component') current.record.header?.classList.remove('selected');
  current = { type: 'mesh', mesh, record };
  buildMesh(mesh, record);
  const status = $('selected-mesh-status');
  if (status) {
    const componentName = record.component?.component || record.component || 'Component';
    const meshName = mesh.userData.displayName || record.label || 'Mesh';
    status.textContent = `${componentName} > ${meshName}`;
  }
}

export function initInspectorPanel() {
  window.addEventListener('mod-viewer-component-selected', event => {
    if (event.detail?.component) selectComponent(event.detail.component);
  });
  window.addEventListener('mod-viewer-mesh-selected', event => {
    if (event.detail?.mesh) selectMesh(event.detail.mesh);
    else {
      if (current?.type === 'component') current.record.header?.classList.remove('selected');
      current = null;
      $('selected-mesh-status').textContent = '';
      clearContent();
    }
  });
  window.addEventListener('mod-viewer-inspector-refresh', event => {
    const component = event.detail?.component;
    const reason = event.detail?.reason || 'selection';
    if (!component || !current) return;
    if (reason === 'state') {
      if ((current.type === 'component' && current.record === component)
          || (current.type === 'mesh' && current.record.component === component)) {
        updateInspectorState();
      }
      return;
    }
    if (current.type === 'component' && current.record === component) buildComponent(component);
    if (current.type === 'mesh' && current.record.component === component) {
      buildMesh(current.mesh, current.record);
    }
  });
  window.addEventListener('mod-viewer-mesh-state-changed', event => {
    const changed = event.detail?.meshes || [];
    if (!current || !changed.length) return;
    const affected = current.type === 'mesh'
      ? changed.includes(current.mesh)
      : (current.record.meshes || []).some(mesh => changed.includes(mesh));
    if (affected) updateInspectorState();
  });
  window.addEventListener('mod-viewer-model-transform-changed', event => {
    const changed = event.detail?.meshes || [];
    if (current?.type === 'mesh' && changed.includes(current.mesh)) {
      physicsUpdates.get(current.mesh)?.();
    }
  });
  clearContent();
}

export function clearInspector() {
  if (current?.type === 'component') current.record.header?.classList.remove('selected');
  current = null;
  selectionCount = 0;
  $('selected-mesh-status').textContent = '';
  clearContent();
}

export function getInspectorSelection() {
  return current;
}
