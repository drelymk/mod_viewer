// Tiny keyed registry for synchronizing current panel views after model state.

const syncers = new Map();

export function registerViewSync(name, sync) {
  if (typeof sync === 'function') syncers.set(name, sync);
  else syncers.delete(name);
}

export function syncView(name) {
  syncers.get(name)?.();
}

export function syncViews() {
  for (const sync of syncers.values()) sync();
}

export function clearViewSyncs() {
  syncers.clear();
}

export function viewSyncCount() {
  return syncers.size;
}
