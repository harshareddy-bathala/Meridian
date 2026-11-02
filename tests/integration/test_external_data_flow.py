"""Stage 34's gate: external data reaches what reads it, and nothing needs it.

*External data reaches every module that reads it, through ``meridian-ingest``
alone and labelled with its source, and the platform keeps scheduling from
what it last stored when every source is unreachable.*

The element-set chain is flown here end to end, on the real propagator:

    deploy/catalogue/meteor.json → catalogue load (satellites, no sets)
    celestrak fixture → raw store → load → element_sets (celestrak, its artefact)
    → pass generation → passes, each naming the fetched set it came from

and then three properties are asserted on what came out:

* **accurate** — a predicted culmination agrees with the simulator's own
  propagation of the same two lines (``meridian_sim.sky_track``, separate code,
  D-252) to a hundredth of a degree, and is the highest point a second either
  side;
* **independent** — with every new socket refused, a later horizon is still
  generated from the stored sets, and the age the scrape publishes grows;
* **apart** — no platform module imports ``meridian_ingest``, and nothing on
  the scheduling path reads an archive table or an archive-only set.

The other hops have tests of their own, named so the gate reads whole:
``test_satnogs_archive_load.py`` (archive receptions and carried sets to an
exported denominator, D-325), ``test_environment_load.py`` and
``tests/unit/test_conditions_gate.py`` (published values to the conditions
feature, D-222, D-224), and ``tests/unit/test_report_summary.py`` (an archive
run to the published model summary).

Reference: docs/DECISIONS.md D-079, D-138, D-225, D-320 to D-325.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.catalogue_file import read_catalogue  # noqa: E402
from meridian.cli_catalogue import load_document  # noqa: E402
from meridian.orbit.skyfield_service import SkyfieldOrbitService  # noqa: E402
from meridian.pass_generation import GenerationHorizon, generate_passes  # noqa: E402
from meridian.store.element_sets import find_current_epochs  # noqa: E402
from meridian.store.passes import find_passes_in_horizon  # noqa: E402
from meridian_ingest.adapters import REGISTRY  # noqa: E402
from meridian_ingest.adapters.protocol import FetchRequest  # noqa: E402
from meridian_ingest.load import load_source  # noqa: E402
from meridian_ingest.raw_store import RawStore  # noqa: E402
from meridian_sim.sky_track import Site, track  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "unit" / "public_fixtures"
STATION = "st_bengaluru"
SITE = (12.97, 77.59, 900.0)
FETCHED = datetime(2026, 11, 2, 14, tzinfo=UTC)
FIRST_DAY = GenerationHorizon(
    start=datetime(2026, 11, 2, 15, tzinfo=UTC),
    end=datetime(2026, 11, 3, 15, tzinfo=UTC),
)
LATER_DAY = GenerationHorizon(
    start=datetime(2026, 11, 4, 15, tzinfo=UTC),
    end=datetime(2026, 11, 5, 15, tzinfo=UTC),
)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RawStore]:
    root = tmp_path / "raw"
    yield RawStore(root)
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


@pytest.fixture
def fetched(rollback: Any, store: RawStore, publish_public: Any) -> Any:
    """The live catalogue, one station listening, and one CelesTrak fetch loaded."""
    load_document(rollback, read_catalogue(REPO / "deploy/catalogue/meteor.json"))
    with rollback.cursor() as cur:
        cur.execute(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
            " alt_m, token_sha256, registration_key_sha256)"
            " values (%s, 'Gate', 'tests', %s, %s, %s, %s, %s)",
            (STATION, *SITE, b"\x07" * 32, b"\x08" * 32),
        )
        cur.execute(
            "insert into station_capabilities (station_id, band, freq_min_hz,"
            " freq_max_hz, modes, polarisation, min_elevation_deg)"
            " values (%s, 'vhf', 137000000, 138000000, %s, 'rhcp', 5.0)",
            (STATION, ["lrpt"]),
        )
    (remote,) = REGISTRY["celestrak_gp"].adapter.plan(FetchRequest(groups=("weather",)))
    publish_public(
        store, "celestrak_gp", remote, FIXTURES / "celestrak-weather.tle", FETCHED
    )
    report = load_source(rollback, store, "celestrak_gp")
    assert report.element_sets_written == 2
    assert report.element_sets_untracked == 1
    return rollback


def stored(conn: Any, horizon: GenerationHorizon) -> list[Any]:
    return find_passes_in_horizon(conn, STATION, horizon.start, horizon.end)


def test_fetched_sets_become_passes_that_name_them(fetched: Any) -> None:
    report = generate_passes(fetched, SkyfieldOrbitService(), FIRST_DAY)

    passes = stored(fetched, FIRST_DAY)
    assert report.passes_stored == len(passes) > 0
    assert {one.satellite_id for one in passes} == {"norad:57166", "norad:59051"}
    with fetched.cursor() as cur:
        cur.execute(
            "select e.source, e.ingest_record_id is not null, p.simulated"
            " from passes p join element_sets e on e.id = p.element_set_id"
            " where p.station_id = %s",
            (STATION,),
        )
        assert set(cur.fetchall()) == {("celestrak", True, False)}


def test_a_predicted_culmination_is_skyfields_own(fetched: Any) -> None:
    """The platform's prediction against another propagation of the same lines."""
    generate_passes(fetched, SkyfieldOrbitService(), FIRST_DAY)
    first = min(stored(fetched, FIRST_DAY), key=lambda one: one.aos)
    with fetched.cursor() as cur:
        cur.execute(
            "select line1, line2 from element_sets where id = %s",
            (first.element_set_id,),
        )
        line1, line2 = cur.fetchone()

    instants = [
        first.max_elevation_at - timedelta(seconds=1),
        first.max_elevation_at,
        first.max_elevation_at + timedelta(seconds=1),
    ]
    before, at, after = track(line1, line2, Site(*SITE), instants)

    assert abs(at.elevation_deg - first.max_elevation_deg) < 0.01
    assert before.elevation_deg <= at.elevation_deg + 1e-3
    assert after.elevation_deg <= at.elevation_deg + 1e-3


def test_with_every_source_unreachable_it_keeps_generating_from_what_it_holds(
    fetched: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The independence test, at the element-set path (D-138, D-321)."""
    generate_passes(fetched, SkyfieldOrbitService(), FIRST_DAY)

    def refused(*_: object, **__: object) -> None:
        message = "the gate has no network"
        raise OSError(message)

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    with pytest.raises(OSError, match="no network"):
        socket.create_connection(("celestrak.org", 443))

    report = generate_passes(fetched, SkyfieldOrbitService(), LATER_DAY)

    assert report.passes_stored == len(stored(fetched, LATER_DAY)) > 0
    sooner = find_current_epochs(fetched, FIRST_DAY.start)
    later = find_current_epochs(fetched, LATER_DAY.start)
    assert sooner == later, "the same sets, because nothing new arrived"
    age = {key: LATER_DAY.start - epoch for key, epoch in later.items()}
    assert all(one > FIRST_DAY.start - later[key] for key, one in age.items())


def test_nothing_live_imports_ingest_or_reads_the_archive() -> None:
    """Apart by construction: the arrow points one way (D-138, D-102, D-325)."""
    platform = REPO / "platform" / "src" / "meridian"
    for path in platform.rglob("*.py"):
        text = path.read_text("utf-8")
        assert "meridian_ingest" not in text.replace("``meridian_ingest", ""), path
    for folder in ("scheduler", "jobs", "orbit"):
        for path in (platform / folder).rglob("*.py"):
            text = path.read_text("utf-8")
            assert "archive_observations" not in text, path
            assert "satnogs_network" not in text, path
