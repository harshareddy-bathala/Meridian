"""What a normaliser returns: stations, receptions, and the digests over them.

One artefact normalises to one :class:`NormalisedBatch`. These are deliberately
*not* the store's insertable types: they carry no ``record_id`` and no
``archive_station_id``, because those are assigned during the load, and a
normaliser able to set one would be a normaliser whose output depended on what
the database already held — which is exactly the property the completion gate
denies.

**Each record computes its own digest.** ``content_sha256`` is a property, not
a field, so no caller can supply one that disagrees with the record it
describes — the reasoning ``NewElementSet`` applies, made unavoidable rather
than conventional. Those digests are what make "this snapshot renormalises to
the same rows" a byte comparison instead of an opinion.

**The outcome vocabulary is the archive's, and it is not MSP's.** ``no_data``,
never ``no_signal``: ``no_signal`` asserts that a station was verifiably
listening and heard nothing (rule 7, D-010), and no heartbeat exists for
somebody else's station. The refusal is here as well as in the CHECK because a
normaliser that wrote ``no_signal`` should stop before the load, where the
mistake is still one artefact rather than a table.

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-139, D-140.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from meridian_ingest.raw_manifest import canonical_sha256, instant

__all__ = [
    "ARCHIVE_OUTCOMES",
    "SATELLITE_KEY_KINDS",
    "NormalisationError",
    "NormalisedBatch",
    "NormalisedReception",
    "NormalisedStation",
]

ARCHIVE_OUTCOMES = ("decoded", "signal_no_decode", "no_data", "unknown")
"""What an archive reception may have come to, in our words.

``no_data`` where MSP would say ``no_signal``. The values differ on purpose, so
an accidental ``union`` of ``observations`` and ``archive_observations`` fails
a CHECK rather than returning a plausible number (D-139).
"""

SATELLITE_KEY_KINDS = ("norad", "international_designator", "source_name")
"""How confidently the object was identified.

``source_name`` is the honest answer when an archive gave a name we could not
resolve. It is kept, rather than dropped or guessed into a catalogue number,
because dropping it would stack a second selection filter on the archive's own.
"""

_MIN_LAT, _MAX_LAT = -90.0, 90.0
_MIN_LON, _MAX_LON = -180.0, 180.0


class NormalisationError(ValueError):
    """A record a normaliser produced that could not be a row.

    Raised at construction, so the artefact that produced it is still the unit
    of failure. The database's CHECKs say the same things; hitting them instead
    would mean discovering a normaliser bug as a half-loaded batch.
    """


@dataclass(frozen=True, slots=True)
class NormalisedStation:
    """A station as an archive described it, before it has a row.

    Raises:
        NormalisationError: The key is blank, the coordinates are half given or
            out of range.
    """

    source_station_key: str
    """The archive's own key for the station, verbatim. Never a path (D-141)."""

    name: str | None = None
    lat_deg: float | None = None
    lon_deg: float | None = None
    """Published, not measured, and frequently absent.

    Stored as a pair or not at all: a latitude without a longitude is not a
    place, and Stage 16 would use it as one.
    """

    alt_m: float | None = None
    capability: Mapping[str, object] | None = None
    """What the archive says the station can receive, in the archive's shape."""

    def __post_init__(self) -> None:
        """Hold the record to what ``archive_stations`` would hold it to."""
        if not self.source_station_key.strip():
            message = "a station with no key cannot be referred to by a reception"
            raise NormalisationError(message)
        if (self.lat_deg is None) != (self.lon_deg is None):
            message = (
                f"{self.source_station_key} has half a location "
                f"({self.lat_deg}, {self.lon_deg}); a latitude without a longitude "
                "is not a place, and a denominator would be computed from it"
            )
            raise NormalisationError(message)
        _in_range("lat_deg", self.lat_deg, _MIN_LAT, _MAX_LAT)
        _in_range("lon_deg", self.lon_deg, _MIN_LON, _MAX_LON)

    @property
    def content_sha256(self) -> bytes:
        """The digest this description is content-keyed by.

        A station whose published coordinates change hashes differently and so
        becomes a new row, leaving every completeness denominator already
        computed from the old ones explainable (D-139).
        """
        return canonical_sha256(
            {
                "source_station_key": self.source_station_key,
                "name": self.name,
                "lat_deg": self.lat_deg,
                "lon_deg": self.lon_deg,
                "alt_m": self.alt_m,
                "capability": None
                if self.capability is None
                else dict(self.capability),
            }
        )


@dataclass(frozen=True, slots=True)
class NormalisedReception:
    """One reception as an archive published it, before it has a row.

    Raises:
        NormalisationError: The outcome or key kind is outside its vocabulary,
            an instant is naive, or the reception ends before it starts.
    """

    source_observation_id: str
    """The archive's own identifier for this reception."""

    satellite_key: str
    """Canonical text, ``norad:NNNNN`` where the archive gave a catalogue
    number. Joined at read time; never a foreign key (D-139)."""

    satellite_key_kind: str
    started_at: datetime
    archive_outcome: str
    source_station_key: str | None = None
    """Which station in the same batch made it, or None where the artefact did
    not say. Resolved to an ``archive_station_id`` by the load."""

    ended_at: datetime | None = None
    max_elevation_deg: float | None = None
    centre_freq_hz: int | None = None
    mode: str | None = None
    source_outcome: str | None = None
    """The archive's own outcome string, verbatim, so the mapping into
    :data:`ARCHIVE_OUTCOMES` stays checkable by someone who doubts it."""

    peak_snr_db: float | None = None
    frames_decoded: int | None = None

    def __post_init__(self) -> None:
        """Hold the record to what ``archive_observations`` would hold it to."""
        if not self.source_observation_id.strip():
            message = "a reception with no identifier cannot be deduplicated"
            raise NormalisationError(message)
        if not self.satellite_key.strip():
            message = f"{self.source_observation_id} names no object"
            raise NormalisationError(message)
        _one_of("archive_outcome", self.archive_outcome, ARCHIVE_OUTCOMES)
        _one_of("satellite_key_kind", self.satellite_key_kind, SATELLITE_KEY_KINDS)
        _aware("started_at", self.started_at)
        _aware("ended_at", self.ended_at)
        if self.ended_at is not None and self.ended_at < self.started_at:
            message = (
                f"{self.source_observation_id} ends at {self.ended_at}, before it "
                f"starts at {self.started_at}"
            )
            raise NormalisationError(message)

    @property
    def content_sha256(self) -> bytes:
        """The digest two normalisations of this reception are compared by.

        Stored beside the row, and the value
        ``insert_archive_observation`` refuses a disagreeing re-normalisation
        on: one key, one transformation version, two different bodies is a
        nondeterministic normaliser, which is a bug in the single property this
        stage exists to demonstrate.
        """
        return canonical_sha256(
            {
                "source_observation_id": self.source_observation_id,
                "source_station_key": self.source_station_key,
                "satellite_key": self.satellite_key,
                "satellite_key_kind": self.satellite_key_kind,
                "started_at": instant(self.started_at),
                "ended_at": None if self.ended_at is None else instant(self.ended_at),
                "max_elevation_deg": self.max_elevation_deg,
                "centre_freq_hz": self.centre_freq_hz,
                "mode": self.mode,
                "archive_outcome": self.archive_outcome,
                "source_outcome": self.source_outcome,
                "peak_snr_db": self.peak_snr_db,
                "frames_decoded": self.frames_decoded,
            }
        )


@dataclass(frozen=True, slots=True)
class NormalisedBatch:
    """Everything one artefact normalised to, under one normaliser version.

    Raises:
        NormalisationError: The version is blank, a key is described twice, or
            a reception names a station the same artefact does not describe.
    """

    transformation_version: str
    """The normaliser that produced this batch, part of the stored key.

    A module constant on the normaliser, not a timestamp or a git hash:
    re-normalising under a new version appends a second row beside the first so
    the two can be compared, which is impossible if the version drifts on every
    run (D-140).
    """

    stations: tuple[NormalisedStation, ...] = ()
    receptions: tuple[NormalisedReception, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Refuse a batch the load could only half apply."""
        if not self.transformation_version.strip():
            message = "a batch with no transformation version cannot be re-compared"
            raise NormalisationError(message)
        _no_duplicates(
            "station", [station.source_station_key for station in self.stations]
        )
        _no_duplicates(
            "reception",
            [reception.source_observation_id for reception in self.receptions],
        )
        described = {station.source_station_key for station in self.stations}
        dangling = sorted(
            {
                reception.source_station_key
                for reception in self.receptions
                if reception.source_station_key is not None
            }
            - described
        )
        if dangling:
            message = (
                f"receptions name stations this artefact does not describe: "
                f"{dangling}. Either the normaliser is wrong, or the adapter must "
                "emit a station record with the key — a link dropped here is a "
                "reception nothing can compute a denominator for"
            )
            raise NormalisationError(message)


def _one_of(name: str, value: str, allowed: tuple[str, ...]) -> None:
    """Hold a value to its vocabulary, naming the MSP confusion where it fits."""
    if value in allowed:
        return
    message = f"{name} {value!r} is not one of {', '.join(allowed)}"
    if value == "no_signal":
        message += (
            " — no_signal asserts that a station was verifiably listening, and we "
            "hold no heartbeat for somebody else's station (rule 7, D-139). The "
            "value you want is no_data"
        )
    raise NormalisationError(message)


def _aware(name: str, value: datetime | None) -> None:
    """Every stored instant is UTC, and a naive one would be read as though it is."""
    if value is not None and value.tzinfo is None:
        message = f"{name} is naive; every stored instant is UTC"
        raise NormalisationError(message)


def _in_range(name: str, value: float | None, low: float, high: float) -> None:
    """The same bounds the columns carry, hit here while the artefact is the unit."""
    if value is not None and not low <= value <= high:
        message = f"{name} is {value}, outside {low} to {high}"
        raise NormalisationError(message)


def _no_duplicates(what: str, keys: list[str]) -> None:
    """One key, one record: a batch is a single artefact's worth of rows.

    A key described twice within one artefact is a normaliser reading the same
    thing twice, and the load would silently keep whichever it saw last.
    """
    repeated = sorted(key for key, count in Counter(keys).items() if count > 1)
    if repeated:
        message = f"one artefact describes the same {what} more than once: {repeated}"
        raise NormalisationError(message)
