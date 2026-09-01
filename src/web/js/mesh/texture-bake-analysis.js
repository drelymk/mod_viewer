// Read-only selected-mesh texture coverage requests.

import { viewerState, samePath } from '../app/state.js';
import { activeMeshes } from './mesh-state.js';
import { isAssetTextureKey, splitTextureKey } from '../textures/texture-key.js';

let requestToken = 0;

const REASON_TEXT = Object.freeze({
  'asset-texture': 'Asset textures are read-only.',
  'no-diffuse': 'Select a diffuse texture before analyzing coverage.',
  'not-diffuse': 'Texture coverage requires a diffuse texture.',
  'different-mod': 'The selected mesh belongs to a different mod.',
  'unsupported-texture-type': 'Texture coverage currently requires a DDS source.',
});

function reasonText(reason) {
  return REASON_TEXT[reason] || 'Texture coverage is unavailable for this mesh.';
}

/** Return the current normal-mesh texture identities used by the backend. */
export function buildTextureUsageSnapshot() {
  return activeMeshes
    .filter(mesh => mesh?.userData?.assetFill !== true)
    .map(mesh => ({
      semantic_key: mesh.userData?.semanticKey || '',
      tex_key: mesh.userData?.texKey || null,
    }));
}

/** Check the selected mesh without touching the backend or material state. */
export function canAnalyzeTextureBake(mesh) {
  const data = mesh?.userData;
  const key = data?.texKey;
  const parsed = splitTextureKey(key);
  if (data?.assetFill === true || isAssetTextureKey(key)) {
    return { editable: false, reason: 'asset-texture', message: reasonText('asset-texture') };
  }
  if (!parsed) {
    return { editable: false, reason: 'no-diffuse', message: reasonText('no-diffuse') };
  }
  if (parsed.role !== 'diffuse') {
    return { editable: false, reason: 'not-diffuse', message: reasonText('not-diffuse') };
  }
  if (!viewerState.currentModPath || !samePath(data.modPath, viewerState.currentModPath)) {
    return { editable: false, reason: 'different-mod', message: reasonText('different-mod') };
  }
  if (!parsed.path.toLowerCase().endsWith('.dds')) {
    return {
      editable: false,
      reason: 'unsupported-texture-type',
      message: reasonText('unsupported-texture-type'),
    };
  }
  return { editable: true, reason: null, message: '' };
}

function currentSelectionStillMatches(mesh, semanticKey, texKey, modPath, isCurrent) {
  if (typeof isCurrent === 'function' && !isCurrent()) return false;
  return activeMeshes.includes(mesh)
    && mesh.userData?.semanticKey === semanticKey
    && (mesh.userData?.texKey || null) === (texKey || null)
    && samePath(mesh.userData?.modPath, modPath)
    && samePath(viewerState.currentModPath, modPath);
}

/**
 * Request selected-mesh coverage using only semantic texture identities.
 * A stale response is discarded and returned as null.
 */
export async function analyzeMeshTextureBake(mesh, { isCurrent } = {}) {
  const token = ++requestToken;
  const eligibility = canAnalyzeTextureBake(mesh);
  if (!eligibility.editable) {
    return {
      status: 'unsupported',
      code: eligibility.reason,
      error: eligibility.message,
    };
  }

  const semanticKey = mesh.userData.semanticKey;
  const texKey = mesh.userData.texKey || null;
  const modPath = viewerState.currentModPath;
  const api = window.pywebview?.api?.analyze_mesh_texture_bake;
  if (typeof api !== 'function') {
    return {
      status: 'error',
      code: 'analysis_unavailable',
      error: 'Texture coverage analysis is unavailable.',
    };
  }
  const textureUsage = buildTextureUsageSnapshot();
  const result = await api(modPath, semanticKey, texKey, textureUsage);
  if (token !== requestToken || !currentSelectionStillMatches(
      mesh, semanticKey, texKey, modPath, isCurrent)) {
    return null;
  }
  return result;
}

/** Convert the stable backend schema into modal-friendly text. */
export function formatBakeAnalysis(result, displayName = key => key) {
  if (!result) return null;
  if (result.status !== 'ok') {
    return {
      kind: result.status === 'unsupported' ? 'unsupported' : 'error',
      title: result.status === 'unsupported' ? 'Texture Coverage Unavailable' : 'Texture Coverage Failed',
      summary: result.error || 'Texture coverage could not be analyzed safely.',
      warning: '',
      rows: [],
    };
  }
  const coverage = result.coverage || {};
  const uniqueUnits = coverage.unique_units == null
    ? 'Unknown'
    : `${coverage.unique_units} ${coverage.unit || 'units'}`;
  const rows = [
    ['Texture', result.texture?.file || 'Unknown'],
    ['Format', result.texture?.format || 'Unknown'],
    ['Texture size', `${result.texture?.width || 0} × ${result.texture?.height || 0}`],
    ['Selected coverage', `${coverage.selected_percent?.toFixed?.(1) ?? 0}% (${coverage.selected_units || 0} ${coverage.unit || 'units'})`],
    ['Unique coverage', uniqueUnits],
    ['Shared coverage', `${coverage.shared_percent_of_selected?.toFixed?.(1) ?? 0}% (${coverage.shared_units || 0} ${coverage.unit || 'units'})`],
  ];
  const shared = (result.shared_with || []).map(item =>
    `${displayName(item.semantic_key)} (${item.shared_units} ${coverage.unit || 'units'})`);
  const unresolved = (result.unresolved_consumers || []).map(displayName);
  let warning = '';
  if (result.safety === 'unknown') {
    warning = unresolved.length
      ? `Safety is unknown because coverage could not be analyzed for: ${unresolved.join(', ')}.`
      : 'Safety is unknown because one or more consumers could not be analyzed.';
  } else if (result.safety === 'shared') {
    warning = shared.length
      ? `Coverage overlaps: ${shared.join(', ')}.`
      : 'Coverage overlaps another active draw using this texture.';
  }
  return {
    kind: result.safety === 'unknown' ? 'unknown' : 'ok',
    title: result.safety === 'safe' ? 'Texture Coverage Is Unique' : 'Texture Coverage Results',
    summary: `${coverage.selected_percent?.toFixed?.(1) ?? 0}% of the texture is touched by this mesh.`,
    warning,
    rows,
  };
}

export function cancelTextureBakeAnalysis() {
  requestToken += 1;
}
