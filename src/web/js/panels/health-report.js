// Read-only INI/resource diagnostics. The backend intentionally returns this
// report even when geometry cannot load, so this module has no scene dependency.

import { openIniEditor } from '../editing/ini-editor.js';
import { bindModalDismiss } from '../ui/modal-shell.js';
import { LANGUAGE_CHANGED, LOCALES, t } from '../i18n/index.js';

const $ = (id) => document.getElementById(id);

let currentReport = null;
let currentFilter = 'all';
let reportLoader = null;
let reportGeneration = 0;
let healthRequestId = 0;
let activeReportLoad = null;
let currentAssetResolution = null;

function diagnosticReason(issue) {
  const key = issue?.reason ? `diagnostics.reason.${issue.reason}` : '';
  if (key && Object.hasOwn(LOCALES.en, key)) {
    return t(key, {count: issue.count});
  }
  return issue?.problem || issue?.message || '';
}

export function diagnosticMessage(issue = {}) {
  const key = `diagnostics.issue.${issue.code || ''}`;
  if (!Object.hasOwn(LOCALES.en, key)) {
    return issue.message || issue.problem || '';
  }
  return t(key, {
    section: issue.section || '',
    source: issue.source || '',
    arguments: issue.arguments || '',
    lhs: issue.lhs || '',
    prefix: issue.prefix || '',
    target: issue.target || '',
    targetDisplay: issue.target_display || issue.target || '',
    key: issue.key || '',
    otherSection: issue.other_section || '',
    firstLine: issue.first_line || '',
    overrideType: issue.override_type || '',
    value: issue.value || '',
    binding: issue.binding || '',
    bindingType: issue.binding_type || '',
    variable: issue.variable || '',
    resource: issue.resource || '',
    stride: issue.stride || '',
    filename: issue.filename || '',
    detail: issue.detail || issue.message || '',
    reason: diagnosticReason(issue),
  });
}

function matchesFilter(issue) {
  if (currentFilter === 'all') return true;
  if (currentFilter === 'error') return issue.severity === 'error';
  return issue.category === currentFilter;
}

function locationText(issue) {
  const parts = [];
  if (issue.ini) parts.push(issue.ini);
  if (issue.section) parts.push(`[${issue.section}]`);
  if (issue.line) parts.push(t('health.line', {line: issue.line}));
  return parts.join(' · ');
}

function renderAssetResolution() {
  const node = $('health-asset-summary');
  const summary = currentAssetResolution;
  if (!node || !summary || summary.index_status === 'not_configured') {
    if (node) node.hidden = true;
    return;
  }
  const exact = Number(summary.exact_draws) || 0;
  const total = Number(summary.total_draws) || 0;
  const parts = summary.index_status === 'unavailable'
    ? [t('health.assetIndexUnavailable')]
    : summary.index_status === 'partial'
      ? [t('health.assetIndexesAvailable', {
        ready: summary.ready_roots || 0,
        configured: summary.configured_roots || 0,
      })]
      : [t('health.assetDrawsExact', {exact, total})];
  for (const [key, label] of [
    ['partial_draws', 'health.partialDraws'],
    ['ambiguous_draws', 'health.ambiguousDraws'],
    ['unmatched_draws', 'health.notFoundDraws'],
  ]) {
    const count = Number(summary[key]) || 0;
    if (count) parts.push(t(label, {count}));
  }
  const components = Array.isArray(summary.components)
    ? summary.components : [];
  const mixed = components.filter(item => item.status === 'mixed').length;
  if (mixed) parts.push(t('health.mixedComponents', {count: mixed}));
  node.textContent = parts.join(' · ');
  node.hidden = false;
}

function renderReport() {
  const report = currentReport || { summary: {}, files: {}, issues: [] };
  const summary = report.summary || {};
  const files = report.files || {};
  $('health-summary').textContent = t('health.summary', {
    errors: summary.errors || 0, warnings: summary.warnings || 0,
    referenced: files.referenced || 0, inactive: files.inactive_only || 0,
    viewer: files.viewer_only || 0,
  });
  renderAssetResolution();

  const issues = (report.issues || []).filter(matchesFilter);
  const list = $('health-list');
  list.innerHTML = '';
  if (!issues.length) {
    const empty = document.createElement('div');
    empty.className = 'health-empty';
    empty.textContent = currentFilter === 'all'
      ? t('health.noIssues')
      : t('health.noFilterIssues');
    list.appendChild(empty);
    return;
  }

  const groups = new Map();
  for (const issue of issues) {
    const group = issue.ini || t('health.assetFiles');
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(issue);
  }
  for (const [name, entries] of groups) {
    const heading = document.createElement('div');
    heading.className = 'health-group-title';
    heading.textContent = name;
    list.appendChild(heading);
    for (const issue of entries) {
      const item = document.createElement('div');
      item.className = `health-item ${issue.severity || 'warning'}`;
      if (issue.ini) {
        item.classList.add('navigable');
        item.title = t('health.openAtLine');
        item.addEventListener('dblclick', () => {
          closeReport();
          openIniEditor(issue.ini, issue.line || 1);
        });
      }

      const marker = document.createElement('span');
      marker.className = 'health-severity';
      marker.textContent = issue.severity === 'error' ? '!' : '△';
      item.appendChild(marker);

      const body = document.createElement('div');
      body.className = 'health-item-body';
      const message = document.createElement('div');
      message.className = 'health-message';
      message.textContent = diagnosticMessage(issue);
      body.appendChild(message);
      const location = locationText(issue);
      if (location) {
        const meta = document.createElement('div');
        meta.className = 'health-location';
        meta.textContent = location;
        body.appendChild(meta);
      }
      if (Array.isArray(issue.files) && issue.files.length) {
        const detail = document.createElement('div');
        detail.className = 'health-detail';
        detail.textContent = t('health.files', {files: issue.files.join(', ')});
        body.appendChild(detail);
      }
      if (issue.source) {
        const source = document.createElement('code');
        source.className = 'health-source';
        source.textContent = issue.source;
        body.appendChild(source);
      }
      item.appendChild(body);
      list.appendChild(item);
    }
  }
}

export function setHealthReport(report, assetResolution = undefined) {
  currentReport = report || null;
  if (assetResolution !== undefined) currentAssetResolution = assetResolution;
  else if (report?.asset_resolution) currentAssetResolution = report.asset_resolution;
  const button = $('health-btn');
  const count = report?.summary?.issues || 0;
  const errors = report?.summary?.errors || 0;
  button.disabled = !report && !reportLoader;
  button.classList.toggle('healthy', !!report && count === 0);
  button.classList.toggle('warning', !!report && count > 0 && errors === 0);
  button.classList.toggle('error', errors > 0);
  $('health-count').textContent = String(count);
  button.title = !report ? (reportLoader ? t('health.run') : t('health.open'))
    : count ? t(count === 1 ? 'health.issueOne' : 'health.issueMany', {count})
      : t('health.noIssues');
  button.setAttribute('aria-label', !report
    ? (reportLoader ? t('health.run') : t('health.open'))
    : t(count === 1 ? 'health.issueOpenOne' : 'health.issueOpenMany', {count}));
  if ($('health-modal-backdrop').classList.contains('show')) renderReport();
}

export function setAssetResolution(assetResolution) {
  currentAssetResolution = assetResolution || null;
  if ($('health-modal-backdrop').classList.contains('show')) renderReport();
}

export function setHealthLoader(loader) {
  reportLoader = typeof loader === 'function' ? loader : null;
  reportGeneration += 1;
  if (!currentReport) setHealthReport(null);
}

function fallbackReport(message = t('health.incomplete')) {
  return {
    summary: { errors: 0, warnings: 1, issues: 1 },
    files: {},
    issues: [{ severity: 'warning', category: 'ini', message }],
  };
}

// Run diagnostics without opening the modal. Loads are tied to the current
// loader generation so a slower report for the previous mod cannot overwrite
// the badge after the user switches folders.
export function refreshHealthReport({force = false} = {}) {
  const loader = reportLoader;
  const generation = reportGeneration;
  if (!loader) return Promise.resolve(null);
  if (!force && activeReportLoad?.generation === generation) {
    return activeReportLoad.promise;
  }

  const requestId = ++healthRequestId;
  const entry = { generation, promise: null };
  entry.promise = (async () => {
    const button = $('health-btn');
    button.disabled = true;
    button.title = t('health.running');
    try {
      const report = await loader();
      if (generation !== reportGeneration || loader !== reportLoader
          || requestId !== healthRequestId) return null;
      const normalized = report && !report.error ? report : fallbackReport();
      setHealthReport(normalized);
      return normalized;
    } catch (error) {
      if (generation !== reportGeneration || loader !== reportLoader
          || requestId !== healthRequestId) return null;
      const detail = error?.message ? `: ${error.message}` : '';
      const fallback = fallbackReport(
        t('health.incomplete', {detail}));
      setHealthReport(fallback);
      return fallback;
    } finally {
      if (activeReportLoad === entry) activeReportLoad = null;
    }
  })();
  activeReportLoad = entry;
  return entry.promise;
}

window.addEventListener(LANGUAGE_CHANGED, () => {
  if (currentReport) setHealthReport(currentReport);
});

async function openReport() {
  if (!currentReport) await refreshHealthReport();
  if (!currentReport) return;
  currentFilter = 'all';
  for (const button of $('health-filters').querySelectorAll('button')) {
    button.classList.toggle('active', button.dataset.healthFilter === 'all');
  }
  renderReport();
  $('health-modal-backdrop').classList.add('show');
}

function closeReport() {
  $('health-modal-backdrop').classList.remove('show');
}

$('health-btn').addEventListener('click', openReport);
$('health-filters').addEventListener('click', (event) => {
  const button = event.target.closest('[data-health-filter]');
  if (!button) return;
  currentFilter = button.dataset.healthFilter;
  for (const candidate of $('health-filters').querySelectorAll('button')) {
    candidate.classList.toggle('active', candidate === button);
  }
  renderReport();
});
bindModalDismiss({
  backdrop: $('health-modal-backdrop'),
  close: closeReport,
  buttons: [$('health-close'), $('health-close-x')],
});

function refreshAfterTextureSave() {
  void refreshHealthReport({force: true});
}

window.addEventListener('mod-viewer-texture-baked', refreshAfterTextureSave);
window.addEventListener('mod-viewer-texture-saved', refreshAfterTextureSave);
