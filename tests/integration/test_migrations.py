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
    }
    assert expected <= tables

    # Deferred, and their absence is deliberate — see D-018.
    deferred = {
        "products",
        "noise_measurements",
        "horizon_profiles",
        "interference_profiles",
    }
    assert not (deferred & tables)


def test_observations_and_heartbeats_are_hypertables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("select hypertable_name from timescaledb_information.hypertables")
        hypertables = {r[0] for r in cur.fetchall()}
    assert {"observations", "heartbeats"} <= hypertables


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
    } <= carrying
    # D-049: element_sets is the deliberate exception. Its provenance lives in
    # `source`, which distinguishes celestrak from spacetrack from manual as
    # well as simulator, and is part of element_set_content_unique (D-057). A
    # boolean here would be derivable from `source` and so able to disagree
    # with it.
    assert "element_sets" not in carrying


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
