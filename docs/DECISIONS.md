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

## Open

Carried from `MSP-SPEC.md` §9, to be resolved before the MSP 0.1 freeze.

| | Question | Note |
|---|---|---|
| **O-1** | Product upload inline or pre-signed URL? | Blocks the `products` table design — resolve these two together, not separately |
| **O-2** | Push assignments or is heartbeat polling sufficient? | Polling works behind NAT, which most stations are. Leaning polling; D-007 assumes it |
| **O-3** | How does a station report a horizon obstruction it already knows about, without pre-empting the learned profile? | |
| **O-4** | Cap `doppler_samples` by count, or transmit as a compressed curve fit? | |

---

## Applying these

D-003 through D-008 and D-010 change `MSP-SPEC.md` and `DATA-MODEL.md`. **Those documents are not yet edited.** MSP-SPEC's closing line requires review by all three team members before implementation begins, and these decisions are the substance of that review. They land in the specification at the MSP 0.1 freeze, as one reviewed change, with the version bumped.
