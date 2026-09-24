"""Feature extraction, yield model, horizon inference, calibration.

**Being built by Stage 17** of docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md, under
decisions D-155 to D-163. In place so far:

* ``feature_rows`` — the raw snapshot's geometry, tracks and bands, typed;
* ``history`` — each station's settled outcomes, answering only for the past
  (D-157);
* ``geometry`` — angles on the circle, and where a pass went;
* ``profiles`` — the learned environment: horizon, interference, timing error
  and element-set divergence (D-159);
* ``features`` — every feature of each labelled pass, in its group;
* ``model_config`` and ``configurations`` — A to D from one key, and the
  cold-start route (D-160, D-161);
* ``examples`` — one population's examples, simulated passes counted apart
  (D-156).

When complete, this module provides:

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
