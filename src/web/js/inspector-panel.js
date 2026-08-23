// Selection-aware details panel.  Mesh creation remains owned by mesh-panel;
// this module only presents the already-authoritative mesh/component state.

import { isRightDockOpen, setRightDockTab } from './right-dock.js';
import { clearSelection } from './selection.js';
import {
  assetMatchLabel, componentMatchLabel, normalizeAssetBinding,
  rangeMatchLabel, summarizeAssetBindings, textureProvenance,
  textureRoleLabels,
} from './asset-diagnostics.js';

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

function addRow(parent, label, value) {
  const row = document.createElement('div');
  row.className = 'inspector-row';
  addText(row, 'inspector-label', label);
  addText(row, 'inspector-value', value == null || value === '' ? '—' : String(value));
  parent.appendChild(row);
  return row;
}

function buildHeader(content, title, subtitle) {
  const header = document.createElement('div');
  header.className = 'inspector-header';
  addText(header, 'inspector-kicker', subtitle);
  const heading = document.createElement('h3');
  heading.textContent = title;
  header.appendChild(heading);
  content.appendChild(header);
}

function buildAssetSection(content, entry) {
  const binding = normalizeAssetBinding(entry?.asset_binding);
  if (!binding) return;
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-asset-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Asset match';
  section.appendChild(title);
  addRow(section, 'Asset', binding.asset);
  addRow(section, 'Type', binding.assetType);
  addRow(section, 'Component', binding.component);
  addRow(section, 'Object', binding.classification);
  addRow(section, 'Geometry hash', binding.geometryHash);
  if (binding.firstIndex !== null || binding.indexCount !== null) {
    const range = [binding.firstIndex, binding.indexCount]
      .map(value => value == null ? '?' : value).join(' / ');
    addRow(section, 'Range', range);
  }
  addRow(section, 'Component match', componentMatchLabel(binding));
  addRow(section, 'Range match', rangeMatchLabel(binding));
  addRow(section, 'Match', assetMatchLabel(binding));
  content.appendChild(section);
}

function buildComponentAssetSection(content, record) {
  const summary = record.assetSummary
    || summarizeAssetBindings((record.meshes || [])
      .map(mesh => mesh.userData.assetEntry), record.assetResolution);
  if (!summary || summary.status === 'unavailable') return;
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-asset-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Asset resolution';
  section.appendChild(title);
  addRow(section, 'Match', summary.status.replace(/^./, value => value.toUpperCase()));
  addRow(section, 'Asset', summary.asset);
  addRow(section, 'Component', summary.component);
  addRow(section, 'Draws', `${summary.matchedDraws} of ${summary.totalDraws} matched`);
  if (summary.exact) addRow(section, 'Exact', summary.exact);
  if (summary.partial) addRow(section, 'Partial', summary.partial);
  if (summary.ambiguous) addRow(section, 'Ambiguous', summary.ambiguous);
  if (summary.unmatched) addRow(section, 'Not found', summary.unmatched);
  if (summary.indexUnavailable) {
    addRow(section, 'Index unavailable', summary.indexUnavailable);
  }
  if (summary.rangesVary) addRow(section, 'Ranges', 'Vary by draw');
  content.appendChild(section);
}

function buildTextureProvenanceSection(content, record, mesh) {
  const entry = record.entry || {};
  if (!entry.asset_binding && !entry.texture_resolution
      && !(entry.asset_slot_evidence || []).length) return;
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-provenance-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Texture provenance';
  section.appendChild(title);
  const sources = textureProvenance(entry);
  textureRoleLabels().forEach(([role, label]) => {
    const row = addRow(section, `${label} (automatic)`, sources[role]);
    row.dataset.provenanceRole = role;
    row.dataset.provenanceKind = 'automatic';
  });
  const override = record.component?.getTextureOverride?.(mesh);
  const current = override?.automatic
    ? (override.resolved ? `Automatic (${override.resolved})` : 'Automatic')
    : override?.value === null ? 'Viewer override (None)'
      : `Viewer override (${override?.value || 'custom'})`;
  const currentRow = addRow(section, 'Diffuse (viewer)', current);
  currentRow.dataset.provenanceKind = 'viewer';
  content.appendChild(section);
}

function buildSlotEvidenceSection(content, entry) {
  const evidence = entry?.asset_slot_evidence;
  if (!Array.isArray(evidence) || !evidence.length) return;
  const section = document.createElement('section');
  section.className = 'inspector-section inspector-slot-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Slot evidence';
  section.appendChild(title);
  evidence.forEach(item => {
    const card = document.createElement('div');
    card.className = 'inspector-slot-evidence';
    addText(card, 'inspector-slot-name', item.resource || 'Texture slot');
    addRow(card, 'Texture', item.texture_hash);
    addRow(card, 'VS', item.vs_hash);
    addRow(card, 'PS', item.ps_hash);
    addRow(card, 'Role', item.role || 'Unknown');
    section.appendChild(card);
  });
  content.appendChild(section);
}

function buildTextureControls(content, record, mesh) {
  const component = record.component;
  const section = document.createElement('section');
  section.className = 'inspector-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Texture override';
  section.appendChild(title);

  const pool = component?.texturePool || [];
  const override = component?.getTextureOverride?.(mesh) || {
    value: undefined, automatic: true, resolved: null,
  };
  const list = document.createElement('div');
  list.className = 'inspector-texture-list';
  const addOption = (label, value, selected, choice) => {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'inspector-texture-option';
    option.textContent = label;
    option.dataset.textureChoice = choice;
    option.dataset.textureValue = value == null ? '' : value;
    option.classList.toggle('selected', selected);
    option.addEventListener('click', () => {
      component?.setTextureOverride?.(mesh, value);
    });
    list.appendChild(option);
  };
  addOption(
    override.resolved ? `Automatic (${override.resolved})` : 'Automatic',
    undefined, override.automatic, 'automatic');
  pool.forEach(option => addOption(
    option.label || option.file || option.tex_key,
    option.tex_key, !override.automatic && override.value === option.tex_key,
    'texture'));
  addOption('None', null, !override.automatic && override.value === null, 'none');
  if (!pool.length) {
    addText(section, 'inspector-muted', 'No texture variants registered.');
  }
  section.appendChild(list);
  if (mesh?.userData?.resolvedTexKey) {
    addRow(section, 'Resolved', mesh.userData.resolvedTexKey);
  }
  content.appendChild(section);
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

function buildComponent(record) {
  const content = showContent();
  content.replaceChildren();
  const meshes = record.meshes || [];
  buildHeader(content, record.component || 'Component', record.source || 'Component');

  const summary = document.createElement('section');
  summary.className = 'inspector-section inspector-summary';
  addRow(summary, 'Draw calls', String(meshes.length));
  addRow(summary, 'Visible', `${meshes.filter(mesh => mesh.visible).length} of ${meshes.length}`);
  content.appendChild(summary);
  buildComponentAssetSection(content, record);

  const material = document.createElement('section');
  material.className = 'inspector-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Material';
  material.appendChild(title);
  if (record.setMaterialKind) material.appendChild(buildMaterialControl(record));
  if (record.openTextureManager) {
    const manage = document.createElement('button');
    manage.type = 'button';
    manage.className = 'ui-button inspector-manage-textures';
    manage.textContent = 'Manage textures';
    manage.addEventListener('click', () => record.openTextureManager());
    material.appendChild(manage);
  }
  content.appendChild(material);

  const poolSection = document.createElement('section');
  poolSection.className = 'inspector-section';
  const poolTitle = document.createElement('div');
  poolTitle.className = 'inspector-section-title';
  poolTitle.textContent = 'Texture pool';
  poolSection.appendChild(poolTitle);
  const pool = record.texturePool || [];
  if (!pool.length) addText(poolSection, 'inspector-muted', 'No texture variants registered.');
  else pool.forEach(option => addRow(poolSection, option.label, option.file || option.tex_key));
  content.appendChild(poolSection);

  if (meshes.length) {
    const meshList = document.createElement('section');
    meshList.className = 'inspector-section';
    const title = document.createElement('div');
    title.className = 'inspector-section-title';
    title.textContent = 'Meshes in component';
    meshList.appendChild(title);
    meshes.forEach(mesh => addRow(meshList, mesh.userData.displayName || 'Mesh',
      mesh.visible ? 'Visible' : 'Hidden'));
    content.appendChild(meshList);
  }
}

function buildMesh(mesh, record) {
  const content = showContent();
  content.replaceChildren();
  const name = mesh.userData.displayName || record.label || 'Mesh';
  const componentName = record.component?.component || record.component || 'Mesh';
  buildHeader(content, name, componentName);
  const summary = document.createElement('section');
  summary.className = 'inspector-section inspector-summary';
  addRow(summary, 'State', mesh.visible ? 'Visible' : 'Hidden');
  addRow(summary, 'Material kind', mesh.userData.materialKindOverride || mesh.userData.materialKind || 'Auto');
  addRow(summary, 'Diffuse', mesh.userData.resolvedTexKey || mesh.userData.texKey || 'None');
  content.appendChild(summary);
  buildAssetSection(content, record.entry);
  buildTextureProvenanceSection(content, record, mesh);
  buildSlotEvidenceSection(content, record.entry);

  const material = document.createElement('section');
  material.className = 'inspector-section';
  const materialTitle = document.createElement('div');
  materialTitle.className = 'inspector-section-title';
  materialTitle.textContent = 'Material';
  material.appendChild(materialTitle);
  const component = record.component;
  if (component?.setMaterialKind) material.appendChild(buildMaterialControl(component));
  if (component?.openTextureManager) {
    const manage = document.createElement('button');
    manage.type = 'button';
    manage.className = 'ui-button inspector-manage-textures';
    manage.textContent = 'Manage textures';
    manage.addEventListener('click', () => component.openTextureManager());
    material.appendChild(manage);
  }
  content.appendChild(material);

  const draw = record.entry?.drawindexed;
  if (draw?.length) {
    const drawInfo = document.createElement('section');
    drawInfo.className = 'inspector-section';
    const title = document.createElement('div');
    title.className = 'inspector-section-title';
    title.textContent = 'Draw';
    drawInfo.appendChild(title);
    addRow(drawInfo, 'Count', String(draw[0] ?? '—'));
    addRow(drawInfo, 'Start', String(draw[1] ?? '—'));
    addRow(drawInfo, 'Base', String(draw[2] ?? '—'));
    content.appendChild(drawInfo);
  }
  buildTextureControls(content, record, mesh);
}

function updateInspectorState() {
  if (!current) return;
  const content = $('inspector-content');
  if (!content) return;
  const values = new Map();
  content.querySelectorAll('.inspector-row').forEach(row => {
    const label = row.querySelector('.inspector-label')?.textContent;
    const value = row.querySelector('.inspector-value');
    if (label && value) values.set(label, value);
  });
  if (current.type === 'mesh') {
    const mesh = current.mesh;
    if (values.has('State')) values.get('State').textContent = mesh.visible ? 'Visible' : 'Hidden';
    if (values.has('Material kind')) {
      values.get('Material kind').textContent =
        mesh.userData.materialKindOverride || mesh.userData.materialKind || 'Auto';
    }
    if (values.has('Diffuse')) {
      values.get('Diffuse').textContent =
        mesh.userData.resolvedTexKey || mesh.userData.texKey || 'None';
    }
    const resolved = [...content.querySelectorAll('.inspector-row')].find(row =>
      row.querySelector('.inspector-label')?.textContent === 'Resolved');
    if (resolved) {
      resolved.querySelector('.inspector-value').textContent =
        mesh.userData.resolvedTexKey || 'None';
    }
    const material = content.querySelector('.inspector-material-kind-control');
    if (material) material.value = current.record.component.getMaterialKind?.() || 'auto';
    const override = current.record.component.getTextureOverride?.(mesh);
    if (override) {
      content.querySelectorAll('.inspector-texture-option').forEach(option => {
        const selected = option.dataset.textureChoice === 'automatic'
          ? override.automatic
          : option.dataset.textureChoice === 'none'
            ? !override.automatic && override.value === null
            : !override.automatic && override.value === option.dataset.textureValue;
        option.classList.toggle('selected', selected);
      });
      const currentProvenance = content.querySelector(
        '[data-provenance-kind="viewer"] .inspector-value');
      if (currentProvenance) {
        currentProvenance.textContent = override.automatic
          ? (override.resolved ? `Automatic (${override.resolved})` : 'Automatic')
          : override.value === null ? 'Viewer override (None)'
            : `Viewer override (${override.value || 'custom'})`;
      }
    }
  } else if (current.type === 'component') {
    const meshes = current.record.meshes || [];
    if (values.has('Visible')) {
      values.get('Visible').textContent =
        `${meshes.filter(mesh => mesh.visible).length} of ${meshes.length}`;
    }
    const material = content.querySelector('.inspector-material-kind-control');
    if (material) material.value = current.record.getMaterialKind?.() || 'auto';
    content.querySelectorAll('.inspector-row').forEach(row => {
      const label = row.querySelector('.inspector-label')?.textContent;
      if (!label || label === 'Visible' || label === 'Draw calls') return;
      const mesh = meshes.find(item =>
        (item.userData.displayName || 'Mesh') === label);
      if (mesh) row.querySelector('.inspector-value').textContent =
        mesh.visible ? 'Visible' : 'Hidden';
    });
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
