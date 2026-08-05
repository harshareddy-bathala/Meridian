/* Meridian — meridian.org.in
 *
 * The home page's intro: an orthographic globe, a real inclined orbit, and a
 * link line whose colour is decided by an actual elevation calculation.
 * Canvas 2D, no libraries.
 *
 * Time is compressed: one globe revolution is 120 s and one orbit is 40 s,
 * where the real figures are 24 h and about 101 min. Everything else —
 * the projection, the ground track's westward drift, the horizon test —
 * is computed rather than faked, and all of it lives in orbit.js, which the
 * document rail draws from as well.
 *
 * This file owns the choreography and nothing else.
 */

import {
  TAU, DEG, STATION, STATION_VEC, HORIZON_MASK,
  readPalette, rgba, mix,
  clamp01, lerp, easeOutCubic,
  makeProjection, hidden, GRATICULE,
  spinAt, satAt, elevation, SATS, findNextPass,
  orbitPath, groundTrack, strokeLines,
} from './orbit.js';

/* ------------------------------------------------------------------ phases --
 * All intro timing lives here, in milliseconds from first paint. Every phase
 * reads its progress through phaseProgress(), so changing one number retimes
 * the sequence and nothing else needs to move.
 *
 * SETTLE must stay equal to --reveal-at in style.css; the content reveal is a
 * CSS animation and the two are deliberately independent mechanisms.
 */
const PHASES = {
  station:  1200,   // 0 → 1200    close-up: horizon, antenna, tick, beam
  pullback: 2800,   // 1200 → 2800 camera lifts, horizon curves
  globe:    4200,   // 2800 → 4200 wireframe and satellites resolve
  settle:   4200,   // 4200 →      continuous rotation
};
const SETTLE_MS = PHASES.settle;

/* ------------------------------------------------------------------ config -- */

const ZOOM_START   = 26;     // globe radius multiplier at t=0; the limb reads flat
const HORIZON_FRAC = 0.72;   // where the horizon sits during the close-up
const NARROW       = 840;    // px; below this the globe moves above the content

let COLOUR = readPalette();

const phaseProgress = (t, from, to) => clamp01((t - from) / (to - from));

/* Which satellite is next to rise, recomputed once a second rather than every
   frame. The search itself is in orbit.js. */
let nextPass = -1;
let nextPassAt = -Infinity;

/* ------------------------------------------------------------------ canvas -- */

const canvas = document.getElementById('scene');
const ctx = canvas.getContext('2d', { alpha: false });
let W = 0, H = 0;

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);   // capped at 2
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width  = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw(clock);
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

/* Cancels the CSS reveal once the content is up, whether the intro ran to the
   end, was skipped, or never started. Guarded because draw() runs every frame
   and touching classList 30 times a second forever is pointless work. */
let introDone = false;

function markIntroDone() {
  if (introDone) return;
  introDone = true;
  document.documentElement.classList.add('intro-done');
}

function draw(t) {
  if (!W || !H) return;

  ctx.fillStyle = rgba(COLOUR.bg, 1);
  ctx.fillRect(0, 0, W, H);

  const pull = easeOutCubic(phaseProgress(t, PHASES.station, PHASES.settle));
  const spin = spinAt(t);
  const L = layout();

  /* Camera lifts from grazing — where the station sits exactly on the limb and
     the horizon is a near-flat line — to the settled three-quarter view. */
  const camStartLat = STATION.lat - Math.PI / 2;
  const camStartLon = STATION.lon;
  const camLat = lerp(camStartLat, 18 * DEG, pull);
  const camLon = lerp(camStartLon, STATION.lon + 25 * DEG, pull) + spin;

  const project = makeProjection(camLat, camLon);
  const R = L.R * Math.pow(ZOOM_START, 1 - pull);

  /* Anchor the whole scene on the station: decide where it should be on
     screen, then place the globe's centre so it lands there. Interpolating
     the centre directly instead would let the station slide off frame,
     because the radius shrinks geometrically and the centre does not. */
  const settledProj = makeProjection(18 * DEG, STATION.lon + 25 * DEG + spin)(STATION_VEC);
  const sx = lerp(W * 0.5,       L.cx + settledProj.h * L.R, pull);
  const sy = lerp(H * HORIZON_FRAC, L.cy - settledProj.v * L.R, pull);

  const here = project(STATION_VEC);
  const cx = sx - here.h * R;
  const cy = sy + here.v * R;

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
  const linkMix = phaseProgress(t, PHASES.station - 400, PHASES.station);

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

  if (!introDone && t > SETTLE_MS + 1200) markIntroDone();
}

/* -------------------------------------------------------------------- loop -- */

let clock = 0;
let lastTs = 0;
let acc = 0;
let raf = 0;

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function frame(ts) {
  raf = requestAnimationFrame(frame);
  if (!lastTs) lastTs = ts;
  const dt = Math.min(ts - lastTs, 100);   // also absorbs any tab-switch jump
  lastTs = ts;

  acc += dt;
  /* Once settled, 30 fps. At one revolution per 120 s the difference is not
     perceptible and it halves the cost of standing still. */
  if (clock >= SETTLE_MS && acc < 33) return;

  clock += acc;
  acc = 0;
  draw(clock);
}

function start() {
  if (raf || reducedMotion.matches) return;
  lastTs = 0;
  raf = requestAnimationFrame(frame);
}

function stop() {
  cancelAnimationFrame(raf);
  raf = 0;
}

/* Click, tap, Escape or any key jumps to the settled state. Modifier keys on
   their own are ignored: holding Shift to type, or Alt to reach a menu, is not
   a request to skip anything. */
function skip(event) {
  if (event && event.type === 'keydown') {
    const k = event.key;
    if (k === 'Shift' || k === 'Control' || k === 'Alt' || k === 'Meta') return;
  }
  if (clock >= SETTLE_MS) return;
  clock = SETTLE_MS;
  markIntroDone();
  draw(clock);
}

function applyMotionPreference() {
  if (reducedMotion.matches) {
    stop();
    clock = SETTLE_MS;
    markIntroDone();
    draw(clock);          // one static settled frame; the loop never runs
  } else {
    start();
  }
}

window.addEventListener('resize', resize, { passive: true });
window.addEventListener('keydown', skip);
window.addEventListener('pointerdown', skip, { passive: true });
reducedMotion.addEventListener('change', applyMotionPreference);

/* theme.js dispatches this after it has swapped data-theme, so the computed
   values are already the new theme's by the time this reads them. Redrawn
   immediately rather than waiting for the next frame, because under reduced
   motion there is no next frame. */
document.addEventListener('themechange', () => {
  COLOUR = readPalette();
  draw(clock);
});

document.addEventListener('visibilitychange', () => {
  if (document.hidden) stop();
  else if (!reducedMotion.matches) start();
});

resize();
applyMotionPreference();
