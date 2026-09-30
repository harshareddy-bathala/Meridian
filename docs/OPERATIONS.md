# Operations

How to run a Meridian deployment: bring it up, admit stations, keep it scheduling, back it up, and read what it says when something goes wrong.

Every command runs from the repository root. `compose` below is shorthand for:

```bash
docker compose -f deploy/docker-compose.yml
```

Decisions this page puts into practice: D-109 to D-115, D-120 to D-128 for station reception, D-138 to D-142 for external archive ingest, D-143 to D-154 for dataset snapshots, D-155 to D-164 for models, D-165 to D-172 for scheduling, and D-200 onwards for Stage 23's hardening. The staged build order is in `SOFTWARE-IMPLEMENTATION-ROADMAP.md`.

---

## Bring-up

### First time

```bash
cp deploy/.env.example deploy/.env
cp deploy/prometheus/metrics_token.example deploy/prometheus/metrics_token
```

**The env file goes in `deploy/`, beside the compose file.** Compose reads `.env` from there, whatever directory the command runs in. A copy at the repository root is ignored unless every command passes `--env-file .env` (D-114).

**Before anything is public, replace every `change-me`:**
- `POSTGRES_PASSWORD`, `TOKEN_HASH_PEPPER`, `REGISTRATION_INVITE_TOKEN`, `METRICS_TOKEN` and `GRAFANA_ADMIN_PASSWORD` in `deploy/.env`;
- the same `METRICS_TOKEN` value in `deploy/prometheus/metrics_token`.

The platform refuses to start in public mode while any of them is still `change-me`. Running with `deploy/docker-compose.public.yml`, setting `TUNNEL_HOSTNAME`, or a non-loopback `PUBLIC_BASE_URL` puts it in public mode.

**On a real deployment, keep the secrets in files instead** (D-201). `deploy/tools/rotate_secret.py init` writes them into `deploy/secrets/`, carrying over any value already set in `deploy/.env`, and `deploy/docker-compose.secrets.yml` mounts them. Add that file to every command:

```bash
python deploy/tools/rotate_secret.py init
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.secrets.yml up -d
```

§ Rotating secrets below is how each one is replaced.

### Pull or build

| Host | Command |
|---|---|
| Raspberry Pi | `compose pull`, then `compose up -d` |
| Laptop, from this checkout | `compose up -d --build` |

The platform image is `ghcr.io/harshareddy-bathala/meridian:main`, published for amd64 and arm64 once CI passes on `main` (D-113). `MERIDIAN_IMAGE` in `deploy/.env` pins another tag, such as `sha-<commit>`.

**One-time step: the package starts private.** On GitHub, open the repository's *Packages* → *meridian* → *Package settings* → *Change visibility* → *Public*. Alternatively, on the Pi, run `docker login ghcr.io` with a token that has `read:packages`.

### Profiles

| Profile | Adds | Needs |
|---|---|---|
| *(default)* | `db`, `migrate`, `api` on `:8000`, `jobs` | `deploy/.env` |
| `metrics` | Prometheus, Alertmanager, Grafana on `:3001` | `deploy/prometheus/metrics_token` |
| `sim` | catalogue and invite seeding, a simulated fleet | nothing more |

```bash
compose --profile metrics up -d
compose ps                      # every service with a healthcheck says (healthy)
curl http://localhost:8000/healthz
```

### Going public

The public deployment is an override file, `deploy/docker-compose.public.yml`, not a profile (D-206). It adds the Cloudflare tunnel, tells the API to trust the edge's `CF-Connecting-IP` for rate limits, and **removes the API's port from the host**, because a profile can add a service but cannot take a port away. It needs Docker Compose 2.24.4 or later.

```bash
python deploy/tools/rotate_secret.py init                    # once, § Rotating secrets
python deploy/tools/rotate_secret.py set tunnel_token < token.txt
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.secrets.yml \
  -f deploy/docker-compose.public.yml --profile metrics up -d
```

With it, `curl http://localhost:8000` on the host is refused, as intended. Reach the API through the hostname, or inside the network:

```bash
compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/healthz').read())"
```

`compose` in the commands elsewhere on this page then means all three files.

**Keep Cloudflare's Rocket Loader, e-mail obfuscation and Web Analytics off for the tunnel hostname.** Each injects a script, and the dashboard's content-security policy refuses anything not served by the platform itself (D-208), so the page would load without them and log a violation in the browser's console.

### Database roles

The API, the jobs process and the CLI inside them connect as `meridian_api`, which may read and write rows and cannot change the schema; `meridian_reader` may only read; only `migrate` uses the owner (D-207). `migrate` runs `meridian db roles` after every migration, so the roles, their grants and their passwords are put right by every `up`.

- **A query by hand, read-only:** `compose exec db psql -U meridian_reader -d meridian`. Inside the database container the local socket needs no password; from another container, use `READER_DATABASE_PASSWORD`.
- **"permission denied" from the API after a manual schema change:** a table created other than by `migrate` has no grants yet. `compose run --rm migrate` grants it.

### Container hardening

Every service drops all Linux capabilities, runs with `no-new-privileges`, and has a read-only root filesystem (D-206). What each writes is a named volume or a tmpfs listed beside it in the compose file. The database runs as its own user, uid 70, from the start.

- **A service that fails with "Read-only file system"** is writing somewhere new. Give it a tmpfs, or a volume if the data must survive a restart, in the compose file, and say why beside it.
- **`compose run` for a command that writes files,** such as `meridian snapshot export`, needs a volume for its output, which § Dataset snapshots already mounts.

Bringing the whole stack up on a clean machine takes under ten minutes. CI measures it on every pull request, for the default and `metrics` profiles together.

---

## Everyday commands

The platform's CLI is in the image, so `compose exec api meridian …` runs it against the deployment's own database.

| Task | Command |
|---|---|
| Admit a station | `compose exec api meridian invite create --label <who>` — prints the invite once |
| Admit several | `compose exec api meridian invite create --label <who> --count 5 > invites.txt` |
| List invites | `compose exec api meridian invite list` |
| Recover a station that got 401 | `compose exec api meridian invite create --label <who> --for-station <station_id>` (D-034) |
| Shut a station out | `compose exec api meridian station revoke --station-id <station_id>` |
| Rotate a platform secret | `python deploy/tools/rotate_secret.py rotate <name>` — § Rotating secrets |
| Load satellites | `compose exec api meridian catalogue load --file deploy/catalogue/development.json` |
| Migration status | `compose exec api meridian db status` — exit 0 only when at head |
| Apply migrations, and re-grant the database roles | `compose run --rm migrate` |
| Check that the newest backup restores | `python deploy/tools/restore_drill.py --latest backups` |
| Generate passes now | `compose exec api meridian passes generate --from <ISO-8601 Z> --to <ISO-8601 Z>` |
| Schedule now | `compose exec jobs meridian schedule --from <ISO-8601 Z> --to <ISO-8601 Z> [--config /datasets/schedule.toml]` — A on the elevation proxy without `--config` (D-168) |
| One scheduling round now | `compose exec jobs meridian jobs run --once` |
| How recent schedule runs fared | `compose exec api meridian schedule runs [--limit 20]` — § Stored measurements and profiles |
| Pass timing error, clock-corrected | `compose exec api meridian passes timing [--station <id>]` — § Stored measurements and profiles |
| Build the horizon and interference profiles now | `compose exec jobs meridian profiles build` — the jobs service does it each round |
| Run the simulator | `compose --profile sim up -d` — `SIMULATOR_*` in `deploy/.env` set count, seed and scenario |
| Check the public surface | `python deploy/tools/verify_public_surface.py https://<hostname>` |
| Fetch and load an external archive | `uv run meridian-ingest …` — its own binary, not in the image; § External archive ingest |
| Freeze the database into a raw snapshot | `compose run --rm --no-deps … api meridian snapshot export --since <ISO-8601 Z>` — § Dataset snapshots |
| Label a raw snapshot | `uv run meridian snapshot label <dir>` — needs no database; § Dataset snapshots |
| Read a dataset's completeness and weights | `uv run meridian snapshot completeness <dataset dir>` — § Completeness and weights |
| Fit a model on a dataset | `uv run meridian model fit <dataset dir> --config model.toml` — needs the `fit` extra; § Models |
| Judge a model | `uv run meridian model evaluate <model dir>` — § Models |
| Compare the schedulers and the oracle | `uv run meridian schedule evaluate <dataset dir> --config schedule-evaluation.toml` — needs no database; prints frames per station-hour and SC-1 (D-172); see `deploy/schedule-evaluation.toml.example` |
| Reliability now | `compose exec api meridian reliability report` — § Reliability figures |
| Why a pass counted as it did | `compose exec api meridian reliability explain <assignment id>` |
| Classify settled passes now | `compose exec api meridian reliability classify` — the `jobs` service does this every round |
| Reliability from a dataset | `uv run meridian snapshot reliability <dataset dir>` — needs no database |
| Build an evaluation report | `uv run meridian report build --snapshot <raw snapshot> --config analysis/configs/evaluation.toml.example --seed <n>` — needs no database; § Evaluation reports |
| Check a report regenerates | `uv run meridian report verify <run dir>` — § Evaluation reports |

**Scheduling needs no command.** The `jobs` service generates passes, schedules them, and builds the profiles under `SCHEDULE_CONFIG` (configuration A on the elevation proxy when it is unset, D-170) every `SCHEDULE_INTERVAL_S` (default 300), over the next `SCHEDULE_HORIZON_S` (default 21600), in every deployment (D-110). Each round then expires work nobody took and classifies every pass that has settled (D-182, D-183). The commands above are for filling a horizon by hand. Every task is idempotent, so running them beside the service writes nothing twice.

---

## Station reception

How a station is configured and run, what it does with an assignment, and what it leaves on its disk (D-120 to D-128). This is the software half: **no physical receiver adapter ships yet**, and nothing here has run against a real SDR or a real decoder. It runs today with the simulated and file-replay receivers.

### Configuring a station

One TOML file says what a station is (D-127). Every table is optional, and what
is left out takes the default below. **An unknown table or key is refused by
name** rather than ignored, so a typo stops the station at start-up instead of
at the end of a pass.

```toml
[station]
base_url = "https://dash.meridian.org.in"   # default http://localhost:8000
state_dir = "/var/lib/meridian-station"     # default: "state" beside this file

[receiver]
kind = "simulated"        # or "replay"; no physical adapter ships yet (D-124)
sample_rate_hz = 1000     # the simulated receiver's rate

[decoders.lrpt]           # one table per mode; a mode with none is not attempted
argv = ["/usr/local/bin/meridian-satdump", "{recording}", "{report_path}"]
timeout_s = 900.0

[policy]
snr_threshold_db = 3.0    # what counts as a detection
minimum_coverage = 0.8    # how much of the window a capture must span

[disk]
bytes_per_second = 2048000   # the receiver's write rate, for the disk guard
margin = 1.25
reserve_bytes = 1073741824

[retention]
keep_recordings = false   # true keeps each recording after its result is sent
products_max_bytes = 2147483648   # the product store's cap; oldest evicted first
```

To replay recordings instead of receiving, name one per assignment:

```toml
[receiver]
kind = "replay"

[receiver.recordings.as_44b2]
path = "recordings/pass.cf32"     # relative to this file
sample_rate_hz = 1000
sample_format = "cf32"            # u8, s8, s16 or cf32
centre_freq_hz = 137900000        # must be within Doppler of the assignment's
gain_db = 32.8
```

**Whether the station is simulated is not in this file.** It is written into the
credentials when the station registers, and read back from there, so no edit here
can make a simulated receiver report for a station the platform records as
measuring the real sky (D-125, D-127). A station whose credentials predate that
field counts as measured, which refuses a synthetic receiver rather than
admitting one.

The state directory holds everything the station owns: `credentials.json`,
`registration_key`, `held.json`, `outbox/` and `captures/`.

### Running a station

```bash
meridian-station --config /etc/meridian/station.toml
```

Also `python -m meridian_client.station --config …`, which is what a systemd unit
or a container runs. `--ticks N` stops after N ticks, for a commissioning run.

**It does not register.** Registration consumes an invite and mints the only copy
of a bearer token (D-023), so a station with no credentials says so and stops
rather than quietly burning an invite. Admit it with `meridian invite create`
first, and register it with the invite that prints.

A revoked token stops the station with the sentence that says what to do, and
exit code 1 (D-024).

### Replaying a recording offline

```bash
meridian-replay --config station.toml \
    --assignment as_44b2.json --recording pass.cf32 --sample-rate-hz 1000
```

Puts one recording through the real pipeline — the same executor, capture folder,
decoder program and outcome rules — and writes the MSP 0.3 body a station would
have queued to stdout, or to `--out`. This is how a decoder wrapper and a
threshold are checked against a pass you already have.

**It cannot submit.** The runner has no transport at all, so nothing it produces
reaches the platform (D-128); the body names the recording it replayed. It leaves
the station's own state directory alone, and a pass it has to refuse — a
recording tuned to another satellite, say — still prints the `not_attempted`
observation the station would have sent.

`--assignment` takes one MSP §4.3 assignment as JSON, as a heartbeat response
delivered it.

### The capture folder

Each assignment the station works gets `<state>/captures/<assignment_id>/`, beside `held.json` and `outbox/`:

| File | What it is |
|---|---|
| `manifest.json` | The assignment, what was tuned and recorded, and the phase reached |
| `recording.u8` | The recording, when the station made it; a replayed file stays where the operator put it |
| `decoder_output/` | Whatever the decoder writes; emptied before every decode |
| `decode_report.json` | The decoder's report, read by the client |
| `decoder.stdout.log`, `decoder.stderr.log` | The decoder's own output |

The phases are `refused`, `capturing`, `captured`, `decoding`, `reported` and `handed_over`. **A restart resumes every folder from its phase**: an interrupted capture is decoded and reported `aborted`, a decode is run again, and a result not yet marked handed over is handed over again. Rebuilt from the same files, it is byte-identical, so the platform stores it once (D-123).

- **Recordings** are deleted once their result is handed over, unless the station keeps them. A replayed file is never deleted.
- **Folders** are pruned thirty days after hand-over.
- **An unreadable manifest** is logged and left alone for an operator. Nothing overwrites it.
- **Recordings never belong in git.** `*.u8`, `*.cf32`, `*.iq` and `captures/` are ignored.

### Decoders

Each mode maps to one command. The command is an argv, never a shell line, and may use only these placeholders:

`{recording}` `{output_dir}` `{report_path}` `{sample_rate_hz}` `{sample_format}` `{centre_freq_hz}` `{mode}`

An unknown placeholder, a format spec, or a placeholder in the program name is refused when the command is built. The decoder runs in its own session at a lower CPU priority, one decode at a time, oldest first. When its timeout expires, its whole process group is sent SIGTERM, then SIGKILL five seconds later (D-124).

**A mode with no decoder is refused before capture**, and the pass is reported `not_attempted`.

**The decoder must write Meridian's report** to `{report_path}`. A station wraps SatDump, or anything else, in a script that writes it:

```json
{
  "format": 1,
  "decoder": "satdump",
  "decoder_version": "1.2.2",
  "frames_decoded": 412,
  "frames_failed": 37,
  "first_frame_offset_s": 35.2,
  "snr": [{"offset_s": 35.0, "snr_db": 3.1}, {"offset_s": 326.0, "snr_db": 11.4}],
  "noise_floor_dbfs": -52.3
}
```

Only `format` and `decoder` are required. Offsets are seconds into the recording. Leave out anything the decoder did not measure: zero is a measurement.

The reader is strict. A report is refused, and the pass is `aborted`, if it:
- has an unknown key;
- gives a boolean or negative number as a count;
- has a number that is not finite;
- has an offset outside the recording;
- lists SNR points out of time order;
- gives a first-frame offset without at least one decoded frame.

The decoder's stderr log says why.

### What each pass is reported as

The first matching row wins (D-122):

| What happened | Reported as |
|---|---|
| The pass never started: no decoder, not enough disk, the receiver refused, or the window closed before capture | `not_attempted`, with the reason |
| The decoder failed, timed out, or wrote a report that was refused | `aborted`, with the reason |
| Capture was interrupted, or covered less than 80% of the window | `aborted`, with whatever evidence there is |
| Frames were decoded, with a detection time | `decoded` |
| Frames were decoded, with no detection time | `aborted` — no time is ever guessed |
| No frames, but SNR reached the threshold (3 dB) | `signal_no_decode` |
| Zero frames counted and nothing reached the threshold | `no_signal` |
| Anything else | `aborted` |

**The detection time** is the first decoded frame or the first SNR sample at or above the threshold, whichever came first. It is counted from the recording's first sample, and `client_notes` says which method found it. A noise floor is sent only with the receiver gain it was measured at.

### Before each capture

- **A disk check:** the estimated recording times 1.25, plus a reserve of 1 GiB, must be free, or the pass is `not_attempted`.
- **An honesty check:** the simulated and file-replay receivers do not hear the sky, and the client refuses to start with one for a station that did not register as simulated (D-125). A replay names the file it replayed in `client_notes`.

While a pass is open, the heartbeat says `listening` only while the receiver is alive. It says `degraded` if the receiver died or the pass could not be captured, and `processing` while a decode waits or runs (D-121).

---

## External archive ingest

Somebody else's observation archive, retrieved once and then read locally for good. **Nothing in the deployment depends on it.** The platform schedules, receives, decodes, monitors and reports with this subsystem uninstalled and every archive unreachable; what it produces is training input for Stage 15's snapshots and Stage 16's completeness figures, and nothing at runtime reads it (D-138).

Decisions this section puts into practice: D-133, D-134, and D-138 to D-142.

### It is not in the deployment image, deliberately

`deploy/Dockerfile` excludes `meridian-ingest` by name. Its metadata is copied in, because uv reads every workspace member to resolve the lockfile, but the package itself is never installed — so the machine that has to keep receiving when every archive is unreachable does not carry the code that talks to one (D-138). `tests/unit/test_layout.py` checks that absence, which makes putting it in the image a decision rather than a default. Run it from a checkout instead:

```bash
uv sync
uv run meridian-ingest sources
```

**There is no `meridian ingest` subcommand.** `meridian/cli.py` imports every `cli_*` module at start-up, so a subcommand would put the archive layer one import away from the scheduling path. A separate binary also means an operator who never installs this still has a complete Meridian, which is the independence test at a shell prompt.

### Settings

```bash
cp deploy/ingest.toml.example ingest.toml
```

With no settings file at all, the defaults fetch the reference archive into `data/ingest/raw` and the gate below runs. The file is looked for at `$MERIDIAN_INGEST_CONFIG`, then `./ingest.toml`, then nowhere — and a path in that variable which does not exist is an error rather than a fall-back, because running on defaults an operator believes they replaced is the failure worth avoiding.

- **`raw_root` is relative to the settings file**, not to the working directory. The example's value assumes the file stays in `deploy/`; copied to the repository root and left alone, it puts the tree *beside* the repository instead of inside it, so change it to `data/ingest/raw` there.
- **Unknown keys are refused**, so a typo is a message rather than a setting that quietly did nothing.
- **No credential belongs in the file.** A source needing a key names the environment variable that holds one (`api_key_env`), and `<NAME>_FILE` takes precedence for a secret mounted as a file. A key pasted in as a value is refused by name.

### The commands

| Task | Command |
|---|---|
| What may be fetched, and under what terms | `uv run meridian-ingest sources` |
| Retrieve artefacts | `uv run meridian-ingest fetch [--since <ISO-8601 Z>] [--until <ISO-8601 Z>] [--limit N]` |
| Normalise, writing nothing | `uv run meridian-ingest normalise` |
| Re-hash the raw store | `uv run meridian-ingest verify` |
| Load into the archive tables | `uv run meridian-ingest load` — reads `DATABASE_URL` |

Every verb takes `--source <id>`; without it, each acts on every enabled source. A named source is used whether or not the settings file disabled it, because naming it is an operator overriding their own default.

**`fetch` is the only command that opens a socket.** The other four read the raw store and have no way to reach an archive — normalisation is typed to take bytes and a manifest, with no client, no URL and no clock anywhere in its signature (D-142).

**A timestamp must carry its offset.** `--since 2026-08-01T00:00:00` is refused; write `2026-08-01T00:00:00Z`. An operator outside UTC would otherwise fetch a different month, quietly, with every retrieved file looking exactly as legitimate as the right one. Bounds are half-open, `[since, until)`.

**`fetch` checks `ATTRIBUTION.md` before opening a socket** (D-134). It refuses when that file is found and the source's attribution entry is missing from it. When no such file is found above the working directory — an installation outside a checkout — it says so and continues: the obligation is enforced by a test over every registered source, and a runtime check that silently passed would be worse than one which admits it could not run.

### Exit codes

| | Means |
|---|---|
| 0 | It ran and succeeded |
| 1 | It ran and failed |
| 2 | The command line was wrong |
| **3** | **`verify` found a stored artefact that no longer matches its manifest** |

Three is its own code so that a monitoring script can tell *"verify could not run"* from *"the raw store is damaged"*. Those call for different people.

### The completion gate, at a prompt

Stage 14's gate is that **an archive snapshot can be downloaded once, then repeatedly normalised and evaluated without network access.** Demonstrate it:

```bash
uv run meridian-ingest fetch
uv run meridian-ingest normalise > first.txt
uv run meridian-ingest normalise > second.txt
diff first.txt second.txt          # empty
```

`normalise` prints a digest per reception, so the comparison is over what would be *stored* rather than over a count that two different transformations could share. To take the network away rather than trust that it went unused:

```bash
unshare -rn uv run meridian-ingest normalise
```

That runs in a network namespace with no interfaces at all, and prints the same thing.

> The reference archive's artefacts ship inside the distribution, so its `fetch` needs no network either. A real source's would — which is exactly why the gate is about every command *after* the fetch.

The same gate is asserted in `tests/unit/test_ingest_gate.py`, which publishes a snapshot, **deletes the fixtures it came from**, and then normalises three times under a guard that fails the test if anything in Python opens a connection. Both the guard and the deletion have positive controls, because a gate passing with an inert guard is the one failure that looks done.

### When `verify` exits 3

```text
meridian-ingest verify: reference_archive/20260921T041950Z-e9b998767990 is 1555 bytes hashing to 0471a8620429, but its manifest records 1554 bytes hashing to e9b998767990
1 of 3 artefacts do not match their manifests. The raw store is not in the database backup — restore it from your own copy (docs/OPERATIONS.md).
```

The bytes on disk are no longer the bytes that arrived, so anything derived from them from now on would be derived from something nobody retrieved.

1. **Restore the raw store** from your own copy — see below.
2. **If you have none, re-fetch that source.** The digest of what arrived is in `ingest_records.sha256`, so a re-fetch returning the same bytes is recognised as the same record and writes nothing new; one returning different bytes becomes a new record and supersedes the old, in a single transaction.
3. **Rows already loaded from that artefact are not wrong.** They were derived before the damage. `archive_observations.content_sha256` is what says whether a re-normalisation agrees with them.

### The raw store is not in the database backup

`deploy/tools/backup.py` dumps Postgres and takes none of `raw_root`. It prints the path it did not take on every run, so the gap is read at backup time rather than discovered at restore time.

**That tree is the one thing here which cannot be recreated without going back to the source** — and a source may have withdrawn the artefact, changed its terms, or stopped existing. Copy it yourself, on the same schedule as the dump:

```bash
tar -C data/ingest -czf backups/raw-$(date -u +%F).tar.gz raw
```

The tree is read-only by construction: a record's directory is sealed after publication, so `rm -rf` on it fails until you `chmod -R u+w` first. That is immutability working, not a permissions fault.

---

### Public environmental and space-weather sources

Stage 31's nine sources — one per class, listed with their terms by `meridian-ingest sources` and in `ATTRIBUTION.md` — arrive through the same four verbs, into `environment_samples` instead of the archive tables (D-220, D-221). **Every one is off until you enable or name it**, so a fresh install still fetches only the reference archive. Re-read a source's terms page, named in `ATTRIBUTION.md`, before its first live fetch.

A source asked about places reads them from its table in `ingest.toml`:

```toml
[sources.open_meteo_cloud]
enabled = true
points = [[12.97, 77.59]]            # [latitude, longitude]; sent rounded to 0.01°

[sources.nasa_firms]
enabled = true
bbox = [74.0, 11.5, 78.6, 18.5]      # west, south, east, north
# key from $FIRMS_MAP_KEY (or $FIRMS_MAP_KEY_FILE)

[sources.isro_bhuvan]
bbox = [74.0, 11.5, 78.6, 18.5]
layers = ["<a Bhuvan layer name>"]   # display only; no default
```

| Source | Needs | Key variable |
|---|---|---|
| `noaa_swpc_kp` | nothing | — |
| `open_meteo_cloud`, `open_meteo_aerosol` | `points` | — |
| `nasa_gibs` | `bbox`, optionally `layers` | — |
| `nasa_firms` | `bbox` | `FIRMS_MAP_KEY` |
| `ornl_modis_ndvi`, `nasa_power_precipitation` | `points`, and `--since`/`--until` | — |
| `nasa_black_marble` | `bbox`, `--since`/`--until`, the `hdf5` extra | `EARTHDATA_TOKEN` |
| `isro_bhuvan` | `bbox`, `layers` | — |

- **Keys never appear in anything printed.** A source that takes its key in the URL is planned with a placeholder, substituted at the request, and redacted from every error (D-223).
- **Published limits are honoured before they are reached.** Each source's own limits are counted in `<raw_root>/.ledger/`, which survives between runs; a fetch waits for a slot that reopens soon and otherwise stops and says when to come back. `sources` prints what is left of each window.
- **Tiles are recorded and never read for a number** — `load` reports them as skipped (D-133). Bhuvan's map images are display only because its terms say so (D-220).
- **Night-time lights need `uv sync --extra hdf5`** (or `pip install 'meridian-ingest[hdf5]'`); without it `normalise` refuses that source by name (D-226).

**Near real time is `follow`, run where `meridian-ingest` is installed, never in the compose stack** (D-225):

```bash
uv run meridian-ingest follow --once        # one round: each due source fetched, then loaded
uv run meridian-ingest follow --interval 300 # rounds until interrupted
```

A source is due when its newest retrieval is older than its cadence (Kp and cloud hourly, fires three-hourly, the rest daily or slower). A round loads only artefacts not yet recorded, so its cost is what arrived, not everything ever held; after a normaliser changes, run `meridian-ingest load` once to re-apply it to what is held. `--no-load` fetches only. A cron line for the machine holding the raw store:

```cron
*/15 * * * *  cd /srv/meridian && DATABASE_URL=... uv run meridian-ingest follow --once >> /var/log/meridian-ingest.log 2>&1
```

**A value is a feature only for passes after it was published**, and "published" is our own fetch unless the artefact states an earlier production time (D-222). So a backfill never supplies features for passes already flown: the conditions group fills in from when `follow` starts running. Features are read from a snapshot's `environment_samples.jsonl`, never from the table while scheduling (D-224).

## Dataset snapshots

Prediction and evaluation never read the live tables. They read an **immutable snapshot**, so every number in a report can be regenerated from a snapshot, a configuration and a seed (rule 8). There are two steps, and only the first one needs the database (D-143):

1. **`export`** freezes the database into a **raw snapshot**: every table the labels need, read at a single instant, plus the registry's answer to "was this station listening", stored next to each closed assignment.
2. **`label`** turns a raw snapshot and a labelling configuration into an **evaluation dataset**: one label per physical pass (D-146 to D-148), the archive's receptions in the archive's own vocabulary, and how both populations were selected — completeness per station-day and a propensity per eligible pass (D-149 to D-153). It opens files and nothing else.

Decisions this section puts into practice: D-143 to D-154.

### Where they go

Everything goes under one **datasets root**: `--root`, else `$MERIDIAN_DATASETS_ROOT`, else `data/datasets`. The `data/` directory is gitignored.

```text
data/datasets/
├── snapshots/<as_of>-<hash12>/    raw snapshots: one .jsonl per table, listening.jsonl, manifest.json
└── evaluation/<hash12>/           evaluation datasets: labels.jsonl, archive_receptions.jsonl,
                                   station_days.jsonl, propensities.jsonl, manifest.json
```

- **Names come from content.** A directory is named after the sha256 of its manifest. That manifest lists every file's digest and row count, and the hash leaves out only `created_at`. So the name *is* the check, and two runs that produce the same content land in the same directory. The second run writes nothing and says `already held, identically`.
- **Directories are read-only.** Each directory is sealed when it is published, so `rm -rf` fails until you `chmod -R u+w`. As with the raw store, that is immutability working, not a permissions fault.

### The commands

| Task | Command |
|---|---|
| Freeze the database from a start time to now | `meridian snapshot export --since <ISO-8601 Z>` — reads `DATABASE_URL` |
| Label a raw snapshot | `meridian snapshot label <raw snapshot dir> [--config snapshot.toml]` |
| Print a dataset's completeness and weights | `meridian snapshot completeness <dataset dir> [--threshold 0.7]` |
| Check a snapshot or dataset against its manifest | `meridian snapshot verify <dir>` |

- **There is no `--until`.** A raw snapshot ends at its export transaction's own `now()`. Several tables hold current state rather than history, so a snapshot can only describe *now*.
- **`--since` must carry its offset**, for the same reason as in `meridian-ingest`.
- **Labelling settings:** copy `deploy/snapshot.toml.example`. Its values are the defaults, and unknown keys are refused. The settings are hashed by value into the dataset's manifest, so a changed setting gives a different dataset rather than an overwritten one.
- **What `label` prints:**
  - every non-zero count by name, with measured and simulated always kept apart;
  - how many confirmed-listening silences it could not judge (`satellite_state_indeterminate`). EVALUATION.md §5 asks for that share to be stated beside any figure that depends on it.
- **`export` also computes the archive's denominator** (D-150): for each located archive station, the passes of every satellite it was seen receiving, propagated with our element sets. What it could not compute — a station with no location, a satellite not in our catalogue or with no element set — is counted in the manifest under `archive_denominator.*`, never dropped.

### Completeness and weights

Every evaluation dataset says how its passes were selected (D-154), and `completeness` prints it for both populations — our stations and the archive's, never summed:

```bash
uv run meridian snapshot completeness data/datasets/evaluation/<hash12>
uv run meridian snapshot completeness data/datasets/evaluation/<hash12> --threshold 0.7
```

- **Station-days** by status. `retained` and `below_threshold` are judged against the threshold; `empty` had nothing eligible; `inactive` is an archive station's day, inside its span of receptions, on which it received nothing — not scored 0, because with no heartbeat an off station and an idle one look the same (D-150).
- **Eligible, attempted, usable.** Attempted is the policy's choice; usable is whether the outcome can be scored. They are never folded together (D-149).
- **The distribution and the sensitivity table**: deciles, a histogram in tenths, and retained and excluded days at each of `[completeness] sensitivity` (D-151).
- **The weights** (D-153): unweighted and weighted success rates with Wilson intervals, the effective sample size, and how many passes were `unsupported` (propensity 0), `certain` (propensity 1) and `floored`. A weighted rate whose effective sample size is below max(30, 0.1 n) ends in **`UNRELIABLE`**: `EVALUATION.md` §4.2 says to label it, not quote it.

**`--threshold` re-judges the station-days without labelling again.** The station-days are in the dataset, so any threshold can be read off them; the weights cover every eligible pass whatever the threshold, so they do not change. To make another threshold *the* dataset's, set `[completeness] threshold` and label again — it is hashed, so that is a new dataset.

**Expect our own station's weights to be `UNRELIABLE`, and most passes `certain` or `unsupported`.** The baseline scheduler is deterministic: within a cell of similar passes it takes all or none, so there is no counterfactual to weight toward. That is a positivity finding to report, not a fault (D-152); prospective randomisation (`EVALUATION.md` §4.3) is its remedy.

The settings are `[completeness]` and `[propensity]` in `deploy/snapshot.toml.example`.

### Exporting from the deployment

The deployment does not publish the database port, so `export` runs inside the compose network. Mount a host directory as the datasets root so the snapshot outlives the container:

```bash
mkdir -p data/datasets
compose run --rm --no-deps --user "$(id -u):$(id -g)" \
  -v "$PWD/data/datasets:/datasets" -e MERIDIAN_DATASETS_ROOT=/datasets \
  api meridian snapshot export --since 2026-08-01T00:00:00Z
```

**`export` only reads.** Its transaction is `REPEATABLE READ, READ ONLY`, so the scheduler and the API carry on while it runs, and every table is read at the same instant.

**Labelling needs no deployment at all.** Run it from a checkout, on any machine that holds the raw snapshot:

```bash
uv run meridian snapshot label data/datasets/snapshots/<as_of>-<hash12>
```

### Exit codes

| | Means |
|---|---|
| 0 | It ran and succeeded |
| 1 | It ran and failed: the database was unreachable, `--since` was unreadable, the settings or `--threshold` were refused, or it was pointed at the wrong kind of directory — `label` at a dataset, `completeness` at a raw snapshot or a dataset labelled before Stage 16 |
| 2 | The command line was wrong |
| **3** | **The snapshot or dataset no longer matches its manifest** |

Code 3 means the same here as it does in `meridian-ingest verify`. `label` and `completeness` return it too: a directory that fails its own check is refused, not read.

### The completion gate, at a prompt

Stage 15's gate is that **the same raw snapshot and the same configuration always produce the same evaluation dataset hash.** To demonstrate it:

```bash
uv run meridian snapshot label data/datasets/snapshots/<dir>      # (written)
uv run meridian snapshot label data/datasets/snapshots/<dir>      # already held, identically
unshare -rn uv run meridian snapshot --root /tmp/elsewhere label data/datasets/snapshots/<dir>
```

All three print the same hash. The last one runs with no network interfaces at all, into a root that has never seen the dataset.

Stage 16's gate is that **every archive-derived result carries its completeness automatically**. To see it, label any raw snapshot — even one with no archive rows — and run `completeness` on the result: both populations print, and a population with nothing to weight says why rather than printing nothing.

Test files assert both:
- `tests/unit/test_completeness_gate.py` labels an empty snapshot, one of our stations only and one with an archive station, and finds the selection in each. It checks that one archive reception fewer lowers completeness, that a deterministic policy is reported as certain and unsupported, and that changing every outcome leaves `propensities.jsonl` byte-identical.
- `tests/unit/test_snapshot_gate.py` labels one snapshot three ways, under a guard that refuses `psycopg.connect` and every Python socket. A positive control shows the guard firing. It also labels in two processes with different hash seeds.
- `tests/integration/test_snapshot_gate.py` exports from a database, **deletes every source row**, and labels again.

### When `verify` exits 3

The directory's bytes are no longer the bytes that were written. Anything computed from them now would be computed from something no export produced.

- **A raw snapshot:** restore it from your copy, then run `verify` again. With no copy, a new `export` gives a *new* snapshot with a new name. That is not a repair: the rows may have changed since.
- **An evaluation dataset:** delete it (`chmod -R u+w` first) and run `label` again from its raw snapshot. Its manifest's `derived_from` names which raw snapshot that is.

### Snapshots are not in the database backup

`deploy/tools/backup.py` names the datasets root it did not take on every run (D-144). **A raw snapshot cannot be recreated.** It is the database as it stood at one instant, and a later export describes a later instant. Any figure computed from a snapshot needs that snapshot kept, so copy the snapshots yourself:

```bash
tar -C data -czf backups/datasets-$(date -u +%F).tar.gz datasets
```

Evaluation datasets can always be regenerated from their raw snapshot and settings, so the raw snapshots are the part that matters.

---

## Models

A model gives each candidate pass `P(decode | station, pass)`, the number the scheduler of Stage 18 will multiply by a pass's value. It is **fitted from an evaluation dataset**, never from the live tables, so it can be regenerated from a snapshot, a configuration file and a seed (rule 8). It is **scored from a file**, with plain Python and no numerical libraries, so the machine that schedules never needs them (D-155).

Decisions this section puts into practice: D-155 to D-164.

**Expect `fit` to refuse today.** A fit needs at least 20 training and 10 validation examples with both outcomes, and every usable example is a measured pass of one of our stations: simulated ones are never fitted on (D-078). Until a station has reported for a few weeks, `fit` says how many it found and exits 1. That refusal is the designed behaviour, not a fault.

### It needs the `fit` extra, which the image does not have

scikit-learn is the platform's `fit` extra, installed for fitting on a workstation and deliberately left out of the deployment image (D-155):

```bash
uv sync --extra fit
```

Without it, `meridian model fit` and `evaluate` say what to install and exit 1. `meridian model show`, and every other command, works without it. CI loads `meridian model` in the built image, and checks that `fit` there asks for the extra.

### Where they go

Models sit under the same datasets root as snapshots, named from their content in the same way:

```text
data/datasets/
├── snapshots/<as_of>-<hash12>/    raw snapshots
├── evaluation/<hash12>/           evaluation datasets
└── models/<hash12>/               models: model.json, manifest.json
```

A model's manifest names the dataset it was fitted on, and that dataset's manifest names its raw snapshot. `fit` and `evaluate` follow those names under the root, check each hash, and refuse a directory that is not the one named. If you moved one, name it with `--dataset` or `--snapshot`.

### Settings

Copy `deploy/model.toml.example`. Its values are the defaults, and unknown keys are refused.

- `configuration` — `A`, `B`, `C` or `D` (D-160). B's model is A's: priority weights the objective, not the model. D's objective is weighted by priority too (D-168).
- `population` — `own`, or `archive` under A only. The two are never pooled (D-156).
- `train_until` and `validate_until` — **there are no defaults, and a fit without them is refused.** A pass rising before `train_until` trains the model, one before `validate_until` calibrates it, and the rest, up to the dataset's `as_of`, is the test span (D-162).
- `min_station_history` — below this many settled outcomes, a station is scored by the geometry-only model (D-161).
- `inverse_regularisation`, `weighting` (`none` or `ipw`), `seed` and `folds` — see the file.

**Choose the dates before you look at any result, and do not move them to improve one.** Look at the dataset's span (`since … as_of`, which `label` prints). Leave the test span long enough to judge on, and keep training and validation in time order, as the scheduler will meet them. The settings are hashed into the model, so changed dates give a new model rather than an overwritten one, and the report prints the dates beside every figure.

### The commands

| Task | Command |
|---|---|
| Fit and publish a model | `uv run meridian model fit <dataset dir> --config model.toml` |
| Print its calibration report | `uv run meridian model evaluate <model dir>` |
| Print what it is | `uv run meridian model show <model dir>` |
| Check a model against its manifest | `uv run meridian snapshot verify <model dir>` |

- **`fit` prints** where the model landed, marked `(written)` or `already held, identically`. It also prints the model's hash, and the size and decodes of each span.
- **`evaluate` takes no `--config`.** It reads the configuration the model was fitted under from the model's manifest, checks its hash, and rebuilds the examples from the dataset.
- **`show` reads the file alone.** It prints the features, the standardisation, the coefficients and the calibration map of both models, and the library versions that fitted them.

### Reading the report

`meridian model evaluate` prints, top to bottom (D-164):

- **Provenance:** the model's, dataset's and configuration's hashes, the three dates, the seed and the regularisation.
- **The examples:** how many trained, calibrated and were judged, and how many simulated passes were left out.
- **The Brier score against a base rate.** The base rate is the training span's decode rate, given to every test pass. The skill is 1 − Brier ÷ base-rate Brier: above 0 the model beats knowing only how often passes decode; at or below 0 it does not, and that is the finding to report.
- **The routes.** How many test passes each model scored, `configured` or `geometry_fallback`, and how well.
- **The reliability diagram.** Ten bins of predicted probability. Each shows its count, its mean prediction and the observed decode frequency with a Wilson 95% interval. Empty bins are printed as dashes, not left out.
- **Calibration by segment.** By station, band and element-set age, each with n and an interval. With a handful of passes in a segment, the interval says more than the point.
- **Rolling-origin folds.** Each is refitted inside the span before the test span, with its Brier score. Then comes the spread across folds, which is the variance of the figures above. A fold with too little data says why.
- **The dataset's completeness**, as `meridian snapshot completeness` prints it (D-154).

The figures are unweighted even after an `ipw` fit, and the report says so: weighting changes what the model learned, not what happened.

### Exit codes

| | Means |
|---|---|
| 0 | It ran and succeeded |
| 1 | It ran and refused. Possible reasons:<br>• too few examples, or one outcome only;<br>• no split dates;<br>• a setting refused;<br>• the `fit` extra missing;<br>• a dataset, snapshot or model that is not the one named;<br>• an empty test span |
| 2 | The command line was wrong |
| **3** | **A dataset, snapshot or model no longer matches its manifest** |

### The completion gate, at a prompt

Stage 17's gate is that **configurations A–D run from one config interface, use temporal splits, handle cold start, and produce reproducible calibrated probabilities.** On a dataset with enough measured passes:

```bash
uv run meridian model fit <dataset> --config model.toml     # (written)
uv run meridian model fit <dataset> --config model.toml     # already held, identically
sed -i 's/^configuration = .*/configuration = "A"/' model.toml
uv run meridian model fit <dataset> --config model.toml     # another model, one line changed
uv run meridian model evaluate <model>                      # dates, Brier, bins, segments, routes
```

`tests/unit/test_prediction_gate.py` asserts each clause through these commands. It builds a 21-day snapshot in which a third station joins inside the test span, and gives every claim a positive control that must fail:
- A to D are fitted from four files that differ in one line.
- Every outcome after `train_until` is reversed in the raw snapshot, and the scaler and coefficients are unmoved.
- No prediction module can shuffle.
- The late station is scored by the geometry-only model until it has history, and says why.
- Two fits under the network guard, and two in separate processes with different hash seeds, name one model.
- Every probability is in [0, 1].

### When `verify` exits 3

The model's bytes are no longer the bytes that were fitted. Delete it (`chmod -R u+w` first) and fit again from its dataset and configuration. The manifest's `derived_from` and `parameters` say which. In one environment the refit names the same directory. With other numpy or scikit-learn versions the name may differ, though its predictions agree within 1e-9 (D-163).

### Models are not in the database backup

Models, like datasets, are regenerable from what they were made from, so the raw snapshots are what needs keeping (§ Snapshots are not in the database backup). To reproduce a model's exact bytes, also keep the library versions `show` prints.

---

## Scheduling

The `jobs` service decides, every `SCHEDULE_INTERVAL_S`, which of the next `SCHEDULE_HORIZON_S` of passes each station receives. It maximises the summed value of what it takes: **yield × frames × priority**, the last under B and D only (D-168). A mixed-integer programme solved by HiGHS finds that maximum, under one set of constraints, and the result is checked against those constraints before anything is written (D-166, D-167). Every run is a row in `schedule_runs`, and every decision carries an explanation (D-170).

Decisions this section puts into practice: D-165 to D-172.

### Settings

`SCHEDULE_CONFIG` in `deploy/.env` names a `schedule.toml` inside the `jobs` container. The datasets directory (`DATASETS_DIR`) is mounted read-only at `/datasets`, so a file kept there is `/datasets/schedule.toml`. Copy `deploy/schedule.toml.example`. Its values are the defaults, and unknown keys are refused.

- `configuration` — `A` to `D`. Unset, the service runs A on the **elevation proxy**: peak elevation over 90°, labelled as such on every decision, and allowed for A and B only.
- `model` — a model directory under the datasets root, as `meridian model fit` printed it. Required for C and D. It must be its configuration's model: A's model for A or B.
- `frames`, `time_limit_s`, `turnaround_s`, `seed` — see the file.

**A configuration the service cannot obey stops it at start, by name.** That covers a learned configuration without a model, another configuration's model, or a model or history that cannot be read. A schedule made some other way than configured must not look like the configured one. After changing the file or its model, run `compose up -d jobs`.

**A model that reads history (C, D) reads the newest labelled dataset under the datasets root.** Every decision states that history's `as_of`, and `meridian_scheduler_history_age_seconds` is its age. Refreshing it means exporting a snapshot and labelling it (§ Dataset snapshots). The service notices the new dataset on its next round, and nothing needs restarting.

### Reading a decision

`GET /api/v1/assignments/{id}`, and a station's page on the dashboard, show each decision's explanation:
- **`terms`** — the value, and the yield, frames and priority it is the product of. The yield's source is `model`, with its route, or `elevation_proxy`.
- **`weighed_against`** — every pass it could not share the antenna with, best first.
- **`rule`** — for a skip, `overlap` or `eligible_cap`.
- **`alternative`** — for a skip, the pass that took its slot; for a selection, the best pass it displaced.
- **`run`** — the solver's status, and the history's `as_of`.

`schedule_runs` holds each run itself: the configuration's hash, the model's hash, the solver's version, status, objective, bound and runtime, and the counts.

- **`optimal`** means proven best.
- **`time_limit`** means the best found when time ran out.
- **`fallback`** means the solver gave no usable answer, and greedy under the same constraints decided instead. `detail` says why.

### Passes behind a declared horizon

A station's capability may declare a horizon mask: a building, a ridge. A pass whose track clears the declared floor nowhere is **not scheduled and not skipped**. It is left undecided, so correcting the mask gives it back on the next round, and the run's report counts it (`below the declared horizon, left undecided`). Only the declared mask does this; the learned horizon informs the yield prediction instead (D-175). A mask is re-sent by registering again, and an entry outside `[0, 360]` for azimuth or `[-90, 90]` for elevation is refused as `malformed`.

### Declined and offline work

A station that stops naming a `held` assignment before its window has declined it. The assignment becomes `revoked` with reason `declined`, and the next round gives its time to another of that station's passes. When a round finds a station `offline`, its work not yet begun becomes `revoked` with reason `offline`. If the station returns still holding such an assignment, it goes back to `held`, because MSP has no message that takes work back. If it returns without it, the pass is decided again (D-171). A revoked assignment is never delivered and never counted as a miss. The public lists show each pass's latest decision.

### Comparing the schedulers

`meridian schedule evaluate` replays a dataset's test span under every scheduler and prints SC-1. It needs no database, and needs the `fit` extra only to have fitted the models:

```bash
uv run meridian model fit <dataset> --config model-a.toml    # and C, and D, with one pair of dates
uv run meridian schedule evaluate <dataset> --config schedule-evaluation.toml
```

Copy `deploy/schedule-evaluation.toml.example`, and name the A, C and D models in it. All three must be fitted on this dataset, with the same split dates, or the command refuses (D-172). It prints, top to bottom:

- **Provenance:** the dataset's, raw snapshot's, models' and configuration's hashes; the objective, the constraints, the solver's version and time limit, and the seed.
- **What was replayed:** the test span's station-days at or above the completeness threshold, the ones left out, the candidates, and how many of them have a known outcome.
- **One row per scheduler:** greedy A and greedy B (existing practice), the optimiser under A to D, and the oracle, which knows every outcome.
  - Each row gives the passes taken, the frames decoded, frames per station-hour, the **unknown share**, the fraction of the oracle's frames, and how each day was solved.
  - The unknown share is the part of the schedule nobody attempted, so it adds no frames. A scheduler that departs from what was actually attempted is judged low by exactly that share.
- **SC-1, D − B**, and **D − greedy B**, each per station-hour and relative, with 95% paired-bootstrap intervals over station-days.

Run it twice and diff: the same dataset, file and seed print the same bytes. `threshold` in the file reads the comparison at another completeness threshold (D-151).

Exit codes are the models' (§ Models): 0 printed, 1 refused, **3 a directory that no longer matches its manifest**.

### Watching it

The dashboard's Scheduling row shows three panels:
- **solver outcomes per hour**, by status: a rising `fallback` line means the solver's answers are missing or being refused;
- **solver time**, against the configured limit;
- **model history age**, absent while no model reads history.

### The completion gate, at a prompt

Stage 18's gate is that **the optimised scheduler always emits a constraint-valid schedule, records its reasoning, and can be compared reproducibly against baselines and an oracle.**

```bash
compose exec jobs meridian schedule --from <ISO-8601 Z> --to <ISO-8601 Z> --config /datasets/schedule.toml
curl -s localhost:8000/api/v1/assignments?limit=5 | python -m json.tool     # each with its explanation
uv run meridian schedule evaluate <dataset> --config schedule-evaluation.toml > one.txt
uv run meridian schedule evaluate <dataset> --config schedule-evaluation.toml > two.txt
diff one.txt two.txt                                                          # nothing
```

`tests/unit/test_scheduler_gate.py` asserts each clause through the commands. It uses a 22-day snapshot in which a second satellite clashes with the first once a day, and each claim has a positive control:
- Every schedule of every scheduler is valid, including when the solver gives no answer or a wrong one. The wrong answer is shown to be wrong.
- Every decision under D explains itself from the model's yield.
- The report is the same twice and in processes with different hash seeds, and it moves with its seed and its data.
- The oracle takes at least every scheduler's frames on every day.

`tests/integration/test_scheduler_gate.py` asserts the database half: every decision a round stores, under A to D and with the solver failing, names a recorded run and explains itself, and no antenna is given two passes at once.

---

## Evaluation reports

Every number in a report is regenerable from a raw snapshot, one configuration and one seed (rule 8, `EVALUATION.md` §9). `meridian report build` computes a report from those three and nothing else, and `meridian report verify` proves a report regenerates. Neither opens a database or a socket.

Decisions this section puts into practice: D-234 to D-237.

### Building a run

```sh
uv run meridian report build \
  --snapshot data/datasets/snapshots/<raw snapshot> \
  --config analysis/configs/evaluation.toml.example \
  --seed 4471
```

- **It fits models, so it needs the `fit` extra** (`uv sync --extra fit`), as `meridian model fit` does. Without it, it says so and exits 1.
- **The configuration** is one file, one table per section. Copy `analysis/configs/evaluation.toml.example`, whose values are the defaults. An unknown table or key is refused, and so is a `seed`. **Set `train_until` and `validate_until` under `[prediction]`** for the snapshot you are reporting. Without them no model is fitted, and the prediction section says so.
- **The seed** is the master seed. Every component that draws a random number draws from a seed derived from it by name, and the run lists each one (D-236).
- **The run** goes under `<datasets root>/reports/<hash12>/`, or where `--output` says. Building the same inputs twice names the same directory, and the second build says `already held, identically`. An `--output` that already holds a different run is refused, never overwritten.

### What a run holds

```text
reports/<hash12>/
├── report.md       the report, rendered from the results files beside it
├── run.jsonl       the run record: method, snapshot, configuration, seeds
├── data.jsonl      the data section's results
├── prediction.jsonl                the prediction section's results
├── reliability_<model>.svg         one reliability diagram per fitted model
├── config.toml     the configuration, byte for byte as it was given
└── manifest.json   every file's digest, the inputs, the seeds, and the environment
```

- **The data section** states:
  - provenance, and the snapshot's sources with their licences;
  - every outcome label and exclusion reason, with measured and simulated kept apart;
  - completeness and weighting for our stations and the archive's;
  - silent-satellite exclusions and the indeterminate share, each with its interval (`EVALUATION.md` §4, §5).
- **The prediction section** covers A, C, D and D∖conditions (B's model is A's):
  - it fits each under the `[prediction]` settings with a seed derived from the master, publishes it under `models/`, and judges it on the test span;
  - it reports the Brier score, the skill against the training base rate, the reliability diagram, calibration by station, band and element-set age, the cold-start routes and the rolling-origin folds;
  - it compares D against D∖conditions, C against A, and D against A on the same passes;
  - it reads SC-2 from D;
  - it counts disturbed passes, and says Kp is **untested** below `min_disturbed`.

  Every interval is a 95% bootstrap that resamples whole station-days (D-237). A model that could not be fitted is a row that says why, and the report is still built.
- **The environment block** in `manifest.json` records the commit (and whether the tree had uncommitted changes), the Python and dependency versions, where the snapshot was read from, and how long the build took. It is **not part of the hash** (D-235), so the hash names the numbers, not the machine. A run built from uncommitted code says so when it is built. Build reported figures from a clean tree.
- The evaluation dataset the run labelled is published under `evaluation/` as `meridian snapshot label` would publish it, and the run names it by hash.

### Verifying a run

```sh
uv run meridian report verify data/datasets/reports/<hash12>
```

`verify` reads the run's configuration and seed. It finds the raw snapshot by its hash, in this order:
1. the path given with `--snapshot`;
2. the path the run recorded;
3. any snapshot under the datasets root with that hash prefix.

It then builds the run again and compares hashes. It prints any difference between this machine's environment and the recorded one, whether or not the hashes match.

| Exit | Meaning |
|---|---|
| 0 | The run regenerated identically |
| 1 | It did not, and the files that differ are named; or the snapshot could not be found, or the directory is not a run |
| 3 | The run or the snapshot does not match its own manifest: it was edited after it was written |

A run edited *and resealed* passes its own manifest check but fails verification, because only regeneration can tell a forged number from a computed one.

---

## Rate limits

The API limits request rates itself (D-202), beside the edge rule on the tunnel hostname (D-088). A refused request gets `429` with `rate_limited` and a `Retry-After` header.

| Bucket | Keyed by | Burst | Refill |
|---|---|---|---|
| MSP heartbeat | the station's bearer token | 6 | 1 per 10 s |
| MSP observations | the station's bearer token | 20 | 1 per 6 s |
| every MSP request | the caller's address | 300 | 10 per s |
| `/api/v1` | the caller's address | 50 | 5 per s |

- **Per worker.** Each API worker keeps its own buckets, and a restart refills them, so with `API_WORKERS=2` a caller can get up to twice these figures.
- **A limited station loses nothing.** The reference client retries a 429 with backoff and keeps an unsent observation queued.
- **Many stations behind one address** share only the per-address bucket, which a 50-station simulated fleet on one host does not reach.
- **Seeing it:** the MSP error panel counts `rate_limited`, and the API panels count 4xx by route.
- **Turning it off:** `RATE_LIMITS=off` in `deploy/.env`, for accelerated simulations on a laptop. The platform refuses to start with it on a public deployment.

The public API also takes no request body and caps its query string at 2 KiB (D-203).

---

## Rotating secrets

Each platform secret is read once, when its process starts (D-201). Rotating one means writing a new file and recreating the services that read it. `deploy/tools/rotate_secret.py` does the first and prints the second. With the secrets override, `compose` in this section means:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.secrets.yml
```

On a public deployment, add `-f deploy/docker-compose.public.yml` to it as well. If you leave it out, the recreated `api` publishes its port on the host and loses `MERIDIAN_PUBLIC` and `CLIENT_ADDRESS_HEADER`, while the tunnel keeps forwarding to it (D-206). The tool adds the file to the command it prints whenever `deploy/secrets/tunnel_token` exists.

**Why the tool and not an editor.** It writes each file `0444` inside a `0700` directory, which is the combination a container's uid can read and another user on the host cannot. It replaces a file by renaming a new one into place, which a bind mount does not follow, so the change reaches a container only when that container is recreated. That is why every step below ends with `--force-recreate`.

### The pepper

**Never replace `TOKEN_HASH_PEPPER` outright.** Every bearer token and every registration key stops matching, so every station gets 401 and none can be recovered through a bound invite, which needs its registration key. Rotate it instead:

```bash
python deploy/tools/rotate_secret.py rotate token_hash_pepper
compose up -d --force-recreate api
```

The old pepper moves to `token_hash_pepper_previous`. The platform accepts it for verification only, and re-hashes each credential under the new pepper as it is used:
- **bearer tokens** move on the station's next heartbeat, so every online station has moved within 30 s. Each move is logged once: `bearer token re-hashed under the new pepper`;
- **registration keys** move only when a station next recovers.

**When to retire the old pepper.** Once every station you expect back has heartbeated since the restart (the dashboard shows each station's last heartbeat), tokens are done:

```bash
python deploy/tools/rotate_secret.py retire token_hash_pepper
compose up -d --force-recreate api
```

Retiring also ends recovery through a bound invite for every station that has not recovered since the rotation, because its registration key is still under the old pepper. There is no hurry to retire: both secrets are 256-bit random values, so keeping an old pepper for verification exposes nothing (`THREAT-MODEL.md` §6). A second pepper rotation is refused until the first is retired.

**Checked on a local stack, 2026-09-28**, with one simulated station: after `rotate` and the recreate, the station's stored token hash changed on its next heartbeat and it stayed online; after `retire` and another recreate it kept heartbeating, and its log held no 401.

### The metrics token

```bash
python deploy/tools/rotate_secret.py rotate metrics_token
compose --profile metrics up -d --force-recreate api jobs prometheus
```

The API, the jobs process and Prometheus read the same file, so they cannot disagree. Scrapes fail for the seconds the API takes to restart; `ApiUnavailable` waits a minute before firing.

Without the secrets override, set the same new value in `METRICS_TOKEN` in `deploy/.env` and in `deploy/prometheus/metrics_token`, then run the same `up` command.

### The bootstrap invite

`REGISTRATION_INVITE_TOKEN` seeds one invite into an empty database and does nothing afterwards (D-020). If it leaked before a station used it, withdraw it:

```bash
compose exec api meridian invite revoke --label "environment bootstrap"
```

`rotate registration_invite_token` changes the file for the next empty database, such as a fresh deployment. It has no effect on this one.

### The tunnel token

Rotate it in the Cloudflare dashboard (*Zero Trust* → *Networks* → *Tunnels* → the tunnel → *Refresh token*), which ends the old token's connections. Store the new value with `python deploy/tools/rotate_secret.py set tunnel_token < token.txt`, then run the command it prints, which recreates the tunnel with the public file. The dashboard is unreachable from outside between the two steps; stations queue their reports and send them when it returns.

### Database passwords

`API_DATABASE_PASSWORD` and `READER_DATABASE_PASSWORD` in `deploy/.env` are set on the roles by `migrate` (D-207). Change one, then:

```bash
compose up -d
```

`migrate` runs again and sets the new password, and compose recreates every service whose `DATABASE_URL` changed. Connections open with the old password stay open until those services restart, which the same command does.

The owner's `POSTGRES_PASSWORD` is set once, when the database volume is created. To change it: `compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "alter role meridian password '<new>'"`, then put the same value in `deploy/.env` and `compose up -d migrate`.

### Station tokens

A station's own token is rotated through a bound invite, and withdrawn with `meridian station revoke` (§ Everyday commands, D-034).

---

## Security scanning

`.github/workflows/security.yml` scans the Python lock, the dashboard lock and the built image on every pull request, on `main`, and every Monday (D-205). Each run keeps three CycloneDX SBOMs as artefacts: `sbom-python`, `sbom-dashboard` and `sbom-image`.

**When the Monday run fails** with no code changed, an advisory was published against something already pinned:
- **Python or npm:** raise the pin in `uv.lock` (`uv lock --upgrade-package <name>`) or `package-lock.json`, and let CI prove the rest still works.
- **The image:** a Debian package is fixed by rebuilding, since the runtime stage applies Debian's updates; a base image is fixed by moving its digest in `deploy/Dockerfile`.
- **Nothing can be done yet:** add the advisory to `.trivyignore` at the repository root, with a comment giving the reason and an `exp:YYYY-MM-DD` after which it fails again.

To scan a local build the same way:

```bash
docker build -f deploy/Dockerfile -t meridian:scan .
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.74.0 \
  image --severity HIGH,CRITICAL --ignore-unfixed meridian:scan
```

---

## Regional monitoring

Stage 32 watches places, not Meridian. Its decisions are D-227 to D-233. **It is not the platform's monitoring**: nothing here is a Prometheus metric, an alert rule or an Alertmanager route, and nothing on the scheduling or reception path reads it (D-228).

### Areas of interest

An area is a place and a label, registered by an operator — there is no endpoint that creates one, and nothing about an area is published until the team settles D-137 (D-227).

| Task | Command |
|---|---|
| Register a rectangle | `meridian regions add --label "Bengaluru urban" --bbox 77.45,12.85,77.75,13.10` |
| Register a polygon | `meridian regions add --label "…" --geojson area.geojson` — one Polygon, one ring |
| List areas | `meridian regions list` |
| Stop watching one | `meridian regions retire <area id>` — kept, never deleted |

A label or note that looks like an email address, a phone number or a street address is refused: an area describes ground, never a person. The same shape registered twice is the same area.

### Reports

```bash
meridian snapshot export --since 2026-06-01T00:00:00Z   # far enough back for the baseline
cp deploy/regions.toml.example regions.toml             # set [baseline] and [current]
meridian regions report --snapshot data/datasets/snapshots/<dir> --config regions.toml
```

The report is computed from the snapshot alone and published under `data/datasets/regions/<hash>/`. Run it twice and the second run prints `already held, identically`. It prints, per active area:

- each series — points, how many are missing, and the latest value with the product it came from;
- each change against the baseline: `ALERT`, `WITHIN` or `insufficient`, with the change, its interval and both periods' counts. An alert needs the whole interval past the threshold (D-231);
- how many of our measured decoded receptions covered the area — simulated ones only if `include_simulated = true`, and then printed apart;
- the two cross-checks (D-233). An **ingest gap** lists the days we imaged the area and a public product had no value: check that `meridian-ingest follow` ran and the source's box covers the area. A chain check that **differs** means decode rates on wet and dry days disagree beyond their intervals: at 137 MHz that is the station, not the sky — look at connectors and feedline weatherproofing;
- how many tiles are held for the area, always called imagery.

A baseline outside the snapshot's scope gives `insufficient`, never a zero: export with an earlier `--since`.

### Alerts

```bash
meridian regions record-alerts --report data/datasets/regions/<dir>
```

Each alert is recorded in `region_alerts` once — recording the same report again writes nothing — and handed to the delivery interface, which **records only** until Stage 29 builds notifications: each attempt is a `region_alert_deliveries` row with channel `record_only` saying so (D-232). An alert recorded by a run that stopped before its delivery was recorded is delivered by the next run, not passed by. Nothing is emailed or messaged.

---

## Stored measurements and profiles

What Stage 19 keeps, where each comes from, and how to read it (D-173 to D-178). Nothing here is dropped on a timer: raw heartbeats and observations are compressed after 7 days and kept (D-178).

### Noise floors

Every observation revision reporting a noise floor also writes a `noise_measurements` row in the same transaction: dBFS at the stated receiver gain, at the assignment's frequency, with no azimuth. They travel in every raw snapshot as `noise_measurements.jsonl`. Nothing writes a survey row yet.

### Products

A decoder names its products in its report, as `{kind, path}` relative to `{output_dir}`. The station keeps each one in `<state_dir>/products/<sha256>` and declares it as `station:products/<sha256>` (D-176):

```toml
[retention]
products_max_bytes = 2147483648   # the store's cap; the oldest products go first
```

The platform records each declared product as a `products` row. `/api/v1/observations` publishes kind, sha256 and size, never the uri. **No product is uploaded**: MSP defines no transfer yet (D-029), so the bytes stay on the station, and one evicted there is not reported.

### Horizon and interference profiles

The `jobs` service builds them each round, and `meridian profiles build` does the same now:

- **Declared** — each capability's mask, written when it changes. The earlier one is kept.
- **Learned** — built once from the newest labelled dataset, by the functions the model's features use: 36 horizon sectors and 48 interference cells a station. A second build from the same dataset prints `already held, identically`. A simulated station's profile is built from its own reports and says so.

`GET /api/v1/stations/{id}/profiles` serves the newest of each, and a station's dashboard page draws the declared horizon dashed and the learned one shaded. **Nothing here feeds prediction**: live scoring reads the dataset itself (D-174). New learned profiles need a new labelled dataset (§ Dataset snapshots).

A failing `profiles` task raises `ScheduledTaskNeverSucceeded` or `ScheduledTaskStalled` at warning, not critical: receiving and scheduling do not wait on it.

### Reading the views

```bash
compose exec api meridian schedule runs --limit 10
compose exec api meridian passes timing --station <station_id>
```

- `schedule runs` reads `scheduler_performance`: each run's solver status (`*` marks a fallback to greedy) and what became of its assignments, including those still owed.
- `passes timing` reads `timing_error`: first detection against predicted rise, raw and **corrected by the station's clock offset** (`EVALUATION.md` §6.1), with element-set age. The `excluded` column names what §6.1 would drop: `clock_offset_unknown` or `within_clock_uncertainty`.

These are for an operator at a prompt. **A reported figure comes from a snapshot**, never from these views (rule 8, D-177).

### Uptime

`GET /api/v1/stations/{id}/uptime?hours=48` gives heartbeats per hour, 1 to 168 whole hours, from the `heartbeats_hourly` continuous aggregate; the dashboard draws it as a strip. It is coverage, not evidence: whether a station was listening for a pass is decided from raw heartbeats.

The aggregate refreshes every 30 minutes and reads raw rows for anything newer. A heartbeat restored into an hour it has already refreshed is counted at the next refresh, not before (D-178).

### The completion gate, at a prompt

Stage 19's gate is that **every deferred table has an active producer, consumer, provenance policy, migration test, and retention decision.**

```bash
compose exec api meridian db status                          # at 0024
compose exec jobs meridian profiles build                    # declared masks, and the newest dataset
compose exec jobs meridian profiles build                    # already held, identically
curl -s localhost:8000/api/v1/stations/<id>/profiles | python -m json.tool
curl -s localhost:8000/api/v1/stations/<id>/uptime | python -m json.tool
```

`tests/unit/test_deferred_storage_gate.py` asserts each clause for each table from the source and the documents, with positive controls. `tests/integration/test_deferred_storage_gate.py` runs a simulated and a measured station through ingest, export, labelling and a build, and finds every row labelled as its station is.

---

## Backup and restore

Host tools, standard library only (D-115). They reach the database through `compose exec db`, so no password appears on the host's command line.

> **They address the compose project named in the compose file, `meridian`.** A second stack on the same host, started with `-p <name>`, is reached only with `--project-name <name>`. Without it, both tools act on `meridian`.

### Back up

```bash
python deploy/tools/backup.py --out backups/meridian-$(date -u +%F).dump
```

- **What it writes:** the dump, and beside it `<dump>.manifest.json` with the sha256, TimescaleDB version, migration revision and time.
- **While it runs:** the API keeps running.
- **Existing files:** it never replaces one.
- **If interrupted:** it leaves only `<dump>.partial`.
- **Harmless warning:** `pg_dump` warns about circular foreign keys on `continuous_agg`. That is TimescaleDB's own catalogue, and it does not affect a full dump.

**A dump is a secret.** It holds every station's token hash and every invite. `backups/` and `*.dump` are gitignored; keep them off shared drives.

**A dump is not the whole deployment.** It takes nothing from the ingest raw store, which holds external artefacts exactly as they were retrieved and cannot be recreated without going back to a source that may no longer serve them (D-141). `backup.py` names that path on every run; copying it is § External archive ingest above. Nor does it take the dataset snapshots (D-144); § Dataset snapshots above.

### Restore

```bash
python deploy/tools/restore.py backups/meridian-2026-09-14.dump
```

**This replaces the deployment's database.** Everything written since the backup is lost. It asks you to type `restore`, unless `--yes` is given.

It refuses before changing anything when:
- the manifest is missing;
- the dump's sha256 differs from the manifest's;
- the database image would install a different TimescaleDB version than the dump came from.

It then:
1. stops `api` and `jobs`;
2. recreates the database;
3. runs TimescaleDB's pre-restore step, `pg_restore`, then the post-restore step;
4. runs `migrate`, so a dump from an older release reaches this code's head and the roles' grants, which `pg_restore --no-acl` left out, are applied again (D-207);
5. starts whichever of `api` and `jobs` was running, and waits for `/healthz`.

If a step after the database is dropped fails, `api` and `jobs` stay stopped on purpose. Fix the cause and run the restore again.

Afterwards, check with `compose exec api meridian db status`.

### Every night, automatically

`deploy/systemd/` holds two timers for the host that runs the stack (D-209):

| Unit | When | Does |
|---|---|---|
| `meridian-backup.timer` | nightly, 02:47 | `scheduled_backup.py`: a dump named for its UTC time, checked against its manifest, then retention |
| `meridian-restore-drill.timer` | Sundays, 03:47 | `restore_drill.py --latest backups`: the newest dump restored into a scratch database and checked |

Install them once. Edit `WorkingDirectory` to the checkout's path and `User` to the account that runs `docker compose` in both `.service` files, then:

```bash
sudo cp deploy/systemd/meridian-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meridian-backup.timer meridian-restore-drill.timer
systemctl list-timers 'meridian-*'
```

**Retention** keeps every dump from the last 7 days, the newest of each of the last 4 weeks, and the newest of each of the last 6 months: 17 at most. The newest is never deleted. A dump you took by hand under another name, such as `backups/meridian-before-upgrade.dump`, is never deleted either. Change the policy with `--keep-daily`, `--keep-weekly` and `--keep-monthly` in the service's `ExecStart`.

**Copy `backups/` off the host.** A dump on the disk that holds the database does not survive that disk. The timers do not do this for you:

```bash
rsync -a --chmod=F600 backups/ you@elsewhere:meridian-backups/
```

### The restore drill

```bash
python deploy/tools/restore_drill.py --latest backups       # or a named dump
```

It restores into `meridian_restore_drill` beside the live database, never into it, checks the migration and reads every table, prints each table's row count, and drops the scratch database whatever happened. It needs free disk for one more copy of the database while it runs. Exit 0 means the dump would restore. **When it fails:** `journalctl -u meridian-restore-drill` says which step; a checksum failure means the file changed after it was written, so take a fresh backup now and check the disk.

CI also performs the full round trip, back up, restore and compare row counts, on every pull request, and `tests/integration/test_restore_drill.py` runs the drill against the test database.

---

## Failure recovery

What happens by itself when each part fails, what to do, and the test that proves the recovery works (D-211). A station's own state lives in its state directory: `credentials.json`, `registration_key`, `held.json`, `outbox/` and `captures/`.

| Failure | What happens by itself | What to do | Proven by |
|---|---|---|---|
| **The database is lost or damaged** | The API answers 503 on `/healthz`; `DatabaseUnavailable` fires. Stations keep executing what they hold and queue their reports. | Restore the newest dump that passed the drill, below. | CI's backup-and-restore round trip; `test_restore_drill.py`; `test_backup_tools.py` |
| **A station lost `credentials.json`**, and kept `registration_key` | It stops and says so; it does not re-register on its own (D-024). | Within an hour of registering and before its first heartbeat, run it with the same invite again. Otherwise `meridian invite create --for-station <id>` and give it that invite: same `station_id`, new token (D-034). | `test_psycopg_registry.py` recovery rows; `test_client_registration.py`; `test_a_revoked_station_is_readmitted_by_a_bound_invite` |
| **A station lost `registration_key` too** | Nothing can prove it is the same station. | `meridian station revoke --station-id <old>`, then register it with a fresh invite. Its history stays under the old id. | — by design; nothing may mint a token for a station without its key |
| **A damaged file in a station's state** | A damaged `credentials.json` or `held.json` stops the client rather than being read as empty; a damaged queued observation is moved to `outbox/failed/` and the rest still send (D-073). | Look at the file the log names. A damaged observation in `outbox/failed/` is lost; it is kept for inspection. | `test_credentials.py`, `test_held_assignments.py`, `test_observation_queue.py`, `test_capture_folder.py` |
| **An observation upload interrupted** | The report stays queued until acknowledged and is sent again. The platform keys it on the assignment, so a second copy of the same report is answered as the first was and stored once (D-015, D-071). | Nothing. | `test_a_lost_acknowledgement_does_not_produce_a_second_observation`; `test_an_identical_resubmission_returns_the_identical_acknowledgement` |
| **A migration fails** | The upgrade is one transaction, so the database stays at the revision it had; `migrate` exits non-zero and `api` and `jobs` do not start. | `compose logs migrate`. Run the previous image (`MERIDIAN_IMAGE=…:sha-<previous>`) while the migration is fixed, then `compose up -d`. | `test_failure_recovery.py`, with a revision that fails half way |
| **The jobs process dies mid-round** | Compose restarts it. Each task commits only when it finishes, so nothing half-written survives, and the next round schedules what the lost one would have. Assignments already delivered stand. | Nothing, unless `ScheduledTaskStalled` fires; then § Alerts. | `test_failure_recovery.py`, dying after the scheduler wrote; `test_jobs_rounds.py` |
| **The platform restarts during a pass** | The station keeps executing what it holds, records, queues the result, and sends it when the platform is back. Its heartbeats fail meanwhile, so it may read `stale` for a minute; a missing heartbeat is not a miss (rule 7). | Nothing. | `test_reception_continues_while_the_platform_is_unreachable`; `test_a_station_receives_holds_executes_and_survives_an_outage`; `test_a_station_restarted_before_delivery_still_delivers` |

### After a restore

A restore returns the database to the moment of the dump. What happened after it is gone, and the network notices in three ways:

- **A station registered since the dump** is unknown. Its token is refused with 401 and it stops. Issue it a fresh invite; a bound invite cannot name a station the database does not have.
- **A station whose token was rotated since the dump** holds a token the restored database has never seen. It gets 401 and stops. Issue a bound invite, and it recovers onto the same `station_id` with its `registration_key`.
- **Reports for assignments issued since the dump** are refused as `unknown_assignment` and set aside in the station's `outbox/failed/`. The observations are lost to the record; the files remain.

Run the restore drill first if there is time, so you restore a dump that is known to restore.

---

## Fault drills, scale runs and the long run

Stage 21's tools. Every result they produce is **simulated** and is labelled so; `docs/SCALE-AND-FAULTS.md` holds the recorded ones. D-188 to D-198 say why each is built the way it is.

### Station faults

The simulator breaks its own stations, from its seed, under a named scenario, and writes every fault to a ledger in its state directory (`faults.jsonl`, or `--ledger PATH`):

```sh
python -m meridian_sim.station --scenario chaos --count 10 --ledger faults.jsonl
```

| Scenario | What breaks |
|---|---|
| `network`, `upload`, `restart`, `receiver`, `revoked` | Stage 10's five, one at a time |
| `heartbeat`, `partition`, `slow`, `drift`, `decoder`, `declines` | Stage 21's six, one at a time (D-188) |
| `faulty` | Stage 10's four recurring faults together |
| `chaos` | every recurring fault together; never a revoked token |

The ledger is ground truth and stays on the simulator's side: it is never sent over MSP and never stored in the database (D-189).

### Platform faults

`deploy/tools/chaos.py` breaks the platform from the host, as an operator would, and writes to the same ledger format (D-194):

```sh
python deploy/tools/chaos.py inject database_restart --ledger faults.jsonl
python deploy/tools/chaos.py inject scheduler_down --duration 300 --ledger faults.jsonl
python deploy/tools/chaos.py run --seed 4471 --hours 72 --ledger faults.jsonl --plan
```

`platform_restart` and `database_restart` restart the service; `scheduler_down` stops the jobs process and starts it again; `api_paused` pauses the API past a station's timeout. The mend always runs, even when the break failed. `--plan` prints a seeded schedule and does nothing.

### Judging a run

```sh
meridian reliability faults --ledger faults.jsonl [--prometheus http://prometheus:9090] [--json verdict.json]
```

For every fault in the ledger it prints each question, answered from what the platform stored:
- `held` — was the silence real?
- `detected` — offline within ninety seconds, or correctly never?
- `no_new_work` — was nothing decided for the station while it was offline?
- `replanned` — was its unbegun work revoked inside the outage?
- `no_false_miss` — is no confirmed miss one the station did not miss?
- `declines_honoured` — was every decline revoked?
- `recovered` — was the station heard again afterwards?
- `alerted` — when did `StationOffline` fire? Only with `--prometheus`.

A dash is a question that did not apply. It exits 1 if any check failed. Inside a container, whose filesystem is read-only, pipe the ledger in:

```sh
docker compose exec -T api meridian reliability faults --ledger - < faults.jsonl
```

**When a check fails.** The detail names the assignments or instants.
- A `held` failure means the fault did not stop the station's heartbeats, which is a broken injection, not a broken platform.
- A `replanned` failure lists work a round left to a station that was offline. `meridian reliability explain` and `assignment_revocations` show what happened to it (D-196).
- A `no_false_miss` failure is a reliability figure counting a miss that did not happen, and is the one to look at first.

### Scale runs

The fleet's side and the platform's, side by side (D-197):

```sh
python deploy/tools/scale_probe.py --api http://127.0.0.1:8000 --jobs http://127.0.0.1:9464 \
    --seconds 360 --out probe-50.json &
python -m meridian_sim.station --count 50 --rounds 12 --scale-report sim-50.json
```

The probe needs `METRICS_TOKEN`. The jobs process's metrics are on `JOBS_METRICS_PORT`, inside the compose network unless published. `docs/SCALE-AND-FAULTS.md` has the whole procedure and the last results.

**Pass generation is the cost that grows** with stations × satellites: about 16 s per round at fifty stations and two satellites. Check it in the probe's `job_tasks` before adding satellites to the catalogue.

### The long run

`deploy/tools/long_run.py` runs the whole stack for hours: stations under `chaos`, platform faults from `chaos.py`'s seeded plan, and a sample of the stack every `--sample-every-minutes`. It then settles, judges and writes `report.json` (D-198):

```sh
python deploy/tools/long_run.py --hours 72 --seed 4471 --out runs/long-72h --up --stations 10
```

`--up` brings the stack up with the `sim` and `metrics` profiles. Run it under its own `--project-name`, with `API_PORT` and `GRAFANA_PORT` set, to keep it away from a stack already running.

It exits 1 on any of:
- an alert with no fault near it;
- a container that died on its own, or is not running at the end;
- observations still queued after settling;
- a failed verdict.

`--fault-gap-minutes` shortens the mean time between platform faults for a rehearsal: an hour is right for three days and too rare for two hours.

`--judge-only` judges a finished run again from `--out` and the stack it left standing, without re-running it: for a judgement lost to anything but the run. On a laptop, keep the host awake with the lid closed and idle sleep inhibited, or the run fails as not unattended.

---

## Monitoring

### Where to look

| What | Where |
|---|---|
| Dashboard *Meridian platform* | Grafana, `http://<host>:3001`, folder *Meridian* |
| Firing alerts | the table at the bottom of that dashboard |
| Alertmanager's view, silences | `compose exec alertmanager amtool alert query --alertmanager.url=http://127.0.0.1:9093` |
| Prometheus targets | `compose exec prometheus wget -qO- http://127.0.0.1:9090/api/v1/targets` |
| Logs | `compose logs --since 30m <service>` — each service keeps three 10 MB files (D-114), and the platform's lines are redacted before they are written (D-204) |
| Raw metrics | `curl -H "Authorization: Bearer $METRICS_TOKEN" http://localhost:8000/metrics` |

Prometheus and Alertmanager are not published on the host. Grafana is, so its admin password must not be `change-me` on a network you share.

**`[redacted]` in a log line** is the platform removing a secret before writing it (D-204): a bearer token, a value whose field names it as a token, key, password, pepper or secret, a password inside a URL, or any secret it loaded at start-up. A malformed request's log line still names the field that failed and why.

**Reading the numbers:**
- **`simulated`:** every station series carries it. Measured and simulated stations are never summed into one figure.
- **API panels:** `meridian_http_*` comes from every API worker.
- **Pool panel:** reflects whichever worker answered the scrape.
- **Scheduling panels:** `meridian_job_*`, `meridian_passes_computed`, `meridian_scheduler_candidates`, `meridian_scheduler_runs_total{status}`, `meridian_scheduler_solver_seconds` and `meridian_scheduler_history_age_seconds` come from the jobs process alone. A rising `status="fallback"` count means the schedule is greedy's; the history age is how long the model's history has gone unrefreshed (D-170).
- **Reliability series (no Grafana panel yet):** `meridian_passes_classified` (by class, so confirmed misses and indeterminate satellites are two of its series) and `meridian_loss_budget_remaining_ratio`, read by the API from `pass_classifications` over the SLO window (D-186). A population with nothing classified yet publishes neither, rather than zeros that would mean "not measured" (D-086). Passes settle a day after their window, so a fresh deployment shows nothing here for its first day.

### Delivering alerts

By default every alert goes to a receiver that sends nothing. Alerts remain visible in Grafana and Alertmanager. To deliver them:

1. Copy `deploy/alertmanager/alertmanager.example.yml` to `deploy/alertmanager/alertmanager.local.yml`, and edit it.
2. Put a webhook URL in `deploy/alertmanager/webhook_url` and an SMTP password in `deploy/alertmanager/smtp_password`. All three files are gitignored.
3. Set `ALERTMANAGER_CONFIG_FILE=alertmanager.local.yml` in `deploy/.env`, then `compose --profile metrics up -d alertmanager`.

The rules are `deploy/prometheus/rules/meridian.yml`, and each one's tests are in `deploy/prometheus/tests/meridian.test.yml`. Change a rule and its test together.

**Some thresholds follow settings.** `ScheduledTaskStalled`'s 900 s is three rounds at the default interval, and the liveness thresholds follow the 30 s heartbeat. The rule files do not read `.env`, so changing `SCHEDULE_INTERVAL_S` or `HEARTBEAT_INTERVAL_S` means editing the numbers there too.

---

## Alerts

One section per alert: what it means, and the first thing to check. Each rule's `runbook` annotation links here.

### ApiUnavailable

**Critical.** Prometheus has not reached `api:8000/metrics` for a minute. Station alerts go quiet at the same time, because station counts come from the API's scrape.

1. `compose ps api`, then `compose logs --since 10m api`. A restart loop names its reason. A placeholder secret in public mode is refused at start-up with the variable's name.
2. **If the container is healthy,** compare `deploy/prometheus/metrics_token` with `METRICS_TOKEN`. A wrong token is answered with the same 404 as a wrong URL, so it reads as a target that is down (D-087).

### DatabaseUnavailable

**Critical.** The API is running but its scrape could not read the database. Heartbeats and observations are refused while this fires. Stations keep their reports queued and send them on recovery.

1. `compose ps db`, then `compose logs --since 10m db`.
2. Check free disk space on the host. The database and every container's logs share it.

### SchemaMigrationMismatch

**Critical.** The database is not at the migration this code expects. A migration that fails outright stops the API from starting and fires `ApiUnavailable` instead. This alert is the database that is up at another revision: restored from an older backup, or left behind by an image rolled back.

`compose exec api meridian db status` says which case it is:
- **Behind the code:** run `compose run --rm migrate`.
- **At a revision this code does not define:** a newer image migrated it. Run that image; migrating from this one cannot help.

### StationStale

**Warning.** A station has missed two heartbeats; its last one is over 60 s old. This is SC-5's detection signal. It fires with no delay and has been measured firing 59 s after a station was stopped.

1. The dashboard's station list shows which station, and whether it is simulated.
2. A station that recovers within a minute was a network blip. One that goes on to `StationOffline` is down.

### StationOffline

**Warning.** A station has sent no heartbeat for over 90 s.

1. Check the station's power and network, and the station client's logs on it.
2. **If the station is retired rather than broken,** withdraw its token with `meridian station revoke`. It still counts as offline, and this alert keeps firing: no command removes a station yet. Silence the alert in Alertmanager rather than leaving it firing unread.
3. **For a simulated station:** fault-injection scenarios take stations offline on purpose. Check `SIMULATOR_SCENARIO`.

### HeartbeatIngestionStopped

**Critical.** No heartbeat from any station has reached the platform for five minutes, while stations that have heartbeated before exist. One station down is `StationOffline`; every station at once points at the path between them and the platform.

1. `compose logs --since 10m tunnel`, and whether the tunnel service is healthy.
2. Run `python deploy/tools/verify_public_surface.py https://<hostname>` from outside the network.
3. Check the MSP error panel for a spike of one error code.

If every station was switched off on purpose, this is expected.

### ObservationsOverdue

**Warning.** Assignments are still held or in progress 45 minutes after their window ended, with no report. The overdue count starts 15 minutes after the window, and the alert waits 30 minutes more. This is not a miss, and nothing is classified from it (D-111).

1. Is the station online? An online station holding reports points at its upload path: check the client's logs and the MSP error panel.
2. An offline station reports when it returns; its outbox survives restarts.

### SchedulerUnavailable

**Critical.** Prometheus has not reached the jobs process for a minute, so nothing is being scheduled.

1. `compose ps jobs`, then `compose logs --since 10m jobs`.
2. It refuses to start on an invalid `SCHEDULE_INTERVAL_S`, `SCHEDULE_HORIZON_S` or `API_LOG_LEVEL`, and the log says which.
3. A token mismatch reads as down here too; see `ApiUnavailable`.

### ScheduledTaskStalled

**Critical** for every task but `profiles`, which is a **warning**. A task (`task` label: `pass_generation`, `schedule`, `profiles`, `expiry_sweep` or `reliability`) has not completed in over 15 minutes, which is three rounds at the default interval. One failed round is logged and retried; three in a row is a problem.

1. `compose logs --since 30m jobs`. Each failed round logs `<task> failed; the next round will try again` with the exception.
2. The *Task failures per hour* panel shows whether it fails every round or only some.
3. Scheduling keeps working from stored passes while `pass_generation` fails, until the horizon runs out.

### ScheduledTaskNeverSucceeded

**Critical** for every task but `profiles`, which is a **warning**. The jobs process has been up for 15 minutes and the named task has not completed once. After a restart there is no earlier success to measure a stall from, so this alert covers that case.

The first checks are the same as `ScheduledTaskStalled`. A failure on every round from start-up usually means a database the jobs process cannot reach, or one at a migration it does not expect (`meridian db status`).

An empty catalogue is not a failure: rounds complete with zero passes, and `meridian_passes_computed` reads 0. Nor is having no labelled dataset: `profiles` completes having written only the declared masks. A `profiles` task that fails every round usually means a newest dataset that no longer matches its manifest; `uv run meridian snapshot verify <dir>` says which.

A failing `reliability` task stops new passes being classified, and so freezes every reliability figure where it was. It reads the file `MERIDIAN_RELIABILITY_CONFIG` names, and refuses to start on one it cannot obey; the log says which setting.

### LossBudgetThresholdReached

**Warning.** Less than a quarter of a population's loss budget is left, and has been for an hour. SC-4's capture target of 90% allows a tenth of the passes a station could have captured to be lost inside the 30-day window (D-185), and three quarters of that is gone. The `simulated` label says which population; the two are never pooled.

A lost pass is not the same as a miss. Every debit carries its reason, and only `confirmed_miss` means the station was confirmed listening and heard nothing (CLAUDE.md rule 7).

1. `meridian reliability report` prints the budget, its debits by reason, and each station's share. The reason says where to look:
   - `station_unavailable`: the station was off or not heartbeating during its passes. Check `StationOffline`'s history and the host.
   - `station_not_confirmed_listening`: it heartbeated but never confirmed it was tuned to the pass. Check its client log and its declared capabilities.
   - `assignment_declined`: it was up and did not take the work. Check the client's queue and clock.
   - `signal_no_decode` or `confirmed_miss`: it listened. The receive chain, the antenna or the decoder is the suspect.
2. `meridian reliability explain <assignment id>` prints one debit's evidence: the assignment's state, the report, the heartbeats and the registry's listening answer.
3. Nothing is retried. A lost pass stays lost, and the budget recovers only as the window moves past it.

### Reliability figures

`meridian reliability report` prints each figure as a count over a count, with its 95% interval, for measured and simulated stations apart:
- pass capture rate (SC-4);
- confirmed miss rate;
- station availability;
- assignment completion and execution rates;
- report delay;
- the loss budget.

Each target is marked as SC-4's, SC-5's, or proposed (D-184). `GET /api/v1/reliability` serves the same report.

`meridian snapshot reliability <dataset>` counts the same figures from an evaluation dataset. It cannot give availability or report delay, and says why rather than printing a number.

The settings are `deploy/reliability.toml.example`. Point `MERIDIAN_RELIABILITY_CONFIG` at a copy to change them, for the jobs service and the API alike: the API finds the classifications the jobs service wrote by the hash of the `[classification]` table.
