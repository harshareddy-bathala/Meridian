"""The irrecoverable-loss budget: every lost pass, debited with its reason.

A missed satellite pass cannot be retried, unlike a failed request, so the
budget is spent by passes, not by time. SC-4 sets it: at a capture target of
90%, one pass in ten of those a station could have captured may be lost inside
the window before the target is broken (D-185).

**Every lost pass is a debit, and only one kind of debit is a miss.** A pass is
debited when it counted towards the capture rate and was not captured, and the
debit carries the pass's class as its reason: ``confirmed_miss``,
``signal_no_decode``, ``station_unavailable``,
``station_not_confirmed_listening`` or ``assignment_declined``. A station that
was off spent its budget as surely as one that listened and heard nothing, but
it did not miss the pass, and no report calls it a miss (CLAUDE.md rule 7).
Passes judged by the satellite are neither captured nor lost.

Standard library only, as the classification is (D-180).

Reference: docs/DECISIONS.md D-180, D-184, D-185.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from meridian.reliability.classification import (
    CAPTURED,
    PASS_CLASSES,
    SATELLITE_CLASSES,
)
from meridian.reliability.slis import PassRecord, eligible

__all__ = ["DEBIT_REASONS", "Debit", "LossBudget", "loss_budget"]

DEBIT_REASONS: tuple[str, ...] = tuple(
    one for one in PASS_CLASSES if one not in CAPTURED | SATELLITE_CLASSES
)
"""Every class that spends the budget, in the classification's order."""


@dataclass(frozen=True, slots=True)
class Debit:
    """One lost pass."""

    reference: str
    station_id: str
    window_end: datetime
    reason: str
    simulated: bool
    impact_passes: int = 1
    """A debit spends one pass of the budget; ``LossBudget.allowed`` says how
    many passes that budget holds."""


@dataclass(frozen=True, slots=True)
class LossBudget:
    """One population's budget over one window."""

    capture_target: float
    eligible: int
    """Passes the station could have captured, which the budget is a share of."""

    allowed: float
    """Passes that may be lost with the target still met:
    ``(1 − target) × eligible``."""

    debits: tuple[Debit, ...]

    @property
    def spent(self) -> int:
        """Passes lost."""
        return sum(one.impact_passes for one in self.debits)

    @property
    def remaining(self) -> float:
        """Passes that may still be lost; negative once the target is broken."""
        return self.allowed - self.spent

    @property
    def remaining_ratio(self) -> float | None:
        """What share of the budget is left; None where nothing was eligible."""
        return self.remaining / self.allowed if self.allowed > 0 else None

    @property
    def exhausted(self) -> bool:
        """Whether more has been lost than the target allows."""
        return self.eligible > 0 and self.spent > self.allowed

    def by_reason(self) -> dict[str, int]:
        """Debits per reason, every reason present."""
        counts = dict.fromkeys(DEBIT_REASONS, 0)
        for one in self.debits:
            counts[one.reason] += 1
        return counts


def loss_budget(passes: Iterable[PassRecord], *, capture_target: float) -> LossBudget:
    """The budget spent by one population's passes.

    Args:
        passes: Classified passes of one population inside the window.
        capture_target: The capture-rate target, SC-4's 0.90 by default.

    Returns:
        The budget, with a debit for every eligible pass not captured, in
        window order.
    """
    held = eligible(passes)
    debits = tuple(
        Debit(
            reference=one.reference,
            station_id=one.station_id,
            window_end=one.window_end,
            reason=one.classification,
            simulated=one.simulated,
        )
        for one in sorted(held, key=lambda one: (one.window_end, one.reference))
        if one.classification not in CAPTURED
    )
    return LossBudget(
        capture_target=capture_target,
        eligible=len(held),
        allowed=(1 - capture_target) * len(held),
        debits=debits,
    )
