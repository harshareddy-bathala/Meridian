# Operations

How to run a Meridian deployment: bring it up, admit stations, keep it scheduling, back it up, and read what it says when something goes wrong.

Every command runs from the repository root. `compose` below is shorthand for:

```bash
docker compose -f deploy/docker-compose.yml
```

Decisions this page puts into practice: D-109 to D-115. The staged build order is in `SOFTWARE-IMPLEMENTATION-ROADMAP.md`.

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
| Build a dataset snapshot | `meridian snapshot` — not built yet; Stage 15 |
| Generate a report | `meridian report` — not built yet; Stage 22 |

**Scheduling needs no command.** The `jobs` service generates passes and schedules them under configuration A every `SCHEDULE_INTERVAL_S` (default 300), over the next `SCHEDULE_HORIZON_S` (default 21600), in every deployment (D-110). The commands above are for filling a horizon by hand. Both tasks are idempotent, so running them beside the service writes nothing twice.

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
