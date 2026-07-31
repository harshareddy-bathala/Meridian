# Meridian Station Protocol (MSP)

**Version 0.1 — draft**

An open protocol for satellite ground stations to join a scheduling network.

---

## 1. Purpose and scope

MSP defines how a receiving station joins a network, declares what it can do, reports its state, receives work, and returns results.

**Design constraints, in priority order:**

1. **Implementable on a microcontroller.** A station with no operating system, kilobytes of RAM and no TLS library must be able to speak it. This constraint drives every decision below.
2. **Stateless server, resumable client.** A station that loses power mid-pass and reboots must rejoin without operator intervention.
3. **Listening state is explicit.** The network must be able to distinguish *heard nothing* from *was not listening*. This is not optional; it is the difference between a valid reliability metric and a meaningless one.
4. **Transport-agnostic semantics.** HTTP is the reference binding. The message semantics do not depend on it.

**Out of scope:** signal processing, decoding, antenna control, and how a station chooses to fulfil an assignment. MSP says *what* to receive and *when*, never *how*.

---

## 2. Model

A **station** is an independently operated receiving installation with a stable identity, a fixed location, and declared capabilities.

A **capability** describes what a station can receive: frequency ranges, modulations, polarisation, whether it can track, and its usable elevation floor.

An **assignment** is an instruction to attempt reception of one satellite over one time window.

An **observation** is the report of an attempt — including attempts that produced nothing, which are as informative as those that succeeded.

A **heartbeat** is a periodic statement of liveness and current state.

```
station                                    platform
   │                                          │
   │──── register ───────────────────────────▶│   once, on first join
   │◀─── station_id + token ──────────────────│
   │                                          │
   │──── heartbeat ──────────────────────────▶│   every 30 s
   │◀─── assignments (0..n) ──────────────────│
   │                                          │
   │   [executes assignment]                  │
   │                                          │
   │──── observation ────────────────────────▶│   after each attempt
   │◀─── ack ─────────────────────────────────│
```

---

## 3. Identity and authentication

On first contact a station registers and receives a `station_id` and a bearer token. The token is presented on every subsequent request.

Tokens, not certificates. A microcontroller station cannot be assumed to hold a certificate store or terminate TLS. Where the transport is unencrypted, the token is the only credential, and the platform must treat station-submitted data as untrusted input regardless.

**A station may only submit observations for assignments issued to it.**

---

## 4. Messages

### 4.1 `register`

Station → platform. Once, on first join.

```json
{
  "name": "nec-rooftop-01",
  "operator": "NTTF NEC",
  "location": { "lat": 12.9716, "lon": 77.5946, "alt_m": 920 },
  "capabilities": [
    {
      "band": "vhf",
      "freq_min_hz": 136000000,
      "freq_max_hz": 138000000,
      "modes": ["lrpt", "fsk", "afsk"],
      "polarisation": "rhcp",
      "tracking": true,
      "min_elevation_deg": 10
    }
  ],
  "client": { "impl": "meridian-reference", "version": "0.1.0" }
}
```

Response:

```json
{ "station_id": "st_7fa3c1", "token": "…", "heartbeat_interval_s": 30 }
```

**Notes.** `min_elevation_deg` is the station's declared floor, not its measured horizon — the platform learns the real obstruction profile from observation history and may override this downward per azimuth. Altitude is required: it materially affects pass geometry.

### 4.2 `heartbeat`

Station → platform. Every `heartbeat_interval_s`.

```json
{
  "station_id": "st_7fa3c1",
  "sent_at": "2026-08-14T09:31:02Z",
  "state": "listening",
  "listening": {
    "assignment_id": "as_44b2",
    "satellite_id": "norad:57166",
    "centre_freq_hz": 137900000,
    "mode": "lrpt"
  },
  "health": {
    "uptime_s": 84213,
    "disk_free_pct": 62,
    "cpu_temp_c": 51.2,
    "rotator": { "present": true, "az_deg": 118.4, "el_deg": 32.1, "homed": true },
    "errors": []
  }
}
```

`state` is one of `idle`, `slewing`, `listening`, `processing`, `degraded`, `maintenance`.

**The `listening` block is the most important field in this protocol.** Without it, an absence of observations is ambiguous. With it, the platform can assert that a station was tuned to a specific frequency for a specific target at a specific time and heard nothing — which is a real measurement.

Response carries any assignments due:

```json
{ "assignments": [ /* see 4.3 */ ], "server_time": "2026-08-14T09:31:02Z" }
```

`server_time` lets a station estimate clock offset without NTP. Stations should report their offset in the next heartbeat.

### 4.3 `assignment`

Platform → station, delivered in a heartbeat response.

```json
{
  "assignment_id": "as_44b2",
  "satellite_id": "norad:57166",
  "start_at": "2026-08-14T09:41:20Z",
  "end_at": "2026-08-14T09:52:07Z",
  "centre_freq_hz": 137900000,
  "mode": "lrpt",
  "expected_max_elevation_deg": 61.4,
  "predicted_yield": 0.91,
  "element_set": { "epoch": "2026-08-14T02:11:00Z", "line1": "1 …", "line2": "2 …" },
  "timing_uncertainty_s": 4.2,
  "priority": 1.0
}
```

**Notes.**

- The element set is carried inline. A microcontroller station cannot be expected to fetch and cache orbital data independently, and this guarantees platform and station propagate from identical inputs — necessary for timing-error measurement to mean anything.
- `timing_uncertainty_s` is the platform's stated 1σ confidence in the window edges. A station should widen its recording window accordingly rather than trusting the boundaries exactly.
- `predicted_yield` is advisory. A station may decline; the platform records declines.

### 4.4 `observation`

Station → platform, after every attempt — **including failures**.

```json
{
  "assignment_id": "as_44b2",
  "station_id": "st_7fa3c1",
  "started_at": "2026-08-14T09:41:18Z",
  "ended_at": "2026-08-14T09:52:10Z",
  "outcome": "decoded",
  "signal": {
    "detected": true,
    "first_detection_at": "2026-08-14T09:41:53Z",
    "peak_snr_db": 11.4,
    "doppler_samples": [
      { "t": "2026-08-14T09:41:53Z", "offset_hz": 3140 },
      { "t": "2026-08-14T09:46:44Z", "offset_hz": 12 }
    ]
  },
  "products": [
    { "kind": "waterfall", "uri": "…", "sha256": "…" },
    { "kind": "image", "uri": "…", "sha256": "…", "frames": 412 }
  ],
  "client_notes": "rotator lagged 2s at AOS"
}
```

`outcome` is one of:

| Value | Meaning |
|---|---|
| `decoded` | Signal received and successfully decoded |
| `signal_no_decode` | Signal present, decoding failed |
| `no_signal` | Station verifiably listening, nothing detected |
| `aborted` | Station started but could not complete |
| `not_attempted` | Station never began — declined, offline, or unhealthy |

**`no_signal` and `not_attempted` must never be conflated.** The first is data. The second is an operational failure.

`first_detection_at` is what makes pass-timing-error measurement possible: the difference between it and the predicted acquisition time, against element-set age, is the project's primary measurement of orbital data quality.

`doppler_samples` are optional and only expected from stations with adequate frequency stability.

---

## 5. Simulated stations

A station may declare itself simulated at registration:

```json
{ "simulated": true, "simulator_run_id": "sim_2026_08_14_a", "seed": 4471 }
```

The platform **must** propagate this flag to every derived record, every API response, and every dashboard element. Simulated observations must never be aggregated with measured observations in any reported figure.

This is a protocol-level requirement rather than a platform convention, because the integrity of every result depends on it.

---

## 6. Error handling

Standard HTTP status semantics. Beyond that:

- A station that receives `401` re-registers.
- A station that cannot reach the platform **continues executing assignments it already holds** and queues observations for later submission. Reception is not blocked on connectivity.
- Queued observations are submitted with their original timestamps; the platform accepts late submissions and records submission delay separately.
- The platform must be idempotent on `assignment_id` — a resubmitted observation replaces rather than duplicates.

---

## 7. Versioning

`MSP-Version: 0.1` header on every request. The platform supports the current major version and one previous. Breaking changes increment the major version.

---

## 8. Reference binding

HTTP/1.1, JSON bodies, four endpoints:

```
POST /msp/v0/register
POST /msp/v0/heartbeat      → assignments
POST /msp/v0/observations
GET  /msp/v0/time           → server time, for clock offset estimation
```

Four endpoints is deliberate. A protocol a student can implement on a microcontroller in an afternoon is more likely to be adopted than a complete one.

---

## 9. Open questions

To be resolved during Phase 1 implementation:

- Should product upload be inline or pre-signed URL? Inline is simpler for constrained clients; pre-signed scales better.
- Should the platform push assignments, or is heartbeat polling sufficient? Polling is simpler and works behind NAT, which most stations are.
- How should a station report a *partial* horizon obstruction it already knows about, without pre-empting the platform's learned profile?
- Should `doppler_samples` be capped in count, or transmitted as a compressed curve fit?

---

*Draft. Reviewed jointly by all three team members before Phase 1 implementation begins, since every module depends on it.*
