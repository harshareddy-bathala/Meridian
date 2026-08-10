"""Constrained optimisation over candidate passes.

**Phase 2. Not implemented** — this module is its interface and nothing else,
built by Stage 18 of docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md, after prediction
because it consumes prediction outputs.

When implemented, this module consumes predictions and produces assignments. It
**does not read the observation store directly** (docs/ARCHITECTURE.md rule 1).

It must enforce non-overlap including slew and settling time, per-station
capability limits, and operator priority weights. It also computes the
retrospective oracle schedule for the schedule-efficiency metric.

The reasoning behind each decision is a **first-class output**, written to
``assignments.reason`` at the time the decision is made and never reconstructed
later. docs/PROJECT.md §13 calls the screen that displays it "the entire project".

Two deliberately naive baselines arrive well before it, at Stage 7:
configuration **A**, which ranks candidates by maximum elevation alone, and
configuration **B**, which adds operator priority. They exist to be beaten
rather than to be the scheduler, and B is the one that counts: docs/EVALUATION.md
measures SC-1 as D − B, not D − A, so reporting against the elevation-only
baseline would take credit for priority weighting existing practice already has.
"""
