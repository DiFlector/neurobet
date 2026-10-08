import asyncio
import logging
import os
import signal
from datetime import datetime, timezone

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [worker] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("worker")

RUNNING = True


def handle_shutdown(signum, frame):
    global RUNNING
    logger.info("Shutdown signal received (%s). Exiting gracefully...", signum)
    RUNNING = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


async def main():
    logger.info("Worker started. Listening to streams on %s", os.getenv("REDIS_URL", "redis://redis:6379/0"))
    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    while RUNNING:
        with open("/tmp/healthy", "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        await asyncio.sleep(5)

    logger.info("Worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
