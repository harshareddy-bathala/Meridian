-- 0019 — An assignment can be taken back, and a pass decided again
--
-- MSP §4.2 says a declined assignment whose window is still ahead may be
-- "reissued elsewhere", and D-022 deferred that to the scheduler because
-- D-008's state machine had no arc for it. See docs/DECISIONS.md D-171.
--
-- **`revoked`** is an assignment the platform withdrew before its window
-- began, never delivered again and never expired: a revoked pass was not
-- missed, declined late or lost; it was taken back. Why is `revoked_reason`:
--   declined  the station held it and then stopped naming it (D-003);
--   offline   its station was offline when a round ran.
-- An offline station's assignment comes back to `held` if the station names it
-- again on its return: MSP has no way to take work from a station, and one
-- that still holds an assignment will execute it.
--
-- **`revision`** numbers a configuration's decisions about one pass. A pass
-- decided again — a skip whose blocker is gone, or a revoked assignment whose
-- station is back — is a new row, so every earlier decision stays as it was
-- made. Revision 0 is every decision made before now, and its id is unchanged.

alter table assignments
    drop constraint assignments_state_check,
    add constraint assignments_state_check
        check (state in ('issued', 'held', 'in_progress', 'reported', 'expired',
                         'revoked')),
    add column revision integer not null default 0
        constraint assignment_revision_counts check (revision >= 0),
    add column revoked_reason text
        constraint assignment_revoked_reason
            check (revoked_reason in ('declined', 'offline')),
    add column revoked_at timestamptz,
    -- A reason is a revocation's, and a revocation has one, with its time.
    add constraint assignment_revocation_whole
        check ((state = 'revoked') = (revoked_reason is not null)
               and (revoked_reason is null) = (revoked_at is null));

alter table assignments
    drop constraint assignment_decision_unique,
    add constraint assignment_decision_unique
        unique (pass_id, model_config, revision);

comment on constraint assignment_decision_unique on assignments is
    'One decision per pass, configuration and revision, so a run can be '
    'repeated and a pass decided again. docs/DECISIONS.md D-066, D-171.';
comment on column assignments.revision is
    'Which decision this is about its pass under its configuration; the '
    'highest is current. docs/DECISIONS.md D-171.';
