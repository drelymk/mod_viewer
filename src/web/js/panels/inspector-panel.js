// Selection-aware details panel. Mesh creation remains owned by mesh-panel;
// this module presents material and texture state for the selected item.

import { getRightDockTab, isRightDockOpen, setRightDockTab } from './right-dock.js';
import { clearSelection } from '../scene/selection.js';
import {
  canEditMeshColor, getMeshColorAdjustment, resetMeshColorAdjustment,
  setMeshColorAdjustment,
} from '../mesh/mesh-color-session.js';
import {
  canSaveTexture, getTextureSaveTargets,
} from '../mesh/texture-save-session.js';
import { openTextureSaveModal } from '../ui/texture-save-modal.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { getLoosePartSource } from '../mesh/loose-parts.js';
import {
  assetDetailLabel, assetMatchLabel, assetSummaryLabel,
} from './asset-diagnostics.js';

const meshRecords = new WeakMap();
let current = null;
let selectionCount = 0;
const $ = id => document.getElementById(id);

function semanticMesh(mesh) {
  return getLoosePartSource(mesh) || mesh;
}

function meshDisplayLabel(mesh, fallback) {
  return mesh?.userData?.loosePartLabel
    || mesh?.userData?.displayName || fallback || t('inspector.mesh');
}

const MATERIAL_KIND_OPTIONS = Object.freeze([
  ['auto', 'inspector.materialKind.auto'],
  ['body', 'inspector.materialKind.body'],
  ['face', 'inspector.materialKind.face'],
  ['hair', 'inspector.materialKind.hair'],
  ['eye', 'inspector.materialKind.eye'],
  ['weapon', 'inspector.materialKind.weapon'],
  ['special', 'inspector.materialKind.special'],
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
    || t('inspector.texture');
}

function automaticTextureLabel(resolved, pool) {
  if (!resolved) return t('inspector.automatic');
  const option = pool.find(item => item.tex_key === resolved);
  if (option) return `${t('inspector.automatic')} · ${textureOptionLabel(option)}`;
  const file = String(resolved).split('::').slice(1).join('::');
  return file ? `${t('inspector.automatic')} · ${basename(file)}` : t('inspector.automatic');
}

function componentContext(record) {
  const meshes = record.meshes || [];
  const total = meshes.length;
  const visible = meshes.filter(mesh => mesh.visible).length;
  return t(total === 1 ? 'inspector.componentSummary.one'
    : 'inspector.componentSummary.many', {count: total, visible});
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

function assetBindingForSummary(summary) {
  if (!summary || summary.status === 'unavailable') return null;
  const status = summary.status === 'mixed' || summary.status === 'ambiguous'
    ? 'ambiguous' : 'exact';
  return {
    status,
    component_status: status === 'exact' ? 'exact' : 'ambiguous',
    range_status: summary.status === 'partial' ? 'unknown' : 'exact',
    asset: summary.asset,
    component_name: summary.component,
    component_ordinal: summary.componentOrdinal,
  };
}

function buildAssetSection(content, value, isSummary = false) {
  const binding = isSummary ? assetBindingForSummary(value) : value;
  if (!binding) return null;
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-asset-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = t('inspector.asset');
  section.appendChild(title);
  const detail = assetDetailLabel(binding)
    || (isSummary ? assetSummaryLabel(value) : '');
  if (detail) addText(section, 'inspector-asset-detail', detail);
  addText(section, 'inspector-asset-status', assetMatchLabel(binding));
  content.appendChild(section);
  return section;
}

function buildMaterialControl(record) {
  const select = document.createElement('select');
  select.className = 'inspector-material-kind-control material-kind-select';
  select.setAttribute('aria-label', t('inspector.materialKind'));
  MATERIAL_KIND_OPTIONS.forEach(([value, key]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = t(key);
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
  title.textContent = t('inspector.material');
  section.appendChild(title);
  if (record.getMaterialKind || record.setMaterialKind) {
    section.appendChild(buildMaterialControl(record));
  } else {
    addText(section, 'inspector-muted', t('inspector.automatic'));
  }
  content.appendChild(section);
}

function buildManageTexturesButton(openTextureManager) {
  if (typeof openTextureManager !== 'function') return null;
  const manage = document.createElement('button');
  manage.type = 'button';
  manage.className = 'ui-button inspector-manage-textures';
  manage.textContent = t('inspector.manageTextures');
  manage.addEventListener('click', () => openTextureManager());
  return manage;
}

function buildComponentTextureSection(content, record) {
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-textures-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = t('inspector.textures');
  section.appendChild(title);
  const pool = record.texturePool || [];
  addText(section, 'inspector-texture-count', pool.length
    ? t('inspector.available', {count: pool.length}) : t('inspector.noneDiscovered'));
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
  title.textContent = t('inspector.texture');
  section.appendChild(title);
  const pool = component?.texturePool || [];
  const override = component?.getTextureOverride?.(mesh) || {
    value: undefined, automatic: true, resolved: null,
  };
  if (!pool.length) addText(section, 'inspector-muted', t('inspector.noneDiscovered'));
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
    override.automatic, 'automatic', override.resolved || t('inspector.automatic'));
  pool.forEach(option => addOption(
    textureOptionLabel(option), option.tex_key,
    !override.automatic && override.value === option.tex_key,
    'texture', option.file || option.label || option.tex_key));
  addOption(t('inspector.none'), null, !override.automatic && override.value === null, 'none');
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
    automatic.title = override.resolved || t('inspector.automatic');
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

const BRIGHTNESS_SLIDER_NEUTRAL = 100;
const BRIGHTNESS_SLIDER_MAX = 200;
const BRIGHTNESS_MAX = 4;

function brightnessSliderPosition(brightness) {
  const numeric = Number(brightness);
  const value = Math.min(BRIGHTNESS_MAX,
    Math.max(0, Number.isFinite(numeric) ? numeric : 1));
  if (value <= 1) return value * BRIGHTNESS_SLIDER_NEUTRAL;
  return BRIGHTNESS_SLIDER_NEUTRAL
    + BRIGHTNESS_SLIDER_NEUTRAL * Math.log(value) / Math.log(BRIGHTNESS_MAX);
}

function brightnessFromSliderPosition(position) {
  const numeric = Number(position);
  const value = Math.min(BRIGHTNESS_SLIDER_MAX,
    Math.max(0, Number.isFinite(numeric) ? numeric : 100));
  if (value <= BRIGHTNESS_SLIDER_NEUTRAL) {
    return value / BRIGHTNESS_SLIDER_NEUTRAL;
  }
  return BRIGHTNESS_MAX ** (
    (value - BRIGHTNESS_SLIDER_NEUTRAL) / BRIGHTNESS_SLIDER_NEUTRAL);
}

function colorControlValue(field, adjustment) {
  if (field === 'hue') return adjustment.hue;
  if (field === 'brightness') return brightnessSliderPosition(adjustment[field]);
  return adjustment[field] * 100;
}

function colorAdjustmentValue(field, controlValue) {
  if (field === 'hue') return controlValue;
  if (field === 'brightness') return brightnessFromSliderPosition(controlValue);
  return controlValue / 100;
}

function formatColorControlValue(field, controlValue) {
  if (field === 'hue') return formatHue(controlValue);
  const value = field === 'brightness'
    ? brightnessFromSliderPosition(controlValue) * 100
    : controlValue;
  return formatPercent(value);
}

function textureEligibilityMessage(eligibility) {
  const keys = {
    'asset-texture': 'texture.reason.assetTexture',
    'no-diffuse': 'texture.reason.noDiffuse',
    'compressed-mod': 'texture.reason.compressedMod',
    'different-mod': 'texture.reason.differentMod',
    'unsupported-texture-type': 'texture.reason.ddsRequired',
  };
  return keys[eligibility?.reason] ? t(keys[eligibility.reason]) : '';
}

/** Build one range control shared by the Inspector's color sliders. */
function buildRangeControl({
  field, label, min, max, step, value, formatValue, onInput, onChange,
  neutralMarker = false,
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
  const sliderWrap = document.createElement('span');
  sliderWrap.className = 'inspector-color-slider-wrap';
  sliderWrap.appendChild(slider);
  if (neutralMarker) {
    const marker = document.createElement('span');
    marker.className = 'inspector-color-slider-neutral-marker';
    marker.setAttribute('aria-hidden', 'true');
    sliderWrap.appendChild(marker);
  }
  row.append(heading, sliderWrap, valueNode);
  return row;
}

function syncTextureSaveAction(section, mesh) {
  const action = section?.querySelector('.inspector-texture-bake');
  if (!action) return;
  const eligibility = canSaveTexture(mesh);
  if (eligibility.reason === 'compressed-mod') {
    action.disabled = true;
    action.title = textureEligibilityMessage(eligibility);
    return;
  }
  const hasTargets = getTextureSaveTargets(mesh).length > 0;
  action.disabled = !hasTargets;
    action.title = hasTargets ? '' : t('inspector.adjustBeforeSave');
}

function updateColorAdjustment(section, mesh, field, controlValue,
                               persist = false) {
  const next = getMeshColorAdjustment(mesh);
  next[field] = colorAdjustmentValue(field, controlValue);
  setMeshColorAdjustment(mesh, next, { persist, render: true });
  syncTextureSaveAction(section, mesh);
}

function buildTextureSaveAction(section, mesh) {
  const eligibility = canSaveTexture(mesh);
  if (eligibility.editable) {
    const bake = document.createElement('button');
    bake.type = 'button';
    bake.className = 'ui-button inspector-texture-bake';
    bake.textContent = t('inspector.saveTexture');
    const hasTargets = getTextureSaveTargets(mesh).length > 0;
    bake.disabled = !hasTargets;
    if (!hasTargets) bake.title = t('inspector.adjustBeforeSave');
    bake.addEventListener('click', async () => {
      bake.disabled = true;
      try {
        await openTextureSaveModal(mesh, {
          isCurrent: () => current?.type === 'mesh'
            && semanticMesh(current.mesh) === mesh,
        });
      } finally {
        bake.disabled = getTextureSaveTargets(mesh).length === 0;
      }
    });
    section.appendChild(bake);
    syncTextureSaveAction(section, mesh);
  } else if (eligibility.reason === 'compressed-mod') {
    const bake = document.createElement('button');
    bake.type = 'button';
    bake.className = 'ui-button inspector-texture-bake';
    bake.textContent = t('inspector.saveTexture');
    bake.disabled = true;
    bake.title = textureEligibilityMessage(eligibility);
    section.appendChild(bake);
  } else if (eligibility.reason === 'unsupported-texture-type') {
    addText(section, 'inspector-texture-bake-hint',
      textureEligibilityMessage(eligibility));
  }
}

function buildColorSection(content, mesh) {
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-color-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = t('inspector.color');
  section.appendChild(title);

  const eligibility = canEditMeshColor(mesh);
  section.dataset.colorEditable = String(eligibility.editable);
  section.dataset.colorReason = eligibility.reason || '';
  if (!eligibility.editable) {
    if (eligibility.reason === 'asset-texture') {
      addText(section, 'inspector-color-readonly-title', t('inspector.assetTexture'));
      addText(section, 'inspector-color-readonly',
        t('inspector.colorUnavailableAsset'));
    } else {
      addText(section, 'inspector-color-readonly-title', t('inspector.noDiffuse'));
      addText(section, 'inspector-color-readonly',
        t('inspector.selectDiffuse'));
    }
    content.appendChild(section);
    return section;
  }

  const adjustment = getMeshColorAdjustment(mesh);
  const addSlider = (field, label, min, max, step, neutralMarker = false) => {
    section.appendChild(buildRangeControl({
      field, label, min, max, step,
      value: colorControlValue(field, adjustment),
      formatValue: value => formatColorControlValue(field, value),
      onInput: value => updateColorAdjustment(section, mesh, field, value),
      onChange: value => updateColorAdjustment(
        section, mesh, field, value, true),
      neutralMarker,
    }));
  };
  addSlider('hue', t('inspector.hue'), -180, 180, 1);
  addSlider('saturation', t('inspector.saturation'), 0, 200, 1);
  addSlider('brightness', t('inspector.brightness'), 0, 200, 1, true);
  addSlider('contrast', t('inspector.contrast'), 0, 200, 1);

  const rgbTitle = document.createElement('div');
  rgbTitle.className = 'inspector-color-subtitle';
  rgbTitle.textContent = t('inspector.rgb');
  section.appendChild(rgbTitle);
  addSlider('red', 'R', 0, 200, 1);
  addSlider('green', 'G', 0, 200, 1);
  addSlider('blue', 'B', 0, 200, 1);

  const tint = document.createElement('div');
  tint.className = 'inspector-color-tint';
  const tintLabel = document.createElement('span');
  tintLabel.className = 'inspector-color-control-heading';
  tintLabel.textContent = t('inspector.tint');
  const tintInput = document.createElement('input');
  tintInput.type = 'color';
  tintInput.setAttribute('aria-label', t('inspector.tintColor'));
  tintInput.className = 'inspector-color-tint-input';
  tintInput.value = adjustment.tint || '#ffffff';
  const tintValue = document.createElement('span');
  tintValue.className = 'inspector-color-value';
  tintValue.dataset.colorTintValue = 'true';
  tintValue.textContent = adjustment.tint?.toUpperCase() || t('inspector.none');
  const clearTint = document.createElement('button');
  clearTint.type = 'button';
  clearTint.className = 'ui-button inspector-color-tint-clear';
  clearTint.textContent = t('common.clear');
  clearTint.disabled = adjustment.tint === null;
  clearTint.setAttribute('aria-label', t('inspector.clearTint'));
  const applyTint = persist => {
    tintValue.textContent = tintInput.value.toUpperCase();
    clearTint.disabled = false;
    const next = getMeshColorAdjustment(mesh);
    next.tint = tintInput.value.toLowerCase();
    setMeshColorAdjustment(mesh, next, { persist, render: true });
    syncTextureSaveAction(section, mesh);
  };
  tintInput.addEventListener('input', () => applyTint(false));
  tintInput.addEventListener('change', () => applyTint(true));
  clearTint.addEventListener('click', () => {
    const next = getMeshColorAdjustment(mesh);
    next.tint = null;
    setMeshColorAdjustment(mesh, next, { persist: true, render: true });
    updateColorControlState(content, mesh);
  });
  tint.append(tintLabel, tintInput, tintValue, clearTint);
  section.appendChild(tint);

  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'ui-button inspector-color-reset';
  reset.textContent = t('inspector.resetColor');
  reset.addEventListener('click', () => {
    resetMeshColorAdjustment(mesh, { persist: true, render: true });
    updateColorControlState(content, mesh);
  });
  section.appendChild(reset);
  buildTextureSaveAction(section, mesh);
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
    value.textContent = formatColorControlValue(field, controlValue);
  });
  const tintInput = section.querySelector('.inspector-color-tint-input');
  const tintValue = section.querySelector('[data-color-tint-value]');
  const clearTint = section.querySelector('.inspector-color-tint-clear');
  if (tintInput) tintInput.value = adjustment.tint || '#ffffff';
  if (tintValue) tintValue.textContent = adjustment.tint?.toUpperCase()
    || t('inspector.none');
  if (clearTint) clearTint.disabled = adjustment.tint === null;
  syncTextureSaveAction(section, mesh);
  return true;
}

function buildComponent(record) {
  const content = showContent();
  content.replaceChildren();
  buildHeader(content, record.component || t('inspector.component'),
    componentContext(record), record.source || '');
  buildAssetSection(content, record.assetSummary, true);
  buildMaterialSection(content, record);
  buildComponentTextureSection(content, record);
}

function buildMesh(mesh, record) {
  const content = showContent();
  content.replaceChildren();
  const source = semanticMesh(mesh);
  const name = meshDisplayLabel(mesh, record.label);
  const component = record.component;
  const componentName = component?.component || component || t('inspector.component');
  buildHeader(content, name, componentName,
    record.entry?.source?.[0]?.ini || '');
  buildAssetSection(content, source.userData.assetEntry?.asset_binding);
  buildMaterialSection(content, component || {});
  buildTextureControls(content, record, source);
  buildColorSection(content, source);
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
    const source = semanticMesh(current.mesh);
    updateTextureControlState(content, source, current.record.component);
    if (!updateColorControlState(content, source)) {
      buildMesh(current.mesh, current.record);
    }
  } else {
    const context = content.querySelector('[data-inspector-context="true"]');
    if (context) context.textContent = componentContext(current.record);
    const count = content.querySelector('.inspector-texture-count');
    if (count) {
      const total = current.record.texturePool?.length || 0;
      count.textContent = total ? t('inspector.available', {count: total})
        : t('inspector.noneDiscovered');
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
  if (status) status.textContent = record.component || t('inspector.component');
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
    const componentName = record.component?.component || record.component
      || t('inspector.component');
    const meshName = meshDisplayLabel(mesh, record.label);
    status.textContent = `${componentName} > ${meshName}`;
  }
}

export function initInspectorPanel() {
  window.addEventListener(LANGUAGE_CHANGED, () => {
    if (current?.type === 'component') {
      buildComponent(current.record);
      const status = $('selected-mesh-status');
      if (status) status.textContent = current.record.component
        || t('inspector.component');
    } else if (current?.type === 'mesh') {
      buildMesh(current.mesh, current.record);
      const status = $('selected-mesh-status');
      if (status) {
        const componentName = current.record.component?.component
          || current.record.component || t('inspector.component');
        const meshName = meshDisplayLabel(current.mesh, current.record.label);
        status.textContent = `${componentName} > ${meshName}`;
      }
    }
  });
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
      ? changed.includes(semanticMesh(current.mesh))
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
