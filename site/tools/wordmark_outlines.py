"""Typesetting for generated SVG: text converted to outlines, from a shipped font.

An SVG that will be viewed as an image — a README banner on GitHub, a social
card — cannot load a font. External requests are blocked in that context, so
`font-family="IBM Plex Sans"` renders in IBM Plex on the three machines that
have it installed and in something else everywhere else, at a different width,
which moves everything that was positioned against it.

So the letters ship as outlines. This module reads one of the .woff2 files in
site/fonts, draws the glyphs a string needs, and returns their combined path
data — after which the text is geometry and renders identically everywhere.

It is pure: it reads a font file and returns a string. It writes nothing, and
knows nothing about any particular drawing.

Coordinates come back in font units with y pointing **up**, the convention
fonts are drawn in and the opposite of SVG's. A caller places the result with
`transform="translate(x, y) scale(s, -s)"`, where s is the desired point size
divided by `units_per_em`.

Needs fontTools and brotli, and only for the outputs that typeset something —
the same optional-dependency arrangement make-images.py has with Pillow:
    pip install fonttools brotli
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Optional, and deliberately so — see the module docstring. Every other output
# in site/tools generates without these installed.
try:
    from fontTools.misc.transform import Offset
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
except ModuleNotFoundError:  # pragma: no cover - exercised by running without it
    TTFont = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class Wordmark:
    """One typeset string, as outlines in font units with y up.

    Attributes:
        path_data: SVG path data for every glyph, in one `d` attribute.
        advance_width_font_units: Total width, tracking included, with no
            trailing tracking after the last letter. A caller centring the
            text needs this: it cannot be guessed from the point size, and a
            guess overruns the frame.
        cap_height_font_units: Height of a capital letter above the baseline,
            for optical centring against a mark or a rule.
        units_per_em: The font's design grid, almost always 1000 or 2048.
    """

    path_data: str
    advance_width_font_units: float
    cap_height_font_units: float
    units_per_em: int


def require_fonttools() -> None:
    """Guard every entry point that typesets. Nothing else in site/tools does."""
    if TTFont is None:
        raise SystemExit(
            "fontTools is not installed. It is needed only to convert text to\n"
            "outlines for generated SVG:\n"
            "    pip install fonttools brotli\n"
            "brotli is what lets it read the shipped .woff2 files."
        )


def _cap_height(font: TTFont) -> float:
    """Capital height from OS/2, falling back to the ascender if absent."""
    os2 = font["OS/2"]
    declared = getattr(os2, "sCapHeight", 0)
    return float(declared) if declared else float(font["hhea"].ascender)


def typeset(font_path: Path, text: str, tracking_em: float) -> Wordmark:
    """Draw a string's glyphs as one path, letter-spaced by `tracking_em`.

    Args:
        font_path: A font file. .woff2 works directly, so the copies already
            in site/fonts are the ones used and there is no second copy of a
            typeface anywhere in the repository.
        text: The string to set. Every character must exist in the font — the
            shipped files are latin-1 subsets, which covers the Latin
            alphabet, digits and common punctuation and nothing else.
        tracking_em: Extra space after each letter, in ems. The lockup's
            0.38 em is measured, not guessed: see site/brand/README.md.

    Returns:
        The outlines and the measurements needed to place them.

    Raises:
        SystemExit: if a character is missing from the font, which would
            otherwise silently typeset as a gap.
    """
    require_fonttools()
    font = TTFont(str(font_path))
    glyph_set = font.getGlyphSet()
    character_map = font.getBestCmap()
    units_per_em = font["head"].unitsPerEm
    tracking_font_units = tracking_em * units_per_em

    commands: list[str] = []
    pen_x = 0.0
    for character in text:
        if ord(character) not in character_map:
            raise SystemExit(
                f"{font_path.name} has no glyph for {character!r}. The shipped "
                "fonts are latin-1 subsets; anything outside that must be "
                "drawn, not typeset."
            )
        glyph_name = character_map[ord(character)]
        commands.append(_glyph_path_data(glyph_set, glyph_name, pen_x))
        pen_x += glyph_set[glyph_name].width + tracking_font_units

    return Wordmark(
        path_data="".join(commands),
        # The last letter is followed by no tracking: the string ends at its
        # own right edge, not a letter-space beyond it.
        advance_width_font_units=pen_x - tracking_font_units,
        cap_height_font_units=_cap_height(font),
        units_per_em=units_per_em,
    )


def _glyph_path_data(glyph_set: object, glyph_name: str, pen_x: float) -> str:
    """One glyph's outline, shifted to its place along the baseline."""
    # Rounded to whole font units. At 1000 units per em and the sizes a banner
    # sets type at, one unit is well under a twentieth of a pixel, and keeping
    # the coordinates integral roughly halves the path data.
    path_pen = SVGPathPen(glyph_set, ntos=lambda value: f"{value:.0f}")
    glyph_set[glyph_name].draw(TransformPen(path_pen, Offset(pen_x, 0)))
    return "".join(path_pen.getCommands())
