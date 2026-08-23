FROM ghcr.io/astral-sh/uv:latest AS uv
FROM python:3.11-slim

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY ai_service/requirements.txt .

# GTX 1050 Ti is Pascal (sm_61). Official cu128+ wheels dropped it; cu124 still has kernels.
# Install deps first without the CPU torch from PyPI, then the CUDA 12.4 wheel.
RUN grep -vE '^torch([=<>]|[[:space:]]*$)' requirements.txt > /tmp/requirements-notorch.txt \
    && uv pip install --system --no-cache -r /tmp/requirements-notorch.txt \
    && uv pip install --system --no-cache --index-url https://download.pytorch.org/whl/cu124 torch==2.6.0

COPY shared/neurobet_filters /app/neurobet_filters
COPY shared/neurobet_features /app/neurobet_features
COPY ai_service/ .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
