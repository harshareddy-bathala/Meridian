/* Meridian — meridian.org.in
 *
 * The front page's globe, the page's half of it. globe.js draws; this file
 * owns everything that touches the page: which thread the drawing runs on,
 * the size and colours it draws with, the presses and drags that turn it, the
 * one line of words under it, and the classes that end the intro.
 *
 * The drawing runs in a worker where the browser can hand a canvas to one.
 * Without a GPU, every frame of a full-screen canvas costs the main thread a
 * copy of the whole bitmap, about 18 ms; a canvas drawn from a worker is
 * delivered without it, so the globe never holds up a tap or a scroll (D-349).
 * Where the browser cannot, globe.js runs here instead, exactly as before.
 */

import { readPalette } from './orbit.js?v=aa6da386';

const root = document.documentElement;
const canvas = document.getElementById('scene');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

/* ------------------------------------------------------------------ thread --
 * Messages wait in a queue until there is something to draw with: a worker
 * that has loaded and taken the canvas, or globe.js imported here. */

let queue = [];
let post = (message) => queue.push(message);
let handedOver = false;

function begin(sender) {
  post = sender;
  resize();
  post({ type: 'palette', colours: readPalette() });
  post({ type: 'motion', reduced: reducedMotion.matches });
  if (!reducedMotion.matches) post({ type: 'run', on: !document.hidden });
  for (const message of queue) post(message);
  queue = [];
}

/* The canvas is handed over only once the worker has said it loaded. Until
   then a failure costs nothing: the canvas is still this page's, and the
   globe runs here instead. After it, a failure costs the globe and never the
   page — the gate theme.js put up is dropped and the content shows. */
function startWorker() {
  if (!('transferControlToOffscreen' in canvas) || typeof Worker !== 'function') return false;

  /* A browser that cannot run a module worker never reads `type`, and would
     load globe.js as a classic script, where `import` is a syntax error. */
  let module = false;
  let worker;
  try {
    worker = new Worker(new URL('./globe.js?v=0818dada', import.meta.url), {
      get type() { module = true; return 'module'; },
    });
  } catch (error) {
    return false;
  }
  if (!module) { worker.terminate(); return false; }

  worker.onmessage = (event) => {
    const { type, detail } = event.data;
    if (type !== 'ready') { hear(type, detail); return; }
    const offscreen = canvas.transferControlToOffscreen();
    worker.postMessage({ type: 'init', canvas: offscreen }, [offscreen]);
    handedOver = true;
    begin((message) => worker.postMessage(message));
  };
  worker.onerror = (event) => {
    event.preventDefault();
    worker.terminate();
    if (handedOver) lose(event.message || 'the worker stopped');
    else startHere();
  };
  return true;
}

async function startHere() {
  try {
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) { lose('no 2D context'); return; }
    const globe = await import('./globe.js?v=0818dada');
    globe.init(ctx, hear);
    begin(globe.receive);
  } catch (error) {
    lose(error);
  }
}

/* The intro is decoration. Nothing here is allowed to cost anyone the page. */
function lose(why) {
  root.classList.remove('intro');
  console.error('Meridian: globe disabled —', why);
}

/* ------------------------------------------------------------------ hearing -- */

let settled = false;
let globe = { cx: 0, cy: 0, R: 0 };

function hear(type, detail) {
  switch (type) {
    /* The first frame is on screen: the intro is real, and the gate in
       theme.js can stand down. Until this, that gate is on a 900 ms fuse. */
    case 'painted': root.classList.add('intro-ready'); break;
    case 'settled': settled = true; break;
    /* Cancels the CSS reveal once the content is up, whether the intro ran to
       the end, was skipped, or never started. */
    case 'introDone': settled = true; root.classList.add('intro-done'); break;
    case 'state': describe(detail); break;
    case 'globe': globe = detail; break;
  }
}

/* ---------------------------------------------------------------- caption --
 * One line under the globe that says, in words, what it is showing. Written
 * only when the state changes, never every frame. */
const stateEl = document.querySelector('.globe-state');
const textEl = document.querySelector('.globe-text');
const swatchEl = stateEl ? stateEl.querySelector('.swatch') : null;
const WORDS = {
  up:   ['swatch-signal', "A satellite is above the station's horizon, so it can listen"],
  next: ['swatch-trace',  'Nothing overhead now. The next pass is already predicted'],
};

function describe(state) {
  if (!stateEl || !textEl || !swatchEl || !WORDS[state]) return;
  stateEl.dataset.state = state;
  swatchEl.className = 'swatch ' + WORDS[state][0];
  textEl.textContent = WORDS[state][1];
}

/* ------------------------------------------------------------------- size -- */

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);   // capped at 2

  /* Measure the canvas, not the window.
     #scene is position: fixed; inset: 0, so CSS sizes it to the *layout*
     viewport. window.innerHeight is the *visual* viewport. On a phone those
     differ by the height of the browser chrome, so sizing the backing store
     from the window built a scene for ~660 CSS px and let CSS stretch it
     across ~780 — about 18% of vertical distortion — while layout()'s H * 0.21
     term recomputed the radius from the wrong number every time the URL bar
     slid in or out. That is the globe that "becomes small". The element's own
     box is the thing being painted into, so it is the thing to ask. */
  const box = canvas.getBoundingClientRect();
  const W = Math.round(box.width)  || window.innerWidth;
  const H = Math.round(box.height) || window.innerHeight;
  post({ type: 'size', W, H, dpr });
}

/* ------------------------------------------------------------------ turning -- */

let dragging = null;              // { x, y, t } at the last move

function onGlobe(x, y) {
  if (!settled || !globe.R) return false;
  const dx = x - globe.cx, dy = y - globe.cy;
  return dx * dx + dy * dy <= (globe.R * 1.15) ** 2;
}

/* A press on a link, a button or the text column is that thing's, never the
   globe's, even where the two overlap on a phone. */
function interactive(target) {
  return !!(target && target.closest && target.closest('a, button, .content, .masthead'));
}

function grab(event) {
  if (event.button > 0 || interactive(event.target)) return;
  if (!onGlobe(event.clientX, event.clientY)) return;
  dragging = { x: event.clientX, y: event.clientY, t: event.timeStamp };
  post({ type: 'grab' });
  root.classList.add('globe-dragging');
}

function move(event) {
  if (!dragging) {
    if (event.pointerType === 'mouse') {
      root.classList.toggle('globe-hover', !interactive(event.target) && onGlobe(event.clientX, event.clientY));
    }
    return;
  }
  post({
    type: 'drag',
    dx: event.clientX - dragging.x,
    dy: event.clientY - dragging.y,
    dt: event.timeStamp - dragging.t,
  });
  dragging = { x: event.clientX, y: event.clientY, t: event.timeStamp };
}

function release() {
  if (!dragging) return;
  dragging = null;
  post({ type: 'release' });
  root.classList.remove('globe-dragging');
}

/* -------------------------------------------------------------------- skip --
 * Click, tap, Escape or any key jumps to the settled state. Modifier keys on
 * their own are ignored: holding Shift to type, or Alt to reach a menu, is not
 * a request to skip anything.
 *
 * A tap that skips the intro must not also press whatever it revealed.
 * skip() runs on pointerdown; the click that completes the same gesture lands
 * after the content has become visible, so without this a single tap during
 * the intro could skip it *and* follow a link. Armed only by a pointer skip,
 * fired at most once, and in the capture phase so it runs before the anchor. */
let swallowNextClick = false;

window.addEventListener('click', (event) => {
  if (!swallowNextClick) return;
  swallowNextClick = false;
  event.preventDefault();
  event.stopPropagation();
}, { capture: true });

function skip(event) {
  if (event && event.type === 'keydown') {
    const k = event.key;
    if (k === 'Shift' || k === 'Control' || k === 'Alt' || k === 'Meta') return;
  }
  if (event && event.type === 'pointerdown' && settled) {
    grab(event);
    return;
  }
  if (settled) return;
  if (event && event.type === 'pointerdown') swallowNextClick = true;
  settled = true;
  post({ type: 'skip' });
}

/* -------------------------------------------------------------------- bind -- */

function bind() {
  window.addEventListener('resize', resize, { passive: true });

  /* Mobile browsers do not agree on which of these fires when the URL bar
     slides. Chrome on Android often changes the visual viewport without a
     window resize; iOS Safari does the reverse on rotation. Listening to both
     and letting resize() re-measure is cheaper than guessing. */
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', resize, { passive: true });
  }

  window.addEventListener('keydown', skip);
  window.addEventListener('pointerdown', skip, { passive: true });
  window.addEventListener('pointermove', move, { passive: true });
  window.addEventListener('pointerup', release, { passive: true });
  window.addEventListener('pointercancel', release, { passive: true });
  reducedMotion.addEventListener('change', () => {
    post({ type: 'motion', reduced: reducedMotion.matches });
  });

  /* theme.js dispatches this after it has swapped data-theme, so the computed
     values are already the new theme's by the time this reads them. */
  document.addEventListener('themechange', () => {
    post({ type: 'palette', colours: readPalette() });
  });

  document.addEventListener('visibilitychange', () => {
    if (!reducedMotion.matches) post({ type: 'run', on: !document.hidden });
  });
}

if (!canvas) {
  root.classList.remove('intro');
} else {
  try {
    bind();
    if (!startWorker()) startHere();
  } catch (error) {
    lose(error);
  }
}
