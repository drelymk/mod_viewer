// Selection-aware details panel. Mesh creation remains owned by mesh-panel;
// this module presents material and texture state for the selected item.

import { getRightDockTab, isRightDockOpen, setRightDockTab } from './right-dock.js';
import { clearSelection } from '../scene/selection.js';
import {
  canEditMeshColor, getMeshColorAdjustment, resetMeshColorAdjustment,
  setMeshColorAdjustment,
} from '../mesh/mesh-color-state.js';
import { canAnalyzeTextureBake } from '../mesh/texture-bake-analysis.js';
import { openTextureBakeModal } from '../ui/texture-bake-modal.js';
import { isNeutralColorAdjustment } from '../mesh/color-adjustment.js';

const meshRecords = new WeakMap();
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
    ? `${pool.length} available` : 'No textures discovered');
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
  addOption(automaticTextureLabel(override.resolved, pool), undefined,
    override.automatic, 'automatic', override.resolved || 'Automatic');
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

function formatHue(value) {
  const rounded = Math.round(value);
  return `${rounded > 0 ? '+' : ''}${rounded}°`;
}

function formatPercent(value) {
  return `${Math.round(value)}%`;
}

function colorControlValue(field, adjustment) {
  return field === 'hue' ? adjustment.hue : adjustment[field] * 100;
}

function colorAdjustmentValue(field, controlValue) {
  return field === 'hue' ? controlValue : controlValue / 100;
}

/** Build one range control shared by the Inspector's color sliders. */
function buildRangeControl({
  field, label, min, max, step, value, formatValue, onInput, onChange,
}) {
  const row = document.createElement('label');
  row.className = 'inspector-color-control';
  row.dataset.colorField = field;
  const heading = document.createElement('span');
  heading.className = 'inspector-color-control-heading';
  heading.textContent = label;
  const valueNode = document.createElement('span');
  valueNode.className = 'inspector-color-value';
  const slider = document.createElement('input');
  slider.type = 'range';
  slider.className = 'inspector-color-slider';
  slider.min = String(min);
  slider.max = String(max);
  slider.step = String(step);
  slider.value = String(value);
  const syncValue = () => {
    valueNode.textContent = formatValue(Number(slider.value));
  };
  slider.addEventListener('input', () => {
    syncValue();
    onInput(Number(slider.value));
  });
  slider.addEventListener('change', () => onChange(Number(slider.value)));
  syncValue();
  row.append(heading, slider, valueNode);
  return row;
}

function updateColorAdjustment(mesh, field, controlValue, persist = false) {
  const next = getMeshColorAdjustment(mesh);
  next[field] = colorAdjustmentValue(field, controlValue);
  setMeshColorAdjustment(mesh, next, { persist, render: true });
  const action = document.querySelector('.inspector-texture-bake');
  if (action) {
    const neutral = isNeutralColorAdjustment(getMeshColorAdjustment(mesh));
    action.disabled = neutral;
    action.title = neutral ? 'Adjust the mesh color before baking.' : '';
  }
}

function buildTextureBakeAction(section, mesh) {
  const eligibility = canAnalyzeTextureBake(mesh);
  if (eligibility.editable) {
    const bake = document.createElement('button');
    bake.type = 'button';
    bake.className = 'ui-button inspector-texture-bake';
    bake.textContent = 'Bake to Texture…';
    const neutral = isNeutralColorAdjustment(getMeshColorAdjustment(mesh));
    bake.disabled = neutral;
    if (neutral) bake.title = 'Adjust the mesh color before baking.';
    bake.addEventListener('click', async () => {
      bake.disabled = true;
      try {
        await openTextureBakeModal(mesh, {
          isCurrent: () => current?.type === 'mesh' && current.mesh === mesh,
        });
      } finally {
        bake.disabled = isNeutralColorAdjustment(
          getMeshColorAdjustment(mesh));
      }
    });
    section.appendChild(bake);
  } else if (eligibility.reason === 'unsupported-texture-type') {
    addText(section, 'inspector-texture-bake-hint', eligibility.message);
  }
}

function buildColorSection(content, mesh) {
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-color-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Color';
  section.appendChild(title);

  const eligibility = canEditMeshColor(mesh);
  section.dataset.colorEditable = String(eligibility.editable);
  section.dataset.colorReason = eligibility.reason || '';
  if (!eligibility.editable) {
    if (eligibility.reason === 'asset-texture') {
      addText(section, 'inspector-color-readonly-title', 'Asset texture');
      addText(section, 'inspector-color-readonly',
        'Color editing is unavailable for Asset textures.');
    } else {
      addText(section, 'inspector-color-readonly-title', 'No diffuse texture');
      addText(section, 'inspector-color-readonly',
        'Select a diffuse texture to adjust its color.');
    }
    content.appendChild(section);
    return section;
  }

  const adjustment = getMeshColorAdjustment(mesh);
  const addSlider = (field, label, min, max, step, formatValue) => {
    section.appendChild(buildRangeControl({
      field, label, min, max, step,
      value: colorControlValue(field, adjustment), formatValue,
      onInput: value => updateColorAdjustment(mesh, field, value),
      onChange: value => updateColorAdjustment(mesh, field, value, true),
    }));
  };
  addSlider('hue', 'Hue', -180, 180, 1, formatHue);
  addSlider('saturation', 'Saturation', 0, 200, 1, formatPercent);
  addSlider('brightness', 'Brightness', 0, 200, 1, formatPercent);
  addSlider('contrast', 'Contrast', 0, 200, 1, formatPercent);

  const rgbTitle = document.createElement('div');
  rgbTitle.className = 'inspector-color-subtitle';
  rgbTitle.textContent = 'RGB';
  section.appendChild(rgbTitle);
  addSlider('red', 'R', 0, 200, 1, formatPercent);
  addSlider('green', 'G', 0, 200, 1, formatPercent);
  addSlider('blue', 'B', 0, 200, 1, formatPercent);

  const tint = document.createElement('label');
  tint.className = 'inspector-color-tint';
  const tintLabel = document.createElement('span');
  tintLabel.className = 'inspector-color-control-heading';
  tintLabel.textContent = 'Tint';
  const tintInput = document.createElement('input');
  tintInput.type = 'color';
  tintInput.className = 'inspector-color-tint-input';
  tintInput.value = adjustment.tint;
  const tintValue = document.createElement('span');
  tintValue.className = 'inspector-color-value';
  tintValue.dataset.colorTintValue = 'true';
  tintValue.textContent = adjustment.tint.toUpperCase();
  const applyTint = persist => {
    tintValue.textContent = tintInput.value.toUpperCase();
    const next = getMeshColorAdjustment(mesh);
    next.tint = tintInput.value;
    setMeshColorAdjustment(mesh, next, { persist, render: true });
    const action = section.querySelector('.inspector-texture-bake');
    if (action) {
      const neutral = isNeutralColorAdjustment(getMeshColorAdjustment(mesh));
      action.disabled = neutral;
      action.title = neutral ? 'Adjust the mesh color before baking.' : '';
    }
  };
  tintInput.addEventListener('input', () => applyTint(false));
  tintInput.addEventListener('change', () => applyTint(true));
  tint.append(tintLabel, tintInput, tintValue);
  section.appendChild(tint);

  addSlider('tintStrength', 'Strength', 0, 100, 1, formatPercent);

  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button inspector-color-reset';
  reset.textContent = 'Reset Color';
  reset.addEventListener('click', () => {
    resetMeshColorAdjustment(mesh, { persist: true, render: true });
    updateColorControlState(content, mesh);
  });
  section.appendChild(reset);
  buildTextureBakeAction(section, mesh);
  content.appendChild(section);
  return section;
}

function updateColorControlState(content, mesh) {
  const section = content.querySelector('.inspector-color-section');
  if (!section) return true;
  const eligibility = canEditMeshColor(mesh);
  if (section.dataset.colorEditable !== String(eligibility.editable)
      || section.dataset.colorReason !== (eligibility.reason || '')) {
    return false;
  }
  if (!eligibility.editable) return true;
  const adjustment = getMeshColorAdjustment(mesh);
  section.querySelectorAll('[data-color-field]').forEach(row => {
    const field = row.dataset.colorField;
    const slider = row.querySelector('.inspector-color-slider');
    const value = row.querySelector('.inspector-color-value');
    if (!slider || !value || !Object.hasOwn(adjustment, field)) return;
    const controlValue = colorControlValue(field, adjustment);
    slider.value = String(controlValue);
    value.textContent = field === 'hue'
      ? formatHue(controlValue) : formatPercent(controlValue);
  });
  const tintInput = section.querySelector('.inspector-color-tint-input');
  const tintValue = section.querySelector('[data-color-tint-value]');
  if (tintInput) tintInput.value = adjustment.tint;
  if (tintValue) tintValue.textContent = adjustment.tint.toUpperCase();
  return true;
}

function buildComponent(record) {
  const content = showContent();
  content.replaceChildren();
  buildHeader(content, record.component || 'Component',
    componentContext(record), record.source || '');
  buildMaterialSection(content, record);
  buildComponentTextureSection(content, record);
}

function buildMesh(mesh, record) {
  const content = showContent();
  content.replaceChildren();
  const name = mesh.userData.displayName || record.label || 'Mesh';
  const component = record.component;
  const componentName = component?.component || component || 'Component';
  buildHeader(content, name, componentName,
    record.entry?.source?.[0]?.ini || '');
  buildMaterialSection(content, component || {});
  buildTextureControls(content, record, mesh);
  buildColorSection(content, mesh);
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
    updateTextureControlState(content, current.mesh, current.record.component);
    if (!updateColorControlState(content, current.mesh)) {
      buildMesh(current.mesh, current.record);
    }
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

function showInspectorOnSelection() {
  if (selectionCount++ === 0 && isRightDockOpen()
      && getRightDockTab() !== 'weight') {
    setRightDockTab('inspector', {persist: false});
  }
}

function selectComponent(record) {
  if (current?.type === 'component') current.record.header?.classList.remove('selected');
  clearSelection();
  showInspectorOnSelection();
  current = {type: 'component', record};
  record.header?.classList.add('selected');
  buildComponent(record);
  const status = $('selected-mesh-status');
  if (status) status.textContent = record.component || 'Component';
}

function selectMesh(mesh) {
  const record = meshRecords.get(mesh);
  if (!record) return;
  showInspectorOnSelection();
  if (current?.type === 'component') current.record.header?.classList.remove('selected');
  current = {type: 'mesh', mesh, record};
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
