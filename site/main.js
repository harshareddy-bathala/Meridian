/* Meridian — meridian.org.in
 *
 * An orthographic globe, a real inclined orbit, and a link line whose colour
 * is decided by an actual elevation calculation. Canvas 2D, no libraries.
 *
 * Time is compressed: one globe revolution is 120 s and one orbit is 40 s,
 * where the real figures are 24 h and about 101 min. Everything else —
 * the projection, the ground track's westward drift, the horizon test —
 * is computed rather than faked.
 */

'use strict';

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

const TAU = Math.PI * 2;
const DEG = Math.PI / 180;

/* Station 001. The one coordinate pair in the repository — docs/MSP-SPEC.md §4.1. */
const STATION = { lat: 12.9716 * DEG, lon: 77.5946 * DEG };

const R_EARTH_KM = 6371;
const ALT_KM     = 800;
const R_SAT      = (R_EARTH_KM + ALT_KM) / R_EARTH_KM;   // 1.1256 Earth radii
const INCLINATION = 98 * DEG;                            // sun-synchronous
const HORIZON_MASK = 5 * DEG;                            // below this, no link

const ROT_PERIOD_MS   = 120000;   // one globe revolution
const ORBIT_PERIOD_MS =  40000;   // one satellite revolution

const ZOOM_START   = 26;     // globe radius multiplier at t=0; the limb reads flat
const HORIZON_FRAC = 0.72;   // where the horizon sits during the close-up
const NARROW       = 840;    // px; below this the globe moves above the content

/* Semantic colours. These three mean exactly one thing each and are used
   nowhere else. Kept as components so they can be interpolated. */
const COLOUR = {
  ink:    [232, 230, 225],
  rule:   [ 30,  34,  41],
  muted:  [110, 116, 128],
  signal: [107, 184, 138],   // above horizon — link acquired
  alert:  [184,  84,  79],   // below horizon — no link
  trace:  [201, 154,  78],   // predicted / next pass
  bg:     [  8,   9,  13],
};

const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
const mix  = (a, b, t) => [a[0] + (b[0] - a[0]) * t,
                           a[1] + (b[1] - a[1]) * t,
                           a[2] + (b[2] - a[2]) * t];

/* -------------------------------------------------------------------- math -- */

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const lerp = (a, b, t) => a + (b - a) * t;
const easeOutCubic = (x) => 1 - Math.pow(1 - x, 3);
const phaseProgress = (t, from, to) => clamp01((t - from) / (to - from));

function llToVec(lat, lon, r) {
  const c = Math.cos(lat) * r;
  return { x: c * Math.cos(lon), y: c * Math.sin(lon), z: r * Math.sin(lat) };
}

const STATION_VEC = llToVec(STATION.lat, STATION.lon, 1);

/* -------------------------------------------------------------- projection --
 * Orthographic. Rotate the Earth-fixed vector into a camera frame, then drop
 * the depth axis. Returns d = depth (positive is the near hemisphere),
 * h = screen right, v = screen up.
 *
 * This is the standard formulation: d is cos(c), the angular distance cosine,
 * and h/v are the usual x/y of an orthographic projection.
 */
function makeProjection(lat0, lon0) {
  const sLat = Math.sin(lat0), cLat = Math.cos(lat0);
  const sLon = Math.sin(lon0), cLon = Math.cos(lon0);
  return function project(p) {
    const x1 =  p.x * cLon + p.y * sLon;
    const y1 = -p.x * sLon + p.y * cLon;
    const z1 =  p.z;
    return {
      d:  x1 * cLat + z1 * sLat,
      h:  y1,
      v: -x1 * sLat + z1 * cLat,
    };
  };
}

/* A point is hidden when it is behind the sphere *and* inside its silhouette.
   For surface points this reduces to d < 0; for satellites above the surface
   it correctly keeps them visible past the limb. */
const hidden = (c) => c.d < 0 && Math.hypot(c.h, c.v) < 1;

/* --------------------------------------------------------------- graticule --
 * 15° spacing, sampled at 3°. Built once. Back-facing lines are drawn at
 * reduced opacity rather than culled — that is what makes it read as a
 * wireframe sphere instead of a disc.
 */
function buildGraticule() {
  const lines = [];
  for (let lon = -180; lon < 180; lon += 15) {
    const pts = [];
    for (let lat = -90; lat <= 90; lat += 3) pts.push(llToVec(lat * DEG, lon * DEG, 1));
    lines.push(pts);
  }
  for (let lat = -75; lat <= 75; lat += 15) {
    const pts = [];
    for (let lon = -180; lon <= 180; lon += 3) pts.push(llToVec(lat * DEG, lon * DEG, 1));
    lines.push(pts);
  }
  return lines;
}
const GRATICULE = buildGraticule();

/* ------------------------------------------------------------------- orbit --
 * Circular, 98° inclination, 800 km. Position from the argument of latitude,
 * rotated by the right ascension of the ascending node.
 */
function eciAt(sat, u) {
  const cu = Math.cos(u),  su = Math.sin(u);
  const ci = Math.cos(INCLINATION), si = Math.sin(INCLINATION);
  const cO = Math.cos(sat.raan),    sO = Math.sin(sat.raan);
  return {
    x: R_SAT * (cO * cu - sO * su * ci),
    y: R_SAT * (sO * cu + cO * su * ci),
    z: R_SAT * (su * si),
  };
}

/* Inertial → Earth-fixed. Subtracting the Earth's rotation here is what turns
   a closed circle into a ground track that drifts west on every revolution. */
function toEcef(p, spin) {
  const c = Math.cos(spin), s = Math.sin(spin);
  return { x: p.x * c + p.y * s, y: -p.x * s + p.y * c, z: p.z };
}

const spinAt = (t) => TAU * t / ROT_PERIOD_MS;
const argAt  = (sat, t) => sat.u0 + TAU * t / ORBIT_PERIOD_MS;
const satAt  = (sat, t) => toEcef(eciAt(sat, argAt(sat, t)), spinAt(t));

/* Elevation of a satellite above the station's local horizon. One dot product
   decides the link colour, and it is the same test the platform's orbit
   service performs for real. */
function elevation(p) {
  const dx = p.x - STATION_VEC.x;
  const dy = p.y - STATION_VEC.y;
  const dz = p.z - STATION_VEC.z;
  const len = Math.hypot(dx, dy, dz);
  return Math.asin((STATION_VEC.x * dx + STATION_VEC.y * dy + STATION_VEC.z * dz) / len);
}

/* Satellite 0's node and phase are not arbitrary. They are the solution to a
 * search over the (RAAN, argument-of-latitude) grid for an orbit that puts a
 * real pass on the intro's timeline:
 *
 *     t=0      2.5°  — above the local horizon so the beam points up, but
 *                      below the 5° mask, so there is no link yet: amber
 *     t=1200  20.4°  — rising, which is what the pull-back is tracking
 *     t=2800  65.0°  — high overhead as the globe resolves: green
 *     t=4200  13.9°  — still up at settle; sets around t=5400
 *
 * Elevation alone is not enough. It is measured from the station's local
 * horizon, not the camera's, so a high pass toward the sub-camera point still
 * projects *below* the station on screen and the opening beam appears to
 * point into the ground. The search therefore also requires the satellite to
 * project above the station through the close-up and pull-back.
 *
 * Solved offline rather than at load: it is a half-million-point search and
 * there is no reason to make every visitor's browser repeat it. To re-derive,
 * scan both angles keeping configurations whose elevation at t=0 is between
 * 1° and 9° and rising, which project above the station at t = 0, 600, 1200,
 * 1900 and 2600, and whose peak exceeds 35° between t=2400 and t=5600.
 * The other two satellites are spaced 120° away in node so the three orbital
 * planes read as distinct.
 */
const SAT0 = { raan: 262.0 * DEG, u0: 143.5 * DEG };

/* The other two satellites replay satellite 0's pass, staggered in time.
 *
 * Spacing them by an arbitrary 120° of node looked fine and was wrong: their
 * ground tracks never came within reach of the station, so they peaked at
 * 0.1° and 1.8° elevation, no link was ever acquired, no next pass was ever
 * findable, and the amber in the legend described something that could not
 * happen.
 *
 * Instead, derive them. To make a satellite repeat satellite 0's geometry T
 * later, rotate its orbital plane by the Earth's rotation over T and wind its
 * phase back by the orbit's own advance over T:
 *
 *     Rz(-spin(T)) · eci(raan + spin(T), u0) = eci(raan, u0)
 *
 * so the two cancel exactly and every satellite gets the same real 77.6° pass
 * over the station, one stagger apart. There is then no moment with neither an
 * acquired link nor a predictable next pass.
 */
const PASS_STAGGER_MS = ORBIT_PERIOD_MS;   // passes arrive every 40 s

const SATS = [0, 1, 2].map((i) => {
  const T = i * PASS_STAGGER_MS;
  return {
    raan: SAT0.raan + TAU * T / ROT_PERIOD_MS,
    u0:   SAT0.u0   - TAU * T / ORBIT_PERIOD_MS,
  };
});

/* Which satellite is next to rise. A real forward search, recomputed once a
   second rather than every frame. Satellites already up are not candidates. */
let nextPass = -1;
let nextPassAt = -Infinity;

/* The search runs a full globe revolution ahead, not one orbit. One orbit is
   not enough: the Earth turns 120° in that time here, so a plane that misses
   the station on this revolution can easily catch it on the next, and a
   shorter horizon returns "no next pass" while three satellites are in view. */
function findNextPass(t) {
  let best = -1, bestDt = Infinity;
  const step = ORBIT_PERIOD_MS / 160;
  for (let i = 0; i < SATS.length; i++) {
    if (elevation(satAt(SATS[i], t)) > HORIZON_MASK) continue;
    for (let dt = 0; dt < ROT_PERIOD_MS; dt += step) {
      if (elevation(satAt(SATS[i], t + dt)) > HORIZON_MASK) {
        if (dt < bestDt) { bestDt = dt; best = i; }
        break;
      }
    }
  }
  return best;
}

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

/* Strokes a set of polylines in two passes — near hemisphere, then far — so
   the whole graticule costs two stroke calls instead of one per line. */
function strokeLines(lines, project, cx, cy, R, colour, aFront, aBack, width) {
  for (let pass = 0; pass < 2; pass++) {
    const back = pass === 0;
    const alpha = back ? aBack : aFront;
    if (alpha <= 0.004) continue;

    ctx.beginPath();
    for (const pts of lines) {
      let pen = false;
      for (const p of pts) {
        const c = project(p);
        if (hidden(c) !== back) { pen = false; continue; }
        const x = cx + c.h * R;
        const y = cy - c.v * R;
        if (pen) ctx.lineTo(x, y);
        else { ctx.moveTo(x, y); pen = true; }
      }
    }
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = rgba(colour, 1);
    ctx.lineWidth = width;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function orbitPath(sat, t, steps) {
  const pts = [];
  const spin = spinAt(t);
  const u = argAt(sat, t);
  for (let i = 0; i <= steps; i++) pts.push(toEcef(eciAt(sat, u + (i / steps) * TAU), spin));
  return pts;
}

/* The sub-satellite track over roughly one revolution either side of now,
   projected onto the surface. Computed at many different times, which is why
   it drifts. */
function groundTrack(sat, t, steps) {
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    const tt = t + ((i / steps) - 0.5) * ORBIT_PERIOD_MS;
    const p = satAt(sat, tt);
    const r = Math.hypot(p.x, p.y, p.z);
    pts.push({ x: p.x / r, y: p.y / r, z: p.z / r });
  }
  return pts;
}

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
    strokeLines(GRATICULE, project, cx, cy, R, COLOUR.rule, aGrat * 0.95, aGrat * 0.35, 1);
  }

  if (t - nextPassAt > 1000) { nextPass = findNextPass(t); nextPassAt = t; }

  /* Ground track of the next predicted pass only — one is informative, three
     is clutter. Drawn faint and finely dashed so it reads as a trace behind
     the wireframe rather than competing with the graticule: it is the one
     element showing a future path, and it should not shout about it. */
  if (nextPass >= 0 && aOthers > 0.004) {
    ctx.setLineDash([2, 6]);
    strokeLines([groundTrack(SATS[nextPass], t, 180)], project, cx, cy, R,
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
      strokeLines([orbitPath(sat, t, 120)], project, cx, cy, R,
                  COLOUR.rule, alpha * aGrat * 1.0, alpha * aGrat * 0.4, 1);
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

document.addEventListener('visibilitychange', () => {
  if (document.hidden) stop();
  else if (!reducedMotion.matches) start();
});

resize();
applyMotionPreference();
