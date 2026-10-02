# ELMFIRE + spread worker.
# Base = upstream ELMFIRE built from the pinned commit in docker/worker/ELMFIRE_REF
# (see .github/workflows/elmfire-image.yml). Its conda env already provides
# python 3.12, numpy and GDAL < 3.11 — do not pip install GDAL on top of it.
ARG ELMFIRE_BASE=ghcr.io/wildfire-guard-capstone/elmfire-base:latest
FROM ${ELMFIRE_BASE}

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY spread ./spread
COPY evaluation ./evaluation

# Pure-Python deps only; the numerical/geo stack comes from the ELMFIRE env.
RUN pip install --no-cache-dir --no-deps . && \
    pip install --no-cache-dir "pydantic>=2.7" "pydantic-settings>=2.3"

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["bash"]
