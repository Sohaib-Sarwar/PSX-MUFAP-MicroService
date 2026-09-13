# ── MUFAP Mutual Funds Service ────────────────────────────────────────────────
# Multi-stage: wheels are built with a compiler present, the runtime image has none.

FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      libxml2 libxslt1.1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Run as a non-root user. A scraper parses untrusted HTML from the public
# internet; a parsing exploit should not land with full container privileges.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
COPY --chown=appuser:appuser app/ ./app/
USER appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8001

EXPOSE 8001

# Liveness only. Tying the container healthcheck to data availability means an
# upstream outage triggers a restart loop that cannot fix anything. --max-time
# is explicit: urllib's default timeout is None, which hangs forever.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
  CMD curl --fail --silent --max-time 4 "http://127.0.0.1:${PORT:-8001}/health" || exit 1

# exec form so uvicorn is PID 1 and receives SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001} --workers 1 --timeout-graceful-shutdown 20"]
