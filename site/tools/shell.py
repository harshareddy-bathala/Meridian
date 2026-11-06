"""Keep the parts every page shares identical: masthead, footer and (i) notes.

The site has no build step (D-036) and each page is a hand-written file, so the
masthead and the footer used to be copied into every page by hand. With five
pages that held; with a dozen, one nav link edited in eleven places out of
twelve is a matter of time. This tool owns those regions, the way
make-images.py owns the still globe in index.html: it rewrites what sits
between two fence comments, and the output is committed, so what is deployed is
still exactly what is in the repository (D-042).

Regions
-------
    <!-- shell:masthead --> ... <!-- /shell:masthead -->   the masthead and nav
    <!-- shell:footer -->   ... <!-- /shell:footer -->     the wide footer
    <!-- shell:terms -->    ... <!-- /shell:terms -->      one popover per (i)
    <!-- shell:glossary --> ... <!-- /shell:glossary -->   /terms/ only
    <!-- shell:give -->     ... <!-- /shell:give -->       /support/, from support.json

The (i) notes
-------------
A note is written in a page as

    <button class="info" popovertarget="term-pass"></button>

and this tool fills the button in and writes the popover it opens into the
page's `shell:terms` region, from `site/terms.json`. The popover is the native
HTML `popover` attribute: no script, so nothing the CSP has to allow, and the
browser handles focus, Escape and dismissal. A term the page names that the
list does not define fails the run, as does a term the list defines twice.

Run:  python site/tools/shell.py           rewrite in place
      python site/tools/shell.py --check   exit 1 if anything is stale
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

from shell_parts import (
    NAV,
    UPI_QR,
    footer,
    give,
    masthead,
    nav_current,
    support_problems,
)

SITE = Path(__file__).resolve().parent.parent

# Every page that carries a shell region. Listed, not globbed, for the reason
# stamp_assets.py gives: a page added without being listed fails --check.
PAGES = [
    "index.html",
    "404.html",
    "how-it-works/index.html",
    "data/index.html",
    "areas/index.html",
    "stations/index.html",
    "support/index.html",
    "developers/index.html",
    "terms/index.html",
    "about/index.html",
    "architecture/index.html",
    "docs/index.html",
    "protocol/index.html",
]

INFO = re.compile(
    r'<button class="info"[^>]*popovertarget="term-([a-z0-9-]+)"[^>]*>.*?</button>',
    re.S,
)


def load_terms() -> dict[str, dict[str, str]]:
    """`site/terms.json`, checked: every entry has a name and a plain line."""
    data = json.loads((SITE / "terms.json").read_text(encoding="utf-8"))
    terms = data["terms"]
    for key, entry in terms.items():
        if not re.fullmatch(r"[a-z0-9-]+", key):
            raise SystemExit(f"terms.json: {key!r} is not a lowercase slug")
        if not entry.get("name") or not entry.get("plain"):
            raise SystemExit(f"terms.json: {key} needs a name and a plain definition")
    return terms


def info_button(key: str, terms: dict[str, dict[str, str]]) -> str:
    """The (i) button for one term, in its one canonical form."""
    if key not in terms:
        raise KeyError(key)
    label = html.escape(f"What “{terms[key]['name']}” means", quote=True)
    return (
        f'<button class="info" type="button" popovertarget="term-{key}" '
        f'aria-label="{label}">i</button>'
    )


def popover(key: str, terms: dict[str, dict[str, str]]) -> str:
    """The note one (i) opens."""
    entry = terms[key]
    return (
        f'    <div class="term" id="term-{key}" popover>\n'
        f'      <p class="term-name">{html.escape(entry["name"])}</p>\n'
        f'      <p class="term-plain">{html.escape(entry["plain"])}</p>\n'
        f'      <p class="term-foot"><a href="/terms/#{key}">All terms</a></p>\n'
        f"    </div>"
    )


def glossary(terms: dict[str, dict[str, str]]) -> str:
    """The /terms/ page's list, in alphabetical order of the name shown."""
    rows = []
    for key, entry in sorted(terms.items(), key=lambda kv: kv[1]["name"].lower()):
        rows.append(
            f'      <div class="gloss-row" id="{key}">\n'
            f"        <dt>{html.escape(entry['name'])}</dt>\n"
            f"        <dd>{html.escape(entry['plain'])}</dd>\n"
            f"      </div>"
        )
    return '    <dl class="gloss">\n' + "\n".join(rows) + "\n    </dl>"


def fill(text: str, name: str, body: str) -> str:
    """Replace the region `name`, if the page has one, keeping the fences."""
    pattern = re.compile(
        rf"(<!-- shell:{name} -->).*?([ \t]*<!-- /shell:{name} -->)", re.S
    )
    inner = "\n" + body + "\n" if body else "\n"
    return pattern.sub(lambda m: m.group(1) + inner + m.group(2), text)


def render(page: str, text: str, terms: dict[str, dict[str, str]], giving: str) -> str:
    """One page with every shell region it carries brought up to date."""
    # "page" on the page itself; "true" on the section a page sits under, which
    # is what aria-current means by a current item that is not this document.
    own = "/" + page.removesuffix("index.html")
    current = nav_current(page)

    def mark(href: str) -> str:
        if href == own:
            return ' aria-current="page"'
        return ' aria-current="true"' if href == current else ""

    nav = "\n".join(
        f'      <a href="{href}"{mark(href)}>{label}</a>' for href, label in NAV
    )
    masthead_class = "masthead reveal" if page == "index.html" else "masthead"
    text = fill(text, "masthead", masthead(masthead_class, nav))
    text = fill(text, "footer", footer())

    try:
        text = INFO.sub(lambda m: info_button(m.group(1), terms), text)
    except KeyError as missing:
        raise SystemExit(
            f"{page}: (i) names {missing}, which terms.json does not define"
        ) from None

    used = list(dict.fromkeys(INFO.findall(text)))
    if used and "<!-- shell:terms -->" not in text:
        raise SystemExit(f"{page}: has (i) notes and no shell:terms region for them")
    text = fill(text, "terms", "\n".join(popover(k, terms) for k in used))
    text = fill(text, "glossary", glossary(terms))
    return fill(text, "give", giving)


def load_giving() -> str:
    """The support page's ways to give, refusing anything that looks unreal."""
    data = json.loads((SITE / "support.json").read_text(encoding="utf-8"))
    problems = support_problems(data)
    if problems:
        raise SystemExit("\n".join(problems))
    return give(data, (SITE / UPI_QR).exists())


def main() -> int:
    """Rewrite every page, or with --check report which would change."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="change nothing; exit 1 if stale"
    )
    args = parser.parse_args()

    terms = load_terms()
    giving = load_giving()
    stale = []
    for page in PAGES:
        path = SITE / page
        before = path.read_text(encoding="utf-8")
        after = render(page, before, terms, giving)
        if after == before:
            continue
        stale.append(page)
        if not args.check:
            path.write_text(after, encoding="utf-8")

    if args.check and stale:
        for page in stale:
            print(
                f"{page}: shared regions are stale; run site/tools/shell.py",
                file=sys.stderr,
            )
        return 1
    verb = "would change" if args.check else "rewrote"
    print(f"{len(PAGES)} pages, {verb} {len(stale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
