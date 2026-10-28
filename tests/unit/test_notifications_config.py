"""The notifications file: which stations get reports, and how delivery retries.

Marked as a unit test by living in ``tests/unit``. What the file allows:
- the example is the defaults, and loads;
- no file means no reports;
- a channel is email, telegram or sink, and a station is listed once;
- an unknown table or key is refused, never ignored;
- a retry waits twice as long each time, up to its cap;
- a week is queued once it has ended and settled, Monday to Monday UTC.

The registry half — a simulated station only on the sink — is
``tests/integration/test_report_compose.py``.

Reference: docs/DECISIONS.md D-300, D-305, D-306.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.notifications.compose import last_week
from meridian.notifications.config import (
    NOTIFICATIONS_CONFIG_ENV,
    NotificationsConfig,
    NotificationsConfigError,
    Recipient,
    RetryPolicy,
    load_deployed_notifications_config,
    load_notifications_config,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "deploy/notifications.toml.example"


def written(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "notifications.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_example_is_the_defaults() -> None:
    assert load_notifications_config(EXAMPLE) == NotificationsConfig()


def test_no_file_means_no_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTIFICATIONS_CONFIG_ENV, raising=False)

    assert load_deployed_notifications_config().stations == ()


def test_stations_are_read_with_their_channels(tmp_path: Path) -> None:
    config = load_notifications_config(
        written(
            tmp_path,
            '[[station]]\nstation_id = "st_a"\nchannel = "email"\n\n'
            '[[station]]\nstation_id = "st_b"\nchannel = "sink"\nweekly = false\n',
        )
    )

    assert config.stations == (
        Recipient("st_a", "email", weekly=True),
        Recipient("st_b", "sink", weekly=False),
    )
    assert config.channels() == {"st_a": "email", "st_b": "sink"}
    assert config.channels(weekly=True) == {"st_a": "email"}


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('[[station]]\nstation_id = "st_a"\nchannel = "sms"\n', "channel must be"),
        ('[[station]]\nchannel = "email"\n', "needs a station_id"),
        (
            '[[station]]\nstation_id = "st_a"\nchannel = "email"\n'
            '[[station]]\nstation_id = "st_a"\nchannel = "sink"\n',
            "listed twice",
        ),
        (
            '[[station]]\nstation_id = "st_a"\nchannel = "email"\n'
            'address = "owner@example.org"\n',
            "unknown keys",
        ),
        ("[recipients]\n", "unknown tables"),
        ("[retry]\nfirst_s = 0\n", "first_s must be"),
        ("[retry]\nfirst_s = 600\ncap_s = 60\n", "cap_s must be at least"),
        ("[reports]\nwait_s = true\n", "whole number"),
        ('[sink]\ndirectory = ""\n', "sink.directory"),
        ("[reports\n", "notifications.toml"),
    ],
    ids=[
        "channel",
        "no-station",
        "twice",
        "an-address",
        "unknown-table",
        "retry-zero",
        "cap-below-first",
        "not-a-number",
        "no-directory",
        "not-toml",
    ],
)
def test_a_file_it_cannot_obey_is_refused(
    tmp_path: Path, text: str, reason: str
) -> None:
    with pytest.raises(NotificationsConfigError, match=reason):
        load_notifications_config(written(tmp_path, text))


@pytest.mark.parametrize(
    ("failures", "wait_s"),
    [(1, 60), (2, 120), (3, 240), (9, 15_360), (10, 21_600), (40, 21_600)],
)
def test_a_retry_waits_twice_as_long_each_time_up_to_its_cap(
    failures: int, wait_s: int
) -> None:
    assert RetryPolicy().wait_after(failures) == wait_s


@pytest.mark.parametrize(
    ("now", "week"),
    [
        # Wednesday: last week, Monday 12 to Monday 19.
        (datetime(2026, 10, 21, 9, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC)),
        # Monday 05:59, six hours unsettled: the week before.
        (datetime(2026, 10, 19, 5, 59, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC)),
        # Monday 06:00, just settled.
        (datetime(2026, 10, 19, 6, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC)),
    ],
    ids=["midweek", "unsettled", "settled"],
)
def test_the_week_queued_has_ended_and_settled(now: datetime, week: datetime) -> None:
    assert last_week(now, 6 * 3_600) == week
    assert last_week(now, 6 * 3_600) + timedelta(days=7) <= now - timedelta(hours=6)
