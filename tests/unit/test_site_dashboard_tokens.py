"""The site and the dashboard share one set of tokens, held equal here (D-341).

The two surfaces are built apart: `site/` is hand-written and served by
Cloudflare Pages, the dashboard is built by Vite and served by the platform. A
visitor moves between them in one click, and a spacing step or a simulated
badge that differs between the two is the seam they would notice. This reads
both stylesheets' custom properties, per theme, and compares the shared ones.

Reference: docs/DECISIONS.md D-036, D-341.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE_CSS = (ROOT / "site" / "style.css").read_text(encoding="utf-8")
DASHBOARD_CSS = (ROOT / "dashboard" / "src" / "app.css").read_text(encoding="utf-8")

SPACING = [f"--space-{n}" for n in range(1, 9)]
COLOURS = ["--bg", "--surface", "--rule", "--ink", "--simulated"]


def block(css: str, opening: str) -> dict[str, str]:
    """The custom properties set in the rule block that opens with `opening`."""
    start = css.index(opening) + len(opening)
    body = css[start : css.index("}", start)]
    return {
        name: value.strip().lower()
        for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)
    }


def test_the_spacing_scale_is_the_same_on_both_surfaces() -> None:
    site = block(SITE_CSS, ":root {")
    dashboard = block(DASHBOARD_CSS, ":root {")
    assert {k: site[k] for k in SPACING} == {k: dashboard[k] for k in SPACING}


def test_the_light_colours_are_the_same_on_both_surfaces() -> None:
    site = block(SITE_CSS, ':root[data-theme="light"] {')
    dashboard = block(DASHBOARD_CSS, ":root {")
    assert {k: site[k] for k in COLOURS} == {k: dashboard[k] for k in COLOURS}


def test_the_dark_colours_are_the_same_on_both_surfaces() -> None:
    dashboard = block(DASHBOARD_CSS, "@media (prefers-color-scheme: dark) {\n  :root {")
    expected = {k: dashboard[k] for k in COLOURS}
    # The site sets dark from its toggle only: it opens light whatever the
    # system prefers (D-344), so it has no dark-by-preference block to compare.
    by_toggle = block(SITE_CSS, ':root[data-theme="dark"] {')
    assert {k: by_toggle[k] for k in COLOURS} == expected


def test_the_site_never_takes_dark_from_the_system() -> None:
    assert "prefers-color-scheme: dark" not in SITE_CSS
