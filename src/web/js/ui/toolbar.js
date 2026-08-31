// Toolbar controls that only coordinate DOM state and viewer-wide controls.

import { activeMeshes } from '../mesh/visibility.js';
import { ENVIRONMENT_PRESETS } from '../scene/environment.js';
import {
  getAmbientOcclusionStrength, getEnvironmentPreset, getKeyLightIntensity,
  setAmbientOcclusionStrength, setEnvironmentPreset, setKeyLightIntensity,
} from '../scene/scene.js';
import { KEY_LIGHT_MAX_INTENSITY } from '../scene/key-light-controller.js';
import { setTextureDisplayMode } from '../scene/render-modes.js';

const $ = (id) => document.getElementById(id);
const AO_MAX_STRENGTH = 1;

function normalizeAmbientOcclusionLevel(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.min(100, Math.max(0, Math.round(number)));
}

function strengthToAmbientOcclusionLevel(value) {
  return normalizeAmbientOcclusionLevel(
    Number(value) / AO_MAX_STRENGTH * 100);
}

function normalizeKeyLightLevel(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.min(100, Math.max(0, Math.round(number)));
}

function intensityToKeyLightLevel(value) {
  return normalizeKeyLightLevel(
    Number(value) / KEY_LIGHT_MAX_INTENSITY * 100);
}

export function initEnvironmentControl() {
  const button = $('environment-btn');
  const icon = $('environment-icon');
  const labels = Object.fromEntries(
    Object.values(ENVIRONMENT_PRESETS).map(preset => [preset.id, preset.label]));
  const popover = $('environment-popover');
  let currentId = getEnvironmentPreset().id;

  function updateControl(id) {
    const name = labels[id] || id;
    icon.dataset.environment = id;
    button.dataset.environment = id;
    button.setAttribute('aria-label', `Environment: ${name}. Click to change.`);
    button.title = `Environment: ${name} (click to change)`;
  }

  function applyEnvironmentPreset(id) {
    if (!setEnvironmentPreset(id)) return false;
    currentId = getEnvironmentPreset().id;
    updateControl(currentId);
    return true;
  }

  function closePopover() {
    if (!popover) return;
    popover.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  }

  function openPopover() {
    if (!popover) return;
    popover.replaceChildren();
    Object.values(ENVIRONMENT_PRESETS).forEach(preset => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'ui-popover-option';
      option.setAttribute('role', 'menuitem');
      option.textContent = preset.label;
      option.classList.toggle('selected', preset.id === currentId);
      option.addEventListener('click', () => {
        applyEnvironmentPreset(preset.id);
        closePopover();
      });
      popover.appendChild(option);
    });
    popover.hidden = false;
    button.setAttribute('aria-expanded', 'true');
  }

  updateControl(currentId);
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', () => {
    if (popover?.hidden === false) closePopover();
    else openPopover();
  });
  document.addEventListener('click', event => {
    if (!popover || popover.hidden || event.target.closest('#environment-control, #environment-popover')) return;
    closePopover();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closePopover();
  });

  return applyEnvironmentPreset;
}

export function initToolPopovers() {
  const textureButton = $('texture-btn');
  const lightButton = $('light-btn');
  const aoButton = $('ao-btn');
  const texturePopover = $('texture-popover');
  const lightPopover = $('light-popover');
  const aoPopover = $('ao-popover');
  const aoSlider = $('ao-slider');
  const aoValue = $('ao-value');
  const lightSlider = $('light-slider');
  const lightValue = $('light-value');
  const close = popover => {
    if (!popover) return;
    popover.hidden = true;
  };
  let activeToolPopover = null;
  const positionPopover = (popover, button) => {
    if (!popover || !button) return;
    const buttonRect = button.getBoundingClientRect();
    const popoverRect = popover.getBoundingClientRect();
    const gutter = 8;
    const maxLeft = Math.max(gutter, window.innerWidth - popoverRect.width - gutter);
    const desiredLeft = buttonRect.left
      + (buttonRect.width - popoverRect.width) / 2;
    const left = Math.min(maxLeft, Math.max(gutter, desiredLeft));
    const top = Math.max(gutter, buttonRect.top - popoverRect.height - gutter);
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
  };
  const closeAll = () => {
    close(texturePopover);
    close(lightPopover);
    close(aoPopover);
    textureButton?.setAttribute('aria-expanded', 'false');
    lightButton?.setAttribute('aria-expanded', 'false');
    aoButton?.setAttribute('aria-expanded', 'false');
    activeToolPopover = null;
  };

  const updateAmbientOcclusionControl = value => {
    const level = normalizeAmbientOcclusionLevel(value);
    if (aoSlider) aoSlider.value = String(level);
    if (aoValue) {
      aoValue.value = `${level}%`;
      aoValue.textContent = `${level}%`;
    }
    aoButton?.classList.toggle('active', level === 100);
    aoButton?.classList.toggle('partial', level > 0 && level < 100);
    const label = `Ambient occlusion: ${level}%`;
    aoButton?.setAttribute('aria-label', label);
    if (aoButton) aoButton.title = label;
    return level;
  };

  const applyAmbientOcclusionLevel = value => {
    const level = normalizeAmbientOcclusionLevel(value);
    setAmbientOcclusionStrength(level / 100 * AO_MAX_STRENGTH);
    return updateAmbientOcclusionControl(
      strengthToAmbientOcclusionLevel(getAmbientOcclusionStrength()));
  };

  function toggleTexturePopover() {
    if (!texturePopover) return;
    const wasOpen = !texturePopover.hidden;
    closeAll();
    if (wasOpen) return;
    texturePopover.replaceChildren();
    [
      ['all', 'All maps'],
      ['diffuse-normal', 'Diffuse and NormalMap'],
      ['diffuse', 'Diffuse only'],
      ['none', 'No textures'],
    ].forEach(([mode, label]) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'ui-popover-option';
      option.setAttribute('role', 'menuitem');
      option.textContent = label;
      option.addEventListener('click', () => {
        setTextureDisplayMode(mode, activeMeshes);
        closeAll();
      });
      texturePopover.appendChild(option);
    });
    texturePopover.hidden = false;
    textureButton?.setAttribute('aria-expanded', 'true');
    activeToolPopover = { popover: texturePopover, button: textureButton };
    positionPopover(texturePopover, textureButton);
  }

  const updateKeyLightControl = value => {
    const level = normalizeKeyLightLevel(value);
    if (lightSlider) lightSlider.value = String(level);
    if (lightValue) {
      lightValue.value = level + '%';
      lightValue.textContent = level + '%';
    }
    lightButton?.classList.toggle('active', level === 100);
    lightButton?.classList.toggle('partial', level > 0 && level < 100);
    lightButton?.classList.toggle('off', level === 0);
    const label = 'Key light: ' + (level === 0 ? 'Off' : level + '%');
    lightButton?.setAttribute('aria-label', label);
    if (lightButton) lightButton.title = label;
    return level;
  };

  const applyKeyLightLevel = value => {
    const level = normalizeKeyLightLevel(value);
    setKeyLightIntensity(level / 100 * KEY_LIGHT_MAX_INTENSITY);
    return updateKeyLightControl(
      intensityToKeyLightLevel(getKeyLightIntensity()));
  };

  function toggleLightPopover() {
    if (!lightPopover) return;
    const wasOpen = !lightPopover.hidden;
    closeAll();
    if (wasOpen) return;
    updateKeyLightControl(intensityToKeyLightLevel(getKeyLightIntensity()));
    lightPopover.hidden = false;
    lightButton?.setAttribute('aria-expanded', 'true');
    activeToolPopover = { popover: lightPopover, button: lightButton };
    positionPopover(lightPopover, lightButton);
    lightSlider?.focus();
  }

  function toggleAmbientOcclusionPopover() {
    if (!aoPopover) return;
    const wasOpen = !aoPopover.hidden;
    closeAll();
    if (wasOpen) return;
    updateAmbientOcclusionControl(
      strengthToAmbientOcclusionLevel(getAmbientOcclusionStrength()));
    aoPopover.hidden = false;
    aoButton?.setAttribute('aria-expanded', 'true');
    activeToolPopover = { popover: aoPopover, button: aoButton };
    positionPopover(aoPopover, aoButton);
    aoSlider?.focus();
  }

  textureButton?.setAttribute('aria-haspopup', 'menu');
  lightButton?.setAttribute('aria-haspopup', 'dialog');
  aoButton?.setAttribute('aria-haspopup', 'dialog');
  textureButton?.setAttribute('aria-expanded', 'false');
  lightButton?.setAttribute('aria-expanded', 'false');
  aoButton?.setAttribute('aria-expanded', 'false');
  updateAmbientOcclusionControl(
    strengthToAmbientOcclusionLevel(getAmbientOcclusionStrength()));
  updateKeyLightControl(intensityToKeyLightLevel(getKeyLightIntensity()));
  textureButton?.addEventListener('click', toggleTexturePopover);
  lightButton?.addEventListener('click', toggleLightPopover);
  aoButton?.addEventListener('click', toggleAmbientOcclusionPopover);
  lightSlider?.addEventListener('input', () => applyKeyLightLevel(lightSlider.value));
  aoSlider?.addEventListener('input', () => applyAmbientOcclusionLevel(aoSlider.value));
  document.addEventListener('click', event => {
    if (event.target.closest(
      '#texture-btn, #texture-popover, #light-btn, #light-popover, #ao-btn, #ao-popover')) {
      return;
    }
    closeAll();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeAll();
  });
  window.addEventListener('resize', () => {
    if (activeToolPopover) {
      positionPopover(activeToolPopover.popover, activeToolPopover.button);
    }
  });

  return () => updateAmbientOcclusionControl(
    strengthToAmbientOcclusionLevel(getAmbientOcclusionStrength()));
}

export function initToolbarOverflow() {
  const button = $('toolbar-more');
  const menu = $('toolbar-overflow');
  if (!button || !menu) return;
  const close = () => {
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  };
  button.addEventListener('click', event => {
    event.stopPropagation();
    menu.hidden = !menu.hidden;
    button.setAttribute('aria-expanded', String(!menu.hidden));
  });
  menu.addEventListener('click', event => {
    const item = event.target.closest('[data-toolbar-target]');
    if (!item) return;
    const target = $(item.dataset.toolbarTarget);
    if (target && !target.disabled) target.click();
    close();
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('#toolbar-more, #toolbar-overflow')) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') close();
  });
}
