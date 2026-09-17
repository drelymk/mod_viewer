// Shared, presentation-only projections for Asset resolver diagnostics.
// Asset identity never participates in mesh keys, texture runs, or saved state.

import { t } from '../i18n/index.js';

const TEXTURE_ROLES = Object.freeze([
  ['diffuse', 'asset.texture.diffuse'],
  ['normal_map', 'asset.texture.normal'],
  ['normal_data', 'asset.texture.normalData'],
  ['light_map', 'asset.texture.lightMap'],
  ['material_map', 'asset.texture.materialMap'],
]);

const PROVENANCE_LABELS = Object.freeze({
  mod_semantic: 'asset.provenance.mod',
  mod_slot_semantic: 'asset.provenance.modSlot',
  mod_slot_legacy: 'asset.provenance.legacySlot',
  mod_texture_hash: 'asset.provenance.modHash',
  asset_original_fallback: 'asset.provenance.fallback',
  unresolved: 'asset.provenance.unresolved',
});
const TEXTURE_ROLE_SOURCE_LABELS = Object.freeze({
  mod_slot_mapping: 'asset.source.modSlot',
  legacy_slot_mapping: 'asset.source.legacySlot',
  dds_analysis: 'asset.source.dds',
});
const TEXTURE_ROLE_LABELS = Object.freeze({
  diffuse: 'asset.texture.diffuse',
  normal_map: 'asset.texture.normal',
  light_map: 'asset.texture.lightMap',
  material_map: 'asset.texture.materialMap',
});

const MATCH_LABELS = Object.freeze({
  unavailable: 'asset.status.unavailable',
  ambiguous: 'asset.status.ambiguous',
  not_found: 'asset.status.notFound',
  exact: 'asset.status.exact',
  partial: 'asset.status.partial',
  unknown: 'asset.status.unknown',
});

function label(key, params) {
  return t(key, params);
}

function numberOrNull(value) {
  return Number.isInteger(value) ? value : null;
}

function bindingOf(value) {
  return value?.asset_binding || value || null;
}

function componentLabel(binding) {
  return binding?.component || (binding?.componentOrdinal !== null
    && binding?.componentOrdinal !== undefined
    ? label('asset.componentOrdinal', {number: binding.componentOrdinal}) : null);
}

export function normalizeAssetBinding(value) {
  const raw = bindingOf(value);
  if (!raw || typeof raw !== 'object' || !raw.status) return null;
  const componentOrdinal = numberOrNull(raw.component_ordinal);
  const component = raw.component_name || null;
  return {
    status: raw.status,
    componentStatus: raw.component_status || null,
    rangeStatus: raw.range_status || null,
    assetType: raw.asset_type || null,
    asset: raw.asset || null,
    component,
    classification: raw.classification || null,
    componentOrdinal,
    geometryHash: raw.geometry_hash || null,
    firstIndex: numberOrNull(raw.first_index),
    indexCount: numberOrNull(raw.index_count),
  };
}

function normalizedBinding(value) {
  return value && Object.hasOwn(value, 'componentStatus')
    ? value : normalizeAssetBinding(value);
}

export function assetMatchLabel(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return label(MATCH_LABELS.unavailable);
  if (normalized.status === 'ambiguous') return label(MATCH_LABELS.ambiguous);
  if (normalized.status === 'not_found') return label(MATCH_LABELS.not_found);
  if (normalized.status === 'exact'
      && normalized.componentStatus === 'exact'
      && normalized.rangeStatus === 'exact') return label(MATCH_LABELS.exact);
  if (normalized.status === 'exact') return label(MATCH_LABELS.partial);
  return label(MATCH_LABELS.unknown);
}

export function bindingMatchKind(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return 'unmatched';
  if (normalized.status === 'exact'
      && normalized.componentStatus === 'exact'
      && normalized.rangeStatus === 'exact') return 'exact';
  if (normalized.status === 'ambiguous') return 'ambiguous';
  if (normalized.status === 'not_found') return 'unmatched';
  return 'partial';
}

export function componentMatchLabel(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return label(MATCH_LABELS.unavailable);
  if (normalized.componentStatus === 'exact') return label(MATCH_LABELS.exact);
  if (normalized.componentStatus === 'ambiguous') return label(MATCH_LABELS.ambiguous);
  if (normalized.componentStatus === 'not_found') return label(MATCH_LABELS.not_found);
  return label(MATCH_LABELS.unknown);
}

export function rangeMatchLabel(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return label(MATCH_LABELS.unavailable);
  if (normalized.rangeStatus === 'exact') return label(MATCH_LABELS.exact);
  if (normalized.rangeStatus === 'ambiguous') return label(MATCH_LABELS.ambiguous);
  return label(MATCH_LABELS.unknown);
}

export function assetDetailLabel(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized || !normalized.asset) return '';
  const displayComponent = componentLabel(normalized);
  const component = displayComponent ? ` · ${displayComponent}` : '';
  const object = normalized.classification ? ` ${normalized.classification}` : '';
  return `${normalized.asset}${component}${object}`;
}

export function assetSecondaryLabel(binding) {
  const detail = assetDetailLabel(binding);
  if (!detail) return '';
  return label('asset.label', {
    value: detail,
  });
}

function identityOf(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized || (!normalized.asset && !normalized.component)) return null;
  return `${normalized.asset || ''}\0${normalized.component || ''}\0${normalized.componentOrdinal ?? ''}`;
}

function rangeOf(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized || normalized.rangeStatus !== 'exact') return null;
  return [normalized.classification, normalized.componentOrdinal,
    normalized.firstIndex, normalized.indexCount].join('\0');
}

export function summarizeAssetBindings(entries = [], resolution = null) {
  const bindings = entries.map(normalizeAssetBinding).filter(Boolean);
  const counts = { exact: 0, partial: 0, ambiguous: 0, unmatched: 0 };
  const indexUnavailable = resolution?.index_status === 'partial'
    || resolution?.index_status === 'unavailable'
    ? entries.filter(entry => !normalizeAssetBinding(entry)).length : 0;
  const identities = new Set();
  const ranges = new Set();
  const assets = new Set();
  bindings.forEach(binding => {
    const kind = bindingMatchKind(binding);
    counts[kind] += 1;
    const identity = identityOf(binding);
    if (identity) identities.add(identity);
    const range = rangeOf(binding);
    if (range) ranges.add(range);
    if (binding.asset) assets.add(binding.asset);
  });
  let status = 'unavailable';
  if (identities.size > 1) status = 'mixed';
  else if (counts.ambiguous) status = 'ambiguous';
  else if (counts.partial || counts.unmatched || indexUnavailable) status = 'partial';
  else if (counts.exact) status = 'exact';
  const first = bindings.find(binding => identityOf(binding));
  return {
    status,
    asset: first?.asset || null,
    component: first?.component || null,
    componentOrdinal: first?.componentOrdinal ?? null,
    rangesVary: ranges.size > 1,
    assets: [...assets].sort(),
    totalDraws: entries.length,
    matchedDraws: counts.exact + counts.partial,
    indexUnavailable,
    ...counts,
  };
}

export function textureProvenance(value) {
  const raw = value?.texture_resolution || value || {};
  return Object.fromEntries(TEXTURE_ROLES.map(([role]) => [
    role, label(PROVENANCE_LABELS[raw[role]] || PROVENANCE_LABELS.unresolved),
  ]));
}

export function textureRoleLabels() {
  return TEXTURE_ROLES.map(([role, key]) => [role, label(key)]);
}

export function textureRoleLabel(role) {
  return label(TEXTURE_ROLE_LABELS[role] || MATCH_LABELS.unknown);
}

export function textureRoleSourceLabel(source) {
  return label(TEXTURE_ROLE_SOURCE_LABELS[source]
    || PROVENANCE_LABELS[source]
    || MATCH_LABELS.unknown);
}

export function provenanceLabel(value) {
  return label(PROVENANCE_LABELS[value] || PROVENANCE_LABELS.unresolved);
}

export function assetResolutionLabel(summary) {
  if (!summary || summary.index_status === 'not_configured') return '';
  if (summary.index_status === 'unavailable') return label('asset.indexUnavailable');
  const total = Number(summary.total_draws) || 0;
  const exact = Number(summary.exact_draws) || 0;
  return label('asset.drawsExact', {exact, total});
}

export function assetSummaryLabel(summary) {
  if (!summary || summary.status === 'unavailable') return '';
  if (summary.status === 'mixed') return label('asset.summaryMixed');
  if (summary.status === 'ambiguous') return label('asset.summaryAmbiguous');
  if (summary.status === 'partial' && !summary.asset) return label('asset.summaryPartial');
  if (!summary.asset) return '';
  const displayComponent = summary.component || (summary.componentOrdinal !== null
    && summary.componentOrdinal !== undefined
    ? label('asset.componentOrdinal', {number: summary.componentOrdinal}) : null);
  const component = displayComponent ? ` · ${displayComponent}` : '';
  const ranges = summary.rangesVary ? ` · ${label('asset.rangesVary')}` : '';
  const partial = summary.status === 'partial'
    ? ` · ${label('asset.partial')}` : '';
  return label('asset.label', {
    value: `${summary.asset}${component}${ranges}${partial}`,
  });
}
