-- 0009 — element sets are identified by their contents, not by their epoch
--
-- `element_sets` is append-only and its comment says "the series is the
-- measurement". The key it shipped with — unique (satellite_id, epoch, source) —
-- quietly contradicted that. Two genuinely different element sets can carry the
-- same epoch from the same source: a catalogue correction, a re-fit after a
-- manoeuvre, or an operator republishing a set for the same epoch. The second
-- one was rejected, and the series lost a measurement behind a constraint that
-- read like a safety feature. See DECISIONS.md D-057.


-- A function rather than an inline expression, for the same two reasons
-- observation_id (0005) is one: a generated column requires an immutable
-- expression, and the store layer needs to compute this itself to answer "do we
-- already hold this set?" without reading the row back.
--
-- IMMUTABLE is a promise, and it is worth stating why it holds here. convert_to()
-- is marked STABLE because text-to-bytes depends on the server encoding in the
-- general case. A two-line element set is fixed-width ASCII by format definition
-- — 69 characters per line, from a character set that predates the format — and
-- every server encoding PostgreSQL supports is ASCII-compatible, so the bytes
-- are identical under any of them.
--
-- The separator is not decoration. Hashing line1 || line2 with nothing between
-- them would let a pair of lines split at a different boundary hash identically.
-- TLE lines are fixed-width so that cannot arise today, but the guarantee costs
-- one byte and does not depend on the format staying fixed-width forever.
create function element_set_content_hash(line1 text, line2 text)
returns bytea
language sql
immutable
strict
parallel safe
as $$
    select sha256(convert_to($1 || E'\n' || $2, 'UTF8'))
$$;

comment on function element_set_content_hash(text, text) is
    'Content identity of an element set. See docs/DECISIONS.md D-057.';


-- Stored rather than virtual: it is read by every insert (the unique index) and
-- written once. A stored generated column rewrites the table when added, which
-- is free today because this archive is nearly empty and will not be free on
-- the Pi in two years — a future column here should be planned, not bolted on.
alter table element_sets
    add column content_sha256 bytea
        generated always as (element_set_content_hash(line1, line2)) stored;


-- The old key permitted one row per (satellite, epoch, source) and so discarded
-- the second of two different sets sharing an epoch.
alter table element_sets
    drop constraint element_set_unique;

-- Source stays in the key deliberately. The same lines arriving from celestrak
-- and from spacetrack are two retrievals with two provenances, and D-049 made
-- `source` the way this table records provenance; collapsing them to one row
-- would discard whichever arrived second. Re-retrieving the same lines from the
-- same source remains one row, which is what the table comment already promised.
alter table element_sets
    add constraint element_set_content_unique
        unique (satellite_id, source, content_sha256);

comment on column element_sets.content_sha256 is
    'sha256 of the two lines, separated by a newline. Identity of the set.';
