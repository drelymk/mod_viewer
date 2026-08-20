// Game-specific material interpretation for the pinned Three.js WebGPU
// renderer. Packed textures remain intact: their authored channels are read
// through stable TSL nodes and their bindings are changed in place.

import {
  DataTexture,
  DoubleSide,
  MeshPhysicalNodeMaterial,
  MeshStandardNodeMaterial,
  NoColorSpace,
  PhysicalLightingModel as ThreePhysicalLightingModel,
  RGBAFormat,
  SRGBColorSpace,
  TSL,
  UnsignedByteType,
  Vector2,
} from 'three/webgpu';
import {
  clamp,
  color,
  diffuseColor,
  float,
  Fn,
  ior,
  materialIOR,
  materialSpecularColor,
  materialSpecularIntensity,
  metalness,
  min,
  mix,
  negateOnBackSide,
  normalMap,
  normalView,
  normalViewGeometry,
  pow2,
  positionViewDirection,
  smoothstep,
  step,
  specularColor,
  specularF90,
  texture,
  uniform,
  uv,
  vec3,
  vec4,
} from 'three/tsl';

const specularColorBlended = TSL.specularColorBlended;

const SOURCE_INFO = Object.freeze({
  normal_data: true,
  light_map: true,
  material_map: true,
});
const CHANNELS = new Set(['r', 'g', 'b', 'a']);

// `normalView` delegates to the material's normal node outside its NORMAL
// sub-build, so assigning it back to `material.normalNode` would recurse.
// Keep the NORMAL-path behavior locally, including r185's flat-shading rule.
const orientedGeometryNormal = /*@__PURE__*/ (Fn((builder) => {
  let node = normalViewGeometry;
  if (builder.isFlatShading() !== true) node = negateOnBackSide(node);
  return node;
}, 'vec3').once())();

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
  return [profile?.shadow_mask, profile?.material_id, profile?.metalness,
    profile?.specular, profile?.specular_area]
    .filter(validRef)
    .map(ref => ref.source)
    .filter((source, index, all) => all.indexOf(source) === index);
}

const DEBUG_MODE_VALUES = Object.freeze({
  off: 0,
  'material-id': 1,
  'specular-area': 2,
});

function normalizeDebugMode(mode) {
  return Object.hasOwn(DEBUG_MODE_VALUES, mode) ? mode : 'off';
}

/** Decode the scalar LightMap.A region value used by the Genshin profile. */
export function decodeMaterialIdValue(raw, decoder) {
  if (decoder !== 'genshin_5_region') return 0;
  if (raw > 0.8) return 2;
  if (raw >= 0.6) return 5;
  if (raw > 0.4) return 3;
  if (raw >= 0.2) return 4;
  return 1;
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

function createSpecularResponseNode(profile, bindings) {
  if (!validRef(profile?.specular)) return float(1);
  const sampled = enabledChannelNode(profile.specular, bindings, 1);
  const response = sampled
    .mul(float(numericOr(profile.specular_scale, 1)))
    .clamp(0, 1);
  return hasNumericValue(profile.specular_influence)
    ? mix(float(1), response,
      clamp(float(Number(profile.specular_influence)), 0, 1))
    : response;
}

function createMaterialIdNode(profile, bindings) {
  const ref = profile?.material_id;
  if (!validRef(ref) || profile?.material_id_decoder !== 'genshin_5_region') {
    return float(0);
  }
  const raw = channelNode(ref, bindings);
  // The inclusive/exclusive comparisons mirror HoyoToon's sequential
  // assignments: later overlapping regions own the exact .40/.80 edges.
  return raw.greaterThan(0.8).select(
    2, raw.greaterThanEqual(0.6).select(
      5, raw.greaterThan(0.4).select(
        3, raw.greaterThanEqual(0.2).select(4, 1))));
}

function createSpecularAreaNode(profile, bindings) {
  return validRef(profile?.specular_area)
    ? enabledChannelNode(profile.specular_area, bindings, 1)
    : float(1);
}

function debugColorNode(state, baseColor) {
  const materialIdColor = state.hasMaterialId
    ? materialIdDebugColor(state.materialIdNode)
    : baseColor;
  const areaColor = state.hasSpecularArea
    ? vec3(state.specularAreaNode)
    : baseColor;
  state.debugOutputNode = state.debugModeNode.lessThan(1.5).select(
    materialIdColor, areaColor);
  return state.debugModeNode.lessThan(0.5).select(
    baseColor, state.debugModeNode.lessThan(1.5).select(
      materialIdColor, areaColor));
}

function materialIdDebugColor(materialIdNode) {
  const id5 = color(0x9b59b6);
  const id4 = materialIdNode.lessThan(5).select(color(0xf1c40f), id5);
  const id3 = materialIdNode.lessThan(4).select(color(0x2ecc71), id4);
  const id2 = materialIdNode.lessThan(3).select(color(0xe74c3c), id3);
  return materialIdNode.lessThan(2).select(color(0x2e86de), id2);
}

function setStableMaterialNodes(material, state, fallbackColor) {
  const { bindings, hasUv, profile } = state;
  const fallbackNormal = orientedGeometryNormal;
  let baseColor;
  if (hasUv) {
    // The conditional keeps the texture binding live without changing the
    // material graph when a diffuse texture is loaded or removed.
    baseColor = bindings.diffuse.enabledNode.select(
      bindings.diffuse.textureNode.rgb, color(fallbackColor));
    material.normalNode = bindings.normal_map.enabledNode.select(
      normalMap(bindings.normal_map.textureNode, state.normalScaleNode), fallbackNormal);
    material.aoNode = bindings.occlusion_map.enabledNode.select(
      bindings.occlusion_map.textureNode.r, float(1));
  } else {
    baseColor = color(fallbackColor);
    material.normalNode = fallbackNormal;
    material.aoNode = float(1);
  }
  material.colorNode = debugColorNode(state, baseColor);

  if (!state.packedResponse) return;

  // r185 exposes specularIntensityNode but MeshPhysicalNodeMaterial's
  // built-in setup does not consume that override. GamePhysicalNodeMaterial
  // applies this stable response before the metallic mix instead.
  state.specularResponseNode = createSpecularResponseNode(profile, bindings);

  if (validRef(profile.metalness)) {
    const sampled = enabledChannelNode(profile.metalness, bindings, 0);
    material.metalnessNode = sampled
      .mul(float(numericOr(profile.metalness_scale, 1)))
      .clamp(0, 1);
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
 * light, leaving indirect terms untouched.
 */
class GenshinLightingModel extends ThreePhysicalLightingModel {
  constructor(material, state) {
    super(...physicalLightingFlags(material));
    this.gameMaterialState = state;
  }

  direct(lightData, builder) {
    const { lightDirection, reflectedLight } = lightData;
    const diffuseBefore = reflectedLight.directDiffuse.toVar('gameDirectDiffuseBefore');
    const specularBefore = reflectedLight.directSpecular.toVar('gameDirectSpecularBefore');
    super.direct(lightData, builder);

    const {
      profile,
      bindings,
      shadowThresholdNode,
      shadowSoftnessNode,
      shadowMaskStrengthNode,
      shadowInfluenceNode,
      specularAreaNode,
      toonSpecularShininessNode,
      toonSpecularThresholdBiasNode,
      toonSpecularSoftnessNode,
      toonSpecularMetalCutoffNode,
    } = this.gameMaterialState;
    const maskRef = profile.shadow_mask;
    const maskBinding = bindings[maskRef.source];
    const authoredMask = maskBinding.enabledNode.select(
      channelNode(maskRef, bindings), float(0.5));
    const lightValue = lightDirection.dot(normalView).clamp()
      .mul(0.5).add(0.5);
    const boundary = lightValue.add(
      authoredMask.sub(0.5).mul(shadowMaskStrengthNode));
    const factor = smoothstep(
      shadowThresholdNode.sub(shadowSoftnessNode),
      shadowThresholdNode.add(shadowSoftnessNode),
      boundary);
    const influencedFactor = mix(float(1), factor, shadowInfluenceNode);
    const enabledFactor = maskBinding.enabledNode.select(
      influencedFactor, float(1));
    const diffuseContribution = reflectedLight.directDiffuse.sub(diffuseBefore);
    reflectedLight.directDiffuse.assign(
      diffuseBefore.add(diffuseContribution.mul(enabledFactor)));

    const areaRef = profile.specular_area;
    let areaGate = float(1);
    if (validRef(areaRef)) {
      const areaBinding = bindings[areaRef.source];
      const threshold = toonSpecularThresholdBiasNode.sub(specularAreaNode);
      const halfDirection = lightDirection.add(positionViewDirection).normalize();
      const ndoth = normalView.dot(halfDirection).clamp(0, 1);
      const term = ndoth.max(0.001).pow(toonSpecularShininessNode);
      const softness = numericOr(profile.toon_specular_softness, 0);
      const computedGate = softness > 0
        ? smoothstep(threshold.sub(toonSpecularSoftnessNode),
          threshold.add(toonSpecularSoftnessNode), term)
        : step(threshold, term);
      areaGate = areaBinding.enabledNode.select(computedGate, float(1));

      const metalRef = validRef(profile.metalness)
        ? profile.metalness : profile.specular;
      if (validRef(metalRef) && toonSpecularMetalCutoffNode) {
        const metalBinding = bindings[metalRef.source];
        const metalRaw = enabledChannelNode(metalRef, bindings, 0);
        const metalRegion = metalBinding.enabledNode.select(
          step(toonSpecularMetalCutoffNode, metalRaw), float(0));
        areaGate = mix(areaGate, float(1), metalRegion);
      }
    }
    const specularContribution = reflectedLight.directSpecular.sub(specularBefore);
    reflectedLight.directSpecular.assign(
      specularBefore.add(specularContribution.mul(areaGate)));
  }
}

class GamePhysicalNodeMaterial extends MeshPhysicalNodeMaterial {
  setupSpecular() {
    const response = this.userData.gameMaterial?.specularResponseNode ?? float(1);
    const specularIntensity = materialSpecularIntensity.mul(response);
    const iorNode = this.iorNode ? float(this.iorNode) : materialIOR;

    ior.assign(iorNode);
    specularColor.assign(
      min(
        pow2(ior.sub(1).div(ior.add(1))).mul(materialSpecularColor),
        vec3(1),
      ).mul(specularIntensity));
    specularColorBlended.assign(
      mix(specularColor, diffuseColor.rgb, metalness));
    specularF90.assign(mix(specularIntensity, 1, metalness));
  }

  setupLightingModel() {
    const state = this.userData.gameMaterial;
    if (validRef(state?.profile?.shadow_mask)) {
      return new GenshinLightingModel(this, state);
    }
    return new ThreePhysicalLightingModel(...physicalLightingFlags(this));
  }

  setupOutput(builder, outputNode) {
    const result = super.setupOutput(builder, outputNode);
    const state = this.userData.gameMaterial;
    if (!state?.debugOutputNode) return result;
    // Keep diagnostics out of the lighting, environment and fog result while
    // leaving the normal graph and pipeline selected by the same uniform.
    return state.debugModeNode.lessThan(0.5).select(
      result, vec4(state.debugOutputNode, result.a));
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
  const packedResponse = Boolean(
    (options.packedResponse ?? hasPackedResponse(profile)) && hasUv);
  const resolvedProfile = profile || { id: 'none' };
  const hasMaterialId = packedResponse && hasUv
    && validRef(resolvedProfile.material_id)
    && resolvedProfile.material_id_decoder === 'genshin_5_region';
  const hasSpecularArea = packedResponse && hasUv
    && validRef(resolvedProfile.specular_area);
  const state = {
    profile: resolvedProfile,
    packedResponse,
    hasUv,
    bindings: createBindings(hasUv),
    normalScaleNode: uniform(new Vector2(1, -1)),
    shadowThresholdNode: uniform(
      numericOr(profile?.shadow_threshold, 0.5)),
    shadowSoftnessNode: uniform(
      numericOr(profile?.shadow_softness, 0.08)),
    shadowMaskStrengthNode: uniform(
      numericOr(profile?.shadow_mask_strength, 0.5)),
    shadowInfluenceNode: uniform(
      numericOr(profile?.shadow_influence, 1.0)),
    materialIdNode: float(0),
    specularAreaNode: float(1),
    toonSpecularShininessNode: uniform(
      numericOr(profile?.toon_specular_shininess, 10.0)),
    toonSpecularThresholdBiasNode: uniform(
      numericOr(profile?.toon_specular_threshold_bias, 1.015)),
    toonSpecularSoftnessNode: uniform(
      numericOr(profile?.toon_specular_softness, 0.0)),
    toonSpecularMetalCutoffNode: hasNumericValue(profile?.toon_specular_metal_cutoff)
      ? uniform(Number(profile.toon_specular_metal_cutoff)) : null,
    debugModeNode: uniform(0),
    hasMaterialId,
    hasSpecularArea,
  };
  // Channel nodes need the final binding table, but the node objects remain
  // stable for the lifetime of the material. Conservative no-UV materials
  // deliberately keep these as scalar fallbacks, so no packed texture node
  // can become reachable in that path.
  state.materialIdNode = hasMaterialId
    ? createMaterialIdNode(resolvedProfile, state.bindings) : float(0);
  state.specularAreaNode = hasSpecularArea
    ? createSpecularAreaNode(resolvedProfile, state.bindings) : float(1);
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

/** Change developer visualization through stable uniforms only. */
export function setMaterialDebugMode(materials, mode) {
  const normalized = normalizeDebugMode(mode);
  const value = DEBUG_MODE_VALUES[normalized];
  for (const item of materials || []) {
    const material = item?.isMaterial ? item : item?.material;
    const state = material?.userData?.gameMaterial;
    if (state?.debugModeNode) state.debugModeNode.value = value;
  }
  return normalized;
}

export function getMaterialDebugMode(material) {
  const value = material?.userData?.gameMaterial?.debugModeNode?.value;
  return Object.entries(DEBUG_MODE_VALUES).find(([, id]) => id === value)?.[0]
    || 'off';
}
