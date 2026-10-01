function afterWeightReadyPaint() {
  return new Promise((resolve) => {
    const afterFrame =
      typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (callback) => setTimeout(callback, 0);
    afterFrame(() => setTimeout(resolve, 0));
  });
}

/** Own the Weight-ready, saved-selection, Rig-build, and Physics-restore stages. */
export function createWeightRigActivationSession({
  getGeneration,
  ensureWeightsLoaded,
  restoreSavedSelection,
  ensureRigLoaded,
  syncPhysicsToSelection,
  waitForWeightReadyPaint = afterWeightReadyPaint,
} = {}) {
  let activationSerial = 0;
  let activeActivation = null;
  let completedActivation = null;
  let paintedGeneration = null;
  let physicsReadyGeneration = null;

  function activate() {
    const generation = getGeneration();
    if (completedActivation?.generation === generation) return Promise.resolve(completedActivation.result);
    if (activeActivation?.generation === generation) return activeActivation.promise;
    const serial = ++activationSerial;
    const isCurrent = () => serial === activationSerial && generation === getGeneration();

    const promise = (async () => {
      let weight;
      try {
        weight = await ensureWeightsLoaded();
      } catch (error) {
        return { weight: { loaded: false, error: error instanceof Error ? error.message : String(error) } };
      }
      if (!isCurrent() || !weight?.loaded || weight.error) return { weight };
      if (weight.noWeights) {
        const result = { weight };
        if (isCurrent()) completedActivation = { generation, result };
        return result;
      }

      if (paintedGeneration !== generation) {
        await waitForWeightReadyPaint();
        if (!isCurrent()) return { weight, stale: true };
        paintedGeneration = generation;
      }

      let selection;
      try {
        selection = await restoreSavedSelection?.({ generation, isCurrent });
      } catch (error) {
        selection = { error: error instanceof Error ? error.message : String(error) };
      }
      if (!isCurrent()) return { weight, selection, stale: true };
      const selectionReady = selection !== false && !selection?.error;

      // Physics consumes the selected source rigs directly and remains useful
      // when cross-source model reconciliation fails.
      const [physics, rig] = await Promise.all([
        physicsReadyGeneration === generation
          ? Promise.resolve({ restored: true, reused: true })
          : Promise.resolve()
              .then(() => syncPhysicsToSelection?.())
              .catch((error) => ({ error: error instanceof Error ? error.message : String(error) })),
        Promise.resolve()
          .then(() => ensureRigLoaded?.())
          .catch((error) => ({ error: error instanceof Error ? error.message : String(error) })),
      ]);
      const result = { weight, selection, physics, rig, stale: !isCurrent() };
      const physicsReady = physics !== false && !physics?.error;
      if (isCurrent() && physicsReady) physicsReadyGeneration = generation;
      if (isCurrent() && rig?.loaded && !rig.error && physicsReady && selectionReady) {
        completedActivation = { generation, result };
      }
      return result;
    })().finally(() => {
      if (activeActivation?.serial === serial) activeActivation = null;
    });

    activeActivation = { generation, serial, promise };
    return promise;
  }

  function invalidate() {
    activationSerial += 1;
    activeActivation = null;
    completedActivation = null;
    paintedGeneration = null;
    physicsReadyGeneration = null;
  }

  return { activate, invalidate };
}
