-- 0011 — a scheduling decision records what it scored and what displaced it
--
-- `assignments` already carries `decision` ('scheduled' or 'skipped'), a
-- human-readable `reason` and the `model_config` that produced it. Between them
-- they say *what* the scheduler concluded. They do not say what it concluded it
-- *from*, and for a skip they do not say what won instead.
--
-- PROJECT.md §13 calls the screen showing this reasoning "the entire project".
-- A screen that can show "skipped: conflicts with another pass" but cannot name
-- the pass, or rank the two, is showing an assertion rather than a decision.
--
-- Both columns are written at the moment the decision is made and never
-- reconstructed afterwards — reconstruction would need the ranking function, the
-- candidate set and the element sets exactly as they were, which is precisely
-- the state that does not survive.
--
-- See DECISIONS.md D-065.


-- What the ranking function returned for this candidate.
--
-- `predicted_yield` cannot serve and is not being reused. It is constrained
-- `between 0 and 1` and *means* an expected yield; an elevation score is a
-- number of degrees and a priority-weighted score is neither bounded nor a
-- probability. Overloading one column with three meanings would make the
-- evaluation in EVALUATION.md §3 unreadable at exactly the point where
-- configurations A, B, C and D have to be compared against each other.
--
-- Nullable, because it is the *scheduler's* score and Phase 1 has runs that
-- predate one. Unitless deliberately: the unit is a property of `model_config`,
-- and writing it into the column name would be wrong for three of the four
-- configurations.
alter table assignments
    add column score double precision;

comment on column assignments.score is
    'Ranking score from model_config''s function. Unit varies by configuration; '
    'not a yield — see predicted_yield for that. docs/DECISIONS.md D-065.';


-- For a skip, the assignment that took the slot.
--
-- Self-referential on purpose. The alternative — naming the winning *pass* —
-- looks equivalent and is not: a pass can be predicted several times over
-- (D-063), so a pass id does not identify the decision that displaced this
-- candidate, only the physical opportunity it collided with.
--
-- Null for every scheduled row, and null for a skip that lost for some other
-- reason: a station out of capability, a window past the horizon, a floor the
-- geometry never cleared. `reason` distinguishes those; this column answers only
-- "which decision beat it", and answering that with a fabricated id would be
-- worse than answering nothing.
alter table assignments
    add column conflicts_with_assignment_id text
        references assignments (assignment_id);

comment on column assignments.conflicts_with_assignment_id is
    'For a skipped assignment, the one that took the slot. Null otherwise.';

create index assignments_conflicts_with_idx
    on assignments (conflicts_with_assignment_id)
    where conflicts_with_assignment_id is not null;


-- A row cannot be displaced by itself. Cheap to state, and the kind of thing a
-- loop that reuses a variable produces without any other symptom.
alter table assignments
    add constraint assignment_conflict_is_another_row
        check (conflicts_with_assignment_id is distinct from assignment_id);
