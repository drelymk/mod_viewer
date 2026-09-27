// Small inline SVG icon factory shared by the vanilla frontend.

const SVG_NS = 'http://www.w3.org/2000/svg';

export function createIcon(name, label = '') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.classList.add('ui-icon');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', label ? 'false' : 'true');
  if (label) svg.setAttribute('aria-label', label);
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', `#icon-${name}`);
  svg.appendChild(use);
  return svg;
}
