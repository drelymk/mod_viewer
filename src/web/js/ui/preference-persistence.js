// Shared bridge lifecycle and ordered writes for global app preferences.

let saveQueue = Promise.resolve();

export function createPreferencePersistence({ getMethod, setMethod, key = null, apply }) {
  const touched = new Set();
  const pending = new Map();
  let loaded = false;
  let loading = null;
  let saving = false;

  async function flush() {
    if (!loaded || saving || !pending.size) return;
    const api = window.pywebview?.api;
    if (typeof api?.[setMethod] !== 'function') return;
    saving = true;
    try {
      while (pending.size) {
        const changes = Object.fromEntries(pending);
        pending.clear();
        const write = saveQueue.then(() => api[setMethod](key ? changes[key] : changes));
        saveQueue = write.catch(() => {});
        try {
          const result = await write;
          if (result?.error) throw new Error(result.error);
        } catch (error) {
          for (const [name, value] of Object.entries(changes)) {
            if (!pending.has(name)) pending.set(name, value);
          }
          console.error(error);
          break;
        }
      }
    } finally {
      saving = false;
    }
  }

  async function load() {
    if (loaded || loading) return loading;
    const api = window.pywebview?.api;
    if (typeof api?.[getMethod] !== 'function') return;
    loading = (async () => {
      try {
        const result = await api[getMethod]();
        if (result?.error) throw new Error(result.error);
        const values = key ? { [key]: result?.value } : result?.value || {};
        for (const [name, value] of Object.entries(values)) {
          if (!touched.has(name)) apply(name, value);
        }
        loaded = true;
        await flush();
      } catch (error) {
        console.error(error);
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  window.addEventListener('pywebviewready', () => void load(), { once: true });
  void load();
  return {
    change(name, value, { persist = true } = {}) {
      touched.add(name);
      if (persist) {
        pending.set(name, value);
        void flush();
      }
    },
  };
}
