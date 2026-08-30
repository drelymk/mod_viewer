const STAT_NAMES = Object.freeze([
  'physicsFrameCount',
  'physicsStepCount',
  'physicsStepMs',
  'physicsTransformBuildCount',
  'physicsTransformMs',
  'physicsDeformCount',
  'physicsDeformMs',
  'physicsDeformedVertexCount',
  'physicsNormalUpdateCount',
  'physicsNormalMs',
  'physicsBoundsUpdateCount',
  'physicsBoundsMs',
  'physicsUiNotifyCount',
  'dynamicShadowUpdateCount',
  'shadowFitCount',
  'sourcePhysicsRigCount',
  'sourcePhysicsStepCount',
  'sourceTransformBuildCount',
  'participatingPhysicsMeshCount',
]);

const stats = Object.fromEntries(STAT_NAMES.map(name => [name, 0]));

export function performanceNow() {
  return globalThis.performance?.now?.() ?? Date.now();
}

export function addWeightPhysicsPerformance(name, value = 1) {
  if (!Object.hasOwn(stats, name)) return;
  const amount = Number(value);
  if (Number.isFinite(amount)) stats[name] += amount;
}

export function getWeightPhysicsPerformanceStats() {
  return {...stats};
}

export function resetWeightPhysicsPerformanceStats() {
  STAT_NAMES.forEach(name => { stats[name] = 0; });
  return getWeightPhysicsPerformanceStats();
}
