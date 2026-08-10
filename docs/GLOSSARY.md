# Glossary

Terms used across the specification documents, the code and the report. Written down because the project spans orbital mechanics, radio, distributed systems and machine learning, and no reader arrives fluent in all four.

---

## Orbits and passes

**Pass** — a single period during which a satellite is above a station's horizon. Eight to fifteen minutes for a low Earth orbit satellite, a few times a day. A pass that is missed does not come back.

**AOS — acquisition of signal** — the moment a satellite becomes receivable, at the start of a pass. Predicted AOS versus actual first detection is this project's primary measurement of orbital data quality.

**LOS — loss of signal** — the end of a pass.

**Elevation** — angle above the horizon, 0° to 90°. The strongest single predictor of whether reception succeeds, and an input to our model rather than a competing baseline.

**Azimuth** — compass bearing, 0° to 360°, measured clockwise from north. Matters because obstructions and interference are directional.

**Maximum elevation** — the highest elevation reached during a pass. The number existing schedulers rank on, and mostly the only one they use.

**LEO — low Earth orbit** — roughly 160–2000 km altitude. Where the satellites we receive live, and why passes are short.

**Element set** — a small published file of orbital parameters from which a satellite's position is predicted. Commonly called a TLE (two-line element set). Free, public, regularly updated, and **distributed with no stated uncertainty** — which is the gap this project measures.

**Epoch** — the timestamp an element set describes. Prediction error grows with the age of the element set relative to its epoch, so **element-set age is a first-class feature everywhere in this system**.

**SGP4** — the standard model that turns an element set into a position. We use the `sgp4` and `skyfield` libraries and never reimplement it; a hand-written version would be slower and subtly wrong.

**Propagation** — computing where a satellite will be at a given time. Here it always means orbital propagation, never radio propagation.

**Doppler shift** — the frequency offset caused by the satellite's motion toward or away from the station. Sweeps across a pass in a characteristic S-curve, which is both a confirmation the right object was received and — as a stretch result — a route to recovering orbital state.

---

## Coordinate frames

Where silent bugs live. These are not interchangeable, and converting between them is done deliberately and tested against known ground-truth passes.

**TEME — true equator, mean equinox** — what SGP4 outputs. Earth-centred, and **not** rotating with the Earth.

**ECEF — Earth-centred, Earth-fixed** — Earth-centred and rotating with the planet, so a fixed point on the ground has constant coordinates.

**Topocentric** — relative to an observer on the surface. Azimuth, elevation and range as a station actually experiences them. The last conversion in the chain.

---

## Radio and the station

**SDR — software-defined radio** — a receiver that digitises the signal and does the demodulation in software. What makes a general-purpose station possible.

**LRPT — low rate picture transmission** — the digital weather imagery downlink from the Meteor-M series, near 137 MHz. **Our primary reception target.** The older analogue APT service is no longer operating, so guidance written before 2025 is out of date on this point.

**APT — automatic picture transmission** — the older analogue 137 MHz weather imagery service, carried by the NOAA POES satellites. Named here only so that pre-2025 tutorials referring to it can be recognised as out of date; it is not a reception target.

**SNR — signal-to-noise ratio** — signal power against background noise, in decibels. Recorded per observation as `peak_snr_db`. The single most useful measured quality figure for a pass, and a feature in the yield model.

**VHF / UHF** — the two bands the station works in: roughly 137 MHz and 435 MHz respectively.

**QFH — quadrifilar helix** — a fixed omnidirectional antenna suited to 137 MHz weather satellites. No moving parts, sees the whole sky.

**Yagi** — a directional antenna. More gain, but must be pointed, which is why the rotator exists.

**Rotator** — the motorised mount that points a directional antenna. Ours is built in the college workshop and driven by an Arduino over the network.

**Slew** — moving the antenna from one bearing to another. Takes time, so the scheduler must leave room for it between passes; a schedule that ignores slew time is not executable.

**LNA — low-noise amplifier** — amplifies the signal at the mast head, before cable losses degrade it. Position in the chain matters more than gain.

**Noise floor** — the background RF level. Varies by direction and by hour, which is why the interference profile is per azimuth and per hour rather than a single number.

**Horizon profile** — the *measured* usable elevation floor per azimuth bin, inferred from outcome history. Distinct from a station's **declared** `min_elevation_deg`, which is what its operator claims. The platform learns the real one and may override the declared value downward.

---

## Protocol and platform

**MSP — Meridian Station Protocol** — the open protocol stations implement to join the network. Four HTTP endpoints, deliberately implementable on a microcontroller.

**Station** — an independently operated receiving installation with a stable identity, a fixed location and declared capabilities.

**Capability** — what a station can receive: frequency range, modes, polarisation, whether it can track, usable elevation floor. A station may have several.

**Assignment** — an instruction to attempt reception of one satellite over one time window.

**Observation** — the report of an attempt, **including attempts that produced nothing**. A null result is data.

**Heartbeat** — a station's periodic statement of liveness and current state. Carries the `listening` block. The period is not fixed by the protocol: the platform assigns it at registration as `heartbeat_interval_s`, currently 30.

**The `listening` block** — the field asserting that a station was tuned to a specific frequency for a specific target at a specific time. It is what makes *heard nothing* distinguishable from *was not listening*, and therefore what makes every reliability metric in this project mean anything.

**Simulated station** — a virtual station speaking real MSP over the real network to the real platform. Not a mock. Labelled as simulated at every layer, and never aggregated with measured data in any reported figure.

**NTP — network time protocol** — how a station synchronises its clock. MSP requires it, but does not trust it: a station also estimates its own offset against `GET /msp/v0/time` and reports that offset with an uncertainty, because a station whose NTP has silently failed is exactly the case the estimate exists to catch.

---

## Evaluation

**Yield** — the probability that an attempted reception produces a successful decode. What the prediction model estimates.

**Calibration** — whether stated probabilities match observed frequencies. A model saying "70% likely" should be right about 70% of the time. **More important here than raw accuracy**, because the scheduler multiplies probabilities by value.

**Brier score** — the standard measure of probabilistic forecast quality. Lower is better. Reported against a base-rate predictor, never in isolation.

**Reliability diagram** — predicted probability against observed frequency, binned. The visual form of calibration.

**Ablation** — evaluating configurations A/B/C/D to isolate what our features actually contribute. The contribution is measured as **D − B**, not D − A, which would take credit for priority weighting that already exists.

**Selection bias** — an observation archive contains only passes someone already chose to observe, so it is not a random sample. **The most serious methodological risk in this project.**

**Completeness ratio** — observed passes ÷ geometrically available passes, per station-day. The denominator is computed by us, not taken from the archive. Reported alongside every archive-derived result.

**IPW — inverse-propensity weighting** — reweighting outcomes by the estimated probability that a pass was observed, to correct for selection bias where completeness is partial.

**Temporal split** — training on earlier data and testing on later. The only split permitted here; a random split leaks future information and flatters the model.

**SLI / SLO** — service level indicator (what is measured) and objective (the target it must meet).

**Irrecoverable loss budget** — how much permanently lost reception is acceptable before something must be fixed. Resembles a standard error budget, but the accounting differs because the work cannot be retried.

**Absence is not a miss** — a station that reported nothing counts as having missed a pass only if its heartbeat confirms it was listening, on the right frequency, for the right target. Load-bearing for every reliability metric in the system.

---

## Conventional short names in code

Not domain terms, but recorded so the "no abbreviations outside this glossary" rule in `CLAUDE.local.md` §4 is honest in both directions. These are permitted as local variable names only, never as part of a public name.

`conn` connection · `cur` cursor · `exc` a caught exception · `argv` / `args` command-line arguments · `raw` an unparsed string straight from the environment

Unit and frame suffixes carried by names — `_hz`, `_deg`, `_m`, `_km`, `_s`, `_db`, `lat`, `lon`, `alt`, `freq` — are required by §4 rather than exempted by it, and are listed here only so a reader knows they are deliberate.
