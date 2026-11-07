"""Write the dashboard's shared design tokens from the site's stylesheet.

The site and the dashboard are two surfaces built apart (D-036): the site is
hand-written and served by Cloudflare Pages, the dashboard is built by Vite and
served by the platform. A visitor moves between them in one click, so the
spacing, the type, the radii, the grounds and the ink have to be one set of
values (D-341). They were copied into `dashboard/src/app.css` by hand and held
equal by a test, and a value the test did not list drifted: the dashboard's
secondary text was the site's `--muted` in light and its `--ink-dim` in dark.

This tool makes the site's stylesheet the one source. It reads the tokens below
from `site/style.css` and writes `dashboard/src/tokens.css`, which the
dashboard imports before its own stylesheet. The output is committed, as
`shell.py`'s is, so the dashboard builds from its own directory alone and the
image needs nothing from `site/` (D-360).

Themes: the site sets dark by its toggle only (D-344), and the dashboard has no
toggle, so it follows the system. The site's `:root[data-theme="dark"]` values
are written under `prefers-color-scheme: dark`.

Run:  python site/tools/dashboard_tokens.py           rewrite in place
      python site/tools/dashboard_tokens.py --check   exit 1 if it is stale
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE_CSS = ROOT / "site" / "style.css"
TOKENS_CSS = ROOT / "dashboard" / "src" / "tokens.css"

# The scale the two surfaces share, set once on the site's bare `:root`.
SCALE = [
    *(f"--space-{n}" for n in range(1, 9)),
    "--step--1",
    "--step-0",
    "--step-1",
    "--step-2",
    "--step-3",
    "--radius-1",
    "--radius-2",
    "--radius-pill",
    "--target",
    "--sans",
    "--mono",
]

# The colours they share, set per theme. The site's signal, alert and trace
# mean above horizon, below horizon and predicted (site/brand/README.md), so
# the dashboard does not take them: liveness and warnings have their own.
COLOURS = [
    "--bg",
    "--surface",
    "--rule",
    "--ink",
    "--ink-dim",
    "--muted",
    "--simulated",
]

SCALE_SELECTOR = ":root"
LIGHT_SELECTOR = ':root, :root[data-theme="light"]'
DARK_SELECTOR = ':root[data-theme="dark"]'

HEADER = """\
/* Generated from site/style.css by site/tools/dashboard_tokens.py. Do not
   edit: change the site's stylesheet and run the tool, and CI fails until this
   file matches it (D-341, D-360). The dashboard's own colours, for liveness,
   snapshots and warnings, are in app.css. */
"""


def top_level_rules(css: str) -> dict[str, str]:
    """Each top-level rule's body, keyed by its selector with spaces collapsed.

    Rules inside an at-rule are skipped, so a `:root` inside a media query is
    never mistaken for the bare one.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules: dict[str, str] = {}
    depth = 0
    start = 0
    head = ""
    for index, char in enumerate(css):
        if char == "{":
            if depth == 0:
                head = css[start:index]
                start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                selector = " ".join(head.split())
                if not selector.startswith("@"):
                    rules[selector] = css[start:index]
                start = index + 1
    return rules


def declarations(body: str) -> dict[str, str]:
    """The custom properties a rule body sets, with spaces collapsed."""
    return {
        name: " ".join(value.split())
        for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)
    }


def pick(
    rules: dict[str, str], selector: str, names: list[str]
) -> list[tuple[str, str]]:
    """The named tokens from one rule, in the order named, or a clear failure."""
    if selector not in rules:
        raise SystemExit(f"site/style.css has no top-level rule `{selector}`")
    found = declarations(rules[selector])
    missing = [name for name in names if name not in found]
    if missing:
        raise SystemExit(
            f"site/style.css `{selector}` does not set {', '.join(missing)}"
        )
    return [(name, found[name]) for name in names]


def render(site_css: str) -> str:
    """The dashboard's token file for one version of the site's stylesheet."""
    rules = top_level_rules(site_css)
    root = pick(rules, SCALE_SELECTOR, SCALE) + pick(rules, LIGHT_SELECTOR, COLOURS)
    dark = pick(rules, DARK_SELECTOR, COLOURS)
    lines = [HEADER, ":root {"]
    lines += [f"  {name}: {value};" for name, value in root]
    lines += ["}", "", "@media (prefers-color-scheme: dark) {", "  :root {"]
    lines += [f"    {name}: {value};" for name, value in dark]
    lines += ["  }", "}", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if the file is stale"
    )
    args = parser.parse_args()

    wanted = render(SITE_CSS.read_text(encoding="utf-8"))
    current = TOKENS_CSS.read_text(encoding="utf-8") if TOKENS_CSS.exists() else ""
    if args.check:
        if current != wanted:
            print(
                "dashboard/src/tokens.css is stale:"
                " run python site/tools/dashboard_tokens.py",
                file=sys.stderr,
            )
            return 1
        print("dashboard/src/tokens.css matches site/style.css")
        return 0
    if current != wanted:
        TOKENS_CSS.write_text(wanted, encoding="utf-8")
        print("wrote dashboard/src/tokens.css")
    else:
        print("dashboard/src/tokens.css already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
