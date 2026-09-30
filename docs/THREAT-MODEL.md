# Threat model

What Meridian protects, from whom, where the trust boundaries are, and which line of code or which decision answers each threat. Written for Stage 23 (D-200) and kept current by every change that moves a mitigation: a commit that closes, weakens or moves one edits its row in the same commit.

**How to read a row.** Each threat names the boundary it crosses, the mitigation, and where that mitigation lives: a module, a file in `deploy/`, or a `D-` entry in `DECISIONS.md`. Its status is one of:

- **mitigated** — the control exists and a test or a CI step exercises it;
- **partial** — a control exists and leaves a stated gap;
- **accepted** — a known risk left in place, with the reason;
- **open** — not yet addressed; the row says which part of Stage 23 addresses it.

A row with no code or decision behind it is not a mitigation, whatever it says.

---

## 1. Scope

The platform (`platform/`), the reference station client (`client/`), the simulator (`simulator/`), the dashboard (`dashboard/`) and the deployment (`deploy/`). The static site (`site/`) is covered only where it shares a hostname or a decision with the platform. External archive ingest (`ingest/`) is covered as a supply-chain and data-provenance concern; it is not in the deployment image (D-138).

Out of scope: RF-level attacks (jamming, spoofing a satellite downlink), physical access to the station, and Cloudflare's own infrastructure. The station never transmits (`CLAUDE.md` rule 4, D-126), so there is no transmit path to abuse.

## 2. Assets

| ID | Asset | Why it matters |
|---|---|---|
| A1 | **Station credentials:** bearer tokens and registration keys | A token lets its holder report as that station. A registration key lets its holder mint a new token for it (D-023, D-034). The platform stores only peppered hashes (D-017); the plaintext lives on the station, in a `0600` file (`meridian_client.credentials`). |
| A2 | **Invites** | An unconsumed invite admits one new station (D-006, D-020). Stored as a sha256 hash, unpeppered, because it is short-lived and single-use. |
| A3 | **Platform secrets:** `TOKEN_HASH_PEPPER`, `METRICS_TOKEN`, the Cloudflare tunnel token, database passwords, `GRAFANA_ADMIN_PASSWORD` | Each one opens a surface. The tunnel token lets its holder serve the public hostname. |
| A4 | **The observation store's integrity** | The system of record. Every reliability figure is computed from it, and `simulated` must never be lost or forged on the way in (`CLAUDE.md` rule 5). |
| A5 | **Station locations** | Stored at full precision; published no more precisely than the station declared (D-082, D-093). |
| A6 | **Availability during a pass** | A pass is 8–15 minutes and never repeats. Downtime during one is permanent loss, not delay. |
| A7 | **Backups, snapshots and models** | A dump holds every token hash and invite hash (§ Backup and restore in `OPERATIONS.md`). Snapshots and models are regenerable from a raw snapshot. |
| A8 | **Operator data** | Station name and operator label. No contact address is held (D-107). |

## 3. Actors

| Actor | Trusted with | Reaches |
|---|---|---|
| Anonymous internet client | nothing | the tunnel hostname: `/msp/v0/*`, `/api/v1/*`, `/`, `/healthz`, `/metrics` |
| Invite holder | one registration | `POST /msp/v0/register` |
| Registered station | its own identity and assignments | every MSP endpoint, under its bearer token |
| Compromised station | the same, used hostilely | as above; it holds a valid token |
| Operator | the deployment | the host, `docker compose`, the CLI inside the image, `deploy/.env` |
| Other user on the host | nothing | the host's process list and filesystem, subject to Unix permissions |
| Cloudflare | TLS termination and the edge rule (D-088) | every public request, in the clear |
| Dependency and image publishers | code that runs in the image | the build (`uv.lock`, `package-lock.json`, base images pinned by digest) |
| A pull request from a fork | nothing | CI, without secrets |

## 4. Trust boundaries

```
 internet ──TLS──▶ Cloudflare edge ──tunnel──▶ cloudflared ──HTTP──▶ api:8000 ──▶ db:5432
   │                  (B3)                        (B3)            (B1, B2)       (B5)
   └── station client ─────────── MSP over the same path ───────────┘
 operator ──▶ host ──▶ docker compose exec ──▶ meridian CLI ──────────────────▶ db   (B4)
 publishers ──▶ uv.lock / npm / base images ──▶ image ──▶ GHCR ──▶ the Pi           (B6)
```

- **B1 — station ↔ MSP.** Untrusted input from the internet, authenticated by bearer token after registration. `meridian.api.msp`.
- **B2 — public API and dashboard.** Unauthenticated reads. `meridian.api.public`, `meridian.api.dashboard`.
- **B3 — the tunnel.** Cloudflare terminates TLS; `cloudflared` carries requests to `api:8000` inside the compose network. The platform sees the tunnel container as the peer of every public request.
- **B4 — operator CLI.** Trusted, and reached only through the host. `meridian …` inside the image.
- **B5 — database.** Reachable only inside the compose network (`expose`, never `ports`, in `deploy/docker-compose.yml`).
- **B6 — supply chain.** Everything that becomes code in the image without being written here.

## 5. Threats and mitigations

### B1 — station ↔ MSP

| ID | Threat | Mitigation | Where | Status |
|---|---|---|---|---|
| M-1 | Registration without permission | Registration requires a single-use invite; a racing second use is refused | D-006, D-020, D-047; `registry.psycopg_registry.PsycopgRegistry.register` | mitigated |
| M-2 | A stolen or leaked invite used later | Invites expire and can be withdrawn; expiry binds bound invites too | D-046; `meridian invite revoke`; `store.invites.revoke_invite` | mitigated |
| M-3 | A stolen bearer token used to report as a station | Tokens are revocable at once, and rotatable through an invite bound to the station and its registration key | D-017, D-034, D-058; `meridian station revoke`; `store.station_tokens` | mitigated |
| M-4 | Guessing a token or a registration key | 32 random bytes each; the registration key is compared with `hmac.compare_digest`; the token is found by an index lookup of its hash, so there is no comparison to time | D-017, D-023 | mitigated |
| M-5 | A station reporting for another station's assignment | A token that names another station is `not_owner` | D-055; `meridian.api.msp.heartbeat`, `meridian.api.msp.observations` | mitigated |
| M-6 | A station claiming to be measured when it is simulated, or the reverse | `simulated` is fixed at registration and derived by the platform on every later record; a recovery that claims otherwise is refused | D-048, D-125, D-127 | mitigated |
| M-7 | A station rewriting its own history | Observations are append-only; a resubmission supersedes and the earlier report stays | D-015, D-070, D-071 | mitigated |
| M-8 | Oversized bodies, unbounded `health`, sample arrays, oversized headers | MSP §6's caps, checked from `Content-Length` before the body is read; a body of undeclared length is refused; `health`, Doppler and SNR arrays capped; the request head bounded by h11 | D-028, D-032, D-050, D-055, D-117, D-203; `meridian.api.request_limits` | mitigated |
| M-9 | Request floods against MSP, from one station or from anyone | Token buckets in the process: heartbeats and observations by the station's bearer token, every MSP request by the caller's address, which behind the tunnel is the edge's `CF-Connecting-IP`; `rate_limited` at 429 with `Retry-After` | D-202, D-206; `meridian.api.rate_limits`; `tests/msp_conformance/test_rate_limits.py` | mitigated |
| M-10 | A leaked database learning tokens or registration keys | Stored as `sha256(pepper ‖ secret)`; the pepper can be rotated without stranding a station | D-017, D-201 | mitigated |
| M-11 | Error bodies leaking SQL, connection strings or submitted tokens | One fixed two-field body; an unhandled exception answers `Internal error.` and the detail goes to the log | D-004; `meridian.api.errors` | mitigated |
| M-12 | Error *logs* leaking a token or a secret | A redacting filter on the log handler of `meridian serve` and `meridian jobs run`: bearer tokens, any field named as a secret, URL passwords, and every secret value the process loaded; the reference client logs none it holds | D-204; `meridian.log_redaction`; `tests/integration/test_log_redaction.py` | mitigated |
| M-13 | A write acknowledged before it is committed | The request's transaction commits before the response is sent | `meridian.api.dependencies.get_connection`; D-088's public run | mitigated |

### B2 — public API and dashboard

| ID | Threat | Mitigation | Where | Status |
|---|---|---|---|---|
| P-1 | Publishing a station's exact position | Coordinates rounded to the station's declared precision at serialisation, and only there; windows widened to whole minutes and angles to whole degrees, so a schedule cannot be inverted | D-082, D-093; `api.public.coordinate_privacy`, `api.public.window_privacy`; CI's boundary check in `ci.yml` | mitigated |
| P-2 | Publishing credentials, invite state or station health internals | Public models name their fields; `verify_public_surface.py` asserts no key matching `token\|invite\|health\|seed\|registration_key` | D-088; `deploy/tools/verify_public_surface.py` | mitigated |
| P-3 | Simulated data presented as measured | `simulated` on every item, and a badge in the dashboard | `CLAUDE.md` rule 5; D-086 | mitigated |
| P-4 | Publishing a number nobody computed | Endpoints with no computation answer `not_yet_computed` | D-086 | mitigated |
| P-5 | Scraping or flooding the public API | The edge rule, 50 requests per 10 s per IP on `/api/`, and the same figures per client in the process, keyed on the edge's header | D-088, D-202, D-206; `meridian.api.rate_limits` | mitigated |
| P-6 | Oversized requests to the public API | No body accepted, the query string capped at 2 KiB, and the request head bounded by h11 at 16 KiB | D-203; `meridian.api.request_limits`; `meridian.cli_serve` | mitigated |
| P-7 | Deep pagination as a denial of service | Keyset pagination, never offset | D-085 | mitigated |
| P-8 | Script injection or framing of the dashboard | A strict content-security policy with no inline script or style and one foreign origin for map tiles, `frame-ancestors 'none'` and `X-Frame-Options`, on every response; checked in Chromium with no violation | D-208; `meridian.api.security_headers`; `tests/msp_conformance/test_security_headers.py` | mitigated |
| P-9 | Another origin reading the API with a visitor's browser | No CORS middleware and no `Access-Control-Allow-*` header, pinned by a preflight and a plain request from another origin | D-091, D-208; `tests/msp_conformance/test_security_headers.py` | mitigated |
| P-10 | Process internals exposed through `/metrics` | A bearer token, and a 404 indistinguishable from an unrouted path without it | D-087; `meridian.metrics.access` | mitigated |
| P-11 | The public API publishes more than a visitor needs | Reviewed as a whole, field by field, with every field pinned by a test; one open finding, altitude | D-210; §7 below; `tests/unit/test_public_privacy.py` | partial — altitude to the metre, §7 |

### B3 — the tunnel

| ID | Threat | Mitigation | Where | Status |
|---|---|---|---|---|
| T-1 | Serving the public hostname with a stolen tunnel token | The token is a `0444` file in a `0700` directory, read through `TUNNEL_TOKEN_FILE`, never in `.env`, argv or the environment; rotated from the Cloudflare dashboard | D-114, D-201, D-206; `deploy/docker-compose.public.yml` | mitigated |
| T-2 | Bypassing the edge by reaching the API port on the host | The public deployment publishes no API port, so every outside request crosses Cloudflare, which overwrites `CF-Connecting-IP`; only that file trusts the header | D-051, D-206; `deploy/docker-compose.public.yml`; `tests/unit/test_compose_hardening.py` | mitigated |
| T-3 | Public exposure with development secrets | The platform refuses to start in public mode while any secret is `change-me` | `meridian.config._refuse_placeholder_secrets` | mitigated |
| T-4 | Cloudflare reads traffic in the clear | Accepted: TLS terminates at the edge by design, and MSP carries no secret but the bearer token, which a revocation withdraws | MSP §3 | accepted |

### B4 — operator CLI and host

| ID | Threat | Mitigation | Where | Status |
|---|---|---|---|---|
| O-1 | Secrets visible in `ps` or `docker inspect` | The tunnel token is in the environment, not argv; the platform's secrets can be read from files | D-114 | mitigated |
| O-2 | A leaked platform secret cannot be replaced safely | Each secret is a file written by a tool and read at start; the pepper overlaps with its predecessor, which verifies and re-hashes but never hashes anything new; a runbook per secret | D-201; `meridian.registry.pepper_rotation`; `deploy/tools/rotate_secret.py`; `tests/integration/test_pepper_rotation.py` | mitigated |
| O-3 | Secrets committed to the repository | `.env`, `metrics_token` and the Alertmanager secret files are gitignored; `.env.example` holds only `change-me` | `.gitignore`; GIT-WORKFLOW rule 4 | mitigated |
| O-4 | A compromised process escalating inside its container | Every service drops all capabilities, runs with `no-new-privileges` and a read-only root filesystem; the platform runs as uid 10001 and the database as uid 70 | D-206; `deploy/docker-compose.yml`; `tests/unit/test_compose_hardening.py` | mitigated |
| O-5 | A backup file read by someone who should not | Written `0600` by the nightly unit (`UMask=0077`); `backups/` and `*.dump` are gitignored and excluded from the image build | D-201, D-209; `deploy/systemd/meridian-backup.service` | mitigated |
| O-6 | Secrets baked into an image layer by a local build | `.dockerignore` excludes every secret path at any depth, checked by a test against each path the repository uses | D-201; `.dockerignore`; `tests/unit/test_layout.py` | mitigated |

### B5 — database

| ID | Threat | Mitigation | Where | Status |
|---|---|---|---|---|
| D-1 | The database reachable from outside | `expose`, never `ports` | `deploy/docker-compose.yml` | mitigated |
| D-2 | A compromised API process rewriting the schema or dropping tables | The API, the jobs process and the CLI connect as `meridian_api`, which may change rows and nothing else; only `migrate` holds the owner's password | D-207; `meridian.store.database_roles`; `tests/integration/test_database_roles.py` | partial — the owner is a superuser, which TimescaleDB requires |
| D-3 | SQL injection | Every statement is parameterised through psycopg; no SQL is built from request text | `meridian.store` | mitigated |
| D-4 | Losing the database | Nightly checksummed backups kept by a daily, weekly and monthly policy; a weekly drill restores the newest into a scratch database and checks it; an off-host copy is the operator's step | D-115, D-209; `deploy/tools/scheduled_backup.py`, `restore_drill.py`; `tests/integration/test_restore_drill.py` | partial — the off-host copy is not automated |
| D-5 | A migration failing half-way | An upgrade runs in one transaction, so a failure leaves the revision it started from; the API does not start until `migrate` succeeds; the recovery is in the runbook | D-019, D-211; `deploy/migrations/env.py`; `tests/integration/test_failure_recovery.py` | mitigated |

### B6 — supply chain

| ID | Threat | Mitigation | Where | Status |
|---|---|---|---|---|
| S-1 | A moved base image | Every base image pinned by tag and digest | D-113; `deploy/Dockerfile` | mitigated |
| S-2 | A changed dependency | `uv.lock` and `package-lock.json`, installed frozen | `deploy/Dockerfile`; `uv lock --check` in CI | mitigated |
| S-3 | A dependency or image with a known vulnerability | `pip-audit` over the whole lock, `npm audit`, and Trivy over the built image, on every change and weekly; fixable HIGH and CRITICAL fail the run | D-205; `.github/workflows/security.yml`; `deploy/Dockerfile` | mitigated |
| S-4 | Not knowing what an image contains | CycloneDX SBOMs of the Python lock, the dashboard lock and the image, kept with every run | D-205; `.github/workflows/security.yml` | mitigated |
| S-5 | Code copied from a GPL project | Read, never copy; attribution in the same commit | `CLAUDE.md` rules 2 and 3; `ATTRIBUTION.md` | mitigated |
| S-6 | External archives reachable from the deployment | `meridian-ingest` is excluded from the image | D-138; `tests/unit/test_layout.py` | mitigated |

## 6. Residual risks

Stated so that nobody has to find them.

- **A compromised station can report plausible lies about its own passes.** No protocol can tell a station that heard nothing from one that says so. Stage 25's reception evidence narrows this; it cannot close it.
- **The pepper is defence in depth, not the main defence.** Tokens and registration keys are 256-bit random values, so their hashes cannot be inverted with or without the pepper. What the pepper adds is that a database leak alone does not let an attacker confirm a guessed token offline.
- **Cloudflare is trusted** with every public request in the clear (T-4).
- **The operator is trusted.** Anyone who can run `docker compose` on the host is root there in effect.

## 7. Public API privacy review

Reviewed for Stage 23 (D-210), field by field, against one question: what does a stranger learn about a station, its operator or its site that they did not choose to publish? `tests/unit/test_public_privacy.py` pins every field below, so a field added to a public response fails the suite until this table covers it.

| Response | Discloses | Protection | Verdict |
|---|---|---|---|
| Station | `name` and `operator` verbatim, as the operator typed them at registration | a display label, not a contact; no contact is held (D-107) | accepted — MSP §4.1 does not yet tell an implementer these two are public; a spec note is owed, in its own pull request |
| Station `location` | latitude and longitude | rounded to the station's declared precision, 2 decimal places (about 1.1 km) unless it declared otherwise, at serialisation only | mitigated (D-082) |
| Station `location.alt_m` | altitude to the metre | none beyond rounding to the metre | **open** — with a terrain model, an altitude to the metre narrows a 1.1 km cell to a contour line. Rounding it to 10 m when the declared precision is coarser than 3 decimal places is proposed; it changes D-082, so it is the team's decision |
| Station capabilities | bands, modes, tracking, the declared horizon mask | the mask is what the operator chose to declare | accepted — an obstruction's bearing is a clue to the site, which the operator can see when they declare it |
| Passes, assignments, observations | when a station hears what | windows widened to whole minutes, angles to whole degrees, so a schedule cannot be inverted into an exact position | mitigated (D-093) |
| Assignment `explanation` and run fields | a decision's value and its terms (yield, frames, priority), the model's route and `model_sha256`, the passes it was weighed against, the run's status and `history_as_of`, `schedule_run_id`, `revision`, and a revocation's reason (`declined` or `offline`) | every pass named is one the passes response already publishes, and a model digest identifies a regenerable file, not a person | accepted — added by Stage 18 (D-170, D-171) and reviewed on merging Stage 23; `revoked_reason` says no more than heartbeat liveness already does |
| Reliability (`/api/v1/reliability`) | network and per-station capture rate, availability, completion and execution rates, report delays and the loss budget, as counts with intervals, and the classification's digest | debits are counted by reason and never listed (D-187), so no figure names a pass; every count is over passes and heartbeats the other responses already publish | accepted — added by Stage 20 (D-184 to D-187) and reviewed on merging Stage 23; a station's availability summarises the liveness the dashboard already shows |
| Heartbeats | state, what it holds and is listening to, clock offset, `sent_at` and `received_at` to the microsecond | no position in any field; the `health` object is never published | accepted — liveness is the point of the dashboard |
| Observations | outcome, peak SNR, provenance, `submitted_at` | no position; no raw product | accepted |
| Observation `products` | each product's kind, sha256 and size | never the station-local `uri`; a kind the station wrote is published only as a short lowercase token, else `other` (D-176) | accepted — added by Stage 19; a hash identifies a file the station holds, not a person or a place |
| Station profiles, declared (`/api/v1/stations/{id}/profiles`) | the capability's horizon mask as bins | the same mask the capabilities response publishes as declared | accepted — added by Stage 19 (D-174), and no more than the operator already chose to publish |
| Station profiles, learned | a floor per 10° of sky with the detections behind it, and the dataset and span it came from | floors to whole degrees, as D-093 publishes angles | **open** — added by Stage 19 (D-174). The operator never chose to publish it, and a skyline can in principle be matched against terrain; at 10° and whole degrees it mostly shows near obstructions. Whether a station may withhold it, as it chooses its location's precision, is the team's decision |
| Station profiles, interference | the noise floor over the station's median, by 45° of sky and 4-hour band of local solar hour, with the gains behind each cell | a tenth of a decibel; relative to the station itself, not calibrated | accepted — added by Stage 19 (D-174); a 4-hour band is too coarse to read a household's routine from, and the pattern is what loss diagnosis needs |
| Station uptime (`/api/v1/stations/{id}/uptime`) | heartbeats and listening heartbeats per hour, and the heartbeat interval | a count per hour; the heartbeats response already lists each one | accepted — added by Stage 19 (D-178) |
| Pagination cursors | the sort key of the last row served | every cursor is an id or a value the same response already publishes, never a timestamp the response widens | checked, no leak |
| Satellites, transmitters, simulator runs | public catalogue data; a run's id and station count | nothing about a person or a place | accepted |
| Every response | never a token, a registration key, an invite, `health`, a seed or a client version | the models name their fields, D-088's verifier checks from outside, and the test above checks from inside | mitigated |

**What the platform holds about operators, and for how long.** A station's name, operator label and full-precision location, which scheduling needs; the hashes of its credentials; the opaque `health` object of every heartbeat, capped at 4 KiB (D-055), which a station could fill with anything, including a hostname. Container logs hold the tunnel's address rather than a caller's, and rotate after three 10 MB files (D-114); the rate limiter holds callers' addresses in memory only (D-202). Heartbeats and observations are kept indefinitely: Stage 19 decided that raw rows are the evidence every reliability figure is regenerated from, and are never dropped on a timer (D-178). That is the largest store of operator data; Stage 33 revisits its retention across deployment tiers with measured volume, and the open D-135 covers what a station keeps.
