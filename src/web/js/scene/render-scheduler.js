// Coalesced on-demand viewport rendering.

let renderCallback = null;
let dirty = false;
let frameId = null;

function schedule() {
  if (frameId !== null || !renderCallback) return;
  frameId = requestAnimationFrame(() => {
    frameId = null;
    if (!renderCallback || !dirty) return;
    dirty = false;
    renderCallback();
    if (dirty) schedule();
  });
}

/** Request one viewport render. Multiple changes before the next animation
 * frame are coalesced so idle scenes submit no new GPU work. */
export function requestRender() {
  dirty = true;
  schedule();
}

/** Install the scene renderer after its dependencies have been created. */
export function setRenderCallback(callback) {
  renderCallback = callback;
  schedule();
}
