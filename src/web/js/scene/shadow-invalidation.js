// Lightweight bridge so animation playback does not import the DOM-bound
// scene module just to invalidate the character shadow map.

let invalidateMap = () => {};

export function setCharacterShadowMapInvalidator(callback) {
  invalidateMap = typeof callback === 'function' ? callback : () => {};
}

export function invalidateCharacterShadowMap(options) {
  invalidateMap(options);
}
