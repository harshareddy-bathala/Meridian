"""Parsing and checking the ``MSP-Version`` header.

MSP §7 requires the header on every request and settles exactly one rule about it,
which `docs/DECISIONS.md` D-016 records: **the major must be supported; an
unrecognised minor within a supported major is accepted.** Minor versions are
additive by definition, so a station built against 0.1 must keep working when the
platform speaks 0.2 — and a platform that rejected 0.2 from a newer station would
make every future addition a breaking change.

This module sits under `meridian.api` and is depended on by every ``/msp/v0``
route through :func:`require_msp_version`. It performs **no I/O**, reads no
configuration and is pure parsing.

Reference: docs/MSP-SPEC.md §7, docs/DECISIONS.md D-016.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header

from meridian.api.errors import UNSUPPORTED_VERSION, MspError

__all__ = [
    "HEADER_NAME",
    "SUPPORTED_MAJORS",
    "ProtocolVersion",
    "parse_msp_version",
    "require_msp_version",
]

HEADER_NAME = "MSP-Version"

SUPPORTED_MAJORS = frozenset({0})
"""Major versions this platform serves.

MSP §7 promises "the current major version and one previous". There is only one
major so far, so the set has one member; when 1.0 lands this becomes ``{0, 1}``
and the promise is kept by editing one line rather than by finding every place a
version was compared.
"""


@dataclass(frozen=True, slots=True)
class ProtocolVersion:
    """A parsed ``major.minor`` protocol version."""

    major: int
    minor: int

    def __str__(self) -> str:
        """Render as it appears on the wire."""
        return f"{self.major}.{self.minor}"


def parse_msp_version(raw: str | None) -> ProtocolVersion:
    """Parse and validate an ``MSP-Version`` header value.

    Args:
        raw: The header value, or ``None`` when the header was absent.

    Returns:
        The parsed version, when its major is supported.

    Raises:
        MspError: ``unsupported_version`` when the header is missing, is not
            ``major.minor``, has non-numeric parts, or names an unsupported
            major.

    Note:
        **A missing header is ``unsupported_version``, not ``malformed``.** MSP §7
        says so explicitly, and the distinction is deliberate: sending the header
        is one line of client code, and requiring it is what makes deprecation
        possible later. `malformed` is about the body.
    """
    if raw is None or not raw.strip():
        raise MspError(
            UNSUPPORTED_VERSION,
            f"{HEADER_NAME} header is required on every MSP request.",
        )

    version = _split_version(raw.strip())
    if version.major not in SUPPORTED_MAJORS:
        supported = ", ".join(str(m) for m in sorted(SUPPORTED_MAJORS))
        raise MspError(
            UNSUPPORTED_VERSION,
            f"Major version {version.major} is not supported; this platform "
            f"serves major {supported}.",
        )

    # The minor is deliberately not checked. An unrecognised minor inside a
    # supported major is accepted, because minor versions are additive (§7) — a
    # station on 0.7 sends fields we ignore, not fields we cannot parse.
    return version


def _split_version(raw: str) -> ProtocolVersion:
    """Split ``major.minor`` into integers, or raise ``unsupported_version``."""
    major_text, separator, minor_text = raw.partition(".")
    if not separator:
        raise MspError(
            UNSUPPORTED_VERSION,
            f"{HEADER_NAME} must be major.minor, got {raw!r}.",
        )
    try:
        return ProtocolVersion(major=int(major_text), minor=int(minor_text))
    except ValueError as exc:
        raise MspError(
            UNSUPPORTED_VERSION,
            f"{HEADER_NAME} must be major.minor with numeric parts, got {raw!r}.",
        ) from exc


def require_msp_version(
    msp_version: str | None = Header(default=None, alias=HEADER_NAME),
) -> ProtocolVersion:
    """FastAPI dependency enforcing MSP §7 on a route.

    Attached to the ``/msp/v0`` router rather than to each route, so an endpoint
    added later cannot forget it — which is the guarantee Stage 3 of the roadmap
    asks the shared layer to provide.

    Args:
        msp_version: The ``MSP-Version`` header, injected by FastAPI.

    Returns:
        The parsed version, for a route that wants to vary on the minor.

    Raises:
        MspError: ``unsupported_version``, handled into MSP §6's body by
            ``meridian.api.errors``.
    """
    return parse_msp_version(msp_version)
