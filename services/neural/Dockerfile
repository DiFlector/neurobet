FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY packages/contracts /app/packages/contracts
RUN pip install --no-cache-dir /app/packages/contracts

COPY packages/db /app/packages/db
RUN pip install --no-cache-dir /app/packages/db

COPY packages/features /app/packages/features
RUN pip install --no-cache-dir /app/packages/features

COPY services/neural/pyproject.toml /app/
COPY services/neural/app /app/app
RUN pip install --no-cache-dir .

RUN mkdir -p /app/models/checkpoints

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD test -f /tmp/healthy || exit 1

CMD ["python", "-m", "app.main"]
