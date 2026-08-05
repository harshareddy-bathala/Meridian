/* Meridian — meridian.org.in
 *
 * The document rail: a contents list that tracks the heading you are reading,
 * and one still frame of the same globe the home page animates.
 *
 * Two rules govern this file.
 *
 * 1. No requestAnimationFrame. The rail draws once, and again only when the
 *    theme or the size changes. The four document pages score 100 on
 *    Lighthouse Performance and a rail that spins would put that at risk for
 *    decoration that competes with the prose it sits beside.
 *
 * 2. Nothing here is required to read the page. The contents links are plain
 *    anchors and work with this file blocked; the canvas is decorative and
 *    aria-hidden. This only ever adds.
 */

import {
  TAU, DEG, STATION, STATION_VEC, HORIZON_MASK,
  readPalette, rgba,
  makeProjection, hidden, GRATICULE,
  spinAt, satAt, elevation, SATS,
  orbitPath, strokeLines,
} from './orbit.js';

/* ---------------------------------------------------------------- contents --
 * Highlights the section currently being read. The observer fires on the
 * headings themselves with the bottom 70% of the viewport masked off, so the
 * "current" heading is the last one to have crossed the upper third rather
 * than whichever happens to be visible — which, on a tall screen, is four of
 * them at once.
 */
function contents() {
  const links = Array.from(document.querySelectorAll('.toc a[href^="#"]'));
  if (!links.length || !('IntersectionObserver' in window)) return;

  const byId = new Map(links.map((a) => [decodeURIComponent(a.hash.slice(1)), a]));
  const headings = Array.from(byId.keys())
    .map((id) => document.getElementById(id))
    .filter(Boolean);
  if (!headings.length) return;

  const seen = new Set();

  function mark() {
    /* The topmost heading that has been passed. Falls back to the first entry
       before any heading has crossed, so the list is never blank. */
    let current = headings.find((h) => seen.has(h.id));
    for (const h of headings) if (seen.has(h.id)) current = h;
    const id = current ? current.id : headings[0].id;

    for (const [key, a] of byId) {
      if (key === id) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
    }
  }

  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.boundingClientRect.top < 0 || e.isIntersecting) seen.add(e.target.id);
      else seen.delete(e.target.id);
    }
    mark();
  }, { rootMargin: '0px 0px -70% 0px', threshold: 0 });

  for (const h of headings) io.observe(h);
  mark();
}

/* ------------------------------------------------------------------ scene --
 * One settled frame: the wireframe, one orbit, and a link line to a satellite
 * that is actually above the station's horizon. The moment drawn is found by
 * searching forward for one, not by picking a number that looked right — the
 * green line means "above the horizon mask" here exactly as it does on the
 * home page, and drawing it green at a moment when it would not be would make
 * the legend a lie on a page that does not carry the legend to correct it.
 */

const SCENE_PX = 168;          // CSS pixels; the panel is 15.5rem at most
const CAM_LAT = 18 * DEG;      // the home page's settled camera
const CAM_LON_OFFSET = 25 * DEG;

/* The moment to draw. Searched, not chosen — the link line is the whole point
 * of the picture and three obvious ways of picking a time all produce a bad
 * one:
 *
 *   the peak of the pass    77° elevation is nearly overhead; the line
 *                           collapses to about 11px and reads as a dot
 *   a fixed elevation       lands on moments where the satellite is behind
 *                           the globe, so the line is drawn at 0.2 alpha
 *   t = SETTLE_MS           no pass in progress at all on some geometries
 *
 * So: over one full globe revolution, take the moment with the longest
 * *visible* link, subject to the satellite being comfortably above the mask
 * (twice it, 10°) with both endpoints on the near hemisphere. The result is a
 * real above-mask moment — the line is green because the elevation test says
 * so, exactly as on the home page, which matters because this page does not
 * carry the legend that would otherwise explain the colour.
 *
 * Done in normalised units, so the answer does not depend on the canvas size.
 * About 2400 iterations, once, at module load.
 */
function findSettledMoment() {
  let bestT = 0, bestLen = -1;
  for (let t = 0; t < 120000; t += 50) {
    const p = satAt(SATS[0], t);
    if (elevation(p) < 2 * HORIZON_MASK) continue;

    const project = makeProjection(CAM_LAT, STATION.lon + CAM_LON_OFFSET + spinAt(t));
    const c = project(p);
    const h = project(STATION_VEC);
    if (hidden(c) || hidden(h)) continue;
    /* Keep the satellite near the disc; one that has wandered far past the
       limb is geometrically fine and reads as a stray dot. */
    if (Math.hypot(c.h, c.v) > 1.18) continue;

    const len = Math.hypot(c.h - h.h, c.v - h.v);
    if (len > bestLen) { bestLen = len; bestT = t; }
  }
  return bestT;
}
const T = findSettledMoment();

function drawScene(canvas) {
  /* display:none below the rail's breakpoint. Nothing to draw, and
     getBoundingClientRect would be 0×0. */
  if (canvas.offsetParent === null) return;

  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const px = Math.round(canvas.getBoundingClientRect().width) || SCENE_PX;

  canvas.width = Math.round(px * dpr);
  canvas.height = Math.round(px * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, px, px);

  const C = readPalette();
  /* The home page's settled camera, at the same instant the scene is drawn. */
  const project = makeProjection(CAM_LAT, STATION.lon + CAM_LON_OFFSET + spinAt(T));

  const cx = px / 2;
  const cy = px / 2;
  const R = px * 0.42;

  /* The limb. */
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, TAU);
  ctx.strokeStyle = rgba(C.muted, 1);
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.globalAlpha = 1;

  strokeLines(ctx, GRATICULE, project, cx, cy, R, C.wire, 0.95, 0.35, 1);
  strokeLines(ctx, [orbitPath(SATS[0], T, 120)], project, cx, cy, R, C.wire, 1, 0.4, 1);

  const p = satAt(SATS[0], T);
  const c = project(p);
  const sx = cx + c.h * R;
  const sy = cy - c.v * R;

  const here = project(STATION_VEC);
  const hx = cx + here.h * R;
  const hy = cy - here.v * R;

  const up = elevation(p) > HORIZON_MASK;
  const colour = up ? C.signal : C.alert;

  ctx.globalAlpha = !hidden(c) && !hidden(here) ? 0.95 : 0.2;
  ctx.strokeStyle = rgba(colour, 1);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(hx, hy);
  ctx.lineTo(sx, sy);
  ctx.stroke();

  ctx.globalAlpha = hidden(c) ? 0.2 : 1;
  ctx.fillStyle = rgba(colour, 1);
  ctx.beginPath();
  ctx.arc(sx, sy, 2.5, 0, TAU);
  ctx.fill();

  /* The station. Same glyph as the settled home page: a ring with two ticks. */
  ctx.globalAlpha = hidden(here) ? 0.25 : 1;
  ctx.strokeStyle = rgba(C.ink, 1);
  ctx.beginPath();
  ctx.arc(hx, hy, 3, 0, TAU);
  ctx.moveTo(hx - 7, hy); ctx.lineTo(hx - 4, hy);
  ctx.moveTo(hx + 4, hy); ctx.lineTo(hx + 7, hy);
  ctx.stroke();
  ctx.globalAlpha = 1;
}

/* -------------------------------------------------------------------- init -- */

contents();

const scene = document.querySelector('.rail-scene');
if (scene) {
  const redraw = () => drawScene(scene);
  redraw();
  document.addEventListener('themechange', redraw);

  /* Only the width matters, and it changes at the rail's breakpoint. Coalesced
     so a drag across the breakpoint does not redraw on every intermediate
     pixel. */
  let pending = 0;
  window.addEventListener('resize', () => {
    clearTimeout(pending);
    pending = setTimeout(redraw, 120);
  }, { passive: true });
}
