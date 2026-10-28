"""A station's receive chains, as the public API states them (Stage 28, D-295).

Turns ``store.watch_reads.ChainWatch`` into a response body: each live
capability of the station, its current baseline, and its latest receive-chain
warnings, open or cleared.

**A chain is named by its band and frequency range**, as ``capabilities``
already publishes them, not by the capability's database id, which the public
API has never published.

**What is coarsened.** Every decibel figure to a tenth, as interference is
published. A baseline's bins say how a station's chain hears the sky by
elevation, which is no more than its receptions already publish through each
reception's peak; ``docs/THREAT-MODEL.md`` §7 reviews it.

**Provenance.** The station's ``simulated`` flag, and each baseline's and
warning's method, so a simulated warning can never be read as a measured one.

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel

from meridian.store.receive_chain_warnings import StoredWarning
from meridian.store.signal_baselines import StoredBaseline
from meridian.store.watch_reads import ChainWatch

__all__ = [
    "PublicAffectedBin",
    "PublicBaselineBin",
    "PublicChainBaseline",
    "PublicChainWarning",
    "PublicReceiveChain",
    "PublicStationReceiveChains",
]


class PublicBaselineBin(BaseModel):
    """The chain's usual signal from ``elevation_deg`` up one bin's width."""

    elevation_deg: float
    median_db: float
    p10_db: float
    receptions: int

    @classmethod
    def from_json(cls, held: dict[str, object]) -> Self:
        """Publish one stored bin."""
        return cls(
            elevation_deg=float(str(held["elevation_deg"])),
            median_db=round(float(str(held["median_db"])), 1),
            p10_db=round(float(str(held["p10_db"])), 1),
            receptions=int(str(held["receptions"])),
        )


class PublicChainBaseline(BaseModel):
    """What the chain usually hears at each elevation, and from when."""

    evidence: str
    """``samples`` (MSP 0.3's series) or ``peak`` (MSP 0.2's peak)."""

    method: str
    trained_from: datetime
    trained_until: datetime
    bin_width_deg: float
    bins: list[PublicBaselineBin]

    @classmethod
    def from_row(cls, row: StoredBaseline) -> Self:
        """Publish one baseline."""
        return cls(
            evidence=row.evidence,
            method=row.method,
            trained_from=row.trained_from,
            trained_until=row.trained_until,
            bin_width_deg=row.bin_width_deg,
            bins=[PublicBaselineBin.from_json(one) for one in row.bins_json],
        )


class PublicAffectedBin(BaseModel):
    """One elevation the chain fell short at when the warning was raised."""

    elevation_deg: float
    shortfall_db: float
    receptions: int

    @classmethod
    def from_json(cls, held: dict[str, object]) -> Self:
        """Publish one affected bin."""
        return cls(
            elevation_deg=float(str(held["elevation_deg"])),
            shortfall_db=round(float(str(held["shortfall_db"])), 1),
            receptions=int(str(held["receptions"])),
        )


class PublicChainWarning(BaseModel):
    """A receive-chain warning: raised once, cleared once if it recovered."""

    method: str
    raised_at: datetime
    shortfall_db: float
    affected_bins: list[PublicAffectedBin]
    cleared_at: datetime | None
    """Null while the warning is open."""
    cleared_shortfall_db: float | None

    @classmethod
    def from_row(cls, row: StoredWarning) -> Self:
        """Publish one warning."""
        return cls(
            method=row.method,
            raised_at=row.raised_at,
            shortfall_db=round(row.shortfall_db, 1),
            affected_bins=[
                PublicAffectedBin.from_json(one) for one in row.affected_bins_json
            ],
            cleared_at=row.cleared_at,
            cleared_shortfall_db=None
            if row.cleared_shortfall_db is None
            else round(row.cleared_shortfall_db, 1),
        )


class PublicReceiveChain(BaseModel):
    """One receive chain: its baseline, and its latest warnings."""

    band: str
    freq_min_hz: int
    freq_max_hz: int
    baseline: PublicChainBaseline | None
    """Null until the chain has heard enough to be watched."""
    warnings: list[PublicChainWarning]
    """Newest first, at most five; empty when it has never had one."""

    @classmethod
    def from_row(cls, row: ChainWatch) -> Self:
        """Publish one chain."""
        return cls(
            band=row.band,
            freq_min_hz=row.freq_min_hz,
            freq_max_hz=row.freq_max_hz,
            baseline=None
            if row.baseline is None
            else PublicChainBaseline.from_row(row.baseline),
            warnings=[PublicChainWarning.from_row(one) for one in row.warnings],
        )


class PublicStationReceiveChains(BaseModel):
    """Every live receive chain of one station."""

    station_id: str
    simulated: bool
    chains: list[PublicReceiveChain]

    @classmethod
    def from_rows(
        cls, station_id: str, simulated: bool, rows: list[ChainWatch]
    ) -> Self:
        """Publish a station's chains."""
        return cls(
            station_id=station_id,
            simulated=simulated,
            chains=[PublicReceiveChain.from_row(one) for one in rows],
        )
