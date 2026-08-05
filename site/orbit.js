/* Meridian — meridian.org.in
 *
 * The orbit model and the projection, shared by the home page's intro
 * animation (main.js) and the still frame in the document rail (rail.js).
 *
 * This file was carved out of main.js when the rail needed the same maths.
 * Nothing here changed in the move — a second copy of an orthographic
 * projection in one directory is a second copy that can drift, and the two
 * canvases are supposed to be showing the same sky.
 *
 * No DOM beyond readPalette(), which reads the theme's custom properties.
 */

export const TAU = Math.PI * 2;
export const DEG = Math.PI / 180;

/* Station 001. The one coordinate pair in the repository — docs/MSP-SPEC.md §4.1. */
export const STATION = { lat: 12.9716 * DEG, lon: 77.5946 * DEG };

const R_EARTH_KM = 6371;
const ALT_KM     = 800;
export const R_SAT       = (R_EARTH_KM + ALT_KM) / R_EARTH_KM;   // 1.1256 Earth radii
export const INCLINATION = 98 * DEG;                             // sun-synchronous
export const HORIZON_MASK = 5 * DEG;                             // below this, no link

export const ROT_PERIOD_MS   = 120000;   // one globe revolution
export const ORBIT_PERIOD_MS =  40000;   // one satellite revolution

/* ---------------------------------------------------------------- colour --
 * The palette is not defined here either. It is read from the custom
 * properties in style.css, which is the only place either theme's values are
 * written down.
 *
 * That matters more than it looks: --signal, --alert and --trace are semantic,
 * each means exactly one thing, and the legend in the home page's footer
 * paints its swatches from the same properties this reads. A second copy in
 * JavaScript would be a copy that can drift out of agreement with the key that
 * claims to explain it.
 *
 * Kept as components rather than strings so they can be interpolated.
 */

const GREY = [128, 128, 128];

export function hexToRgb(value) {
  const h = String(value).trim().replace(/^#/, '');
  const full = h.length === 3 ? h[0] + h[0] + h[1] + h[1] + h[2] + h[2] : h;
  /* Unparseable means style.css did not load, in which case the page is
     unstyled text and the canvas is the least of it. Grey, and carry on. */
  if (!/^[0-9a-f]{6}$/i.test(full)) return GREY;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function readPalette() {
  const cs = getComputedStyle(document.documentElement);
  const c = (name) => hexToRgb(cs.getPropertyValue('--' + name));
  return {
    ink:    c('ink'),
    rule:   c('rule'),
    wire:   c('wire'),     // graticule and orbit paths
    muted:  c('muted'),
    signal: c('signal'),   // above horizon — link acquired
    alert:  c('alert'),    // below horizon — no link
    trace:  c('trace'),    // predicted / next pass
    bg:     c('bg'),
  };
}

export const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
export const mix  = (a, b, t) => [a[0] + (b[0] - a[0]) * t,
                                  a[1] + (b[1] - a[1]) * t,
                                  a[2] + (b[2] - a[2]) * t];

/* -------------------------------------------------------------------- math -- */

export const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
export const lerp = (a, b, t) => a + (b - a) * t;
export const easeOutCubic = (x) => 1 - Math.pow(1 - x, 3);

export function llToVec(lat, lon, r) {
  const c = Math.cos(lat) * r;
  return { x: c * Math.cos(lon), y: c * Math.sin(lon), z: r * Math.sin(lat) };
}

export const STATION_VEC = llToVec(STATION.lat, STATION.lon, 1);

/* -------------------------------------------------------------- projection --
 * Orthographic. Rotate the Earth-fixed vector into a camera frame, then drop
 * the depth axis. Returns d = depth (positive is the near hemisphere),
 * h = screen right, v = screen up.
 *
 * This is the standard formulation: d is cos(c), the angular distance cosine,
 * and h/v are the usual x/y of an orthographic projection.
 */
export function makeProjection(lat0, lon0) {
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
export const hidden = (c) => c.d < 0 && Math.hypot(c.h, c.v) < 1;

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
export const GRATICULE = buildGraticule();

/* ------------------------------------------------------------------- orbit --
 * Circular, 98° inclination, 800 km. Position from the argument of latitude,
 * rotated by the right ascension of the ascending node.
 */
export function eciAt(sat, u) {
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
export function toEcef(p, spin) {
  const c = Math.cos(spin), s = Math.sin(spin);
  return { x: p.x * c + p.y * s, y: -p.x * s + p.y * c, z: p.z };
}

export const spinAt = (t) => TAU * t / ROT_PERIOD_MS;
export const argAt  = (sat, t) => sat.u0 + TAU * t / ORBIT_PERIOD_MS;
export const satAt  = (sat, t) => toEcef(eciAt(sat, argAt(sat, t)), spinAt(t));

/* Elevation of a satellite above the station's local horizon. One dot product
   decides the link colour, and it is the same test the platform's orbit
   service performs for real. */
export function elevation(p) {
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

export const SATS = [0, 1, 2].map((i) => {
  const T = i * PASS_STAGGER_MS;
  return {
    raan: SAT0.raan + TAU * T / ROT_PERIOD_MS,
    u0:   SAT0.u0   - TAU * T / ORBIT_PERIOD_MS,
  };
});

/* Which satellite is next to rise. A real forward search. Satellites already
 * up are not candidates.
 *
 * The search runs a full globe revolution ahead, not one orbit. One orbit is
 * not enough: the Earth turns 120° in that time here, so a plane that misses
 * the station on this revolution can easily catch it on the next, and a
 * shorter horizon returns "no next pass" while three satellites are in view.
 */
export function findNextPass(t) {
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

/* --------------------------------------------------------------- geometry -- */

export function orbitPath(sat, t, steps) {
  const pts = [];
  const spin = spinAt(t);
  const u = argAt(sat, t);
  for (let i = 0; i <= steps; i++) pts.push(toEcef(eciAt(sat, u + (i / steps) * TAU), spin));
  return pts;
}

/* The sub-satellite track over roughly one revolution either side of now,
   projected onto the surface. Computed at many different times, which is why
   it drifts. */
export function groundTrack(sat, t, steps) {
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    const tt = t + ((i / steps) - 0.5) * ORBIT_PERIOD_MS;
    const p = satAt(sat, tt);
    const r = Math.hypot(p.x, p.y, p.z);
    pts.push({ x: p.x / r, y: p.y / r, z: p.z / r });
  }
  return pts;
}

/* --------------------------------------------------------------- drawing --
 * Strokes a set of polylines in two passes — near hemisphere, then far — so
 * the whole graticule costs two stroke calls instead of one per line.
 *
 * Takes the context explicitly rather than closing over one: two canvases use
 * this now, and the home page's is not the rail's.
 */
export function strokeLines(ctx, lines, project, cx, cy, R, colour, aFront, aBack, width) {
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
