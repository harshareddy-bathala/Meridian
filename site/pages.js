/* Meridian — what every page but the front does once it has loaded.
 *
 * Three things, all optional: a page whose script is blocked loses them and
 * keeps everything else.
 *
 * 1. Sections rise into place as they scroll into view, with cube-motion's
 *    reveal (MIT, vendored unmodified in /vendor/cube-motion-0.1.0/, see
 *    ATTRIBUTION.md). Only what is still below the fold at load is touched:
 *    something already on screen is never hidden and shown again.
 *
 * 2. On How it works, the step being read is marked, so its picture is the one
 *    in full colour.
 *
 * 3. A term's note drops down from its (i). The native popover attribute
 *    opens it, closes it when the (i) is pressed again, and closes it on a
 *    press anywhere else; this only places it under the button and keeps it
 *    there while the page scrolls. Without this file the note opens centred.
 */

import { reveal } from './vendor/cube-motion-0.1.0/reveal.js';

const root = document.documentElement;

/* ------------------------------------------------------------- reveal -- */

function belowTheFold(el) {
  return el.getBoundingClientRect().top > window.innerHeight;
}

if ('IntersectionObserver' in window && typeof Element.prototype.animate === 'function') {
  const waiting = [...document.querySelectorAll(
    '.section, .journey > .step, .tiles > li, .doors > li, .index-row'
  )].filter(belowTheFold);
  if (waiting.length) reveal(waiting);
}

/* ------------------------------------------------------------ journey --
   How it works: the step crossing the middle of the screen is the one being
   read, and is drawn in full colour; the rest step back (style.css, wide
   screens only). */

const journey = document.querySelector('.journey');
if (journey && 'IntersectionObserver' in window) {
  const steps = [...journey.querySelectorAll('.step')];
  const watch = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      for (const step of steps) step.classList.toggle('is-active', step === entry.target);
    }
  }, { rootMargin: '-45% 0px -45% 0px' });
  steps.forEach((step) => watch.observe(step));
  if (steps.length) steps[0].classList.add('is-active');
  journey.classList.add('journey-live');
}

/* -------------------------------------------------------------- notes -- */

const GAP = 8;        // px between the (i) and its note
const MARGIN = 16;    // px the note keeps from the edge of the screen

let open = null;      // { note, button } while a note is showing

function place(note, button) {
  const r = button.getBoundingClientRect();
  const w = note.offsetWidth;
  const h = note.offsetHeight;
  const left = Math.max(MARGIN, Math.min(r.left + r.width / 2 - 28, window.innerWidth - w - MARGIN));
  const below = r.bottom + GAP;
  const above = window.innerHeight - below - MARGIN < h && r.top - GAP - h > MARGIN;
  note.classList.toggle('term-above', above);
  note.style.setProperty('--term-x', `${Math.round(left)}px`);
  note.style.setProperty('--term-y', `${Math.round(above ? r.top - GAP - h : below)}px`);
}

for (const note of document.querySelectorAll('.term[popover]')) {
  const button = document.querySelector(`.info[popovertarget="${note.id}"]`);
  if (!button) continue;
  note.classList.add('term-anchored');
  /* Placed once before it shows, from its last size or a fair guess, so its
     first frame is already in the right place; then again once measured. */
  note.addEventListener('beforetoggle', (event) => {
    if (event.newState !== 'open') return;
    const from = event.source || document.activeElement;
    const owner = from && from.matches && from.matches('.info') ? from : button;
    const r = owner.getBoundingClientRect();
    const w = Math.min(352, window.innerWidth - 2 * MARGIN);
    note.style.setProperty('--term-x', `${Math.round(Math.max(MARGIN, Math.min(r.left - 20, window.innerWidth - w - MARGIN)))}px`);
    note.style.setProperty('--term-y', `${Math.round(r.bottom + GAP)}px`);
  });
  note.addEventListener('toggle', (event) => {
    const shown = event.newState === 'open';
    for (const b of document.querySelectorAll(`.info[popovertarget="${note.id}"]`)) {
      b.setAttribute('aria-expanded', shown ? 'true' : 'false');
    }
    if (!shown) { if (open && open.note === note) open = null; return; }
    /* The (i) that opened it, when a term appears more than once. */
    const from = event.source || document.activeElement;
    const owner = from && from.matches && from.matches('.info') ? from : button;
    open = { note, button: owner };
    place(note, owner);
  });
}

let ticking = false;
function follow() {
  if (!open || ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    if (open) place(open.note, open.button);
  });
}
window.addEventListener('scroll', follow, { passive: true });
window.addEventListener('resize', follow, { passive: true });

root.classList.add('pages-ready');
