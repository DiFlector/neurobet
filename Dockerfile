# Fast and lightweight Python container with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt-get/lists/*

# Copy requirements and install dependencies
COPY requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application files
COPY . .

# Ensure output directory exists
RUN mkdir -p /app/output

# Set Python unbuffered output
ENV PYTHONUNBUFFERED=1

# Command to run Telegram bot
CMD ["python", "telegram_bot.py"]
