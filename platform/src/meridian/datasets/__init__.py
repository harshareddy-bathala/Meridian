"""Dataset snapshots, their labels, and the hashing both are named by.

Stage 15 of docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md. Prediction and evaluation
read an immutable snapshot, never the live tables, so that every published
number can be regenerated from a snapshot, a configuration and a seed
(``CLAUDE.md`` rule 8).

Two steps, and the line between them is the stage's argument (D-143):

* **export** reads the database once, inside one repeatable-read transaction,
  and writes a sealed raw snapshot;
* **label** turns a raw snapshot and a labelling configuration into an
  evaluation dataset, with no database, no clock and no network — so the same
  two inputs always give the same hash.

Stage 30's evidence dataset shares this package's manifest and hashing rather
than defining its own (``ARCHITECTURE.md``, ``platform/datasets``).

Reference: docs/DECISIONS.md D-143 to D-147.
"""
