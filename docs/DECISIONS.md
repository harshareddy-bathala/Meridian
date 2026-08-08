# Decisions

Decisions taken during implementation, with the reasoning behind each.

The specification documents state *what* the system does. This file records *why* it does it that way, and what the alternative was. In a viva the second question always follows the first.

**Rules.** One entry per decision, appended never rewritten. A decision that is later reversed gets a new entry marked `Supersedes: D-NNN`; the original stays, because the fact that we changed our minds is part of the record. Every entry carries a date and a status.

**Status values:** `accepted` · `open` · `superseded`

---

## D-001 — Licence: Apache-2.0

**2026-07-31 · accepted**

The repository is licensed Apache-2.0.

Permissive terms plus an explicit patent grant. MSP is meant to be implemented by people who have not asked our permission, and a reference client nobody can safely adopt is not a reference client. The patent grant is what Apache-2.0 adds over MIT and it costs us nothing.

This is available to us only because GPL-licensed decoders (GNU Radio, SatDump) are invoked as **separate processes** over defined interfaces and never linked. That boundary is now load-bearing for the licence, not just for architectural tidiness.

*Rejected:* MIT — same adoption story, no patent clause, no reason to prefer it here. GPL-3.0 — would restrict adoption by exactly the constrained and institutional stations we want joining the network.

---

## D-002 — Specification documents live in `docs/`

**2026-07-31 · accepted**

`ARCHITECTURE.md`, `MSP-SPEC.md`, `DATA-MODEL.md`, `EVALUATION.md` and the project document moved from the repository root into `docs/`. The project document is now `docs/PROJECT.md` — the `v3` suffix is dropped because git carries version history and a version in a filename invites `v4` beside it rather than replacing it.

`README.md` and `CLAUDE.md` already referenced `docs/…` paths, so every cross-reference between the documents was broken until this move. Nothing about the layout changed; the files caught up with what was already written down.

`README.md`, `CLAUDE.md`, `ATTRIBUTION.md`, `LICENSE` and `.gitignore` stay at root, where tooling and readers expect them.

---

## D-003 — No decline message. Stations report what they hold.

**2026-07-31 · accepted**

MSP §4.3 states that a station may decline an assignment and that the platform records declines, but no message in the protocol carried one.

**Decision.** Add `held_assignments: [assignment_id]` to the heartbeat. The station reports which assignments it currently holds; the platform reconciles that against what it issued. **A decline is simply absence from the list.**

Why this shape rather than an explicit decline message:

- **Self-healing.** A lost decline message is not a lost decline — the next heartbeat carries the same truth. An explicit decline that fails to arrive leaves the platform permanently wrong about a station's intent.
- **Idempotent by construction.** The heartbeat states current holdings, not a transition. Replaying it changes nothing.
- **Costs one array on a message that already exists.** No new endpoint, no new state machine on the client. This matters for the microcontroller station, which is the constraint driving every MSP decision.
- **Covers cases a decline message does not** — a station that rebooted and lost its assignments, or that never received them, reports the same way as one that refused.

`not_attempted` (§4.4) keeps its existing meaning: an operational failure, reported after the fact, distinct from a decline. Conflating the two would corrupt the reliability accounting the protocol exists to protect.

---

## D-004 — Error responses have a fixed two-field shape

**2026-07-31 · accepted**

MSP §6 said "standard HTTP status semantics" and stopped, which left every client to guess at the body.

```json
{ "error": "<code>", "message": "<text>" }
```

`error` is a stable machine-readable code the client may branch on. `message` is human text for logs and is never parsed.

Two flat string fields, no nesting, no arrays. A microcontroller client can extract both with a substring scan and never needs a JSON tree walker. That is the whole reason for the shape.

---

## D-005 — `simulated` is top-level in `register`

**2026-07-31 · accepted**

MSP §5 showed the simulated block as a standalone snippet; §4.1's `register` example omitted it, leaving its placement ambiguous.

**It is top-level, alongside `name` and `location`** — not nested under `client`.

Being simulated is a property of the **station**, not of the client software implementing the protocol. A real station could run the simulator's client build for testing; a simulated station could be driven by the reference client. The flag describes which of those the thing on the other end actually is.

This flag propagates to every derived record, every API response and every dashboard element. Ambiguity about where it lives is the most expensive kind of ambiguity in this protocol.

---

## D-006 — Registration requires an invite token

**2026-07-31 · accepted**

`POST /msp/v0/register` requires an invite token issued out-of-band by the platform operator.

Open registration with rate limiting is a **growth decision, not a Phase 1 decision**. Phase 1 ends with the platform publicly reachable from outside the college network, and shipping an unauthenticated write endpoint to a public address is not a defensible position in a viva or anywhere else.

Nothing here forecloses open registration later. Reversing this is a policy change at one endpoint, not a protocol change.

---

## D-007 — Assignments capped at 8 per heartbeat response

**2026-07-31 · accepted**

MSP §4.2's response returned `assignments[]` with no bound. A constrained client needs to size a buffer at compile time.

Cap is **8**. Comfortably more than a station can execute in one heartbeat interval, small enough to fit constrained memory. Where more are due, the platform returns the 8 soonest and the rest arrive on subsequent heartbeats — which at a 30-second interval is not a delay that matters against an 8-to-15-minute pass.

---

## D-008 — `assignments` carries an explicit state

**2026-07-31 · accepted**

`DATA-MODEL.md` recorded the scheduler's decision but not the station's response, so D-003's reconciliation had nowhere to land.

Add `assignments.state`:

```
issued  →  held  →  in_progress  →  reported
                 ↘
                   expired
```

| State | Meaning |
|---|---|
| `issued` | Platform has issued it; not yet seen in a station's `held_assignments` |
| `held` | Station has confirmed it holds it |
| `in_progress` | Station is executing — heartbeat `listening` block references it |
| `reported` | An observation has been received for it |
| `expired` | Issued, never held, and the window has passed |

`expired` is the decline case from D-003, and it is a distinct outcome from `not_attempted` — the station never took the work, as against took it and failed. The reliability layer needs both, separately.

---

## D-009 — Add an interference measurement table

**2026-07-31 · accepted**

`EVALUATION.md` §2 lists "interference profile — noise floor by azimuth and hour" as one of *our* features, and `ARCHITECTURE.md` names it in the prediction module. No table in `DATA-MODEL.md` held it, so the feature had no source.

Add a hypertable keyed by station, azimuth bin, and time, recording measured noise floor. Populated from observations and from dedicated survey sweeps. Like `horizon_profiles`, the derived profile is versioned so a prediction can be traced to the profile that produced it.

Exact columns are settled when the migrations are written; recording here that the table exists and why, so it is not discovered missing halfway through Phase 2.

---

## D-010 — `observations.outcome` is pinned to the MSP enum

**2026-07-31 · accepted**

MSP §4.4 defines exactly five outcome values — `decoded`, `signal_no_decode`, `no_signal`, `aborted`, `not_attempted`. `DATA-MODEL.md` said only `outcome`.

The five values are written into the schema as a constrained type, and `DATA-MODEL.md` names them explicitly with a pointer to MSP §4.4 as the authority. Two documents holding the same enum will drift; one of them has to be the source and the other has to say so.

---

## D-011 — Restore SC-6 to the evaluation document

**2026-07-31 · accepted**

`PROJECT.md` §9 lists SC-1 through SC-6. `EVALUATION.md` §1 lists only SC-1 through SC-5, dropping **SC-6 — station registered, online, publicly visible**.

SC-6 is restored to `EVALUATION.md`. It is the one criterion that is pass/fail rather than measured, and it is effectively the Phase 1 exit criterion, so its omission from the methodology document is the wrong way round: it is the easiest to verify and the most visible to an examiner.

---

## D-012 — Three distributions, `src/` layout, and `platform` is never an import package

**2026-08-01 · accepted**

**`platform` is a Python standard library module name.** If `platform/` becomes an import package — which happens by default under a flat layout, under `pytest`'s rootdir insertion, and under any `python -m` from the repository root — then `import platform` resolves to ours instead of the stdlib. The stdlib module is imported during interpreter and package startup by `sysconfig`, `setuptools`, `pip`, `uvicorn` and others. The failure is not a clean `ModuleNotFoundError`; it is an `AttributeError` raised from inside a third-party package at import time, with a traceback pointing nowhere near our code.

The fix is a `src/` layout, where the directory name and the import name are deliberately different:

```
pyproject.toml              uv workspace root, all shared tool config
platform/pyproject.toml     distribution: meridian
platform/src/meridian/      orbit, prediction, scheduler, registry,
                            observations, reliability, api, store
client/pyproject.toml       distribution: meridian-client
client/src/meridian_client/
simulator/pyproject.toml    distribution: meridian-sim
simulator/src/meridian_sim/
```

`platform/` stays exactly where `ARCHITECTURE.md` and `CLAUDE.md` put it, and every module keeps its documented name. It is now a *distribution root* rather than a package — no `__init__.py`, never on `sys.path`, so it can never shadow anything. The only documentation change is the `src/meridian/` level inside it, which `CLAUDE.md`'s layout is updated to show. `ARCHITECTURE.md` refers to modules as `platform/orbit` and so on without describing the file tree, so it needs no edit.

**Three distributions rather than one**, because the reference station client must `pip install` on a Raspberry Pi without dragging in `psycopg`, `fastapi` and `sqlalchemy`. Extras can only add dependencies, never remove them, so one distribution cannot deliver this. The split also enforces `ARCHITECTURE.md`'s "the station client knows nothing about the database" **at install time** — an import of `meridian.store` from `meridian_client` fails when the package is built, not when a reviewer happens to notice. A boundary a reviewer can forget to check is a boundary that erodes.

Cost: `README.md`'s `python -m simulator.station` becomes `python -m meridian_sim.station`. A top-level import package called `simulator` is the same class of mistake as `platform`, just less likely to detonate.

*Rejected:* renaming the directory to `meridian/` with a flat layout. It solves the shadowing but needs an edit to the architecture tree in two documents, and it does not give the client its own dependency closure.

---

## D-013 — Schema mechanics: keys, enums, and `simulated` coverage

**2026-08-01 · accepted**

`DATA-MODEL.md` gives column tuples but no types, no keys and no nullability, so the migrations could not be written from it. Settling the mechanics here rather than discovering them one table at a time:

**Surrogate primary keys.** ~~Every table gets `id bigint generated always as identity primary key`.~~ `DATA-MODEL.md` referenced `assignments.pass_id` and `passes.element_set_id` without ever giving those tables a key to reference.

*Amended 2026-08-08 — natural keys where one exists.* The migrations do not implement the rule above and never did, and `DATA-MODEL.md` states the opposite rule while citing this entry for it. Recording the change here rather than leaving the log silently contradicted by the schema it governs.

`stations` is keyed on `station_id`, `satellites` on `satellite_id`, `assignments` on `assignment_id` — identifiers MSP puts on the wire, so a surrogate would have meant every join carrying a second key nobody outside the database can name. `observations` has no `id` column at all: it is keyed `(assignment_id, revision, started_at)`, which D-015's revision lineage requires and the hypertable rule below then extends with the partition column. `passes` and `element_sets`, which have no wire identifier, do take the surrogate. The rule this entry should have stated is *a natural key where the domain supplies one, a surrogate where it does not* — which is what was built.

**Hypertable keys include the partition column.** TimescaleDB requires the partitioning column to be part of any unique or primary key on a hypertable. So `observations`, `heartbeats` and `noise_measurements` are keyed `(id, <partition column>)`. This is a constraint of the storage engine, not a modelling choice, and it is why the natural-looking `primary key (id)` will not work there.

**Enums are `text` with `CHECK` constraints**, not Postgres `enum` types. Adding a value to a Postgres enum cannot be done inside a transaction in the general case, and Rule 9 of `GIT-WORKFLOW.md` forbids editing a merged migration — so every future enum change would be an awkward migration. A `CHECK` constraint is dropped and recreated in one transaction.

**`simulated` extends to `passes`, `assignments` and `heartbeats`.** The convention in `DATA-MODEL.md` says the flag belongs on "every table that can hold simulated data" and `ARCHITECTURE.md` rule 4 says it propagates to "every derived record", but only three tables were named. A pass computed for a simulated station, an assignment issued to one, and its heartbeats are all simulated records. Without the flag on `passes` and `assignments`, no dashboard query can honour the rule that simulated and measured data are never aggregated together.

**The value is always copied from the station's registry record, never read from the payload.** MSP §3 says station-submitted data is untrusted, and provenance is the last field to take on trust. A CI test asserts that no row disagrees with its station.

**Never partition a hypertable on a client-supplied timestamp.** `heartbeats` partitions on `received_at` (platform clock), not `sent_at`. A station with a dead RTC reporting `sent_at: 1970-01-01` would otherwise create a 1970 chunk, and every compression and retention policy would then do the wrong thing to it forever. `observations` partitions on `started_at` because analysis needs it, but ingest rejects anything outside `[now − 30 days, now + 1 hour]` as `malformed` — MSP §3 already requires treating station input as untrusted and `ARCHITECTURE.md` puts validation in the API layer, so this is exactly where it belongs.

**Naming: the derived registry conclusion is `stations.liveness`, not `stations.health`.** Three different things were heading for the same name — the station's *reported* `state` enum (MSP §4.2), the `health` *object* it sends alongside it, and the platform's *derived* conclusion about whether the station is alive. `CLAUDE.md` says keep naming minimal and unambiguous; three meanings for one word is the opposite. Reported enum stays `state`, the reported object stays `health`, the derived column is `liveness`.

**Liveness thresholds come from SC-5.** `stale` after 60 s (two missed heartbeats), `offline` after 90 s (three). Ninety is not arbitrary: SC-5 requires detecting an injected node failure within 90 s, so the success criterion sets the threshold rather than the other way round, and the Phase 3 SLI aligns with it for free.

---

## D-014 — `held_assignments` is stored as an array column on `heartbeats`

**2026-08-01 · accepted**

D-003 makes `held_assignments` the entire decline mechanism and D-008 derives every `assignments.state` transition from reconciling it. The `heartbeats` table in `DATA-MODEL.md` had no column for it — the mechanism the protocol rests on had nowhere to land.

```sql
held_assignments text[] not null default '{}'
```

An array column rather than a `heartbeat_held_assignments` join table. The heartbeat is a point-in-time statement of holdings, always read whole and never queried by element, so a join table would add a second write per heartbeat and buy nothing. At a 30-second interval across fifty simulated stations that write volume is not free.

`not null default '{}'` is load-bearing. MSP §4.2 states that an empty list is meaningful and "must be sent as `[]`, not omitted" — a `NULL` here would silently turn "the station holds nothing" into "the station said nothing", and those have opposite reconciliation outcomes.

---

## D-015 — Observations are append-only; a resubmission supersedes

**2026-08-01 · accepted**

Two documents specified incompatible ingest semantics. `DATA-MODEL.md` said observations are "immutable once written. Corrections are new rows referencing the original." `MSP-SPEC.md` §6 said the platform "must be idempotent on `assignment_id` — a resubmitted observation **replaces** rather than duplicates." Replace and append-only cannot both be true, and neither document provided a column to carry the lineage either way.

**Resolved in favour of append-only**, with lineage carried by a `revision` counter rather than a self-referencing id. The natural key is `(assignment_id, revision)`: a resubmission appends `revision + 1` and the highest revision is the current one.

A pointer column (`supersedes_id references observations(id)`) was the first shape considered and is rejected on modelling grounds, not technical ones. `revision` orders the lineage explicitly rather than leaving it to be reconstructed by walking a chain; it gives the MSP §4.4 acknowledgement something meaningful to return; and `(assignment_id, revision)` has to exist as the primary key regardless, so a pointer would be a second mechanism describing the same relationship.

*Correction, verified against TimescaleDB 2.29 on 2026-08-01:* an earlier draft of this entry rejected the pointer on the grounds that nothing can hold a foreign key pointing at a hypertable. **That is false on current TimescaleDB** — such a foreign key both creates and enforces. It was true before 2.11. The decision is unchanged; the reasoning above is the real one.

A resubmission is also checked against `content_sha256` over the canonical body: a byte-identical resubmission — exactly the queued-retry case MSP §6 describes — returns the existing revision and writes nothing. Only a *changed* resubmission appends.

```sql
create view observations_current as
select distinct on (assignment_id) *
from observations
order by assignment_id, revision desc;
```

`MSP-SPEC.md` §6 is amended to say "supersedes rather than duplicates". **The client-visible behaviour is unchanged** — a station that resubmits still sees one current observation and no duplicate — so this is a wording change at the protocol level and a real change only inside the platform.

Append-only wins because the observation store is the system of record for every reliability figure in the project. An overwrite silently destroys the evidence that a station reported something different the first time, and "we overwrote it" is not an answer in a viva. It also preserves the distinction between `started_at` and `submitted_at` that MSP §6 relies on to record submission delay for queued observations.

---

## D-016 — MSP 0.1 amendments

**2026-08-01 · accepted**

Four gaps found while preparing the implementation. All four are additive or clarifying; none breaks a client written against the current text.

**Add `clock_offset_s` *and* `clock_uncertainty_s` to the heartbeat body (§4.2).** `DATA-MODEL.md` has `heartbeats.clock_offset_s` and §4.2 only said stations "should report their offset in the next heartbeat" without providing a field to report it in. But `EVALUATION.md` §6.1 needs **two** numbers, not one: "stations synchronise time via NTP and report clock offset in heartbeats. **Timing error smaller than the reported clock uncertainty is discarded.**" That reported clock *uncertainty* exists in no message and no table anywhere in the corpus, so §6.1's discard rule was unimplementable as written.

Both are optional top-level `float|null`, in seconds. `null` means unknown and must never be conflated with `0.0` — a station claiming perfect clock accuracy and a station that cannot measure its own are opposite cases. The estimator is specified in the text as `offset = server_time − (t_send + t_recv) / 2`, because if each implementer derives their own the aggregate measurement is meaningless. Without both fields SC-3 cannot be measured.

**Strike "declined" from `not_attempted` (§4.4).** The table read "Station never began — declined, offline, or unhealthy". That contradicts §4.2, §4.3, D-003, D-008 and `DATA-MODEL.md`, all of which state that a decline is `assignments.state = 'expired'` and **never produces an observation row at all**. Since D-010 names MSP §4.4 as the authority for this enum, the authority document was the one carrying the wrong word. Now reads "Station never began — offline or unhealthy."

**Define the observation ack body.** §2's diagram showed an ack; §4.4 never specified it. Per D-004's two-field discipline for errors, the success body is equally flat:

```json
{ "observation_id": "ob_9c21", "assignment_id": "as_44b2", "superseded": false }
```

`superseded` tells a client that resubmitted after a queued reconnection that the platform already had an earlier report — useful for its logs, ignorable by a microcontroller.

**Define `GET /msp/v0/time`.** Response `{ "server_time": "2026-08-14T09:31:02Z" }`. **Unauthenticated** — a station that has lost its token still needs to establish clock offset before re-registering, and the response contains nothing sensitive.

**Version parsing.** `MSP-Version: 0.1` is parsed as `major.minor`; the path carries only the major (`/msp/v0/`). "Current major and one previous" is a statement about the major component. A request whose major does not match a supported version gets `unsupported_version`; an unrecognised minor within a supported major is accepted, because minor versions are additive by definition.

---

## D-017 — Station bearer tokens are opaque secrets, stored hashed

**2026-08-01 · accepted**

`DATA-MODEL.md` stores a "token hash" on `stations`. `deploy/.env.example` declares `TOKEN_SECRET`, which "signs station bearer tokens". Those are two different designs — hashed opaque tokens and signed tokens — and only one can be built.

**Opaque tokens.** 32 bytes from `secrets.token_urlsafe`, stored as a SHA-256 hash, compared in constant time. `TOKEN_SECRET` is removed from `deploy/.env.example`.

*Clarified 2026-08-08.* "Compared in constant time" described the implementation loosely enough to be wrong in one place and unnecessary in another, and the code matched neither reading until this correction. Precisely:

- **The bearer token is never compared in Python.** `store.stations.find_station_id_by_token_hash` hashes the presented token and *looks the hash up* in an indexed `bytea` column. There is no comparison to make constant-time; the timing that remains is an index probe, which does not vary with how many leading bytes matched.
- **The registration key is compared**, in `registry.psycopg_registry._key_matches`, because it is fetched by `station_id` and then checked. That comparison was `bytes.__eq__` — which short-circuits — and is now `hmac.compare_digest`. This is the one that matters: the registration key authorises minting a new bearer token on an existing station (D-023, D-034), so a timing oracle on it is a credential-recovery path rather than an information leak.

The advantage of a signed token is validation without a database read. That advantage does not exist here: `platform/registry` is "the authority on whether a station was listening at a given moment" and must load the station row on every heartbeat anyway to update health state and last-heartbeat age. So signing buys no round trip and adds a signing-key rotation problem, on a platform whose entire point is running unattended.

Opaque tokens are also revocable immediately by deleting a row, which matters more on a publicly reachable endpoint than statelessness does. Note that "stateless server" in MSP §1 means no per-session state between requests, not no database — the platform is a database-backed service either way.

---

## D-018 — Phase 1 builds 8 of the 13 tables

**2026-08-01 · accepted**

Built now: `stations`, `station_capabilities`, `satellites`, `element_sets`, `passes`, `assignments`, `observations`, `heartbeats`. These are exactly what the Phase 1 exit criterion needs — a station registers, heartbeats, holds an assignment against a computed pass, and reports an observation.

Deferred, with reasons:

- **`products`** — blocked on O-1, which is unresolved and which MSP §9 says must be settled together with this table. Rule 9 makes a wrong migration permanent, so the table waits for the decision rather than guessing at it. Instead, the observation endpoint **validates the `products` array and stores it verbatim** in `observations.products_json jsonb`, so nothing a station sends is lost while the transfer mechanism stays open. Phase 1 produces no products anyway — the simulator generates no waterfalls and there is no receiver until Phase 4, and a transfer protocol frozen before a real station exists will be frozen wrong.
- **`noise_measurements`, `interference_profiles`** (D-009) — the source data comes from a physical receiver doing survey sweeps. There is no receiver until Phase 4.
- **`horizon_profiles`** — derived from observation outcome history, of which there is none yet. Phase 2, with the prediction module that consumes it.

`satellites.transmitters` is `jsonb` for now rather than a normalised table. `DATA-MODEL.md` describes "known transmitters (frequency, mode, polarisation)" without giving a structure, and nothing in Phase 1 queries across transmitters — the assignment message reads one centre frequency and mode. It is normalised when the scheduler needs to select on it.

---

## D-019 — Migrations are raw SQL applied by Alembic

**2026-08-01 · accepted**

Migration files are hand-written SQL. Alembic supplies revision ordering and the applied-revision table; each revision is a thin wrapper that executes its `.sql` file.

Declarative SQLAlchemy models with autogenerated migrations were the obvious alternative and are rejected on two grounds. First, autogenerate cannot model what this schema actually needs — `create_hypertable`, compression policies, retention policies and the derived views are all Timescale-specific SQL it would emit as opaque `op.execute` blocks anyway, so the "generated" migration is hand-written for the parts that matter and machine-written for the parts that do not. Second, `GIT-WORKFLOW.md` Rule 10 says nothing gets committed that its author cannot explain line by line, and an autogenerated migration is precisely the artefact nobody on a three-person team can walk a reviewer through.

Raw SQL also keeps `create_hypertable` and the `CHECK` constraints from D-013 readable in the file where they take effect, which matters when the schema is the thing being defended.

---

## D-020 — Invite tokens are rows in a table, not a configuration value

**2026-08-01 · accepted**

MSP §4.1 requires that an invite token is **consumed** by a successful registration, and that a reused one returns `403 invalid_invite`. `deploy/.env.example` provided a single static `REGISTRATION_INVITE_TOKEN`.

A static environment variable cannot be consumed, cannot be revoked, cannot be issued per operator, and — the part that matters — **cannot admit a second station**. Either the platform rejects every registration after the first, or the token is not single-use and D-006 is not implemented. As written, the mechanism that D-006 calls the defensible alternative to an unauthenticated write endpoint could not work at all.

```sql
invite_tokens (token_sha256 primary key, label, created_at,
               expires_at, consumed_at, consumed_by_station_id)
```

plus a `meridian invite create` command. `REGISTRATION_INVITE_TOKEN` is kept but redefined: on an empty database it seeds exactly one invite row, so `docker compose up` still yields a platform a station can register against without an extra manual step — which the ten-minute bring-up requirement needs.

This blocks registration outright, so it lands before D-006 can be said to be implemented at all. Phase 3's fifty simulated stations need fifty invites; there was no mechanism to issue them.

---

## D-021 — `assignments` carries its own window, and transmitters are a table

**2026-08-01 · accepted**

`assignments` could not produce the §4.3 assignment message. Three fields had no column — `centre_freq_hz`, `mode`, `timing_uncertainty_s` — and two more were being taken from the wrong place.

**`start_at` and `end_at` are columns on `assignments`, not the pass's `aos`/`los`.** They are not the same thing. `GIT-WORKFLOW.md`'s own worked example describes widening an assignment window by `timing_uncertainty_s` precisely because a station recording at exactly the predicted AOS starts after the pass has already begun. The documentation described a column the schema did not have. D-008 compounds it: `expired` is defined as being set "after `end_at`", against a table with no `end_at`.

**`satellites`' "known transmitters" becomes a child table** `satellite_transmitters(satellite_id, centre_freq_hz, mode, polarisation, bandwidth_hz, active, source)`, not the `jsonb` column first proposed in D-018. The scheduler must join transmitters against capability frequency ranges — `freq_min_hz <= centre_freq_hz <= freq_max_hz` — and that predicate is not indexable inside a JSON blob. `active` also carries the silent-satellite status `DATA-MODEL.md` asks `satellites` to track, which `EVALUATION.md` §5 needs.

*Supersedes the `satellites.transmitters jsonb` part of D-018; the rest of D-018 stands.*

---

## D-022 — Phase 1 expires assignments only after `end_at`; reissue is Phase 2

**2026-08-01 · accepted**

MSP §4.2's reconciliation table says that when an assignment was issued, is absent from `held_assignments`, and its window is **still ahead**, the platform should "reissue elsewhere or mark `expired`". D-008's state machine has no arc for that. There is no state meaning "we took it back and gave it to another station", so a reissued assignment either lingers as `issued` forever or receives an `expired` that misreports when it happened.

**Phase 1 does not reissue.** An assignment expires only after `end_at`, exactly as D-008 defines it. Reissue, and whatever state it needs — `revoked` is the obvious candidate — is a Phase 2 decision that arrives with the scheduler.

This is the smaller change and Phase 1 has one station, so there is nowhere to reissue *to*. Recording it because the gap is real and a reader comparing MSP §4.2 against D-008 will otherwise find the contradiction and assume it was missed.

---

## D-023 — Registration recovery: a client-generated registration key

**2026-08-02 · accepted**

The registration response carries the only copy of the bearer token. If the database commit succeeds and the response is then lost — a dropped connection, a timeout, a client crash between receiving bytes and writing them to disk — the invite is consumed and the station has no credential. Nothing in the protocol recovered from that.

**The client generates a `registration_key` and sends it in the register body.** 32 bytes from `secrets.token_urlsafe`, persisted by the client *before* the request is sent. The platform stores `sha256(pepper ‖ key)` on the station row, never the key.

```
invite unconsumed              → create station, store key hash, return a new token
invite consumed, key matches   → same station_id, mint and return a NEW token
invite consumed, key differs   → 403 invalid_invite
```

Recovery is permitted only while `stations.last_heartbeat_at is null` **and** `now() - registered_at <= REGISTRATION_RECOVERY_WINDOW_S` (default 3600). **Both, not either.** A station that has heartbeat has a working token by definition; a station that never heartbeat but registered a month ago would otherwise leave its consumed invite live indefinitely, which is the thing the window exists to prevent. Requiring both is what stops a leaked invite from rotating credentials at will.

*Amended by D-034*, which corrects an `or` here to an `and` — the two clauses were written as alternatives and described in the same paragraph as a single window, which are not the same rule — and which defines the separate path for a station past the window. This entry stands otherwise.

The property this buys is that **`register` becomes idempotent from the client's side**, which is the actual requirement. Retrying is safe, no plaintext token is ever stored, and the invite is never consumed twice.

*Rejected:* an operator-issued replacement invite. Zero implementation cost, but it leaves an orphaned station row per incident, needs a human in the loop for a failure mode caused by a dropped packet, and does not survive Phase 3's fifty simulated stations registering unattended.

*Rejected:* a short-lived recovery secret returned alongside the token. It travels in the same response that was lost, so it does not address the stated failure — it moves it one level down and adds an endpoint.

*Rejected:* making the field optional to keep the change additive. That leaves the platform with two registration paths, one recoverable and one not, and the unrecoverable one is the default a hurried implementer picks. Generating 32 random bytes is within reach of every station that can speak the protocol at all.

**On the version.** A new *required* field is a major-version change under MSP §7. It is not treated as one here because between the 0.1 freeze on 2026-08-01 and this entry, not one endpoint had been implemented and no client existed to break — the specification was frozen by a decision log, not by a deployment. Recorded explicitly rather than glossed, because it is the kind of exemption that gets claimed twice if it is not written down as being claimed once.

---

## D-024 — A `401` does not mean re-register

**2026-08-02 · accepted**

MSP §6 said "a station that receives `401` re-registers". §3 says an invite is used once and never again. A station holding a revoked token has no invite to present, so the instruction was unfollowable.

**`401` means stop, log, and surface to the operator.** The reference client does not re-register and does not retry with the same token. Recovery is the operator issuing a replacement invite, which the station presents together with **its existing `registration_key`**, rotating its credential rather than creating a second station row for the same physical installation.

*The mechanism is D-034, not D-023.* This entry originally pointed at D-023's flow, which cannot serve it: a replacement invite is unconsumed, so D-023 reads it as a new registration and creates a duplicate station. D-034 binds the replacement invite to a `station_id` so it resolves to the right row.

The client must not loop here. A station retrying a revoked token every thirty seconds against a publicly reachable endpoint is a denial of service the network inflicts on itself, and with fifty simulated stations it is a denial of service with a multiplier.

---

## D-025 — Clock offset sign, named once

**2026-08-02 · accepted**

```
clock_offset = platform clock − station clock
```

This is already what MSP §4.2's estimator computes — `offset = server_time − (t_send + t_recv) / 2` — but the convention was never named, only implied by a formula. Naming it matters because the same quantity is written in five places: the client that estimates it, the column that stores it, the timing analysis that consumes it, the tests, and the report. A sign flip in any one of them is silent, survives review, and inverts a published figure.

**A station whose clock runs fast reports a negative offset.** That sentence is the test case.

`null` continues to mean unknown and is never `0.0` (D-016). `clock_uncertainty_s` is unsigned — it is a 1σ magnitude, not an interval.

---

## D-026 — Assignment delivery policy

**2026-08-02 · accepted**

MSP §4.2 defined the reconciliation table but not the delivery policy behind it, leaving six questions that would each have been answered differently by whoever wrote the endpoint first.

| Question | Answer |
|---|---|
| How far ahead are assignments returned? | `start_at <= now + 2 h` |
| Are held assignments redelivered? | **Yes**, while in-horizon and not yet `reported` |
| How does a lost heartbeat response recover? | It does not need to — redelivery covers it |
| More than 8 due? | ~~The 8 with the earliest `start_at`; the rest follow (D-007)~~ — **superseded by D-035**: more than 8 eligible is forbidden, not paginated |
| When does an assignment expire? | `now > end_at` and state is `issued` or `held` |
| When is it eligible for reissue? | Never in Phase 1 (D-022) |
| Does Phase 2 add `revoked`? | Yes, with the scheduler — not now |

*Amended by D-035.* The horizon above is stated as a bound on `start_at` alone, which excludes an assignment already under way and so contradicts the redelivery rule two rows above it. D-035 restates the eligibility predicate and resolves the cap.

**Redelivery is the load-bearing choice.** It makes the heartbeat idempotent in the same way D-003 made the decline idempotent: a lost response is not lost work, because the next heartbeat thirty seconds later carries the same assignments. The alternative — deliver once, then track per-station delivery receipts — needs an acknowledgement the protocol does not have and a table to hold it, to solve a problem that redelivery solves for free. The client deduplicates by `assignment_id`, which it must do anyway.

Two hours is roughly one and a quarter LEO orbits: far enough ahead that a station is never idle waiting for work, near enough that a station holding an assignment has current element sets for it, and small enough that eight slots are rarely the binding constraint.

---

## D-027 — `observation_id` is derived, not allocated

**2026-08-02 · accepted**

MSP §4.4's acknowledgement returns an `observation_id`. The table's key is `(assignment_id, revision, started_at)` (D-013, D-015) and carries no public identifier, so the acknowledgement had nothing to put in the field.

```sql
observation_id text generated always as (
    'ob_' || substr(encode(sha256(convert_to(
        assignment_id || ':' || revision::text, 'UTF8')), 'hex'), 1, 12)
) stored
```

**Derived rather than allocated.** An idempotent retry — the queued-reconnection case of MSP §6 — must return the *same* `observation_id` as the original submission. A derived id has that property by construction; a random one requires reading the existing row back before answering, on the exact path D-015 optimised for writing nothing. It is also regenerable from a dataset snapshot, which `CLAUDE.md` rule 8 requires of every number in a report.

`generated … stored` rather than computed in Python, so the relationship is enforced by the database and cannot drift between the ingest path and the public API, and so the column is indexable for the Stage 11 read endpoints. Twelve hex characters is 48 bits — collision-free across any observation volume this network will produce, and short enough to read aloud in a viva.

*Rejected:* `ob_<assignment_id>_<revision>`. It is derived and stable too, but it publishes the internal key structure in a public identifier, so the format can never change without breaking clients that parsed it.

---

## D-028 — Heartbeat completeness and request size limits

**2026-08-02 · accepted**

Four gaps found while tracing MSP §4.2 against the `heartbeats` table.

**`listening_mode` is stored.** MSP §4.2's listening block carries `mode`; the table stored the assignment, satellite and frequency and dropped it. `Registry.was_listening()` is the sole authority on what counts as a confirmed miss, and a station tuned to the right frequency running the wrong demodulator did not observe the pass. The column joins the existing all-or-nothing `heartbeat_listening_complete` CHECK — a partial listening block cannot support the assertion the block exists to make.

**`simulated` is stored on `heartbeats`.** D-013 already ruled that the flag extends to heartbeats; the table did not have it. Copied from the station's registry record, never read from the payload.

**The health object is capped at 4 KiB** serialised; above that the heartbeat is `malformed`. `health` is opaque diagnostic JSON stored verbatim, written every thirty seconds by every station. Fifty stations at an unbounded object size is a storage exhaustion with no attacker required — just one station with a verbose error array and a loop.

**Request bodies are capped:** 64 KiB for `register`, `heartbeat` and `time`; 256 KiB for `observations`, which carries the Doppler array (D-032). Enforced as middleware, ahead of JSON parsing, so an oversized body is rejected before it is allocated.

---

## D-029 — O-1 resolved: products are metadata in MSP 0.x; transfer is a pre-signed PUT

**2026-08-02 · accepted**

Phases 1 and 2 store the `products` array verbatim as submitted — `kind`, `uri`, `sha256`, optional `frames` — in `observations.products_json`, exactly as D-018 routes around it. **No transfer mechanism is defined in MSP 0.x.** A station that has nowhere to put a product omits the array, which stays valid.

The *direction* is settled now, because D-018 says the `products` table cannot be designed until it is: **pre-signed PUT to object storage**, not inline upload.

Inline was the simpler option for a constrained client and is rejected on arithmetic. A waterfall PNG for a fifteen-minute pass is single-digit megabytes; base64 inflates it by a third; D-028 caps an observation body at 256 KiB. Raising the cap to fit a product would mean sizing every request buffer in the system for the largest artefact any station might ever produce, on a protocol whose first design constraint is a microcontroller with kilobytes of RAM. Pre-signed URLs keep the bulk transfer off the MSP path entirely, and a station that cannot do a plain HTTP PUT to a URL it was handed is a station that cannot speak MSP either.

O-1 is closed. The `products` table is designed against this at Stage 19, when a receiver exists to produce a product.

---

## D-030 — O-2 resolved: polling, for all of MSP 0.x

**2026-08-02 · accepted**

Heartbeat polling. Confirmed rather than "leaning", and not revisited within the 0.x line.

Most stations are behind NAT, and a push channel needs either an inbound port — which is the exact constraint SC-6 and the tunnel exist to work around — or a persistent outbound socket held open indefinitely, which a microcontroller with no TLS library and kilobytes of RAM cannot do. D-007's cap of 8 already assumes polling and would be meaningless without it.

A thirty-second poll interval against an 8-to-15-minute pass is a delivery latency of at most one heartbeat on work that begins minutes later. There is no problem here for push to solve.

---

## D-031 — O-3 resolved: a declared horizon mask, kept distinct from the learned one

**2026-08-02 · accepted**

A capability may carry an optional azimuth-resolved obstruction it already knows about:

```json
"horizon_mask": [ { "az_deg": 0, "min_el_deg": 25 }, { "az_deg": 90, "min_el_deg": 8 } ]
```

Stored as `station_capabilities.horizon_mask_json jsonb not null default '[]'`. Optional and additive, so no client written against MSP 0.1 breaks.

**Declared and learned never merge into one number.** The Phase 2 `horizon_profiles` table carries `source in ('declared', 'learned')`, and the scheduler takes `max(declared, learned)` per azimuth bin. A declaration therefore constrains scheduling immediately — which is the operator's legitimate need, they can see the building — but never overwrites a measurement and never appears in a learned profile's training data. That separation is what "without pre-empting the learned profile" has to mean; storing the declaration into the same column the model writes would make the model's own output an input to itself.

The flat list is deliberately coarse. A station that knows its horizon to a degree is unusual; a station that knows there is a building to the north is normal.

---

## D-032 — O-4 resolved: `doppler_samples` capped at 512 by count

**2026-08-02 · accepted**

Capped by count. More than 512 samples in one observation is `malformed`.

512 samples across a fifteen-minute pass is one every 1.75 seconds — well beyond what any receiver in this project produces, and beyond what is useful, since the Doppler curve is smooth on that timescale. At roughly 50 bytes per sample it is about 25 KiB, comfortably inside D-028's 256 KiB observation body.

*Rejected:* transmitting a compressed curve fit. The samples are the **raw measurement**. Fitting at ingest bakes a model into the system of record, and the residual between the samples and any model is precisely what Stage 22's orbit-uncertainty report needs to look at. A curve fit also moves work onto the constrained client to save bytes that D-028's cap says we have.

---

## D-033 — `DATABASE_URL` is one value; the driver prefix is normalised, not configured

**2026-08-02 · accepted**

`deploy/docker-compose.yml` gave the `migrate` service `postgresql+psycopg://…` and the `api` service `postgresql://…` — the same variable name holding two different values, because SQLAlchemy needs the driver prefix and `psycopg.connect` rejects it. CI has one `DATABASE_URL` and therefore could serve only one of the two, which is why the CI job could not apply migrations before running the tests that require them.

`meridian.config` normalises instead:

```python
def libpq_url(url: str) -> str:      # strips "+psycopg" — for psycopg.connect
def sqlalchemy_url(url: str) -> str: # adds "+psycopg"   — for alembic
```

`deploy/migrations/env.py` imports `sqlalchemy_url` rather than rebuilding the URL from `POSTGRES_*` a second time. Alembic ships as a dependency of the `meridian` distribution, so the import direction is one that already exists.

*Rejected:* two environment variables, `DATABASE_URL` and `ALEMBIC_DATABASE_URL`. Two names for one connection is how a staging database gets migrated while a production one is queried, and nothing ever detects it.

---

## D-034 — A replacement invite is bound to a station; recovery and rotation are different rules

**2026-08-03 · accepted**

D-024 sends a station that received `401` to "an operator-issued replacement invite presented with its existing `registration_key`", and says that rotates the credential through D-023. It cannot. D-023 defines two cases and this is neither:

```
invite unconsumed            → create a station
invite consumed, key matches → recover that station
```

A replacement invite is **unconsumed**, and the register body carries no `station_id`. Following the specification as written creates a second station row for one physical installation — precisely the outcome D-024 says it exists to avoid. The recovery path also cannot serve it: a station that has been running long enough to have its token revoked has heartbeat, and has been registered for longer than the recovery window.

**`invite_tokens` gains `issued_for_station_id`, nullable.** An unbound invite (`null`) admits a new station, exactly as before. A **bound** invite names an existing station and admits only that one:

```
bound invite, key matches the named station → same station_id, mint a NEW token
bound invite, key differs                   → 403 invalid_invite
```

A bound invite is exempt from D-023's recovery window. That is not a loosening: the window's whole purpose is to require authorisation for a rotation that is not a dropped-packet retry, and an operator issuing an invite against a named station *is* that authorisation, given explicitly instead of inferred from a clock.

**The two flows stay separate because they answer different questions.** D-023 recovers from a lost response, unattended, within an hour, and must work for fifty simulated stations registering at once with no human present. D-034 rotates a compromised or revoked credential, is rare, and should require a human — the operator has to decide the station is who it claims to be. Collapsing them into one rule is what produced the contradiction: a single window cannot be both short enough to contain a dropped packet and long enough to cover a revocation six months later.

`meridian invite create --for-station st_7fa3c1` issues one. The column is a foreign key to `stations`, so an invite naming a station that does not exist cannot be created.

*Rejected:* a separate `POST /msp/v0/rotate` endpoint. It is the cleaner factoring on paper, but MSP has four endpoints on purpose (§8) and this adds a fifth that every conforming implementation must carry to handle an event most stations never see. Registration already accepts an invite and a key and already returns a token; a bound invite reuses that shape without adding a line to a microcontroller client.

*Rejected:* letting the station send its `station_id` in the register body and matching on `registration_key` alone. That makes the key a permanent password rather than a one-time recovery secret, and a key leaked once is then a credential rotation available to anyone forever, with no operator in the loop and nothing to revoke.

---

## D-035 — Assignment delivery eligibility, and why the cap is an invariant rather than a queue

**2026-08-03 · accepted**

Two defects in D-026's delivery policy, both of which would have shipped as written.

**The horizon excluded work in progress.** D-026 bounds the response by `start_at within [now, now + 2 h]` while promising redelivery "until it is reported or its window has passed". Those disagree the moment a window opens: at `start_at + 1 s` the assignment fails the `start_at >= now` test and vanishes from the response, so a station that rebooted mid-pass is told it has nothing to do. The predicate is now:

```
state in ('issued', 'held')  and  end_at >= now  and  start_at <= now + 2 h
```

Bounded below by `end_at` and above by `start_at`. The two-hour reasoning in D-026 is unchanged; only which column it applies to.

**The cap of 8 starves.** D-007 caps the response at 8 and D-026 says "the rest follow" on subsequent heartbeats. That is true of a queue that drains, and false here: D-026 also redelivers held assignments, so the earliest 8 are returned again on every heartbeat and a ninth is never in the response at all. It is not delayed by 30 seconds; it is delivered only if one of the 8 ahead of it disappears first, which for non-overlapping passes usually happens and for overlapping ones may not. "The rest follow" was inherited from a deliver-once model that D-026 had already replaced.

**More than 8 eligible for one station is forbidden, not paginated.** The platform logs a warning and delivers the earliest 8; the invariant belongs to whoever creates assignments — a human in Phase 1, the scheduler in Phase 2.

This is the honest fix for Phase 1. Real pagination needs per-assignment delivery state, which is the acknowledgement table D-026 explicitly declined to build, to solve a problem Phase 1 cannot yet have: one station, a two-hour horizon, and 8–15-minute passes give at most a handful of eligible assignments, and nothing in Phase 1 creates them automatically. Building the machinery now would mean designing it against a scheduler that does not exist. Recording it as an invariant with a warning means the day it is violated, the log says so.

*Rejected:* raising the cap. 8 is a buffer size a microcontroller commits to at compile time (D-007). Any finite cap has this property; raising it moves the starvation point without removing it.

*Rejected:* `order by (last_delivered_at nulls first, start_at)` with a delivery timestamp on `assignments`. It does fix starvation, and it is where Phase 2 should go. It is one column and one write per heartbeat per assignment — a write on the hot path, against a table Phase 1 has no automated writer for, to prevent a state Phase 1 cannot reach. Deferred with the scheduler, not rejected on the merits.

---

## D-036 — The public site and the dashboard are two surfaces, not one

**2026-08-04 · accepted**

`meridian.org.in` was registered. The obvious move is to point it at the platform and call that the public site, which would quietly conflate two things with different jobs and different availability requirements.

**Decision.** Two surfaces, deployed independently:

| Surface | Content | Serves from | Source |
|---|---|---|---|
| `meridian.org.in` | a static page describing the project | Cloudflare Pages | `site/` |
| `dash.meridian.org.in` | the live dashboard — stations, passes, reliability | cloudflared tunnel → the Pi | `dashboard/` |

The separation is about what each is allowed to depend on. The dashboard is a view onto the observation store and is therefore only up when the platform is up — that is correct, and SC-6 is stated against it: "a virtual station is visible on the public site from outside the college network" is satisfied by the tunnelled dashboard, not by `site/`. The description of the project has no such dependency and must not acquire one. A page that explains what Meridian is should not go dark because a Raspberry Pi on a roof lost power, and during Phase 1 that Pi does not exist at all.

`site/` is therefore plain HTML, CSS and vanilla JS with no build step and no npm — the whole directory is what gets served. This is deliberately not a stack decision. `docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md` asks for the dashboard's front end to be chosen and recorded; that question stays open, and nothing here constrains the answer. Choosing a framework for a page with one heading and one link would have prejudged it.

The zone is on Cloudflare rather than the registrar because the apex is needed for Pages and `dash.` is needed for the tunnel that `deploy/docker-compose.yml` already defines. One zone, both surfaces.

*Rejected:* serving the landing page from FastAPI as a static route. One deployment, one domain, no Pages account — and it makes the project's public description a runtime dependency of the station being alive. That trades the independence the project claims for the convenience of one fewer moving part.

*Rejected:* GitHub Pages. Free and adequate for the static page, but it cannot host the tunnel subdomains, so the zone ends up split across two providers with the apex on the weaker one.

*Rejected:* `www.meridian.org.in` as canonical. The apex is what people will type and what goes in the report. `www` redirects to it.

---

## D-037 — The public site has two themes

**2026-08-05 · accepted**

`site/style.css` opened with a statement that there is no light mode, by choice: *"This is an instrument panel, not a document."* That was true of a page with one heading and one link. It stopped being true when the site acquired documentation.

The instrument-panel reading still holds for the front page — a globe on near-black, one screen, nothing to read at length. It does not hold for four pages of specification prose, which people read in daylight, on borrowed screens, and beside other documents. A single committed dark theme was a defensible position for a business card and is an imposition on a reference text.

**Decision.** Two themes. `prefers-color-scheme` decides by default; a toggle in the masthead overrides it and persists in `localStorage`.

The mechanism is constrained by the CSP. `_headers` allows no `unsafe-inline`, which rules out the usual three-line inline script that sets the theme before first paint — and without something running before first paint, every visitor whose stored preference differs from their system setting sees a flash of the wrong theme. The resolution is `site/theme.js`: an external file, loaded in `<head>` **without `defer`**, so it blocks parsing exactly long enough to set `data-theme` on the root element. One extra same-origin request of about 400 bytes, and the no-`unsafe-inline` posture is untouched.

**The canvas now reads its palette from CSS.** `main.js` held its own copy of the seven colours as RGB triplets. A second theme would have made that fourteen values in two places — plus the swatches in the footer legend, which claim to explain what the colours on the canvas mean. `readPalette()` pulls them from the computed custom properties instead, and `theme.js` dispatches a `themechange` event that triggers a re-read. `site/tools/make-images.py` keeps its own constants; it is a build-time tool with no DOM to read from.

**Two contrast failures were fixed on the way.** `--muted` against `--bg` measured **4.24:1**, under the 4.5:1 WCAG AA requires for the 15 px body text it was used for; it is now `#787E8A` at 4.88:1. `.legend dd` was painted in `--rule`, a hairline colour, at about **1.3:1** — text that could not be read at all. Both were pre-existing, and neither was found by looking at the page. They were found by computing the ratios. The full table for both themes is in `site/README.md`.

*Amended by D-039*, which raises the hairline and body-text values again after the two themes were seen side by side.

*Rejected:* system preference only, with no toggle. Half the code and no flash to worry about, but a visitor whose operating system is dark can never see the light theme, and nothing signals that the site has one.

*Rejected:* relaxing the CSP to allow an inline theme script. It is the ordinary solution and it would work. It also means `script-src` gains `unsafe-inline`, or a nonce a static host cannot generate, and the claim that the site makes no third-party requests stops being enforced by a header and becomes a promise. A 400-byte file is cheaper than that.

---

## D-038 — The public site is five pages, and what it does not say

**2026-08-05 · accepted**

`site/` was one page. It is now `/`, `/architecture/`, `/protocol/`, `/docs/`, `/about/` and a 404, sharing a masthead, nav and footer.

**The front page stays one non-scrolling screen.** No sections below the fold. The globe and the single screen are the whole impression, and putting a scrolling marketing page underneath would have spent that to gain content the subpages carry better. It gains nav, a tightened paragraph, two links and a real footer. Nothing else.

**Documentation on the site is editorial, not mirrored.** `/architecture/` and `/protocol/` are written for a reader arriving cold. They are not renderings of `docs/ARCHITECTURE.md` and `docs/MSP-SPEC.md`, and they link to those as the authoritative text. The alternative was a Markdown build step producing committed HTML, which would keep site and specification in exact agreement — and D-036 committed `site/` to no build step and no npm, on the grounds that choosing a stack for a page with one heading would prejudge the dashboard's. Five hand-written pages do not change that calculation. The risk accepted is that an editorial page drifts from the document it summarises; the mitigation is that it summarises rather than restates, so drift surfaces as staleness rather than as contradiction.

**Positioning.** The site presents Meridian as a non-profit effort building open ground-station software and an open protocol. It does not describe its origin. That is recorded here rather than left implicit, because the repository says otherwise in `CLAUDE.md` and `docs/PROJECT.md` — both public, and both one GitHub link away from the site. The constraint applied instead is that nothing on the site is false: status is stated as pre-launch, no station count is invented, and no simulated figure is presented as measured. That is the same rule the platform is built under.

**The home page has no Lighthouse Performance score, and this is a choice.** Content sits at `opacity: 0` until `--reveal-at: 4200ms`, so Chrome records no LCP candidate inside Lighthouse's trace window and the Performance category returns null rather than a low number. Real-world LCP would be about 4.8 s. Accessibility, Best Practices and SEO are 100 on every page, and the four subpages — which carry no canvas and no reveal — score 100 in all four categories with an LCP of 0.4 s.

Two fixes were on the table and both were declined: exempting the `<h1>` alone from the reveal, which would have put LCP at 0.2 s while leaving the canvas sequence entirely untouched, and shortening the sequence to about 1.8 s. The four-stage intro at its current pace was judged worth more than a score on the one page whose job is an impression rather than information. It is recorded so that it reads as a decision rather than an oversight.

*Rejected:* a Markdown build step under `site/tools/`. Deferred rather than rejected on the merits — it becomes the right answer the moment the site needs to carry a specification verbatim.

*Rejected:* a `_redirects` file for the `www` → apex redirect. Cloudflare Pages matches `_redirects` on path only and documents domain-level redirects as unsupported, so the host-based form never fires, and the path-only form `/* → https://meridian.org.in/:splat` would put the apex into a redirect loop. The redirect stays the dashboard Redirect Rule D-036 already specified, and `site/README.md` now records why a file cannot replace it.

---

## D-039 — Hairlines are for seeing, not for passing

**2026-08-05 · accepted** · *amends D-037*

D-037 checked every **text** pair against WCAG AA and fixed the two that failed. It did not check the pairs that are not text, because the standard does not ask it to: a purely decorative divider is exempt from the 3:1 contrast requirement for user-interface components.

Seen on a real screen, that exemption turned out to be the wrong thing to lean on. `--rule` measured **1.25:1** on the dark background and **1.36:1** on the light one, and the canvas wire on paper was **1.47:1**. A rule that a reader has to hunt for is not a subtle rule; it is a rule that is not there, and the pages read as a wall of unstructured text on any panel without OLED-grade blacks. Passing an audit and being legible are different questions and only one of them was asked.

**Decision.** Raise the non-text values in both themes, to a target of roughly 1.7:1 — enough that a divider is unambiguously present, low enough that it stays a hairline rather than becoming a box.

| Token | Theme | From | To | Ratio |
|---|---|---|---|---|
| `--rule` | dark | `#1E2229` | `#333944` | 1.25 → **1.71:1** |
| `--rule` | light | `#DCD7CE` | `#C9C2B6` | 1.36 → **1.68:1** |
| `--wire` | light | `#D5CFC4` | `#BFB8AB` | 1.47 → **1.87:1** |
| `--alert` | dark | `#B8544F` | `#C4635C` | 4.19 → **5.01:1** |

**`--rule` and `--wire` are no longer the same value in the dark theme.** They were, and that was a coincidence rather than a decision — a divider between blocks of prose and a wireframe stroke on a near-black canvas do not want the same weight. The wire stays at 1.25:1 because the globe reads correctly there; only the rule moves.

**`--alert` is fixed rather than caveated.** D-037 left it at 4.19:1 with a note in `site/README.md` saying it is never used as text. A colour that has to be documented as unusable is worse than a colour that is usable, and the fix was four characters.

**Body copy moved off `--muted` onto `--ink-dim`** — 4.88:1 → **8.20:1** dark, 6.03:1 → **8.86:1** light. `--muted` clears AA and prose set in it still reads as washed out, which is the gap between the threshold and the thing the threshold is a proxy for. The rule now is that `--muted` is for text that is *glanced at* — nav, metadata, tracked uppercase mono — and `--ink-dim` is for text that is *read*. `.standfirst` moves up to `--ink` to keep the deck distinct from the body it now shares a colour with.

The layer diagram's strokes moved from `--rule` to `--muted` for a different reason: a diagram needed to understand the page is a graphical object under WCAG 1.4.11 and owes 3:1, which `--rule` does not clear at any of these values.

*Rejected:* raising `--rule` far enough to clear 3:1 as well. At that weight the hairlines stop dividing and start boxing, which is the thing `site/style.css` has said not to do since the first commit. 1.7:1 is a judgement, not a threshold, and it is written down here so the next person knows it was chosen rather than defaulted to.

---

## D-040 — The document pages get a rail, and the footer stops repeating the nav

**2026-08-05 · accepted**

Three problems with the five-page site as D-038 shipped it, all visible only once it was on a real screen at a real width.

**The measure left 40% of a wide viewport empty.** `.doc` is capped at `46rem`, correctly — a 68-character line is the measure prose wants and widening it would be a regression. But the cap was applied inside a single-column grid, so on a 1440px display the entire right-hand third was blank.

**Decision.** A sticky rail in that space: a contents list that tracks the heading being read, and a still frame of the same globe the front page animates. Three grid tracks, the middle one an elastic spacer, so the prose stays on the left margin and the rail lands on the right — under the theme toggle, which is what the masthead already puts there.

**One contents list, not two.** Above 1180px it is the rail; below, it falls back into normal flow as a two-column list of links. The obvious alternative — ship a `<details>` and force it open on wide screens — needs `::details-content` to do the forcing, which is too new to depend on, and a duplicated list is a list that can disagree with itself. The `<h2>` ids and the contents entries are generated from one pass over the same headings for the same reason, and `verify_site.py` now fails the build if a fragment points at nothing.

**`main.js` split into `orbit.js` + `main.js`.** The rail needed the projection, the graticule, the orbit model and the elevation test. Copying seventy lines would have put two orthographic projections in one directory that could drift apart while both claiming to draw the same sky. `orbit.js` is an ES module holding the maths; `main.js` keeps the intro choreography and imports it; `rail.js` imports the same. `script-src 'self'` covers modules and `modulepreload` alike, so the CSP is untouched. Both entry points gain `type="module"`, which is also what removes the now-redundant `defer`.

**The rail canvas draws once.** No `requestAnimationFrame`, no loop, redraw only on `themechange` and `resize`. The four document pages score 100 on Lighthouse Performance and an animation competing with the prose beside it would have risked that for decoration. Measured after the change: still 100/100/100/100, TBT 0 ms.

The moment it draws is searched, not chosen — over one globe revolution, the longest *visible* link with the satellite comfortably above the mask and both endpoints on the near hemisphere. Picking the peak of the pass gives an 11px line, and picking a fixed elevation lands on moments where the satellite is behind the Earth. The line is green because the elevation test says so, exactly as on the front page, which matters because this page does not carry the legend that would otherwise explain the colour.

**The footer was the masthead nav a second time.** Four links repeated for no reason. It is now a sitemap — the documents, the source, the licence, the attribution, and the contact address — which is content the top nav has no room for. The front page keeps its single-line colophon; it is one screen and this is not.

**Page transitions are four lines of CSS.** `@view-transition { navigation: auto; }` cross-fades between two documents in Chrome and Edge; Firefox and Safari navigate normally, which is the behaviour the site had already. No JavaScript, no request. Reduced motion cancels it on the pseudo-elements rather than with `@view-transition { navigation: none }` inside a media query — the nested form is newer and less certainly supported, and the pseudo-element form is unambiguously valid. Verified by reading the parsed rule back out of `document.styleSheets`, not by assuming.

**The 404 gained an acquisition sweep** — an arm that turns and a trace that never closes — in CSS on an inline SVG. The front page's canvas is a scene worth 8 KB of JavaScript; a 404 is not.

**There is now a contact address.** `hello@meridian.org.in`, on `/about/`, in the footer, and as `contactPoint` in the `Organization` JSON-LD, plus a `.well-known/security.txt`. **It depends on Cloudflare Email Routing being configured** — free, forward-only, and not yet done at the time of writing. A published address that bounces is worse than no address, so this is a pre-deploy blocker and is recorded as one in `site/README.md`.

`nonprofitStatus` is deliberately not set in the structured data. Its values assert a specific legal registration; non-profit stays a prose claim.

*Rejected:* a WebGL globe in the rail. It is what "3D" usually means and it would need either a CDN, which the CSP forbids outright, or a vendored copy of a library — roughly 600 KB and an `ATTRIBUTION.md` entry — to decorate the margin of a document. `main.js` already computes a real orthographic projection of a real 98° orbit in no dependencies at all; the rail draws that.

*Rejected:* a loading screen. The site paints in 0.4 s; an overlay that covers it and dismisses on load would add latency to every navigation and cost the four document pages their Performance score, to simulate the slowness it appears to be hiding.

*Rejected:* centring the measure to close the gap. `site/style.css` has been asymmetric on purpose since the first commit — masthead top-left, content anchored lower-left, nothing centred. Centring the document column would have fixed the empty space by breaking the alignment with the masthead above it.

---

## D-041 — The intro stops being load-bearing, and three Cloudflare settings stop editing the site

**2026-08-05 · accepted** · *amends D-037, D-038*

The site went live and behaved differently on every browser: native-blue links in places, a globe that shrank or vanished on phones, and in Brave no globe at all — the 4.2 s elapsed and the text appeared over nothing. Invisible links responded to hover and click throughout the intro.

**Two of those were not defects in this repository.** They were Cloudflare settings rewriting what it ships, and they are recorded here because the next person to see the symptom will look in the code first, as we did.

| Setting | What it did |
|---|---|
| **Browser Cache TTL = 4 hours** | overrode `_headers`, which says `max-age=0, must-revalidate`. Live responses returned `max-age=14400` for CSS and JS while HTML correctly returned `max-age=0`, because HTML is `cf-cache-status: DYNAMIC` and escapes the rule. Every deploy therefore had a four-hour window in which returning visitors ran **new HTML against old CSS**. |
| **Email Address Obfuscation** | rewrote `mailto:hello@meridian.org.in` into `/cdn-cgi/l/email-protection#…`, replaced the visible address with `[email protected]`, and injected a decode script. When that script is blocked the address never decodes. |
| **Web Analytics** | injected `static.cloudflareinsights.com/beacon.min.js`, which the CSP blocks — a console error on every page load, and no analytics collected either way. |

**The blue links had exactly one cause and it was the cache.** The proof is in which elements were unstyled. The previous stylesheet contains `.wordmark .lede .cta .meta .legend` and does not contain `.skip .brand .nav .doc .standfirst .eyebrow .prose .rail .toc .colophon-wide .theme-toggle`. Every element that rendered as a browser default was in the second list and every element that rendered correctly was in the first — including, decisively, a correctly letter-spaced `.wordmark` span inside a browser-blue `.brand` anchor. Rendering the live site at 390 px with an empty profile found **0 of 79 links** in default blue. No stylesheet change was needed or made.

**The intro's failure mode was the inverse of what its comment claimed.** `style.css` said the reveal was done in CSS "so the content appears even if main.js fails to load or is blocked", with JS only able to cancel it. In fact the 4.2 s delay ran unconditionally, and a `main.js` that never ran also never added `.intro-done` — so a blocked or mismatched script produced the full blank wait *and* no globe. Strictly worse than having no intro.

**Decision.** The delay is opt-in. `theme.js` — already render-blocking, already on every page — adds `intro` to the root element before the first paint, but only when the document asks with `data-intro`, reduced motion is off, and a 2D context can be created. It then removes it again after 900 ms unless `main.js` has added `intro-ready` from its first painted frame. `main.js` guards `getContext` returning null and drops the gate on any exception.

The animation itself is unchanged — same phases, same 4.2 s, same composition. Only the default changed. Verified by blocking `/main.js` and `/orbit.js` in turn: content is immediate in both cases, where it previously waited the full delay and then showed nothing.

**`opacity: 0` is not hidden.** The reveal left the masthead, copy, calls to action and footer transparent but fully hit-tested, focusable and in the accessibility tree for 4.2 s — the cursor turned into a pointer over links that were not on screen, and a tap both skipped the intro and followed the link underneath. Adding `visibility: hidden` to the `from` keyframe fixes all of it: with `fill-mode: both` that state holds through the delay, and `visibility` steps to visible on the animation's first frame, so nothing about the appearance changes. A one-shot capturing `click` handler, armed only by a pointer skip, covers the gesture that straddles the transition.

**The globe was being stretched, not resized.** `#scene` is `position: fixed; inset: 0`, so CSS sizes it to the *layout* viewport; `resize()` built the backing store from `window.innerWidth/innerHeight`, the *visual* viewport. On a phone those differ by the height of the browser chrome, so a scene drawn for ~660 CSS px was stretched by CSS across ~780 — about 18 % of vertical distortion — while `layout()`'s `H * 0.21` recomputed the radius from the wrong number every time the URL bar moved. Measuring `canvas.getBoundingClientRect()` instead fixes the distortion and the resizing together, and changes no constant in the animation. A `visualViewport` listener joins the `window` one, since mobile browsers do not agree on which fires.

**The rail stopped charging phones for what it hides.** Below 1180 px the canvas is `display: none` and the contents highlight is invisible, but `rail.js` statically imported `orbit.js` — 12 KB, preloaded — ran a 2 400-iteration search at module top level, and set up an `IntersectionObserver` that fired on every scroll. The `offsetParent` guard only stopped the drawing. `orbit.js` is now a dynamic `import()` behind a `matchMedia('(min-width: 1180px)')` gate, along with the search and the observer, and the `modulepreload` hint is gone from the four document pages. Deferred rather than deleted: crossing the breakpoint still activates both. Verified: a 390 px document page makes **zero** requests for `orbit.js`; at 1440 px it makes exactly one.

**On the home page's Lighthouse score.** D-038 recorded Performance as null (`NO_LCP`) and treated that as the accepted cost of the intro. It now reports **100**, and that is *not* evidence the page got faster. Measured with a `PerformanceObserver`, the real LCP is still **4 400 ms** — the `.lede`, exactly as designed. What changed is that `visibility: hidden` gives the element a clean entry into the render tree at 4.2 s, so a candidate exists where `opacity: 0` had produced none; Lighthouse's Lantern simulator then models the element's resource-readiness and ignores the animation delay, and returns 0.4 s. The score is a modelling artifact. **Do not cite it as a speed result.** FCP, which is real, is 108 ms and comes from the canvas.

**Footer copy.** "Non-profit · Receive only — the station never transmits" is gone from the bar, which now reads `© 2026 Meridian · Apache-2.0`; the brand line drops "non-profit" too. The claim survives once, on `/about/`, where it is explained rather than asserted. "Receive only" remains on `/architecture/` as one of the six rules — a footer is the wrong place to repeat a technical constraint on every page. The home colophon gains the contact address, which is the only place on that page it can go without breaking the one-screen rule.

*Rejected:* content-first, with the globe fading in behind it. The better architecture, and the one that would make the score honest — but the intro is the site's whole first impression and the instruction was explicit that it not change.

*Rejected:* fingerprinting the assets so mixed releases become impossible regardless of cache rules. It is the belt-and-braces answer and it needs a build step, which D-036 rules out. Respecting the headers already shipped achieves the same thing with a dashboard toggle.

*Rejected:* adding `static.cloudflareinsights.com` to `script-src` to make the injected beacon work. That would trade an enforced guarantee for a broken one to enable analytics nobody asked for.

---

## D-042 — Asset URLs carry a content hash, and the site's invariants are enforced rather than written down

**2026-08-06 · accepted** · *reverses a rejection in D-041*

D-041 rejected fingerprinting the assets: *"It is the belt-and-braces answer and it needs a build step, which D-036 rules out. Respecting the headers already shipped achieves the same thing with a dashboard toggle."*

**The dashboard toggle was recorded as the remedy and was never applied.** Measured against the live site the day after D-041 was written:

```
$ curl -sSI https://meridian.org.in/style.css | grep -i cache-control
Cache-Control: public, max-age=14400, must-revalidate

$ curl -sS https://meridian.org.in/ | grep -c __cf_email__
1
$ curl -sS https://meridian.org.in/ | grep -c cloudflareinsights
1
```

All three settings in D-041's table are still in force. The four-hour window is still open, the contact address still renders as `[email protected]` in any browser that blocks the decode script, and the analytics beacon is still injected and still blocked by the CSP on every page load.

The same symptom was reported again from a phone and a desktop the same day, and the same evidence identified it: the rules missing from the phone's rendering were exactly the set added in `334d4e5`, and the rules that survived were exactly the set present at `2d7372a`.

**Decision.** Keep asking for the dashboard changes, but stop depending on them. Every reference to a stylesheet or a script carries `?v=<first 8 hex of SHA-256 of the file>`, applied by `site/tools/stamp_assets.py`. A URL now names one set of bytes, so no cache — Cloudflare's, a browser's, a corporate proxy's — can pair one build's HTML with another's CSS, whatever `Cache-Control` says by the time it arrives.

**This does not add a build step, and D-036 still holds.** The stamped files are committed; `site/` is still exactly what is deployed, with no transformation between the repository and the origin. The stamper is a source-editing tool in the same category as `make-images.py`, which has generated committed artefacts in this directory since the site existed. CI runs `--check` and fails if the committed stamps do not match the committed files, which is what makes the guarantee real rather than a habit.

`orbit.js` is stamped into `main.js` and `rail.js` before their own hashes are taken — otherwise stamping changes the files whose hashes were just computed — and `index.html`'s `modulepreload` carries the same stamp as the import, or the preload and the import are two URLs and the file is fetched twice.

**`verify_site.py` did not exist.** `site/README.md` stated that it fails the build on a broken `href="#…"` and on a `modulepreload` for `orbit.js` appearing on a document page. There was no such file and no CI job for the site at all. It exists now, and checks five things, each corresponding to a claim the README makes: fragment links resolve, the `modulepreload` rule holds, the sitemap matches the pages that exist, nothing relies on inline CSS or JS the CSP forbids, and **every anchor sits in a context the stylesheet actually colours**.

That last check exists because the bug it catches shipped. `.foot-bar` styled the wide footer's bottom bar and nothing styled the anchor inside it, so the licence link was drawn in user-agent blue on all five document pages — a genuine stylesheet defect, distinct from the cache problem D-041 analysed, and reported alongside it. The check derives the styled contexts from `style.css` itself, counting only rules that set `color`: `.nav a` appears twice in that file, once with a colour and once inside the reduced-motion query with nothing but `transition: none`, and counting the second is what would let a stylesheet that had lost the first still pass.

**The check was written twice, because the first version passed a page that was visibly broken.** It reduced each anchor to the *set* of class names above it and asked whether any of them appeared in a rule that styled a link. On `/docs/`, `.index-row h2 a` styles the title of each row — so `index-row` was in the set, and the two "…on GitHub" links in the rows' `<p>` descriptions were declared fine while rendering in browser blue. A set of class names cannot distinguish `.index-row a` from `.index-row h2 a`; the intermediate element is the whole difference.

It now records each anchor's full ancestor chain, tag and classes in document order, and matches selectors against it properly: the subject unit must match the anchor, and the units before it must match ancestors in order. Combinators are read loosely — `>` as descendant, pseudo-classes and attribute tests dropped — always in the direction that risks missing a problem rather than inventing one, since a check that cries wolf gets switched off. Verified by deleting each of the fourteen link rules in turn and confirming the count of reported anchors matches the markup. `.cta-quiet` is the one that correctly reports nothing: it is a modifier on an element that already carries `.cta`, and removing it dims no link that was not already coloured.

The lesson is the general one. **An enforcement check that has never failed is not evidence of anything.** Both versions of this one passed on a clean tree; only deliberately breaking the stylesheet distinguished them.

**The home page shows a globe without JavaScript.** D-041 made the intro fallible, so a blocked `main.js` no longer costs the visitor the page — but it still cost them the picture, and the canvas stayed empty. The settled frame now ships as inline SVG generated by `make-images.py` from the same projection, graticule and elevation test that draw the social card, so there is no second copy of the geometry. It is hidden by `theme.js`'s `intro` class before the first paint when the animation is going to run, and by `main.js`'s `intro-ready` under reduced motion; it returns when the 900 ms fuse fires, which is the case it exists for. It costs about 3.3 KB gzipped and no request.

Inline rather than `<img src>`: an external SVG document cannot read the page's theme custom properties, so it could not follow the masthead's toggle.

**The heading leaves the reveal, and the intro keeps everything else.** D-041 measured the home page's real LCP at 4 400 ms and accepted it, recording that content-first was the better architecture but that the instruction not to change the intro was explicit. That instruction was given before search visibility was a requirement; it is now one, and 4.4 s is a failing Core Web Vital on the single page that has to rank — with the additional risk that Google's renderer samples the heading while it is still `visibility: hidden` and treats it as hidden text.

The `<h1>` therefore carries no `reveal` class and paints with the canvas at about 110 ms. **Nothing else changed:** the same 4.2 s, the same phases, the same stagger on the masthead, body copy, calls to action and footer, the same easing. The intro's opening is now a title card over the close-up rather than an empty frame, which is a smaller change to the first impression than the alternatives D-041 weighed. `site/README.md` records why the class must not be put back, because nothing will fail if it is — the page will just stop ranking.

**Also fixed, from the same pass.** `text-size-adjust: 100%`, without which iOS Safari inflates the prose column on rotation. A 24 px floor on the nav, footer and rail link targets, which were 17 px and failed WCAG 2.2 Target Size (Minimum); the nav also stops shrinking to 10 px below 840 px, a size it was given to solve a width problem that belongs to the metadata line, not to it. `.legend dd` is visually hidden on narrow screens instead of `display: none`, so a screen reader still hears the key it no longer has room to show. The `.cta` arrow's hover transform moves behind `@media (hover: hover)`, because a touch screen synthesises hover on tap and leaves the glyph permanently nudged. A print stylesheet, because the document pages are specifications and specifications get printed.

`ruff check` and `ruff format --check` were both failing on `main` — two findings in `make-images.py`, unrelated to any of the above. Fixed in passing.

*Rejected:* going `immutable` on CSS and JS now that the URLs are content-addressed. It is safe and it is not necessary; these files are small enough that revalidation costs a 304, and `immutable` on an unstamped URL — which is what this becomes if the stamper ever silently stops running — cannot be recovered from without renaming the file.

*Rejected:* obfuscating the contact address ourselves so Cloudflare's rewriter finds nothing to match. It fixes the symptom by adopting the technique that caused it, and the honest fix is one dashboard toggle.

*Not fixed here, because it is not in this repository:* `www.meridian.org.in` returns 200 and serves a full duplicate of the site. The canonical tags point at the apex, which mitigates it; a 301 redirect rule is the fix. The `Report-To` and `NEL` headers Cloudflare injects also make every visitor's browser report to `a.nel.cloudflare.com`, which cannot be turned off on the free plan — `site/README.md` now says so rather than claiming the site makes no third-party requests.

---

## D-043 — AI-assisted commits carry a trailer, from here forward

**2026-08-06 · accepted**

`GIT-WORKFLOW.md` Rule 10 asks the team to decide whether AI-assisted commits are
marked, and to record the answer here rather than letting it happen by default.
Every previous pass through this file noted the question as still open. It is the
last Stage 0 item outstanding.

**Commits made with AI assistance carry a `Co-Authored-By` trailer.** Rule 10's
own argument against — that the trailer would appear on nearly every commit and add
little signal — is accepted as true and is not treated as decisive. The trailer is
cheap, it is machine-readable, and its absence on a project that says in its own
workflow document that it uses AI assistants would read as a gap rather than as a
choice. A signal that is usually present is still the signal; the one that matters
is the commit that does *not* carry it.

**Existing history is not rewritten.** The trailer applies from this entry forward.
Rewriting the eight commits already on `main` would change every SHA for a
bookkeeping change, and the trailers added would be a reconstruction from memory
rather than a record made at the time — which is the same objection this file
raises against editing an accepted decision instead of amending it.

**The load-bearing rule is unchanged, and it is not this one.** Rule 10's first
paragraph — never commit code you cannot explain line by line — is what the viva
actually tests. A trailer is provenance, not comprehension, and marking a commit
does not license committing something its author cannot walk a reviewer through.

`ATTRIBUTION.md` is unaffected. It covers ideas taken from reading another
project's source, which is a different question with a different answer, and it
continues to be kept in the same commit as the work it describes.

---

## D-044 — Section 9 of `CLAUDE.local.md` was never in force

**2026-08-06 · accepted**

`CLAUDE.local.md` §9 specifies an exact ruff and mypy configuration under the
heading *"Aspirational standards decay. These are wired into CI."* The committed
`pyproject.toml` configured something else, and had since the repository was
scaffolded:

| | §9 asks for | was configured | consequence |
|---|---|---|---|
| `line-length` | 88 | 100 | — |
| `D` | required | absent | §5's docstring rules unchecked |
| `ANN` | required | absent | §5.2's units-on-every-argument unchecked |
| `C90` | max-complexity 8 | absent | §2's complexity limit unchecked |
| `PL` | 5 args / 8 branches / 30 statements / 4 returns | absent | §2's size limits unchecked |
| `N` `W` `RET` `ARG` `TRY` `PTH` `ERA` | required | absent | §3, §4 and §7 unchecked |
| mypy | `disallow_any_explicit`, `warn_unreachable` | neither | an explicit `Any` passed `strict` |
| module ≤ 400 lines | a CI step | absent | the one limit ruff cannot express |

Turning it on produced 77 findings. None was a defect in running code — they are
long lines, missing docstrings and two real size violations — which is the point:
the rules had never been applied, so nothing had ever been written against them.
This is D-042's lesson arriving a second time. **An enforcement check that has
never failed is not evidence of anything**, and one that was never wired in is not
even a check.

**The ruleset is the union, not the substitution.** §9's list does not include
`DTZ`, `TID`, `T20`, `UP`, `B` or `RUF`, all of which were already configured, and
adopting §9 verbatim would have *removed* the only mechanical enforcement of two
hard project rules: `DTZ` bans the naive `datetime` that §6 and `DATA-MODEL.md`
both forbid, and `TID` carries the banned-import rule that keeps `sgp4` and
`skyfield` inside `meridian.orbit` per `ARCHITECTURE.md` rule 2. A standards
document that is a floor is useful; treating it as a ceiling would have made the
codebase less checked, not more.

**Four deviations, each recorded in `pyproject.toml` beside the ignore:**

| Ignore | Scope | Reason |
|---|---|---|
| `RUF002` | global | pre-existing. `σ` and `−` in docstrings are correct domain notation |
| `TRY003` | global | `config.py`'s refusal names the variable, the reason, and `openssl rand -hex 32`. Moving that into an exception class satisfies the rule and costs the operator the thing that makes it actionable |
| `D` `ANN` `PLR2004` `PLC0415` | `tests/**` | a test's name is its docstring; `assert heartbeat_interval_s == 30` *is* the assertion, and lifting 30 into a constant makes the test assert the constant equals itself; `test_layout.py` imports late on purpose, to prove the `src/` layout keeps `platform` off `sys.path` (D-012) |
| `D` `ANN` `PLR` `C90` `T201` | `site/tools/**` | stdlib-only build scripts producing committed artefacts. §9 scopes its own module-length check to `platform client simulator`, so the size and complexity families are scoped the same way. `E501` is deliberately **not** ignored |

**Two findings were real and were fixed rather than ignored.**
`InsecureConfiguration` becomes `InsecureConfigurationError` (`N818`).
`OrbitService.pass_windows` took six arguments against §2's cap of five and now
takes a `PassSearch` dataclass — which also gave the elevation floor and the coarse
step somewhere to carry their reasoning, instead of arriving at a call site as two
indistinguishable floats. `api/app.py`'s health body drops `dict[str, Any]` for
`dict[str, str]`, and its `except Exception` narrows to `(psycopg.Error, OSError)`
so a `TypeError` in our own code can no longer be reported to an operator as
"database unreachable".

**The module-length check is a CI step, and it was verified by breaking it.** §9's
own snippet ends `| grep -q . && exit 1 || exit 0`, whose trailing `|| exit 0`
returns success when nothing matches *and* when something does. The version in
`ci.yml` is a plain loop, and it was confirmed to fail by appending 300 lines to
`config.py` and confirming a non-zero exit, then restoring the file. Nothing in the
tree exceeds 400 lines today; `config.py` at 233 is the longest.

*Rejected:* adopting §9 verbatim, `DTZ` and `TID` included in the loss. Exact
compliance with the document at the cost of un-enforcing `ARCHITECTURE.md` rule 2
is the letter defeating the purpose.

*Rejected:* leaving `line-length` at 100 and recording a deviation. The reformat
cost 11 files and 22 lines — measured before deciding — which is not a price worth
a permanent divergence from the written standard.

---

## D-045 — Clock uncertainty is floored at the clock's own resolution

**2026-08-06 · accepted** · *implements D-016*

D-016 and `MSP-SPEC.md` §4.2 say `clock_uncertainty_s` may be `null` for unknown
but must never be `0.0`, because a station claiming perfect clock accuracy and a
station that cannot measure its own are opposite cases. The estimator computes
`uncertainty = RTT / 2`, which satisfies that — right up until the round trip
measures as zero.

**It does.** Running the reference client against `GET /msp/v0/time` on localhost:

```
#1  offset=-0.00041s  uncertainty=0.00000s  rtt=0.00000s
#4  offset=-0.00032s  uncertainty=0.00000s  rtt=0.00000s
```

`datetime.now()` resolves to **15.625 ms on Windows**. A localhost round trip
finishes inside one tick, `received_at - sent_at` is exactly zero, and the station
reports the one value the specification forbids. Not an edge case: it was four
measurements out of six on the first machine it ran on, and it is the normal case
for a station colocated with the platform — which is what the Pi will be.

**Decision.** The uncertainty is `max(RTT / 2, CLOCK_RESOLUTION_S)`, where the
resolution is read from `time.get_clock_info("time").resolution` rather than
assumed. Half the round trip is the *network* bound on the estimate; the clock's
resolution is the *instrument* bound; the honest figure is whichever is larger. A
measurement cannot be more certain than the clock that made it.

This is load-bearing rather than cosmetic. `EVALUATION.md` §6.1 discards timing
error smaller than the reported clock uncertainty. A station reporting `0.0`
discards nothing, so every timing error it produces — including the ones that are
pure clock noise — is kept and attributed to element-set age, which is the SC-3
figure.

Read from the platform rather than hard-coded because the number is genuinely
different per host: about 15.6 ms on Windows, about 1 ns on Linux. On the Pi the
round trip will dominate and the floor will never bind, which is correct — the
floor exists for the case where it does.

*Rejected:* measuring the round trip with `perf_counter`, whose resolution here is
100 ns. It would make the RTT accurate and is the right instrument for a duration,
but the offset needs wall-clock instants either way, and mixing two clocks in one
estimate means the uncertainty no longer describes the clock the offset was
measured against. The floor is the smaller and more honest change.

*Rejected:* reporting `null` when the round trip measures zero. Defensible on the
letter of D-016 — the uncertainty genuinely is unknown at that resolution — but it
throws away a real bound. The station does know its uncertainty is no worse than
one clock tick.

**Found by running the client against the endpoint, not by reading the code.** Both
were written against D-016 and both looked correct.

---

## D-046 — Invite expiry is checked before MSP §4.1's table, and it binds bound invites too

**2026-08-08 · accepted** · *implements D-020, D-034; found by audit*

`store.invites.revoke_invite` implements withdrawal as `set expires_at = now()`, deliberately, so that a withdrawn invite and a lapsed one are one fact to every future reader. `registry.psycopg_registry.register()` then never read `expires_at` at all.

**The consequence was that `meridian invite revoke` did nothing.** It reported "Revoked 1 invite(s)", set the column, and the invite went on registering stations. `--expires-in-days` was decorative for the same reason. MSP-SPEC.md §6 defines `invalid_invite` as "unknown, already used, **or withdrawn**"; the third case had no implementation.

Two things had to be decided to fix it, neither settled by the specification.

**Where the check goes: before the table, not inside a row.** MSP §4.1's six-row table selects an outcome from `(invite state, registration_key)`. Expiry is not one of its axes. Putting the check ahead of the table means one predicate covers creation, unbound recovery and bound recovery alike, and no future row can be added that forgets it.

**A bound invite is *not* exempt.** D-034 exempts a bound invite from the recovery *window*, on the reasoning that an operator issuing an invite against a named `station_id` has supplied the authorisation the window exists to require. That argument does not extend to expiry: an operator who issued a bound invite and then withdrew it has withdrawn the authorisation itself. Exempting bound invites would make the operator's only revocation control silently inapplicable to the one case — post-window credential rotation — where it is most likely to be used in anger.

**The comparison is made by the database, not in Python.** This was the third thing that had to be decided, and it was forced by a test that passed or failed depending on which tests ran before it.

`revoke_invite` writes `expires_at = now()` — the *database's* clock. The first implementation compared that against a Python `datetime.now(UTC)` captured at the start of the request. Measured on the development machine:

```
python datetime.now(UTC)   : 2026-08-08 07:37:22.123359+00:00
postgres now()             : 2026-08-08 07:37:22.129477+00:00   <- transaction start
postgres clock_timestamp() : 2026-08-08 07:37:22.136484+00:00
```

Two separate problems. Postgres `now()` is *transaction start*, not statement time, so its value depends on when the transaction opened — which is what made the test non-deterministic. And the Python clock ran ~6 ms behind, with a 15.6 ms resolution on Windows (D-045 measured the same tick): the two readings above, taken either side of a database round trip, were *bit-identical*.

Neither margin matters for D-023's one-hour recovery window, which is why `is_recovery_eligible` still takes a Python `now`. It matters absolutely here, because `expires_at = now()` sets the deadline to *this instant* — the margin is zero by construction, so any skew at all lets a revoked invite through.

So `Invite` carries `is_expired`, computed in the `select` as `(expires_at is not null and expires_at <= now())`. One clock writes it and the same clock reads it. `cli._invite_state` was making the identical cross-clock comparison and now uses the same column, so `meridian invite list` cannot report "pending" for an invite it just revoked either.

An invite with a null `expires_at` never expires, which is D-020's default and what the bootstrap invite relies on.

---

## D-047 — One invite admits one station, enforced by acting on the race guard

**2026-08-08 · accepted** · *implements D-020; found by audit*

`store.invites.consume_invite` guards the race in SQL with `where consumed_at is null` and reports the outcome by return value, its docstring stating that the loser "must treat that as `invalid_invite`". Both call sites in `psycopg_registry` discarded that value.

**Two concurrent `POST /msp/v0/register` with the same unconsumed invite therefore both succeeded.** Both passed the `consumed_at is null` read in `register()`, both inserted a station with a different generated `station_id`, and both called `consume_invite`; under READ COMMITTED the second blocked on the row lock, re-evaluated the predicate, got `rowcount 0` — and nothing looked at it. One invite, two stations. That is precisely the property D-020 says `invite_tokens` exists to provide.

**The guard is acted on inside the caller's transaction**, in `_consume_or_raise`, so raising rolls the station row back with it. The alternative — checking first, then inserting — reintroduces the same race one statement earlier; the `update`'s own row lock is the only serialisation point available without escalating the isolation level for every registration.

The loser is rejected as `invalid_invite`, not as a conflict. MSP §3 does not let a client learn why its invite failed, and "someone else got there first" is exactly the kind of detail an attacker probing a leaked invite would want.

**Both this and D-046 were found by reading the code against the specification, not by a failing test.** Neither had a test that could have caught it: the suite exercised one registration at a time, against invites it had just created.

---

## D-048 — Recovery restores an identity, and `simulated` is never taken from a station

**2026-08-08 · accepted** · *implements D-005, D-013; found by audit*

MSP §4.1's recovery rows say only "same `station_id`, newly minted token". They are silent on what happens to the rest of the payload — `name`, `location`, `capabilities`, `client`, and `simulated` — which a recovering station sends in full because the request shape is the same one it used to register.

`_recover_unbound_station` and `_recover_bound_station` read all of them and discarded all of them.

**Recovery restores an identity; it does not re-register.** Ignoring the profile fields is the right default: recovery exists because a response was lost in flight (D-023) or an operator authorised a rotation (D-034), not because the station's description changed. A station that genuinely moved should be re-registered, not recovered. This is now stated in `Registry.register`'s docstring rather than left as behaviour a reader has to infer from an omission.

**`simulated` is the exception, and is rejected on mismatch.** A station cannot change its own nature. Silently ignoring a mismatch meant a simulated station could recover claiming `simulated: false` — keeping the stored `true`, so the row stayed correct, but the platform answered `200` to a request whose central claim it had discarded. The reverse is the dangerous direction if the two ever drift. `403 invalid_invite`, collapsed into the same error as every other rejecting row because MSP §3 does not let a client learn why.

**The wider rule: `simulated` is platform-derived, always.** MSP §4.2 puts no `simulated` on the wire today, but `store.heartbeats.NewHeartbeat` accepts one from its caller, and the only value a heartbeat route would have to hand is whatever the station sent. `meridian.observations`' module docstring already states the invariant — "copied from the station's registry record" — with nothing to read it from. `store.stations.find_station_provenance` is now that reader, and `store/heartbeats.py`'s header says so at the point of use.

`store.assignments.DueAssignment` also omitted `simulated`, which `assignments` has carried since `0004_passes.sql`. An assignment delivered over MSP §4.3 would have had no way to mark itself. Added.

---

## D-049 — `element_sets` records provenance in `source`, not a `simulated` boolean

**2026-08-08 · accepted** · *clarifies D-013*

D-013 enumerated the tables that carry a `simulated` boolean — `stations`, `passes`, `assignments`, `observations`, `heartbeats` — and did not consider `element_sets`, which has `source in ('celestrak', 'spacetrack', 'manual', 'simulator')` instead.

**That asymmetry is kept, deliberately.** `simulated` would be exactly `source = 'simulator'`, and a stored column derivable from another column in the same row is a column that can disagree with it. `source` also carries more: it distinguishes `celestrak` from `spacetrack` from `manual`, which element-set age and divergence analysis need and a boolean would flatten. It is part of `element_set_unique (satellite_id, epoch, source)`, so the same set from two providers is two rows by design.

**The obligation this creates is on `passes`, and it is not yet met.** A pass computed from a simulator-sourced element set is simulated, and `passes.simulated` is the column that has to say so. Nothing writes `passes` yet — propagation is Stage 6 — so this is recorded now, before the code exists, rather than discovered afterwards: **whoever inserts a `passes` row must set `simulated` from its element set's `source`, not default it to `false`.** The risk this closes is a dashboard filtering `where simulated = false` and silently including simulator-derived passes, which is the credibility failure CLAUDE.md's fifth rule is about.

---

## D-050 — A request that declares no body size is rejected, like one that declares too much

**2026-08-08 · accepted** · *implements D-028*

D-028 decided the caps and said they are "enforced as middleware, ahead of JSON parsing". No middleware existed. `MSP-SPEC.md` §6 has tabulated four limits since Stage 3 and `grep -i middleware platform/` returned nothing, so every cap in the specification was a claim about the reference implementation that the reference implementation did not meet. Two of the four apply to endpoints that do not exist yet; **the 64 KiB body cap applies to `register` and `time`, which are live and publicly reachable under the `public` profile.** `meridian.api.request_limits` now enforces them.

**The sub-question D-028 did not answer: what happens to a body whose size is not declared.** §6 requires rejection "before the body is parsed", and a `Transfer-Encoding: chunked` request declares no length — so before parsing there is nothing to compare against the cap. Three options:

*Allow it through.* Rejected. It is a complete bypass of the check, reachable by any client that chooses chunked encoding, and it is the option under which the cap reads as enforced while not being.

*Count bytes as the body streams and abort past the limit.* Rejected, though it is the technically fuller answer. It moves the decision to after parsing has begun, which is a different rule than the one §6 states, and aborting mid-stream from ASGI middleware means interrupting an application that has already been entered — the failure path is harder to reason about than the thing it protects.

*Reject a body-bearing request that declares no length.* **Taken.** MSP §8 binds JSON bodies over HTTP/1.1, and every client that sends a JSON body sends `Content-Length` for it — `httpx`, which the reference client uses, does so for any `json=` or `bytes` body and only goes chunked for a generator. So this costs no real station, and it is the only option under which "rejected before the body is parsed" is literally true.

Scoped to `POST`, `PUT` and `PATCH`. `GET /msp/v0/time` carries no body and declares no length; requiring one there would reject every correct call to the one endpoint a station with no credentials and a wrong clock can still reach.

A malformed length — `64K`, `1.5`, empty, negative — is refused for the same reason. Leniency there is the bypass again with an extra step: a value that fails to parse and is treated as absent is a value an attacker chooses on purpose.

---

## D-051 — Rate limiting is deferred, and the reason is that a wrong limiter is worse than none

**2026-08-08 · accepted**

`rate_limited` has been one of MSP §6's eight codes since Stage 3. Nothing raises it and no limiter exists. `POST /msp/v0/register` is internet-reachable under the `public` profile, unauthenticated by design (D-006 admits stations by invite, not by network position), and performs a database lookup per request.

**Deferred to the deployment stage, deliberately, and not because it is unimportant.** The obvious implementation — an in-process per-IP token bucket — does not work correctly in this deployment and would be actively harmful:

- **The client IP is not the peer address.** Public traffic arrives through a Cloudflare tunnel, so every request presents the tunnel's address. A limiter keyed on the peer would rate-limit the entire network as one client — the first genuinely busy day would look exactly like an attack.
- **Keying on `CF-Connecting-IP` instead is worse.** The compose file also publishes the API on the host, so a request that did not come through the tunnel can set that header to anything. An attacker rotates it per request and is never limited; an operator on a fixed address is. The limiter would then be a control that fails open for the case it exists for and closed for the case it does not.
- **In-process state is the wrong lifetime.** It resets on every restart and does not survive a second worker, which is the shape the deployment takes on the Pi.

The correct place is the edge — Cloudflare's own rate limiting on the tunnel hostname — with the platform-side limiter, if one is still wanted, keyed on the invite token rather than on any address. **That is a deployment-stage decision and it needs the deployment to exist.** Recorded here so that the gap between "the protocol defines `rate_limited`" and "nothing can produce it" is a decision on the record rather than an omission somebody finds in a viva.

The trigger to revisit: the first time the platform is reachable from outside the college network for longer than a demonstration, which is Stage 10's exit condition rather than Phase 1's.

---

## D-052 — Longitude is ISO 6709, and migration 0007 rewrites rather than rejects

**2026-08-08 · accepted** · *corrects the schema shipped in 0002, 0003 and 0004*

`stations.lon_deg` shipped with `check (lon_deg between -180 and 360)`. That upper bound is **azimuth's range applied to a longitude** — the two sit three lines apart in `DATA-MODEL.md`'s conventions and the wrong one was copied. Under it, `200` and `-160` are two storable spellings of the same meridian, so two stations at the same place sort, group and subtract as though they were 360 degrees apart. Nothing had noticed because the one registered station is at 77°E.

**Longitude is −180..+180 (ISO 6709).** Corrected in the schema, and mirrored in `api/models/registration.py`'s `Location`, whose `le=360` had the same bound for the same reason.

**Existing rows are rewritten, not rejected.** A `CHECK` is validated against existing rows when it is created, so adding the constraint alone would fail the migration outright on any station stored under the old range — half way through a deployment, which is the worst place to discover it. `0007` runs `update stations set lon_deg = lon_deg - 360 where lon_deg > 180` first. That is lossless: 200E *is* −160, not an approximation of it, so no operator intent is guessed at. `tests/integration/test_migration_lifecycle.py` steps a scratch database to 0006, writes the row the old constraint allowed, then upgrades — because a test that asserted only the constraint would pass against a revision that forgot the rewrite.

### Three smaller judgement calls in the same migration

**`satellite_transmitters.source` is restricted to `('manual', 'simulator')`** — the two values anything can produce today. `element_sets.source` also lists `celestrak` and `spacetrack`, but those publish element sets and not transmitter records, and enumerating a source no code can write would be scaffolding for a design that does not exist. D-021 chose `CHECK` over a Postgres `enum` precisely so that an ingest adapter can widen this in one transactional statement when there is one.

**`passes.max_elevation_deg` is 0..90, and must be at least `min_elevation_deg`.** The original −90..90 allowed a maximum elevation of −40, which is not a pass: `GLOSSARY.md` defines one as a period during which the satellite is *above* the horizon. The pair constraint is the one that makes the two columns interpretable together — a window is the interval where elevation is at or above the floor, so the peak over that interval cannot be below it. A row violating it is a propagation or frame-conversion error, which is the silent class `CLAUDE.md` warns coordinate frames produce, and it is far cheaper to catch on insert than in a reliability figure three stages later.

**`stations.client_impl` is renamed to `client_implementation`.** `CLAUDE.local.md` §4 permits no abbreviation absent from `GLOSSARY.md`, which lists `lat`, `lon`, `alt` and `freq` and does not list `impl`. Renamed at the column and not only in Python, because an attribute called `client_implementation` writing to a column called `client_impl` moves the abbreviation instead of removing it. The MSP wire field stays `client.impl` — it is fixed by §4.1 and is carried by a Pydantic alias, which is the same mechanism now carrying `lat`, `lon`, `az_deg` and `min_el_deg` while the Python identifiers spell themselves out. `populate_by_name` is deliberately left off, so the wire contract stays exactly what the specification prints.

---

## D-053 — Meridian is its own network; no third-party client runs on our station

**2026-08-08 · accepted**

"Why not just contribute to SatNOGS?" is the first question this project will be asked, and the answer has to be on the record rather than improvised.

~~**We do not run `satnogs-client` or TinyGS firmware on our hardware, and we build no adapter for either.**~~ **Meridian is an independent network with an open protocol; the contribution is MSP and the platform behind it, not another node on somebody else's map.** Nothing in this repository speaks to another network, and no adapter for one is built here.

*Amended 2026-08-08, the same day, before this entry was relied on.* The struck sentence overreached and **contradicted `PROJECT.md` §5.4**, which has said since the project document was written that registering our station on an existing public network is a *"separate, optional requirement"* whose value, if done, is that it *"demonstrates that the station is good enough for an independent network to accept"*. **§5.4 stands.** What this entry actually settles is narrower, and is the part that was ever in question: the platform depends on no external network, and no third-party client sits anywhere on Meridian's path from prediction to reception. Whether an operator additionally points their own hardware at another network on their own time is not a decision this repository gets to make, and forbidding it here would be the decision log legislating past its own scope.

**This is not a licence problem, and saying so matters.** `satnogs-client` is AGPL-3.0, and running it as a separate process would create no obligation on this repository — the same separate-process argument `README.md` already makes for GPL decoders, which are invoked and never linked. AGPL §13 would bite only if we *modified* it and exposed the modified version over a network, and those changes would be published separately in any case. CLAUDE.md's third rule still forbids copying any of it in, and that is unchanged. **The reason we do not run it is scope, not licence.**

**The binding constraint, had we wanted to run both concurrently, is the radio.** An SDR is opened by exactly one process, so Meridian and a third-party client cannot use the same receiver at the same time; `rotctld` accepts several clients but two schedulers issuing conflicting bearings is meaningless. Resolving that means either a second receiver — **and there is no dual-hardware plan** — or a time-sharing arbiter, which is real engineering (window negotiation, pre-emption, a foreign client inside our own scheduling story) for something no success criterion asks for. Both are rejected **as platform features**; neither says anything about what an operator does with their own box outside a Meridian window.

**One receiver serving two bands is a scheduling constraint, not a limitation to engineer around.** `ARCHITECTURE.md` already has the scheduler enforce non-overlap **per station**, including slew and settling time, so a 137 MHz pass and a 437 MHz pass on one receiver cannot both be issued — the rule that makes the single-receiver case correct is the same rule that was already there for slew. That a station must choose between two visible passes is the oversubscribed problem this project exists to solve. Hardware that could never conflict would remove the demonstration rather than improve it.

**What replaces "join them" is "match them".** The station is expected to perform comparably to a competent SatNOGS or TinyGS station on the bands it covers, and that comparison is the honest way to show the network is worth joining. Parity is measured on our own numbers — decode yield, `peak_snr_db`, and predicted-versus-actual AOS — under `docs/EVALUATION.md`'s method, against our own station's history. It is **not** established by ingesting another network's observations and comparing totals: their archive contains only passes somebody chose to observe, which is the selection bias `EVALUATION.md` §1 names as this project's main methodological threat, and importing it to score ourselves would import the bias with it.

**The design obligation this leaves is small and worth keeping.** Reception (SDR capture, rotator control, decoders) stays separable from the MSP client rather than interleaving protocol calls with radio control, and standard interfaces are preferred where one exists — hamlib `rotctld` for the rotator, files or pipes for decoder output. That costs nothing now, and it is what stops "independent" from quietly meaning "unable to do anything else". It is a shape, not a feature, and no SatNOGS or TinyGS code, dependency or API call belongs in this repository.

**Hardware this assumes.** The full ₹43,500 build — `PROJECT.md` §17's Tiers 1, 2 **and 3** — so tracking is funded rather than optional. One Pi-and-SDR station receiving on **137 MHz** (Meteor-M LRPT, the primary target) through a fixed QFH, and **437 MHz** through the crossed Yagi on the workshop-built rotator, the latter being where the cubesat traffic SatNOGS carries mostly sits. Tier 3 buys the antenna, rotator, motors and amplifier — not the receiver, which already tunes both. Alongside it, an **ESP LoRa node as a second receiving path** for LoRa-modulated satellites — a receiver, not a controller, and not a replacement for anything: the Arduino Uno R4 WiFi remains the rotator controller exactly as `CLAUDE.md`'s stack section states. The LoRa node is TinyGS-class hardware by nature and is nonetheless ours, speaking MSP; that it *could* run TinyGS firmware and will not is the clearest illustration of this decision.

---

## D-054 — Liveness is derived on read, and the stored column is dropped

**2026-08-08 · accepted** · *resolves a contradiction in D-013's own schema*

The codebase said both things at once. `0002_stations.sql` declared `stations.liveness` as a stored `text` with a `CHECK` and a partial index; `Registry.liveness(station_id, *, now)` took the current instant as a parameter, which only makes sense if the answer is computed. Nothing ever wrote the column — every row has carried the `'never_seen'` default since the table existed. Stage 5's roadmap entry offers both options and observes that "dynamic calculation avoids stale stored values".

**Derived on read.** Liveness is a function of one stored instant and the current time. A stored conclusion is correct only until the clock passes its next threshold, and **nothing moves the clock on the platform's behalf** — so a station that stopped reporting would keep reading `online` until some unrelated write happened to refresh it. That is precisely the case liveness exists to detect, which makes the stored form wrong exactly when it matters. A column derivable from another column in the same row is a column that can disagree with it; D-049 refused that for `element_sets` and the same argument applies here. Migration `0008` drops it, taking `stations_liveness_idx` with it — an index over a column with one distinct value in every row could never have been selective. `last_heartbeat_at` stays: it is the measurement, `liveness` was the opinion.

**The thresholds do not derive from the heartbeat interval, and this was nearly got wrong.** D-013 already fixed `stale` at 60 s and `offline` at 90 s from SC-5, and states the direction of the dependency: the success criterion sets the threshold rather than the other way round. Deriving them from `Settings.heartbeat_interval_s` would let a deployment raise the interval and quietly stop meeting SC-5 while every dashboard still read "offline" as though it meant the same thing.

**What the interval does constrain is its own ceiling.** At D-030's 30 s, `stale` is two missed heartbeats and `offline` is three. At 45 s a station heartbeating exactly on time sits more than 60 s past its last heartbeat for a third of every cycle and flaps into `stale` while behaving exactly as specified — and every reliability figure reading liveness inherits it. So `load_settings` refuses `HEARTBEAT_INTERVAL_S` above 30. **That refusal fires on every start, not only a public one**, unlike the placeholder-secret check beside it: a placeholder on loopback exposes nothing, whereas a wrong liveness number on a laptop is the one that gets copied into a report.

**Where the code lives.** `meridian.registry.liveness` is a leaf module importing nothing from `meridian`, which is what lets `meridian.config` check a setting against it without an import cycle through the store layer. It owns the `Liveness` vocabulary (re-exported from `meridian.registry`, so callers are unaffected), both thresholds with their provenance, and a pure `derive_liveness` that reads no clock — the instant is passed in, so a caller classifying a page of stations gives them all one value and a test states an age instead of sleeping for it.

**An unknown station raises rather than returning a fifth value.** `UnknownStationError` is a `LookupError`. A caller reaches `liveness()` with an id it has already listed, so an id the registry cannot find is a caller bug, not a state for every call site to branch on. Soft-deleted stations read as absent rather than as permanently offline — a deleted station is not a fault to investigate.

**A safety test refused the drop, and it was right to.** `test_no_sql_file_drops_or_truncates` banned `drop column` outright in any migration — "migrations add; they do not destroy". Rather than edit the assertion until it passed, the ban stays and the exception is now an explicit entry in `ALLOWED_DESTRUCTIVE` naming the file and the statement, so weakening the rule costs a reviewed line in a diff instead of a quiet change to a test nobody re-reads. The bar for an entry is that **no data can be lost** — not that losing it would be convenient — and `stations.liveness` clears it because nothing ever wrote the column. Two further tests hold the allowance honest: one fails if an entry no longer matches the file it names, so a stale permission cannot sit there licensing a future drop; the other refuses any allowance whose statement touches `observations`, `heartbeats`, `passes` or `assignments`, whatever the file is called. That last one was verified to fail by appending a drop against `observations`, which is how we know it can.

*Rejected:* the roadmap's other option, a periodic background job writing the column. It buys nothing here — the derived answer is a subtraction against an indexed timestamp — and it would reintroduce the staleness above between runs. A job is still the right shape for *alerting* on transitions later, and it can compute transitions by comparing derived values without needing anywhere to store the current one.

---

## D-055 — A token that names another station is `not_owner`, and `health` is capped where the body cap cannot see it

**2026-08-08 · accepted** · *records two choices made while implementing MSP §4.2*

**A bearer token that authenticates as one station, presenting a body naming another, is `not_owner` (403).** MSP §6's table glosses `not_owner` as "Assignment was not issued to this station", so §6 does not describe this case at all. The eight codes are closed — a ninth would reach a station as a 500 (`errors.MspError` refuses to build one) — so the choice is between an existing code and inventing protocol, and inventing protocol is not on the table.

`unauthorized` (401) is the near miss and is **rejected on its consequence**. D-024 requires a station meeting 401 to *stop, log and surface to its operator*, and not retry. That is correct for a dead credential and wrong here: the token is live and the fault is the station's own body. A 401 would take a working station off the network over a client bug, and the operator would go looking at the credential rather than at the payload. `not_owner` says "you are not who you claim" at 403, without condemning the token.

**The token is the identity; the body's `station_id` is the station restating it.** The disagreement is refused rather than resolved either way — trusting the token would let a leaked credential file heartbeats under another station's name, and trusting the body would be no authentication at all. The response names neither value, so a token holder cannot probe which station ids exist (MSP §3).

*Consequence for the specification:* `MSP-SPEC.md` §6's gloss for `not_owner` is now narrower than its use. A third-party implementer reading only §6 would not expect a 403 here. The gloss should widen to cover identity as well as assignment ownership; that is a wording change to the published protocol and belongs in the next specification pass, not a silent edit alongside an endpoint.

**`health` is capped at 4 KiB in the Pydantic model, not in the request-size middleware.** D-028 sets both limits and D-050 built the middleware, which reads `Content-Length` and knows nothing about the body's contents. A 60 KiB request carrying a 60 KiB `health` object passes the 64 KiB body cap and must still be refused, so the narrower limit has to live where the parsed object is. Both produce `malformed`, so the split is invisible on the wire and matters only to whoever goes looking for where a limit is enforced.

---

## D-056 — `was_listening()` takes a `mode` and a Doppler-sized frequency tolerance

**2026-08-08 · accepted** · *settles the contract before anything implements it*

`Registry.was_listening()` is declared in Phase 1 and implemented in Stage 5, and the roadmap calls it *"the only authority reliability uses to classify absence"*. Two faults in its declared signature are cheap to fix now and become cross-module changes the moment anything calls it.

**It gains `mode`.** D-028 added `heartbeats.listening_mode` for exactly this method — *"a station tuned to the right frequency running the wrong demodulator did not observe the pass"* — and the signature then omitted the parameter that would let it use the column. Without it, the method cannot answer the question D-028 stored the data to answer.

**With five parameters it takes a dataclass instead.** `CLAUDE.local.md` §2 caps a function at four (five, hard) and says to take a dataclass beyond that. `ListeningQuery` bundles the station, the satellite, the frequency, the mode and the window, matching the precedent set by `store.stations.NewStation` and `OrbitService.pass_windows`.

**The frequency match is a tolerance, not an equality, and the tolerance is Doppler.** `centre_freq_hz: int` compared exactly would answer "was listening" as *false* for every station that retunes during a pass — which is every station that works. The shift at 137 MHz in low Earth orbit reaches roughly ±3 kHz, wider than the receiver's passband, so a station has to retune continuously across a pass rather than sit on the nominal frequency. A station reporting its *current* tuned frequency mid-pass is reporting a Doppler-shifted one.

So the tolerance is the largest shift the geometry can produce: `centre_freq_hz × v_max / c`, with `v_max` a bound on range rate in low Earth orbit. **Fractional rather than fixed, because Doppler is proportional to frequency** — a fixed ±4 kHz would be right at 137 MHz and three times too tight at 437 MHz, which is the band Tier 3 adds (D-053).

*Checked against the risk it creates:* a tolerance wide enough to confuse two satellites would let a station listening to one be credited for another. At 137 MHz the tolerance is under ±4 kHz, and the closest pair in the band this project cares about is NOAA's 137.9125 MHz against Meteor's 137.900 MHz — 12.5 kHz apart, comfortably outside. The tolerance is narrower than the band's own channel spacing, which is the property that has to hold.

**Timing is judged on `received_at`, never `sent_at`.** `0006_heartbeats.sql` already says `sent_at` is the station's clock and untrusted, and this is where that matters most: a station with a broken clock could otherwise assert coverage of any window it liked, and this method decides what counts as a miss. `received_at` is the platform's own clock and the hypertable's partitioning column, so the honest choice is also the fast one. Network delay is seconds against a pass of eight to fifteen minutes.

**Overlap, not coverage.** The roadmap's wording is *"must prove overlap with the pass time"*, and one confirming heartbeat inside the window is what this returns. **This is a real limit and is stated rather than hidden:** a station that listened for thirty seconds of a twelve-minute pass and stopped satisfies it. Refining it to a coverage fraction needs a threshold nobody has evidence for yet, and belongs with the reliability layer that consumes it in Stage 20 — the method returning `bool` is what would have to change, so it is flagged here as the known revision rather than designed around now.

**A valid assignment is part of the proof.** The roadmap lists it fourth, and it is the one criterion that is not a property of the heartbeat alone: the `listening_assignment_id` must name an assignment actually issued to that station. Without the join a station could assert listening against an id it invented, and the platform would count a miss against a pass nobody scheduled.

---

## Open

All four questions carried from `MSP-SPEC.md` §9 are now resolved.

| | Question | Resolution |
|---|---|---|
| **O-1** | Product upload inline or pre-signed URL? | D-029 — metadata only in 0.x; pre-signed PUT when the table lands |
| **O-2** | Push assignments or is heartbeat polling sufficient? | D-030 — polling, for all of 0.x |
| **O-3** | How does a station report a horizon obstruction it already knows about? | D-031 — optional `horizon_mask`, kept distinct from the learned profile |
| **O-4** | Cap `doppler_samples` by count, or transmit a compressed curve fit? | D-032 — capped at 512 by count |

**Nothing is now unrecorded.** `GIT-WORKFLOW.md` Rule 10's question — whether AI-assisted commits are marked — was carried as outstanding through every previous pass and is settled by **D-043**: a `Co-Authored-By` trailer from that entry forward, with existing history left alone. That was the last Stage 0 item.

---

## Applying these

**Landed 2026-07-31**, on branch `msp-0.1-decisions`.

| Decision | Applied to |
|---|---|
| D-003 decline via `held_assignments` | `MSP-SPEC.md` §4.2, §4.3 |
| D-004 error body | `MSP-SPEC.md` §6 |
| D-005 `simulated` top-level | `MSP-SPEC.md` §4.1, §5 |
| D-006 invite token | `MSP-SPEC.md` §3, §4.1 |
| D-007 assignment cap of 8 | `MSP-SPEC.md` §4.2 |
| D-008 `assignments.state` | `DATA-MODEL.md` |
| D-009 interference measurement and profile | `DATA-MODEL.md` |
| D-010 `outcome` enum pinned to MSP §4.4 | `DATA-MODEL.md` |
| D-011 SC-6 restored | `EVALUATION.md` §1 |

**Landed 2026-08-01**, preparing Phase 1 implementation.

| Decision | Applied to |
|---|---|
| D-012 three distributions, `src/` layout | — (tooling; layout in the docs is unchanged) |
| D-013 keys, enums, partitioning, `liveness`, `simulated` coverage | `DATA-MODEL.md` |
| D-014 `held_assignments` array column | `DATA-MODEL.md` |
| D-015 append-only observations, `supersedes_id` | `DATA-MODEL.md`, `MSP-SPEC.md` §6 |
| D-016 MSP 0.1 amendments | `MSP-SPEC.md` §4.2, §4.4, §7, §8 |
| D-017 opaque hashed tokens | `DATA-MODEL.md`, `deploy/.env.example` |
| D-018 Phase 1 table subset | `DATA-MODEL.md` |
| D-019 raw SQL under Alembic | — (tooling) |
| D-020 `invite_tokens` table | `DATA-MODEL.md`, `deploy/.env.example` |
| D-021 assignment window columns, `satellite_transmitters` | `DATA-MODEL.md` |
| D-022 no reissue in Phase 1 | `DATA-MODEL.md` |

**Landed 2026-08-02**, closing the Stage 0 specification gaps in `docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md`.

| Decision | Applied to |
|---|---|
| D-023 registration key and credential rotation | `MSP-SPEC.md` §4.1, §6; `0002_stations.sql`; `deploy/.env.example` |
| D-024 `401` does not mean re-register | `MSP-SPEC.md` §6 |
| D-025 clock offset sign named | `MSP-SPEC.md` §4.2; `tests/unit/test_clock_offset_convention.py` |
| D-026 assignment delivery policy | `MSP-SPEC.md` §4.2 |
| D-027 derived `observation_id` | `MSP-SPEC.md` §4.4; `DATA-MODEL.md`; `0005_observations.sql` |
| D-028 heartbeat completeness and size limits | `MSP-SPEC.md` §4.2, §6; `DATA-MODEL.md`; `0006_heartbeats.sql` |
| D-029 O-1 products transfer | `MSP-SPEC.md` §4.4, §9 |
| D-030 O-2 polling | `MSP-SPEC.md` §9 |
| D-031 O-3 declared horizon mask | `MSP-SPEC.md` §4.1, §9; `DATA-MODEL.md`; `0002_stations.sql` |
| D-032 O-4 Doppler sample cap | `MSP-SPEC.md` §4.4, §9 |
| D-033 one `DATABASE_URL` | `meridian/config.py`, `deploy/migrations/env.py`, `docker-compose.yml`, CI |

**Landed 2026-08-03**, closing the contradictions a review found in the Stage 0 record.

| Decision | Applied to |
|---|---|
| D-034 bound replacement invites | `MSP-SPEC.md` §4.1, §6; `DATA-MODEL.md`; `0002_stations.sql` |
| D-035 delivery eligibility and the cap | `MSP-SPEC.md` §4.2 |
| D-023 amendment — `and`, not `or` | `MSP-SPEC.md` §4.1; `DATA-MODEL.md`; `meridian/config.py`; `deploy/.env.example` |
| D-024 amendment — points at D-034 | `MSP-SPEC.md` §6 |
| D-026 amendment — superseded rows marked | — (this file) |

**Landed 2026-08-05**, building out the public site.

| Decision | Applied to |
|---|---|
| D-037 two themes, palette read from CSS | `site/theme.js`, `site/style.css`, `site/main.js`, `site/README.md` |
| D-037 contrast fixes (`--muted`, `.legend dd`) | `site/style.css`, `site/README.md` |
| D-038 five pages, shared shell | `site/index.html`, `site/{architecture,protocol,docs,about}/index.html`, `site/404.html` |
| D-038 SEO and metadata layer | all pages; `site/robots.txt`, `site/sitemap.xml`, `site/site.webmanifest`, `site/_headers` |
| D-038 `_redirects` cannot do host matching | `site/README.md` (no file added) |
| — brand exports for marketing | `site/tools/make-images.py`, `site/brand/` |
| D-039 hairline and body-copy contrast raised | `site/style.css`, `site/README.md` |
| D-040 document rail, module split, transitions | `site/orbit.js`, `site/rail.js`, `site/style.css`, `site/main.js`, all pages |
| D-040 wide footer, contact address | all pages, `site/.well-known/security.txt` |
| D-041 intro gate inverted, `visibility` fix | `site/theme.js`, `site/main.js`, `site/style.css`, `site/index.html` |
| D-041 canvas sized from its own box | `site/main.js` |
| D-041 rail deferred behind its breakpoint | `site/rail.js`, `site/{architecture,protocol,docs,about}/index.html` |
| D-041 footer copy, three Cloudflare settings | all pages; dashboard, not this repository |

**Landed 2026-08-06**, after the site was reported broken again from a real phone and a real desktop.

| Decision | Applied to |
|---|---|
| D-042 `?v=<hash>` on every CSS and JS reference | `site/tools/stamp_assets.py`, all pages, `site/main.js`, `site/rail.js` |
| D-042 `verify_site.py` and the CI `site` job | `site/tools/verify_site.py`, `.github/workflows/ci.yml`, `pyproject.toml` |
| D-042 `.foot-bar a` — the unstyled licence link | `site/style.css` |
| D-042 still globe for the no-JavaScript path | `site/tools/make-images.py`, `site/index.html`, `site/style.css` |
| D-042 `<h1>` out of the reveal, LCP 4 400 ms → ~110 ms | `site/index.html`, `site/style.css`, `site/README.md` |
| D-042 mobile and accessibility fixes, print sheet | `site/style.css` |
| D-042 header hardening | `site/_headers` |
| D-042 the three Cloudflare settings, still outstanding | dashboard, not this repository |

**Landed 2026-08-06**, closing the last Stage 0 item and putting Stage 1's enforcement into force.

| Decision | Applied to |
|---|---|
| D-043 AI-assisted commits carry a trailer | `docs/GIT-WORKFLOW.md` rule 10 (question closed); no code |
| D-044 the section 9 ruleset, unioned with `DTZ`/`TID` | `pyproject.toml` |
| D-044 four documented deviations | `pyproject.toml` per-file-ignores |
| D-044 `InsecureConfiguration` → `InsecureConfigurationError` | `meridian/config.py`, `tests/unit/test_config.py` |
| D-044 `pass_windows` takes `PassSearch` | `meridian/orbit/{types.py,service.py,__init__.py}` |
| D-044 `dict[str, str]` and a narrowed `except` | `meridian/api/app.py` |
| D-044 module-length check, verified by breaking it | `.github/workflows/ci.yml` |
| — one psycopg pool in the lifespan (roadmap Stage 2) | `meridian/store/pool.py`, `meridian/api/app.py`, `tests/integration/test_pool.py` |
| — MSP version parsing and the §6 error shape (Stage 3) | `meridian/api/{versioning.py,errors.py}` |
| — `GET /msp/v0/time` (Stage 4.1) | `meridian/api/msp.py`, `tests/msp_conformance/` |
| — client transport and clock estimator (Stage 4.1) | `meridian_client/{transport.py,clock.py}` |
| D-045 clock uncertainty floored at clock resolution | `meridian_client/clock.py`, `tests/unit/test_clock_offset_convention.py` |

**Migrations were amended in place rather than patched.** `GIT-WORKFLOW.md` Rule 9 protects *merged* migrations; `deploy/migrations/` was still untracked when D-023 through D-035 landed, so 0002, 0005 and 0006 were drafts, not history. A 0007 that patched a 0006 nobody had ever applied would have been a worse artefact to defend than one readable file per table. From the first commit of `deploy/migrations/`, Rule 9 binds normally — and that commit has not happened yet at the time D-034 amends `0002_stations.sql`.

**On the joint review.** `MSP-SPEC.md` required a joint review by all three team members before Phase 1 implementation began. That review did not take place as a meeting. D-012 through D-022 were written instead: every gap the review would have been convened to find is recorded above with its reasoning and its rejected alternative, and the specification is frozen at 0.1 by that written record.

This is a deliberate trade — a written decision log is more durable evidence than a meeting nobody minuted, and Phase 1 has a hard week-15 downstream gate. It is recorded here rather than quietly dropped, because the requirement is written into the specification and anyone reading the repository can see it.

**On amending accepted entries.** D-023, D-024 and D-026 are amended above rather than rewritten. An entry records what was decided and why at the time it was decided; editing that away leaves a log that has never been wrong, which is not evidence of anything. The amendment notes say what the original got wrong and point at the entry that supersedes it, so a reader following a cross-reference from the specification arrives at the current rule either way.
