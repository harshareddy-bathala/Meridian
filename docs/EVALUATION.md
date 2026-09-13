# Evaluation Methodology

How every claim in this project is tested, and the threats to validity, stated before anyone raises them.

**Read this before writing model, scheduler or analysis code.** It constrains the implementation.

---

## 1. The claims

| ID | Claim | Target |
|---|---|---|
| SC-1 | Yield-predicting scheduling beats existing practice | ≥ 20% more decoded frames per station-hour |
| SC-2 | Predictions are calibrated | ≥ 25% Brier score reduction vs base rate |
| SC-3 | Orbital timing uncertainty is correctly estimated | ≥ 68% of passes within stated 1σ |
| SC-4 | Our station is reliable | ≥ 90% pass capture rate over 30 days |
| SC-5 | Failures are detected quickly | ≤ 90 s time to detect |
| SC-6 | A station is registered, online and publicly visible | Achieved / not |

SC-6 is the only criterion that is pass/fail rather than measured, and it is effectively the Phase 1 exit criterion. It is listed here because a methodology document that omits the easiest criterion to verify — and the most visible to an examiner — has the omission the wrong way round.

### Proposed — to agree with the team

Four criteria for the post-reception layer (modules 13–17, D-092). **Every target below is proposed — to agree with the team**, and none is a commitment until that agreement is recorded. SC-1 to SC-6 are unchanged. The method is §11.

| ID | Claim | Target |
|---|---|---|
| SC-7 | The reception verdict is calibrated | ≥ 40% Brier score reduction vs base rate, on held-out measured receptions — *proposed, to agree with the team* |
| SC-8 | Loss diagnosis names the injected cause | ≥ 80% recall for each of the five causes, and ≤ 5% of diagnoses naming a wrong cause, on simulated faults — *proposed, to agree with the team* |
| SC-9 | The health watch warns before reception fails | a warning before the first failed reception in ≥ 90% of injected degradation runs, and ≤ 1 false alarm per station per 30 days on fault-free runs — *proposed, to agree with the team* |
| SC-10 | The evidence dataset is regenerable | identical content hash from the same snapshot, configuration and seed — Achieved / not — *proposed, to agree with the team* |

Owner reports (module 16) have no numeric criterion; they are proven by working end to end in the demonstration (§11.4).

**SC-7's target is higher than SC-2's on purpose.** SC-2 forecasts a pass that has not happened; the verdict reads the evidence of one that has, so a verdict that only matched SC-2's margin would be using that evidence badly. **SC-7 also cannot be measured until D-103 settles what "usable" means** — a label built from the verdict's own inputs would make any target meaningless. **SC-8 and SC-9 are simulated results** and are labelled so wherever they appear (§11.2, §11.3, D-102).

---

## 2. The prediction model is hybrid by design

Elevation and operator priority are **inputs to our model, not competing baselines**.

| Signal | Source | Role |
|---|---|---|
| Maximum elevation | Geometry | Strongest single feature |
| Azimuth profile | Geometry | Interacts with obstruction |
| Operator priority | Human input | Objective weight, not a prediction |
| **Learned horizon profile** | **Ours** | Per-azimuth usable elevation floor, inferred from outcome history |
| **Element-set age** | **Ours** | Proxy for prediction error |
| **Interference profile** | **Ours** | Noise floor by azimuth and hour |
| **Station health history** | **Ours** | Recent failure rate at this station |
| **Per-satellite history** | **Ours** | This satellite's observed decode rate |

**Cold start is a functional requirement.** A newly registered station has no history. The model must degrade gracefully to geometry-only prediction and recover as data accumulates. Implement this as an explicit fallback path, tested, not as an accident of missing features.

---

## 3. Ablation — four configurations, all reported

A combined model cannot show what our contribution added. The model layer **must** support selecting a feature configuration by config flag.

| Config | Features | Question |
|---|---|---|
| **A** | Elevation only | Naive baseline |
| **B** | Elevation + priority weighting | What existing practice achieves |
| **C** | Our features only | Do our signals carry independent information? |
| **D** | All combined | The shipped system |

**SC-1 is measured as D − B.** Not D − A, which would flatter us by taking credit for priority weighting that already exists.

C matters independently: if our features carry no signal on their own, that is a finding worth reporting, and it changes what we claim.

A hybrid is not automatically better. Adding a weak or noisy feature can degrade a model. The ablation is how we find out rather than assume.

---

## 4. Threat to validity: selection bias

**This is the most serious methodological risk in the project.**

An observation archive contains only passes that someone already decided to observe. Passes nobody scheduled have no recorded outcome. Therefore:

1. Training data is conditioned on a prior scheduling decision — not a random sample of available passes.
2. Retrospective scheduler comparison needs counterfactual outcomes for passes never observed. Those do not exist.

Left unaddressed, "we measured a 20% improvement" does not survive review.

### 4.1 Mitigation one — near-complete windows

For each station-day, compute:

```
completeness = observed_passes / geometrically_available_passes
```

where the denominator is computed by us from element sets and the station's declared capability, not taken from the archive.

Restrict primary SC-1 evaluation to station-days above a completeness threshold (initially 0.8, tuned and reported). Where nearly every available pass was observed, the counterfactual is nearly complete.

**Report the completeness distribution alongside every result.** A result on 0.9-complete windows is much stronger than one on 0.3-complete windows, and hiding the difference is how projects fail review.

### 4.2 Mitigation two — off-policy evaluation

Where completeness is partial, apply inverse-propensity weighting. Fit a propensity model estimating P(observed | pass features) against the historical scheduling policy, then weight outcomes by the inverse.

Report effective sample size. If it collapses, the estimate is unreliable and must be labelled as such rather than quoted.

### 4.3 Mitigation three — prospective evaluation

Once our own station is live, we control the policy. Alternate scheduling configurations on a randomised schedule and compare outcomes directly.

This is the cleanest evidence available and it has no selection bias, because we assign the policy. Sample size will be modest — a single station generating a few tens of passes per day — so report confidence intervals honestly and do not over-claim from a small n.

**This is the strongest argument for building the station at all.**

---

## 5. Confounding: silent satellites

Some satellites transmit intermittently or are dormant. A pass producing nothing may mean a bad prediction, or a transmitter that was off.

**Mitigation.** Cross-check each null observation against contemporaneous observations of the same satellite elsewhere in the archive. Where an object was demonstrably silent network-wide during the window, exclude the observation from yield scoring and report the exclusion count separately.

Where the archive is too sparse to determine this, mark the observation `indeterminate` and report what fraction of the dataset that represents.

---

## 6. Measuring orbital data quality

### 6.1 Primary — pass timing error

For every observation with a detected signal:

```
timing_error = actual_first_detection - predicted_acquisition_of_signal
```

Regress the magnitude of this error against element-set age at the time of the pass, segmented by orbital regime.

**The two terms are on different clocks and must be reconciled before they are subtracted.** `actual_first_detection` is `observations.first_detection_at`, stamped by the *station*. `predicted_acquisition_of_signal` is computed by the *platform*. The convention is fixed by `docs/DECISIONS.md` D-025:

```
clock_offset = platform clock − station clock

corrected_first_detection = first_detection_at + clock_offset_s
timing_error = corrected_first_detection − predicted_acquisition_of_signal
```

A station whose clock runs fast reports a negative `clock_offset_s`, so the correction moves its timestamp earlier. Getting this sign wrong does not raise anything: every number keeps a plausible magnitude and the published relationship between timing error and element-set age simply inverts. That is why the convention is named in one place and referenced from the others rather than restated.

**Why this is the primary method:** it needs only an accurate clock. It is robust to receiver oscillator drift, needs no frequency calibration, and directly demonstrates the effect we claim. It works on every observation, including from microcontroller stations.

Requirement: stations synchronise time via NTP and report `clock_offset_s` and `clock_uncertainty_s` in heartbeats (MSP §4.2). Timing error smaller than the reported clock uncertainty is discarded. An observation whose station reported `clock_offset_s: null` — meaning unknown, never `0.0` — is excluded from this measurement rather than assumed synchronised.

### 6.2 Secondary — Doppler orbit determination

Recovering orbital state from the observed frequency curve.

**Stated honestly:** consumer receiver oscillators drift with temperature, which corrupts frequency measurement directly; a single pass from a single station is a poorly conditioned estimation problem; convergence needs multiple passes.

This is a **stretch result**. No project claim depends on it. If it works, it is a stronger and more interesting validation of the uncertainty model. If it does not, SC-3 is satisfied by timing error alone.

### 6.3 Risk to the primary method

**SC-3 is unchanged. The method in §6.1 carries a risk, and is tested before SC-3 relies on it** (`docs/DECISIONS.md` D-097).

The effect being measured is small. Public element sets are roughly 1 km accurate at epoch and drift about 1–2 km per day, which at 7.5 km/s along track is about 0.13–0.27 s of timing per day of age.

`first_detection_at` can vary by far more than that for reasons unrelated to the orbit: the horizon and obstructions in the acquisition direction, link margin, and the detector's threshold. Near the horizon an overhead pass at 850 km rises about one degree every 14 seconds, so a detection elevation that varies by two degrees between passes moves first detection by roughly half a minute. If that spread dominates, timing error regressed against element-set age measures the station's horizon rather than the element set.

**Open question, tested before it is answered.**

1. On archive data, take receptions whose element set was under a day old — where the orbital contribution is known to be sub-second — and measure the spread of first-detection offset.
2. If that spread exceeds the effect SC-3 exists to detect, §6.1 is not fit as written, and that finding is reported rather than worked around.
3. A possible alternative is **timing from the Doppler curve.** Range rate crosses zero at closest approach, mid-pass and high in the sky, away from the horizon and the acquisition link margin. A constant receiver frequency offset moves the literal zero crossing but not the curve's steepest point, so that is the form to test. §6.2's oscillator-drift objection still applies.

---

## 7. Calibration over accuracy

A model that says "70% likely" should be right about 70% of the time. That is more useful for scheduling than raw accuracy, because the scheduler multiplies probabilities by value.

Every model ships with:

- A **reliability diagram** — predicted probability against observed frequency, binned
- A **Brier score**, compared against a base-rate predictor (SC-2)
- **Calibration by segment** — per band, per station, per element-set-age bucket, because aggregate calibration can hide segment-level failure

---

## 8. Data splits

**Temporal splits only.** Train on earlier data, test on later.

Random splits leak future information: the same satellite, the same station and near-identical conditions appear on both sides, and the model looks far better than it is. Any use of `shuffle=True` on observation data is a bug.

Report the split boundary date with every result.

---

## 9. Reproducibility

Every reported figure is regenerable from:

1. A dataset snapshot with a content hash
2. A configuration file
3. A random seed

Analysis scripts write these three things into their output alongside the result. A figure that cannot be regenerated is not a result and does not go in the report.

---

## 10. Reporting rules

- **Report what we measure, including nulls.** If the ablation shows no improvement, that is the finding.
- **Never aggregate simulated with measured data** in any reported figure. **And never train or evaluate a model on simulated observations at all** — the simulator draws outcomes from elevation, which is the model's strongest feature, so a model fitted to them rediscovers the generator and scores well for a reason that means nothing. The aggregation rule alone would not catch that: no reported figure would have mixed the two. See `docs/DECISIONS.md` D-078.
- **State sample size and confidence intervals** on every number.
- **State the completeness ratio** on every archive-derived result.
- If SC-1 is not met, §9 of the project document applies: a calibrated model is a result in its own right, and an honestly reported small gain beats a loosely claimed large one.

---

## 11. After reception — the four proofs

How modules 13–17 are proven (D-092). Everything above applies to them — temporal splits (§8), regenerability (§9) and the reporting rules (§10). In particular, **simulated and measured results are never pooled**, and every number carries its sample size and a confidence interval.

**The novelty these proofs support is narrow** (D-096). Other networks already rate receptions, manually and by rule. What is tested here is that Meridian's confidence is *calibrated* and that its cause of loss is *attributed automatically and scored*. No result is written up as something nobody else does.

### 11.1 Reception verdict — calibration (SC-7)

**What it is.** For every reception, a probability that it is usable, computed from decoder statistics, frames decoded against frames expected, `peak_snr_db`, `outcome`, and listening evidence from heartbeats. A calibrated probability, not a hand-weighted score. A reception that received nothing still gets one.

**Method — the discipline of §7.**

- **Label.** Settled by D-103 before any training, and independent of the verdict's inputs. Until then SC-7 is not measured.
- **Data.** Measured receptions only: our station's, and archive receptions where the inputs exist. **Never simulated** — D-078 applies without exception (D-102).
- **Split.** Temporal: train on earlier receptions, calibrate on a later interval, test on the latest untouched interval. Report the boundary dates.
- **Base rate.** The fraction of usable receptions in the training period, predicted for every test reception.
- **Report.** Brier score against the base rate; a reliability diagram; and calibration **by segment** — per station, per band, per data type (image or telemetry), per decoder version, and with and without the decoder statistics D-100 proposes, because a verdict computed from less evidence must still be calibrated.

**Selection bias, and why §4's form of it is limited here.** The verdict is only ever applied to receptions that were scheduled, so training on scheduled receptions matches the population it serves. Archive receptions were scheduled by other policies at other stations, though, so archive and own-station results are separate segments and are not pooled.

**It does not claim** that a low verdict means the data is useless to everyone, or that it predicts anything before a pass — that is SC-2's model.

### 11.2 Loss diagnosis — per-cause confusion matrix (SC-8)

**What it is.** For every failed or partial reception, the most likely cause — satellite silent, station not listening, obstruction, interference, or a timing or clock fault — or *undetermined* when the evidence is insufficient.

**Method.**

- **Ground truth comes from injected faults** with known causes (roadmap Stage 25): gradual signal degradation, a new obstruction, interference and a silent satellite, alongside the existing receiver-down fault for a station not listening and clock drift for timing. The cause is written to the simulator's run record, **never sent over MSP and never stored by the platform**, and is joined to diagnoses only here (D-102).
- **Negative control.** Stage 21's degraded decoder is a failure with no category in the list. The correct diagnosis is *undetermined*, and how often it is reported as something else is reported.
- **Report.** A confusion matrix with injected causes as rows and diagnosed causes, *undetermined* included, as columns; per-cause recall; the fraction naming a wrong cause; and the *undetermined* fraction, which is never folded into either. Several seeds per cause, with the spread between seeds.
- **Real cases** the team can label are reported in their own table, however few, and never added to the simulated matrix.

**It does not claim** real-world diagnostic accuracy. SC-8 shows that evidence of the shape a fault produces is attributed to that fault. Whether real faults produce that shape is what the real cases begin to answer, and the report says so beside the number.

### 11.3 Station health watch — detection delay and false alarms (SC-9)

**What it is.** A comparison of a station's signal strength at each elevation against its own history, warning when the receive chain — antenna, cable, LNA — is degrading, before reception fails.

**Method.**

- **Injected gradual degradation** (Stage 25): a receive-chain loss growing over days, at several rates, after a fault-free history long enough to build a baseline.
- **Detection delay** — time from degradation onset to the warning, and the loss in dB at that moment.
- **Lead** — time from the warning to the first reception the degradation turns into a failure. SC-9 asks that the warning come first.
- **False-alarm rate** — warnings per station per 30 days on fault-free runs.
- **Simulated results**, labelled as such and reported apart from anything measured.

**To verify — a controlled real-station test.** If station 001's LNA is powered through the SDR's bias-tee, switching the bias-tee off removes the LNA's gain on command: a real, known receive-chain fault. It is a step rather than a gradual loss, so it tests that the watch responds to a real shortfall on real data, and it is reported as one measured case, not a rate. Whether the LNA is powered that way is confirmed when the station is built.

**It does not claim** to locate which part of the chain degraded, or to detect a fault that leaves signal strength against elevation unchanged.

### 11.4 Owner reports — end to end in the demonstration

**What it is.** A plain-language message to a station's owner after each pass, and a weekly station summary, rendered from versioned templates with no language model (D-095).

**Proof.** In the demonstration, a decoded pass and a diagnosed loss each produce a delivered report, and the delivered text regenerates to its recorded content hash from the stored results and template version. There is no numeric success criterion, and none is invented.

**It does not claim** that owners act on reports, or any measure of their usefulness.

### 11.5 Evidence dataset — identical hash on regeneration (SC-10)

**What it is.** A package of receptions with verdict, diagnosed cause and full provenance — station, element set, `simulated` flag, content hash — with measured and simulated receptions in separate files (D-101).

**Proof.** Export once. Regenerate from the same snapshot, configuration and seed, **on a machine other than the one that produced it**. The content hashes are identical. This is `CLAUDE.md` rule 8 turned into a test.

**It does not claim** that the labels inside it are correct — only that anyone can reproduce exactly what Meridian concluded, and check it.
