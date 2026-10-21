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

COPY packages/sports-core /app/packages/sports-core
RUN pip install --no-cache-dir /app/packages/sports-core

COPY sports/tennis /app/sports/tennis
RUN pip install --no-cache-dir /app/sports/tennis

COPY services/neural/pyproject.toml /app/
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
    && pip install --no-cache-dir \
        "pydantic>=2.7.0" \
        "numpy>=1.26.0" \
        "scipy>=1.12.0" \
        "scikit-learn>=1.4.0" \
        "joblib>=1.3.0" \
        "redis>=5.0.4" \
        "sqlalchemy>=2.0.30" \
        "psycopg2-binary>=2.9.9" \
        "catboost>=1.2.0" \
        "lightgbm>=4.0.0" \
        "xgboost>=2.0.0"

COPY services/neural/app /app/app
RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p /app/models/checkpoints

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD test -f /tmp/healthy || exit 1

CMD ["python", "-m", "app.main"]
