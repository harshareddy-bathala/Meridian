"""Every dependency is pinned, and something proposes moving each pin (D-256).

Stage 24's clause is *all dependencies and images are pinned*. Read from the
files that say what runs:

* **images** — every image compose or a workflow runs, and every base the
  Dockerfile builds from, is named by digest. A tag is a name the registry may
  point elsewhere tomorrow; a digest is the bytes. The platform's own image is
  the one exception: it is ``${MERIDIAN_IMAGE}``, the deployment's choice, and
  ``.env.example`` says to pin it to a ``sha-<commit>`` tag;
* **Actions** — every ``uses:`` names a commit, not a tag a maintainer, or
  whoever took their account, can move;
* **packages** — Python and the dashboard install from their lockfiles, and the
  uv that reads the lockfile is one version everywhere;
* **updates** — Dependabot watches every one of those ecosystems, so a pin is
  moved on purpose, through CI, rather than left to rot.

Each check has a positive control on a small text written here.

Reference: docs/DECISIONS.md D-113, D-256.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSE = sorted((REPO / "deploy").glob("docker-compose*.yml"))
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
DOCKERFILE = REPO / "deploy" / "Dockerfile"
DEPENDABOT = REPO / ".github" / "dependabot.yml"

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
OWN_IMAGE = "${MERIDIAN_IMAGE"


def unpinned_images(text: str) -> list[str]:
    """Every ``image:`` value, or ``*_IMAGE:`` setting, not named by digest."""
    found = re.findall(r"^\s*(?:image|[A-Z_]*_IMAGE):\s*['\"]?([^\s'\"#]+)", text, re.M)
    return [
        one for one in found if not one.startswith(OWN_IMAGE) and not DIGEST.search(one)
    ]


def unpinned_actions(text: str) -> list[str]:
    """Every ``uses:`` that names a tag or a branch rather than a commit."""
    found = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", text, re.M)
    return [one for one in found if not one.startswith("./") and not COMMIT.match(one)]


def unpinned_bases(text: str) -> list[str]:
    """Every ``FROM`` or ``COPY --from`` naming an image, not a stage, by tag."""
    stages = set(
        re.findall(r"^FROM[ \t].*[ \t]AS[ \t]+(\w+)[ \t]*$", text, re.M | re.I)
    )
    found = re.findall(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", text, re.M)
    found += re.findall(r"COPY\s+--from=(\S+)", text)
    return [
        one
        for one in found
        if one not in stages and "$" not in one and not DIGEST.search(one)
    ]


def unversioned_uv(text: str) -> list[str]:
    """Every ``setup-uv`` step whose next lines do not pin ``version``."""
    lines = text.splitlines()
    return [
        line.strip()
        for i, line in enumerate(lines)
        if "setup-uv@" in line
        and not any("version:" in later for later in lines[i + 1 : i + 4])
    ]


# --- the repository ------------------------------------------------------------


def test_every_image_is_named_by_digest() -> None:
    texts = {
        path.name: path.read_text(encoding="utf-8") for path in [*COMPOSE, *WORKFLOWS]
    }

    assert sum(len(re.findall(r"@sha256:", one)) for one in texts.values()) >= 6
    assert {name: unpinned_images(text) for name, text in texts.items()} == {
        name: [] for name in texts
    }


def test_every_base_the_image_builds_from_is_named_by_digest() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert len(re.findall(r"@sha256:", text)) >= 3
    assert unpinned_bases(text) == []


def test_every_action_is_named_by_commit() -> None:
    texts = {path.name: path.read_text(encoding="utf-8") for path in WORKFLOWS}

    assert sum(len(re.findall(r"uses:", one)) for one in texts.values()) >= 10
    assert {name: unpinned_actions(text) for name, text in texts.items()} == {
        name: [] for name in texts
    }


def test_one_uv_reads_the_lockfile_everywhere() -> None:
    texts = {path.name: path.read_text(encoding="utf-8") for path in WORKFLOWS}
    pinned = {
        re.search(r'UV_VERSION:\s*"([^"]+)"', text).group(1)  # type: ignore[union-attr]
        for text in texts.values()
        if "setup-uv@" in text
    }
    built_with = re.search(r"astral-sh/uv:([\d.]+)@", DOCKERFILE.read_text("utf-8"))

    assert {name: unversioned_uv(text) for name, text in texts.items()} == {
        name: [] for name in texts
    }
    assert built_with
    assert pinned == {built_with.group(1)}


def test_packages_install_from_their_lockfiles() -> None:
    assert (REPO / "uv.lock").is_file()
    assert (REPO / "dashboard" / "package-lock.json").is_file()
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv sync --frozen" in dockerfile
    assert "npm ci" in dockerfile


def test_the_platform_image_is_the_deployments_to_pin() -> None:
    assert "sha-<commit>" in (REPO / "deploy" / ".env.example").read_text("utf-8")


def test_dependabot_watches_every_pinned_ecosystem() -> None:
    text = DEPENDABOT.read_text(encoding="utf-8")
    watched = set(re.findall(r"package-ecosystem:\s*([\w-]+)", text))

    assert watched == {"github-actions", "docker", "docker-compose", "uv", "npm"}


# --- positive controls ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("    image: prom/prometheus:v3.13.2\n", ["prom/prometheus:v3.13.2"]),
        (
            '  TIMESCALE_IMAGE: "timescale/timescaledb:2.29.0-pg16"\n',
            ["timescale/timescaledb:2.29.0-pg16"],
        ),
        ("    image: grafana/grafana@sha256:abc\n", ["grafana/grafana@sha256:abc"]),
        ("    image: ${MERIDIAN_IMAGE:-ghcr.io/x/meridian:main}\n", []),
        (f"    image: prom/prometheus:v3.13.2@sha256:{'a' * 64}\n", []),
    ],
)
def test_an_image_by_tag_is_found(text: str, found: list[str]) -> None:
    assert unpinned_images(text) == found


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("      - uses: actions/checkout@v4\n", ["actions/checkout@v4"]),
        ("      - uses: actions/checkout@main\n", ["actions/checkout@main"]),
        (f"      - uses: actions/checkout@{'a' * 40} # v4.4.0\n", []),
        ("      - uses: ./.github/actions/local\n", []),
    ],
)
def test_an_action_by_tag_is_found(text: str, found: list[str]) -> None:
    assert unpinned_actions(text) == found


def test_a_base_by_tag_is_found_and_a_stage_is_not() -> None:
    text = (
        "FROM python:3.11-slim AS build\n"
        "COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /uv\n"
        f"FROM node:24@sha256:{'b' * 64} AS dashboard\n"
        "COPY --from=build /app /app\n"
    )

    assert unpinned_bases(text) == ["python:3.11-slim", "ghcr.io/astral-sh/uv:0.11.20"]


def test_a_setup_uv_without_a_version_is_found() -> None:
    text = (
        f"      - uses: astral-sh/setup-uv@{'c' * 40}\n"
        "        with:\n"
        "          enable-cache: true\n"
        "      - run: uv sync\n"
    )

    assert len(unversioned_uv(text)) == 1
