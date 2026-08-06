"""Stamp every CSS and JS reference in the site with its file's content hash.

Why this exists
---------------
The site's assets live at fixed URLs — /style.css, /main.js — and a deploy
replaces the bytes behind them without changing the name. Any cache holding one
of those files then serves it alongside HTML from a different build, and the
visitor renders a hybrid of two versions of the site that has never been tested
and cannot be reproduced.

That is not hypothetical. It shipped: a phone held style.css from the previous
build for four hours and rendered the current HTML against it, so every rule
added in that deploy was missing and every link on the page fell back to the
user-agent blue. The cache was behaving correctly. The URLs were the bug.

`?v=<hash>` makes it impossible by construction. A file's URL changes when its
bytes change, so a cache can only ever return the bytes that URL named.

Ordering
--------
main.js and rail.js both `import './orbit.js'`, so orbit.js has to be stamped
into them before their own hashes can be taken — otherwise the stamping changes
the files whose hashes were just computed. The passes are therefore:

    1. hash orbit.js, and rewrite the import specifier in main.js and rail.js
    2. hash the now-rewritten main.js and rail.js, and theme.js and style.css
    3. rewrite the HTML, including index.html's modulepreload for orbit.js —
       which must carry the *same* stamp as the import, or the preload and the
       import are two different URLs and the browser fetches the file twice

Run:  python site/tools/stamp_assets.py           rewrite in place
      python site/tools/stamp_assets.py --check   exit 1 if anything is stale
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent

# Eight hex characters of SHA-256. This is a cache key, not a signature: it has
# to change when the file changes, and nothing else is asked of it.
STAMP_CHARS = 8

# Every page that references a stamped asset. Listed rather than globbed so
# that a page added without being added here fails the CI check below on its
# first commit, instead of silently going unstamped.
PAGES = [
    "index.html",
    "404.html",
    "about/index.html",
    "architecture/index.html",
    "docs/index.html",
    "protocol/index.html",
]

# Referenced from HTML by absolute path.
HTML_ASSETS = ["style.css", "theme.js", "main.js", "rail.js", "orbit.js"]

# Referenced from JavaScript by relative specifier, which is why orbit.js is
# not in the list above alone.
MODULE_IMPORTERS = ["main.js", "rail.js"]


def stamp_of(text: str) -> str:
    """The cache key for a file's contents."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:STAMP_CHARS]


def restamp(text: str, url: str, stamp: str) -> str:
    """Replace every *quoted* reference to `url` with one carrying `stamp`.

    The quotes are the whole point. Both file types write their references
    inside them — `href="/style.css"`, `import './orbit.js'` — and requiring
    them is what stops this rewriting the same path where it appears in prose.
    Without that it edits the comments: rail.js opens with a paragraph about
    what `./orbit.js` is for, and a looser pattern stamps that sentence.

    An existing `?v=` is matched and replaced rather than appended to, which is
    what makes running this twice a no-op instead of a double stamp.
    """
    pattern = re.compile(r"(['\"])" + re.escape(url) + r"(?:\?v=[0-9a-f]+)?\1")
    return pattern.sub(lambda m: f"{m.group(1)}{url}?v={stamp}{m.group(1)}", text)


def stamped_sources() -> dict[str, str]:
    """Every asset's final text, keyed by file name. Pure — nothing is written.

    Returns the *stamped* text of the module importers, not what is on disk, so
    the caller writes and hashes the same bytes.
    """
    sources = {name: (SITE / name).read_text(encoding="utf-8") for name in HTML_ASSETS}

    orbit_stamp = stamp_of(sources["orbit.js"])
    for name in MODULE_IMPORTERS:
        sources[name] = restamp(sources[name], "./orbit.js", orbit_stamp)

    return sources


def stamped_pages(sources: dict[str, str]) -> dict[str, str]:
    """Every page's final text, keyed by path. Pure — nothing is written."""
    stamps = {name: stamp_of(text) for name, text in sources.items()}

    pages = {}
    for page in PAGES:
        text = (SITE / page).read_text(encoding="utf-8")
        for name, stamp in stamps.items():
            text = restamp(text, f"/{name}", stamp)
        pages[page] = text
    return pages


def write_if_changed(path: Path, text: str) -> bool:
    """Write only on a real change, so mtimes stay meaningful. True if written."""
    if path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    """Stamp, or report what stamping would change."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if any file would change",
    )
    check = ap.parse_args().check

    sources = stamped_sources()
    pages = stamped_pages(sources)
    everything = {**sources, **pages}

    stale = [
        name
        for name, text in everything.items()
        if (SITE / name).read_text(encoding="utf-8") != text
    ]

    if check:
        for name in stale:
            print(f"site/{name}: asset stamps are stale", file=sys.stderr)
        if stale:
            print("\nRun: python site/tools/stamp_assets.py", file=sys.stderr)
        return 1 if stale else 0

    for name, text in everything.items():
        if write_if_changed(SITE / name, text):
            print(f"site/{name}")
    if not stale:
        print("already stamped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
