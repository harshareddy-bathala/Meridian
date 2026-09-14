"""Who may scrape a metrics endpoint, and what a refusal looks like.

The endpoint publishes process internals — resident memory, file descriptors,
garbage-collection counts — and Stage 11 puts the platform behind a tunnel on a
public hostname, so it travels to the internet along with everything else. This
module decides whether one request may read it.

A refused request is answered **exactly as an unmatched path is** (D-087): a 404
carrying the same body ``meridian.api.errors`` produces for any URL that matches
no route. A 401 would confirm the endpoint exists and invite a second attempt;
answering as though nothing is there ends the conversation. The reason is written
to the platform's log instead, where an operator can diagnose a misconfigured
scrape and a stranger cannot.

It performs no I/O, opens no database and reads no environment — the token is
handed in by the caller from ``Settings``, so the comparison is testable as a
pure function.

It lives under ``meridian.metrics`` rather than ``meridian.api`` because two
processes serve metrics: the API, and the scheduled jobs (D-109). Both check the
same token with this one function.

Reference: docs/DECISIONS.md D-087, D-090, D-109.
"""

from __future__ import annotations

import logging
import secrets

__all__ = ["BEARER_PREFIX", "is_metrics_scrape_authorised"]

_log = logging.getLogger(__name__)

BEARER_PREFIX = "Bearer "
"""RFC 6750's scheme, matched case-sensitively as the specification writes it.

Prometheus sends exactly this. Accepting other spellings would widen the surface
for no station's benefit — nothing but a scrape ever calls this endpoint.
"""


def is_metrics_scrape_authorised(header: str | None, expected_token: str) -> bool:
    """Whether one request may read ``/metrics``.

    Args:
        header: The request's ``Authorization`` header as sent, or ``None`` when
            the request carried none.
        expected_token: ``Settings.metrics_token`` — the value Prometheus is
            configured with.

    Returns:
        True only when the header carries a bearer token equal to
        ``expected_token``.

    Note:
        Compared with :func:`secrets.compare_digest`, which takes the same time
        whether the first character differs or the last. ``==`` returns as soon
        as two bytes disagree, and a caller who can measure that can recover the
        token one character at a time.

        Every rejection is logged at warning with its reason, because the likely
        cause is a scrape configured wrongly rather than an intruder, and that is
        otherwise invisible: the response says only that nothing is there.
    """
    if header is None:
        _log.warning("refused a /metrics scrape: no Authorization header")
        return False

    if not header.startswith(BEARER_PREFIX):
        _log.warning("refused a /metrics scrape: Authorization is not a bearer token")
        return False

    presented = header.removeprefix(BEARER_PREFIX)
    if not secrets.compare_digest(presented, expected_token):
        _log.warning("refused a /metrics scrape: the bearer token did not match")
        return False

    return True
