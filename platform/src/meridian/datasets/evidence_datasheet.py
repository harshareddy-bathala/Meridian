"""What an evidence package says about itself, for people who did not build it.

Two files of every package, rendered from fixed templates and the package's own
facts, so they are as regenerable as its rows (rule 8):

* ``datasheet.md`` — what a row is, each field, what was left out and why, the
  populations and counts, how to regenerate the package, and what it does not
  claim;
* ``licence.txt`` — the package's licence, CC BY 4.0 (D-313), and how to credit
  it.

Nothing here reads a clock, a path or the machine: a datasheet that named the
code version or where it was built would differ between two regenerations of
the same package. Those are in the manifest's environment, which is not
hashed.

Reference: docs/DECISIONS.md D-104, D-310 to D-313; CLAUDE.md rule 8.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

__all__ = ["LICENCE", "LICENCE_ID", "datasheet"]

LICENCE_ID = "CC-BY-4.0"

LICENCE = b"""\
Meridian evidence dataset

This dataset is licensed under the Creative Commons Attribution 4.0
International licence (CC BY 4.0).

  https://creativecommons.org/licenses/by/4.0/

You may share and adapt it for any purpose, including commercially, provided
you give appropriate credit, link to the licence, and say whether you changed
it. Credit it as:

  Meridian evidence dataset, content sha256 <the hash in manifest.json>,
  https://github.com/harshareddy-bathala/Meridian

It holds only Meridian's own receptions and the conclusions Meridian drew from
them. No record of any archive or other ingested source is included; element
sets are referenced by identifier, epoch, source and hash, and their lines are
not republished.

There is no warranty. A verdict or a diagnosis is what Meridian concluded, not
a fact about the sky.
"""
"""The package's ``licence.txt``, the same in every package (D-313)."""


def datasheet(  # noqa: PLR0913 — one package's facts, each by name
    *,
    snapshot_sha256: bytes,
    since: datetime,
    as_of: datetime,
    schema_revision: str,
    transformation_version: str,
    seed: int,
    counts: Mapping[str, int],
) -> bytes:
    """The package's ``datasheet.md``, from its own facts alone."""
    populations = "\n".join(
        f"| {name} | `{_file(name)}` | {count} |" for name, count in counts.items()
    )
    return f"""\
# Meridian evidence dataset

Receptions made by stations of the Meridian network, each with the verdict and
the diagnosis Meridian concluded, and enough provenance to check them.

## This package

| | |
|---|---|
| raw snapshot | `{snapshot_sha256.hex()}` |
| receptions from | {_instant(since)} |
| snapshot taken | {_instant(as_of)} |
| schema revision | {schema_revision} |
| transformation | {transformation_version} |
| seed | {seed} |
| licence | {LICENCE_ID}, see `licence.txt` |

| population | file | receptions |
|---|---|---|
{populations}

**Measured and simulated receptions are never in one file.** A simulated
reception was made by a virtual station whose outcomes are drawn from a model;
it is not evidence about the sky. Simulated receptions are in a package only
when its configuration asked for them by name (`config.toml`).

## A row

One JSON object per line, keys sorted, ordered by `assignment_id`. Instants are
UTC to the millisecond; hashes are lowercase hex.

A row is one **reception**: a pass a station was assigned and held, whose
window had closed when the snapshot was taken. A held pass with no report is a
reception that returned nothing, and is included. A declined, revoked or
skipped assignment is not a reception, and is not.

- `assignment_id`, `pass_id`, `station_id`, `satellite_id` — what was received,
  by whom.
- `window_start`, `window_end`, `centre_freq_hz`, `mode` — what the station was
  asked to do.
- `listening_confirmed` — whether the station's own heartbeats confirmed it was
  listening, on that frequency, for that satellite, in that window. A pass it
  was not confirmed listening for is not a miss.
- `pass` — the predicted geometry: acquisition and loss of signal, maximum
  elevation, and the azimuths at each end.
- `element_set` — the orbital elements the pass was predicted from, by `id`,
  `epoch`, `source` and `content_sha256`.
- `observation` — the station's current report: outcome, signal, first
  detection, peak SNR, noise floor and gain, decoder and frames, SNR and
  Doppler samples, and its `content_sha256`. `null` when there was no report.
- `lineage` — every revision of the report, oldest first, each with its
  `content_sha256`. A station may correct a report; the last revision is
  current.
- `verdicts` — every calibrated probability that the current revision is
  usable, one per model `method`.
- `diagnoses` — for a lost or partial reception, the cause Meridian named, or
  `undetermined`, with every candidate cause it tested and the evidence it
  read, per `method` and configuration.
- `products` — the decoded products of the current revision, by `kind`,
  `sha256` and `size_bytes`. The products themselves are not in the package.
- `simulated` — whether a virtual station made it.

**Left out, on purpose:** station coordinates, which Meridian holds more
precisely than any station publishes; station names and their own free-text
notes; where products are stored; and an element set's lines, which belong to
the source they were taken from.

## Regenerating it

The package is a pure function of the raw snapshot named above, the
configuration in `config.toml`, and the seed. With the snapshot,

    meridian dataset verify <this directory> --snapshot <the snapshot>

rebuilds it and says whether its content hash is identical. The export draws
nothing at random; the seed is recorded so that the package names all three of
its inputs.

## What it does not claim

That any verdict or diagnosis is correct. The package is exactly what Meridian
concluded, so that anyone can check it, not a set of labels known to be true.
""".encode()


def _file(population: str) -> str:
    if population == "measured":
        return "receptions.jsonl"
    return "receptions_simulated.jsonl"


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
