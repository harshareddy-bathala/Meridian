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

**Not measured here — the alert.** SC-5's detection is derived on read, so its ninety seconds are arithmetic (D-192). The measured half is when `StationOffline` fires, which needs Prometheus. On a stack with the `metrics` profile:

```sh
python -m meridian_sim.station --scenario network --count 5 --ledger faults.jsonl
meridian reliability faults --ledger faults.jsonl --prometheus http://localhost:9090
```

prints each outage's alert latency. It has not been run for this document.

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

Seventy-two hours of the full stack with simulated stations under the `chaos` scenario and seeded platform faults is Stage 21's last part, and Stage 24's acceptance item. Its tool and its result are recorded here when it has run.
