-- 0026 — a transmitter's nominal frame interval
--
-- The reception verdict compares frames decoded against frames *expected*, and
-- the platform computes expected from the pass and this interval so that every
-- station's ratio has one definition (DECISIONS.md D-103, D-104). A station
-- never sends it.
--
-- Nullable with no default: an interval nobody has stated is unknown, and the
-- ratio is then left absent rather than guessed. A default would put a number
-- into every existing downlink that nobody ever looked up (D-250).
--
-- Seconds as a float, not whole milliseconds: Meteor LRPT's is 8192 bits at
-- 72 kbit/s, 0.113778 s, and rounding it to 114 ms would put two tenths of a
-- percent of error into every ratio computed from it.

alter table satellite_transmitters
    add column frame_interval_s double precision;

alter table satellite_transmitters
    add constraint transmitter_frame_interval_positive check (
        frame_interval_s is null or frame_interval_s > 0
    );
