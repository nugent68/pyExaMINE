# syntax=docker/dockerfile:1
#
# pyExaMINE container.
#
# Uses the official Astral uv image so we don't have to install uv
# ourselves. Python 3.12 is well within the project's `>=3.10`
# constraint and is the most-tested CPython release among the
# 3.10/3.11/3.12 set the lockfile resolves cleanly against.
#
# Build:   docker build -t pyexamine:latest .
# Run:     docker run --rm pyexamine                       # default smoke run
#          docker run --rm pyexamine --mineral lithium --steps 200 --no-viz
#          docker run --rm -v $(pwd)/runs:/data pyexamine --all
#
# Output convention: results land in /data inside the container by
# default (PYEXAMINE_OUTPUT_DIR env var). Mount a writable host
# directory there to persist results:
#   docker run -v $(pwd)/runs:/data pyexamine ...
#   shifter --image=... --volume=$SCRATCH/run:/data ...
# This makes the same image work under Docker (writable image FS) and
# Shifter (read-only image FS) without code changes.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Native libraries that pure-Python wheels link against. LightGBM's
# wheel is dynamically linked against libgomp (OpenMP runtime), which
# the slim base image doesn't ship. Install before switching to the
# unprivileged user.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user. The container's UID/GID can be overridden at
# build time so bind-mounted output volumes keep host ownership.
ARG UID=1000
ARG GID=1000
RUN groupadd --gid ${GID} app \
    && useradd --uid ${UID} --gid ${GID} --create-home --shell /bin/bash app

# Pre-create the conventional /data mountpoint as the runtime user. When
# nothing is mounted on top, /data is writable inside the image (Docker)
# but read-only under Shifter; either way users can override it with a
# bind mount of any writable host directory.
RUN mkdir /data && chown app:app /data

WORKDIR /app
RUN chown app:app /app
USER app

# Headless matplotlib (no DISPLAY, no GUI backend probing).
ENV MPLBACKEND=Agg

# Default output directory inside the container. run_simulation.py and
# scripts/regenerate_outputs.py both read this when --output-dir /
# --output-root aren't passed explicitly, so the standard recipe is
# always:
#   <runtime> --bind-mount-host-dir-to /data <image> ...
ENV PYEXAMINE_OUTPUT_DIR=/data

# uv keeps its bytecode / wheel cache under $HOME by default. Pinning
# the venv inside /app means it lands on the same filesystem as the
# project source and isn't recreated on a chown / volume-mount.
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# 1. Install project dependencies first. Copying just the lockfile +
#    pyproject.toml means edits to source code don't bust this layer.
#    --extra trajectory pulls torch in for the trajectory surrogate;
#    on linux/amd64 uv resolves to the CUDA-12 torch wheel, which is
#    what we want for NERSC GPU nodes (and harmless on CPU nodes -- it
#    just doesn't initialise CUDA).
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --extra trajectory

# 2. Copy the rest of the project + install pyexamine itself into the
#    venv (records package metadata; lets `python -m ...` find modules
#    via the installed entry-points if they're added later).
COPY --chown=app:app . .
RUN uv sync --frozen --no-dev --extra trajectory

ENTRYPOINT ["uv", "run", "python", "run_simulation.py"]
CMD ["--all", "--steps", "200", "--seed", "42", "--no-viz"]
