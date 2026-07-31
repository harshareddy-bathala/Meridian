# Meridian
## A satellite ground station network and control platform

**Project document — problem, design, method, and plan**

Department of Computer Technology · Final Year Project · Team of 3 · Two semesters
Document version 3.0

---

## 1. In one paragraph

Satellites pass overhead for only a few minutes at a time, and a pass that is missed is gone for good. Today, ground stations decide which pass to listen to using little more than geometry — whichever satellite goes highest. That ignores most of what actually determines whether reception works. **Meridian** is a platform that predicts which reception opportunities are worth taking, schedules them, monitors the stations doing the work, and reports honestly on the results. It also defines **MSP**, an open protocol any station can implement to join the network, with a public website showing what the network is hearing in real time. We build one station ourselves — as the network's first member and as the instrument that proves the software works.

**The project is software. The station is how we prove it.**

*Only two things are given proper names: the project (**Meridian**) and the protocol (**MSP**, the Meridian Station Protocol). Everything else is described in plain terms — the platform, the station client, the dashboard, the station.*

---

## 2. The problem

### 2.1 The window does not come back

A satellite in low Earth orbit is visible from a fixed point on the ground for roughly eight to fifteen minutes, a few times a day. During that window it transmits — weather imagery, telemetry, scientific data. When the window closes, the satellite is over the horizon and the transmission is over.

There is no retry. A web server that fails a request can serve it again a second later. A ground station that misses a pass has lost that data permanently.

This is an unusual property, and very little software is designed around it.

### 2.2 The decision is made badly

Given several satellites passing in the same hour and one antenna, something has to choose. In practice the choice is made on orbital geometry alone — pick the pass that reaches the highest elevation, because higher usually means closer and stronger.

"Usually" is doing a lot of work in that sentence. What actually determines whether a pass produces usable data also includes:

- **Obstructions.** A building to the north-west blocks that direction regardless of how high the satellite goes.
- **Interference.** A rooftop in a city has a different noise floor at 8 a.m. than at 8 p.m., and a different one facing the road than facing open ground.
- **Station health.** A station with a stuck rotator or a full disk will accept a booking and produce nothing.
- **The quality of the orbital data itself.**

### 2.3 The accuracy of public orbit data is unstated

Satellite positions are predicted from small published files of orbital parameters. These files are free, public, regularly updated — and **distributed without explicit uncertainty estimates**, which leaves the software consuming them to treat them as exact.

They are not exact, and their accuracy decays as they age. Published validation work has repeatedly shown that predicted position error grows with the age of the element set, and can become significant for small satellites within days.

Precise uncertainty data does exist — organisations with high-accuracy tracking maintain it — but it is not published alongside the element sets that open ground station software actually uses. So the software proceeds as though the numbers were perfect, because nothing better is offered.

The consequences are practical. A pass predicted to start at 15:34 may actually start at 15:33 or 15:36. A directional antenna aimed using stale data points at empty sky. And nobody notices, because nothing measures it.

### 2.4 There is no operational discipline

Together: ground reception is a service with a hard, unforgiving deadline, run without the practices that other deadline-bound services take for granted. No target for how many scheduled receptions should succeed. No distinction between a station that is idle and one that is broken. No measure of how good a schedule was compared with the best available. No budget for how much permanent data loss is acceptable before something must be fixed.

**That absence is what this project addresses.**

---

## 3. Related work

### 3.1 What exists in practice

| Category | What it provides | Representative |
|---|---|---|
| Open ground station networks | Volunteer stations, shared booking, observation archive | SatNOGS (Libre Space Foundation) |
| Low-cost telemetry networks | Microcontroller-class stations for narrowband telemetry | TinyGS |
| Single-station automation | Auto-scheduling and decoding on one machine | r2cloud |
| Decoder suites | Signal to image or telemetry frame | SatDump, gr-satellites |
| Signal processing frameworks | Demodulation building blocks | GNU Radio |
| Hardware control | Antenna pointing, radio control | Hamlib |
| Pass prediction | When a satellite is overhead | Gpredict, and the SGP4 model beneath it |

These solve the **execution** layer — how to point, receive and decode. They do it well, and we use several of them.

### 3.2 Where the gap is

The layer above — deciding what is worth doing, predicting whether it will work, and reporting on how well it went — **remains comparatively unexplored in open-source ground station software.** Scheduling, where automated at all, works from a hand-maintained priority list and selects the highest passes.

Related work does exist and we do not claim otherwise. Satellite range scheduling has been studied as an oversubscribed combinatorial optimisation problem in the operations research and AI planning literature, principally in the context of large government antenna networks. Commercial mission-planning and ground-segment-as-a-service systems address parts of this space too. Neither body of work is available to, nor designed for, the low-cost open stations that make up most of the world's amateur receiving capacity — and neither addresses the uncertainty of the orbital data being scheduled against.

### 3.3 Reading list

*To be verified individually and expanded during Phase 1; each entry confirmed against the actual publication before citation in the final report.*

**Orbital mechanics and propagation**
- Hoots & Roehrich, *Spacetrack Report No. 3: Models for Propagation of NORAD Element Sets*, 1980 — the SGP4 reference
- Vallado, *Fundamentals of Astrodynamics and Applications* — standard text
- Vallado & Crawford, *SGP4 Orbit Determination*, AIAA/AAS 2008
- Kelso, validation studies of SGP4 against precision ephemerides

**Scheduling**
- Barbulescu et al., studies of the Air Force Satellite Control Network range scheduling problem — the canonical oversubscribed scheduling formulation
- Marinelli et al., Lagrangian heuristics for satellite range scheduling under resource constraints

**Prediction quality and evaluation**
- Brier, *Verification of forecasts expressed in terms of probability*, 1950
- Niculescu-Mizil & Caruana, *Predicting Good Probabilities with Supervised Learning*, ICML 2005
- Dudík, Langford & Li, *Doubly Robust Policy Evaluation and Learning*, ICML 2011 — off-policy evaluation

**Operations**
- Beyer et al., *Site Reliability Engineering*, O'Reilly 2016 — service level objectives and error budgets

---

## 4. Design principles

**Software first.** The platform is built and proven before hardware is purchased. Hardware validates software; it cannot rescue it.

**Open by default.** MSP is published and a reference client released. Any station, built by anyone, can join without our permission.

**Modular.** Receivers, antennas and decoders will change. The platform must not care which are attached.

**Measurable.** Every claim carries a number, produced by a stated method, reproducible from a seed and a configuration file. A claim we cannot measure is not made.

**Independent.** Nothing essential lives outside our own code. External services are enrichment, never a dependency.

---

## 5. Proposed solution

### 5.1 What we are building

**A platform.** It knows where satellites are and how confident it should be about that. It predicts how likely each upcoming reception is to succeed, schedules the ones worth taking, watches the stations doing the work, and reports honestly on results.

**A protocol and a network.** MSP defines how a receiving station registers, declares its capabilities, reports that it is alive and what it is currently listening to, receives instructions, and submits what it heard. Any station speaking MSP can join. We publish the specification and a reference client, and the platform hosts the registry, the data and the public site.

**A station.** We build one. It is the network's first member and our measuring instrument.

### 5.2 Our own network

Our station reports to our platform, over our protocol, and appears on our public website. It does not push to any external network as a condition of working.

1. **It makes the project independent.** If every external service disappeared tomorrow, Meridian would still schedule, receive, decode, monitor and report.
2. **It makes the demonstration self-contained.** Everything shown runs on infrastructure we built.
3. **It makes the network real rather than hypothetical.** A published protocol, a reference client, a live registry and a public site mean a second station could join without asking us.

We are honest about size: **the network has one physical station.** Behaviour at scale is demonstrated with fifty simulated stations speaking the real protocol to the real platform. Growth is future work, not a claim.

### 5.3 Two station types, deliberately

The network runs two kinds of station on purpose:

- A **Raspberry Pi** with a software-defined receiver — Linux, full signal processing, tracking antenna.
- A **microcontroller station** (ESP32-class) — no operating system, kilobytes of memory, a single narrowband radio.

Both speak MSP. That is the point. A protocol that only works on the machine it was designed for is not a protocol, and running it across hardware two orders of magnitude apart in capability is how we demonstrate the boundary was drawn correctly.

If the microcontroller carries a LoRa transceiver it also receives satellite telemetry. If it does not, it still joins as a non-receiving station reporting environmental and power telemetry — and still proves the point.

### 5.4 External networks — separate and optional

Registering our station on an existing public network is a **separate, optional requirement.** If done, it demonstrates that the station is good enough for an independent network to accept. The demonstration does not depend on it and the platform does not require it.

Archived observations from public networks are used as **training data** for the prediction models. They never sit between the scheduler and a station.

### 5.5 The public face

The dashboard is reachable from anywhere: a map of registered stations and their status, what the network is receiving right now, the queue of upcoming passes with the predicted value of each, reliability figures updated continuously, and measured-versus-predicted orbit error as it accumulates.

Anyone with the link can watch. That is the demo, and it works from a phone.

---

## 6. What makes this different

**The scheduling objective is different.** Existing schedulers rank a list and take the highest passes. Ours estimates the probability that reception succeeds and schedules to maximise expected results, with elevation and operator priority folded in as inputs rather than discarded.

**We measure the accuracy of orbital data instead of assuming it.** Public element sets carry no uncertainty estimate. We derive one from observation and use it to make better decisions.

**We treat reception as a service with reliability targets** — including an *irrecoverable loss budget*. We present this as an operational framing rather than a technical novelty: the mechanism resembles a standard error budget, but the accounting differs because the work cannot be retried, and no existing ground station software applies either.

**We built the network, not just a station.** A published protocol, a reference client, a registry and a public platform.

---

## 7. Architecture

*See the accompanying architecture diagram.*

```
                          ANYONE, ANYWHERE
                                 │
                    ┌────────────▼────────────┐
                    │   DASHBOARD + PUBLIC API │
                    └────────────┬────────────┘
                                 │
        ┌────────────────────────▼─────────────────────────┐
        │                  PLATFORM                         │      ┌──────────────┐
        │  Orbit service   Prediction      Scheduler        │◄╌╌╌╌╌┤  External    │
        │  Station registry  Observation store  Reliability │      │  archives    │
        └────────────────────────┬─────────────────────────┘      │  (optional,  │
                                 │                                 │  data only)  │
        ┌────────────────────────▼─────────────────────────┐      └──────────────┘
        │       MERIDIAN STATION PROTOCOL  (MSP)            │   open specification
        └───────┬─────────────────┬─────────────────┬──────┘
                │                 │                 │
        ┌───────▼──────┐  ┌───────▼───────┐  ┌──────▼───────┐
        │ Station 001  │  │ Microcontrol- │  │ 50 simulated │
        │ Pi + SDR +   │  │ ler station   │  │ (labelled)   │
        │ rotator      │  │ ESP32-class   │  │              │
        └──────────────┘  └───────────────┘  └──────────────┘
```

**The independence test:** remove every dashed element and the system still schedules, receives, decodes, monitors and reports.

**MSP is a first-class deliverable**, not an implementation detail. It is what turns a program into a network.

---

## 8. Evaluation methodology

This section states how each claim will be tested, and acknowledges the threats to validity before they are raised.

### 8.1 The prediction model is hybrid by design

Elevation and operator priority are not competitors to our model — they are inputs to it.

- **Elevation** is the strongest single predictor and remains the first feature.
- **Operator priority** is a weight in the objective function, not something to predict away.
- **Our contribution** is the additional signal: learned horizon profile, element-set age, station health, interference profile by azimuth and hour, and per-satellite history.

This is also a functional requirement, not just a design preference. A newly joined station has no history, so the model has nothing to learn from and **must** fall back to geometry until data accumulates.

### 8.2 Ablation, not a single number

A combined model cannot show what our contribution added. Four configurations are evaluated and all four reported:

| | Features | Question answered |
|---|---|---|
| **A** | Elevation only | The naive baseline |
| **B** | Elevation + priority weighting | What existing practice achieves |
| **C** | Our features only | Do our signals carry information independently? |
| **D** | All combined | The shipped system |

**D − B is the measured contribution.** C establishes whether the new signals stand alone.

A hybrid is not automatically better; adding a weak signal can degrade a model. That is what the ablation is for.

### 8.3 Threat to validity: selection bias

**This is the most serious methodological risk in the project and we state it plainly.**

An observation archive contains only passes that someone already decided to observe. Passes nobody scheduled have no recorded outcome. Two consequences:

1. The prediction model trains on outcomes conditioned on a prior scheduling decision, not on a random sample of passes.
2. Comparing schedulers retrospectively requires counterfactual outcomes for passes never observed, which do not exist.

**Three mitigations, applied together:**

- **Near-complete windows.** Restrict comparison to station-days where a high proportion of geometrically available passes were actually observed. Where nearly everything was scheduled, the counterfactual is nearly complete. The completeness ratio is reported alongside every result.
- **Off-policy evaluation.** Where completeness is partial, apply inverse-propensity weighting with the propensity model fitted to the historical scheduling policy, and report the effective sample size.
- **Prospective evaluation.** Once our own station is live we control the policy. Alternate scheduling policies on a randomised schedule and compare outcomes directly. This is the cleanest evidence, and it is the reason the station matters.

### 8.4 Confounding: silent satellites

Some satellites transmit intermittently. A pass that produced nothing may mean a bad prediction or a silent transmitter. Observations are cross-checked against contemporaneous receptions of the same satellite elsewhere in the archive; where an object was demonstrably silent network-wide, the observation is excluded from yield scoring and reported separately.

### 8.5 Measuring orbital error

**Primary method — pass timing error.** The difference between predicted and actual acquisition-of-signal, as a function of element-set age. This requires only an accurate clock, is robust to receiver imperfection, and directly demonstrates the effect we claim.

**Secondary method — Doppler orbit determination.** Recovering an orbital state from the observed frequency curve. Harder, and honestly so: consumer receiver oscillators drift with temperature, which corrupts frequency measurement directly, and a single pass from a single station is poorly conditioned. This is reported as a stretch result. **No project claim depends on it.**

### 8.6 Data splits and reproducibility

All evaluation uses temporal splits — train on earlier data, test on later. Never random splits, which leak future information. Every reported figure is regenerable from a recorded dataset snapshot, a configuration file and a seed.

---

## 9. Success criteria

| ID | Criterion | Target |
|---|---|---|
| SC-1 | Decoded frames per station-hour, configuration D vs B, on near-complete windows | ≥ 20% improvement |
| SC-2 | Prediction calibration, Brier score vs base-rate predictor | ≥ 25% reduction |
| SC-3 | Pass timing error predicted vs measured, within stated 1σ | ≥ 68% of passes |
| SC-4 | Pass capture rate of our station over 30 days continuous | ≥ 90% |
| SC-5 | Time to detect an injected node failure | ≤ 90 s |
| SC-6 | Station registered, online, publicly visible | Achieved / not |

> **Stated contingency on SC-1.** If the data does not support a 20% margin, the project is not invalidated. A *calibrated* prediction model — one whose stated probabilities match observed frequencies — is a result in its own right, and a smaller measured gain reported honestly is a better outcome than a large one claimed loosely. We report what we measure.

---

## 10. What we build, and what we use

We write the parts that are the project. We use libraries for the parts that are solved, well tested, and would only get worse if we rewrote them.

**We build:** MSP and its specification · station registry · observation store and data model · orbit uncertainty model · timing-error and Doppler analysis · obstruction profile inference · reception outcome prediction · scheduler and optimiser · station client · rotator controller firmware · reliability layer, service targets and failure injection · station simulator · public API and dashboard

**We use as libraries:**

| Library | Why not rebuild |
|---|---|
| Orbit propagation (`sgp4`, `skyfield`) | The standard model. A hand-written version would be slower and subtly wrong. |
| Rotator control (Hamlib) | Speaking a standard protocol is interoperability. Inventing our own would isolate us. |
| Demodulation and decoding (GNU Radio, SatDump) | Each decoder is a full project in itself. |
| Numerical and machine learning libraries | Obviously. |
| Metrics and dashboards (Prometheus, Grafana) | Obviously. |

**The test:** *does writing this ourselves produce a result or teach us something?* Scheduling, yes. Uncertainty modelling, yes. Error-correcting codes, no.

**On borrowed ideas.** We read existing projects to understand how problems were solved, then write our own implementations. Every instance is recorded in an attribution file in the repository, updated in the same commit as the work. Existing projects are licensed such that copying source carries obligations, and unacknowledged copying in an academic submission is a serious matter.

---

## 11. Development approach: software first

We build and prove the platform in software before spending on hardware. If the software is not working, hardware cannot save it. If the software *is* working, hardware becomes one more station joining a network that already runs.

**How that is possible with no station:** the simulator generates virtual stations that speak real MSP to the real platform — indistinguishable from physical stations from the platform's side. Meanwhile, historical observation archives give us large volumes of real passes with known outcomes to train and test against.

**One hard date:** hardware work begins by **week 15**. If the software phase overruns, hardware is cut and the project ships as a platform validated on archival and simulated data. That is still a complete project.

---

## 12. Phases

Thirty-six weeks planned against roughly thirty-two working weeks, the difference being examination periods and holidays. Slack is deliberate.

| Phase | Weeks | Content | Done when |
|---|---|---|---|
| **1 — Foundations** | 1–7 | Data model and store. Orbit service and pass prediction. MSP drafted and published. Station registry. First simulated station end to end. | A virtual station is visible on our public site from outside the college network |
| **2 — Intelligence** | 8–14 | Archive import. Obstruction inference. Uncertainty model. Prediction model. Ablation A–D. Scheduler. | The ablation result is written down, whatever it is |
| **3 — Operations** | 12–18 | Reliability layer, service targets, alerting, loss budget. Failure injection. Fifty-station simulator. Dashboard in full. | Platform runs unattended 72 hours; an injected failure is detected without anyone watching |
| **4 — Hardware** | 15–24 | Antenna, receiver, amplifier, cabling. Rotator with controller firmware. Station client on the Pi. Installation. | Station 001 receives on a schedule the platform produced |
| **5 — Validation** | 22–30 | Continuous operation. Prospective policy comparison. Timing-error measurement. All criteria measured. | Every criterion reported — met or missed |
| **6 — Close** | 29–36 | Report, documentation, optional external registration, optional upstream contributions, rehearsal. | Demo rehearsed end to end three times |

Phases overlap deliberately: hardware procurement runs during Phase 3, and validation begins before hardware work finishes.

---

## 13. Demonstration scenario

What an examiner sees, end to end, in about twelve minutes.

1. **Open the website** — on their own phone, from outside the college network. The map shows Station 001 online, with simulated stations elsewhere.
2. **Look at the pass queue.** Six satellites approaching. Each carries a predicted yield. One near the top has a *lower* maximum elevation than one below it.
3. **Ask why.** The platform explains: the higher pass crosses an obstructed azimuth and its element set is six days old, beyond the learned uncertainty threshold. The lower pass is clean and fresh. **This one screen is the entire project.**
4. **The scheduler commits.** Three minutes before acquisition the plan is issued over MSP; the station acknowledges.
5. **The antenna moves** — on its own, on the roof, to the computed bearing.
6. **Reception begins.** The waterfall fills; the characteristic Doppler curve appears.
7. **Data arrives.** A decoded image or telemetry frame appears as the pass proceeds.
8. **The pass ends.** Reliability figures update live; the loss budget ticks.
9. **The measurement.** Actual acquisition time is compared against prediction, plotted against element-set age and our confidence band.
10. **Break it.** A station is disconnected mid-operation. Within ninety seconds an alert fires and the scheduler re-plans around the loss.
11. **Show the scale.** Switch to fifty simulated stations, clearly labelled, scheduled by the same optimiser, with the ablation comparison plotted across them.

Every step runs on our own infrastructure.

---

## 14. Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Roof access denied or delayed | High | Medium | Phases 1–3 need no hardware; the platform is validated on archive and simulation regardless |
| Site RF environment prevents reception | High | Medium | Early reception test with the kit antenna before any installation spend; interference survey in Phase 1 |
| Selection bias invalidates SC-1 | High | Medium | Three mitigations in §8.3; prospective evaluation on our own station is the fallback |
| Ablation shows no improvement | Medium | Medium | Stated contingency under §9; calibration is a result independently |
| Doppler orbit determination too noisy | Low | High | Already demoted to a stretch result; timing error is the primary method |
| Single-person dependency on orbital work | High | Medium | Paired review on all propagation code; the platform lead maintains working knowledge of the orbit service |
| Software phase overruns | Medium | Medium | Week 15 hardware gate; project ships without hardware if breached |
| Hardware failure late in project | Medium | Medium | Contingency budget; spare amplifier and receiver held from Phase 3 |
| Scope expansion | High | Medium | Exclusions frozen; reversal requires written agreement from all three and the guide |

---

## 15. Station overview

*See the accompanying station build diagram for the full signal chain.*

```
   SATELLITE
       │
   QFH antenna (137 MHz)  ·  Crossed Yagi (435 MHz, on rotator)
       │
   Low-noise amplifier + filter        ← at the mast head
       │
   Low-loss coax + surge arrestor
       │
   Software-defined receiver
       │
   Raspberry Pi 5 — station client     ← tune, record, decode, report
       │
   Internet  →  PLATFORM
```

**Primary reception target at 137 MHz** is the digital LRPT downlink from the Meteor-M series. The older analogue APT service is no longer operating, so guidance written before 2025 is out of date on this point — a detail worth stating because it is the first thing a knowledgeable examiner will ask.

The rotator is built in the college workshop: printed structure with the worm gear machined in metal, driven by stepper motors under an Arduino controller taking pointing commands over the network.

**The station never transmits.** Reception only, which avoids the licensing that transmission would require.

---

## 16. Data and ethics

The system receives only open, unencrypted transmissions intended for public reception — weather satellite imagery and amateur satellite telemetry. No attempt is made to receive or decode protected communications. No personal data is collected, stored or processed. Observation records and derived datasets are published openly.

---

## 17. Budget

**Already available, no cost:** Raspberry Pi 5 with power supply, active cooling and 256 GB NVMe; Arduino Uno R4 WiFi as rotator controller; three laptops; college 3D printing, tool and die laboratory, mechanical workshop, rooftop space.

**Hosting:** the platform runs on the Pi, published to a public address through a secure tunnel — no static IP, works from behind the college network, no cost.

| Tier | Contents | Cost |
|---|---|---|
| **1 — Core station** | Receiver, amplifier, antenna, feedline, surge protection, enclosure, mast | ₹24,300 |
| **2 — Second station type** | Microcontroller station and antenna | ₹3,300 |
| **3 — Tracking** *(optional)* | Rotator, motors, drivers, structure, UHF Yagi and amplifier | ₹15,900 |

**Requested: ₹27,600** (Tiers 1 and 2, including 15% contingency) — approximately ₹9,200 per member.
**With tracking: ₹43,500.**

Tier 3 is genuinely optional: every claim the project makes is provable with a fixed antenna. Tracking adds a second band, an antenna that moves under software control during the demonstration, and substantive use of the institute's machining facilities.

Nothing is purchased before the software that consumes it exists — with one exception, the receiver, bought early so that reception from our site is confirmed before any installation spend.

---

## 18. Team and individual contributions

Each member owns a separable, individually assessable deliverable, and each must be able to explain the whole system.

| Member | Owns | Individually assessable contribution |
|---|---|---|
| **1 — Platform & reliability** *(lead)* | Observation store, public API, dashboard, deployment, monitoring, service targets, failure injection | Design and evaluation of the reliability layer, including the loss-budget formulation and measured failure-detection performance |
| **2 — Orbit & prediction** | Propagation, uncertainty model, timing-error analysis, prediction model, scheduler, simulator | The orbital uncertainty model and the ablation study, with calibration analysis |
| **3 — Station & radio** | Antennas, radio chain, interference survey, rotator, controller firmware, station client, installation | Site RF characterisation and the link-budget analysis, plus the station client as MSP reference implementation |

MSP is designed jointly and reviewed by all three, as the interface every module depends on.

---

## 19. Deliverables

1. Platform, deployable from a single command
2. MSP specification, published
3. Reference station client
4. Public dashboard, reachable from anywhere
5. Orbit uncertainty model with measured validation
6. Prediction model with ablation and calibration analysis
7. Scheduler with measured comparison against baseline policies
8. Reliability layer with service targets and failure testing
9. Fifty-station simulator with reproducible runs
10. One operational ground station
11. Technical report and demonstration

---

## 20. In short

Satellite reception happens under a deadline that cannot be missed twice, using orbital data whose accuracy is never stated, scheduled by a rule that ignores most of what matters, with no way of telling whether it is working.

We are building the layer that fixes that — the protocol and the network for other stations to join, and one station on our roof to prove it is true.
