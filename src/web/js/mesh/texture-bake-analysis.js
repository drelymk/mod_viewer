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
    .map(mesh => {
      const data = mesh.userData || {};
      const textureKeys = {
        diffuse: data.texKey || null,
        normal_map: data.normalMapKey || null,
        normal_data: data.normalDataKey || null,
        light_map: data.lightMapKey || null,
        material_map: data.materialMapKey || null,
        emission_map: data.emissionMapKey || null,
      };
      return {
        semantic_key: data.semanticKey || '',
        // Keep the legacy alias for callers that only consume diffuse usage.
        tex_key: textureKeys.diffuse,
        texture_keys: textureKeys,
      };
    });
}

/** Capture every identity and role binding used by a bake request. */
export function captureTextureBakeState(mesh) {
  const data = mesh?.userData || {};
  return {
    modPath: viewerState.currentModPath,
    semanticKey: data.semanticKey || null,
    metadataKey: data.metadataKey || null,
    texKey: data.texKey || null,
    textureUsage: buildTextureUsageSnapshot(),
  };
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

export function textureBakeStateMatches(mesh, snapshot) {
  if (!snapshot) return false;
  const current = captureTextureBakeState(mesh);
  return samePath(current.modPath, snapshot.modPath)
    && current.semanticKey === snapshot.semanticKey
    && current.metadataKey === snapshot.metadataKey
    && current.texKey === snapshot.texKey
    && JSON.stringify(current.textureUsage)
      === JSON.stringify(snapshot.textureUsage)
    && activeMeshes.includes(mesh);
}

function currentSelectionStillMatches(mesh, snapshot, isCurrent) {
  if (typeof isCurrent === 'function' && !isCurrent()) return false;
  return textureBakeStateMatches(mesh, snapshot);
}

/**
 * Request selected-mesh coverage using only semantic texture identities.
 * A stale response is discarded and returned as null.
 */
export async function analyzeMeshTextureBake(mesh, { isCurrent, snapshot } = {}) {
  const token = ++requestToken;
  const eligibility = canAnalyzeTextureBake(mesh);
  if (!eligibility.editable) {
    return {
      status: 'unsupported',
      code: eligibility.reason,
      error: eligibility.message,
    };
  }

  const state = snapshot || captureTextureBakeState(mesh);
  const api = window.pywebview?.api?.analyze_mesh_texture_bake;
  if (typeof api !== 'function') {
    return {
      status: 'error',
      code: 'analysis_unavailable',
      error: 'Texture coverage analysis is unavailable.',
    };
  }
  const result = await api(
    state.modPath, state.semanticKey, state.texKey, state.textureUsage);
  if (token !== requestToken || !currentSelectionStillMatches(
      mesh, state, isCurrent)) {
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
    ['Mip levels', result.mip_summary?.levels || result.texture?.mip_count || 1],
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
    warning = 'Only unique texture blocks will be baked. Shared blocks will '
      + 'remain unchanged. The live Color adjustment will be reset after a '
      + 'successful bake, so shared areas may return to their original '
      + 'texture color.';
    if (shared.length) warning += ` Shared with: ${shared.join(', ')}.`;
    const levels = result.mip_summary?.shared_levels || [];
    if (levels.length) {
      warning += ` Some mip levels contain shared blocks, so appearance may `
        + `differ at farther viewing distances (levels ${levels.join(', ')}).`;
    }
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
