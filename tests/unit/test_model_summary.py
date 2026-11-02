"""``model-summary-1`` — the file ``/api/v1/insights/model`` serves, checked whole.

No marker: a model and some JSON.

Reference: docs/DECISIONS.md D-086, D-332.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from meridian.api.public.models.model_insights import BINS, PublicModelInsights


def summary() -> dict[str, Any]:
    """A valid summary: one configuration fitted, one the archive cannot support."""
    bins = [
        {
            "low": index / BINS,
            "high": (index + 1) / BINS,
            "n": 0 if index == 9 else 12,
            "mean_predicted": None if index == 9 else index / BINS + 0.05,
            "observed": None if index == 9 else index / BINS,
            "observed_interval": None if index == 9 else [0.0, 0.5],
        }
        for index in range(BINS)
    ]
    return {
        "schema": "model-summary-1",
        "run_sha256": "ab" * 32,
        "method": "report-9",
        "label": "Measured by the SatNOGS network, not by Meridian. Selection-biased.",
        "source": {
            "name": "SatNOGS Network",
            "licence": "CC BY-SA 4.0",
            "terms_url": "https://network.satnogs.org/terms",
            "retrieved_from": "2026-10-01T00:00:00Z",
            "retrieved_until": "2026-10-29T00:00:00Z",
        },
        "simulated": False,
        "completeness_ratio": 0.31,
        "split": {
            "train_until": "2026-10-15",
            "validate_until": "2026-10-22",
            "as_of": "2026-10-29T00:00:00Z",
        },
        "configurations": [
            {
                "name": "A",
                "computable": True,
                "n_test": 108,
                "n_decoded": 41,
                "brier": 0.19,
                "brier_interval": [0.16, 0.22],
                "base_rate": 0.38,
                "base_brier": 0.236,
                "skill": 0.19,
                "skill_interval": None,
                "bins": bins,
            },
            {
                "name": "C",
                "computable": False,
                "reason": "needs a learned station profile",
            },
        ],
    }


def parse(body: dict[str, Any]) -> PublicModelInsights:
    return PublicModelInsights.model_validate_json(json.dumps(body))


def test_a_valid_summary_is_served_as_written_with_its_status() -> None:
    served = parse(summary()).model_dump(mode="json")

    assert served["status"] == "computed"
    assert served["schema"] == "model-summary-1"
    assert served["configurations"][1] == {
        "name": "C",
        "computable": False,
        "reason": "needs a learned station profile",
    }
    assert served["configurations"][0]["bins"][9]["observed"] is None
    assert served["configurations"][0]["skill_interval"] is None


def test_another_schema_is_refused() -> None:
    body = summary() | {"schema": "model-summary-2"}

    with pytest.raises(ValidationError):
        parse(body)


def test_a_configuration_that_is_not_computable_carries_no_number() -> None:
    body = summary()
    body["configurations"][1]["brier"] = 0.0

    with pytest.raises(ValidationError):
        parse(body)


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b["configurations"][0]["bins"].pop(),
        lambda b: b.update(extra=1),
        lambda b: b["configurations"].append(copy.deepcopy(b["configurations"][0])),
        lambda b: b.update(run_sha256="not-a-hash"),
        lambda b: b["configurations"][0].update(n_test="108"),
        lambda b: b["source"].update(terms_url="http://network.satnogs.org/terms"),
        lambda b: b.update(label=""),
    ],
    ids=[
        "nine bins",
        "unknown key",
        "a configuration twice",
        "a hash that is not one",
        "a count as text",
        "terms over plain http",
        "no label",
    ],
)
def test_a_damaged_summary_is_refused_whole(change: Any) -> None:
    body = summary()
    change(body)

    with pytest.raises(ValidationError):
        parse(body)
