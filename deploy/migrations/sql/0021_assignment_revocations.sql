-- 0021 — every revocation and reinstatement, kept after the assignment moves on
--
-- An assignment's `revoked_reason` and `revoked_at` describe its state now, and
-- D-171's reinstatement clears them when a station that was offline comes back
-- still holding the work. That is right for the state, and it leaves the
-- platform with no record that it ever took the work back: nothing could say
-- how often the scheduler revoked an offline station's work, and Stage 21's
-- gate could not show that it did (D-196).
--
-- **Append-only, one row per event.** Written by the same statement that moves
-- the assignment, so an event and the change it records cannot disagree. A
-- reinstatement is a row of its own rather than an update to the revocation,
-- for the reason `pass_classifications` is append-only: what was true at an
-- instant stays readable after it stops being true.
--
-- **Plain table, no hypertable.** A revocation is rare next to a heartbeat —
-- one per piece of work a station declines or loses by going offline.
--
-- See DECISIONS.md D-171, D-196.

create table assignment_revocations (
    event_id       bigint      generated always as identity primary key,
    assignment_id  text        not null references assignments (assignment_id),
    station_id     text        not null references stations (station_id),
    event          text        not null
                               check (event in ('revoked', 'reinstated')),
    -- Why it was revoked; a reinstatement has no reason of its own.
    reason         text        check (reason in ('declined', 'offline')),
    -- The platform's instant for the decision: the scheduling round's `now` for
    -- an offline revocation, the heartbeat's for a decline or a reinstatement.
    at             timestamptz not null,

    constraint assignment_revocation_event_reason
        check ((event = 'revoked') = (reason is not null))
);

create index assignment_revocations_assignment_idx
    on assignment_revocations (assignment_id, at);
create index assignment_revocations_station_idx
    on assignment_revocations (station_id, at);

comment on table assignment_revocations is
    'Every revocation of an assignment and every reinstatement of one, '
    'append-only, written with the change it records. docs/DECISIONS.md D-196.';
