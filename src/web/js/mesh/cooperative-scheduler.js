// Small scheduling primitives shared by the Weight, Rig, and physics loaders.
// The time budget keeps progress predictable while giving the browser regular
// opportunities to paint and process input.

const DEFAULT_BUDGET_MS = 8;

function now() {
  return typeof globalThis.performance?.now === 'function'
    ? globalThis.performance.now() : Date.now();
}

export async function yieldToBrowser() {
  if (typeof globalThis.scheduler?.yield === 'function') {
    await globalThis.scheduler.yield();
    return;
  }
  await new Promise(resolve => setTimeout(resolve, 0));
}

export function createWorkBudget({budgetMs = DEFAULT_BUDGET_MS,
    onYield = null} = {}) {
  const limit = Number.isFinite(Number(budgetMs)) && Number(budgetMs) > 0
    ? Number(budgetMs) : DEFAULT_BUDGET_MS;
  let startedAt = now();
  let largestChunkMs = 0;
  let yieldCount = 0;

  return {
    async checkpoint() {
      const elapsed = now() - startedAt;
      largestChunkMs = Math.max(largestChunkMs, elapsed);
      if (elapsed < limit) return;
      yieldCount += 1;
      if (typeof onYield === 'function') onYield(elapsed);
      await yieldToBrowser();
      startedAt = now();
    },
    getStats() {
      return {largestChunkMs, yieldCount};
    },
  };
}

export const COOPERATIVE_BUDGET_MS = DEFAULT_BUDGET_MS;
