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

const BACKGROUND_WIDTH = 512;
const BACKGROUND_HEIGHT = 256;
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
    color: 0x998b7d,
    size: freeze([18, 12, 18]),
    position: freeze([0, 1, 0]),
  }),
  floor: freeze({
    color: 0x6d6056,
    size: freeze([18, 18]),
    position: freeze([0, -4.95, 0]),
  }),
  mainCard: freeze({
    color: 0xffd6af,
    intensity: 2.8,
    distance: 6,
    size: freeze([5.5, 4.0]),
  }),
  ceilingCard: freeze({
    color: 0xfff0de,
    intensity: 1.4,
    position: freeze([0, 4.75, 0]),
    size: freeze([6.0, 5.0]),
    target: freeze([0, 0, 0]),
  }),
  fillCard: freeze({
    color: 0xeaf1f7,
    intensity: 1.15,
    position: freeze([-4.5, 1.2, 3.5]),
    size: freeze([5.5, 4.5]),
    target: freeze([0, 0, 0]),
  }),
  roomLight: freeze({
    color: 0xffdfbf,
    intensity: 85,
    distance: 0,
    decay: 2,
    distanceFromOrigin: 2.5,
  }),
});
const STUDIO_CAPTURE = freeze({
  room: freeze({
    color: 0x9b9c9e,
    size: freeze([18, 12, 18]),
    position: freeze([0, 1, 0]),
  }),
  floor: freeze({
    color: 0x6a7078,
    size: freeze([18, 18]),
    position: freeze([0, -4.95, 0]),
  }),
  mainCard: freeze({
    color: 0xffffff,
    intensity: 2.6,
    distance: 6,
    size: freeze([6.0, 4.8]),
  }),
  fillCard: freeze({
    color: 0xe8eef5,
    intensity: 1.1,
    position: freeze([-4.5, 1.0, 3.5]),
    size: freeze([5.0, 4.5]),
    target: freeze([0, 0, 0]),
  }),
  overheadCard: freeze({
    color: 0xffffff,
    intensity: 1.4,
    position: freeze([0, 4.8, 0]),
    size: freeze([7.0, 4.0]),
    target: freeze([0, 0, 0]),
  }),
  backCard: freeze({
    color: 0xdde3ea,
    intensity: 0.8,
    position: freeze([0, 2.0, -5.5]),
    size: freeze([4.5, 4.0]),
    target: freeze([0, 1, 0]),
  }),
  roomLight: freeze({
    color: 0xffffff,
    intensity: 80,
    distance: 0,
    decay: 2,
    distanceFromOrigin: 2.0,
  }),
});

const makeStops = (entries) => freeze(entries.map(([offset, color]) => freeze({ offset, color })));

const makeGradient = (type, entries, options = {}) =>
  freeze({
    type,
    stops: makeStops(entries),
    ...options,
  });

const radialBackground = (center, outerRadius, entries, overlay) =>
  makeGradient('radial', entries, {
    center: freeze(center),
    innerRadius: 0.04,
    outerRadius,
    ...(overlay ? { overlay: makeGradient('vertical', overlay) } : {}),
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
const makePreset = (id, label, background, ambient, hemisphere, accent, atmosphere = null) =>
  freeze({
    id,
    label,
    ...(background ? { background } : {}),
    ...(ambient ? { ambient } : {}),
    ...(hemisphere ? { hemisphere } : {}),
    ...(accent ? { accent } : {}),
    ...(atmosphere ? { atmosphere } : {}),
  });

// Keep the visual vocabulary in one declarative table.
export const ENVIRONMENT_PRESETS = freeze({
  default: makePreset('default', 'Default'),
  studio: makePreset(
    'studio',
    'Studio',
    radialBackground(
      [0.5, 0.4],
      0.82,
      [
        [0, '#69717c'],
        [0.38, '#4e5662'],
        [1, '#2b313a'],
      ],
      [
        [0, 'rgba(0,0,0,0)'],
        [0.7, 'rgba(0,0,0,0.02)'],
        [1, 'rgba(0,0,0,0.12)'],
      ],
    ),
    makeAmbient(0xf5f7fa, 0.38),
    makeHemisphere(0xe1e9f3, 0x737b85, 0.42),
    makeAccent(0xffffff, 0.18, [4, 8, 6]),
  ),
  indoor: makePreset(
    'indoor',
    'Indoor',
    radialBackground(
      [0.5, 0.43],
      0.88,
      [
        [0, '#807165'],
        [0.42, '#62564d'],
        [1, '#36312e'],
      ],
      [
        [0, 'rgba(24,12,8,0.10)'],
        [0.66, 'rgba(0,0,0,0)'],
        [1, 'rgba(0,0,0,0.12)'],
      ],
    ),
    makeAmbient(0xfff2e5, 0.32),
    makeHemisphere(0xfff0dc, 0x686868, 0.38),
    makeAccent(0xffd2aa, 0.45, [4, 8, 5]),
  ),
  outdoor: makePreset(
    'outdoor',
    'Outdoor',
    null,
    makeAmbient(0xdbeaff, 0.26),
    makeHemisphere(0x78b5ed, 0x667068, 0.45),
    makeAccent(OUTDOOR_ATMOSPHERE.sunColor, OUTDOOR_ATMOSPHERE.sunIntensity, OUTDOOR_ATMOSPHERE.sunDirection),
    OUTDOOR_ATMOSPHERE,
  ),
  sunset: makePreset(
    'sunset',
    'Sunset',
    null,
    makeAmbient(0xc8bfdc, 0.22),
    makeHemisphere(0xa9b3dd, 0x625468, 0.4),
    makeAccent(SUNSET_ATMOSPHERE.sunColor, SUNSET_ATMOSPHERE.sunIntensity, SUNSET_ATMOSPHERE.sunDirection),
    SUNSET_ATMOSPHERE,
  ),
});

function directionFromPresetAccent(id) {
  return freeze(new THREE.Vector3().fromArray(ENVIRONMENT_PRESETS[id].accent.position).normalize().toArray());
}

const DEFAULT_PMREM = freeze({ size: 256, sigma: 0, near: 0.1, far: 100 });

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

function createIndoorCaptureScene() {
  const captureScene = new THREE.Scene();
  captureScene.userData.iblPresetId = 'indoor';
  const { room, floor } = createCaptureRoom(INDOOR_CAPTURE);

  const mainPosition = new THREE.Vector3()
    .fromArray(IBL_PROFILES.indoor.dominantDirection)
    .multiplyScalar(INDOOR_CAPTURE.mainCard.distance)
    .toArray();
  const mainCard = createLightCard({
    ...INDOOR_CAPTURE.mainCard,
    position: mainPosition,
    target: [0, 0, 0],
  });
  const ceilingCard = createLightCard(INDOOR_CAPTURE.ceilingCard);
  const fillCard = createLightCard(INDOOR_CAPTURE.fillCard);

  const roomLight = new THREE.PointLight(
    INDOOR_CAPTURE.roomLight.color,
    INDOOR_CAPTURE.roomLight.intensity,
    INDOOR_CAPTURE.roomLight.distance,
    INDOOR_CAPTURE.roomLight.decay,
  );
  roomLight.position
    .fromArray(IBL_PROFILES.indoor.dominantDirection)
    .multiplyScalar(INDOOR_CAPTURE.roomLight.distanceFromOrigin);
  captureScene.add(room, floor, mainCard, ceilingCard, fillCard, roomLight);
  return captureScene;
}

function createStudioCaptureScene() {
  const captureScene = new THREE.Scene();
  captureScene.userData.iblPresetId = 'studio';
  const { room, floor } = createCaptureRoom(STUDIO_CAPTURE);
  const mainPosition = new THREE.Vector3()
    .fromArray(IBL_PROFILES.studio.dominantDirection)
    .multiplyScalar(STUDIO_CAPTURE.mainCard.distance)
    .toArray();
  const mainCard = createLightCard({
    ...STUDIO_CAPTURE.mainCard,
    position: mainPosition,
    target: [0, 0, 0],
  });
  const fillCard = createLightCard(STUDIO_CAPTURE.fillCard);
  const overheadCard = createLightCard(STUDIO_CAPTURE.overheadCard);
  const backCard = createLightCard(STUDIO_CAPTURE.backCard);
  const roomLight = new THREE.PointLight(
    STUDIO_CAPTURE.roomLight.color,
    STUDIO_CAPTURE.roomLight.intensity,
    STUDIO_CAPTURE.roomLight.distance,
    STUDIO_CAPTURE.roomLight.decay,
  );
  roomLight.position
    .fromArray(IBL_PROFILES.studio.dominantDirection)
    .multiplyScalar(STUDIO_CAPTURE.roomLight.distanceFromOrigin);
  captureScene.add(room, floor, mainCard, fillCard, overheadCard, backCard, roomLight);
  return captureScene;
}

const IBL_PROFILES = freeze({
  outdoor: freeze({
    environmentIntensity: 0.65,
    lightIntensity: freeze({
      ambient: 0.04,
      hemisphere: 0.08,
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
    environmentIntensity: 0.38,
    lightIntensity: freeze({
      ambient: 0.13,
      hemisphere: 0.24,
      accent: 0.3,
    }),
    dominantDirection: directionFromPresetAccent('indoor'),
    pmrem: DEFAULT_PMREM,
    createCaptureScene: createIndoorCaptureScene,
  }),
  studio: freeze({
    environmentIntensity: 0.4,
    lightIntensity: freeze({
      ambient: 0.14,
      hemisphere: 0.26,
      accent: 0.24,
    }),
    dominantDirection: directionFromPresetAccent('studio'),
    pmrem: DEFAULT_PMREM,
    createCaptureScene: createStudioCaptureScene,
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

function createBackgroundTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = BACKGROUND_WIDTH;
  canvas.height = BACKGROUND_HEIGHT;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Could not create the environment background canvas.');

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.generateMipmaps = false;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  return { context, texture };
}

function createGradient(context, spec) {
  if (spec.type === 'radial') {
    const [x, y] = spec.center;
    const radiusScale = Math.max(BACKGROUND_WIDTH, BACKGROUND_HEIGHT);
    return context.createRadialGradient(
      x * BACKGROUND_WIDTH,
      y * BACKGROUND_HEIGHT,
      spec.innerRadius * radiusScale,
      x * BACKGROUND_WIDTH,
      y * BACKGROUND_HEIGHT,
      spec.outerRadius * radiusScale,
    );
  }
  if (spec.type === 'vertical') {
    return context.createLinearGradient(0, 0, 0, BACKGROUND_HEIGHT);
  }
  throw new Error(`Unknown procedural background type: ${spec.type}`);
}

function paintGradient(context, spec) {
  const gradient = createGradient(context, spec);
  for (const stop of spec.stops) gradient.addColorStop(stop.offset, stop.color);
  context.fillStyle = gradient;
  context.fillRect(0, 0, BACKGROUND_WIDTH, BACKGROUND_HEIGHT);
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
  const background = createBackgroundTexture();
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
        skyNode: null,
        texture: null,
        attempted: false,
        available: false,
        error: null,
        generationCount: 0,
      },
    ]),
  );

  function drawBackground(preset) {
    if (!preset.background) {
      scene.background = originalBackground;
      return;
    }

    background.context.clearRect(0, 0, BACKGROUND_WIDTH, BACKGROUND_HEIGHT);
    paintGradient(background.context, preset.background);
    if (preset.background.overlay) {
      paintGradient(background.context, preset.background.overlay);
    }
    background.texture.needsUpdate = true;
    scene.background = background.texture;
  }

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

    const profile = IBL_PROFILES[id];
    const resource = iblResources.get(id);
    if (preset.atmosphere) resource.skyNode ||= createAtmosphereNode(preset.atmosphere);
    scene.backgroundNode = resource?.skyNode || null;
    drawBackground(preset);
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
        captureScene.backgroundNode = resource.skyNode;
      } else captureScene = profile.createCaptureScene();
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

  function prepareResource(id, retry = false) {
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
        if (disposed || currentPresetId !== id) return false;
        const generated = generateIblResource(id);
        if (generated && !disposed && currentPresetId === id) {
          applyPreset(id);
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
      skyActive: !!iblResources.get(currentPresetId)?.skyNode,
    };
  }

  function getDominantLightDirection() {
    const direction = IBL_PROFILES[currentPresetId]?.dominantDirection;
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
      resource.skyNode?.dispose();
      resource.target = null;
      resource.skyNode = null;
      resource.texture = null;
      resource.available = false;
    }
    pmremGenerator?.dispose();
    pmremGenerator = null;
    background.texture.dispose();
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
