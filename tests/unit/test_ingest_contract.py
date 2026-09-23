"""The adapter and normaliser contract: what it refuses, and what its shape promises.

Two of these tests are unusual and deliberate. One reads
:meth:`Normaliser.normalise`'s *signature* and asserts it takes one argument of
one type, because "normalisation cannot reach a network" is a claim about the
shape of a function and would otherwise be only a sentence in a docstring. The
other compares :class:`SourceDescriptor`'s fields with the store's
``NewIngestSource``, because the same seven fields defined in two places is a
thing that drifts.

No network, no database, no disk.

Reference: docs/DECISIONS.md D-133, D-134, D-139, D-140, D-142.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any, get_type_hints

import pytest

from meridian.store.ingest_sources import NewIngestSource
from meridian_ingest.adapters.protocol import (
    Adapter,
    FetchRequest,
    Normaliser,
    SourceDescriptor,
    TermsNotRecordedError,
    TileIsNotAQuantityError,
    refuse_a_tile,
    require_source_version,
)
from meridian_ingest.normalise.records import (
    NormalisationError,
    NormalisedBatch,
    NormalisedReception,
    NormalisedStation,
)
from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievalError

SOURCE = "reference_archive"
RETRIEVED_AT = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)
STARTED_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)


def a_descriptor(**overrides: Any) -> SourceDescriptor:
    fields: dict[str, Any] = {
        "source_id": SOURCE,
        "source_class": "archive_receptions",
        "name": "Reference archive",
        "licence": "CC-BY-4.0",
        "terms_url": "https://example.invalid/terms",
        "attribution_entry": "Ingested data sources: reference adapter",
        "access_constraint": "none",
    }
    fields.update(overrides)
    return SourceDescriptor(**fields)


def a_provenance(**overrides: Any) -> Provenance:
    fields: dict[str, Any] = {
        "source_id": SOURCE,
        "original_identifier": "receptions/2026-08.json",
        "source_version": 'W/"3f9a1c7"',
        "payload_kind": "data",
        "retrieved_at": RETRIEVED_AT,
        "media_type": "application/json",
    }
    fields.update(overrides)
    return Provenance(**fields)


def a_reception(**overrides: Any) -> NormalisedReception:
    fields: dict[str, Any] = {
        "source_observation_id": "obs-1",
        "satellite_key": "norad:99997",
        "satellite_key_kind": "norad",
        "started_at": STARTED_AT,
        "archive_outcome": "decoded",
    }
    fields.update(overrides)
    return NormalisedReception(**fields)


# --- what the shape of the contract promises -------------------------------


def test_normalise_takes_a_stored_artefact_and_nothing_else() -> None:
    """D-142's guarantee is a signature, so it is read as one.

    A retriever, a URL or a clock in this signature is the whole difference
    between a completion gate that is demonstrated and one that is described.
    """
    signature = inspect.signature(Normaliser.normalise)
    hints = get_type_hints(Normaliser.normalise)

    assert list(signature.parameters) == ["self", "artefact"]
    assert hints["artefact"] is StoredArtefact
    assert hints["return"] is NormalisedBatch


def test_planning_returns_names_rather_than_bytes() -> None:
    """The other half of the same seam: an adapter opens no socket."""
    hints = get_type_hints(Adapter.plan)

    assert hints["request"] is FetchRequest
    assert hints["return"] == tuple[RemoteArtefact, ...]


def test_a_descriptor_carries_exactly_the_columns_the_source_table_holds() -> None:
    """Seven fields defined twice, and the reason each definition exists.

    ``SourceDescriptor`` refuses construction; ``NewIngestSource`` does not,
    because there the database is the check. Drift between them would be
    discovered as an insert failing on a source somebody had already fetched
    from.
    """
    descriptor = {field.name for field in dataclasses.fields(SourceDescriptor)}
    insertable = {field.name for field in dataclasses.fields(NewIngestSource)}

    assert descriptor == insertable


class _Whole:
    """The smallest thing that is both an adapter and a normaliser."""

    transformation_version = "stub-1"

    @property
    def descriptor(self) -> SourceDescriptor:
        return a_descriptor()

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:  # noqa: ARG002
        return ()

    def source_version(self, retrieved: object) -> str:  # noqa: ARG002
        return "v1"

    def normalise(self, artefact: object) -> NormalisedBatch:  # noqa: ARG002
        return NormalisedBatch(transformation_version=self.transformation_version)


class _Partial:
    """An adapter somebody stopped writing: it can plan, and nothing else."""

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:  # noqa: ARG002
        return ()


def test_the_protocols_recognise_a_whole_implementation() -> None:
    """So a registry can reject a half-written adapter rather than fail mid-fetch."""
    assert isinstance(_Whole(), Adapter)
    assert isinstance(_Whole(), Normaliser)


def test_the_protocols_reject_a_half_written_one() -> None:
    """Without this the check above could pass against anything at all."""
    assert not isinstance(_Partial(), Adapter)
    assert not isinstance(_Partial(), Normaliser)


# --- a source cannot be described without its terms ------------------------


def test_a_complete_descriptor_is_accepted() -> None:
    assert a_descriptor().licence == "CC-BY-4.0"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("name", "  "),
        ("licence", ""),
        ("attribution_entry", ""),
        ("terms_url", "http://example.invalid/terms"),
        ("terms_url", "example.invalid/terms"),
        ("source_id", "Reference Archive"),
        ("source_class", "receptions"),
        ("access_constraint", "free"),
    ],
)
def test_a_source_whose_terms_are_not_recorded_cannot_be_described(
    field_name: str, value: str
) -> None:
    """D-134 in type form: the refusal happens before a socket could open."""
    with pytest.raises(TermsNotRecordedError):
        a_descriptor(**{field_name: value})


# --- a fetch is bounded, and an artefact is asked for over https -----------


def test_a_fetch_request_is_bounded_by_default() -> None:
    assert FetchRequest().limit == 50


@pytest.mark.parametrize("limit", [0, -1])
def test_a_fetch_of_nothing_is_refused(limit: int) -> None:
    with pytest.raises(ValueError, match="not a fetch"):
        FetchRequest(limit=limit)


def test_a_backwards_interval_is_refused() -> None:
    with pytest.raises(ValueError, match="precedes"):
        FetchRequest(since=RETRIEVED_AT, until=RETRIEVED_AT - timedelta(days=1))


def test_a_naive_interval_is_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        FetchRequest(since=datetime(2026, 9, 1))  # noqa: DTZ001


def test_an_artefact_asked_for_over_plain_http_is_refused() -> None:
    """Our digest would faithfully record whatever an intermediary substituted."""
    with pytest.raises(RetrievalError, match="intermediary"):
        RemoteArtefact(
            url="http://example.invalid/a.json",
            original_identifier="a.json",
            payload_kind="data",
        )


def test_an_artefact_with_no_identifier_is_refused() -> None:
    with pytest.raises(RetrievalError, match="cite it by"):
        RemoteArtefact(
            url="https://example.invalid/a.json",
            original_identifier=" ",
            payload_kind="data",
        )


# --- versions and tiles ----------------------------------------------------


def a_remote() -> RemoteArtefact:
    return RemoteArtefact(
        url="https://example.invalid/a.json",
        original_identifier="receptions/2026-08.json",
        payload_kind="data",
    )


def test_a_source_version_is_stripped_and_returned() -> None:
    assert require_source_version('  W/"3f9a1c7" ', a_remote()) == 'W/"3f9a1c7"'


@pytest.mark.parametrize("version", ["", "   ", "\n"])
def test_an_artefact_of_unknown_vintage_is_refused_before_publication(
    version: str,
) -> None:
    """Bytes in the raw store that no row could ever point at are worse than none."""
    with pytest.raises(ValueError, match="no source version"):
        require_source_version(version, a_remote())


def test_a_tile_is_refused_before_a_number_is_derived_from_it() -> None:
    with pytest.raises(TileIsNotAQuantityError, match="picture of a measurement"):
        refuse_a_tile(a_provenance(payload_kind="tile"))


def test_data_passes_the_tile_guard() -> None:
    refuse_a_tile(a_provenance())


# --- the records a normaliser returns --------------------------------------


def test_a_station_digest_changes_when_its_published_location_does() -> None:
    """The content key, and why it is computed rather than supplied."""
    here = NormalisedStation("gs-1", lat_deg=51.5, lon_deg=-0.1)
    moved = NormalisedStation("gs-1", lat_deg=52.0, lon_deg=-0.2)

    assert (
        here.content_sha256
        == NormalisedStation("gs-1", lat_deg=51.5, lon_deg=-0.1).content_sha256
    )
    assert here.content_sha256 != moved.content_sha256
    assert len(here.content_sha256) == 32


def test_a_reception_digest_is_stable_across_equal_records() -> None:
    assert a_reception().content_sha256 == a_reception().content_sha256
    assert a_reception().content_sha256 != a_reception(mode="lrpt").content_sha256


def test_no_caller_can_supply_a_digest_that_disagrees_with_the_record() -> None:
    """It is a property, not a field, which is what makes that unrepresentable."""
    names = {field.name for field in dataclasses.fields(NormalisedStation)}

    assert "content_sha256" not in names


def test_an_msp_outcome_is_refused_with_the_reason() -> None:
    """``no_signal`` claims listening evidence no archive can give us."""
    with pytest.raises(NormalisationError, match="no_data"):
        a_reception(archive_outcome="no_signal")


def test_an_unknown_outcome_is_refused() -> None:
    with pytest.raises(NormalisationError, match="archive_outcome"):
        a_reception(archive_outcome="probably_fine")


def test_an_unresolved_object_is_a_kind_rather_than_a_refusal() -> None:
    """Kept as what the archive said, because dropping it is a second filter."""
    kept = a_reception(satellite_key="NOAA-19", satellite_key_kind="source_name")

    assert kept.satellite_key == "NOAA-19"


def test_a_guessed_key_kind_is_refused() -> None:
    with pytest.raises(NormalisationError, match="satellite_key_kind"):
        a_reception(satellite_key_kind="probably_norad")


def test_a_reception_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(NormalisationError, match="before it"):
        a_reception(ended_at=STARTED_AT - timedelta(minutes=1))


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(NormalisationError, match="naive"):
        a_reception(started_at=datetime(2026, 8, 14, 9, 41, 18))  # noqa: DTZ001


def test_half_a_location_is_refused() -> None:
    with pytest.raises(NormalisationError, match="half a location"):
        NormalisedStation("gs-1", lat_deg=51.5)


def test_a_location_off_the_globe_is_refused() -> None:
    with pytest.raises(NormalisationError, match="lat_deg"):
        NormalisedStation("gs-1", lat_deg=91.0, lon_deg=0.0)


# --- a batch is one artefact's worth of rows -------------------------------


def test_a_batch_links_its_receptions_to_its_own_stations() -> None:
    batch = NormalisedBatch(
        transformation_version="reference-1",
        stations=(NormalisedStation("gs-1"),),
        receptions=(a_reception(source_station_key="gs-1"),),
    )

    assert batch.receptions[0].source_station_key == "gs-1"


def test_a_reception_naming_a_station_the_artefact_does_not_describe_is_refused() -> (
    None
):
    """A dropped link is a reception nothing can compute a denominator for."""
    with pytest.raises(NormalisationError, match="does not describe"):
        NormalisedBatch(
            transformation_version="reference-1",
            stations=(NormalisedStation("gs-1"),),
            receptions=(a_reception(source_station_key="gs-2"),),
        )


def test_a_reception_with_no_station_is_allowed() -> None:
    """Many archives publish receptions without saying who made them."""
    batch = NormalisedBatch(
        transformation_version="reference-1", receptions=(a_reception(),)
    )

    assert batch.receptions[0].source_station_key is None


def test_one_artefact_describing_a_station_twice_is_refused() -> None:
    with pytest.raises(NormalisationError, match="same station"):
        NormalisedBatch(
            transformation_version="reference-1",
            stations=(NormalisedStation("gs-1"), NormalisedStation("gs-1", name="dup")),
        )


def test_one_artefact_describing_a_reception_twice_is_refused() -> None:
    with pytest.raises(NormalisationError, match="same reception"):
        NormalisedBatch(
            transformation_version="reference-1",
            receptions=(a_reception(), a_reception(mode="lrpt")),
        )


def test_a_batch_with_no_transformation_version_is_refused() -> None:
    with pytest.raises(NormalisationError, match="transformation version"):
        NormalisedBatch(transformation_version="  ")


def test_an_empty_batch_is_a_legitimate_answer() -> None:
    """An artefact describing no receptions is a fact about the archive, not an error.

    Counting them is Stage 16's business; refusing them here would make an
    empty month indistinguishable from a normaliser that broke.
    """
    batch = NormalisedBatch(transformation_version="reference-1")

    assert batch.stations == ()
    assert batch.receptions == ()
