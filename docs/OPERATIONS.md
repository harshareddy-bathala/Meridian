# Operations

How to run a Meridian deployment: bring it up, admit stations, keep it scheduling, back it up, and read what it says when something goes wrong.

Every command runs from the repository root. `compose` below is shorthand for:

```bash
docker compose -f deploy/docker-compose.yml
```

Decisions this page puts into practice: D-109 to D-115, D-120 to D-128 for station reception, D-138 to D-142 for external archive ingest, and D-143 to D-147 for dataset snapshots. The staged build order is in `SOFTWARE-IMPLEMENTATION-ROADMAP.md`.

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

The platform refuses to start in public mode while any of them is still `change-me`. Setting `CLOUDFLARE_TUNNEL_TOKEN` or `TUNNEL_HOSTNAME`, or a non-loopback `PUBLIC_BASE_URL`, puts it in public mode.

`TOKEN_HASH_PEPPER`, `REGISTRATION_INVITE_TOKEN` and `METRICS_TOKEN` can instead be read from files with `*_FILE` variables. The file must be mounted into the containers by an override file.

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
| `public` | the Cloudflare tunnel | `CLOUDFLARE_TUNNEL_TOKEN` and real secrets |
| `sim` | catalogue and invite seeding, a simulated fleet | nothing more |

```bash
compose --profile metrics --profile public up -d
compose ps                      # every service with a healthcheck says (healthy)
curl http://localhost:8000/healthz
```

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
| Load satellites | `compose exec api meridian catalogue load --file deploy/catalogue/development.json` |
| Migration status | `compose exec api meridian db status` — exit 0 only when at head |
| Apply migrations | `compose run --rm migrate` |
| Generate passes now | `compose exec api meridian passes generate --from <ISO-8601 Z> --to <ISO-8601 Z>` |
| Schedule now | `compose exec api meridian schedule --from <ISO-8601 Z> --to <ISO-8601 Z> --config A` |
| One scheduling round now | `compose exec jobs meridian jobs run --once` |
| Run the simulator | `compose --profile sim up -d` — `SIMULATOR_*` in `deploy/.env` set count, seed and scenario |
| Check the public surface | `python deploy/tools/verify_public_surface.py https://<hostname>` |
| Fetch and load an external archive | `uv run meridian-ingest …` — its own binary, not in the image; § External archive ingest |
| Freeze the database into a raw snapshot | `compose run --rm --no-deps … api meridian snapshot export --since <ISO-8601 Z>` — § Dataset snapshots |
| Label a raw snapshot | `uv run meridian snapshot label <dir>` — needs no database; § Dataset snapshots |
| Generate a report | `meridian report` — not built yet; Stage 22 |

**Scheduling needs no command.** The `jobs` service generates passes and schedules them under configuration A every `SCHEDULE_INTERVAL_S` (default 300), over the next `SCHEDULE_HORIZON_S` (default 21600), in every deployment (D-110). The commands above are for filling a horizon by hand. Both tasks are idempotent, so running them beside the service writes nothing twice.

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

## Dataset snapshots

Prediction and evaluation never read the live tables. They read an **immutable snapshot**, so every number in a report can be regenerated from a snapshot, a configuration and a seed (rule 8). There are two steps, and only the first one needs the database (D-143):

1. **`export`** freezes the database into a **raw snapshot**: every table the labels need, read at a single instant, plus the registry's answer to "was this station listening", stored next to each closed assignment.
2. **`label`** turns a raw snapshot and a labelling configuration into an **evaluation dataset**: one label per pass (D-146, D-147), plus the archive's receptions in the archive's own vocabulary. It opens files and nothing else.

Decisions this section puts into practice: D-143 to D-147.

### Where they go

Everything goes under one **datasets root**: `--root`, else `$MERIDIAN_DATASETS_ROOT`, else `data/datasets`. The `data/` directory is gitignored.

```text
data/datasets/
├── snapshots/<as_of>-<hash12>/    raw snapshots: one .jsonl per table, listening.jsonl, manifest.json
└── evaluation/<hash12>/           evaluation datasets: labels.jsonl, archive_receptions.jsonl, manifest.json
```

- **Names come from content.** A directory is named after the sha256 of its manifest. That manifest lists every file's digest and row count, and the hash leaves out only `created_at`. So the name *is* the check, and two runs that produce the same content land in the same directory. The second run writes nothing and says `already held, identically`.
- **Directories are read-only.** Each directory is sealed when it is published, so `rm -rf` fails until you `chmod -R u+w`. As with the raw store, that is immutability working, not a permissions fault.

### The commands

| Task | Command |
|---|---|
| Freeze the database from a start time to now | `meridian snapshot export --since <ISO-8601 Z>` — reads `DATABASE_URL` |
| Label a raw snapshot | `meridian snapshot label <raw snapshot dir> [--config snapshot.toml]` |
| Check a snapshot or dataset against its manifest | `meridian snapshot verify <dir>` |

- **There is no `--until`.** A raw snapshot ends at its export transaction's own `now()`. Several tables hold current state rather than history, so a snapshot can only describe *now*.
- **`--since` must carry its offset**, for the same reason as in `meridian-ingest`.
- **Labelling settings:** copy `deploy/snapshot.toml.example`. Its values are the defaults, and unknown keys are refused. The settings are hashed by value into the dataset's manifest, so a changed setting gives a different dataset rather than an overwritten one.
- **What `label` prints:**
  - every non-zero count by name, with measured and simulated always kept apart;
  - how many confirmed-listening silences it could not judge (`satellite_state_indeterminate`). EVALUATION.md §5 asks for that share to be stated beside any figure that depends on it.

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
| 1 | It ran and failed: the database was unreachable, `--since` was unreadable, the settings were refused, or it was pointed at something that is not a raw snapshot |
| 2 | The command line was wrong |
| **3** | **The snapshot or dataset no longer matches its manifest** |

Code 3 means the same here as it does in `meridian-ingest verify`. `label` returns it too: a raw snapshot that fails its own check is refused, not labelled.

### The completion gate, at a prompt

Stage 15's gate is that **the same raw snapshot and the same configuration always produce the same evaluation dataset hash.** To demonstrate it:

```bash
uv run meridian snapshot label data/datasets/snapshots/<dir>      # (written)
uv run meridian snapshot label data/datasets/snapshots/<dir>      # already held, identically
unshare -rn uv run meridian snapshot --root /tmp/elsewhere label data/datasets/snapshots/<dir>
```

All three print the same hash. The last one runs with no network interfaces at all, into a root that has never seen the dataset.

Two test files assert the same thing:
- `tests/unit/test_snapshot_gate.py` labels one snapshot three ways, under a guard that fails if anything opens a connection. A positive control shows the guard firing. It also labels in two processes with different hash seeds.
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
4. runs `migrate`, so a dump from an older release reaches this code's head;
5. starts whichever of `api` and `jobs` was running, and waits for `/healthz`.

If a step after the database is dropped fails, `api` and `jobs` stay stopped on purpose. Fix the cause and run the restore again.

Afterwards, check with `compose exec api meridian db status`.

A backup schedule, retention and a restore drill on the real deployment are Stage 23's. CI already performs the round trip on every pull request.

---

## Monitoring

### Where to look

| What | Where |
|---|---|
| Dashboard *Meridian platform* | Grafana, `http://<host>:3001`, folder *Meridian* |
| Firing alerts | the table at the bottom of that dashboard |
| Alertmanager's view, silences | `compose exec alertmanager amtool alert query --alertmanager.url=http://127.0.0.1:9093` |
| Prometheus targets | `compose exec prometheus wget -qO- http://127.0.0.1:9090/api/v1/targets` |
| Logs | `compose logs --since 30m <service>` — each service keeps three 10 MB files (D-114) |
| Raw metrics | `curl -H "Authorization: Bearer $METRICS_TOKEN" http://localhost:8000/metrics` |

Prometheus and Alertmanager are not published on the host. Grafana is, so its admin password must not be `change-me` on a network you share.

**Reading the numbers:**
- **`simulated`:** every station series carries it. Measured and simulated stations are never summed into one figure.
- **API panels:** `meridian_http_*` comes from every API worker.
- **Pool panel:** reflects whichever worker answered the scrape.
- **Scheduling panels:** `meridian_job_*`, `meridian_passes_computed` and `meridian_scheduler_candidates` come from the jobs process alone.
- **Not published until Stage 20:** confirmed misses, indeterminate outcomes and loss budget remaining. A zero there would mean "not measured" (D-086, D-111).

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

**Critical.** A task (`task` label: `schedule` or `pass_generation`) has not completed in over 15 minutes, which is three rounds at the default interval. One failed round is logged and retried; three in a row is a problem.

1. `compose logs --since 30m jobs`. Each failed round logs `<task> failed; the next round will try again` with the exception.
2. The *Task failures per hour* panel shows whether it fails every round or only some.
3. Scheduling keeps working from stored passes while `pass_generation` fails, until the horizon runs out.

### ScheduledTaskNeverSucceeded

**Critical.** The jobs process has been up for 15 minutes and the named task has not completed once. After a restart there is no earlier success to measure a stall from, so this alert covers that case.

The first checks are the same as `ScheduledTaskStalled`. A failure on every round from start-up usually means a database the jobs process cannot reach, or one at a migration it does not expect (`meridian db status`).

An empty catalogue is not a failure: rounds complete with zero passes, and `meridian_passes_computed` reads 0.

<!-- Stage 20 adds LossBudgetThresholdReached here, with confirmed misses and
     indeterminate outcomes, once platform/reliability decides what a miss is. -->
