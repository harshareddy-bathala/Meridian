/* Meridian — meridian.org.in
 *
 * The front page's globe: an orthographic globe, a real inclined orbit, and a
 * link line whose colour is decided by an actual elevation calculation.
 * Canvas 2D, no libraries, and no DOM: everything it needs from the page
 * arrives as a message, and everything the page needs from it leaves as one.
 *
 * That is so it can run in a worker. main.js hands the canvas over with
 * transferControlToOffscreen() and this file draws into it off the main
 * thread, so a frame never holds up a tap, a scroll or the page's own work.
 * Where a browser cannot do that, main.js imports this same file and runs it
 * on the main thread instead; nothing below knows which (D-349).
 *
 * Time is compressed: one globe revolution is 120 s and one orbit is 40 s,
 * where the real figures are 24 h and about 101 min. Everything else —
 * the projection, the ground track's westward drift, the horizon test —
 * is computed rather than faked, and all of it lives in orbit.js, which the
 * document rail draws from as well.
 *
 * In:  size {W, H, dpr} · palette {colours} · motion {reduced} · run {on}
 *      skip · grab · drag {dx, dy, dt} · release
 * Out: painted · settled · introDone · state {detail: 'up' | 'next'}
 *      globe {detail: {cx, cy, R}}, where it is, for the page's hit test
 */

import {
  TAU, DEG, STATION, STATION_VEC, HORIZON_MASK,
  rgba, mix,
  clamp01, lerp, easeOutCubic,
  makeProjection, hidden, GRATICULE,
  spinAt, satAt, elevation, SATS, findNextPass,
  orbitPath, groundTrack, strokeLines,
} from './orbit.js?v=aa6da386';

/* ------------------------------------------------------------------ phases --
 * All intro timing lives here, in milliseconds from first paint. Every phase
 * reads its progress through phaseProgress(), so changing one number retimes
 * the sequence and nothing else needs to move.
 *
 * SETTLE must stay equal to --reveal-at in style.css; the content reveal is a
 * CSS animation and the two are deliberately independent mechanisms.
 */
const PHASES = {
  station:   350,   // 0 → 350     close-up: horizon, antenna, beam
  pullback:  950,   // 350 → 950   camera lifts, horizon curves
  globe:    1400,   // 950 → 1400  wireframe and satellites resolve
  settle:   1400,   // 1400 →      continuous rotation, and the globe is yours
};
const SETTLE_MS = PHASES.settle;

/* ------------------------------------------------------------------ config -- */

const ZOOM_START   = 26;     // globe radius multiplier at t=0; the limb reads flat
const HORIZON_FRAC = 0.72;   // where the horizon sits during the close-up
const NARROW       = 1024;   // px, 64rem; below this the globe moves above the content

let COLOUR = null;           // the theme's colours, sent by the page (readPalette lives there)

const phaseProgress = (t, from, to) => clamp01((t - from) / (to - from));

/* Which satellite is next to rise, recomputed once a second rather than every
   frame. The search itself is in orbit.js. */
let nextPass = -1;
let nextPassAt = -Infinity;

/* ------------------------------------------------------------------- state -- */

let ctx = null;                   // the canvas's 2D context, from init()
let send = () => {};              // how a message reaches the page
let W = 0, H = 0;

/* The visitor can turn the globe by dragging or swiping it: two offsets on top
   of the slow spin, in radians, longitude (unbounded) and tilt (held within
   ±35°, so the station never rolls out of view). A flick keeps turning and
   slows to a stop; under reduced motion the globe follows the hand exactly
   and never coasts. The page turns pointer movement into drag messages. */
const TILT_MAX = 35 * DEG;
const COAST_MS = 420;             // the time constant of the slow-down after a flick
let userLon = 0, userTilt = 0;
let velLon = 0;                   // radians per ms, while coasting
let dragging = false;
let reduced = false;
let globe = { cx: 0, cy: 0, R: 0 };   // where the globe was last drawn

/* Messages that are true once, sent once. */
const sent = new Set();
function once(name) {
  if (sent.has(name)) return;
  sent.add(name);
  send(name);
}

/* The page hit-tests presses against the globe, so it hears where it is, but
   only when that moves by half a pixel: once settled, never again until a
   resize. */
let placed = '';
function place(cx, cy, R) {
  globe = { cx, cy, R };
  const key = [cx, cy, R].map((v) => Math.round(v * 2)).join();
  if (key === placed) return;
  placed = key;
  send('globe', { cx, cy, R });
}

/* What the caption under the globe should say. Sent only when it changes. */
let said = 'up';
function say(state) {
  if (state === said) return;
  said = state;
  send('state', state);
}


/* Where the settled globe sits. Upper-right, running off the top and right
   edges; above the content, smaller, on narrow screens. Never hidden.
   Sized to leave the lower-left third and the colophon clear — satellites
   orbit at 1.13 R, so the radius also decides how far they stray. */
function layout() {
  if (W < NARROW) {
    return { cx: W * 0.52, cy: H * 0.21, R: Math.min(W * 0.38, H * 0.21) };
  }
  return { cx: W * 0.82, cy: H * 0.22, R: Math.min(W * 0.24, H * 0.40) };
}

/* ----------------------------------------------------------------- drawing -- */

/* Height of the dish above the ground in glyph units. The link line starts
   here, not at the station's ground point — a beam that leaves from the dirt
   beside the mast is the first thing the eye catches as wrong. */
const ANTENNA_H = 26;

/* The close-up glyph: a mast and a dish. Nothing else.
   It carried a vertical tick mark for scale, which read as a stray line
   floating beside the antenna because nothing anchored it — the horizon
   already gives the scale, so the tick was answering a question no one had. */
function drawAntenna(x, y, scale, alpha) {
  if (alpha <= 0.004) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.lineWidth = 1 / scale;
  ctx.lineCap = 'round';
  ctx.strokeStyle = rgba(COLOUR.ink, 1);

  ctx.beginPath();
  ctx.moveTo(0, 0); ctx.lineTo(0, -ANTENNA_H);          // mast
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(0, -ANTENNA_H, 9, Math.PI, TAU);              // dish, opening upward
  ctx.stroke();

  ctx.restore();
  ctx.globalAlpha = 1;
}

function drawStationMarker(x, y, alpha, front) {
  if (alpha <= 0.004) return;
  ctx.globalAlpha = alpha * (front ? 1 : 0.25);
  ctx.strokeStyle = rgba(COLOUR.ink, 1);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(x, y, 3, 0, TAU);
  ctx.moveTo(x - 7, y); ctx.lineTo(x - 4, y);
  ctx.moveTo(x + 4, y); ctx.lineTo(x + 7, y);
  ctx.stroke();
  ctx.globalAlpha = 1;
}

function draw(t) {
  if (!W || !H || !COLOUR) return;

  ctx.fillStyle = rgba(COLOUR.bg, 1);
  ctx.fillRect(0, 0, W, H);

  const pull = easeOutCubic(phaseProgress(t, PHASES.station, PHASES.settle));
  const spin = spinAt(t);
  const L = layout();

  /* Camera lifts from grazing — where the station sits exactly on the limb and
     the horizon is a near-flat line — to the settled three-quarter view. */
  const camStartLat = STATION.lat - Math.PI / 2;
  const camStartLon = STATION.lon;
  const camLat = lerp(camStartLat, 18 * DEG + userTilt, pull);
  const camLon = lerp(camStartLon, STATION.lon + 25 * DEG + userLon, pull) + spin;

  const project = makeProjection(camLat, camLon);
  const R = L.R * Math.pow(ZOOM_START, 1 - pull);

  /* Anchor the whole scene on the station: decide where it should be on
     screen, then place the globe's centre so it lands there. Interpolating
     the centre directly instead would let the station slide off frame,
     because the radius shrinks geometrically and the centre does not. */
  const settledProj = makeProjection(18 * DEG + userTilt, STATION.lon + 25 * DEG + userLon + spin)(STATION_VEC);
  const sx = lerp(W * 0.5,       L.cx + settledProj.h * L.R, pull);
  const sy = lerp(H * HORIZON_FRAC, L.cy - settledProj.v * L.R, pull);

  const here = project(STATION_VEC);
  const cx = sx - here.h * R;
  const cy = sy + here.v * R;
  place(cx, cy, R);

  /* Phase opacities. */
  const aGrat    = phaseProgress(t, PHASES.pullback - 500, PHASES.globe);
  const aAntenna = 1 - phaseProgress(t, PHASES.station, PHASES.pullback);
  const aOthers  = phaseProgress(t, PHASES.pullback + 300, PHASES.globe);
  const aMarker  = phaseProgress(t, PHASES.pullback - 200, PHASES.globe);

  /* Link lines leave from the dish, not from the ground at the mast's foot.
     The offset is the glyph's own height, so it shrinks with the antenna and
     reaches zero exactly as the antenna finishes fading — by the time the
     globe resolves the line starts at the station point, which is correct
     once a station is a dot rather than a drawing. */
  const antScale = lerp(1, 0.12, pull);
  const feedY = sy - ANTENNA_H * antScale * aAntenna;

  /* The limb. During the close-up this *is* the horizon line — same circle,
     just a very large one. Nothing special-cases it. */
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, TAU);
  ctx.strokeStyle = rgba(COLOUR.muted, 1);
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.globalAlpha = 1;

  if (aGrat > 0.004) {
    strokeLines(ctx, GRATICULE, project, cx, cy, R, COLOUR.wire, aGrat * 0.95, aGrat * 0.35, 1);
  }

  if (t - nextPassAt > 1000) { nextPass = findNextPass(t); nextPassAt = t; }

  /* Ground track of the next predicted pass only — one is informative, three
     is clutter. Drawn faint and finely dashed so it reads as a trace behind
     the wireframe rather than competing with the graticule: it is the one
     element showing a future path, and it should not shout about it. */
  if (nextPass >= 0 && aOthers > 0.004) {
    ctx.setLineDash([2, 6]);
    strokeLines(ctx, [groundTrack(SATS[nextPass], t, 180)], project, cx, cy, R,
                COLOUR.trace, aOthers * 0.25, aOthers * 0.08, 1);
    ctx.setLineDash([]);
  }

  /* The close-up draws a commanded pointing direction, not an acquired link,
     so it is amber — the station is aimed where the schedule says the pass
     will be. From the pull-back onward the line reports measured link state,
     and the colour crosses over as the close-up ends. Physics forces this
     distinction: a pass with a usable peak clears the 5° mask within about
     300 ms of rising, so elevation alone cannot hold the line amber for the
     whole of phase 1. */
  const linkMix = phaseProgress(t, PHASES.station - 300, PHASES.station);
  let anyUp = false;

  for (let i = 0; i < SATS.length; i++) {
    const sat = SATS[i];
    const alpha = i === 0 ? 1 : aOthers;
    if (alpha <= 0.004) continue;

    if (aGrat > 0.004) {
      strokeLines(ctx, [orbitPath(sat, t, 120)], project, cx, cy, R,
                  COLOUR.wire, alpha * aGrat * 1.0, alpha * aGrat * 0.4, 1);
    }

    const p = satAt(sat, t);
    const c = project(p);
    const px = cx + c.h * R;
    const py = cy - c.v * R;
    const visible = !hidden(c);

    const el = elevation(p);
    const up = el > HORIZON_MASK;
    if (up && alpha > 0.5) anyUp = true;
    let colour = up ? COLOUR.signal : (i === nextPass ? COLOUR.trace : COLOUR.alert);
    if (i === 0) colour = mix(COLOUR.trace, colour, linkMix);

    /* Link line, 1px, no glow. Drawn even when an endpoint is round the back —
       dimmed, because the geometry is still true, it is just not in view.
       An acquired link is the brightest thing on the canvas; that is the whole
       point of the colour. */
    const linkVisible = visible && !hidden(here);
    ctx.globalAlpha = alpha * (linkVisible ? (up ? 0.95 : 0.5) : 0.15);
    ctx.strokeStyle = rgba(colour, 1);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sx, feedY);
    ctx.lineTo(px, py);
    ctx.stroke();

    /* The satellite. */
    ctx.globalAlpha = alpha * (visible ? 1 : 0.2);
    ctx.fillStyle = rgba(colour, 1);
    ctx.beginPath();
    ctx.arc(px, py, 2.5, 0, TAU);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  drawAntenna(sx, sy, antScale, aAntenna);
  drawStationMarker(sx, sy, aMarker, !hidden(here));
  say(anyUp ? 'up' : 'next');

  if (t >= SETTLE_MS) once('settled');
  if (t > SETTLE_MS + 1200) once('introDone');
  once('painted');
}

/* -------------------------------------------------------------------- loop --
   requestAnimationFrame exists in a worker that owns an OffscreenCanvas in
   every browser that can hand one over; the timer is for one that cannot. */

const tick = typeof requestAnimationFrame === 'function'
  ? (fn) => requestAnimationFrame(fn)
  : (fn) => setTimeout(() => fn(performance.now()), 16);
const untick = typeof cancelAnimationFrame === 'function' ? cancelAnimationFrame : clearTimeout;

let clock = 0;
let lastTs = 0;
let acc = 0;
let raf = 0;

function frame(ts) {
  raf = tick(frame);
  if (!lastTs) lastTs = ts;
  const dt = Math.min(ts - lastTs, 100);   // also absorbs any tab-switch jump
  lastTs = ts;

  acc += dt;

  /* A flick keeps turning, and slows to a stop. */
  if (!dragging && Math.abs(velLon) > 1e-7) {
    userLon += velLon * dt;
    velLon *= Math.exp(-dt / COAST_MS);
  }

  /* Once settled and still, 30 fps. At one revolution per 120 s the
     difference is not perceptible and it halves the cost of standing still.
     While the globe is in someone's hand it draws every frame. */
  const handled = dragging || Math.abs(velLon) > 1e-7;
  if (clock >= SETTLE_MS && !handled && acc < 33) return;

  clock += acc;
  acc = 0;
  draw(clock);
}

function start() {
  if (raf || reduced) return;
  lastTs = 0;
  raf = tick(frame);
}

function stop() {
  untick(raf);
  raf = 0;
}

function settle() {
  clock = Math.max(clock, SETTLE_MS);
  once('introDone');
  draw(clock);
}

function turn(dLon, dTilt) {
  userLon += dLon;
  userTilt = Math.max(-TILT_MAX, Math.min(TILT_MAX, userTilt + dTilt));
  if (!raf) draw(clock);   // reduced motion: no loop, so draw now
}

/* ---------------------------------------------------------------- messages -- */

export function init(context, sender) {
  ctx = context;
  send = sender;
}

export function receive(message) {
  switch (message.type) {
    case 'size': {
      W = message.W;
      H = message.H;
      ctx.canvas.width = Math.round(W * message.dpr);
      ctx.canvas.height = Math.round(H * message.dpr);
      ctx.setTransform(message.dpr, 0, 0, message.dpr, 0, 0);
      if (COLOUR) draw(clock);
      break;
    }
    case 'palette':
      COLOUR = message.colours;
      draw(clock);
      break;
    case 'motion':
      reduced = message.reduced;
      if (reduced) { stop(); settle(); }   // one still, settled frame; no loop
      else start();
      break;
    case 'run':
      if (message.on) start();
      else stop();
      break;
    case 'skip':
      if (clock < SETTLE_MS) settle();
      break;
    case 'grab':
      dragging = true;
      velLon = 0;
      break;
    case 'drag': {
      if (!globe.R) break;
      const dLon = -message.dx / globe.R;
      turn(dLon, message.dy / globe.R);
      velLon = reduced ? 0 : dLon / Math.max(message.dt, 1);
      break;
    }
    case 'release':
      dragging = false;
      break;
  }
}

/* In a worker this file is the entry point: it tells the page it has loaded,
   and the page answers with the canvas. On the main thread it is imported, and
   main.js calls init() and receive() directly. */
if (typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope) {
  const post = (type, detail) => self.postMessage({ type, detail });
  self.onmessage = (event) => {
    const message = event.data;
    if (message.type === 'init') init(message.canvas.getContext('2d', { alpha: false }), post);
    else receive(message);
  };
  post('ready');
}
