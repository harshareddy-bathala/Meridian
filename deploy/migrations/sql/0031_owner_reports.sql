-- 0031 — owner reports: one row per report, and every attempt to deliver it
--
-- Stage 29 tells a station's owner, in plain words, what each pass came to and
-- how the station's week went (D-098). A report is rendered from a versioned
-- template filled with stored results, never by a language model.
--
-- `report_deliveries` holds one row per report, written once (D-301). The
-- rendered text is not stored. `inputs_json` names the stored rows the report
-- was read from, and `template_version` the template that rendered it, so the
-- text can be rendered again at any time. `inputs_sha256` is of the facts read,
-- `content_sha256` of the text sent, and a regeneration must reproduce both.
--
-- A pass report is about one assignment, and takes its station and `simulated`
-- from it; a weekly report is about one station's week, which starts at Monday
-- 00:00 UTC. A report is queued once per subject and channel, whatever the
-- template version: the owner hears about a pass once.
--
-- **No recipient is held here, or anywhere in the database** (D-107, D-300).
-- An address or chat id lives in the sender's secret file, keyed by station.
-- **A simulated station's report goes only to the sink** (rule 5), and the
-- check below makes that the database's rule as well as the sender's.
--
-- `report_delivery_attempts` is append-only, like `region_alert_deliveries`.
-- An attempt sent or abandoned ends its report, and only one may. A failed
-- attempt says when to try again; `error_class` is a short code, never a
-- server's own words, which can echo the address they were refused for.
--
-- Plain tables: their volume is passes and weeks of team-operated stations.

create table report_deliveries (
    id                  bigint      generated always as identity primary key,
    station_id          text        not null references stations (station_id),
    kind                text        not null check (kind in ('pass', 'weekly')),
    assignment_id       text        references assignments (assignment_id),
    period_start        timestamptz,
    channel             text        not null
                                    check (channel in ('email', 'telegram', 'sink')),
    template_version    text        not null
                                    check (template_version ~ '^(pass|weekly)-[0-9]+$'),
    -- The stored rows the report was read from: the observation revision, the
    -- verdict and the diagnosis of a pass; the receptions and warnings of a week.
    inputs_json         jsonb       not null check (jsonb_typeof(inputs_json) = 'object'),
    inputs_sha256       bytea       not null check (length(inputs_sha256) = 32),
    content_sha256      bytea       not null check (length(content_sha256) = 32),
    queued_at           timestamptz not null default now(),
    simulated           boolean     not null,

    constraint report_delivery_subject check (
        (kind = 'pass' and assignment_id is not null and period_start is null)
        or (kind = 'weekly' and assignment_id is null and period_start is not null)
    ),
    constraint report_delivery_template_kind
        check (split_part(template_version, '-', 1) = kind),
    constraint report_delivery_week_starts_monday check (
        period_start is null
        or (extract(isodow from period_start at time zone 'UTC') = 1
            and period_start at time zone 'UTC'
                = date_trunc('day', period_start at time zone 'UTC'))
    ),
    -- Rule 5, as a constraint (D-301).
    constraint report_delivery_simulated_to_sink
        check (not simulated or channel = 'sink')
);

create unique index report_deliveries_pass_once
    on report_deliveries (assignment_id, channel) where kind = 'pass';

create unique index report_deliveries_week_once
    on report_deliveries (station_id, period_start, channel) where kind = 'weekly';

create index report_deliveries_station_idx
    on report_deliveries (station_id, queued_at);

comment on table report_deliveries is
    'One owner report, written once, with the rows it was read from and the '
    'hashes its regeneration must reproduce. No recipient (D-098, D-301).';


create table report_delivery_attempts (
    id                  bigint      generated always as identity primary key,
    delivery_id         bigint      not null references report_deliveries (id),
    attempted_at        timestamptz not null,
    outcome             text        not null
                                    check (outcome in ('sent', 'failed', 'abandoned')),
    error_class         text        check (error_class ~ '^[a-z][a-z0-9_]{0,39}$'),
    next_attempt_at     timestamptz,

    constraint report_delivery_attempt_shape check (
        (outcome = 'sent' and error_class is null and next_attempt_at is null)
        or (outcome = 'failed' and error_class is not null
            and next_attempt_at is not null and next_attempt_at > attempted_at)
        or (outcome = 'abandoned' and error_class is not null
            and next_attempt_at is null)
    )
);

-- A report ends once: sent, or given up on.
create unique index report_delivery_attempts_final
    on report_delivery_attempts (delivery_id) where outcome in ('sent', 'abandoned');

create index report_delivery_attempts_delivery_idx
    on report_delivery_attempts (delivery_id, attempted_at desc, id desc);

comment on table report_delivery_attempts is
    'Every attempt to deliver an owner report, appended; an error is a class, '
    'never a server''s text (D-305).';
