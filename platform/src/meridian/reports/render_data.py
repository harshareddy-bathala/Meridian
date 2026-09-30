"""The data section of ``report.md``, rendered from ``data.jsonl`` alone.

Reference: docs/DECISIONS.md D-235; ``EVALUATION.md`` §4 and §5.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, rate, table

__all__ = ["render_data"]

Row = Mapping[str, object]

_POPULATIONS = {"own": "our stations", "archive": "archive stations"}


def render_data(rows: Sequence[Row]) -> list[str]:
    """The section's lines, without newlines.

    Args:
        rows: ``data.jsonl``, parsed, in its written order.
    """
    return [
        "## Data",
        "",
        "Measured and simulated are counted apart and never summed. Every"
        " simulated figure is simulated (rule 5).",
        "",
        *_provenance(rows),
        *_counted(rows, "label", "Outcome labels", "Label"),
        *_counted(rows, "exclusion", "Exclusions", "Reason"),
        *_selection(rows),
        *_silences(rows),
    ]


def _of(rows: Sequence[Row], kind: str) -> list[Row]:
    return [one for one in rows if one["row"] == kind]


def _provenance(rows: Sequence[Row]) -> list[str]:
    raw, dataset = _of(rows, "input")
    lines = [
        "### Provenance",
        "",
        *table(
            ("Input", "Value"),
            [
                ("raw snapshot", f"`{raw['sha256']}`"),
                ("window", f"{raw['since']} to {raw['as_of']}"),
                ("schema revision", cell(raw["schema_revision"])),
                ("evaluation dataset", f"`{dataset['sha256']}`"),
                ("labelling rules", cell(dataset["transformation_version"])),
                ("labelling configuration", f"`{dataset['config_sha256']}`"),
            ],
        ),
        "",
    ]
    sources = _of(rows, "source")
    lines.extend(["**Archive sources**", ""])
    if not sources:
        lines.extend(["None: this snapshot holds no archive rows.", ""])
    else:
        lines.extend(
            table(
                ("Source", "Licence", "Terms", "Records"),
                [
                    (
                        cell(one["source_id"]),
                        cell(one["licence"]),
                        cell(one["terms_url"]),
                        cell(one["records"]),
                    )
                    for one in sources
                ],
            )
        )
        lines.append("")
    return [*lines, *_counted(rows, "count", "**Snapshot counts**", "Count")]


def _counted(rows: Sequence[Row], kind: str, title: str, name: str) -> list[str]:
    """Named counts, populations side by side; a count with none gets a column."""
    found = _of(rows, kind)
    heading = title if title.startswith("**") else f"### {title}"
    if not found:
        return [heading, "", "None stated.", ""]
    unsplit = any("total" in one for one in found)
    headers = (name, "Measured", "Simulated", *(("Unsplit",) if unsplit else ()))
    body = [
        (
            cell(one["name"]),
            cell(one.get("measured")),
            cell(one.get("simulated")),
            *((cell(one.get("total")),) if unsplit else ()),
        )
        for one in found
    ]
    return [heading, "", *table(headers, body), ""]


def _selection(rows: Sequence[Row]) -> list[str]:
    completeness = {str(one["population"]): one for one in _of(rows, "completeness")}
    weighting = {str(one["population"]): one for one in _of(rows, "weighting")}
    order = [one for one in _POPULATIONS if one in completeness]
    body = [
        *_completeness_lines([completeness[one] for one in order]),
        *_weighting_lines([weighting[one] for one in order]),
    ]
    return [
        "### Completeness and weighting",
        "",
        *table(("", *(_POPULATIONS[one] for one in order)), body),
        "",
    ]


def _completeness_lines(found: Sequence[Row]) -> list[tuple[str, ...]]:
    return [
        ("threshold", *(cell(one["threshold"]) for one in found)),
        ("station-days", *(_days(one) for one in found)),
        ("eligible passes", *(cell(one["eligible"]) for one in found)),
        ("attempted", *(cell(one["attempted"]) for one in found)),
        ("usable", *(cell(one["usable"]) for one in found)),
        ("days with a ratio", *(_spread(one, "count") for one in found)),
        ("completeness deciles", *(_spread(one, "deciles") for one in found)),
        ("histogram (tenths)", *(_spread(one, "histogram") for one in found)),
        ("sensitivity", *(_sensitivity(one) for one in found)),
    ]


def _days(one: Row) -> str:
    held = _table(one, "station_days")
    return " · ".join(f"{name} {count}" for name, count in sorted(held.items()))


def _spread(one: Row, key: str) -> str:
    return cell(_table(one, "distribution")[key])


def _sensitivity(one: Row) -> str:
    steps = one["sensitivity"]
    if not isinstance(steps, list):
        message = f"sensitivity is {steps!r}, not a list"
        raise TypeError(message)
    return " · ".join(
        f"{step['threshold']:g}: {step['retained']} kept, {step['excluded']} out"
        for step in steps
    )


def _table(one: Row, key: str) -> Mapping[str, object]:
    held = one[key]
    if not isinstance(held, Mapping):
        message = f"{key} is {held!r}, not a table"
        raise TypeError(message)
    return held


def _weighting_lines(found: Sequence[Row]) -> list[tuple[str, ...]]:
    if any("not_weighted" in one for one in found):
        return [
            (
                "weighting",
                *(f"not weighted: {one['not_weighted']}" for one in found),
            )
        ]

    def weighted(one: Row) -> str:
        flag = " — **unreliable**" if one["unreliable"] else ""
        return rate(one["weighted_rate"]) + flag

    return [
        ("propensity model", *(cell(one["model"]) for one in found)),
        ("weight floor", *(cell(one["floor"]) for one in found)),
        ("unweighted rate", *(rate(one["unweighted"]) for one in found)),
        ("weighted rate", *(weighted(one) for one in found)),
        ("effective n", *(cell(one["ess"]) for one in found)),
        ("weighted passes", *(cell(one["weighted"]) for one in found)),
        ("unsupported", *(cell(one["unsupported"]) for one in found)),
        ("weights, min to max", *(cell(one["weight_quartiles"]) for one in found)),
    ]


def _silences(rows: Sequence[Row]) -> list[str]:
    found = _of(rows, "silence")
    body = [
        (
            cell(one["population"]),
            cell(one["labelled"]),
            cell(one["confirmed_silences"]),
            cell(one["satellite_silent"]),
            cell(one["indeterminate"]),
            rate(one["indeterminate_of_silences"]),
            rate(one["indeterminate_of_labelled"]),
        )
        for one in found
    ]
    return [
        "### Silent satellites",
        "",
        "A confirmed-listening pass that received nothing is a miss only if the"
        " satellite was heard elsewhere; one that was silent network-wide is"
        " excluded from yield scoring, and one the archive cannot decide is"
        " indeterminate (`EVALUATION.md` §5).",
        "",
        *table(
            (
                "Population",
                "Labelled",
                "Confirmed silences",
                "Silent, excluded",
                "Indeterminate",
                "Of silences",
                "Of labelled",
            ),
            body,
        ),
        "",
    ]
