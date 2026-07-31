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

**Why this is the primary method:** it needs only an accurate clock. It is robust to receiver oscillator drift, needs no frequency calibration, and directly demonstrates the effect we claim. It works on every observation, including from microcontroller stations.

Requirement: stations synchronise time via NTP and report clock offset in heartbeats. Timing error smaller than the reported clock uncertainty is discarded.

### 6.2 Secondary — Doppler orbit determination

Recovering orbital state from the observed frequency curve.

**Stated honestly:** consumer receiver oscillators drift with temperature, which corrupts frequency measurement directly; a single pass from a single station is a poorly conditioned estimation problem; convergence needs multiple passes.

This is a **stretch result**. No project claim depends on it. If it works, it is a stronger and more interesting validation of the uncertainty model. If it does not, SC-3 is satisfied by timing error alone.

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
- **Never aggregate simulated with measured data** in any reported figure.
- **State sample size and confidence intervals** on every number.
- **State the completeness ratio** on every archive-derived result.
- If SC-1 is not met, §9 of the project document applies: a calibrated model is a result in its own right, and an honestly reported small gain beats a loosely claimed large one.
