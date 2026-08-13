# Meridian Station Protocol (MSP)

**Version 0.2**

An open protocol for satellite ground stations to join a scheduling network.

> **Status.** Frozen at 0.1 on 2026-08-01. Decisions D-003 through D-007 were applied on 2026-07-31; D-015 and D-016 followed, adding the clock fields to §4.2, correcting `not_attempted` in §4.4, defining the observation acknowledgement and `GET /msp/v0/time`, and settling version parsing in §7.
>
> **Completed 2026-08-02** by D-023 through D-032, which closed the remaining undefined recovery behaviour — lost registration responses, `401` semantics, the clock-offset sign, assignment delivery and expiry, observation identity, and request limits — and resolved all four open questions in §9.
>
> All of it is additive except one: **`registration_key` in §4.1 is a new required field**, which under §7's own rule is a major-version change. It is not treated as one, because between the freeze on 2026-08-01 and this amendment no endpoint had been implemented and no client existed to break. This is the last change that gets that argument. From here the version rules apply as written.
>
> The joint review by all three team members that this document originally required before implementation was not held as a meeting. The decision log was written in its place — see the closing note of `docs/DECISIONS.md`.

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

Registration requires an **invite token**, issued out of band by the platform operator and presented in the `register` request. Registration is not open.

On first contact a station registers and receives a `station_id` and a bearer token. The bearer token is presented on every subsequent request; the invite token is used once and never again.

**How the token is presented**, which every implementer needs and which this section previously left to be guessed:

```http
Authorization: Bearer <token>
```

One space between the scheme and the token. The token is opaque — a station must not parse it, and the platform makes no promise about its length or alphabet beyond it being printable ASCII with no whitespace. `register` and `time` take no `Authorization` header; `heartbeat` and `observations` require one.

A request whose header is absent, whose scheme is not `Bearer`, or whose token is unknown or revoked receives `401 unauthorized` (§6). The three are one response on purpose: distinguishing them would tell an unauthenticated caller which station ids exist.

The scheme is matched **case-sensitively as `Bearer`**. RFC 7235 makes HTTP auth schemes case-insensitive, so this is stricter than HTTP requires, and it is stated here because a conformance kit cannot be written against an unstated rule — a station sending `bearer` is refused, and its implementer is entitled to read why in the specification rather than discover it against a running platform.

Tokens, not certificates. A microcontroller station cannot be assumed to hold a certificate store or terminate TLS. Where the transport is unencrypted, the token is the only credential, and the platform must treat station-submitted data as untrusted input regardless.

**A station may only submit observations for assignments issued to it.**

*On invite tokens.* Open registration with rate limiting is a growth decision, not a launch one — see `docs/DECISIONS.md` D-006. An unauthenticated write endpoint on a publicly reachable platform is not defensible, and the network has no scale problem that open registration would solve. Relaxing this later is a policy change at one endpoint, not a protocol change.

---

## 4. Messages

### 4.1 `register`

Station → platform. Once, on first join.

```json
{
  "invite_token": "…",
  "registration_key": "…",
  "name": "nec-rooftop-01",
  "operator": "NTTF NEC",
  "location": { "lat": 12.9716, "lon": 77.5946, "alt_m": 920 },
  "location_precision_decimals": 2,
  "simulated": false,
  "capabilities": [
    {
      "band": "vhf",
      "freq_min_hz": 136000000,
      "freq_max_hz": 138000000,
      "modes": ["lrpt", "fsk", "afsk"],
      "polarisation": "rhcp",
      "tracking": true,
      "min_elevation_deg": 10,
      "horizon_mask": [
        { "az_deg": 0, "min_el_deg": 25 },
        { "az_deg": 90, "min_el_deg": 8 }
      ]
    }
  ],
  "client": { "impl": "meridian-reference", "version": "0.1.0" }
}
```

Response:

```json
{ "station_id": "st_7fa3c1", "token": "…", "heartbeat_interval_s": 30 }
```

**Notes.**

- `min_elevation_deg` is the station's declared floor, not its measured horizon — the platform learns the real obstruction profile from observation history and may override this downward per azimuth. Altitude is required: it materially affects pass geometry.
- `location` is **ISO 6709**: `lat` in −90..+90, `lon` in −180..+180, `alt_m` in metres above the ellipsoid. A longitude outside that range is `malformed`, including one expressed as 0..360 — 200 is not accepted as a spelling of −160, because a platform that silently rewrote it would be guessing which convention a station meant. Stated because the ranges were previously left implicit and the reference implementation accepted 0..360 for longitude by mistake; see `docs/DECISIONS.md` D-052.
- `location_precision_decimals` is **optional**, is **top-level** rather than inside `location` — which stays purely ISO 6709 — and governs **publication only**. It is an integer from 1 to 6; anything else, including a string, is `malformed`. Whatever it says, the platform stores the full precision the station sent and schedules against it: a rounded coordinate reaching pass geometry would shift predicted acquisition by seconds, and the station would be scheduled for a place it is not.

  Decimal places rather than a distance in metres, because rounding a coordinate is then one operation with no geodesy in it — a metre figure would need a `cos(latitude)` term and would have to say what it meant at the poles. At the equator: 1 ≈ 11 km, 2 ≈ 1.1 km, 3 ≈ 110 m, 4 ≈ 11 m, 5 ≈ 1.1 m, 6 ≈ 11 cm. Longitude's true ground distance *shrinks* with `cos(latitude)`, so the declared figure is an upper bound on what is disclosed and never a lower one — which is the safe direction for a privacy control. There is no "exact" value, only a finest one: 6 decimal places is finer than any fix an operator types in by hand.

  **Omitting the field means 2**, roughly 1.1 km — a map pin that lands on the right campus rather than the right building. A station that never stated a preference has not consented to having its rooftop published, so the default sits at the conservative end. Only the operator knows whether the installation is a university roof or a home address, which is why this is declared per station instead of fixed by the platform. Altitude is always published to the nearest metre regardless: sub-metre altitude is instrument noise, not an address. See `docs/DECISIONS.md` D-082.
- `horizon_mask` is **optional** and azimuth-resolved: an obstruction the operator already knows about, because they can see the building. The platform applies `max(declared, learned)` per azimuth bin, so a declaration constrains scheduling immediately but never overwrites a measurement and never becomes training data for the learned profile. A station that omits it is not claiming a clear horizon, only that it has not measured one. See `docs/DECISIONS.md` D-031.
- `simulated` is **required** and **top-level**, alongside `name` and `location`. It is a property of the station, not of the client implementation — a physical station may run the simulator's client build for testing, and a simulated station may be driven by the reference client. See §5 and `docs/DECISIONS.md` D-005.
- `invite_token` is consumed by a successful registration. A rejected or reused token returns `403` with error code `invalid_invite`.

**`registration_key` is required, and it is what makes registration recoverable.** The station generates it once — 32 bytes of cryptographic randomness — and **persists it locally before sending the request**. The platform stores only its hash.

The failure it exists for: the platform commits the registration, the response is lost in flight, and the station is left with a consumed invite and no token. Presenting the *same* invite with the *same* `registration_key` then returns the same `station_id` and a **newly minted** bearer token, so retrying is safe. The same invite with a *different* key is `403 invalid_invite` — that is a second station trying to use a spent invite.

Recovery is available only while the station **has never sent a heartbeat** *and* the request arrives within the platform's recovery window (one hour by default) of `registered_at`. **Both conditions, not either.** A station that has heartbeat holds a working token by definition; a station that never heartbeat but registered a month ago is a consumed invite that would otherwise stay live forever. Requiring both is what stops a leaked invite from rotating credentials at will. See `docs/DECISIONS.md` D-023.

**Past that window, recovery is a bound replacement invite.** An operator issues an invite naming an existing `station_id`; the station presents it with its stored `registration_key` and receives a new token on that same `station_id`, with no second row for one physical installation. A bound invite is exempt from the recovery window — an operator authorised this specific rotation, which is the thing the window exists to require — and is `403 invalid_invite` if the key does not match the station it names. This is the path §6 sends a station down after a `401`. See `docs/DECISIONS.md` D-034.

| Invite | `registration_key` | Result |
|---|---|---|
| Unconsumed, unbound | any | Create a station, store the key hash, return a token |
| Consumed, unbound, in recovery | matches the station that consumed it | Same `station_id`, newly minted token |
| Consumed, unbound, in recovery | differs | `403 invalid_invite` |
| Consumed or out of recovery, unbound | — | `403 invalid_invite` |
| Unconsumed, bound to a station | matches that station | Same `station_id`, newly minted token, window ignored |
| Unconsumed, bound to a station | differs | `403 invalid_invite` |

### 4.2 `heartbeat`

Station → platform. Every `heartbeat_interval_s`.

```json
{
  "station_id": "st_7fa3c1",
  "sent_at": "2026-08-14T09:31:02Z",
  "state": "listening",
  "held_assignments": ["as_44b2", "as_44b9"],
  "listening": {
    "assignment_id": "as_44b2",
    "satellite_id": "norad:57166",
    "centre_freq_hz": 137900000,
    "mode": "lrpt"
  },
  "clock_offset_s": 0.184,
  "clock_uncertainty_s": 0.05,
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

`clock_offset_s` and `clock_uncertainty_s` are optional and may be `null`. **`null` means unknown and is never the same as `0.0`** — a station claiming a perfect clock and a station that cannot measure its own are opposite cases, and treating them alike corrupts every timing measurement derived from them.

**The sign convention, stated once for the whole network:**

```
clock_offset = platform clock − station clock
```

estimated from §8's `/time` endpoint as:

```
offset = server_time − (t_send + t_recv) / 2
```

**A station whose clock runs fast reports a negative offset.** The convention is named here rather than left implied by the formula because the same quantity is written by the client, stored in a column, consumed by the timing analysis and printed in the report — and a sign flip in any one of those is silent, survives review, and inverts a published figure (`docs/DECISIONS.md` D-025).

The estimator is specified here rather than left to the implementer because timing error against element-set age is aggregated across every station in the network, and offsets derived by different methods cannot be pooled. Uncertainty is the station's own 1σ estimate of that offset — typically NTP's reported dispersion. It is an unsigned magnitude, not an interval. `docs/EVALUATION.md` §6.1 discards any measured timing error smaller than the reported uncertainty, which needs both numbers to be present.

**The `listening` block is the most important field in this protocol.** Without it, an absence of observations is ambiguous. With it, the platform can assert that a station was tuned to a specific frequency for a specific target at a specific time and heard nothing — which is a real measurement.

All four of its fields are stored, `mode` included: a station tuned to the right frequency running the wrong demodulator did not observe the pass, and the platform must be able to tell that apart from a miss. The block is all-or-nothing — send every field or omit the block — because a partial block cannot support the assertion the block exists to make.

`health` is opaque to the platform and stored verbatim, and is capped at **4 KiB** serialised; a larger object is `malformed`. See §6 on request limits.

**`held_assignments` is how the platform learns what a station actually has.** It lists every assignment the station currently holds and intends to execute. The platform reconciles it against what it issued:

| Situation | Platform reads it as |
|---|---|
| Issued, and present in the list | The station holds it — state `held` |
| Issued, absent, window still ahead | The station has not accepted it. **Phase 1 changes nothing** — it stays `issued`, is offered again on the next heartbeat, and expires after `end_at` if it is never taken. Reissue to another station arrives with the scheduler (D-022) |
| Absent, window has passed | `expired` — the station never took the work |
| **Present**, window has passed | **Not `expired`.** The station took the work and is finishing with it; its observation may still arrive. Overdue alone is not the test (D-067) |
| Present, but never issued to this station | Protocol error. Log and ignore; do not act on it |

The last row means an id this station was **never** issued. A station still naming an assignment the platform has already closed as `reported` or `expired` has lagged behind a state change, which is not a protocol error and is not logged as one.

The `listening` block moves the assignment it names from `held` to `in_progress` — but only if the same heartbeat also lists that assignment in `held_assignments`. A station listening on something it says it does not hold has contradicted itself in one message, and a contradiction is not evidence for either half. A station may confirm an assignment and report listening on it in the same heartbeat, which is what happens when a pass opens between two polls.

**There is no decline message. A decline is absence from this list.** The heartbeat states current holdings rather than announcing a transition, which makes it idempotent and self-healing: a lost message is not a lost decline, because the next heartbeat carries the same truth thirty seconds later. It also covers cases an explicit decline never would — a station that rebooted and lost its assignments reports identically to one that refused them, and in both cases the platform's correct action is the same.

An empty list is meaningful and must be sent as `[]`, not omitted. It states that the station holds nothing.

This is deliberately distinct from `not_attempted` in §4.4, which is an operational failure reported after the fact. Never conflate them. See `docs/DECISIONS.md` D-003.

Response carries any assignments due:

```json
{ "assignments": [ /* see 4.3 */ ], "server_time": "2026-08-14T09:31:02Z" }
```

**`assignments` contains at most 8 entries.** A constrained client must be able to size its buffer at compile time.

**Delivery policy.** An assignment is returned to its station when

```
state in ('issued', 'held', 'in_progress')   and   end_at >= now   and   start_at <= now + 2 h
```

sorted by `start_at` ascending, capped at 8.

**The lower bound is `end_at`, not `start_at`.** An assignment whose window has opened is the station's *current* work, and dropping it from the response at the moment it became current would contradict the redelivery rule below — a station that lost its state mid-pass would be told it had nothing to do.

**`in_progress` is in the predicate for the same reason** (`docs/DECISIONS.md` D-067). A station executing an assignment has not reported it and its window has not passed, so the redelivery rule below applies to it unchanged. Omitting the state would make the assignment vanish from the response at the moment the station reported listening on it — the same failure as above, reached through the state column instead of the clock.

**An assignment the station already holds is returned again.** Delivery is not once-only: a station sees the same assignment on every heartbeat until it is reported or its `end_at` has passed. This is the same idea as `held_assignments` above, in the other direction — the response states the current work rather than announcing a change, so a lost response is not lost work and the next heartbeat carries the same truth. **A client must deduplicate by `assignment_id`** and must not treat redelivery as a new assignment.

**The cap is not a queue.** Because held assignments are redelivered, returning the earliest 8 of 9 returns the *same* 8 every time: the ninth waits behind them rather than arriving in turn, and may never be delivered at all. MSP 0.x forbids the situation rather than paginating it — **at most 8 assignments may be eligible for one station at any instant**, which is an invariant on whoever creates them: by hand in Phase 1, the scheduler in Phase 2. The platform logs a warning when a station's eligible set exceeds 8, so a violation is visible rather than silent. Pagination, or the per-assignment delivery state it would need, arrives with the scheduler that could produce the overload. See `docs/DECISIONS.md` D-035.

Consequently there is no acknowledgement message and none is needed. An assignment expires when its `end_at` has passed and the station never reported it. Phase 1 does not reissue an expired assignment to another station; that arrives with the scheduler (`docs/DECISIONS.md` D-022, D-026).

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
- `predicted_yield` is advisory. **A station may decline by omitting the assignment from `held_assignments` in its next heartbeat** (§4.2). There is no decline message and none is needed. The platform records the decline when it reconciles.

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
| `not_attempted` | Station never began — offline or unhealthy |

**`no_signal` and `not_attempted` must never be conflated.** The first is data. The second is an operational failure.

**Neither is a decline.** A declined assignment produces no observation at all — it is absence from `held_assignments` in §4.2, and the platform records it as `expired`. `not_attempted` means the station took the work and then failed to start it.

`first_detection_at` is what makes pass-timing-error measurement possible: the difference between it and the predicted acquisition time, against element-set age, is the project's primary measurement of orbital data quality.

`doppler_samples` are optional and only expected from stations with adequate frequency stability. **At most 512 samples**; more is `malformed`. That is one sample every 1.75 seconds across a fifteen-minute pass, beyond both what any receiver here produces and what is useful on a curve this smooth. They are transmitted as samples rather than as a fitted curve because they are the raw measurement, and the residual against a model is what the orbit-uncertainty analysis needs to see (`docs/DECISIONS.md` D-032).

`products` carries **metadata only** — `kind`, `uri`, `sha256`, and whatever else the product type warrants. The platform stores the array as submitted. **MSP 0.x defines no transfer mechanism**; a station with nowhere to put an artefact omits the array entirely, which is valid. When transfer is defined it will be a pre-signed PUT to object storage, off the MSP path, rather than an inline upload — a waterfall is megabytes and this protocol's request bodies are sized for a microcontroller (`docs/DECISIONS.md` D-029).

Response is an acknowledgement:

```json
{ "observation_id": "ob_05601bd09768", "assignment_id": "as_44b2", "superseded": false }
```

`observation_id` is stable and derived from the assignment and revision, so **a resubmission that changes nothing returns the identical id** — the queued-retry case of §6 is idempotent all the way out to the acknowledgement. It is `ob_` followed by twelve hexadecimal characters; the example above is the real id for `as_44b2` revision 1, and is regenerable. It is an opaque string; do not parse it.

`superseded` is `true` when the platform already held an observation for this assignment and this submission replaced it as the current one. A station that resubmits after a period offline can log the difference; a constrained client may ignore the field entirely.

---

## 5. Simulated stations

A station declares whether it is simulated at registration. `simulated` is a **required, top-level** field of the `register` message (§4.1) — never nested, never omitted, never inferred. A simulated station sends two further top-level fields:

```json
{
  "simulated": true,
  "simulator_run_id": "sim_2026_08_14_a",
  "seed": 4471
}
```

`simulator_run_id` and `seed` are required when `simulated` is `true` and absent when it is `false`. Together they are what make a run reproducible.

The platform **must** propagate this flag to every derived record, every API response, and every dashboard element. Simulated observations must never be aggregated with measured observations in any reported figure.

This is a protocol-level requirement rather than a platform convention, because the integrity of every result depends on it.

---

## 6. Error handling

Standard HTTP status semantics. Every error response carries this body, and no other shape:

```json
{ "error": "invalid_invite", "message": "Invite token has already been used." }
```

Two flat string fields. No nesting, no arrays, no optional members. A microcontroller client can extract both with a substring scan and never needs a JSON tree walker — which is the entire reason for the shape.

`error` is a stable, machine-readable code and is the only field a client may branch on. `message` is human text for logs and **must never be parsed**. Codes are additive: a client that meets an unknown `error` treats it as a generic failure for its status class rather than failing closed.

| Code | Status | Meaning |
|---|---|---|
| `invalid_invite` | 403 | Invite token unknown, already used, or withdrawn |
| `unauthorized` | 401 | Bearer token missing, unknown or revoked |
| `not_owner` | 403 | Assignment was not issued to this station |
| `unknown_assignment` | 404 | No such `assignment_id` |
| `malformed` | 400 | Body failed validation |
| `unsupported_version` | 400 | `MSP-Version` outside the supported range (§7) |
| `rate_limited` | 429 | Too many requests |
| `server_error` | 500 | Fault on the platform side; the station should retry |

Beyond that:

- A station that receives `401` **stops, logs, and surfaces the failure to its operator.** It does not re-register and does not retry with the same token. Its own invite was consumed at registration and cannot be reused; recovery is the operator issuing a **replacement invite bound to that `station_id`**, which the station presents together with its stored `registration_key` to rotate its credential under §4.1 — onto the same station row, and without the recovery window that governs the lost-response case. Retrying a revoked token on a 30-second loop is a denial of service a network inflicts on itself. See `docs/DECISIONS.md` D-024 and D-034.
- A station that cannot reach the platform **continues executing assignments it already holds** and queues observations for later submission. Reception is not blocked on connectivity.
- Queued observations are submitted with their original timestamps; the platform accepts late submissions and records submission delay separately.
- The platform must be idempotent on `assignment_id` — a resubmitted observation **supersedes** rather than duplicates. A station never sees two current observations for one assignment. Internally the platform appends rather than overwrites, so the earlier report survives as part of the record; that is invisible on the wire and stations need not account for it.

### Request limits

A request over its limit is rejected as `malformed` before the body is parsed.

| Limit | Value |
|---|---|
| `register`, `heartbeat`, `time` body | 64 KiB |
| `observations` body | 256 KiB |
| `health` object, serialised | 4 KiB |
| `doppler_samples` | 512 entries |

**A request carrying a body must declare its length.** A `POST` without a `Content-Length` header — a chunked body — is `malformed`, because a body of undeclared size cannot be checked against the limit before it is parsed, which is what the paragraph above requires. Every HTTP client that sends a JSON body sends `Content-Length` for it, so this constrains no ordinary station; it is stated because a station implementer streaming a body would otherwise discover it as a rejection. `GET /msp/v0/time` carries no body and is unaffected.

These are stated in the protocol rather than left to the deployment because a station needs to know what it may send before it sends it, and because `health` is opaque JSON written every thirty seconds by every station — unbounded, that is storage exhaustion with no attacker required. See `docs/DECISIONS.md` D-028, D-032 and D-050.

---

## 7. Versioning

`MSP-Version: 0.2` header on every request. The platform supports the current major version and one previous. Breaking changes increment the major version.

**The current minor is 0.2, and 0.1 is still accepted.** 0.2 added one optional field to `register` (§4.1). That is additive, which this section says is a minor bump, so a 0.1 station omits the field, receives the default, and needs no change — see `docs/DECISIONS.md` D-082. This is the rule's first real exercise rather than a hypothetical, and the document version moved with the text so that "0.1" continues to name exactly one document.

The header carries `major.minor`; the path carries the major only, so `MSP-Version: 0.1` is served at `/msp/v0/`. "Current major and one previous" is a statement about the major component. A request whose major falls outside the supported range gets `unsupported_version`; an unrecognised **minor** within a supported major is accepted, because minor versions are additive by definition and a station built against 0.1 must keep working when the platform speaks 0.2. A missing header is `unsupported_version` — sending it is one line of client code, and requiring it is what makes deprecation possible later.

---

## 8. Reference binding

HTTP/1.1, JSON bodies, four endpoints:

```
POST /msp/v0/register
POST /msp/v0/heartbeat      → assignments
POST /msp/v0/observations
GET  /msp/v0/time           → server time, for clock offset estimation
```

`GET /msp/v0/time` takes no body and returns one field:

```json
{ "server_time": "2026-08-14T09:31:02Z" }
```

**It is unauthenticated.** A station that has lost its token still needs to establish clock offset before re-registering, and the response contains nothing that is not already public. It touches no database and is the cheapest endpoint in the system.

Four endpoints is deliberate. A protocol a student can implement on a microcontroller in an afternoon is more likely to be adopted than a complete one.

---

## 9. Open questions

**None remain.** O-1 through O-4 were resolved on 2026-08-02; the resolutions are recorded in `docs/DECISIONS.md` and written into the text above.

| | Question | Resolution |
|---|---|---|
| **O-1** | Product upload inline or pre-signed URL? | Metadata only in 0.x; pre-signed PUT off the MSP path when the `products` table lands. Inline cannot fit under a body size a microcontroller can buffer — §4.4, D-029 |
| **O-2** | Push assignments, or is heartbeat polling sufficient? | Polling, for all of 0.x. Push needs an inbound port or a persistent socket; most stations have neither — §4.2, D-030 |
| **O-3** | How should a station report a partial horizon obstruction it already knows about? | Optional `horizon_mask` per capability, combined as `max(declared, learned)` so a declaration constrains scheduling but never becomes training data — §4.1, D-031 |
| **O-4** | Cap `doppler_samples`, or transmit a compressed curve fit? | Capped at 512 by count. The samples are the raw measurement; fitting at ingest destroys the residual the analysis needs — §4.4, D-032 |

**Resolved since the first draft**, and now written into the text above:

| Was | Now | Where |
|---|---|---|
| No mechanism for the decline that §4.3 promised | `held_assignments` on the heartbeat; a decline is absence from the list | §4.2, D-003 |
| No error response body | Fixed `{error, message}` shape with a code table | §6, D-004 |
| `simulated` placement ambiguous | Required and top-level in `register` | §4.1, §5, D-005 |
| Registration unauthenticated | Invite token, issued out of band | §3, §4.1, D-006 |
| `assignments` array unbounded | Capped at 8 per response | §4.2, D-007 |
| No field carried the clock offset §4.2 asked for | `clock_offset_s` and `clock_uncertainty_s` on the heartbeat | §4.2, D-016 |
| `not_attempted` wrongly included "declined" | Declines never produce an observation | §4.4, D-016 |
| Observation acknowledgement undefined | Three-field ack with `superseded` | §4.4, D-016 |
| `GET /msp/v0/time` response undefined | `{ "server_time": … }`, unauthenticated | §8, D-016 |
| Header `0.1` against path `/v0/` unexplained | Header is `major.minor`, path is major | §7, D-016 |
| "Replaces rather than duplicates" contradicted the append-only observation store | "Supersedes"; identical on the wire | §6, D-015 |
| A lost register response consumed the invite and left the station with no token | `registration_key`; retrying the same invite with the same key rotates rather than fails | §4.1, D-023 |
| §6 told a station to re-register on `401`, which §3's single-use invite made impossible | `401` stops and alerts; the operator issues a replacement invite | §6, D-024 |
| Offset sign was implied by a formula and never named | `clock_offset = platform clock − station clock` | §4.2, D-025 |
| Delivery horizon, redelivery and expiry were undefined | Two-hour horizon, held assignments redelivered, expiry after `end_at` | §4.2, D-026 |
| The acknowledgement's `observation_id` had no definition | Derived from assignment and revision, so a retry returns the same id | §4.4, D-027 |
| `listening.mode` was in the message and nowhere else; no size limits existed | `mode` stored; body, `health` and Doppler caps | §4.2, §6, D-028 |

---

*Version 0.1 was frozen 2026-08-01 by the decision log rather than by a review meeting, and completed on 2026-08-02 when D-023 through D-032 closed the last undefined recovery paths and the four open questions. Every module depends on this document; changes go through a `spec(msp):` pull request with a `D-` entry behind them.*

*Version 0.2, 2026-08-12: `location_precision_decimals` added to `register` (§4.1, D-082). The first change since the freeze to add a field rather than clarify one, and therefore the first to move the version — additively, under §7's own rule, so every 0.1 station keeps working untouched.*
