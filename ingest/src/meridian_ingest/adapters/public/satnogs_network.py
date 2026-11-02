"""Archive receptions: another network's observations, for training and evaluation only.

**What it is for, and what it is not** (D-322, amending D-053). SatNOGS Network
publishes every observation its volunteer stations make, each rated by its own
rules. Meridian reads them to fit the yield model and the simulator's outcome
distributions, and to cross-check a satellite our own station heard nothing
from (D-138). Never as a runtime input to scheduling or reception (D-102), and
never in a published comparison of their totals with ours (D-053). The rows
land in ``archive_observations``, apart from our own (D-139).

**Written from the public API's own responses and pages, not its code.** The
network's software is AGPL, and nothing of it was read or copied (rule 3). The
observations endpoint is undocumented beyond its browsable page, so what is
relied on here is what that page and its answers show: ``norad_cat_id``,
``start`` and ``end`` filter, results come newest first, 25 to a page, and
further pages are a cursor in a ``Link`` header.

**One request is one satellite over one six-hour window**, aligned to the UTC
day. The fetch path does not follow cursors (a listing expands one level), so
the window is sized to fit in one page: a Meteor satellite is observed about
sixteen times a day across the whole network. A page that comes back full may
have been cut short, and is refused rather than stored as though it were
complete, because a reception silently missing from the archive is a selection
filter nobody chose (D-139, D-150). Shrink the window if it ever happens.

**A reception belongs to the window it starts in.** Whatever the endpoint
means by ``start`` and ``end``, the normaliser keeps only observations whose
start falls inside the artefact's window, so adjacent windows never count one
reception twice.

**Their words, kept.** ``status`` is the network's own rating — good, bad,
unknown, failed — and ``waterfall_status`` a person's vetting. Both are kept
verbatim in ``source_outcome``; ``archive_outcome`` is our reading of them,
in our vocabulary, and never ``no_signal`` (D-139). A ``future`` observation
has not happened, and is not a reception.

Reference: docs/DECISIONS.md D-053, D-102, D-138, D-139, D-150, D-322, D-323;
https://network.satnogs.org/ (licence statement), https://network.satnogs.org/api/.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
)
from meridian_ingest.adapters.public.common import (
    parse_json,
    require_interval,
    version_from_headers,
)
from meridian_ingest.normalise.records import (
    NormalisationError,
    NormalisedBatch,
    NormalisedReception,
    NormalisedStation,
)
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = [
    "PAGE_SIZE",
    "SATNOGS_NETWORK",
    "TRANSFORMATION_VERSION",
    "WINDOW",
    "SatnogsNetworkAdapter",
    "SatnogsNetworkNormaliser",
    "archive_outcome",
]

SATNOGS_NETWORK = SourceDescriptor(
    source_id="satnogs_network",
    source_class="archive_receptions",
    name="SatNOGS Network observations",
    licence=(
        "CC BY-SA 4.0: the network states that all data are distributed freely "
        "under the Creative Commons Attribution-ShareAlike licence"
    ),
    terms_url="https://network.satnogs.org/",
    attribution_entry="SatNOGS Network observations",
    access_constraint="none",
)

BASE_URL = "https://network.satnogs.org/api/observations/"
TRANSFORMATION_VERSION = "satnogs-network-1"
WINDOW = timedelta(hours=6)
PAGE_SIZE = 25
"""Results the endpoint returns to a page, as its answers show. A page this
full may have more behind its cursor."""


class SatnogsNetworkAdapter:
    """One artefact per satellite per six-hour window that has ended."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return SATNOGS_NETWORK

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """Every whole window inside ``[since, until)``, for each satellite named.

        A window still open is left for a later fetch: its observations have
        not all happened, and those that have are not yet rated.

        Raises:
            ValueError: No satellite is named, the interval is missing, or more
                windows are planned than the request allows. Never cut short:
                the windows left out would simply be absent from the archive.
        """
        if not request.catalog_numbers:
            message = (
                f"{SATNOGS_NETWORK.source_id} is asked about satellites: set "
                f"[sources.{SATNOGS_NETWORK.source_id}] catalog_numbers"
            )
            raise ValueError(message)
        since, until = require_interval(request, SATNOGS_NETWORK.source_id)
        windows = _windows(since, until)
        planned = tuple(
            _remote(number, start)
            for number in request.catalog_numbers
            for start in windows
        )
        if len(planned) > request.limit:
            message = (
                f"{len(planned)} windows are planned and the limit is "
                f"{request.limit}; raise request_budget or fetch a shorter interval"
            )
            raise ValueError(message)
        return planned

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The server's validator for this page, or when it served it."""
        return version_from_headers(retrieved)


class SatnogsNetworkNormaliser:
    """One page of observations as receptions and the stations that made them."""

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """The receptions that started inside this artefact's window.

        Raises:
            NormalisationError: The page is not a list of observations, is full
                and so possibly cut short, names another satellite than the one
                asked about, or carries no window to judge starts against.
        """
        provenance = artefact.manifest.provenance
        refuse_a_tile(provenance)
        if provenance.valid_from is None or provenance.valid_to is None:
            message = f"{artefact.raw_path} carries no window to judge starts against"
            raise NormalisationError(message)
        payload = parse_json(artefact)
        if not isinstance(payload, list):
            message = f"{artefact.raw_path} is not a JSON array of observations"
            raise NormalisationError(message)
        if len(payload) >= PAGE_SIZE:
            message = (
                f"{artefact.raw_path} holds {len(payload)} observations, a full "
                "page: more may be behind its cursor, so it is refused rather "
                "than stored as complete. Fetch a shorter window"
            )
            raise NormalisationError(message)
        asked = provenance.original_identifier.split("/", 1)[0].removeprefix("norad=")
        stations: dict[str, NormalisedStation] = {}
        receptions = []
        for index, entry in enumerate(payload):
            where = f"{artefact.raw_path}[{index}]"
            observation = _object(entry, where)
            if str(observation.get("norad_cat_id")) != asked:
                message = (
                    f"{where} is satellite {observation.get('norad_cat_id')}, not "
                    f"{asked}: the filter was not applied, and the page is not "
                    "what was asked for"
                )
                raise NormalisationError(message)
            started = _instant(observation.get("start"), f"{where}.start")
            if observation.get("status") == "future":
                continue
            if not provenance.valid_from <= started < provenance.valid_to:
                continue
            station = _station(observation, where)
            stations.setdefault(station.source_station_key, station)
            receptions.append(_reception(observation, station, started, where))
        return NormalisedBatch(
            transformation_version=self.transformation_version,
            stations=tuple(stations.values()),
            receptions=tuple(receptions),
        )


def archive_outcome(status: str, waterfall_status: str, frames: int) -> str:
    """Our reading of the network's rating, in ``archive_observations``' words.

    Args:
        status: The network's automatic rating: good, bad, unknown or failed.
        waterfall_status: A person's vetting of the waterfall, when there is one.
        frames: How many demodulated-data files the observation carries.

    Returns:
        ``decoded`` for a good observation that carries demodulated data;
        ``signal_no_decode`` for a good one that does not, or one a person
        vetted as having signal; ``no_data`` for a bad one; ``unknown``
        otherwise. A failed observation is the station's failure, not the
        satellite's silence, so it says nothing either way.
    """
    if status == "good":
        return "decoded" if frames > 0 else "signal_no_decode"
    if status == "bad":
        return "no_data"
    if waterfall_status == "with-signal":
        return "signal_no_decode"
    return "unknown"


def _windows(since: datetime, until: datetime) -> list[datetime]:
    """Starts of the whole windows in ``[since, until)``, on the UTC six-hour grid."""
    first = since.astimezone(UTC)
    day = first.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day + WINDOW * -(-(first - day) // WINDOW)
    starts = []
    while start + WINDOW <= until:
        starts.append(start)
        start += WINDOW
    return starts


def _remote(number: int, start: datetime) -> RemoteArtefact:
    """One satellite's window, named by both. Never a path element (D-141)."""
    end = start + WINDOW
    return RemoteArtefact(
        url=(
            f"{BASE_URL}?format=json&norad_cat_id={number}"
            f"&start={_iso(start)}&end={_iso(end)}"
        ),
        original_identifier=f"norad={number}/{_iso(start)}",
        payload_kind="data",
        valid_from=start,
        valid_to=end,
    )


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _object(entry: object, where: str) -> dict[str, object]:
    if not isinstance(entry, dict):
        message = f"{where} is {type(entry).__name__}, not an observation object"
        raise NormalisationError(message)
    return {str(key): value for key, value in entry.items()}


def _instant(value: object, where: str) -> datetime:
    if not isinstance(value, str):
        message = f"{where} is not a timestamp"
        raise NormalisationError(message)
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        message = f"{where} {value!r} is not an ISO 8601 instant"
        raise NormalisationError(message) from exc
    if moment.tzinfo is None:
        message = f"{where} {value!r} carries no offset"
        raise NormalisationError(message)
    return moment.astimezone(UTC)


def _station(observation: dict[str, object], where: str) -> NormalisedStation:
    key = observation.get("ground_station")
    if not isinstance(key, int):
        message = f"{where} names no ground station"
        raise NormalisationError(message)
    return NormalisedStation(
        source_station_key=str(key),
        name=_text(observation.get("station_name")),
        lat_deg=_number(observation.get("station_lat")),
        lon_deg=_number(observation.get("station_lng")),
        alt_m=_number(observation.get("station_alt")),
    )


def _reception(
    observation: dict[str, object],
    station: NormalisedStation,
    started: datetime,
    where: str,
) -> NormalisedReception:
    status = _text(observation.get("status")) or "unknown"
    waterfall = _text(observation.get("waterfall_status")) or "unknown"
    demoddata = observation.get("demoddata")
    frames = len(demoddata) if isinstance(demoddata, list) else 0
    frequency = observation.get("observation_frequency")
    mode = _text(observation.get("transmitter_mode"))
    return NormalisedReception(
        source_observation_id=str(observation.get("id")),
        satellite_key=f"norad:{observation.get('norad_cat_id')}",
        satellite_key_kind="norad",
        started_at=started,
        ended_at=_instant(observation.get("end"), f"{where}.end"),
        archive_outcome=archive_outcome(status, waterfall, frames),
        source_station_key=station.source_station_key,
        max_elevation_deg=_number(observation.get("max_altitude")),
        centre_freq_hz=frequency if isinstance(frequency, int) else None,
        mode=None if mode is None else mode.lower(),
        source_outcome=f"status={status};waterfall_status={waterfall}",
    )


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
