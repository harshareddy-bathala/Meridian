"""``[verdict]`` — how the report fits and judges the reception verdict (SC-7).

The table holds :class:`~meridian.prediction.verdict_config.VerdictConfig`'s
settings, checked by it, and the section's own bootstrap ``resamples``:
- the split dates;
- ``inverse_regularisation``;
- ``partial_below``;
- ``rubric``.

The seed is not here: the fit's is derived from the run's master seed, as
every component's is (D-236). A ``seed`` in the table is refused rather than
ignored.

The table is optional. Without dates, the verdict cannot be fitted, and the
section says *not measured* with the reason, as SC-7 is until enough measured
receptions are rated (D-260).

Reference: docs/DECISIONS.md D-236, D-262, D-264.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from meridian.prediction.verdict_config import VerdictConfig, VerdictConfigError

__all__ = [
    "VerdictSectionConfig",
    "VerdictSectionConfigError",
    "verdict_section_config",
]

_RESAMPLES = (100, 100_000)


class VerdictSectionConfigError(ValueError):
    """A ``[verdict]`` table that cannot be obeyed."""


@dataclass(frozen=True, slots=True)
class VerdictSectionConfig:
    """The verdict's settings, and the section's bootstrap."""

    verdict: VerdictConfig = field(default_factory=VerdictConfig)
    """The fit's settings; its seed is replaced by the derived one."""

    resamples: int = 2000

    def parameters(self) -> dict[str, object]:
        """The values the section's numbers depend on, without the seed."""
        settings = {
            name: value
            for name, value in self.verdict.parameters().items()
            if name != "seed"
        }
        return {"verdict": settings, "resamples": self.resamples}


def verdict_section_config(table: object) -> VerdictSectionConfig:
    """Read ``[verdict]``.

    Raises:
        VerdictSectionConfigError: Not a table, a seed, an unknown setting, or
            a value its owner refuses.
    """
    if not isinstance(table, dict):
        message = f"[verdict] must be a table, not {table!r}"
        raise VerdictSectionConfigError(message)
    if "seed" in table:
        message = "[verdict] sets seed; the report derives every seed (D-236)"
        raise VerdictSectionConfigError(message)
    known = {one.name for one in fields(VerdictConfig)} - {"seed"}
    unknown = sorted(set(table) - known - {"resamples"})
    if unknown:
        message = f"[verdict]: unknown settings {unknown}"
        raise VerdictSectionConfigError(message)
    resamples = table.get("resamples", 2000)
    low, high = _RESAMPLES
    if isinstance(resamples, bool) or not isinstance(resamples, int):
        message = f"[verdict] resamples must be a whole number, not {resamples!r}"
        raise VerdictSectionConfigError(message)
    if not low <= resamples <= high:
        message = f"[verdict] resamples = {resamples} is outside {low}..{high}"
        raise VerdictSectionConfigError(message)
    try:
        verdict = VerdictConfig(
            **{name: value for name, value in table.items() if name in known}
        )
    except VerdictConfigError as exc:
        message = f"[verdict]: {exc}"
        raise VerdictSectionConfigError(message) from exc
    return VerdictSectionConfig(verdict=verdict, resamples=resamples)
