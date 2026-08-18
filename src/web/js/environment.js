// Viewer-only environment moods. This module deliberately knows nothing
// about mods, meshes, INIs, or the Python bridge.

import * as THREE from 'three';

const BACKGROUND_WIDTH = 512;
const BACKGROUND_HEIGHT = 256;
const freeze = Object.freeze;

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
 * Create the synchronous, asset-free environment controller.
 *
 * Environment presets define baseline scene lighting. The movable key light
 * in scene.js remains an independent user-controlled inspection light layered
 * on top of these presets.
 */
export function createEnvironmentController({
  scene,
  ambientLight,
  hemisphereLight,
  lightTarget,
}) {
  if (!scene || !ambientLight || !hemisphereLight || !lightTarget) {
    throw new Error('EnvironmentController needs a scene, viewer lights and target.');
  }

  const originalBackground = scene.background;
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

  function applyAccent(config) {
    if (!config) {
      accentLight.visible = false;
      accentLight.intensity = 0;
      return;
    }
    accentLight.color.set(config.color);
    // This is local to lightTarget, so the accent follows offset models.
    accentLight.position.fromArray(config.position);
    accentLight.intensity = config.intensity;
    accentLight.visible = true;
  }

  function applyLights(preset) {
    ambientLight.color.set(preset.ambient.color);
    ambientLight.intensity = preset.ambient.intensity;
    hemisphereLight.color.set(preset.hemisphere.color);
    hemisphereLight.groundColor.set(preset.hemisphere.groundColor);
    hemisphereLight.intensity = preset.hemisphere.intensity;
    applyAccent(preset.accent);
  }

  function applyDefault() {
    scene.background = originalBackground;
    restoreLightState(ambientLight, originalAmbient);
    restoreLightState(hemisphereLight, originalHemisphere);
    applyAccent(null);
  }

  function setPreset(id) {
    if (disposed) return false;
    const preset = ENVIRONMENT_PRESETS[id];
    if (!preset) return false;

    if (id === 'default') {
      applyDefault();
    } else {
      drawBackground(preset);
      applyLights(preset);
    }
    currentPresetId = preset.id;
    return true;
  }

  function getPreset() {
    return ENVIRONMENT_PRESETS[currentPresetId];
  }

  function dispose() {
    if (disposed) return;
    applyDefault();
    background.texture.dispose();
    lightTarget.remove(accentLight);
    disposed = true;
  }

  return {
    setPreset,
    getPreset,
    dispose,
  };
}
