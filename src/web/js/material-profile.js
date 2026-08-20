// Packed material interpretation for the pinned Three.js r165 renderer.
// Texture roles stay intact; this adapter samples their channels directly in
// the built-in physical shader instead of creating one derived texture per
// semantic channel.

import {
  DoubleSide, MeshPhysicalMaterial, MeshStandardMaterial, ShaderChunk,
} from 'three';

const SHADER_VERSION = 'r165-packed-material-v2';
const SOURCE_INFO = Object.freeze({
  normal_data: { uniform: 'gameNormalData', enabled: 'gameNormalDataEnabled', sample: 'gameNormalDataSample' },
  light_map: { uniform: 'gameLightMap', enabled: 'gameLightMapEnabled', sample: 'gameLightMapSample' },
  material_map: { uniform: 'gameMaterialMap', enabled: 'gameMaterialMapEnabled', sample: 'gameMaterialMapSample' },
});
const CHANNELS = new Set(['r', 'g', 'b', 'a']);

const PACKED_UNIFORM_DECLARATIONS = `
uniform sampler2D gameNormalData;
uniform sampler2D gameLightMap;
uniform sampler2D gameMaterialMap;
uniform bool gameNormalDataEnabled;
uniform bool gameLightMapEnabled;
uniform bool gameMaterialMapEnabled;
uniform float gameMaterialMetalnessScale;
uniform float gameMaterialSpecularScale;
uniform float gameMaterialSpecularInfluence;
uniform float gameToonShadowThreshold;
uniform float gameToonShadowSoftness;
uniform float gameToonShadowMaskStrength;
uniform float gameToonShadowInfluence;
`;

const TOON_SHADOW_FUNCTIONS = `
// The channel is validated; this boundary is a viewer approximation of the
// game response, not a literal reproduction of the source shader equation.
float gameToonShadow(
    float ndotl,
    float authoredMask,
    float threshold,
    float softness
) {
    float lightValue = ndotl * 0.5 + 0.5;
    float boundary = lightValue
        + ( authoredMask - 0.5 )
        * clamp( gameToonShadowMaskStrength, 0.0, 1.0 );
    float edge = max( softness, 0.0001 );
    return smoothstep( threshold - edge, threshold + edge, boundary );
}
`;

function requiredReplace(source, marker, replacement, label) {
  if (!source.includes(marker)) {
    throw new Error(`Packed material shader marker missing in Three.js r165: ${label}`);
  }
  return source.replace(marker, replacement);
}

function validRef(ref) {
  return !!ref && SOURCE_INFO[ref.source] && CHANNELS.has(ref.channel);
}

function hasNumericValue(value) {
  return value != null && Number.isFinite(Number(value));
}

function refExpression(ref) {
  if (!validRef(ref)) return null;
  const sample = SOURCE_INFO[ref.source].sample;
  const channel = `${sample}.${ref.channel}`;
  return ref.invert ? `( 1.0 - ${channel} )` : channel;
}

function profileSources(profile) {
  return [profile.shadow_mask, profile.metalness, profile.specular]
    .filter(validRef)
    .map(ref => ref.source)
    .filter((source, index, all) => all.indexOf(source) === index);
}

function packedSampleCode(profile) {
  const sources = profileSources(profile);
  return sources.map(source => {
    const info = SOURCE_INFO[source];
    return `
vec4 ${info.sample} = vec4( 0.0 );
if ( ${info.enabled} ) {
	${info.sample} = texture2D( ${info.uniform}, vUv );
}`;
  }).join('\n');
}

function responseCode(profile) {
  const metalness = validRef(profile.metalness)
    ? (() => {
      const info = SOURCE_INFO[profile.metalness.source];
      return `
if ( ${info.enabled} ) {
	gameMaterialMetalnessResponse = clamp(
		${refExpression(profile.metalness)} * gameMaterialMetalnessScale,
		0.0, 1.0 );
}`;
    })() : '';
  const specular = validRef(profile.specular)
    ? (() => {
      const info = SOURCE_INFO[profile.specular.source];
      const sample = refExpression(profile.specular);
      const hasInfluence = hasNumericValue(profile.specular_influence);
      const response = hasInfluence
        ? `mix(
		1.0,
		clamp( ${sample} * gameMaterialSpecularScale, 0.0, 1.0 ),
		clamp( gameMaterialSpecularInfluence, 0.0, 1.0 ) )`
        : `clamp(
		${sample} * gameMaterialSpecularScale,
		0.0, 1.0 )`;
      return `
if ( ${info.enabled} ) {
	gameMaterialSpecularResponse = ${response};
}`;
    })() : '';
  const shadow = validRef(profile.shadow_mask)
    ? (() => {
      const info = SOURCE_INFO[profile.shadow_mask.source];
      const sample = refExpression(profile.shadow_mask);
      return [
        'if ( ' + info.enabled + ' ) {',
        '\tgameToonShadowMask = ' + sample + ';',
        '\tgameToonShadowEnabled = true;',
        '}',
      ].join('\n');
    })() : '';
  return {
    metalness,
    specular,
    declarations: `
float gameMaterialMetalnessResponse = 0.0;
float gameMaterialSpecularResponse = 1.0;
bool gameToonShadowEnabled = false;
float gameToonShadowMask = 0.5;
${metalness}
${specular}
${shadow}
`,
  };
}

function patchDirectionalToonShadow(source) {
  const directionalMarker = '#if ( NUM_DIR_LIGHTS > 0 ) && defined( RE_Direct )';
  const directCall =
    '\t\tRE_Direct( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );';
  const sectionStart = source.indexOf(directionalMarker);
  if (sectionStart < 0) {
    throw new Error(
      'Packed material shader marker missing in Three.js r165: directional lights');
  }
  const callStart = source.indexOf(directCall, sectionStart);
  if (callStart < 0) {
    throw new Error(
      'Packed material shader marker missing in Three.js r165: directional RE_Direct');
  }
  const replacement = [
    '\t\t{',
    '\t\t\tvec3 gameDirectDiffuseBefore = reflectedLight.directDiffuse;',
    '\t\t\t' + directCall.trim(),
    '\t\t\tif ( gameToonShadowEnabled ) {',
    '\t\t\t\tfloat gameToonShadowFactor = gameToonShadow(',
    '\t\t\t\t\tsaturate( dot( geometryNormal, directLight.direction ) ),',
    '\t\t\t\t\tgameToonShadowMask,',
    '\t\t\t\t\tgameToonShadowThreshold,',
    '\t\t\t\t\tgameToonShadowSoftness',
    '\t\t\t\t);',
    '\t\t\t\tgameToonShadowFactor = mix(',
    '\t\t\t\t\t1.0,',
    '\t\t\t\t\tgameToonShadowFactor,',
    '\t\t\t\t\tclamp( gameToonShadowInfluence, 0.0, 1.0 )',
    '\t\t\t\t);',
    '\t\t\t\treflectedLight.directDiffuse = mix(',
    '\t\t\t\t\tgameDirectDiffuseBefore,',
    '\t\t\t\t\treflectedLight.directDiffuse,',
    '\t\t\t\t\tgameToonShadowFactor',
    '\t\t\t\t);',
    '\t\t\t}',
    '\t\t}',
  ].join('\n');
  return source.slice(0, callStart) + replacement
    + source.slice(callStart + directCall.length);
}

function patchPackedMaterialShader(shader, profile) {
  let fragment = shader.fragmentShader;
  const toonShadowFunctions = validRef(profile.shadow_mask)
    ? TOON_SHADOW_FUNCTIONS : '';
  fragment = requiredReplace(
    fragment,
    '#include <uv_pars_fragment>',
    `#include <uv_pars_fragment>\n${PACKED_UNIFORM_DECLARATIONS}\n${toonShadowFunctions}`,
    'uv_pars_fragment');

  const responses = responseCode(profile);
  fragment = requiredReplace(
    fragment,
    '#include <roughnessmap_fragment>',
    `${packedSampleCode(profile)}\n${responses.declarations}\n#include <roughnessmap_fragment>`,
    'roughnessmap_fragment');

  if (validRef(profile.metalness)) {
    const info = SOURCE_INFO[profile.metalness.source];
    fragment = requiredReplace(
      fragment,
      '#include <metalnessmap_fragment>',
      `#include <metalnessmap_fragment>\nif ( ${info.enabled} ) {\n\tmetalnessFactor = gameMaterialMetalnessResponse;\n}`,
      'metalnessmap_fragment');
  }

  let physicalLights = ShaderChunk.lights_physical_fragment;
  if (!physicalLights) {
    throw new Error('Three.js r165 physical lighting shader is unavailable.');
  }
  if (validRef(profile.specular)) {
    physicalLights = requiredReplace(
      physicalLights,
      'float specularIntensityFactor = specularIntensity;',
      'float specularIntensityFactor = specularIntensity * gameMaterialSpecularResponse;',
      'lights_physical_fragment.specularIntensityFactor');
  }
  if (validRef(profile.shadow_mask)) {
    let lightsBegin = ShaderChunk.lights_fragment_begin;
    if (!lightsBegin) {
      throw new Error('Three.js r165 directional lighting shader is unavailable.');
    }
    lightsBegin = patchDirectionalToonShadow(lightsBegin);
    fragment = requiredReplace(
      fragment,
      '#include <lights_fragment_begin>',
      lightsBegin,
      'lights_fragment_begin');
  }
  fragment = requiredReplace(
    fragment,
    '#include <lights_physical_fragment>',
    physicalLights,
    'lights_physical_fragment');
  shader.fragmentShader = fragment;
}

function declarePackedUniforms(shader) {
  shader.fragmentShader = requiredReplace(
    shader.fragmentShader,
    '#include <common>',
    `#include <common>\n${PACKED_UNIFORM_DECLARATIONS}`,
    'common');
}

function hasPackedResponse(profile) {
  return validRef(profile?.shadow_mask)
    || validRef(profile?.metalness)
    || validRef(profile?.specular);
}

function materialUniforms(profile) {
  return {
    gameNormalData: { value: null },
    gameLightMap: { value: null },
    gameMaterialMap: { value: null },
    gameNormalDataEnabled: { value: false },
    gameLightMapEnabled: { value: false },
    gameMaterialMapEnabled: { value: false },
    gameMaterialMetalnessScale: {
      value: Number.isFinite(Number(profile?.metalness_scale))
        ? Number(profile.metalness_scale) : 1,
    },
    gameMaterialSpecularScale: {
      value: Number.isFinite(Number(profile?.specular_scale))
        ? Number(profile.specular_scale) : 1,
    },
    gameMaterialSpecularInfluence: {
      value: hasNumericValue(profile?.specular_influence)
        ? Number(profile.specular_influence) : 0,
    },
    gameToonShadowThreshold: { value: 0.5 },
    gameToonShadowSoftness: { value: 0.08 },
    gameToonShadowMaskStrength: { value: 0.5 },
    gameToonShadowInfluence: { value: 1.0 },
  };
}

/** Create the stock or physical material appropriate for one profile. */
export function createGameMaterial(profile, fallbackColor, options = {}) {
  const packedResponse = hasPackedResponse(profile) && options.hasUv !== false;
  const materialOptions = {
    side: DoubleSide,
    roughness: 1.0,
    metalness: 0.0,
    color: fallbackColor,
  };
  const material = packedResponse
    ? new MeshPhysicalMaterial({ ...materialOptions, specularIntensity: 1.0 })
    : new MeshStandardMaterial(materialOptions);
  configureGameMaterial(material, profile, { packedResponse });
  return material;
}

/** Attach a stable profile-specific shader adapter to a material. */
export function configureGameMaterial(material, profile, options = {}) {
  if (!profile || !profile.id || profile.id === 'none') return material;
  const uniforms = materialUniforms(profile);
  const packedResponse = options.packedResponse ?? hasPackedResponse(profile);
  const state = { profile, uniforms, shader: null, packedResponse };
  material.userData.gameMaterial = state;
  if (packedResponse) {
    // The packed maps use the same primary UV set as the authored diffuse.
    // Force the built-in UV varying even when a fixture has no diffuse map.
    material.defines = { ...(material.defines || {}), USE_UV: '' };
  }
  material.customProgramCacheKey = () =>
    `mod-viewer:${SHADER_VERSION}:${profile.id}`;
  material.onBeforeCompile = shader => {
    state.shader = shader;
    Object.assign(shader.uniforms, uniforms);
    if (state.packedResponse) patchPackedMaterialShader(shader, profile);
    else declarePackedUniforms(shader);
  };
  return material;
}

/** Update packed texture uniforms without invalidating/recompiling the shader. */
export function updateGameMaterialTextures(mesh, maps) {
  const state = mesh.material?.userData?.gameMaterial;
  if (!state) return false;
  const values = {
    gameNormalData: maps.normal_data || null,
    gameLightMap: maps.light_map || null,
    gameMaterialMap: maps.material_map || null,
  };
  let changed = false;
  for (const [name, value] of Object.entries(values)) {
    if (state.uniforms[name].value !== value) changed = true;
    state.uniforms[name].value = value;
  }
  const enabled = {
    gameNormalDataEnabled: !!values.gameNormalData,
    gameLightMapEnabled: !!values.gameLightMap,
    gameMaterialMapEnabled: !!values.gameMaterialMap,
  };
  for (const [name, value] of Object.entries(enabled)) {
    if (state.uniforms[name].value !== value) changed = true;
    state.uniforms[name].value = value;
  }
  return changed;
}

/** Return the packed roles sampled by this material's current shader. */
export function getGameMaterialSources(material) {
  const state = material?.userData?.gameMaterial;
  if (!state?.packedResponse) return new Set();
  return new Set(profileSources(state.profile));
}

/** Release adapter-side references before the owning material is disposed. */
export function disposeGameMaterial(material) {
  const state = material?.userData?.gameMaterial;
  if (!state) return;
  for (const uniform of Object.values(state.uniforms)) uniform.value = null;
  state.shader = null;
  delete material.userData.gameMaterial;
}
