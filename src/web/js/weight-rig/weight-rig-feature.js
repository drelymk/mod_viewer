// The optional Weight/Rig feature boundary.
//
// Keep this module free of static imports from the Weight/Rig graph. Normal
// model loading can register mesh lifecycle state here without loading the
// feature; explicit Weight/Rig activation or weighted export hydrates that
// state into the real runtime.

let featurePromise = null;
let feature = null;
let coreModule = null;
let corePromise = null;
let skinning = null;
let pendingRigMetadata = null;
let pendingHumanoidRigMetadata = null;
const pendingSkinningMeshes = new Set();

export function getLoadedWeightRigFeature() {
  return feature;
}

export function setPendingRigMetadata(metadata) {
  pendingRigMetadata = metadata;
  if (feature) feature.setRigMetadata(metadata);
}

export function setPendingHumanoidRigMetadata(metadata) {
  pendingHumanoidRigMetadata = metadata;
  if (feature) feature.setHumanoidRigMetadata(metadata);
}

export function getSkinningBaseMaterial(mesh) {
  return skinning?.getSkinningBaseMaterial(mesh) || mesh?.material;
}

export function withSkinningBaseMaterial(mesh, operation) {
  return skinning ? skinning.withSkinningBaseMaterial(mesh, operation) : operation();
}

export function registerSkinningMesh(mesh) {
  pendingSkinningMeshes.add(mesh);
  return coreModule?.registerWeightRigMesh(mesh);
}

export function unregisterSkinningMesh(mesh) {
  pendingSkinningMeshes.delete(mesh);
  return coreModule?.unregisterWeightRigMesh(mesh);
}

export function refreshSkinningAfterShapeChange(mesh) {
  return coreModule?.refreshWeightRigAfterShapeChange(mesh) || false;
}

export function disposeSkinningExperiment(mesh, options = {}) {
  pendingSkinningMeshes.delete(mesh);
  return coreModule?.disposeWeightRigMesh(mesh, options);
}

export function destroyModelPhysicsSession() {
  pendingSkinningMeshes.clear();
  return coreModule?.destroyWeightRigModel();
}

export async function loadWeightRigFeature() {
  if (feature) return feature;
  if (featurePromise) return featurePromise;

  featurePromise = (async () => {
    const core = await loadCore();
    const [panel, overlay, status] = await Promise.all([
      import('../panels/weight-rig-panel.js'),
      import('../scene/rig-overlay-controller.js'),
      import('./weight-rig-status.js'),
    ]);
    feature = {
      ...core.weightRigApi,
      createRigOverlayController: overlay.createRigOverlayController,
      weightRigStatus: status.weightRigStatus,
    };
    panel.initWeightRigPanel();
    return feature;
  })();

  return featurePromise;
}

async function loadCore() {
  corePromise ||= import('./weight-rig-core.js').then((core) => {
    coreModule = core;
    skinning = core.weightRigSkinningRuntime;
    for (const mesh of pendingSkinningMeshes) core.registerWeightRigMesh(mesh);
    core.weightRigApi.setRigMetadata(pendingRigMetadata);
    core.weightRigApi.setHumanoidRigMetadata(pendingHumanoidRigMetadata);
    return core;
  });
  return corePromise;
}

// Load authored weights without activating panels, inferred rigs, or Physics.
export async function loadModelExportSkinning(meshes) {
  if (!meshes.some((mesh) => mesh.visible && mesh.userData.skinningAvailable)) return new Map();
  const core = await loadCore();
  return core.loadModelExportSkinning(meshes);
}
