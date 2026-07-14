# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3

FROM ${NODE_IMAGE} AS ui-builder
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY requirements.build.lock requirements.container.lock ./
RUN python -m pip install --require-hashes --no-cache-dir -r requirements.build.lock && \
    python -m pip wheel --require-hashes --wheel-dir /wheels \
      -r requirements.container.lock

COPY pyproject.toml README.md LICENSE .env.example ./
COPY config ./config
COPY --from=ui-builder /ui/dist ./ui/dist
COPY src ./src
RUN python -m pip wheel --no-build-isolation --no-deps --wheel-dir /wheels .

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    YAGAMI_PROJECT_ROOT=/app \
    YAGAMI_CONFIG_PATH=/app/config/yagami.toml \
    YAGAMI_POLICY_PATH=/app/config/policy.yaml \
    YAGAMI_DB_PATH=/data/yagami.db \
    YAGAMI_HEADLESS=true \
    YAGAMI_REQUIRE_AUTH=true

RUN addgroup --system yagami && adduser --system --ingroup yagami --home /app yagami
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --no-cache-dir /wheels/*.whl && rm -rf /wheels
COPY --chown=yagami:yagami config ./config
RUN mkdir -p /data && chown yagami:yagami /data

USER yagami
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

CMD ["yagami", "--host", "0.0.0.0", "--port", "8000", "--allow-remote"]
