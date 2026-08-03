-- 0003 — satellites, transmitters, element sets

create table satellites (
    -- 'norad:NNNNN' as text, not a bare integer: objects without NORAD ids exist
    -- (DATA-MODEL.md conventions).
    satellite_id    text        primary key,
    name            text        not null,
    orbital_regime  text        not null default 'leo'
                                check (orbital_regime in ('leo', 'meo', 'geo', 'heo', 'other')),
    -- The silent-satellite confound in EVALUATION.md §5: a pass that produced
    -- nothing may mean a bad prediction or a transmitter that was switched off.
    active          boolean     not null default true,
    deleted_at      timestamptz
);


-- A child table rather than a column on satellites (D-021). The scheduler selects
-- a transmitter by joining against a capability's frequency range —
-- freq_min_hz <= centre_freq_hz <= freq_max_hz — and that predicate is not
-- indexable inside a JSON blob.
create table satellite_transmitters (
    id              bigint      generated always as identity primary key,
    satellite_id    text        not null references satellites (satellite_id),
    centre_freq_hz  bigint      not null,
    mode            text        not null,
    polarisation    text,
    bandwidth_hz    bigint,
    active          boolean     not null default true,
    source          text        not null default 'manual',
    deleted_at      timestamptz
);

create index satellite_transmitters_freq_idx
    on satellite_transmitters (centre_freq_hz) where deleted_at is null;


-- Every element set ever retrieved, for every tracked object. NEVER OVERWRITTEN
-- — the historical series is what makes uncertainty modelling possible, and
-- element-set age is a first-class feature everywhere in this project.
create table element_sets (
    id              bigint      generated always as identity primary key,
    satellite_id    text        not null references satellites (satellite_id),
    epoch           timestamptz not null,
    retrieved_at    timestamptz not null default now(),
    line1           text        not null,
    line2           text        not null,
    source          text        not null
                                check (source in ('celestrak', 'spacetrack', 'manual', 'simulator')),

    -- The same set retrieved twice is one row. Divergence between *successive*
    -- sets is a derived view, never a stored column.
    constraint element_set_unique unique (satellite_id, epoch, source)
);

create index element_sets_satellite_epoch_idx
    on element_sets (satellite_id, epoch desc);

comment on table element_sets is
    'Append-only. Never update or delete a row here; the series is the measurement.';
