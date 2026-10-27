"""Watch every receive chain: train its baseline, judge it, raise or clear.

Stage 28's live half, run by ``meridian watch run`` and by the jobs service
each round. It trains the baselines that are due
(:mod:`~meridian.reliability.watch_build`), then judges each chain on its
latest heard receptions (:mod:`~meridian.reliability.watch_detect`):
- a chain with no open warning whose shortfall raises one gets a row;
- a chain with an open warning whose shortfall has recovered has it cleared.

Nothing else is written. A judgement that changes nothing writes nothing, so
the row count of ``receive_chain_warnings`` is the count of warnings (D-292).

**``raised_at`` is ``now``**, when the watch concluded: the instant the warning
could first be seen (D-292). The latest reception it read is in its evidence.

**The hash names the baseline's settings too.** A warning was judged against a
baseline trained under ``[baseline]``, so ``config_sha256`` hashes ``[watch]``
with the baseline's own hash.

Reference: docs/DECISIONS.md D-105, D-290 to D-294.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from meridian.orbit.service import OrbitService
from meridian.reliability import diagnosis
from meridian.reliability import watch_baseline as baselines
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.watch_baseline import EXCLUDED_CAUSES, reception_bins
from meridian.reliability.watch_build import (
    BaselineRunReport,
    Chain,
    Places,
    baseline_sha256,
    build_baselines,
    group_chains,
    readings_of,
)
from meridian.reliability.watch_detect import (
    METHOD,
    Judgement,
    ReceptionShortfall,
    compare,
    judge,
)
from meridian.store.receive_chain_warnings import (
    NewWarning,
    clear_warning,
    find_open_warnings,
    raise_warning,
)
from meridian.store.signal_baselines import StoredBaseline, find_current_baseline
from meridian.store.stations import Connection
from meridian.store.watch_reads import ChainReception, find_chain_receptions

__all__ = ["WatchRunReport", "watch_chains", "watch_sha256"]


@dataclass(frozen=True, slots=True)
class WatchRunReport:
    """What one run of the watch trained, judged, raised and cleared."""

    method: str
    config_sha256: bytes
    baselines: BaselineRunReport
    judged: int
    """Chains judged against a baseline."""

    no_baseline: int
    too_few: int
    """Chains with a baseline but too few recent receptions to judge."""

    raised: tuple[str, ...] = ()
    cleared: tuple[str, ...] = ()
    open: int = 0
    """Warnings open after the run, under this method and configuration."""

    unreadable: tuple[str, ...] = ()


def watch_sha256(config: ReliabilityConfig) -> bytes:
    """The hash stored with every warning: the watch's settings and the baseline's."""
    text = json.dumps(
        {
            "watch": config.watch.parameters(),
            "baseline": baseline_sha256(config).hex(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).digest()


def watch_chains(
    conn: Connection,
    orbit: OrbitService,
    *,
    config: ReliabilityConfig,
    now: datetime,
    station_id: str | None = None,
) -> WatchRunReport:
    """Train what is due, judge every chain, and raise or clear its warning.

    Args:
        conn: An open connection.
        orbit: Places samples in a station's sky.
        config: The deployment's reliability configuration.
        now: When the run happens: the end of the recent window, and the
            instant any warning is raised or cleared at.
        station_id: One station's chains, or every station's.

    Returns:
        What was done.
    """
    trained = build_baselines(
        conn, orbit, config=config, now=now, station_id=station_id
    )
    rows = find_chain_receptions(
        conn,
        between=(now - timedelta(seconds=config.watch.lookback_s), now),
        diagnosis_method=diagnosis.METHOD,
        diagnosis_sha256=config.diagnosis.sha256(),
        excluded_causes=EXCLUDED_CAUSES,
        station_id=station_id,
    )
    chains, _ambiguous = group_chains(rows)
    run = _Run(conn, orbit, config, watch_sha256(config), now)
    run.open_now = {
        (one.station_id, one.capability_id): one.id
        for one in find_open_warnings(
            conn,
            method=METHOD,
            config_sha256=run.config_sha256,
            station_id=station_id,
        )
    }
    for chain, receptions in sorted(chains.items()):
        run.watch(chain, receptions)
    return WatchRunReport(
        method=METHOD,
        config_sha256=run.config_sha256,
        baselines=trained,
        judged=run.judged,
        no_baseline=run.no_baseline,
        too_few=run.too_few,
        raised=tuple(run.raised),
        cleared=tuple(run.cleared),
        open=len(run.open_now),
        unreadable=tuple(run.unreadable),
    )


@dataclass
class _Run:
    """One run's collaborators, and what it has done so far."""

    conn: Connection
    orbit: OrbitService
    config: ReliabilityConfig
    config_sha256: bytes
    now: datetime
    open_now: dict[tuple[str, int], int] = field(default_factory=dict)
    """Each chain's open warning id."""

    places: Places = field(default_factory=Places)
    judged: int = 0
    no_baseline: int = 0
    too_few: int = 0
    raised: list[str] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def watch(self, chain: Chain, receptions: list[ChainReception]) -> None:
        """Judge one chain against its baseline; raise or clear its warning."""
        baseline = find_current_baseline(
            self.conn,
            station_id=chain[0],
            capability_id=chain[1],
            evidence=chain[2],
            method=baselines.METHOD,
            config_sha256=baseline_sha256(self.config),
        )
        if baseline is None:
            self.no_baseline += 1
            return
        recent = self._recent(receptions, chain[2], baseline)
        judgement = judge(recent, self.config.watch)
        if judgement is None:
            self.too_few += 1
            return
        self.judged += 1
        label = f"{chain[0]} capability {chain[1]}: {judgement.shortfall_db} dB short"
        warning = self.open_now.get((chain[0], chain[1]))
        if warning is None and judgement.raises:
            written = self._raise(baseline, judgement)
            if written is not None:
                self.raised.append(label)
                self.open_now[(chain[0], chain[1])] = written
        elif warning is not None and judgement.clears:
            if clear_warning(
                self.conn, warning, at=self.now, shortfall_db=judgement.shortfall_db
            ):
                self.cleared.append(label)
                del self.open_now[(chain[0], chain[1])]

    def _recent(
        self,
        receptions: list[ChainReception],
        evidence: str,
        baseline: StoredBaseline,
    ) -> list[ReceptionShortfall]:
        """The chain's latest judgeable receptions, oldest first.

        Read from the newest back, and only as far as ``recent_receptions``
        that crossed enough of the baseline's bins.
        """
        medians = {
            float(str(one["elevation_deg"])): float(str(one["median_db"]))
            for one in baseline.bins_json
        }
        found: list[ReceptionShortfall] = []
        for row in reversed(receptions):
            if len(found) == self.config.watch.recent_receptions:
                break
            try:
                readings = readings_of(
                    self.conn, self.orbit, row, evidence, self.places
                )
            except LookupError as exc:
                self.unreadable.append(f"{row.assignment_id}: {exc}")
                continue
            shortfall = compare(
                reception_bins(readings, self.config.baseline),
                medians,
                self.config.watch,
                assignment_id=row.assignment_id,
                started_at=row.started_at,
                end_at=row.end_at,
            )
            if shortfall is not None:
                found.append(shortfall)
        return found[::-1]

    def _raise(self, baseline: StoredBaseline, judgement: Judgement) -> int | None:
        evidence: dict[str, object] = {
            "baseline": {
                "id": baseline.id,
                "evidence": baseline.evidence,
                "method": baseline.method,
                "trained_until": baseline.trained_until.isoformat(),
            },
            "receptions": [
                {
                    "assignment_id": one.assignment_id,
                    "started_at": one.started_at.isoformat(),
                    "shortfall_db": one.shortfall_db,
                }
                for one in judgement.receptions
            ],
            "through": max(one.end_at for one in judgement.receptions).isoformat(),
            "parameters": self.config.watch.parameters(),
        }
        return raise_warning(
            self.conn,
            NewWarning(
                baseline_id=baseline.id,
                raised_at=self.now,
                shortfall_db=judgement.shortfall_db,
                affected_bins=judgement.affected,
                evidence=evidence,
                method=METHOD,
                config_sha256=self.config_sha256,
            ),
        )
