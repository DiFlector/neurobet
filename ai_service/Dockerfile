FROM ghcr.io/astral-sh/uv:latest AS uv
FROM python:3.11-slim

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY ai_service/requirements.txt .

# TORCH_VARIANT=cpu (default, docker-compose.yml) — PyPI CPU wheel, no NVIDIA libs.
# TORCH_VARIANT=cuda (docker-compose-cuda.yml) — cu124 wheel for Pascal+ (sm_61 / GTX 1050 Ti).
ARG TORCH_VARIANT=cpu
RUN grep -vE '^torch([=<>]|[[:space:]]*$)' requirements.txt > /tmp/requirements-notorch.txt \
    && uv pip install --system --no-cache -r /tmp/requirements-notorch.txt \
    && if [ "$TORCH_VARIANT" = "cuda" ]; then \
         uv pip install --system --no-cache --index-url https://download.pytorch.org/whl/cu124 torch==2.6.0; \
       else \
         uv pip install --system --no-cache --index-url https://download.pytorch.org/whl/cpu torch==2.6.0; \
       fi

COPY shared/neurobet_filters /app/neurobet_filters
COPY shared/neurobet_features /app/neurobet_features
COPY shared/neurobet_time /app/neurobet_time
COPY ai_service/ .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
