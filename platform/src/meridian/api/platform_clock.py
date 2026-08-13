"""The platform's clock, and the one form every MSP timestamp is written in.

Two functions the API layer shares: reading the platform's own time, and
rendering an instant the way MSP puts it on the wire. Both live here rather than
inside an endpoint module because more than one endpoint stamps a response with
them, and because a second rendering of the same instant is a second thing a
station's parser has to cope with.

Reading the clock is a named function rather than a call inline so a test can
substitute it and assert on an exact wire string, instead of on a value that
moves while it is being compared.

Reference: docs/MSP-SPEC.md §4.2, §8; docs/DECISIONS.md D-025.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["format_server_time", "utc_now"]


def utc_now() -> datetime:
    """The platform clock, as timezone-aware UTC."""
    return datetime.now(UTC)


def format_server_time(now: datetime) -> str:
    """Render an instant in the form MSP §8 shows on the wire.

    MSP §8's example is ``2026-08-14T09:31:02Z``. Python's ``isoformat`` writes
    the offset as ``+00:00``, which is the same instant and a different string —
    and a microcontroller client comparing suffixes, or one whose parser accepts
    only the ``Z`` form, sees a different thing. The specification shows one form,
    so the platform emits that one.

    Args:
        now: A timezone-aware UTC instant.

    Returns:
        ISO-8601 with millisecond precision and a ``Z`` suffix.

    Raises:
        ValueError: If ``now`` is naive. A naive datetime here would be rendered
            as though it were UTC and would silently publish a wrong clock to
            every station estimating its offset against it.
    """
    if now.tzinfo is None:
        raise ValueError("server_time must be timezone-aware UTC")
    return now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
