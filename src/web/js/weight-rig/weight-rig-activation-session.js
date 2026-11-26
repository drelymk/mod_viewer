function afterWeightReadyPaint() {
  return new Promise((resolve) => {
    const afterFrame =
      typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (callback) => setTimeout(callback, 0);
    afterFrame(() => setTimeout(resolve, 0));
  });
}

const failure = (error) => ({ error: error instanceof Error ? error.message : String(error) });
const succeeded = (result) => result !== false && !result?.error;

/**
 * Activate Weight/Rig in two stages. The shared stage decodes the model's
 * weights, which every feature reads, and lets the Weight-ready state paint.
 * Then three independent branches run in parallel:
 * - weight: restore the saved bone selection, then sync Physics to it (Physics
 *   consumes the selected source rigs directly, so it never waits on the Rig);
 * - rig: build the model rig;
 * - animation: publish the game-clip library, which needs only the weights.
 * A branch that succeeds is kept until `invalidate()` (called on every model
 * rebaseline); a failed one retries on the next activation without rerunning
 * the others.
 */
export function createWeightRigActivationSession({
  getGeneration,
  ensureWeightsLoaded,
  branches = {},
  waitForWeightReadyPaint = afterWeightReadyPaint,
} = {}) {
  let activationSerial = 0;
  let activeActivation = null;
  let painted = false;
  const done = new Map();

  function runBranch(name, isCurrent) {
    if (done.has(name)) return Promise.resolve(done.get(name));
    return Promise.resolve()
      .then(() => branches[name]?.({ isCurrent }))
      .catch(failure)
      .then((result) => {
        if (isCurrent() && succeeded(result)) done.set(name, result);
        return result;
      });
  }

  function activate() {
    const generation = getGeneration();
    if (activeActivation?.generation === generation) return activeActivation.promise;
    const serial = ++activationSerial;
    const isCurrent = () => serial === activationSerial && generation === getGeneration();

    const promise = (async () => {
      let weights;
      try {
        weights = await ensureWeightsLoaded();
      } catch (error) {
        return { weights: { loaded: false, ...failure(error) } };
      }
      if (!isCurrent() || !weights?.loaded || weights.error || weights.noWeights) return { weights };
      if (!painted) {
        await waitForWeightReadyPaint();
        if (!isCurrent()) return { weights, stale: true };
        painted = true;
      }
      const [weight, rig, animation] = await Promise.all(
        ['weight', 'rig', 'animation'].map((name) => runBranch(name, isCurrent)),
      );
      return { weights, weight, rig, animation, stale: !isCurrent() };
    })().finally(() => {
      if (activeActivation?.serial === serial) activeActivation = null;
    });

    activeActivation = { generation, serial, promise };
    return promise;
  }

  function invalidate() {
    activationSerial += 1;
    activeActivation = null;
    painted = false;
    done.clear();
  }

  return { activate, invalidate };
}
