FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# rasterio/pyogrio wheels bundle GDAL but still link libexpat; libgomp is needed by the scientific wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Project code, SQL schema and the example baseline (methods.py imports inputs/example_change_detection.py).
COPY src ./src
COPY sql ./sql
COPY configs ./configs
COPY inputs/example_change_detection.py inputs/aoi.geojson ./inputs/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Input rasters and outputs are mounted at run time (see docker-compose.yml).
ENTRYPOINT ["s2cd"]
CMD ["--help"]
