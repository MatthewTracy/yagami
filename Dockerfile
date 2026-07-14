# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.12-slim-bookworm AS runtime

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
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
COPY --chown=yagami:yagami config ./config
RUN mkdir -p /data && chown yagami:yagami /data

USER yagami
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

CMD ["yagami", "--host", "0.0.0.0", "--port", "8000", "--allow-remote"]
