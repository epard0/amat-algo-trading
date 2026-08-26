# The executor and the strategy share one image because `executor` invokes
# `amat plan` as a subprocess and therefore needs the strategy's interpreter and
# dependency tree present. They remain separate *packages* with a one-way
# dependency; sharing an image does not let either import the other.

FROM python:3.12-slim-bookworm

# `uv` runs the strategy subprocess. Pinned by digest-bearing tag rather than
# `latest` so an image rebuild cannot silently change the build tool.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TZ=UTC

# `tini` reaps zombies and forwards SIGTERM, so `docker compose stop` reaches
# the daemon's handler instead of being absorbed by PID 1. `git` is present
# because the strategy detects its own code revision for plan provenance; a
# missing `git` degrades that to "<undetected>" rather than failing, but the
# provenance is worth the few megabytes.
RUN apt-get update \
    && apt-get install --no-install-recommends -y tini git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Run unprivileged.
RUN useradd --create-home --uid 10001 executor

# These paths must exist *in the image*, owned by `executor`, before compose
# mounts named volumes over them: Docker initialises an empty named volume from
# the image's content at that path, ownership included. Without this the
# volumes are created root-owned and the unprivileged process cannot write its
# journal -- which surfaces as a PermissionError on the first tick, not at
# build time.
RUN mkdir -p /var/lib/amat-executor/plans \
             /var/lib/amat-executor/reports \
             /home/executor/.cache/uv \
             /home/executor/amat-venv \
    && chown -R executor:executor /var/lib/amat-executor /home/executor

WORKDIR /opt/executor

# Dependency layer first, so edits to source do not invalidate the install.
COPY pyproject.toml README.md ./
RUN uv venv /opt/executor/.venv \
    && uv pip install --python /opt/executor/.venv/bin/python --no-cache hatchling

COPY src/ ./src/
RUN uv pip install --python /opt/executor/.venv/bin/python --no-cache .

ENV PATH="/opt/executor/.venv/bin:${PATH}"

USER executor

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["executor", "daemon"]
