# meridian-ingest image: the archive and public-source layer, apart from the
# platform (D-138, D-352).
#
# The platform image excludes this distribution by name, so the machine that has
# to keep scheduling with every source unreachable carries none of it. This image
# is the other half: `meridian-ingest` and the `meridian` it imports, run as its
# own container under docker-compose.ingest.yml, never inside the jobs service
# (D-225). It serves nothing, listens on nothing and has no dashboard.
#
# The same bases as deploy/Dockerfile, pinned by the same digests (D-256), so one
# Dependabot update moves both.

FROM python:3.11.17-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.20@sha256:eaa5f1a3305307aaf9e67fe2bbba1d85ebbb2d8a63bce23af21797bfafbe0f8b /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# `--package meridian-ingest` installs it and what it depends on, which is
# `meridian` and httpx; the client and simulator are not among them. The `hdf5`
# extra is night-time lights' reader (D-226): this image is where that source
# runs, so it carries the library the platform image never does.
COPY pyproject.toml uv.lock ./
COPY platform/pyproject.toml platform/
COPY client/pyproject.toml client/
COPY simulator/pyproject.toml simulator/
COPY ingest/pyproject.toml ingest/
RUN mkdir -p platform/src/meridian client/src/meridian_client simulator/src/meridian_sim \
            ingest/src/meridian_ingest \
    && touch platform/src/meridian/__init__.py \
            client/src/meridian_client/__init__.py \
            simulator/src/meridian_sim/__init__.py \
            ingest/src/meridian_ingest/__init__.py \
    && uv sync --frozen --no-dev --no-editable --package meridian-ingest --extra hdf5

COPY platform/ platform/
COPY ingest/ ingest/
RUN uv sync --frozen --no-dev --no-editable --package meridian-ingest --extra hdf5


FROM python:3.11.17-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS runtime

# Security updates at build time, and the base image's unused pip removed, for
# the reasons deploy/Dockerfile gives (D-205).
RUN apt-get update \
    && apt-get -y upgrade --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip uninstall --yes --no-cache-dir pip setuptools wheel

RUN useradd --create-home --uid 10001 meridian

WORKDIR /app
COPY --from=build --chown=meridian:meridian /app/.venv /app/.venv
# `fetch` refuses a source whose attribution entry is missing, and looks for
# this file above its working directory (D-134). Without it here the check
# would say it could not run, and continue.
COPY ATTRIBUTION.md ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The raw store and the rate ledger, on a volume. Made here so a fresh named
# volume takes this ownership rather than root's (deploy/Dockerfile explains).
RUN install -d -o meridian -g meridian /var/lib/meridian-ingest

USER meridian

# No shell, and no socket opened but by `fetch` (D-142).
CMD ["meridian-ingest", "follow", "--interval", "300"]
