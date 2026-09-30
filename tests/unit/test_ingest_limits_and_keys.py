"""A source's published limits, its key, and where it is asked about.

Three things Stage 31 adds to the fetch path, each tested without a socket:

* the ledger stops a fetch **before** a published limit rather than being
  refused at it, waits when a slot reopens soon, and remembers across runs;
* a key is substituted only at the request, and never appears in anything the
  retriever raises — not even where the source takes it in the URL path;
* ``points``, ``bbox`` and ``layers`` reach the adapter from the settings file,
  and a malformed one is refused by name.

Reference: docs/DECISIONS.md D-220, D-223, D-225.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from meridian_ingest.cli_follow import due_sources, follow_round
from meridian_ingest.config import ConfigurationError, load_settings
from meridian_ingest.extent import BoundingBox, GeoPoint
from meridian_ingest.http_retriever import REDACTED, HttpRetriever, SourceAccess
from meridian_ingest.politeness import BudgetExhaustedError, RequestBudget, RetryPolicy
from meridian_ingest.rate_ledger import RateLimiter, RateWindow, RequestLedger
from meridian_ingest.raw_store import RawStore
from meridian_ingest.retrieval import KEY_PLACEHOLDER, RemoteArtefact, RetrievalError

T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
KEY = "a1b2c3d4e5f6secret"


class Clock:
    """A clock a test moves by hand, and a sleep that moves it."""

    def __init__(self) -> None:
        self.now = T0
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def limiter(
    tmp_path: Path, clock: Clock, *windows: RateWindow, wait: float = 30.0
) -> RateLimiter:
    return RateLimiter(
        windows, RequestLedger(tmp_path / "ledger.log"), wait, clock, clock.sleep
    )


# --- published limits --------------------------------------------------------


def test_a_window_is_honoured_under_its_headroom() -> None:
    assert RateWindow(5_000, 600).allowed == 4_500
    assert RateWindow(1, 60).allowed == 1


def test_the_fetch_stops_before_the_limit_rather_than_being_refused(
    tmp_path: Path,
) -> None:
    clock = Clock()
    rate = limiter(tmp_path, clock, RateWindow(10, 3_600))
    for n in range(9):
        rate.acquire(f"artefact {n}")
    with pytest.raises(BudgetExhaustedError, match="published limit"):
        rate.acquire("artefact 9")
    assert len(RequestLedger(tmp_path / "ledger.log").instants()) == 9


def test_a_slot_that_reopens_soon_is_waited_for(tmp_path: Path) -> None:
    clock = Clock()
    rate = limiter(tmp_path, clock, RateWindow(2, 10))
    rate.acquire("first")
    clock.now += timedelta(seconds=4)
    rate.acquire("second")
    assert clock.slept == [6.0]


def test_the_ledger_remembers_across_runs(tmp_path: Path) -> None:
    """The source counts every run's requests together, so we must too."""
    clock = Clock()
    for _ in range(3):
        limiter(tmp_path, clock, RateWindow(4, 3_600)).acquire("one")
    fresh = limiter(tmp_path, clock, RateWindow(4, 3_600))
    assert fresh.remaining() == {RateWindow(4, 3_600): 0}
    with pytest.raises(BudgetExhaustedError):
        fresh.acquire("fourth")


def test_the_ledger_forgets_what_no_window_can_count(tmp_path: Path) -> None:
    clock = Clock()
    rate = limiter(tmp_path, clock, RateWindow(100, 60))
    rate.acquire("old")
    clock.now += timedelta(hours=1)
    rate.acquire("new")
    assert RequestLedger(tmp_path / "ledger.log").instants() == [clock.now]


def test_the_ledger_holds_instants_and_nothing_else(tmp_path: Path) -> None:
    limiter(tmp_path, Clock(), RateWindow(10, 60)).acquire(f"x/{KEY}/y")
    assert KEY not in (tmp_path / "ledger.log").read_text("utf-8")


# --- keys --------------------------------------------------------------------


def keyed(
    url: str = f"https://example.org/api/{KEY_PLACEHOLDER}/area",
) -> RemoteArtefact:
    return RemoteArtefact(
        url=url, original_identifier="area/latest", payload_kind="data"
    )


def retriever(
    handler: Callable[[httpx.Request], httpx.Response], key: str | None = KEY
) -> HttpRetriever:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpRetriever(
        client,
        RequestBudget(4),
        RetryPolicy(attempts=1),
        SourceAccess(key=key),
    )


def test_the_key_is_substituted_at_the_request_only() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"ok", headers={"ETag": '"v1"'})

    remote = keyed()
    with retriever(handler) as fetching:
        fetched = fetching.retrieve(remote)
    assert seen == [f"https://example.org/api/{KEY}/area"]
    assert fetched.remote.url == remote.url
    assert KEY not in repr(fetched)


def test_a_key_in_the_path_is_redacted_from_a_refusal() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"no")

    with retriever(handler) as fetching, pytest.raises(RetrievalError) as caught:
        fetching.retrieve(keyed())
    assert KEY not in str(caught.value)


def test_a_key_is_redacted_even_from_an_error_the_transport_writes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with retriever(handler) as fetching, pytest.raises(RetrievalError) as caught:
        fetching.retrieve(keyed())
    assert KEY not in str(caught.value)
    assert REDACTED in str(caught.value)


def test_a_keyed_artefact_without_a_key_stops_before_any_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made")

    with (
        retriever(handler, key=None) as fetching,
        pytest.raises(RetrievalError, match="api_key_env"),
    ):
        fetching.retrieve(keyed())


def test_the_access_repr_never_shows_the_key() -> None:
    assert KEY not in repr(SourceAccess(key=KEY))


# --- places ------------------------------------------------------------------


def settings_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ingest.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_places_reach_the_source_settings(tmp_path: Path) -> None:
    settings = load_settings(
        settings_file(
            tmp_path,
            "[sources.open_meteo_cloud]\npoints = [[12.97, 77.59]]\n"
            "[sources.nasa_firms]\nbbox = [74.0, 11.5, 78.6, 18.5]\n"
            '[sources.isro_bhuvan]\nlayers = ["lulc:KA_LULC50K_1516"]\n',
        )
    )
    assert settings.for_source("open_meteo_cloud").places.points == (
        GeoPoint(12.97, 77.59),
    )
    assert settings.for_source("nasa_firms").places.bbox == BoundingBox(
        74.0, 11.5, 78.6, 18.5
    )
    assert settings.for_source("isro_bhuvan").places.layers == ("lulc:KA_LULC50K_1516",)


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ("points = [[95.0, 10.0]]", "latitude 95.0"),
        ("points = [12.0]", "2 numbers"),
        ("bbox = [78.0, 11.0, 74.0, 18.0]", "no inside"),
        ('layers = [""]', "layer names"),
    ],
)
def test_a_malformed_place_is_refused_by_name(
    tmp_path: Path, body: str, said: str
) -> None:
    with pytest.raises(ConfigurationError, match=said):
        load_settings(settings_file(tmp_path, f"[sources.nasa_firms]\n{body}\n"))


def test_a_real_source_is_off_until_enabled_and_its_key_variable_is_known(
    tmp_path: Path,
) -> None:
    settings = load_settings(settings_file(tmp_path, "timeout_s = 5.0\n"))
    assert settings.enabled_sources() == ("reference_archive",)
    assert settings.for_source("nasa_firms").key_env == "FIRMS_MAP_KEY"
    enabled = load_settings(
        settings_file(tmp_path, "[sources.noaa_swpc_kp]\nenabled = true\n")
    )
    assert enabled.enabled_sources() == ("noaa_swpc_kp", "reference_archive")


# --- follow ------------------------------------------------------------------


def test_follow_fetches_what_is_due_and_then_nothing_until_its_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reference archive's retriever reads files, so nothing is reached."""
    monkeypatch.chdir(Path(__file__).parents[2])
    settings = load_settings(settings_file(tmp_path, 'raw_root = "raw"\n'))
    store = RawStore(settings.raw_root)
    sources = ("reference_archive",)
    assert due_sources(store, sources, T0) == sources

    in_august = datetime(2026, 8, 20, tzinfo=UTC)
    assert not follow_round(settings, sources, load=False, now=in_august)
    newest = store.read(store.scan("reference_archive")[-1]).manifest.provenance
    assert due_sources(store, sources, newest.retrieved_at + timedelta(hours=1)) == ()
    assert (
        due_sources(store, sources, newest.retrieved_at + timedelta(days=1)) == sources
    )
