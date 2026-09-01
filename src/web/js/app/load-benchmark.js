// Opt-in timing probe for the real mod-open path. The benchmark enables this
// through a page init script; ordinary viewer loads do not collect timings.

let current = null;
let nextId = 0;

function enabled() {
  return globalThis.__modViewerBenchmark?.enabled === true;
}

function snapshot(state) {
  if (!state) return null;
  return {
    ...state,
    stages: {...state.stages},
  };
}

export function beginLoadBenchmark() {
  if (!enabled()) {
    current = null;
    return null;
  }

  const state = {
    id: ++nextId,
    started: performance.now(),
    finished: false,
    stages: {},
    first_model_frame_seconds: null,
  };
  current = state;
  return state;
}

function recordStage(state, name, started) {
  state.stages[name] = Math.max(0, performance.now() - started) / 1000;
}

export function measureLoadStage(name, operation) {
  const state = current;
  if (!state) return operation();
  const started = performance.now();
  try {
    return operation();
  } finally {
    recordStage(state, name, started);
  }
}

export function measureAsyncLoadStage(name, operation) {
  const state = current;
  if (!state) return operation();
  const started = performance.now();
  try {
    return Promise.resolve(operation()).finally(() => {
      recordStage(state, name, started);
    });
  } catch (error) {
    recordStage(state, name, started);
    throw error;
  }
}

function scheduleFirstModelFrame(state) {
  requestAnimationFrame(() => {
    if (current !== state) return;
    state.first_model_frame_seconds = Math.max(
      0, performance.now() - state.started) / 1000;
  });
}

export function finishLoadBenchmark(fields = {}) {
  if (!current) return null;
  const state = current;
  current.finished = true;
  current.finished_at = performance.now();
  current.total_seconds = Math.max(
    0, current.finished_at - current.started) / 1000;
  Object.assign(current, fields);
  if (fields.success === true) scheduleFirstModelFrame(state);
  return snapshot(current);
}

export function getLoadBenchmark() {
  return snapshot(current);
}
