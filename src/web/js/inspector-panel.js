// Selection-aware details panel.  Mesh creation remains owned by mesh-panel;
// this module only presents the already-authoritative mesh/component state.

import { setRightDockTab } from './right-dock.js';

const meshRecords = new WeakMap();
let current = null;
let selectionCount = 0;

const $ = id => document.getElementById(id);

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
  addText(row, 'inspector-value', value || '—');
  parent.appendChild(row);
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

function buildTextureControls(content, record, mesh) {
  const sourceList = record.textureList;
  const section = document.createElement('section');
  section.className = 'inspector-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Texture override';
  section.appendChild(title);

  const rows = sourceList?.querySelectorAll('.tex-item') || [];
  if (!rows.length) {
    addText(section, 'inspector-muted', 'No texture variants registered.');
  } else {
    const list = document.createElement('div');
    list.className = 'inspector-texture-list';
    rows.forEach(row => {
      const clone = document.createElement('button');
      clone.type = 'button';
      clone.className = 'inspector-texture-option';
      clone.textContent = row.textContent;
      clone.classList.toggle('selected', row.classList.contains('selected'));
      clone.addEventListener('click', () => row.click());
      list.appendChild(clone);
    });
    section.appendChild(list);
  }
  if (mesh?.userData?.resolvedTexKey) {
    addRow(section, 'Resolved', mesh.userData.resolvedTexKey);
  }
  content.appendChild(section);
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

  const material = document.createElement('section');
  material.className = 'inspector-section';
  const title = document.createElement('div');
  title.className = 'inspector-section-title';
  title.textContent = 'Material';
  material.appendChild(title);
  if (record.materialSelect) {
    const select = record.materialSelect.cloneNode(true);
    select.className = 'inspector-material-kind-control material-kind-select';
    select.removeAttribute('id');
    select.addEventListener('change', () => {
      record.materialSelect.value = select.value;
      record.materialSelect.dispatchEvent(new Event('change', { bubbles: true }));
    });
    material.appendChild(select);
  }
  if (record.texBtn) {
    const manage = document.createElement('button');
    manage.type = 'button';
    manage.className = 'ui-button inspector-manage-textures';
    manage.textContent = 'Manage textures';
    manage.addEventListener('click', () => record.texBtn.click());
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

  const material = document.createElement('section');
  material.className = 'inspector-section';
  const materialTitle = document.createElement('div');
  materialTitle.className = 'inspector-section-title';
  materialTitle.textContent = 'Material';
  material.appendChild(materialTitle);
  const component = record.component;
  if (component?.materialSelect) {
    const select = component.materialSelect.cloneNode(true);
    select.className = 'inspector-material-kind-control material-kind-select';
    select.removeAttribute('id');
    select.addEventListener('change', () => {
      component.materialSelect.value = select.value;
      component.materialSelect.dispatchEvent(new Event('change', { bubbles: true }));
    });
    material.appendChild(select);
  }
  if (component?.texBtn) {
    const manage = document.createElement('button');
    manage.type = 'button';
    manage.className = 'ui-button inspector-manage-textures';
    manage.textContent = 'Manage textures';
    manage.addEventListener('click', () => component.texBtn.click());
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
  } else if (current.type === 'component') {
    const meshes = current.record.meshes || [];
    if (values.has('Visible')) {
      values.get('Visible').textContent =
        `${meshes.filter(mesh => mesh.visible).length} of ${meshes.length}`;
    }
  }
}

function selectComponent(record) {
  if (selectionCount++ === 0) setRightDockTab('inspector', { persist: false });
  current = { type: 'component', record };
  buildComponent(record);
  const status = $('selected-mesh-status');
  if (status) status.textContent = record.component || 'Component';
}

function selectMesh(mesh) {
  const record = meshRecords.get(mesh);
  if (!record) return;
  if (selectionCount++ === 0) setRightDockTab('inspector', { persist: false });
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
  clearContent();
}

export function clearInspector() {
  current = null;
  selectionCount = 0;
  $('selected-mesh-status').textContent = '';
  clearContent();
}

export function getInspectorSelection() {
  return current;
}
