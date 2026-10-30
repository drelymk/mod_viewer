// Deterministic soft PCF, adapted from Three.js r185 (MIT).
// Keep close-up shadow edges smooth without per-pixel sampling noise.

import { Fn, add, fract, ivec2, mix, reference, renderGroup, texture, vec2 } from 'three/tsl';

export const stableShadowFilter = /*@__PURE__*/ Fn(({ depthTexture, shadowCoord, shadow, depthLayer }) => {
  const mapSize = reference('mapSize', 'vec2', shadow).setGroup(renderGroup);
  const texelSize = vec2(1).div(mapSize);
  const fraction = fract(shadowCoord.xy.mul(mapSize).add(0.5)).toConst();
  const uv = shadowCoord.xy.sub(fraction.sub(0.5).mul(texelSize));
  const gatherCompare = (offset) => {
    let samples = texture(depthTexture, uv).offset(offset).gather();
    if (depthTexture.isArrayTexture) samples = samples.depth(depthLayer);
    return samples.compare(shadowCoord.z);
  };
  const topLeft = gatherCompare(ivec2(-1, 1)).toConst();
  const topRight = gatherCompare(ivec2(1, 1)).toConst();
  const bottomLeft = gatherCompare(ivec2(-1, -1)).toConst();
  const bottomRight = gatherCompare(ivec2(1, -1)).toConst();

  return add(
    mix(topLeft.x, topRight.y, fraction.x).add(topLeft.y).add(topRight.x).mul(fraction.y),
    mix(topLeft.w, topRight.z, fraction.x).add(topLeft.z).add(topRight.w),
    mix(bottomLeft.x, bottomRight.y, fraction.x).add(bottomLeft.y).add(bottomRight.x),
    mix(bottomLeft.w, bottomRight.z, fraction.x).add(bottomLeft.z).add(bottomRight.w).mul(fraction.y.oneMinus()),
  ).mul(1 / 9);
});
