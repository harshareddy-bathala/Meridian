"""The migrations produce the schema the documents describe.

Marked ``integration``: these need a real TimescaleDB, never SQLite. The schema
uses ``timestamptz``, arrays, ``CHECK`` constraints, generated columns and
hypertables, so a suite that passes on SQLite says nothing about what runs on the
Pi.

    docker run -d --name meridian-test -p 5433:5432 \\
        -e POSTGRES_PASSWORD=meridian -e POSTGRES_USER=meridian \\
        -e POSTGRES_DB=meridian_test timescale/timescaledb:2.29.0-pg16
    export DATABASE_URL=postgresql://meridian:meridian@localhost:5433/meridian_test
    uv run alembic -c deploy/alembic.ini upgrade head
    uv run pytest -m integration

The ``conn`` and ``scalar`` fixtures come from ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

ZERO_HASH = bytes(32)
# A second, distinct hash. `stations.token_sha256` is unique since migration
# 0007, so any test inserting a second station needs its own value — two
# stations sharing a bearer token was never meaningful, the schema simply could
# not say so before.
OTHER_HASH = bytes([1]) * 32


@pytest.fixture
def rollback(conn: Any) -> Iterator[Callable[..., Any]]:
    """Run statements against the migrated schema and undo them afterwards.

    ``conn`` is session-scoped and shared, so anything written here would
    otherwise be visible to every test that runs later and to the developer's
    database after the run. ``force_rollback`` makes the block unconditional —
    it rolls back on success as well as on failure.

    Rows matter for the tests below. A constraint or a view can be inspected in
    the catalogue, but inspecting it only confirms that the definition exists,
    not that it does what it was written for.
    """
    with conn.transaction(force_rollback=True):

        def execute(sql: str, *args: object) -> Any:
            with conn.cursor() as cur:
                cur.execute(sql, args or None)
                return cur.fetchall() if cur.description else None

        yield execute


@pytest.fixture
def fixtures(rollback: Callable[..., Any]) -> Callable[..., Any]:
    """The smallest row graph an observation can hang off:
    one station, one satellite."""
    rollback(
        "insert into satellites (satellite_id, name) values (%s, %s)",
        "norad:99999",
        "Test",
    )
    rollback(
        "insert into stations (station_id, name, operator, lat_deg, lon_deg, alt_m,"
        " token_sha256, registration_key_sha256)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s)",
        "st_fixture",
        "Fixture",
        "tests",
        51.5,
        -0.1,
        20.0,
        ZERO_HASH,
        ZERO_HASH,
    )
    return rollback


def test_all_expected_tables_exist(conn) -> None:
    """Phase 1 builds these and no others (D-018, D-020, D-021)."""
    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'public' and table_type = 'BASE TABLE'"
        )
        tables = {r[0] for r in cur.fetchall()}

    expected = {
        "invite_tokens",
        "stations",
        "station_capabilities",
        "satellites",
        "satellite_transmitters",
        "element_sets",
        "passes",
        "assignments",
        "observations",
        "heartbeats",
        # Stage 14's ingest and archive tables (0016, D-139, D-140).
        "ingest_sources",
        "ingest_records",
        "archive_stations",
        "archive_observations",
        # Stage 20's record of every classified pass (0020, D-182).
        "pass_classifications",
        # Stage 18's scheduler runs (0018, D-170).
        "schedule_runs",
        # Stage 21's record of every revocation and reinstatement (0025, D-196).
        "assignment_revocations",
        # D-018's four deferred tables, built at Stage 19 once each had a
        # producer and a consumer (0023, D-173, D-174, D-176).
        "noise_measurements",
        "horizon_profiles",
        "interference_profiles",
        "products",
        # The label "usable", rated blind (0027, D-260), and the verdict
        # calibrated against it (0028, D-263).
        "reception_ratings",
        "reception_verdicts",
        # Why each loss happened (0029, D-272).
        "loss_diagnoses",
    }
    assert expected <= tables


def test_observations_and_heartbeats_are_hypertables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("select hypertable_name from timescaledb_information.hypertables")
        hypertables = {r[0] for r in cur.fetchall()}
    assert {"observations", "heartbeats", "noise_measurements"} <= hypertables


def test_heartbeats_partitions_on_platform_clock(scalar) -> None:
    """Never partition on a client-supplied timestamp (D-013).

    A station with a dead RTC reporting ``sent_at: 1970-01-01`` would otherwise
    create a 1970 chunk that every compression and retention policy mishandles.
    """
    column = scalar(
        "select column_name from timescaledb_information.dimensions "
        "where hypertable_name = 'heartbeats'"
    )
    assert column == "received_at"


def test_held_assignments_defaults_to_empty_not_null(scalar) -> None:
    """MSP §4.2: an empty list is meaningful and must stay
    distinguishable from absence."""
    nullable = scalar(
        "select is_nullable from information_schema.columns "
        "where table_name = 'heartbeats' and column_name = 'held_assignments'"
    )
    assert nullable == "NO"


def test_simulated_flag_reaches_every_derived_table(conn) -> None:
    """ARCHITECTURE.md rule 4 — the flag propagates to every derived record.

    ``heartbeats`` is included since D-028. D-013 had already ruled that the flag
    extends there; the table did not carry it, so no dashboard query could have
    honoured the rule that simulated and measured data never aggregate together.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.columns "
            "where column_name = 'simulated' and table_schema = 'public'"
        )
        carrying = {r[0] for r in cur.fetchall()}
    assert {
        "stations",
        "passes",
        "assignments",
        "observations",
        "heartbeats",
        "pass_classifications",
        "noise_measurements",
        "horizon_profiles",
        "interference_profiles",
        "products",
        "reception_ratings",
        "reception_verdicts",
        "loss_diagnoses",
    } <= carrying
    # D-049: element_sets is the deliberate exception. Its provenance lives in
    # `source`, which distinguishes celestrak from spacetrack from manual as
    # well as simulator, and is part of element_set_content_unique (D-057). A
    # boolean here would be derivable from `source` and so able to disagree
    # with it.
    assert "element_sets" not in carrying
    # D-139 exempts the ingest and archive tables for the same reason: they
    # describe what somebody else published, not what one of our stations
    # received, and the table name is the label. What replaces the column is
    # that these rows are never pooled with `observations` — which the
    # outcome vocabulary below makes a CHECK violation rather than a habit.
    assert not (
        {
            "ingest_sources",
            "ingest_records",
            "archive_stations",
            "archive_observations",
        }
        & carrying
    )


def test_outcome_enum_is_exactly_the_msp_five(scalar) -> None:
    """D-010 pins this to MSP §4.4. Drift between the two is the failure mode.

    Exactly these five, not "these five among others". A sixth value added to the
    column and not to the specification is the same drift in the other direction,
    and asserting membership one way round cannot see it.
    """
    import re

    definition = scalar(
        "select pg_get_constraintdef(oid) from pg_constraint "
        "where conname = 'observations_outcome_check'"
    )
    assert definition is not None

    accepted = set(re.findall(r"'([a-z_]+)'::text", definition))
    assert accepted == {
        "decoded",
        "signal_no_decode",
        "no_signal",
        "aborted",
        "not_attempted",
    }


def test_a_sixth_outcome_is_rejected(fixtures) -> None:
    """The constraint holds against a write, not only in the catalogue."""
    with pytest.raises(psycopg.errors.CheckViolation):
        fixtures(
            "insert into observations (assignment_id, started_at, ended_at, station_id,"
            " satellite_id, outcome, content_sha256)"
            " values (%s, now(), now(), %s, %s, %s, %s)",
            "as_bad",
            "st_fixture",
            "norad:99999",
            "partial_decode",
            ZERO_HASH,
        )


def test_observations_current_returns_the_latest_revision(fixtures) -> None:
    """D-015: a resubmission appends; the view hides the history from callers.

    Two revisions of one assignment go in. ``observations`` must hold both — the
    earlier report survives, which is the whole point of appending — and
    ``observations_current`` must show only the later one, because MSP §6
    promises a station never sees two current observations for one assignment.
    """
    for revision, outcome in ((1, "no_signal"), (2, "decoded")):
        fixtures(
            "insert into observations (assignment_id, revision, started_at, ended_at,"
            " station_id, satellite_id, outcome, signal_detected, first_detection_at,"
            " content_sha256) values (%s, %s, timestamptz '2026-08-14T09:41:18Z',"
            " timestamptz '2026-08-14T09:52:10Z', %s, %s, %s, %s, %s, %s)",
            "as_view",
            revision,
            "st_fixture",
            "norad:99999",
            outcome,
            outcome == "decoded",
            "2026-08-14T09:41:53Z" if outcome == "decoded" else None,
            ZERO_HASH,
        )

    both = fixtures(
        "select revision from observations where assignment_id = %s order by revision",
        "as_view",
    )
    assert [row[0] for row in both] == [1, 2]

    current = fixtures(
        "select revision, outcome from observations_current where assignment_id = %s",
        "as_view",
    )
    assert current == [(2, "decoded")]


def test_observation_id_is_a_stored_generated_column(scalar) -> None:
    """D-027: MSP §4.4's acknowledgement needs an id, and a retry needs the same one.

    Generated in the database rather than in Python so the ingest path and the
    public API cannot drift apart — which they would, silently, the first time
    one of them changed the separator.
    """
    generated = scalar(
        "select is_generated from information_schema.columns "
        "where table_name = 'observations' and column_name = 'observation_id'"
    )
    assert generated == "ALWAYS"


def test_listening_block_is_all_or_nothing_including_mode(scalar) -> None:
    """D-028: a partial listening block cannot support the assertion it exists to make.

    ``mode`` joined the constraint because a station tuned to the right frequency
    running the wrong demodulator did not observe the pass, and
    ``Registry.was_listening()`` is the only authority on what counts as a
    confirmed miss.
    """
    definition = scalar(
        "select pg_get_constraintdef(oid) from pg_constraint "
        "where conname = 'heartbeat_listening_complete'"
    )
    assert definition is not None
    for column in (
        "listening_assignment_id",
        "listening_satellite_id",
        "listening_freq_hz",
        "listening_mode",
    ):
        assert column in definition


def test_stations_carry_a_registration_key_hash(scalar) -> None:
    """D-023: without it a lost register response strands a station permanently."""
    data_type = scalar(
        "select data_type from information_schema.columns "
        "where table_name = 'stations' and column_name = 'registration_key_sha256'"
    )
    assert data_type == "bytea"

    nullable = scalar(
        "select is_nullable from information_schema.columns "
        "where table_name = 'stations' and column_name = 'registration_key_sha256'"
    )
    assert nullable == "NO"


def test_a_bound_invite_cannot_be_consumed_by_another_station(fixtures) -> None:
    """D-034: the binding is the security property, so the database enforces it.

    A replacement invite names the station whose token it rotates. If any other
    station could redeem it, an operator recovering station A would have issued a
    credential rotation for station B — and the register handler would be the only
    thing standing between the two.
    """
    fixtures(
        "insert into stations (station_id, name, operator, lat_deg, lon_deg, alt_m,"
        " token_sha256, registration_key_sha256)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s)",
        "st_other",
        "Other",
        "tests",
        0.0,
        0.0,
        0.0,
        OTHER_HASH,
        ZERO_HASH,
    )
    fixtures(
        "insert into invite_tokens (token_sha256, label, issued_for_station_id)"
        " values (%s, %s, %s)",
        ZERO_HASH,
        "replacement for st_fixture",
        "st_fixture",
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        fixtures(
            "update invite_tokens set consumed_at = now(), consumed_by_station_id = %s"
            " where token_sha256 = %s",
            "st_other",
            ZERO_HASH,
        )


def test_a_bound_invite_is_consumable_by_the_station_it_names(fixtures) -> None:
    """The other half of D-034: the binding must not block the case it exists for."""
    fixtures(
        "insert into invite_tokens (token_sha256, label, issued_for_station_id)"
        " values (%s, %s, %s)",
        ZERO_HASH,
        "replacement for st_fixture",
        "st_fixture",
    )
    fixtures(
        "update invite_tokens set consumed_at = now(), consumed_by_station_id = %s"
        " where token_sha256 = %s",
        "st_fixture",
        ZERO_HASH,
    )

    rows = fixtures(
        "select consumed_by_station_id from invite_tokens where token_sha256 = %s",
        ZERO_HASH,
    )
    assert rows == [("st_fixture",)]


def test_an_unbound_invite_still_admits_any_station(fixtures) -> None:
    """D-020's ordinary invite is unchanged by D-034: null means "any new station"."""
    fixtures(
        "insert into invite_tokens (token_sha256, label) values (%s, %s)",
        ZERO_HASH,
        "seeded",
    )
    fixtures(
        "update invite_tokens set consumed_at = now(), consumed_by_station_id = %s"
        " where token_sha256 = %s",
        "st_fixture",
        ZERO_HASH,
    )

    rows = fixtures(
        "select issued_for_station_id, consumed_by_station_id from invite_tokens"
        " where token_sha256 = %s",
        ZERO_HASH,
    )
    assert rows == [(None, "st_fixture")]


def test_declared_horizon_mask_defaults_to_an_empty_array(scalar) -> None:
    """D-031: a station that declares nothing is not claiming a clear horizon.

    The default is ``[]`` rather than ``NULL`` so "declared nothing" and
    "declared an empty mask" stay the same statement, and neither is mistaken
    for a learned profile.
    """
    default = scalar(
        "select column_default from information_schema.columns "
        "where table_name = 'station_capabilities'"
        " and column_name = 'horizon_mask_json'"
    )
    assert default is not None and "[]" in default


def test_no_plaintext_token_columns_exist(conn) -> None:
    """D-017: tokens are opaque secrets stored hashed, never in the clear.

    A column called ``token`` or ``invite_token`` appearing anywhere in this
    schema means someone stored a credential a database dump would expose.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = 'public' and column_name in "
            "('token', 'invite_token', 'bearer_token', 'registration_key')"
        )
        offenders = cur.fetchall()
    assert offenders == []


def test_liveness_is_not_a_stored_column(conn) -> None:
    """D-054: liveness is derived on read, so 0008 dropped the column.

    A stored conclusion is only correct until the clock passes its next
    threshold, and nothing moves the clock on the platform's behalf — so a
    station that went quiet would keep reading `online` until an unrelated
    write refreshed it, which is the case liveness exists to detect. The
    vocabulary lives in `registry.liveness` now. Asserted here because a
    re-added column would fail nothing else: the derivation would keep working
    while the schema quietly grew a second, disagreeing answer.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_name = 'stations' and column_name = 'liveness'"
        )
        assert cur.fetchall() == []


def test_element_sets_are_keyed_on_content_not_epoch(scalar) -> None:
    """D-057: two different sets sharing an epoch are two rows, not one.

    The key 0003 shipped, `(satellite_id, epoch, source)`, discarded the second
    of two sets published for one epoch — a catalogue correction or a re-fit
    after a manoeuvre — which is the measurement that would explain why a
    prediction from that epoch was wrong. Read from the catalogue rather than
    by inserting, because the failure this guards is the *old* key coming back,
    and a row-level test would pass under either key.
    """
    columns = scalar(
        "select string_agg(a.attname, ',' order by a.attname) "
        "from pg_constraint c "
        "join pg_attribute a on a.attrelid = c.conrelid "
        " and a.attnum = any(c.conkey) "
        "where c.conname = 'element_set_content_unique'"
    )
    assert columns == "content_sha256,satellite_id,source"


# --- Migration 0007's corrections -------------------------------------------
#
# Each of these inserts a row rather than reading pg_constraint. A catalogue
# query confirms a definition exists; only a write confirms it refuses what it
# was written to refuse.


def test_two_stations_cannot_share_a_bearer_token_hash(fixtures) -> None:
    """`find_station_id_by_token_hash` uses `fetchone()`.

    Without the unique index a duplicate hash would authenticate as whichever
    row Postgres returned first — silently, and not necessarily the same one
    twice.
    """
    with pytest.raises(psycopg.errors.UniqueViolation):
        fixtures(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
            " alt_m, token_sha256, registration_key_sha256)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s)",
            "st_twin",
            "Twin",
            "tests",
            51.5,
            -0.1,
            20.0,
            ZERO_HASH,  # the hash st_fixture already holds
            ZERO_HASH,
        )


def _insert_element_set(execute: Any) -> Any:
    """One element set for the fixture satellite, returning its id."""
    rows = execute(
        "insert into element_sets (satellite_id, epoch, line1, line2, source)"
        " values (%s, now(), %s, %s, 'manual') returning id",
        "norad:99999",
        "1 99999U",
        "2 99999",
    )
    return rows[0][0]


def _insert_pass(execute: Any, *, max_elevation_deg: float, floor_deg: float) -> None:
    """One pass window with the two elevations under test."""
    execute(
        "insert into passes (satellite_id, station_id, aos, los, max_elevation_deg,"
        " max_elevation_at, aos_azimuth_deg, los_azimuth_deg, element_set_id,"
        " min_elevation_deg)"
        " values (%s, %s, now(), now() + interval '10 minutes', %s,"
        " now() + interval '5 minutes', 10, 200, %s, %s)",
        "norad:99999",
        "st_fixture",
        max_elevation_deg,
        _insert_element_set(execute),
        floor_deg,
    )


def test_a_pass_peaking_below_the_horizon_is_refused(fixtures) -> None:
    """GLOSSARY.md defines a pass as a period *above* the horizon.

    The original range allowed -40, which describes a satellite that never rose.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_pass(fixtures, max_elevation_deg=-40.0, floor_deg=-90.0)


def test_a_pass_peaking_below_its_own_floor_is_refused(fixtures) -> None:
    """A window is the interval where elevation is at or above the floor.

    So the peak over that interval cannot be below it. A row violating this is a
    propagation or frame-conversion bug, and catching it here is far cheaper
    than finding it in a reliability figure three stages later.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_pass(fixtures, max_elevation_deg=5.0, floor_deg=10.0)


def test_a_pass_at_its_floor_is_accepted(fixtures) -> None:
    """The constraint is `>=`, not `>`: a grazing pass is a real pass."""
    _insert_pass(fixtures, max_elevation_deg=10.0, floor_deg=10.0)


def test_a_transmitter_source_outside_the_enumeration_is_refused(fixtures) -> None:
    """`element_sets.source` was constrained and this twin was not (D-021)."""
    with pytest.raises(psycopg.errors.CheckViolation):
        fixtures(
            "insert into satellite_transmitters"
            " (satellite_id, centre_freq_hz, mode, source)"
            " values (%s, 137100000, 'lrpt', 'satnogs')",
            "norad:99999",
        )


def test_a_miscased_transmitter_polarisation_is_refused(fixtures) -> None:
    """'RHCP' is a row that exists and never joins.

    Every query looks for 'rhcp', so an uppercase value reads as missing data
    rather than as a bug — which is why the constraint matters more than the
    typo it catches.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        fixtures(
            "insert into satellite_transmitters"
            " (satellite_id, centre_freq_hz, mode, polarisation)"
            " values (%s, 137100000, 'lrpt', 'RHCP')",
            "norad:99999",
        )


def test_an_unknown_transmitter_polarisation_stays_storable(fixtures) -> None:
    """The column is nullable and stays nullable.

    A transmitter whose polarisation nobody has established is a real record,
    and a CHECK admits anything that is not false — so NULL needs no clause.
    """
    fixtures(
        "insert into satellite_transmitters"
        " (satellite_id, centre_freq_hz, mode) values (%s, 137100000, 'lrpt')",
        "norad:99999",
    )


# --- 0011, scheduling decisions -----------------------------------------------


def _insert_scheduled_pass(
    execute: Any, element_set_id: Any, *, hours_ahead: int = 0
) -> Any:
    """One pass for the fixture station and satellite, returning its id.

    ``element_set_id`` is passed in rather than created here so two passes can
    share one set: giving each its own would insert the same two lines twice and
    collide on D-057's content key. ``hours_ahead`` then moves the acquisition,
    which is what ``pass_prediction_unique`` keys on to tell two predictions
    apart.
    """
    rows = execute(
        "insert into passes (satellite_id, station_id, aos, los, max_elevation_deg,"
        " max_elevation_at, aos_azimuth_deg, los_azimuth_deg, element_set_id,"
        " min_elevation_deg)"
        " values (%s, %s, now() + %s * interval '1 hour',"
        " now() + %s * interval '1 hour' + interval '10 minutes', 40,"
        " now() + %s * interval '1 hour' + interval '5 minutes', 10, 200, %s, 10)"
        " returning id",
        "norad:99999",
        "st_fixture",
        hours_ahead,
        hours_ahead,
        hours_ahead,
        element_set_id,
    )
    return rows[0][0]


def _insert_assignment(
    execute: Any,
    assignment_id: str,
    pass_id: Any,
    *,
    score: float | None = None,
    conflicts_with: str | None = None,
) -> None:
    """One assignment row. Naming a conflict makes it a skip, which is what a
    row that lost to another decision is."""
    execute(
        "insert into assignments (assignment_id, pass_id, station_id, start_at,"
        " end_at, centre_freq_hz, mode, timing_uncertainty_s, decision, reason,"
        " score, conflicts_with_assignment_id)"
        " values (%s, %s, %s, now(), now() + interval '12 minutes', 137100000,"
        " 'lrpt', 0.5, %s, 'test', %s, %s)",
        assignment_id,
        pass_id,
        "st_fixture",
        "skipped" if conflicts_with else "scheduled",
        score,
        conflicts_with,
    )


def test_a_scheduling_decision_records_the_score_it_was_ranked_on(fixtures) -> None:
    """`predicted_yield` could not have held this: it is capped at 1.

    An elevation score is a number of degrees, so the two are different
    quantities and storing one in the other's column would make the comparison
    between EVALUATION.md's configurations unreadable.
    """
    pass_id = _insert_scheduled_pass(fixtures, _insert_element_set(fixtures))
    _insert_assignment(fixtures, "asg_a", pass_id, score=47.2)

    stored = fixtures("select score from assignments where assignment_id = %s", "asg_a")
    assert stored[0][0] == 47.2


def test_a_score_outside_the_predicted_yield_range_is_accepted(fixtures) -> None:
    """180 degrees is nonsense as a yield and fine as a score — the point of
    the new column being unconstrained."""
    pass_id = _insert_scheduled_pass(fixtures, _insert_element_set(fixtures))
    _insert_assignment(fixtures, "asg_b", pass_id, score=180.0)


def test_a_skip_can_name_the_decision_that_displaced_it(fixtures) -> None:
    """PROJECT.md §13's screen shows *why*, and "why" is another assignment."""
    element_set_id = _insert_element_set(fixtures)
    pass_id = _insert_scheduled_pass(fixtures, element_set_id)
    other_pass_id = _insert_scheduled_pass(fixtures, element_set_id, hours_ahead=2)
    _insert_assignment(fixtures, "asg_winner", pass_id, score=70.0)
    _insert_assignment(
        fixtures, "asg_loser", other_pass_id, score=12.0, conflicts_with="asg_winner"
    )

    stored = fixtures(
        "select conflicts_with_assignment_id from assignments where assignment_id = %s",
        "asg_loser",
    )
    assert stored[0][0] == "asg_winner"


def test_an_assignment_cannot_be_displaced_by_itself(fixtures) -> None:
    """A loop reusing one variable produces this and has no other symptom."""
    pass_id = _insert_scheduled_pass(fixtures, _insert_element_set(fixtures))
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_assignment(fixtures, "asg_self", pass_id, conflicts_with="asg_self")


def test_a_conflict_naming_no_such_assignment_is_refused(fixtures) -> None:
    """The reference is a foreign key, so the explanation cannot dangle."""
    pass_id = _insert_scheduled_pass(fixtures, _insert_element_set(fixtures))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert_assignment(
            fixtures, "asg_orphan", pass_id, conflicts_with="asg_never_existed"
        )


def test_a_scheduled_assignment_names_no_conflict(fixtures) -> None:
    """Null is the normal case, and the column stays nullable to say so."""
    pass_id = _insert_scheduled_pass(fixtures, _insert_element_set(fixtures))
    _insert_assignment(fixtures, "asg_clean", pass_id, score=55.0)

    stored = fixtures(
        "select score, conflicts_with_assignment_id from assignments"
        " where assignment_id = %s",
        "asg_clean",
    )
    assert stored[0] == (55.0, None)


def test_a_skip_cannot_leave_issued(fixtures) -> None:
    """0017: a skip is a record, so no transition may move it (D-165)."""
    element_set_id = _insert_element_set(fixtures)
    pass_id = _insert_scheduled_pass(fixtures, element_set_id)
    other_pass_id = _insert_scheduled_pass(fixtures, element_set_id, hours_ahead=2)
    _insert_assignment(fixtures, "asg_kept", pass_id, score=70.0)
    _insert_assignment(
        fixtures, "asg_lost", other_pass_id, score=12.0, conflicts_with="asg_kept"
    )

    fixtures(
        "update assignments set state = 'held' where assignment_id = %s", "asg_kept"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        fixtures(
            "update assignments set state = 'expired' where assignment_id = %s",
            "asg_lost",
        )


# --- Migration 0016's ingest and archive tables ------------------------------
#
# The completion gate for Stage 14 is that a snapshot downloaded once can be
# normalised repeatedly with no network. These tests are about the half of that
# the schema owns: provenance that cannot be incomplete, arrivals that cannot be
# overwritten, and archive rows that cannot be mistaken for our own.


@pytest.fixture
def archive_fixtures(rollback):
    """One source and one retrieved artefact, the smallest graph these hang off."""
    rollback(
        "insert into ingest_sources (source_id, source_class, name, licence,"
        " terms_url, access_constraint, attribution_entry)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        "reference_archive",
        "archive_receptions",
        "Reference archive",
        "CC-BY-4.0",
        "https://example.invalid/terms",
        "none",
        "Ingested data sources: reference adapter",
    )
    return rollback


def _insert_record(
    execute, *, identifier: str = "art-1", sha: bytes = ZERO_HASH
) -> int:
    rows = execute(
        "insert into ingest_records (source_id, original_identifier, source_version,"
        " payload_kind, retrieved_at, sha256, raw_path, media_type, byte_count)"
        " values (%s, %s, %s, %s, now(), %s, %s, %s, %s) returning record_id",
        "reference_archive",
        identifier,
        "v1",
        "data",
        sha,
        f"reference_archive/20260920T000000Z-{identifier}/artefact.bin",
        "application/json",
        128,
    )
    return int(rows[0][0])


def test_a_source_without_its_terms_cannot_be_registered(rollback) -> None:
    """D-134 in the database rather than in a reviewer's memory.

    A blank licence would let a record exist that arrived under terms nobody
    wrote down — which is precisely the state ATTRIBUTION.md exists to make
    impossible, and the one that cannot be repaired afterwards because the
    retrieval has already happened.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        rollback(
            "insert into ingest_sources (source_id, source_class, name, licence,"
            " terms_url, access_constraint, attribution_entry)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            "no_terms",
            "archive_receptions",
            "Nameless",
            "   ",
            "https://example.invalid/terms",
            "none",
            "entry",
        )


def test_an_identical_refetch_is_one_row(archive_fixtures) -> None:
    """The same bytes retrieved twice conflict rather than duplicating.

    This is what makes a re-run of `fetch` cheap and safe: the loader takes the
    id it already has. A *differing* re-fetch has a different digest and so
    inserts, which is how supersession stays visible.
    """
    first = _insert_record(archive_fixtures)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_record(archive_fixtures)
    assert first > 0


def test_a_differing_refetch_supersedes_rather_than_overwrites(
    archive_fixtures,
) -> None:
    """Append-only: nothing is rewritten, and the old row says what replaced it."""
    first = _insert_record(archive_fixtures)
    second = _insert_record(archive_fixtures, sha=OTHER_HASH)
    archive_fixtures(
        "update ingest_records set superseded_by = %s where record_id = %s",
        second,
        first,
    )

    stored = archive_fixtures(
        "select superseded_by from ingest_records where record_id = %s", first
    )
    assert stored[0][0] == second


def test_a_record_cannot_supersede_itself(archive_fixtures) -> None:
    record_id = _insert_record(archive_fixtures)
    with pytest.raises(psycopg.errors.CheckViolation):
        archive_fixtures(
            "update ingest_records set superseded_by = %s where record_id = %s",
            record_id,
            record_id,
        )


@pytest.mark.parametrize(
    "raw_path",
    [
        "/etc/passwd",
        "../outside/artefact.bin",
        "reference_archive/../../artefact.bin",
        "",
    ],
)
def test_a_raw_path_leaving_the_store_is_refused(archive_fixtures, raw_path) -> None:
    """D-141: the store's layout is ours, and a path is never a remote string.

    The constraint is the second line of defence — the writer builds the path
    from our own timestamp and our own checksum — but it is the one that stays
    true when somebody writes a second writer.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        archive_fixtures(
            "insert into ingest_records (source_id, original_identifier,"
            " source_version, payload_kind, retrieved_at, sha256, raw_path,"
            " media_type, byte_count)"
            " values (%s, %s, %s, %s, now(), %s, %s, %s, %s)",
            "reference_archive",
            "escaping",
            "v1",
            "data",
            ZERO_HASH,
            raw_path,
            "application/json",
            1,
        )


def _insert_archive_observation(execute, record_id: int, **overrides) -> None:
    values = {
        "source_observation_id": "obs-1",
        "transformation_version": "reference-1",
        "satellite_key": "norad:99999",
        "satellite_key_kind": "norad",
        "archive_outcome": "decoded",
    }
    values.update(overrides)
    execute(
        "insert into archive_observations (record_id, source_id,"
        " source_observation_id, transformation_version, content_sha256,"
        " satellite_key, satellite_key_kind, started_at, archive_outcome)"
        " values (%s, %s, %s, %s, %s, %s, %s, now(), %s)",
        record_id,
        "reference_archive",
        values["source_observation_id"],
        values["transformation_version"],
        ZERO_HASH,
        values["satellite_key"],
        values["satellite_key_kind"],
        values["archive_outcome"],
    )


def test_an_archive_reception_cannot_claim_an_msp_outcome(archive_fixtures) -> None:
    """`no_signal` asserts a station was listening; we hold no heartbeat for one.

    Rule 7 makes "absence is not a miss" load-bearing for every reliability
    figure, and the evidence that distinguishes the two is a heartbeat we do not
    have for somebody else's station. Different values mean an accidental union
    of the two tables fails here rather than returning a plausible number.
    """
    record_id = _insert_record(archive_fixtures)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_archive_observation(
            archive_fixtures, record_id, archive_outcome="no_signal"
        )


def test_an_unknown_satellite_is_stored_and_creates_no_catalogue_row(
    archive_fixtures,
) -> None:
    """No FK to `satellites` (D-139), and deliberately so.

    An FK would force the loader either to drop receptions for objects we do not
    track — a second selection filter stacked on the archive's own, invisible
    downstream — or to insert into `satellites`, letting an external archive
    decide what pass generation propagates. Coverage is a number we report, not
    a filter we apply.
    """
    before = archive_fixtures("select count(*) from satellites")[0][0]
    record_id = _insert_record(archive_fixtures)
    _insert_archive_observation(
        archive_fixtures, record_id, satellite_key="norad:00001"
    )

    stored = archive_fixtures(
        "select satellite_key from archive_observations where record_id = %s",
        record_id,
    )
    after = archive_fixtures("select count(*) from satellites")[0][0]

    assert stored == [("norad:00001",)]
    assert after == before


def test_renormalising_under_a_new_version_appends(archive_fixtures) -> None:
    """Retrieval and transformation are separate events (D-140).

    The same artefact normalised again under a new normaliser is a second row,
    not an overwritten one — which is why `transformation_version` is part of
    the key and is not a column on the arrival.
    """
    record_id = _insert_record(archive_fixtures)
    _insert_archive_observation(archive_fixtures, record_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_archive_observation(archive_fixtures, record_id)


def test_denominator_inputs_says_what_stage_16_can_compute(archive_fixtures) -> None:
    """Incomplete stations are counted and published, never silently dropped.

    A completeness denominator computed only over the stations we happened to
    have coordinates for, reported as though it covered all of them, is this
    project's own methodological threat arriving through the back door.
    """
    record_id = _insert_record(archive_fixtures)
    for key, lat, lon, capability in (
        ("nowhere", None, None, None),
        ("located", 51.5, -0.1, None),
        ("described", 51.5, -0.1, '{"modes": ["lrpt"]}'),
    ):
        archive_fixtures(
            "insert into archive_stations (record_id, source_id, source_station_key,"
            " lat_deg, lon_deg, capability_json, content_sha256)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            record_id,
            "reference_archive",
            key,
            lat,
            lon,
            capability,
            ZERO_HASH,
        )

    stored = archive_fixtures(
        "select source_station_key, denominator_inputs from archive_stations"
        " order by source_station_key"
    )
    assert stored == [
        ("described", "location_and_capability"),
        ("located", "location_only"),
        ("nowhere", "neither"),
    ]


def test_a_half_published_location_is_refused(archive_fixtures) -> None:
    """A latitude without a longitude is not a location, and would be used as one."""
    record_id = _insert_record(archive_fixtures)
    with pytest.raises(psycopg.errors.CheckViolation):
        archive_fixtures(
            "insert into archive_stations (record_id, source_id, source_station_key,"
            " lat_deg, content_sha256) values (%s, %s, %s, %s, %s)",
            record_id,
            "reference_archive",
            "half",
            51.5,
            ZERO_HASH,
        )


def test_every_stored_artefact_carries_its_terms(archive_fixtures) -> None:
    """ "Were we allowed to use this?" is one query returning no nulls.

    A property of the schema rather than of the loader: the provenance columns
    are `not null` on both sides of the join, so the view cannot produce a
    record whose licence nobody recorded.
    """
    _insert_record(archive_fixtures)
    incomplete = archive_fixtures(
        "select count(*) from ingest_provenance"
        " where licence is null or terms_url is null or attribution_entry is null"
    )
    assert incomplete[0][0] == 0


def test_the_ingest_tables_are_plain_tables(conn) -> None:
    """No hypertable and no compression policy on any of them.

    Archive receptions are months old when they load, so a compression policy on
    `started_at` would compress a chunk on creation and every backfill would
    write into a compressed one. Making one a hypertable later is supported;
    undoing it is not, and `downgrade()` always raises.
    """
    with conn.cursor() as cur:
        cur.execute("select hypertable_name from timescaledb_information.hypertables")
        hypertables = {r[0] for r in cur.fetchall()}

    assert not (
        {
            "ingest_sources",
            "ingest_records",
            "archive_stations",
            "archive_observations",
        }
        & hypertables
    )


def _classification(fixtures: Callable[..., Any], **columns: object) -> None:
    """One ``pass_classifications`` row over a fresh pass and assignment."""
    fixtures(
        "insert into element_sets (satellite_id, epoch, line1, line2, source)"
        " values ('norad:99999', now(), 'l1', 'l2', 'manual')"
    )
    fixtures(
        "insert into passes (satellite_id, station_id, aos, los, max_elevation_deg,"
        " max_elevation_at, aos_azimuth_deg, los_azimuth_deg, element_set_id,"
        " min_elevation_deg) select 'norad:99999', 'st_fixture', now(),"
        " now() + interval '10 minutes', 40, now() + interval '5 minutes', 10, 200,"
        " max(id), 10 from element_sets"
    )
    fixtures(
        "insert into assignments (assignment_id, pass_id, station_id, start_at,"
        " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason)"
        " select 'as_fixture', max(id), 'st_fixture', now(),"
        " now() + interval '10 minutes', 137900000, 'lrpt', 4, 'test' from passes"
    )
    values = {
        "assignment_ids": ["as_fixture"],
        "classification": "confirmed_miss",
        "evidence": "{}",
        "config_sha256": ZERO_HASH,
    } | columns
    fixtures(
        "insert into pass_classifications (assignment_id, assignment_ids, pass_id,"
        " station_id, satellite_id, window_start, window_end, classification,"
        " evidence, method, config_sha256, simulated)"
        " select 'as_fixture', %s, max(id), 'st_fixture', 'norad:99999', now(),"
        " now() + interval '10 minutes', %s, %s::jsonb, 'classification-1', %s,"
        " false from passes",
        values["assignment_ids"],
        values["classification"],
        values["evidence"],
        values["config_sha256"],
    )


def test_a_classification_outside_the_eight_is_refused(fixtures) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _classification(fixtures, classification="missed")


def test_the_representative_is_the_first_pooled_assignment(fixtures) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _classification(fixtures, assignment_ids=["as_other", "as_fixture"])


def test_a_classification_is_held_once_per_method_and_configuration(
    fixtures,
) -> None:
    _classification(fixtures)
    with pytest.raises(psycopg.errors.UniqueViolation):
        fixtures(
            "insert into pass_classifications (assignment_id, assignment_ids,"
            " pass_id, station_id, satellite_id, window_start, window_end,"
            " classification, evidence, method, config_sha256, simulated)"
            " select assignment_id, assignment_ids, pass_id, station_id,"
            " satellite_id, window_start, window_end, 'station_unavailable',"
            " evidence, method, config_sha256, simulated from pass_classifications"
        )


# --- 0023, the deferred tables ------------------------------------------------


def _insert_observation(execute: Any, assignment_id: str = "as_stored") -> Any:
    """One observation of the fixture station, returning its started_at.

    `observations` has no foreign key to `assignments`, so none is needed here.
    """
    rows = execute(
        "insert into observations (assignment_id, revision, started_at, ended_at,"
        " station_id, satellite_id, outcome, content_sha256)"
        " values (%s, 1, now() - interval '1 hour', now() - interval '50 minutes',"
        " 'st_fixture', 'norad:99999', 'no_signal', %s)"
        " returning started_at",
        assignment_id,
        ZERO_HASH,
    )
    return rows[0][0]


def _insert_noise(execute: Any, **overrides: object) -> None:
    """One noise row: an observation's floor unless ``overrides`` say otherwise."""
    row: dict[str, object] = {
        "source": "observation",
        "assignment_id": "as_noise",
        "revision": 1,
        "azimuth_deg": None,
        "noise_floor_dbfs": -52.3,
    }
    row.update(overrides)
    execute(
        "insert into noise_measurements (station_id, measured_at, source,"
        " assignment_id, revision, centre_freq_hz, azimuth_deg, noise_floor_dbfs,"
        " receiver_gain_db, simulated)"
        " values ('st_fixture', now(), %s, %s, %s, 137100000, %s, %s, 32.8, false)",
        row["source"],
        row["assignment_id"],
        row["revision"],
        row["azimuth_deg"],
        row["noise_floor_dbfs"],
    )


def test_an_observation_floor_names_its_reception(fixtures) -> None:
    """D-173: an observation's row names the reception; a sweep's names none."""
    _insert_noise(fixtures)
    _insert_noise(fixtures, source="survey", assignment_id=None, revision=None)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_noise(fixtures, assignment_id=None, revision=None)


def test_a_sweep_cannot_claim_a_reception(fixtures) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_noise(fixtures, source="survey")


def test_an_observation_floor_claims_no_direction(fixtures) -> None:
    """D-173: one floor covers a whole pass, so it was pointed nowhere."""
    _insert_noise(
        fixtures, source="survey", assignment_id=None, revision=None, azimuth_deg=90.0
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_noise(fixtures, azimuth_deg=90.0)


def test_an_infinite_floor_is_refused(fixtures) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_noise(fixtures, noise_floor_dbfs=float("-inf"))


def _insert_horizon(execute: Any, **overrides: object) -> None:
    """One learned bin unless ``overrides`` say otherwise."""
    row: dict[str, object] = {
        "source": "learned",
        "capability_id": None,
        "dataset_sha256": ZERO_HASH,
        "trained_from": "2026-08-01T00:00:00Z",
        "trained_until": "2026-09-01T00:00:00Z",
        "sample_count": 4,
        "azimuth_deg": 0.0,
    }
    row.update(overrides)
    execute(
        "insert into horizon_profiles (station_id, source, method, capability_id,"
        " dataset_sha256, trained_from, trained_until, azimuth_deg,"
        " azimuth_width_deg, min_elevation_deg, sample_count, simulated)"
        " values ('st_fixture', %s, 'test-1', %s, %s, %s, %s, %s, 10, 6.5, %s,"
        " false)",
        row["source"],
        row["capability_id"],
        row["dataset_sha256"],
        row["trained_from"],
        row["trained_until"],
        row["azimuth_deg"],
        row["sample_count"],
    )


def _insert_capability(execute: Any) -> Any:
    rows = execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, modes, polarisation, min_elevation_deg)"
        " values ('st_fixture', 'vhf', 136000000, 138000000, '{lrpt}', 'rhcp', 10)"
        " returning id"
    )
    return rows[0][0]


def test_a_learned_profile_is_built_once_per_dataset(fixtures) -> None:
    """D-174: a dataset identifies a learned profile, so a rebuild is refused."""
    _insert_horizon(fixtures)
    _insert_horizon(fixtures, azimuth_deg=10.0)
    _insert_horizon(fixtures, dataset_sha256=OTHER_HASH)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_horizon(fixtures)


def test_a_declared_bin_carries_no_learned_provenance(fixtures) -> None:
    """D-031, D-174: declared and learned are never the same row."""
    capability = _insert_capability(fixtures)
    _insert_horizon(
        fixtures,
        source="declared",
        capability_id=capability,
        dataset_sha256=None,
        trained_from=None,
        trained_until=None,
        sample_count=None,
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_horizon(
            fixtures,
            source="declared",
            capability_id=capability,
            trained_from=None,
            trained_until=None,
            sample_count=None,
            azimuth_deg=20.0,
        )


def test_a_learned_bin_names_its_dataset_and_count(fixtures) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_horizon(fixtures, sample_count=None)


def _insert_interference(
    execute: Any, sample_count: int, gains: tuple[float | None, float | None]
) -> None:
    execute(
        "insert into interference_profiles (station_id, method, dataset_sha256,"
        " trained_from, trained_until, azimuth_deg, azimuth_width_deg, hour_start,"
        " hour_width, noise_lift_db, station_median_dbfs, sample_count,"
        " gain_min_db, gain_max_db, simulated)"
        " values ('st_fixture', 'test-1', %s, '2026-08-01Z', '2026-09-01Z',"
        " 45, 45, %s, 4, 1.5, -52.0, %s, %s, %s, false)",
        ZERO_HASH,
        4 * sample_count % 24,
        sample_count,
        gains[0],
        gains[1],
    )


def test_an_interference_cell_states_the_gains_behind_it(fixtures) -> None:
    """D-174: Stage 27 compares at the same gain, so a cell says which."""
    _insert_interference(fixtures, 0, (None, None))
    _insert_interference(fixtures, 3, (20.0, 32.8))
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_interference(fixtures, 2, (None, None))


def test_an_empty_interference_cell_states_no_gain(fixtures) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_interference(fixtures, 0, (32.8, 32.8))


def _insert_product(execute: Any, started_at: Any, index: int = 0) -> None:
    execute(
        "insert into products (assignment_id, revision, observation_started_at,"
        " station_id, element_index, kind, sha256, size_bytes, simulated)"
        " values ('as_stored', 1, %s, 'st_fixture', %s, 'waterfall', %s, 1024,"
        " false)",
        started_at,
        index,
        ZERO_HASH,
    )


def test_a_product_belongs_to_a_stored_observation(fixtures) -> None:
    """D-176: the foreign key into the hypertable holds."""
    started_at = _insert_observation(fixtures)
    _insert_product(fixtures, started_at)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert_product(fixtures, "2020-01-01T00:00:00Z", index=1)


def test_one_row_per_submitted_element(fixtures) -> None:
    started_at = _insert_observation(fixtures)
    _insert_product(fixtures, started_at)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_product(fixtures, started_at)


def test_no_deferred_table_has_a_retention_policy(conn) -> None:
    """D-178: nothing that holds evidence is dropped on a timer."""
    with conn.cursor() as cur:
        cur.execute(
            "select hypertable_name from timescaledb_information.jobs"
            " where proc_name = 'policy_retention'"
        )
        retained = {r[0] for r in cur.fetchall()}
    assert not retained


def test_the_profiles_and_products_are_plain_tables(conn) -> None:
    """Their volume is profiles built and receptions, not heartbeats."""
    with conn.cursor() as cur:
        cur.execute("select hypertable_name from timescaledb_information.hypertables")
        hypertables = {r[0] for r in cur.fetchall()}
    assert not ({"horizon_profiles", "interference_profiles", "products"} & hypertables)


# --- 0024, the views and the hourly aggregate -----------------------------------


def _detected_observation(execute: Any, assignment_id: str, *, seconds: int) -> Any:
    """A pass, its assignment, and an observation first detected ``seconds`` in.

    Returns the detection instant.
    """
    pass_id = _insert_scheduled_pass(execute, _insert_element_set(execute))
    _insert_assignment(execute, assignment_id, pass_id)
    rows = execute(
        "insert into observations (assignment_id, revision, started_at, ended_at,"
        " station_id, satellite_id, outcome, signal_detected, first_detection_at,"
        " content_sha256)"
        " select %s, 1, p.aos, p.los, 'st_fixture', 'norad:99999', 'decoded', true,"
        " p.aos + %s * interval '1 second', %s from passes p where p.id = %s"
        " returning first_detection_at",
        assignment_id,
        seconds,
        ZERO_HASH,
        pass_id,
    )
    return rows[0][0]


def _clock(execute: Any, at: Any, offset_s: float, uncertainty_s: float) -> None:
    execute(
        "insert into heartbeats (station_id, sent_at, received_at, state,"
        " clock_offset_s, clock_uncertainty_s)"
        " values ('st_fixture', %s, %s, 'idle', %s, %s)",
        at,
        at,
        offset_s,
        uncertainty_s,
    )


def _timing(execute: Any, assignment_id: str) -> tuple[Any, ...]:
    rows = execute(
        "select uncorrected_error_s, timing_error_s, excluded, simulated"
        " from timing_error where assignment_id = %s",
        assignment_id,
    )
    return tuple(rows[0])


def test_timing_error_is_corrected_by_the_station_s_clock(fixtures) -> None:
    """EVALUATION.md §6.1: first detection + clock offset − predicted AOS."""
    detected = _detected_observation(fixtures, "as_timed", seconds=40)
    _clock(fixtures, detected, -2.0, 0.5)

    uncorrected, corrected, excluded, simulated = _timing(fixtures, "as_timed")

    assert float(uncorrected) == 40.0
    assert float(corrected) == 38.0
    assert excluded is None
    assert simulated is False


def test_an_unknown_clock_offset_is_excluded_not_assumed_zero(fixtures) -> None:
    _detected_observation(fixtures, "as_untimed", seconds=40)

    _, corrected, excluded, _ = _timing(fixtures, "as_untimed")

    assert corrected is None
    assert excluded == "clock_offset_unknown"


def test_an_error_inside_the_clock_s_uncertainty_is_flagged(fixtures) -> None:
    detected = _detected_observation(fixtures, "as_fuzzy", seconds=3)
    _clock(fixtures, detected, 0.0, 5.0)

    assert _timing(fixtures, "as_fuzzy")[2] == "within_clock_uncertainty"


def test_a_clock_reported_long_after_the_detection_is_not_used(fixtures) -> None:
    """An offset reported later describes a clock the detection was not made on."""
    detected = _detected_observation(fixtures, "as_late", seconds=40)
    _clock(fixtures, detected + timedelta(hours=1), -2.0, 0.5)

    assert _timing(fixtures, "as_late")[2] == "clock_offset_unknown"


def test_scheduler_performance_follows_a_run_s_assignments(fixtures) -> None:
    fixtures(
        "insert into schedule_runs (run_id, decided_at, horizon_start, horizon_end,"
        " model_config, config_sha256, parameters, yield_source, solver,"
        " solver_version, status, objective, time_limit_s, runtime_s, stations,"
        " candidates, scheduled, skipped)"
        " values ('sr_perf', now(), now(), now() + interval '6 hours', 'A', %s,"
        " '{}'::jsonb, 'elevation_proxy', 'highs', '1.0', 'fallback', 1.0, 10,"
        " 0.1, 1, 2, 2, 0)",
        ZERO_HASH,
    )
    element_set = _insert_element_set(fixtures)
    for index, assignment_id in enumerate(("as_run_a", "as_run_b")):
        _insert_assignment(
            fixtures,
            assignment_id,
            _insert_scheduled_pass(fixtures, element_set, hours_ahead=index),
        )
    fixtures(
        "update assignments set schedule_run_id = 'sr_perf'"
        " where assignment_id in ('as_run_a', 'as_run_b')"
    )
    fixtures(
        "insert into observations (assignment_id, revision, started_at, ended_at,"
        " station_id, satellite_id, outcome, signal_detected, first_detection_at,"
        " frames_decoded, decoder, content_sha256)"
        " values ('as_run_a', 1, now(), now(), 'st_fixture', 'norad:99999',"
        " 'decoded', true, now(), 12, 'satdump', %s)",
        ZERO_HASH,
    )

    (row,) = fixtures(
        "select fell_back, decoded, outstanding, frames_decoded"
        " from scheduler_performance where run_id = 'sr_perf'"
    )

    assert row == (True, 1, 1, 12)


def test_heartbeats_hourly_is_a_continuous_aggregate_with_no_retention(conn) -> None:
    """D-178: the aggregate exists, is real-time, and nothing is dropped."""
    with conn.cursor() as cur:
        cur.execute(
            "select materialized_only"
            " from timescaledb_information.continuous_aggregates"
            " where view_name = 'heartbeats_hourly'"
        )
        assert cur.fetchall() == [(False,)]
        cur.execute(
            "select proc_name from timescaledb_information.jobs"
            " where hypertable_name in ('heartbeats', 'observations')"
            " or proc_name = 'policy_retention'"
        )
        procs = {row[0] for row in cur.fetchall()}
    assert "policy_retention" not in procs


def test_a_run_over_both_populations_is_two_rows_never_one_total(fixtures) -> None:
    """Rule 5: a real station's outcomes are never summed with simulated ones."""
    fixtures(
        "insert into stations (station_id, name, operator, lat_deg, lon_deg, alt_m,"
        " token_sha256, registration_key_sha256, simulated, simulator_run_id, seed)"
        " values ('st_fixture_sim', 'Sim', 'tests', 51.5, -0.1, 20, %s, %s, true,"
        " 'run-both', 1)",
        OTHER_HASH,
        OTHER_HASH,
    )
    fixtures(
        "insert into schedule_runs (run_id, decided_at, horizon_start, horizon_end,"
        " model_config, config_sha256, parameters, yield_source, solver,"
        " solver_version, status, objective, time_limit_s, runtime_s, stations,"
        " candidates, scheduled, skipped)"
        " values ('sr_both', now(), now(), now() + interval '6 hours', 'A', %s,"
        " '{}'::jsonb, 'elevation_proxy', 'highs', '1.0', 'optimal', 1.0, 10,"
        " 0.1, 2, 2, 2, 0)",
        ZERO_HASH,
    )
    element_set = _insert_element_set(fixtures)
    _insert_assignment(
        fixtures, "as_real", _insert_scheduled_pass(fixtures, element_set)
    )
    _insert_assignment(
        fixtures,
        "as_sim",
        _insert_scheduled_pass(fixtures, element_set, hours_ahead=1),
    )
    fixtures(
        "update assignments set station_id = 'st_fixture_sim', simulated = true"
        " where assignment_id = 'as_sim'"
    )
    fixtures(
        "update assignments set schedule_run_id = 'sr_both'"
        " where assignment_id in ('as_real', 'as_sim')"
    )

    rows = fixtures(
        "select simulated, assignments, scheduled from scheduler_performance"
        " where run_id = 'sr_both' order by simulated"
    )

    assert rows == [(False, 1, 2), (True, 1, 2)]


def test_a_rating_names_an_observation_that_exists(rollback) -> None:
    """D-260: a rating belongs to one observation revision, by key."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        rollback(
            "insert into reception_ratings (assignment_id, revision,"
            " observation_started_at, station_id, usable, rubric, rater, simulated)"
            " values ('as_none', 1, now(), 'st_none', true, 'usable-1', 'hr', false)"
        )
