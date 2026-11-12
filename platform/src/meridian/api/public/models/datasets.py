"""The published evidence packages, as their index states them and the API serves them.

``meridian dataset publish`` copies a public-cut package to
``<published>/evidence/<hash>/`` and writes ``evidence/index.json``, schema
``evidence-index-1`` (D-369). The deployment names the index with
``EVIDENCE_INDEX``.

**The index is checked, not trusted**, as the model summary is (D-332):
- the schema string must match exactly, and no key may be unknown;
- every package is named by the first twelve characters of its content hash,
  and appears once;
- every file is named as a sealed directory names one, appears once, and the
  index lists the package's manifest among them;
- each population's file is one of the package's files, and the simulated
  population, and only it, is labelled simulated (rule 5).

A file listed here is served only if its bytes still have the digest and the
size the index states, checked as it is read (``meridian.api.public.datasets``).

This module builds no SQL and opens no connection, and imports nothing from
``meridian.datasets``, which the API may not (D-143): the shape is pinned
against the publishing command by ``tests/unit/test_evidence_publish.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "MANIFEST_FILE",
    "PackageFile",
    "PackagePopulation",
    "PublicDataset",
    "PublicDatasets",
]

MANIFEST_FILE = "manifest.json"

_HASH = r"^[0-9a-f]{64}$"
_FILE_NAME = r"^[a-z0-9_]+\.[a-z]+$"
_POPULATION_FILES = {
    "measured": "receptions.jsonl",
    "simulated": "receptions_simulated.jsonl",
}


class _Strict(BaseModel):
    """A part of the index: every key known, nothing coerced from another type."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PackageFile(_Strict):
    """One file a reader may download, with what it must hash to."""

    name: str = Field(pattern=_FILE_NAME)
    sha256: str = Field(pattern=_HASH)
    size_bytes: int = Field(ge=0)


class PackagePopulation(_Strict):
    """One population's receptions, in a file of its own (D-311)."""

    population: Literal["measured", "simulated"]
    file: str = Field(pattern=_FILE_NAME)
    receptions: int = Field(ge=0)
    simulated: bool


class PublicDataset(_Strict):
    """One published package: what it was built from, and its files."""

    name: str = Field(pattern=r"^[0-9a-f]{12}$")
    content_sha256: str = Field(pattern=_HASH)
    snapshot_sha256: str = Field(pattern=_HASH)
    config_sha256: str = Field(pattern=_HASH)
    transformation: str = Field(pattern=r"^evidence-[0-9]+$")
    since: datetime
    as_of: datetime
    created_at: datetime
    licence: Literal["CC-BY-4.0"]
    citation: str = Field(min_length=1)
    populations: list[PackagePopulation] = Field(min_length=1)
    files: list[PackageFile] = Field(min_length=2)

    @model_validator(mode="after")
    def _named_and_whole(self) -> Self:
        if not self.content_sha256.startswith(self.name):
            raise ValueError(f"package {self.name} is not named by its hash")
        if self.content_sha256 not in self.citation:
            raise ValueError(f"package {self.name}'s citation names another hash")
        names = [one.name for one in self.files]
        if len(names) != len(set(names)) or MANIFEST_FILE not in names:
            raise ValueError(f"package {self.name}'s files are {names}")
        populations = [one.population for one in self.populations]
        if len(populations) != len(set(populations)):
            raise ValueError(f"package {self.name} names a population twice")
        for one in self.populations:
            if one.file != _POPULATION_FILES[one.population] or one.file not in names:
                raise ValueError(f"{one.population} is in {one.file}")
            if one.simulated != (one.population == "simulated"):
                raise ValueError(f"{one.population} is labelled {one.simulated}")
        return self

    def file(self, name: str) -> PackageFile | None:
        """The file of this name, or ``None``."""
        return next((one for one in self.files if one.name == name), None)


class PublicDatasets(_Strict):
    """Every published package, newest snapshot first, as the index states them."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )

    status: Literal["computed"] = "computed"
    schema_: Literal["evidence-index-1"] = Field(alias="schema")
    packages: list[PublicDataset]

    @model_validator(mode="after")
    def _each_once(self) -> Self:
        names = [one.name for one in self.packages]
        if len(names) != len(set(names)):
            raise ValueError(f"a package is listed twice: {names}")
        return self

    def package(self, name: str) -> PublicDataset | None:
        """The package of this name, or ``None``."""
        return next((one for one in self.packages if one.name == name), None)
