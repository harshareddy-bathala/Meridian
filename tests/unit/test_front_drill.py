"""What the §12.2 drill decides from the front's answers, without a deployment.

``deploy/tools/front_drill.py`` stops the api of a running stack and asks the
front, so its effect is exercised in CI's image job and by an operator on the
Pi. What it *judges* is pure, and pinned here: each way EVALUATION.md §12.2
says the drill fails must be named as a failure, and a good run must pass.

Marked as a unit test by living in ``tests/unit``: no Docker, no network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "deploy/tools"
STOPPED_AT = datetime(2026, 10, 2, 15, 8, 30, tzinfo=UTC)
BEFORE = "Fri, 02 Oct 2026 15:08:12 GMT"
AFTER = "Fri, 02 Oct 2026 15:09:00 GMT"


@pytest.fixture(scope="module")
def drill() -> Iterator[ModuleType]:
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(
        "front_drill", TOOLS / "front_drill.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["front_drill"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(TOOLS))
        sys.modules.pop("front_drill", None)
        sys.modules.pop("compose_db", None)


def view(drill: ModuleType, path: str = "/api/v1/reliability", **changed: Any) -> Any:
    """A kept copy served well: 200, STALE, dated before the stop, in two seconds."""
    fields = {"status": 200, "cache": "STALE", "date": BEFORE, "seconds": 2.0}
    return drill.View(path=path, **(fields | changed))


def gone(drill: ModuleType, path: str, **changed: Any) -> Any:
    fields = {"status": 504, "cache": "MISS", "date": AFTER, "seconds": 2.0}
    return drill.View(path=path, **(fields | changed))


def outage(drill: ModuleType, *views: Any, also: tuple[Any, ...] = ()) -> list[str]:
    asked = drill.Asked(views=tuple(views), also=also)
    return list(drill.judge_outage(asked, STOPPED_AT).failures)


def test_copies_labelled_dated_and_prompt_pass(drill: ModuleType) -> None:
    also = (gone(drill, drill.UNWARMED), gone(drill, drill.NEVER_KEPT, cache=""))

    assert outage(drill, view(drill, "/"), view(drill), also=also) == []


def test_a_page_that_errors_fails(drill: ModuleType) -> None:
    (failure,) = outage(drill, view(drill, status=504, cache="MISS"))

    assert "the page errors" in failure


def test_a_copy_that_does_not_say_it_is_one_fails(drill: ModuleType) -> None:
    """§12.2's serious failure: a stale figure presented as current."""
    (failure,) = outage(drill, view(drill, cache="HIT"))

    assert "without saying it is one" in failure


@pytest.mark.parametrize("date", [AFTER, "", "not a date"])
def test_a_copy_not_dated_before_the_stop_fails(drill: ModuleType, date: str) -> None:
    """Its age is what the dashboard shows; a wrong Date is a wrong age."""
    (failure,) = outage(drill, view(drill, date=date))

    assert "not from before the stop" in failure


def test_a_page_that_hangs_fails_even_if_it_answers(drill: ModuleType) -> None:
    (failure,) = outage(drill, view(drill, seconds=drill.PROMPT_S + 0.1))

    assert "the page hangs" in failure


def test_an_unwarmed_view_must_fail_promptly_not_hang(drill: ModuleType) -> None:
    slow = gone(drill, drill.UNWARMED, seconds=20.0)

    (failure,) = outage(drill, view(drill), also=(slow,))

    assert "to fail: the page hangs" in failure


def test_an_msp_route_answered_during_the_outage_fails(drill: ModuleType) -> None:
    """The front keeps no MSP response (D-282); a 200 here is one it kept."""
    kept = view(drill, drill.NEVER_KEPT)

    (failure,) = outage(drill, view(drill), also=(kept,))

    assert "from a copy nobody owed" in failure


def test_the_drill_refuses_to_start_on_a_platform_already_down(
    drill: ModuleType,
) -> None:
    """With no live answer to compare with, a stale copy proves nothing."""
    asked = drill.Asked(views=(view(drill), view(drill, "/", cache="EXPIRED")))

    (failure,) = drill.judge_warm(asked).failures

    assert "already a copy" in failure
    assert drill.judge_warm(drill.Asked(views=())).failures


def test_a_copy_still_served_after_the_api_is_back_fails(drill: ModuleType) -> None:
    asked = drill.Asked(views=(view(drill), view(drill, "/", cache="EXPIRED")))

    (failure,) = drill.judge_recovery(asked).failures

    assert "still a copy after the api came back" in failure


def test_the_warmers_report_is_read_as_it_is_written(drill: ModuleType) -> None:
    one = {"path": "/", "status": 200, "cache": "STALE", "date": BEFORE, "seconds": 2}
    report = json.dumps({"stations": 3, "views": [one], "also": []})

    asked = drill.parse_report(report)

    assert asked.stations == 3
    assert asked.views[0].made_at() == datetime(2026, 10, 2, 15, 8, 12, tzinfo=UTC)
    with pytest.raises(sys.modules["compose_db"].ToolError):
        drill.parse_report("warmed 21 views")


def test_the_front_is_given_time_to_find_a_recreated_api(drill: ModuleType) -> None:
    """A recreated api has a new address, and the front looks it up again."""
    copy = drill.Asked(views=(view(drill),))
    live = drill.Asked(views=(view(drill, cache="MISS"),))
    rounds = iter([copy, copy, live])
    now = [0.0]

    asked, verdict = drill.ask_until_live(
        lambda: next(rounds),
        drill.judge_warm,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert asked is live
    assert not verdict.failures


def test_an_api_the_front_never_finds_still_fails(drill: ModuleType) -> None:
    copy = drill.Asked(views=(view(drill),))
    now = [0.0]

    _, verdict = drill.ask_until_live(
        lambda: copy,
        drill.judge_recovery,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert now[0] >= drill.FRONT_FINDS_API_S
    (failure,) = verdict.failures
    assert "still a copy after the api came back" in failure
