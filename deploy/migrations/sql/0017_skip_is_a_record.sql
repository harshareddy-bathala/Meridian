-- 0017 — A skip is a record of a decision, never an assignment
--
-- `assignments` holds both halves of every scheduling decision (D-065): the
-- passes taken and the passes skipped, each skip naming what displaced it. A
-- skip took the column default `state = 'issued'`, and until D-165 nothing on
-- the delivery path looked at `decision`. Every heartbeat therefore handed a
-- station the passes the scheduler had rejected, beside the ones it had chosen,
-- and once their windows closed the skips expired as though the station had
-- declined them — a decline count inflated by exactly the scheduler's own
-- rejections.
--
-- The queries now say `decision = 'scheduled'`. This constraint makes the rule
-- the table's as well: a skip stays `issued` for good, so no query written
-- later can move one, and a transition that tried would fail loudly rather
-- than publish a skip as held, executed or declined.
--
-- `issued` rather than a new state or a null because every reader of
-- `assignments.state` already reads it beside `decision` or through a query
-- that filters on it, and a sixth state would have to be explained to each of
-- them for a row that has no life to track.
--
-- **Existing rows.** A deployment that ran the Stage 7–17 jobs service holds
-- skips that were delivered and have since been marked `held`, `in_progress`,
-- `reported` or `expired`. Those states describe a delivery that should never
-- have happened, not anything the station did with an assignment, so they are
-- returned to `issued` here before the constraint is added. An observation a
-- station submitted against one of them is untouched: it is a real reception,
-- and it stays in `observations` as the evidence of what happened.

update assignments
set state = 'issued'
where decision = 'skipped'
  and state <> 'issued';

alter table assignments
    add constraint assignment_skip_is_a_record
        check (decision = 'scheduled' or state = 'issued');

comment on constraint assignment_skip_is_a_record on assignments is
    'A skipped decision is never delivered, held, executed or expired. '
    'docs/DECISIONS.md D-165.';
