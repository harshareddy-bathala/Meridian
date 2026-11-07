"""The site and the dashboard share one set of tokens, generated from the site.

The two surfaces are built apart: `site/` is hand-written and served by
Cloudflare Pages, the dashboard is built by Vite and served by the platform. A
visitor moves between them in one click, and a spacing step or a simulated
badge that differs between the two is the seam they would notice.

`site/tools/dashboard_tokens.py` writes `dashboard/src/tokens.css` from
`site/style.css`. These tests hold the committed file to the tool's output,
and hold the dashboard's own stylesheet to setting none of those values itself,
because a hand copy beside the generated one is how the two drifted before.

Reference: docs/DECISIONS.md D-036, D-341, D-344, D-360.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SITE_CSS = (ROOT / "site" / "style.css").read_text(encoding="utf-8")
DASHBOARD = ROOT / "dashboard" / "src"
APP_CSS = (DASHBOARD / "app.css").read_text(encoding="utf-8")
TOKENS_CSS = (DASHBOARD / "tokens.css").read_text(encoding="utf-8")


def load_tool() -> ModuleType:
    """The generator, loaded from its path: site/tools is not a package."""
    path = ROOT / "site" / "tools" / "dashboard_tokens.py"
    spec = importlib.util.spec_from_file_location("dashboard_tokens", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = load_tool()


def test_the_committed_tokens_are_the_sites() -> None:
    assert TOOL.render(SITE_CSS) == TOKENS_CSS, (
        "dashboard/src/tokens.css is stale: run python site/tools/dashboard_tokens.py"
    )


def test_a_change_to_the_sites_value_changes_the_dashboards() -> None:
    # The positive control: the generator reads the site's value, not a copy.
    changed = SITE_CSS.replace("--space-4: 1rem;", "--space-4: 1.125rem;", 1)
    assert changed != SITE_CSS
    assert "--space-4: 1.125rem;" in TOOL.render(changed)


def test_a_token_the_site_stops_setting_fails_the_tool() -> None:
    without = SITE_CSS.replace("--muted:", "--unused:")
    with pytest.raises(SystemExit, match="--muted"):
        TOOL.render(without)


def test_a_root_inside_a_media_query_is_not_the_bare_one() -> None:
    rules = TOOL.top_level_rules(
        "@media print { :root { --bg: red; } }\n:root { --bg: blue; }"
    )
    assert TOOL.declarations(rules[":root"]) == {"--bg": "blue"}


def test_the_dashboard_sets_no_shared_token_itself() -> None:
    shared = set(TOOL.SCALE) | set(TOOL.COLOURS)
    set_by_hand = set(re.findall(r"(--[a-z0-9-]+)\s*:", APP_CSS)) & shared
    assert set_by_hand == set()


def test_the_dashboard_reads_no_token_nothing_sets() -> None:
    # `--dim` was the dashboard's own name for what the site calls `--muted`.
    # A read of a name neither file sets resolves to nothing, silently.
    own = set(re.findall(r"(--[a-z0-9-]+)\s*:", APP_CSS))
    generated = set(re.findall(r"(--[a-z0-9-]+)\s*:", TOKENS_CSS))
    read = set(re.findall(r"var\((--[a-z0-9-]+)", APP_CSS))
    assert read - own - generated == set()


def test_the_dashboard_loads_the_tokens_before_its_own_stylesheet() -> None:
    main = (DASHBOARD / "main.tsx").read_text(encoding="utf-8")
    assert main.index('import "./tokens.css";') < main.index('import "./app.css";')


def test_the_site_never_takes_dark_from_the_system() -> None:
    assert "prefers-color-scheme: dark" not in SITE_CSS
