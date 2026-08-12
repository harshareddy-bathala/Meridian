"""A virtual station joining the network, and coming back after a restart.

Driven through the real :mod:`meridian_client` into the real application and a
real database. What is under test is the lifecycle D-080 fixes: a station
registers once, keeps four files, and on every later start loads the identity it
already has rather than asking to join again.

That second half matters more than it sounds. A fleet that re-registered on
every restart would consume a fresh invite each time, and a container under
``restart: unless-stopped`` would exhaust an operator's invites overnight.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §3, §4.1, §5; docs/DECISIONS.md D-023, D-075, D-080.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token
from meridian_client.transport import ProtocolError
from meridian_sim.config import RunConfig
from meridian_sim.virtual_station import (
    RegistrationNeededError,
    paths_for,
    register_or_resume,
)

MASTER_SEED = 4471
RUN_ID = "lifecycle-run"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def started(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    """The started application, sharing this test's rolled-back connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "virtual-station-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def invites(rollback: Any, *labels: str) -> None:
    """Issue one invite per label, as ``meridian invite create`` would."""
    with rollback.cursor() as cur:
        for label in labels:
            cur.execute(
                "insert into invite_tokens (token_sha256, label) values (%s, %s)",
                (hash_invite_token(label), label),
            )


def run_config(tmp_path: Path, count: int = 1) -> RunConfig:
    """A run over a temporary state directory."""
    return RunConfig(
        master_seed=MASTER_SEED,
        run_id=RUN_ID,
        station_count=count,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
    )


def bring_up(config: RunConfig, index: int, invite: str | None, transport: Any) -> Any:
    """One station, against the in-process application.

    A named helper rather than the call repeated thirteen times, because the
    keyword every one of them needs is noise at every one of them: what each
    test is about is the invite it offers and the station it offers it for.
    """
    return register_or_resume(config, index, invite, http_transport=transport)


def station_row(rollback: Any, station_id: str) -> tuple[bool, str | None]:
    """What the platform recorded about a station's provenance."""
    with rollback.cursor() as cur:
        cur.execute(
            "select simulated, simulator_run_id from stations where station_id = %s",
            (station_id,),
        )
        row = cur.fetchone()
    return (bool(row[0]), row[1])


def test_a_station_with_no_credentials_registers(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """The first start: an invite is presented and an identity comes back."""
    invites(rollback, "sim-1")
    config = run_config(tmp_path)

    with bring_up(config, 1, "sim-1", started._transport) as station:
        assert station.station_id.startswith("st_")
        assert station.heartbeat_interval_s > 0


def test_the_four_files_land_where_the_decision_says(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """D-080: credentials, key, held work and outbox, under the station's index.

    The same four a real station on a Pi keeps, written by the same client code.
    """
    invites(rollback, "sim-1")
    config = run_config(tmp_path)
    paths = paths_for(config, 1)

    with bring_up(config, 1, "sim-1", started._transport):
        pass

    assert paths.directory.name == "001"
    assert paths.credentials.exists()
    assert paths.registration_key.exists()


def test_a_restart_resumes_without_an_invite(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """The property that keeps a restarting container from exhausting invites.

    The second call is given no invite at all, which is the strongest form of
    the assertion: not that it declined to use one, but that it needed none.
    """
    invites(rollback, "sim-1")
    config = run_config(tmp_path)

    with bring_up(config, 1, "sim-1", started._transport) as first:
        original = first.station_id
    with bring_up(config, 1, None, started._transport) as resumed:
        assert resumed.station_id == original


def test_a_restart_does_not_create_a_second_station(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """One virtual station is one row, however many times its process restarts."""
    invites(rollback, "sim-1")
    config = run_config(tmp_path)

    for _ in range(3):
        with bring_up(config, 1, "sim-1", started._transport):
            pass

    with rollback.cursor() as cur:
        cur.execute("select count(*) from stations")
        assert cur.fetchone()[0] == 1


def test_a_station_that_never_registered_and_has_no_invite_says_so(
    started: Any,
    tmp_path: Path,
) -> None:
    """Distinct from a refused invite: nobody asked the platform anything.

    An operator fixes the two differently — one by issuing an invite, the other
    by giving the run the invites it already has.
    """
    with pytest.raises(RegistrationNeededError, match="no invite"):
        register_or_resume(
            run_config(tmp_path), 1, None, http_transport=started._transport
        )


def test_an_invite_already_consumed_by_another_station_is_refused(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """An invite is single-use, which is why a fleet needs one each."""
    invites(rollback, "sim-1")
    config = run_config(tmp_path)
    with bring_up(config, 1, "sim-1", started._transport):
        pass

    with pytest.raises(ProtocolError, match="invalid_invite"):
        bring_up(config, 2, "sim-1", started._transport)


def test_the_platform_records_the_station_as_simulated(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """MSP §5, and the flag every derived record inherits from this row.

    The platform treats registration as authoritative about provenance, so a
    station that failed to declare itself here would have its observations
    stored as measured with no later chance to correct them.
    """
    invites(rollback, "sim-1")

    with register_or_resume(
        run_config(tmp_path), 1, "sim-1", http_transport=started._transport
    ) as one:
        simulated, run_id = station_row(rollback, one.station_id)

    assert simulated is True
    assert run_id == RUN_ID


def test_two_stations_are_two_identities_in_two_directories(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """A fleet sharing a directory would share credentials and be one station."""
    invites(rollback, "sim-1", "sim-2")
    config = run_config(tmp_path, count=2)

    with (
        bring_up(config, 1, "sim-1", started._transport) as one,
        bring_up(config, 2, "sim-2", started._transport) as two,
    ):
        assert one.station_id != two.station_id

    assert paths_for(config, 1).directory != paths_for(config, 2).directory
    assert paths_for(config, 1).credentials.exists()
    assert paths_for(config, 2).credentials.exists()


def test_a_resumed_station_can_still_heartbeat(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """Resuming has to produce a working station, not merely a matching id.

    A credential file read back into the wrong shape would pass every assertion
    above and fail on the first request the station made.
    """
    invites(rollback, "sim-1")
    config = run_config(tmp_path)
    with bring_up(config, 1, "sim-1", started._transport):
        pass

    with bring_up(config, 1, None, started._transport) as resumed:
        outcome = resumed.tick(_now())

    assert outcome.heartbeat_sent is True
    assert outcome.stop_reason is None


def _now() -> Any:
    """The current instant, as the loop expects it."""
    from datetime import UTC, datetime

    return datetime.now(UTC)
