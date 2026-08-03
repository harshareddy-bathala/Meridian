"""Constrained optimisation over candidate passes.

**Phase 2. Stub only.**

When implemented, this module consumes predictions and produces assignments. It
**does not read the observation store directly** (docs/ARCHITECTURE.md rule 1).

It must enforce non-overlap including slew and settling time, per-station
capability limits, and operator priority weights. It also computes the
retrospective oracle schedule for the schedule-efficiency metric.

The reasoning behind each decision is a **first-class output**, written to
``assignments.reason`` at the time the decision is made and never reconstructed
later. docs/PROJECT.md §13 calls the screen that displays it "the entire project".

Phase 1 ships a deliberately naive baseline instead — greedy highest maximum
elevation — which is configuration A/B from docs/EVALUATION.md and exists to be
beaten, not to be the scheduler.
"""
