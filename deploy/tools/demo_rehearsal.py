"""Rehearse the demonstration in a browser, against a running demo stack.

    uvx --with playwright python deploy/tools/demo_rehearsal.py \\
        --base http://localhost:8080 [--chromium PATH] [--project-name NAME]

Stage 35's gate has two halves. The platform's, that every step of
``PROJECT.md`` §13 the software can show is in what ``/api/v1`` answers, is
``tests/integration/test_demo_rehearsal.py``. This is the browser's, run against
the ``demo`` profile as a visitor meets it, through the front:

1. **Every view renders** (live, stations, schedule, results, areas, changes,
   insights and the kiosk), with no error on the console and **no
   content-security-policy violation** (D-208), the policy the platform sends
   being the one in force.
2. **Simulated figures say so.** The network and a station's schedule, drawn
   from a simulated fleet, carry the simulated badge (rule 5).
3. **A pass is explained.** Choosing a station and one of its passes opens the
   explanation, with the orbit's age.
4. **The api stops,** as an operator would stop it, and every view is served
   from the front's copy **marked not live** (D-282, D-284).
5. **The api starts again,** always, whatever happened above, and the next
   refresh clears every mark.

**It stops the api for about a minute.** Stations keep receiving and queue their
reports (§12.1); nothing is lost and nothing here touches the database.

Playwright is not a dependency of any distribution, because only this tool uses
it; ``uvx --with playwright`` brings it for the one run. ``--chromium`` names a
browser binary when Playwright's own download is not wanted.

Exit status: 0 the rehearsal passed, 1 it did not, or could not be run.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 35's completion gate;
docs/DECISIONS.md D-208, D-282, D-284, D-334, D-335.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from compose_db import Compose, ToolError, add_compose_arguments, compose_from
from front_drill import run, wait_for_api

VIEWS = (
    "live",
    "stations",
    "schedule",
    "results",
    "areas",
    "changes",
    "insights",
    "kiosk",
)
"""Every view the dashboard has (D-334, D-364, D-366)."""

OUTAGE_VIEWS = ("live", "results", "insights")
"""The views asked for again with the api stopped: those a visitor opens first."""

SETTLE_MS = 4_000
"""How long a view is given to make its requests and draw."""

NOT_LIVE_MS = 30_000
"""How long a view may take to say it is a copy: past the dashboard's timeout."""

CSP_PROBE = """
window.__violations = [];
document.addEventListener("securitypolicyviolation", (event) => {
  window.__violations.push(`${event.violatedDirective} ${event.blockedURI}`);
});
"""
"""Records every policy violation the page meets, before any of its own code runs."""


@dataclass
class Verdict:
    """What the rehearsal found, step by step."""

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def check(self, ok: bool, what: str) -> None:
        """Record one check, and print it as it happens."""
        (self.passed if ok else self.failed).append(what)
        print(f"{'  ok  ' if ok else '  FAIL'} {what}", flush=True)


def open_view(page: Any, base: str, view: str) -> None:
    """Load one view fresh and give it time to draw."""
    page.goto(f"{base}/#/{view}")
    page.wait_for_timeout(SETTLE_MS)


def rehearse_views(page: Any, base: str, verdict: Verdict, errors: list[str]) -> None:
    """Steps 1 to 3: every view, live."""
    for view in VIEWS:
        open_view(page, base, view)
        violations = page.evaluate("window.__violations")
        verdict.check(page.locator("main h2").count() > 0, f"{view}: the view renders")
        verdict.check(not violations, f"{view}: no policy violation {violations or ''}")
    verdict.check(not errors, f"no console error {errors[:3] or ''}")
    open_view(page, base, "live")
    verdict.check(
        page.locator(".badge-simulated").count() > 0,
        "live: the fleet is marked simulated",
    )
    explain_a_pass(page, base, verdict)


def explain_a_pass(page: Any, base: str, verdict: Verdict) -> None:
    """Step 3: choose a station with passes ahead, and one of its passes."""
    open_view(page, base, "schedule")
    options = page.locator("select option")
    for index in range(1, options.count()):
        page.select_option("select", index=index)
        page.wait_for_timeout(SETTLE_MS)
        if page.locator("ol.entries button").count() > 0:
            break
    entries = page.locator("ol.entries button")
    verdict.check(entries.count() > 0, "schedule: a station has a pass ahead")
    if entries.count() == 0:
        return
    entries.first.click()
    pane = page.locator(".explanation")
    verdict.check(pane.count() == 1, "schedule: choosing a pass explains it")
    verdict.check(
        "orbit" in pane.inner_text(), "schedule: the explanation names the orbit's age"
    )
    verdict.check(
        pane.locator(".badge-simulated").count() > 0,
        "schedule: the decision is marked simulated",
    )


def rehearse_outage(page: Any, base: str, compose: Compose, verdict: Verdict) -> None:
    """Steps 4 and 5: stop the api, read every view as a copy, start it again."""
    print("-> stop the api", flush=True)
    run(compose, "stop", "api")
    try:
        for view in OUTAGE_VIEWS:
            page.goto(f"{base}/#/{view}")
            note = page.locator(".snapshot-note")
            try:
                note.first.wait_for(timeout=NOT_LIVE_MS)
                said = note.first.inner_text()
            except Exception:
                said = ""
            verdict.check(
                "Not live." in said,
                f"{view}: with the api stopped, it says it is not live",
            )
    finally:
        print("-> start the api", flush=True)
        run(compose, "start", "api")
        wait_for_api(compose)
    time.sleep(5)
    for view in OUTAGE_VIEWS:
        open_view(page, base, view)
        verdict.check(
            page.locator(".snapshot-note").count() == 0,
            f"{view}: live again, and no longer marked",
        )


def rehearse(args: argparse.Namespace) -> Verdict:
    """The whole rehearsal, in one browser."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 — optional
    except ImportError as exc:
        raise ToolError(
            "Playwright is needed: run this with `uvx --with playwright python …`"
        ) from exc
    verdict = Verdict()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.add_init_script(CSP_PROBE)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        rehearse_views(page, args.base, verdict, errors)
        if not args.skip_outage:
            rehearse_outage(page, args.base, compose_from(args), verdict)
        browser.close()
    return verdict


def main(argv: list[str] | None = None) -> int:
    """Run the rehearsal and say whether it passed."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--base",
        default="http://localhost:8080",
        help="the front, as a browser reaches it",
    )
    parser.add_argument(
        "--chromium",
        default=None,
        help="a Chromium binary, instead of Playwright's own",
    )
    parser.add_argument(
        "--skip-outage",
        action="store_true",
        help="leave the api running: steps 1 to 3 only",
    )
    add_compose_arguments(parser)
    args = parser.parse_args(argv)
    try:
        verdict = rehearse(args)
    except ToolError as exc:
        print(f"the rehearsal could not run: {exc}", file=sys.stderr)
        return 1
    print(f"{len(verdict.passed)} checks passed, {len(verdict.failed)} failed")
    return 0 if not verdict.failed else 1


if __name__ == "__main__":
    sys.exit(main())
