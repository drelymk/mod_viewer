// Builds per-mesh surface influence graphs on a small same-origin module-worker
// pool so the meshes of a model build in parallel. Workers run the same pure
// accumulator as the cooperative build. The cooperative main-thread build stays
// the fallback when workers cannot start or fail to load.
import { buildSurfaceInfluenceGraphCooperative } from './weight-rig-surface.js';
import { createWorkBudget } from './cooperative-scheduler.js';

const MAX_WORKERS = 4;

class WorkersUnavailable extends Error {}

function defaultPoolSize() {
  const cores = Number(globalThis.navigator?.hardwareConcurrency) || 2;
  return Math.max(1, Math.min(MAX_WORKERS, cores - 1));
}

function defaultCreateWorker() {
  if (typeof globalThis.Worker !== 'function') return null;
  return new Worker(new URL('./surface-graph-worker.js', import.meta.url), { type: 'module' });
}

/** Create a lazily started pool; each worker runs one job at a time. */
export function createSurfaceGraphPool({ size = defaultPoolSize(), createWorker = defaultCreateWorker } = {}) {
  const slots = [];
  const queue = [];
  let disabled = false;

  // Any start or load failure moves every waiting job to the cooperative build.
  function disable() {
    disabled = true;
    const jobs = [...queue, ...slots.map((slot) => slot.job).filter(Boolean)];
    queue.length = 0;
    for (const slot of slots.splice(0)) slot.worker.terminate();
    for (const job of jobs) job.reject(new WorkersUnavailable());
  }

  function spawn() {
    let worker;
    try {
      worker = createWorker();
    } catch {
      worker = null;
    }
    if (!worker) {
      disable();
      return null;
    }
    const slot = { worker, job: null };
    worker.addEventListener('message', ({ data }) => {
      const job = slot.job;
      slot.job = null;
      if (data.error !== undefined) job.reject(new Error(data.error));
      else job.resolve(data.graph);
      pump();
    });
    worker.addEventListener('error', (event) => {
      event.preventDefault();
      disable();
    });
    slots.push(slot);
    return slot;
  }

  function pump() {
    while (queue.length && !disabled) {
      const slot = slots.find((candidate) => !candidate.job) || (slots.length < size ? spawn() : null);
      if (!slot) return;
      slot.job = queue.shift();
      slot.worker.postMessage(slot.job.args, slot.job.transfer);
    }
  }

  return {
    get available() {
      return !disabled;
    },
    // Start every worker now so module loading overlaps other work.
    prewarm() {
      while (!disabled && slots.length < size) spawn();
    },
    run(args, transfer) {
      return new Promise((resolve, reject) => {
        queue.push({ args, transfer, resolve, reject });
        pump();
      });
    },
    terminate: disable,
  };
}

// Copy only each view's bytes. A view into the shared skin download would
// otherwise clone the whole download into every job.
function ownedCopy(values) {
  return ArrayBuffer.isView(values) ? values.slice() : values;
}

let sharedPool = null;

function getSharedPool() {
  return (sharedPool ||= createSurfaceGraphPool());
}

export function prewarmSurfaceGraphWorkers() {
  getSharedPool().prewarm();
}

/**
 * Build one mesh's surface influence graph on the worker pool. If workers are
 * unavailable, use the cooperative build instead. Both produce the same graph.
 */
export async function buildSurfaceInfluenceGraphParallel(
  baselinePositions,
  triangleIndices,
  indices,
  weights,
  influenceCount,
  boneIds = null,
  boundingSphereRadius = null,
  { budget = createWorkBudget(), isCurrent = () => true, pool = null } = {},
) {
  await budget.checkpoint();
  if (!isCurrent()) return null;
  const workers = pool || getSharedPool();
  if (workers.available) {
    const arrays = [baselinePositions, triangleIndices ?? null, indices, weights].map(ownedCopy);
    const transfer = arrays.filter((values) => ArrayBuffer.isView(values)).map((values) => values.buffer);
    try {
      const graph = await workers.run([...arrays, influenceCount, boneIds, boundingSphereRadius], transfer);
      return isCurrent() ? graph : null;
    } catch (error) {
      if (!(error instanceof WorkersUnavailable)) throw error;
    }
  }
  return buildSurfaceInfluenceGraphCooperative(
    baselinePositions,
    triangleIndices,
    indices,
    weights,
    influenceCount,
    boneIds,
    boundingSphereRadius,
    { budget, isCurrent },
  );
}
