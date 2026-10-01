-- 0027 — reception ratings: the label "usable" (D-106, D-260)
--
-- The reception verdict is a calibrated probability that a reception is
-- usable, and a calibrated probability needs a label that is not built from
-- the verdict's own inputs. D-260 settles D-106: the label is a person's
-- rating of the decoded product, made without seeing the verdict or any of
-- its inputs. A reception with no product has nothing to rate and is unusable
-- by definition; that rule lives in the labeller, not here, so this table
-- holds ratings only.
--
-- Append-only. A second rating of the same revision is a new row and the
-- latest one is the label; the earlier one stays, so a disagreement between
-- two ratings is on the record rather than overwritten (D-104).
--
-- `rater` is a short tag the rater chooses, not a name. PROJECT.md §16 holds
-- no personal data, and D-107 is still open on what may be held; a tag is
-- enough to tell two raters apart (D-260).
--
-- A plain table, as `products`: its volume is the rated-observation count.
-- The key into observations carries started_at because it is part of that
-- hypertable's primary key (D-013).

create table reception_ratings (
    id                      bigint      generated always as identity primary key,
    assignment_id           text        not null,
    revision                integer     not null,
    observation_started_at  timestamptz not null,
    station_id              text        not null references stations (station_id),

    usable                  boolean     not null,
    -- Which written instructions the rater followed. A changed rubric is a
    -- different label, and a model fitted on one says which (D-260).
    rubric                  text        not null check (rubric ~ '^[a-z0-9][a-z0-9.-]{0,31}$'),
    rater                   text        not null check (rater ~ '^[a-z0-9][a-z0-9_-]{0,15}$'),
    rated_at                timestamptz not null default now(),

    -- Copied from the station's registry record, never inferred (rule 5). The
    -- rating tool refuses a simulated reception, and the column says so anyway.
    simulated               boolean     not null,

    constraint reception_rating_observation_fk
        foreign key (assignment_id, revision, observation_started_at)
        references observations (assignment_id, revision, started_at)
);

create index reception_ratings_revision_idx
    on reception_ratings (assignment_id, revision, rated_at);

comment on table reception_ratings is
    'A person''s rating of a decoded reception, blind to the verdict and its '
    'inputs: the label "usable" (D-106, D-260). Append-only; the latest rating '
    'of a revision is its label.';
