// Lightweight bridge so animation playback does not import the DOM-bound
// scene module just to invalidate the character shadow map.

let invalidateMap = () => {};
let invalidateGeometry = () => {};

export function setCharacterShadowMapInvalidator(callback) {
  invalidateMap = typeof callback === 'function' ? callback : () => {};
}

export function setCharacterShadowGeometryInvalidator(callback) {
  invalidateGeometry = typeof callback === 'function' ? callback : () => {};
}

export function invalidateCharacterShadowMap(options) {
  invalidateMap(options);
}

export function invalidateCharacterShadowGeometry(options) {
  invalidateGeometry(options);
}
