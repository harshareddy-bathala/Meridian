"""Which vocabulary a failure is answered in, decided by the path.

`install_error_handlers` is installed on the application, so its last three
handlers see MSP requests and public API requests alike. Two of them answer in
whichever vocabulary the path belongs to, and these tests are what fail if that
is ever inverted — a station receiving `invalid_query`, or a dashboard receiving
`unknown_assignment`.

The third, for a request that matched no route, deliberately answers the same way
everywhere (D-090), so the tests for it assert *sameness* across surfaces where
the others assert difference.

The application here is a bare `FastAPI` with throwaway routes rather than
`create_app()`: the behaviour under test is the handler stack, and building the
real application would drag in settings and a connection pool that have nothing
to do with it.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem.

Reference: docs/DECISIONS.md D-084, D-090; docs/MSP-SPEC.md §6.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meridian.api.errors import MALFORMED, SERVER_ERROR, install_error_handlers
from meridian.api.public.envelope import (
    INVALID_QUERY,
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    STATUS_FOR_PUBLIC_CODE,
    PublicError,
)


@pytest.fixture
def client() -> TestClient:
    """Routes that fail on purpose, paired across the two surfaces.

    Paired deliberately: almost every assertion below is that one surface
    answers differently from the other, or — for the routing handler — that it
    does not, and neither can be shown from one path alone.
    """
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/v1/missing")
    def _public_route() -> None:
        raise PublicError(NOT_FOUND, "No station with that id.")

    @app.get("/api/v1/paged")
    def _public_query(limit: int) -> int:
        return limit

    @app.get("/msp/v0/paged")
    def _msp_query(limit: int) -> int:
        return limit

    @app.get("/api/v1/broken")
    def _public_surprise() -> None:
        raise RuntimeError("something the author did not foresee")

    @app.get("/msp/v0/broken")
    def _msp_surprise() -> None:
        raise RuntimeError("something the author did not foresee")

    # Errors are wanted as responses, not re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


def test_a_public_error_answers_in_the_public_vocabulary(client: TestClient) -> None:
    """404 and `not_found` — a code MSP §6 does not have."""
    response = client.get("/api/v1/missing")

    assert response.status_code == 404
    assert response.json() == {
        "error": NOT_FOUND,
        "message": "No station with that id.",
    }


def test_a_public_query_failure_is_invalid_query_not_malformed(
    client: TestClient,
) -> None:
    """A read endpoint has no body, so `malformed` would point at nothing.

    An unparseable `limit` makes FastAPI raise `RequestValidationError`, which is
    one of the two handlers shared between the surfaces — so this is the case
    where the guard clause has to do real work.
    """
    response = client.get("/api/v1/paged?limit=not-a-number")

    assert response.status_code == 400
    assert response.json() == {
        "error": INVALID_QUERY,
        "message": "Request query failed validation.",
    }


def test_the_same_failure_on_an_msp_path_is_still_malformed(
    client: TestClient,
) -> None:
    """The other half of the guard, which is what makes the first half a choice.

    Identical failure, identical handler, different surface. Without this a
    change that answered `invalid_query` everywhere would pass the test above and
    quietly break every station's error handling.
    """
    response = client.get("/msp/v0/paged?limit=not-a-number")

    assert response.status_code == 400
    assert response.json() == {
        "error": MALFORMED,
        "message": "Request body failed validation.",
    }


def test_an_unexpected_failure_keeps_the_two_field_body_on_both_surfaces(
    client: TestClient,
) -> None:
    """Same code and same shape either side, and no detail from the exception.

    The message must not carry `str(exc)`: on a psycopg failure that is the
    failing SQL and often the connection string.
    """
    public = client.get("/api/v1/broken")
    msp = client.get("/msp/v0/broken")

    assert public.status_code == msp.status_code == 500
    assert (
        public.json()
        == msp.json()
        == {
            "error": SERVER_ERROR,
            "message": "Internal error.",
        }
    )
    assert "foresee" not in public.text


@pytest.mark.parametrize("path", ["/api/v1/statons", "/msp/v0/registr", "/nope", "/"])
def test_an_unknown_url_answers_in_the_envelope_on_every_surface(
    client: TestClient, path: str
) -> None:
    """D-090: the same two-field body wherever the typo was made.

    `/msp/v0/registr` is the case that decides the design. MSP §6 has no code for
    "no such endpoint" and D-084 closed that table — so the answer here proves
    the request is treated as never having become an MSP operation, rather than
    as an operation that failed in a way §6 would have to name.
    """
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"error": NOT_FOUND, "message": "No such endpoint."}


def test_an_unknown_url_never_answers_in_fastapis_own_shape(
    client: TestClient,
) -> None:
    """The regression this replaces: `{"detail": "Not Found"}`.

    Asserted separately from the body above because `detail` reappearing is what
    a future FastAPI upgrade or a stray `HTTPException` would look like, and it
    would still be a 404 with a plausible-looking body.
    """
    response = client.get("/api/v1/statons")

    assert "detail" not in response.json()


@pytest.mark.parametrize("path", ["/api/v1/missing", "/msp/v0/broken"])
def test_the_wrong_method_is_refused_in_the_same_words(
    client: TestClient, path: str
) -> None:
    """405 travels with `method_not_allowed`, on both surfaces.

    Both paths exist as GET routes, so this reaches the router's method check
    rather than its path check — a different Starlette failure arriving at the
    same handler.
    """
    response = client.post(path)

    assert response.status_code == 405
    assert response.json() == {
        "error": METHOD_NOT_ALLOWED,
        "message": "That method is not allowed on this endpoint.",
    }


def test_the_code_and_the_status_can_never_disagree() -> None:
    """The status is read from the code's own table, not from the exception.

    A handler that returned `exc.status_code` beside a looked-up code would let
    the two drift apart the moment Starlette raised a status the mapping does not
    list. This pins that every routing code is served at its table's status.
    """
    assert STATUS_FOR_PUBLIC_CODE[NOT_FOUND] == 404
    assert STATUS_FOR_PUBLIC_CODE[METHOD_NOT_ALLOWED] == 405
