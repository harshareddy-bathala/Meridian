"""The data section: what the report rests on, and what was left out of it.

The roadmap's data report is seven things, each read from the two verified
directories and nothing else:

* **provenance** and **the snapshot manifest** — both hashes, the window, the
  schema, every archive source with the licence it arrived under, and every
  count the export stated;
* **outcome labels** and **exclusion reasons** — every label and every reason,
  zeros included, measured and simulated apart (D-146, D-078);
* **the completeness distribution** and the weighting beside it, per
  population (``EVALUATION.md`` §4, D-151 to D-154);
* **the indeterminate fraction** and **silent-satellite exclusions**
  (``EVALUATION.md`` §5), each with its interval.

Rows, not text. Each is one line of ``data.jsonl``, and ``report.md`` is
rendered from them, so every number the report prints is in a hashed file.
Measured and simulated counts sit in separate fields and are never summed
(rule 5).

Reference: docs/DECISIONS.md D-078, D-146, D-151 to D-154, D-235.
"""

from __future__ import annotations

from collections.abc import Mapping

from meridian.datasets.labels import EXCLUSIONS, LABELS
from meridian.datasets.manifest import Manifest, content_sha256
from meridian.datasets.publish import SnapshotDirectory
from meridian.datasets.result_reader import read_results
from meridian.datasets.weighting import wilson

__all__ = ["DATA_FILE", "POPULATIONS", "data_rows"]

DATA_FILE = "data.jsonl"

POPULATIONS = ("measured", "simulated")
"""The two populations a count is kept apart in, never summed (rule 5)."""

_SILENCES = ("confirmed_miss", "satellite_silent", "satellite_state_indeterminate")
"""The labels a confirmed-listening pass with nothing received can take (§5)."""


def data_rows(
    raw: SnapshotDirectory, dataset: SnapshotDirectory
) -> list[dict[str, object]]:
    """Every row of the data section.

    Args:
        raw: The raw snapshot, verified.
        dataset: The evaluation dataset labelled from it, verified.

    Returns:
        Rows in a fixed order: inputs, sources, counts, labels, exclusions,
        completeness and weighting, then silences.
    """
    counts = dataset.manifest.counts
    return [
        *_inputs(raw.manifest, dataset.manifest),
        *_sources(raw.manifest),
        *_raw_counts(raw.manifest.counts),
        *(_split("label", name, counts, f"labels.{name}") for name in LABELS),
        *(_split("exclusion", name, counts, f"excluded.{name}") for name in EXCLUSIONS),
        *_selection(dataset),
        *(_silence(population, counts) for population in POPULATIONS),
    ]


def _inputs(raw: Manifest, dataset: Manifest) -> list[dict[str, object]]:
    """The two directories this section read, each by its hash."""
    return [
        {
            "row": "input",
            "name": "raw_snapshot",
            "sha256": content_sha256(raw),
            "schema_revision": raw.schema_revision,
            "since": raw.since,
            "as_of": raw.as_of,
        },
        {
            "row": "input",
            "name": "evaluation_dataset",
            "sha256": content_sha256(dataset),
            "transformation_version": dataset.transformation_version,
            "config_sha256": dataset.config_sha256,
        },
    ]


def _sources(raw: Manifest) -> list[dict[str, object]]:
    """Every archive source, with the terms its rows arrived under (D-134)."""
    return [
        {
            "row": "source",
            "source_id": one.source_id,
            "licence": one.licence,
            "terms_url": one.terms_url,
            "attribution_entry": one.attribution_entry,
            "records": one.records,
        }
        for one in sorted(raw.sources, key=lambda one: one.source_id)
    ]


def _raw_counts(counts: Mapping[str, int]) -> list[dict[str, object]]:
    """The export's counts, one row per table, populations side by side.

    A count the export states for both populations becomes one row with both;
    any other count is kept whole under ``total``, because a count with no
    population is not ours to split.
    """
    split = {
        name.rsplit(".", 1)[0]
        for name in counts
        if name.rsplit(".", 1)[-1] in POPULATIONS
    }
    rows: list[dict[str, object]] = [
        _split("count", name, counts, name) for name in sorted(split)
    ]
    rows.extend(
        {"row": "count", "name": name, "total": counts[name]}
        for name in sorted(counts)
        if name.rsplit(".", 1)[0] not in split
    )
    return rows


def _split(
    row: str, name: str, counts: Mapping[str, int], prefix: str
) -> dict[str, object]:
    """One named count, measured and simulated in their own fields."""
    return {"row": row, "name": name} | {
        population: counts.get(f"{prefix}.{population}", 0)
        for population in POPULATIONS
    }


def _selection(dataset: SnapshotDirectory) -> list[dict[str, object]]:
    """Each population's completeness and weighting, as the dataset states them."""
    rows: list[dict[str, object]] = []
    for result in read_results(dataset):
        rows.append(
            {"row": "completeness", "population": result.population}
            | result.completeness.parameters()
        )
        rows.append(
            {"row": "weighting", "population": result.population}
            | result.weighting.parameters()
        )
    return rows


def _silence(population: str, counts: Mapping[str, int]) -> dict[str, object]:
    """Confirmed silences, the ones excluded as silent, and the undecided share.

    Two shares, because §5 asks for the fraction of the dataset and a reader
    also needs the fraction of the silences it could have been: the first says
    how much it matters, the second how often the archive could not decide.
    """
    silent, excluded, undecided = (
        counts.get(f"labels.{name}.{population}", 0) for name in _SILENCES
    )
    labelled = sum(counts.get(f"labels.{name}.{population}", 0) for name in LABELS)
    confirmed = silent + excluded + undecided
    return {
        "row": "silence",
        "population": population,
        "labelled": labelled,
        "confirmed_silences": confirmed,
        "satellite_silent": excluded,
        "indeterminate": undecided,
        "indeterminate_of_silences": _share(undecided, confirmed),
        "indeterminate_of_labelled": _share(undecided, labelled),
    }


def _share(part: int, whole: int) -> dict[str, float] | None:
    """A share with its Wilson interval, or None when there is nothing to share."""
    if whole == 0:
        return None
    rate = wilson(part / whole, whole)
    return {"estimate": rate.estimate, "low": rate.low, "high": rate.high, "n": rate.n}
