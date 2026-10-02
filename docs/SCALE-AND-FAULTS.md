# Scale and faults — Stage 21's measurements

**Every number here is simulated** (CLAUDE.md rule 5). Virtual stations speak real MSP to a real platform, but no station is a radio, and a figure measured against them says nothing about how a real station receives. What they do measure is the platform: how it bears a fleet, and how it handles each way a fleet can fail.

What is proven at every change is in CI and in the tests named below. The tables are one machine on one day, with the commands that regenerate them. D-188 to D-197 record why each part is built the way it is.

---

## Faults

**The gate** is `tests/integration/test_fault_gate.py`. It runs in CI's integration job, in about 15 seconds.
- **The fleet:** five stations run the `chaos` scenario, every recurring station fault at once, through real MSP on a stated clock. That is ninety minutes of rounds, with a scheduling round every minute.
- **The judgement:** every settled pass is classified, and then `meridian reliability faults`' own function judges the run's fault ledger against the platform's records (D-192). The gate is that no fault fails any question it is asked, and that the questions were actually asked.
- **What one run looks like** (the counts vary with the platform's random assignment ids; the verdicts do not):

  | Question | Answered | Passed |
  |---|---|---|
  | `held` — the silence was real | ~45 | all |
  | `detected` — offline within 90 s, or correctly never | ~57 | all |
  | `no_new_work` while offline | ~64 | all |
  | `replanned` — unbegun work revoked inside the outage | ~31 | all |
  | `no_false_miss` | ~120 | all |
  | `declines_honoured` | 3–5 | all |
  | `recovered` | ~58 | all |

- **Two positive controls must fail it:**
  - removing one offline revocation from the history;
  - planting a confirmed miss on a pass the station reported.

**Platform faults against the real stack** run in CI's `compose` job ("The platform survives its own faults"). `deploy/tools/chaos.py` restarts the API and the database, pauses the API past a station's timeout, and stops and starts the scheduler, against a running stack with a simulated station (D-194). It then requires:
- the simulator still running, and never restarted;
- no station registered twice;
- heartbeats stored again;
- four closed windows in the ledger.

**What building the gate found**, each fixed in the stage and recorded:

| Finding | Decision |
|---|---|
| After a database restart, each stale pooled connection failed the request that borrowed it — up to eight refused heartbeats per restart | D-193 |
| A heartbeat was judged at the API's clock and stored, expired and delivered at the database's | D-195 |
| A reinstatement erased the only record that work was ever revoked, so replanning could not be shown | D-196 |
| The simulator "declined" work the platform had never seen it hold, which MSP reads as not yet arrived | D-188 |

**The alert, SC-5's measured half.** SC-5's detection is derived on read, so its ninety seconds are arithmetic (D-192). The measured half is when `StationOffline` fires, and the long-run rehearsal below measured it against the real stack's Prometheus.
- **The alert is summed over the fleet** (D-197), so it times a fault only when it rose for that station: not already firing for another, and not before this station read offline.
- **29 station outages met that test.** Each alert fired **20 to 80 seconds after the fault began**, and 20 to 50 seconds after the station read offline. That is inside SC-5's ninety seconds every time.
- **The other outages are not a failure.** The alert was already firing for another station, so it timed nothing.

---

## Scale

**Function at fifty stations** is `tests/e2e/test_fifty_stations.py`, in CI. Fifty stations register through MSP, are scheduled from real pass generation over the development catalogue, and are heard on every round, and none stops. The same file shows **the platform's series do not grow with the fleet**: five stations and fifty expose the same scrape-time series, and no label anywhere is a station's identity (D-197).

**Latency and load** were measured against a running platform on **2026-09-29**, on one laptop:
- AMD Ryzen 5 7530U, 12 threads, 7 GiB RAM;
- PostgreSQL 16.14 with TimescaleDB 2.29 in Docker;
- Python 3.11.16;
- `meridian serve` with one worker, and `meridian jobs run` with its defaults: a round every 5 minutes over a 6-hour horizon, and the development catalogue's two satellites.

Each fleet ran twelve rounds at the real thirty-second cadence, six minutes, with `deploy/tools/scale_probe.py` scraping both processes every fifteen seconds. The same state directory was kept throughout, so each larger fleet resumed the smaller one's stations.

| Stations | Heartbeats answered / sent | Accepted per s | Heartbeat p50 / p95 | Rounds overran | Longest round | Upload queue, most | Pool open / fewest idle / most waiting | API series |
|---|---|---|---|---|---|---|---|---|
| 1 | 12 / 12 | 0.033 | ≤ 25 ms / ≤ 50 ms | 0 | 0.04 s | 0 | 2 / 2 / 0 | 111 |
| 5 | 60 / 60 | 0.167 | ≤ 10 ms / ≤ 10 ms | 0 | 0.05 s | 0 | 2 / 2 / 0 | 111 |
| 10 | 120 / 120 | 0.333 | ≤ 10 ms / ≤ 25 ms | 0 | 0.13 s | 0 | 2 / 1 / 0 | 111 |
| 50 | 600 / 600 | 1.667 | ≤ 10 ms / ≤ 10 ms | 0 | 0.35 s | 0 | 2 / 2 / 0 | 111 |
| 50, again | 600 / 600 | 1.667 | ≤ 10 ms / ≤ 10 ms | 0 | 0.37 s | 0 | 2 / 1 / 0 | 144 |

**How to read it:**
- **Latencies are histogram bucket bounds**, so "≤ 10 ms" means every observation up to that quantile landed in the 10 ms bucket. The one-station figure is twelve requests, so its tail is one slow request.
- **The longest round is the fleet's own time** to tick every station in turn, on one thread (D-076). At fifty it is a third of a second of a thirty-second cadence.
- **The second fifty-station run is the only one in which passes opened**, so it is also the only one that uploaded observations. Its extra series are that traffic's route and delay histograms, and a `revoked` assignment state, which appeared when stations idle between runs went offline. They are new *kinds* of activity, not more stations, which is the property D-197's test holds.

**The scheduler at fifty stations** (the second run, whose window held two jobs rounds):

| Task | Mean per round | p95 bound |
|---|---|---|
| pass generation | 16.1 s | ≤ 30 s |
| schedule, 95 candidates, optimal | 0.13 s | ≤ 0.25 s |
| expiry sweep | 0.01 s | ≤ 0.1 s |
| reliability classification | 0.02 s | ≤ 0.1 s |

**Pass generation is the cost that grows.** It propagates every satellite for every station over the horizon, so it scales with stations × satellites. At fifty stations and two satellites it is about a third of a second per station, a twentieth of the five-minute round. A catalogue of twenty satellites at the same fleet would put a round near three minutes. That is the number to watch before the catalogue grows, and it is recorded here rather than optimised now, because nothing yet runs near it. The solver itself took under a millisecond.

**Not loaded by these runs:**
- **The upload path at volume.** Most six-minute windows held no pass. The gate and the fifty-station test exercise it functionally.
- **Prometheus's own cost.** Every figure above is read from the endpoints Prometheus would scrape.

### Regenerating the table

With a database migrated to head:

```sh
export DATABASE_URL=… TOKEN_HASH_PEPPER=… METRICS_TOKEN=… JOBS_METRICS_PORT=9121
meridian catalogue load --file deploy/catalogue/development.json
meridian invite create --label scale --count 50 > invites.txt
meridian serve --port 8121 &
meridian jobs run --metrics-host 127.0.0.1 &
for n in 1 5 10 50; do
  python deploy/tools/scale_probe.py --api http://127.0.0.1:8121 \
      --jobs http://127.0.0.1:9121 --seconds 360 --out probe-$n.json &
  python -m meridian_sim.station --count $n --seed 4471 --run-id scale-$n \
      --base-url http://127.0.0.1:8121 --state-dir state --invites invites.txt \
      --rounds 12 --scale-report sim-$n.json
  wait
done
```

---

## The long run

`deploy/tools/long_run.py` runs the whole compose stack under faults for hours, samples it, and judges it (D-198). Since Stage 24 it also judges the machine's resources, resumes after its own interruption, and seals itself (D-257). The seventy-two hour run is Stage 24's acceptance item and **has not been run**. It is done on the Pi, from `OPERATIONS.md` § The 72-hour acceptance run on the Pi. What follows are its two rehearsals: Stage 21's, then Stage 24's of the sealed tool.

**The rehearsal, 2026-09-30, 06:59 to 09:09 UTC**, on the laptop above:
- ten stations under `chaos`, and seed 4471's four platform faults: the scheduler stopped for five minutes, the API paused past a station's timeout, the API restarted and the database restarted;
- a sample every fifteen minutes, and ten minutes to settle.

| Recorded | Result |
|---|---|
| Faults judged | **414, none failed**: 410 station faults of ten kinds, and the four platform faults |
| Host asleep during the run | none |
| Containers that died on their own | none; every service running at the end |
| Alerts | 100 — `StationStale` 62, `StationOffline` 37, `SchedulerUnavailable` 1 |
| False positives | **none**: every alert fell inside a fault in the ledger, or within ten minutes of one closing |
| Upload queue | at most one report waiting; empty after settling |
| Peak memory | API 233 MiB, Grafana 206, database 96, jobs 65, Prometheus 52, simulator 31, Alertmanager 23 |

**The verdict, question by question.**

| Question | Passed | Did not apply |
|---|---|---|
| `held` | 140 | 270 |
| `detected` | 177 | 233 |
| `no_new_work` | 191 | 219 |
| `replanned` | 19 | 391 |
| `no_false_miss` | 357 | 57 |
| `declines_honoured` | 2 | 408 |
| `recovered` | 182 | 232 |
| `alerted` | 29 | 381 |

In the 19 outages a scheduling round saw, the station's unbegun work was revoked within 101 seconds of it going offline, which is the next round.

**It took three runs, and each earlier one found something.**
- **The first stalled when the laptop suspended.** The tool slept through it, because a monotonic sleep does not count a suspend. It now waits against the wall clock and fails a run whose host slept.
- **The second was cut by a lid close.** Its record exposed three questions the checker was asking wrongly, now recorded in D-192: work already revoked is not owed a revocation again; a round owes an outage only if it finished reading while the station was silent; and a fleet-wide alert times a fault only when it rose for that station. Judged again with the fixes, that run's record also failed nothing.
- **The third, above, ran clean.** Its own judgement then failed for a reason in the tool: it read the station ledger through `exec` after stopping the simulator. The tool now reads the ledger from the simulator's volume, fails loudly when it cannot, and can judge a finished run again with `--judge-only`, which is how the table above was produced from the run's own stack.

**Regenerating it.**

```sh
MERIDIAN_IMAGE=… API_PORT=8131 GRAFANA_PORT=3031 \
python deploy/tools/long_run.py --hours 2 --seed 4471 --sample-every-minutes 15 \
    --fault-gap-minutes 20 --settle-minutes 10 --out runs/long-2h \
    --project-name meridian-s21 --up --stations 10
```

The laptop was kept awake with its lid closed (a user-level `handle-lid-switch` inhibitor) and idle sleep inhibited. A seventy-two hour run on a laptop needs both.

### Stage 24's rehearsal: interrupted, resumed and sealed

**2026-10-01, 11:36 to 13:46 UTC**, on the laptop above. The setup was:
- the image built from `feat/stage-24` rebased on Stage 26 (`7069385`);
- ten stations under `chaos`, and seed 4471's four platform faults;
- a sample every fifteen minutes, and ten minutes to settle;
- `SIGTERM` sent to the tool on purpose seven seconds into the API pause, then `--resume`.

It is a rehearsal of the tool, not the acceptance run: two hours on an x86 laptop.

| Recorded | Result |
|---|---|
| Faults judged | **375, none failed**: 371 station faults of ten kinds, and the four platform faults |
| Interrupted | once. The tool unpaused the API and closed the fault at the second it was stopped, and `--resume` carried on 13 s later, skipping nothing |
| Host asleep | none |
| Containers | none restarted on their own, none unhealthy, all running after settling |
| Alerts | 95 — `StationStale` 58, `StationOffline` 36, `SchedulerUnavailable` 1 |
| False positives | **none** |
| Owed alerts | none missed: `SchedulerUnavailable` fired for the five-minute `scheduler_down` |
| Upload queue | empty throughout, and after the stations' faults were stopped for settling |
| Peak memory | Grafana 258 MiB, API 247, database 114, Prometheus 86, jobs 70, simulator 39, Alertmanager 39 |
| Memory growth | not judged: two hours is too few samples to fit, as it should be |
| Database | 11.6 MiB at the start, 13.0 MiB at the end |
| Sealed | fault run `28de65a16180`, with its record inside. A report built with it calls it **too short**, and names the run's machine, image and interruption. `report verify` regenerates that report identically. Built after review, when gaps stopped counting, it reads 1.778 hours watched and 0.223 hours of gap. That gap is the sealed record's, dated from 12:06 by the fault fixed below; the true gap was thirteen seconds |

**The verdict, question by question.**

| Question | Passed | Did not apply |
|---|---|---|
| `held` | 127 | 244 |
| `detected` | 159 | 212 |
| `no_new_work` | 169 | 202 |
| `replanned` | 23 | 348 |
| `no_false_miss` | 324 | 51 |
| `declines_honoured` | 4 | 367 |
| `recovered` | 164 | 211 |
| `alerted` | 22 | 349 |

**It found four things, each fixed before Stage 24 merged:**
- **The first attempt measured nothing.** This laptop's `deploy/prometheus/metrics_token` was at mode 600. Prometheus runs as `nobody`, so every scrape failed and `ApiUnavailable` and `SchedulerUnavailable` fired from the first minute. The run would have failed on them as false positives, which is right. `--preflight` now checks the file can be read, and the runbook says to `chmod 644` it. The rehearsal ran from a copy of `deploy/` with its own token.
- **The seal was refused at the end.** `data/datasets` did not exist before `up`, so Docker created it as root when it mounted it into the jobs service, and the seal, run as the operator, could not write. The tool now makes the datasets root before `up` and refuses to start if it cannot write there. The run was sealed afterwards with `--judge-only`, which is what that flag is for.
- **`--resume` counted the faults injected before the stop as skipped,** two here. It now counts only faults its ledger never opened.
- **`--resume` dated the gap from the last sample, thirteen minutes early.** It now dates it from the tool's last act, sample or fault.

The sealed record was made before the last two fixes, so it says two faults were skipped and the gap began at 12:06. The table above gives what happened, read from the ledger.

**Regenerating it** (the rehearsal's compose directory was `git archive HEAD deploy`, with a fresh `metrics_token` and an env file of the example's values on its own ports):

```sh
python deploy/tools/long_run.py --hours 2 --seed 4471 --sample-every-minutes 15 \
    --fault-gap-minutes 20 --settle-minutes 10 --out data/long-2h-s24 \
    --compose-file data/rehearsal/deploy/docker-compose.yml --env-file data/rehearsal.env \
    --project-name meridian-s24 --up --stations 10 --datasets data/datasets
```

---

## Ground-truth faults

*Stage 25, D-253. This section is the **specification of the four faults' effects**, written before Stage 27's diagnosis exists. D-105 asks for it to be reviewed by a team member who is not the diagnosis author, to guard against the two agreeing by construction.*

**Review:** pending. Record here who reviewed it and when, before Stage 27 begins.

Each fault is injected into a virtual station's receiver, `meridian_sim/sky_effects.py`, and changes what that station *measures*. Nothing about the fault travels anywhere else:
- the MSP body carries only the evidence it changed;
- the fault itself, its parameters and the passes it acted on go to the run's ledger, beside the seed, and never into a platform table (D-105, D-189);
- `tests/unit/test_simulator_sky_ground_truth.py` holds both halves.

### What each fault does

The evidence it acts on is D-251's: 25 SNR samples across the window, and a noise floor at a fixed gain.

| Fault | Scenario | Shape, drawn from the seed | Effect on a pass that began while it held |
|---|---|---|---|
| `signal_degradation` | `degradation` | a rate, 1 to 8 dB/day, from an onset tick of 5 to 60 | every sample of a heard pass loses *rate × days since onset*, measured at the pass's start; the floor does not move |
| `obstruction` | `obstruction` | a sector 30° to 90° wide from a random azimuth, blocked below 15° to 35°, from an onset tick of 5 to 60; **never declared** in `horizon_mask` | a sample whose direction is inside the sector and above the horizon but below the elevation hears nothing, and its SNR becomes noise; the floor does not move |
| `interference` | `interference` | a sector 45° to 120° wide, a daily window of 2 to 6 hours from a random UTC hour, a rise of 6 to 15 dB, from an onset tick of 5 to 60 | a sample inside both the sector and the hours has its floor raised by the rise, and loses that much SNR if the pass was heard. The pass's floor is the mean power of its samples' floors, so it rises with the share of the pass affected |
| `satellite_silent` | `silent` | the satellite the operator names, silent from tick 10 to 60 for 20 to 120 ticks, **at every station** | every sample of a pass of that satellite is noise; the floor does not move |

`sky` runs all four together.

A sample's direction comes from the element set in the assignment, at the station's registered site, computed with Skyfield. `tests/unit/test_simulator_sky_track.py` checks it against the platform's own pass prediction, to 0.01°.

### How the outcome follows

The outcome is derived again from the surviving evidence, by the frame count every pass uses (D-251):
- **`decoded`** stays decoded while frames above the 5 dB bar remain, or while none of the ones it had were lost;
- **a heard pass** that still has a sample at or above 3 dB is `signal_no_decode`;
- **otherwise** it is `no_signal`.

First detection and peak SNR are re-read from the samples. `aborted` and `not_attempted` passes are never changed, because they measured nothing.

**A pass counts as touched only if a fault changed something about it.** Examples of passes that are not touched:
- one that began before a degradation's onset;
- one that never crossed an obstruction;
- one outside an interference source's hours;
- one of a different satellite from the silent one.

Each touched pass is named in an `act` line against its fault. A silence is named when the pass begins, because its window may close before the pass ends.

### What the ledger records

The four kinds open on the station they act on, as every station fault does, with the seed and the true instant of onset.

The `detail` holds the shape, for example:
- `rate_db_per_day`;
- `azimuth_from_deg`, `width_deg` and `below_elevation_deg`;
- `start_hour_utc`, `hours` and `rise_db`;
- `satellite_id`.

A silence opens on every station with `"fleet_wide": true`, so a reader counts one cause rather than one per station. Only a silence closes. The other three persist, and a station process that restarts opens them again at its restart, so the ledger's onset stays the one the effect is measured from.

### What a diagnosis can and cannot see

This is the evidence Stage 27's tests will find, stated so the specification and the diagnoser can be compared.
- **Degradation** shows as a shortfall in SNR against the station's own history at the same elevation. It shows on every heard pass, whatever its direction or hour.
- **Obstruction** shows as signal lost in one sector at low elevation, pass after pass, while the rest of each pass is unchanged. The declared mask does not explain it.
- **Interference** shows as a raised floor, at the same gain, in one sector and a band of hours.
- **A silent satellite** shows as every station listening and hearing nothing from one satellite at the same time, while their floors are normal.

The negative control is Stage 21's `decoder_degraded`, which has no cause category. Stage 27 should call it *undetermined*.

**Known limits, stated rather than hidden:**
- a decoded pass whose peak was under the 5 dB bar counts no decodable frames, so it has none to lose and stays `decoded` for as long as it is still heard. The outcome model said it decoded, and nothing in the evidence contradicts that until the signal is gone;
- the effects are shapes, not a link budget (D-251);
- the claim they support is D-105's narrow one: given evidence of this shape, the diagnoser names the fault.
