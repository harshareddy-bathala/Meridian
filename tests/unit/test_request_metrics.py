"""Every request is counted, and no caller can choose a label's value.

The labels are the point. A route label taken from the typed path would put
station identifiers into Prometheus and let a scan of random URLs create a series
per URL, so these tests pin that the label is the matched route's template, that
anything unrouted collapses to one value, and that a request the application
fails on is still counted as the 500 its caller received.

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/DECISIONS.md D-109, D-111.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from meridian.api.request_metrics import (
    UNROUTED,
    RequestMetricsMiddleware,
    RouteTemplates,
    method_label,
    status_class_label,
)

ROUTE = "/unit-request-metrics/items/{item_id}"
FAILING_ROUTE = "/unit-request-metrics/fails"
INCLUDED_ROUTE = "/unit-request-metrics/prefixed/things/{thing_id}"


def requests_counted(route: str, method: str, status_class: str) -> float:
    """The counter's current value for one label set, zero when never seen."""
    value = REGISTRY.get_sample_value(
        "meridian_http_requests_total",
        {"route": route, "method": method, "status_class": status_class},
    )
    return value or 0.0


@pytest.fixture
def client() -> TestClient:
    """A two-route application wrapped in the middleware, and nothing else."""
    app = FastAPI()
    app.add_middleware(RequestMetricsMiddleware)

    @app.get(ROUTE)
    def item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    @app.get(FAILING_ROUTE)
    def fails() -> None:
        raise RuntimeError("the application failed")

    # An included router, as every MSP and public route is: FastAPI records such
    # a route in the scope without its prefix, which is the case that broke.
    router = APIRouter()

    @router.get("/things/{thing_id}")
    def thing(thing_id: str) -> dict[str, str]:
        return {"thing_id": thing_id}

    app.include_router(router, prefix="/unit-request-metrics/prefixed")

    return TestClient(app, raise_server_exceptions=False)


def test_a_routed_request_is_labelled_with_its_template_not_its_path(
    client: TestClient,
) -> None:
    """Two different identifiers are one series, named by the template."""
    before = requests_counted(ROUTE, "GET", "2xx")

    client.get("/unit-request-metrics/items/first")
    client.get("/unit-request-metrics/items/second")

    assert requests_counted(ROUTE, "GET", "2xx") == before + 2
    typed = REGISTRY.get_sample_value(
        "meridian_http_requests_total",
        {
            "route": "/unit-request-metrics/items/first",
            "method": "GET",
            "status_class": "2xx",
        },
    )
    assert typed is None


def test_a_route_on_an_included_router_keeps_its_prefix(client: TestClient) -> None:
    """The label is the full template, prefix included, not the router's part."""
    before = requests_counted(INCLUDED_ROUTE, "GET", "2xx")

    client.get("/unit-request-metrics/prefixed/things/abc")

    assert requests_counted(INCLUDED_ROUTE, "GET", "2xx") == before + 1
    assert requests_counted("/things/{thing_id}", "GET", "2xx") == 0.0


def test_a_literal_template_wins_over_a_parameter_in_the_same_place() -> None:
    """The same precedence routing gives a literal segment."""
    templates = RouteTemplates(["/stations/{station_id}", "/stations/nearby"])

    assert templates.label_for("/stations/nearby") == "/stations/nearby"
    assert templates.label_for("/stations/st_1") == "/stations/{station_id}"
    assert templates.label_for("/stations/st_1/extra") == UNROUTED


def test_an_unrouted_request_collapses_to_one_label(client: TestClient) -> None:
    """A scan of made-up URLs adds to one series rather than creating many."""
    before = requests_counted(UNROUTED, "GET", "4xx")

    client.get("/unit-request-metrics/no-such-path-1")
    client.get("/unit-request-metrics/no-such-path-2")

    assert requests_counted(UNROUTED, "GET", "4xx") == before + 2


def test_a_request_the_application_fails_on_is_counted_as_a_500(
    client: TestClient,
) -> None:
    """The exception propagates past the middleware, and is still recorded."""
    before = requests_counted(FAILING_ROUTE, "GET", "5xx")

    response = client.get(FAILING_ROUTE)

    assert response.status_code == 500
    assert requests_counted(FAILING_ROUTE, "GET", "5xx") == before + 1


def test_the_duration_is_observed_under_the_same_route_label(
    client: TestClient,
) -> None:
    """Latency is broken down by the same bounded label as the count."""
    labels = {"route": ROUTE}
    before = REGISTRY.get_sample_value(
        "meridian_http_request_duration_seconds_count", labels
    )

    client.get("/unit-request-metrics/items/timed")

    after = REGISTRY.get_sample_value(
        "meridian_http_request_duration_seconds_count", labels
    )
    assert after == (before or 0.0) + 1


@pytest.mark.parametrize(
    ("method", "expected"),
    [("GET", "GET"), ("POST", "POST"), ("OPTIONS", "OPTIONS"), ("BREW", "other")],
)
def test_a_non_standard_method_is_labelled_other(method: str, expected: str) -> None:
    """A method is a string the caller chooses, so only the standard set is kept."""
    assert method_label(method) == expected


@pytest.mark.parametrize(
    ("status", "expected"), [(200, "2xx"), (304, "3xx"), (404, "4xx"), (503, "5xx")]
)
def test_the_status_is_labelled_by_class(status: int, expected: str) -> None:
    """The class, never the exact status."""
    assert status_class_label(status) == expected
