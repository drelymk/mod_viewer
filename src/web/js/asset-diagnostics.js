// Shared, presentation-only projections for Asset resolver diagnostics.
// Asset identity never participates in mesh keys, texture runs, or saved state.

const TEXTURE_ROLES = Object.freeze([
  ['diffuse', 'Diffuse'],
  ['normal_map', 'Normal'],
  ['normal_data', 'Normal data'],
  ['light_map', 'Light map'],
  ['material_map', 'Material map'],
]);

const PROVENANCE_LABELS = Object.freeze({
  mod_semantic: 'Mod',
  mod_texture_hash: 'Mod hash match',
  asset_original_fallback: 'Asset fallback',
  unresolved: 'Not resolved',
});

function numberOrNull(value) {
  return Number.isInteger(value) ? value : null;
}

function bindingOf(value) {
  return value?.asset_binding || value || null;
}

export function normalizeAssetBinding(value) {
  const raw = bindingOf(value);
  if (!raw || typeof raw !== 'object' || !raw.status) return null;
  const componentOrdinal = numberOrNull(raw.component_ordinal);
  const component = raw.component_name || (componentOrdinal !== null
    ? `Component ${componentOrdinal}` : null);
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
  if (!normalized) return 'Unavailable';
  if (normalized.status === 'ambiguous') return 'Ambiguous';
  if (normalized.status === 'not_found') return 'Not found';
  if (normalized.status === 'exact'
      && normalized.componentStatus === 'exact'
      && normalized.rangeStatus === 'exact') return 'Exact';
  if (normalized.status === 'exact') return 'Partial';
  return normalized.status.replace(/_/g, ' ');
}

export function bindingMatchKind(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return 'unmatched';
  if (assetMatchLabel(normalized) === 'Exact') return 'exact';
  if (normalized.status === 'ambiguous') return 'ambiguous';
  if (normalized.status === 'not_found') return 'unmatched';
  return 'partial';
}

export function componentMatchLabel(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return 'Unavailable';
  if (normalized.componentStatus === 'exact') return 'Exact';
  if (normalized.componentStatus === 'ambiguous') return 'Ambiguous';
  if (normalized.componentStatus === 'not_found') return 'Not found';
  return normalized.componentStatus || 'Unknown';
}

export function rangeMatchLabel(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return 'Unavailable';
  if (normalized.rangeStatus === 'exact') return 'Exact';
  if (normalized.rangeStatus === 'ambiguous') return 'Ambiguous';
  if (normalized.rangeStatus === 'unknown') return 'Unknown';
  return normalized.rangeStatus || 'Unknown';
}

export function assetSecondaryLabel(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized || !normalized.asset) return '';
  const component = normalized.component ? ` · ${normalized.component}` : '';
  const object = normalized.classification ? ` ${normalized.classification}` : '';
  return `Asset: ${normalized.asset}${component}${object}`;
}

function identityOf(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return null;
  return `${normalized.asset || ''}\0${normalized.component || ''}`;
}

function rangeOf(binding) {
  const normalized = normalizedBinding(binding);
  if (!normalized) return null;
  return [normalized.classification, normalized.componentOrdinal,
    normalized.firstIndex, normalized.indexCount].join('\0');
}

export function summarizeAssetBindings(entries = []) {
  const bindings = entries.map(normalizeAssetBinding).filter(Boolean);
  const counts = { exact: 0, partial: 0, ambiguous: 0, unmatched: 0 };
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
  else if (counts.partial || counts.unmatched) status = 'partial';
  else if (counts.exact) status = 'exact';
  const first = bindings.find(binding => identityOf(binding));
  return {
    status,
    asset: first?.asset || null,
    component: first?.component || null,
    rangesVary: ranges.size > 1,
    assets: [...assets].sort(),
    totalDraws: entries.length,
    matchedDraws: counts.exact + counts.partial,
    ...counts,
  };
}

export function textureProvenance(value) {
  const raw = value?.texture_resolution || value || {};
  return Object.fromEntries(TEXTURE_ROLES.map(([role]) => [
    role, PROVENANCE_LABELS[raw[role]] || PROVENANCE_LABELS.unresolved,
  ]));
}

export function textureRoleLabels() {
  return TEXTURE_ROLES;
}

export function provenanceLabel(value) {
  return PROVENANCE_LABELS[value] || PROVENANCE_LABELS.unresolved;
}

export function assetResolutionLabel(summary) {
  if (!summary || summary.index_status === 'not_configured') return '';
  if (summary.index_status === 'unavailable') return 'Index unavailable';
  const total = Number(summary.total_draws) || 0;
  const exact = Number(summary.exact_draws) || 0;
  return `${exact} / ${total} draws exact`;
}

export function assetSummaryLabel(summary) {
  if (!summary || summary.status === 'unavailable') return '';
  if (summary.status === 'mixed') return 'Asset: Mixed';
  if (summary.status === 'ambiguous') return 'Asset: Ambiguous';
  if (summary.status === 'partial' && !summary.asset) return 'Asset: Partial';
  if (!summary.asset) return '';
  const component = summary.component ? ` · ${summary.component}` : '';
  const ranges = summary.rangesVary ? ' · ranges vary' : '';
  return `Asset: ${summary.asset}${component}${ranges}`;
}
