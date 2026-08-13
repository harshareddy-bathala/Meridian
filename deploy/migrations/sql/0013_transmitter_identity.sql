-- 0013 — a transmitter is identified by what it transmits
--
-- Found by writing the catalogue loader (DECISIONS.md D-079). `satellites` and
-- `element_sets` can both absorb a repeated load without writing twice — the
-- first on its primary key, the second on `element_set_content_unique` — and
-- `satellite_transmitters` could not. It has an identity column and no natural
-- key, so loading one catalogue file twice produced two rows for one downlink.
--
-- That is not merely untidy. `find_active_transmitters` feeds pass generation,
-- so a duplicated transmitter computes every pass of that satellite twice, and
-- the scheduler then sees two candidates competing for one station at one
-- moment. The duplicate loses to itself and gets written down as a rejection,
-- which puts a fabricated conflict into the record the evaluation reads.
--
-- The natural key is the satellite, the frequency and the mode. Two entries
-- differing only in `polarisation` or `bandwidth_hz` are one downlink described
-- twice rather than two downlinks: those columns say how well it is received,
-- not what is being received, and `find_active_transmitters` does not read
-- either of them.
--
-- Partial on `deleted_at is null`, so withdrawing a transmitter and adding it
-- back later is allowed. A soft delete records that we stopped tracking a
-- downlink, and nothing about that should stop us tracking it again — the
-- constraint exists to prevent two live rows for one thing, not to make the
-- catalogue append-only.
create unique index satellite_transmitters_identity_idx
    on satellite_transmitters (satellite_id, centre_freq_hz, mode)
    where deleted_at is null;

comment on index satellite_transmitters_identity_idx is
    'One live row per satellite downlink, so re-loading a catalogue file is a '
    'no-op rather than a duplicate. See docs/DECISIONS.md D-079.';
