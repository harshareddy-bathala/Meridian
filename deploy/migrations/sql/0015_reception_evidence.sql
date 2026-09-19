-- 0015 — MSP 0.3's reception evidence on observations
--
-- An observation may now say what the receiver and the decoder measured: a noise
-- floor at a stated gain, SNR across the pass, and the decoder's own statistics
-- (MSP §4.4, DECISIONS.md D-103, D-117). Seven nullable columns on the hypertable
-- rather than a side table, for the reason doppler_samples already lives here:
-- every reader of an observation wants them, and immutability (D-015) should be
-- enforced in one place for one record (D-119).
--
-- Every column is nullable and has no default. Null means "not measured", which
-- is what every observation stored before 0.3 is, and a default would put a
-- measurement into rows that never had one. An unknown value is absent, never
-- zero (D-117).
--
-- **Compressed chunks.** `observations` compresses chunks older than seven days,
-- and a deployment that has run for a week has some. Adding nullable columns and
-- CHECK constraints to a hypertable with compressed chunks was tested against
-- TimescaleDB 2.29 before this was written, not assumed: the statements below
-- succeed, the existing compressed rows stay readable, and the constraints are
-- enforced on new rows in both compressed and uncompressed time ranges.
-- tests/integration/test_migration_lifecycle.py repeats that against this file.
--
-- **The CHECKs restate D-117's rules**, as 0005 restates D-072's, so a body the
-- request model accepted can never be refused by the table as a 500. The model
-- is the stricter of the two: the 512-sample cap and the shape of each SNR sample
-- are checked there only, exactly as for doppler_samples.

alter table observations
    -- Relative to the receiver's full scale, at receiver_gain_db. dBFS rather than
    -- dBm because RF calibration is outside what a station can honestly claim,
    -- and a relative floor is only ever compared with the same station's own
    -- history at the same gain (D-103).
    add column noise_floor_dbfs double precision,
    add column receiver_gain_db double precision,

    -- MSP §4.4's snr_samples array, in the wire's shape and the submitted order.
    -- null (not measured) and '[]' (measured, nothing to report) stay distinct.
    add column snr_samples      jsonb,

    -- The decode block, flattened. The decoder is named whenever any of its
    -- statistics are present, because a figure from an unnamed decoder cannot be
    -- segmented by decoder or version (EVALUATION.md §11.1).
    add column decoder          text,
    add column decoder_version  text,
    add column frames_decoded   integer,
    add column frames_failed    integer;

alter table observations
    add constraint observation_frames_counted check (
        frames_decoded >= 0 and frames_failed >= 0
    ),

    -- A floor without its gain cannot be compared with anything (D-117).
    add constraint observation_noise_floor_has_gain check (
        noise_floor_dbfs is null or receiver_gain_db is not null
    ),

    add constraint observation_decode_named check (
        decoder <> ''
        and (decoder is not null
             or (decoder_version is null
                 and frames_decoded is null
                 and frames_failed is null))
    ),

    -- Frames recovered must agree with what the station said happened. Only
    -- applies when frames were counted: a decoder with no frame structure sends
    -- none. `aborted` may carry any count, because a decode can stop part-way.
    add constraint observation_frames_agree_with_outcome check (
        frames_decoded is null
        or outcome = 'aborted'
        or (outcome = 'decoded' and frames_decoded >= 1)
        or (outcome in ('signal_no_decode', 'no_signal') and frames_decoded = 0)
    ),

    -- A station that never began measured nothing.
    add constraint observation_not_attempted_measured_nothing check (
        outcome <> 'not_attempted'
        or (noise_floor_dbfs is null
            and receiver_gain_db is null
            and snr_samples is null
            and decoder is null
            and decoder_version is null
            and frames_decoded is null
            and frames_failed is null)
    );

-- The view's `*` was expanded when 0005 created it, so without this the new
-- columns would exist on the table and be invisible through the view every
-- reader is told to use. `create or replace` keeps every existing column in
-- place and appends the new ones, which is the only change it permits.
create or replace view observations_current as
select distinct on (assignment_id) *
from observations
order by assignment_id, revision desc;
