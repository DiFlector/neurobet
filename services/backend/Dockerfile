FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install packages
COPY packages/contracts /app/packages/contracts
RUN pip install --no-cache-dir /app/packages/contracts

COPY packages/db /app/packages/db
RUN pip install --no-cache-dir /app/packages/db

COPY packages/streams /app/packages/streams
RUN pip install --no-cache-dir /app/packages/streams

# Install backend dependencies & app
COPY services/backend/pyproject.toml /app/
COPY services/backend/app /app/app
RUN pip install --no-cache-dir .

# Copy migration configuration
COPY alembic.ini /app/
COPY migrations /app/migrations

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
