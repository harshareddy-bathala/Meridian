"""What the platform showed of itself while a fleet ran against it. Stdlib only.

Stage 21's scale runs measure the platform from two sides. The fleet's side —
heartbeats answered, rounds that overran, each station's upload queue — is the
simulator's ``--scale-report``. This is the platform's side, read from the same
``/metrics`` Prometheus scrapes, so the numbers are the ones an operator's
dashboards would show:

- **request latency** per route template, p50 and p95, from the histogram's
  buckets over the run — so a bucket's upper bound, never an interpolated guess;
- **heartbeats accepted** per second;
- **pool pressure**: connections open, and the fewest idle and the most waiting
  seen at any sample;
- **the scheduler**: rounds, and solver time p95, from the jobs process;
- **series**: how many ``meridian_*`` series the API exposes, at most — the
  number that must not grow with the fleet (D-197).

Prometheus itself is not needed: the probe scrapes the two endpoints with the
scrape token, every ``--every`` seconds for ``--seconds``, and compares the
first scrape with the last.

    python deploy/tools/scale_probe.py --api http://127.0.0.1:8121 \\
        --jobs http://127.0.0.1:9121 --seconds 600 --out scale-50.json

Everything that decides something is a pure function over exposition text, so
``tests/unit/test_scale_probe.py`` pins it without a platform.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPORT_FORMAT = "meridian-scale-probe/1"

Series = tuple[str, tuple[tuple[str, str], ...]]
"""A metric name and its sorted labels: one series."""

Samples = dict[Series, float]

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})?\s+(\S+)")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def parse(text: str) -> Samples:
    """Every sample in Prometheus text exposition, by series."""
    samples: Samples = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if match is None:
            continue
        name, _, labels, value = match.groups()
        pairs = tuple(sorted(_LABEL.findall(labels or "")))
        try:
            samples[(name, pairs)] = float(value)
        except ValueError:
            continue
    return samples


def labels_of(series: Series) -> dict[str, str]:
    """A series' labels, as a mapping."""
    return dict(series[1])


def total(samples: Samples, name: str, **match: str) -> float:
    """The sum of every sample of ``name`` whose labels include ``match``."""
    return sum(
        value
        for series, value in samples.items()
        if series[0] == name and match.items() <= labels_of(series).items()
    )


def delta(before: Samples, after: Samples, name: str, **match: str) -> float:
    """How much a counter, or a sum of counters, grew between two scrapes."""
    return total(after, name, **match) - total(before, name, **match)


def quantile(
    before: Samples, after: Samples, histogram: str, q: float, **match: str
) -> float | None:
    """The ``q`` quantile of a histogram's observations between two scrapes.

    The upper bound of the first bucket whose cumulative count reaches ``q`` of
    the whole: a bound the observations are known to lie under, not an
    interpolation within a bucket. ``None`` when nothing was observed, and
    ``inf`` when the quantile lies past the last finite bucket.
    """
    bucket = f"{histogram}_bucket"
    bounds: dict[float, float] = {}
    for series in after:
        if series[0] != bucket:
            continue
        labels = labels_of(series)
        if not match.items() <= labels.items():
            continue
        le = float(labels["le"])
        bounds[le] = bounds.get(le, 0.0) + after[series] - before.get(series, 0.0)
    if not bounds or bounds.get(float("inf"), 0.0) <= 0:
        return None
    whole = bounds[float("inf")]
    for le in sorted(bounds):
        if bounds[le] >= q * whole:
            return le
    return float("inf")


def routes(samples: Samples) -> list[str]:
    """Every route template the request histogram has seen."""
    return sorted(
        {
            labels_of(series)["route"]
            for series in samples
            if series[0] == "meridian_http_request_duration_seconds_count"
        }
    )


def series_count(samples: Samples, prefix: str = "meridian_") -> int:
    """How many series with ``prefix`` a scrape exposed."""
    return sum(1 for series in samples if series[0].startswith(prefix))


def summarise(
    api: list[Samples], jobs: list[Samples], seconds: float
) -> dict[str, object]:
    """The report, from every API and jobs scrape of the run, in order."""
    first, last = api[0], api[-1]
    per_route = {
        route: {
            "requests": delta(
                first,
                last,
                "meridian_http_request_duration_seconds_count",
                route=route,
            ),
            "p50_s": quantile(
                first, last, "meridian_http_request_duration_seconds", 0.5, route=route
            ),
            "p95_s": quantile(
                first, last, "meridian_http_request_duration_seconds", 0.95, route=route
            ),
        }
        for route in routes(last)
    }
    heartbeats = delta(first, last, "meridian_msp_heartbeats_total")
    report: dict[str, object] = {
        "format": REPORT_FORMAT,
        "window_s": round(seconds, 1),
        "scrapes": len(api),
        "routes": per_route,
        "heartbeats_accepted": heartbeats,
        "heartbeats_accepted_per_s": round(heartbeats / seconds, 3) if seconds else 0,
        "pool_size_max": max(
            total(one, "meridian_db_pool_connections", kind="size") for one in api
        ),
        "pool_available_min": min(
            total(one, "meridian_db_pool_connections", kind="available") for one in api
        ),
        "pool_waiting_max": max(
            total(one, "meridian_db_pool_connections", kind="waiting") for one in api
        ),
        "series_max": max(series_count(one) for one in api),
        "series_final": series_count(last),
        "stations_by_liveness": {
            labels_of(series)["liveness"]: value
            for series, value in last.items()
            if series[0] == "meridian_stations" and value
        },
    }
    if jobs:
        report.update(_jobs_summary(jobs))
    return report


JOB_TASKS = ("pass_generation", "schedule", "expiry_sweep", "reliability")
"""The jobs process's tasks, each timed by ``meridian_job_duration_seconds``."""


def _jobs_summary(jobs: list[Samples]) -> dict[str, object]:
    """The jobs process over the run: rounds, what each task took, the solver.

    The scheduler's candidate count and solver time are gauges holding the last
    run's value, so the largest seen at any scrape is reported; the tasks'
    durations are a histogram, so their mean and p95 are over the run.
    """
    first, last = jobs[0], jobs[-1]
    duration = "meridian_job_duration_seconds"
    tasks: dict[str, object] = {}
    for task in JOB_TASKS:
        count = delta(first, last, f"{duration}_count", task=task)
        spent = delta(first, last, f"{duration}_sum", task=task)
        tasks[task] = {
            "rounds": count,
            "mean_s": round(spent / count, 3) if count else None,
            "p95_s": quantile(first, last, duration, 0.95, task=task),
        }
    return {
        "scheduler_runs": delta(first, last, "meridian_scheduler_runs_total"),
        "scheduler_candidates_max": max(
            total(one, "meridian_scheduler_candidates") for one in jobs
        ),
        "solver_seconds_max": max(
            total(one, "meridian_scheduler_solver_seconds") for one in jobs
        ),
        "job_tasks": tasks,
        "jobs_series_max": max(series_count(one) for one in jobs),
    }


def scrape(url: str, token: str) -> Samples:
    """One authenticated scrape of ``url``/metrics."""
    request = urllib.request.Request(
        f"{url.rstrip('/')}/metrics", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return parse(response.read().decode("utf-8"))


def probe(
    api_url: str,
    jobs_url: str | None,
    token: str,
    seconds: float,
    every: float,
    sleep: Callable[[float], None] = time.sleep,
    fetch: Callable[[str, str], Samples] = scrape,
) -> dict[str, object]:
    """Scrape both endpoints every ``every`` seconds for ``seconds``, and summarise."""
    api: list[Samples] = []
    jobs: list[Samples] = []
    rounds = max(1, int(seconds // every))
    for index in range(rounds + 1):
        api.append(fetch(api_url, token))
        if jobs_url:
            jobs.append(fetch(jobs_url, token))
        if index < rounds:
            sleep(every)
    return summarise(api, jobs, rounds * every)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", required=True, help="the API's base URL")
    parser.add_argument("--jobs", default=None, help="the jobs process's metrics URL")
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--every", type=float, default=15.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--token-env",
        default="METRICS_TOKEN",
        help="environment variable holding the scrape token",
    )
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"scale_probe: ${args.token_env} is not set", file=sys.stderr)
        return 1
    report = probe(args.api, args.jobs, token, args.seconds, args.every)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
