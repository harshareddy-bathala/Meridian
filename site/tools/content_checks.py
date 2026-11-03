"""The visitor pages' rules, checked (D-340, D-343). Run by verify_site.py.

amounts      no page states a sum of money. Funding is described by what it
             keeps running and what it builds, never by a figure (D-343).
ways to give support.json holds a real UPI ID and a real Sponsors URL, or
             nothing. A placeholder that ships sends someone's money nowhere.
terms        every (i) names a term site/terms.json defines
breakpoints  style.css tests only the four shared widths (D-341)
figures      every picture says what it is: an illustration, an example or
             simulated. None may pass for a measurement (CLAUDE.md rule 5).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from shell_parts import support_problems

# A currency sign or code next to a number, either way round, and the Indian
# number words a figure in rupees is usually written with.
AMOUNT = re.compile(
    r"(?:₹|\bRs\.?|\bINR|\bUSD|US\$|\$|€|\bEUR|£|\bGBP)\s?\d"
    r"|\d[\d,.]*\s?(?:₹|\bINR\b|\bUSD\b|\brupees?\b|\bdollars?\b|\blakhs?\b|\bcrores?\b|\bk\b(?=\s*(?:a|per)\s+(?:month|year)))",
    re.I,
)

# The four widths, as min-width or one sixteenth of a rem under, as max-width.
BREAKPOINTS = {40, 64, 80, 120}
MEDIA_WIDTH = re.compile(r"\((min|max)-width:\s*([\d.]+)(rem|px|em)\)")

FIGURE = re.compile(r'<figure class="fig[^"]*">(.*?)</figure>', re.S)
CAPTION = re.compile(r"<figcaption>(.*?)</figcaption>", re.S)
HONEST_CAPTION = re.compile(r"^(Illustration|Example|Simulated)\b")


def visible_text(page_html: str) -> str:
    """A page's words, without markup, JSON-LD or comments."""
    text = re.sub(r"<script\b.*?</script>|<!--.*?-->", " ", page_html, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def check_amounts(site: Path, pages: list[str]) -> list[str]:
    """No page, and neither data file the pages are built from, states a sum."""
    sources = {
        name: visible_text((site / name).read_text(encoding="utf-8")) for name in pages
    }
    for data in ("terms.json", "support.json"):
        sources[data] = (site / data).read_text(encoding="utf-8")
    return [
        f"{name}: states an amount of money ({match.group(0).strip()!r}); "
        "describe what support pays for, never a figure (D-343)"
        for name, text in sources.items()
        for match in AMOUNT.finditer(text)
    ]


def check_ways_to_give(site: Path) -> list[str]:
    """support.json is publishable: real values, or none."""
    data = json.loads((site / "support.json").read_text(encoding="utf-8"))
    return support_problems(data)


def check_terms(site: Path, pages: list[str]) -> list[str]:
    """Every (i) opens a definition the list has."""
    terms = json.loads((site / "terms.json").read_text(encoding="utf-8"))["terms"]
    problems = []
    for name in pages:
        text = (site / name).read_text(encoding="utf-8")
        for key in re.findall(r'popovertarget="term-([a-z0-9-]+)"', text):
            if key not in terms:
                problems.append(f"{name}: (i) names {key!r}, not in terms.json")
        for key in re.findall(r'id="term-([a-z0-9-]+)" popover', text):
            if key not in terms:
                problems.append(f"{name}: popover term-{key} is not in terms.json")
    return problems


def check_breakpoints(css: str) -> list[str]:
    """Media queries test only 40, 64, 80 and 120rem (D-341)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    problems = []
    for media in re.findall(r"@media[^{]*", css):
        for kind, value, unit in MEDIA_WIDTH.findall(media):
            width = float(value) + (0.0625 if kind == "max" else 0)
            if unit != "rem" or width not in BREAKPOINTS:
                problems.append(
                    f"style.css: {media.strip()} tests a width outside the four "
                    "(min-width 40/64/80/120rem, or max-width one sixteenth under)"
                )
    return problems


def check_figures(site: Path, pages: list[str]) -> list[str]:
    """No picture on the site can be read as a measurement."""
    problems = []
    for name in pages:
        for body in FIGURE.findall((site / name).read_text(encoding="utf-8")):
            caption = CAPTION.search(body)
            words = visible_text(caption.group(1)).strip() if caption else ""
            if not HONEST_CAPTION.search(words):
                problems.append(
                    f"{name}: a figure captioned {words!r} does not say it is an "
                    "illustration, an example or simulated"
                )
    return problems


def check_content(site: Path, pages: list[str], css: str) -> list[str]:
    """Every check in this file."""
    return (
        check_amounts(site, pages)
        + check_ways_to_give(site)
        + check_terms(site, pages)
        + check_breakpoints(css)
        + check_figures(site, pages)
    )
