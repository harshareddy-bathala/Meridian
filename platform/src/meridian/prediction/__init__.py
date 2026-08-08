"""Feature extraction, yield model, horizon inference, calibration.

**Phase 2. Stub only** — CLAUDE.md (repository root) says not to scaffold this
beyond an empty module with a documented interface.

When implemented, this module provides:

* ``P(decode | station, pass)`` for a candidate pass;
* the learned per-azimuth horizon profile for a station;
* calibration metrics — reliability diagram, Brier score against a base rate,
  and calibration by segment.

Two constraints fixed before any code lands here, from docs/EVALUATION.md:

* **All four ablation configurations are selectable by config flag** — A elevation
  only, B elevation + priority, C our features only, D all combined. SC-1 is
  measured as D − B, so B must be runnable as a first-class configuration and not
  reconstructed afterwards.
* **Cold start is a functional requirement.** A newly registered station has no
  history and the model must degrade to geometry-only prediction along an explicit
  tested fallback path, not by accident of missing features.

This module knows nothing about MSP or HTTP.
"""
