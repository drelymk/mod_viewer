// Canonical role-aware texture keys shared by the browser texture paths.

export const TEXTURE_ROLES = new Set([
  'diffuse',
  'normal_map',
  'normal_data',
  'light_map',
  'material_map',
  'emission_map',
]);

export function splitTextureKey(key) {
  if (typeof key !== 'string') return null;
  const index = key.indexOf('::');
  if (index <= 0) return null;
  const role = key.slice(0, index);
  const path = key.slice(index + 2);
  if (!TEXTURE_ROLES.has(role) || !path) return null;
  return { role, path };
}

export function textureRole(key) {
  return splitTextureKey(key)?.role || null;
}

export function textureFile(key) {
  return splitTextureKey(key)?.path || '';
}

/** Return whether a canonical role-aware key names an Asset texture. */
export function isAssetTextureKey(key) {
  const parsed = splitTextureKey(key);
  return !!parsed?.path.startsWith('asset/');
}
