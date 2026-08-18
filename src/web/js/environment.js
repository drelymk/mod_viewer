// Viewer-only environment presets. This module deliberately knows nothing
// about mods, meshes, INIs, or the Python bridge.

import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const SCENE_STATE_PROPERTIES = [
  'environmentIntensity',
  'environmentRotation',
];

// Keep all visual tuning in the preset table. The light scales are applied to
// captured viewer-light intensities, never to their already-scaled values.
export const ENVIRONMENT_PRESETS = Object.freeze({
  default: Object.freeze({
    id: 'default',
    type: 'default',
    label: 'Default',
    lightScale: 1,
  }),
  studio: Object.freeze({
    id: 'studio',
    type: 'room',
    label: 'Studio',
    // Keep the procedural studio neutral without stacking a full-strength
    // room map on top of the viewer's ambient and hemisphere fill.
    environmentIntensity: 0.55,
    lightScale: 0.85,
    background: 0x20252e,
  }),
  indoor: Object.freeze({
    id: 'indoor',
    type: 'room',
    label: 'Indoor',
    environmentIntensity: 0.4,
    lightScale: 0.85,
    lightProfile: 'indoor',
    background: 0x2b211e,
  }),
  outdoor: Object.freeze({
    id: 'outdoor',
    type: 'room',
    label: 'Outdoor',
    environmentIntensity: 0.85,
    lightScale: 1,
    lightProfile: 'outdoor',
    background: 0x162b43,
  }),
});

const LIGHT_PROFILES = Object.freeze({
  indoor: Object.freeze([
    Object.freeze({
      id: 'indoorKey', color: 0xffc27d, intensity: 0.8,
      position: Object.freeze([4, 8, 5]),
    }),
    Object.freeze({
      id: 'indoorFill', color: 0x9dbde5, intensity: 0.22,
      position: Object.freeze([-5, 4, -4]),
    }),
  ]),
  outdoor: Object.freeze([
    Object.freeze({
      id: 'outdoorSun', color: 0xffe7bf, intensity: 1.05,
      position: Object.freeze([-6, 10, 6]),
    }),
    Object.freeze({
      id: 'outdoorSkyFill', color: 0x8ebdff, intensity: 0.28,
      position: Object.freeze([4, 5, -6]),
    }),
  ]),
});

function cloneSceneValue(value) {
  return value && typeof value.clone === 'function' ? value.clone() : value;
}

function captureSceneState(scene) {
  const state = {
    background: scene.background,
    environment: scene.environment,
  };
  for (const property of SCENE_STATE_PROPERTIES) {
    if (property in scene) state[property] = cloneSceneValue(scene[property]);
  }
  return state;
}

function restoreSceneState(scene, state) {
  scene.background = state.background;
  scene.environment = state.environment;
  for (const property of SCENE_STATE_PROPERTIES) {
    if (!(property in state) || !(property in scene)) continue;
    const current = scene[property];
    const original = state[property];
    if (current && typeof current.copy === 'function' && original) {
      current.copy(original);
    } else {
      scene[property] = original;
    }
  }
}

function collectLights(viewerLights) {
  const values = Array.isArray(viewerLights)
    ? viewerLights
    : Object.values(viewerLights || {});
  const lights = [];
  const seen = new Set();
  for (const value of values) {
    const light = value?.light || value;
    if (!light || !light.isLight || typeof light.intensity !== 'number' ||
        seen.has(light)) continue;
    seen.add(light);
    lights.push({ light, intensity: light.intensity });
  }
  return lights;
}

function disposeRoomEnvironment(room) {
  if (!room) return;
  if (typeof room.dispose === 'function') {
    room.dispose();
    return;
  }
  const resources = new Set();
  room.traverse?.((object) => {
    if (!object.isMesh) return;
    if (object.geometry) resources.add(object.geometry);
    if (Array.isArray(object.material)) {
      object.material.forEach(material => resources.add(material));
    } else if (object.material) {
      resources.add(object.material);
    }
  });
  resources.forEach(resource => resource.dispose?.());
}

function createProfileLights(scene) {
  const target = new THREE.Object3D();
  scene.add(target);
  const lights = new Map();
  for (const profile of Object.values(LIGHT_PROFILES)) {
    for (const config of profile) {
      if (lights.has(config.id)) continue;
      const light = new THREE.DirectionalLight(config.color, 0);
      light.visible = false;
      light.target = target;
      scene.add(light);
      lights.set(config.id, light);
    }
  }
  return { target, lights };
}

/**
 * Create a scene-level environment controller.
 *
 * `subscribe()` events are intentionally data-only so the toolbar can own all
 * DOM handling. Room lighting is generated lazily and preset selection uses a
 * generation token so a future asynchronous lighting source can remain safe.
 */
export function createEnvironmentController({
  scene,
  renderer,
  viewerLights,
  requestRender = () => {},
}) {
  if (!scene || !renderer) throw new Error('EnvironmentController needs a scene and renderer.');

  const originalScene = captureSceneState(scene);
  const lights = collectLights(viewerLights);
  const subscribers = new Set();
  const profileLights = createProfileLights(scene);
  let roomTarget = null;
  let currentPresetId = 'default';
  let applyGeneration = 0;
  let disposed = false;

  function emit(event) {
    for (const subscriber of subscribers) {
      try {
        subscriber(event);
      } catch (error) {
        console.error('Environment subscriber failed:', error);
      }
    }
  }

  function requestSceneRender() {
    try {
      requestRender();
    } catch (error) {
      console.error('Environment render request failed:', error);
    }
  }

  function applyLightScale(scale) {
    for (const { light, intensity } of lights) {
      light.intensity = intensity * scale;
    }
  }

  function applyLightProfile(profileId) {
    for (const light of profileLights.lights.values()) {
      light.visible = false;
      light.intensity = 0;
    }
    for (const config of LIGHT_PROFILES[profileId] || []) {
      const light = profileLights.lights.get(config.id);
      if (!light) continue;
      light.color.set(config.color);
      light.position.fromArray(config.position);
      light.intensity = config.intensity;
      light.visible = true;
    }
  }

  function setOptionalSceneProperty(property, value) {
    if (property in scene && value !== undefined) scene[property] = value;
  }

  function applyPresetScene(preset, environment) {
    // Environments provide lighting and a dark, theme-matched presentation
    // color; they never restore or display a panorama.
    scene.background = preset.background === undefined
      ? originalScene.background
      : new THREE.Color(preset.background);
    scene.environment = environment;
    setOptionalSceneProperty('environmentIntensity', preset.environmentIntensity);
    applyLightScale(preset.lightScale ?? 1);
    applyLightProfile(preset.lightProfile);
  }

  function ensureRoomEnvironment() {
    if (roomTarget) return roomTarget;

    const pmremGenerator = new THREE.PMREMGenerator(renderer);
    const room = new RoomEnvironment(renderer);
    try {
      roomTarget = pmremGenerator.fromScene(room);
    } finally {
      disposeRoomEnvironment(room);
      pmremGenerator.dispose();
    }
    return roomTarget;
  }

  function applyDefault() {
    restoreSceneState(scene, originalScene);
    applyLightScale(ENVIRONMENT_PRESETS.default.lightScale);
    applyLightProfile(null);
  }

  async function setPreset(id) {
    const preset = ENVIRONMENT_PRESETS[id];
    if (!preset) {
      return { ok: false, id, error: new Error(`Unknown environment preset: ${id}`) };
    }
    if (disposed) {
      return { ok: false, id, error: new Error('Environment controller is disposed.') };
    }
    if (id === currentPresetId) {
      return { ok: true, id, changed: false };
    }

    const generation = ++applyGeneration;
    if (preset.type === 'default') {
      applyDefault();
      currentPresetId = preset.id;
      emit({ type: 'active', id: preset.id });
      requestSceneRender();
      return { ok: true, id: preset.id };
    }

    const isRoom = preset.type === 'room';
    if (isRoom) emit({ type: 'loading', id: preset.id, loading: true });
    try {
      const environment = ensureRoomEnvironment().texture;
      if (disposed || generation !== applyGeneration) {
        return { ok: false, id: preset.id, stale: true };
      }

      applyPresetScene(preset, environment);
      currentPresetId = preset.id;
      emit({ type: 'active', id: preset.id });
      requestSceneRender();
      return { ok: true, id: preset.id };
    } catch (error) {
      const stale = disposed || generation !== applyGeneration;
      console.error(`Could not load environment ${preset.id}:`, error);
      if (!stale) {
        emit({
          type: 'error',
          id: preset.id,
          error,
          previousId: currentPresetId,
        });
      }
      return { ok: false, id: preset.id, error, stale };
    } finally {
      if (isRoom) emit({ type: 'loading', id: preset.id, loading: false });
    }
  }

  function getPreset() {
    return ENVIRONMENT_PRESETS[currentPresetId];
  }

  function subscribe(listener) {
    if (typeof listener !== 'function') return () => {};
    subscribers.add(listener);
    return () => subscribers.delete(listener);
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    applyGeneration += 1;
    restoreSceneState(scene, originalScene);
    applyLightScale(ENVIRONMENT_PRESETS.default.lightScale);
    roomTarget?.dispose?.();
    roomTarget = null;
    for (const light of profileLights.lights.values()) scene.remove(light);
    scene.remove(profileLights.target);
    subscribers.clear();
  }

  return {
    setPreset,
    getPreset,
    getLightScale: () => ENVIRONMENT_PRESETS[currentPresetId].lightScale ?? 1,
    subscribe,
    dispose,
  };
}
