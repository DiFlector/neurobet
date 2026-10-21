FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY packages/contracts /app/packages/contracts
RUN pip install --no-cache-dir /app/packages/contracts

COPY packages/observability /app/packages/observability
RUN pip install --no-cache-dir /app/packages/observability

COPY packages/db /app/packages/db
RUN pip install --no-cache-dir /app/packages/db

COPY sports/tennis /app/packages/sport-tennis
RUN pip install --no-cache-dir /app/packages/sport-tennis

COPY services/research/pyproject.toml /app/
RUN pip install --no-cache-dir "playwright>=1.43.0" && playwright install --with-deps chromium

COPY packages/sports-core /app/packages/sports-core
RUN pip install --no-cache-dir /app/packages/sports-core

COPY services/research/app /app/app
RUN pip install --no-cache-dir .

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD test -f /tmp/healthy || exit 1

CMD ["python", "-m", "app.main"]
