"""Run the drill of EVALUATION.md §12.2 against a running deployment. Stdlib only.

    python deploy/tools/front_drill.py
    python deploy/tools/front_drill.py --compose-file deploy/docker-compose.yml …

*With the platform unreachable, the dashboard renders its last snapshot and says
how old it is. It fails if the page errors, hangs waiting on an upstream, or
shows figures that look live when they are not.* The dashboard's half of that —
reading the front's labels and saying so on the page — is tested with the
dashboard (D-284). This is the deployment's half, run for real:

1. **Warm.** Ask the front for every view (D-283). Each must be answered by the
   api, or there is no outage to compare with and the drill refuses to start.
   The front is given half a minute to find an api it lost track of.
2. **Stop the api**, as an operator would, with `docker compose stop api`.
3. **Ask again.** Every view must come back `200`, marked `STALE`, carrying a
   `Date` from before the stop, promptly. A view nobody warmed must fail
   promptly, not hang, and an MSP route must not be answered from a copy at
   all: the front keeps none (D-282).
4. **Start the api**, always, whatever happened above.
5. **Ask once more.** Nothing may be `STALE` now, once the front has had the
   same half minute: a copy served after the platform is back is a stale
   figure presented as current.

The questions are asked from inside the compose network, by the warmer in the
`front-warm` container, because the public deployment gives the front no host
port (D-206). So this runs the same on a laptop, in CI and on the Pi.

**It stops the api for about a minute.** Stations keep receiving and queue their
reports (§12.1); nothing is lost, and nothing here touches the database.

Exit status: 0 the drill passed, 1 it did not, or could not be run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from compose_db import Compose, ToolError, add_compose_arguments, compose_from

FRONT = "http://front:8080"
WARMER = "/warm/tools/front_warm.py"

PROMPT_S = 5.0
"""How long the front may take to answer with the api stopped.

It gives up on the api in two seconds (D-282). Five is that and a slow Pi; a
hang would be the warmer's own twenty-second timeout or more.
"""

UNWARMED = "/api/v1/stations?limit=7"
"""A public view no browser and no warmer asks for, so the front holds no copy."""

NEVER_KEPT = "/msp/v0/time"
"""An MSP route. Forwarded, never kept, whatever was asked before."""

API_BACK_S = 120.0

FRONT_FINDS_API_S = 30.0
"""How long the front may take to find an api that came back at a new address.

It asks Docker's resolver every ten seconds and holds a failed answer as long
(`nginx.conf`), so a recreated api can go unseen for twenty. `compose up`
recreates the api whenever its configuration changes, as CI's sim step does,
and until the front finds it every view is honestly a copy.
"""


@dataclass(frozen=True, slots=True)
class View:
    """One answer, as `front_warm.py --json` reports it."""

    path: str
    status: int
    cache: str
    date: str
    seconds: float

    def made_at(self) -> datetime | None:
        """The ``Date`` header as an instant, or ``None`` if it has none."""
        try:
            return parsedate_to_datetime(self.date).astimezone(UTC)
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class Asked:
    """One round of questions: every view, and the paths asked besides."""

    views: tuple[View, ...]
    also: tuple[View, ...] = ()
    stations: int = 0


@dataclass(slots=True)
class Verdict:
    """What failed, in the words an operator reads. Empty means passed."""

    failures: list[str] = field(default_factory=list)

    def fail(self, view: View, why: str) -> None:
        self.failures.append(
            f"{view.path}: {why} "
            f"(HTTP {view.status}, {view.cache or 'no label'}, {view.seconds:.1f}s)"
        )


def parse_report(output: str) -> Asked:
    """`front_warm.py --json`'s one line, as an :class:`Asked`."""
    try:
        document = json.loads(output)
        return Asked(
            views=tuple(View(**view) for view in document["views"]),
            also=tuple(View(**view) for view in document["also"]),
            stations=int(document["stations"]),
        )
    except (ValueError, KeyError, TypeError) as broken:
        raise ToolError(f"the warmer's report could not be read: {broken}") from broken


def judge_warm(asked: Asked) -> Verdict:
    """Before the outage: every view answered, and by the api."""
    verdict = Verdict()
    if not asked.views:
        verdict.failures.append("the warmer asked for nothing")
    for view in asked.views:
        if view.status != 200:
            verdict.fail(view, "did not answer before the outage")
        elif view.cache == "STALE":
            verdict.fail(view, "was already a copy: the api is not answering")
    return verdict


def judge_outage(asked: Asked, stopped_at: datetime) -> Verdict:
    """With the api stopped: copies, labelled, dated before the stop, promptly."""
    verdict = Verdict()
    for view in asked.views:
        made_at = view.made_at()
        if view.status != 200:
            verdict.fail(view, "the page errors: no copy was served")
        elif view.cache != "STALE":
            verdict.fail(view, "a copy served without saying it is one")
        elif made_at is None or made_at > stopped_at:
            verdict.fail(view, f"its Date, {view.date!r}, is not from before the stop")
        if view.seconds > PROMPT_S:
            verdict.fail(view, f"took over {PROMPT_S:.0f}s: the page hangs")
    for view in asked.also:
        if view.status == 200:
            verdict.fail(view, "answered with the api stopped, from a copy nobody owed")
        if view.seconds > PROMPT_S:
            verdict.fail(view, f"took over {PROMPT_S:.0f}s to fail: the page hangs")
    return verdict


def judge_recovery(asked: Asked) -> Verdict:
    """With the api back: its own answers again, and no copy presented as current."""
    verdict = Verdict()
    for view in asked.views:
        if view.status != 200:
            verdict.fail(view, "did not answer after the api came back")
        elif view.cache == "STALE":
            verdict.fail(view, "still a copy after the api came back")
    return verdict


def ask(compose: Compose, *also: str) -> Asked:
    """One round of questions, asked by the warmer inside the compose network."""
    extra = [argument for path in also for argument in ("--also", path)]
    command = compose.command(
        "exec", "-T", "front-warm", "python", WARMER, "--base", FRONT, "--json", *extra
    )
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise ToolError(
            f"could not ask through front-warm: {result.stderr.strip()}"
            " — is the stack up, with the front and front-warm services?"
        )
    return parse_report(result.stdout)


def run(compose: Compose, *args: str) -> None:
    """One `docker compose` command, raising with its own words if it fails."""
    result = subprocess.run(
        compose.command(*args), text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ToolError(f"docker compose {' '.join(args)}: {result.stderr.strip()}")


def wait_for_api(compose: Compose) -> None:
    """Until the api's own healthcheck passes again."""
    deadline = time.monotonic() + API_BACK_S
    while time.monotonic() < deadline:
        result = subprocess.run(
            compose.command("ps", "--format", "{{.Service}} {{.Health}}", "api"),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.stdout.strip() == "api healthy":
            return
        time.sleep(2.0)
    raise ToolError(f"the api was not healthy again within {API_BACK_S:.0f}s")


def ask_until_live(
    asking: Callable[[], Asked],
    judge: Callable[[Asked], Verdict],
    *,
    within: float = FRONT_FINDS_API_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Asked, Verdict]:
    """Ask until *judge* passes, or *within* seconds, then return the last round."""
    deadline = clock() + within
    while True:
        asked = asking()
        verdict = judge(asked)
        if not verdict.failures or clock() >= deadline:
            return asked, verdict
        sleep(2.0)


def drill(compose: Compose) -> list[str]:
    """Run the five steps. Returns what failed; empty means the drill passed."""
    print("-> warm: ask the front for every view", flush=True)
    warmed, before = ask_until_live(lambda: ask(compose), judge_warm)
    if before.failures:
        return ["the drill could not start:", *before.failures]
    print(f"   {len(warmed.views)} views for {warmed.stations} stations", flush=True)

    stopped_at = datetime.now(UTC)
    print("-> stop the api", flush=True)
    run(compose, "stop", "api")
    try:
        print("-> ask again, with the api stopped", flush=True)
        outage = judge_outage(ask(compose, UNWARMED, NEVER_KEPT), stopped_at)
    finally:
        print("-> start the api", flush=True)
        run(compose, "start", "api")
        wait_for_api(compose)
    print("-> ask once more, with the api back", flush=True)
    _, recovery = ask_until_live(lambda: ask(compose), judge_recovery)
    return [*outage.failures, *recovery.failures]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_compose_arguments(parser)
    args = parser.parse_args(argv)
    try:
        failures = drill(compose_from(args))
    except ToolError as refused:
        print(f"front drill: {refused}", file=sys.stderr)
        return 1
    if failures:
        print("FAILED: EVALUATION.md §12.2")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(
        "passed: every view was served from its copy, labelled and dated, with"
        " the api stopped, and from the api again once it was back"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
