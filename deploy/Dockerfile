# Meridian platform image.
#
# Multi-stage so the runtime layer carries no build toolchain.
# .github/workflows/image-publish.yml builds it for amd64 and arm64 and pushes it
# to GHCR, so the Pi runs `docker compose pull` rather than `docker build` —
# compiling wheels on a Pi is how the ten-minute bring-up becomes forty (D-113).

# Every base image is pinned by tag and by digest. A floating `latest` makes the
# image that passed CI and the image on the Pi different builds with one name; a
# tag alone still lets its publisher move it. The digest is the multi-arch index,
# so one pin covers both architectures. To move a pin, change the tag and take
# the new digest from `docker buildx imagetools inspect <image>:<tag>`.

# The dashboard (D-081). Pinned to the build platform: the output is HTML, CSS
# and JavaScript, identical for every target, so a multi-arch build compiles it
# once natively instead of running npm under arm64 emulation.
FROM --platform=$BUILDPLATFORM node:24.21.0-bookworm-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553 AS dashboard

WORKDIR /dashboard
# The lockfile first, for the same reason uv.lock goes first below.
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build


FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3 AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.20@sha256:eaa5f1a3305307aaf9e67fe2bbba1d85ebbb2d8a63bce23af21797bfafbe0f8b /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# --all-packages installs every workspace member. The root project is a container
# and depends on nothing, so a plain `uv sync --no-dev` would install an empty
# environment with no alembic and no meridian on it.
#
# meridian-ingest is the exception, and it is excluded rather than omitted:
# uv reads every member's pyproject to resolve the workspace, so its metadata
# has to be here, while the package itself must not be. The archive layer on
# the machine that has to keep receiving when every archive is unreachable is
# precisely what the independence test forbids (D-138). Its absence is checked
# by tests/unit/test_layout.py, so a fifth distribution is a decision rather
# than a default.
#
# Dependency metadata first, so a source-only change does not re-resolve.
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
    && uv sync --frozen --no-dev --no-editable --all-packages --no-install-package meridian-ingest

COPY platform/ platform/
COPY client/ client/
COPY simulator/ simulator/
RUN uv sync --frozen --no-dev --no-editable --all-packages --no-install-package meridian-ingest


FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3 AS runtime

# Never run as root. The platform is publicly reachable by design.
RUN useradd --create-home --uid 10001 meridian

WORKDIR /app
COPY --from=build --chown=meridian:meridian /app/.venv /app/.venv
COPY --chown=meridian:meridian deploy/ deploy/
# Owned by root and not writable by the platform: nothing at runtime has any
# business changing the page it serves.
COPY --from=dashboard /dashboard/dist /app/dashboard

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DASHBOARD_DIR=/app/dashboard

# Where a simulated fleet keeps its credentials, held work and outboxes
# (docs/DECISIONS.md D-080). Created here rather than left to the volume mount:
# Docker copies an image directory's ownership into a fresh named volume, and
# invents a root-owned one when the path is absent — which this container, which
# is deliberately not root, then cannot write a single file to.
RUN install -d -o meridian -g meridian /var/lib/meridian-sim

USER meridian
EXPOSE 8000

# No transmit code paths anywhere, and no shell in the default command.
# `meridian serve` rather than uvicorn directly: it applies API_LOG_LEVEL and
# API_WORKERS, and refuses several workers without a metrics directory (D-109).
CMD ["meridian", "serve", "--host", "0.0.0.0", "--port", "8000"]
