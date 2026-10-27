"""Train each receive chain's baseline from its own heard receptions.

The first half of Stage 28's watch, run by ``meridian watch baseline`` and by
the jobs service. For every chain that heard something in the training
interval, it decides whether the chain needs a baseline, places each reception's
samples in the station's sky, and writes the bins :func:`~meridian.reliability.
watch_baseline.train` gives (D-293).

**When a chain is trained** (D-293):
- it has no baseline under this method and configuration;
- or its latest baseline's interval ended at least ``retrain_s`` before this
  one ends, **and no warning is open on the chain**. A shortfall the watch has
  seen is never learned as the chain's normal signal.

**The interval ends at the start of today, UTC**, and reaches back
``train_s``. So every run within one day asks for the same interval, and the
store writes it once (D-291).

**Two kinds of evidence, never mixed** (D-291). A reception with MSP 0.3's
samples is placed sample by sample, by the pass's own element set at the
station's registered site, through the same ``place`` loss diagnosis uses. One
with only MSP 0.2's peak counts once, at the pass's maximum elevation.

**One unreadable reception does not stop the rest.** Its element set gone, it
is named in the report and left out; the chain is trained from the others.

**The hash names the diagnosis it read.** A baseline leaves out the receptions
Stage 27 put down to another cause, so the diagnosis method and configuration
are part of what made it, and part of ``config_sha256``.

Reference: docs/DECISIONS.md D-158, D-272, D-290, D-291, D-293.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from meridian.orbit.service import OrbitService
from meridian.orbit.types import ElementSet, GroundSite
from meridian.reliability import diagnosis
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.diagnosis_gather import place
from meridian.reliability.watch_baseline import (
    EXCLUDED_CAUSES,
    METHOD,
    Reading,
    reception_bins,
    train,
)
from meridian.store.diagnosis_reads import find_site
from meridian.store.element_sets import find_element_set_by_id
from meridian.store.receive_chain_warnings import has_open_warning
from meridian.store.signal_baselines import (
    NewBaseline,
    find_current_baseline,
    insert_baseline,
)
from meridian.store.stations import Connection
from meridian.store.watch_reads import ChainReception, find_chain_receptions

__all__ = [
    "BaselineRunReport",
    "baseline_sha256",
    "build_baselines",
    "training_interval",
]

_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class BaselineRunReport:
    """What one run trained."""

    method: str
    config_sha256: bytes
    trained_from: datetime
    trained_until: datetime
    chains: int
    """Chains, by evidence, that heard something in the interval."""

    written: int
    kept: int
    """Chains whose baseline is recent enough."""

    held: int
    """Chains not trained again because a warning is open on them."""

    too_little: tuple[str, ...] = ()
    """Chains, each named, whose history gave too few bins for a baseline."""

    ambiguous: int = 0
    """Receptions left out because no capability, or more than one, covers
    their frequency (D-290)."""

    unreadable: tuple[str, ...] = ()
    """Receptions that could not be placed, each with why."""

    simulated: int = 0
    """How many of those written were simulated stations' baselines."""


def baseline_sha256(config: ReliabilityConfig) -> bytes:
    """The hash stored with every baseline: its settings and the diagnosis read."""
    text = json.dumps(
        {
            "baseline": config.baseline.parameters(),
            "diagnosis": {
                "method": diagnosis.METHOD,
                "config_sha256": config.diagnosis.sha256().hex(),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).digest()


def training_interval(
    now: datetime, config: ReliabilityConfig
) -> tuple[datetime, datetime]:
    """The interval a run at ``now`` trains over: ``train_s`` up to today's start."""
    at = now.astimezone(UTC)
    until = datetime(at.year, at.month, at.day, tzinfo=UTC)
    return until - timedelta(seconds=config.baseline.train_s), until


@dataclass
class _Places:
    """What one run has read once: sites and element sets."""

    sites: dict[str, GroundSite | None] = field(default_factory=dict)
    element_sets: dict[int, ElementSet] = field(default_factory=dict)


def build_baselines(
    conn: Connection,
    orbit: OrbitService,
    *,
    config: ReliabilityConfig,
    now: datetime,
    station_id: str | None = None,
) -> BaselineRunReport:
    """Train every chain that needs a baseline, from its interval's receptions.

    Args:
        conn: An open connection.
        orbit: Places samples in a station's sky.
        config: The deployment's reliability configuration: its ``[baseline]``
            table, and the diagnosis whose causes are left out.
        now: When the run happens; the interval ends at the start of its day.
        station_id: One station's chains, or every station's.

    Returns:
        What was trained and stored.
    """
    since, until = training_interval(now, config)
    config_sha256 = baseline_sha256(config)
    rows = find_chain_receptions(
        conn,
        between=(since, until),
        diagnosis_method=diagnosis.METHOD,
        diagnosis_sha256=config.diagnosis.sha256(),
        excluded_causes=EXCLUDED_CAUSES,
        station_id=station_id,
    )
    chains, ambiguous = _group(rows)
    places = _Places()
    written = kept = held = simulated = 0
    too_little: list[str] = []
    unreadable: list[str] = []
    for chain, receptions in sorted(chains.items()):
        decision = _decide(conn, chain, config, config_sha256, until)
        kept += decision == "kept"
        held += decision == "held"
        if decision != "train":
            continue
        votes, failed = _votes(
            conn, orbit, receptions, evidence=chain[2], config=config, places=places
        )
        unreadable.extend(failed)
        bins = train(votes, config.baseline)
        if not bins:
            too_little.append(f"{chain[0]} capability {chain[1]} ({chain[2]})")
            continue
        insert_baseline(
            conn,
            NewBaseline(
                station_id=chain[0],
                capability_id=chain[1],
                evidence=chain[2],
                bin_width_deg=config.baseline.bin_width_deg,
                bins=[one.as_json() for one in bins],
                receptions=len(votes),
                trained_from=since,
                trained_until=until,
                method=METHOD,
                config_sha256=config_sha256,
            ),
        )
        written += 1
        simulated += receptions[0].simulated
    return BaselineRunReport(
        method=METHOD,
        config_sha256=config_sha256,
        trained_from=since,
        trained_until=until,
        chains=len(chains),
        written=written,
        kept=kept,
        held=held,
        too_little=tuple(too_little),
        ambiguous=ambiguous,
        unreadable=tuple(unreadable),
        simulated=simulated,
    )


_Chain = tuple[str, int, str]
"""A station, a capability and a kind of evidence."""


def _group(
    rows: list[ChainReception],
) -> tuple[dict[_Chain, list[ChainReception]], int]:
    """Receptions by chain, and how many belonged to no single chain."""
    chains: dict[_Chain, list[ChainReception]] = defaultdict(list)
    ambiguous = 0
    for row in rows:
        evidence = _evidence(row)
        if evidence is None:
            continue
        if row.capability_id is None or row.chains != 1:
            ambiguous += 1
            continue
        chains[(row.station_id, row.capability_id, evidence)].append(row)
    return chains, ambiguous


def _votes(  # noqa: PLR0913 — one chain's receptions and what places them
    conn: Connection,
    orbit: OrbitService,
    receptions: list[ChainReception],
    *,
    evidence: str,
    config: ReliabilityConfig,
    places: _Places,
) -> tuple[list[dict[float, tuple[float, int]]], list[str]]:
    """Each reception's bins, and the receptions that could not be placed."""
    votes = []
    failed = []
    for one in receptions:
        try:
            readings = _readings(conn, orbit, one, evidence, places)
        except LookupError as exc:
            failed.append(f"{one.assignment_id}: {exc}")
            continue
        votes.append(reception_bins(readings, config.baseline))
    return votes, failed


def _evidence(row: ChainReception) -> str | None:
    """``samples`` for MSP 0.3's series, ``peak`` for 0.2's peak, else nothing."""
    if row.snr_samples:
        return "samples"
    if row.peak_snr_db is not None:
        return "peak"
    return None


def _decide(
    conn: Connection,
    chain: _Chain,
    config: ReliabilityConfig,
    config_sha256: bytes,
    until: datetime,
) -> str:
    """``train``, ``kept`` (recent enough) or ``held`` (a warning is open)."""
    station, capability, evidence = chain
    current = find_current_baseline(
        conn,
        station_id=station,
        capability_id=capability,
        evidence=evidence,
        method=METHOD,
        config_sha256=config_sha256,
    )
    if current is None:
        return "train"
    due = current.trained_until + timedelta(seconds=config.baseline.retrain_s)
    if until < due:
        return "kept"
    if has_open_warning(conn, station_id=station, capability_id=capability):
        return "held"
    return "train"


def _readings(
    conn: Connection,
    orbit: OrbitService,
    row: ChainReception,
    evidence: str,
    places: _Places,
) -> list[Reading]:
    """One reception's signal, each reading at its elevation."""
    if evidence == "peak":
        peak = row.peak_snr_db
        return [] if peak is None else [Reading(row.max_elevation_deg, peak)]
    site = _site(conn, row.station_id, places)
    if site is None:
        raise LookupError(f"station {row.station_id} has no site")
    placed = place(
        orbit,
        _element_set(conn, row.element_set_id, places),
        site,
        row.snr_samples or [],
    )
    return [Reading(one.elevation_deg, one.snr_db) for one in placed]


def _site(conn: Connection, station_id: str, places: _Places) -> GroundSite | None:
    if station_id not in places.sites:
        found = find_site(conn, station_id)
        places.sites[station_id] = (
            None
            if found is None
            else GroundSite(found.lat_deg, found.lon_deg, found.alt_m)
        )
    return places.sites[station_id]


def _element_set(conn: Connection, element_set_id: int, places: _Places) -> ElementSet:
    if element_set_id not in places.element_sets:
        stored = find_element_set_by_id(conn, element_set_id)
        if stored is None:
            raise LookupError(f"element set {element_set_id} is gone")
        places.element_sets[element_set_id] = ElementSet(
            satellite_id=stored.satellite_id,
            epoch=stored.epoch,
            line1=stored.line1,
            line2=stored.line2,
            source=stored.source,
        )
    return places.element_sets[element_set_id]
