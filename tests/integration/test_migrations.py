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
    """The smallest row graph an observation can hang off: one station, one satellite."""
    rollback("insert into satellites (satellite_id, name) values (%s, %s)", "norad:99999", "Test")
    rollback(
        "insert into stations (station_id, name, operator, lat_deg, lon_deg, alt_m,"
        " token_sha256, registration_key_sha256) values (%s, %s, %s, %s, %s, %s, %s, %s)",
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


def test_all_expected_tables_exist(conn) -> None:  # type: ignore[no-untyped-def]
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
    deferred = {"products", "noise_measurements", "horizon_profiles", "interference_profiles"}
    assert not (deferred & tables)


def test_observations_and_heartbeats_are_hypertables(conn) -> None:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("select hypertable_name from timescaledb_information.hypertables")
        hypertables = {r[0] for r in cur.fetchall()}
    assert {"observations", "heartbeats"} <= hypertables


def test_heartbeats_partitions_on_platform_clock(scalar) -> None:  # type: ignore[no-untyped-def]
    """Never partition on a client-supplied timestamp (D-013).

    A station with a dead RTC reporting ``sent_at: 1970-01-01`` would otherwise
    create a 1970 chunk that every compression and retention policy mishandles.
    """
    column = scalar(
        "select column_name from timescaledb_information.dimensions "
        "where hypertable_name = 'heartbeats'"
    )
    assert column == "received_at"


def test_held_assignments_defaults_to_empty_not_null(scalar) -> None:  # type: ignore[no-untyped-def]
    """MSP §4.2: an empty list is meaningful and must stay distinguishable from absence."""
    nullable = scalar(
        "select is_nullable from information_schema.columns "
        "where table_name = 'heartbeats' and column_name = 'held_assignments'"
    )
    assert nullable == "NO"


def test_simulated_flag_reaches_every_derived_table(conn) -> None:  # type: ignore[no-untyped-def]
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
    assert {"stations", "passes", "assignments", "observations", "heartbeats"} <= carrying


def test_outcome_enum_is_exactly_the_msp_five(scalar) -> None:  # type: ignore[no-untyped-def]
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
    assert accepted == {"decoded", "signal_no_decode", "no_signal", "aborted", "not_attempted"}


def test_a_sixth_outcome_is_rejected(fixtures) -> None:  # type: ignore[no-untyped-def]
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


def test_observations_current_returns_the_latest_revision(fixtures) -> None:  # type: ignore[no-untyped-def]
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
        "select revision from observations where assignment_id = %s order by revision", "as_view"
    )
    assert [row[0] for row in both] == [1, 2]

    current = fixtures(
        "select revision, outcome from observations_current where assignment_id = %s", "as_view"
    )
    assert current == [(2, "decoded")]


def test_observation_id_is_a_stored_generated_column(scalar) -> None:  # type: ignore[no-untyped-def]
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


def test_listening_block_is_all_or_nothing_including_mode(scalar) -> None:  # type: ignore[no-untyped-def]
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


def test_stations_carry_a_registration_key_hash(scalar) -> None:  # type: ignore[no-untyped-def]
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


def test_a_bound_invite_cannot_be_consumed_by_another_station(fixtures) -> None:  # type: ignore[no-untyped-def]
    """D-034: the binding is the security property, so the database enforces it.

    A replacement invite names the station whose token it rotates. If any other
    station could redeem it, an operator recovering station A would have issued a
    credential rotation for station B — and the register handler would be the only
    thing standing between the two.
    """
    fixtures(
        "insert into stations (station_id, name, operator, lat_deg, lon_deg, alt_m,"
        " token_sha256, registration_key_sha256) values (%s, %s, %s, %s, %s, %s, %s, %s)",
        "st_other",
        "Other",
        "tests",
        0.0,
        0.0,
        0.0,
        ZERO_HASH,
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


def test_a_bound_invite_is_consumable_by_the_station_it_names(fixtures) -> None:  # type: ignore[no-untyped-def]
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
        "select consumed_by_station_id from invite_tokens where token_sha256 = %s", ZERO_HASH
    )
    assert rows == [("st_fixture",)]


def test_an_unbound_invite_still_admits_any_station(fixtures) -> None:  # type: ignore[no-untyped-def]
    """D-020's ordinary invite is unchanged by D-034: null means "any new station"."""
    fixtures("insert into invite_tokens (token_sha256, label) values (%s, %s)", ZERO_HASH, "seeded")
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


def test_declared_horizon_mask_defaults_to_an_empty_array(scalar) -> None:  # type: ignore[no-untyped-def]
    """D-031: a station that declares nothing is not claiming a clear horizon.

    The default is ``[]`` rather than ``NULL`` so "declared nothing" and
    "declared an empty mask" stay the same statement, and neither is mistaken
    for a learned profile.
    """
    default = scalar(
        "select column_default from information_schema.columns "
        "where table_name = 'station_capabilities' and column_name = 'horizon_mask_json'"
    )
    assert default is not None and "[]" in default


def test_no_plaintext_token_columns_exist(conn) -> None:  # type: ignore[no-untyped-def]
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
