// One readiness boundary for the bundled native bridge.

let readiness = null;

export function bridgeReady() {
  if (!readiness) {
    readiness = new Promise((resolve, reject) => {
      const ready = () => {
        const api = window.pywebview?.api;
        if (api) resolve(api);
        else reject(new Error('Native bridge is unavailable after readiness.'));
      };
      if (window.pywebview?.api) ready();
      else window.addEventListener('pywebviewready', ready, { once: true });
    });
  }
  return readiness;
}

export function reportPersistenceFailure(error) {
  console.error('Viewer persistence failed:', error);
  window.dispatchEvent(new CustomEvent('viewer-persistence-error', { detail: error }));
}
