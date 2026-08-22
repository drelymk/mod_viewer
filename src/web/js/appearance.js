// Global floating-panel opacity control backed by the app config.

const DEFAULT_PANEL_OPACITY = 58;

const $ = id => document.getElementById(id);

function normalizeOpacity(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return DEFAULT_PANEL_OPACITY;
  return Math.min(100, Math.max(0, Math.round(number)));
}

export function initPanelOpacityControl() {
  const button = $('appearance-btn');
  const popover = $('appearance-popover');
  const slider = $('panel-opacity');
  const output = $('panel-opacity-value');
  if (!button || !popover || !slider || !output) return;

  let loadedOpacity = null;
  let pendingOpacity = null;
  let userChanged = false;

  const apply = value => {
    const opacity = normalizeOpacity(value);
    const factor = opacity / 100;
    slider.value = String(opacity);
    output.value = `${opacity}%`;
    output.textContent = `${opacity}%`;
    document.documentElement.style.setProperty('--panel-opacity', String(factor));
    document.documentElement.style.setProperty('--panel-blur', `${factor * 10}px`);
    document.documentElement.style.setProperty(
      '--panel-shadow-opacity', String(factor * 0.22));
    button.setAttribute('aria-label', `Panel opacity: ${opacity}%`);
    button.title = `Panel opacity: ${opacity}%`;
    return opacity;
  };
  const close = (restoreFocus = false) => {
    popover.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (restoreFocus) button.focus();
  };

  apply(DEFAULT_PANEL_OPACITY);
  button.addEventListener('click', event => {
    event.stopPropagation();
    popover.hidden = !popover.hidden;
    button.setAttribute('aria-expanded', String(!popover.hidden));
    if (!popover.hidden) slider.focus();
  });
  const saveOpacity = async opacity => {
    if (loadedOpacity === null) {
      pendingOpacity = opacity;
      return;
    }
    if (opacity === loadedOpacity) return;
    const save = window.pywebview?.api?.set_panel_opacity;
    if (typeof save !== 'function') {
      pendingOpacity = opacity;
      return;
    }
    try {
      const result = await save.call(window.pywebview.api, opacity);
      if (result?.error) {
        console.error(result.error);
        return;
      }
      loadedOpacity = normalizeOpacity(result?.value ?? opacity);
      pendingOpacity = null;
    } catch (error) {
      console.error(error);
    }
  };
  const loadOpacity = async () => {
    const load = window.pywebview?.api?.get_panel_opacity;
    if (typeof load !== 'function') return false;
    try {
      const result = await load.call(window.pywebview.api);
      if (result?.error) {
        console.error(result.error);
        return true;
      }
      loadedOpacity = normalizeOpacity(result?.value);
      if (!userChanged) apply(loadedOpacity);
      else if (pendingOpacity !== null) await saveOpacity(pendingOpacity);
    } catch (error) {
      console.error(error);
    }
    return true;
  };

  slider.addEventListener('input', () => {
    userChanged = true;
    apply(slider.value);
  });
  slider.addEventListener('change', () => {
    userChanged = true;
    const opacity = apply(slider.value);
    void saveOpacity(opacity);
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.appearance-wrap')) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !popover.hidden) close(true);
  });

  void loadOpacity().then(loaded => {
    if (!loaded) window.addEventListener('pywebviewready', loadOpacity, {once: true});
  });
}
