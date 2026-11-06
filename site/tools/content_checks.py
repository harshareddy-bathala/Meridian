"""The visitor pages' rules, checked (D-340, D-343). Run by verify_site.py.

amounts      no page states a sum of money. Funding is described by what it
             keeps running and what it builds, never by a figure (D-343).
ways to give support.json holds a real UPI ID and a real Sponsors URL, or
             nothing. A placeholder that ships sends someone's money nowhere.
terms        every (i) names a term site/terms.json defines
breakpoints  style.css tests only the four shared widths (D-341)
figures      every picture says what it is: an illustration, an example or
             simulated. None may pass for a measurement (CLAUDE.md rule 5).
wording      no page talks about how the site itself is written, and none calls
             Meridian a non-profit. A visitor reads what Meridian does, not
             notes on the prose; and the project's status is not one to claim.
shape        every page but the front and the 404 has one title, a standfirst
             of one sentence, and ends on exactly one next step (D-347).
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

# Sentences about the site's own writing, and a status Meridian does not hold.
WORDING = re.compile(
    r"plain words|plain language|\(i\) beside|small \(i\)|every page starts"
    r"|each technical term|non-?profit|not-for-profit",
    re.I,
)


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


def check_wording(site: Path, pages: list[str]) -> list[str]:
    """No page describes its own prose, or claims a status Meridian lacks.

    Read from the whole page, JSON-LD and the head's metadata included, since a
    title or a structured-data description is what a search result shows.
    """
    found = []
    for name in pages:
        page = (site / name).read_text(encoding="utf-8")
        text = html.unescape(re.sub(r"<!--.*?-->", " ", page, flags=re.S))
        found += [
            f"{name}: says {match.group(0)!r}; say what Meridian does instead"
            for match in WORDING.finditer(text)
        ]
    return found


STANDFIRST = re.compile(r'<p class="standfirst">(.*?)</p>', re.S)
# A full stop, question or exclamation mark that ends a sentence: followed by
# space and a capital, or by the end. "MSP 0.1" and "e.g." are neither.
SENTENCE_END = re.compile(r"[.?!](?=\s+[A-Z“\"]|\s*$)")
NEXT_OPEN = '<nav class="next" aria-label="Next step">'
NEXT = re.compile(re.escape(NEXT_OPEN) + r'\s*<a class="next-link" href="[^"]+">')
CONTENT = re.compile(r'<section\b|<div class="prose')
WHOLE_SCREEN = {"index.html", "404.html"}


def check_shape(site: Path, pages: list[str]) -> list[str]:
    """A page title, one sentence, the content, one next step (D-347)."""
    problems = []
    for name in pages:
        if name in WHOLE_SCREEN:
            continue
        page = (site / name).read_text(encoding="utf-8")
        main = page[page.find("<main") : page.find("</main>")]
        titles = main.count("<h1")
        if titles != 1:
            problems.append(f"{name}: has {titles} titles, not one")
        lead = STANDFIRST.findall(main)
        words = visible_text(lead[0]).strip() if len(lead) == 1 else ""
        sentences = len(SENTENCE_END.findall(words))
        if sentences != 1:
            problems.append(f"{name}: the standfirst is {sentences} sentences, not one")
        steps = len(NEXT.findall(main))
        if steps != 1:
            problems.append(f"{name}: ends on {steps} next steps, not one")
        elif CONTENT.search(main[main.rfind(NEXT_OPEN) :]):
            problems.append(f"{name}: content follows the next step")
    return problems


def check_content(site: Path, pages: list[str], css: str) -> list[str]:
    """Every check in this file."""
    return (
        check_amounts(site, pages)
        + check_ways_to_give(site)
        + check_terms(site, pages)
        + check_breakpoints(css)
        + check_figures(site, pages)
        + check_wording(site, pages)
        + check_shape(site, pages)
    )
