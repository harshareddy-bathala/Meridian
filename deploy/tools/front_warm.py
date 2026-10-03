"""Ask the front for every view the dashboard shows, so it keeps a copy of each.

Standard library only.

    python deploy/tools/front_warm.py                          # once, against :8080
    python deploy/tools/front_warm.py --base http://front:8080 --every 300

The front keeps the last good answer to a request it has forwarded (D-282). A
view nobody opened before an outage has no copy, and a visitor arriving during
the outage would find that station's page empty. This makes the requests the
dashboard would make, for every station the directory lists, through the front:
the page, the assets the page names, and each route in
``deploy/front/snapshot-routes.txt`` (D-283).

**It goes through the front, never to the api directly,** because a copy is kept
only of what passes through the front, under the exact path and query a browser
will ask for. The routes file is checked against the dashboard's own requests by
``dashboard/src/snapshotRoutes.test.ts``, and the encoding of a station id here
is the browser's: ``encodeURIComponent`` in a path, ``URLSearchParams`` in a
query.

**It is paced under the public API's limit** of five requests a second per
caller (D-202), and waits out a ``429`` once, as its ``Retry-After`` says.

``--json`` prints every answer, with its ``Date`` and how long it took, for
``front_drill.py`` to judge: the drill of EVALUATION.md §12.2 asks the front
from inside the compose network, where this tool already runs.

``--every`` runs it in a loop, which is how the ``front-warm`` compose service
runs it. A round that fails is reported and the loop carries on. With
``--status-file``, each finished round writes its counts there, and the
service's healthcheck reads how long ago that was.

Exit status, once: 0 every view answered by the api; 1 otherwise — the api did
not answer (a ``STALE`` copy came back), or a view failed.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from urllib.parse import quote, quote_plus

ROUTES = Path(__file__).resolve().parents[1] / "front" / "snapshot-routes.txt"
STATION = "{station}"
DIRECTORY = "/api/v1/stations?limit=200"
"""The directory's first page, as the dashboard asks for it; later pages add
``&cursor=``, which the dashboard appends after ``limit`` in the same way."""

PACE_S = 0.25
"""Four requests a second, under the public API's five (D-202)."""

ASSET = re.compile(r"""(?:src|href)=["'](/assets/[^"']+)["']""")
CSS_ASSET = re.compile(r"""url\(\s*["']?(/assets/[^"')\s]+)["']?\s*\)""")


@dataclass(frozen=True, slots=True)
class Answer:
    """What one request through the front came back with."""

    path: str
    status: int
    cache: str
    """``Meridian-Cache``: ``MISS``, ``EXPIRED``, ``HIT``, ``STALE``, or empty."""
    body: bytes = b""
    date: str = ""
    """The ``Date`` header: for a ``STALE`` copy, when the api made it (D-282)."""
    seconds: float = 0.0
    """How long the front took to answer."""

    def report(self) -> dict[str, object]:
        """Everything but the body, for the drill to judge (front_drill.py)."""
        return {
            "path": self.path,
            "status": self.status,
            "cache": self.cache,
            "date": self.date,
            "seconds": round(self.seconds, 3),
        }


@dataclass(slots=True)
class Round:
    """One pass over every view."""

    answers: list[Answer] = field(default_factory=list)
    stations: int = 0

    @property
    def failed(self) -> list[Answer]:
        return [a for a in self.answers if a.status != 200]

    @property
    def stale(self) -> list[Answer]:
        return [a for a in self.answers if a.cache == "STALE"]

    @property
    def ok(self) -> bool:
        return bool(self.answers) and not self.failed and not self.stale

    def summary(self) -> str:
        line = (
            f"warmed {len(self.answers)} views for {self.stations} stations: "
            f"{len(self.failed)} failed, {len(self.stale)} stale"
        )
        if self.stale:
            line += " (the api did not answer; the front served its copies)"
        for answer in self.failed[:5]:
            line += f"\n  {answer.status} {answer.path}"
        return line


Fetch = Callable[[str], Answer]


def path_segment(value: str) -> str:
    """``encodeURIComponent``: what the dashboard puts in a path."""
    return quote(value, safe="!~*'()")


def query_value(value: str) -> str:
    """``URLSearchParams``: what the dashboard puts in a query.

    The form-urlencoded serializer leaves ``*-._`` and alphanumerics alone and
    writes a space as ``+``. ``quote_plus`` also leaves ``~``, which the browser
    encodes, so that one is put back.
    """
    return quote_plus(value, safe="*").replace("~", "%7E")


def read_routes(path: Path = ROUTES) -> list[str]:
    """The routes, one a line, comments and blank lines dropped."""
    routes = []
    for line in path.read_text().splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            routes.append(text)
    return routes


def expand(route: str, station_id: str) -> str:
    """One route for one station, encoded where it sits."""
    path, _, query = route.partition("?")
    path = path.replace(STATION, path_segment(station_id))
    query = query.replace(STATION, query_value(station_id))
    return f"{path}?{query}" if query else path


def assets_of(html: bytes) -> list[str]:
    """The assets a page or a stylesheet names, in order, once each."""
    text = html.decode("utf-8", "replace")
    found = ASSET.findall(text) + CSS_ASSET.findall(text)
    return list(dict.fromkeys(found))


def station_ids(fetch: Fetch, first: str = DIRECTORY) -> tuple[list[str], list[Answer]]:
    """Every station in the directory, following ``next_cursor`` as the dashboard."""
    ids: list[str] = []
    answers: list[Answer] = []
    path: str | None = first
    for _ in range(50):  # the dashboard's MAX_PAGES
        if path is None:
            break
        answer = fetch(path)
        answers.append(answer)
        if answer.status != 200:
            break
        page = json.loads(answer.body)
        ids.extend(str(item["station_id"]) for item in page.get("items", []))
        cursor = page.get("next_cursor")
        path = f"{first}&cursor={query_value(cursor)}" if cursor else None
    return ids, answers


def warm(fetch: Fetch, routes: Iterable[str]) -> Round:
    """Ask for the page, its assets, the directory and every route."""
    result = Round()
    page = fetch("/")
    result.answers.append(page)
    if page.status == 200:
        for asset in assets_of(page.body):
            answer = fetch(asset)
            result.answers.append(answer)
            if asset.endswith(".css") and answer.status == 200:
                for nested in assets_of(answer.body):
                    result.answers.append(fetch(nested))

    ids, pages = station_ids(fetch)
    result.answers.extend(pages)
    result.stations = len(ids)
    for route in routes:
        if route == DIRECTORY:
            continue
        if STATION in route:
            result.answers.extend(fetch(expand(route, station)) for station in ids)
        else:
            result.answers.append(fetch(route))
    return result


def write_status(path: Path, result: Round) -> None:
    """The round's counts, replaced whole, so a reader never sees half a file."""
    status = {
        "finished_at": time.time(),
        "ok": result.ok,
        "views": len(result.answers),
        "stations": result.stations,
        "failed": len(result.failed),
        "stale": len(result.stale),
    }
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(status) + "\n")
    partial.replace(path)


def http_fetch(base: str, timeout_s: float, pace_s: float = PACE_S) -> Fetch:
    """A paced GET through the front, waiting out one ``429``."""

    def once(path: str) -> tuple[Answer, float | None]:
        request = urllib.request.Request(base.rstrip("/") + path)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = response.read()
                headers, status = response.headers, response.status
            wait = None
        except urllib.error.HTTPError as error:
            body, headers, status = b"", error.headers, error.code
            retry = headers.get("Retry-After")
            wait = float(retry) if status == 429 and retry else None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            print(f"  {path}: {reason}", file=sys.stderr)
            return Answer(path, 0, "", seconds=time.monotonic() - started), None
        answer = Answer(
            path,
            status,
            headers.get("Meridian-Cache", ""),
            body,
            date=headers.get("Date", ""),
            seconds=time.monotonic() - started,
        )
        return answer, wait

    def fetch(path: str) -> Answer:
        time.sleep(pace_s)
        answer, wait = once(path)
        if wait is not None:
            time.sleep(min(wait, 60.0))
            answer, _ = once(path)
        return answer

    return fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--base", default="http://localhost:8080", help="the front's URL"
    )
    parser.add_argument("--routes", type=Path, default=ROUTES)
    parser.add_argument(
        "--timeout", type=float, default=20.0, help="seconds per request"
    )
    parser.add_argument(
        "--every", type=float, default=None, help="repeat every N seconds until stopped"
    )
    parser.add_argument(
        "--status-file", type=Path, default=None, help="write each round's counts here"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print every answer as JSON instead of the summary, for front_drill.py",
    )
    parser.add_argument(
        "--also",
        action="append",
        default=[],
        metavar="PATH",
        help="with --json: a path outside the routes to ask for as well",
    )
    args = parser.parse_args(argv)

    routes = read_routes(args.routes)
    fetch = http_fetch(args.base, args.timeout)
    if args.json:
        result = warm(fetch, routes)
        report = {
            "stations": result.stations,
            "views": [answer.report() for answer in result.answers],
            "also": [fetch(path).report() for path in args.also],
        }
        print(json.dumps(report))
        return 0
    if args.every is None:
        result = warm(fetch, routes)
        print(result.summary())
        return 0 if result.ok else 1

    def stopping(_signum: int, _frame: FrameType | None) -> None:
        # At once, mid-round if need be: a round left half done costs nothing,
        # and in an outage one takes as long as every request's timeout.
        raise SystemExit(0)

    # As PID 1 in a container, SIGTERM is ignored without a handler, and
    # `compose stop` would wait out its grace period every time.
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    while True:
        try:
            result = warm(fetch, routes)
            print(result.summary(), flush=True)
            if args.status_file is not None:
                write_status(args.status_file, result)
        except Exception as error:  # a malformed answer must not end the loop
            print(f"round failed: {error!r}", file=sys.stderr, flush=True)
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
