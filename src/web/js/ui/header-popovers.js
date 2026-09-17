// The three header settings share one exclusive popover group.

const CONTROLS = [
  ['environment-btn', 'environment-popover'],
  ['appearance-btn', 'appearance-popover'],
  ['language-btn', 'language-popover'],
];

export function closeHeaderPopovers(exceptPopoverId = null) {
  for (const [buttonId, popoverId] of CONTROLS) {
    if (popoverId === exceptPopoverId) continue;
    const button = document.getElementById(buttonId);
    const popover = document.getElementById(popoverId);
    if (popover) popover.hidden = true;
    if (button) button.setAttribute('aria-expanded', 'false');
  }
}
