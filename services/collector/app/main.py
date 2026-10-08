import asyncio
import logging
import os
import random
import signal
import sys
from datetime import datetime, timezone
import httpx

# Configure logging
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


async def poll_cycle():
    """Single polling cycle for Fonbet line data."""
    now = datetime.now(timezone.utc).isoformat()
    sport = os.getenv("PRIMARY_SPORT", "tennis")
    logger.info("[%s] Polling Fonbet sport line: %s", now, sport)
    # Heartbeat file for Docker healthcheck
    with open("/tmp/healthy", "w") as f:
        f.write(now)


async def main():
    logger.info("Starting Neurobet Fonbet Collector (Primary Sport: %s)", os.getenv("PRIMARY_SPORT", "tennis"))
    min_seconds = float(os.getenv("FONBET_POLL_MIN_SECONDS", "5.0"))
    max_seconds = float(os.getenv("FONBET_POLL_MAX_SECONDS", "10.0"))

    # Initial heartbeat
    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    while RUNNING:
        try:
            await poll_cycle()
        except Exception as e:
            logger.error("Error during polling cycle: %s", e)

        delay = random.uniform(min_seconds, max_seconds)
        logger.debug("Sleeping for %.2f seconds (jitter 5-10s)...", delay)
        
        # Sleep in small slices to respond quickly to shutdown signal
        steps = int(delay * 10)
        for _ in range(steps):
            if not RUNNING:
                break
            await asyncio.sleep(0.1)

    logger.info("Collector stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
