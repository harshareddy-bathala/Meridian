"""Generate the site's raster images.

Four groups, selectable with --what:

  og      the social card, og-image.png
  icons   favicon.ico, apple-touch-icon.png, and the web manifest's icons
  brand   site/brand/ — the marketing exports, including the profile picture
  globe   the still globe, spliced into index.html as inline SVG. This is the
          one output that is not an image file: it is the picture the home page
          shows when main.js cannot run.

The globe is a real orthographic projection of a 15-degree graticule with a
98-degree inclined circular orbit at 800 km — the same model as site/main.js,
not an illustration of it. The station sits at 12.9716 N, 77.5946 E and the
link line is green because an elevation calculation says the satellite is
above its horizon.

Run:  python site/tools/make-images.py
Needs: Pillow (its bundled FreeType reads the shipped .woff2 files directly,
so there is no second copy of the fonts anywhere).
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

# Optional, and deliberately so. Every raster output needs Pillow; the still
# globe is SVG and needs nothing but this file. CI checks that the block spliced
# into index.html is current, and it should not have to install an imaging
# library to do it — so a missing Pillow is an error only for the outputs that
# actually use it, raised by require_pillow() below.
try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:
    Image = ImageDraw = ImageFont = None

SITE = Path(__file__).resolve().parent.parent
FONTS = SITE / "fonts"
BRAND = SITE / "brand"

W, H = 1200, 630
SS = 3  # supersampling; Pillow's draw primitives are not antialiased

BG = (8, 9, 13)
INK = (232, 230, 225)
MUTED = (110, 116, 128)
RULE = (30, 34, 41)
SIGNAL = (107, 184, 138)

# The light theme's ground and ink, for exports that will sit on a light page.
PAPER = (250, 249, 247)
INK_DARK = (20, 22, 27)

DEG = math.pi / 180
TAU = math.tau

CX, CY, R = 985.0, 250.0, 268.0  # globe: upper right, bleeding off both edges
CAM_LAT, CAM_LON = 18 * DEG, 102.6 * DEG  # matches main.js settled camera

STATION_LAT, STATION_LON = 12.9716 * DEG, 77.5946 * DEG
R_SAT = (6371 + 800) / 6371
INCL = 98 * DEG


# --------------------------------------------------------------- geometry --


def ll(lat: float, lon: float, r: float = 1.0):
    c = math.cos(lat) * r
    return c * math.cos(lon), c * math.sin(lon), r * math.sin(lat)


def project(v):
    """Orthographic. Returns (depth, screen-right, screen-up); depth > 0 is near."""
    x, y, z = v
    s_lat, c_lat = math.sin(CAM_LAT), math.cos(CAM_LAT)
    s_lon, c_lon = math.sin(CAM_LON), math.cos(CAM_LON)
    x1 = x * c_lon + y * s_lon
    y1 = -x * s_lon + y * c_lon
    return x1 * c_lat + z * s_lat, y1, -x1 * s_lat + z * c_lat


def hidden(d: float, h: float, v: float) -> bool:
    return d < 0 and math.hypot(h, v) < 1


def screen(h: float, v: float):
    return CX + h * R, CY - v * R


def runs(lines, *, near: bool, to_screen=screen):
    """Split polylines into contiguous runs on one side of the limb.

    `to_screen` maps a projected (h, v) pair to output coordinates. It defaults
    to the card's own frame; the SVG still globe passes its own, because that
    drawing is 200 units square and this one is 1200 by 630. Everything before
    the mapping — the projection and the limb test — is shared, which is the
    point: there is one orthographic projection in this file, not two.
    """
    out = []
    for pts in lines:
        run = []
        for p in pts:
            d, h, v = project(p)
            if hidden(d, h, v) == near:
                if len(run) > 1:
                    out.append(run)
                run = []
                continue
            run.append(to_screen(h, v))
        if len(run) > 1:
            out.append(run)
    return out


def graticule(step_deg: int = 3):
    """A 15-degree graticule, sampled every `step_deg` along each line.

    The sampling step is a straight trade of file size against how polygonal
    the curves look. A chord subtending `step_deg` on a circle of radius R
    departs from it by R(1 - cos(step_deg / 2)): at 3 degrees and 268 px that
    is 0.09 px, and at 15 degrees and 86 px it is 0.74 px. The card is a raster
    and can afford 3; the SVG ships inside index.html and cannot.
    """
    lines = []
    for lon in range(-180, 180, 15):
        lines.append([ll(lat * DEG, lon * DEG) for lat in range(-90, 91, step_deg)])
    for lat in range(-75, 76, 15):
        lines.append([ll(lat * DEG, lon * DEG) for lon in range(-180, 181, step_deg)])
    return lines


def sat_at(raan: float, u: float):
    ci, si = math.cos(INCL), math.sin(INCL)
    co, so = math.cos(raan), math.sin(raan)
    cu, su = math.cos(u), math.sin(u)
    return (
        R_SAT * (co * cu - so * su * ci),
        R_SAT * (so * cu + co * su * ci),
        R_SAT * su * si,
    )


def orbit(raan: float, steps: int = 360):
    return [sat_at(raan, i / steps * TAU) for i in range(steps + 1)]


def elevation(p) -> float:
    s = ll(STATION_LAT, STATION_LON)
    d = (p[0] - s[0], p[1] - s[1], p[2] - s[2])
    n = math.hypot(*d)
    return math.asin((s[0] * d[0] + s[1] * d[1] + s[2] * d[2]) / n)


# Pick an orbit plane and phase putting the satellite mid-pass rather than at
# culmination: overhead, the link line collapses to nothing and the one piece
# of colour on the card is wasted. Among candidates near the target elevation,
# prefer the one that draws the longest visible line.
TARGET_EL = 32 * DEG
_best = (0.0, 0.0, -1.0)
for _i in range(360):
    _raan = _i / 360 * TAU
    for _j in range(720):
        _u = _j / 720 * TAU
        _p = sat_at(_raan, _u)
        if abs(elevation(_p) - TARGET_EL) > 0.6 * DEG:
            continue
        _d, _h, _v = project(_p)
        if hidden(_d, _h, _v):
            continue
        _sd, _sh, _sv = project(ll(STATION_LAT, STATION_LON))
        if _sd < 0.25:  # station must sit well inside the disc
            continue
        _sep = math.hypot(_h - _sh, _v - _sv)
        if _sep > _best[2]:
            _best = (_raan, _u, _sep)
RAAN, U_SAT, _ = _best
SAT = sat_at(RAAN, U_SAT)
PEAK_EL = elevation(SAT)

_sd, _sh, _sv = project(ll(STATION_LAT, STATION_LON))
STX, STY = screen(_sh, _sv)
_pd, _ph, _pv = project(SAT)
PTX, PTY = screen(_ph, _pv)


# ------------------------------------------------------------------- text --

COPY_LINES = [
    "Predictive scheduling",
    "and reliability for satellite",
    "ground stations.",
]
SUBLINE = "A pass lasts minutes and never repeats."
META_LEFT = "MERIDIAN.ORG.IN"
META_RIGHT = "LAUNCHING 2026 · APACHE-2.0"


# -------------------------------------------------------------------- PNG --


def require_pillow() -> None:
    """Guard every raster entry point. `--what globe` never reaches this."""
    if Image is None:
        raise SystemExit(
            "Pillow is not installed. Every output except `--what globe` needs it:\n"
            "    pip install Pillow"
        )


def blend(fg, a: float):
    """Flatten an alpha against the background. Nothing here overlaps enough
    for real compositing to be worth the cost."""
    return tuple(round(BG[i] + (fg[i] - BG[i]) * a) for i in range(3))


def tracked(draw, xy, text, font, fill, spacing, anchor="ls"):
    """Pillow has no letter-spacing, so advance by hand."""
    width = sum(font.getlength(ch) + spacing for ch in text) - spacing
    x, y = xy
    if anchor == "rs":
        x -= width
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + spacing
    return width


def write_png() -> Path:
    require_pillow()
    img = Image.new("RGB", (W * SS, H * SS), BG)
    dr = ImageDraw.Draw(img)
    s = lambda v: v * SS  # noqa: E731
    poly = lambda run: [(x * SS, y * SS) for x, y in run]  # noqa: E731

    for run in runs(graticule(), near=False):
        dr.line(poly(run), fill=blend(RULE, 0.35), width=SS)
    for run in runs(graticule(), near=True):
        dr.line(poly(run), fill=blend(RULE, 0.95), width=SS)

    dr.ellipse(
        [s(CX - R), s(CY - R), s(CX + R), s(CY + R)],
        outline=blend(MUTED, 0.55),
        width=SS,
    )

    for run in runs([orbit(RAAN)], near=False):
        dr.line(poly(run), fill=blend(RULE, 0.45), width=SS)
    for run in runs([orbit(RAAN)], near=True):
        dr.line(poly(run), fill=blend(MUTED, 0.6), width=SS)

    dr.line([(s(STX), s(STY)), (s(PTX), s(PTY))], fill=blend(SIGNAL, 0.9), width=SS)
    dr.ellipse([s(PTX - 4), s(PTY - 4), s(PTX + 4), s(PTY + 4)], fill=SIGNAL)
    dr.ellipse(
        [s(STX - 4), s(STY - 4), s(STX + 4), s(STY + 4)],
        outline=INK,
        width=round(1.5 * SS),
    )

    # the mark: limb plus one meridian, same construction as favicon.svg
    mx, my, mr = 80 + 32 * 0.62, 78 + 32 * 0.62, 22 * 0.62
    mark(dr, s(mx), s(my), mr * SS, INK)

    sans = lambda px, w="Regular": ImageFont.truetype(  # noqa: E731
        str(FONTS / f"IBMPlexSans-{w}.woff2"), round(px * SS)
    )
    mono = lambda px: ImageFont.truetype(  # noqa: E731
        str(FONTS / "IBMPlexMono-Regular.woff2"), round(px * SS)
    )

    tracked(dr, (s(138), s(108)), "MERIDIAN", sans(21, "Medium"), INK, 7.6 * SS)
    dr.line([(s(80), s(148)), (s(W - 80), s(148))], fill=RULE, width=SS)

    big = sans(56)
    for i, line in enumerate(COPY_LINES):
        dr.text((s(80), s(290 + i * 66)), line, font=big, fill=INK, anchor="ls")

    dr.text((s(80), s(476)), SUBLINE, font=sans(21), fill=MUTED, anchor="ls")

    dr.line([(s(80), s(H - 96)), (s(W - 80), s(H - 96))], fill=RULE, width=SS)
    tracked(dr, (s(80), s(H - 56)), META_LEFT, mono(15), MUTED, 2.4 * SS)
    tracked(
        dr, (s(W - 80), s(H - 56)), META_RIGHT, mono(15), MUTED, 2.4 * SS, anchor="rs"
    )

    out = SITE / "og-image.png"
    # Kept as full RGB. A 256-colour palette halves the file but median-cut
    # spends the whole palette on the near-black gradient and quantises the
    # green link line away to grey — losing the one piece of colour the card
    # exists to show. The page never loads this file; only crawlers fetch it.
    img.resize((W, H), Image.LANCZOS).save(out, optimize=True)
    return out


# ------------------------------------------------------- the still globe --
#
# index.html carries a <canvas> that main.js animates. When main.js does not
# run — scripts blocked, a shield in the way, an ES module that fails to link,
# a browser with no 2D context — that canvas stays empty and the page loses its
# only picture. This writes the settled frame as an inline SVG that sits behind
# the canvas and is hidden by CSS the instant main.js reports a painted frame.
#
# It draws the same moment as the social card above, from the same projection
# and the same orbit search, so the two pictures agree.
#
# Inline, not an <img src>. An external SVG document cannot read the page's
# theme custom properties, so it could not follow the masthead's theme toggle;
# every stroke here is coloured by a CSS rule in style.css instead.

SVG_BOX = 200.0  # viewBox units, square
SVG_R = 86.0  # globe radius in those units. The orbit sits at 1.1256 R,
# which reaches 96.8 of the 100 available — it fits, just.

FENCE_OPEN = "<!-- globe-still: generated by tools/make-images.py -->"
FENCE_CLOSE = "<!-- /globe-still -->"


def svg_screen(h: float, v: float):
    """Projected (h, v) to viewBox coordinates. v is up, SVG's y is down."""
    return SVG_BOX / 2 + h * SVG_R, SVG_BOX / 2 - v * SVG_R


def path_d(polylines) -> str:
    """SVG path data for a set of runs, one moveto per run.

    Coordinates are rounded to one decimal — 0.1 of a 200-unit box is a
    twentieth of a pixel at the size this is ever drawn — and the pairs after
    the first take SVG's implicit-lineto form, which is what keeps the whole
    graticule inside index.html rather than beside it.
    """
    parts = []
    for run in polylines:
        head, *tail = run
        pairs = " ".join(f"{x:.1f} {y:.1f}" for x, y in tail)
        parts.append(f"M{head[0]:.1f} {head[1]:.1f}L{pairs}")
    return "".join(parts)


def globe_svg_markup() -> str:
    """The still globe, as one <svg> element. Pure — no files are touched."""
    grat = graticule(step_deg=15)
    ring = [orbit(RAAN, steps=180)]
    to = svg_screen

    stx, sty = svg_screen(_sh, _sv)
    ptx, pty = svg_screen(_ph, _pv)

    return (
        f'<svg class="scene-still" viewBox="0 0 {SVG_BOX:.0f} {SVG_BOX:.0f}" '
        'aria-hidden="true" focusable="false">'
        f'<path class="gs-wire gs-back" '
        f'd="{path_d(runs(grat, near=False, to_screen=to))}"/>'
        f'<path class="gs-wire" d="{path_d(runs(grat, near=True, to_screen=to))}"/>'
        f'<circle class="gs-limb" cx="{SVG_BOX / 2:.0f}" '
        f'cy="{SVG_BOX / 2:.0f}" r="{SVG_R:.0f}"/>'
        f'<path class="gs-wire gs-back" '
        f'd="{path_d(runs(ring, near=False, to_screen=to))}"/>'
        f'<path class="gs-orbit" d="{path_d(runs(ring, near=True, to_screen=to))}"/>'
        f'<line class="gs-link" x1="{stx:.1f}" y1="{sty:.1f}" '
        f'x2="{ptx:.1f}" y2="{pty:.1f}"/>'
        # Radii in viewBox units, so the markers scale with the globe. The
        # canvas keeps them at a constant 2.5 px because it animates through a
        # 26x zoom and a marker that grew with it would swamp the close-up;
        # a still has no zoom, and a dot that does not scale is a dot that is
        # too small on a desktop and too large on a phone. 1.1 units is 2.5 px
        # at the desktop radius, which is where the two agree.
        f'<circle class="gs-sat" cx="{ptx:.1f}" cy="{pty:.1f}" r="1.1"/>'
        f'<circle class="gs-station" cx="{stx:.1f}" cy="{sty:.1f}" r="1.3"/>'
        "</svg>"
    )


def write_globe_svg() -> Path:
    """Splice the still globe into index.html between its two fence comments.

    Idempotent: re-running replaces the block rather than adding a second one,
    so `make-images.py` twice in a row leaves the file byte-identical.
    """
    page = SITE / "index.html"
    text = page.read_text(encoding="utf-8")

    start = text.find(FENCE_OPEN)
    end = text.find(FENCE_CLOSE)
    if start < 0 or end < 0 or end < start:
        raise SystemExit(
            f"{page}: expected the fence comments\n  {FENCE_OPEN}\n  {FENCE_CLOSE}\n"
            "The still globe is generated into that block; without it there is "
            "nowhere to write."
        )

    body = f"{FENCE_OPEN}\n{globe_svg_markup()}\n"
    page.write_text(text[:start] + body + text[end:], encoding="utf-8", newline="\n")
    return page


def mark(dr, cx: float, cy: float, r: float, colour) -> None:
    """The limb and one meridian — the same two strokes as favicon.svg, at any
    size. Stroke weights are proportions of the 64-unit viewBox the SVG uses,
    so the raster and the vector stay the same drawing.

    Coordinates are device pixels: supersample before calling, not after.
    """
    dr.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        outline=colour,
        width=max(1, round(r * 4 / 22)),
    )
    # The SVG's "A 10.5 22" arc is the left half of an ellipse about the same
    # centre. In Pillow, 0 degrees is 3 o'clock and angles run clockwise, so
    # 90 to 270 traces bottom to top the long way round — the left half.
    rx = r * 10.5 / 22
    dr.arc(
        [cx - rx, cy - r, cx + rx, cy + r],
        90,
        270,
        fill=colour,
        width=max(1, round(r * 3.5 / 22)),
    )


def render_mark(px: int, ink, ground, frac: float = 0.62):
    """One square mark at `px`, rendered at 2x and downsampled.

    `frac` is the mark's outer diameter as a fraction of the frame. 0.62 is
    chosen so a circular avatar crop — which cuts to the inscribed circle —
    never touches the limb, while the mark still carries weight at 40px.

    `ground` of None gives a transparent PNG.
    """
    require_pillow()
    # 3x where it is affordable — Pillow's arc and ellipse are not antialiased,
    # and a small icon is where that shows. At 2048 a 3x buffer is 150 MB for
    # a gain nothing can see, so the large exports settle for 2x.
    ss = px * (3 if px <= 600 else 2)
    if ground is None:
        img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
        colour = (*ink, 255)
    else:
        img = Image.new("RGB", (ss, ss), ground)
        colour = ink
    mark(ImageDraw.Draw(img), ss / 2, ss / 2, ss * frac / 2, colour)
    return img.resize((px, px), Image.LANCZOS)


def write_icons() -> list[Path]:
    """The favicon fallback, the iOS icon, and the manifest's icons.

    favicon.svg already handles every modern browser and inverts itself with
    prefers-color-scheme; this .ico exists for the ones that ignore it. It is
    the dark-ground variant, which stays legible on a light tab strip as well.
    """
    out = []

    master = render_mark(256, INK, BG, frac=0.60)
    ico = SITE / "favicon.ico"
    master.save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    out.append(ico)

    touch = SITE / "apple-touch-icon.png"
    render_mark(180, INK, BG, frac=0.60).save(touch, optimize=True)
    out.append(touch)

    for size in (192, 512):
        p = SITE / f"icon-{size}.png"
        render_mark(size, INK, BG, frac=0.60).save(p, optimize=True)
        out.append(p)

    # Maskable icons may be cropped to a circle 80% of the frame across, so
    # everything that must survive has to sit inside the middle 80%. At 0.44
    # the mark clears that with room to spare and still fills the badge.
    p = SITE / "icon-512-maskable.png"
    render_mark(512, INK, BG, frac=0.44).save(p, optimize=True)
    out.append(p)

    return out


def write_touch_icon() -> Path:
    """Kept as a name because it is what the rest of the world calls it."""
    return write_icons()[1]


def write_lockup(px_w: int, ink, ground) -> Image.Image:
    """Mark and wordmark on one horizontal line, for banners and headers.

    The lockup is measured and then centred rather than positioned by eye:
    the wordmark is tracked out by 0.38em, so its width is not something you
    can guess from the point size and a guess overruns the canvas.
    """
    require_pillow()
    ss = 2
    px_h = round(px_w * 0.25)
    w, h = px_w * ss, px_h * ss
    img = Image.new("RGB", (w, h), ground)
    dr = ImageDraw.Draw(img)

    r = h * 0.26
    size = round(r * 0.80)
    font = ImageFont.truetype(str(FONTS / "IBMPlexSans-Medium.woff2"), size)
    spacing = size * 0.38
    text_w = sum(font.getlength(c) + spacing for c in "MERIDIAN") - spacing
    gap = r * 1.5

    x0 = (w - (2 * r + gap + text_w)) / 2
    mark(dr, x0 + r, h / 2, r, ink)
    tracked(dr, (x0 + 2 * r + gap, h / 2 + size * 0.36), "MERIDIAN", font, ink, spacing)

    return img.resize((px_w, px_h), Image.LANCZOS)


def write_brand() -> list[Path]:
    """site/brand/ — the marketing exports.

    PNG throughout. The mark is two hairline strokes on flat ground, which is
    the worst case for JPEG: no alpha, and visible ringing around the strokes
    at the sizes an avatar is actually displayed at. A PNG of a two-stroke
    vector is small anyway. One JPEG ships for the rare upload form that
    refuses PNG, and it is not the file to reach for otherwise.
    """
    BRAND.mkdir(exist_ok=True)
    out = []

    variants = (
        ("light", INK, None),  # transparent, for dark backgrounds
        ("dark", INK_DARK, None),  # transparent, for light backgrounds
        ("onblack", INK, BG),  # the profile picture
        ("onpaper", INK_DARK, PAPER),
    )

    for name, ink, ground in variants:
        master = render_mark(2048, ink, ground)
        for size in (2048, 1024, 512):
            img = master if size == 2048 else master.resize((size, size), Image.LANCZOS)
            p = BRAND / f"meridian-mark-{name}-{size}.png"
            img.save(p, optimize=True)
            out.append(p)

    jpg = BRAND / "meridian-mark-onblack-2048.jpg"
    render_mark(2048, INK, BG).save(jpg, quality=92, optimize=True, subsampling=0)
    out.append(jpg)

    for name, ink, ground in (("onblack", INK, BG), ("onpaper", INK_DARK, PAPER)):
        p = BRAND / f"meridian-lockup-{name}-2048.png"
        write_lockup(2048, ink, ground).save(p, optimize=True)
        out.append(p)

    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--what", choices=["all", "og", "icons", "brand", "globe"], default="all"
    )
    what = ap.parse_args().what

    outputs: list[Path] = []
    if what in ("all", "globe"):
        outputs.append(write_globe_svg())
    if what in ("all", "og"):
        outputs.append(write_png())
        print(
            f"orbit raan {math.degrees(RAAN):6.1f} deg   elevation "
            f"{math.degrees(PEAK_EL):5.1f} deg   link is "
            f"{'above' if PEAK_EL > 5 * DEG else 'below'} the horizon"
        )
    if what in ("all", "icons"):
        outputs += write_icons()
    if what in ("all", "brand"):
        outputs += write_brand()

    for p in outputs:
        print(f"{p.relative_to(SITE.parent)}  {p.stat().st_size / 1024:.1f} KB")
