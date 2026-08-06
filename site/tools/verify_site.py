"""Check the static site's invariants. Stdlib only; runs in CI.

site/README.md states several rules about this site and, until this file
existed, enforced none of them. Each check below corresponds to a claim made
there, and each one exists because breaking it is silent:

  fragments      a mistyped href="#..." scrolls nowhere and says nothing
  modulepreload  orbit.js must be preloaded on index.html and never on a
                 document page, where it undoes the dynamic import
  sitemap        an added page that nobody listed is a page nobody indexes
  link styling   an <a> in a context with no CSS rule falls back to the
                 user-agent blue. This shipped: the wide footer's licence link
                 was drawn in browser blue on five pages for two commits.
  inline assets  the CSP allows no 'unsafe-inline', so an inline <style>, an
                 inline <script> or a style="" attribute is a blank element in
                 production and a working one on localhost

Run:  python site/tools/verify_site.py
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent

# The pages the sitemap is expected to list, and the one that must not appear.
INDEXED = [
    "index.html",
    "about/index.html",
    "architecture/index.html",
    "docs/index.html",
    "protocol/index.html",
]
NOINDEX = ["404.html"]

# JSON-LD lives in <script type="..."> blocks that are data, never executed, so
# script-src does not apply to them and they are not an inline-script finding.
DATA_SCRIPT_TYPES = {"application/ld+json"}


# Elements that never have a closing tag, so the ancestor stack must not push
# one for them.
VOID_TAGS = {"br", "img", "link", "meta", "hr", "input", "source", "wbr", "col"}

# One element in an anchor's ancestor chain: its tag and its classes.
Element = tuple[str, frozenset[str]]


class Page(HTMLParser):
    """One page, reduced to the facts the checks below need.

    Records each anchor's full ancestor chain — tag *and* classes, in document
    order — not just the set of classes above it. The tags are what make the
    difference between `.index-row a` and `.index-row h2 a`, and collapsing the
    chain to a set of class names cannot tell those apart. That is not
    hypothetical: it is how the first version of this check passed a page whose
    `<p>` links were unstyled, because the `<h2>` links above them were not.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.anchors: list[tuple[str, list[Element]]] = []
        self.modulepreloads: list[str] = []
        self.inline: list[str] = []
        self._chain: list[Element] = []
        self._script_is_data = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        element: Element = (tag, frozenset(a.get("class", "").split()))

        if "id" in a:
            self.ids.add(a["id"])
        if "style" in a:
            self.inline.append(f'style="" on <{tag}>')
        if tag == "style":
            self.inline.append("inline <style> block")
        if tag == "script":
            self._script_is_data = a.get("type", "") in DATA_SCRIPT_TYPES
            if "src" not in a and not self._script_is_data:
                self.inline.append("inline <script> block")
        if tag == "link" and a.get("rel") == "modulepreload":
            self.modulepreloads.append(a.get("href", ""))
        if tag == "a":
            self.anchors.append((a.get("href", ""), [*self._chain, element]))

        if tag not in VOID_TAGS:
            self._chain.append(element)

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID_TAGS and self._chain:
            self._chain.pop()


def read_pages() -> dict[str, Page]:
    """Parse every page once."""
    pages = {}
    for name in INDEXED + NOINDEX:
        page = Page()
        page.feed((SITE / name).read_text(encoding="utf-8"))
        pages[name] = page
    return pages


def _flat_rules(css: str):
    """Every `selector { declarations }` pair, at any nesting depth.

    Neither side may contain a brace, which is what makes this cope with
    @media without knowing anything about it: the prelude `@media (...) {` is
    followed by another `{` before its closing brace, so no match starts there
    and the engine moves on to the rules inside.
    """
    for match in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        yield match.group(1), match.group(2)


def colouring_rules(css: str) -> list[str]:
    """Selectors of every rule that sets `color`. Nothing else styles a link.

    The distinction matters. `.nav a` appears twice in style.css: once with a
    colour, and once inside the reduced-motion query with nothing but
    `transition: none`. Counting the second as proof the link is styled is what
    makes this check pass a stylesheet that has lost the first.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for selector, body in _flat_rules(css):
        if re.search(r"(^|[;\s])color\s*:", body):
            out.extend(part.strip() for part in selector.split(","))
    return out


CLASS_IN_UNIT = re.compile(r"\.([a-z][a-z0-9-]*)")

# Hyphens included: a custom element is spelled `station-map`, and a pattern
# that stopped at the hyphen would read that as the tag `station` and match the
# wrong elements. Nothing on this site is a custom element yet; the cost of
# being right about it now is one character.
TAG_IN_UNIT = re.compile(r"^([a-z][a-z0-9-]*)")

# One compound selector, reduced to what this needs: a tag name or None, and
# the classes it requires. Pseudo-classes, attribute tests and pseudo-elements
# are dropped — they narrow when a rule applies, never which elements it can
# reach, and dropping them can only make this check more permissive.
Unit = tuple[str | None, frozenset[str]]


def parse_unit(text: str) -> Unit:
    """`.foot-col`, `h2`, `a:hover`, `a[aria-current]` to (tag, classes)."""
    head = re.split(r"[:\[]", text, maxsplit=1)[0]
    tag = TAG_IN_UNIT.match(head)
    return (tag.group(1) if tag else None, frozenset(CLASS_IN_UNIT.findall(head)))


def parse_selector(selector: str) -> list[Unit]:
    """A selector to its sequence of compound units, left to right.

    Child, sibling and general-sibling combinators are read as descendant
    combinators. That is deliberately the loose direction: it can only let a
    rule claim to reach an element it would not really reach, which risks
    missing a problem rather than inventing one. style.css uses `>` in three
    places and none of them target an anchor.
    """
    return [parse_unit(part) for part in re.split(r"\s*[>+~]\s*|\s+", selector.strip()) if part]


def unit_matches(unit: Unit, element: Element) -> bool:
    """Does one compound selector match one element?"""
    tag, classes = unit
    element_tag, element_classes = element
    return (tag is None or tag == element_tag) and classes <= element_classes


def selector_matches(units: list[Unit], chain: list[Element]) -> bool:
    """Does a descendant selector match an element, given its ancestor chain?

    `chain` ends with the element itself. The last unit has to match it, and
    the units before it have to match ancestors in order — not necessarily
    adjacent ones, which is what a descendant combinator means.
    """
    if not units or not unit_matches(units[-1], chain[-1]):
        return False

    remaining = list(reversed(units[:-1]))
    for element in reversed(chain[:-1]):
        if remaining and unit_matches(remaining[0], element):
            remaining.pop(0)
    return not remaining


def anchor_selectors(css: str) -> list[list[Unit]]:
    """Every colouring selector that could have an anchor as its subject.

    Only the subject matters. `.prose a` styles the anchor; `.prose p` does
    not, however many anchors sit inside one.

    A subject with no tag name qualifies, because that is how most of this
    stylesheet reaches an anchor: `.cta`, `.skip` and `.brand` are classes on
    the anchor itself. Whether one actually matches is left to
    selector_matches, which has the element in front of it. A subject naming
    some other tag — `.legend dd`, `.index-row p` — cannot reach an anchor and
    is dropped here.
    """
    out = []
    for selector in colouring_rules(css):
        units = parse_selector(selector)
        if units and units[-1][0] in ("a", None):
            out.append(units)
    return out


def check_fragments(pages: dict[str, Page]) -> list[str]:
    """Every href="#..." points at an element on the same page."""
    problems = []
    for name, page in pages.items():
        for href, _chain in page.anchors:
            if not href.startswith("#") or href == "#":
                continue
            if href[1:] not in page.ids:
                problems.append(f'{name}: href="{href}" matches no id on the page')
    return problems


def check_modulepreload(pages: dict[str, Page]) -> list[str]:
    """orbit.js is preloaded on the home page and on no other."""
    problems = []
    for name, page in pages.items():
        preloads_orbit = any("orbit.js" in h for h in page.modulepreloads)
        if name == "index.html" and not preloads_orbit:
            problems.append(
                "index.html: no modulepreload for orbit.js. main.js imports it "
                "statically and needs the hint."
            )
        if name != "index.html" and preloads_orbit:
            problems.append(
                f"{name}: modulepreload for orbit.js. Document pages import it "
                "dynamically above 1180px only; preloading it makes every phone "
                "fetch 12 KB it will not use."
            )
    return problems


def check_sitemap() -> list[str]:
    """The sitemap lists exactly the indexed pages, and no noindex page."""
    text = (SITE / "sitemap.xml").read_text(encoding="utf-8")
    listed = set(re.findall(r"<loc>https://meridian\.org\.in(/[^<]*)</loc>", text))
    expected = {"/" if p == "index.html" else "/" + p.removesuffix("index.html") for p in INDEXED}

    problems = [
        f"sitemap.xml: {url} is listed but is not an indexed page" for url in listed - expected
    ]
    problems += [f"sitemap.xml: {url} exists but is not listed" for url in expected - listed]
    return problems


def describe(chain: list[Element]) -> str:
    """An anchor's ancestor chain, as something readable in an error."""
    return " > ".join(tag + "".join("." + c for c in sorted(classes)) for tag, classes in chain)


def check_link_styling(pages: dict[str, Page], css: str) -> list[str]:
    """Every anchor is reached by some rule in style.css that sets a colour."""
    selectors = anchor_selectors(css)
    problems = []
    for name, page in pages.items():
        for href, chain in page.anchors:
            if any(selector_matches(units, chain) for units in selectors):
                continue
            problems.append(
                f'{name}: <a href="{href}"> is reached by no rule that sets a colour.\n'
                f"    {describe(chain)}\n"
                "    It will render in the user-agent blue."
            )
    return problems


def check_no_inline(pages: dict[str, Page]) -> list[str]:
    """The CSP allows no 'unsafe-inline'; nothing may rely on it."""
    return [
        f"{name}: {what} — blocked by the CSP in production"
        for name, page in pages.items()
        for what in page.inline
    ]


def main() -> int:
    """Run every check; report all failures rather than the first."""
    pages = read_pages()
    css = (SITE / "style.css").read_text(encoding="utf-8")

    problems = (
        check_fragments(pages)
        + check_modulepreload(pages)
        + check_sitemap()
        + check_link_styling(pages, css)
        + check_no_inline(pages)
    )

    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s)", file=sys.stderr)
        return 1

    print(f"{len(pages)} pages checked, all invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
