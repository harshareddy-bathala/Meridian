"""The budget, the backoff, and the HTTP binding that obeys them.

Stage 14 is the only one whose traffic goes to somebody else's server, so these
are the rules that decide whether Meridian is a good guest. The policy is pure
and tested directly; the retriever is driven through ``httpx.MockTransport``,
which is in-process and opens no socket — D-142 holds here as everywhere else,
and Part 13's guard will prove it rather than take this file's word.

Nothing sleeps: ``_sleep`` is replaced, and what it was *asked* to wait for is
asserted instead, which is the only way the retry schedule is observable at all.

Reference: docs/DECISIONS.md D-134, D-142.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from meridian_ingest import http_retriever
from meridian_ingest.http_retriever import (
    USER_AGENT,
    HttpRetriever,
    open_client,
)
from meridian_ingest.politeness import (
    RETRYABLE_STATUSES,
    BudgetExhaustedError,
    RequestBudget,
    RetryPolicy,
    is_retryable,
    parse_retry_after,
)
from meridian_ingest.retrieval import RemoteArtefact, RetrievalError

NOW = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)
URL = "https://archive.invalid/v1/receptions-2026-08.json"
BODY = b'{"receptions": []}'

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def waited(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every delay the retriever asked for, without any of them elapsing."""
    delays: list[float] = []
    monkeypatch.setattr(http_retriever, "_sleep", delays.append)
    return delays


def a_remote(url: str = URL) -> RemoteArtefact:
    return RemoteArtefact(
        url=url, original_identifier="receptions-2026-08.json", payload_kind="data"
    )


Fetcher = Callable[..., HttpRetriever]


@pytest.fixture
def fetcher() -> Iterator[Fetcher]:
    """Builds retrievers over an in-process transport, and closes them after.

    A ``with`` block cannot do this: the retriever has to outlive the call that
    made it, because the body it returns is read afterwards.
    """
    made: list[HttpRetriever] = []

    def make(
        handler: Handler, budget: int = 10, policy: RetryPolicy | None = None
    ) -> HttpRetriever:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        made.append(HttpRetriever(client, RequestBudget(budget), policy))
        return made[-1]

    yield make
    for one in made:
        one.close()


def responder(*responses: httpx.Response) -> Handler:
    """Answers each request with the next response, repeating the last."""
    remaining = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return handle


# --- the request budget ----------------------------------------------------


def test_a_budget_is_spent_before_the_request_not_counted_after_it() -> None:
    """A ceiling checked afterwards is a ceiling already exceeded."""
    budget = RequestBudget(ceiling=2)

    budget.spend("first")
    budget.spend("second")

    assert budget.remaining == 0
    with pytest.raises(BudgetExhaustedError, match="spent its 2 requests"):
        budget.spend("third")
    assert budget.spent == 2, "the refused request was never made"


def test_the_refusal_names_where_the_fetch_stopped() -> None:
    """So an operator can resume from there rather than start again."""
    budget = RequestBudget(ceiling=1)
    budget.spend("receptions-2026-07.json")

    with pytest.raises(BudgetExhaustedError, match=r"receptions-2026-08\.json"):
        budget.spend("receptions-2026-08.json")


def test_a_budget_that_could_not_pay_for_anything_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot fetch anything"):
        RequestBudget(ceiling=0)


def test_retries_are_spent_from_the_same_budget(waited: list[float]) -> None:
    """A retry is a request the archive served, whatever we made of the response."""
    handler = responder(httpx.Response(503))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    budget = RequestBudget(ceiling=10)

    with (
        HttpRetriever(client, budget, RetryPolicy(attempts=3)) as fetching,
        pytest.raises(RetrievalError),
    ):
        fetching.retrieve(a_remote())

    assert budget.spent == 3
    assert len(waited) == 2, "waited between attempts, not after the last one"


def test_a_fetch_stops_before_exceeding_its_budget(waited: list[float]) -> None:
    """The ceiling is the promise; the retry policy bends to it, not the reverse."""
    handler = responder(httpx.Response(503))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    budget = RequestBudget(ceiling=2)

    with (
        HttpRetriever(client, budget, RetryPolicy(attempts=5)) as fetching,
        pytest.raises(BudgetExhaustedError),
    ):
        fetching.retrieve(a_remote())

    assert budget.spent == 2, "the third attempt was refused, not made"
    assert len(waited) == 1, "waited once, between the two requests it could afford"


# --- the backoff -----------------------------------------------------------


def test_each_retry_may_wait_longer_than_the_last() -> None:
    """Exponential, and the ceiling is what doubles — not the delay itself."""
    policy = RetryPolicy(attempts=5, base_delay_s=1.0, max_delay_s=30.0)

    ceilings = [max(policy.delay_before(n) for _ in range(200)) for n in (1, 2, 3)]

    assert ceilings[0] < ceilings[1] < ceilings[2]


def test_a_delay_is_spread_over_the_whole_interval() -> None:
    """Full jitter: without it every retry lands at the same phase of a rate window."""
    policy = RetryPolicy(base_delay_s=4.0)

    samples = [policy.delay_before(1) for _ in range(200)]

    assert all(0.0 <= one <= 4.0 for one in samples)
    assert min(samples) < 1.0
    assert max(samples) > 3.0


def test_the_delay_never_exceeds_the_ceiling() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=2.0)

    assert all(policy.delay_before(10) <= 2.0 for _ in range(200))


@pytest.mark.parametrize(
    ("attempts", "base", "maximum"),
    [(0, 1.0, 30.0), (-1, 1.0, 30.0), (4, 10.0, 1.0), (4, -1.0, 30.0)],
)
def test_a_policy_that_could_not_work_is_refused(
    attempts: int, base: float, maximum: float
) -> None:
    with pytest.raises(ValueError, match=r"attempts is not a policy|delays must rise"):
        RetryPolicy(attempts=attempts, base_delay_s=base, max_delay_s=maximum)


# --- what the archive itself asks for --------------------------------------


def test_a_servers_own_delay_replaces_our_computed_one() -> None:
    """It knows something about its capacity that a backoff curve does not."""
    assert RetryPolicy(max_delay_s=30.0).honour(12.0, "a.json") == 12.0


def test_a_wait_longer_than_we_will_give_is_a_refusal_not_a_sleep() -> None:
    """Holding a terminal open for an hour is a hung process, not politeness."""
    with pytest.raises(BudgetExhaustedError, match="Run it again later"):
        RetryPolicy(max_delay_s=30.0).honour(3600.0, "a.json")


def test_retry_after_reads_seconds() -> None:
    assert parse_retry_after("120", NOW) == 120.0


def test_retry_after_reads_an_http_date() -> None:
    later = NOW + timedelta(seconds=90)
    header = later.strftime("%a, %d %b %Y %H:%M:%S GMT")

    assert parse_retry_after(header, NOW) == pytest.approx(90.0)


def test_a_date_already_past_is_no_wait_at_all() -> None:
    header = (NOW - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")

    assert parse_retry_after(header, NOW) == 0.0


@pytest.mark.parametrize("header", [None, "", "  ", "soon please", "-"])
def test_an_unusable_hint_falls_back_to_our_own_backoff(header: str | None) -> None:
    """A malformed header still means slow down; it just cannot say how much."""
    assert parse_retry_after(header, NOW) is None


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUSES))
def test_a_transient_status_is_worth_asking_again(status: int) -> None:
    assert is_retryable(status)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 451])
def test_a_permanent_status_is_not(status: int) -> None:
    """A 403 is the same 403 in thirty seconds, and asking again is rude."""
    assert not is_retryable(status)


# --- the HTTP binding ------------------------------------------------------


def test_an_artefact_is_returned_with_its_bytes_and_its_headers(
    fetcher: Fetcher,
) -> None:
    handler = responder(
        httpx.Response(
            200, content=BODY, headers={"ETag": '"abc"', "Content-Type": "text/json"}
        )
    )
    fetching = fetcher(handler)

    retrieved = fetching.retrieve(a_remote())

    assert b"".join(retrieved.chunks) == BODY
    assert retrieved.headers["etag"] == '"abc"'
    assert retrieved.media_type == "text/json"


def test_the_media_type_is_stored_without_its_parameters(fetcher: Fetcher) -> None:
    """The charset says how to read the bytes; the column records what they are."""
    handler = responder(
        httpx.Response(
            200,
            content=BODY,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    )

    retrieved = fetcher(handler).retrieve(a_remote())

    assert retrieved.media_type == "application/json"


def test_a_response_with_no_type_is_not_guessed_at(fetcher: Fetcher) -> None:
    handler = responder(httpx.Response(200, content=BODY, headers={}))
    fetching = fetcher(handler)

    media_type = fetching.retrieve(a_remote()).media_type

    assert media_type in {"application/octet-stream", "text/plain"}


def test_a_transient_failure_is_retried_and_then_succeeds(
    waited: list[float], fetcher: Fetcher
) -> None:
    handler = responder(
        httpx.Response(503),
        httpx.Response(200, content=BODY, headers={"ETag": '"abc"'}),
    )
    fetching = fetcher(handler)

    retrieved = fetching.retrieve(a_remote())

    assert b"".join(retrieved.chunks) == BODY
    assert len(waited) == 1


def test_a_permanent_refusal_is_not_retried(
    waited: list[float], fetcher: Fetcher
) -> None:
    """Four attempts at a 404 is four requests an archive did not deserve."""
    handler = responder(httpx.Response(404))
    fetching = fetcher(handler)

    with pytest.raises(RetrievalError, match="asking again would return as well"):
        fetching.retrieve(a_remote())

    assert waited == []


def test_a_transport_failure_is_retried(waited: list[float], fetcher: Fetcher) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    fetching = fetcher(refuse, policy=RetryPolicy(attempts=2))

    with pytest.raises(RetrievalError, match="ConnectError"):
        fetching.retrieve(a_remote())

    assert len(waited) == 1


def test_the_failure_says_what_each_attempt_met(
    waited: list[float], fetcher: Fetcher
) -> None:
    """ "Four attempts failed" sends an operator to run it again to find out."""
    handler = responder(httpx.Response(503), httpx.Response(500))
    fetching = fetcher(handler, policy=RetryPolicy(attempts=2))

    with pytest.raises(RetrievalError) as raised:
        fetching.retrieve(a_remote())

    assert "attempt 1: HTTP 503" in str(raised.value)
    assert "attempt 2: HTTP 500" in str(raised.value)
    assert len(waited) == 1


def test_a_servers_retry_after_is_what_gets_waited(
    waited: list[float], fetcher: Fetcher
) -> None:
    handler = responder(
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, content=BODY),
    )
    fetching = fetcher(handler)

    fetching.retrieve(a_remote())

    assert waited == [7.0]


def test_a_retry_after_beyond_the_policy_stops_the_fetch(
    waited: list[float], fetcher: Fetcher
) -> None:
    handler = responder(httpx.Response(429, headers={"Retry-After": "3600"}))
    fetching = fetcher(handler)

    with pytest.raises(BudgetExhaustedError, match="asked for 3600s"):
        fetching.retrieve(a_remote())

    assert waited == []


def test_a_body_that_stopped_short_of_its_declared_length_is_retried(
    waited: list[float], fetcher: Fetcher
) -> None:
    """A truncated artefact handed on is a failure recorded as a fact."""
    handler = responder(
        httpx.Response(200, content=BODY, headers={"Content-Length": "9999"}),
        httpx.Response(200, content=BODY),
    )
    fetching = fetcher(handler)

    retrieved = fetching.retrieve(a_remote())

    assert b"".join(retrieved.chunks) == BODY
    assert len(waited) == 1


def test_nothing_downstream_sees_the_bytes_of_a_failed_attempt(
    waited: list[float], fetcher: Fetcher
) -> None:
    """Which is why the body is read here rather than streamed straight through."""
    handler = responder(
        httpx.Response(200, content=b"half", headers={"Content-Length": "9999"}),
        httpx.Response(200, content=BODY),
    )
    fetching = fetcher(handler)

    assert b"".join(fetching.retrieve(a_remote()).chunks) == BODY
    assert len(waited) == 1


# --- how the client identifies itself --------------------------------------


def test_the_client_says_who_it_is_and_where_to_complain() -> None:
    """An archive operator should find out whose traffic this is in one search."""
    with open_client() as client:
        assert client.headers["User-Agent"] == USER_AGENT

    assert "meridian-ingest/" in USER_AGENT
    assert "github.com" in USER_AGENT


def test_the_client_does_not_follow_a_redirect() -> None:
    """The recorded URL must be where the bytes actually came from."""
    with open_client() as client:
        assert client.follow_redirects is False


def test_a_redirect_is_a_refusal_rather_than_a_silent_move(
    waited: list[float], fetcher: Fetcher
) -> None:
    handler = responder(
        httpx.Response(302, headers={"Location": "https://else.invalid/"})
    )
    fetching = fetcher(handler)

    with pytest.raises(RetrievalError, match="HTTP 302"):
        fetching.retrieve(a_remote())

    assert waited == []
