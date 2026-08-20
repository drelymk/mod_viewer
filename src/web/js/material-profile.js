// Game-specific material interpretation for the pinned Three.js WebGPU
// renderer. Packed textures remain intact: their authored channels are read
// through stable TSL nodes and their bindings are changed in place.

import {
  DataTexture,
  DoubleSide,
  MeshPhysicalNodeMaterial,
  MeshStandardNodeMaterial,
  NoColorSpace,
  PhysicalLightingModel,
  RGBAFormat,
  SRGBColorSpace,
  UnsignedByteType,
  Vector2,
} from 'three/webgpu';
import {
  clamp,
  color,
  float,
  mix,
  normalMap,
  normalView,
  normalViewGeometry,
  smoothstep,
  texture,
  uniform,
  uv,
} from 'three/tsl';

const SOURCE_INFO = Object.freeze({
  normal_data: true,
  light_map: true,
  material_map: true,
});
const CHANNELS = new Set(['r', 'g', 'b', 'a']);

function createPlaceholder(name, bytes, colorSpace) {
  const result = new DataTexture(
    new Uint8Array(bytes), 1, 1, RGBAFormat, UnsignedByteType);
  result.name = `mod-viewer-${name}-placeholder`;
  result.colorSpace = colorSpace;
  result.needsUpdate = true;
  return result;
}

// These textures are deliberately shared. A disabled binding still needs a
// valid texture object so changing a role does not introduce a new graph or
// force a material/pipeline rebuild.
const DIFFUSE_PLACEHOLDER = createPlaceholder(
  'diffuse', [255, 255, 255, 255], SRGBColorSpace);
const NORMAL_PLACEHOLDER = createPlaceholder(
  'normal', [128, 128, 255, 255], NoColorSpace);
const PACKED_PLACEHOLDER = createPlaceholder(
  'packed', [0, 0, 0, 255], NoColorSpace);

const PLACEHOLDERS = Object.freeze({
  diffuse: DIFFUSE_PLACEHOLDER,
  normal_map: NORMAL_PLACEHOLDER,
  occlusion_map: PACKED_PLACEHOLDER,
  normal_data: PACKED_PLACEHOLDER,
  light_map: PACKED_PLACEHOLDER,
  material_map: PACKED_PLACEHOLDER,
});

function validRef(ref) {
  return !!ref && SOURCE_INFO[ref.source] === true && CHANNELS.has(ref.channel);
}

function hasNumericValue(value) {
  return value != null && Number.isFinite(Number(value));
}

function profileSources(profile) {
  return [profile?.shadow_mask, profile?.metalness, profile?.specular]
    .filter(validRef)
    .map(ref => ref.source)
    .filter((source, index, all) => all.indexOf(source) === index);
}

function hasPackedResponse(profile) {
  return profileSources(profile).length > 0;
}

function createBinding(role, uvNode) {
  return {
    role,
    placeholder: PLACEHOLDERS[role],
    textureNode: texture(PLACEHOLDERS[role], uvNode),
    enabledNode: uniform(false),
  };
}

function createBindings(hasUv) {
  // Keep the UV nodes stable for the lifetime of the material. They are only
  // attached to the graph when the geometry actually has a UV attribute.
  const primaryUv = hasUv ? uv() : null;
  const secondaryUv = hasUv ? uv(1) : null;
  return {
    diffuse: createBinding('diffuse', primaryUv),
    normal_map: createBinding('normal_map', primaryUv),
    occlusion_map: createBinding('occlusion_map', secondaryUv),
    normal_data: createBinding('normal_data', primaryUv),
    light_map: createBinding('light_map', primaryUv),
    material_map: createBinding('material_map', primaryUv),
  };
}

function channelNode(ref, bindings) {
  const binding = bindings[ref.source];
  let result = binding.textureNode[ref.channel];
  if (ref.invert) result = float(1).sub(result);
  return result;
}

function enabledChannelNode(ref, bindings, disabledValue) {
  const binding = bindings[ref.source];
  return binding.enabledNode.select(
    channelNode(ref, bindings), float(disabledValue));
}

function numericOr(value, fallback) {
  return hasNumericValue(value) ? Number(value) : fallback;
}

function setStableMaterialNodes(material, state, fallbackColor) {
  const { bindings, hasUv, profile } = state;
  if (hasUv) {
    // The conditional keeps the texture binding live without changing the
    // material graph when a diffuse texture is loaded or removed.
    material.colorNode = bindings.diffuse.enabledNode.select(
      bindings.diffuse.textureNode.rgb, color(fallbackColor));
    material.normalNode = bindings.normal_map.enabledNode.select(
      normalMap(bindings.normal_map.textureNode, state.normalScaleNode), normalViewGeometry);
    material.aoNode = bindings.occlusion_map.enabledNode.select(
      bindings.occlusion_map.textureNode.r, float(1));
  } else {
    material.colorNode = color(fallbackColor);
    material.normalNode = normalViewGeometry;
    material.aoNode = float(1);
  }

  if (!state.packedResponse) return;

  if (validRef(profile.metalness)) {
    const sampled = enabledChannelNode(profile.metalness, bindings, 0);
    material.metalnessNode = sampled
      .mul(float(numericOr(profile.metalness_scale, 1)))
      .clamp(0, 1);
  }

  if (validRef(profile.specular)) {
    const sampled = enabledChannelNode(profile.specular, bindings, 1);
    const response = sampled
      .mul(float(numericOr(profile.specular_scale, 1)))
      .clamp(0, 1);
    material.specularIntensityNode = hasNumericValue(profile.specular_influence)
      ? mix(float(1), response,
        clamp(float(Number(profile.specular_influence)), 0, 1))
      : response;
  }
}

function physicalLightingFlags(material) {
  return [
    material.useClearcoat,
    material.useSheen,
    material.useIridescence,
    material.useAnisotropy,
    material.useTransmission,
    material.useDispersion,
  ].map(value => value === true);
}

/**
 * Genshin's LightMap.G is a per-light toon-shadow mask. The lighting model
 * captures only the direct diffuse contribution produced by the current
 * light, leaving direct specular and all indirect terms untouched.
 */
class GenshinLightingModel extends PhysicalLightingModel {
  constructor(material, state) {
    super(...physicalLightingFlags(material));
    this.gameMaterialState = state;
  }

  direct(lightData, builder) {
    const { lightDirection, reflectedLight } = lightData;
    const before = reflectedLight.directDiffuse.toVar('gameDirectDiffuseBefore');
    super.direct(lightData, builder);

    const { profile, bindings } = this.gameMaterialState;
    const maskRef = profile.shadow_mask;
    const maskBinding = bindings[maskRef.source];
    const authoredMask = maskBinding.enabledNode.select(
      channelNode(maskRef, bindings), float(0.5));
    const lightValue = lightDirection.dot(normalView).clamp()
      .mul(0.5).add(0.5);
    const boundary = lightValue.add(authoredMask.sub(0.5).mul(0.5));
    const factor = smoothstep(float(0.5 - 0.08), float(0.5 + 0.08), boundary);
    const enabledFactor = maskBinding.enabledNode.select(factor, float(1));
    const contribution = reflectedLight.directDiffuse.sub(before);
    reflectedLight.directDiffuse.assign(
      before.add(contribution.mul(enabledFactor)));
  }
}

class GamePhysicalNodeMaterial extends MeshPhysicalNodeMaterial {
  setupLightingModel() {
    const state = this.userData.gameMaterial;
    if (validRef(state?.profile?.shadow_mask)) {
      return new GenshinLightingModel(this, state);
    }
    return new PhysicalLightingModel(...physicalLightingFlags(this));
  }
}

/** Create the stock or physical material appropriate for one profile. */
export function createGameMaterial(profile, fallbackColor, options = {}) {
  const hasUv = options.hasUv !== false;
  const packedResponse = hasPackedResponse(profile) && hasUv;
  const materialOptions = {
    side: DoubleSide,
    roughness: 1.0,
    metalness: 0.0,
    color: fallbackColor,
  };
  const material = packedResponse
    ? new GamePhysicalNodeMaterial({ ...materialOptions, specularIntensity: 1.0 })
    : new MeshStandardNodeMaterial(materialOptions);
  configureGameMaterial(material, profile, { packedResponse, hasUv, fallbackColor });
  return material;
}

/** Attach stable profile-specific TSL nodes to a material. */
export function configureGameMaterial(material, profile, options = {}) {
  const hasUv = options.hasUv !== false;
  const packedResponse = options.packedResponse ?? (hasPackedResponse(profile) && hasUv);
  const state = {
    profile: profile || { id: 'none' },
    packedResponse,
    hasUv,
    bindings: createBindings(hasUv),
    normalScaleNode: uniform(new Vector2(1, -1)),
  };
  state.sources = state.bindings;
  state.nodes = {
    diffuse: state.bindings.diffuse,
    normal: state.bindings.normal_map,
    ao: state.bindings.occlusion_map,
    normalData: state.bindings.normal_data,
    lightMap: state.bindings.light_map,
    materialMap: state.bindings.material_map,
  };
  material.userData.gameMaterial = state;
  setStableMaterialNodes(material, state, options.fallbackColor ?? material.color);
  return material;
}

function updateBinding(binding, value) {
  const next = value || binding.placeholder;
  const changed = binding.textureNode.value !== next
    || binding.enabledNode.value !== !!value;
  binding.textureNode.value = next;
  binding.enabledNode.value = !!value;
  return changed;
}

/** Update texture bindings without invalidating or rebuilding the material. */
export function updateGameMaterialTextures(mesh, maps = {}) {
  const state = mesh.material?.userData?.gameMaterial;
  if (!state) return false;
  let changed = false;
  const values = {
    diffuse: maps.diffuse,
    normal_map: maps.normal_map,
    occlusion_map: maps.ao_map,
    normal_data: maps.normal_data,
    light_map: maps.light_map,
    material_map: maps.material_map,
  };
  for (const [role, value] of Object.entries(values)) {
    changed = updateBinding(state.bindings[role], value) || changed;
  }
  if (Object.hasOwn(maps, 'normal_map_y_sign')) {
    state.normalScaleNode.value.set(
      1, Number.isFinite(maps.normal_map_y_sign) ? maps.normal_map_y_sign : -1);
  }
  return changed;
}

/** Return the packed roles sampled by this material's current node graph. */
export function getGameMaterialSources(material) {
  const state = material?.userData?.gameMaterial;
  if (!state?.packedResponse) return new Set();
  return new Set(profileSources(state.profile));
}

/** Release adapter-side references before the owning material is disposed. */
export function disposeGameMaterial(material) {
  const state = material?.userData?.gameMaterial;
  if (!state) return;
  for (const binding of Object.values(state.bindings)) {
    binding.textureNode.value = binding.placeholder;
    binding.enabledNode.value = false;
  }
  delete material.userData.gameMaterial;
}
