// Lightweight interaction state shared with normal mesh picking.

let transformInteractionActive = false;
let jointPickingActive = false;

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
