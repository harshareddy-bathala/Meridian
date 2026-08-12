# Meridian platform image.
#
# Multi-stage so the runtime layer carries no build toolchain. CI builds this
# multi-arch and pushes to GHCR so the Pi runs `docker compose pull` rather than
# `docker build` — compiling psycopg wheels on a Pi is how the ten-minute
# bring-up requirement becomes forty.

# Every tag here is pinned. A floating `latest` means the image that passed CI
# and the image that runs on the Pi are different builds with the same name, and
# the difference surfaces as an unreproducible failure weeks later. Digest
# pinning arrives with the multi-arch push.

FROM python:3.11.15-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# --all-packages installs every workspace member. The root project is a container
# and depends on nothing, so a plain `uv sync --no-dev` would install an empty
# environment with no alembic and no meridian on it.
#
# Dependency metadata first, so a source-only change does not re-resolve.
COPY pyproject.toml uv.lock ./
COPY platform/pyproject.toml platform/
COPY client/pyproject.toml client/
COPY simulator/pyproject.toml simulator/
RUN mkdir -p platform/src/meridian client/src/meridian_client simulator/src/meridian_sim \
    && touch platform/src/meridian/__init__.py \
            client/src/meridian_client/__init__.py \
            simulator/src/meridian_sim/__init__.py \
    && uv sync --frozen --no-dev --no-editable --all-packages

COPY platform/ platform/
COPY client/ client/
COPY simulator/ simulator/
RUN uv sync --frozen --no-dev --no-editable --all-packages


FROM python:3.11.15-slim-bookworm AS runtime

# Never run as root. The platform is publicly reachable by design.
RUN useradd --create-home --uid 10001 meridian

WORKDIR /app
COPY --from=build --chown=meridian:meridian /app/.venv /app/.venv
COPY --chown=meridian:meridian deploy/ deploy/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Where a simulated fleet keeps its credentials, held work and outboxes
# (docs/DECISIONS.md D-080). Created here rather than left to the volume mount:
# Docker copies an image directory's ownership into a fresh named volume, and
# invents a root-owned one when the path is absent — which this container, which
# is deliberately not root, then cannot write a single file to.
RUN install -d -o meridian -g meridian /var/lib/meridian-sim

USER meridian
EXPOSE 8000

# No transmit code paths anywhere, and no shell in the default command.
CMD ["uvicorn", "meridian.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
