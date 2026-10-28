"""The recipients file: the sender's secret, and no refusal ever repeats it.

Marked as a unit test by living in ``tests/unit``. An address is personal data
(D-107), so every message this file can cause names the key, never the value.

Reference: docs/DECISIONS.md D-107, D-300.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.notifications.config import NotificationsConfigError
from meridian.notifications.recipients import (
    RECIPIENTS_FILE_ENV,
    load_deployed_recipients,
    load_recipients,
)

OWNER = "owner-private@example.org"


def written(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "recipients.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_each_key_maps_to_its_recipient(tmp_path: Path) -> None:
    path = written(
        tmp_path, f'[recipients]\nst_001 = " {OWNER} "\nteam = "123456789"\n'
    )

    assert load_recipients(path) == {"st_001": OWNER, "team": "123456789"}


def test_no_file_means_no_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RECIPIENTS_FILE_ENV, raising=False)

    assert load_deployed_recipients() == {}


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (f'[recipients]\nst_001 = "{OWNER}\n', "is not TOML"),
        (f'[recipients]\nst_001 = "{OWNER} and more"\n', "malformed"),
        ("[recipients]\nst_001 = 7\n", "must be text"),
        (f'[stations]\nst_001 = "{OWNER}"\n', r"only a \[recipients\] table"),
        (f'[recipients]\nst_001 = "{"x" * 250}{OWNER}"\n', "malformed"),
    ],
    ids=["not-toml", "spaces", "not-text", "other-table", "too-long"],
)
def test_a_refusal_names_the_key_and_never_the_recipient(
    tmp_path: Path, text: str, reason: str
) -> None:
    with pytest.raises(NotificationsConfigError, match=reason) as raised:
        load_recipients(written(tmp_path, text))

    assert OWNER not in str(raised.value)
    assert raised.value.__cause__ is None


def test_an_unreadable_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NotificationsConfigError, match="cannot be read"):
        load_recipients(tmp_path / "absent.toml")
