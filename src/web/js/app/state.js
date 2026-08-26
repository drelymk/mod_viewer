// Mutable application/session state shared by the frontend flow modules.

export const viewerState = {
  currentModPath: null,
  currentSource: null,

  displayedModPath: null,
  displayedSource: null,

  semanticRefreshEpoch: 0,
  modTransitionInFlight: false,

  rightDockEnabled: false,

  lastToggles: {},

  assetFill: {
    available: false,
    loaded: false,
    loading: false,
    textureKeys: new Set(),
    fillId: null,
    epoch: 0,
  },
};

/** Normalize paths for same-folder comparisons across Windows path spellings. */
export function samePath(a, b) {
  const norm = (path) => path.replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '');
  return !!a && !!b && norm(a) === norm(b);
}
