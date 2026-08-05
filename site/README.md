# site/

The static site at **meridian.org.in**. Plain HTML, CSS and vanilla JS — no framework, no build step, no npm. This directory *is* the deployment artefact.

It is not the dashboard. See `docs/DECISIONS.md` D-036 for why the public site and the live dashboard are separate surfaces, and D-037 and D-038 for the two-theme system and the move to five pages.

## Running it locally

```
python -m http.server 8080 --directory site
```

then open `http://127.0.0.1:8080/`.

**Opening `index.html` directly from the filesystem will not work.** Assets are referenced as `/style.css` and `/main.js` — absolute paths, which is what Cloudflare Pages needs and what keeps them correct on any URL. Over `file://` those resolve to the root of your drive, so no CSS and no JS load and the page renders as unstyled text under a giant SVG mark. That is expected. Serve it over HTTP.

## Pages

| Path | |
|---|---|
| `index.html` | The front page. One non-scrolling screen — canvas globe, one heading, one paragraph, two links, footer. |
| `architecture/` | The layer diagram as inline SVG, the six modules, the rules, deployment. |
| `protocol/` | MSP 0.1: constraints, model, the four endpoints, message shapes, errors, versioning. |
| `docs/` | Index of every specification, linking to the canonical Markdown on GitHub. |
| `about/` | What Meridian is, what it will not do, where things stand, how to take part. |
| `404.html` | `noindex`. Shares the shell. |

The subpages carry no intro and no reveal, which is why they load instantly. Only `index.html` loads `main.js`.

## The document rail

Above **1180px**, `.doc` becomes a three-track grid — prose, an elastic spacer, then the rail — so the prose stays on the left margin and the rail lands on the right, under the theme toggle. Below that width the rail falls back into normal flow between the standfirst and the prose, as a two-column list of links.

**There is one contents list, not two.** Forcing a `<details>` open on wide screens would need `::details-content`, which is too new to rely on, and a second list is a list that can disagree with the first.

**The `<h2>` ids and the contents entries come from one pass over the same headings** (`toc.py`, in the job scratch). `verify_site.py` fails if any `href="#…"` on a page points at no element, because a mistyped fragment is silent otherwise.

**`position: sticky` works only because the body is `overflow-x: clip`, not `hidden`.** `hidden` would make the body a scroll container and pin the rail in place with no error anywhere. If that property is ever changed, the rail stops sticking.

**Below 1180px the rail costs nothing at all.** `orbit.js` is a **dynamic** `import()` behind `matchMedia('(min-width: 1180px)')`, together with the settled-moment search and the scroll-spy. A phone loading a document page makes zero requests for it. It was a static import with a `modulepreload` hint, so every phone fetched and parsed 12 KB and ran a 2 400-iteration search to feed a canvas that `display: none` was going to hide — the `offsetParent` guard only ever stopped the drawing. Deferred, not deleted: crossing the breakpoint activates both.

**Never add `<link rel="modulepreload" href="/orbit.js">` to a document page.** It defeats the whole arrangement. `verify_site.py` fails the build if it reappears there, and fails if it goes missing from `index.html`, where `main.js` imports statically and genuinely needs it.

**The rail canvas draws exactly once** — on load, and again only on `themechange` and `resize`. No `requestAnimationFrame`. That is what keeps TBT at 0 ms and the four document pages at Performance 100; do not turn it into a loop without re-running Lighthouse.

The moment it draws is *searched*, not hard-coded: the longest visible link over one globe revolution, with the satellite above twice the horizon mask and both endpoints on the near hemisphere. The comment in `rail.js` lists the three obvious alternatives and what each of them draws wrong.

## The module split

`orbit.js` holds the projection, the graticule, the orbit model and the elevation test. `main.js` and `rail.js` both import it, and both are `type="module"` — which is also why neither carries `defer` any more, since modules defer by default. Every page that loads one also carries `<link rel="modulepreload" href="/orbit.js">`, so the import is not a second round trip.

`script-src 'self'` covers modules and modulepreload alike. **No `_headers` change was needed for this and none should be.**

`theme.js` is deliberately *not* a module — a module would be deferred, and deferring it is exactly what reintroduces the theme flash.

**Editorial, not mirrored.** `architecture/` and `protocol/` are written for a reader arriving cold; they are not renderings of `docs/ARCHITECTURE.md` and `docs/MSP-SPEC.md` and must not become them. Those documents are authoritative and are linked as such.

## Files

| Path | |
|---|---|
| `style.css` | Tokens for both themes, layout, the prose layer, the rail, the wide footer, the 404 sweep and the content reveal. The reveal is a CSS animation, not JS, so content still appears if `main.js` fails to load. |
| `theme.js` | Theme resolution. **Loaded in `<head>` without `defer`, before `style.css`** — see below. Not a module. |
| `orbit.js` | The projection, graticule, orbit model and elevation test. Imported by both canvases. No DOM except `readPalette()`. |
| `main.js` | The home page's intro choreography. Home page only. |
| `rail.js` | The document rail: contents scroll-spy and one still canvas frame. Document pages only. |
| `.well-known/security.txt` | RFC 9116. Renew `Expires:` before it lapses — an expired file counts as none. |
| `fonts/` | IBM Plex Sans and Mono, latin-1 subset, SIL OFL-1.1. `OFL.txt` ships with them — see `ATTRIBUTION.md`. |
| `_headers` | Cloudflare Pages: CSP, HSTS and cache policy. |
| `robots.txt`, `sitemap.xml`, `site.webmanifest` | Crawlers and installability. The sitemap lists exactly the five indexed URLs; update it when a page is added. |
| `og-image.png`, `apple-touch-icon.png`, `favicon.ico`, `icon-*.png` | Generated. Never loaded by a page — crawlers, iOS and the manifest only. |
| `brand/` | Marketing exports. Generated. See `brand/README.md`. |
| `tools/make-images.py` | Regenerates all of the above. Needs Pillow. |

## Themes

`prefers-color-scheme` decides by default; the masthead toggle overrides it and persists in `localStorage` under `meridian-theme`.

**`theme.js` must stay in `<head>`, before the stylesheet, with no `defer`.** It blocks parsing exactly long enough to set `data-theme` on the root element before the first paint. Deferring it, or moving it after `style.css`, reintroduces a flash of the wrong theme for every visitor whose stored preference differs from their system setting. The CSP allows no inline script, which is why this is a file rather than the usual three lines in the head — that is the whole reason it exists as a separate request.

The stylesheet carries three sets of values: bare `:root` (light, the no-JS default), a `prefers-color-scheme: dark` media query (dark, the no-JS path), and `:root[data-theme="…"]` rules that outrank both by specificity. JS wins whenever it has run; the media queries carry the page when it has not.

**The canvas reads its palette from CSS.** `main.js` has no colour constants: `readPalette()` pulls the custom properties from the computed root style, and `theme.js` dispatches a `themechange` event on `document` that triggers a re-read and a redraw. Add a colour by adding a custom property, never by adding a constant to `main.js`. `tools/make-images.py` is the exception — it is a build-time tool with no DOM.

### Contrast

Every text pair clears WCAG AA. Measured, not assumed — computed with the WCAG relative-luminance formula and checked in the DevTools picker. Four of these failed at some point and were found by computing the ratios, never by looking at the page.

| Token | On dark `#08090D` | On light `#FAF9F7` | Used for |
|---|---|---|---|
| `--ink` | 15.96:1 | 17.20:1 | headings, links, emphasis, standfirst |
| `--ink-dim` | 8.20:1 | 8.86:1 | **body text**, table cells, code, legend labels |
| `--muted` | 4.88:1 | 6.03:1 | nav, metadata, contents list, list markers |
| `--signal` | 8.39:1 | 4.79:1 | above horizon |
| `--trace` | 7.79:1 | 5.14:1 | predicted / next pass |
| `--alert` | 5.01:1 | 6.28:1 | below horizon |
| `--ink-dim` on `--surface` | 7.66:1 | 8.18:1 | `<pre>` blocks |

**`--muted` is for text that is glanced at; `--ink-dim` is for text that is read.** That distinction is the rule, not the ratio. `--muted` clears AA at 4.88:1 and prose set in it still reads as washed out — the threshold is a proxy for legibility, not the thing itself. Nav labels, timestamps and tracked uppercase mono are `--muted`; anything set as a paragraph is `--ink-dim`. See D-039.

#### The two non-text colours

| Token | On dark | On light | |
|---|---|---|---|
| `--rule` | 1.71:1 | 1.68:1 | hairline dividers, borders, underlines |
| `--wire` | 1.25:1 | 1.87:1 | graticule and orbit strokes on canvas |

These are **not text colours and must never be set on text** — `.legend dd` was, at about 1.3:1, and `.prose li::marker` was, which is what the `--ink-dim` / `--muted` split exists to fix.

They are also not held to 4.5:1, because a decorative divider is exempt. They are held to about **1.7:1** instead, which is a judgement rather than a threshold: enough that a rule is unambiguously present, low enough that it stays a hairline and does not become a box. Both sat near 1.25:1 before D-039 and were effectively invisible on any panel without OLED blacks.

`--rule` and `--wire` are **deliberately different in the dark theme.** They were once the same value by coincidence. A divider between blocks of prose and a wireframe stroke on near-black do not want the same weight.

The layer diagram's `.d-box` and `.d-line` use `--muted`, not `--rule`. A diagram needed to understand the page is a graphical object under WCAG 1.4.11 and owes 3:1, which no hairline value clears.

## The animation

An orthographic globe with a 15° graticule, a 98°-inclination circular orbit at 800 km, and link lines coloured by a real elevation calculation against the station's horizon. Time is compressed — one globe revolution is 120 s and one orbit 40 s, against 24 h and ~101 min in reality — but the projection, the ground track's westward drift and the horizon test are computed, not drawn.

**All intro timing is the `PHASES` object at the top of `main.js`.** Every phase reads its progress through `phaseProgress()`, so changing one number retimes the sequence and nothing else moves. `PHASES.settle` must stay equal to `--reveal-at` in `style.css`; the canvas and the content reveal are deliberately independent mechanisms that happen to agree.

The satellite constants (`SAT0`) are the solution to an offline search, not arbitrary — the comment above them states the constraints and how to re-derive them.

Two behaviours that are requirements, not niceties:

- `prefers-reduced-motion: reduce` renders one static settled frame and never starts the loop.
- Click, tap, `Escape` or any key skips the intro.

### The intro is opt-in, and that is the important part

`theme.js` adds `intro` to the root element before the first paint, and takes it away again after 900 ms unless `main.js` has added `intro-ready` from its first painted frame. **The 4.2 s delay only applies when the globe is actually running.**

It used to be unconditional, with JS able only to cancel it — so a `main.js` that was blocked, stale or broken produced the full blank wait *and* no globe. Test the failure, not the success: block `/main.js` in DevTools and reload, then block `/orbit.js`. Content must be immediate in both cases. If you change any of this, that is the check that matters.

The gate is keyed off `data-intro` on `<html>`, which only the home page carries. `theme.js` is not a module and must never become one — a module defers, and deferring it reintroduces both the theme flash and this gate arriving after paint.

**`visibility: hidden` is in the reveal keyframe on purpose.** `opacity: 0` leaves an element hit-tested, focusable and in the accessibility tree, so the invisible header and links responded to hover and click for the whole intro. With `fill-mode: both` the `from` state holds through the delay and `visibility` steps to visible on the animation's first frame, so it costs nothing visually. Do not remove it.

**On the front page's Lighthouse Performance score.** It reports 100. **That is not a speed result and must not be quoted as one.** Real LCP, measured with a `PerformanceObserver`, is **4 400 ms** — the `.lede`, exactly as designed. Lighthouse's simulator models when the element's resources are ready and does not model an animation delay, so it returns 0.4 s. FCP, which is real, is 108 ms and comes from the canvas. D-038 recorded this as null; D-041 explains why the number appeared and why it means nothing. The four subpages score 100 in all four categories on desktop and 99 on mobile, and those are real.

## Regenerating the images

```
python site/tools/make-images.py            # everything
python site/tools/make-images.py --what og      # the social card
python site/tools/make-images.py --what icons   # favicon, iOS, manifest
python site/tools/make-images.py --what brand   # site/brand/
```

The social card draws with the same projection and orbit model as `main.js`, and reads the shipped `.woff2` files directly, so there is no second copy of the fonts. Kept as full RGB: a 256-colour palette halves the file but quantises the green link line to grey.

The mark is drawn by one `mark()` function shared by every output, built from the same geometry as `favicon.svg` — a circle of radius 22 in a 64-unit box, plus the left half of an ellipse with a horizontal radius of 10.5.

## Page transitions

`@view-transition { navigation: auto; }` in `style.css`. Cross-document transitions need **both** pages to opt in; every page loads this stylesheet, so every page does. Chrome and Edge cross-fade; Firefox and Safari navigate normally, which is what the site did before and needs no fallback.

Reduced motion cancels it on the pseudo-elements, not with `@view-transition { navigation: none }` inside the media query. The nested form is newer and less certainly supported; the pseudo-element form is unambiguously valid. Confirmed by reading the parsed `CSSViewTransitionRule` back out of `document.styleSheets` rather than by assuming the at-rule survived parsing — an unrecognised at-rule is dropped silently.

## Three Cloudflare settings that edit this site

The repository is not the last word on what visitors receive. Three dashboard settings rewrite it, and all three have caused a real bug. Check them after any Cloudflare change:

| Setting | Must be | Why |
|---|---|---|
| Caching → Configuration → **Browser Cache TTL** | `Respect Existing Headers` | At its default it overrode `_headers` and served CSS and JS with `max-age=14400` while HTML stayed at `max-age=0`. Filenames are not fingerprinted, so every deploy opened a **four-hour window where returning visitors ran new HTML against old CSS** — which renders every element added since the last release as an unstyled browser default. That is the "broken blue links" bug, and no amount of CSS fixes it. |
| Scrape Shield → **Email Address Obfuscation** | Off | Rewrites `mailto:` into `/cdn-cgi/l/email-protection#…`, shows `[email protected]`, and needs an injected script to decode. Blocked script, permanently hidden address. |
| **Web Analytics** | Off | Injects `static.cloudflareinsights.com/beacon.min.js`, which the CSP blocks. Console error on every load, no analytics collected. Do not "fix" it by adding the host to `script-src`. |
| Speed → Optimization → **Rocket Loader** | Off | Rewrites script tags and would break the ES modules outright. Currently off; check it stays that way. |

```
curl -sI https://meridian.org.in/style.css | grep -i cache-control   # max-age=0
curl -s  https://meridian.org.in/about/ | grep -c cdn-cgi            # 0
```

and load any page with the console open — it must be clean.

**Purge Everything after each deploy** until the TTL setting is confirmed.

## Deploying

> **Before the first deploy that includes the contact address: configure Cloudflare Email Routing.**
> `hello@meridian.org.in` appears on `/about/`, in every wide footer, in the `Organization` JSON-LD and in `security.txt`. It does not exist until routing is set up, and a published address that bounces is worse than none.
>
> Dashboard → **Compute → Email Service → Email Routing** → *Onboard Domain* → `meridian.org.in` → accept the MX/SPF/DKIM records. Then **Destination addresses** → add the real mailbox → confirm the verification mail (nothing routes until this is done). Then **Routing rules** → custom address `hello@meridian.org.in` → forward to it. A catch-all is worth enabling too. Free on every plan, forward-only — it delivers into an existing mailbox, it does not create one.
>
> Verify by mailing the address from an unrelated account.

Cloudflare Pages, Git integration, production branch `main`:

- Framework preset: **None**
- Build command: **empty**
- Build output directory: **`site`**

`_headers` is Pages-specific and applies automatically.

The CSP allows no `unsafe-inline`, so no page may ever gain an inline `<style>`, an inline `<script>`, or a `style="..."` attribute. That constraint is what makes the "no third-party requests" claim enforced rather than asserted. `manifest-src 'self'` is stated explicitly because it falls back to `default-src`, and `'none'` would block `site.webmanifest`.

JSON-LD lives in `<script type="application/ld+json">` blocks. Those are data blocks, never prepared as script, so `script-src` does not apply — verified with no console violation on any page in either theme. If a browser ever reports one, the fix is a `'sha256-…'` hash in `script-src`, not `unsafe-inline`.

### The `www` → apex redirect is not a file, and cannot be

It is a Cloudflare dashboard **Redirect Rule**. A `_redirects` file cannot replace it: Pages matches `_redirects` on path only and [documents domain-level redirects as unsupported](https://developers.cloudflare.com/pages/configuration/redirects/), so

- `https://www.meridian.org.in/* → …` never fires, silently, and
- the path-only form `/* → https://meridian.org.in/:splat` puts the **apex into an infinite redirect loop**.

Verify it after any DNS change:

```
curl -I https://www.meridian.org.in
```

should return `301` to `https://meridian.org.in/`. The only mechanism that could live in this repository is a Pages Function reading the `Host` header, which would put a Worker in front of every request to a site that otherwise needs none.
