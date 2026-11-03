/* Meridian — sections rise into place as they scroll into view.
 *
 * Every page but the front, which has its own intro. Uses cube-motion's
 * reveal (MIT, vendored unmodified in /vendor/cube-motion-0.1.0/, see
 * ATTRIBUTION.md): opacity and a 12px lift on the Web Animations API, one
 * curve, and opacity alone under prefers-reduced-motion.
 *
 * Only what is still below the fold when the page loads is touched. Something
 * already on screen is never hidden and shown again, which would read as a
 * flicker; and a page whose script is blocked loses the motion, never the
 * content, because nothing is hidden until this file runs.
 */

import { reveal } from './vendor/cube-motion-0.1.0/reveal.js';

const root = document.documentElement;

function belowTheFold(el) {
  return el.getBoundingClientRect().top > window.innerHeight;
}

if (
  !root.hasAttribute('data-intro') &&
  'IntersectionObserver' in window &&
  typeof Element.prototype.animate === 'function'
) {
  const waiting = [...document.querySelectorAll(
    '.section, .journey > .step, .tiles > li, .doors > li, .index-row'
  )].filter(belowTheFold);
  if (waiting.length) reveal(waiting);
}
