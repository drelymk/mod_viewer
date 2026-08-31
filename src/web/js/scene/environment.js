// Viewer-only environment moods. This module deliberately knows nothing
// about mods, meshes, INIs, or the Python bridge.

import * as THREE from 'three/webgpu';
import { SkyMesh } from 'three/addons/objects/SkyMesh.js';

const BACKGROUND_WIDTH = 512;
const BACKGROUND_HEIGHT = 256;
const freeze = Object.freeze;
const OUTDOOR_SKY = freeze({
  turbidity: 2.5,
  rayleigh: 0.8,
  mieCoefficient: 0.003,
  mieDirectionalG: 0.75,
  cloudCoverage: 0,
  cloudDensity: 0,
  cloudElevation: 0.5,
});
const INDOOR_CAPTURE = freeze({
  room: freeze({
    color: 0x29231f,
    size: freeze([18, 12, 18]),
    position: freeze([0, 1, 0]),
  }),
  floor: freeze({
    color: 0x151312,
    size: freeze([18, 18]),
    position: freeze([0, -4.95, 0]),
  }),
  mainCard: freeze({
    color: 0xffbd82,
    intensity: 3.5,
    distance: 6,
    size: freeze([4.5, 3.2]),
  }),
  ceilingCard: freeze({
    color: 0xffe0c2,
    intensity: 1.25,
    position: freeze([0, 4.75, 0]),
    size: freeze([5.5, 3.5]),
    target: freeze([0, 0, 0]),
  }),
  fillCard: freeze({
    color: 0xb9cee2,
    intensity: 0.45,
    position: freeze([-4.5, 1.5, -5]),
    size: freeze([3.2, 2.4]),
    target: freeze([0, 0, 0]),
  }),
  roomLight: freeze({
    color: 0xffbd82,
    intensity: 35,
    distance: 12,
    decay: 2,
    distanceFromOrigin: 2.5,
  }),
});

const makeStops = (entries) => freeze(
  entries.map(([offset, color]) => freeze({ offset, color })));

const makeGradient = (type, entries, options = {}) => freeze({
  type,
  stops: makeStops(entries),
  ...options,
});

const radialBackground = (center, outerRadius, entries, overlay) => makeGradient(
  'radial',
  entries,
  {
    center: freeze(center),
    innerRadius: 0.04,
    outerRadius,
    ...(overlay ? { overlay: makeGradient('vertical', overlay) } : {}),
  },
);

const verticalBackground = (entries) => makeGradient('vertical', entries);
const makeAmbient = (color, intensity) => freeze({ color, intensity });
const makeHemisphere = (color, groundColor, intensity) => freeze({
  color, groundColor, intensity,
});
const makeAccent = (color, intensity, position) => freeze({
  color, intensity, position: freeze(position),
});
const makePreset = (id, label, background, ambient, hemisphere, accent) => freeze({
  id,
  label,
  ...(background ? { background } : {}),
  ...(ambient ? { ambient } : {}),
  ...(hemisphere ? { hemisphere } : {}),
  ...(accent ? { accent } : {}),
});

// Keep the visual vocabulary in one declarative table. These are presentation
// moods, not attempts to reproduce literal rooms or outdoor photographs.
export const ENVIRONMENT_PRESETS = freeze({
  default: makePreset('default', 'Default'),
  studio: makePreset(
    'studio',
    'Studio',
    radialBackground(
      [0.5, 0.4],
      0.82,
      [[0, '#4a515c'], [0.38, '#343b46'], [1, '#151b24']],
      [[0, 'rgba(0,0,0,0)'], [0.7, 'rgba(0,0,0,0.02)'],
       [1, 'rgba(0,0,0,0.24)']],
    ),
    makeAmbient(0xf5f7fa, 0.38),
    makeHemisphere(0xe1e9f3, 0x454b55, 0.42),
    makeAccent(0xffffff, 0.18, [4, 8, 6]),
  ),
  indoor: makePreset(
    'indoor',
    'Indoor',
    radialBackground(
      [0.5, 0.43],
      0.88,
      [[0, '#735746'], [0.42, '#4b382f'], [1, '#1b1717']],
      [[0, 'rgba(24,12,8,0.34)'], [0.66, 'rgba(0,0,0,0)'],
       [1, 'rgba(0,0,0,0.28)']],
    ),
    makeAmbient(0xffd4af, 0.32),
    makeHemisphere(0xffd3a6, 0x2b3440, 0.38),
    makeAccent(0xffb36b, 0.45, [4, 8, 5]),
  ),
  outdoor: makePreset(
    'outdoor',
    'Outdoor',
    verticalBackground([
      [0, '#285b8a'], [0.42, '#75a8ce'], [0.68, '#879eae'], [1, '#46515a'],
    ]),
    makeAmbient(0xdbeaff, 0.26),
    makeHemisphere(0x78b5ed, 0x667068, 0.45),
    makeAccent(0xffe6bd, 0.7, [-6, 10, 6]),
  ),
});

function directionFromPresetAccent(id) {
  return freeze(new THREE.Vector3()
    .fromArray(ENVIRONMENT_PRESETS[id].accent.position)
    .normalize()
    .toArray());
}

const DEFAULT_PMREM = freeze({size: 256, sigma: 0, near: 0.1, far: 100});

function configureOutdoorSky(sky) {
  sky.scale.setScalar(10000);
  sky.turbidity.value = OUTDOOR_SKY.turbidity;
  sky.rayleigh.value = OUTDOOR_SKY.rayleigh;
  sky.mieCoefficient.value = OUTDOOR_SKY.mieCoefficient;
  sky.mieDirectionalG.value = OUTDOOR_SKY.mieDirectionalG;
  sky.cloudCoverage.value = OUTDOOR_SKY.cloudCoverage;
  sky.cloudDensity.value = OUTDOOR_SKY.cloudDensity;
  sky.cloudElevation.value = OUTDOOR_SKY.cloudElevation;
  sky.sunPosition.value.fromArray(IBL_PROFILES.outdoor.dominantDirection);
  sky.showSunDisc.value = false;
}

function createOutdoorCaptureScene() {
  const captureScene = new THREE.Scene();
  captureScene.userData.iblPresetId = 'outdoor';
  const sky = new SkyMesh();
  configureOutdoorSky(sky);
  captureScene.add(sky);
  return captureScene;
}

function createLightCard({color, intensity, position, size, target}) {
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

function createIndoorCaptureScene() {
  const captureScene = new THREE.Scene();
  captureScene.userData.iblPresetId = 'indoor';
  const room = new THREE.Mesh(
    new THREE.BoxGeometry(...INDOOR_CAPTURE.room.size),
    new THREE.MeshStandardMaterial({
      color: INDOOR_CAPTURE.room.color,
      roughness: 1,
      metalness: 0,
      side: THREE.BackSide,
    }),
  );
  room.position.fromArray(INDOOR_CAPTURE.room.position);

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(...INDOOR_CAPTURE.floor.size),
    new THREE.MeshStandardMaterial({
      color: INDOOR_CAPTURE.floor.color,
      roughness: 1,
      metalness: 0,
    }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.fromArray(INDOOR_CAPTURE.floor.position);

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
  roomLight.position.fromArray(IBL_PROFILES.indoor.dominantDirection)
    .multiplyScalar(INDOOR_CAPTURE.roomLight.distanceFromOrigin);
  captureScene.add(room, floor, mainCard, ceilingCard, fillCard, roomLight);
  return captureScene;
}

const IBL_PROFILES = freeze({
  outdoor: freeze({
    environmentIntensity: 0.1,
    lightIntensity: freeze({
      ambient: 0.04,
      hemisphere: 0.08,
      accent: 0.4,
    }),
    dominantDirection: directionFromPresetAccent('outdoor'),
    pmrem: DEFAULT_PMREM,
    createCaptureScene: createOutdoorCaptureScene,
  }),
  indoor: freeze({
    environmentIntensity: 0.15,
    lightIntensity: freeze({
      ambient: 0.05,
      hemisphere: 0.1,
      accent: 0.3,
    }),
    dominantDirection: directionFromPresetAccent('indoor'),
    pmrem: DEFAULT_PMREM,
    createCaptureScene: createIndoorCaptureScene,
  }),
});

function disposeCaptureScene(captureScene) {
  if (!captureScene) return;
  const resources = new Set();
  captureScene.traverse(object => {
    if (object.geometry) resources.add(object.geometry);
    const materials = Array.isArray(object.material)
      ? object.material
      : object.material ? [object.material] : [];
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
  let preparationAttempted = false;
  let preparePromise = null;
  const iblResources = new Map(
    Object.keys(IBL_PROFILES).map(id => [id, {
      target: null,
      texture: null,
      attempted: false,
      available: false,
      error: null,
      generationCount: 0,
    }]),
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
    hemisphereLight.intensity = intensities?.hemisphere
      ?? preset.hemisphere.intensity;
    applyAccent(preset.accent, intensities?.accent);
  }

  function restoreEnvironment() {
    scene.environment = originalEnvironment;
    scene.environmentIntensity = originalEnvironmentIntensity;
  }

  function applyDefault() {
    scene.background = originalBackground;
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

    drawBackground(preset);
    const profile = IBL_PROFILES[id];
    const resource = iblResources.get(id);
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
    return true;
  }

  function generateIblResource(id, pmremGenerator) {
    const profile = IBL_PROFILES[id];
    const resource = iblResources.get(id);
    resource.attempted = true;
    resource.error = null;
    let captureScene = null;
    let generatedTarget = null;
    try {
      captureScene = profile.createCaptureScene();
      const {sigma, near, far, size} = profile.pmrem;
      generatedTarget = pmremGenerator.fromScene(
        captureScene, sigma, near, far, {size});
      if (!generatedTarget?.texture) {
        throw new Error(`${id} PMREM generation returned no texture.`);
      }
      resource.target = generatedTarget;
      resource.texture = generatedTarget.texture;
      resource.available = true;
      resource.generationCount += 1;
      generatedTarget = null;
      return true;
    } catch (error) {
      resource.available = false;
      resource.error = error?.message || String(error);
      console.debug(
        `${id} environment preparation failed; using baseline lighting.`,
        error,
      );
      return false;
    } finally {
      generatedTarget?.dispose?.();
      disposeCaptureScene(captureScene);
    }
  }

  async function prepare() {
    if (disposed) return false;
    if (preparePromise) return preparePromise;
    if (preparationAttempted) {
      return [...iblResources.values()].every(resource => resource.available);
    }
    preparationAttempted = true;

    preparePromise = (async () => {
      let pmremGenerator = null;
      try {
        let allAvailable = true;
        for (const id of Object.keys(IBL_PROFILES)) {
          // Yield before each GPU-heavy capture so renderer startup and the
          // intervals between resources remain available to browser work.
          await new Promise(resolve => setTimeout(resolve, 0));
          if (disposed) return false;
          pmremGenerator ||= new THREE.PMREMGenerator(renderer);
          const generated = generateIblResource(id, pmremGenerator);
          allAvailable = generated && allAvailable;
          if (generated && !disposed && currentPresetId === id) {
            applyPreset(id);
            onVisualChange?.();
          }
        }
        return allAvailable;
      } finally {
        pmremGenerator?.dispose?.();
        preparePromise = null;
      }
    })();
    return preparePromise;
  }

  function getDebugState() {
    const activeIblPreset = [...iblResources.entries()]
      .find(([, resource]) => resource.texture
        && scene.environment === resource.texture)?.[0] || null;
    const resources = Object.fromEntries(
      [...iblResources.entries()].map(([id, resource]) => [id, {
        attempted: resource.attempted,
        available: resource.available,
        generationCount: resource.generationCount,
        error: resource.error,
      }]),
    );
    return {
      preset: currentPresetId,
      activeIblPreset,
      environmentActive: activeIblPreset !== null,
      environmentIntensity: scene.environmentIntensity,
      preparationInFlight: preparePromise !== null,
      resources,
      totalPmremGenerationCount: [...iblResources.values()]
        .reduce((total, resource) => total + resource.generationCount, 0),
      activeDominantDirection: getDominantLightDirection(),
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
    applyDefault();
    for (const resource of iblResources.values()) {
      resource.target?.dispose?.();
      resource.target = null;
      resource.texture = null;
      resource.available = false;
    }
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
