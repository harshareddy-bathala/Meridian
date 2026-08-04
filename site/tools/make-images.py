"""Generate the site's raster images.

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

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SITE = Path(__file__).resolve().parent.parent
FONTS = SITE / "fonts"

W, H = 1200, 630
SS = 3  # supersampling; Pillow's draw primitives are not antialiased

BG = (8, 9, 13)
INK = (232, 230, 225)
MUTED = (110, 116, 128)
RULE = (30, 34, 41)
SIGNAL = (107, 184, 138)

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


def runs(lines, *, near: bool):
    """Split polylines into contiguous runs on one side of the limb."""
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
            run.append(screen(h, v))
        if len(run) > 1:
            out.append(run)
    return out


def graticule():
    lines = []
    for lon in range(-180, 180, 15):
        lines.append([ll(lat * DEG, lon * DEG) for lat in range(-90, 91, 3)])
    for lat in range(-75, 76, 15):
        lines.append([ll(lat * DEG, lon * DEG) for lon in range(-180, 181, 3)])
    return lines


def sat_at(raan: float, u: float):
    ci, si = math.cos(INCL), math.sin(INCL)
    co, so = math.cos(raan), math.sin(raan)
    cu, su = math.cos(u), math.sin(u)
    return (R_SAT * (co * cu - so * su * ci), R_SAT * (so * cu + co * su * ci), R_SAT * su * si)


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

COPY_LINES = ["Predictive scheduling", "and reliability for satellite", "ground stations."]
SUBLINE = "A pass lasts minutes and never repeats."
META_LEFT = "MERIDIAN.ORG.IN"
META_RIGHT = "LAUNCHING 2026 · APACHE-2.0"


# -------------------------------------------------------------------- PNG --


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
    img = Image.new("RGB", (W * SS, H * SS), BG)
    dr = ImageDraw.Draw(img)
    s = lambda v: v * SS  # noqa: E731
    poly = lambda run: [(x * SS, y * SS) for x, y in run]  # noqa: E731

    for run in runs(graticule(), near=False):
        dr.line(poly(run), fill=blend(RULE, 0.35), width=SS)
    for run in runs(graticule(), near=True):
        dr.line(poly(run), fill=blend(RULE, 0.95), width=SS)

    dr.ellipse([s(CX - R), s(CY - R), s(CX + R), s(CY + R)], outline=blend(MUTED, 0.55), width=SS)

    for run in runs([orbit(RAAN)], near=False):
        dr.line(poly(run), fill=blend(RULE, 0.45), width=SS)
    for run in runs([orbit(RAAN)], near=True):
        dr.line(poly(run), fill=blend(MUTED, 0.6), width=SS)

    dr.line([(s(STX), s(STY)), (s(PTX), s(PTY))], fill=blend(SIGNAL, 0.9), width=SS)
    dr.ellipse([s(PTX - 4), s(PTY - 4), s(PTX + 4), s(PTY + 4)], fill=SIGNAL)
    dr.ellipse([s(STX - 4), s(STY - 4), s(STX + 4), s(STY + 4)], outline=INK, width=round(1.5 * SS))

    # the mark: limb plus one meridian, same construction as favicon.svg
    mx, my, mr = 80 + 32 * 0.62, 78 + 32 * 0.62, 22 * 0.62
    dr.ellipse(
        [s(mx - mr), s(my - mr), s(mx + mr), s(my + mr)], outline=INK, width=round(4 * 0.62 * SS)
    )
    arx, ary = 10.5 * 0.62, 22 * 0.62
    dr.arc(
        [s(mx - arx), s(my - ary), s(mx + arx), s(my + ary)],
        90,
        270,
        fill=INK,
        width=round(3.5 * 0.62 * SS),
    )

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
    tracked(dr, (s(W - 80), s(H - 56)), META_RIGHT, mono(15), MUTED, 2.4 * SS, anchor="rs")

    out = SITE / "og-image.png"
    # Kept as full RGB. A 256-colour palette halves the file but median-cut
    # spends the whole palette on the near-black gradient and quantises the
    # green link line away to grey — losing the one piece of colour the card
    # exists to show. The page never loads this file; only crawlers fetch it.
    img.resize((W, H), Image.LANCZOS).save(out, optimize=True)
    return out


def write_touch_icon(size: int = 180) -> Path:
    """iOS does not accept an SVG favicon, so the mark is rasterised once."""
    img = Image.new("RGB", (size * SS, size * SS), BG)
    dr = ImageDraw.Draw(img)
    c, r = size * SS / 2, size * SS * 0.30
    dr.ellipse([c - r, c - r, c + r, c + r], outline=INK, width=round(r * 4 / 22))
    rx = r * 10.5 / 22
    dr.arc([c - rx, c - r, c + rx, c + r], 90, 270, fill=INK, width=round(r * 3.5 / 22))

    out = SITE / "apple-touch-icon.png"
    img.resize((size, size), Image.LANCZOS).save(out, optimize=True)
    return out


if __name__ == "__main__":
    outputs = (write_png(), write_touch_icon())
    print(
        f"orbit raan {math.degrees(RAAN):6.1f} deg   elevation "
        f"{math.degrees(PEAK_EL):5.1f} deg   link is "
        f"{'above' if PEAK_EL > 5 * DEG else 'below'} the horizon"
    )
    for p in outputs:
        print(f"{p.relative_to(SITE.parent)}  {p.stat().st_size / 1024:.1f} KB")
