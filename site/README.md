# site/

The static page at **meridian.org.in**. Plain HTML, CSS and vanilla JS — no framework, no build step, no npm. This directory *is* the deployment artefact.

It is not the dashboard. See `docs/DECISIONS.md` D-036 for why the public page and the live dashboard are separate surfaces.

## Running it locally

```
python -m http.server 8080 --directory site
```

then open `http://127.0.0.1:8080/`.

**Opening `index.html` directly from the filesystem will not work.** Assets are referenced as `/style.css` and `/main.js` — absolute paths, which is what Cloudflare Pages needs and what keeps them correct on any URL. Over `file://` those resolve to the root of your drive, so no CSS and no JS load and the page renders as unstyled text under a giant SVG mark. That is expected. Serve it over HTTP.

## Files

| Path | |
|---|---|
| `index.html` | All copy. Real semantic HTML — the page must read correctly with CSS and JS both disabled. |
| `style.css` | Tokens, layout, and the content reveal. The reveal is a CSS animation, not JS, so content still appears if `main.js` fails to load. |
| `main.js` | The canvas animation. |
| `fonts/` | IBM Plex Sans and Mono, latin-1 subset, SIL OFL-1.1. `OFL.txt` ships with them — see `ATTRIBUTION.md`. |
| `_headers` | Cloudflare Pages: CSP and cache policy. |
| `og-image.png`, `apple-touch-icon.png` | Generated. Never loaded by the page — crawlers and iOS only. |
| `tools/make-images.py` | Regenerates both. Needs Pillow. |

## The animation

An orthographic globe with a 15° graticule, a 98°-inclination circular orbit at 800 km, and link lines coloured by a real elevation calculation against the station's horizon. Time is compressed — one globe revolution is 120 s and one orbit 40 s, against 24 h and ~101 min in reality — but the projection, the ground track's westward drift and the horizon test are computed, not drawn.

**All intro timing is the `PHASES` object at the top of `main.js`.** Every phase reads its progress through `phaseProgress()`, so changing one number retimes the sequence and nothing else moves. `PHASES.settle` must stay equal to `--reveal-at` in `style.css`; the canvas and the content reveal are deliberately independent mechanisms that happen to agree.

The satellite constants (`SAT0`) are the solution to an offline search, not arbitrary — the comment above them states the constraints and how to re-derive them.

Two behaviours that are requirements, not niceties:

- `prefers-reduced-motion: reduce` renders one static settled frame and never starts the loop.
- Click, tap, `Escape` or any key skips the intro.

## Regenerating the images

```
python site/tools/make-images.py
```

Draws with the same projection and orbit model as `main.js`, and reads the shipped `.woff2` files directly, so there is no second copy of the fonts. Kept as full RGB: a 256-colour palette halves the file but quantises the green link line to grey.

## Deploying

Cloudflare Pages, Git integration, production branch `main`:

- Framework preset: **None**
- Build command: **empty**
- Build output directory: **`site`**

`_headers` is Pages-specific and applies automatically. The `www` → apex redirect is a dashboard **Redirect Rule**, not a `_redirects` file — Pages matches `_redirects` on path only, so a host-based rule there never fires.

The CSP in `_headers` allows no `unsafe-inline`, so `index.html` must never gain an inline `<style>`, an inline `<script>`, or a `style="..."` attribute. That constraint is what makes the "no third-party requests" claim enforced rather than asserted.
