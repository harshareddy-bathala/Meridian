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

*Amended by D-067.* The eligibility predicate above lists two states, which was complete until heartbeat reconciliation could produce a third: an assignment being executed is `in_progress`, and under the predicate as written it disappears from the response mid-pass. `in_progress` joins `issued` and `held`; the two bounds are unchanged.

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

*Amended by D-057.* The key named above, `element_set_unique (satellite_id, epoch, source)`, no longer exists — migration 0009 replaced it with `element_set_content_unique (satellite_id, source, content_sha256)`. The reasoning is untouched by that: `source` is still in the key, so the same lines from two providers are still two rows by design. Only the column list changed.

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

## D-057 — An element set is identified by its contents, not by its epoch

**2026-08-08 · accepted** · *migration 0009; found while starting Stage 6*

`element_sets` shipped with `unique (satellite_id, epoch, source)` and the table comment *"the same set retrieved twice is one row"*. The comment describes deduplication; the constraint does something stronger and different. **Two genuinely different element sets can carry the same epoch from the same source** — a catalogue correction, a re-fit after a manoeuvre, an operator republishing for the same epoch — and the second was rejected.

That is a data-loss path sitting behind a constraint that reads like a safety feature. `DATA-MODEL.md` says this table is never overwritten because the historical series is what makes uncertainty modelling possible, and the set that gets discarded is exactly the one that would explain why a prediction from that epoch was wrong.

**The key becomes `(satellite_id, source, content_sha256)`**, with the hash computed by an `IMMUTABLE` function and stored in a generated column. Three outcomes, which is the table this decision was chosen from and which `tests/integration/test_store_element_sets.py` asserts one test each:

| Insert | Rows after | Before 0009 |
|---|---|---|
| the same lines twice, one source | 1 | 1 |
| different lines, same epoch, same source | 2 | **1 — silently** |
| the same lines from `celestrak` then `spacetrack` | 2 | 2 |

**`source` stays in the key deliberately.** Keying on content alone would collapse the third row to one, discarding whichever retrieval arrived second. D-049 made `source` the way this table records provenance rather than a `simulated` boolean, so two retrievals of identical lines from two catalogues are two facts — and their agreement is the only cheap cross-check available on element-set data we did not generate.

**A generated column rather than a value the caller supplies.** The alternative — hashing in Python and inserting the result — makes a row whose hash disagrees with its lines representable, and the unique constraint then guards nothing, because two identical sets could carry different hashes. The `IMMUTABLE` marking is justified the same way `observation_id`'s is in `0005`: `convert_to` is `STABLE` because text-to-bytes depends on server encoding in the general case, and a two-line element set is fixed-width ASCII by format definition, so the bytes are identical under every encoding PostgreSQL supports. The lines are joined with a newline rather than concatenated bare — a pair split at a different boundary would otherwise hash identically, which cannot arise while the format stays fixed-width and costs one byte to stop depending on that.

*Consequence:* `find_element_set_current_at` orders by `epoch` and breaks ties on `retrieved_at`. Two sources publishing one epoch is now representable, so the tie-break had to be named rather than left to the planner — CLAUDE.md requires every number in a report to be regenerable, and a query that resolves a tie differently on different days is not.

---

## D-058 — A deleted station cannot be recovered, and rotation reports whether it happened

**2026-08-08 · accepted** · *found auditing `store/stations.py`*

`stations` carries a `deleted_at` for soft deletion, and `find_station_id_by_token_hash`, `find_station_heartbeat` and `revoke_station_token` all filter on it — a deleted station reads as absent. The two functions on the recovery path did not. `find_station_for_recovery` would return a deleted station's registration key, and `rotate_station_token` would mint a fresh bearer token onto it and return `None`.

The result was a recovery that answered `200` with a plaintext token, followed by `401` on the station's very next request, because the lookup that authenticates the token excludes exactly the row the rotation had just written to. **A success that did nothing** — the same shape as the invite race D-020 exists to prevent, and the reason that one is checked by return value.

**Both functions now exclude `deleted_at`, and `rotate_station_token` returns `bool`.** `meridian.registry.psycopg_registry` raises `InvalidInviteError` on a false return, which MSP §3 renders as `invalid_invite` — a client is not told whether the invite was bad or the station was gone.

**Rejected: leaving it, on the grounds that nothing deletes stations yet.** True today — only tests write `deleted_at` — and that is what makes it cheap to fix now. The failure is invisible from the client's side (a valid-looking token that never works) and would surface as an intermittent registration bug in whichever later stage adds the delete, long after the cause was written.

**Rejected: a distinct `station_deleted` error code.** MSP §3 deliberately gives one answer to every invite refusal so a client cannot enumerate station ids by watching which failure it gets. A deleted station is a refusal like any other.

*Consequence:* the second guard in `_mint_and_rotate` is unreachable while `_recovery_info_or_raise` runs first. It is kept because the return value is what tells the caller its plaintext token is valid, and a caller holding a credential should not infer that from another function's ordering.

---

## D-059 — A pass belongs to the search it rises in, and the search looks past its own end to find it whole

**2026-08-09 · accepted** · *`pass_windows`; Stage 6 session C*

`pass_windows(search)` is given a start and an end, and Stage 7's pass-generation job will call it over a rolling horizon, so two searches meet at a boundary. A pass straddling that boundary has to belong to exactly one of them, and it has to come back whole — a scheduler cannot tell a window that ends because the satellite set from one that ends because the search stopped looking.

**The search widens by `PASS_SEARCH_MARGIN_S` at both ends and then keeps only the passes whose *acquisition* falls in `[start, end)`.** Acquisition is the key because it is the one boundary a pass has exactly once; keying on the window's overlap with the interval would match a straddling pass in both searches.

**The margin is 30 minutes, and the number is derived rather than picked.** A satellite is above the horizon while it lies within `arccos(R_earth / (R_earth + h))` of the station and covers that arc at the orbital rate: at 850 km — the altitude of the 137 MHz weather satellites this project receives — that is 56.2° of a 101.8-minute orbit, so 15.9 minutes; at 1400 km it is 22.0 minutes. Thirty minutes covers low Earth orbit with room. A pass that outlasts it **raises** rather than being returned clipped, and `tests/unit/test_pass_windows_reference.py` provokes that by shrinking the margin to a minute, because a guard nobody has fired is a claim rather than a check.

**Rejected: return the clipped pass and flag it** with an `is_truncated` field on `PassWindow`. That pushes the same decision onto Stage 7 and onto the completeness denominator, and `EVALUATION.md` §4.1 needs a denominator computed one way rather than one that depends on how the caller happened to slice time. The flag does exist one layer down, on `pass_search.ElevationPass`, where it is what lets this layer detect the condition at all.

**Rejected: drop passes not wholly inside the interval.** A day-by-day job would lose roughly two passes per satellite per boundary, permanently and silently, and the completeness ratio would improve as a result — the exact failure mode the ratio exists to expose.

~~*Known limit, stated rather than hidden.* Two searches meeting at a seam recompute the same acquisition on different coarse grids, so their answers can differ by up to the 0.05 s refinement tolerance. If a seam falls within that tolerance of a true acquisition, a pass can in principle be claimed by both searches or by neither. `tests/unit/test_pass_windows_reference.py` pins the deterministic case — a seam placed on an acquisition computed from the same grid — and the residual is a coincidence of order 1e-6 per boundary per satellite. It belongs to whichever job splits the horizon, which should de-duplicate on `(satellite_id, aos)`, and is recorded here so that job is written knowing it.~~

*Amended 2026-08-09 by D-063 — the limit is removed, not merely bounded.* The paragraph above accepted a residual ambiguity and pushed de-duplication onto the caller. Measuring it while building the pass store showed it was far larger than "one boundary in a million": shifting a horizon by seven seconds moved **every** acquisition by about two milliseconds, because the coarse grid began wherever the caller's horizon began. A rolling job keyed on acquisition would have stored a fresh copy of every pass on every run. D-063 aligns the scan grid to a fixed anchor, so two searches sharing a `coarse_step_s` now compute the identical acquisition to the microsecond and the seam question does not arise. The rest of this entry — searching past the interval, keying on acquisition — stands unchanged.

---

## D-060 — The Phase 1 timing uncertainty is an along-track prior, and it says so

**2026-08-09 · accepted** · *`orbit/uncertainty.py`; Stage 6 session D*

MSP §4.3 carries `timing_uncertainty_s` to stations, which use it to decide how much extra to record either side of a predicted acquisition. Phase 1 has no measured timing error to fit a model to, and `service.py` already rules out the tempting answer: returning `0.0` claims perfect knowledge of an element set whose accuracy is unstated, and a station reading it opens no margin at all.

**The prior is `(1.0 km + 3.0 km/day × age) / 7.5 km/s`.** An along-track position error moves the satellite forward or back along a path it follows anyway, so the instant it reaches the horizon shifts by that distance over its speed. Each constant has a source rather than a justification: Vallado, Crawford, Hujsak & Kelso, *Revisiting Spacetrack Report #3* (AIAA 2006-6753) puts SGP4 accuracy near epoch at about 1 km for low Earth orbit and its growth at 1–3 km/day; 7.5 km/s is `sqrt(mu/a)` at 800 km, the band the 137 MHz weather satellites occupy. That gives 0.13 s at epoch, 0.53 s after a day and 2.9 s after a week — which matches the operational experience that predictions from week-old elements are seconds out, not minutes.

**The growth rate is taken at the top of the published range deliberately.** The two errors are not symmetric: overstating costs a station some disk, understating has it start recording after the pass began, and a pass is eight to fifteen minutes that never repeats.

**It models along-track error only, and the docstring says so rather than implying completeness.** A pass boundary is a shallow crossing, so cross-track error also moves the crossing instant — by more than it moves the satellite. The figure is therefore a floor. Quantifying the rest needs measured timing error against predicted boundaries, which is exactly what Phase 2 collects.

**Rejected: inflating the prior by a safety factor** to cover what it omits. Any multiplier would be invented, and `CLAUDE.local.md` §5.4 rules out a constant whose provenance is "because it works". A defensible under-estimate that names its own limit is better evidence than a comfortable number nobody can source.

*Consequence:* every result carries `method = "vallado2006-along-track-v1"`. SC-3 compares this prior against a model fitted to measurement, and that comparison is impossible if a stored prediction cannot say which produced it — so the string is versioned, and replacing the model means a new string rather than an edit to this one.

---

## D-061 — The speed of light is written twice, and a test holds the two equal

**2026-08-09 · accepted** · *`orbit/doppler.py`, `registry/doppler_tolerance.py`*

Both modules need `c`. `registry.doppler_tolerance` uses it to bound how far off frequency a station may report and still count as listening; `orbit.doppler` uses it to compute the shift itself. They answer different questions — a bound against a measurement — and sit on opposite sides of a module boundary.

**Both define it, and `tests/unit/test_doppler.py` asserts they agree.** `registry.doppler_tolerance` is documented as a leaf that imports nothing from `meridian`, which is what keeps the registry free of an import cycle through the orbit package; coupling it to `meridian.orbit` to share one float would spend that property on a defined constant that has not changed since 1983.

**Rejected: a shared constants module.** It would hold one value, and `CLAUDE.local.md` §4 exists because modules like that accumulate everything that has no other home. The cost of the duplication is drift, and drift is what the assertion prevents.

**Rejected: importing one from the other.** Whichever direction is chosen makes one of the two modules depend on a package it has no other reason to know about, and the registry's leaf property is load-bearing while the duplication is not.

*Consequence:* the duplication is safe only while the test exists. It is written as a test rather than an assertion at import time so that it fails in CI rather than at whatever moment a station first registers.

---

## D-062 — MSP owes an implementer a way to check their own station, and does not have one

**2026-08-09 · accepted** · *found planning Stage 7; owned by Stage 10*

`CLAUDE.md` describes MSP as "an open protocol any receiving station can implement to join the network". `tests/msp_conformance` has 62 tests, and every one of them checks **our server**. Someone writing a station — in Python, in C on a microcontroller, in whatever a contributor prefers — has no way to check that their client is correct short of registering against a live platform and reading the errors.

That is the same defect class as a docstring describing a test that does not exist: a capability asserted in prose with nothing behind it. An open protocol whose only conformance suite tests the reference server is a specification with a reference implementation, not an open protocol.

**Recorded now, built at Stage 10.** Stage 10 already builds the simulator against the real client over real MSP, which is where the client-side seam gets its shape; a station-side conformance harness is the same seam pointed the other way. The deliverable is a suite an outside implementer can run against their own station, plus `docker compose up` giving them a platform to run it against — the compose requirement already exists.

**Rejected: building it now.** Stage 7 has no client work in it, and the harness would be written against endpoints that Stages 8 and 9 have not finished defining. Writing it early would mean writing it twice.

**Rejected: treating the existing conformance suite as sufficient.** It fixes the wire format, which is necessary and not sufficient: it cannot tell an implementer that their retry policy, their clock-offset estimator or their assignment state machine is wrong, and those are where a station implementation actually fails.

*Consequence:* the roadmap gains an owner for a goal that was previously only implied by CLAUDE.md's description of the protocol. Until it is built, "any receiving station can implement MSP" is a claim about the specification rather than a tested property.

---

## D-063 — A computed pass has a stable identity, which needs an aligned scan grid

**2026-08-09 · accepted** · *migration 0010; `orbit/skyfield_service.py`*

`passes` shipped with no unique key. The pass-generation job runs over a rolling horizon and is expected to be re-run — after a crash, when a new element set arrives, or on the next tick — and each re-run would have inserted a second copy of everything it already held. That is not untidiness: `passes` is the denominator of the completeness ratio in `EVALUATION.md` §4.1, so duplicating it halves every completeness figure the project publishes.

**Identity is the prediction, not the pass:** `unique (station_id, satellite_id, element_set_id, min_elevation_deg, aos)`. Re-running with unchanged inputs writes nothing. A **newer element set** predicting the same rise is a second row on purpose — the same rise predicted twice, seconds apart, and the difference between them is the measurement that makes element-set age a usable feature. D-057 reached the same conclusion for `element_sets`: the series is the measurement, so nothing in it is overwritten.

**The constraint is worthless without a deterministic acquisition, and it was not deterministic.** `pass_windows` began its coarse scan at the caller's horizon, so the bracket around a crossing depended on where the horizon started. Measured directly rather than assumed: shifting the horizon by 7 s moved every acquisition by ~2 ms, and by 17 s moved it ~21 ms. Every re-run would have produced acquisitions that never compared equal, and the constraint would have silently never fired — a safety feature that does nothing, which is the failure mode this project has already found twice.

**The scan grid is therefore aligned to a fixed anchor** (`GRID_ANCHOR`, 2000-01-01) rather than to the interval requested, so the grid is a property of `coarse_step_s` alone. Two searches sharing a step sample the same instants wherever their horizons begin, and one pass has one acquisition to the microsecond. `tests/unit/test_pass_windows_reference.py` asserts equality across four unaligned horizons, and asserts it exactly — near-equality is precisely the state that was wrong.

**Rejected: rounding `aos` to the second for identity.** It collapses the common case and fails on the uncommon one: an acquisition near a second boundary rounds two ways, which is about 5% of passes at a 0.05 s refinement tolerance. Fixing the determinism at the source costs less and is checkable.

**Rejected: one row per physical pass, updated in place as better elements arrive.** It discards the prediction history, which is the input to the uncertainty model, and it makes `passes` mutable — the same mistake D-057 corrected in `element_sets`.

*Consequence:* the completeness denominator must count **distinct physical passes**, not rows, because one pass may legitimately hold several predictions. The grouping rule belongs to the evaluation stage that computes the ratio, and is owed by it rather than assumed here. *Settled by D-148.*

---

## D-064 — What pass generation skips, and why every skip is deliberate

**2026-08-10 · accepted** · *`registry/capability_match.py`, `pass_generation.py`, Stage 7*

The pass-generation job decides which station-and-satellite pairs are worth propagating at all. Every pair it skips is a pass that never enters `passes`, and `passes` is the completeness denominator (`EVALUATION.md` §4.1) — so **a wrong skip does not produce a visible error, it produces a better-looking ratio.** That asymmetry is why each filter is recorded here rather than left to read off the SQL.

**A capability covers a transmission when the nominal frequency is inside the declared range, endpoints included, *and* the mode matches.** Mode comparison is case- and whitespace-insensitive but whole-string: `modes` is free lowercase text in Phase 1 because decoder naming varies too much across projects to freeze an enum, so `LRPT` against a catalogue's `lrpt` is a matching bug, while `lrpt` matching `lrpt_hrpt` by prefix would conflate two demodulators.

**A chain declaring no modes matches nothing.** `station_capabilities.modes` defaults to `'{}'` and the API sets no minimum, so this is reachable from a real registration. Reading the empty list as "any mode" would schedule the station for every pass in its frequency range, and those become *confirmed misses*: the station heartbeats, listens, and returns no frames. That is the one input the reliability layer must never be handed on purpose — D-056 and rule 7 exist to separate a broken station from an idle one, and this would manufacture the confusion at the source. A station that receives no passes notices and asks; a reliability figure quietly poisoned by passes nobody could have decoded does not.

**Where several chains cover one transmission, the search uses the lowest declared floor.** The station can take the pass if any of its hardware can. Searching at the higher floor would drop passes the other antenna could have received, and — again — a dropped pass is not merely unscheduled, it is absent from the denominator forever.

**The nominal frequency is tested, not the range the signal actually sweeps.** A pass Doppler-shifts by roughly ±3 kHz at 137 MHz, so a chain whose declared range ends within a few kilohertz of the nominal loses part of the excursion. Phase 1 does not model that: the declared range describes hardware the operator knows and we do not, and narrowing it by a margin nobody has measured would exclude working stations. **This is a stated limit, not an oversight** — the failure it leaves is visible in the observation record as reception degrading at one end of a pass, rather than silent. Note the deliberate asymmetry with D-056: `was_listening` needs a Doppler-sized *tolerance* because it compares against a frequency a station reported mid-pass; this compares against a catalogue value that is never shifted.

**Stations excluded: deleted, and token revoked. Stations kept: offline, stale, and never-seen.** The first two are permanent — a revoked token cannot authenticate, so such a station can never be issued an assignment nor report against one (D-017, D-058), and its passes would pad the denominator with opportunities nothing could act on. Liveness is the opposite case: a station that was down for a day genuinely had passes it did not take, *and that is the outage*. Removing them would let an outage improve the numbers, which is the same failure "absence is not a miss" guards against at the far end of the pipeline.

**Satellites excluded: deleted, inactive, or with an inactive transmitter.** `EVALUATION.md` §5's silent-satellite confound — a pass that produced nothing may mean a bad prediction or a payload that was switched off. Where the catalogue already records that it was off, the cheapest place to keep the pass out is before it is computed.

**The element set is the one current at the horizon start, never the newest.** Fixing it at the horizon start is what makes re-running the job over a past horizon reproduce its own output, which together with D-063's aligned grid is why a second run stores nothing. Using the newest set would change the answer every time a catalogue published, and each run would write a duplicate prediction of every pass.

**One propagation per station-and-satellite pair, not per transmitter.** `passes` has no transmitter column, and rightly so: the geometry is a property of the two bodies, not of a downlink. A satellite whose transmitters a station can receive on different chains is searched once, at the lowest of those chains' floors.

*Consequence, stated rather than discovered later:* a satellite that is tracked, has a live transmitter and has **no element set** the archive can place at the horizon start produces no passes. That is a gap in the archive rather than a decision, so it is **reported by name** in `GenerationReport.satellites_without_element_set` and printed by the CLI. Silence there would be indistinguishable from "this satellite never rises".

---

## D-065 — A schedule records what it scored, what displaced what, and how long the antenna takes

**2026-08-10 · accepted** · *migration 0011; `scheduler/{elevation_baseline,conflict_rejection}.py`, Stage 7*

`assignments` could say a pass was `skipped` and give a `reason` in prose. It could not say what the candidate scored, or which decision took the slot. `PROJECT.md` §13 calls the screen showing this reasoning *"the entire project"* — and a screen that says "skipped: conflicted with another pass" without naming the pass or ranking the two is showing an assertion, not a decision.

**`score` is a new column, not `predicted_yield` reused.** `predicted_yield` is constrained `between 0 and 1` and *means* an expected yield. Configuration A's score is a number of degrees; B's is a priority-weighted quantity that is neither bounded nor a probability. Overloading one column with three meanings would make `EVALUATION.md` §3's comparison of A, B, C and D unreadable at exactly the point it has to be read. The column is unitless deliberately: the unit is a property of `model_config`, so the two are read together or not at all.

**`conflicts_with_assignment_id` names an assignment, not a pass.** Naming the winning *pass* looks equivalent and is not: one physical pass can be predicted several times (D-063), so a pass id identifies an opportunity rather than the decision that displaced this candidate. Within a single run the two are interchangeable, which is why the pure scheduling layer works in pass ids and the run that writes the schedule maps them — across runs, only the assignment id still answers the question.

**The ranking's tie-break is part of the algorithm.** Ties are not an edge case here: a station tracking one satellite sees near-identical geometry on consecutive days, and simulated stations sharing a seed produce exact ties by construction. Ranking is therefore a total order — score descending, then earlier acquisition, then `pass_id`. Left to the input order, the schedule would depend on the order rows came back from the database, and every number computed from it would stop being reproducible, which CLAUDE.md requires each one to be. Earlier acquisition wins because a pass in hand is worth more than an identical one later: less time for the station to go offline, for the element set to age, or for an operator to intervene.

**Ranking is global; non-overlap is per station.** A pass competes for one station's antenna, so conflicts are judged within a station and two stations never conflict however much their passes overlap — a rule that serialised them would make every station after the first useless. Ranking deliberately does *not* group by station, because the Stage 18 optimiser allocates across the network and a per-station ranking here would quietly fix its shape.

**Both selections and rejections are outputs, and every candidate appears in exactly one of them.** A scheduler returning only what it took would leave the skipped passes in no list and no table, and the screen explaining the schedule would have a hole in it with nothing able to detect one.

**Turnaround between two receptions is an input, not a constant, and its value is not decided here.** `ARCHITECTURE.md` requires non-overlap *including slew and settling time*, so the rule takes a `turnaround_s` and honours it; two passes that abut exactly are compatible for a fixed antenna and are not for a rotator, and both answers are correct for the station they describe. What the *platform* should pass is genuinely undecided: nobody has measured station 001's rotator, `stations` has no column for it, and `station_capabilities.tracking` is a boolean that does not imply a duration. **Recorded as owed by the scheduler run** (Stage 7 Session D), which is the first code that must supply a number.

*Consequence, stated rather than discovered later:* whatever value that run picks, the baselines and the Stage 18 optimiser must use the same one, or `EVALUATION.md`'s SC-1 measurement of **D − B** compares two schedulers working to different physical constraints and attributes the difference to the model.

---

## D-066 — Configuration B is a product, priority lives on the satellite, and a schedule can be re-run

**2026-08-10 · accepted** · *migration 0012; `scheduler/{priority_baseline,run,assignment_records}.py`, Stage 7*

`EVALUATION.md` §3 defines configuration **B** as *"elevation + priority weighting"* and says nothing about how the two combine. Writing the scheduler run surfaced two further gaps in the schema. All three are settled here.

**B's score is elevation × priority, not elevation + priority.** The two have no common unit: degrees plus a weight is a number with no meaning, and its behaviour depends entirely on the arbitrary scale chosen for the weight — a network using priorities of 1–5 and one using 100–500 would rank differently for no reason anybody intended. A multiplier is a statement about relative worth, which is what "weighting" already means.

*The property this was chosen for:* with every priority at 1.0, **B reduces to A exactly** — same order, same scores. That is asserted in the tests rather than assumed, and it is what makes SC-1's **D − B** a measurement of priority weighting rather than partly of a formula change.

*Rejected: lexicographic — priority first, elevation as tie-break.* It lets a priority-2 satellite at 10° displace a priority-1 pass at 50°, so a station spends its slot on geometry that mostly does not decode. A weight should shade a decision, not overrule it.

**A non-positive priority is refused rather than clamped.** Zero makes every pass of that satellite tie at exactly zero whatever its geometry, so the ranking silently stops being about elevation and falls through to the tie-break; negative inverts it, ranking that satellite's worst pass above its best. Both produce a schedule that looks entirely normal. A satellite an operator wants excluded is **deactivated**, which removes it from pass generation and from the completeness denominator honestly (D-064) rather than filling the denominator with opportunities the scheduler was never going to take.

**Priority belongs to `satellites`, not to `assignments`.** `assignments.priority` records what a decision *used*, which is the right thing for it to record and useless as an input — reading it back would derive next week's weighting from last week's schedule, so the first run would have nothing and every run after it would be quoting itself. An operator has opinions about *objects*: "Meteor-M is the project, this cubesat is a bonus". Migration 0012 adds `satellites.priority`, defaulting to 1.0 to match `assignments.priority` and so preserve the reduction above.

**A schedule has an identity, so a run can be repeated.** Re-running the scheduler inserted a second complete copy — the failure D-063 fixed for `passes`, in the table that consumes them, and worse here: two `scheduled` rows for one pass means a station told twice to receive the same thing, and MSP §4.2's reconciliation holds two ids for one reception. `unique (pass_id, model_config)` fixes it, and **both parts of the key matter**: keyed on the pass alone, configurations A and B would collide, and running both over one horizon is exactly what the ablation requires — the two schedules have to coexist to be compared.

**Assignment ids are derived, not random**: `as_` + the first twelve hex of `sha256(pass_id:model_config)`, following `observations.observation_id` (D-027). A repeat therefore mints the same ids and collapses onto that constraint, and a skip can name the assignment that displaced it without a round trip to discover what id the winner was given.

**Phase 1's station turnaround is zero, and that is a fact about the hardware rather than a simplification.** D-065 left this open. `PROJECT.md` builds station 001 with a fixed quadrifilar helix at 137 MHz and makes the tracking build — a crossed Yagi on a rotator — Tier 3 and explicitly optional: *"every claim the project makes is provable with a fixed antenna."* A fixed antenna does not slew, so there is nothing to wait for between passes, and any positive value would discard passes the station could genuinely have taken. The simulated stations are fixed by construction too.

*What has to change before a tracking station can be scheduled correctly, stated now rather than discovered then:* turnaround becomes a per-station column with a **measured** value, because `station_capabilities.tracking` is a boolean that implies no duration. Until that exists, a station registering with a rotator will be scheduled as though it were fixed. The constant lives in `cli_schedule.PHASE_1_TURNAROUND_S` with this reasoning attached, and the non-overlap rule takes it as a parameter — so the change is a value and a column, not a rewrite.

**One transmitter per assignment, chosen deterministically.** A satellite may carry several downlinks a station can receive; the assignment names one frequency. Phase 1 takes the first in the catalogue's order — by frequency, then by id. Which downlink is worth more is a question about expected yield, and there is no model to answer it with until Stage 17. Picking arbitrarily but reproducibly is the honest placeholder, and the choice is visible in `assignments.centre_freq_hz` rather than hidden.

---

## D-067 — Overdue is not expired, and an assignment being executed is still delivered

**2026-08-10 · accepted** · *`registry/heartbeat_reconciliation.py`, `store/assignments.py`, `api/msp.py`, Stage 8*

Wiring MSP §4.2's reconciliation into the heartbeat endpoint surfaced two defects. Both are the same shape as the one D-035 found in D-026 — a rule stated in two places, disagreeing — and both would have shipped, because each was a query written against one sentence of a specification that contains two.

**An assignment the station still names does not expire, however overdue it is.** `expire_overdue_assignments` moved every `issued` or `held` row past its `end_at`. MSP §4.2's table defines that row as *"Absent, window has passed"*, and D-008 defines the state as the station never having taken the work. A station that held a pass, executed it and is queuing its observation satisfies neither, and expiring it is not a cosmetic mislabel: **D-008 has no arc from `expired` to `reported`**, so the observation arriving in Stage 9 would have nowhere to land. The predicate now excludes anything named in the heartbeat driving the sweep.

*The cost, stated plainly:* a station that stops heartbeating altogether never has its overdue rows swept, because the only sweep is the one its own heartbeat triggers. D-026 accepted per-heartbeat reconciliation and Phase 1 has no periodic job; the alternative is a background sweeper, which is a scheduled process to maintain and a second writer on the hot table, to correct rows nothing reads until the reliability layer exists. **A periodic sweep is owed by the reliability stage**, which is also the first consumer that would notice the difference. Recording it because a station stuck at `held` forever is a real state a reader will find.

**An assignment in `in_progress` must still be delivered.** The delivery predicate was `state in ('issued', 'held')`, which was correct until reconciliation could produce a third state. The moment a station reports a `listening` block, its row leaves `held` — and the assignment disappears from its own heartbeat response, mid-pass. A station that reboots while receiving is then told it has nothing to do.

This is precisely the failure D-035 fixed by moving the lower bound from `start_at` to `end_at`, arriving a second time through a different column. MSP §4.2's prose is the arbiter and was already right: a station sees an assignment *"on every heartbeat until it is reported or its `end_at` has passed"* — and executing it is neither. The literal predicate printed two paragraphs later was the thing that was wrong.

```
state in ('issued', 'held', 'in_progress')  and  end_at >= now  and  start_at <= now + 2 h
```

*This amends D-035 as D-035 amended D-026.* `MSP-SPEC.md` §4.2's delivery block is updated in the same change, so the specification and the query cannot drift again.

**Three smaller rules, settled while the comparison was being written.**

*A station listing an assignment the platform has already `reported` or `expired` is not a protocol error.* MSP §4.2's fourth row is for an id *never issued to this station*, which is a broken implementation and worth a warning. A station lagging behind a state change is neither, and treating the two alike would fill the log with the one signal meant to mean something is genuinely wrong. The comparison is therefore against every assignment ever issued to that station, not against the two states that can transition.

*A `listening` block naming an assignment absent from the same heartbeat's `held_assignments` starts nothing.* The message contradicts itself, and a contradiction is not evidence. `in_progress` means the station holds this work and is executing it; half of that claim, from a station that just denied the other half, does not support the transition — and `Registry.was_listening()` rests on it.

*Holds are applied before starts.* A station whose pass opened between two heartbeats genuinely confirms and begins in one message, and `mark_assignment_in_progress` moves a row only out of `held`. Refusing the pair would delay `in_progress` by a whole 30-second interval, which against an 8-minute pass is the part carrying the rise.

---

## D-068 — The station writes things down before it claims them

**2026-08-11 · accepted** · *`client/credentials.py`, `client/held_assignments.py`, Stage 8*

A station holds three pieces of durable state: a registration key, a bearer token, and the assignments it has accepted. All three have the same failure mode — a power cut between deciding something and recording it — and the same fix, applied in the same direction each time.

**The write comes before the claim.** The registration key reaches disk before the `register` request is sent (D-023), and an assignment reaches the record before it can appear in `held_assignments`. Reversed, each becomes a lie the platform cannot detect: a consumed invite with no key to recover it, or a station reporting that a pass is covered when the work vanished with its memory. Getting the order right is what removes the error path entirely — if the write fails, the station simply never makes the claim, the platform reads absence as a decline (D-003), and the outcome is correct with nobody handling an exception.

**Every write is temp-then-rename, at `0600` for the secrets.** A truncated credential file is worse than an absent one: absent is a station that should register, truncated is a station that can neither register nor say why. The mode is enforced on POSIX and is best-effort on Windows, which honours only the read-only bit — a station client's real deployment is a Raspberry Pi sharing a filesystem with other service accounts, and that is the case the mode is for.

**Absent and corrupt are different, and are treated differently.** A missing credential file returns `None`, because a first boot is the normal path and not a failure. A file that exists and cannot be read *raises*, because registering again would consume a second invite and create a duplicate row for one physical installation. The same split applies to the assignment record: an empty record is a station that genuinely holds nothing, and an unreadable one is a station that may be holding a pass right now — treating the second as the first would silently decline work already accepted.

**The key and the token are separate files** because they have different lifetimes. A bearer token rotates; the registration key outlives every token it ever recovers (D-034). One combined file would rewrite the key on every rotation, which is the one thing rotation must not touch.

**The record's file format is the wire format.** An assignment is stored exactly as MSP §4.3 delivered it, so reading a record and reading a response are the same code path. A second representation would be a second parser, and the two would drift — with the drift showing up as a station that reboots and misreads its own window.

---

## D-069 — The loop's cadence, and why the execution seam exists before the radio

**2026-08-11 · accepted** · *`client/station_loop.py`, `client/execution.py`, Stage 8*

**A tick that overran is skipped, not queued.** The loop schedules from when a tick was *due*, so the cadence does not drift by the duration of the work; but when a tick runs past the next slot entirely — a long network stall — the missed slots are dropped rather than fired back to back. A heartbeat states the present (D-003), so a burst of them carries no information that the next single one would not, and fifty simulated stations catching up together produce exactly the traffic shape of the incident that caused the stall.

**Scheduled on a monotonic clock, never the wall clock.** A station is *expected* to have its clock corrected — measuring and reporting that correction is what §4.2's `clock_offset_s` is for — and a loop scheduled against a clock that can step backwards stalls, while one that steps forwards spins.

**The heartbeat gets a smaller retry budget than the transport's default.** Four attempts at a ten-second read timeout, plus backoff, can exceed forty seconds; against a thirty-second interval that is a request still in flight when the next is due. `retry_policy_attempts_for` derives the count from the interval instead. The reasoning is that **a heartbeat that cannot finish inside its own interval has already failed** — retrying it does not deliver it sooner than the next tick, and the next tick carries the same statement anyway.

**An unreachable platform is not a refused one.** A transport failure leaves the station executing and holding; only `401` stops the loop, and it stops rather than retries or re-registers (D-024). Work is started from the on-disk record and never from a response, which is what makes an outage during a pass change nothing the station does.

**`PassExecutor` ships now, with one implementation that receives nothing.** The receiver, decoder and rotator are Stage 13, and it would have been reasonable to leave the seam until then. Three reasons not to:

- Without something to start and stop, `assignments.state` never reaches `in_progress` and the whole D-067 path is untestable from the client side.
- `Registry.was_listening()` is the authority every reliability figure rests on, and it needs `listening` blocks to exist before there is a decoder to produce them.
- A station with no radio attached is a real configuration, not a gap: a simulated station (Stage 10), a client under test, a Pi being commissioned before its SDR arrives. All three hold assignments, report listening, and produce evidence.

*Named `PassExecutor`, not `Executor` or `Receiver`.* Stage 13 uses `Receiver` for the narrower thing that owns an SDR; an executor drives a receiver, a decoder and possibly a rotator, and the loop should depend on the whole job rather than one part of it.

**Neither `begin` nor `end` may block for the length of a pass.** The loop is single-threaded and must keep heartbeating throughout an 8-to-15-minute reception. An implementation that blocked would stop the station reporting exactly while it had something to report, and the platform would read that as the station going offline — turning a successful pass into a confirmed outage.

---

## D-070 — The canonical body is the record we store, and only the platform computes it

**2026-08-11 · accepted** · *`meridian/observations/canonical_body.py`, Stage 9*

D-015 makes a byte-identical resubmission write nothing by comparing `content_sha256` "over the canonical body", and `DATA-MODEL.md` repeats the phrase. Neither says what canonical means. The roadmap's Stage 9 brief lists the sub-questions — key ordering, timestamp format, numeric values, omitted fields versus `null`, array ordering — and answers none of them. There is no database behaviour to fall back on either: unlike `element_sets.content_sha256`, this column is `bytea not null` with no default and no generated expression, so something in Python has to decide.

**The hash is taken over the record the platform stores, after deriving `satellite_id` and `simulated` — not over the bytes the station sent.** Keys sorted; no whitespace; timestamps in the `…Z` millisecond form the API already emits; arrays in submitted order; `revision`, `submitted_at` and `observation_id` excluded, since they are assigned by the platform and including them would make every retry a change.

Two of those follow from something rather than being chosen. **Omitted and explicit `null` hash identically** because both reach the column as `None`, and canonicalising the stored record gets that for free instead of leaving it as a rule someone has to remember. **Array order is significant** because `doppler_samples` is a time series: two orderings are two different measurements, and sorting them would make a corrupted upload indistinguishable from the original.

*Rejected: hashing the received bytes.* Tempting, because it is one line and exactly matches the words "byte-identical". It fails on regenerability — `CLAUDE.md` rule 8 requires every number in a report to be reproducible from a dataset snapshot, and a snapshot of `observations` could not verify its own hashes without also storing every request body, which we do not. It also makes whitespace and key order significant, so a client that re-serialised the same facts on a retry would append a revision recording no change at all.

*Rejected: RFC 8785 (JSON Canonicalization Scheme) via a dependency.* JCS exists to make two different languages agree on one byte string. **Nothing here needs that agreement**: the hash appears in no message, no acknowledgement and no table a station can read, so the platform is the only thing that ever computes one. Paying for a dependency whose float rules are ECMAScript's `Number::toString` — which no reviewer here could check by eye — buys a property we have no use for. `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` is the whole implementation, and floats render through Python's `repr`, which is the shortest string that round-trips to the same IEEE-754 double.

---

## D-071 — A submission serialises on its assignment row, and a late one still lands

**2026-08-11 · accepted** · *`meridian/store/observations.py`, `meridian/observations/ingest.py`, Stage 9*

Two gaps, both found by tracing a queued observation back to the platform.

**Concurrency: the lock is the assignment row.** Two submissions for one assignment arriving together must not both append a revision, and the roadmap asks for "a row or advisory lock" to stop it. Nothing in this repository locks anything today — there is no `select … for update` and no advisory lock in any module. Ingest has to read the assignment regardless, to check ownership and to derive `satellite_id` through the join to `passes`, so **that read becomes `select … for update`** and costs one clause on a query already required. The assignment row is the right object: it is what the revisions are keyed to, it is a plain table rather than a hypertable, and locking it needs no isolation-level change for the rest of the system. This is D-020's reasoning applied a second time — a row lock is the serialisation point available without making every other transaction pay for it.

**A late observation is stored, and its assignment stays `expired`.** D-067 stopped an assignment expiring while the station still names it, but that only covers a station that is still talking. One that goes offline for a day comes back holding a queued observation for a row the platform has since expired — and D-008 has no arc from `expired` to `reported`.

Refusing it is the wrong answer twice over: it destroys a completed result, which is the one thing Stage 9's completion gate forbids, and it does so precisely for the station that had the worst outage, which is where the reliability data is most interesting. **So the observation is written and no state transition is attempted.**

**The mirror case is `issued`, and it does transition.** A station that took delivery, lost the network before its next heartbeat, executed the pass from its on-disk record and submitted afterwards was never seen to hold anything — D-008 draws no arc from `issued` to `reported` either, but here the tidy answer and the correct one agree. Leaving the row `issued` keeps it eligible for delivery under D-026, so the platform would keep offering a pass it has already been told about. The transition therefore runs from `issued`, `held` or `in_progress`, and only `expired` is left alone.

*Rejected: adding the `expired → reported` arc.* It would tidy the state machine and lose the evidence. `expired` records that the station stopped naming the work — the decline case D-003 defines — and an observation arriving afterwards does not undo that; it makes the pair *anomalous*, which is exactly what `meridian.reliability` must be able to see. CLAUDE.md rule 7 rests on being able to distinguish a station that was listening from one that was not, and overwriting the first half of that record to make a diagram symmetric is not a trade worth making. The combination is logged at warning, so the anomaly is visible rather than merely queryable.

---

## D-072 — Which observation bodies are refused, and which are merely stored

**2026-08-11 · accepted** · *`meridian/api/models/observation.py`, Stage 9*

MSP §4.4 defines the message; the roadmap says validation must check that "signal fields are consistent" and that "Doppler and products are bounded" without saying what either means.

**Refused as `malformed` by the Pydantic model, before any of it reaches a service:** `started_at` after `ended_at`; more than 512 `doppler_samples` (D-032); a non-finite float anywhere, since JSON has no `NaN` literal but a parser will hand one back for `1e400`; a `signal` block whose `detected` disagrees with the presence of `first_detection_at`; and an `outcome` that contradicts `detected` — `decoded` and `signal_no_decode` require a detection, `no_signal` and `not_attempted` require its absence, and `aborted` may be either because a station can abort at any point in a pass.

**D-013's window on `started_at` — `[now − 30 days, now + 1 hour]` — is checked at the route instead**, against the same `utc_now()` the response is stamped with. It is the one rule here that needs a clock, and a model that reads one cannot be tested at a fixed instant. Everything above is a statement about the body alone and stays where the body is parsed.

The detection rule is already a `CHECK` on the table. It is restated in the model because a constraint violation surfaces as a `500`, and telling a station its own body was malformed is both true and more useful than telling it the platform broke.

**`products` is bounded by the 256 KiB body cap and by nothing else.** MSP §4.4 says a product carries `kind`, `uri`, `sha256` "and whatever else the product type warrants", so any per-element schema we invented now would reject valid submissions from a station type we have not built yet. D-018 stores the array verbatim in `products_json` until the `products` table lands at Stage 19, and D-029 settles that the artefacts themselves never travel this path — so the array is metadata whose size is already constrained by the request.

---

## D-073 — Finished work crosses the execution seam by drain, and the queue is the first durable point

**2026-08-11 · accepted** · *`client/execution.py`, `client/observation_queue.py`, Stage 9*

`PassExecutor` is `begin()` and `end()`, and nothing crosses it — D-069 shipped the seam before there was anything to hand over. An observation now has to get from whatever received the pass to the upload queue.

**It gains a third call, `take_completed()`, returning whatever became ready since the last one.** The loop drains it each tick and writes what it gets to the queue.

*Rejected: `end()` returning the observation.* Fewer calls, impossible to forget, and wrong at the next stage. Stage 13's pipeline is capture, stop, **run the decoder subprocess**, extract metrics, build the observation — the decode happens after `end()` and takes real time, and D-069 already forbids either call blocking the single-threaded loop. A synchronous return forces the decode inside `end()` and gets redesigned the moment a real decoder exists. A drain costs the same one method and lets a slow executor answer three ticks later.

*Rejected: giving the executor the queue.* It hides the control flow inside an implementation the loop cannot see, makes every executor responsible for durability, and stops a loop test asserting what was produced.

**The queue file is the first durable point, and the window before it is a stated limit.** An observation exists in memory from the moment the decoder finishes until `enqueue` returns; a crash in that window loses it. Nothing closes that window except the producer writing to disk itself, which is the option rejected above. At Stage 13 the decoder's own output files survive the crash and make the observation reconstructible; until then the exposure is a fake executor's in-memory value. This is written down rather than left implicit, because the alternative is a system that reads as crash-safe end to end and is not.

**An unreadable queue entry is set aside, not raised — the opposite of D-068.** That entry says a file that exists and cannot be read *raises*, and this one says it is moved to `failed/`, logged at error, and stepped over. The difference is what the file is. An unreadable credential or assignment record may describe a pass the station is supposed to be receiving right now, so continuing would mean acting on a state it cannot see. An unreadable queue entry is an observation that is already lost, and refusing to go on would strand every *other* queued observation behind it — turning one damaged file into a station that permanently reports nothing. Setting it aside is not silent: the file is still on disk, in a directory whose name says what happened.

**Submission failures do not get a retry mechanism of their own.** The transport already retries `429` and the `5xx` family and then raises, so a failed upload simply stays queued and goes again on the next tick — which is also what an outage looks like, so there is one path rather than two. `malformed`, `not_owner` and `unknown_assignment` are permanent: retrying them changes nothing, so the payload moves to a `failed/` directory where an operator can find it, rather than looping forever against a platform that has already refused it. `unauthorized` stops the loop, as it does everywhere else (D-024).

---

## D-074 — One undeliverable observation must not stop a station reporting

**2026-08-12 · accepted** · *`client/observation_submission.py`, `client/observation_queue.py`, Stage 9*

D-073 said the drain stops at the first entry the platform did not take, and that submission failures need no retry mechanism of their own because a failed upload simply waits for the next tick. Reviewing the finished code against its own reasoning found the hole: an entry that fails the *same way every time* is never given up on and never stepped over, so the queue never moves past it.

The harm is not the lost observation. It is that the station goes on heartbeating and goes on reporting `listening`, so the platform sees a healthy station that was listening and sent nothing — which is the definition of a missed pass under `CLAUDE.md` rule 7. **A stuck queue manufactures false misses in the one number this project exists to publish**, and nothing anywhere detects it.

**The drain now stops only when the platform could not be reached, and steps over an entry when the platform answered.** A transport failure says nothing about the entry and everything about the network, so the tick ends there as before. An answer — an MSP error, or an acknowledgement the station cannot parse — is evidence about that one exchange, and the next entry may well be fine.

D-073's reason for stopping was that a backlog should not be hammered through in one tick. That reason survives, because it never applied to the case being changed: **a station's queue holds one entry per pass**, six to fifteen a day rather than thousands, and during a real outage the first entry fails with a transport error and the drain still stops at one request. The burst it guarded against cannot be reached from here.

**An entry whose `started_at` has fallen outside the platform's thirty-day acceptance window is moved to `failed/` instead of being sent.** This needs no attempt counter and no new state, because the queue already carries the fact: D-013's window is what the platform checks, so an entry older than it will be refused as `malformed` on every future tick for the rest of the station's life. It is quarantined for the same reason the three permanent MSP codes are — it is a payload that can never be delivered — and the threshold is not a new number, it is the platform's own, mirrored the way `MAX_DOPPLER_SAMPLES` already is.

*Rejected: counting attempts and quarantining at a threshold.* The obvious answer, and it fails on both storage and calibration. Storing the count in the entry breaks D-068's property that the queued file is exactly the body that will be sent; storing it beside the entry adds a second piece of state that can disagree with the directory; holding it in memory resets on the restart an operator reaches for first. And any threshold small enough to act promptly is one a ten-minute platform incident burns through, quarantining every queued observation on the network at once — turning a brief outage into permanent data loss across fifty stations. Stepping over the entry gets the same outcome without needing a number nobody can calibrate.

*Rejected: distinguishing a body-specific `500` from an overloaded platform.* There is no signal that separates them — a `500` caused by one payload and a `500` caused by load are the same response. Treating both as "the platform answered, try the next" is correct for each: if the platform is genuinely struggling, the next entry fails too and the tick costs a handful of requests rather than one.

---

## D-075 — A virtual station's identity comes from its index, not its station id

**2026-08-12 · accepted** · *`simulator/config.py`, Stage 10*

The roadmap gives the seed derivation as `station_seed = deterministic_hash(master_seed, station_id)`. It cannot work. `station_id` is minted by the platform during registration, and the seed is needed *before* that — the station's location, altitude and declared capabilities are all generated from it, and all three are in the registration body.

**The derivation is from a one-based station index:** `station_seed = sha256(f"{master_seed}:{index}")`.

This also makes the roadmap's own requirement true by construction rather than by care. "Increasing the station count must not alter station 1's behaviour" holds because station 1's seed never mentioned the count, or any other station, or anything the platform assigned.

*Rejected: seeding from `station_id` once registration has returned.* It inverts the dependency — a station would have to register before it could decide what to register as — and it would make a station's identity depend on the order the platform happened to admit it in, so a re-run against a fresh database could produce different stations from the same seed.

---

## D-076 — N stations share one thread, and the supervisor ticks each in turn

**2026-08-12 · accepted** · *`simulator/supervisor.py`, Stage 10*

`StationLoop.run()` blocks on `_sleep` between ticks, and D-069 states that nothing may block the loop. Running several stations in one process therefore needs something other than N calls to `run()`.

**The supervisor never calls `run()`.** It holds N loops, calls `tick(now)` on each in turn, and sleeps once per round, scheduling on `time.monotonic` under `_next_due_at`'s existing skip-don't-queue rule. A station whose tick returns a `stop_reason` is retired from the round; the others continue, and the supervisor exits when every station has stopped.

*Rejected: a thread, or an asyncio task, per station.* Fifty threads to make fifty HTTP calls of a few milliseconds each buys nothing at this scale, and it costs the property the stage exists to establish: with concurrency, a determinism failure depends on scheduling order and stops being reproducible. One thread also means a test can drive N stations through an exact number of ticks with no synchronisation, no timeouts and no flakiness.

Scale is Stage 21's, and this shape is what it will measure. If fifty stations on one thread turn out not to keep up, that is a finding Stage 21 is built to produce rather than a problem to pre-solve here.

---

## D-077 — What "deterministic" is allowed to claim, and the two tests that prove it

**2026-08-12 · accepted** · *`simulator/outcomes.py`, `simulator/executor.py`, Stage 10*

The roadmap asks that "the same seed produces identical canonical observations". Read literally it is unachievable, and it is worth saying why rather than quietly testing something weaker. An observation carries `started_at` and `ended_at`; those come from an assignment window, which comes from a real pass computed over a real element set at a real wall-clock moment. Two runs a day apart cannot produce the same bytes, whatever the seed says.

**Two assertions are made, and the difference between them is recorded.**

1. **Frozen inputs, byte-identical bodies.** With the element sets and the clock pinned, two runs at one seed produce byte-identical observation bodies, checked by a single hash. This needs no platform: the test hands the executor its assignments directly and compares `build_observation_body` output.
2. **The decision sequence, against a live platform.** Where the clock cannot be pinned, the hash covers what the simulator *chose* — outcome, detection offset, peak SNR, Doppler series — per assignment, excluding the instants it did not choose.

The first is the real claim. The second is the cheap check that can run end to end, and it proves less: it would not catch a station making the right decisions about the wrong passes. Both are stated so that a reader of the test suite knows which one they are looking at.

---

## D-078 — Simulated observations are excluded from training and evaluation sets

**2026-08-12 · accepted** · *`simulator/outcomes.py`, `docs/EVALUATION.md`, Stage 10*

`EVALUATION.md` §10 already forbids aggregating simulated results with measured ones in any reported figure. **That rule is not sufficient once this stage exists**, and the gap is created by a choice made here.

The simulator draws outcomes from elevation, because traffic uncorrelated with geometry would be a weak test of the scheduler and of the reliability layer — the two things it exists to exercise. Elevation is also the prediction model's strongest single feature. A model trained or evaluated on simulated observations would therefore be rediscovering the simulator's own generative rule, and would score well for a reason that means nothing.

The dangerous part is that it would happen *invisibly*: no reported figure would mix simulated and measured data, so the existing rule would be satisfied throughout, and the number that came out would look like a result.

**Simulated observations are excluded from every model training set and every evaluation set, not merely from reported aggregates.** Stages 15 and 17 enforce it where the datasets are built; the decision is recorded now, in the same stage as the code that creates the hazard, because a methodological threat discovered later is one that has already contaminated something.

*Rejected: making outcomes arbitrary so the hazard cannot arise.* Drawing outcomes from the seed alone, uncorrelated with geometry, closes the hole completely. It also produces a network whose passes succeed and fail at random, against which a scheduler that ranks by elevation performs exactly as well as one that ranks by coin flip — so the simulator would stop being able to test the thing it was built to test.

---

## D-079 — The local catalogue is loaded from a file, and the simulator never writes it

**2026-08-12 · accepted** · *`meridian/cli_catalogue.py`, `meridian/store/satellites.py`, Stage 10*

Stage 10's completion gate needs an assignment to exist, and the chain behind one is satellites and transmitters, then element sets, then `passes generate`, then `schedule`. The last two are built. **Nothing can supply the first three**: there is no catalogue command, no seed migration, and `store/satellites.py` is read-only. `insert_element_set` exists and is called by nothing outside the tests. Stage 14 is archive *ingest*, which is a network path and deliberately later.

**`meridian catalogue load --file <path>` inserts satellites, transmitters and element sets from one local JSON document**, idempotently — loading twice writes nothing the second time, matching `insert_element_set`'s contract and D-063's rule for pass generation.

One command rather than three, because the three are one fact: this is the catalogue this deployment can see. A local file rather than a fetch, because the independence test says the standalone path is built first and external archives are enrichment on top of it — and because a gate that depends on a third party being reachable is a gate that fails for reasons that have nothing to do with our software.

**The simulator does not call it.** `element_sets.source` admits `'simulator'` and that value stays unused: a simulator that wrote its own orbits would be testing the platform against passes that do not exist. Geometry is real and only outcomes are synthetic, which is the property that makes the simulator evidence rather than decoration.

---

## D-080 — Where a virtual station's state lives, and what a restart keeps

**2026-08-12 · accepted** · *`simulator/virtual_station.py`, Stage 10*

Each virtual station owns `<state-dir>/<index>/`, holding `credentials.json`, `registration.key`, `held.json` and `outbox/` — the same four things a real station keeps, in the same formats, because it is the same client code writing them.

**Keyed on the station index, not on the run id.** The roadmap requires that a restart preserve credentials and queues, and a path containing a per-run identifier would defeat that on the first restart: every station would find an empty directory, register again, and consume a fresh invite. A run id that changes per run cannot name state that must outlive the run.

The consequence worth stating: two runs with *different master seeds* against one state directory will find credentials belonging to stations generated from the other seed. That is a misuse rather than a case to handle — a new seed is a new network — and the run configuration records the seed alongside the state so the mismatch is visible rather than silent.

---

## D-081 — The dashboard is TypeScript, Vite, React and Leaflet, served by the platform

**2026-08-13 · accepted** · *`dashboard/`, `meridian/api/app.py`, Stage 11*

D-036 split the public site from the dashboard and deliberately left the dashboard's stack open, because choosing a framework for a page with one heading would have prejudged it. Stage 11 is the stage that has to choose.

**TypeScript, Vite, React and Leaflet, in `dashboard/`, built into the image and served by FastAPI at the same origin as the API.**

Same origin is the load-bearing half. `dash.meridian.org.in` already resolves to the tunnel, so the built assets and `/api/v1` arrive from one host — which means **no CORS middleware exists anywhere in this repository**, no preflight, no second hostname, and no second deploy target. A cross-origin dashboard would have bought nothing and cost a security control that has to be right.

*Rejected: FastAPI templates plus HTMX.* Genuinely cheaper, and the honest argument for it is real — no Node, no build step. It loses on the screen that matters: `PROJECT.md` §13 describes a map cross-filtered against a pass queue and a scheduling-reason panel, and under HTMX every interaction is a round trip to a Raspberry Pi behind a tunnel. The map is Leaflet either way, so the "no JavaScript build" saving was never as large as it looked.

*Rejected: Cloudflare Pages for the dashboard.* It contradicts D-036, which places `dash.` on the tunnel precisely because the dashboard is a view onto the observation store and should be down when the platform is down.

*Rejected: Mapbox or Google Maps.* An API key is a runtime dependency on an external service, on the one surface SC-6 is stated against. The independence test applies to the map tiles too: a tile failure degrades to a graticule with the markers still drawn.

`CLAUDE.local.md`'s size, naming and complexity limits apply to TypeScript as much as to Python. `ruff` and `mypy` cannot reach it, so eslint carries `max-lines`, `max-lines-per-function` and `complexity` set to the same numbers — otherwise the standards stop at the language boundary, which is where a second codebase quietly grows different rules.

---

## D-082 — A station declares how precisely its location may be published

**2026-08-13 · accepted** · *`docs/MSP-SPEC.md` §4.1, `meridian/api/public/coordinate_privacy.py`, Stage 11*

The roadmap's do-not-expose list ends with "unrestricted raw station coordinates **if privacy policy later requires approximation**" — a conditional nobody had resolved, and Stage 11 is the stage that publishes coordinates. Registration stores what the operator typed, to six decimal places if they typed six.

**`location_precision_decimals`, an optional integer from 1 to 6, top-level in the `register` body, defaulting to 2.** It governs **publication only**: the platform stores the full precision the station sent and schedules against it, always.

Top-level rather than inside `location`, because `location` is ISO 6709 and should stay purely a coordinate; this is a property of the station, which is the same argument §4.1 already makes for `simulated`.

Decimal places rather than a distance in metres. Rounding is then one operation with no geodesy in it — a metre figure needs a `cos(latitude)` term and has to say what it means at the poles. At the equator: 1 ≈ 11 km, 2 ≈ 1.1 km, 3 ≈ 110 m, 4 ≈ 11 m, 5 ≈ 1.1 m, 6 ≈ 11 cm. Longitude's true ground distance *shrinks* with `cos(latitude)`, so a declared figure is an upper bound on what is disclosed and never a lower one, which is the safe direction for a privacy control.

**There is no `exact` value, only a finest one**, and that is the point rather than a simplification. An `exact` sentinel requires a branch — publish raw, or round — and the branch is a live code path that emits full-precision coordinates. With a bounded integer every publication goes through `round(value_deg, decimals)` and full-precision disclosure stops being a state the code can reach. Six decimal places is finer than any fix an operator types by hand, so nothing is lost.

**The default is 2, and the migration's column default is the backfill** — one statement, applied by Postgres to every station registered before the field existed. The default lands on operators who never read the specification and therefore consented to nothing, so it sits at the conservative end.

Altitude is always published to the nearest metre, independent of the field: sub-metre altitude is instrument noise, not an address, and coupling it would be one more thing to reason about.

**The rounding happens at serialisation, in the public response model, and nowhere else.** Not in the store, because `pass_generation`, `orbit` and the scheduler read the same rows through `find_receiving_stations` and must see full precision — a rounded latitude shifts every predicted acquisition. Not in SQL, because one query cannot serve both readers and a `round()` in a `select` is a second place the rule lives. The boundary is enforced mechanically: `meridian.api` becomes a banned import outside `platform/src/meridian/api/**`, which also closes the general rule `ARCHITECTURE.md` already claims.

**MSP goes to 0.2.** An optional field added to a request is additive, and §7 says additive changes are a minor bump. This is the first change since the 0.1 freeze to add a field rather than clarify one, so it is §7's first real exercise: a 0.1 station omits the field, receives the default, and needs no change, because the platform already accepts an unrecognised minor within a supported major. The document version moves with the text so that "0.1" continues to name exactly one document.

*Rejected: a platform-wide rounding policy.* An operator on a university rooftop and one at a home address have different exposure, and only they know which they are.

*Rejected: `"exact"` / `"approximate"`.* Two values, which reads simply and fails twice. The name carries no unit, which `CLAUDE.local.md` §4 does not permit — the field never states how approximate it is. And two postures do not deliver the choice this decision claims to give: an operator wanting 11 km cannot have it, and one happy at 110 m must pick between 1.1 km and their exact roof. The first operator who wants a third value forces another spec change.

---

## D-083 — The public read API is `/api/v1`, and makes no compatibility promise

**2026-08-13 · accepted** · *`meridian/api/public/`, Stage 11*

MSP is `/msp/v0` with a required `MSP-Version` header because it is a published protocol that strangers implement, and §7 makes them an explicit deprecation promise. The read API is a different kind of thing and should not borrow the shape of that promise by accident.

**`/api/v1`, versioned in the path, with no version header and no compatibility guarantee beyond "the dashboard at this commit works against it."** A breaking change becomes `/api/v2`, served beside `v1` for as long as anything third-party is known to use it — which is currently nothing.

Not `/public/v0`: "public" describes who may call it, not what it is, and a `v0` alongside `/msp/v0` invites a reader to assume MSP's promise applies here too.

No version header, for a reason specific to this deployment: the dashboard is shipped *by* the platform, from the same image, so a header would encode a version skew that same-origin deployment makes impossible. It would also break `curl https://dash.meridian.org.in/api/v1/stations`, which is a thing an examiner does, and the version stays in the path so it survives being pasted into a chat window.

---

## D-084 — The public surface has its own error vocabulary, and MSP §6 stays closed

**2026-08-13 · accepted** · *`meridian/api/public/envelope.py`, `meridian/api/errors.py`, Stage 11*

`install_error_handlers` is registered on the application, not on the MSP router, so it already governs every path the platform serves. The public API therefore inherits MSP §6's handlers whether or not that was intended — and MSP §6 has no code for "no such station".

**A second code table, in its own module, reusing the same body shape.** `{"error": ..., "message": ...}` stays exactly as it is — two flat strings, one error shape in the repository — while the *vocabulary* differs: `not_found`, `invalid_query`, `server_error`, each with its own status mapping and its own `PublicError` that refuses a code absent from the table.

**MSP §6's eight codes are not widened.** That table's docstring says a code absent from it cannot be sent, which is what stops an endpoint inventing a ninth; adding `not_found` would make the table stop being "every code MSP §6 defines", and an implementer reading the reference implementation would find a code the published specification does not list. `tests/msp_conformance` asserts the envelope's exact shape, and it should keep passing for the reason it was written rather than because it was edited.

The two shared handlers — `RequestValidationError` and the catch-all `Exception` — each gain one guard clause on which surface the path belongs to. `is_public_surface` compares against the prefix exactly or with a trailing slash, never a bare `startswith`, so `/api/v10/...` is not mistaken for a public path.

*Rejected: mounting the public API as a sub-application.* It would give each surface its own handler stack for free, which is the right shape. Inside a `Mount`, `request.app` is the sub-application, and `get_connection` and `get_settings` read `request.app.state.pool` — the pool would not be there. A structural win that breaks the dependency every route needs is not a win.

---

## D-085 — Public list endpoints paginate by keyset, never by offset

**2026-08-13 · accepted** · *`meridian/api/public/pagination.py`, Stage 11*

Nothing in the platform paginates yet. `MAX_ASSIGNMENTS_PER_RESPONSE` looks like a page size and its own docstring says it is not one — D-035 caps eligible assignments rather than paging them, because held work is redelivered and the earliest eight of nine would be the same eight forever.

**Every public list endpoint takes `limit` and an opaque `cursor`, and the cursor encodes the last row's sort key.** Default 50, maximum 200. The route asks the store for `limit + 1` rows and trims, so "is there another page" is computed rather than guessed, and the trimming is a pure function rather than a `limit + 1` buried in SQL.

Keyset rather than offset, for a reason specific to this data. `observations` and `heartbeats` are hypertables with compression policies; `offset 10000` reads and discards ten thousand rows, decompressing chunks to do it, on a Pi's NVMe. Worse, offset pagination **duplicates and skips rows on a live feed** — a dashboard paging observations while a station is reporting would show one twice and hide another. A project whose stated rule is that every published number is regenerable cannot ship a pagination scheme returning a different set depending on when the page was turned.

The prerequisite already holds: every store read carries a total, stable `order by` — `station_id asc`, `aos asc, id asc` — because those orderings were chosen for reproducibility. Keyset needs exactly that, so the cost is a tiebreaker column where one is missing, not a redesign.

`limit` defaults to 50 because that is the fleet size the project demonstrates, so the station map fits one page at the scale it is shown at. 200 caps what one query costs the Pi.

---

## D-086 — An endpoint with no computation behind it says so, and publishes no numbers

**2026-08-13 · accepted** · *`meridian/api/public/reliability.py`, `meridian/api/public/aggregates.py`, Stage 11*

Stage 11 fixes the public URL surface, but `meridian.reliability` and `meridian.prediction` are docstring-only stubs and nothing registers a metric anywhere. Two of the roadmap's endpoint groups have nothing to call.

**They answer `200` with `{"status": "not_yet_computed", "reason": ..., "available_from_stage": N}` and no numeric field of any kind.** Safety comes from *absence*, not from a sentinel: a chart reaching for a value gets nothing back and draws nothing. `status` is a `Literal`, so mypy pins it and it appears in the schema as a discriminator; the eventual real response carries `"status": "computed"` beside its data, and the dashboard's single branch survives the transition.

`simulated` is deliberately **not** on this body. There is no data, so there is no provenance to state — which is what "every *applicable* response carries `simulated`" means, said out loud so the omission reads as deliberate rather than forgotten.

*Rejected: `{"value": null}` or `{"value": 0, "computed": false}`.* Both leave a number-shaped hole exactly where a chart will read, and a zero that means "not measured" is the precise failure this project exists to avoid.

*Rejected: `501`.* Unambiguous at the HTTP layer, and wrong in practice: it routes the *normal* case through the dashboard's error path, so the branch that should mean "the platform is broken" fires on every page load, and every load logs a failure at the edge. A status that fires constantly stops being read.

*Rejected: omitting the endpoints.* The URL surface would then move when the computation lands, which is the thing fixing it now is meant to prevent.

---

## D-087 — `/metrics` requires a bearer token and answers 404 without one

**2026-08-13 · accepted** · *`meridian/api/metrics_access.py`, `deploy/prometheus/prometheus.yml`, Stage 11*

`/metrics` has been unauthenticated since it was added, which was correct while the platform was only reachable on a laptop. Stage 11 puts the platform behind a tunnel on a public hostname, and the endpoint goes with it.

**A bearer token compared with `secrets.compare_digest`, and a `404` byte-identical to any unmatched path when it is absent or wrong.** `METRICS_TOKEN` joins `TOKEN_HASH_PEPPER` in the public-mode placeholder refusal, so a public deployment cannot start with `change-me` on it. Prometheus authenticates with a credentials file inside the compose network.

404 rather than 401: a 401 confirms the endpoint exists and invites a second guess. The rejection is logged at warning with its reason, so a misconfigured scrape is diagnosable from the platform's log rather than from the response.

*Rejected: a peer-address allowlist.* This is D-051's own argument inverted — a tunnelled request presents the tunnel container's address, which is inside the compose network, so no address rule can separate the tunnel from Prometheus.

*Rejected: a second port.* Structurally the cleanest answer, and premature: the endpoint currently emits only default process collectors, so a separate process would report the wrong process, and doing it properly needs the multiprocess registry that arrives with Stage 12's domain metrics.

*Rejected: excluding the path at the tunnel and nothing else.* Unverifiable from the repository, which is exactly how D-041's three recorded remedies were still unapplied when D-042 checked them a day later.

---

## D-088 — Rate limiting is applied at the Cloudflare edge, and verified by a script

**2026-08-13 · accepted** · *`deploy/tools/verify_public_surface.py`, Stage 11* · *Resolves D-051*

D-051 deferred rate limiting and named its own revisit trigger: "the first time the platform is reachable from outside the college network for longer than a demonstration." Stage 11 is that moment.

**A rate-limit rule on the tunnel hostname, in Cloudflare, and nothing in the process.** Every argument D-051 made against an in-process limiter still holds and now serves as the justification rather than the deferral: the client address is the tunnel's, `CF-Connecting-IP` is forgeable on the port compose also publishes, and the edge is the only place that sees the real caller.

**The part that matters is the verification, not the rule.** A control living outside the repository is a control nobody can prove is on, and this project has already run that experiment: D-041 recorded three Cloudflare remedies and D-042 found all three unapplied. So the rule ships with `deploy/tools/verify_public_surface.py` — stdlib only, like `site/tools/verify_site.py` — which asserts from outside that `/metrics` is 404, that a station listing carries `simulated` on every item and no key matching `token|invite|health|seed|registration_key`, and that a burst of requests eventually returns `429`. That last assertion is the executable proof the rule exists. Its transcript is pasted into this entry when the rule is applied, exactly as D-042 pasted `curl` output.

It cannot run in CI — a fork PR has no public hostname — so it is an operator tool, run against a deployment, and re-running it is one command.

**Rehearsal, 2026-09-14 — against `localhost`, not the public hostname.** A cold `docker compose --profile sim up --build` from the Stage 11 branch, then the verifier. It proves the checks and the platform agree; it is **not** the transcript this entry asks for, which must come from outside the college network with the edge rule on and `--burst` given.

```
verifying http://localhost:8000

metrics refused    PASS  refused, and indistinguishable from a path that is not there
dashboard served   PASS  the dashboard page is served at /
virtual station    PASS  st_b10dee is listed, simulated and online
lists labelled     PASS  stations 1; passes 2; assignments 2; observations 0; simulator-runs 1; satellites 2
rate limited       SKIP  not attempted; rerun with --burst N once the edge rule is on

every check that can run passed
```

**Public run, 2026-09-14 — the transcript this entry asks for.** Run against `https://dash.meridian.org.in`, from a mobile network outside the college, with the stack on a laptop behind the tunnel.

The edge rule, as applied. The free plan allows one rule, matched on path only, counted per IP, over 10 seconds:

| Setting | Value |
|---|---|
| Match | URI Path starts with `/api/` |
| Count by | IP |
| Limit | 50 requests per 10 seconds |
| Action | Block for 10 seconds, which answers 429 |

The site at the apex has no `/api/` paths, so in effect the rule covers only the dashboard.

```
verifying https://dash.meridian.org.in

metrics refused    PASS  refused, and indistinguishable from a path that is not there
dashboard served   PASS  the dashboard page is served at /
virtual station    PASS  st_cd2a4e is listed, simulated and online
lists labelled     PASS  stations 1; passes 2; assignments 2; observations 0; simulator-runs 1; satellites 2
rate limited       PASS  96 of 150 request(s) refused with 429

every check that can run passed
```

Two things this run found:
- **The burst had to be concurrent.** Sent one at a time, it never exceeded the free plan's window, and a working rule read as missing. The verifier now sends it from 25 workers.
- **The platform had a commit-ordering bug.** A newly registered station's first heartbeat was refused with 401: FastAPI returned the request's connection, which is what commits, only after the response had been sent. Every route now declares its connection with `scope="function"`, and `tests/msp_conformance/test_commit_before_response.py` pins it.

---

## D-089 — The README banner is a generated SVG pair, and the type is outlines

**2026-08-13 · accepted** · *`site/tools/{banner_svg,starfield,wordmark_outlines,orthographic_projection}.py`, `site/brand/meridian-banner-*.svg`, `README.md`*

The repository is the first thing an outside contributor sees, and it opened with a heading. The site has had an identity since D-036; the README had none of it.

**One drawing, generated into two themes, selected by `<picture>` and `prefers-color-scheme`** — the mark and wordmark over a seeded star field, with Earth rising into the top-right corner. 1500×500, so the same file serves the README, the social preview and a profile header rather than three variants that drift.

**The text ships as outlines, not as a `font-family`.** A README image is rendered in a context that blocks external fonts, stylesheets and scripts. `font-family="IBM Plex Sans"` would render in IBM Plex on the three machines that have it installed and in something else everywhere else, at a different width — which moves everything positioned against it, and the lockup is measured rather than eyeballed. `wordmark_outlines.py` reads the `.woff2` files already in `site/fonts`, so there is still exactly one copy of the typeface in the repository.

**Motion is CSS, not SMIL.** Both survive being embedded as an image; only CSS can be switched off by `prefers-reduced-motion`, because an `<img>` runs no script and there is nothing else to gate it with. Everything moves slowly and the satellite rests mid-sky rather than at the origin when motion is declined.

**The projection is the one already in the repository.** `orthographic_projection.py` is lifted out of `make-images.py` and now serves the social card, the still globe inside `index.html` and the banner. The extraction was verified by regenerating both existing outputs and confirming they were byte-identical.

`make-images.py` goes from 575 lines to 509 with it. That is still over the 400-line module limit, which D-044 deliberately does not enforce on `site/tools/` — but the limit exists because a long module does not get read, and that applies to a build script as much as to anything else. The remainder is the social card's own drawing code, and extracting it is a separate piece of work. Recorded here rather than left to be found later. The five modules added by this entry are all inside the limit.

**The nebula is almost colourless on purpose.** `site/brand/README.md` reserves `--signal`, `--alert` and `--trace` as semantic — *above horizon*, *below horizon*, *predicted* — and forbids them in a logo treatment. The wash is near-black indigo and violet, which reads as depth rather than as a fourth brand colour.

*Rejected: a raster banner.* The lockup PNG already exists and could have been cropped. It cannot carry two themes without two exports that then drift, it is soft at any width but the one it was rendered at, and at 1500×500 it is larger than both SVGs together.

*Rejected: hand-writing the SVG.* Faster, and it would have been the only brand asset that could not be regenerated — which `site/brand/README.md` forbids for the PNGs for exactly the reason that would apply here.

*Rejected: a system font stack as a fallback.* There is no fallback worth having: the drawing is centred on measured widths, so a substituted face does not degrade it, it breaks it.

**`README.md`'s opening sentence changed with it.** It described Meridian as "a control platform for satellite ground stations", which is the thing `CLAUDE.md` opens by saying this project is not. The banner's subline and that sentence now say the same thing.

**Amended 2026-08-13.** Two parts of the entry above no longer describe what ships.

The satellite is gone. It crossed the banner in a straight line and read as a stray mark on the image rather than as something in the sky; nine meteors on near-parallel diagonal tracks replace it. Nothing on the banner is a satellite now, which is a real loss for a project about receiving them and was still the right call — a thing that has to be explained before it reads correctly is not working.

**The module list in this entry's header is the one it was written with, and three modules have joined since.** `meteor_tracks.py` draws the shower that replaced the satellite, and the lockup and its motion moved out of `banner_svg.py` into `banner_lockup.py` and `banner_motion.py` as that file approached the 400-line limit. `starfield.py`, `wordmark_outlines.py` and `orthographic_projection.py` are unchanged and still named above. Recorded here rather than by editing the header, because an entry that is silently rewritten stops being a record of what was decided when.

**"The nebula is almost colourless on purpose" is now true of the dark theme only.** The light theme is no longer the dark one with the sky removed: it carries the same star field and the same shower in near-black on warm paper, over a wash of cornflower, dusty rose and lilac. That is the first colour in the brand that is neither ink nor semantic, and it is worth being explicit that it was added deliberately rather than by drift. It stays clear of `--signal`, `--alert` and `--trace`, which still mean *above horizon*, *below horizon* and *predicted* and are still unavailable as decoration. The globe's graticule and the subline darkened at the same time: the paper palette in `site/style.css` was drawn for hairlines on a flat ground, and a coloured wash underneath eats contrast that a flat ground does not.

---

## D-090 — An unknown URL is answered in the two-field envelope, on every surface

**2026-08-13 · accepted** · *`meridian/api/errors.py`, `meridian/api/public/envelope.py`, Stage 11*

Every failure the platform raises deliberately leaves in `{"error": ..., "message": ...}`, and D-004 records why: a microcontroller client extracts both fields with a substring scan and never needs a JSON tree walker. **A request to a path no route matches leaves in `{"detail": "Not Found"}` instead** — FastAPI's built-in handler, which nothing in this repository had replaced. The same is true of a wrong method, which answers `{"detail": "Method Not Allowed"}`. Neither is either surface's shape, and the module docstring in `api/errors.py` claiming to own "the one shape every error response takes" was simply wrong about the two cases a client author hits most while their code is still wrong.

**Both are answered in the two-field envelope, with `not_found` (404) and `method_not_allowed` (405), on every path the platform serves.**

**MSP §6 is not widened to provide the code, because §6 does not govern this case.** Its eight codes each describe an MSP *operation* failing — a bad invite, an unowned assignment, an unparseable body. A request to `/msp/v0/registr` never became an operation: it never matched a route, no version header was checked, no body was read. Answering it is a transport-level act that happens before the protocol starts, so the closed table D-084 defended stays closed and an implementer reading the reference implementation still finds exactly the eight codes the specification lists.

That argument is what makes one vocabulary acceptable across both surfaces. A station receiving `not_found` is not receiving an MSP §6 code it cannot look up; it is receiving the platform's statement that the URL it asked for is not part of any API here.

**The two codes live in `STATUS_FOR_PUBLIC_CODE` rather than in a third table, and this is the weakest part of the decision.** That table is documented as the public read API's vocabulary, and it is now also the platform's transport vocabulary — one table serving two ideas. A third table would be conceptually cleaner and would mean three code tables in a project with two surfaces, which is worse in every way a reader would experience. Recorded as a tension rather than resolved, so whoever adds a fourth code knows which of the two ideas they are extending.

**It also settles what D-087 has to match.** That decision requires `/metrics` without a bearer token to return a 404 "byte-identical to an unmatched path", which was unimplementable while an unmatched path returned whatever FastAPI happened to produce. It is now a fixed, tested shape.

*Rejected: fixing only `/api/v1` and leaving MSP paths as they are.* The smallest change, and it makes the two surfaces disagree about their most common error. D-004's promise is to the microcontroller client, which is on the MSP side — fixing the surface that promise was not made about, and not the one it was, inverts the reason the promise exists.

*Rejected: adding `not_found` to MSP §6 as version 0.3.* Explicit, and it spends a protocol version and a conformance-test revision on an error no client branches on, three days after D-084 argued the table should not grow. If §6 gains a ninth code it should be for an operation that can fail in a new way.

*Rejected: accepting `{"detail": ...}` as the platform's 404 and pointing D-087 at it.* Costs nothing and keeps a third body shape in a repository whose error handling is otherwise uniform, permanently, so that one FastAPI default never has to be overridden.

---

## D-091 — The dashboard is served at `/` and `/assets/` only, with no catch-all

**2026-09-13 · accepted** · *`meridian/api/dashboard.py`, `deploy/Dockerfile`, Stage 11*

D-081 puts the built dashboard at the same origin as the API. The usual way to do that is a static mount at `/` with a single-page-app fallback: any path no route claimed gets `index.html`, and the browser's router takes it from there. **That fallback contradicts D-090, and it does so in two places.**

The obvious one: `/api/v1/statoins` would answer `200` with an HTML page instead of `not_found`, so a client author's typo stops being an error. The subtle one is Starlette's matching order. A route whose path matches but whose method does not is only a *partial* match, and the router keeps looking — so a mount at `/`, which fully matches every path, wins. `GET /msp/v0/register` would stop answering `405 method_not_allowed` and start answering whatever the static mount says. Mounting it *after* the routers, as D-081 anticipated, does not help, because order only decides between two full matches.

**So the dashboard gets exactly two routes: `GET /` serves `index.html`, and `/assets/` serves Vite's hashed build output. Every other path is unrouted, and answers as D-090 says.** A missing asset raises Starlette's own 404, which the routing handler already turns into the envelope. When the dashboard adds views, they are addressed by URL fragment (`/#/stations/st_…`), which never reaches the server.

The build lives in the image at the path `DASHBOARD_DIR` names. It is read when the application is constructed rather than through `Settings`, because routes have to exist before the lifespan that loads settings runs. When the variable is unset, or the directory holds no `index.html`, no dashboard route is added — a developer running `uvicorn` from a checkout without Node gets the API alone, not a startup failure.

*Rejected: a fallback that excludes `/api/`, `/msp/`, `/healthz` and `/metrics` by prefix.* It fixes the typo case and not the 405 case unless it reimplements method matching, and it is a second list of the platform's surfaces that every new one has to be added to.

*Rejected: HTML5 history routing with the fallback limited to paths without a dot.* Nicer URLs, and it still answers `200` for `/anything-at-all`, which is a page telling a caller that a URL exists when it does not.

---

## D-092 — The station map draws its own graticule; tiles are optional decoration from OpenStreetMap

**2026-09-13 · accepted** · *`dashboard/src/StationMap.tsx`, Stage 11*

D-081 rejected keyed map providers and required that "a tile failure degrades to a graticule with the markers still drawn". It did not name where tiles come from, and a tile server is an external service on the surface SC-6 is judged on — so that choice is recorded rather than made in a config file.

**The map is built so that tiles are never load-bearing.** The graticule (every 10°, heavier every 30° and heaviest at the equator and prime meridian) and the station markers are Leaflet vector layers drawn from data the platform served. Tiles are a layer underneath them. If no tile ever loads — offline, blocked, rate-limited, or the provider gone — the page says so in one line and the map is still a correct map of where the stations are, at the precision each operator permitted.

**Tiles come from the OpenStreetMap Foundation's standard tile server by default, with its attribution, and `VITE_MAP_TILE_URL` replaces or disables them at build time.** No key, no account, nothing to rotate. An empty value builds a dashboard that makes no third-party request at all, which is the build to use wherever the independence test is being demonstrated rather than asserted.

The OSM tile usage policy permits light, attributed use and forbids heavy use. A dashboard polled by a handful of viewers is the first; if the public dashboard ever draws real traffic, the answer is a self-hosted tile set or a raster served from `/assets/`, and this entry is where to change it.

*Rejected: no base map at all, graticule only.* Honest and fully independent, and a station at 12.97°N 77.59°E drawn on a blank grid tells a reader nothing until they look the coordinates up. The tiles are what make the map a map; the design only has to make sure they are not what makes it *work*.

*Rejected: bundling a coastline dataset (Natural Earth, 110 m) as a vector layer.* Independent and recognisable, and roughly 100 kB of GeoJSON added to every page load to draw an outline tiles already provide. Worth revisiting if the tile policy ever becomes the constraint.

---

## D-093 — Pass windows and angles are published to the minute and the degree

**2026-09-14 · accepted** · *`meridian/api/public/window_privacy.py`, Stage 11*

D-082 lets an operator publish their station's position no more precisely than they choose, and rounds the coordinates on the way out. Stage 11's remaining endpoints publish things **computed from the stored position** — a pass's acquisition and loss times, its peak elevation, its azimuths, and the assignment and observation windows that inherit them. At full precision those undo D-082.

The leak is not hypothetical arithmetic. A kilometre of cross-track displacement moves a low-Earth-orbit pass's peak elevation by roughly a tenth of a degree and its acquisition time by a fraction of a second, and a station publishes dozens of passes a day. Anyone with the public element sets can fit the one position that reproduces them all, and recover the site far more finely than the two decimals its operator declared.

**Every public window is widened to whole minutes — its start floored, its end ceiled — and every angle is published as whole degrees.** That applies to passes, assignments and observations alike, whatever precision the station declared, and it happens in one module, as D-082's rounding does.

Widening rather than rounding is the point of the direction: a published window always *contains* the real one, so a reader is never told a pass ends before it does. A minute is a small fraction of an 8–15 minute pass and loses nothing a person reading a queue needs; the station itself receives exact times over MSP, which is where precision is required. Whole degrees keep "a 72° pass" meaningful while taking away the tenths an inversion would lean on.

This is coarsening, not a proof of privacy. A minute and a degree make the fit far weaker than the declared precision needs; they do not make it impossible for a station publishing thousands of passes over months. An operator for whom that matters should declare coarse coordinates *and* understand that a public schedule is information — which `MSP-SPEC.md` §4.1's description of the field should say when the specification is next revised.

*Rejected: scaling the coarsening to the declared precision.* It sounds more exact and is mostly bookkeeping: the relationship between decimals and seconds depends on orbit altitude and geometry, so a "matched" rule would be a guess wearing a formula. One rule is testable as a table and explainable in a sentence.

*Rejected: not publishing pass geometry at all.* Stage 11 asks for upcoming passes and assignment reasons, and a scheduling reason like "peak elevation below the configured minimum" is unreadable without the elevation it refers to.

---

## D-094 — The post-reception layer adds no hardware and no budget

**2026-09-14 · accepted** · *synopsis review*

At the synopsis review the faculty panel asked what Meridian does with data after receiving it. The request already before the department is ₹27,600 for Tiers 1 and 2, and an answer that needed another purchase would be an answer to a different question.

**The hardware for this work is what `PROJECT.md` §17 already requests: one SDR and a fixed 137 MHz antenna.** No L-band chain, no second SDR, no dish. Tier 3 tracking stays optional exactly as §17 states it, and nothing in modules 13–17 (D-095) depends on it — every one of them reads a reception the fixed antenna already produces, or a simulated one.

The line is held here because the viva needs measured numbers. Each capability below is proven on data the funded station and the simulator produce; one that needed new hardware to prove would arrive after the week-15 hardware gate at best, and unproven at worst.

*A tension this entry does not resolve.* D-053's closing paragraph, "Hardware this assumes", describes the full ₹43,500 build with tracking "funded rather than optional". §17, and D-066's zero turnaround for a fixed antenna, say otherwise. This entry relies on §17 and does not supersede D-053; which of the two describes the station the team intends to build is listed under Open (D-108).

*Rejected: an L-band receiving path as part of this answer.* It is the obvious way to receive more from the same satellites, and it needs a dish, a feed, an LNA and tracking. It is recorded as a candidate for later work (D-101), not as a response to the panel.

---

## D-095 — After reception: five capabilities, numbered 13 to 17

**2026-09-14 · accepted** · *synopsis review*

The panel's question found a real gap, not a presentational one. The platform predicts, schedules, and from Stage 20 counts misses — but an observation that arrives is stored and nothing further is concluded from it. A station owner cannot tell whether last night's pass was usable, why the one before it failed, or that their cable is going.

**Five capabilities, numbered 13 to 17 in the submission module list:**

| # | Capability | The question it answers |
|---|---|---|
| 13 | reception verdict | How likely is this reception to be usable? |
| 14 | loss diagnosis | If it failed or was partial, what most likely caused it? |
| 15 | station health watch | Is this station's receive chain degrading before reception fails? |
| 16 | owner reports | What does the station's owner need to know, in plain language? |
| 17 | evidence dataset | Can someone who was not in the room check all of the above? |

The answer had to meet three requirements, and these five meet them for stated reasons:

- **Any data type, including nothing.** None of the five reads image content. The verdict reads decoder statistics and signal measurements that an LRPT image, a LoRa telemetry frame and an empty pass all produce. A pass that received nothing still has an outcome, heartbeat evidence and a noise floor, and those are what loss diagnosis reads.
- **A measured number in the viva.** The verdict has a reliability diagram and a Brier score; diagnosis has a per-cause confusion matrix; the health watch has detection delay and false-alarm rate; the evidence dataset regenerates to an identical hash. **Owner reports are the exception** and are proven by working end to end in the demonstration. That is said plainly rather than dressed up with a metric nobody would defend.
- **No hardware and no budget** (D-094).

**In prose they are lowercase — the reception verdict, loss diagnosis — and never proper nouns.** `CLAUDE.md` permits two. The numbers belong to the submission module list, which is kept outside this repository, and appear only where that list is referenced.

They extend planned work rather than sitting beside it: the verdict uses `EVALUATION.md` §7's calibration discipline, diagnosis builds on Stage 20's miss classification, the health watch and diagnosis read the profiles Stage 19 creates, and the evidence dataset uses Stage 15's snapshot manifest. Placement is D-102, storage D-104, and the build order Stages 25–30 of the roadmap — after Stage 24, so that no existing stage number moves.

---

## D-096 — The microcontroller station stays a LoRa satellite receiver

**2026-09-14 · accepted** · *synopsis review*

The earlier proposal would have turned Tier 2 into a field sensor product. It stays what `PROJECT.md` §5.3 and D-053 describe: an ESP32-class station with a LoRa transceiver, receiving LoRa-modulated satellite telemetry and speaking MSP.

Its purpose is to prove the protocol boundary on hardware two orders of magnitude smaller than the Pi, and a sensor product would change what it proves. A node reporting field readings has no pass, assignment or listening block to exercise — it would show that MSP can carry arbitrary telemetry, which is not something MSP §1 claims or needs.

It also serves the post-reception layer directly: a LoRa telemetry pass is the second data type the reception verdict and loss diagnosis must handle, so "any data type" is tested on real hardware rather than asserted.

§5.3's fallback — a microcontroller without LoRa joining as a non-receiving station that reports its own environmental and power telemetry — is the station reporting on itself, not a sensing product, and is unchanged.

---

## D-097 — What was dropped from the earlier proposal

**2026-09-14 · accepted** · *synopsis review*

Recorded so that none of these is later found in a draft and taken for planned work. None is documented anywhere as planned:

- mission engine;
- research mission;
- area-change detection;
- personal node workspace;
- farmer sensor node;
- WhatsApp delivery;
- any paid AI service;
- a public orbital-data trust page.

Each fails at least one of the three requirements in D-095: it needs hardware or a recurring cost that D-094 rules out, it has no measured proof reachable in the project's time, or it serves people outside the network before the network has anything verified to tell them. Three have specific reasons worth keeping. **WhatsApp** business messaging is charged per conversation. **A paid AI service** is a recurring cost, an external dependency on the report path, and an output no one can regenerate from a snapshot, a configuration and a seed (`CLAUDE.md` rule 8). **A trust page** rating public element sets would publish a judgement of orbital-data accuracy before SC-3 has measured it — a claim ahead of its evidence.

Area-change detection returns only as a gated, validated candidate in D-101, after the reception verdict exists to gate it.

---

## D-098 — Owner reports are templates, sent by email or Telegram

**2026-09-14 · accepted** · *synopsis review*

After each pass, the station's owner receives a plain-language message: what was received and its verdict, or the diagnosed cause of the loss. Once a week, a station health summary. **Every message is rendered from a versioned template filled with stored results. There is no language model anywhere on this path.**

- **Regenerable.** The same stored verdict, diagnosis and template version produce the same text, and the delivery record keeps its content hash. A generated paragraph is neither reproducible nor something a team member can walk a reviewer through line by line (`GIT-WORKFLOW.md` Rule 10).
- **It cannot say more than the evidence.** A report is a statement the platform makes to a person. A template can only print what is stored, including "undetermined"; a generated summary can round an undetermined cause up into a confident one.
- **No cost, no new dependency** (D-094, D-097).

**Delivery is email, and a Telegram bot is acceptable because it is free.** Both are outbound and optional under the independence test: a failed delivery is recorded and retried, and never delays or changes scheduling, reception or any stored result. The report is a notification; the dashboard remains the place the record is read.

*Rejected: dashboard only.* The owner has to go looking, which is the thing this capability exists to remove. *Rejected: SMS.* Charged per message.

Where the owner's address comes from is not settled — it is personal data, and `PROJECT.md` §16 says none is held. See D-107.

---

## D-099 — The novelty claim is narrow, and is written narrowly

**2026-09-14 · accepted** · *synopsis review*

SatNOGS already rates observations — manually, as *with signal*, *without signal* or *unknown*, and through automatic rating rules. A claim that nobody assesses receptions would be false, and it is the kind of claim an examiner checks.

**Meridian claims two things:**

1. an automatic, **calibrated** confidence for every reception — a probability whose stated values are tested against observed frequencies with a reliability diagram and a Brier score; and
2. an automatic **attribution of cause** for every loss, which says "undetermined" when the evidence does not support a cause, and is scored per cause against known causes.

A label is not a probability, and a rule is not a calibrated one. That difference is the whole claim, and it is measurable, which is why it is the one made.

**No document, page, report or slide says "nobody else does this"**, "the first" or anything equivalent. `PROJECT.md` §3.2 already takes this posture for scheduling, and the same care applies here.

---

## D-100 — SC-3's measurement method carries a risk, and is tested before it is relied on

**2026-09-14 · accepted** · *the risk is recorded; the choice of method is open*

**SC-3 is not changed.** It states what is claimed — that the stated 1σ timing uncertainty is honest — and changing a target before the method has been measured would be fitting the claim to a worry.

**The size of the effect SC-3 measures.** Public element sets are roughly 1 km accurate at epoch and drift about 1–2 km per day. At 7.5 km/s along track, each day of age adds about 0.13–0.27 s of timing. D-060's prior deliberately takes the top of the published 1–3 km/day range, but on any reading the orbital contribution is sub-second for a fresh set and a few seconds after a week.

**What `first_detection_at` depends on besides the orbit.** It is the instant the station first detected the signal, so it moves with the horizon and obstruction in the acquisition direction, with link margin, and with the detector's threshold. Near the horizon elevation changes slowly: for an overhead pass at 850 km, the first 10° of elevation spans 8.4° of orbital arc, which takes 2.4 minutes at 3.54°/min — about 14 s per degree. A detection elevation that varies by two degrees from pass to pass moves `first_detection_at` by roughly half a minute, which is tens of times the effect being measured. SC-3's coverage could then be met or missed for reasons that have nothing to do with the element set.

**Decision.**

1. The risk is written into `EVALUATION.md` §6.3 and `PROJECT.md` §14.
2. **Before SC-3 relies on `first_detection_at`, the measurement is tested on archive data.** Take receptions whose element set was under a day old, where the orbital contribution is known to be sub-second, and measure the spread of first-detection offset. If that spread is larger than the effect SC-3 exists to detect, the method is not fit as it stands, and that is reported rather than worked around.
3. **An alternative is noted, not adopted: timing from the Doppler curve.** Range rate passes through zero at closest approach, mid-pass and high in the sky, where horizon and acquisition link margin do not apply. A constant receiver frequency offset shifts the curve's literal zero crossing, but not the point of steepest slope, so that is the offset-robust form. Oscillator drift during a pass, `EVALUATION.md` §6.2's objection, still applies.

*Open question.* Which method SC-3 is measured by, decided after step 2's result. A third option is recorded with its flaw: comparing against the predicted crossing of the station's learned horizon profile instead of geometric AOS removes the obstruction term but not link margin, and the profile is learned from the same detections, so it is circular unless learned on a disjoint period.

---

## D-101 — Phase 2 is a list of candidates, not a commitment

**2026-09-14 · accepted** · *synopsis review*

`PROJECT.md` §21, "Phase 2 candidates — not committed", records two directions: HRPT reception from the Meteor-M satellites already in the catalogue, and area alerts for people who do not run a station. **Nothing in it is planned work.** It has no roadmap stage, no success criterion and no line in the budget.

**Phase 2 starts only when both hold:** the Phase 1 post-reception stages (roadmap Stages 25–30) have passed their completion gates, and the tracking tier is approved. The first condition is not ceremony: an area alert is only safe if the reception verdict can stop a bad pass from raising one, so the gate has to exist and be measured first. The second is physical — the HRPT dish must track.

**Every alert type needs an external validation source before it is built** — NASA FIRMS detections for fire, MODIS or Sentinel-2 vegetation indices for greenness change. They are used offline to score alerts and never sit on the path that raises one, which is the independence test applied in advance to a product that does not exist.

**A naming clash, stated rather than left to be found.** "Phase 1" and "Phase 2" here mean this project's committed scope and what may follow it. They are not `PROJECT.md` §12's Phase 1 (Foundations, weeks 1–7) and Phase 2 (Intelligence, weeks 8–14). The terms came from the synopsis response and §21 says so in its first paragraph; a rename is listed under Open (D-108).

---

## D-102 — Where the five capabilities live

**2026-09-14 · accepted** · *`docs/ARCHITECTURE.md`*

Placement follows `ARCHITECTURE.md`'s rules: boundaries are firm, only `platform/reliability` decides what counts as a miss, `platform/prediction` knows nothing about MSP, and the scheduler does not read the observation store.

| Capability | Owner | Why there |
|---|---|---|
| 13 reception verdict | `platform/prediction` | It is a calibrated probability model, and prediction already owns calibration, temporal evaluation and model versioning (Stage 17). It reads stored observation fields through `platform/observations`' interface and never an MSP body, so prediction still knows nothing about MSP. |
| 14 loss diagnosis | `platform/reliability` | A cause cannot be attributed without first deciding whether the station was listening — "station not listening" *is* that decision. Rule 3 puts it in one place; a diagnoser anywhere else would restate it. |
| 15 station health watch | `platform/reliability` | It detects degradation before loss and feeds the same alerting and loss accounting as Stage 20. It reads signal measurements from observations and elevation from `platform/orbit`, each through its interface. |
| 16 owner reports | **new** `platform/notifications` | It composes outputs from three modules and sends them through external services. Inside any one of those modules it would make that module depend on the other two *and* gain an outbound network dependency. Kept separate, the external dependency lives in one place, like `ingest`, and its failure degrades nothing else. |
| 17 evidence dataset | **new** `platform/datasets` | It shares Stage 15's snapshot manifest and content hashing, and reads stored rows through `store` — it never recomputes a verdict or a diagnosis, which is what keeps its hash stable. Placed in `platform/observations`, the system of record would depend on prediction and reliability, which both read it: a cycle. |

**Not the registry, for the health watch.** The registry's health is liveness and the reported `health` object, both derived from heartbeats alone. D-013 already found three meanings heading for the word "health"; a receive-chain trend computed from observations and geometry would be a fourth.

**The verdict is not the yield prediction.** Yield is estimated before a pass — `P(decode | station, pass)`, for the scheduler. The verdict is estimated after it — `P(usable | what was received)`. A pass's own verdict must never be a feature of the yield prediction for that pass; past verdicts may inform station history under a temporal split.

**The verdict informs; reliability decides.** Whether a decoded reception with a low verdict counts as captured for SC-4 is reliability's rule, set in Stage 27, not the verdict's.

**Runtime evidence is Meridian's own.** Loss diagnosis reads the station's heartbeats, the network's own contemporaneous receptions and the catalogue's `satellite_transmitters.active`. Archive observations cross-check silent satellites in evaluation, as `EVALUATION.md` §5 already does, and not at runtime — `CLAUDE.md` says external data is training input only. Whether archive rows already ingested locally may count as runtime evidence is open (D-108). *The consequence, stated:* with one physical station, "satellite silent" is attributable for a measured reception only when the catalogue marks the transmitter inactive, and is otherwise undetermined. **Simulated receptions are never evidence for a measured station's diagnosis.**

**Two new modules mean two new commit scopes**, `notifications` and `datasets`, in `GIT-WORKFLOW.md` Rule 3 and the CI `conventions` pattern. They are added in the change that creates each module, not here: `CLAUDE.md` asks that no directory exist before its stage, and a scope with no module behind it sends the same signal.

*Rejected: one `platform/post_reception` module holding all five.* It groups code by when it runs rather than by what it decides, and would put a second miss decision outside reliability and a second calibration pipeline outside prediction.

*Rejected: the evidence dataset in `analysis/`.* That directory holds the report's evaluation scripts. The dataset is published for outside researchers, so it is produced by the platform's own command with the same manifest Stage 15 defines.

---

## D-103 — Proposed: optional reception evidence fields for MSP 0.3

**2026-09-14 · accepted 2026-09-14, amended by D-116 and D-117** · *`MSP-SPEC.md` §4.4, §6 and §7 at 0.3; D-116 moves the implementation from Stage 25 to Stage 13*

**First, whether §4.4 as of 0.2 is already enough.**

| The capabilities need | In MSP 0.2 |
|---|---|
| outcome, detection, first detection, peak SNR, Doppler | yes, typed |
| listening evidence and clock offset | yes, from the heartbeat |
| frames decoded | only as `products[].frames` — untyped, and D-072 deliberately validates no per-product schema |
| noise floor | no |
| SNR across the pass | no — only its peak |

**With 0.2 alone, all five still work, and two are weaker.** The verdict runs without a frames ratio. The health watch compares `peak_snr_db` against the pass's maximum elevation — one point per pass, which is coarse. Loss diagnosis cannot attribute interference at all and returns "undetermined" for it. A model feature cannot rest on `products[].frames`, a field no station is obliged to send in any particular shape.

**Proposal — three optional, additive parts of the observation body:**

```json
"signal": {
  "detected": true,
  "peak_snr_db": 11.4,
  "noise_floor_dbfs": -52.3,
  "receiver_gain_db": 32.8,
  "snr_samples": [ { "t": "2026-08-14T09:41:53Z", "snr_db": 3.1 } ]
},
"decode": {
  "decoder": "satdump",
  "decoder_version": "1.2.2",
  "frames_decoded": 412,
  "frames_failed": 37
}
```

- **`noise_floor_dbfs` with `receiver_gain_db`.** Relative to full scale, at a stated gain, rather than dBm: RF calibration is outside the software roadmap, and a relative figure at a known gain is what a station can report honestly. Interference is judged against the same station's own history, where a relative figure is sufficient — and only at the same gain, which is why the gain travels with it.
- **`snr_samples`, capped at 512** like `doppler_samples` (D-032). The health watch maps each instant to an elevation through the orbit service, which turns one point per pass into a curve.
- **`decode` as its own flat block.** Decoder name and version, because a decoder upgrade shifts every statistic and calibration has to be segmented by it; frames decoded and failed as plain integers. **Frames *expected* is not sent** — the platform computes it from the pass and the transmitter's nominal frame interval (D-104), so every station's ratio has one definition.

**Version impact.** Every field is optional and additive, so under §7 this is a minor bump to **0.3**, the rule's second exercise after D-082. Stations built against 0.1 or 0.2 need no change. A microcontroller omits the arrays and sends, at most, three flat numbers. Two full 512-sample arrays are about 50 KiB, inside D-028's 256 KiB observation cap.

**Process.** `GIT-WORKFLOW.md` Rule 9: a `spec(msp):` change with its conformance tests merges first, and Stage 25 implements against the merged text. The status is `open` because this changes a published protocol, and that is for the team to review before the specification pull request is written.

*Rejected: promoting `products[].frames`.* One decode can produce several products — an image and a frames file — so per-product counts double-count. Decoder statistics describe the decode, not an artefact of it.

*Rejected: the noise floor inside the heartbeat's `health` object.* It is opaque, capped at 4 KiB, and describes the station at an instant rather than a pass.

---

## D-104 — What the post-reception layer stores

**2026-09-14 · accepted** · *`docs/DATA-MODEL.md`; describes, no migration*

Six planned tables — `reception_verdicts`, `loss_diagnoses`, `signal_baselines`, `receive_chain_warnings`, `report_deliveries`, `dataset_exports` — are described in `DATA-MODEL.md`. The rules they share are settled here, because each is the kind of rule a migration makes permanent.

**Append-only, and bound to an observation revision.** A verdict is for `(assignment_id, revision)`. A new revision gets a new verdict and the old one stays, as D-015 keeps the old observation. Re-running with a new model version appends too: the evidence dataset has to be able to say which version concluded what.

**Every row names the method that produced it**, as a versioned string, following D-060's `method`. **`simulated` is copied from the station's registry record**, as for every table that can hold simulated data.

**Loss diagnosis covers** every failed or partial reception — an observation whose outcome is not `decoded`, or whose verdict falls below the partial threshold — and every held assignment whose window passed with no observation. **Not an `expired` assignment**: that is a decline, which D-008 keeps distinct from any reception. So a diagnosis is keyed on the assignment, with the observation revision nullable. The partial threshold is configuration, recorded with every run, and its value is chosen at Stage 26 from the calibrated verdict rather than in advance.

**The deferred tables are reused, not duplicated.** `noise_measurements` gains observation-sourced rows from each reception's noise floor; `interference_profiles` is the baseline a "raised" noise floor is judged against; `horizon_profiles` is the obstruction evidence; `products` is referenced by hash from the evidence dataset.

**"Health" stays out of every new name.** D-013 separated `state`, `health` and liveness; the health watch's tables are `signal_baselines` and `receive_chain_warnings`.

**Two gaps found in the existing model.**

- `noise_measurements.noise_floor_dbm` presumes absolute calibration the roadmap excludes. If D-103 is accepted, the stored value is dBFS with its gain and is named for it; settled when Stage 19 writes that migration.
- Frames expected needs the transmitter's **nominal frame interval**, which `satellite_transmitters` does not carry. It is added as a nullable attribute; where it is unknown the verdict omits the ratio rather than guessing one.

**The evidence dataset writes measured and simulated receptions to separate files**, with the flag on every row as well, and **exports measured receptions only unless simulated ones are asked for by name.** D-078's hazard does not end at the platform's edge: a researcher training on the package should not be able to mix the two by accident.

**Simulator ground truth never enters these tables** (D-105).

---

## D-105 — Simulated faults prove diagnosis and the health watch; D-078 still binds the verdict

**2026-09-14 · accepted** · *clarifies D-078*

D-078 excludes simulated observations from every training and evaluation set, because the simulator draws outcomes from elevation and a model would rediscover the generator. Loss diagnosis and the health watch are proven on simulator faults — on the face of it, exactly what D-078 forbids.

**The distinction is what the number claims.** D-078 guards against a result that looks like a fact about the world and is a fact about the simulator. A confusion matrix over injected faults claims something narrower, and true: given evidence of the shape a fault produces, the diagnoser names the fault. That tests the attribution logic, and it is the only source of ground truth for causes nobody labels in real data.

- **SC-8 and SC-9 are simulated results.** Labelled at every layer, reported separately from any real labelled cases, and never pooled with them.
- **They claim correct attribution of simulated faults, not real-world accuracy**, and the report says so beside the number. Real cases the team labels are reported as their own count, however small.
- **The reception verdict gets no such exception.** It is a calibrated probability, D-078's hazard applies to it unchanged, and it is trained and evaluated on measured receptions only.
- **Ground truth never travels through MSP or into the platform's database.** The simulator writes the injected cause into its own run record beside the seed, and the evaluation joins it to diagnoses afterwards. If the label travelled with the observation, a diagnoser able to read it would score perfectly and prove nothing.

*The circularity, stated.* The same team writes the simulator's fault effects and the diagnoser, so the two could agree by construction. Two mitigations: the fault effects are specified in Stage 25 before the diagnoser exists in Stage 27, and they are reviewed by a team member other than the diagnoser's author. Stage 21's existing "degraded decoder" fault is a cause diagnosis has no category for, so it serves as a negative control — the right answer to it is "undetermined".

---

## D-106 — What "usable" means for the reception verdict

**2026-09-14 · open**

A calibrated probability needs a label, and the label must not be built from the verdict's own inputs. If "usable" were defined as, say, frames decoded above some fraction of frames expected, the verdict would be predicting a threshold on a number it reads, and a perfect Brier score would prove arithmetic.

Candidates, none decided:

- **A human rating of each product, made blind to the verdict.** Independent of every input; slow, and at six to fifteen passes a day from one station it bounds the sample the team can rate.
- **Agreement with another reception of the same pass**, by another station or in an archive. Independent, but sparse while the network has one physical station.
- **A product-level check that uses no verdict input**, such as line continuity in a decoded image. Cheap, but it may correlate with the frames ratio closely enough to reintroduce the circularity.
- **SatNOGS vetting ratings on archive observations, as additional training labels.** Permitted as external training input; but *with signal* is not *usable*, and they rate a different network's receptions.

To be settled before Stage 26 begins, together with SC-7's target. **SC-7 cannot be measured until it is.**

---

## D-107 — An owner's contact address is personal data, and `PROJECT.md` §16 says none is held

**2026-09-14 · open**

§16: *"No personal data is collected, stored or processed."* Owner reports (D-098) need an email address or a Telegram chat identifier. `stations.operator` is a display name taken from MSP §4.1, not a contact.

- **(a)** Collect a contact out of band when an operator is issued an invite, with consent, never published, deleted on request — and amend §16 to say so.
- **(b)** Send reports only to stations the team operates, where the address is the team's own, and leave §16 as it stands. This proves delivery in the demonstration without holding anyone else's data.
- **(c)** No contact at all: an owner reads reports on an authenticated page. Holds nothing, and puts back the need to go looking.

**In no option is the contact an MSP field.** A microcontroller has no use for it, and a contact address on the wire goes wherever a station's request logs go.

Until the team decides, Stage 29 is built to option (b), which is the only one consistent with §16 as written.

---

## D-108 — Four smaller questions this work leaves open

**2026-09-14 · open**

1. **Archive rows as runtime diagnosis evidence.** D-102 limits runtime evidence to Meridian's own data, because `CLAUDE.md` calls external data training input only. Archive observations already ingested locally are not a runtime dependency on an external service, and would make "satellite silent" attributable for a one-station network. Allowing it widens a hard rule's wording, so it is the team's call.
2. **D-053 against `PROJECT.md` §17.** D-053 assumes the ₹43,500 build with tracking funded; §17 requests ₹27,600 with tracking optional, and D-066 relies on the fixed antenna. D-094 follows §17 for this work. The record should say once which station is being built.
3. **"Phase 1" and "Phase 2" mean two different things** — §12's build phases, and the committed scope versus D-101's candidates. A rename of the second pair, such as "this project" and "follow-on candidates", would remove the clash.
4. **Where Stages 25–30 fall in §12's weeks.** They need measured receptions, which exist only from Phase 4, and they follow Stage 24 in the roadmap only so that no stage number moves. Their calendar placement is not agreed.

---

## D-109 — Metrics are counted where they happen, and counted from the database at scrape time

**2026-09-14 · accepted** · *`meridian/metrics/`, `meridian/api/app.py`, `deploy/prometheus/prometheus.yml`, Stage 12*

Until now `/metrics` served only `prometheus_client`'s process collectors, and Stage 3's request and protocol metrics were never added. Stage 12 needs Meridian's own numbers, and they come from three different places. Each place gets the collection method that suits it.

- **Events inside a request are counted in the API process.** This covers requests, MSP error codes, heartbeats, observations, registrations and their delays. When `PROMETHEUS_MULTIPROC_DIR` is set, `prometheus_client` runs in multiprocess mode, because D-051 expects more than one worker on the Pi and a per-process counter would then answer differently on every scrape. With the variable unset, as in tests and development, the default registry is used.
- **State the database already holds is read at scrape time.** A custom collector computes stations by liveness, assignments by state, overdue assignments, database reachability, pool use and schema currency. It stores nothing, so no gauge can go stale, and liveness is still derived only by `registry/liveness.py` (D-054). When the database cannot be reached, the collector emits `meridian_database_reachable 0` and no counts, so a zero is never published to mean "unknown" (D-086).
- **Scheduled jobs count themselves in their own process** (D-110), and serve those counts on a listener inside the compose network. Prometheus scrapes it as a second target. It requires the same bearer token as `/metrics` and answers an unauthorised scrape the same way. The token check moves from `meridian.api` to `meridian.metrics`, so both processes use one implementation. This is the separate process D-087 called premature while there was nothing for it to report.

*Rejected: a Pushgateway.* Another container on the Pi, and a job that dies keeps reporting its last success until someone deletes the series, which is the opposite of what an alert on a stopped scheduler needs.

*Rejected: a job-runs table read by the API.* It would be a migration and a retention question created only for monitoring, and it would put scheduler timings in the system of record.

---

## D-110 — Pass generation and scheduling run as a supervised process in the default profile

**2026-09-14 · accepted** · *`meridian/jobs/`, `deploy/docker-compose.yml`, Stage 12*

Passes and assignments are only produced when someone runs `meridian passes generate` and `meridian schedule`. The only thing that runs them on a timer is a shell loop in the `sim` profile, whose own comment says "Stage 12 owns scheduled jobs". A deployment without the simulator therefore schedules nothing. That fails the independence test: Meridian must schedule using its own station alone.

**`meridian jobs run` is a long-running process.** Each round generates passes and then schedules with configuration A over a six-hour horizon starting now, and the rounds repeat every `SCHEDULE_INTERVAL_S` (default 300).
- Both steps are idempotent over a horizon (D-063, D-066), so overlapping rounds write nothing twice.
- A failed round is logged and counted; it does not stop the process.
- SIGTERM ends the wait between rounds at once, and lets a running round finish its transaction.

It runs as a `jobs` service in the **default** profile, and `sim-scheduler` is removed. Configuration A stays until Stage 18 supplies a constrained scheduler to switch to.

*Rejected: cron, inside the container or on the host.* A host crontab is outside the repository and fails the clean-machine requirement. In-container cron needs root, and it hides a failing run in cron's own mail rather than in a metric.

*Rejected: running the jobs inside the API process.* Several workers would each schedule, and a slow optimisation would compete with heartbeats for the same event loop.

---

## D-111 — The metric catalogue, and the three the roadmap lists that Stage 12 does not publish

**2026-09-14 · accepted** · *`meridian/metrics/`, `deploy/prometheus/rules/`, Stage 12, Stage 20*

**Names.** Every name starts with `meridian_` and carries its unit as a suffix, as the Prometheus convention requires and `CLAUDE.local.md` §4 already asks of Python names.

**Labels.** Only these labels, each with a bounded set of values:
- route template;
- method;
- status class (`2xx`…`5xx`);
- MSP error code;
- observation outcome;
- assignment state;
- liveness;
- scheduled task (`task`, never `job`: Prometheus sets `job` from the scrape configuration and would rename a metric's own `job` label to `exported_job`);
- `simulated`.

No station, satellite, assignment or token identifier is ever a label (Stage 3). A path no route matches is labelled `unrouted`, never with its raw path, so a scan cannot create series. Every series that could mix simulated and measured stations carries `simulated` (Hard rule 5).

**Not published until Stage 20: confirmed misses, indeterminate outcomes and loss budget remaining.** The roadmap lists all three under Stage 12, and none of them can be computed yet. Only `platform/reliability` decides a miss, and it is still a docstring. A series held at zero would say "no misses" where the truth is "not measured", which D-086 already refuses for the public API. The loss-budget alert waits with them, and the rules file marks where it goes.

**The "observation queue growing" alert watches what the platform can see.** A station's upload queue is on the station, and MSP 0.2 heartbeats do not report its depth. The platform can see an assignment that is still `held` or `in_progress` when its window ended more than `OVERDUE_AFTER_S` ago. `meridian_assignments_overdue` counts those.

This counts reports that have not arrived; it does not count misses, and nothing is classified from it. A queue-depth field in the heartbeat would be a separate `spec(msp)` change under Rule 9.

---

## D-112 — Alerts are Prometheus rules with unit tests, routed by Alertmanager

**2026-09-14 · accepted** · *`deploy/prometheus/rules/`, `deploy/alertmanager/`, `deploy/grafana/provisioning/`, Stage 12*

**Rules.** The alert rules are files under `deploy/prometheus/rules/`. Each alert has a `promtool test rules` case that proves it fires and a case that proves it stays silent, and CI runs them.

An alert that has never been seen to fire is a claim, and the demonstration in `PROJECT.md` §13 depends on one firing within ninety seconds.

**Routing.** An Alertmanager container joins the `metrics` profile. The tracked configuration routes everything to a receiver that sends nothing. A deployment that wants email or a webhook supplies its own configuration from an untracked file, which a tracked example shows how to write. No address is committed or held by the platform (D-107).

**Grafana** is provisioned from files: the Prometheus datasource, and one platform dashboard as JSON. Its admin password keeps the existing placeholder refusal.

*Rejected: Grafana-managed alerting.* Rules would live in Grafana's database, or in provisioning YAML that no tool in this repository can evaluate against sample data, so "the alert works" could only be shown by breaking something live. It would also save only one small container.

---

## D-113 — The platform image is built for amd64 and arm64 and published to GHCR from `main`

**2026-09-14 · accepted** · *`.github/workflows/`, `deploy/Dockerfile`, `deploy/docker-compose.yml`, Stage 12*

The Dockerfile's header has promised this since Stage 1: the Pi pulls, it does not build. Compiling the dependency set on a Pi is how the ten-minute bring-up becomes forty.

**Build and push.** A workflow on push to `main` builds `linux/amd64` and `linux/arm64` with buildx and pushes to `ghcr.io/harshareddy-bathala/meridian`.
- The tags are `sha-<short>` and `main`.
- It never runs on a pull request, so a fork needs no credentials and the existing `image` job stays the per-PR check.

**Only after CI passes, and `main` moves last.** The workflow runs when the CI workflow has succeeded on a push to `main`, not on the push itself.
- A workflow triggered by the push would run beside CI and could publish a commit whose tests, cold bring-up or restore round trip then failed.
- It pushes `sha-<short>` first, then runs the image's entry points on both architectures from the registry.
- Only then does it point `main` at that image, so the tag a Pi pulls never names an image that failed to import on its own architecture.
- A GHCR package starts private. Making it public, or logging the Pi in, is a one-time step in `docs/OPERATIONS.md`.

**Compose.** Every platform service gets `image: ${MERIDIAN_IMAGE:-ghcr.io/harshareddy-bathala/meridian:main}` beside its `build:`. `docker compose pull && docker compose up` is then the Pi's path, and `up --build` is still a laptop's.

**Base images** are pinned by digest as well as tag, so the build that passed CI and the build on the Pi start from the same bytes.

*Rejected: building on the Pi.* It fails the ten-minute requirement on the hardware it is stated for.

*Rejected: Docker Hub.* A second account and a second secret, with GHCR already beside the repository.

---

## D-114 — Every container's logs are capped, and secrets can be read from files

**2026-09-14 · accepted** · *`deploy/docker-compose.yml`, `deploy/.env.example`, `meridian/config.py`, `meridian/cli_serve.py`, Stage 12*

**Logs.** No service has a `logging:` block, so Docker's default `json-file` driver grows without limit on a 256 GB card that also holds the database. Every service now uses `json-file` with `max-size: 10m` and `max-file: 3`, from one YAML anchor.

`API_LOG_LEVEL` has been loaded since Stage 1 and read by nothing. `meridian serve` applies it, with one plain-text line format across uvicorn's loggers and Meridian's. The format stays plain because `docker compose logs` is the operator's first tool.

**Secrets.** `METRICS_TOKEN`, `TOKEN_HASH_PEPPER` and `REGISTRATION_INVITE_TOKEN` each also accept a `*_FILE` variant naming a file to read. The file wins when both are set, and the placeholder refusal applies to its contents.

**Tunnel token.** The tunnel reads its token from the `TUNNEL_TOKEN` environment variable instead of `--token` on the command line, where `ps` shows it to every user on the host. `docker inspect` still shows environment variables, but only to someone who can already control Docker, which is root on that host anyway.

**Where `.env` lives: `deploy/.env`, beside the compose file.** Compose reads `.env` from the project directory, which is the directory of the first `-f` file. It does not read the working directory. So `docker compose -f deploy/docker-compose.yml` from the repository root never read the `.env` there, though the README, the compose header and `.env.example` all said to put it there.

Every value fell back to its `${VAR:-default}`, which is why CI passed with the example copied to the wrong place. It is also why an edit to that file would have silently changed nothing.

The documented location is now `deploy/.env`, which needs no flag. A deployment that already keeps its file at the root keeps working as long as it passes `--env-file .env` on every command, and the tools in `deploy/tools` accept the same flag.

Rotation, redaction and least-privilege database users remain Stage 23's.

---

## D-115 — Backup and restore are host tools, and restore refuses what it cannot restore faithfully

**2026-09-14 · accepted** · *`deploy/tools/backup.py`, `deploy/tools/restore.py`, Stage 12, Stage 23*

**Where they run.** `pg_dump` and `pg_restore` are not in the platform image, which carries no PostgreSQL client and should not grow one. Both run on the host, drive `docker compose exec -T db`, and use the stdlib only, like `verify_public_surface.py`.

**Backup** streams `pg_dump --format=custom` to a file. Beside it, it writes a manifest with:
- the file's sha256;
- the TimescaleDB extension version;
- the alembic revision;
- the creation time.

**Restore.**
1. Refuse on a checksum mismatch.
2. Refuse when the running TimescaleDB version differs from the manifest's. A TimescaleDB dump restored into another extension version is not supported upstream.
3. Stop `api` and `jobs`, then recreate the database.
4. Run `timescaledb_pre_restore()`, then `pg_restore`, then `timescaledb_post_restore()`.
5. Run `migrate`, start `api` and `jobs` again, and wait for `/healthz`.

CI performs the round trip: back up, drop the database, restore, and compare row counts.

*Rejected: `meridian db backup`.* It would need the PostgreSQL client in the platform image, or a database connection that cannot run `pg_dump` at all.

*Rejected: copying the volume.* It only works with the database stopped, and it is tied to the host's filesystem and architecture.

A backup schedule, retention, and a restore drill on the real deployment are Stage 23's.

---

## D-116 — MSP 0.3 lands with Stage 13, not Stage 25

**2026-09-14 · accepted** · *`docs/MSP-SPEC.md`, `docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md` Stage 25, Stage 13*

**D-103 is accepted, and its fields arrive with the stage that first produces them.** Stage 13 builds the decoder integration, the one step that can measure a noise floor, SNR across a pass, and decoder statistics. Building that pipeline against MSP 0.2 would squeeze all of it into untyped `products[].frames` and `client_notes`, then rewrite the pipeline at Stage 25. D-103's own rejected alternatives explain why that fails.

**The order is D-103's, and only the stage changes.**
1. A `spec(msp):` pull request carries this entry, D-117 and D-118, and the specification edit. It is **documentation only**, as MSP 0.2's was (#11).
2. The Stage 13 pull request then implements, in this order: platform storage, platform validation, then the client.

**Why the specification PR carries no conformance tests.** D-103 said the specification change comes "with its conformance tests". But a conformance test asserts the platform's bytes, and the platform cannot produce them until the implementation exists. A test merged ahead of its implementation must be skipped or marked to fail, which is scaffolding. The tests land in the Stage 13 pull request, in the same commit as the behaviour they pin.

**The platform deploys before any 0.3 client.** Today's request model ignores unknown fields, so a 0.2 platform would accept a 0.3 body and silently drop its evidence.

**What Stage 25 keeps:**
- observation-sourced `noise_measurements` rows;
- the transmitter's nominal frame interval, and frames expected;
- the simulator's production of the new fields;
- the four ground-truth faults.

Its "Specification first" subsection and the first platform and station-client bullets are now Stage 13's. The roadmap is edited to say so.

*Rejected: staying on MSP 0.2 for Stage 13 and upgrading at Stage 25.* The client pipeline would be built twice. The digest rule (D-118), which has to be decided before the first 0.3 row exists, would wait for a stage that does not need it.

---

## D-117 — What MSP 0.3 refuses

**2026-09-14 · accepted** · *`docs/MSP-SPEC.md` §4.4, §6; `meridian/api/models/observation.py`, Stage 13*

D-072's principle carries over: refuse a body that contradicts itself, store anything merely unusual. Every field D-103 adds is optional, and every rule below constrains only the new fields, so **no valid 0.2 body is refused by 0.3**.

**`signal` gains three optional fields:**
- `noise_floor_dbfs`: a finite number.
- `receiver_gain_db`: a finite number.
- `snr_samples`: an array of `{ "t": <UTC instant>, "snr_db": <finite number> }`.
  - **At most 512 entries**; more is `malformed`, like `doppler_samples` (D-032).
  - Order is significant: they are a time series, stored and hashed as sent.
  - `null` (not measured) and `[]` (measured, nothing worth reporting) remain different claims.

**A noise floor requires a gain.** D-103 makes the floor comparable only at a stated gain, so a floor without one cannot be compared with anything, and is `malformed`. A gain without a floor is accepted; it describes the receiver.

**`decode` is an optional top-level object.**
- `decoder`: a non-empty string. **Required whenever the block is present**, because a statistic from an unnamed decoder cannot be segmented by decoder (EVALUATION.md §11.1).
- `decoder_version`: optional string.
- `frames_decoded`, `frames_failed`: optional integers, each ≥ 0. Optional because a decoder with no frame structure has no frames to count, and zero would be a false claim.

**Outcome consistency, applied only when `frames_decoded` is present:**

| `outcome` | `frames_decoded` must be |
|---|---|
| `decoded` | ≥ 1 |
| `signal_no_decode` | 0 |
| `no_signal` | 0 |
| `aborted` | anything |

**`not_attempted` carries no `decode` block and none of the three new `signal` fields.** A station that never began measured nothing. Its body already carries no detection (D-072).

**D-072 is unchanged.**
- `decoded` and `signal_no_decode` still require `signal.detected` with `first_detection_at`.
- A station whose decoder produced frames but no timing reports `aborted`, with its `decode` block and no `signal` block (D-122).
- The alternative — `detected` without an instant — would admit a guess into the timing measurement SC-3 is built on.

---

## D-118 — Fields added after 0.2 are hashed only when present

**2026-09-14 · accepted** · *`meridian/observations/canonical_body.py`, D-070, Stage 13*

**The problem.** D-070's canonical body renders every field of the stored record, writing an absent one as `null`. Adding MSP 0.3's fields under that rule would change the canonical bytes of **every observation already stored**, whose new columns are all `null`. Two things would break:
- **Every stored `content_sha256` would stop matching its own row.** A dataset snapshot could no longer verify its hashes, which is hard rule 8.
- **A 0.2 observation still queued on a station** would retry as a "change" and write a spurious new revision, defeating D-015.

**The rule.** The keys `noise_floor_dbfs`, `receiver_gain_db`, `snr_samples` and `decode` are rendered only when their value is not `null`. Every later additive field follows the same rule. It is a stated exception to D-070's "absent fields are written as `null`", and it applies only to keys introduced after 0.2.

**What is preserved:**
- **Absent and `null` still hash identically**, so a station omitting a field and one sending `null` agree, as before.
- **`snr_samples: []` still differs from absent.** An empty array is not `null` and is rendered.
- Inside `decode`, the optional members are rendered as `null` when missing. The block's own shape is new in 0.3, so there are no stored digests to protect there.

A golden digest of a 0.2 observation is pinned before the change and must survive it.

*Rejected: a `canonical_version` column and re-hashing old rows.* It changes stored history to accommodate a schema change, and a snapshot taken before the migration would then disagree with one taken after, over identical facts.

---

## D-119 — MSP 0.3's evidence is stored as columns on `observations`

**2026-09-14 · accepted** · *`deploy/migrations/sql/0015_reception_evidence.sql`, `docs/DATA-MODEL.md`, Stage 13*

**Seven nullable columns on the `observations` hypertable**, mirroring how `doppler_samples` is already held:

| Column | Type | From |
|---|---|---|
| `noise_floor_dbfs` | `double precision` | `signal.noise_floor_dbfs` |
| `receiver_gain_db` | `double precision` | `signal.receiver_gain_db` |
| `snr_samples` | `jsonb` | `signal.snr_samples`, as sent |
| `decoder` | `text` | `decode.decoder` |
| `decoder_version` | `text` | `decode.decoder_version` |
| `frames_decoded` | `integer` | `decode.frames_decoded` |
| `frames_failed` | `integer` | `decode.frames_failed` |

`observations_current` is recreated in the same migration, because a view's `*` is expanded when the view is created.

*Rejected: a side table keyed on the observation.* Every row read by the verdict would need a join, and immutability (D-015) would have to be enforced in two places for one record.

**The compressed-chunk question is tested, not assumed.** `observations` has compression enabled with a 7-day policy, and no migration has altered it since. A nullable `ADD COLUMN` is supported on compressed hypertables. A `CHECK` added across compressed chunks is not established for TimescaleDB 2.29.

The migration is therefore tested by compressing a chunk holding an existing row and then upgrading. **Database `CHECK`s are added only where that test proves they apply.** Wherever they cannot be, D-117's rules are enforced by the request model alone, and the migration's header says so. The model already restates every database rule (D-072), so no body the model accepts could be refused later.

**Not exposed on `/api/v1` yet.** The public observation history keeps its current fields. Decoder statistics are published when Stages 25 and 26 give them a reader. D-083 makes `/api/v1` no compatibility promise, and adding fields breaks nothing that reads it today.

---

## D-120 — The reception layer sits below `PassExecutor`

**2026-09-14 · accepted** · *`meridian_client/reception/`, `meridian_client/execution.py`, Stage 13*

**`PassExecutor` stays the loop's only view of reception** (D-069, D-073). Stage 13 adds `ReceptionExecutor`, one implementation composed of three narrower protocols in a new `meridian_client/reception/` subpackage:
- **`Receiver`** owns an SDR, or stands in for one. It starts a capture, reports whether it is still alive, and stops it.
- **`Decoder`** turns a finished recording into a decode report.
- **`RotatorController`** points an antenna, or does nothing (D-126).

The names are D-069's: an executor drives a receiver, a decoder and possibly a rotator.

**No threads.** The loop is single-threaded, and none of `begin`, `end` or `take_completed` may block for a pass.
- A real receiver is a subprocess writing a file: `rtl_sdr` is the shape the protocol is checked against, though no physical adapter ships at this stage.
- A decoder is a subprocess.
- Both are polled on each tick. The only waits allowed are bounded ones of seconds, when stopping or killing a process.

A thread would make the executor's state concurrent for no gain, because every long-running part is already another process.

**What ships:**
- the three protocols;
- a simulated receiver and a file-replay receiver (D-125);
- the null rotator (D-126);
- the subprocess decoder adapter (D-124);
- `ReceptionExecutor`.

**The simulator is unchanged.** `SimulatedExecutor` keeps implementing `PassExecutor` directly. Its outcome model, determinism (D-077) and golden digest stay exactly as they are; Stage 25 extends it with the new fields.

---

## D-121 — The executor states its capture window, and the loop reports what it says

**2026-09-14 · accepted** · *`meridian_client/execution.py`, `meridian_client/station_loop.py`, `meridian_client/held_assignments.py`, Stage 13*

Four faults in the loop become real once reception takes real time.

**1. Capture starts late, and is never widened.**
- The loop begins an assignment on the first tick at or after `start_at`, so up to one heartbeat interval late.
- MSP §4.3 asks a station to widen by `timing_uncertainty_s` if it can afford the recording, and nothing does.

`PassExecutor` gains **`capture_window(assignment) -> CaptureWindow`**, and `ReceptionExecutor` answers with the assignment's window widened by `timing_uncertainty_s` at each end.
- The platform has already widened by one σ (D-021), so a capture covers two σ in total. D-060 prices the trade: overstating costs disk; understating loses the start of the pass.
- The loop clips a capture's end to the next held assignment's window, so one pass's tail never delays the next pass's head.
- `run()` sleeps until the earlier of the next heartbeat and the next capture edge. Capture then starts on time, at the cost of one extra heartbeat at each edge. That extra heartbeat is also the one that reports `listening` promptly.

**2. Work is dropped from the heartbeat while it is still being done.**
- The loop drops an assignment at `end_at`, so the heartbeat stops naming it during the widened tail, and while its decoder runs.

An assignment now **stays in `held_assignments` until its result has been handed to the queue**. D-067 already made the platform keep an assignment the station still names out of expiry, however overdue, so nothing changes there.

**3. The heartbeat copies `listening` from the assignment, not from the receiver.**
- A dead receiver would keep claiming it was listening, which is exactly the claim hard rule 7 must be able to trust.

`PassExecutor` gains **`status(running) -> ExecutionStatus`**: a `state`, a `listening` block, and the ids still unfinished. `ReceptionExecutor` reports:
- `listening`, with the frequency and mode the receiver actually tuned, only while the receiver is alive;
- `processing` while a decode runs;
- `degraded`, with no listening block, when a receiver has died inside its window.

`NullExecutor` and `SimulatedExecutor` report what the loop reports today, so their behaviour is byte-identical.

**4. A held pass that never began disappears.**
- An assignment the station held but never started is dropped at its window end, and the platform later expires it as a decline.
- A decline is absence from `held_assignments` (D-003), and that is not what happened: the station took the work and failed to start it.

The loop now calls `end()` for a held assignment whose window closed without `begin`, and the executor yields `not_attempted` for it (MSP §4.4).

`StationLoop`'s constructor is unchanged, since everything new arrives through the protocol it already holds.

---

## D-122 — How a reception's outcome is derived

**2026-09-14 · accepted** · *`meridian_client/reception/outcome_rules.py`, Stage 13*

The rules are a pure function from the facts of one reception to an `ObservationResult`, and the first matching row wins.

| Facts | `outcome` | `signal` | `decode` |
|---|---|---|---|
| Never started — receiver refused, disk guard, no decoder for the mode, or window closed before `begin` | `not_attempted` | — | — |
| Started; the decoder failed, timed out, or wrote an invalid report | `aborted` | — | — |
| Capture interrupted, or covering less than the policy's minimum of the window, with a valid report | `aborted` | evidence if any | yes |
| Complete; frames ≥ 1 and a detection offset | `decoded` | detected, at the instant below | yes |
| Complete; frames ≥ 1 and no timing | `aborted` | — | yes |
| Complete; frames 0 or not counted; an SNR sample at or above the threshold | `signal_no_decode` | detected, at the first such sample | yes |
| Complete; frames 0; no SNR sample at or above the threshold | `no_signal` | not detected, with SNR, floor and gain | yes |
| A report with neither frames nor SNR | `aborted` | — | — |

**`no_signal` is reached only by a complete capture and a successful decode that found nothing.** That is what "verifiably listening, nothing detected" means (MSP §4.4). Every failure of the station's own chain is `aborted` or `not_attempted`, so absence of signal is never confused with a broken station (hard rule 7).

**The detection instant.**
- It is the recording's first-sample time plus the earliest evidence offset in the report: the first decoded frame, or the first SNR sample at or above the threshold.
- The first-sample time is derived when capture stops, as `stopped_at − sample_count / sample_rate`, not from when the receiver process was launched. Start-up latency would otherwise bias every timing-error measurement by an amount `clock_uncertainty_s` does not cover.
- The method and threshold are named in `client_notes`, because first detection depends on the detector (D-100).
- An offset outside the recording invalidates the report. It is never clamped into range.

**Unknown is absent, never zero.** A floor, gain, version or count the decoder does not report is omitted from the body.

**SNR samples beyond 512** are reduced by taking the sample nearest the centre of each of 512 equal time buckets. They stay raw values, not averages (D-032), and `peak_snr_db` is taken over the full series before the reduction.

---

## D-123 — Each reception keeps a capture folder, and a restart resumes from it

**2026-09-14 · accepted** · *`meridian_client/reception/capture_folder.py`, `capture_recovery.py`, Stage 13*

**One folder per assignment**: `<state>/captures/<assignment_id>/`, beside `held.json` and `outbox/`. It holds a `manifest.json`, the recording (or a reference to it), the decoder's output directory, its report, and its stdout and stderr logs.

**The manifest records the phase**, and is rewritten temp-then-rename on each transition (D-068):
- `refused`
- `capturing`
- `captured`
- `decoding`
- `reported`
- `handed_over`

**Recovery at start-up reads every manifest:**

| Phase found | Action |
|---|---|
| `capturing` | the capture was interrupted: decode what was recorded, and report `aborted` |
| `captured`, `decoding` | clear the decoder's output and decode again |
| `reported` | rebuild the result and hand it over again |
| `handed_over` | nothing |

The rebuilt body is deterministic, so a result handed over twice produces an identical digest, and the platform writes nothing the second time (D-015). The window D-073 names — between the queue write and `handed_over` — is unchanged, and now reconstructible, as D-073 anticipated.

**A disk guard runs before every capture.** The estimated recording size times a margin, plus a fixed reserve, must be free, or the pass is `not_attempted`, with the reason in `client_notes`. A full disk mid-capture is a worse outcome than a pass never started.

**A recording is referenced where it is, never copied or hashed on the loop.** An LRPT pass is about a gigabyte, and copying or hashing that on a Pi takes seconds the loop may not spend (D-069). The manifest records path, size and modification time, and a recording that changed before its decode is refused.

**Retention.**
- The recording is deleted once its manifest reaches `handed_over`, unless the station is configured to keep recordings.
- Manifests, reports and logs are pruned after thirty days, the acceptance window D-074 already mirrors.
- Recordings are never committed to the repository (`GIT-WORKFLOW.md` Rule 4); the file patterns are gitignored.

---

## D-124 — A decoder is a supervised subprocess that writes Meridian's report

**2026-09-14 · accepted** · *`meridian_client/reception/subprocess_decoder.py`, `decode_report.py`, D-001, Stage 13*

**Invocation.**
- Each mode, such as `lrpt`, maps to an argv template in the station's configuration.
- Templates are validated when the configuration is loaded, against a closed set of placeholders: `{recording}`, `{output_dir}`, `{report_path}`, `{sample_rate_hz}`, `{sample_format}`, `{centre_freq_hz}` and `{mode}`. An unknown placeholder is a configuration error, not a runtime surprise.
- The process is started with no shell, so a file name can never become a command, in its own session so its whole process group can be stopped.
- Its stdout and stderr go to files in the capture folder, never to pipes, which deadlock a child that writes more than a pipe buffer while nobody reads.

**Supervision.**
- The timeout is measured on an injected monotonic clock.
- When it expires: terminate the process group, wait a bounded time, then kill it.
- At most one decode runs at a time, oldest first, at a lower CPU priority. A capture always takes precedence over a decode.
- A mode with no configured decoder is refused at `begin`, and the pass is `not_attempted`.

**The output contract is Meridian's own JSON report**, written to `{report_path}`:
- `decoder`, `decoder_version`;
- `frames_decoded`, `frames_failed`, and the offset of the first decoded frame;
- SNR points as offsets into the recording;
- noise floor.

Parsing is strict: integers exclude booleans, and numbers must be finite. A missing or unparseable report is a failed decode. A station wraps whichever decoder it runs in a script that writes this file.

**The GPL boundary holds by construction** (D-001). SatDump and GNU Radio are only ever run as separate programs named in configuration, and nothing in the client links, imports or copies them.

**No SatDump output reader ships yet.** Reading SatDump's own files — its frame file's naming, its sync-marker handling, where it logs SNR — is unverified without a real recording through a real SatDump. A reader built from documentation alone could count frames wrongly with nothing to show it, and a physical adapter may stay unvalidated at this stage, but it may not be wrong.

The reader, and its `ATTRIBUTION.md` entry, land when a recorded pass validates them. Until then, a SatDump wrapper script writes the report above.

---

## D-125 — A synthetic receiver can only report for a simulated station

**2026-09-14 · accepted** · *`meridian_client/reception/synthetic_receivers.py`, `reception_executor.py`, hard rule 5, Stage 13*

**The hazard.** A simulated receiver or a file-replay receiver attached to a station registered `simulated: false` would report `listening` and submit a measured-looking observation of a pass nobody heard. That is a simulated result presented as measured, which hard rule 5 exists to prevent, delivered through the one path the platform trusts.

**The guard is structural.**
- Every `Receiver` declares `hears_the_sky`: true for a physical receiver, false for the simulated and replay receivers.
- `ReceptionExecutor` is constructed with the station's `simulated` flag, and **refuses a receiver that does not hear the sky for a station that is not simulated**, at construction, before any pass.

**Replay says it is replay.** A file-replay result names the recording it replayed in `client_notes`. An offline replay run (Stage 13's optional runner) writes a body to a file or stdout and never submits it.

*Rejected: trusting configuration discipline.* The mistake this prevents is exactly the one a tired operator makes, and it leaves no trace in the data.

---

## D-126 — Receive-only is structural, and antenna tracking is deferred

**2026-09-14 · accepted** · *`meridian_client/reception/`, `docs/ARCHITECTURE.md` rules 2 and 6, D-066, D-094, Stage 13*

**The station never transmits (hard rule 4), and the reception layer has nowhere a transmission could come from.**
- No protocol has a transmit-shaped method.
- No adapter opens a device for writing.
- The decoder adapter runs programs that read a recording.

A test asserts the protocol and adapter surface. A denylist of transmit-capable programs is not claimed as a safeguard, because a list cannot be complete, and a partial list would be presented as a guarantee it is not.

**Only the null rotator ships.**
- `RotatorController` is defined, so a tracking station later changes an implementation, not the executor.
- `NullRotator` is what the funded station has: a fixed QFH antenna for 137 MHz (D-094). Phase 1 schedules every station as fixed (D-066).
- `slewing` is never reported.

**Why tracking waits.** Pointing an antenna needs azimuth and elevation over the pass, which means propagation in the client. ARCHITECTURE rule 2 allows only `platform/orbit` to import a propagator, and the ruff ban enforces it. The two ways forward are recorded for the stage that funds the tracking hardware:
- **(a)** the assignment carries a pointing table computed by the platform — an MSP change;
- **(b)** rule 2 is relaxed for the client's use of `sgp4`, with the element set it already receives (§4.3).

Neither is decided here, and neither is needed by a fixed antenna.

*Rejected: a Hamlib `rotctld` adapter now.* It would be an adapter with nothing to point it, tested against a fake server, for hardware that is optional and unfunded.

---

## D-127 — A station is configured by one file, and it cannot declare itself simulated

**2026-09-18 · accepted** · *`meridian_client/station_config.py`, `station_wiring.py`, `credentials.py`, Stage 13*

Stage 13 built a reception layer that nothing outside the tests could construct. A station needs to say what it receives with, what decodes each mode, and where its state lives, or the layer is a library with no operator.

**One TOML file, read with `tomllib`.** Standard library, so the client still installs on a Pi with `httpx` as its only dependency. Tables: `[station]`, `[receiver]`, `[decoders.<mode>]`, `[policy]`, `[disk]`, `[retention]`. Every table is optional and every default is the one the documentation names.

**Unknown tables and keys are refused, not ignored.** A misspelled key that silently left a setting at its default would be discovered at the end of a pass, and a pass never repeats. This is D-124's rule for decoder placeholders, applied to the whole file — and each decoder command is validated as it is read, so a bad argv template fails at start-up.

**Relative paths resolve against the file's own directory**, so a configuration and the recordings it names can be moved together.

**`simulated` is not in this file.** It decides whether a receiver that does not hear the sky may report at all (D-125), and a flag an operator could edit between runs would let a simulated receiver report for a station the platform records as measuring the real sky. So it is written into the credentials at registration, from the profile the station actually sent, and read back from there.
- `StationCredentials` gains `simulated`; `register()` sets it.
- **A credential file written before the field existed reads as `false`** — the direction that refuses a synthetic receiver rather than admitting one.

**No physical receiver is configurable**, because none ships (D-124). `kind = "sdr"` is refused by name rather than ignored.

*Rejected: environment variables, as the simulator uses.* The simulator's fleet is one process with three settings; a station has a decoder argv per mode and a recording table, which is a file, not an environment.

---

## D-128 — The offline replay runner writes a body and cannot submit one

**2026-09-18 · accepted** · *`meridian_client/replay.py`, D-125, Stage 13*

`python -m meridian_client.replay` puts one recording through the real executor, the real capture folder, the real decoder program and the real outcome rules, and writes the MSP 0.3 body a station would have queued. It is how a decoder wrapper and an SNR threshold are validated against a pass someone already has, before a station is trusted to report from them.

**It has no path to the platform.** No transport and no upload queue are imported, so "it never submits" is a property of the module rather than a promise about a run, and a test asserts it against the source.

**It may run on a real station's configuration**, since nothing can reach the platform from it, and the body names the recording it replayed (D-125). The station's own state directory is untouched: the capture folder is a temporary one unless `--work-dir` says otherwise.

**A refused pass is still a body.** A recording tuned to another satellite prints a `not_attempted` observation and exits 0 — that is what the station would have sent, and seeing it is the point of the tool.

**`python -m meridian_client.station` runs a registered station** from the same configuration, and **never registers**: registration consumes an invite and mints the only copy of a bearer token (D-023), so a station that registered itself whenever its state directory looked empty would burn an invite on every wiped disk. A station with no credentials says so and stops.

---

## D-129 — Two tiers: the platform may run in a cloud, the station keeps its own record

**2026-09-18 · accepted** · *`ARCHITECTURE.md` Deployment, `EVALUATION.md` §12, Stage 33*

Everything runs on one machine today: Postgres, the API, the jobs process and the tunnel, on the Pi's NVMe (D-033, Stage 12). Archive processing, model training and public service have appetites a Pi does not, and the machine that must never stop receiving is a poor place to run a training job.

**The deployment may have two tiers.** A **station tier** — the Pi, the station client, the receiver and the decoder — and a **platform tier**, which may run on the same Pi or on a cloud host. Which tier the platform sits in is a deployment choice, not an architectural one: the same image, the same compose file, the same schema.

**The station keeps the authoritative local record of its own receptions.** A reception exists first on the station — its capture folder, its manifest, its upload queue — and the platform's row is a copy of something the station already holds. This is not a new mechanism; it is the durable queue the client already has, named as the authority it already is.

**Reconciliation after a disconnection is resend, not merge.** The station re-sends what the platform has not acknowledged, and the platform's ingest is idempotent by construction: the observation identifier is derived (D-027), revisions are append-only (D-015), and a replayed report is a no-op rather than a duplicate. Nothing has to decide which of two copies is newer, because only one side ever authors a reception.

**Authority is split, and neither side overwrites the other.** The station is authoritative for what it received; the platform is authoritative for what it assigned. A station that holds a reception for an assignment the platform never issued keeps it and reports it — that is evidence of a scheduling fault, not something to discard.

**The independence test is unchanged** and now has two drills that are run rather than asserted (`EVALUATION.md` §12): the station keeps receiving, recording and decoding with the cloud unreachable, and the dashboard serves its last snapshot with the network down.

*Rejected: making the cloud tier the system of record.* It would put an unfunded host that may expire (D-130) on the path between a pass and the record of it, which is precisely the runtime dependency `CLAUDE.md`'s independence test forbids. *Rejected: a station-side database replica.* The queue already survives a disconnection; a replica adds a merge problem the split above is designed not to have.

Which host, on whose account, and how long a station retains an unacknowledged reception are open (D-135).

---

## D-130 — No success criterion may depend on a cloud host

**2026-09-18 · accepted** · *`PROJECT.md` §9, §17; `EVALUATION.md` §12; every roadmap completion gate*

Cloud hosting here is funded by student credit. It may expire before submission, and it is not something the project can renew.

**Nothing assessed depends on it.** No success criterion, no demonstration step and no roadmap completion gate may require a cloud host to be reachable. SC-1 to SC-10 are unchanged by D-129 and every one of them is measurable on a single machine. A stage whose gate can only be met in a cloud is a stage written wrongly, and is rewritten rather than excused.

**The single-machine path stays supported and tested.** `docker compose up` on a clean machine producing a working platform in under ten minutes remains the deployment of record, and CI keeps measuring it on every pull request. The cloud tier is an addition that must never become the only path that works.

**If the credit expires, what is lost is throughput** — larger backfills, faster training, a public dashboard that does not share the Pi — and no claim, no measurement and no deliverable. That is the whole point of writing it down before the credit is spent rather than after it lapses.

*Rejected: budgeting for paid hosting.* §17 requests hardware and nothing recurring; a recurring cost that outlives the credit is a liability three students carry personally. *Rejected: quietly relying on it and hoping.* The failure mode is discovering in week 26 that the demonstration needs a host nobody can pay for.

---

## D-131 — Published environmental and space-weather data are prediction features, not only presentation

**2026-09-18 · accepted** · *`platform/prediction`, `EVALUATION.md` §2, §3; Stage 31*

Two physical effects the system currently cannot see, both of which it already has the measurements to be confused by:

- **Geomagnetic disturbance affects signals propagating through the ionosphere.** At 137 MHz the ionosphere is largely transparent, but a disturbed ionosphere raises absorption and scintillation, which moves the noise floor and the decode rate for reasons that have nothing to do with the station, the geometry or the element set. Without a disturbance index those passes look like station degradation — exactly the conclusion the receive-chain watch (module 15) exists to draw, and exactly the false alarm it must not raise.
- **Cloud cover explains an unusable image after a clean decode.** The frames arrived, the checksum passed, and the scene underneath was overcast. The reception verdict (module 13) is a probability that a reception is *usable*, so it has to be able to separate "the link failed" from "the link worked and the sky was white".

**Decision.** Published geomagnetic and solar activity indices, and local atmospheric conditions, become candidate features of the prediction module and candidate inputs to the reception verdict.

**Candidate, not committed.** They enter the ablation as one named feature group and either earn their place or do not. The group's contribution is isolated by a leave-one-group-out run against configuration D, reported beside the four configurations (`EVALUATION.md` §3). SC-1 is still measured as D − B and does not move.

**They are features, never gates.** A missing index never blocks scheduling or reception, and no pass is ever skipped because a forecast said cloudy. Skipping on a forecast would make an external service a runtime dependency, and it would poison the archive by never observing the passes the model most needs to learn from (`EVALUATION.md` §4).

**A feature value is the one published before the pass**, never a later revision or a reanalysis — the same temporal rule element-set age already obeys, for the same reason: a model that reads the corrected value has read the future.

*Rejected: using them only to decorate the dashboard.* The information is about whether reception worked, which is what this project measures; leaving it in the presentation layer would be discarding a signal we had already paid the ingest cost for.

---

## D-132 — Ingest widens beyond imagery, with its provenance rules unchanged

**2026-09-18 · accepted** · *`ingest/`, Stage 14, Stage 31*

Stage 14 was written for external *observation archives* — other people's receptions of the same satellites. D-131 needs environmental and space-weather products too, which are a different kind of data arriving through the same pipe.

**One ingest subsystem, wider scope.** The adapter contract is unchanged: download into immutable raw storage, validate and hash, normalise separately, load, never overwrite the original, and never become required by the operational scheduler.

**Every provenance field stays required** for every source, whatever it carries: source name, original identifier, retrieval timestamp, source version, licence, checksum and transformation version. Widening what we ingest does not relax what we record about it — a widened scope is exactly when provenance discipline usually slips.

**The snapshot requirement stays.** Prediction and evaluation read immutable snapshots (Stage 15), never a live feed, so that every published number is regenerable from a snapshot, a configuration and a seed (`CLAUDE.md` rule 8). A feature computed from whatever an endpoint returned that afternoon is not a measurement anyone can check.

**Access constraints are per source and recorded per source.** Some sources need a free key and count requests against it; some need registration before a download is permitted; at least one needs no key at all. Each adapter records which it is, and keys are secrets — never committed, per `GIT-WORKFLOW.md` Rule 4.

**Classes of source are named here; vendors are not.** A class is what the decision rests on, and naming a company in this file would bind an architectural decision to a commercial relationship that may not outlast the project. The candidate sources are named where they can change without a decision changing: `ATTRIBUTION.md` and Stage 31.

*Rejected: a second ingest subsystem for environmental data.* Same obligations, same failure modes, same rate limits; two of them would mean two provenance implementations and one of them rotting.

---

## D-133 — A styled map tile is a visualisation, never a measurement

**2026-09-18 · accepted** · *`ingest/`, `dashboard`, Stages 31 and 32*

Near-real-time imagery and derived products are published both as data and as pre-rendered map tiles. The tiles are far easier to obtain, and sampling a pixel from one is a tempting way to get a number.

**No derived number may be computed by sampling a rendered tile.** Numbers come from the corresponding data product. Tiles are for display.

A tile has been through a colour map, a projection, resampling and lossy compression, all chosen for legibility rather than fidelity. A pixel read back is a fact about the rendering, not about the scene — several physical values map to one colour, and the styling can change upstream without notice or version. Such a number is also not regenerable (`CLAUDE.md` rule 8): re-fetching the tile a month later can give a different answer with nothing in our record to explain it.

**Consequence.** A tile may appear on the dashboard, in a report and in the demonstration. It may not appear in a prediction feature, in a verdict input, or in an evidence dataset record as anything other than a reference to the tile.

*Rejected: calibrating against the published palette.* It can be made to work and it produces a number whose error budget nobody can state — the worst of both, because it looks like a measurement.

---

## D-134 — Every ingested source is credited, with its licence and its terms

**2026-09-18 · accepted** · *`ATTRIBUTION.md`, Stage 14, Stage 31*

`ATTRIBUTION.md` covers code and approaches we read. Ingested data is neither, and it arrives with obligations of its own.

**Each source class gets an entry** naming the source, its licence and its terms of use, and **the entry lands before the first retrieval**, not after. This is Rule 5 of `GIT-WORKFLOW.md` — attribution in the same commit as the work — extended from code we read to data we take.

**Terms are recorded as well as licence**, because data terms routinely constrain redistribution independently of any licence label, and that constraint decides what the evidence dataset (module 17, D-104) may contain and republish. A source we may compute from is not automatically a source we may republish.

**Consequence for exports.** A source whose terms forbid redistribution can still be used as a feature input; its records are then referenced in an export by identifier and checksum rather than bundled — the same treatment products already get (D-104).

*Rejected: a licences section in the ingest code.* It would be read by nobody outside the module, and the file that answers "did you take this fairly?" in a viva is this one.

---

## D-138 — Ingest is a fourth distribution, and the dependency points one way

**2026-09-19 · accepted** · *`ingest/src/meridian_ingest/`, root `pyproject.toml`, `deploy/Dockerfile`, Stage 14*

`CLAUDE.md`'s layout has placed `ingest/` at the top level beside `platform/` since Stage 0, and D-012 makes each such root a distribution. Stage 14 is where that stops being a plan.

**`meridian-ingest` is a fourth workspace member.** It depends on `meridian`; `meridian` never depends on it. The arrow is one-way, and it is the independence test in install form — the same argument that made the station client its own distribution, where "the client knows nothing about the database" is enforced when it installs rather than when it is reviewed. A platform that could import the archive layer would put an external archive one import away from the scheduling path.

**It is not installed into the runtime image.** `deploy/Dockerfile` syncs every workspace package, which would place the archive layer on the machine that must keep working when every archive is unreachable. The sync names the three shipped distributions instead, so a fourth arrives only on purpose.

**Its command is `meridian-ingest`, not a `meridian` subcommand.** `cli.py` imports every `cli_*` module eagerly, so a subcommand would make the platform import ingest at start-up. A separate binary also means an operator who never installs it still has a complete Meridian — the independence test visible at a shell prompt.

**What archive data may be used for**, stated narrowly because D-053 forbids comparing our totals with another network's and the temptation to do it anyway will live in these tables: fitting the simulator's outcome distributions, cross-checking a satellite our own station heard nothing from, and training and evaluation input under Stage 16's weighting. Never a runtime input to scheduling or reception (D-102), and never a published comparison.

**The completeness denominator stays ours.** Stage 16 computes what was available from our own orbit service. No table here holds an archive's own availability count and no schedule feed is normalised, so the wrong denominator is unavailable rather than merely discouraged.

*Rejected: a package inside `meridian`.* Nothing would then stop the import going the other way, and the property this whole stage rests on would be a convention instead of a fact.

---

## D-139 — Archive receptions are stored apart from our own

**2026-09-19 · accepted** · *`archive_observations`, `archive_stations`, `observations.provenance`, Stage 14*

`DATA-MODEL.md` has listed `observations.provenance ∈ {station, archive, manual}` since Stage 1, but the table cannot hold an archive reception. It is keyed `(assignment_id, revision, started_at)`, its `observation_id` is generated from `(assignment_id, revision)` (D-027), and `station_id` references our own `stations`. A reception someone else made has no Meridian assignment and no registered station, so storing one means inventing both. The enum anticipates rows the key forbids, and this entry settles it.

**Archive receptions go in `archive_observations`, the observing station in `archive_stations`.** `observations` stays the record of our own network, which keeps every reliability number computed from rows whose listening evidence we actually hold — rule 7 means nothing where no heartbeat exists.

**`observations.provenance` keeps its now-unused `archive` value.** Narrowing the CHECK means altering a constraint on a compressed hypertable, which D-119 established we may not assume is safe on TimescaleDB 2.29, and the column is published by the public API. The value is retired by a `comment on column` — catalogue-only, and safe — and in the model: `insert_observation` never sets it.

**The archive gets its own outcome vocabulary.** `no_data`, not `no_signal`: `no_signal` asserts that a station was verifiably listening and heard nothing (D-010), and we hold no heartbeat for someone else's station. Different values mean an accidental `union` of the two tables fails a CHECK instead of quietly returning a plausible number. `source_outcome` keeps the archive's own string verbatim, so every mapping stays auditable.

**No foreign key to `satellites`.** An FK would force the load path either to drop receptions for objects we do not track — a second selection filter stacked invisibly on the archive's own — or to insert into `satellites`, which would let an external archive decide what pass generation propagates. `satellite_key` is canonical text with its kind, joined at read time; coverage is reported as a number, never applied as a filter.

**Neither table carries `simulated`.** `element_sets` is the documented exception the conformance test already names, exempt because its provenance lives in `source`; these are the same case, and here the table name is the label. The obligation that replaces the column is that archive rows are never pooled with `observations`.

*Rejected: a synthetic `stations` row per archive station.* It would fill the registry with stations that never registered, never held a token and never sent a heartbeat, and every query that counts stations would have to learn to exclude them.

---

## D-140 — The provenance tables are created once, at Stage 14

**2026-09-19 · accepted** · *`DATA-MODEL.md` Ingest and regional tables, migration 0016, Stages 14 and 31*

`DATA-MODEL.md` introduced `ingest_sources` and `ingest_records` as planned for Stages 31 and 32 (D-132). Stage 14 needs both first, and two tables with one purpose would be the second set of provenance conventions D-132 exists to prevent.

**Stage 14 creates them; Stage 31 uses them unchanged**, adding its own sample tables against them. `environment_samples`, `areas_of_interest` and `area_series` remain Stage 31's and 32's.

**`transformation_version` moves off `ingest_records`.** Retrieval and transformation are separate events: re-normalising one artefact under a new normaliser would have to overwrite that column, which is the one thing an append-only arrival forbids. It belongs on the normalised row — `archive_observations` here, `environment_samples` later — where a second normalisation appends rather than rewrites.

**`ingest_sources` is insert-only, and changed terms mean a new `source_id`.** Stored records keep pointing at the terms they actually arrived under. `licence`, `terms_url` and `attribution_entry` are `not null` and non-empty, which puts D-134 in the database rather than in a reviewer's memory: no source can be registered without its terms written down, so no record can exist that arrived under terms nobody recorded.

*Rejected: leaving the tuples to Stage 31.* Migrations are forward-only (`GIT-WORKFLOW.md` Rule 9), so a column the archive path needs costs less now than as a retrofit onto a table that already holds rows.

---

## D-141 — The raw store is immutable, and no remote string becomes a filename

**2026-09-19 · accepted** · *`meridian_ingest/raw_store.py`, `data/ingest/raw/`, Stage 14*

Stage 14's completion gate is that a snapshot is downloaded once and then normalised repeatedly with no network. That makes the raw store the thing the gate rests on, and the one artefact in this system that cannot be recreated without going back to the source.

**The layout is `<raw root>/<source id>/<retrieved at>-<hash prefix>/`**, holding the bytes exactly as received and a manifest of the provenance fields beside them. `source_id` is ours and pattern-checked, and the directory name is our timestamp and our checksum: **no string from a remote source is ever a path element**. `original_identifier` lives in the manifest and the database only. That is `capture_folder.py`'s lesson applied by construction instead of by validation.

**Publication is a directory rename.** Bytes stream into a scratch directory, hashed as they arrive and re-hashed from disk afterwards — the stream proves the wire, the re-read proves the disk — and the manifest is written inside before the directory is renamed into place (D-068). A rename onto a non-empty directory fails, so immutability is the filesystem's rather than ours, and a crash leaves a scratch directory with no manifest, which is never mistaken for a record. The unit of publication is the directory, because half of one would be a manifest describing bytes that are not there.

**A differing re-fetch is a new row**, linked from the one it replaces by `superseded_by` in the same transaction; an identical re-fetch conflicts on `(source_id, original_identifier, sha256)` and returns the existing id. Filling a column that was null is the only update in the whole write path.

**A source that goes back to an earlier version is reported, not re-linked.** If A is superseded by B and a later fetch returns A's bytes again, that fetch conflicts onto A's row: nothing is inserted, its raw directory has no row of its own, and `superseded_by` goes on naming B. Re-linking would mean rewriting a filled column. `meridian-ingest load` names each such artefact instead, and nothing reads supersession before Stage 16, which settles what "current" means if it needs to (found auditing Stage 14, 2026-09-23).

**The raw store is outside the database backup.** `deploy/tools/backup.py` dumps Postgres; this tree is not in it. The runbook says so, and the backup names the root it did not take, rather than leaving an operator to find out after losing the one thing the gate depends on.

*Rejected: raw bytes in the database.* The gate wants a tree an operator can copy to a laptop, and a multi-megabyte artefact per row slows every dump for something never queried by content.

---

## D-142 — No test reaches the network, and the first adapter is ours

**2026-09-19 · accepted** · *`tests/unit/test_ingest_gate.py`, `meridian_ingest/adapters/`, Stage 14*

A stage whose input we do not control is the one place a test suite quietly acquires a dependency on somebody else's uptime.

**Adapters are exercised against recorded fixtures, and a live fetch is a manual command an operator runs.** CI never opens a socket to a source. The gate test installs a guard over `socket.socket` itself rather than over module bindings, so a module that imported the name earlier still gets an object that raises — and it carries a positive control asserting that a connection attempt really does fail. Without that control the gate can pass because the guard is inert, which is the worst failure available, because it looks like success.

**Normalisation cannot reach a network, by signature.** It takes bytes and their manifest, read from the raw store: no client, no adapter, no URL, no clock. There is no argument through which it could fetch anything, which is what makes the gate provable rather than asserted.

**The first adapter is fixture-backed, over synthetic artefacts we author.** A recorded fixture from a real source would be that source's data committed to a public repository, which is exactly the redistribution question D-136 leaves open. The real source is adopted when its licence and terms are recorded (D-134), and `fetch` refuses, before opening a socket, if the source's attribution entry is missing.

*Rejected: recording a fixture from a real archive now.* It would settle D-136 by accident, in a commit about test plumbing.

---

## D-143 — A snapshot is exported once, and labelled offline as often as needed

**2026-09-23 · accepted** · *`meridian.datasets`, `meridian snapshot`, Stage 15*

Stage 15's gate is that the same raw snapshot and the same transformation configuration always produce the same evaluation dataset hash. That sentence has two inputs, and this entry makes them two steps — Stage 14's fetch-then-normalise split, applied to our own tables.

**`meridian snapshot export` is the only step that reads the database.** It reads every table it needs inside one `REPEATABLE READ, READ ONLY` transaction, so all of them are read at the same instant, and writes them to an immutable directory: the **raw snapshot**.

**`meridian snapshot label` is a pure function** of a raw snapshot and a labelling configuration file. It opens no database, reads no clock and reaches no network, and it writes the **evaluation dataset**. The gate is proven on this step, and it can be — nothing it could read differs between two runs.

**`as_of` is the export transaction's own time, and cannot be chosen.** Several columns are current state rather than history: `assignments.state` moves from `issued` to `expired` by reconciliation, `stations.token_revoked_at` is cleared when a token is reissued, and `satellite_transmitters.active` has no history at all. A snapshot "as of last Tuesday" read today would mix Tuesday's rows with today's states and present the mixture as Tuesday. An operator chooses where the snapshot starts (`--since`); where it ends is when it was taken, and the manifest says so.

**What an export cannot reconstruct is written down rather than approximated.**

- **Capabilities** have no `created_at`. They are written at registration and soft-deleted, so a capability is taken as effective from `stations.registered_at` until `deleted_at`.
- **Token revocation** is current state and is not used by any label.
- **Transmitter status** is current state. It is exported for reference and is not used as evidence that a satellite was silent in the past (D-147).

**Features are not computed here.** The raw snapshot carries element sets, priorities and capabilities so that Stage 17 can compute features from it; the evaluation dataset carries labels, keys and provenance. Putting features in Stage 15 would fix the feature set before the prediction stage has decided it.

*Rejected: labelling straight from the database.* The gate could then only be tested against a database that nobody else holds, and rule 8 asks that someone who was not in the room can regenerate the number.

---

## D-144 — Snapshots are canonical JSON Lines in content-addressed directories

**2026-09-23 · accepted** · *`meridian.datasets`, `data/datasets/`, Stage 15*

**One file per table, one row per line, rows ordered by primary key.** Each line is rendered by D-070's rules: sorted keys, no whitespace, timestamps in UTC truncated to milliseconds, arrays in stored order, and `NaN` refused. Files are uncompressed.

**Each directory has a `manifest.json`** listing every file with its sha256 and row count, the schema revision, `since` and `as_of`, the simulated and measured counts, and — for an evaluation dataset — the raw snapshot it came from, the transformation version, and the sha256 of the labelling configuration's resolved values — not of the file's bytes, so a comment or a reordered key changes no hash, and an absent file and one spelling out the defaults, which give the same labels, give the same hash. The directory's **content hash is the sha256 of the manifest's canonical bytes with `created_at` left out**, so taking the same inputs twice gives the same hash while the manifest still records when each was made.

**The hash names the directory, and the directory is sealed.** It is written under a scratch name, synced, renamed into place and made read-only — the raw store's publication (D-141). A raw snapshot is `data/datasets/snapshots/<as_of>-<hash prefix>/`, an evaluation dataset `data/datasets/evaluation/<hash prefix>/`.

**No table records them in Stage 15.** The files are what an outsider regenerates from, and Stage 30's `dataset_exports` refers to a snapshot by its hash, which needs no foreign key. That also keeps this stage free of a migration.

**A raw snapshot is outside the database backup, and cannot be retaken.** Because `as_of` cannot be chosen (D-143), a snapshot lost is a snapshot gone. `deploy/tools/backup.py` names the directory it did not take, as it does for the raw store.

*Rejected: Parquet.* It needs `pyarrow`, and its bytes are not promised to be identical across library versions — so the hash would depend on which version wrote it, which is the one property this stage exists to remove.

*Rejected: gzip.* Its header carries a modification time, so two identical exports would hash differently.

---

## D-145 — Listening evidence is frozen at export by the registry, never re-derived

**2026-09-23 · accepted** · *`meridian.datasets.export`, `Registry.was_listening`, Stage 15*

Rule 7 makes `Registry.was_listening()` the only authority on whether a station was listening, and `ARCHITECTURE.md` says to encode that in one place and never duplicate it. The labeller runs with no database (D-143), so it cannot call the registry.

**The export calls it, and stores the answer.** For every scheduled assignment whose window has closed, the export asks the registry with the assignment's own station, satellite, frequency, mode and window, and writes `listening_confirmed` beside the assignment in `listening.jsonl`. The heartbeats overlapping each window are exported as well, so the answer can be checked by hand.

**The labeller only reads that column.** It contains no Doppler tolerance, no heartbeat matching and no window arithmetic, so a change to what "listening" means is made in the registry and reaches the next snapshot, and cannot drift apart from it.

*Rejected: re-implementing the check over exported heartbeats.* It would put the definition of a miss in two places, which is the failure rule 7 exists to prevent.

---

## D-146 — What a pass is labelled, and in what order the rules apply

**2026-09-23 · accepted** · *`meridian.datasets.labels`, Stage 15*

**The unit is a geometrically available pass** — one `passes` row, which is already per station and satellite. A pass exists whether or not anyone scheduled it, and that is what Stage 16's completeness ratio divides by.

*Amended by D-148:* the unit is a **physical** pass — every prediction of one rise, grouped — because D-063 keeps several `passes` rows for one pass, and labelling each row on its own counted the unscheduled predictions as passes nobody took.

**Several assignments can share a pass.** `assignment_decision_unique` is `(pass_id, model_config)`, because configurations A and B are scheduled over the same horizon to be compared. The reception is physical, not per configuration, so the evidence is pooled:

- `scheduled_by` lists, sorted, every configuration that scheduled the pass;
- the observation used is the latest revision (by `submitted_at`, up to `as_of`) across the pass's assignments, and where more than one assignment reported, the most informative outcome wins in the order `decoded`, `signal_no_decode`, `no_signal`, `aborted`, `not_attempted`;
- listening is confirmed if it is confirmed for any of them.

**The first rule that matches decides:**

| # | Condition | Result |
|---|---|---|
| 1 | The pass's window ends after `as_of − settle_margin` | excluded: `report_window_open` |
| 2 | No assignment scheduled it | excluded: `not_scheduled` |
| 3 | Every scheduled assignment is `expired` and none reported | `assignment_declined` |
| 4 | The observation is `decoded` | `successful_reception` |
| 5 | The observation is `signal_no_decode` | `signal_no_decode` |
| 6 | The observation is `aborted` or `not_attempted` | `station_unavailable` |
| 7 | No signal or no observation, and no heartbeat at all overlaps the window | `station_unavailable` |
| 8 | No signal or no observation, heartbeats exist, and listening is not confirmed | `station_not_confirmed_listening` |
| 9 | No signal or no observation, and listening is confirmed | `confirmed_miss`, `satellite_silent` or `satellite_state_indeterminate`, by D-147 |

`satellite_silent` and `satellite_state_indeterminate` are also excluded from yield scoring, with the reason `satellite_silent` or `satellite_state_indeterminate`, and counted apart (`EVALUATION.md` §5).

**The settle margin is 24 hours by default** and is set in the labelling configuration, which the manifest hashes. A station holds unsent observations in a durable queue across an outage; without a margin, a report still on its way would be labelled as absence.

**`simulated` is a column on every row, not a label.** The roadmap lists "simulated observation" beside the outcomes. Making it a label would hide what the simulated pass actually did, and leave the simulator unable to exercise the labeller that its runs exist to test. Every row carries `simulated`; a simulated row also carries the exclusion reason `simulated`, so no training or evaluation code can use it by default (D-078); and the manifest counts the two populations apart. Rule 5 is met at this layer by the column, as it is at every other.

**"Cancelled or revoked" becomes `assignment_declined`.** The schema has no cancelled or revoked assignment. `expired` is the decline case (`DATA-MODEL.md`), and token revocation is current state that cannot be dated (D-143). A label named for events the data cannot record would always be empty, or wrong.

**Each labelled row carries** the pass and station keys, `label` or `exclusion_reason`, the observation's own `outcome` verbatim as `source_outcome`, `listening_confirmed`, `scheduled_by` and `simulated`. Stages 26 and 27 reuse these label names where they overlap rather than coining new ones.

*Rejected: a row per assignment.* Configurations A and B would each claim the same reception, and every count drawn from the dataset would double it.

---

## D-147 — A satellite is called silent only on contemporaneous evidence

**2026-09-23 · accepted** · *`meridian.datasets.labels`, `EVALUATION.md` §5, Stage 15*

A station confirmed listening that heard nothing has either missed the pass or been listening to a satellite that was not transmitting. `EVALUATION.md` §5 says to tell them apart by cross-checking contemporaneous observations of the same satellite elsewhere, and to mark the rest `indeterminate`.

**The evidence is every reception of the same satellite within ± 12 hours of the pass**, from two places:

- our own observations at any station, including this one's other passes, from the same population as the pass — a simulated reception is never evidence about a measured pass, nor the other way round;
- archive receptions, matched when their `satellite_key_kind` is `norad` and the key, which ingest stores as `norad:<number>`, equals our `satellite_id`, and used only for measured passes, since an archive describes the real sky.

**The decision:**

- any contemporaneous reception with a signal (`decoded` or `signal_no_decode`, in either vocabulary) → `confirmed_miss`. The satellite was transmitting; this station did not hear it.
- otherwise, at least **two** contemporaneous attempts that heard nothing — our own `no_signal` with listening confirmed, or archive `no_data` — → `satellite_silent`.
- otherwise → `satellite_state_indeterminate`, and the manifest reports what fraction of confirmed-listening absences that is.

**The window and the count are configuration**, hashed into the manifest with the rest.

**Archive data is read here and not at runtime.** D-102 keeps loss diagnosis on our own evidence because diagnosis runs live. Labelling is offline, reads a frozen snapshot, and produces training input — which is what `CLAUDE.md` says external data is for.

*Rejected: `satellite_transmitters.active`.* It is today's status, applied to every past pass, and it cannot be null, so "unknown" could never be reached.

*Rejected: calling every confirmed-listening absence a miss.* With one physical station and sparse archives, a dormant satellite would appear as the station failing, and every reliability figure downstream would inherit it.

---

## D-148 — The unit is a physical pass, not a prediction of one

**2026-09-23 · accepted** · *`meridian.datasets.physical_passes`, `meridian.datasets.labels`, Stage 16; amends D-146*

D-063 keeps every prediction: a newer element set that predicts the same rise is a second `passes` row, on purpose. Its consequence was written down and left owed: *the completeness denominator must count distinct physical passes, not rows.* D-146 then made the label unit "one `passes` row". The two disagree, and in the direction that hides the error. A pass predicted twice and scheduled once gives one scheduled row and one `not_scheduled` row, and completeness comes out about half what it really is. Stage 15's tests did not see this, because every fixture pass had one prediction.

**Predictions of the same station and satellite whose `[aos, los)` windows overlap are one physical pass.** Overlap is the test rather than equal `aos`, because two element sets disagree on acquisition by seconds and neither of them is the true one. Grouping is transitive: A overlaps B and B overlaps C makes one pass. It runs in `(station, satellite, aos, pass_id)` order, so it has one answer.

**The representative prediction is the newest one available before the pass.** Of the members whose `computed_at` is not after the group's earliest `aos`, it is the one whose element-set epoch is latest, with ties broken by the lowest `pass_id`. Where every member was computed after the rise, the one computed first is used. Availability is judged by when the prediction was made, not by its elements' epoch: element sets are published hours after their epoch, so an epoch before the pass can still be knowledge from after it. The representative's geometry is what completeness and the propensity see, so neither can use elements from after the pass (rule 6, applied to features as well as splits).

**A rise is in a snapshot whole or not at all.** Two predictions of one rise can fall either side of `--since` by seconds. Export therefore reads every prediction whose window ends after `since`, and labelling keeps a physical pass only if it rises at or after `since`. Scoping by `aos` alone would export the later prediction without the earlier, scheduled one, and label the rise `not_scheduled` at every snapshot boundary.

**Evidence is pooled across members**, exactly as D-146 already pools it across configurations. Assignments to any member schedule the physical pass; the most informative latest report wins; listening is confirmed if confirmed for any member. Each labelled row lists its members as `pass_ids`, sorted, and keys on the representative's `pass_id`.

**`TRANSFORMATION_VERSION` becomes `labels-2`.** A dataset labelled per row can never share a hash with one labelled per physical pass (D-144).

*Rejected: counting distinct `(station, satellite, aos)`.* Two element sets predict acquisitions seconds apart, so this counts the same pass twice. It is the bug moved rather than fixed.

*Rejected: keeping only the latest prediction at pass generation.* D-063 rejected rewriting `passes`, because the prediction history is the uncertainty model's input.

---

## D-149 — What completeness divides, and by what

**2026-09-23 · accepted** · *`meridian.datasets.completeness`, `EVALUATION.md` §4.1, Stage 16*

`EVALUATION.md` §4.1 writes completeness as observed ÷ geometrically available, per station-day. Each term needs a definition before it can be computed the same way twice.

**A station-day is the UTC date of the physical pass's `aos`.** Local days would put a station's day boundary in a different place for every longitude and move with daylight saving. A pass that crosses midnight belongs to the day it rises in, as D-059 assigns a pass to the horizon it rises in.

**Available and eligible means all of these hold:**

- the pass is measured, not simulated (D-078): the two populations are never in one ratio;
- its report window has settled (D-146 rule 1): a pass still in its margin is neither observed nor missed yet;
- it is not `satellite_silent` (D-147): a satellite that was not transmitting offered no opportunity.

`satellite_state_indeterminate` stays in the denominator, because not knowing is not evidence that the opportunity was absent.

**Observed means the historical policy attempted it,** not that it succeeded and not that its label is usable:

- for our stations, some assignment of the pass received a report, or the registry confirmed the station was listening. A confirmed silence with no report is an attempt that heard nothing (rule 7), and it is labelled `confirmed_miss` or a satellite state; counting it as unattempted would score a miss the policy was never credited with trying;
- for an archive station, one of its receptions matches the pass (D-150).

Completeness measures the selection. Whether an attempted pass carries a usable yield label is a separate number, reported beside it and never folded into it. Folding it in would make a station that attempts everything but loses a report look like a station that chose not to attempt.

**Our stations and archive stations are reported apart.** They are separate populations with separate policies, and D-053 forbids putting our totals beside another network's.

---

## D-150 — The archive denominator is computed by us and frozen at export

**2026-09-23 · accepted** · *`meridian.datasets.archive_passes`, `meridian.datasets.export`, Stage 16*

D-138 says the denominator stays ours: nothing here trusts an archive's own count of what was available. So Stage 16 computes the passes each archive station could have received, with our orbit service and our element sets.

**It is computed at export and written as `archive_passes.jsonl`.** Propagation is floating-point work in a C extension. Freezing its answer once keeps labelling a function of files, which is D-145's reasoning applied to geometry instead of listening. The labeller's hash then depends on the bytes in the snapshot, not on the orbit library giving identical floats on every machine. The element set used for each satellite is the one current at the start of each UTC day in scope, which is how pass generation chooses one.

**A station's first search starts an hour early.** Search keeps only passes that rise inside it, so a reception just after the span or the snapshot begins may belong to a pass that rose just before. That pass is computed so the reception can be placed, and, like our own rises before `since` (D-148), it is in no denominator.

**Capability is what the station has demonstrated.** An archive's capability description, where `capability_json` holds one at all, is in the archive's own vocabulary and is not read by anything here, so "could receive" cannot come from declared hardware. The denominator covers the satellites the station has at least one reception of in the snapshot, at or above a configured elevation floor (default 0°). This is narrower than true availability, so it favours completeness, and the decision says so here rather than letting a figure imply otherwise.

**What cannot be computed is counted, not dropped:**

- a station without a location (`denominator_inputs = 'neither'`);
- a received satellite that is not in our catalogue, or has no element set before the day.

Each gets a count in the manifest. A denominator computed over the stations that happened to publish coordinates, and reported as though it covered all of them, is the selection bias arriving by the back door (migration 0016).

**A reception matches a computed pass** when it names the same satellite and its `started_at` falls inside the pass window widened by a configured tolerance (default 120 s). The tolerance exists because the station's clock and elements are not ours.

**An archive station's days run from its first reception in scope to its last.** A day inside that span with no reception is counted as `inactive` and kept out of the distribution. With no heartbeat, we cannot tell a station that was switched off from one that chose nothing, and scoring such a day 0 would claim the second. Days outside the span are not the station's at all.

*Rejected: propagating at label time.* The gate's hash would then rest on bit-identical propagation across machines, which is a property of `sgp4` builds and not one we can promise.

---

## D-151 — Near-complete windows and how their threshold is reported

**2026-09-23 · accepted** · *`meridian.datasets.completeness`, `meridian snapshot completeness`, `EVALUATION.md` §4.1, Stage 16*

**The threshold is configuration, default `0.8`,** set in the labelling file's `[completeness]` table and hashed into the manifest like every other setting. A station-day at or above it is retained for primary evaluation.

**Every report states, with no option to leave them out:**

- the completeness distribution: count, deciles, and a histogram in tenths;
- retained and excluded station-days, by population;
- the same two counts at each sensitivity threshold, default `0.5, 0.6, 0.7, 0.8, 0.9`.

A station-day with no eligible pass has no ratio. It is counted as `empty` and appears in no distribution.

The sensitivity table is what lets a reader judge the threshold rather than trust it. A result that holds at 0.8 and collapses at 0.7 is a different finding from one that holds at both.

---

## D-152 — The propensity is binned, and never sees an outcome

**2026-09-23 · accepted** · *`meridian.datasets.propensity`, `EVALUATION.md` §4.2, Stage 16*

`EVALUATION.md` §4.2 asks for P(observed | pass features) under the historical policy. Stage 17 owns the numerical dependencies, and a propensity fitted here has to be one a viva can read by eye. So it is estimated by counting.

**The estimate is observed ÷ available within a cell** of station × satellite × maximum-elevation band × local-solar-hour band. The default bands are elevation 0–15, 15–30, 30–60 and 60–90°, and hours in blocks of four. Local solar hour is UTC hour plus longitude ÷ 15, so it needs no timezone database and moves with the sun rather than a government.

**A sparse cell falls back to a coarser one.** A cell with fewer than `min_cell` available passes (default 20) drops the hour band. If that is still sparse, it drops the satellite, and the coarsest level is station × elevation band. Every propensity row records the level it came from.

**The estimator never sees an outcome.** Its only inputs are pre-pass features (taken from the representative prediction, D-148) and the attempted flag. It takes no label, no report and no reception. Future outcome information therefore cannot enter by construction, and a test that changes every outcome and finds every propensity unchanged is the check.

**Our own station's policy is deterministic, and the diagnostics will say so.** The baseline scheduler takes the best pass by elevation and priority, so within a cell a pass is nearly always taken or nearly never. Propensities near 0 and 1 are a **positivity violation**: there is no counterfactual to weight toward. That is reported as the finding it is. The remedy is prospective randomisation (`EVALUATION.md` §4.3), and no weighting scheme stands in for it.

**The estimator sits behind a `PropensityModel` protocol,** so Stage 17 can add a fitted model beside it, not in place of it. The binned estimate stays as the baseline that any fitted one is compared with.

---

## D-153 — How weights are floored, and when a weighted result is unreliable

**2026-09-23 · accepted** · *`meridian.datasets.weighting`, `EVALUATION.md` §4.2, Stage 16*

**Propensities are floored at `0.05` by default** (configuration), so no weight exceeds 20, and the report counts how many were floored. A floor biases the estimate towards the unweighted one. It is preferred to an unbounded weight because it fails visibly, and the count says by how much.

**A pass in a cell with propensity 0 has no support.** Nothing like it was ever attempted, so no weight can represent it. It is excluded from the weighted estimate and counted as `unsupported`, never silently absorbed.

**Reported every time:**

- the unweighted success rate with a Wilson 95% interval;
- the Hajek-weighted success rate (weights normalised to sum to one), with a Wilson interval taken at n = ESS;
- the effective sample size, (Σw)² ÷ Σw²;
- the weight distribution: min, quartiles, max, and the floored count;
- an overlap histogram of propensity, in tenths, separately for attempted and not-attempted passes.

Success is `successful_reception` among attempted passes with a usable yield label. For an archive station, it is `decoded` among its matched receptions.

**Unreliable is a flag, not a footnote.** A weighted estimate whose ESS is below max(30, 0.1 × n) is marked `unreliable: true`. `EVALUATION.md` §4.2 says such an estimate must be labelled rather than quoted; the flag is that label, carried in the data so no report can drop it.

**At the default floor, only the 30 decides.** With every weight between 1 and 1 ÷ floor, ESS cannot fall below 4r ÷ (1 + r)² of n, where r is that ratio — about 0.18 n when r is 20. The 0.1 n clause can bind only when r exceeds about 38 — a floor below about 0.026. It stays in the rule because the floor is configuration, and a report made under a lower one must still carry the flag; `tests/unit/test_datasets_weighting.py` sets the floor to 0.01 to show that it does.

*Rejected: bootstrap intervals.* They need a seed and many resamples to say roughly what the Wilson interval at the ESS already says, and they would make the report's bytes depend on the resample count.

---

## D-154 — The completeness gate is a type

**2026-09-23 · accepted** · *`meridian.datasets.result`, `meridian.datasets.evaluation`, Stage 16*

Stage 16's gate is that every archive-derived result *automatically* includes completeness information and, where relevant, IPW diagnostics. "Automatically" rules out a report template someone remembers to fill in.

**Completeness is written into every evaluation dataset.** `meridian snapshot label` writes `station_days.jsonl` and `propensities.jsonl` beside `labels.jsonl`, and a completeness summary into the manifest. No evaluation dataset exists without them, and any result drawn from one can reach them.

**A result is an `EvaluationResult`.** It cannot be constructed without a `CompletenessSummary`, and it carries either `IpwDiagnostics` or `NotWeighted(reason)`. The reason is a stated one, such as "prospective, policy assigned by us", and never an absent field. Stage 17's figures are built as `EvaluationResult`s, so a figure without its completeness is a type error, not a review comment.

*Rejected: a check in the report writer.* It guards one output path. A notebook, a second script or a copied number would each be a path it never sees.

---

## D-155 — Fit with a library, score without one

**2026-09-24 · accepted** · *`meridian.prediction`, `platform/pyproject.toml`, `deploy/Dockerfile`, Stage 17*

Stage 17 is where the roadmap allows numerical dependencies. Fitting a model needs them. Scoring a pass does not: the scheduler of Stage 18 runs in the `jobs` service on the station's Pi, and what it needs from a fitted logistic regression is a dot product and a sigmoid.

**scikit-learn is an optional extra, `meridian[fit]`,** used by `meridian model fit` and nothing else, together with numpy, which the fitting module imports directly. The platform image does not install the extra, as it does not install `meridian-ingest` (D-138). `tests/unit/test_layout.py` checks the Dockerfile, and CI asks the built image that scikit-learn and scipy are absent. Only `meridian.prediction.fit` may import scikit-learn or scipy, in any distribution.

**numpy is already in the image, and that is not this decision's doing.** skyfield depends on it, so it has been on the Pi since Stage 5. The first draft of this entry said the image carried no numerical stack; CI's image check found numpy there on the first run, and the claim is narrowed to scikit-learn and scipy, which are the heavy part.

**Scoring is plain Python all the same,** in `meridian.prediction.score`, and no prediction module but `fit` imports numpy. `tests/unit/test_prediction_boundaries.py` checks both lines. The reason is not image size: a fitted model is a JSON file of coefficients (D-163), and a scorer that is a dot product and a sigmoid in the standard library gives the same probability from that file whichever numpy version is installed, or none. What the Pi reads is data, not a pickled object from a library it does not have.

**The model is L2-regularised logistic regression.** The roadmap asks for a simple interpretable model first, and a coefficient per feature is something a viva can read. A more complex model is a later decision, taken if the ablation shows this one leaves signal on the table.

*Rejected: scikit-learn as an ordinary dependency.* About 100 MB of scipy and scikit-learn in an image whose job is to receive, for a computation that is ten lines of Python. *Rejected: our own IRLS on numpy.* It would be an optimiser to write and test that a library already provides, which fails `CLAUDE.md`'s build-or-use test.

---

## D-156 — The training example is a labelled physical pass

**2026-09-24 · accepted** · *`meridian.prediction`, Stage 17*

**An example is one physical pass (D-148) from an evaluation dataset.** It is positive if labelled `successful_reception`, and negative if labelled `signal_no_decode` or `confirmed_miss` — the `USABLE_LABELS` of D-149. Every other label and every exclusion of D-146 stays out: a pass nobody listened for says nothing about whether it would have decoded, and a silent satellite says nothing about the station (`EVALUATION.md` §5).

**Simulated passes are refused, not filtered quietly** (D-078). A dataset whose usable examples are all simulated fails with that reason, so a model fitted on the simulator's own rule cannot be produced by accident.

**One fit, one population.** The configuration names `own` or `archive`, defaulting to `own`. The two are never pooled: they differ in what an outcome means (D-139) and in how they were selected (D-149). Every report carries that population's `EvaluationResult` (D-154), so a model's figures cannot be stated without the completeness of the data it was judged on.

**IPW sample weights are a configuration option, off by default.** Our own station's propensities are near 0 and 1 (D-152), so weighting today would mostly amplify noise; the option exists so the comparison can be made once randomised scheduling gives the weights support.

---

## D-157 — Features are point-in-time

**2026-09-24 · accepted** · *`meridian.prediction.history`, `meridian.prediction.profiles`, Stage 17*

A history feature computed with the pass's own outcome, or any later one, reads the future, and a model scored on it looks better than any deployed model can be.

**A pass's features read only outcomes that had settled before it began:** an earlier pass contributes if its `los` plus the labelling `settle_margin_s` (D-146) is at or before this pass's `aos`. The same rule governs every station-history feature and every learned profile. Heartbeats are read up to `aos` only.

**Features are a pure function of the raw snapshot and its labels.** They read no clock, no database and no network, so two runs over one dataset produce the same bytes. `tests/unit/test_prediction_features.py` changes every outcome after a pass's `aos` and finds that pass's features byte-identical; changing an earlier, settled outcome is the positive control that shows the test can fail.

*Rejected: `submitted_at` before `aos` as the cut.* It is what the platform knew, but a label also depends on listening evidence and on the satellite judgement of D-147, which are settled later. The settle margin is the rule the labels are already made under.

---

## D-158 — Pass tracks are computed at export and frozen

**2026-09-24 · accepted** · *`meridian.datasets.export`, `meridian.store.snapshot_reads`, Stage 17*

The learned profiles of D-159 need to know where in the sky a satellite was during a pass, not just where it rose and set. `passes` stores aos and los azimuth and the maximum elevation, and no track.

**Export computes each exported pass's track and writes `pass_tracks.jsonl`:** azimuth and elevation every 30 s from `aos` to `los`, from the pass's own element set. This follows D-150: labelling and fitting never propagate, so their hashes do not rest on bit-identical floating point from `sgp4` across machines, and a raw snapshot stays the whole of what they read.

**Propagation moves out of the export's transaction.** Stage 16 computed the archive passes inside the `REPEATABLE READ` snapshot, which the Stage 16 review found would hold that snapshot open for minutes as archive ingest grows. Export now reads every table, commits, and then computes both the tracks and the archive passes from the rows it read. The rows are the same either way, because they come from one snapshot; only the time the snapshot is held changes.

*Rejected: tracks computed in labelling.* It would make `label` depend on `meridian.orbit` and on float reproducibility, which D-150 kept out.

---

## D-159 — The learned environment

**2026-09-24 · accepted** · *`meridian.prediction.profiles`, `EVALUATION.md` §2, Stage 17*

Four features are learned from a station's own settled history (D-157), and each is inferred rather than declared. A declared horizon mask (D-031) is a capability; the learned profile is what the outcomes say, and the two are kept apart.

- **Horizon profile.** Per station, per 10° azimuth sector: the elevation at which signal was first detected, found by matching `first_detection_at` against the pass's track. The sector's value is a low quantile of those elevations, shrunk towards a prior of 0° by the sector's count. A pass's feature is the share of its track above the learned horizon.
- **Interference profile.** Per station, per sector and 4-hour band of local solar hour: the reported `noise_floor_dbfs`, relative to the station's median. A pass's feature is the mean over the sectors its track crosses.
- **Timing error.** The station's median of `first_detection_at` minus `aos` in recent history.
- **Element-set divergence.** The spread of `aos` across a physical pass's member predictions (D-148). It needs no history, and it is a direct reading of how much the orbit estimate moved.

**A sparse cell falls back to its prior and exposes its count**, so the model can learn how far to trust it. No profile is ever missing; it is at its prior with a count of zero.

---

## D-160 — Configurations A–D are chosen by configuration only

**2026-09-24 · accepted** · *`meridian.prediction.model_config`, `EVALUATION.md` §3, Stage 17*

**The feature groups are fixed in code, and `configuration = "A" | "B" | "C" | "D"` chooses between them.** No configuration needs a code edit, and the four run through one function.

| Configuration | Model inputs | Objective |
|---|---|---|
| A | maximum elevation | probability |
| B | as A | probability × satellite priority |
| C | our features: learned horizon, interference, element-set age and divergence, timing error, station health, per-satellite history | probability |
| D | all groups | probability |

**Priority is never a model input.** It is what an operator values, not a cause of reception, so B's probabilities are A's, and B differs from A in the value the scheduler maximises (D-066). SC-1 is measured as D − B by Stage 18 on that basis. Stage 17 reports calibration for A, C and D, and states that B's calibration is A's.

**The public-conditions group of `EVALUATION.md` §3 is a named group with no features** until Stage 31 ingests them, so D∖conditions can be run without a code change when it has something in it.

---

## D-161 — Cold start is a path, not a default

**2026-09-24 · accepted** · *`meridian.prediction.score`, `EVALUATION.md` §2, Stage 17*

**A station with fewer than `min_station_history` settled examples** (configuration) is scored by the geometry-only model, fitted alongside the configured one, and not by the configured model with its history features set to something. Every prediction carries `path` — `configured` or `geometry_fallback` — and the reason, so a report can count how many predictions came from each.

**An unseen satellite, or a station with no interference or health history,** takes each missing feature at its prior with a count of zero (D-159). It never raises and never yields NaN. The roadmap's four cases — new station, unseen satellite, missing interference, missing health — each have a test.

---

## D-162 — Temporal splits, on dates the configuration states

**2026-09-24 · accepted** · *`meridian.prediction.splits`, `EVALUATION.md` §8, Stage 17*

**The configuration names `train_until` and `validate_until`.** A pass is assigned by its `aos`: before the first is training, before the second is validation, and from there to the dataset's `as_of` is test. The test span is not read until evaluation. Every result states both dates.

**Everything learned is learned on training data only:** feature scaling, the regularisation strength, and the profiles' priors. Platt calibration is fitted on validation. **Rolling-origin folds** — each training span ending later than the one before — give the variance of the reported figures.

**No function here accepts a shuffle,** and the split takes only dates. A test shows that no example after `train_until` reaches training, whatever order the examples arrive in.

---

## D-163 — A fitted model is a published directory

**2026-09-24 · accepted** · *`meridian.prediction.fit`, `DATA-MODEL.md`, Stage 17*

**A fit publishes a directory named by its content hash,** by the same rules as a snapshot (D-144): fsync and rename, sealed read-only. It holds `model.json`: the feature list, the scaler, the coefficients, the calibration map, the geometry-only fallback model, the evaluation dataset's sha256, the configuration's sha256, the seed, and the versions of numpy and scikit-learn that fitted it.

**Coefficients are rounded to 12 significant figures before hashing.** Refitting in the same environment reproduces the same hash. Across environments a solver may differ in its last bits, so the claim there is that predictions agree within 1e-9, not that the bytes match, and the gate states that limit instead of implying more.

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

**Opened 2026-09-14 by the post-reception layer**, each waiting on the team rather than on evidence the documents already hold. D-103, opened with them, was accepted the same day for Stage 13 (D-116):

| | Question | Blocks |
|---|---|---|
| **D-100** | Which method SC-3 is measured by, after `first_detection_at` is tested on archive data | SC-3's analysis in Stage 22 |
| **D-106** | What label "usable" is, independent of the verdict's inputs | Stage 26 and SC-7 |
| **D-107** | Whether an owner's contact is held, and how §16 of `PROJECT.md` changes | Stage 29 beyond team-operated stations |
| **D-108** | Archive rows as runtime evidence; D-053 against §17; phase naming; calendar placement | — |

**Opened 2026-09-18 with the two-tier deployment and public data**, each needing the team rather than another document:

| | Question | Blocks |
|---|---|---|
| **D-135** | Which cloud host and tier, on whose account, what happens when the credit lapses, and how long a station retains an unacknowledged reception | Stage 33 |
| **D-136** | Whether any ingested source's terms permit its records to be republished inside the evidence dataset, which decides that dataset's own licence | Stage 30's licence entry |
| **D-137** | Who may register an area of interest, and whether a registration is public — it is the first record in this system that describes a place someone cares about rather than a satellite | Stage 32 |

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

**Landed 2026-09-14**, opening Stage 13 with MSP 0.3.

| Decision | Applied to |
|---|---|
| D-103 accepted, D-116 moved to Stage 13 | `MSP-SPEC.md` header, §4.4, §6, §7; `SOFTWARE-IMPLEMENTATION-ROADMAP.md` Stage 25 |
| D-117 0.3 validation rules | `MSP-SPEC.md` §4.4, §6 |
| D-118 to D-126 | — (implemented by the Stage 13 pull request) |

**Landed 2026-09-18**, widening the deployment and the data the platform may read.

| Decision | Applied to |
|---|---|
| D-129 two tiers, the station authoritative for its receptions | `ARCHITECTURE.md` Deployment and Rules; `EVALUATION.md` §12 |
| D-130 no criterion depends on a cloud host | `PROJECT.md` §9, §17; `EVALUATION.md` §12 |
| D-131 environmental and space-weather features | `PROJECT.md` §8.1; `EVALUATION.md` §2, §3 |
| D-132 widened ingest, provenance unchanged | `SOFTWARE-IMPLEMENTATION-ROADMAP.md` Stage 14, Stage 31; `DATA-MODEL.md` |
| D-133 a tile is not a measurement | `ARCHITECTURE.md` Rules; `DATA-MODEL.md`; Stages 31 and 32 |
| D-134 every ingested source is credited | `ATTRIBUTION.md` |
| Modules 18 to 20, and Stages 31 to 33 | `PROJECT.md` §5.6, §19; `SOFTWARE-IMPLEMENTATION-ROADMAP.md`; `GLOSSARY.md` |

**Landed 2026-09-21**, building the ingest subsystem and its completion gate.

| Decision | Applied to |
|---|---|
| D-138 a fourth distribution, the dependency pointing one way | `ingest/`; `deploy/Dockerfile`'s `--no-install-package meridian-ingest`; `tests/unit/test_layout.py`; `tests/unit/test_import_boundaries.py` |
| D-139 archive receptions stored apart from our own | `deploy/migrations/sql/0016_archive_ingest.sql`; `DATA-MODEL.md` |
| D-140 the provenance tables created once, at Stage 14 | 0016 and the four `meridian/store/` modules over it |
| D-141 an immutable raw store, and no remote string as a filename | `meridian_ingest/{provenance,raw_manifest,raw_layout,raw_store}.py`; `deploy/tools/backup.py`; `OPERATIONS.md` |
| D-142 no test reaches the network, and the first adapter is ours | `meridian_ingest/adapters/reference.py`; `tests/unit/test_ingest_gate.py`; `tests/integration/test_ingest_gate.py` |
| — the completion gate, and how to run it by hand | `OPERATIONS.md` § External archive ingest |

**Landed 2026-09-23**, building dataset snapshots, the labels and Stage 15's completion gate.

| Decision | Applied to |
|---|---|
| D-143 export once, label offline | `meridian/datasets/export.py` (the only step that opens a database); `meridian/datasets/evaluation.py`; `meridian/cli_snapshot.py` |
| D-144 canonical JSON Lines in content-addressed directories | `meridian/datasets/{canonical,manifest,manifest_parse,publish}.py`; `meridian/store/snapshot_reads.py`; `deploy/tools/backup.py`; `DATA-MODEL.md` |
| D-145 listening frozen at export by the registry | `meridian/datasets/export.py` (`listening.jsonl`); `meridian/datasets/labels.py` reads the answer only |
| D-146 the labels and their precedence | `meridian/datasets/labels.py`; `tests/unit/test_datasets_labels.py` |
| D-147 silence judged on contemporaneous evidence | `meridian/datasets/evidence.py`; `meridian/datasets/label_config.py`; `deploy/snapshot.toml.example` |
| — the completion gate, and how to run it by hand | `tests/unit/test_snapshot_gate.py`; `tests/integration/test_snapshot_gate.py`; `OPERATIONS.md` § Dataset snapshots |

**Landed 2026-09-23**, building completeness and selection-bias tooling and Stage 16's completion gate.

| Decision | Applied to |
|---|---|
| D-148 the physical pass is the unit, `labels-2` | `meridian/datasets/{physical_passes,pooled_evidence,labels}.py`; `DATA-MODEL.md` |
| D-149 eligible, attempted and usable, per UTC station-day | `meridian/datasets/completeness.py`; `meridian/datasets/selection.py` |
| D-150 the archive denominator, computed and frozen at export | `meridian/datasets/{archive_passes,archive_matching}.py`; `meridian/datasets/export.py`; `meridian/store/snapshot_reads.py`; `DATA-MODEL.md` |
| D-151 the threshold, the distribution and the sensitivity table | `meridian/datasets/{selection_config,completeness,result_reader,completeness_report}.py`; `deploy/snapshot.toml.example` |
| D-152 a binned propensity that never sees an outcome | `meridian/datasets/propensity.py`; `tests/unit/test_datasets_boundaries.py` |
| D-153 the floor, support, ESS and the `unreliable` flag | `meridian/datasets/weighting.py`; `deploy/snapshot.toml.example` |
| D-154 the gate is a type | `meridian/datasets/{result,selection,evaluation}.py`; the manifest's optional `summary` in `meridian/datasets/manifest.py`; `meridian/cli_snapshot.py` |
| — the completion gate, and how to run it by hand | `tests/unit/test_completeness_gate.py`; `OPERATIONS.md` § Dataset snapshots |

**The raw store is the first thing in this system that a database backup does not hold.** `deploy/tools/backup.py` dumps Postgres; retrieved artefacts are on disk, outside it, and cannot be recreated without going back to a source that may have withdrawn them. The tool now names that path on every run rather than leaving the gap to be discovered at restore time.

**Migrations were amended in place rather than patched.** `GIT-WORKFLOW.md` Rule 9 protects *merged* migrations; `deploy/migrations/` was still untracked when D-023 through D-035 landed, so 0002, 0005 and 0006 were drafts, not history. A 0007 that patched a 0006 nobody had ever applied would have been a worse artefact to defend than one readable file per table. From the first commit of `deploy/migrations/`, Rule 9 binds normally — and that commit has not happened yet at the time D-034 amends `0002_stations.sql`.

**On the joint review.** `MSP-SPEC.md` required a joint review by all three team members before Phase 1 implementation began. That review did not take place as a meeting. D-012 through D-022 were written instead: every gap the review would have been convened to find is recorded above with its reasoning and its rejected alternative, and the specification is frozen at 0.1 by that written record.

This is a deliberate trade — a written decision log is more durable evidence than a meeting nobody minuted, and Phase 1 has a hard week-15 downstream gate. It is recorded here rather than quietly dropped, because the requirement is written into the specification and anyone reading the repository can see it.

**On amending accepted entries.** D-023, D-024 and D-026 are amended above rather than rewritten. An entry records what was decided and why at the time it was decided; editing that away leaves a log that has never been wrong, which is not evidence of anything. The amendment notes say what the original got wrong and point at the entry that supersedes it, so a reader following a cross-reference from the specification arrives at the current rule either way.
