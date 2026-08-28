// Selection-aware details panel. Mesh creation remains owned by mesh-panel;
// this module only presents the already-authoritative mesh/component state.

import { isRightDockOpen, setRightDockTab } from './right-dock.js';
import { clearSelection } from '../scene/selection.js';
import {
  SIGNIFICANT_RESIDUAL_RATIO, SIGNIFICANT_VERTEX_WEIGHT,
  getSkinningState, loadSkinningWeights, resetSkinningExperiment,
  setSelectedBone, setSkinningAngle, setSkinningAxis, setSkinningChainAngle,
  setSkinningChainAxis, setSkinningChainText, setSkinningHeatmap,
  setSkinningHeatmapMode,
  setVirtualChainVisible,
} from '../mesh/weight-experiment.js';

const meshRecords = new WeakMap();
let current = null;
let selectionCount = 0;
const MISSING_INFLUENCE_DISPLAY_LIMIT = 8;

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

function syncSkinningAngleControls(section, mesh) {
  const state = getSkinningState(mesh);
  if (!state || !section) return;
  const angle = section.querySelector('.inspector-skinning-angle');
  const angleValue = section.querySelector('.inspector-skinning-angle-value');
  if (angle) angle.value = state.angle;
  if (angleValue) angleValue.textContent = `${state.angle}\u00b0`;
  const chainAngle = section.querySelector('.inspector-skinning-chain-angle');
  const chainValue = section.querySelector('.inspector-skinning-chain-value');
  if (chainAngle) chainAngle.value = state.chainAngle;
  if (chainValue) chainValue.textContent = `${state.chainAngle}\u00b0`;
  const showHelpers = section.querySelector('.inspector-skinning-chain-show');
  if (showHelpers) {
    showHelpers.textContent = state.chainHelpersVisible
      ? 'Hide Virtual Chain' : 'Show Virtual Chain';
    showHelpers.setAttribute('aria-pressed', String(state.chainHelpersVisible));
  }
}

function syncSkinningHeatmapControls(section, mesh) {
  const state = getSkinningState(mesh);
  if (!state || !section) return;
  const bone = section.querySelector('.inspector-skinning-heatmap');
  if (bone) {
    const active = state.heatmapMode === 'bone';
    bone.setAttribute('aria-pressed', String(active));
    bone.classList.toggle('active', active);
    bone.textContent = active
      ? 'Hide Weight Heatmap' : 'Show Weight Heatmap';
  }
  const residual = section.querySelector('.inspector-skinning-residual');
  if (residual) {
    const available = !!state.chainCoverage;
    const active = state.heatmapMode === 'chain-residual';
    residual.disabled = !available;
    residual.setAttribute('aria-pressed', String(active));
    residual.classList.toggle('active', active);
    residual.textContent = active
      ? 'Hide Residual Heatmap' : 'Show Residual Heatmap';
  }
}

function coveragePercent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function coverageNumber(value) {
  return Number(value || 0).toFixed(3);
}

function significantMissingInfluences(state) {
  const entries = state.missingInfluences || [];
  const totalResidual = entries.reduce(
    (total, entry) => total + entry.residualContribution, 0);
  if (!totalResidual) return [];
  const selected = entries.filter(entry =>
    entry.residualContribution / totalResidual >= SIGNIFICANT_RESIDUAL_RATIO
      || entry.maxVertexWeight >= SIGNIFICANT_VERTEX_WEIGHT);
  return selected;
}

function buildSkinningCoverageControls(parent, mesh, state) {
  const coverage = document.createElement('div');
  coverage.className = 'inspector-skinning-coverage';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-subtitle';
  title.textContent = 'Chain Coverage';
  coverage.appendChild(title);
  const note = addText(coverage, 'inspector-skinning-hint',
    'Ranked IDs describe coverage only; order is not inferred.');
  const empty = addText(coverage, 'inspector-skinning-hint',
    'Enter a valid chain to measure omitted influence.');
  const stats = document.createElement('div');
  stats.className = 'inspector-skinning-coverage-stats';
  coverage.appendChild(stats);
  const residual = document.createElement('button');
  residual.type = 'button';
  residual.className = 'ui-button inspector-skinning-residual';
  residual.addEventListener('click', () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    setSkinningHeatmapMode(
      mesh, latest.heatmapMode === 'chain-residual' ? null : 'chain-residual');
    update(latest);
  });
  coverage.appendChild(residual);

  const missingTitle = document.createElement('div');
  missingTitle.className = 'inspector-skinning-missing-title';
  missingTitle.textContent = 'Missing influences';
  coverage.appendChild(missingTitle);
  const missing = document.createElement('div');
  missing.className = 'inspector-skinning-missing';
  coverage.appendChild(missing);
  const addMissing = document.createElement('button');
  addMissing.type = 'button';
  addMissing.className = 'ui-button inspector-skinning-add-missing';
  addMissing.textContent = 'Add Significant Missing IDs';
  addMissing.addEventListener('click', () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    const additions = significantMissingInfluences(latest)
      .map(entry => entry.boneId);
    if (!additions.length) return;
    const nextText = [...latest.chainIds, ...additions].join(',');
    setSkinningChainText(mesh, nextText);
    const section = coverage.closest('.inspector-skinning-section');
    const chainInput = section?.querySelector('.inspector-skinning-chain-ids');
    if (chainInput) chainInput.value = nextText;
    update(getSkinningState(mesh));
  });
  coverage.appendChild(addMissing);
  parent.appendChild(coverage);

  function addStat(label, value) {
    const row = document.createElement('div');
    row.className = 'inspector-skinning-coverage-stat';
    addText(row, 'inspector-label', label);
    addText(row, 'inspector-value', value);
    stats.appendChild(row);
  }

  function update(latest = getSkinningState(mesh)) {
    if (!latest) return;
    const data = latest.chainCoverage;
    stats.replaceChildren();
    missing.replaceChildren();
    const valid = !!data;
    empty.hidden = valid;
    note.hidden = !valid;
    stats.hidden = !valid;
    residual.hidden = !valid;
    missingTitle.hidden = !valid;
    missing.hidden = !valid;
    addMissing.hidden = true;
    if (!valid) {
      syncSkinningHeatmapControls(
        coverage.closest('.inspector-skinning-section'), mesh);
      return;
    }
    const vertexCount = data.vertexCount || 0;
    addStat('Average', coveragePercent(data.averageCoverage));
    addStat('Fully covered', coveragePercent(
      vertexCount ? data.fullyCoveredVertices / vertexCount : 0));
    addStat('\u226599% vertices', coveragePercent(
      vertexCount ? data.covered99Vertices / vertexCount : 0));
    addStat('\u226595% vertices', coveragePercent(
      vertexCount ? data.covered95Vertices / vertexCount : 0));
    addStat('Max residual', coveragePercent(data.maxResidual));
    if (data.overweightVertices || data.underweightVertices) {
      addStat('Weight sanity',
        `${data.overweightVertices} >100.1% / `
        + `${data.underweightVertices} <99.9%`);
    }

    const entries = latest.missingInfluences || [];
    const totalResidual = entries.reduce(
      (total, entry) => total + entry.residualContribution, 0);
    entries.slice(0, MISSING_INFLUENCE_DISPLAY_LIMIT).forEach(entry => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'inspector-skinning-missing-row';
      const share = totalResidual
        ? entry.residualContribution / totalResidual : 0;
      row.textContent = `ID ${entry.boneId}  residual ${coveragePercent(share)}`
        + ` · ${entry.affectedVertexCount} verts`;
      row.title = `Total omitted weight ${coverageNumber(entry.totalWeight)}; `
        + `maximum per-vertex weight ${coverageNumber(entry.maxVertexWeight)}`;
      row.addEventListener('click', () => {
        setSelectedBone(mesh, entry.boneId);
        setSkinningHeatmapMode(mesh, 'bone');
        const section = coverage.closest('.inspector-skinning-section');
        const select = section?.querySelector('.inspector-skinning-bone');
        if (select) select.value = entry.boneId;
        syncSkinningHeatmapControls(section, mesh);
      });
      missing.appendChild(row);
    });
    if (entries.length > MISSING_INFLUENCE_DISPLAY_LIMIT) {
      addText(missing, 'inspector-skinning-hint',
        `+ ${entries.length - MISSING_INFLUENCE_DISPLAY_LIMIT} more`);
    }
    addMissing.hidden = !significantMissingInfluences(latest).length;
    syncSkinningHeatmapControls(
      coverage.closest('.inspector-skinning-section'), mesh);
  }

  update(state);
  return {update};
}

function buildSkinningChainControls(parent, mesh, state) {
  const chain = document.createElement('div');
  chain.className = 'inspector-skinning-chain';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-subtitle';
  title.textContent = 'Virtual Chain Test';
  chain.appendChild(title);
  addText(chain, 'inspector-skinning-hint',
    'Enter an ordered sequence of loaded Bone IDs.');

  const idsLabel = document.createElement('label');
  idsLabel.className = 'inspector-skinning-field';
  addText(idsLabel, 'inspector-label', 'Chain IDs');
  const idsInput = document.createElement('input');
  idsInput.type = 'text';
  idsInput.className = 'inspector-skinning-chain-ids';
  idsInput.placeholder = '0,2,3,4';
  idsInput.value = state.chainText;
  idsInput.setAttribute('aria-label', 'Virtual chain bone IDs');
  idsLabel.appendChild(idsInput);
  chain.appendChild(idsLabel);

  const chainStatus = addText(chain, 'inspector-skinning-chain-status', '');
  chainStatus.setAttribute('aria-live', 'polite');
  chainStatus.hidden = true;

  const axisRow = document.createElement('div');
  axisRow.className = 'inspector-skinning-chain-axis';
  addText(axisRow, 'inspector-label', 'Axis');
  const axisButtons = document.createElement('span');
  ['X', 'Y', 'Z'].forEach(axis => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'inspector-skinning-axis-button';
    button.textContent = axis;
    button.addEventListener('click', () => {
      setSkinningChainAxis(mesh, axis);
      update();
    });
    axisButtons.appendChild(button);
  });
  axisRow.appendChild(axisButtons);
  chain.appendChild(axisRow);

  const bendRow = document.createElement('div');
  bendRow.className = 'inspector-skinning-chain-bend';
  const bendHeader = document.createElement('div');
  bendHeader.className = 'inspector-skinning-rotation-header';
  addText(bendHeader, 'inspector-label', 'Total Bend');
  const bendValue = addText(
    bendHeader, 'inspector-skinning-chain-value', `${state.chainAngle}\u00b0`);
  bendRow.appendChild(bendHeader);
  const bendSlider = document.createElement('input');
  bendSlider.type = 'range';
  bendSlider.className = 'inspector-skinning-chain-angle';
  bendSlider.min = '-60';
  bendSlider.max = '60';
  bendSlider.step = '1';
  bendSlider.value = state.chainAngle;
  bendSlider.addEventListener('input', () => {
    setSkinningChainAngle(mesh, bendSlider.value);
    syncSkinningAngleControls(chain.closest('.inspector-skinning-section'), mesh);
  });
  bendRow.appendChild(bendSlider);
  chain.appendChild(bendRow);

  const helperRow = document.createElement('div');
  helperRow.className = 'inspector-skinning-chain-actions';
  const showHelpers = document.createElement('button');
  showHelpers.type = 'button';
  showHelpers.className = 'ui-button inspector-skinning-chain-show';
  showHelpers.addEventListener('click', () => {
    setVirtualChainVisible(mesh, !getSkinningState(mesh).chainHelpersVisible);
    update();
  });
  helperRow.appendChild(showHelpers);
  const resetChain = document.createElement('button');
  resetChain.type = 'button';
  resetChain.className = 'ui-button inspector-skinning-chain-reset';
  resetChain.textContent = 'Reset Chain';
  resetChain.addEventListener('click', () => {
    resetSkinningExperiment(mesh);
    renderSkinningControls(
      chain.closest('.inspector-skinning-section'), mesh,
      getSkinningState(mesh));
  });
  helperRow.appendChild(resetChain);
  chain.appendChild(helperRow);
  const coverage = buildSkinningCoverageControls(chain, mesh, state);

  function update() {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    idsInput.value = latest.chainText;
    chainStatus.textContent = latest.chainError || '';
    chainStatus.hidden = !chainStatus.textContent;
    const valid = latest.chainIds.length >= 2;
    bendSlider.disabled = !valid;
    axisButtons.querySelectorAll('button').forEach(button => {
      button.disabled = !valid;
      button.classList.toggle('selected', button.textContent === latest.chainAxis);
    });
    showHelpers.disabled = !valid;
    showHelpers.textContent = latest.chainHelpersVisible
      ? 'Hide Virtual Chain' : 'Show Virtual Chain';
    showHelpers.setAttribute('aria-pressed', String(latest.chainHelpersVisible));
    syncSkinningAngleControls(
      chain.closest('.inspector-skinning-section'), mesh);
    coverage.update(latest);
    syncSkinningHeatmapControls(
      chain.closest('.inspector-skinning-section'), mesh);
  }

  idsInput.addEventListener('input', () => {
    setSkinningChainText(mesh, idsInput.value);
    update();
  });
  parent.appendChild(chain);
  update();
}

function renderSkinningControls(section, mesh, state) {
  const load = section.querySelector('.inspector-skinning-load');
  const status = section.querySelector('.inspector-skinning-status');
  const controls = section.querySelector('.inspector-skinning-controls');
  if (!load || !status || !controls) return;
  if (!state?.loaded) {
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
  controls.replaceChildren();

  const summary = document.createElement('div');
  summary.className = 'inspector-skinning-summary';
  summary.textContent = `${state.influenceCount} influences / vertex · ${state.boneIds.length} bone IDs`;
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
  });
  boneLabel.appendChild(boneSelect);
  controls.appendChild(boneLabel);

  const axisRow = document.createElement('div');
  axisRow.className = 'inspector-skinning-axis';
  addText(axisRow, 'inspector-label', 'Axis');
  const axisButtons = document.createElement('span');
  ['X', 'Y', 'Z'].forEach(axis => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'inspector-skinning-axis-button';
    button.textContent = axis;
    button.classList.toggle('selected', axis === state.axis);
    button.addEventListener('click', () => {
      setSkinningAxis(mesh, axis);
      axisButtons.querySelectorAll('button').forEach(item => {
        item.classList.toggle('selected', item === button);
      });
    });
    axisButtons.appendChild(button);
  });
  axisRow.appendChild(axisButtons);
  controls.appendChild(axisRow);

  const rotationRow = document.createElement('div');
  rotationRow.className = 'inspector-skinning-rotation';
  const rotationHeader = document.createElement('div');
  rotationHeader.className = 'inspector-skinning-rotation-header';
  addText(rotationHeader, 'inspector-label', 'Rotation');
  const rotationValue = addText(rotationHeader, 'inspector-value', `${state.angle}°`);
  rotationRow.appendChild(rotationHeader);
  rotationValue.classList.add('inspector-skinning-angle-value');
  const slider = document.createElement('input');
  slider.type = 'range';
  slider.className = 'inspector-skinning-angle';
  slider.min = '-45';
  slider.max = '45';
  slider.step = '1';
  slider.value = state.angle;
  slider.addEventListener('input', () => {
    setSkinningAngle(mesh, slider.value);
    syncSkinningAngleControls(section, mesh);
    rotationValue.textContent = `${getSkinningState(mesh).angle}°`;
  });
  rotationRow.appendChild(slider);
  controls.appendChild(rotationRow);

  const heatmap = document.createElement('button');
  heatmap.type = 'button';
  heatmap.className = 'ui-button inspector-skinning-heatmap';
  heatmap.textContent = 'Show Weight Heatmap';
  heatmap.setAttribute('aria-pressed', String(state.heatmapMode === 'bone'));
  heatmap.addEventListener('click', () => {
    const enabled = setSkinningHeatmap(
      mesh, getSkinningState(mesh).heatmapMode !== 'bone');
    syncSkinningHeatmapControls(section, mesh);
    return enabled;
  });
  controls.appendChild(heatmap);

  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button inspector-skinning-reset';
  reset.textContent = 'Reset';
  reset.addEventListener('click', () => {
    resetSkinningExperiment(mesh);
    renderSkinningControls(section, mesh, getSkinningState(mesh));
  });
  controls.appendChild(reset);

  buildSkinningDiagnostics(
    controls, state, mesh.geometry.attributes.position.count);
  buildSkinningChainControls(controls, mesh, state);
}

function buildSkinningSection(content, mesh) {
  if (!mesh?.userData?.modPath || !mesh.userData.semanticKey
      || mesh.userData.assetFill === true) return;
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-skinning-section';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-title';
  title.textContent = 'Experimental — Skin Weights';
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
  renderSkinningControls(section, mesh, getSkinningState(mesh));
  load.addEventListener('click', async () => {
    load.disabled = true;
    status.hidden = true;
    status.textContent = '';
    try {
      await loadSkinningWeights(mesh);
    } catch (error) {
      console.error('Could not load skin weights', error);
    }
    renderSkinningControls(section, mesh, getSkinningState(mesh));
  });
  content.appendChild(section);
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
