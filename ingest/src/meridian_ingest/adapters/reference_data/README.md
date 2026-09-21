# The reference archive's fixtures

**Everything in this directory is invented.** No byte of it is a recording of
any real archive, any real ground station, or any real reception. The station
names, coordinates, identifiers, elevations and frame counts were written for
this purpose and describe nothing that happened.

That is the point. A recorded fixture from a real source would be that source's
data committed to a public repository, which is exactly the redistribution
question D-136 leaves open — and settling it by accident, in a commit about test
plumbing, is not a decision (D-142). The real source is adopted when its licence
and terms are recorded (D-134), and `fetch` refuses to open a socket before its
attribution entry exists.

## Why these files ship inside the package

The reference adapter is not a test double. It is a registered source, and
`meridian-ingest` fetches from it the same way it will fetch from a real one —
so that the stage's completion gate can be demonstrated end to end by anyone who
installs the distribution, including someone who was not in the room and has no
checkout of this repository.

The URLs the adapter plans are under `.invalid`, a reserved suffix that never
resolves. Even wiring a real retriever underneath this adapter reaches nothing.

## What each file is here to exercise

| File | What it carries |
| --- | --- |
| `receptions-2026-07.json` | A station with a location and a declared capability, and one with a location only. A reception of an object the archive could not resolve, kept as the name it gave. |
| `receptions-2026-08.json` | The July station, described identically, so the load finds the row it already has. The same *other* station with **moved coordinates**, so it becomes a second row and July's denominator stays explainable. A station with no location at all. A result string the mapping does not know, and a reception with no station named. |
| `coverage-2026-08.png` | A 2×2 greyscale tile. It exists so that "a normaliser refuses a tile" (D-133) is exercised against a real artefact rather than asserted about a hypothetical one. |

The two JSON files are `reference-archive/1`: a `schema` string, the month it
`covers`, its `stations`, and its `receptions`. The format is ours and is
deliberately unlike any real archive's, so nobody mistakes the normaliser for
one that would work against a named source.

Reference: `docs/DECISIONS.md` D-133, D-134, D-136, D-142; `ATTRIBUTION.md`.
