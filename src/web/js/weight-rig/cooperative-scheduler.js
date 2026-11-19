// Small scheduling primitives shared by the Weight, Rig, and physics loaders.
// The time budget keeps progress predictable while giving the browser regular
// opportunities to paint and process input.

const BUDGET_MS = 8;

function now() {
  return typeof globalThis.performance?.now === 'function' ? globalThis.performance.now() : Date.now();
}

export async function yieldToBrowser() {
  if (typeof globalThis.scheduler?.yield === 'function') {
    await globalThis.scheduler.yield();
    return;
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
}

export function createWorkBudget() {
  let startedAt = now();

  return {
    async checkpoint() {
      if (now() - startedAt < BUDGET_MS) return;
      await yieldToBrowser();
      startedAt = now();
    },
  };
}
