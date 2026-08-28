// Selection-aware details panel. Mesh creation remains owned by mesh-panel;
// this module only presents the already-authoritative mesh/component state.

import { isRightDockOpen, setRightDockTab } from './right-dock.js';
import { clearSelection } from '../scene/selection.js';
import { translateModel } from '../scene/scene.js';
import { activeMeshes } from '../mesh/mesh-state.js';
import {
  getContinuousMotionState, setContinuousMotionAxis,
  startContinuousMotion, stopContinuousMotion,
} from '../mesh/weight-motion-test.js';
import {
  GRAVITY_WORLD_DIRECTION,
  SIGNIFICANT_RESIDUAL_RATIO, SIGNIFICANT_VERTEX_WEIGHT,
  INFLUENCE_GRAPH_TOP_K,
  getSkinningState, loadSkinningWeights, resetSkinningExperiment,
  setSelectedBone, setSkinningAngle, setSkinningAxis, setSkinningChainAngle,
  setSkinningChainAxis, setSkinningChainText, setSkinningHeatmap,
  setSkinningHeatmapMode,
  buildCandidateTree, setCandidateTreeRoot,
  setInfluenceVisualizationMode,
  setForestAxis, setForestAngle,
  setPhysicsAxis, setPhysicsTargetAngle, setPhysicsFrequency,
  setPhysicsDamping, setPhysicsMotionStrength,
  setPhysicsLinearMotionStrength, setPhysicsContinuousLinearResponse,
  getPhysicsConstraintDiagnostics,
  setPhysicsConstraintsEnabled, setPhysicsMaxBendDegrees,
  setPhysicsGravityEnabled, setPhysicsGravityScale,
  setPhysicsEnabled,
  applyPhysicsKick,
  resetPhysicsMotion,
  setVirtualChainVisible,
} from '../mesh/weight-experiment.js';

const meshRecords = new WeakMap();
const skinningUpdates = new WeakMap();
const physicsUpdates = new WeakMap();
let current = null;
let selectionCount = 0;
const MISSING_INFLUENCE_DISPLAY_LIMIT = 8;
const INFLUENCE_NEIGHBOR_DISPLAY_LIMIT = 8;

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
  return `${(Number(value || 0) * 100).toFixed(3)}%`;
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

function graphMetric(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(3) : 'n/a';
}

function relationshipNeighbor(relationship, boneId) {
  return Number(relationship.boneA) === Number(boneId)
    ? Number(relationship.boneB) : Number(relationship.boneA);
}

function compareNeighborRelationships(a, b) {
  return (Number(b.containment) || 0) - (Number(a.containment) || 0)
    || (Number(b.jaccard) || 0) - (Number(a.jaccard) || 0)
    || (Number(a.normalizedDistance ?? Infinity)
      - Number(b.normalizedDistance ?? Infinity))
    || relationshipNeighbor(a, a._boneId) - relationshipNeighbor(b, b._boneId);
}

function candidateTreeText(tree) {
  if (!tree) return '';
  const lines = [`Root ${tree.rootId}`];
  const append = (orientation, boneId, prefix, last, root = false) => {
    if (!root) {
      lines.push(`${prefix}${last ? '\u2514\u2500 ' : '\u251c\u2500 '}${boneId}`);
    }
    const children = orientation?.childrenById?.[boneId] || [];
    children.forEach((child, index) => append(
      orientation,
      child,
      root ? '' : `${prefix}${last ? '   ' : '\u2502  '}`,
      index === children.length - 1));
  };
  const forest = tree.forest;
  const primary = forest?.components?.[forest.primaryComponentId];
  const primaryOrientation = primary || tree.orientation;
  if (Number.isFinite(Number(tree.rootId))) {
    append(primaryOrientation, Number(tree.rootId), '', true, true);
  }
  if (forest?.components?.length > 1) {
    lines.push(`Candidate forest has ${forest.components.length} components`);
    forest.components.forEach(component => {
      if (component.primary) return;
      const rootLabel = component.rootId == null
        ? 'no root' : `Root ${component.rootId}`;
      lines.push(`Component ${component.componentId} \u00b7 ${rootLabel} (auto)`);
      if (component.rootId != null) {
        append(component, Number(component.rootId), '', true, true);
      }
    });
  } else if ((tree.components || []).length > 1) {
    lines.push(`Candidate graph has ${tree.components.length} components`);
    const rooted = new Set(Object.keys(tree.orientation?.depthById || {})
      .filter(id => tree.orientation.depthById[id] !== null).map(Number));
    tree.components.forEach(component => {
      if (!component.some(id => rooted.has(Number(id)))) {
        lines.push(`[component: ${component.join(', ')}]`);
      }
    });
  }
  return lines.join('\n');
}

function compareGraphRelationships(a, b) {
  return (Number(b.containment) || 0) - (Number(a.containment) || 0)
    || (Number(b.jaccard) || 0) - (Number(a.jaccard) || 0)
    || (Number(a.normalizedDistance ?? Infinity)
      - Number(b.normalizedDistance ?? Infinity))
    || Number(a.boneA) - Number(b.boneA)
    || Number(a.boneB) - Number(b.boneB);
}

function topGraphRelationships(graph) {
  const selected = new Map();
  (graph?.nodes || []).forEach(node => {
    const candidates = (graph.relationships || [])
      .filter(item => item.boneA === node.boneId || item.boneB === node.boneId)
      .map(item => ({...item, _boneId: node.boneId}))
      .sort(compareNeighborRelationships)
      .slice(0, INFLUENCE_GRAPH_TOP_K);
    candidates.forEach(item => {
      const key = `${Math.min(item.boneA, item.boneB)}:${Math.max(
        item.boneA, item.boneB)}`;
      const {_boneId, ...relationship} = item;
      selected.set(key, relationship);
    });
  });
  return [...selected.values()].sort(compareGraphRelationships);
}

function forestDiagnosticsPayload(state) {
  const forest = state?.candidateForest;
  if (!forest) return null;
  const physics = state.physicsEnabled || state.physicsState
    ? {
      enabled: !!state.physicsEnabled,
      axis: state.physicsAxis,
      targetAngle: state.physicsTargetAngle,
      frequencyHz: state.physicsFrequencyHz,
      dampingRatio: state.physicsDampingRatio,
      motionResponse: state.physicsMotionStrength,
      angularResponse: state.physicsMotionStrength,
      linearResponse: state.physicsLinearMotionStrength,
      continuousLinearResponse: state.physicsContinuousLinearResponse,
      lastRootAngularDelta: state.lastRootAngularDelta,
      lastProjectedAngularDelta: state.lastProjectedAngularDelta,
      motionEventCount: state.motionEventCount,
      lastRootTranslationDeltaWorld: state.lastRootTranslationDeltaWorld,
      lastRootTranslationDeltaLocal: state.lastRootTranslationDeltaLocal,
      lastTranslationLag: state.lastTranslationLag,
      translationEventCount: state.translationEventCount,
      lastRootLinearVelocityWorld: state.lastRootLinearVelocityWorld,
      lastRootLinearVelocityLocal: state.lastRootLinearVelocityLocal,
      lastRootLinearVelocityDelta: state.lastRootLinearVelocityDelta,
      continuousMotionEventCount: state.continuousMotionEventCount,
      gravity: {
        enabled: !!state.physicsGravityEnabled,
        scale: state.physicsGravityScale,
        worldDirection: [...GRAVITY_WORLD_DIRECTION],
        localDirection: [...(state.physicsGravityLocal
          || GRAVITY_WORLD_DIRECTION)],
        referenceRadius: state.physicsGravityDiagnostics?.referenceRadius ?? 0,
        minLeverRatio: state.physicsGravityDiagnostics?.minLeverRatio ?? 0.15,
        activeComponentCount:
          state.physicsGravityDiagnostics?.activeComponentCount || 0,
        clampedComponentCount:
          state.physicsGravityDiagnostics?.clampedComponentCount || 0,
        maxAbsTotalAcceleration:
          state.physicsGravityDiagnostics?.maxAbsTotalAcceleration || 0,
        maxAbsLocalAcceleration:
          state.physicsGravityDiagnostics?.maxAbsLocalAcceleration || 0,
        components: state.physicsGravityDiagnostics?.components || [],
      },
      constraints: getPhysicsConstraintDiagnostics(state),
      settled: !!state.physicsSettled,
      joints: Object.fromEntries(
        [...(state.physicsState?.joints || new Map()).entries()]
          .map(([boneId, joint]) => [boneId, {
            angle: Number(joint.angle) || 0,
            angularVelocity: Number(joint.angularVelocity) || 0,
          }])),
    }
    : null;
  return {
    primaryRootId: forest.primaryRootId,
    primaryComponentId: forest.primaryComponentId,
    componentByBoneId: forest.componentByBoneId,
    components: forest.components,
    axis: state.forestAxis,
    angle: state.forestAngle,
    deformationMode: state.deformationMode,
    physics,
  };
}

function graphDiagnosticsPayload(state, includeForest = false) {
  const graph = state?.influenceGraph;
  if (!graph) return null;
  const tree = state.candidateTree;
  const payload = {
    rootId: state.candidateRootId ?? null,
    boundingSphereRadius: graph.boundingSphereRadius ?? null,
    nodes: (graph.nodes || []).map(node => ({
      boneId: node.boneId,
      totalWeight: node.totalWeight,
      affectedVertexCount: node.affectedVertexCount,
      maxVertexWeight: node.maxVertexWeight,
      weightedCenter: node.weightedCenter,
      weightedRadius: node.weightedRadius,
    })),
    topRelationships: topGraphRelationships(graph),
    candidateTree: tree ? {
      rootId: tree.rootId,
      components: tree.components,
      edges: tree.edges,
      orientation: tree.orientation,
    } : null,
  };
  if (includeForest) payload.candidateForest = forestDiagnosticsPayload(state);
  return payload;
}

async function copyGraphDiagnostics(state, includeForest = false) {
  const payload = graphDiagnosticsPayload(state, includeForest);
  if (!payload) throw new Error('Influence graph is not available.');
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

function buildSkinningInfluenceGraphControls(
    parent, mesh, state, onStateChange = null) {
  const section = document.createElement('div');
  section.className = 'inspector-skinning-influence-graph';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-subtitle';
  title.textContent = 'Influence Graph';
  section.appendChild(title);
  addText(section, 'inspector-skinning-hint',
    'Diagnostic relationships only; no hierarchy is inferred.');

  const influenceLabel = document.createElement('label');
  influenceLabel.className = 'inspector-skinning-field';
  addText(influenceLabel, 'inspector-label', 'Influence ID');
  const influenceSelect = document.createElement('select');
  influenceSelect.className = 'inspector-skinning-influence-select';
  influenceSelect.setAttribute('aria-label', 'Influence graph bone ID');
  state.boneIds.forEach(id => {
    const option = document.createElement('option');
    option.value = id;
    option.textContent = id;
    influenceSelect.appendChild(option);
  });
  influenceLabel.appendChild(influenceSelect);
  section.appendChild(influenceLabel);

  const stats = document.createElement('div');
  stats.className = 'inspector-skinning-graph-stats';
  section.appendChild(stats);
  const neighborsTitle = document.createElement('div');
  neighborsTitle.className = 'inspector-skinning-missing-title';
  neighborsTitle.textContent = 'Candidate neighbors';
  section.appendChild(neighborsTitle);
  const neighbors = document.createElement('div');
  neighbors.className = 'inspector-skinning-neighbors';
  section.appendChild(neighbors);
  const relationship = document.createElement('div');
  relationship.className = 'inspector-skinning-relationship';
  section.appendChild(relationship);

  const graphButton = document.createElement('button');
  graphButton.type = 'button';
  graphButton.className = 'ui-button inspector-skinning-influence-graph-show';
  graphButton.addEventListener('click', () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    setInfluenceVisualizationMode(
      mesh, latest.influenceVisualizationMode === 'graph' ? null : 'graph');
    update(latest);
  });
  section.appendChild(graphButton);
  let copying = false;
  const copyButton = document.createElement('button');
  copyButton.type = 'button';
  copyButton.className = 'ui-button inspector-skinning-copy-graph';
  copyButton.textContent = 'Copy Graph Diagnostics';
  copyButton.addEventListener('click', async () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    copying = true;
    copyButton.disabled = true;
    copyStatus.textContent = '';
    try {
      await copyGraphDiagnostics(latest);
      copyStatus.textContent = 'Graph diagnostics copied.';
    } catch (error) {
      copyStatus.textContent = error instanceof Error
        ? error.message : String(error);
    } finally {
      copying = false;
      copyButton.disabled = !getSkinningState(mesh)?.influenceGraph;
    }
  });
  section.appendChild(copyButton);
  const copyStatus = addText(
    section, 'inspector-skinning-copy-status', '');

  const treeSection = document.createElement('div');
  treeSection.className = 'inspector-skinning-candidate-tree';
  const treeTitle = document.createElement('div');
  treeTitle.className = 'inspector-skinning-subtitle';
  treeTitle.textContent = 'Candidate Tree';
  treeSection.appendChild(treeTitle);
  addText(treeSection, 'inspector-skinning-hint',
    'Manual root changes direction only; edge selection uses relationship evidence.');
  const rootLabel = document.createElement('label');
  rootLabel.className = 'inspector-skinning-field';
  addText(rootLabel, 'inspector-label', 'Manual Root');
  const rootSelect = document.createElement('select');
  rootSelect.className = 'inspector-skinning-tree-root';
  rootSelect.setAttribute('aria-label', 'Candidate tree root ID');
  state.boneIds.forEach(id => {
    const option = document.createElement('option');
    option.value = id;
    option.textContent = id;
    rootSelect.appendChild(option);
  });
  rootLabel.appendChild(rootSelect);
  treeSection.appendChild(rootLabel);
  const treeActions = document.createElement('div');
  treeActions.className = 'inspector-skinning-chain-actions';
  const buildTree = document.createElement('button');
  buildTree.type = 'button';
  buildTree.className = 'ui-button inspector-skinning-build-tree';
  buildTree.textContent = 'Build Candidate Tree';
  buildTree.addEventListener('click', () => {
    buildCandidateTree(mesh, Number(rootSelect.value));
    const latest = getSkinningState(mesh);
    update(latest);
    onStateChange?.(latest);
  });
  treeActions.appendChild(buildTree);
  const showTree = document.createElement('button');
  showTree.type = 'button';
  showTree.className = 'ui-button inspector-skinning-tree-show';
  showTree.addEventListener('click', () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    setInfluenceVisualizationMode(
      mesh, latest.influenceVisualizationMode === 'tree' ? null : 'tree');
    update(latest);
  });
  treeActions.appendChild(showTree);
  treeSection.appendChild(treeActions);
  const treeStatus = addText(
    treeSection, 'inspector-skinning-tree-status', '');
  const treeOutput = document.createElement('pre');
  treeOutput.className = 'inspector-skinning-tree-output';
  treeSection.appendChild(treeOutput);
  section.appendChild(treeSection);
  parent.appendChild(section);

  let selectedRelationship = null;

  function addStat(label, value) {
    const row = document.createElement('div');
    row.className = 'inspector-skinning-coverage-stat';
    addText(row, 'inspector-label', label);
    addText(row, 'inspector-value', value);
    stats.appendChild(row);
  }

  function update(latest = getSkinningState(mesh)) {
    if (!latest) return;
    const graph = latest.influenceGraph;
    const valid = !!graph;
    stats.replaceChildren();
    neighbors.replaceChildren();
    relationship.replaceChildren();
    stats.hidden = !valid;
    neighborsTitle.hidden = !valid;
    neighbors.hidden = !valid;
    graphButton.disabled = !valid;
    graphButton.hidden = !valid;
    copyButton.disabled = !valid || copying;
    copyButton.hidden = !valid;
    if (!valid) copyStatus.textContent = '';
    graphButton.textContent = latest.influenceVisualizationMode === 'graph'
      ? 'Hide Influence Graph' : 'Show Influence Graph';
    graphButton.classList.toggle(
      'active', latest.influenceVisualizationMode === 'graph');
    buildTree.disabled = !valid;
    rootSelect.disabled = !valid;
    if (!valid) {
      showTree.disabled = true;
      showTree.textContent = 'Show Candidate Tree';
      treeOutput.textContent = '';
      treeStatus.textContent = '';
      return;
    }

    const selectedId = Number(latest.selectedBone);
    const selectedNode = graph.nodes.find(node => node.boneId === selectedId)
      || graph.nodes[0];
    if (!selectedNode) return;
    influenceSelect.value = selectedNode.boneId;
    rootSelect.value = String(
      latest.candidateRootId ?? graph.nodes[0].boneId);
    addStat('Total weight', graphMetric(selectedNode.totalWeight));
    addStat('Vertices', selectedNode.affectedVertexCount.toLocaleString());
    addStat('Max weight', graphMetric(selectedNode.maxVertexWeight));
    addStat('Weighted radius', graphMetric(selectedNode.weightedRadius));
    if (selectedRelationship
        && Number(selectedRelationship.boneA) !== selectedNode.boneId
        && Number(selectedRelationship.boneB) !== selectedNode.boneId) {
      selectedRelationship = null;
    }

    const candidateRelationships = graph.relationships
      .filter(item => item.boneA === selectedNode.boneId
        || item.boneB === selectedNode.boneId)
      .map(item => ({...item, _boneId: selectedNode.boneId}))
      .sort(compareNeighborRelationships);
    candidateRelationships.slice(0, INFLUENCE_NEIGHBOR_DISPLAY_LIMIT)
      .forEach(item => {
        const neighborId = relationshipNeighbor(item, selectedNode.boneId);
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'inspector-skinning-neighbor-row';
        row.textContent = `ID ${neighborId}  overlap ${graphMetric(item.containment)}`
          + ` \u00b7 distance ${graphMetric(item.normalizedDistance)}`;
        row.title = `Shared vertices ${item.sharedVertexCount}; `
          + `Jaccard ${graphMetric(item.jaccard)}`;
        row.addEventListener('click', () => {
          selectedRelationship = item;
          setSelectedBone(mesh, neighborId);
          const mainSelect = section.closest('.inspector-skinning-section')
            ?.querySelector('.inspector-skinning-bone');
          if (mainSelect) mainSelect.value = neighborId;
          update(getSkinningState(mesh));
        });
        neighbors.appendChild(row);
      });
    if (candidateRelationships.length > INFLUENCE_NEIGHBOR_DISPLAY_LIMIT) {
      addText(neighbors, 'inspector-skinning-hint',
        `+ ${candidateRelationships.length - INFLUENCE_NEIGHBOR_DISPLAY_LIMIT} more`);
    }

    if (selectedRelationship) {
      const item = selectedRelationship;
      const heading = addText(relationship, 'inspector-skinning-subtitle',
        `${item.boneA} \u2194 ${item.boneB}`);
      heading.dataset.relationshipDetail = 'true';
      [
        ['Shared vertices', item.sharedVertexCount.toLocaleString()],
        ['Containment', graphMetric(item.containment)],
        ['Jaccard', graphMetric(item.jaccard)],
        ['Min overlap', graphMetric(item.minOverlap)],
        ['Product overlap', graphMetric(item.productOverlap)],
        ['Center distance', graphMetric(item.centerDistance)],
        ['Normalized distance', graphMetric(item.normalizedDistance)],
      ].forEach(([label, value]) => addStatTo(relationship, label, value));
    }

    const tree = latest.candidateTree;
    treeStatus.textContent = tree
      ? `Candidate graph has ${tree.components.length} component${
        tree.components.length === 1 ? '' : 's'}.`
      : 'Build a candidate tree from the manually selected root.';
    treeOutput.textContent = tree ? candidateTreeText(tree) : '';
    showTree.disabled = !tree;
    showTree.textContent = latest.influenceVisualizationMode === 'tree'
      ? 'Hide Candidate Tree' : 'Show Candidate Tree';
    showTree.classList.toggle(
      'active', latest.influenceVisualizationMode === 'tree');
  }

  function addStatTo(parentNode, label, value) {
    const row = document.createElement('div');
    row.className = 'inspector-skinning-coverage-stat';
    addText(row, 'inspector-label', label);
    addText(row, 'inspector-value', value);
    parentNode.appendChild(row);
  }

  influenceSelect.addEventListener('change', () => {
    selectedRelationship = null;
    setSelectedBone(mesh, Number(influenceSelect.value));
    update();
  });
  rootSelect.addEventListener('change', () => {
    const latestRoot = setCandidateTreeRoot(mesh, Number(rootSelect.value));
    const latest = getSkinningState(mesh);
    if (latestRoot !== false) update(latest);
    onStateChange?.(latest);
  });
  update(state);
  skinningUpdates.set(mesh, update);
  return {update};
}

function forestSummary(forest) {
  if (!forest) return 'Build a candidate tree to create a rooted candidate forest.';
  const components = forest.components || [];
  const nodeCount = components.reduce(
    (total, component) => total + (component.nodeIds || []).length, 0);
  const edgeCount = components.reduce(
    (total, component) => total + Number(component.edgeCount || 0), 0);
  const maxDepth = Math.max(0, ...components.map(
    component => Number(component.maxDepth || 0)));
  const roots = components.map(component => {
    const kind = component.primary ? 'primary' : 'auto';
    return `${component.componentId}: ${component.rootId} (${kind})`;
  }).join('  ');
  return [
    `${components.length} component${components.length === 1 ? '' : 's'}`
      + ` \u00b7 ${nodeCount} nodes \u00b7 ${edgeCount} edges`,
    `Max depth ${maxDepth} \u00b7 Primary root ${forest.primaryRootId}`,
    `Roots ${roots}`,
  ].join('\n');
}

function buildSkinningForestControls(parent, mesh, state, onStateChange = null) {
  const section = document.createElement('div');
  section.className = 'inspector-skinning-forest';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-subtitle';
  title.textContent = 'Candidate Forest Deformation';
  section.appendChild(title);
  addText(section, 'inspector-skinning-hint',
    'Experimental deformation across every candidate-tree component.'
      + ' Roots stay fixed; secondary roots are spatially selected.');

  const summary = document.createElement('pre');
  summary.className = 'inspector-skinning-forest-summary';
  section.appendChild(summary);

  const axisRow = document.createElement('div');
  axisRow.className = 'inspector-skinning-forest-axis';
  addText(axisRow, 'inspector-label', 'Axis');
  const axisButtons = document.createElement('span');
  ['X', 'Y', 'Z'].forEach(axis => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'inspector-skinning-axis-button';
    button.textContent = axis;
    button.addEventListener('click', () => {
      setForestAxis(mesh, axis);
      update();
      onStateChange?.(getSkinningState(mesh));
    });
    axisButtons.appendChild(button);
  });
  axisRow.appendChild(axisButtons);
  section.appendChild(axisRow);

  const angleRow = document.createElement('div');
  angleRow.className = 'inspector-skinning-forest-bend';
  const angleHeader = document.createElement('div');
  angleHeader.className = 'inspector-skinning-rotation-header';
  addText(angleHeader, 'inspector-label', 'Forest Angle');
  const angleValue = addText(
    angleHeader, 'inspector-skinning-forest-angle-value', `${state.forestAngle}°`);
  angleRow.appendChild(angleHeader);
  const angleSlider = document.createElement('input');
  angleSlider.type = 'range';
  angleSlider.className = 'inspector-skinning-forest-angle';
  angleSlider.min = '-60';
  angleSlider.max = '60';
  angleSlider.step = '1';
  angleSlider.value = state.forestAngle;
  angleSlider.addEventListener('input', () => {
    setForestAngle(mesh, angleSlider.value);
    update();
    onStateChange?.(getSkinningState(mesh));
  });
  angleRow.appendChild(angleSlider);
  section.appendChild(angleRow);

  let copying = false;
  const actions = document.createElement('div');
  actions.className = 'inspector-skinning-chain-actions';
  const copyButton = document.createElement('button');
  copyButton.type = 'button';
  copyButton.className = 'ui-button inspector-skinning-copy-forest';
  copyButton.textContent = 'Copy Forest Diagnostics';
  copyButton.addEventListener('click', async () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    copying = true;
    copyButton.disabled = true;
    copyStatus.textContent = '';
    try {
      await copyGraphDiagnostics(latest, true);
      copyStatus.textContent = 'Forest diagnostics copied.';
    } catch (error) {
      copyStatus.textContent = error instanceof Error
        ? error.message : String(error);
    } finally {
      copying = false;
      update();
    }
  });
  actions.appendChild(copyButton);
  section.appendChild(actions);
  const copyStatus = addText(
    section, 'inspector-skinning-forest-copy-status', '');

  function update(latest = getSkinningState(mesh)) {
    if (!latest) return;
    const forest = latest.candidateForest;
    const valid = !!forest;
    summary.textContent = forestSummary(forest);
    axisButtons.querySelectorAll('button').forEach(button => {
      button.disabled = !valid;
      button.classList.toggle('selected', button.textContent === latest.forestAxis);
    });
    angleSlider.disabled = !valid;
    angleSlider.value = latest.forestAngle;
    angleValue.textContent = `${latest.forestAngle}°`;
    copyButton.disabled = !valid || copying;
    copyButton.hidden = !valid;
    if (!valid) copyStatus.textContent = '';
  }

  parent.appendChild(section);
  update(state);
  return {update};
}

function physicsSummary(state) {
  if (!state?.candidateForest) {
    return 'Build a candidate tree to enable spring physics.';
  }
  const status = state.physicsEnabled
    ? (state.physicsSettled ? 'Settled' : 'Active') : 'Disabled';
  const jointCount = state.physicsState?.joints?.size
    ?? (state.candidateForest.components || []).reduce(
      (total, component) => total + Math.max(
        0, (component.nodeIds || []).length - 1), 0);
  const angularResponse = Number(state.physicsMotionStrength) || 0;
  const linearResponse = Number(state.physicsLinearMotionStrength) || 0;
  const continuousResponse = Number(
    state.physicsContinuousLinearResponse) || 0;
  const lastRootAngularDelta = Number(state.lastRootAngularDelta) || 0;
  const lastProjectedAngularDelta = Number(
    state.lastProjectedAngularDelta) || 0;
  const lastTranslationLag = Number(state.lastTranslationLag) || 0;
  const gravityScale = Number(state.physicsGravityScale) || 0;
  const gravityDiagnostics = state.physicsGravityDiagnostics;
  const gravityMaxAcceleration = Number(
    gravityDiagnostics?.maxAbsTotalAcceleration) || 0;
  const gravityDirection = (state.physicsGravityLocal
    || GRAVITY_WORLD_DIRECTION).map(value => Number(value).toFixed(2));
  const constraintDiagnostics = getPhysicsConstraintDiagnostics(state);
  return [
    `Angular response ${angularResponse.toFixed(2)} / `
      + `Discrete linear ${linearResponse.toFixed(2)}`,
    `Continuous response ${continuousResponse.toFixed(2)} / `
      + `Velocity events ${state.continuousMotionEventCount || 0}`,
    `Translation lag ${lastTranslationLag * 180 / Math.PI} deg / `
      + `Events ${state.translationEventCount || 0}`,
    `Model input ${lastRootAngularDelta * 180 / Math.PI}Â° Â· `
      + `Projected ${lastProjectedAngularDelta * 180 / Math.PI}Â° Â· `
      + `Events ${state.motionEventCount || 0}`,
    `Physics ${status} · ${jointCount} dynamic joints`,
    `Target bend ${state.physicsTargetAngle}° · Axis ${state.physicsAxis}`,
    `Frequency ${Number(state.physicsFrequencyHz).toFixed(2)} Hz · `
      + `Damping ${Number(state.physicsDampingRatio).toFixed(2)}`,
    `Gravity ${state.physicsGravityEnabled ? 'On' : 'Off'} / `
      + `Scale ${gravityScale.toFixed(1)} / Max `
      + `${(gravityMaxAcceleration * 180 / Math.PI).toFixed(1)} deg/s2`,
    `Gravity local [${gravityDirection.join(', ')}]`,
    `Joint limits ${constraintDiagnostics.enabled ? 'On' : 'Off'} / `
      + `Max component ${constraintDiagnostics.maxComponentBend.toFixed(0)}°`,
    `At limit ${constraintDiagnostics.atLimitCount} / `
      + `${constraintDiagnostics.limitedJointCount} joints`,
  ].join('\n');
}

function buildSkinningPhysicsControls(parent, mesh, state) {
  const section = document.createElement('div');
  section.className = 'inspector-skinning-physics';
  const title = document.createElement('div');
  title.className = 'inspector-skinning-subtitle';
  title.textContent = 'Spring Physics Prototype';
  section.appendChild(title);
  addText(section, 'inspector-skinning-hint',
    'Dynamic local bends follow the candidate forest with fixed-step springs.');

  const summary = document.createElement('pre');
  summary.className = 'inspector-skinning-physics-summary';
  section.appendChild(summary);

  const axisRow = document.createElement('div');
  axisRow.className = 'inspector-skinning-physics-axis';
  addText(axisRow, 'inspector-label', 'Axis');
  const axisButtons = document.createElement('span');
  ['X', 'Y', 'Z'].forEach(axis => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'inspector-skinning-axis-button';
    button.textContent = axis;
    button.addEventListener('click', () => {
      setPhysicsAxis(mesh, axis);
      update();
    });
    axisButtons.appendChild(button);
  });
  axisRow.appendChild(axisButtons);
  section.appendChild(axisRow);

  const targetLabel = document.createElement('label');
  targetLabel.className = 'inspector-skinning-field';
  addText(targetLabel, 'inspector-label', 'Target Bend');
  const targetValue = addText(targetLabel,
    'inspector-skinning-physics-target-value', '0°');
  const targetSlider = document.createElement('input');
  targetSlider.type = 'range';
  targetSlider.className = 'inspector-skinning-physics-target-angle';
  targetSlider.min = '-40';
  targetSlider.max = '40';
  targetSlider.step = '1';
  targetSlider.value = state.physicsTargetAngle;
  targetSlider.addEventListener('input', () => {
    setPhysicsTargetAngle(mesh, targetSlider.value);
    update();
  });
  targetLabel.appendChild(targetSlider);
  section.appendChild(targetLabel);

  const frequencyLabel = document.createElement('label');
  frequencyLabel.className = 'inspector-skinning-field';
  addText(frequencyLabel, 'inspector-label', 'Frequency (Hz)');
  const frequencyInput = document.createElement('input');
  frequencyInput.type = 'number';
  frequencyInput.className = 'inspector-skinning-physics-frequency';
  frequencyInput.min = '0.1';
  frequencyInput.max = '10';
  frequencyInput.step = '0.05';
  frequencyInput.value = state.physicsFrequencyHz;
  frequencyInput.addEventListener('change', () => {
    setPhysicsFrequency(mesh, frequencyInput.value);
    update();
  });
  frequencyLabel.appendChild(frequencyInput);
  section.appendChild(frequencyLabel);

  const dampingLabel = document.createElement('label');
  dampingLabel.className = 'inspector-skinning-field';
  addText(dampingLabel, 'inspector-label', 'Damping');
  const dampingInput = document.createElement('input');
  dampingInput.type = 'number';
  dampingInput.className = 'inspector-skinning-physics-damping';
  dampingInput.min = '0';
  dampingInput.max = '2';
  dampingInput.step = '0.05';
  dampingInput.value = state.physicsDampingRatio;
  dampingInput.addEventListener('change', () => {
    setPhysicsDamping(mesh, dampingInput.value);
    update();
  });
  dampingLabel.appendChild(dampingInput);
  section.appendChild(dampingLabel);

  const motionLabel = document.createElement('label');
  motionLabel.className = 'inspector-skinning-field';
  const motionHeader = document.createElement('span');
  motionHeader.className = 'inspector-skinning-rotation-header';
  addText(motionHeader, 'inspector-label', 'Angular Response');
  const motionValue = addText(motionHeader,
    'inspector-skinning-physics-motion-value', '0.35');
  motionLabel.appendChild(motionHeader);
  const motionInput = document.createElement('input');
  motionInput.type = 'range';
  motionInput.className = 'inspector-skinning-physics-motion-strength';
  motionInput.min = '0';
  motionInput.max = '1';
  motionInput.step = '0.05';
  motionInput.value = state.physicsMotionStrength;
  motionInput.addEventListener('input', () => {
    setPhysicsMotionStrength(mesh, motionInput.value);
    update();
  });
  motionLabel.appendChild(motionInput);
  section.appendChild(motionLabel);

  const linearLabel = document.createElement('label');
  linearLabel.className = 'inspector-skinning-field';
  const linearHeader = document.createElement('span');
  linearHeader.className = 'inspector-skinning-rotation-header';
  addText(linearHeader, 'inspector-label', 'Linear Response');
  const linearValue = addText(linearHeader,
    'inspector-skinning-physics-linear-value', '0.35');
  linearLabel.appendChild(linearHeader);
  const linearInput = document.createElement('input');
  linearInput.type = 'range';
  linearInput.className = 'inspector-skinning-physics-linear-strength';
  linearInput.min = '0';
  linearInput.max = '1';
  linearInput.step = '0.05';
  linearInput.value = state.physicsLinearMotionStrength;
  linearInput.addEventListener('input', () => {
    setPhysicsLinearMotionStrength(mesh, linearInput.value);
    update();
  });
  linearLabel.appendChild(linearInput);
  section.appendChild(linearLabel);

  const continuousResponseLabel = document.createElement('label');
  continuousResponseLabel.className = 'inspector-skinning-field';
  const continuousResponseHeader = document.createElement('span');
  continuousResponseHeader.className = 'inspector-skinning-rotation-header';
  addText(continuousResponseHeader, 'inspector-label', 'Continuous Response');
  const continuousResponseValue = addText(continuousResponseHeader,
    'inspector-skinning-physics-continuous-response-value', '0.35');
  continuousResponseLabel.appendChild(continuousResponseHeader);
  const continuousResponseInput = document.createElement('input');
  continuousResponseInput.type = 'range';
  continuousResponseInput.className =
    'inspector-skinning-physics-continuous-response';
  continuousResponseInput.min = '0';
  continuousResponseInput.max = '1';
  continuousResponseInput.step = '0.05';
  continuousResponseInput.value = state.physicsContinuousLinearResponse;
  continuousResponseInput.addEventListener('input', () => {
    setPhysicsContinuousLinearResponse(mesh, continuousResponseInput.value);
    update();
  });
  continuousResponseLabel.appendChild(continuousResponseInput);
  section.appendChild(continuousResponseLabel);

  const gravity = document.createElement('div');
  gravity.className = 'inspector-skinning-physics-gravity';
  const gravityTitle = document.createElement('div');
  gravityTitle.className = 'inspector-skinning-subtitle';
  gravityTitle.textContent = 'Gravity';
  gravity.appendChild(gravityTitle);
  addText(gravity, 'inspector-skinning-hint',
    'Gravity is projected onto the selected Physics Axis.');

  const gravityEnableLabel = document.createElement('label');
  gravityEnableLabel.className = 'inspector-skinning-physics-enable-label';
  const gravityEnableInput = document.createElement('input');
  gravityEnableInput.type = 'checkbox';
  gravityEnableInput.className = 'inspector-skinning-physics-gravity-enable';
  gravityEnableInput.addEventListener('change', () => {
    setPhysicsGravityEnabled(mesh, gravityEnableInput.checked);
    update();
  });
  gravityEnableLabel.appendChild(gravityEnableInput);
  addText(gravityEnableLabel, 'inspector-label', 'Enable Gravity');
  gravity.appendChild(gravityEnableLabel);

  const gravityScaleLabel = document.createElement('label');
  gravityScaleLabel.className = 'inspector-skinning-field';
  const gravityScaleHeader = document.createElement('span');
  gravityScaleHeader.className = 'inspector-skinning-rotation-header';
  addText(gravityScaleHeader, 'inspector-label', 'Gravity Scale');
  const gravityScaleValue = addText(gravityScaleHeader,
    'inspector-skinning-physics-gravity-scale-value', '1.0');
  gravityScaleLabel.appendChild(gravityScaleHeader);
  const gravityScaleInput = document.createElement('input');
  gravityScaleInput.type = 'range';
  gravityScaleInput.className = 'inspector-skinning-physics-gravity-scale';
  gravityScaleInput.min = '0';
  gravityScaleInput.max = '2';
  gravityScaleInput.step = '0.1';
  gravityScaleInput.value = state.physicsGravityScale;
  gravityScaleInput.addEventListener('input', () => {
    setPhysicsGravityScale(mesh, gravityScaleInput.value);
    update();
  });
  gravityScaleLabel.appendChild(gravityScaleInput);
  gravity.appendChild(gravityScaleLabel);

  const gravityDirection = addText(gravity,
    'inspector-skinning-physics-gravity-direction', 'Direction Down (-Y)');
  const gravityDiagnostic = addText(gravity,
    'inspector-skinning-physics-gravity-diagnostic',
    'Max Gravity Accel 0.0 deg/s2');
  addText(gravity, 'inspector-skinning-hint',
    'A poorly aligned axis may produce little visible gravity motion.');
  section.appendChild(gravity);

  const constraints = document.createElement('div');
  constraints.className = 'inspector-skinning-physics-constraints';
  const constraintsTitle = document.createElement('div');
  constraintsTitle.className = 'inspector-skinning-subtitle';
  constraintsTitle.textContent = 'Rest Constraints';
  constraints.appendChild(constraintsTitle);
  addText(constraints, 'inspector-skinning-hint',
    'Limits are measured from the inferred rest pose and distributed across '
      + 'each component\'s depth.');

  const constraintsEnableLabel = document.createElement('label');
  constraintsEnableLabel.className =
    'inspector-skinning-physics-enable-label';
  const constraintsEnableInput = document.createElement('input');
  constraintsEnableInput.type = 'checkbox';
  constraintsEnableInput.className =
    'inspector-skinning-physics-constraints-enable';
  constraintsEnableInput.addEventListener('change', () => {
    setPhysicsConstraintsEnabled(mesh, constraintsEnableInput.checked);
    update();
  });
  constraintsEnableLabel.appendChild(constraintsEnableInput);
  addText(constraintsEnableLabel, 'inspector-label', 'Enable Joint Limits');
  constraints.appendChild(constraintsEnableLabel);

  const maxBendLabel = document.createElement('label');
  maxBendLabel.className = 'inspector-skinning-field';
  const maxBendHeader = document.createElement('span');
  maxBendHeader.className = 'inspector-skinning-rotation-header';
  addText(maxBendHeader, 'inspector-label', 'Max Component Bend');
  const maxBendValue = addText(maxBendHeader,
    'inspector-skinning-physics-max-bend-value', '45°');
  maxBendLabel.appendChild(maxBendHeader);
  const maxBendInput = document.createElement('input');
  maxBendInput.type = 'range';
  maxBendInput.className = 'inspector-skinning-physics-max-bend';
  maxBendInput.min = '0';
  maxBendInput.max = '90';
  maxBendInput.step = '1';
  maxBendInput.value = state.physicsMaxBendDegrees;
  maxBendInput.addEventListener('input', () => {
    setPhysicsMaxBendDegrees(mesh, maxBendInput.value);
    update();
  });
  maxBendLabel.appendChild(maxBendInput);
  constraints.appendChild(maxBendLabel);

  const constraintsDiagnostic = addText(constraints,
    'inspector-skinning-physics-constraints-diagnostic',
    'At Limit 0 / 0 joints');
  section.appendChild(constraints);

  const translation = document.createElement('div');
  translation.className = 'inspector-skinning-physics-translation';
  const translationTitle = document.createElement('div');
  translationTitle.className = 'inspector-skinning-subtitle';
  translationTitle.textContent = 'Model Translation Test';
  translation.appendChild(translationTitle);
  addText(translation, 'inspector-skinning-hint',
    'Move the model by a fraction of the selected mesh radius.');

  const translationAxisRow = document.createElement('div');
  translationAxisRow.className = 'inspector-skinning-physics-translation-axis';
  addText(translationAxisRow, 'inspector-label', 'Move Axis');
  const translationAxisButtons = document.createElement('span');
  let translationAxis = 'X';
  ['X', 'Y', 'Z'].forEach(axis => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'inspector-skinning-axis-button';
    button.textContent = axis;
    button.addEventListener('click', () => {
      translationAxis = axis;
      update();
    });
    translationAxisButtons.appendChild(button);
  });
  translationAxisRow.appendChild(translationAxisButtons);
  translation.appendChild(translationAxisRow);

  const translationStepRow = document.createElement('label');
  translationStepRow.className = 'inspector-skinning-field';
  addText(translationStepRow, 'inspector-label', 'Step × Radius');
  const translationStepValue = addText(translationStepRow,
    'inspector-skinning-physics-translation-step-value', '0.10');
  const translationStepInput = document.createElement('input');
  translationStepInput.type = 'range';
  translationStepInput.className = 'inspector-skinning-physics-translation-step';
  translationStepInput.min = '0.01';
  translationStepInput.max = '0.50';
  translationStepInput.step = '0.01';
  translationStepInput.value = '0.10';
  translationStepInput.addEventListener('input', updateTranslationStep);
  translationStepRow.appendChild(translationStepInput);
  translation.appendChild(translationStepRow);

  const translationActions = document.createElement('div');
  translationActions.className = 'inspector-skinning-chain-actions';
  const moveMinus = document.createElement('button');
  moveMinus.type = 'button';
  moveMinus.className = 'ui-button inspector-skinning-physics-translation-minus';
  moveMinus.textContent = 'Move -';
  moveMinus.addEventListener('click', () => moveModel(-1));
  translationActions.appendChild(moveMinus);
  const movePlus = document.createElement('button');
  movePlus.type = 'button';
  movePlus.className = 'ui-button inspector-skinning-physics-translation-plus';
  movePlus.textContent = 'Move +';
  movePlus.addEventListener('click', () => moveModel(1));
  translationActions.appendChild(movePlus);
  translation.appendChild(translationActions);
  section.appendChild(translation);

  const continuous = document.createElement('div');
  continuous.className = 'inspector-skinning-physics-continuous';
  const continuousTitle = document.createElement('div');
  continuousTitle.className = 'inspector-skinning-subtitle';
  continuousTitle.textContent = 'Continuous Translation Test';
  continuous.appendChild(continuousTitle);
  addText(continuous, 'inspector-skinning-hint',
    'Ramp model velocity to test acceleration, stopping, and reversal.');

  let continuousAxis = 'X';
  const continuousAxisRow = document.createElement('div');
  continuousAxisRow.className = 'inspector-skinning-physics-continuous-axis';
  addText(continuousAxisRow, 'inspector-label', 'Axis');
  const continuousAxisButtons = document.createElement('span');
  ['X', 'Y', 'Z'].forEach(axis => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'inspector-skinning-axis-button';
    button.textContent = axis;
    button.addEventListener('click', () => {
      continuousAxis = axis;
      setContinuousMotionAxis(axis);
      update();
    });
    continuousAxisButtons.appendChild(button);
  });
  continuousAxisRow.appendChild(continuousAxisButtons);
  continuous.appendChild(continuousAxisRow);

  const continuousSpeedLabel = document.createElement('label');
  continuousSpeedLabel.className = 'inspector-skinning-field';
  addText(continuousSpeedLabel, 'inspector-label', 'Speed x Radius / sec');
  const continuousSpeedValue = addText(continuousSpeedLabel,
    'inspector-skinning-physics-continuous-speed-value', '0.50');
  const continuousSpeedInput = document.createElement('input');
  continuousSpeedInput.type = 'range';
  continuousSpeedInput.className = 'inspector-skinning-physics-continuous-speed';
  continuousSpeedInput.min = '0.05';
  continuousSpeedInput.max = '2';
  continuousSpeedInput.step = '0.05';
  continuousSpeedInput.value = '0.50';
  continuousSpeedInput.addEventListener('input', () => update());
  continuousSpeedLabel.appendChild(continuousSpeedInput);
  continuous.appendChild(continuousSpeedLabel);

  const continuousAccelerationLabel = document.createElement('label');
  continuousAccelerationLabel.className = 'inspector-skinning-field';
  addText(continuousAccelerationLabel,
    'inspector-label', 'Acceleration x Radius / sec2');
  const continuousAccelerationValue = addText(continuousAccelerationLabel,
    'inspector-skinning-physics-continuous-acceleration-value', '1.50');
  const continuousAccelerationInput = document.createElement('input');
  continuousAccelerationInput.type = 'range';
  continuousAccelerationInput.className =
    'inspector-skinning-physics-continuous-acceleration';
  continuousAccelerationInput.min = '0.10';
  continuousAccelerationInput.max = '5';
  continuousAccelerationInput.step = '0.10';
  continuousAccelerationInput.value = '1.50';
  continuousAccelerationInput.addEventListener('input', () => update());
  continuousAccelerationLabel.appendChild(continuousAccelerationInput);
  continuous.appendChild(continuousAccelerationLabel);

  const continuousActions = document.createElement('div');
  continuousActions.className = 'inspector-skinning-chain-actions';
  const continuousMinus = document.createElement('button');
  continuousMinus.type = 'button';
  continuousMinus.className =
    'ui-button inspector-skinning-physics-continuous-minus';
  continuousMinus.textContent = 'Move -';
  continuousMinus.addEventListener('click', () => startContinuous(-1));
  continuousActions.appendChild(continuousMinus);
  const continuousStop = document.createElement('button');
  continuousStop.type = 'button';
  continuousStop.className =
    'ui-button inspector-skinning-physics-continuous-stop';
  continuousStop.textContent = 'Stop';
  continuousStop.addEventListener('click', () => {
    stopContinuousMotion();
    update();
  });
  continuousActions.appendChild(continuousStop);
  const continuousPlus = document.createElement('button');
  continuousPlus.type = 'button';
  continuousPlus.className =
    'ui-button inspector-skinning-physics-continuous-plus';
  continuousPlus.textContent = 'Move +';
  continuousPlus.addEventListener('click', () => startContinuous(1));
  continuousActions.appendChild(continuousPlus);
  continuous.appendChild(continuousActions);

  const continuousCurrent = addText(continuous,
    'inspector-skinning-physics-continuous-current', 'Current Speed 0.00');
  section.appendChild(continuous);

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
  addText(enableLabel, 'inspector-label', 'Enable');
  section.appendChild(enableLabel);

  const actions = document.createElement('div');
  actions.className = 'inspector-skinning-chain-actions';
  const kickMinus = document.createElement('button');
  kickMinus.type = 'button';
  kickMinus.className = 'ui-button inspector-skinning-physics-kick-minus';
  kickMinus.textContent = 'Kick -';
  kickMinus.addEventListener('click', () => {
    applyPhysicsKick(mesh, -1);
    update();
  });
  actions.appendChild(kickMinus);
  const kickPlus = document.createElement('button');
  kickPlus.type = 'button';
  kickPlus.className = 'ui-button inspector-skinning-physics-kick-plus';
  kickPlus.textContent = 'Kick +';
  kickPlus.addEventListener('click', () => {
    applyPhysicsKick(mesh, 1);
    update();
  });
  actions.appendChild(kickPlus);
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

  let copying = false;
  const copyButton = document.createElement('button');
  copyButton.type = 'button';
  copyButton.className = 'ui-button inspector-skinning-copy-physics';
  copyButton.textContent = 'Copy Physics Diagnostics';
  copyButton.addEventListener('click', async () => {
    const latest = getSkinningState(mesh);
    if (!latest) return;
    copying = true;
    update();
    try {
      await copyGraphDiagnostics(latest, true);
      copyStatus.textContent = 'Physics diagnostics copied.';
    } catch (error) {
      copyStatus.textContent = error instanceof Error
        ? error.message : String(error);
    } finally {
      copying = false;
      update();
    }
  });
  section.appendChild(copyButton);
  const copyStatus = addText(
    section, 'inspector-skinning-physics-copy-status', '');

  function updateTranslationStep() {
    translationStepValue.textContent = Number(
      translationStepInput.value).toFixed(2);
  }

  function moveModel(direction) {
    const latest = getSkinningState(mesh);
    const radius = Number(latest?.influenceGraph?.boundingSphereRadius);
    const step = Number(translationStepInput.value);
    if (!Number.isFinite(radius) || radius <= 0
        || !Number.isFinite(step)) return;
    const delta = [0, 0, 0];
    const axisIndex = {X: 0, Y: 1, Z: 2}[translationAxis];
    delta[axisIndex] = direction * radius * step;
    translateModel(activeMeshes, delta);
    update();
  }

  function startContinuous(direction) {
    const latest = getSkinningState(mesh);
    const radius = Number(latest?.influenceGraph?.boundingSphereRadius);
    const speedScale = Number(continuousSpeedInput.value);
    const accelerationScale = Number(continuousAccelerationInput.value);
    if (!Number.isFinite(radius) || radius <= 0
        || !Number.isFinite(speedScale) || !Number.isFinite(accelerationScale)) {
      return;
    }
    startContinuousMotion(activeMeshes, {
      axis: continuousAxis,
      speedScale,
      accelerationScale,
      radius,
      direction,
    });
    update();
  }

  function update(latest = getSkinningState(mesh)) {
    if (!latest) return;
    const valid = !!latest.candidateForest;
    summary.textContent = physicsSummary(latest);
    axisButtons.querySelectorAll('button').forEach(button => {
      button.disabled = !valid;
      button.classList.toggle(
        'selected', button.textContent === latest.physicsAxis);
    });
    targetSlider.disabled = !valid;
    targetSlider.value = latest.physicsTargetAngle;
    targetValue.textContent = `${latest.physicsTargetAngle}°`;
    frequencyInput.disabled = !valid;
    frequencyInput.value = latest.physicsFrequencyHz;
    dampingInput.disabled = !valid;
    dampingInput.value = latest.physicsDampingRatio;
    motionInput.disabled = !valid;
    motionInput.value = latest.physicsMotionStrength;
    motionValue.textContent = Number(latest.physicsMotionStrength).toFixed(2);
    linearInput.disabled = !valid;
    linearInput.value = latest.physicsLinearMotionStrength;
    linearValue.textContent = Number(
      latest.physicsLinearMotionStrength).toFixed(2);
    continuousResponseInput.disabled = !valid;
    continuousResponseInput.value = latest.physicsContinuousLinearResponse;
    continuousResponseValue.textContent = Number(
      latest.physicsContinuousLinearResponse).toFixed(2);
    gravityEnableInput.disabled = !valid;
    gravityEnableInput.checked = !!latest.physicsGravityEnabled;
    gravityScaleInput.disabled = !valid;
    gravityScaleInput.value = Number(
      latest.physicsGravityScale).toFixed(1);
    gravityScaleValue.textContent = Number(
      latest.physicsGravityScale).toFixed(1);
    const gravityLocal = latest.physicsGravityLocal
      || GRAVITY_WORLD_DIRECTION;
    gravityDirection.textContent = `Direction Down (-Y) -> local [`
      + `${gravityLocal.map(value => Number(value).toFixed(2)).join(', ')}]`;
    const gravityDiagnostics = latest.physicsGravityDiagnostics;
    const maxGravityAcceleration = Number(
      gravityDiagnostics?.maxAbsTotalAcceleration) || 0;
    const clampedGravityComponents = Number(
      gravityDiagnostics?.clampedComponentCount) || 0;
    gravityDiagnostic.textContent = `Max Gravity Accel ${(
      maxGravityAcceleration * 180 / Math.PI).toFixed(1)} deg/s2`
      + ` / Clamped ${clampedGravityComponents}`;
    const constraintDiagnostics = getPhysicsConstraintDiagnostics(latest);
    constraintsEnableInput.disabled = !valid;
    constraintsEnableInput.checked = constraintDiagnostics.enabled;
    maxBendInput.disabled = !valid;
    maxBendInput.value = constraintDiagnostics.maxComponentBend;
    maxBendValue.textContent = `${constraintDiagnostics.maxComponentBend.toFixed(0)}°`;
    constraintsDiagnostic.textContent = `At Limit ${constraintDiagnostics.atLimitCount}`
      + ` / ${constraintDiagnostics.limitedJointCount} joints`
      + ` · Max Usage ${(constraintDiagnostics.maxUsage * 100).toFixed(0)}%`;
    translationAxisButtons.querySelectorAll('button').forEach(button => {
      button.disabled = !valid;
      button.classList.toggle('selected', button.textContent === translationAxis);
    });
    translationStepInput.disabled = !valid;
    updateTranslationStep();
    const radius = Number(latest.influenceGraph?.boundingSphereRadius);
    const canTranslate = valid && Number.isFinite(radius) && radius > 0;
    moveMinus.disabled = !canTranslate;
    movePlus.disabled = !canTranslate;
    continuousSpeedInput.disabled = !canTranslate;
    continuousSpeedInput.value = Number(
      continuousSpeedInput.value).toFixed(2);
    continuousSpeedValue.textContent = Number(
      continuousSpeedInput.value).toFixed(2);
    continuousAccelerationInput.disabled = !canTranslate;
    continuousAccelerationInput.value = Number(
      continuousAccelerationInput.value).toFixed(2);
    continuousAccelerationValue.textContent = Number(
      continuousAccelerationInput.value).toFixed(2);
    continuousAxisButtons.querySelectorAll('button').forEach(button => {
      button.disabled = !canTranslate;
      button.classList.toggle('selected', button.textContent === continuousAxis);
    });
    const continuousState = getContinuousMotionState();
    const currentSpeed = radius > 0
      ? continuousState.velocity / radius : 0;
    continuousCurrent.textContent = `Current Speed ${currentSpeed >= 0 ? '+' : ''}`
      + `${currentSpeed.toFixed(2)} x Radius / sec`;
    continuousMinus.disabled = !canTranslate;
    continuousPlus.disabled = !canTranslate;
    continuousStop.disabled = !canTranslate || !continuousState.running;
    enableInput.disabled = !valid;
    enableInput.checked = !!latest.physicsEnabled;
    kickMinus.disabled = !valid || !latest.physicsEnabled;
    kickPlus.disabled = !valid || !latest.physicsEnabled;
    reset.disabled = !valid || !latest.physicsEnabled;
    copyButton.disabled = !valid || copying;
    copyButton.hidden = !valid;
    if (!valid) copyStatus.textContent = '';
  }

  parent.appendChild(section);
  update(state);
  physicsUpdates.set(mesh, update);
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
  let forest = null;
  let physics = null;
  const influenceGraph = buildSkinningInfluenceGraphControls(
    chain, mesh, state, latest => {
      forest?.update(latest);
      physics?.update(latest);
    });
  forest = buildSkinningForestControls(
    chain, mesh, state, latest => physics?.update(latest));
  physics = buildSkinningPhysicsControls(chain, mesh, state);

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
    influenceGraph.update(latest);
    forest.update(latest);
    physics.update(latest);
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
  skinningUpdates.delete(mesh);
  physicsUpdates.delete(mesh);
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
    skinningUpdates.get(mesh)?.();
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
