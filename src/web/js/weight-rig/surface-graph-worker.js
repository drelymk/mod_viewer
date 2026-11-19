// Module worker that builds one mesh's surface influence graph off the main
// thread. It runs the same pure accumulator as the cooperative build.
import { buildSurfaceInfluenceGraph } from './weight-rig-surface.js';

self.addEventListener('message', ({ data }) => {
  try {
    self.postMessage({ graph: buildSurfaceInfluenceGraph(...data) });
  } catch (error) {
    self.postMessage({ error: error instanceof Error ? error.message : String(error) });
  }
});
