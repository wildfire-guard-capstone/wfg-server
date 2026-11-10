# API server: FastAPI + DB(models · migrations · seed) + rules engine + risk (risk/) + proposal agent (agent/) + AI assistant (assistant/).
# ELMFIRE runs in the separate worker image (docker/worker); the API only builds the weather input.
# Build from the repo root: docker build -f docker/api/Dockerfile -t wfg-api .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

# rasterio wheel(ELMFIRE 기상 입력 — 덱 기상 격자 읽기)이 시스템의 libexpat을 쓴다(slim에는 없음)
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (cached unless pyproject/uv.lock change). Versions come from uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY rules ./rules
COPY agent ./agent
COPY assistant ./assistant
COPY risk ./risk
COPY spread ./spread
COPY config ./config

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
