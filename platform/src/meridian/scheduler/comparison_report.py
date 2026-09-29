"""``meridian schedule evaluate``'s text — the comparison, and how to regenerate it.

Pure: results in, lines out. The hashes, the seed, the solver's version and
every setting head the report, because a number without them cannot be
regenerated (rule 8). Then what was replayed and what was left out: the
completeness threshold and the station-days either side of it (D-151), and
how many candidates have a known outcome. Then one row per scheduler, and
SC-1.

**Nothing that varies from run to run is printed.** Solver runtimes are not,
so the same dataset, configuration and seed print the same bytes; a day the
time limit cut short is counted in the ``solved`` column instead, since only
then can a scheduler's figure — or the oracle's bound — depend on the machine.

Reference: docs/DECISIONS.md D-151, D-167, D-168, D-172; docs/EVALUATION.md §3.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from meridian.scheduler.comparison import Gain, Totals, paired_gain, totals
from meridian.scheduler.replay import ORACLE, SCHEDULERS, ReplayResults

__all__ = ["comparison_lines"]

_SC1 = ("D", "B")
_AGAINST_PRACTICE = ("D", "greedy B")


def comparison_lines(
    results: ReplayResults, *, config_sha256: bytes, resamples: int
) -> list[str]:
    """The report, top to bottom, without newlines.

    Args:
        results: Every scheduler's days, and the replay they were run on.
        config_sha256: The comparison configuration's hash.
        resamples: How many bootstrap resamples the intervals are drawn from.
    """
    lines = [
        *_header(results, config_sha256, resamples),
        *_replayed(results),
    ]
    if not results.replay.days:
        lines.append(
            "no station-day of the test span reaches the threshold; nothing is"
            " compared (read it at another with `threshold`)"
        )
        return lines
    summed = {name: totals(results.results[name]) for name in SCHEDULERS}
    hours = sum(one.hours for one in results.replay.days)
    lines.extend(_table(summed, hours))
    lines.extend(_gains(results, resamples))
    if any(status != "optimal" for status, _ in summed[ORACLE].statuses):
        lines.append(
            "  the oracle bounds a day only where it was solved to optimality;"
            " the solved column says where it was not"
        )
    lines.append(
        "  a pass with no known outcome adds no frames to any schedule, so a"
        " scheduler is judged low by its unknown share"
    )
    return lines


def _header(results: ReplayResults, config_sha256: bytes, resamples: int) -> list[str]:
    replay, config = results.replay, results.config
    return [
        f"schedule comparison over dataset {replay.dataset_sha256.hex()}",
        f"  raw snapshot       {replay.raw_sha256.hex()}",
        *(
            f"  model {name}            {digest.hex()}"
            for name, digest in sorted(replay.models.items())
        ),
        "  model B            A's (D-160)",
        f"  configuration file {config_sha256.hex()}",
        f"  test span          {replay.test_from.isoformat()} to"
        f" {replay.as_of.isoformat()} (the dataset's as_of)",
        f"  objective          yield × frames ({config.frames}) × priority"
        " under B and D (D-168)",
        f"  constraints        turnaround {config.turnaround_s:g} s, at most 8"
        " eligible at once (D-166)",
        f"  solver             highs {results.solver_version}, time limit"
        f" {config.time_limit_s:g} s per station-day and scheduler",
        f"  seed               {config.seed}; {resamples} bootstrap resamples",
    ]


def _replayed(results: ReplayResults) -> list[str]:
    replay = results.replay
    candidates = sum(len(one.pass_ids) for one in replay.days)
    hours = sum(one.hours for one in replay.days)
    outcomes = [
        replay.outcomes[pass_id] for one in replay.days for pass_id in one.pass_ids
    ]
    known = [one for one in outcomes if one.frames is not None]
    unknown = Counter(one.why for one in outcomes if one.frames is None)
    unknown_text = " · ".join(f"{why} {n}" for why, n in sorted(unknown.items()))
    decoded = sum(1 for one in known if one.frames)
    return [
        f"  completeness       threshold {replay.threshold:g}:"
        f" {len(replay.days)} station-days replayed;"
        f" {replay.left_out.get('below_threshold', 0)} below it and"
        f" {replay.left_out.get('empty', 0)} empty left out (D-151)",
        f"  candidates         {candidates} passes over {hours:.1f} station-hours;"
        f" {replay.left_out.get('simulated', 0)} simulated left out (D-078)",
        f"  outcomes known     {len(known)} of {candidates}: {decoded} decoded,"
        f" {len(known) - decoded} nothing decoded;"
        f" unknown: {unknown_text or 'none'}",
    ]


def _table(summed: Mapping[str, Totals], hours: float) -> list[str]:
    bound = summed[ORACLE].frames
    lines = [
        f"  {'scheduler':<10} {'taken':>6} {'frames':>8} {'per hour':>9}"
        f" {'unknown':>14} {'of oracle':>9}  solved",
    ]
    for name in SCHEDULERS:
        one = summed[name]
        share = one.unknown_share
        unknown = f"{one.unknown} ({'—' if share is None else f'{share:.1%}'})"
        of_oracle = "—" if bound == 0 else f"{one.frames / bound:.3f}"
        solved = " · ".join(f"{status} {n}" for status, n in one.statuses)
        lines.append(
            f"  {name:<10} {one.selected:>6} {one.frames:>8}"
            f" {one.per_hour(hours):>9.3f} {unknown:>14} {of_oracle:>9}  {solved}"
        )
    return lines


def _gains(results: ReplayResults, resamples: int) -> list[str]:
    hours = [one.hours for one in results.replay.days]
    lines = []
    for label, (first, second) in (
        ("SC-1, D − B", _SC1),
        ("D − greedy B", _AGAINST_PRACTICE),
    ):
        gain = paired_gain(
            results.results[first],
            results.results[second],
            hours,
            seed=results.config.seed,
            resamples=resamples,
        )
        lines.append(f"  {label:<16} {_gain_text(gain, len(hours))}")
    return lines


def _gain_text(gain: Gain, days: int) -> str:
    interval = gain.per_hour_interval
    text = (
        f"{gain.per_hour:+.3f} frames per station-hour"
        f" (95% interval {interval.low:+.3f} to {interval.high:+.3f})"
    )
    if gain.relative is None:
        text += ", no relative gain: the second took no frames"
    elif gain.relative_interval is None:
        text += f", {gain.relative:+.1%} (no interval: a resample took no frames)"
    else:
        low, high = gain.relative_interval.low, gain.relative_interval.high
        text += f", {gain.relative:+.1%} (95% interval {low:+.1%} to {high:+.1%})"
    return f"{text}; paired bootstrap over {days} station-days, {gain.resamples} draws"
