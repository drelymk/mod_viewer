// The optional Weight/Rig feature boundary.
//
// Keep this module free of static imports from the Weight/Rig graph. Normal
// model loading can register mesh lifecycle state here without loading the
// feature; the first explicit Weight/Rig activation hydrates that state into
// the real runtime.

let featurePromise = null;
let feature = null;
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
  return skinning
    ? skinning.withSkinningBaseMaterial(mesh, operation)
    : operation();
}

export function registerSkinningMesh(mesh) {
  pendingSkinningMeshes.add(mesh);
  return skinning?.registerSkinningMesh(mesh);
}

export function unregisterSkinningMesh(mesh) {
  pendingSkinningMeshes.delete(mesh);
  return skinning?.unregisterSkinningMesh(mesh);
}

export function refreshSkinningAfterShapeChange(mesh) {
  return skinning?.refreshSkinningAfterShapeChange(mesh) || false;
}

export function disposeSkinningExperiment(mesh, options = {}) {
  pendingSkinningMeshes.delete(mesh);
  return skinning?.disposeSkinningExperiment(mesh, options);
}

export function destroyModelPhysicsSession() {
  pendingSkinningMeshes.clear();
  return skinning?.destroyModelPhysicsSession();
}

export async function loadWeightRigFeature() {
  if (feature) return feature;
  if (featurePromise) return featurePromise;

  featurePromise = (async () => {
    // Importing the composition root is intentionally the first step: it
    // installs the initialized runtime consumed by the public facades.
    await import('./weight-rig-core.js');
    const [runtime, panel, overlay, skinningModule, status] = await Promise.all([
      import('./weight-rig-runtime.js'),
      import('../panels/weight-rig-panel.js'),
      import('../scene/rig-overlay-controller.js'),
      import('./skinning-runtime.js'),
      import('./weight-rig-status.js'),
    ]);
    skinning = skinningModule;
    for (const mesh of pendingSkinningMeshes) {
      skinning.registerSkinningMesh(mesh);
    }

    feature = {
      ...runtime,
      createRigOverlayController: overlay.createRigOverlayController,
      weightRigStatus: status.weightRigStatus,
    };
    feature.setRigMetadata(pendingRigMetadata);
    feature.setHumanoidRigMetadata(pendingHumanoidRigMetadata);

    panel.initWeightRigPanel();
    return feature;
  })();

  return featurePromise;
}
