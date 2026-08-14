// Read-only INI/resource diagnostics. The backend intentionally returns this
// report even when geometry cannot load, so this module has no scene dependency.

import { openIniEditor } from './ini-editor.js';

const $ = (id) => document.getElementById(id);

let currentReport = null;
let currentFilter = 'all';

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

function renderReport() {
  const report = currentReport || { summary: {}, files: {}, issues: [] };
  const summary = report.summary || {};
  const files = report.files || {};
  $('health-summary').textContent =
    `${summary.errors || 0} errors · ${summary.warnings || 0} warnings · ` +
    `${files.referenced || 0} referenced assets · ${files.inactive_only || 0} inactive-only · ` +
    `${files.viewer_only || 0} viewer-only`;

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

export function setHealthReport(report) {
  currentReport = report || null;
  const button = $('health-btn');
  const count = report?.summary?.issues || 0;
  const errors = report?.summary?.errors || 0;
  button.disabled = !report;
  button.classList.toggle('healthy', !!report && count === 0);
  button.classList.toggle('warning', !!report && count > 0 && errors === 0);
  button.classList.toggle('error', errors > 0);
  $('health-count').textContent = String(count);
  button.title = !report ? 'Open a mod to run INI diagnostics'
    : count ? `${count} INI diagnostic issue${count === 1 ? '' : 's'}`
      : 'No INI issues found';
  if ($('health-modal-backdrop').classList.contains('show')) renderReport();
}

function openReport() {
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
$('health-close').addEventListener('click', closeReport);
$('health-close-x').addEventListener('click', closeReport);
$('health-filters').addEventListener('click', (event) => {
  const button = event.target.closest('[data-health-filter]');
  if (!button) return;
  currentFilter = button.dataset.healthFilter;
  for (const candidate of $('health-filters').querySelectorAll('button')) {
    candidate.classList.toggle('active', candidate === button);
  }
  renderReport();
});
$('health-modal-backdrop').addEventListener('click', (event) => {
  if (event.target.id === 'health-modal-backdrop') closeReport();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && $('health-modal-backdrop').classList.contains('show')) {
    closeReport();
  }
});
