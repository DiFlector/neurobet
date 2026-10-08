"""Main entrypoint for FON.BET live collector service."""

import asyncio
import logging
import os
import random
import signal
from datetime import datetime, timezone

from .config import settings
from .service import CollectorService

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [collector] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("collector")

RUNNING = True


def handle_shutdown(signum, frame):
    global RUNNING
    logger.info("Shutdown signal received (%s). Exiting gracefully...", signum)
    RUNNING = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


async def main():
    global RUNNING
    logger.info(
        "Starting Neurobet Live Collector v%s (Sport: %s, Locale: %s, Jitter: %.1f-%.1fs)...",
        settings.collector_version,
        settings.sport_code,
        settings.locale,
        settings.poll_min_seconds,
        settings.poll_max_seconds,
    )

    service = CollectorService()
    try:
        await service.initialize()
    except Exception as e:
        logger.error("Failed to initialize collector service: %s. Continuing with retries...", e)

    # Initial heartbeat
    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    while RUNNING:
        try:
            # Update healthcheck timestamp
            with open("/tmp/healthy", "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())

            await service.poll_once()

        except Exception as exc:
            logger.error("Unhandled exception in main collector loop: %s", exc)

        # Polling delay with random jitter (5.0 - 10.0s)
        delay = random.uniform(settings.poll_min_seconds, settings.poll_max_seconds)
        logger.debug("Sleeping for %.2fs...", delay)

        # Responsive sleep slices
        steps = int(delay * 10)
        for _ in range(steps):
            if not RUNNING:
                break
            await asyncio.sleep(0.1)

    # Clean shutdown
    await service.shutdown()
    logger.info("Collector service terminated cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
