// Lightweight interaction state shared with normal mesh picking.

let transformInteractionActive = false;
let jointPickingActive = false;
let previewInteractionActive = false;

export function isRigPreviewInteractionActive() {
  return previewInteractionActive;
}

export function setRigPreviewInteractionActive(value) {
  previewInteractionActive = !!value;
}

export function isRigTransformInteractionActive() {
  return transformInteractionActive;
}

export function isRigJointPickingActive() {
  return jointPickingActive;
}

export function setRigTransformInteractionActive(value) {
  transformInteractionActive = !!value;
}

export function setRigJointPickingActive(value) {
  jointPickingActive = !!value;
}
