// Packed material interpretation for the pinned Three.js r165 renderer.
// Texture roles stay intact; this adapter samples their channels directly in
// the built-in physical shader instead of creating one derived texture per
// semantic channel.

import {
  DoubleSide, MeshPhysicalMaterial, MeshStandardMaterial, ShaderChunk,
} from 'three';

const SHADER_VERSION = 'r165-packed-material-v1';
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

function refExpression(ref) {
  if (!validRef(ref)) return null;
  const sample = SOURCE_INFO[ref.source].sample;
  const channel = `${sample}.${ref.channel}`;
  return ref.invert ? `( 1.0 - ${channel} )` : channel;
}

function profileSources(profile) {
  return [profile.metalness, profile.specular]
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
      return `
if ( ${info.enabled} ) {
	gameMaterialSpecularResponse = clamp(
		${refExpression(profile.specular)} * gameMaterialSpecularScale,
		0.0, 1.0 );
}`;
    })() : '';
  return {
    metalness,
    specular,
    declarations: `
float gameMaterialMetalnessResponse = 0.0;
float gameMaterialSpecularResponse = 1.0;
${metalness}
${specular}
`,
  };
}

function patchPackedMaterialShader(shader, profile) {
  let fragment = shader.fragmentShader;
  fragment = requiredReplace(
    fragment,
    '#include <uv_pars_fragment>',
    `#include <uv_pars_fragment>\n${PACKED_UNIFORM_DECLARATIONS}`,
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
  return validRef(profile?.metalness) || validRef(profile?.specular);
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

/** Release adapter-side references before the owning material is disposed. */
export function disposeGameMaterial(material) {
  const state = material?.userData?.gameMaterial;
  if (!state) return;
  for (const uniform of Object.values(state.uniforms)) uniform.value = null;
  state.shader = null;
  delete material.userData.gameMaterial;
}
