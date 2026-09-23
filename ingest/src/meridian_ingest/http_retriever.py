"""The one place in this distribution that opens a socket.

A :class:`~meridian_ingest.retrieval.Retriever` over ``httpx``, obeying
:mod:`meridian_ingest.politeness` and deciding nothing itself. It is reached
only by ``meridian-ingest fetch``; no test constructs one against a real host
(D-142), and the reference adapter never needs one at all.

**The body is read to completion here, before anything downstream sees it.** A
stream that fails halfway is worth retrying, and it can only be retried while
nobody else has been handed the first half of it — hand the raw store a
truncated artefact and the failure has already been recorded as a fact. The
bytes spool to memory up to :data:`SPOOL_MAX` and to a temporary file beyond
it, so a large snapshot is still bounded by disk rather than by RAM.

**A retriever owns its spools** and closes them when it does, which is why it
is a context manager. A fetch that abandons a response halfway should not leave
a partial download behind for the same reason the raw store sweeps its scratch
directories.

Reference: docs/DECISIONS.md D-134, D-141, D-142.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile
from types import TracebackType
from typing import IO

import httpx

from meridian_ingest import __version__
from meridian_ingest.politeness import (
    RequestBudget,
    RetryPolicy,
    is_retryable,
    parse_retry_after,
)
from meridian_ingest.retrieval import RemoteArtefact, RetrievalError, RetrievedArtefact

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "SPOOL_MAX",
    "USER_AGENT",
    "HttpRetriever",
    "open_client",
]

USER_AGENT = (
    f"meridian-ingest/{__version__} (+https://github.com/harshareddy-bathala/Meridian)"
)
"""Who we are and where to complain.

An archive operator seeing unexpected traffic should be able to find out whose
it is in one search. A default user agent, or a browser's, makes us
indistinguishable from a scraper — and being distinguishable is the cheapest
half of being polite.
"""

DEFAULT_TIMEOUT_S = 30.0
SPOOL_MAX = 8 * 1024 * 1024
"""Bytes held in memory before a download spills to a temporary file."""

_READ_BLOCK = 1 << 16


def open_client(
    user_agent: str = USER_AGENT, timeout_s: float = DEFAULT_TIMEOUT_S
) -> httpx.Client:
    """An HTTP client configured the way a polite one is.

    Args:
        user_agent: Overridable only so a deployment can add its own contact.
        timeout_s: Applied to connect, read and write alike.

    Returns:
        A client the caller closes, usually by handing it to
        :class:`HttpRetriever` in a ``with`` block.

    Note:
        Redirects are **not** followed. A redirect changes the URL an artefact
        was fetched from, and that URL is what an operator reads back out of
        the manifest to check where a record came from; silently following one
        makes the recorded provenance a place the bytes did not come from.
    """
    return httpx.Client(
        headers={"User-Agent": user_agent},
        timeout=timeout_s,
        follow_redirects=False,
    )


class _Again(Exception):  # noqa: N818 — a control-flow signal, not a reportable error
    """One attempt failed in a way that another attempt might not.

    Private, and never escapes :meth:`HttpRetriever.retrieve`: what a caller
    sees is either the artefact or a :class:`RetrievalError` naming every
    attempt.
    """

    def __init__(self, reason: str, retry_after_s: float | None = None) -> None:
        """Carry why, and whatever the server said about when."""
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = retry_after_s


class HttpRetriever:
    """Fetches artefacts over HTTP, within a budget and a backoff policy.

    Args:
        client: An open ``httpx.Client``, usually from :func:`open_client`.
            Injected rather than built, so a test drives the whole retry path
            through ``httpx.MockTransport`` without a socket.
        budget: The ceiling on requests. Shared across every artefact in one
            fetch, because the archive counts them all together.
        policy: How long to wait between attempts.
    """

    def __init__(
        self,
        client: httpx.Client,
        budget: RequestBudget,
        policy: RetryPolicy | None = None,
    ) -> None:
        """Hold the client and the rules. Nothing is fetched yet."""
        self._client = client
        self._budget = budget
        self._policy = policy or RetryPolicy()
        self._spools: list[IO[bytes]] = []

    def __enter__(self) -> HttpRetriever:
        """Return self, so a fetch can own its downloads in a ``with`` block."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close every spooled download, and the client with them."""
        self.close()

    def close(self) -> None:
        """Discard every buffered body and close the client."""
        for spool in self._spools:
            spool.close()
        self._spools.clear()
        self._client.close()

    def retrieve(self, remote: RemoteArtefact) -> RetrievedArtefact:
        """Fetch one artefact, retrying what is worth retrying.

        Args:
            remote: What to ask for.

        Returns:
            The response, its body already read and its headers intact.

        Raises:
            BudgetExhaustedError: The fetch has spent its requests, or the
                archive asked for a longer wait than this fetch will give it.
                Raised *before* the request, so the ceiling is never exceeded.
            RetrievalError: The artefact could not be fetched, or was refused
                permanently. The message lists what each attempt met, because
                "four attempts failed" without saying how is the report that
                makes an operator run it again to find out.
        """
        met: list[str] = []
        for attempt in range(1, self._policy.attempts + 1):
            self._budget.spend(remote.original_identifier)
            try:
                return self._once(remote)
            except _Again as again:
                met.append(f"attempt {attempt}: {again.reason}")
                # Only wait for a retry this fetch can still afford. Sleeping
                # and *then* refusing on the budget would hold the process open
                # for a request that was never going to be made.
                if attempt < self._policy.attempts and self._budget.remaining > 0:
                    self._wait(attempt, again, remote)
        joined = "; ".join(met)
        message = f"{remote.url} could not be fetched — {joined}"
        raise RetrievalError(message)

    def _once(self, remote: RemoteArtefact) -> RetrievedArtefact:
        """One attempt: ask, judge the status, and read the body in full."""
        try:
            with self._client.stream(
                "GET", remote.url, headers=dict(remote.headers)
            ) as response:
                self._judge(response)
                return self._read(remote, response)
        except httpx.RequestError as exc:
            reason = f"{type(exc).__name__}: {exc}"
            raise _Again(reason) from exc

    def _judge(self, response: httpx.Response) -> None:
        """Turn a status into success, a retry, or a permanent refusal."""
        if response.status_code == httpx.codes.OK:
            return
        response.read()
        if is_retryable(response.status_code):
            after = parse_retry_after(
                response.headers.get("Retry-After"), datetime.now(tz=UTC)
            )
            raise _Again(f"HTTP {response.status_code}", after)
        message = (
            f"{response.request.url} returned HTTP {response.status_code}, which "
            "asking again would return as well"
        )
        raise RetrievalError(message)

    def _read(
        self, remote: RemoteArtefact, response: httpx.Response
    ) -> RetrievedArtefact:
        """Spool the body, and refuse one that stopped short of its own length."""
        # SIM115 is suppressed because the spool outlives this call — it *is*
        # body the caller streams from — so the retriever owns it and closes
        # it, rather than a `with` block closing it before anyone reads it.
        spool: IO[bytes] = SpooledTemporaryFile(max_size=SPOOL_MAX)  # noqa: SIM115
        self._spools.append(spool)
        written = 0
        for block in response.iter_bytes():
            written += len(block)
            spool.write(block)
        declared = response.headers.get("Content-Length")
        if declared is not None and declared.isdigit() and int(declared) != written:
            reason = f"{written} bytes arrived of the {declared} the archive declared"
            raise _Again(reason)
        spool.seek(0)
        return RetrievedArtefact(
            remote=remote,
            chunks=_blocks(spool),
            media_type=_media_type(response),
            headers=dict(response.headers),
        )

    def _wait(self, attempt: int, again: _Again, remote: RemoteArtefact) -> None:
        """Sleep for as long as the archive asked, or as long as the policy says."""
        if again.retry_after_s is None:
            delay = self._policy.delay_before(attempt)
        else:
            delay = self._policy.honour(again.retry_after_s, remote.original_identifier)
        _sleep(delay)


def _blocks(spool: IO[bytes]) -> Iterator[bytes]:
    """The buffered body, a block at a time."""
    while block := spool.read(_READ_BLOCK):
        yield block


def _media_type(response: httpx.Response) -> str:
    """What the response said the bytes are, without its parameters.

    ``application/json; charset=utf-8`` is stored as ``application/json``: the
    charset describes how to read the bytes, and the column records what they
    are. A response with no type at all gets the neutral one rather than a
    guess, because this value is stored as though a source had said it.
    """
    declared = response.headers.get("Content-Type", "").split(";")[0].strip()
    return declared or "application/octet-stream"


def _sleep(seconds: float) -> None:
    """Indirection so a test can run the retry path without waiting."""
    time.sleep(seconds)
