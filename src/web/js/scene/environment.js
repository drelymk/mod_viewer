// Viewer-only environment moods. This module deliberately knows nothing
// about mods, meshes, INIs, or the Python bridge.

import * as THREE from 'three/webgpu';
import {
  Fn,
  cameraPosition,
  color,
  exp,
  fwidth,
  max,
  mix,
  mx_fractal_noise_float,
  neutralToneMapping,
  normalWorldGeometry,
  smoothstep,
  vec2,
  vec3,
  vec4,
  varyingProperty,
} from 'three/tsl';
import { SkyMesh } from 'three/addons/objects/SkyMesh.js';

const freeze = Object.freeze;
function makeAtmosphere(sunElevation, sunAzimuth, sky, exposure, groundColor) {
  const direction = new THREE.Vector3().setFromSphericalCoords(
    1,
    THREE.MathUtils.degToRad(90 - sunElevation),
    THREE.MathUtils.degToRad(sunAzimuth),
  );
  const daylight = THREE.MathUtils.smoothstep(direction.y, 0, 0.7);
  const { cloudCoverage, cloudDensity, cloudElevation, cloudScale, ...scattering } = sky;
  return freeze({
    sky: freeze(scattering),
    clouds: freeze({ coverage: cloudCoverage, density: cloudDensity, elevation: cloudElevation, scale: cloudScale }),
    sunDirection: freeze(direction.toArray()),
    sunColor: new THREE.Color(0xffa35c).lerp(new THREE.Color(0xfff2df), daylight).getHex(),
    sunIntensity: THREE.MathUtils.lerp(0.35, 0.7, daylight),
    exposure,
    groundColor,
  });
}

const OUTDOOR_ATMOSPHERE = makeAtmosphere(
  50,
  -45,
  {
    turbidity: 2.5,
    rayleigh: 0.8,
    mieCoefficient: 0.003,
    mieDirectionalG: 0.75,
    cloudCoverage: 0.4,
    cloudDensity: 0.65,
    cloudElevation: 0.5,
    cloudScale: 0.0004,
  },
  0.25,
  0x53605b,
);
const SUNSET_ATMOSPHERE = makeAtmosphere(
  5,
  -160,
  {
    turbidity: 6,
    rayleigh: 2.5,
    mieCoefficient: 0.006,
    mieDirectionalG: 0.82,
    cloudCoverage: 0.43,
    cloudDensity: 0.6,
    cloudElevation: 0.45,
    cloudScale: 0.0004,
  },
  0.5,
  0x514856,
);
// Bake broad room illumination as well as softbox highlights into PMREM.
// Dark capture walls leave metals and shadow-facing surfaces almost black,
// even when the viewer's ambient light makes diffuse surfaces readable.
const INDOOR_CAPTURE = freeze({
  room: freeze({
    color: 0xb99a7b,
    size: freeze([18, 10, 18]),
    position: freeze([0, 2, 0]),
  }),
  floor: freeze({
    color: 0x866044,
    size: freeze([18, 18]),
    position: freeze([0, -2.95, 0]),
  }),
  mainCard: freeze({
    color: 0xd8eaff,
    intensity: 3.0,
    distance: 8,
    size: freeze([3.8, 3.2]),
  }),
  ceilingCard: freeze({
    color: 0xffe2bf,
    intensity: 0.75,
    position: freeze([0, 6.5, 0]),
    size: freeze([7.0, 5.0]),
    target: freeze([0, 0, 0]),
  }),
  fillCard: freeze({
    color: 0xffc58f,
    intensity: 1.3,
    position: freeze([5.0, 1.0, 1.5]),
    size: freeze([6.0, 4.5]),
    target: freeze([0, 0, 0]),
  }),
  roomLight: freeze({
    color: 0xffd6ab,
    intensity: 50,
    distance: 0,
    decay: 2,
    distanceFromOrigin: 1.5,
  }),
});
const STUDIO_CAPTURE = freeze({
  room: freeze({
    color: 0xa6a6a6,
    size: freeze([18, 12, 18]),
    position: freeze([0, 1, 0]),
  }),
  floor: freeze({
    color: 0x7e7e7e,
    size: freeze([18, 18]),
    position: freeze([0, -4.95, 0]),
  }),
  mainCard: freeze({
    color: 0xffffff,
    intensity: 1.7,
    distance: 6,
    size: freeze([7.0, 5.5]),
  }),
  fillCard: freeze({
    color: 0xf5f7fa,
    intensity: 0.8,
    position: freeze([5.0, 2.2, 3.5]),
    size: freeze([4.5, 5.5]),
    target: freeze([0, 0, 0]),
  }),
  overheadCard: freeze({
    color: 0xffffff,
    intensity: 0.7,
    position: freeze([0, 4.8, 0]),
    size: freeze([7.0, 4.0]),
    target: freeze([0, 0, 0]),
  }),
  backCard: freeze({
    color: 0xf7f7f7,
    intensity: 1.25,
    position: freeze([0, 2.5, -5.5]),
    size: freeze([6.0, 3.0]),
    target: freeze([0, 1, 0]),
  }),
  roomLight: freeze({
    color: 0xffffff,
    intensity: 50,
    distance: 0,
    decay: 2,
    distanceFromOrigin: 2.0,
  }),
});

const makeAmbient = (color, intensity) => freeze({ color, intensity });
const makeHemisphere = (color, groundColor, intensity) =>
  freeze({
    color,
    groundColor,
    intensity,
  });
const makeAccent = (color, intensity, position) =>
  freeze({
    color,
    intensity,
    position: freeze(position),
  });
const makePreset = (id, label, createBackgroundNode, ambient, hemisphere, accent, atmosphere = null) =>
  freeze({
    id,
    label,
    ...(createBackgroundNode ? { createBackgroundNode } : {}),
    ...(ambient ? { ambient } : {}),
    ...(hemisphere ? { hemisphere } : {}),
    ...(accent ? { accent } : {}),
    ...(atmosphere ? { atmosphere } : {}),
  });

const OUTDOOR_PRESET = makePreset(
  'outdoor',
  'Outdoor',
  () => createAtmosphereNode(OUTDOOR_ATMOSPHERE),
  makeAmbient(0xdbeaff, 0.26),
  makeHemisphere(0x78b5ed, 0x667068, 0.45),
  makeAccent(OUTDOOR_ATMOSPHERE.sunColor, OUTDOOR_ATMOSPHERE.sunIntensity, OUTDOOR_ATMOSPHERE.sunDirection),
  OUTDOOR_ATMOSPHERE,
);

// Keep the visual vocabulary in one declarative table.
export const ENVIRONMENT_PRESETS = freeze({
  default: makePreset('default', 'Default'),
  studio: makePreset(
    'studio',
    'Studio',
    createStudioBackgroundNode,
    makeAmbient(0xffffff, 0.32),
    makeHemisphere(0xfafafa, 0x858585, 0.36),
    makeAccent(0xffffff, 0.16, [-4, 6, 5]),
  ),
  indoor: makePreset(
    'indoor',
    'Indoor',
    createIndoorBackgroundNode,
    makeAmbient(0xffd4a6, 0.28),
    makeHemisphere(0xe6f0ff, 0xb07b4b, 0.36),
    makeAccent(0xd8eaff, 0.42, [-5, 2.5, -7]),
  ),
  outdoor: OUTDOOR_PRESET,
  nature: freeze({ ...OUTDOOR_PRESET, id: 'nature', label: 'Nature' }),
  sunset: makePreset(
    'sunset',
    'Sunset',
    () => createAtmosphereNode(SUNSET_ATMOSPHERE),
    makeAmbient(0xc8bfdc, 0.22),
    makeHemisphere(0xa9b3dd, 0x625468, 0.4),
    makeAccent(SUNSET_ATMOSPHERE.sunColor, SUNSET_ATMOSPHERE.sunIntensity, SUNSET_ATMOSPHERE.sunDirection),
    SUNSET_ATMOSPHERE,
  ),
});

function directionFromPresetAccent(id) {
  return freeze(new THREE.Vector3().fromArray(ENVIRONMENT_PRESETS[id].accent.position).normalize().toArray());
}

const lightingPresetId = (id) => (id === 'nature' ? 'outdoor' : id);

const DEFAULT_PMREM = freeze({ size: 256, sigma: 0, near: 0.1, far: 100 });

function createStudioBackgroundNode() {
  const keyDirection = vec3(...directionFromPresetAccent('studio'));
  return Fn(() => {
    const direction = normalWorldGeometry.normalize();
    // A broad curved sweep suggests a seamless backdrop without placing a
    // floor or contact shadow under models of arbitrary size and position.
    const sweep = smoothstep(-0.5, 0.35, direction.y);
    const backdrop = mix(color(STUDIO_CAPTURE.floor.color).mul(0.32), color(STUDIO_CAPTURE.room.color).mul(0.7), sweep);
    const keySpill = max(0, direction.dot(keyDirection)).pow(4).mul(0.22).add(0.78);
    const cove = exp(direction.y.add(0.25).mul(3).pow(2).negate()).mul(0.018);
    return vec4(backdrop.mul(keySpill).add(cove), 1);
  })();
}

function createIndoorBackgroundNode() {
  const capture = INDOOR_CAPTURE;
  const daylightDirection = vec3(...directionFromPresetAccent('indoor'));
  return Fn(() => {
    const direction = normalWorldGeometry.normalize();
    // Suggest warm indoor bounce and broad daylight without architectural
    // edges or a physical floor that would need to track the model.
    const tone = smoothstep(-0.8, 0.8, direction.y);
    const warmth = mix(color(capture.floor.color).mul(0.32), color(capture.room.color).mul(0.28), tone);
    const daylight = smoothstep(-0.4, 1, direction.dot(daylightDirection)).mul(0.04);
    return vec4(warmth.add(color(capture.mainCard.color).mul(daylight)), 1);
  })();
}

function createAtmosphereNode(atmosphere) {
  const direction = normalWorldGeometry.normalize();
  const sky = new SkyMesh();
  for (const [name, value] of Object.entries(atmosphere.sky)) sky[name].value = value;
  sky.cloudSpeed.value = 0;
  sky.cloudCoverage.value = 0;
  sky.sunPosition.value.fromArray(atmosphere.sunDirection);
  sky.showSunDisc.value = true;
  // SkyMesh initializes scattering varyings in its vertex node. Reuse that
  // initialization and its r185 world-position varying for the background
  // sphere, while Three.js keeps its own far-plane projection. The zero-valued
  // dependency includes those vertex assignments without changing sky color.
  const initializeSky = Fn(() => {
    const projection = sky.material.vertexNode.toVar();
    varyingProperty('vec3', 'v_positionWorld').assign(cameraPosition.add(normalWorldGeometry));
    return projection;
  })().toVertexStage();
  const scattering = sky.material.colorNode.add(initializeSky.mul(0));
  const cloudField = Fn(([p]) => mx_fractal_noise_float(vec3(p, 0), 5, 2.1, 0.5).mul(0.5).add(0.5));
  const node = Fn(() => {
    // Compress sky highlights in linear space for both background and IBL.
    // Models retain the viewport's existing sRGB/NoToneMapping contract.
    const skyColor = neutralToneMapping(scattering.rgb, atmosphere.exposure);
    const { coverage, density, elevation, scale } = atmosphere.clouds;
    const uv = direction.xz.div(max(direction.y, 0.05)).mul((scale * 1000) / (1 - elevation * 0.9));
    const field = cloudField(uv).toVar();
    const threshold = 1 - coverage;
    // Filter at the current render resolution; fade distant clouds into
    // the atmospheric horizon instead of adding fog to inspection geometry.
    const edgeWidth = max(fwidth(field).mul(1.5), 0.015);
    const mask = smoothstep(edgeWidth.negate().add(threshold), edgeWidth.add(threshold + 0.12), field)
      .mul(density)
      .mul(smoothstep(0.02, 0.22, direction.y));
    const sun = sky.sunPosition;
    const towardSun = vec2(sun.x, sun.z).div(max(sun.y, 0.15)).mul(0.12);
    const sunDensity = max(0, cloudField(uv.add(towardSun)).sub(threshold));
    const transmission = exp(sunDensity.mul(-5));
    const rim = max(0, field.sub(threshold).sub(sunDensity)).mul(2);
    const forward = max(0, direction.dot(sun)).pow(6);
    const illumination = transmission.mul(max(sun.y, 0).mul(0.5).add(0.3)).add(rim).add(forward.mul(0.25));
    const cloudColor = color(0x7889aa).mul(0.25).add(color(atmosphere.sunColor).mul(illumination));
    const cloudedSky = mix(skyColor, cloudColor, mask);
    return vec4(mix(color(atmosphere.groundColor), cloudedSky, smoothstep(-0.35, 0.02, direction.y)), 1);
  })();
  // SkyMesh supplies its existing scattering nodes; its unused mesh resources
  // never enter the viewer scene. The renderer owns the background geometry.
  sky.geometry.dispose();
  sky.material.dispose();
  return node;
}

function createLightCard({ color, intensity, position, size, target }) {
  const card = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshLambertMaterial({
      color: 0x000000,
      emissive: color,
      emissiveIntensity: intensity,
      side: THREE.DoubleSide,
    }),
  );
  card.position.fromArray(position);
  card.scale.set(size[0], size[1], 1);
  card.lookAt(new THREE.Vector3().fromArray(target || [0, 0, 0]));
  return card;
}

function createCaptureRoom({ room: roomConfig, floor: floorConfig }) {
  const room = new THREE.Mesh(
    new THREE.BoxGeometry(...roomConfig.size),
    new THREE.MeshStandardMaterial({
      color: roomConfig.color,
      roughness: 1,
      metalness: 0,
      side: THREE.BackSide,
    }),
  );
  room.position.fromArray(roomConfig.position);

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(...floorConfig.size),
    new THREE.MeshStandardMaterial({
      color: floorConfig.color,
      roughness: 1,
      metalness: 0,
    }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.fromArray(floorConfig.position);
  return { room, floor };
}

function createRoomCaptureScene(id, config) {
  const captureScene = new THREE.Scene();
  captureScene.userData.iblPresetId = id;
  const { room, floor } = createCaptureRoom(config);
  const direction = IBL_PROFILES[id].dominantDirection;
  const mainPosition = new THREE.Vector3().fromArray(direction).multiplyScalar(config.mainCard.distance).toArray();
  const mainCard = createLightCard({
    ...config.mainCard,
    position: mainPosition,
    target: [0, 0, 0],
  });
  captureScene.add(room, floor, mainCard);
  for (const name of ['fillCard', 'ceilingCard', 'overheadCard', 'backCard']) {
    if (config[name]) captureScene.add(createLightCard(config[name]));
  }
  const roomLight = new THREE.PointLight(
    config.roomLight.color,
    config.roomLight.intensity,
    config.roomLight.distance,
    config.roomLight.decay,
  );
  roomLight.position.fromArray(direction).multiplyScalar(config.roomLight.distanceFromOrigin);
  captureScene.add(roomLight);
  return captureScene;
}

const IBL_PROFILES = freeze({
  outdoor: freeze({
    // The display-compressed sky needs a stronger IBL contribution than the
    // former HDR capture; retain diffuse fill when preparation completes.
    environmentIntensity: 1.2,
    lightIntensity: freeze({
      ambient: 0.12,
      hemisphere: 0.24,
      accent: OUTDOOR_ATMOSPHERE.sunIntensity,
    }),
    dominantDirection: OUTDOOR_ATMOSPHERE.sunDirection,
    pmrem: DEFAULT_PMREM,
    atmosphere: OUTDOOR_ATMOSPHERE,
  }),
  sunset: freeze({
    environmentIntensity: 0.65,
    lightIntensity: freeze({
      ambient: 0.12,
      hemisphere: 0.22,
      accent: SUNSET_ATMOSPHERE.sunIntensity,
    }),
    dominantDirection: SUNSET_ATMOSPHERE.sunDirection,
    pmrem: DEFAULT_PMREM,
    atmosphere: SUNSET_ATMOSPHERE,
  }),
  indoor: freeze({
    environmentIntensity: 0.65,
    lightIntensity: freeze({
      ambient: 0.2,
      hemisphere: 0.28,
      accent: 0.42,
    }),
    dominantDirection: directionFromPresetAccent('indoor'),
    pmrem: DEFAULT_PMREM,
    capture: INDOOR_CAPTURE,
  }),
  studio: freeze({
    environmentIntensity: 0.5,
    lightIntensity: freeze({
      ambient: 0.1,
      hemisphere: 0.2,
      accent: 0.16,
    }),
    dominantDirection: directionFromPresetAccent('studio'),
    pmrem: DEFAULT_PMREM,
    capture: STUDIO_CAPTURE,
  }),
});

function disposeCaptureScene(captureScene) {
  if (!captureScene) return;
  const resources = new Set();
  captureScene.traverse((object) => {
    if (object.geometry) resources.add(object.geometry);
    const materials = Array.isArray(object.material) ? object.material : object.material ? [object.material] : [];
    for (const material of materials) resources.add(material);
  });
  for (const resource of resources) resource.dispose?.();
  captureScene.clear();
}

function captureLightState(light, includeGround = false) {
  return {
    color: light.color.clone(),
    ...(includeGround ? { groundColor: light.groundColor.clone() } : {}),
    intensity: light.intensity,
  };
}

function restoreLightState(light, state) {
  light.color.copy(state.color);
  if (state.groundColor) light.groundColor.copy(state.groundColor);
  light.intensity = state.intensity;
}

/**
 * Create the synchronously constructible environment controller.
 *
 * Environment presets define baseline scene lighting. The movable key light
 * in scene.js remains an independent user-controlled inspection light layered
 * on top of these presets.
 */
export function createEnvironmentController({
  renderer,
  scene,
  ambientLight,
  hemisphereLight,
  lightTarget,
  onVisualChange = null,
}) {
  if (!renderer || !scene || !ambientLight || !hemisphereLight || !lightTarget) {
    throw new Error('EnvironmentController needs a renderer, scene, viewer lights and target.');
  }

  const originalBackground = scene.background;
  const originalBackgroundNode = scene.backgroundNode;
  const originalEnvironment = scene.environment;
  const originalEnvironmentIntensity = scene.environmentIntensity;
  const originalAmbient = captureLightState(ambientLight);
  const originalHemisphere = captureLightState(hemisphereLight, true);
  const accentLight = new THREE.DirectionalLight(0xffffff, 0);
  accentLight.visible = false;
  accentLight.target = lightTarget;
  // Preset positions are offsets from the model target. The existing
  // movable key light updates this target whenever the model is reframed.
  lightTarget.add(accentLight);

  let currentPresetId = 'default';
  let disposed = false;
  let preparationEnabled = false;
  let pmremGenerator = null;
  const iblResources = new Map(
    Object.keys(IBL_PROFILES).map((id) => [
      id,
      {
        promise: null,
        target: null,
        backgroundNode: null,
        texture: null,
        attempted: false,
        available: false,
        error: null,
        generationCount: 0,
      },
    ]),
  );

  function applyAccent(config, intensity = null) {
    if (!config) {
      accentLight.visible = false;
      accentLight.intensity = 0;
      return;
    }
    accentLight.color.set(config.color);
    // This is local to lightTarget, so the accent follows offset models.
    accentLight.position.fromArray(config.position);
    accentLight.intensity = intensity ?? config.intensity;
    accentLight.visible = true;
  }

  function applyLights(preset, intensities = null) {
    ambientLight.color.set(preset.ambient.color);
    ambientLight.intensity = intensities?.ambient ?? preset.ambient.intensity;
    hemisphereLight.color.set(preset.hemisphere.color);
    hemisphereLight.groundColor.set(preset.hemisphere.groundColor);
    hemisphereLight.intensity = intensities?.hemisphere ?? preset.hemisphere.intensity;
    applyAccent(preset.accent, intensities?.accent);
  }

  function restoreEnvironment() {
    scene.environment = originalEnvironment;
    scene.environmentIntensity = originalEnvironmentIntensity;
  }

  function applyDefault() {
    scene.background = originalBackground;
    scene.backgroundNode = originalBackgroundNode;
    restoreEnvironment();
    restoreLightState(ambientLight, originalAmbient);
    restoreLightState(hemisphereLight, originalHemisphere);
    applyAccent(null);
  }

  function applyPreset(id) {
    const preset = ENVIRONMENT_PRESETS[id];
    if (id === 'default') {
      applyDefault();
      return;
    }

    const resourceId = lightingPresetId(id);
    const profile = IBL_PROFILES[resourceId];
    const resource = iblResources.get(resourceId);
    resource.backgroundNode ||= preset.createBackgroundNode();
    scene.background = originalBackground;
    scene.backgroundNode = resource.backgroundNode;
    if (profile && resource?.available && resource.texture) {
      applyLights(preset, profile.lightIntensity);
      scene.environment = resource.texture;
      scene.environmentIntensity = profile.environmentIntensity;
      return;
    }

    applyLights(preset);
    restoreEnvironment();
  }

  function setPreset(id) {
    if (disposed) return false;
    const preset = ENVIRONMENT_PRESETS[id];
    if (!preset) return false;
    currentPresetId = preset.id;
    applyPreset(id);
    if (preparationEnabled) void prepareResource(id, true);
    return true;
  }

  function generateIblResource(id) {
    const profile = IBL_PROFILES[id];
    const resource = iblResources.get(id);
    resource.attempted = true;
    resource.error = null;
    let captureScene = null;
    let generatedTarget = null;
    let rendererState = null;
    try {
      rendererState = THREE.RendererUtils.saveRendererState(renderer);
      renderer.setMRT(null);
      pmremGenerator ||= new THREE.PMREMGenerator(renderer);
      if (profile.atmosphere) {
        captureScene = new THREE.Scene();
        captureScene.userData.iblPresetId = id;
        captureScene.backgroundNode = resource.backgroundNode;
      } else captureScene = createRoomCaptureScene(id, profile.capture);
      const { sigma, near, far, size } = profile.pmrem;
      generatedTarget = pmremGenerator.fromScene(captureScene, sigma, near, far, { size });
      if (!generatedTarget?.texture) {
        throw new Error(`${id} PMREM generation returned no texture.`);
      }
      resource.target = generatedTarget;
      resource.texture = generatedTarget.texture;
      resource.available = true;
      resource.generationCount += 1;
      generatedTarget = null;
      return true;
    } finally {
      if (rendererState) THREE.RendererUtils.restoreRendererState(renderer, rendererState);
      generatedTarget?.dispose?.();
      disposeCaptureScene(captureScene);
    }
  }

  function prepareResource(presetId, retry = false) {
    const id = lightingPresetId(presetId);
    const resource = iblResources.get(id);
    if (disposed || !resource) return Promise.resolve(false);
    if (resource.promise) return resource.promise;
    // Only another explicit selection retries a failure; passive preparation
    // and repeated selections during one pending capture never schedule more work.
    if (resource.available || (resource.attempted && !retry)) return Promise.resolve(resource.available);
    resource.promise = (async () => {
      try {
        // Let startup and rapid preset changes settle before expensive GPU work.
        await new Promise((resolve) => setTimeout(resolve, 0));
        if (disposed || lightingPresetId(currentPresetId) !== id) return false;
        const generated = generateIblResource(id);
        if (generated && !disposed && lightingPresetId(currentPresetId) === id) {
          applyPreset(currentPresetId);
          onVisualChange?.();
        }
        return generated;
      } catch (error) {
        resource.error = error?.message || String(error);
        console.debug(`${id} environment preparation failed; using baseline lighting.`, error);
        return false;
      } finally {
        resource.promise = null;
      }
    })();
    return resource.promise;
  }

  function prepare() {
    if (disposed) return Promise.resolve(false);
    preparationEnabled = true;
    return prepareResource(currentPresetId);
  }

  function getDebugState() {
    const activeIblPreset =
      [...iblResources.entries()].find(
        ([, resource]) => resource.texture && scene.environment === resource.texture,
      )?.[0] || null;
    const resources = Object.fromEntries(
      [...iblResources.entries()].map(([id, resource]) => [
        id,
        {
          attempted: resource.attempted,
          available: resource.available,
          generationCount: resource.generationCount,
          error: resource.error,
        },
      ]),
    );
    return {
      preset: currentPresetId,
      activeIblPreset,
      environmentActive: activeIblPreset !== null,
      environmentIntensity: scene.environmentIntensity,
      preparationInFlight: [...iblResources.values()].some((resource) => resource.promise !== null),
      resources,
      totalPmremGenerationCount: [...iblResources.values()].reduce(
        (total, resource) => total + resource.generationCount,
        0,
      ),
      activeDominantDirection: getDominantLightDirection(),
      atmosphere: getPreset().atmosphere || null,
      skyActive: !!getPreset().atmosphere && !!iblResources.get(lightingPresetId(currentPresetId))?.backgroundNode,
    };
  }

  function getDominantLightDirection() {
    const direction = IBL_PROFILES[lightingPresetId(currentPresetId)]?.dominantDirection;
    return direction ? [...direction] : null;
  }

  function getPreset() {
    return ENVIRONMENT_PRESETS[currentPresetId];
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    currentPresetId = 'default';
    applyDefault();
    for (const resource of iblResources.values()) {
      resource.target?.dispose?.();
      resource.backgroundNode?.dispose();
      resource.target = null;
      resource.backgroundNode = null;
      resource.texture = null;
      resource.available = false;
    }
    pmremGenerator?.dispose();
    pmremGenerator = null;
    lightTarget.remove(accentLight);
  }

  return {
    prepare,
    setPreset,
    getPreset,
    getDominantLightDirection,
    getDebugState,
    dispose,
  };
}
