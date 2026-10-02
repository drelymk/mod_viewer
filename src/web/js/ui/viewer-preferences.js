// Persist viewer controls without serializing model or camera orientation.

import {
  activeMeshes,
  toggleGlossy,
  toggleSmoothShading,
  toggleToonShading,
  toggleWireframe,
} from '../mesh/visibility.js';
import {
  getBloomEnabled,
  setAmbientOcclusionStrength,
  setBloomEnabled,
  setKeyLightIntensity,
  toggleGrid,
  toggleTrackballGizmo,
} from '../scene/scene.js';
import { setTextureDisplayMode } from '../scene/render-modes.js';
import { setOutlinesEnabled } from '../scene/outline-renderer.js';
import { LANGUAGE_CHANGED, t } from '../i18n/index.js';
import { createPreferencePersistence } from './preference-persistence.js';

const $ = (id) => document.getElementById(id);

export function initViewerPreferences({ applyEnvironmentPreset, syncToolControls, syncBloomControl }) {
  const applyOutlines = (value) => {
    const enabled = setOutlinesEnabled(value);
    const button = $('outline-btn');
    button.classList.toggle('active', enabled);
    button.setAttribute('aria-pressed', String(enabled));
    const label = t('render.outlines', { state: enabled ? t('common.on') : t('common.off') });
    button.title = label;
    button.setAttribute('aria-label', label);
    return enabled;
  };
  const booleanTools = {
    wireframe: ['wire-btn', toggleWireframe],
    outlines: ['outline-btn', () => applyOutlines()],
    bloom: [
      'bloom-btn',
      () => {
        setBloomEnabled(!getBloomEnabled());
        syncBloomControl();
      },
    ],
    glossy: ['glossy-btn', toggleGlossy],
    toonShading: ['toon-btn', toggleToonShading],
    grid: ['grid-btn', toggleGrid],
    smoothShading: ['shading-btn', toggleSmoothShading],
    navigationGizmo: ['trackball-btn', toggleTrackballGizmo],
  };
  const readBoolean = (name) =>
    name === 'bloom' ? getBloomEnabled() : $(booleanTools[name][0]).getAttribute('aria-pressed') === 'true';
  const persistence = createPreferencePersistence({
    getMethod: 'get_viewer_preferences',
    setMethod: 'set_viewer_preferences',
    apply(name, value) {
      if (Object.hasOwn(booleanTools, name)) {
        if (typeof value === 'boolean' && readBoolean(name) !== value) booleanTools[name][1]();
      } else if (name === 'environment') {
        applyEnvironmentPreset(value);
      } else if (name === 'textureMode') {
        setTextureDisplayMode(value, activeMeshes);
      } else if (name === 'ambientOcclusion') {
        setAmbientOcclusionStrength(value);
        syncToolControls();
      } else if (name === 'keyLightIntensity') {
        setKeyLightIntensity(value);
        syncToolControls();
      }
    },
  });
  for (const [name, [id, toggle]] of Object.entries(booleanTools)) {
    $(id).addEventListener('click', () => {
      toggle();
      persistence.change(name, readBoolean(name));
    });
  }
  window.addEventListener(LANGUAGE_CHANGED, () => {
    const button = $('grid-btn');
    const label = t('render.grid', {
      state: readBoolean('grid') ? t('common.on') : t('common.off'),
    });
    button.title = label;
    button.setAttribute('aria-label', label);
  });
  return { ...persistence, applyOutlines };
}
