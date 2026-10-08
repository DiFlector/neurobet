FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install packages
COPY packages/contracts /app/packages/contracts
RUN pip install --no-cache-dir /app/packages/contracts

COPY packages/streams /app/packages/streams
RUN pip install --no-cache-dir /app/packages/streams

# Install collector dependencies & playwright
COPY services/collector/pyproject.toml /app/
COPY services/collector/README.md /app/
COPY services/collector/app /app/app
RUN pip install --no-cache-dir .
RUN playwright install --with-deps chromium

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD test -f /tmp/healthy || exit 1

CMD ["python", "-m", "app.main"]
