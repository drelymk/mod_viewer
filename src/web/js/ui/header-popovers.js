// Close header popovers before opening another viewer control.

const CONTROLS = [['environment-btn', 'environment-popover']];

export function closeHeaderPopovers(exceptPopoverId = null) {
  for (const [buttonId, popoverId] of CONTROLS) {
    if (popoverId === exceptPopoverId) continue;
    const button = document.getElementById(buttonId);
    const popover = document.getElementById(popoverId);
    if (popover) popover.hidden = true;
    if (button) button.setAttribute('aria-expanded', 'false');
  }
}
