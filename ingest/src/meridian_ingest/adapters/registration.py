"""What the registry holds for one source, beyond its descriptor.

The descriptor says what a source *is* and what it may be used under (D-134).
This says how it is reached: its normaliser, a retriever when it is not plain
HTTP, its published rate limits, the variable its key is read from, and how
often ``meridian-ingest follow`` asks it again. Operational, and so kept off
the descriptor, whose fields are the ``ingest_sources`` columns and nothing else.

Reference: docs/DECISIONS.md D-134, D-223, D-225.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian_ingest.adapters.protocol import Adapter, Normaliser
from meridian_ingest.rate_ledger import RateWindow
from meridian_ingest.retrieval import Retriever

__all__ = ["Registration"]


@dataclass(frozen=True, slots=True)
class Registration:
    """One source's pair, and how it is to be reached."""

    adapter: Adapter
    normaliser: Normaliser
    retriever: Retriever | None = None
    """How its artefacts are obtained, when it is not plain HTTP.

    The reference archive serves the synthetic files shipped beside it, so it
    declares that here rather than leaving ``fetch`` to recognise an id and
    special-case it. A source that says nothing is fetched over HTTP, under the
    budget and backoff in :mod:`meridian_ingest.politeness`.
    """

    enabled_by_default: bool = False
    """Whether ``fetch`` with no settings file reaches it. Only the reference
    archive is: a real source is fetched from when an operator asks."""

    rate_limits: tuple[RateWindow, ...] = ()
    """The source's own published limits, honoured under a headroom (D-223).
    Empty where the source publishes none we could verify."""

    ledger: str | None = None
    """Whose allowance requests count against, where two sources share one —
    two endpoints of one service limited per client. Defaults to the source."""

    key_env: str | None = None
    """The variable a key is read from when the settings file names none."""

    cadence_s: int = 86_400
    """How long ``follow`` leaves between fetches of this source (D-225)."""

    lookback_s: int = 86_400
    """How far back ``follow`` asks, so a missed run leaves no gap."""

    @property
    def ledger_id(self) -> str:
        """The allowance this source's requests are counted in."""
        return self.ledger or self.adapter.descriptor.source_id
