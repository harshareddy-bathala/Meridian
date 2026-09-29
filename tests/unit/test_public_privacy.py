"""Every field the public API publishes, pinned to the privacy review (D-210).

``docs/THREAT-MODEL.md`` §7 reviews what each public response discloses. That
review is only true while the responses stay as reviewed, so the field set is
written out here, model by model. Adding a field fails this test until the field
is added here and to §7 in the same change, which is the review happening, and
removing one fails it too, so the record never overstates what is published.

Marked as a unit test by living in ``tests/unit``: imports models, runs nothing.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from pydantic import BaseModel

import meridian.api.public.models as public_models

REVIEWED = {
    "Explanation": {"alternative", "rule", "run", "terms", "weighed_against"},
    "ExplanationRun": {"history_as_of", "status"},
    "ExplanationTerms": {
        "frames",
        "frames_term",
        "priority",
        "priority_weighted",
        "value",
        "yield_",
        "yield_path",
        "yield_reason",
        "yield_source",
    },
    "HorizonMaskPoint": {"azimuth_deg", "min_elevation_deg"},
    "ListeningBlock": {"assignment_id", "centre_freq_hz", "mode", "satellite_id"},
    "NotMeasuredFigure": {"reason", "status"},
    "NotYetComputed": {"available_from_stage", "reason", "status"},
    "Page": {"items", "next_cursor"},
    "PublicAssignment": {
        "assignment_id",
        "centre_freq_hz",
        "conflicts_with_assignment_id",
        "decision",
        "end_at",
        "explanation",
        "issued_at",
        "mode",
        "model_sha256",
        "pass_id",
        "predicted_yield",
        "prediction_config",
        "priority",
        "reason",
        "revision",
        "revoked_reason",
        "satellite_id",
        "schedule_run_id",
        "score",
        "simulated",
        "start_at",
        "state",
        "station_id",
        "timing_uncertainty_s",
    },
    "PublicBudget": {
        "allowed",
        "by_reason",
        "capture_target",
        "eligible",
        "exhausted",
        "remaining",
        "remaining_ratio",
        "spent",
    },
    "PublicCapability": {
        "band",
        "freq_max_hz",
        "freq_min_hz",
        "horizon_mask",
        "min_elevation_deg",
        "modes",
        "polarisation",
        "tracking",
    },
    "PublicDelays": {"n", "p50_s", "p95_s"},
    "PublicHeartbeat": {
        "clock_offset_s",
        "clock_uncertainty_s",
        "held_assignments",
        "id",
        "listening",
        "received_at",
        "sent_at",
        "simulated",
        "state",
        "station_id",
    },
    "PublicObservation": {
        "assignment_id",
        "ended_at",
        "observation_id",
        "outcome",
        "peak_snr_db",
        "provenance",
        "revision",
        "satellite_id",
        "signal_detected",
        "simulated",
        "started_at",
        "station_id",
        "submitted_at",
    },
    "PublicPass": {
        "aos",
        "aos_azimuth_deg",
        "element_set_epoch",
        "los",
        "los_azimuth_deg",
        "max_elevation_at",
        "max_elevation_deg",
        "min_elevation_deg",
        "pass_id",
        "satellite_id",
        "simulated",
        "station_id",
    },
    "PublicPopulation": {
        "assignment_completion_rate",
        "capture_rate",
        "confirmed_miss_rate",
        "loss_budget",
        "passes",
        "schedule_execution_rate",
        "simulated",
        "station_availability",
        "stations",
        "submission_delay",
        "targets",
    },
    "PublicProportion": {"denominator", "estimate", "interval", "numerator"},
    "PublicReliability": {
        "classification_sha256",
        "failure_detection",
        "measured",
        "method",
        "simulated",
        "status",
        "window_end",
        "window_start",
    },
    "PublicSatellite": {
        "element_set_age_s",
        "is_active",
        "latest_element_set_epoch",
        "name",
        "orbital_regime",
        "priority",
        "satellite_id",
    },
    "PublicShare": {"covered_s", "estimate", "span_s"},
    "PublicSimulatorRun": {
        "first_registered_at",
        "last_heartbeat_at",
        "last_registered_at",
        "run_id",
        "simulated",
        "station_count",
    },
    "PublicStation": {
        "last_heartbeat_at",
        "liveness",
        "location",
        "location_precision_decimals",
        "name",
        "operator",
        "registered_at",
        "simulated",
        "station_id",
    },
    "PublicStationReliability": {"availability", "budget", "capture", "station_id"},
    "PublicTarget": {"at_least", "claim", "met", "name", "target", "value"},
    "PublicTransmitter": {
        "bandwidth_hz",
        "centre_freq_hz",
        "is_active",
        "mode",
        "polarisation",
        "satellite_id",
        "source",
    },
    "PublishedLocation": {"alt_m", "lat_deg", "lon_deg"},
    "StationLiveness": {"last_heartbeat_at", "liveness", "simulated", "station_id"},
    "WeighedPass": {"assignment_id", "decision", "pass_id", "value"},
}

NEVER_PUBLISHED = (
    "token",
    "registration_key",
    "invite",
    "health",
    "seed",
    "client_implementation",
    "client_version",
    "password",
)
"""Words no public field name may contain; D-088's verifier checks the same list
from outside, against a running deployment."""


def _published_models() -> dict[str, type[BaseModel]]:
    found = {}
    for info in pkgutil.iter_modules(public_models.__path__):
        module = importlib.import_module(f"{public_models.__name__}.{info.name}")
        for name, value in vars(module).items():
            # `name.isidentifier()`: pydantic registers each parametrised
            # generic, such as `Page[PublicPass]`, in its module under that
            # name once the app has used it. Its fields are `Page`'s.
            if (
                name.isidentifier()
                and inspect.isclass(value)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
            ):
                found[name] = value
    return found


def test_every_published_field_is_one_the_review_covers() -> None:
    published = {
        name: set(model.model_fields) for name, model in _published_models().items()
    }

    assert published == REVIEWED, (
        "The public API's fields changed. Review what the change discloses, then "
        "update docs/THREAT-MODEL.md §7 and REVIEWED here in the same commit."
    )


def test_no_published_field_is_a_credential_or_station_internal() -> None:
    names = {field for fields in REVIEWED.values() for field in fields}

    assert [n for n in names if any(word in n for word in NEVER_PUBLISHED)] == []
