"""Dataset snapshots, their labels, their selection, and the hashing all are named by.

Stages 15 and 16 of docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md. Prediction and evaluation
read an immutable snapshot, never the live tables, so that every published
number can be regenerated from a snapshot, a configuration and a seed
(``CLAUDE.md`` rule 8).

Two steps, and the line between them is the stage's argument (D-143):

* **export** reads the database once, inside one repeatable-read transaction,
  and writes a sealed raw snapshot;
* **label** turns a raw snapshot and a labelling configuration into an
  evaluation dataset, with no database, no clock and no network — so the same
  two inputs always give the same hash. Every evaluation dataset also says
  how its passes were selected — completeness per station-day, a propensity
  per eligible pass, and the weights' diagnostics — so no result drawn from
  one can be stated without them (Stage 16).

Stage 30's evidence dataset shares this package's manifest and hashing rather
than defining its own (``ARCHITECTURE.md``, ``platform/datasets``).

Reference: docs/DECISIONS.md D-143 to D-154.
"""
