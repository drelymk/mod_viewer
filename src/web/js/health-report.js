// Read-only INI/resource diagnostics. The backend intentionally returns this
// report even when geometry cannot load, so this module has no scene dependency.

import { openIniEditor } from './ini-editor.js';
import { bindModalDismiss } from './modal-shell.js';

const $ = (id) => document.getElementById(id);

let currentReport = null;
let currentFilter = 'all';
let reportLoader = null;
let reportGeneration = 0;
let activeReportLoad = null;
let currentAssetResolution = null;

function matchesFilter(issue) {
  if (currentFilter === 'all') return true;
  if (currentFilter === 'error') return issue.severity === 'error';
  return issue.category === currentFilter;
}

function locationText(issue) {
  const parts = [];
  if (issue.ini) parts.push(issue.ini);
  if (issue.section) parts.push(`[${issue.section}]`);
  if (issue.line) parts.push(`line ${issue.line}`);
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
    ? ['Asset resolution: index unavailable']
    : summary.index_status === 'partial'
      ? [`Asset resolution: ${summary.ready_roots || 0} of `
        `${summary.configured_roots || 0} indexes available`]
      : [`Asset resolution: ${exact} / ${total} draws exact`];
  for (const [key, label] of [
    ['partial_draws', 'partial'],
    ['ambiguous_draws', 'ambiguous'],
    ['unmatched_draws', 'not found'],
  ]) {
    const count = Number(summary[key]) || 0;
    if (count) parts.push(`${count} ${label}`);
  }
  const components = Array.isArray(summary.components)
    ? summary.components : [];
  const mixed = components.filter(item => item.status === 'mixed').length;
  if (mixed) parts.push(`${mixed} mixed component${mixed === 1 ? '' : 's'}`);
  node.textContent = parts.join(' · ');
  node.hidden = false;
}

function renderReport() {
  const report = currentReport || { summary: {}, files: {}, issues: [] };
  const summary = report.summary || {};
  const files = report.files || {};
  $('health-summary').textContent =
    `${summary.errors || 0} errors · ${summary.warnings || 0} warnings · ` +
    `${files.referenced || 0} referenced assets · ${files.inactive_only || 0} inactive-only · ` +
    `${files.viewer_only || 0} viewer-only`;
  renderAssetResolution();

  const issues = (report.issues || []).filter(matchesFilter);
  const list = $('health-list');
  list.innerHTML = '';
  if (!issues.length) {
    const empty = document.createElement('div');
    empty.className = 'health-empty';
    empty.textContent = currentFilter === 'all'
      ? 'No INI issues found.'
      : 'No issues match this filter.';
    list.appendChild(empty);
    return;
  }

  const groups = new Map();
  for (const issue of issues) {
    const group = issue.ini || 'Asset files';
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
        item.title = 'Double-click to open this INI at the reported line';
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
      message.textContent = issue.message;
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
        detail.textContent = `Files: ${issue.files.join(', ')}`;
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
  button.title = !report ? (reportLoader ? 'Run INI diagnostics' : 'Open a mod to run INI diagnostics')
    : count ? `${count} INI diagnostic issue${count === 1 ? '' : 's'}`
      : 'No INI issues found';
  button.setAttribute('aria-label', !report
    ? (reportLoader ? 'Run INI diagnostics' : 'Open a mod to run INI diagnostics')
    : `${count} INI diagnostic issue${count === 1 ? '' : 's'}. Open diagnostics`);
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

function fallbackReport(message = 'The INI diagnostics could not be completed.') {
  return {
    summary: { errors: 0, warnings: 1, issues: 1 },
    files: {},
    issues: [{ severity: 'warning', category: 'ini', message }],
  };
}

// Run diagnostics without opening the modal. Loads are tied to the current
// loader generation so a slower report for the previous mod cannot overwrite
// the badge after the user switches folders.
export function refreshHealthReport() {
  const loader = reportLoader;
  const generation = reportGeneration;
  if (!loader) return Promise.resolve(null);
  if (activeReportLoad?.generation === generation) return activeReportLoad.promise;

  const entry = { generation, promise: null };
  entry.promise = (async () => {
    const button = $('health-btn');
    button.disabled = true;
    button.title = 'Running INI diagnostics…';
    try {
      const report = await loader();
      if (generation !== reportGeneration || loader !== reportLoader) return null;
      const normalized = report && !report.error ? report : fallbackReport();
      setHealthReport(normalized);
      return normalized;
    } catch (error) {
      if (generation !== reportGeneration || loader !== reportLoader) return null;
      const detail = error?.message ? `: ${error.message}` : '';
      const fallback = fallbackReport(
        `The INI diagnostics could not be completed${detail}`);
      setHealthReport(fallback);
      return fallback;
    } finally {
      if (activeReportLoad === entry) activeReportLoad = null;
    }
  })();
  activeReportLoad = entry;
  return entry.promise;
}

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
